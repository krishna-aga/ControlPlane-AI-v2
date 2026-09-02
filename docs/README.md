# ControlPlane.ai

A governance/safety proxy that sits between an enterprise and its LLM calls.
Enterprises run multiple AI use cases — a customer support bot, an internal
copilot, a regulated decision-support tool — each with different risk and speed
requirements. ControlPlane.ai lets an organization define safety policy once
(PII handling, prompt injection defense, toxicity handling, secret leakage
prevention), lock the parts that must never be loosened, let each individual
AI agent tune the rest, and enforces the result on every real request —
without hardcoding any of those rules into the code that does the checking.

**Why that separation matters:** hardcoding safety differences into checking
code means every policy change needs a code change and a redeploy, and there's
no clean audit trail for "what rule was live on what date, and who approved
it." The fix this project implements: **rules are data, not code.** They're
authored by humans, merged with enforceable locking, versioned and hashed, and
handed to the part of the system that actually enforces them at request time.

## The three planes

| Plane | Role | Speed | Status |
|---|---|---|---|
| **[Control plane](control-plane/README.md)** | Authors and resolves policy — org-level rules, per-agent overrides, locking, compiling to a versioned, hashed bundle | Human-speed, changes rarely | Built |
| **[Data plane](data-plane/README.md)** | Applies the compiled bundle to a live prompt/response pair, millisecond-scale | Real-time, every request | Built |
| **[Learning plane](learning-plane/README.md)** | Offline: the audit ledger, reviewer queue, shadow deploy, the 100-case synthetic eval, calibration sweep, calibration → new-policy-version loop | Batch, asynchronous | Built (Audit Bot deferred — needs API budget) |

## Everything else

- **[Multi-tenancy & auth](cross-cutting/multi-tenancy-and-auth.md)** — organizations, agents, JWT sessions, and why an org-policy edit cascades to every agent underneath it
- **[LLM integration](cross-cutting/llm-integration.md)** — the real Gemini call, the canary token embedded in its system prompt, and the mock it falls back to
- **[Frontend](cross-cutting/frontend.md)** — the React app: signup/login, policy editors, the chat + pipeline-trace demo

## How a request actually flows

```
 user prompt
    │
    ▼
 ┌─────────────────────────────────────────────┐
 │ INPUT GATE  (data plane)                     │   secrets / injection / PII
 │   detectors → signals only, no decision      │   detectors, run in parallel
 └───────────────────┬───────────────────────────┘
                      ▼
 ┌─────────────────────────────────────────────┐
 │ RISK FUSION (input)                          │   block / redact / allow
 │   the ONLY place an action is decided        │
 └───────────────────┬───────────────────────────┘
          block ──────┤──────── allow / redact
      (LLM never       ▼
       called)   ┌───────────────┐
                  │ LLM call       │   Gemini, or the deterministic
                  │ (redacted      │   mock if no key is configured
                  │  prompt)       │
                  └───────┬───────┘
                          ▼
 ┌─────────────────────────────────────────────┐
 │ OUTPUT CHECKS  T0 (secrets, canary)          │   T0 → T1, T1 runs in
 │              → T1 (PII, toxicity)             │   parallel under a
 │   detectors → signals only                    │   real per-check timeout
 └───────────────────┬───────────────────────────┘
                      ▼
 ┌─────────────────────────────────────────────┐
 │ RISK FUSION (output)                         │   block / regenerate  preempt
 │   composes modifiers on a base "allow"       │   everything; redact /
 └───────────────────┬───────────────────────────┘   flag_visible / flag compose
                      ▼
        response (un-redacted for the user,
     re-redacted for anything the model itself leaked)
                      │
                      ▼
        every stage's decision + signals written to
        the hash-chained ledger (learning plane)
```

Every arrow in that diagram is real code, not a mock — this document set exists
to walk through exactly how, and just as importantly, what was deliberately
**not** built and why.

## Running it locally

```bash
# backend
cd apps/api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL, JWT_SECRET, optionally GEMINI_API_KEY
uvicorn main:app --reload --port 8000

# frontend
cd apps/web
npm install
npm run dev   # http://localhost:5173
```

No seed data exists — the first thing to do is sign up an organization through
the frontend (or `POST /auth/signup`), which is deliberate: see
[multi-tenancy & auth](cross-cutting/multi-tenancy-and-auth.md) for why the old
auto-seeded demo org was removed.

## Source of truth

The actual specification lives in [`.agents/PRD_V1_Consolidated.md`](<../.agents/PRD_V1_Consolidated (1).md>)
and the build plan in [`.agents/context.md`](../.agents/context.md) — this
`docs/` tree explains what was *actually built* against that spec, including
every place a real decision had to be made that the spec left open, and every
place something was deliberately left out. The full decision-by-decision
engineering log (bugs found, fixes verified, evidence behind each tradeoff)
lives in [`.agents/decisions/DECISIONS.md`](../.agents/decisions/DECISIONS.md);
unresolved and resolved product questions live in
[`.agents/questions/`](../.agents/questions/OPEN_QUESTIONS.md). This `docs/`
tree is the curated, judge-facing version of that same story — organized by
component instead of by chronology.
