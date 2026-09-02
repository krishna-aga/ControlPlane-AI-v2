# ControlPlane.ai

A governance/safety proxy that sits between an enterprise and its LLM calls.
An organization signs up, authors one org-level safety policy (PII handling,
prompt injection defense, toxicity handling, secret leakage prevention),
locks whatever parts must never be loosened, then creates any number of AI
agents (a support bot, an internal copilot, ...) that each tune the rest for
their own use case. Every real chat request runs through the compiled,
versioned, hashed result of that policy — rules are data, not hardcoded
checking logic.

The system is organized into three planes:

- **Control plane** — authors and resolves policy (org-level rules, per-agent
  overrides, locking, compiling to a versioned, hashed bundle). Human-speed.
- **Data plane** — applies the compiled bundle to a live prompt/response
  pair. Millisecond-scale, every request.
- **Learning plane** — offline analysis: the audit ledger, reviewer queue,
  shadow deploy, a 100-case synthetic eval, calibration, and the
  calibration → new-policy-version loop.

For the full architectural deep dive (with a request-flow diagram) see
[`docs/README.md`](docs/README.md). For current build status and internal
project notes see [`.agents/context.md`](.agents/context.md).

Repository: https://github.com/krishna-aga/ControlPlane-AI-v2

## Table of contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Running the project](#running-the-project)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Troubleshooting & FAQ](#troubleshooting--faq)
- [Maintainers](#maintainers)

## Requirements

This project has no requirements beyond a standard Python + Node toolchain
and a Postgres database:

- **Python 3.11+** (developed against 3.14)
- **Node.js 20+** and **npm**
- **A Postgres database.** Development used [Neon](https://neon.tech)
  (serverless Postgres) — any Postgres 14+ instance works, since nothing in
  the schema is Neon-specific.
- **(Optional) a Gemini API key.** Without one, `/check` falls back to a
  deterministic mock LLM — the whole app runs and demos fully with no key at
  all. See [`docs/cross-cutting/llm-integration.md`](docs/cross-cutting/llm-integration.md).

No special requirements beyond the above — no Docker, no external services,
no message queue.

## Installation

Clone the repository, then install both apps' dependencies. A root
`package.json` wraps both the Python and Node install steps:

```bash
git clone https://github.com/krishna-aga/ControlPlane-AI-v2.git
cd ControlPlane-AI-v2
npm run install:all
```

This does three things:
1. Creates a Python virtualenv at `apps/api/.venv` and installs
   `apps/api/requirements.txt` into it.
2. Copies `apps/api/.env.example` to `apps/api/.env` (only if `.env` doesn't
   already exist — never overwrites your own config).
3. Runs `npm install` for both the root and `apps/web`.

**Before first run**, fill in `apps/api/.env`:

```bash
DATABASE_URL=postgresql://user:password@ep-xxxx.neon.tech/controlplane?sslmode=require
JWT_SECRET=change-me-to-a-long-random-string
GEMINI_API_KEY=      # optional — leave blank to use the mock LLM
GEMINI_MODEL=        # optional — defaults to a specific model in code
```

Then apply the database migrations, in order, against that `DATABASE_URL`
(there is no migration runner yet — this is a manual step, same as every
migration so far):

```bash
apps/api/db/migrations/0001_init.sql
apps/api/db/migrations/0002_multi_tenant.sql
apps/api/db/migrations/0003_learning_plane.sql
apps/api/db/migrations/0004_mock_eval_only.sql
apps/api/db/migrations/0005_demo_org.sql
```

e.g. `psql "$DATABASE_URL" -f apps/api/db/migrations/0001_init.sql`, repeated
per file in numeric order.

## Running the project

Once installed and migrated, start both the API and the frontend together
from the repo root:

```bash
npm run dev
```

This runs the FastAPI backend (`http://localhost:8000`, with `--reload`) and
the Vite dev server (`http://localhost:5173`) concurrently, labeled and
color-coded in one terminal. To run them separately instead:

```bash
npm run dev:api   # FastAPI on :8000
npm run dev:web   # Vite on :5173
```

Open `http://localhost:5173`. There's no seed data for real organizations —
sign up first (through the UI, or `POST /auth/signup`), then create an
agent and author a policy for it. The one deliberate exception is the
auto-seeded, credential-free **Demo Org**: the login screen's "View demo"
button logs straight into it to show the Learning Plane's calibration
dashboard against a fixed sample policy and a 100-case mock dataset, without
needing to sign up or author a policy first.

## Configuration

All runtime configuration lives in `apps/api/.env` (see `.env.example` for
the full annotated list):

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Yes | Postgres connection string (Neon or otherwise) |
| `JWT_SECRET` | Yes | Signs session tokens (7-day expiry, bcrypt + JWT, no RBAC) |
| `GEMINI_API_KEY` | No | Enables real LLM calls; omit to use the built-in deterministic mock |
| `GEMINI_MODEL` | No | Overrides the default Gemini model |

Beyond environment variables, the actual safety behavior (which checks run,
at what thresholds, what's locked) is **authored as policy data through the
app itself**, not as configuration files — see
[`docs/control-plane/README.md`](docs/control-plane/README.md) for the org
policy / agent policy / locking model. The frontend's policy screens edit
this policy directly (as YAML) and compile it into a versioned bundle; there
is no separate config file to hand-edit for check behavior.

## Project structure

```
apps/
  api/            FastAPI backend (control plane, data plane, learning plane)
    control_plane/  policy resolution + compilation
    data_plane/     detectors, fusion (the only place an action is decided), the live pipeline
    learning_plane/ audit ledger analysis, mock calibration, demo org seed
    db/             connection, queries, migrations
    routers/        HTTP route handlers
  web/            React (Vite) frontend
    src/screens/    one file per page (login, policy editors, chat demo, learning plane)
    src/api/        fetch wrapper / API client
    src/components/ shared UI (pipeline trace panel, calibration chart)
docs/             judge-facing architecture writeup, organized by plane
.agents/          internal working notes, PRDs, and project context (see context.md)
```

See [`docs/README.md`](docs/README.md) for how a request actually flows
through the system end to end, with a diagram.

## Troubleshooting & FAQ

**The backend fails to start / crashes with a connection timeout.**
Serverless Postgres (Neon) suspends its compute when idle and needs a moment
to wake on the first connection after a period of inactivity. The backend
already retries pool creation with backoff for this — if it still fails,
just try starting it again; a second attempt after the database has woken up
almost always succeeds.

**The frontend shows `ERR_CONNECTION_REFUSED` on port 8000.**
The backend isn't running (or crashed on startup — check its terminal
output). Start it with `npm run dev:api` and confirm `http://localhost:8000/docs`
loads before retrying the frontend action.

**Chat responses look scripted/repetitive.**
`GEMINI_API_KEY` is unset (or invalid), so `/check` is using the
deterministic mock LLM. This is intentional fallback behavior, not a bug —
set a real key in `apps/api/.env` and restart the backend for real model
calls. See [`docs/cross-cutting/llm-integration.md`](docs/cross-cutting/llm-integration.md).

**I don't see any organizations/agents after installing.**
Correct — there's no seed data for real tenants by design (see
[`docs/cross-cutting/multi-tenancy-and-auth.md`](docs/cross-cutting/multi-tenancy-and-auth.md)
for why). Sign up your own organization first. If you just want to see the
Learning Plane's calibration dashboard without signing up, use "View demo"
on the login screen instead.

**Where are the automated tests?**
There isn't a committed automated test suite yet — verification so far has
been ad-hoc scripts run during development. See `.agents/context.md` for the
current state of this gap.

## Maintainers

- [@krishna-aga](https://github.com/krishna-aga)
