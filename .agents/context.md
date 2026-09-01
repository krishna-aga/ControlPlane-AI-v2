# ControlPlane.ai — build context

You're implementing the real backend + frontend for this project. Two other files sit next to this one in `.agents/`:

- **`PRD_V1_Consolidated.md`** — the source of truth for schema, logic, and scope. Every field name, threshold, lock rule, and fusion function in this doc is final for V1. Don't redesign anything it already specifies; if you hit a real gap it doesn't cover, stop and ask rather than inventing a decision.
- **`controlplane_demo.html`** — a **pure frontend mock**, no backend, no DB. It's a UI/UX reference for the 3-screen flow (org policy → agent policy + compile → chat with pipeline trace) and its detector logic is regex/keyword stubs for demo purposes only. Match its *flow and layout*, not its implementation — every piece of logic in it needs a real backend equivalent.

## What this is

A governance/safety proxy that sits between an enterprise and its LLM calls. Three planes: **control plane** (authors and resolves policy), **data plane** (applies it to live traffic, milliseconds), **learning plane** (offline — ledger, reviewer queue, shadow eval, calibration). **Do not build the learning plane yet** — only its ledger table needs to exist, since the data plane writes to it. Reviewer queue, shadow eval, and calibration are out of scope until told otherwise.

## Stack

- **Backend:** FastAPI (Python), one service, organized as routers — not microservices.
- **Frontend:** React.
- **Database:** Neon (Postgres). Use `JSONB` columns for array/nested ledger fields (`modifiers`, `contributing_signals`) — don't serialize them as text.
- Backend talks to Neon directly (`psycopg`/`asyncpg` or an ORM of your choice). Frontend never talks to Neon — only to the FastAPI routes.

## Suggested layout

```
/apps
  /api                       # FastAPI service
    /routers
      policies.py            # org/agent YAML in, resolve()/compile() out
      check.py                # POST /check — the checking pipeline + LLM call
      ledger.py                 # GET ledger rows, tamper-verify endpoint
    /control_plane              # resolve(), compile(), lock logic
    /data_plane                   # detectors, fusion, orchestrator
    /models                        # Pydantic schemas: bundle, signal, ledger row
    /db                             # Neon connection, table definitions/migrations
  /web                        # React app
```

## Non-negotiable architectural rules (from the PRD — enforce these in code review, not just convention)

1. **No detector ever decides an action.** Every detector returns a pure signal: `{source, type, score, status}`, `status ∈ {"ok", "timeout", "disabled"}`. Only the **Risk Fusion and Action Engine** turns signals into an action. Detectors and fusion code must never import each other — only the orchestrator (`check.py`) imports both.
2. **Per-check toggles, not tier-level.** There is no `checks_enabled: {t0,t1,t2}` field. Every individual check has its own boolean: `input_checks_enabled {secrets, injection, pii}`, `output_t0_checks_enabled {secrets, canary}`, `output_t1_checks_enabled {pii, toxicity}`. A check defaults to **off** — a layer must explicitly set it `true`.
3. **`status: "disabled"` is not the same as `status: "timeout"`.** A disabled check is never invoked by the orchestrator at all (that's what saves the cost), and its signal is excluded from fusion scoring entirely — never defaulted to a "safe" score. A timeout (`status: "timeout"`, `score: null`) means *unknown*, and escalates. Don't conflate these.
4. **Locking: `locks:` list, not inline flags.** Layer-authoring YAML (`org-baseline.yaml`, `tenant-*.yaml`) expresses locks as a top-level `locks:` array of dotted field paths, not per-field wrapper objects. `resolve()` rejects a layer where a `locks:` entry doesn't resolve to a field actually set in that same layer.
5. **Three lock shapes, three strictness rules** (see PRD §3.4.1 for the full worked example and reference `resolve()` implementation):
   - Numeric threshold fields (`*_threshold`): tenant may only set a value `<=` the org's (lower = fires sooner = stricter).
   - Ordered enum (`toxicity_mild_action`, `fail_mode`): each has a fixed strictness ranking; tenant may only move up the ranking, never down. Add new ordered-enum fields to one shared ranking table, don't hand-roll comparisons per field.
   - Boolean check-toggles: a lock pins an exact value (mandatory-on, or the rarer mandatory-off) — no "stricter" direction, just match-or-reject.
6. **Compiled bundle is JSON, not YAML.** Layer-authoring files stay YAML (human-edited). The compiled bundle a gateway/orchestrator reads is JSON: `{"_meta": {policy_name, version, hash, source_layers, compiled_at}, "fields": {...}}`. The hash is computed over `fields` only, canonicalized the same way the ledger already hashes its own rows (`sha256(json.dumps(fields, sort_keys=True))`) — never over the whole document, and never including `_meta` (avoids self-reference).
7. **`compile()` guards to implement:** reject if `input_pii_review_threshold >= input_pii_redact_threshold` or the output equivalent; reject if any `locks:` entry doesn't resolve to a field set in that layer. **Known open gap, flag it in a TODO rather than silently fixing it a different way:** `compile()` currently does *not* reject a `null` toxicity threshold, even though fusion needs a real number — ask before deciding how to close this.
8. **Output-stage fusion composes modifiers, it doesn't pick a single winner.** `block`/`regenerate` are whole-response outcomes and preempt everything. Otherwise the base action is `allow`, and `redact` / `flag_visible` / `flag` can all stack as a `modifiers[]` array on top of it. See PRD §4.5 for the exact `decide_output_fusion()` logic — implement it as written, this was a deliberate correction from an earlier single-winner design.
9. **No T2, no session state, no semantic cache, no complexity router, no grounding, no injection-on-output classifier.** All explicitly deferred past V1 — don't scaffold fields, endpoints, or DB columns for them. If a schema field doesn't exist for a not-yet-built check, that's intentional (per PRD §4.6), not an oversight to "fix."

## Ledger

Hash-chained, append-only. Each row includes `prev_hash` and its own `hash` computed the same way as bundle hashing. Two row shapes — don't unify them:
- **Input-stage:** flat `action` + `review_needed` boolean.
- **Output-stage:** `action` + `modifiers[]` array (no separate `review_needed` — folded into `modifiers` as `"flag"`).

Never write raw prompt/response content or PII values into the ledger — types and scores only (`{"type": "PII", "score": 0.85, "status": "ok"}`), never the actual detected string.

## Testing

Match the layered approach already agreed:
1. Detector unit tests — real fixtures, no bundle/fusion involved.
2. Fusion unit tests — synthetic signals fed straight into `decide_*` functions, no real detectors. Explicitly cover: disabled-excluded-not-defaulted-safe, timeout-still-escalates, disabled-does-not-escalate, modifier composition, block/regenerate preemption.
3. Per-check toggle contract tests — assert a disabled check's detector function is never *called* (not just that its result is ignored).
4. Resolver/lock tests — one per lock shape, using the org-baseline/tenant-support-bot pair from PRD §3.4.1 as fixtures.
5. Golden end-to-end tests — real orchestrator, **mocked LLM client** (deterministic scripted response, injected via dependency injection so `call_llm()` never hits a real API in tests). Assert on the final ledger row, not just the HTTP response.

## What to build first

1. Neon schema: `bundles` table, `ledger` table (JSONB for signals/modifiers), migrations.
2. `control_plane/resolve.py` + `compile.py` — port directly from PRD §3.4.1's reference implementation.
3. `POST /policies/org`, `POST /policies/agent` (runs resolve/compile, persists bundle, returns it + any clamp events).
4. Detectors — start with the same regex/keyword approach as the demo mock (fast to get working end-to-end), then swap in Presidio for PII and a real toxicity classifier once the pipeline is proven.
5. `POST /check` — the real orchestrator, fusion, ledger write.
6. React frontend, reusing the 3-screen flow from `controlplane_demo.html` but calling real endpoints instead of in-page JS state.

If anything here conflicts with `PRD_V1_Consolidated.md`, the PRD wins — this file is a build-order summary of it, not a replacement.