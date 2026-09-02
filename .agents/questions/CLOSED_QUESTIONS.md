# Closed Questions

Questions that were once in `OPEN_QUESTIONS.md`, now resolved. Kept here rather than
deleted so the history of *why* a direction was picked isn't lost — matches
`.agents/decisions/DECISIONS.md`'s standard of recording how something was decided,
not just what the final state is. When a question in `OPEN_QUESTIONS.md` gets
resolved, move its entry here (append the resolution) rather than deleting it.

## No LLM vendor chosen for `call_llm()`

**Originally opened:** while building the `/check` orchestrator — `data_plane/llm_client.py`
was a deterministic mock (BYOK per PRD §4.1), no provider picked, nothing had
actually called a real model yet.

**Resolved:** Gemini, via a direct REST call (`httpx`, no SDK). User provided a
`GEMINI_API_KEY`. Falls back to the old mock automatically when the key isn't set.
See `.agents/decisions/DECISIONS.md` §8 and `docs/cross-cutting/llm-integration.md`
for the full writeup — canary token embedded in a real system instruction, retry path
now sends a real stricter-safety instruction on regenerate, verified without spending
API quota via a fully mocked `httpx` transport before the user's first live call.

## `LOCATION` PII detection: excluded entirely, or scored down instead of dropped?

**Originally opened:** Presidio's `LOCATION` entity fires on any place name at a flat
0.85 confidence — "what is the capital of France?" would sit above the redact
threshold if included. Excluding it entirely avoided that false positive, but real
usage then showed a home address ("...42 MG Road, Dehradun, Uttarakhand 248001") went
completely undetected because of the same exclusion — a genuine precision/recall
tradeoff with no free fix.

**Resolved:** put directly to the user as three options (keep excluded / re-include
down-weighted / re-include at full confidence). **Decision: re-include `LOCATION` at
full confidence.** Catching real addresses reliably was judged more important than
avoiding false positives on ordinary geography questions. Accepted, confirmed
consequence: a message like "what is the capital of France?" will now also flag
"France" as PII — this is a known cost of the choice, not a bug. See
`.agents/decisions/DECISIONS.md` §10 and `docs/data-plane/detectors.md`.

## `GET /ledger` (list + hash-chain verify) wasn't built yet

**Originally opened:** the ledger was written to correctly and its integrity had been
checked manually via a throwaway script, but there was no actual endpoint to list
ledger rows or run that verification on demand.

**Resolved:** built as part of the Learning Plane implementation
(`.agents/Learning_Plane_PRD_Draft.md`) — `GET /ledger` (filters: `agent_id`,
`review_needed`, `flagged`, `limit`/`offset`) and `GET /ledger/verify` (walks the
whole chain, recomputes every row's hash from its own contents + the previous row's
stored hash, returns the first broken row if any). Verified against the real Neon
ledger (48+ real rows accumulated from manual testing) — `ok: true`. One real bug
caught while building it: `request_id` comes back from asyncpg as a `uuid.UUID`
object, but the hash was originally computed over `str(uuid4())` before insertion —
recomputing with the UUID object's default string form would have mismatched every
row. Fixed by `str()`-ing it back before rehashing. See
`apps/api/routers/ledger.py` and `docs/learning-plane/README.md`.

## Synthetic eval quota — Gemini free-tier daily quota blocked a full 100-case run

**Originally opened:** the first build of the 100-case eval ran every case through
the real `/check` pipeline (real Gemini calls per case). A partial run succeeded (4
cases, real responses, real ledger rows) before the account's free-tier **daily**
quota was fully exhausted — confirmed via the API's own "You exceeded your current
quota" message, not just a per-minute limit, so retry-with-backoff couldn't recover
it. Two full run attempts were made; neither completed.

**Resolved:** not "wait for quota to reset" — the PRD itself was revised to remove
live execution from this feature entirely. `synthetic_test_set_100.json` was
regenerated as fully self-contained mock data (`mock_response` + pre-authored
`mock_signals` per case, in the real `{source, type, score, status}` shape).
`learning_plane/mock_eval.py` replaced `eval_runner.py`: it feeds each case's stored
mock signals through the real fusion functions (`decide_input`, `decide_t0_output`,
`decide_t1_output`, `decide_output_fusion`) and nothing else — no detector call, no
LLM call, no ledger write, ever, in the calibration/FN-rate/metrics path. Verified
against all 100 cases: 0 mismatches between recomputed `(action, modifiers)` and
each case's authored `correct_base_action`/`correct_modifiers`. This structurally
can't burn API quota again, regardless of how many times it's run. `data_plane/llm_client.py`'s
retry-with-backoff on 429 was kept (it's a legitimate general resilience
improvement for the real, live `/check` endpoint) but is no longer load-bearing for
this feature. See `docs/learning-plane/README.md`.
