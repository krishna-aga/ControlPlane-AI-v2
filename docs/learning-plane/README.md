# Learning Plane

Offline, batch, asynchronous — nothing here runs while a user waits. The
ledger shipped first; this doc originally described a much smaller surface
("only the ledger is built"). That's no longer accurate — the rest of V1's
learning plane (reviewer queue, shadow deploy, the synthetic eval, calibration
sweep, and the calibration → new-policy-version loop) is now built too. Audit
Bot is the one piece still deliberately deferred — see the bottom of this doc.

Source: `apps/api/db/migrations/0001_init.sql` + `0003_learning_plane.sql`
+ `0004_mock_eval_only.sql` (schema), `apps/api/routers/ledger.py`
(list/verify/review/summary), `apps/api/routers/learning.py` (mock eval,
calibration, shadow deploy, reviewer queue), `apps/api/learning_plane/`
(`replay.py`, `mock_eval.py`, `metrics.py`), and
`apps/web/src/screens/LearningPlane.jsx` on the frontend.

## Required pages — a literal checklist, not just backend mechanics

An early pass built the backend mechanics for this plane correctly but never
rendered a ledger view at all — a gap only caught because the PRD was
revised to spell out, explicitly, which pages must exist. Kept here so it
doesn't regress again. All four live as tabs on one screen
(`LearningPlane.jsx`, route `/agents/:agentId/learning`):

1. **Audit Ledger** — browse/search real ledger rows. KPIs: total logs,
   breakdown by action, breakdown by stage, most recent entry timestamp.
2. **Reviewer Queue** — flagged rows, approve/reject. KPIs: total flagged,
   pending, approved, rejected.
3. **Calibration + Metrics** — combined on one page: the calibration sweep
   chart, the FP-rate/FN-rate summary, and a filterable, scrollable table of
   all 100 individual mock cases (prompt, category, ground truth, expected
   vs. actual, a ✓/✗ match column) — all reading the same mock dataset.
   **Demo Org only** — see below; hidden entirely for every real agent.
4. **Policy Versions** — version list, production picker, shadow-deploy
   trigger + diff table, all in one place.

### "Calibration + Metrics" only exists for the Demo Org

An earlier pass showed this tab — including the mock test-case table — on
*every* agent's Learning Plane, including real ones a real org creates for
its own use (e.g. a "support-bot"). That's wrong: the 100 mock cases are
hand-authored against one exact policy (§5 below — PII bands 0.4/0.8,
`output_pii_redact_threshold` locked at 0.8, toxicity off). Showing that
tab, or those numbers, against a real agent running a different policy would
silently misrepresent what the FP/FN rates and calibration curve mean for
that agent.

Fixed with a single, auto-seeded **Demo Org** (`organizations.is_demo`, at
most one row — `learning_plane/demo_seed.py` creates it plus one "Demo
Agent" idempotently on every startup, running exactly the canonical policy
the mock dataset assumes). `POST /auth/demo-login` hands out a token for it
with no credential check — there's nothing behind it worth protecting — and
the frontend's "View demo" button on the login screen calls it directly, no
signup needed. `LearningPlane.jsx` reads `is_demo` off the logged-in
organization and only renders the "Calibration + Metrics" tab (and includes
it in the tab list at all) when true; every mock-eval endpoint in
`routers/learning.py` independently re-checks `org["is_demo"]` server-side
and 403s otherwise, so this isn't just a hidden button — a real org calling
those endpoints directly gets rejected too. Full details:
[multi-tenancy & auth](../cross-cutting/multi-tenancy-and-auth.md#the-one-deliberate-exception-to-no-seed-data-the-demo-org).

## Why the ledger exists at all

Every decision the data plane makes — block, redact, allow, with which
signals contributed — gets written as an append-only, hash-chained row. The
point isn't just logging; it's **provable tamper-evidence**: if any row is
altered after the fact, every hash after it in the chain breaks, and that's
checkable independently of trusting the system that wrote it.

## Two row shapes, enforced at the database level

Input-stage and output-stage decisions carry genuinely different information
(a flat `action` + `review_needed` boolean vs. `action` + a `modifiers[]`
array), and the two are never allowed to blur into one shape:

```sql
CHECK (
    (stage = 'input'  AND tier_reached IS NULL AND modifiers IS NULL AND review_needed IS NOT NULL)
    OR
    (stage = 'output' AND review_needed IS NULL AND modifiers IS NOT NULL)
)
```

This is a real, enforced constraint, not just a convention documented in a
comment — a row that mixes the two shapes fails to insert, full stop. It would
have been just as easy (and much more common) to leave this as an
application-level assumption that quietly drifts as the code around it
changes; putting it in the schema means it can't.

## The hash chain

```sql
prev_hash TEXT,       -- null only for the very first row ever written
hash      TEXT NOT NULL,
```

```python
def _ledger_hash(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()

async def _write_ledger_row(conn, **fields):
    prev_hash = await get_last_ledger_hash(conn)
    hash_ = _ledger_hash({"prev_hash": prev_hash, **fields})
    return await insert_ledger_row(conn, prev_hash=prev_hash, hash_=hash_, **fields)
```

Each row's hash covers its own contents plus the previous row's hash — the
standard blockchain-style chain construction, applied to an audit log instead
of a currency ledger. It was verified by an independent script that pulls
every row straight from Postgres, recomputes each hash from scratch, and
confirms it against what's stored, and that each row's `prev_hash` actually
matches the row before it — the tamper-evidence property was checked, not
just asserted to exist.

## Example rows

```json
// input stage
{
  "request_id": "fd98358f-...", "stage": "input", "action": "redact",
  "review_needed": false, "modifiers": null,
  "contributing_signals": [
    {"source": "input_pii", "type": "PII", "score": 0.85, "status": "ok"},
    {"source": "input_injection", "type": "injection", "score": null, "status": "disabled"}
  ],
  "prev_hash": "c4d59bc3...", "hash": "d3d2736c..."
}
```

```json
// output stage
{
  "request_id": "fd98358f-...", "stage": "output", "tier_reached": "t1",
  "action": "allow", "modifiers": ["redact", "flag_visible"],
  "contributing_signals": [
    {"source": "t1_pii", "type": "PII", "score": 0.81, "status": "ok"},
    {"source": "t1_toxicity", "type": "toxicity", "score": 0.42, "status": "ok"}
  ],
  "prev_hash": "d3d2736c...", "hash": "e15bdfd6..."
}
```

## What never goes in the ledger

`contributing_signals` carries **types and scores only** — never the actual
detected string. A PII hit is recorded as `{"type": "PII", "score": 0.85}`,
never the name or card number that triggered it. The same rule applies to
redaction's own bookkeeping: the un-redaction map (see
[redaction.md](../data-plane/redaction.md)) that lets a user see their own
data restored in a response lives only in a local variable for the life of
one request — it's never passed to the ledger writer at all.

## `GET /ledger` — list and hash-chain verify

Closes what used to be an open question: there's now a real endpoint to list
ledger rows (filterable by `agent_id`, `review_needed`, `flagged`, with
`limit`/`offset`) and to verify the whole chain on demand —
`GET /ledger/verify` recomputes every row's hash from its own contents plus
the previous row's stored hash and reports the first row where they diverge,
or `{"ok": true}`. Verified against the real Neon ledger.

One real bug this caught while building it: asyncpg decodes the `request_id`
column back into a `uuid.UUID` object, but the hash was originally computed
over `str(uuid4())` *before* the row was ever inserted (see `data_plane/pipeline.py`).
Recomputing the hash straight from the fetched row would `str()` a `UUID`
object into a different string than the one that was actually hashed —
every single row would look "tampered" even though nothing had changed.
Fixed by explicitly `str()`-ing `request_id` back before rehashing.

`GET /ledger/summary?agent_id=` backs the Audit Ledger page's KPI row —
total row count, a `GROUP BY action` breakdown, a `GROUP BY stage`
breakdown, and `MAX(created_at)` — all computed server-side rather than
paginating the full table client-side to count it.

## Multi-version bundle storage — the prerequisite for everything below

`bundles` gained two columns: `is_production BOOLEAN` and `label TEXT`
(migration `0003_learning_plane.sql`). Multiple compiled versions can now
exist per agent simultaneously, each independently addressable — not just a
linear "latest wins" history. `POST /check` loads whichever version is
flagged `is_production`, never simply the most recent one.

The existing policy-edit endpoints (`POST /policies/org`,
`POST /agents/{id}/policy`) keep today's behavior: compiling auto-promotes
the new bundle to production immediately (`promote: true` by default), so
editing policy in the ordinary screens still takes effect right away. The one
caller that opts out is the calibration flow below, which compiles with
`promote: false` — the new version is stored but sits alongside the existing
production bundle until a human explicitly promotes it:

```
GET  /agents/{id}/bundles                          # version list: label, hash, is_production, compiled_at
POST /agents/{id}/bundles/{bundle_id}/promote       # flips production, transactionally unsets the old one
```

## Shadow deploy — offline replay, not live traffic

Answers "if we switched to policy version B, what would change?" without
touching live traffic or re-running any detector. It replays every
**already-stored** output-stage ledger row for an agent through the same
pure fusion functions the live pipeline uses (`decide_t0_output`,
`decide_t1_output`, `decide_output_fusion`), once under each of two selected
bundle versions' resolved fields, and diffs the results:

```python
# learning_plane/replay.py
def replay_output_decision(t0_signals, t1_signals, bundle_fields, retried=False) -> dict:
    t0_action = fusion.decide_t0_output(t0_signals)
    t1_action, t1_review_needed = fusion.decide_t1_output(t1_signals, bundle_fields, retried=retried)
    return fusion.decide_output_fusion(t0_action, t1_action, t1_review_needed, bundle_fields)
```

Because it only replays stored signals, this can only meaningfully compare
versions that differ in **threshold values on checks both versions already
ran** — it can't evaluate a candidate that enables a check the compared
version never invoked, since no signal was ever recorded for it. That's a
real scope boundary (documented in the original PRD draft), not an
oversight. `POST /agents/{id}/shadow-deploy` takes `{version_a, version_b}`
(neither has to be production) and returns a per-row table plus a headline
"N out of M decisions changed" count.

## Reviewer queue — a label on the ledger, not a new table

`GET /learning/reviewer-queue?agent_id=` is a thin filter over the same
`list_ledger` query the plain `GET /ledger` uses: input rows where
`review_needed = true`, output rows where `'flag'` is in `modifiers`. A
reviewer's decision is written directly onto the existing ledger row —
`review_verdict` (`"approved"` or `"rejected"`), `reviewed_by`,
`reviewed_at` — via `POST /ledger/{id}/review`. **No downstream wiring on
purpose:** approving or rejecting doesn't trigger anything automatically (no
auto-adjusted thresholds, no auto-recalibration). It's purely a label that
feeds the FP-rate metric below — consistent with the human-gated design
throughout this plane, where a human always takes the next step deliberately.

## The 100-case mock eval — no live execution, ever

**This redesigned a first attempt that burned real API quota.** The
original version ran all 100 cases through the real `/check` pipeline (real
Gemini call per case) to produce ground truth. Two full-run attempts against
a free-tier Gemini key both failed to complete — the first crashed on an
unrelated transient Neon connection blip after a batch of real successes and
429s; the second, after fixing that, ran until the account's **daily** quota
was fully exhausted (confirmed via the API's own "You exceeded your current
quota" message — not a per-minute limit a retry could absorb). See
`.agents/questions/CLOSED_QUESTIONS.md` for the full incident.

**The fix wasn't a retry strategy — it was removing live execution from the
feature entirely.** `synthetic_test_set_100.json`
(`apps/api/learning_plane/data/` — copied out of `.agents/` so the app
doesn't depend on that directory sticking around) is now fully self-contained
mock data: every case ships a hand-authored `mock_response` and pre-authored
`mock_signals` in the real signal shape (`{source, type, score, status}`),
authored against the actual org-baseline thresholds in use (0.4/0.8 PII
bands) so the fusion math produces the intended action for real, not against
placeholder numbers. Toxicity is included per case only as
`{"type": "toxicity", "score": null, "status": "disabled"}` — the org
policy has that check off, and a disabled check correctly contributes
nothing rather than being defaulted to "safe."

`learning_plane/mock_eval.py` feeds those stored signals through the exact
same fusion functions the live pipeline uses — `decide_input`,
`decide_t0_output`, `decide_t1_output`, `decide_output_fusion` — and nothing
else:

```python
# learning_plane/mock_eval.py — the only "execution" here is arithmetic
def evaluate_case(case: dict, bundle_fields: dict) -> tuple[str, list[str]]:
    by_type = {s["type"]: s for s in case["mock_signals"]}
    secrets, injection = by_type.get("secrets", ...), by_type.get("injection", ...)
    pii, toxicity = by_type.get("PII", ...), by_type.get("toxicity", ...)

    input_action, _ = fusion.decide_input([_sig(secrets, "secrets"), _sig(injection, "injection")], bundle_fields)
    if input_action == "block":
        return "block", []

    t0_action = fusion.decide_t0_output([_sig(secrets, "secrets")])
    t1_action, review = fusion.decide_t1_output([_sig(toxicity, "toxicity"), _sig(pii, "PII")], bundle_fields)
    fused = fusion.decide_output_fusion(t0_action, t1_action, review, bundle_fields)
    return fused["action"], fused["modifiers"]
```

No detector call, no LLM call, no ledger write — the mock cases never touch
the real `ledger` table, which stays a record of genuine traffic only. This
is pure, synchronous, sub-millisecond computation, so there's no "run" to
trigger and wait on: `GET /agents/{id}/mock-eval` (and its
`/metrics`/`/calibration` siblings) recompute fresh against whichever bundle
is currently production for that agent, every time they're called.
Verified against all 100 cases: recomputing every one and comparing against
its authored `correct_base_action`/`correct_modifiers` produces **zero
mismatches** — the fusion logic and the hand-authored ground truth agree
exactly.

### Metrics — FN-rate, FP-rate (no latency)

```
GET /learning/agents/{id}/mock-eval/metrics
```

- **FN-rate** — true-allow-only (PRD's term): cases where the recomputed
  action was `allow` with zero modifiers, i.e. nothing already caught it.
  FN-rate = (eligible cases where `ground_truth_risky`) / (eligible cases).
  Anything already flagged/redacted/blocked is excluded by definition — it
  was caught, not missed.
- **FP-rate** — ground-truth comparison on cases fusion flagged, redacted,
  or blocked: how many of those were actually fine per the hand-authored set.
- **No latency metric.** The earlier design's p50/p95 came from real
  pipeline timing; once live execution was removed there was nothing genuine
  left to measure, and fabricating a placeholder number was explicitly
  rejected rather than quietly added back.

**Important framing:** this is *"false-negative rate on a hand-authored mock
test set,"* not *"estimated false-negative rate via independent judge"* and
not *"measured on live traffic."* It only measures whether the fusion logic
catches the specific cases someone thought to test, using scores assigned by
hand — not unpredictable real-traffic blind spots, and not validated against
what a real model/detector would actually produce. That broader claim is
what Audit Bot (below) is for, and real-pipeline validation against this
mock set is a separate, deliberate, manual exercise for later — never
something any script or page here triggers automatically.

### Calibration — per check, not one blended curve

```
GET /learning/agents/{id}/mock-eval/calibration
```

Revised from an earlier single combined chart to an **isolated per-check
comparison**, matching the PRD's per-check ground truth: at a candidate
threshold, take one case's own stored mock-signal score for one specific
check, decide fire/no-fire at that threshold, and compare directly against
that case's own `ground_truth_checks[check]` — never the whole-row
`correct_base_action`. A case where ground truth is `true` and the signal
doesn't fire is a false negative *for that check*; ground truth `false` and
it fires anyway is a false positive.

The response is `{"continuous": {...}, "binary": {...}}`:

- **3 continuous checks get a real sweep**, each over its own bundle
  threshold field and range: `output_pii` → `output_pii_redact_threshold`
  (0.8–1.0, step 0.02 — narrowed to respect the org-baseline lock,
  tighten-only at 0.8, so every swept value is one the resolver would
  actually let an agent enforce); `input_pii` →
  `input_pii_redact_threshold` and `output_toxicity` →
  `toxicity_regenerate_threshold` (0.3–0.9, step 0.05 each — neither field
  is locked in the demo policy, so they use the PRD's own generic
  illustrative range instead of the locked-only 0.8–1.0 range that doesn't
  apply to them). Each point is pure dataset arithmetic — no bundle lookup,
  no ledger read, nothing beyond the case's own pre-authored mock signal.
- **4 binary checks get one FN/FP number, not a curve**:
  `input_secrets`, `output_secrets`, `input_injection`, `output_canary` all
  fire at a fixed cutoff (`score >= 1.0`, PRD §5.1 — regex/exact-match, no
  tunable bundle threshold to sweep at all).

**The known coverage gap (PRD §3) shows up honestly, not as a bug:**
`input_pii`, `input_secrets`, `output_secrets`, and `output_canary` have
**zero planted true-positive cases** in the 100-case set. `input_pii`'s FN
curve is `None` (undefined, 0/0) at every threshold — its FP curve still
renders, since there are true-negative cases to false-alarm on. The three
zero-positive binary checks report `fn_rate: null`, which the frontend
renders as **"n/a (untested)"** per PRD §4's exact phrasing, never a
misleadingly clean 0%. `input_injection` (8 real planted cases) is the one
binary check with a genuinely meaningful FN/FP pair.

The frontend (`screens/LearningPlane.jsx`'s `CalibrationMetricsTab`) renders
one section per continuous check, each with **two separate charts** side by
side — FN-vs-threshold and FP-vs-threshold — rather than one chart with two
lines. `components/CalibrationChart.jsx` was generalized from a fixed
two-line chart into a single-series chart parameterized by `metricKey`
(`"fn_rate"` | `"fp_rate"`), reused for both charts of all three checks; the
4 binary checks render as plain number cards below the charts. Still a
small inline-SVG chart, no charting library.

### Calibration → new policy version (the human-gated loop, half-wired)

**Scoped to `output_pii` only**, per PRD §6.1 (it never generalizes this
flow to other fields) — the `input_pii`/`output_toxicity` charts are
read-only, with no "Create New Policy" action wired to their points.
Picking a point on the `output_pii` chart and clicking "Create New Policy"
opens a confirm view: the org-baseline policy shown read-only (so the user
can see what floor/locks apply), the agent's own policy shown editable with
the candidate threshold pre-filled, and a **proactive** clamp warning —
`POST /agents/{id}/calibration-preview` dry-runs the real `resolve()`
against the candidate value and reports whether org-baseline would clamp it,
*before* the user commits, not as a surprise after. Confirming calls the
same `POST /agents/{id}/policy` endpoint everything else uses, just with
`promote: false` and a label — the new version lands in that agent's version
list, and from there the normal shadow-deploy/promote mechanics above apply.
This deliberately stops short of auto-promotion: calibration proposes, a
human still has to separately decide to make it live.

## Deferred, and why

**Audit Bot.** The independent-judge mechanism for estimating false-negative
rate on real, unpredictable traffic (as opposed to the hand-authored mock set
above, which never touches a live model). Not built — it needs meaningful API budget (every audit is a
separate LLM call, run at scale against sampled live traffic), which isn't
available right now. Documented as a design reference and a future-work
proposal only; see `.agents/Learning_Plane_PRD_Draft.md` §7 for the full
system prompt, ledger row shape, and external-facing proposal text if this
gets picked up later.
