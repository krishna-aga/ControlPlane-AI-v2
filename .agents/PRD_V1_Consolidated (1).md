# PRD — ControlPlane.ai — Version 1 (Consolidated)

**Doc owner:** [you]
**Status:** V1 scope locked for the components below; anything marked *Deferred* is intentionally out of this version, not forgotten.
**Purpose of this document:** the three layer-specific PRDs (Control Plane, Data Plane, Infra & Platform) remain the source of truth and stay in sync with future discussion. This document pulls only the **V1-committed** content from all of them into one place, so V1's actual scope can be read end-to-end without cross-referencing three files.

---

## 1. Project Overview

**What this is:** A hackathon prototype (Accenture Innovation Challenge 2026, "PS-1 / ControlPlane.ai" track) — a governance/safety proxy sitting between enterprises and their LLM calls. Enterprises run multiple AI use cases (customer support bot, internal copilot, regulated decision-support tool) with different risk/speed needs; this system enforces per-use-case safety policy (PII, injection, toxicity checks) via a layered, lockable policy system, without hardcoding rules into checking code.

**Why it exists:** hardcoding safety differences into checking code means every rule change requires a redeploy, and there's no clean audit trail for "what rule was live on what date, and who approved it." The fix: **rules are data, not code** — authored by humans, merged with enforceable locking, versioned, and handed to the part of the system that enforces them at runtime.

**Three planes:**
- **Control plane** — decides the rules. Human-speed, changes rarely.
- **Data plane** — applies the rules to live traffic, millisecond-scale.
- **Learning plane** — offline. Ledger, reviewer queue, shadow eval, calibration.

**Core architectural rule, non-negotiable, applies everywhere in the data plane:** no individual detector/check ever decides a final action. Every detector is a pure signal producer (`{source, type, score, status}`), where `status ∈ {"ok", "timeout", "disabled"}`. `status: "timeout"` means the detector hung past its deadline — treated as *unknown, not safe*, and escalates. `status: "disabled"` means the check was turned off for this bundle — excluded from fusion math entirely (never defaulted to a safe score), but still written to the ledger so a reviewer can tell "checked, clean" apart from "never checked." The **Risk Fusion and Action Engine** is the only component allowed to turn signals into an action (`block` / `redact` / `regenerate` / `flag_visible` / `flag` / `allow`). The orchestrator (`check.py`) is the only code that imports both detectors and fusion and does branching — detectors and fusion never import each other. The orchestrator also owns which detectors get called at all: a check with `status: "disabled"` in its bundle field is never invoked — that's what actually saves the cost/latency, not fusion ignoring a result after the fact.

---

## 2. Architecture — One Backend, Three Planes

```
/apps
  /api                    # single FastAPI service
    /routers
      bundles.py           # bundle CRUD — wraps control-plane resolve()/compile()
      check.py               # POST /check — the actual checking pipeline + LLM call
    /control_plane          # resolve(), compile(), policy layer logic
    /data_plane               # detectors, fusion, LLM call
    /models                    # Pydantic schemas — bundle, ledger row, etc.
    /policy                     # V1: raw YAML layer files, read straight off disk
```

Platform API, control-plane compile logic, and the data-plane checking pipeline all live in **one FastAPI (Python) service**, organized as routers, not microservices. No frontend in V1 — the whole thing is Postman/curl-testable, so there's always a demoable core even if nothing else gets built.

---

## 3. Control Plane (V1)

### 3.1 Purpose

Make safety rules data, not code — authored by humans (compliance/risk owners), merged with enforceable locking, versioned, and handed to the data plane as a single flat "bundle" it can enforce without knowing anything about policy logic.

> **Note on latency:** the control plane does not set, compute, or derive any latency/timing value. It only resolves and ships **which check tiers (T0/T1/T2) are enabled**. Timing is entirely a data-plane runtime concern.

### 3.2 Core Concepts

| Term | Meaning |
|---|---|
| **Policy layer** | A YAML file expressing rules at one altitude. V1 has two: org / tenant. |
| **Locked field** | A setting a compliance layer marks as non-loosenable by inner layers. Three shapes: **numeric threshold** (locked layer sets a bound, inner layers may only tighten), **tier boolean** (locked layer can force a tier on), and **ordered enum** (locked layer pins a floor on a small ranked set of values — e.g. `toxicity_mild_action: regenerate` locked means a tenant can't loosen it to `flag_visible`). |
| **Resolver** | Merges layers into one flat setting list, enforcing locks. |
| **Bundle** | The final compiled, versioned, hashed output the data plane reads. Compiled as **JSON**, not YAML — see §3.4. |
| **Per-check toggle** | Every individual check (secrets, injection, PII, canary, toxicity, ...) is independently enabled/disabled per bundle — not just gated by tier. A tenant or compliance layer must explicitly set a check `true` for it to run at all. No check is "always on" by default, including T0's checks. |
| **`locks:` list** | How a layer marks fields as non-loosenable, instead of inline comments. A layer lists the dotted paths of fields it's locking (e.g. `output_t1_checks_enabled.toxicity`) in a top-level `locks:` array. `compile()` rejects a layer where a `locks:` entry doesn't resolve to a field actually set in that same layer. |
| **Check tier (T0/T1/T2)** | Groupings by cost/latency profile — T0 = near-zero-cost deterministic checks, T1 = cheap parallel model checks, T2 = expensive LLM-as-judge (deferred, §4.6). No latency number is stored anywhere. Whether a tier "runs" is now derived from its per-check toggles, not a separate tier-level switch — if every check under a tier is off, that tier simply never executes. |

### 3.3 V1 Requirements

- [ ] Define policy schema — two families of fields:
  - **Check selection** (booleans, one per check that actually exists in V1 — no field for anything not yet built): `input_checks_enabled {secrets, injection, pii}`, `output_t0_checks_enabled {secrets, canary}`, `output_t1_checks_enabled {pii, toxicity}`
  - **Thresholds & enums**: `input_pii_review_threshold`, `input_pii_redact_threshold`, `output_pii_review_threshold`, `output_pii_redact_threshold`, `toxicity_regenerate_threshold`, `toxicity_block_threshold`, `toxicity_mild_action`, `fail_mode`
  - No `grounding_threshold` field for V1 — grounding isn't built, and per-decision, fields for not-yet-built checks are omitted rather than added as no-ops.
- [ ] 2 hardcoded YAML layers: `org-baseline.yaml` and one tenant layer (`support-bot.yaml`). No jurisdiction layer in V1.
- [ ] `resolve()` — merges layers, applies field-level locking via each layer's `locks:` list:
  - Numeric thresholds: locked layer sets a **max/min bound**, inner layers may only tighten
  - Per-check / tier booleans: locked layer can force a check **on**; inner layer cannot turn it off
  - **Ordered enum** (`toxicity_mild_action`): `regenerate` is stricter than `flag_visible`; a locked layer pins a floor the same way a boolean lock does
  - Rejects any layer where a `locks:` entry references a field not set in that layer
- [ ] `compile()` — runs `resolve()`, stamps version number + sha256 hash (computed over the resolved field values only, not the metadata — see §3.4), writes the resolved bundle to `/bundles/` as **JSON**, not YAML. No timing/latency field is computed or included anywhere in the bundle.
- [ ] **Compile-time guards:** reject any bundle where `input_pii_review_threshold >= input_pii_redact_threshold` or `output_pii_review_threshold >= output_pii_redact_threshold`.
- [ ] Resolver prints a clamp warning whenever it overrides a requested value (console log sufficient for V1)
- [ ] Gateway (stub) can load a bundle by name and print its resolved settings
- [ ] Manual demo: edit tenant YAML → recompile → new bundle version/hash → show diff

**V1 acceptance criteria:** same prompt run against two different bundles (support-bot vs. decision-support) produces visibly different check-tier sets, and the locking mechanism visibly blocks one attempted override.

### 3.4 Bundle Contract (control plane → data plane)

Two distinct formats, for two different audiences:

**Layer-authoring format — YAML, hand-written by a compliance/risk owner or tenant.** Values stay plain and readable; locking is expressed once, in a separate `locks:` list, rather than scattered as inline flags — a locked field's value and its lock status can't silently drift apart, since `compile()` rejects any `locks:` entry that doesn't resolve to a field actually set in that same layer.

```yaml
# jurisdiction-eu.yaml
input_checks_enabled:
  secrets: true
  injection: true
  pii: true

output_t0_checks_enabled:
  secrets: true
  canary: true

output_t1_checks_enabled:
  pii: true
  toxicity: true

output_pii_redact_threshold: 0.5
toxicity_mild_action: regenerate

locks:
  - output_pii_redact_threshold
  - output_t1_checks_enabled.toxicity
  - toxicity_mild_action
```

**Compiled bundle format — JSON, machine-only, what the data plane actually reads.** Matches the ledger's own hashing approach (`sha256(json.dumps(record, sort_keys=True))`) instead of mixing a YAML bundle with JSON ledger rows. `_meta.hash` is computed over `fields` **only** — never over the whole document — so the hash doesn't need to reference itself.

```json
{
  "_meta": {
    "policy_name": "support-bot-eu",
    "version": 7,
    "hash": "sha256:a3f9c2...",
    "source_layers": ["org-baseline", "jurisdiction-eu", "tenant-support-bot"],
    "compiled_at": "2026-09-01T10:00:00Z"
  },
  "fields": {
    "input_checks_enabled": {"secrets": true, "injection": true, "pii": true},
    "output_t0_checks_enabled": {"secrets": true, "canary": true},
    "output_t1_checks_enabled": {"pii": true, "toxicity": true},
    "input_pii_review_threshold": 0.4,
    "input_pii_redact_threshold": 0.7,
    "output_pii_review_threshold": 0.4,
    "output_pii_redact_threshold": 0.5,
    "toxicity_regenerate_threshold": null,
    "toxicity_block_threshold": null,
    "toxicity_mild_action": "regenerate",
    "fail_mode": "open"
  }
}
```

`compile()` does **not** currently reject a bundle with a null toxicity threshold, which is a real gap: `decide_t1_output()` would be comparing a score against nothing. Flagged, not yet resolved.

Input and output PII have **separate, independently-tunable threshold bands** — input PII risk is "reaches the model vendor," output PII risk is "reaches the end user," and the two stages can be tuned differently. There is no standalone `on_pii` action field — the action (allow / flag-for-review / redact) is always *derived* from which threshold band a score falls into, never separately configured.

**No tier-level `checks_enabled {t0,t1,t2}` field exists anymore.** Earlier drafts had a tier-level gate alongside per-check toggles; it was removed once every check became individually toggleable, since "does this tier run" is now fully derived from whether any of its checks are on. One consequence: there's currently no field anywhere that lets a bundle select T2 — the old `checks_enabled.t2: true` no-op-vs-reject question is moot by elimination rather than by decision, and the demo language in §5 that assumed a T2 toggle needs revisiting once T2 gets its own `output_t2_checks_enabled` field when it's actually built.

### 3.4.1 Two-Layer Worked Example (org → tenant)

V1 has exactly two layers (§3.3): `org-baseline.yaml` and `tenant-support-bot.yaml`. Full example, exercising all three lock shapes:

```yaml
# org-baseline.yaml
input_checks_enabled:
  secrets: true
  injection: true
  pii: true

output_t0_checks_enabled:
  secrets: true
  canary: true

output_t1_checks_enabled:
  pii: true
  toxicity: false

input_pii_review_threshold: 0.4
input_pii_redact_threshold: 0.8
output_pii_review_threshold: 0.4
output_pii_redact_threshold: 0.8
toxicity_regenerate_threshold: 0.5
toxicity_block_threshold: 0.85
toxicity_mild_action: flag_visible
fail_mode: open

locks:
  - input_checks_enabled.secrets
  - output_t0_checks_enabled.canary
  - output_pii_redact_threshold
```

```yaml
# tenant-support-bot.yaml
input_checks_enabled:
  secrets: false     # attempts to override a locked field — rejected, org's true wins
  pii: false          # unlocked — tenant's own choice, allowed

output_t1_checks_enabled:
  toxicity: true      # unlocked — tenant turns on what org left off

output_pii_redact_threshold: 0.6   # locked at 0.8, but 0.6 is *stricter* (lower) — allowed
toxicity_mild_action: regenerate    # unlocked — tenant's own pick
```

**Resolved (`fields` block of the compiled bundle):**
```json
{
  "input_checks_enabled": {"secrets": true, "injection": true, "pii": false},
  "output_t0_checks_enabled": {"secrets": true, "canary": true},
  "output_t1_checks_enabled": {"pii": true, "toxicity": true},
  "input_pii_review_threshold": 0.4,
  "input_pii_redact_threshold": 0.8,
  "output_pii_review_threshold": 0.4,
  "output_pii_redact_threshold": 0.6,
  "toxicity_regenerate_threshold": 0.5,
  "toxicity_block_threshold": 0.85,
  "toxicity_mild_action": "regenerate",
  "fail_mode": "open"
}
```
`fail_mode` isn't in either file's overridden fields above — the tenant simply inherits org's `open` unchanged, same as `input_pii_review_threshold`. Not locked here, since a generic support-bot use case has no compliance reason to mandate one direction; a regulated-workflow layer (not built for V1 — only two layers exist) would be the one to lock `fail_mode: closed`.

Clamp event logged: `{"field": "input_checks_enabled.secrets", "requested": false, "enforced": true, "locked_by": "org-baseline"}`.

**`resolve()` for the two-layer case** — "stricter" is field-type-dependent, so this needs a per-field-type comparator, not one generic rule:
```python
def resolve(org: dict, tenant: dict) -> tuple[dict, list[dict]]:
    merged, events = deepcopy(org), []
    locks = set(org.get("locks", []))

    for path, tenant_value in flatten(tenant).items():
        if path == "locks":
            continue
        org_value = get_path(merged, path)

        if path not in locks:
            set_path(merged, path, tenant_value)   # unlocked — tenant is free
            continue

        if is_stricter_or_equal(path, tenant_value, org_value):
            set_path(merged, path, tenant_value)   # tightening a lock is always allowed
        else:
            events.append({"field": path, "requested": tenant_value,
                            "enforced": org_value, "locked_by": "org-baseline"})
            # merged keeps org_value — no change

    return merged, events

def is_stricter_or_equal(path, new, old):
    if path.endswith("_threshold"):
        return new <= old                          # lower = fires sooner = stricter

    ENUM_RANKS = {
        "toxicity_mild_action": {"flag_visible": 0, "regenerate": 1},
        "fail_mode": {"open": 0, "closed": 1},
    }
    if path in ENUM_RANKS:
        rank = ENUM_RANKS[path]
        return rank[new] >= rank[old]               # only allowed to move up the ranking

    return new == old                               # boolean check-toggle: locked value is fixed, no "tighten" direction
```
The boolean case has no tighten-further direction — a lock on a check-toggle just pins that exact value (mandatory-on, or the rarer mandatory-off); the only question is whether the tenant's value matches it. `ENUM_RANKS` is the one place to add a field when a new ordered-enum shows up — `fail_mode` reuses the exact mechanism `toxicity_mild_action` already established, rather than each enum field getting its own bespoke comparison.

**Override/clamp event (control plane → learning plane, via audit log):**
```json
{
  "policy_name": "support-bot",
  "requested_by": "tenant",
  "field": "output_t1_checks_enabled.toxicity",
  "requested_value": false,
  "enforced_value": true,
  "locked_by_layer": "org-baseline",
  "timestamp": "..."
}
```

### 3.5 Non-Functional Requirements

- `resolve()` and `compile()` must run in well under 1s (build-time step, not a runtime hot path).
- Bundle hash must be deterministic — same inputs always produce the same hash.
- No raw tenant content (PII, prompts) ever appears in policy files or bundles.

---

## 4. Data Plane (V1)

The control plane decided the rules. The data plane applies them to *this specific request, right now*, while a user is waiting.

### 4.1 Request Path (V1)

```
user prompt
   ↓
Gateway              who is this, which bundle applies
   ↓
Input Gate           detectors only — produces signals, decides nothing
   ↓
Risk fusion (input-stage checkpoint)   ← decides: block / redact / allow
   ↓
[Semantic cache / Complexity router / Model call — see §4.6, not built for V1
 beyond a plain LLM call: prompt in, response out, BYOK]
   ↓
Output checks: T0 → T1 (T2 deferred, §4.6)  — detectors only, produce signals
   ↓
Risk fusion (output-stage checkpoint)  ← decides: allow (+ modifiers) / regenerate / block
```

### 4.2 Part 1 — Input Gate *(Finalized)*

Checks the **prompt**, before the model is called.

**Checks in scope:**

| Check | Tier | Method | Library |
|---|---|---|---|
| Secret/credential leakage | Deterministic | Regex, vendored patterns | Adapted from `detect-secrets` (Apache 2.0) — **not** a runtime dependency |
| Prompt injection | Deterministic | Regex/keyword list | Hand-rolled, no library |
| PII detection | Scored, 0–1 | NER-based | **Presidio** (`presidio-analyzer`), ~9ms warm with `en_core_web_sm` |

Each of these three checks is independently toggleable via the bundle's `input_checks_enabled {secrets, injection, pii}` — none is on by default; a layer must explicitly set it `true`. A disabled check is never invoked by the orchestrator and appears in the ledger as `status: "disabled"`, not simply absent.

**Explicitly out of scope for V1:** toxicity-on-input, off-topic classification, injection classifier (model-based), encoding/obfuscation tricks, indirect injection via RAG docs, session-state-aware signals.

**Secret patterns (vendored):**
```python
SECRET_PATTERNS = {
    "aws_key":            r'(?:A3T[A-Z0-9]|ABIA|ACCA|AKIA|ASIA)[0-9A-Z]{16}',
    "github_token":       r'(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36}',
    "slack_token":        r'xox(?:a|b|p|o|s|r)-(?:\d+-)+[a-z0-9]+',
    "openai_key":         r'sk-[A-Za-z0-9-_]*[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}',
    "jwt":                r'eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*?',
    "stripe_key":         r'(?:r|s)k_live_[0-9a-zA-Z]{24}',
    "private_key_header": r'-----BEGIN (RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----',
}
```
This exact dict is reused verbatim at T0 output (§4.3) — no reimplementation.

**Injection patterns (hand-rolled):**
```python
INJECTION_PATTERNS = [
    r"ignore (all|your|the) (previous|prior|above) instructions",
    r"disregard (the|all|your) (above|previous|prior)",
    r"you are now (a|an)?",
    r"print your system prompt",
    r"reveal your (instructions|system prompt|prompt)",
    r"forget (everything|all) (you|that)",
    r"new instructions:",
    r"act as (if|though)",
]
```

**Fusion logic — action set `block` / `redact` / `allow` only:**
```python
def decide_input(signals: list[Signal], bundle: Bundle) -> tuple[Action, bool]:
    active = [s for s in signals if s["status"] != "disabled"]   # disabled checks excluded, not defaulted safe

    if any(s["type"] == "secrets" and s["score"] >= 1.0 for s in active):
        return "block", False
    if any(s["type"] == "injection" and s["score"] >= 1.0 for s in active):
        return "block", False

    pii_signals = [s for s in active if s["type"] == "PII"]
    max_score = max((s["score"] for s in pii_signals), default=0)

    if max_score > bundle.input_pii_redact_threshold:
        return "redact", False
    elif max_score > bundle.input_pii_review_threshold:
        return "allow", True    # borderline — feeds reviewer queue
    else:
        return "allow", False
```
Compile-time guard: `input_pii_review_threshold` must be `<` `input_pii_redact_threshold`.

**Redaction execution (mechanical, never re-enters fusion):**
1. PII spans replaced with placeholders (e.g. `[CARD_1]`)
2. Un-redaction map held **in memory for that request only** — never written to the ledger
3. Redacted prompt sent to the LLM
4. On response, placeholders swapped back to real values before returning to the user

**Orchestration contract:**
```python
# input_gate.py — pure signal producer, no fusion import, no decisions
def scan(prompt: str) -> list[Signal]: ...

# risk_fusion.py — the only place an action is ever decided
def decide_input(signals: list[Signal], bundle: Bundle) -> tuple[Action, bool]: ...

# check.py — orchestrator, imports both, owns all branching
def handle_request(prompt, bundle):
    signals = input_gate.scan(prompt)
    action, review_needed = risk_fusion.decide_input(signals, bundle)

    if action == "block":
        return build_response(action, signals, review_needed)   # LLM never called
    if action == "redact":
        prompt = apply_redaction(prompt, signals)

    llm_response = call_llm(prompt)   # only reached if not blocked
    ...
```

**Ledger row shape (Input Gate stage):**
```json
{
  "request_id": "...",
  "stage": "input",
  "action": "redact",
  "review_needed": false,
  "contributing_signals": [
    {"source": "input_pii", "type": "PII", "score": 0.85, "status": "ok"},
    {"source": "input_injection", "type": "injection", "score": null, "status": "disabled"}
  ]
}
```

### 4.3 Part 5, T0 — Deterministic Output Checks *(Finalized)*

Runs against the **response**, after the model call.

| Check | Method | Notes |
|---|---|---|
| Secret/credential leakage | Same `SECRET_PATTERNS` as Input Gate | Reused verbatim, run against the response — catches the model echoing back a credential. |
| Canary token | Exact string match against a token planted in the system prompt | Maps to `block`, not `redact`. |

Both are independently toggleable via `output_t0_checks_enabled {secrets, canary}` — including these two, since T0's earlier "always on, not worth exposing as a choice" framing was superseded once every check became individually toggleable. A disabled T0 check is skipped by the orchestrator and recorded as `status: "disabled"` in the ledger.

**PII (including checksummed IDs) is not part of T0** — it moved entirely into T1 (§4.4) as a single unified check. This keeps T0 strictly deterministic with no model involvement anywhere in the check.

**Explicitly deferred:** Aadhaar/PAN validation (Verhoeff), blocklist/forbidden terms, email/phone as a T0-certain signal (genuinely unsolved — no checksum-style validity check exists to distinguish legitimate disclosure from a leak).

**Fusion logic:**
```python
def decide_t0_output(signals: list[Signal], bundle: Bundle) -> Action:
    active = [s for s in signals if s["status"] != "disabled"]
    if any(s["type"] == "canary_leak" and s["score"] >= 1.0 for s in active):
        return "block"   # entire system prompt exfiltrated — no span to redact, no partial fix
    if any(s["type"] == "secrets" and s["score"] >= 1.0 for s in active):
        return "redact"
    return "allow"
```
Canary is checked first — a canary leak has no "span" to mask and is evidence of a successful jailbreak, so `block` discards the whole response rather than attempting partial redaction. This output is combined with T1's decision at Part 6 (§4.5) — T0 doesn't resolve across tiers itself.

### 4.4 Part 5, T1 — Parallel Small-Model Checks

Detectors run in parallel (`asyncio.gather`); hard deadline on the join — a hung detector produces a signal with `status: "timeout"` and `score: null`, meaning *unknown*, not *safe*, pushing toward escalation. This is distinct from `status: "disabled"` (check turned off on purpose, excluded from fusion entirely rather than escalated). Models warmed at startup.

| Check | Status | Notes |
|---|---|---|
| PII (NER + checksummed) | **In scope.** | The single, complete PII check for the output stage — absorbs checksum-valid IDs (Presidio's `CreditCardRecognizer`, Luhn, `score: 1.0`) and NER-based detections (names, addresses, `score: 0–1`) under one two-threshold band, no special-casing by source. |
| Toxicity classifier | **In scope, banded, mild action tenant-selectable and lockable.** | Severe toxicity → `block`, unconditionally. Mild toxicity's action is a **choice**: `regenerate` (retry once with stricter instructions) or `flag_visible` (response shown with a fixed disclaimer, no retry). Retry cap on `regenerate`: **1** — still toxic on re-check → `block`. |
| Grounding check | **Deferred.** | Needs RAG in scope first. |
| Injection classifier (output-side) | **Deferred to a future version.** | Two distinct meanings exist — propagation (output contains payload for a downstream consumer) vs. success (did an input-side injection actually work) — not designed further for V1. |

**Bundle fields:**
```yaml
output_t1_checks_enabled:
  pii: true
  toxicity: true
output_pii_review_threshold: 0.4
output_pii_redact_threshold: 0.7
toxicity_regenerate_threshold: <TBD — numeric value not yet set>   # null in the compiled JSON bundle
toxicity_block_threshold: <TBD — numeric value not yet set>        # null in the compiled JSON bundle
toxicity_mild_action: regenerate   # or "flag_visible" — lockable
```
Compile-time guard: `output_pii_review_threshold` must be `<` `output_pii_redact_threshold`. **Not yet a guard, flagged as a gap:** `compile()` doesn't reject a `null` toxicity threshold, even though `decide_t1_output()` needs a real number to compare against.

**`flag_visible` — a new action, distinct from `flag`:** `flag` means "uncertain; user still gets a response, a human sees it later" — silent to the user. Mild toxicity needed the opposite: the user is shown the response **and told about it**. `flag_visible` keeps `flag` itself consistently silent everywhere else it's used.

**V1 disclaimer text (fixed, not a bundle field yet):**
> "This response may have toxic intent, apologies for this."

**`decide_t1_output(signals, bundle, retried=False)`** — resolves only among T1's own signals (PII, toxicity):
```python
def decide_t1_output(signals: list[Signal], bundle: Bundle, retried: bool = False) -> tuple[Action, bool]:
    action = "allow"
    review_needed = False

    # --- Toxicity ---
    tox_signals = [s for s in signals if s["type"] == "toxicity" and s["status"] != "disabled"]
    tox_score = max((s["score"] for s in tox_signals), default=0)
    tox_unknown = any(s["status"] == "timeout" for s in tox_signals)   # timeout only — "disabled" already filtered out above

    if tox_unknown:
        action = "block" if retried else "regenerate"   # timeout = unknown, not safe — escalate
    elif tox_score >= bundle.toxicity_block_threshold:
        action = "block"
    elif tox_score >= bundle.toxicity_regenerate_threshold:
        if bundle.toxicity_mild_action == "flag_visible":
            action = "flag_visible"
        else:
            action = "block" if retried else "regenerate"   # retry cap: 1

    # --- PII (only evaluated if toxicity hasn't already forced block/regenerate/flag_visible) ---
    if action == "allow":
        pii_signals = [s for s in signals if s["type"] == "PII" and s["status"] != "disabled"]
        pii_score = max((s["score"] for s in pii_signals), default=0)

        if pii_score > bundle.output_pii_redact_threshold:
            action = "redact"
        elif pii_score > bundle.output_pii_review_threshold:
            review_needed = True

    return action, review_needed
```

### 4.5 Part 6 — Risk Fusion (Output Stage)

V1's output-stage fusion combines **T0 + T1 only** (T2 deferred, §4.6; grounding absent until RAG is in scope). Practical V1 signal set: canary (T0), secrets (T0), PII (T1), toxicity (T1).

**Composable modifiers, not a strict single-winner ladder:** `block` and `regenerate` are whole-response outcomes and preempt everything else outright. `redact`, `flag_visible`, and silent `flag` can legitimately apply to the *same* response at once (e.g. a toxicity disclaimer and an unrelated PII redaction) — these compose as modifiers on a base `allow` rather than competing under a single winner.

```python
def decide_output_fusion(t0_action: Action, t1_action: Action, t1_review_needed: bool, bundle: Bundle) -> dict:
    if t0_action == "block" or t1_action == "block":
        return {"action": "block", "modifiers": []}
    if t1_action == "regenerate":
        return {"action": "regenerate", "modifiers": []}

    modifiers = []
    if t0_action == "redact" or t1_action == "redact":
        modifiers.append("redact")
    if t1_action == "flag_visible":
        modifiers.append("flag_visible")
    if t1_review_needed:
        modifiers.append("flag")

    return {"action": "allow", "modifiers": modifiers}
```

**Modifier order-independence (explicit assumption for V1):** `redact` edits the response body, `flag_visible` appends a fixed disclaimer, `flag` never touches the response body — none read each other's output, so application order doesn't matter for the three modifiers that exist today.

**Output-stage redaction execution:**
1. Collect spans from **both** T0 (secrets) and T1 (PII) into a single list before masking — one combined pass, not two sequential passes (avoids offset-shifting bugs)
2. Replace all collected spans with placeholders in one pass
3. If `flag_visible` is present in `modifiers`, append the fixed disclaimer to the redacted response
4. No un-redaction step — output-stage redaction is the last thing that happens before the user sees the response

**Ledger row shape (Output stage):**
```json
{
  "request_id": "...",
  "stage": "output",
  "tier_reached": "t1",
  "action": "allow",
  "modifiers": ["redact", "flag_visible"],
  "contributing_signals": [
    {"source": "t1_pii", "type": "PII", "score": 0.81},
    {"source": "t1_toxicity", "type": "toxicity", "score": 0.42}
  ]
}
```
Note: input-stage rows keep the older flat `action` + `review_needed` shape (§4.2) — the two stages have genuinely different row shapes, since only output rows compose modifiers.

### 4.6 Explicitly Deferred (Data Plane, not V1)

- **Parts 2–4 (Semantic Cache, Complexity Router, Model Call + Stream Monitor)** — deprioritized placeholders. Known constraints carried forward for whenever they're picked up: cache key must include `(tenant_id, entitlement_scope, policy_hash, embedding)`; never cache a PII-flagged response; `allow_downrouting` is a lockable field; BYOK key never logged.
- **T2 (LLM-as-judge)** — future version. No schema field exists for it at all in V1 (per-check fields are only added for checks that actually exist), so the earlier "no-op vs. explicit rejection" question is moot by omission rather than resolved by decision. When T2 is built, it gets its own `output_t2_checks_enabled` field and that question should be revisited for real.
- **Part 7 (Session State)** — future version. **V1 treats every request independently** — no server-held conversation history, no `flag_count` escalation, no tampered-context/`terminate_session` handling. A reference design (context ownership, hash-based tamper detection, `flag_count` trajectory signal) exists in the full Data Plane PRD for whenever this is picked back up, but nothing from it is built for V1.
- **Injection classifier on output** — future version (see §4.4).
- **Grounding check** — future version, blocked on RAG being in scope.

### 4.7 Non-Functional Requirements

- No individual check ever produces a final action — enforced by code review, not just convention.
- No raw tenant content (PII values, full prompts) ever written to the ledger — types and scores only.

---

## 5. Infra & Platform (V1)

*Goal: the fallback tier — proves the core mechanism works with the least possible engineering overhead. No frontend required.*

- [ ] Single FastAPI service, running locally
- [ ] Policy files are local, plain YAML on disk (`org-baseline.yaml` + one tenant layer) — no database in V1
- [ ] `resolve()` / `compile()` as specified in §3.3, bundle written to a local JSON file with version + hash
- [ ] `POST /check` — takes a prompt + bundle name, runs the checking pipeline, returns the decision
- [ ] No persistence beyond the local filesystem
- [ ] A short README/Postman collection — import, hit send, get a response, no live coding under pressure

**V1 acceptance criteria:** from Postman, `POST /check` with a prompt and bundle name returns a real decision (allow/redact/regenerate/block) with which check tiers ran — and editing the tenant YAML + recompiling demonstrably changes the bundle's resolved values, provably enforcing a lock.

**Tooling:** FastAPI (backend), Postman importing FastAPI's auto-generated OpenAPI spec (API testing), plain YAML + local JSON (policy/bundle storage). No hosted DB, no frontend, no Turborepo/React yet — all of that is V2+.

**Non-functional:** no credentials ever committed to the repo (`.env` + `.gitignore` from V1).

---

## 6. Learning Plane (V1 scope)

*Offline, batch, asynchronous — nothing here runs while a user waits.*

**Real, built for V1:**
- **Audit ledger**, hash-chained (`record["hash"] = sha256(json.dumps(record, sort_keys=True))` over `prev_hash` + record contents) — records **types and scores, never values**. Tamper with one record and every hash after it breaks, provable on stage.
- **Calibration sweep script** producing a tradeoff curve (false-positive rate / false-negative estimate / escalation rate / cost vs. candidate threshold values) — calibration *shows* the tradeoff, a human picks a point on it. Doesn't decide automatically.

**Stubbed, not fully built:**
- **Shadow eval** — offline script over a fixed labelled set of ~200 stored interactions, rather than live 2% sampling.
- **Reviewer queue** — a small table with approve/reject buttons, or a CSV; **not a separate table from the ledger** — it's a filtered view (`WHERE review_needed = true` on input rows, or `WHERE 'flag' IN modifiers` on output rows per §4.5's schema).

**The human-gated loop (design reference, not fully wired for V1):** calibration proposes a threshold change → a human approves (never automatic) → control plane produces a new policy version/hash → shadow deploy compares old vs. new → data plane enforces the new version → new decisions flow back to the ledger. The human-approval gate is the point: without it, reviewer fatigue can quietly relax the system's own thresholds over time.

---

## 7. V1 Explicitly Out of Scope (aggregated across all planes)

- Real authentication / RBAC on who can edit which policy layer
- A frontend of any kind (Postman/curl only — frontend is Infra PRD's V2)
- Hosted database (local YAML/JSON only — Postgres is Infra PRD's V2)
- Jurisdiction policy layer, rollback, real approval workflow (all Control Plane PRD's V3)
- Session state / multi-turn conversation handling (§4.6)
- T2 (LLM-as-judge), grounding check, output-side injection classifier (§4.6)
- Semantic cache, complexity router (§4.6)
- Any latency *budget* field or tenant-facing latency configuration — this system speaks only in check tiers (on/off), never milliseconds, anywhere
- Multi-region deployment, autoscaling, production-grade RBAC/SSO

---

## 8. Open Questions Carried Into V1

- Numeric values for `toxicity_regenerate_threshold` / `toxicity_block_threshold` — not yet set (currently placeholders).
- `input_pii_review_threshold` / `input_pii_redact_threshold` V1 defaults — `0.4` / `0.7` proposed, not locked against real traffic (none exists yet).
- `en_core_web_sm` vs. `en_core_web_lg` for Presidio — accuracy/latency tradeoff not yet benchmarked.
- No schema field currently lets a bundle select T2 at all (§4.6) — resolved by omission for now; needs a real `output_t2_checks_enabled` field and a no-op-vs-rejection decision once T2 is actually built.
- `compile()` doesn't yet reject a bundle with `toxicity_regenerate_threshold` / `toxicity_block_threshold` left `null` — `decide_t1_output()` would silently compare against nothing (§3.4, §4.4).
- Which fields get locked at the org-baseline layer for V1 — proposed minimally: at least one PII threshold and one tier.
- Who "owns" a policy in the fictional company, for the governance slide — needs a name/role before demo.
