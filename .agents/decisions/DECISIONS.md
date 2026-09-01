# Engineering Decisions & Correctness Notes

ControlPlane.ai's spec (`.agents/PRD_V1_Consolidated.md`) and build plan
(`.agents/context.md`) are the source of truth for scope and logic. This document
records what happened *underneath* that spec while building it: decisions made to fill
real gaps the PRD leaves open, and defects caught and fixed during implementation.

For a governance/safety system, the standard we're holding ourselves to isn't just
"does it run" — it's "does it enforce what it claims to enforce, and can you prove it."
The entries below are evidence of that: several are bugs that would have silently
undermined a stated guarantee (a lock, a hash, an atomicity promise) had they shipped.
Each is recorded here rather than fixed quietly, because how a defect was found and
closed is itself part of the story a governance product needs to tell.

---

## 1. Schema decisions beyond the literal build plan

### `policy_layers` table

The build plan's first milestone calls for two tables — `bundles` and `ledger`. We
added a third: `policy_layers`, which persists every org/tenant YAML edit made through
the product's policy-authoring screens, versioned rather than overwritten in place.

**Why:** without it, editing a layer through the UI would have nowhere durable to live
between "authored" and "compiled" — the two-screen flow (org policy → agent policy +
compile) needs the org layer to still be there when the tenant layer is edited later,
and needs history so a compiled bundle's `source_layers` can be traced back to the
exact layer version that produced it. This was flagged explicitly as an assumption
beyond the spec before being kept, rather than decided silently.

---

## 2. Correctness guarantees enforced at the database layer, not by convention

### The ledger's two row shapes

The spec is explicit that input-stage and output-stage ledger rows are genuinely
different shapes (flat `action` + `review_needed` vs. `action` + `modifiers[]`) and
must not be unified. Rather than trusting application code to keep that promise, the
`ledger` table carries a `CHECK` constraint that makes the two shapes mutually
exclusive at the database level:

```sql
CHECK (
    (stage = 'input'  AND tier_reached IS NULL AND modifiers IS NULL AND review_needed IS NOT NULL)
    OR
    (stage = 'output' AND review_needed IS NULL AND modifiers IS NOT NULL)
)
```

A row that mixes the two shapes is rejected by Postgres itself — this can't drift out
of sync with application logic as the codebase grows.

### Atomic policy writes

`POST /policies/org` and `POST /policies/agent` each perform two writes: persist the
new layer version, then — if compilation succeeds — persist the resulting bundle.

**Defect found:** in initial testing, these were two independent database calls. A
deliberately invalid edit (`output_pii_review_threshold >= output_pii_redact_threshold`,
which the compile-time guard is supposed to reject) revealed that the layer write had
already committed by the time the compile step rejected the input. The result: a
tenant policy layer existed in the database with no corresponding compiled bundle — a
silent inconsistency between "what was requested" and "what is actually enforced,"
which is precisely the kind of gap a governance system cannot afford.

**Fix:** both writes now run inside a single database transaction. A rejected compile
rolls back the layer write as well — the operation either fully succeeds or leaves no
trace.

### Bundle hash integrity

`resolve()`'s working copy of the org layer initially carried the org layer's own
`locks:` array through into the resolved field set. Left uncaught, the compiled
bundle's `fields` block — the exact object `compile()` hashes to produce the bundle's
identity — would have silently included locking metadata that has no business
affecting what the bundle *is*. Caught by diffing the resolver's output against the
PRD's own worked example before moving on; fixed by stripping `locks` from the merged
result before it's returned.

---

## 3. Operational robustness against the real database endpoint

Three issues surfaced specifically from testing against the live Neon Postgres
instance rather than a local mock, each silent until exercised for real:

- **Prepared-statement caching vs. pooled connections.** Neon's pooled endpoint is
  PgBouncer running in transaction-pooling mode, where a "connection" isn't a stable
  physical link across queries. asyncpg's default prepared-statement cache assumes it
  is, which produces intermittent `prepared statement does not exist` errors under
  concurrent load — the kind of failure that's easy to miss in a quick manual test and
  only shows up once. Disabled via `statement_cache_size=0`.
- **JSONB round-tripping.** Registered explicit codecs for `json`/`jsonb` on the
  connection pool so Python dicts and lists pass through transparently, rather than
  leaving every query site responsible for manual `json.dumps`/`json.loads` calls —
  removes an entire category of "forgot to serialize" bugs before they could occur.
- **Connection-string parsing.** Neon's connection string includes
  `&channel_binding=require`. Read via a shell `source` of a `.env` file, the unquoted
  `&` is interpreted as bash's background-job operator, silently truncating the
  variable before the application ever saw it. Fixed by quoting the value, and by
  having the application load its own environment via `python-dotenv` — removing the
  shell-parsing step, and the class of bug it caused, entirely.

---

## 4. The data plane, end to end

`POST /check` (`routers/check.py`) is the orchestrator described in the PRD's core
rule: the only code that imports both detectors and fusion, and the only place
branching happens. Detectors (`data_plane/detectors/`) return raw matches with spans;
the orchestrator reduces each check to exactly one `{source, type, score, status}`
signal before it reaches fusion or the ledger — spans are used locally for redaction
and never persisted, satisfying "never write raw values into the ledger" while still
giving redaction something concrete to act on.

Two design choices worth noting:

- **Redaction is span-based and collected across detectors before masking**, per PRD
  §4.5 step 1 — output-stage redaction gathers T0 secrets spans and T1 PII spans into
  one list before replacing anything, avoiding the offset-shifting bug that a
  sequential two-pass replace would introduce.
- **The LLM call is fully dependency-injected** (`data_plane/llm_client.py`). No
  vendor has been chosen yet (open question, not yet asked of the user at time of
  writing), so `call_llm()` is currently a deterministic mock — same prompt always
  produces the same response, including on a `regenerate` retry, which is what makes
  the retry-cap-of-1 path in `decide_t1_output` actually exercisable end-to-end today.
  Swapping in a real BYOK provider later touches only this one function.

**Verified, not just written:** every fusion rule from the PRD's own test checklist —
disabled-excluded-not-defaulted-safe, timeout-still-escalates (including that a second
timeout after a retry escalates to `block`, not another `regenerate`), and the full
three-way modifier composition (`redact` + `flag_visible` + `flag` stacking, with
`block`/`regenerate` preempting all of it) — plus the demo's own five preset scenarios
(clean prompt, injection, card number, canary leak, toxic response) run against the
real orchestrator and a live Neon instance. The ledger's hash chain was independently
walked end-to-end (recomputing every row's hash from `prev_hash` + contents and
comparing against what's stored) and confirmed intact across a live sequence of input
and output rows — the tamper-evidence property isn't just asserted, it's been checked.

## 5. Swapping the PII detector for Presidio

Once the pipeline above was proven end-to-end on a hand-rolled regex PII detector
(per context.md's staged approach — get it working fast, then swap in the real
library), it was replaced with Presidio (`presidio-analyzer` + spaCy), per the PRD's
actual V1 method for PII: NER-based, `en_core_web_sm`. Because both versions expose
the same `scan(text) -> list[dict]` interface, the swap touched exactly one file
(`data_plane/detectors/pii.py`) — fusion, the orchestrator, and the ledger were
untouched, which is the entire point of keeping detectors as pure, swappable signal
producers.

Two issues surfaced while wiring it in, both before they could reach a demo:

- **Wrong model, by default.** Presidio's own default configuration pulls
  `en_core_web_lg` (400MB) regardless of what's already installed, silently ignoring
  the PRD's explicit choice of the smaller `en_core_web_sm` — caught mid-download (a
  spaCy model wheel appearing where a fast NER pass was expected is an obvious tell)
  and fixed by constructing the `NlpEngineProvider` with an explicit model config
  rather than relying on `AnalyzerEngine()`'s defaults.
- **A real false positive: geography questions flagged as PII.** Presidio's `LOCATION`
  entity recognizer fires on any country or city name — "what is the capital of
  France?" came back scored 0.85 and staged for redaction. For a governance system,
  a check that treats ordinary factual questions as a privacy leak erodes trust in
  every other check it makes. `LOCATION` was dropped from the entity allowlist;
  re-tested to confirm the false positive is gone while genuine PII (a Luhn-valid
  credit card, a name-and-email pair via NER) still correctly triggers redaction.

The spaCy pipeline's first inference call costs ~4-5 seconds cold; `pii.warm_up()`
runs once at app startup (`main.py`'s `lifespan`, PRD §4.4's "models warmed at
startup") so that cost is paid before any real request, not during one.

## 6. Real toxicity classifier + genuine parallel/timeout execution for T1

The toxicity check was upgraded from a keyword stub to a real classifier
(`martin-ha/toxic-comment-model`, DistilBERT-based) — chosen specifically to stay
local and warm-at-startup like Presidio, avoiding a second external API/vendor
dependency (Perspective API, OpenAI moderation) on top of the LLM vendor question
that's already open. Same `scan(text) -> list[dict]` interface as every other
detector, so nothing above it changed.

**A calibration observation, not a fix:** this model is fairly binary in practice —
clear threats and profanity score near 1.0, mild insults ("you're an idiot") score
near 0.0, with little middle ground. The demo's own mock toxic response happens to
score 0.96 (correctly blocks against the 0.85 threshold), but the PRD's two-threshold
banding (`regenerate` between 0.5–0.85, `block` above 0.85) assumes real-world scores
actually land in that middle band sometimes. Worth knowing before treating threshold
tuning as solved — flagged here rather than silently declared "done."

**Parallel execution with a real deadline was also added** for every stage's
detectors (input, T0, T1), addressing a gap from a straightforward reading of PRD
§4.4: *"Detectors run in parallel (`asyncio.gather`); hard deadline on the join"* —
previously nothing in `routers/check.py` could ever actually produce a `timeout`
signal, even though `fusion.py`'s handling of one was already implemented and tested.

**An honest result, not the one expected:** measured directly (`toxicity.scan` +
`pii.scan`, 5 rounds, models pre-warmed), running both concurrently via
`asyncio.gather(asyncio.to_thread(...))` produced **no wall-clock speedup** over
calling them sequentially — 0.99x. The reason is Python's GIL: spaCy's NLP pipeline
and the HF tokenizer spend most of their time in pure-Python code, which holds the
GIL, so two threads contending for it don't run meaningfully faster than one after
another. Real concurrency for CPU-bound Python work needs separate *processes*, which
would mean a second copy of each model in memory per worker — a real cost not taken
on here since it wasn't asked for.

What the change *did* correctly deliver, verified directly rather than assumed:
- The event loop is no longer blocked while a request's own detectors run, so
  concurrent `/check` requests aren't serialized behind each other.
- A genuinely enforceable per-detector deadline. Tested by swapping in a detector
  that sleeps for 10 seconds against a 2-second `DETECTOR_TIMEOUT_SECONDS`: it was cut
  off at exactly 2.0s and produced `{"score": null, "status": "timeout"}` — which
  `decide_t1_output()` already correctly escalates (`regenerate`, then `block` on
  retry) rather than treating as safe.

`DETECTOR_TIMEOUT_SECONDS` is an internal orchestrator constant, not a bundle field —
kept deliberately distinct from the PRD's rule that no tenant-facing latency budget
exists anywhere in policy (§7).

## 7. Multi-tenant redesign + full React frontend

V1 had exactly one hardcoded org layer and one hardcoded tenant layer, seeded on
startup from YAML files on disk (`db/seed.py`). That was replaced with real
multi-tenancy: organizations sign up and log in (JWT, bcrypt-hashed passwords), each
creates one org-level policy, then any number of agents, each with its own agent-layer
policy resolved against the org layer. `db/seed.py` and the on-disk default YAML files
are gone entirely — every organization now has to author its own policy through the
real API, matching how this would actually work in production.

**Schema changes** (`db/migrations/0002_multi_tenant.sql`): new `organizations` and
`agents` tables; `policy_layers` and `bundles` now scope by `organization_id`/`agent_id`
instead of the old hardcoded `name` string. One subtlety: a plain
`UNIQUE(agent_id, version)` doesn't work for the org-layer half of `policy_layers`
since `agent_id` is `NULL` there and Postgres treats `NULL != NULL` in uniqueness
checks — used partial unique indexes (`WHERE layer_type = 'org'` / `'agent'`) instead,
so each still gets a clean per-org or per-agent version sequence.

**Cascading recompilation:** an org-level policy edit can affect every agent under
that org (a tightened lock, a new threshold), not just one — so `POST /policies/org`
recompiles every agent's bundle in the same transaction as the org-layer write, not
just the one being edited. An agent-level edit only recompiles that one agent.

**Endpoints added:** `POST/GET /auth/{signup,login}`, `POST/GET /agents`,
`POST/GET /agents/{id}/policy`, `GET /agents/{id}`. `/check` now takes `agent_id`
instead of a hardcoded `policy_name`, and every policy/agent/check endpoint is
ownership-checked against the JWT's organization — an org can't read or compile
another org's agents.

**One accuracy fix while wiring this up:** `resolve()`'s clamp events hardcoded
`"locked_by": "org-baseline"`, a leftover from the single-tenant naming. Now takes the
real org name as a parameter, so a clamp event correctly reads e.g.
`"locked_by": "Acme Corp"` instead of a string that no longer means anything once
multiple real organizations exist.

**Frontend** (`apps/web`, Vite + React + react-router): signup/login, an org-policy
screen, an agents list with agent creation, a per-agent policy+compile screen, and a
chat demo screen — visual language (colors, card/badge/trace-chip styling) taken
directly from `.agents/controlplane_demo.html` per the brief to use it as a guide,
rebuilt against the real API instead of the demo's in-page mock logic.

**Verified in a real browser, not just built successfully:** signup → org policy save
→ agent creation → agent policy compile → all 4 of the demo's preset chat scenarios →
a free-text clean prompt, driven end-to-end with a headless Chromium script. Caught one
bug during that — not in the app, in the *test driver*: clicking a second preset while
the first request was still in flight silently no-op'd, because the app correctly
guards `send()` against overlapping requests. The fix was waiting for the input to
re-enable before firing the next action, not a code change to the app.

## 8. Real LLM wired in: Gemini

The vendor question from `OPEN_QUESTIONS.md` #2 is resolved: `data_plane/llm_client.py`
now calls Gemini via a direct REST call (`httpx`, no SDK) instead of the deterministic
mock. Falls back to the old mock automatically when `GEMINI_API_KEY` isn't set, so the
app still runs without a key — same reasoning as Presidio/toxicity's warm-up pattern,
nothing above this file needed to change.

Two things folded into the real call that the mock couldn't exercise:
- **The canary token now lives in an actual system instruction** sent to the model
  (`BASE_SYSTEM_PROMPT`), not just simulated by keyword-matching the user's prompt.
  If a real prompt injection ever gets the model to repeat it, T0's canary check is
  now catching a genuine exfiltration, not a scripted one.
- **`call_llm()` takes a `retry: bool` parameter**, and the orchestrator's regenerate
  path now passes `retry=True` on the second call, appending a stricter safety
  instruction to the system prompt — the mock never distinguished the two calls, so
  this path existed in `fusion.py` and was tested there, but wasn't actually wired
  through to the model call until now.

**Verified without spending API quota:** the user explicitly asked not to trigger real
calls against their free-tier key while testing. Everything below is checked either
by direct code reading or by driving the real `call_llm()` function with
`httpx.AsyncClient` monkeypatched to a fake transport that never touches the network —
response parsing, the canary token's presence in the request payload, the strict-suffix
on retry, empty-candidate handling (Gemini's safety filter can return zero candidates),
error propagation on a non-200 status (modeled on a 429 to mirror a rate-limit hit),
and the no-key mock fallback. The first real call against the live API is left for the
user to trigger through the actual chat UI, once, under their own control.

## 9. A real production bug: unhandled Neon connection drops crashed every request through auth

Found via the frontend, not a script: the chat screen showed "Request failed" with a
browser-console CORS error on `/check`. The actual cause, from the backend's own log,
had nothing to do with CORS or Gemini:

```
asyncpg.exceptions.ConnectionDoesNotExistError: connection was closed in the middle of operation
  File ".../auth.py", line 44, in get_current_org
```

Neon's pooled endpoint silently closes idle connections server-side (normal behavior
for serverless Postgres); asyncpg's pool has no way to know a connection is dead until
it tries to use it. This exception was unhandled, so it escaped all the way to
Starlette's outermost error handler — which sits *outside* `CORSMiddleware`, since
middleware registered via `add_middleware` sits inside the framework's own top-level
error boundary. The resulting 500 response had no CORS headers, and the browser
reports "no CORS headers" as a CORS policy block rather than surfacing the real 500 —
which is exactly why this looked like a networking/CORS bug and wasn't one.

**Fix, in `db/connection.py`:**
- `max_inactive_connection_lifetime=60` on the pool — proactively recycles idle
  connections before Neon's own reaper drops them, reducing how often the race happens
  (doesn't eliminate it — a connection can still die between being handed out and used).
- `with_db_retry()` — a small helper that retries a pool call once if it hits this
  specific class of error. Applied to `auth.get_current_org`, since that runs on
  *every* authenticated request — the highest-traffic, most failure-visible path.
  Verified directly (a fake connection object raising the real asyncpg exception on
  its first call, succeeding on retry) rather than just asserted.

**Not yet done:** the same retry treatment isn't applied to every other pool call site
in `db/queries.py` — only the one that actually crashed and is exercised on every
request. If this recurs elsewhere (a policy or agent endpoint mid-transaction, say),
the same `with_db_retry` helper is there to wrap it, but that's not done preemptively
everywhere to avoid changing code that hasn't shown the problem.

**Separately, flagged not fixed:** while looking at this file, `GEMINI_MODEL`'s default
was found changed to `"gemini-3.6-flash"` — not a model name either of us verified
exists. Left as-is per instruction not to silently revert someone else's edit, but
this will surface as a clean `LLMError` (404 from Gemini, caught and turned into a 502)
the first time `/check` actually reaches the LLM call, not a crash — worth confirming
that model id is real before relying on it.

## 10. PRD §8's open question resolved with evidence: `en_core_web_sm` → `en_core_web_lg`

Found via real usage, not a script: typing "My name is Priya Agarwal and I live at 42
MG Road, Dehradun, Uttarakhand 248001" into the chat demo produced zero PII signal —
`pii.scan()` returned an empty list despite an actual name being present.

Root cause, found by running the same text through Presidio with no entity
restriction: `en_core_web_sm`'s NER classified "Priya Agarwal" as `ORGANIZATION`, not
`PERSON` — a known weakness of the small English model on South Asian names, which
our `_ENTITIES` allowlist then silently dropped (it only keeps `PERSON`, not
`ORGANIZATION`). This is exactly the tradeoff the PRD flagged as unresolved (§8:
"`en_core_web_sm` vs. `en_core_web_lg` for Presidio — accuracy/latency tradeoff not
yet benchmarked") — now actually benchmarked instead of left open:

- **Accuracy:** swapping to `en_core_web_lg` correctly classifies the same name as
  `PERSON` (score 0.85).
- **Latency:** measured directly, 20 warm calls each — `en_core_web_sm` 4.4ms/call,
  `en_core_web_lg` 4.2ms/call. No meaningful difference; the PRD's "~9ms" figure was
  never actually a live tradeoff at the per-request level. The real cost of `lg` is a
  larger download (400MB vs ~12MB) and startup memory footprint, not per-request time.

Switched the default model in `data_plane/detectors/pii.py` on that evidence.

**What this fix did not solve on its own:** the same test case's street address, city,
state, and PIN code still went undetected — not a model-accuracy issue this time.
Presidio's `LOCATION` entity had been deliberately excluded from the allowlist (§5
above) because it fires on *any* place name at the same 0.85 confidence, harmless or
not ("capital of France," still confirmed unaffected by the `lg` swap).

Put to the user directly as a real product-risk tradeoff rather than picked silently:
keep `LOCATION` excluded (misses real addresses), re-include it fully (catches
addresses, but also flags harmless place mentions), or re-include it down-weighted (a
middle ground requiring custom scoring logic not yet built). **Decision: re-include
`LOCATION` at full confidence** — catching real addresses reliably was judged more
important than avoiding false positives on ordinary geography questions. Verified both
directions after the change: the same address test case now flags both the name
*and* "Dehradun" as PII, and "capital of France" is confirmed to still flag "France" —
that false positive is now a known, accepted cost of the choice, not a bug.

## 11. Input un-redaction: a spec'd feature that was silently missing until asked about

PRD §4.2 describes a 4-step redaction execution: mask PII spans → hold an
un-redaction map in memory for that request only → send the redacted prompt to the
LLM → **swap placeholders back to real values before returning the response to the
user**. Only the first three steps existed. `_apply_redaction()` returned redacted
text alone, with no map, and nothing anywhere restored a placeholder afterward. The
gap wasn't caught by any of the extensive `/check` testing earlier, because none of
those test prompts happened to produce a response that echoed the placeholder back —
it took a direct question ("are placeholders getting replaced back?") to surface that
the feature was never actually built, only three-quarters of it.

**Fix:** `_apply_redaction()` now returns `(redacted_text, unredact_map)`. A new
`_unredact()` restores the map's real values by literal placeholder substitution.
Applied as the last step before the response leaves `/check` — after any
output-stage redaction and the `flag_visible` disclaimer, never before — so it can't
interfere with either.

**A related bug caught while implementing, before it could ship:** input-stage and
output-stage redaction independently number placeholders from 0 in two separate
`_apply_redaction()` calls. Restoring by literal string match without some
disambiguation would have used the input map to overwrite an unrelated output-stage
placeholder if they ever collided on the same index (`[REDACTED_0]` meaning two
different things depending on which stage produced it). Fixed with a `prefix`
parameter (`INPUT_REDACTED` vs. `OUTPUT_REDACTED`) so the two namespaces can never
collide — verified directly with a constructed collision case, not just reasoned
about.

**Verified without any LLM call:** a real detector-scanned redact/restore round trip
(credit card number → placeholder → simulated model response echoing it → restored to
the original value in the final text), confirmation that output-stage redaction is
never restored (per PRD §4.5's explicit "no un-redaction step" for that side), and the
prefix-collision case above — all exercised directly against the actual functions, not
assumed correct from reading the diff.

## 12. Deliberate scope gaps — documented, not silently resolved

`compile()` does not currently reject a bundle where `toxicity_regenerate_threshold`
or `toxicity_block_threshold` is left `null`, even though the output-stage fusion logic
needs a real number to compare a toxicity score against. This is a known open question
in the PRD itself (§3.4, §8), not an oversight: the spec calls for it to be resolved by
a decision, not by an implementation silently picking a default. It is left as an
explicit `TODO` in `control_plane/compile.py` pending that decision, rather than
patched over.
