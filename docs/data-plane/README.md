# Data Plane

The control plane decided the rules. The data plane applies them to *this
specific request, right now*, while a user is waiting. Two files carry the
core architectural rule this whole plane is built around: **no individual
detector ever decides an action.**

Source: `apps/api/routers/check.py` (the orchestrator), `apps/api/data_plane/fusion.py`
(the only place a decision is made), `apps/api/data_plane/detectors/` (pure
signal producers — see [detectors.md](detectors.md)),
[redaction.md](redaction.md) for the masking/restoring mechanics.

## The non-negotiable rule

Every detector returns a **signal**, never an action:

```json
{"source": "input_pii", "type": "PII", "score": 0.85, "status": "ok"}
```

`status` is one of three values, and the difference between two of them is the
part most implementations get wrong:

| Status | Meaning | Fusion treats it as |
|---|---|---|
| `"ok"` | Check ran, here's the score | Normal input to scoring |
| `"disabled"` | Check was turned off for this bundle — **never even invoked** | Excluded from scoring entirely — not defaulted to "safe" |
| `"timeout"` | Check ran but didn't finish before the deadline | **Unknown, not safe** — escalates |

Detectors and fusion logic never import each other. `check.py` is the only
module that imports both and does any branching. This isn't a style
preference — it's what makes "which checks are actually on" fully auditable
from the bundle alone, and what makes it impossible for a detector to
accidentally short-circuit the system it's supposed to just be feeding data
into.

## Request path

```
prompt → [input detectors, parallel] → decide_input() → block|redact|allow
                                                              │
                                          (block: LLM never called)
                                                              │
                                                     call_llm(prompt)
                                                              │
                              [T0 detectors] → decide_t0_output()
                              [T1 detectors, parallel] → decide_t1_output()
                                                              │
                                            decide_output_fusion()
                                                              │
                                    block | regenerate | allow (+ modifiers)
```

## Fusion — every rule, verified, not just written

`fusion.py` has exactly four functions, ported directly from the PRD's own
pseudocode. Each one was checked against the PRD's own required test
checklist with synthetic signals — no detectors, no HTTP, no database:

**`decide_input`** — secrets or injection at full confidence block outright
(the LLM is never called); PII is threshold-banded into `redact` / `allow`
(flagged for review) / `allow`:

```python
if any(s["type"] == "secrets" and s["score"] >= 1.0 for s in active):
    return "block", False
...
if max_score > bundle["input_pii_redact_threshold"]:
    return "redact", False
elif max_score > bundle["input_pii_review_threshold"]:
    return "allow", True   # borderline — feeds a reviewer queue (not built, see learning plane)
```

**`decide_t0_output`** — canary leak blocks outright (a leaked system prompt
has no partial fix); a leaked secret gets redacted:

```python
if any(s["type"] == "canary_leak" and s["score"] >= 1.0 for s in active):
    return "block"
if any(s["type"] == "secrets" and s["score"] >= 1.0 for s in active):
    return "redact"
```

**`decide_t1_output`** — the one with real nuance. A `timeout` on toxicity
escalates (`regenerate` first, `block` if it happens again after a retry) —
*unknown is not safe*:

```python
if tox_unknown:
    action = "block" if retried else "regenerate"
elif tox_score >= bundle["toxicity_block_threshold"]:
    action = "block"
elif tox_score >= bundle["toxicity_regenerate_threshold"]:
    action = "flag_visible" if bundle["toxicity_mild_action"] == "flag_visible" else ("block" if retried else "regenerate")
```

PII is only evaluated if toxicity hasn't already forced an outcome — a
severely toxic response doesn't also need a PII verdict, it's getting
regenerated or blocked regardless.

**`decide_output_fusion`** — composes, doesn't just pick a winner. `block` and
`regenerate` are whole-response outcomes that preempt everything; `redact`,
`flag_visible`, and silent `flag` can all legitimately apply to the *same*
response at once (a toxicity disclaimer stacked on an unrelated PII
redaction):

```python
if t0_action == "block" or t1_action == "block":
    return {"action": "block", "modifiers": []}
if t1_action == "regenerate":
    return {"action": "regenerate", "modifiers": []}

modifiers = []
if t0_action == "redact" or t1_action == "redact": modifiers.append("redact")
if t1_action == "flag_visible": modifiers.append("flag_visible")
if t1_review_needed: modifiers.append("flag")
return {"action": "allow", "modifiers": modifiers}
```

Verified directly (not just read and trusted): disabled-excluded-not-defaulted-safe,
timeout-escalates (including that a *second* timeout after a retry produces
`block`, not another `regenerate` — the retry cap is real), and all three
modifiers composing on one `allow` at once.

## The orchestrator owns which detectors run at all

This is the actual cost saving a per-check toggle buys — not fusion ignoring a
result after the fact, but the orchestrator never invoking a disabled
detector:

```python
input_results = await _run_checks([
    ("input_secrets", "secrets", bool(checks_in.get("secrets")), secrets.scan, body.prompt),
    ("input_injection", "injection", bool(checks_in.get("injection")), injection.scan, body.prompt),
    ("input_pii", "PII", bool(checks_in.get("pii")), pii.scan, body.prompt),
])
```

`_run_checks` resolves a disabled entry to a `status: "disabled"` signal
immediately — `secrets.scan()` is never called, not called-and-ignored.

## Parallel execution and real timeouts — what actually works, and an honest limit

The PRD calls for T1 to run its checks "in parallel (`asyncio.gather`); hard
deadline on the join." That's implemented for every stage (input, T0, T1), via
`asyncio.to_thread` + `asyncio.wait_for`:

```python
async def _run_detector(source, type_, scan_fn, text):
    try:
        matches = await asyncio.wait_for(
            asyncio.to_thread(scan_fn, text), timeout=DETECTOR_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return _sig(source, type_, None, "timeout"), []
    return _sig(source, type_, _max_score(matches), "ok"), matches
```

This was tested directly, not assumed: a detector rigged to sleep 10 seconds
against a 2-second deadline was cut off at exactly 2.0s and produced a real
`status: "timeout"` signal, which `decide_t1_output` already escalates
correctly.

**What this does *not* deliver, confirmed by benchmark rather than assumed:**
wall-clock speedup. Running the toxicity and PII detectors concurrently was
measured at 0.99x vs. running them one after another — no improvement.
Python's GIL means two threads both running GIL-bound code (spaCy's pipeline,
a HF tokenizer) don't actually run any faster together. What the change
*does* deliver: the event loop stays unblocked for other concurrent requests,
and the timeout enforcement above is real. Genuine multi-process parallelism
would fix the throughput question too, at the cost of loading a second copy
of each model per worker — an open tradeoff, not decided yet.

`DETECTOR_TIMEOUT_SECONDS` (2.0s) is an internal orchestrator constant, not a
policy field — deliberately kept separate from the PRD's rule that no
tenant-facing latency budget exists anywhere in a bundle.

## The regenerate retry, actually wired to the model

`retried` starts `False`; on a `regenerate` verdict the orchestrator calls the
LLM a second time with `retry=True` (which sends a stricter safety
instruction — see [LLM integration](../cross-cutting/llm-integration.md)) and
re-runs every output check against the new response. If it's `regenerate`
again, `decide_t1_output`'s own `retried` flag turns that into `block` instead
— the retry cap of 1 is enforced in the fusion logic itself, not by the
orchestrator counting attempts.

## What's out of scope for V1, on purpose

- **T2 (LLM-as-judge).** No field, no code path — not "disabled," genuinely
  absent, per PRD §4.6.
- **Grounding checks.** Blocked on RAG being in scope at all; not built.
- **Output-side injection classifier.** Two different things could be meant
  by it (propagation vs. success of an injection) and the PRD explicitly
  defers deciding which until a later version.
- **Session state / multi-turn history.** Every request is independent — no
  `flag_count` escalation across a conversation, no tamper-detected context.
