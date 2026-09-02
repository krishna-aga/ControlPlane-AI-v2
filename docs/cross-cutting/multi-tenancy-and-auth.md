# Multi-tenancy & Auth

Not one of the three named planes, but load-bearing infrastructure underneath
all of them — this is what turned V1 from "one hardcoded org, one hardcoded
agent" into a real product multiple organizations can actually use at once.

Source: `apps/api/auth.py`, `apps/api/routers/auth.py`,
`apps/api/routers/agents.py`, `apps/api/db/migrations/0002_multi_tenant.sql`.

## Why this exists

V1's first working version had exactly one org layer and one agent layer,
both auto-seeded from YAML files on disk at startup
(`db/seed.py`, since deleted). That was correct for proving the pipeline, but
it isn't a product — nobody signs up, nobody owns their own policy, there's no
isolation between two different companies' agents. This layer replaces that
with real signup/login and per-organization data isolation.

## Schema: organizations and agents as first-class rows

```sql
CREATE TABLE organizations (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agents (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    name            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, name)
);
```

`policy_layers` and `bundles` (see [control plane](../control-plane/README.md))
were altered to carry `organization_id`/`agent_id` instead of the old
hardcoded `name` string they used to be keyed by.

**One real subtlety in that migration:** a plain `UNIQUE(agent_id, version)`
doesn't work for the org-layer half of `policy_layers`, since `agent_id` is
`NULL` for an org layer and Postgres treats `NULL != NULL` in uniqueness
checks — two org-layer rows for the same org could otherwise both claim
version 1. Fixed with partial unique indexes instead:

```sql
CREATE UNIQUE INDEX idx_org_layer_version   ON policy_layers (organization_id, version) WHERE layer_type = 'org';
CREATE UNIQUE INDEX idx_agent_layer_version ON policy_layers (agent_id, version)        WHERE layer_type = 'agent';
```

## Auth: bcrypt + JWT, nothing more exotic

```python
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def create_token(organization_id: int) -> str:
    payload = {"organization_id": organization_id,
               "exp": datetime.now(timezone.utc) + timedelta(hours=24*7)}
    return jwt.encode(payload, _secret(), algorithm="HS256")
```

`get_current_org` is a FastAPI dependency, attached to every protected route
via `Depends(get_current_org)` — it decodes the bearer token, loads the
organization row, and 401s if either the token or the org is invalid. Every
policy, agent, and check endpoint uses it; there is no endpoint that reads or
writes another organization's data by design, not by convention — ownership
is checked in code on every single request (`agent["organization_id"] !=
org["id"]` → 404, not 403, so a probing request can't even confirm another
org's agent ID exists).

## A real production bug this surfaced, not a design flaw

Neon's pooled Postgres connection silently drops idle connections server-side
— normal serverless-Postgres behavior. `get_current_org` runs on *every*
authenticated request, and an unhandled `ConnectionDoesNotExistError` there
escaped all the way to Starlette's outermost error handler, which sits
*outside* the app's CORS middleware. The resulting 500 had no CORS headers,
and the browser reported that as "blocked by CORS policy" — which is why this
looked like a networking bug in the frontend when the real cause was an
unhandled database error in the auth path. Fixed with `max_inactive_connection_lifetime=60`
on the pool (proactively recycles idle connections) plus a `with_db_retry()`
wrapper that retries this specific class of error once, applied to
`get_current_org` since it's the highest-traffic path in the whole app.

## Cascading recompilation

An org can have any number of agents. An org-policy edit (a newly tightened
lock, a changed threshold) can affect every one of them, not just whichever
agent someone happens to be looking at — so `POST /policies/org` recompiles
every agent's bundle in the same transaction as the org-layer write. See
[control plane](../control-plane/README.md#multi-tenancy-one-org-layer-per-organization-cascading-recompilation)
for the code.

## API surface

| Method | Path | Auth required | Does |
|---|---|---|---|
| `POST` | `/auth/signup` | No | Create an organization, return a token |
| `POST` | `/auth/login` | No | Verify credentials, return a token |
| `POST` | `/auth/demo-login` | No, no credentials at all | Token for the single auto-seeded Demo Org — see below |
| `GET`/`POST` | `/agents` | Yes | List / create agents for the caller's org |
| `GET` | `/agents/{id}` | Yes, ownership-checked | Agent detail + latest layer + bundle |

## The one deliberate exception to "no seed data": the Demo Org

"No seed data" (above) is still the rule for every real organization — a
fresh database has zero *real* orgs until someone signs up. One narrow,
named exception: `organizations.is_demo` (at most one row, enforced by a
partial unique index) marks a single auto-seeded "Demo Org" + "Demo Agent"
pair, created idempotently on every startup by
`learning_plane/demo_seed.py` if it doesn't already exist.

This isn't a return to the old hardcoded-seed design — it exists for one
specific reason: the learning plane's 100-case mock calibration dataset
(`docs/learning-plane/README.md`) is authored against one exact policy, and
is only a meaningful demo against an agent actually running that policy.
`POST /auth/demo-login` hands out a token for this org with no credential
check at all (there's nothing behind it worth protecting — it's the fixed
sample policy the PRD describes, not a real tenant's data), which is what
the frontend's "View demo" button on the login screen calls. Every mock-eval
endpoint (`routers/learning.py`) independently checks `org["is_demo"]` and
403s otherwise, so even a direct API call from a real org can't pull up
calibration numbers that don't apply to its own policy.

## What's out of scope

- **No RBAC.** One organization = one login. There's no concept of multiple
  human users within an org, or different permission levels — explicitly out
  of scope per the PRD (§7: "real authentication/RBAC on who can edit which
  policy layer" is V3 territory).
- **No password reset / email verification flow.** Signup and login exist;
  account recovery doesn't.
- **JWT has no revocation.** A token is valid until it expires (7 days) or the
  organization row is deleted — there's no server-side session table to
  invalidate a token early.
