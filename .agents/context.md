# ControlPlane.ai — project context

**Read this first, then go deeper as needed — this file is the map, not the whole
territory.** A large chunk of V1 is already built and running. This file orients a
new agent (or a human) to the current state; it does not re-explain everything the
other docs already cover in depth.

## Where everything actually lives

**File authority, as of this writing (per explicit user direction — supersedes
the older framing below where it conflicts):** [`Learning_Plane_PRD_Draft.md`](Learning_Plane_PRD_Draft.md)
and this file (`context.md`, which gets updated regularly) are the current,
trustworthy sources for the learning plane and overall build state. Every
other `.md` under `.agents/` — `PRD_V1_Consolidated.md`, `decisions/DECISIONS.md`,
`questions/OPEN_QUESTIONS.md`/`CLOSED_QUESTIONS.md` — is **outdated** and kept
around for historical idea-reference only, not as ground truth on current
state or current rules. `docs/` is judge-facing and is kept in sync with
reality as part of the same work that changes the code (see below); it
remains reliable.

| You need... | Go to |
|---|---|
| The current learning-plane spec — mechanics, required pages, what's decided | [`Learning_Plane_PRD_Draft.md`](Learning_Plane_PRD_Draft.md) — current |
| A polished, judge-facing explanation of what's built and why, per component | [`../docs/`](../docs/README.md) — start there for "how does X actually work"; current |
| The original V1 spec (control/data plane schema, thresholds, fusion logic) | [`PRD_V1_Consolidated.md`](<PRD_V1_Consolidated (1).md>) — **outdated**, idea-reference only |
| The old chronological decision log | [`decisions/DECISIONS.md`](decisions/DECISIONS.md) — **outdated**, idea-reference only |
| Old unresolved/resolved question logs | [`questions/OPEN_QUESTIONS.md`](questions/OPEN_QUESTIONS.md) / [`questions/CLOSED_QUESTIONS.md`](questions/CLOSED_QUESTIONS.md) — **outdated**, idea-reference only |
| A pure frontend mock — UI/UX and visual reference only, no backend | [`controlplane_demo.html`](controlplane_demo.html) |

If anything conflicts: `Learning_Plane_PRD_Draft.md` and this file win on
*what the rules should be and what's actually been decided*; `docs/` wins on
*what's actually been built and why it looks the way it does*. This file is a
map between them, not a third source of truth.

## What this is

A governance/safety proxy that sits between an enterprise and its LLM calls. An
organization signs up, authors one org-level safety policy (PII handling, prompt
injection defense, toxicity handling, secret leakage prevention), locks whatever
parts must never be loosened, then creates any number of AI agents (a support bot, an
internal copilot, ...) that each tune the rest for their own use case. Every real
chat request runs through the compiled, versioned, hashed result of that policy —
rules are data, not hardcoded checking logic.

Three planes: **control plane** (authors and resolves policy, human-speed),
**data plane** (applies it to live traffic, millisecond-scale), **learning plane**
(offline — ledger, and eventually reviewer queue / shadow eval / calibration).

## Current status (read `docs/` for the real depth here)

| Plane / area | Status |
|---|---|
| Control plane — policy layers, locking (3 shapes), `resolve()`/`compile()`, bundle hashing, multi-version bundles + `is_production` | **Built.** [`docs/control-plane/`](../docs/control-plane/README.md) |
| Data plane — orchestrator, fusion, all 5 detectors, redaction + un-redaction, parallel/timeout execution | **Built.** [`docs/data-plane/`](../docs/data-plane/README.md) |
| Learning plane — hash-chained ledger, `GET /ledger` list+verify+summary, reviewer queue (built, temporarily off the tab nav), shadow deploy, 100-case **mock** eval (FN-rate/FP-rate, no live execution, per-case browsable table), **per-check** calibration (3 swept checks each with separate FN-vs-threshold/FP-vs-threshold graphs, 4 fixed-cutoff checks with a single FN/FP number), calibration→new-policy-version loop (`output_pii` only), 4 required pages (Audit Ledger, Reviewer Queue, Calibration+Metrics **— Demo Org only**, Policy Versions) | **Built.** [`docs/learning-plane/`](../docs/learning-plane/README.md) |
| Multi-tenancy & auth — orgs sign up/log in, own their agents, JWT-protected | **Built.** [`docs/cross-cutting/multi-tenancy-and-auth.md`](../docs/cross-cutting/multi-tenancy-and-auth.md) |
| LLM integration — real Gemini call, mock fallback, retry-with-backoff on 429 | **Built.** [`docs/cross-cutting/llm-integration.md`](../docs/cross-cutting/llm-integration.md) |
| Frontend — signup/login, org + agent policy editors, chat + trace demo, learning plane screen | **Built.** [`docs/cross-cutting/frontend.md`](../docs/cross-cutting/frontend.md) |
| Audit Bot (independent-judge FN-rate estimate on real traffic) | **Not built, deliberately** — blocked on API budget, documented as a future-proposal only. See `docs/learning-plane/README.md`. |
| Automated test suite (pytest or similar) | **Does not exist.** Everything verified so far was ad-hoc scripts run during development, logged in `DECISIONS.md`, not committed as a repeatable test suite. See Testing section below. |

## Stack (as actually implemented)

- **Backend:** FastAPI (Python 3.14), one service, routers not microservices. Lives in `apps/api/`.
- **Frontend:** React + Vite + `react-router-dom`. Lives in `apps/web/`.
- **Database:** Neon (Postgres), `asyncpg`. `JSONB` for array/nested fields
  (`modifiers`, `contributing_signals`, `content`, `fields`) with codecs registered so
  Python dicts/lists pass through transparently — see `db/connection.py`.
- **Auth:** bcrypt password hashing + JWT (`pyjwt`), 7-day expiry. No RBAC, no
  password reset flow — one login per organization.
- **PII detection:** Presidio + spaCy's `en_core_web_lg` (not `en_core_web_sm` — see
  `docs/data-plane/detectors.md` for the benchmark that decided this).
- **Toxicity detection:** `martin-ha/toxic-comment-model` (DistilBERT, via
  `transformers`+`torch`, run locally).
- **LLM:** Gemini via a direct `httpx` REST call (no SDK). Falls back to a
  deterministic mock automatically if `GEMINI_API_KEY` isn't set.

## Actual layout

```
/apps
  /api
    main.py                       # FastAPI app, CORS, router registration, model warm-up
    auth.py                       # bcrypt, JWT encode/decode, get_current_org dependency
    /routers
      auth.py                     # POST /auth/signup, /auth/login, /auth/demo-login
      agents.py                   # agent CRUD + per-agent policy/compile
      policies.py                 # org policy + cascading recompile
      check.py                    # POST /check — the orchestrator
      ledger.py                   # GET /ledger list+filter, GET /ledger/verify, POST /ledger/{id}/review
      learning.py                 # mock eval, calibration sweep/preview, shadow-deploy, reviewer-queue
      _compile_helper.py          # shared compile-and-persist, used by policies.py + agents.py
    /control_plane
      resolve.py                  # merge + lock enforcement
      compile.py                  # hashing, bundle assembly, compile-time guards
      locks.py                    # ENUM_RANKS + is_stricter_or_equal — the 3 lock shapes
    /data_plane
      fusion.py                   # the only place an action is decided
      pipeline.py                 # run_pipeline() — the actual check pipeline; POST /check's only caller
      llm_client.py               # real Gemini call + mock fallback, retry-with-backoff on 429
      /detectors
        secrets.py / injection.py / canary.py / pii.py / toxicity.py
    /learning_plane
      replay.py                   # shared replay mechanic for shadow deploy only (offline replay of stored ledger signals)
      mock_eval.py                 # runs the 100-case MOCK set through real fusion logic — no detector/LLM/ledger, ever
      metrics.py                   # FN-rate / FP-rate + per-check calibration math (no latency — nothing real to measure)
      demo_seed.py                  # idempotently seeds the singleton Demo Org + Demo Agent on every startup
      /data/synthetic_test_set_100.json  # 100% mock — ground_truth_checks (per-check bool) + mock_signals (per-source) — lives here, not under .agents/
    /db
      connection.py                # pool, JSONB codecs, stale-connection retry helper, pool-init retry-with-backoff
      queries.py                    # all SQL, org/agent-scoped
      /migrations
        0001_init.sql                # policy_layers, bundles, ledger
        0002_multi_tenant.sql        # organizations, agents; re-scopes the above
        0003_learning_plane.sql      # bundles.is_production/label, ledger review columns, eval tables
        0004_mock_eval_only.sql      # drops the eval tables 0003 added — no live-run history needed, all recomputed
        0005_demo_org.sql            # organizations.is_demo (at most one row) — see learning_plane/demo_seed.py
  /web
    /src
      /screens                    # Login, Signup, OrgPolicy, AgentsList, AgentPolicy, Chat, LearningPlane
      /components                 # Layout (nav), Trace (pipeline visualization), CalibrationChart (inline SVG)
      /api/client.js               # fetch wrapper, JWT storage
```

## Non-negotiable architectural rules (enforced in code, not just convention)

1. **No detector ever decides an action.** Every detector returns a pure signal:
   `{source, type, score, status}`, `status ∈ {"ok", "timeout", "disabled"}`. Only
   `fusion.py` turns signals into an action. Detectors and fusion never import each
   other — only `data_plane/pipeline.py` imports both (the actual orchestration used
   by both `POST /check` and the learning-plane eval runner — `routers/check.py`
   itself is now a thin auth/lookup wrapper around `pipeline.run_pipeline()`).
2. **Per-check toggles, not tier-level.** No `checks_enabled: {t0,t1,t2}` field
   exists. Every check has its own boolean, default **off**.
3. **`status: "disabled"` ≠ `status: "timeout"`.** Disabled = never invoked, excluded
   from fusion entirely. Timeout = ran but didn't finish in time, `score: null`,
   *unknown, not safe* — escalates.
4. **Locking is a `locks:` list**, not inline flags. `resolve()`/`compile()` reject a
   layer whose `locks:` entry doesn't resolve to a field that same layer sets.
5. **Three lock shapes, one comparator table** (`control_plane/locks.py`): numeric
   threshold (`new <= old`), ordered enum (`ENUM_RANKS`, add new fields there, don't
   hand-roll comparisons), boolean toggle (exact match, no direction).
6. **Compiled bundle is JSON**, hashed over `fields` only —
   `sha256(json.dumps(fields, sort_keys=True))` — never over `_meta`.
7. **`compile()` guards:** rejects `input_pii_review_threshold >= input_pii_redact_threshold`
   (and the output equivalent). **Known, deliberately open gap:** does not reject a
   `null` toxicity threshold — flagged as a `TODO` in the code, not silently patched.
8. **Output-stage fusion composes modifiers**, doesn't pick one winner. `block` /
   `regenerate` preempt everything; `redact` / `flag_visible` / `flag` can all stack
   on one `allow`.
9. **No T2, no session state, no semantic cache, no complexity router, no grounding,
   no injection-on-output classifier.** All explicitly deferred — a missing field for
   a not-yet-built check is intentional, not a bug to "fix."
10. **Multiple bundle versions can exist per agent; exactly one is `is_production`.**
    Editing org/agent policy through the normal screens still auto-promotes the new
    compile (today's UX, unchanged) — only the learning plane's calibration flow
    compiles with `promote=false`, so a calibration-proposed version sits alongside
    the live one until someone explicitly promotes it via the version picker.
    `POST /check` always loads the `is_production`-flagged bundle, never simply "the
    latest one." See [`docs/learning-plane/README.md`](../docs/learning-plane/README.md).

## Ledger

Hash-chained, append-only, two row shapes enforced by a DB `CHECK` constraint (not
just convention): input rows have flat `action` + `review_needed`; output rows have
`action` + `modifiers[]`, no `review_needed`. Never write raw prompt/response content
or PII values — types and scores only, e.g. `{"type": "PII", "score": 0.85, "status": "ok"}`.
Same rule applies to the redaction un-redaction map — it lives only in a request-scoped
local variable, never reaches the ledger writer.

## Multi-tenancy (this changed the shape of everything — read this if confused)

V1 originally had one hardcoded org layer and one hardcoded agent, auto-seeded on
startup. **That's gone.** Organizations now sign up for real
(`POST /auth/signup`), get a JWT, and every protected endpoint is
ownership-checked against it. An org creates its own org-level policy first, then
any number of agents, each with its own agent-level policy resolved against the
org's. There is no seed data — a fresh database has zero organizations, zero agents,
zero bundles, until someone signs up through the real flow. Full story:
[`docs/cross-cutting/multi-tenancy-and-auth.md`](../docs/cross-cutting/multi-tenancy-and-auth.md).

## How to run it

A root `package.json` wraps both halves:

```bash
npm run install:all   # venv + pip install (api) + cp .env.example .env if missing, npm install (web)
npm run dev           # both servers together — api:8000, web:5173 — via `concurrently`
```

Or the two halves separately, same as before:

```bash
# backend
cd apps/api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL + JWT_SECRET; GEMINI_API_KEY optional
uvicorn main:app --reload --port 8000

# frontend
cd apps/web
npm install
npm run dev             # http://localhost:5173
```

First real action: sign up an organization through `/signup` (or `POST /auth/signup`)
— there's nothing pre-populated to log into for a *real* org. (The one deliberate
exception: a singleton "Demo Org" auto-seeds itself on backend startup — see
`learning_plane/demo_seed.py` and the Learning Plane section above — reachable via
the "View demo" button on the login screen, no signup needed.)

Five Postgres migrations need to have been applied, in order: `0001_init.sql`
through `0005_demo_org.sql` (`apps/api/db/migrations/`). There's no migration
runner wired up — they're applied manually against Neon (`psql` or a short asyncpg
script), same process each time. Two Neon/asyncpg-specific gotchas already handled
in `db/connection.py`: `statement_cache_size=0` (Neon's pooled endpoint is PgBouncer
in transaction mode) and a retry-with-backoff around pool creation itself (Neon's
serverless compute can be suspended and take a few seconds to wake on the first
connection of a session — this bit us for real, more than once, mid-development).

## Testing

**No automated test suite exists yet.** The layered approach below was the intended
design from early in the project and is still the right target, but every check made
so far (fusion logic, redaction/un-redaction, the resolver, the Presidio/toxicity
model swaps, the full `/check` pipeline against a live Neon instance, a real browser
walkthrough of the frontend) was a one-off verification script or manual browser
session — logged in `DECISIONS.md` with what was checked and how, but not committed
as `pytest` files that could be re-run. Writing that suite for real is open work, not
done work:

1. Detector unit tests — real fixtures, no bundle/fusion involved.
2. Fusion unit tests — synthetic signals straight into `decide_*` functions. Cover:
   disabled-excluded-not-defaulted-safe, timeout-still-escalates, modifier
   composition, block/regenerate preemption. (All of this was manually verified
   already — see `DECISIONS.md` — just needs to become actual test files.)
3. Per-check toggle contract tests — assert a disabled check's detector function is
   never *called*, not just that its result is ignored.
4. Resolver/lock tests — one per lock shape, using the PRD §3.4.1 org/agent pair as
   fixtures.
5. Golden end-to-end tests — real orchestrator, **mocked LLM client** injected via
   dependency injection so `call_llm()` never hits a real API in tests. Assert on the
   final ledger row, not just the HTTP response.

## Documentation — keep both in sync, every change, not as a separate pass

Two surfaces, two different readers:

- **`../docs/`** — judge-facing, organized by component. Explains what was built, why,
  and what was deliberately skipped, with real code/JSON/YAML from the actual source.
  Update the relevant page **in the same piece of work** as any change that would make
  it inaccurate — a model swap, a schema change, a new endpoint.
- **`.agents/`** — agent/dev-facing working history. `decisions/DECISIONS.md` is the
  chronological log (problem → decision → why it matters) that `docs/` gets written
  from. `questions/OPEN_QUESTIONS.md` holds genuinely unresolved forks; once one is
  resolved, move it to `questions/CLOSED_QUESTIONS.md` (append the resolution, don't
  delete the entry) and reflect the resolution in the relevant `docs/` page too.

## What's next

Check [`questions/OPEN_QUESTIONS.md`](questions/OPEN_QUESTIONS.md) for the live list.
As of this writing, the open items are: whether thread-based parallelism is good
enough or genuine multi-process concurrency is worth the memory cost; whether
`compile()` should reject a null toxicity threshold or apply a default; and whether
the leaked-and-rotated Neon credential's git history should be rewritten. The
Gemini-quota item that used to be here is closed — see below.

**Learning plane, built this session, then corrected** (`.agents/Learning_Plane_PRD_Draft.md`
is now implemented — that file, like the rest of `.agents/`, isn't guaranteed to
stick around, so the durable record of what shipped lives here and in
[`docs/learning-plane/README.md`](../docs/learning-plane/README.md)): multi-version
bundle storage with a version picker and manual promotion, shadow deploy (offline
replay of stored ledger signals under two bundle versions' thresholds), the reviewer
queue (approve/reject verdicts on flagged/review-needed ledger rows — a label only,
no downstream wiring, by design; built but currently pulled off the tab nav per a
later request — see below), a **per-check** calibration comparison (revised from an
earlier single blended curve), and the calibration → new-policy-version loop (with a
proactive clamp-warning preview before confirming).

**The first pass of the 100-case eval ran every case through the real pipeline**
(real Gemini calls) — two full attempts both failed to complete against a
free-tier key, the second exhausting the account's **daily** quota entirely, not
just a per-minute rate limit. The PRD itself was then revised to remove live
execution from this feature: `synthetic_test_set_100.json` is now 100% mock data
(pre-authored `mock_signals` per case), and `learning_plane/mock_eval.py`
recomputes every case's decision straight from those stored signals via the real
fusion functions — no detector call, no LLM call, no ledger write, ever, in this
path. Verified against all 100 cases: zero mismatches between recomputed actions
and each case's authored ground truth. The same PRD revision also introduced §0, a
literal checklist of four required frontend pages (Audit Ledger, Reviewer Queue,
Calibration + Metrics, Policy Versions) after an earlier pass built the backend
mechanics correctly but never rendered a ledger view — all four now exist as tabs
on `LearningPlane.jsx`. Audit Bot (§7 of the draft) stays a documented
future-proposal, not built — it needs API budget the project doesn't have right
now. Everything above was verified against the real Neon DB and a real browser
session (headless Chrome driven directly over the DevTools protocol, since neither
Playwright nor `chromium-cli` were available in this environment) — not just
unit-level.

**Two later, smaller revisions on top of the above:**
1. **Reviewer Queue pulled off the tab nav** (per an explicit request to
   simplify the demo) — the tab, component, and `GET /learning/reviewer-queue`
   endpoint are all still there, just not listed in `LearningPlane.jsx`'s
   `tabs` arrays; re-adding `"Reviewer Queue"` to both is a one-line revert.
2. **Ground truth went from one flat `ground_truth_risky` bool to
   `ground_truth_checks`** — one bool per each of V1's 7 checks — and
   calibration was reworked from a single combined FP+FN chart on
   `output_pii_redact_threshold` into a genuinely **per-check** comparison:
   each check's own mock-signal score vs. that check's own ground truth, not
   the whole-row action. The 3 checks with a real bundle threshold
   (`output_pii`, `input_pii`, `output_toxicity`) each get **two separate
   graphs** (FN-vs-threshold, FP-vs-threshold — no more combined two-line
   chart); the other 4 checks fire at a fixed cutoff with no tunable
   threshold, so they get a single FN/FP number instead. Per the PRD's own
   coverage-gap note (§3), `input_pii`/`input_secrets`/`output_secrets`/
   `output_canary` have zero planted true-positive cases in the dataset —
   their FN figure is `None`/"n/a (untested)" everywhere, not a misleading
   0%, and this now renders that way rather than silently omitting it. The
   "Create New Policy" flow (§6.1) stays scoped to `output_pii` only, per the
   PRD text. Full mechanics: [`docs/learning-plane/README.md`](../docs/learning-plane/README.md#calibration--per-check-not-one-blended-curve).
