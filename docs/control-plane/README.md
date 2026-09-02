# Control Plane

The control plane's job: turn human-authored policy into a single flat,
versioned, hashed artifact — a **bundle** — that the data plane can enforce at
request time without knowing anything about policy logic, locking, or where
the rules came from.

Source: `apps/api/control_plane/` (`resolve.py`, `compile.py`, `locks.py`),
`apps/api/routers/policies.py`, `apps/api/routers/agents.py`, schema in
`apps/api/db/migrations/0001_init.sql` + `0002_multi_tenant.sql`.

## The two-layer model

Every organization authors exactly one **org layer** — its baseline policy,
including whatever it wants to lock. Every agent (one per AI use case: a
support bot, an internal copilot, ...) authors its own **agent layer** — a
partial override on top of the org layer. `resolve()` merges the two into one
flat set of fields; `compile()` wraps that in a versioned, hashed bundle.

```yaml
# org layer — org-baseline.yaml, illustrative
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
# agent layer — support-bot.yaml, illustrative
input_checks_enabled:
  secrets: false     # attempts to override a locked field — rejected, org's true wins
  pii: false          # unlocked — agent's own choice, allowed

output_t1_checks_enabled:
  toxicity: true      # unlocked — agent turns on what org left off

output_pii_redact_threshold: 0.6   # locked at 0.8, but 0.6 is *stricter* — allowed
toxicity_mild_action: regenerate    # unlocked — agent's own pick
```

**Fields left out of the agent layer simply inherit the org's value unchanged**
— there's no need to repeat everything the org already set.

### No field for a check that doesn't exist yet

`input_checks_enabled`, `output_t0_checks_enabled`, `output_t1_checks_enabled`
only ever contain booleans for checks that are actually built. There is no
`output_t2_checks_enabled` field anywhere, because T2 (LLM-as-judge) isn't
built — adding a no-op field for it would be scaffolding for something that
doesn't exist. Same reasoning for the absence of a `grounding_threshold`
field. When T2 is built, it gets its own field then, not before.

There's also deliberately **no tier-level switch** (`checks_enabled: {t0, t1,
t2}`). Whether a tier "runs" is fully derived from whether any of its
individual checks are on — a separate tier gate would just be a second way to
express the same thing, with the two able to drift out of sync.

## Locking: three shapes, one comparator table

A layer marks a field as locked by listing its dotted path in a top-level
`locks:` array — not as an inline flag next to the value. This means a
locked field's value and its lock status can never silently drift apart: they
live in the same layer, and `compile()`/`resolve()` reject a layer whose
`locks:` entry doesn't actually resolve to a field that layer sets.

Three lock shapes exist, each with a different idea of "stricter":

| Shape | Example field | "Stricter" means |
|---|---|---|
| Numeric threshold | `output_pii_redact_threshold` | A **lower** value (fires sooner) |
| Ordered enum | `toxicity_mild_action` | Moving **up** a fixed rank (`flag_visible` → `regenerate`) |
| Boolean toggle | `input_checks_enabled.secrets` | No direction — the locked value is just pinned |

All three are handled by one small comparator (`control_plane/locks.py`), not
per-field special cases:

```python
ENUM_RANKS = {
    "toxicity_mild_action": {"flag_visible": 0, "regenerate": 1},
    "fail_mode": {"open": 0, "closed": 1},
}

def is_stricter_or_equal(path: str, new, old) -> bool:
    if path.endswith("_threshold"):
        return new <= old                     # lower = fires sooner = stricter
    if path in ENUM_RANKS:
        rank = ENUM_RANKS[path]
        return rank[new] >= rank[old]          # only allowed to move up the ranking
    return new == old                          # boolean toggle: locked value is fixed
```

Adding a new ordered-enum field later means adding one line to `ENUM_RANKS` —
not writing a new comparison function.

## `resolve()`

Merges an agent layer onto an org layer, applying the locks above:

```python
def resolve(org: dict, tenant: dict, org_name: str = "org-baseline") -> tuple[dict, list[dict]]:
    validate_locks(org, org_name)
    validate_locks(tenant, "tenant")

    merged = {k: v for k, v in deepcopy(org).items() if k != "locks"}
    events = []
    locks = set(org.get("locks", []))

    for path, tenant_value in flatten({k: v for k, v in tenant.items() if k != "locks"}).items():
        org_value = get_path(merged, path)

        if path not in locks:
            set_path(merged, path, tenant_value)          # unlocked — agent is free
            continue

        if is_stricter_or_equal(path, tenant_value, org_value):
            set_path(merged, path, tenant_value)           # tightening a lock is always allowed
        else:
            events.append({
                "field": path, "requested": tenant_value,
                "enforced": org_value, "locked_by": org_name,
            })
            # merged keeps org_value — no change
```

Running the worked example above through `resolve()` produces exactly this —
verified against the spec's own numbers, not just written to match them:

```json
{
  "input_checks_enabled": {"secrets": true, "injection": true, "pii": false},
  "output_t0_checks_enabled": {"secrets": true, "canary": true},
  "output_t1_checks_enabled": {"pii": true, "toxicity": true},
  "output_pii_redact_threshold": 0.6,
  "toxicity_mild_action": "regenerate",
  "fail_mode": "open"
}
```

plus one **clamp event** — the audit trail of an attempted override that
locking blocked:

```json
{"field": "input_checks_enabled.secrets", "requested": false, "enforced": true, "locked_by": "Acme Corp"}
```

`locked_by` is the *real organization's name*, not a hardcoded label — see
[Decisions §7](../../.agents/decisions/DECISIONS.md) for the bug this used to
be (`"org-baseline"` printed literally, regardless of which org it was).

## `compile()`

Runs `resolve()`, then:

1. Hashes the resolved **fields only** — `sha256(json.dumps(fields, sort_keys=True))` — never the metadata, so the hash can't reference itself.
2. Enforces two compile-time guards: `input_pii_review_threshold < input_pii_redact_threshold`, and the same for the output pair.
3. Wraps it all in the bundle format the data plane actually reads:

```json
{
  "_meta": {
    "policy_name": "support-bot",
    "version": 2,
    "hash": "sha256:e62ce07f...",
    "source_layers": ["Acme Corp", "support-bot"],
    "compiled_at": "2026-09-01T12:59:22Z"
  },
  "fields": { "...": "the resolved fields above" }
}
```

Layer-authoring files stay human-readable YAML; the compiled bundle is
machine-only JSON — two formats for two different audiences, not because YAML
and JSON are interchangeable choices, but because a compliance officer edits
one and a request-time enforcer reads the other.

**A known, deliberately un-fixed gap:** `compile()` does not reject a bundle
where `toxicity_regenerate_threshold` or `toxicity_block_threshold` is left
`null`, even though the data plane's fusion logic needs a real number to
compare a toxicity score against. This is flagged as a `TODO` in the code
itself rather than silently patched with an invented default — the PRD calls
this out as a genuinely open question (who decides the default, and is a
silent default even acceptable for a safety threshold), not something to
resolve unilaterally.

## Multi-tenancy: one org layer per organization, cascading recompilation

V1 originally had exactly one hardcoded org and one hardcoded agent
(`org-baseline` / `support-bot`), auto-seeded on startup. That's gone —
organizations now sign up for real, and every org authors its own policy from
scratch. See [multi-tenancy & auth](../cross-cutting/multi-tenancy-and-auth.md)
for the full auth story; the control-plane-relevant part is this:

An org can have any number of agents, and an org-level policy edit can affect
*every* agent under that org (a newly tightened lock, a changed threshold) —
not just the one someone happens to be looking at. So `POST /policies/org`
recompiles every agent's bundle in the same database transaction as the
org-layer write:

```python
async def post_org_policy(body: LayerIn, org=Depends(get_current_org)):
    async with pool.acquire() as conn, conn.transaction():
        layer_row = await insert_org_layer(conn, org["id"], body.content)
        agents = await list_agents(conn, org["id"])
        for agent in agents:
            await compile_and_persist_for_agent(conn, org["id"], agent["id"], org["name"], agent["name"])
```

An agent-level edit (`POST /agents/{id}/policy`), by contrast, only recompiles
that one agent.

## A real bug this design caught, not just tolerated

Both endpoints do two writes: the layer, then (if compile succeeds) the
bundle. Testing a deliberately invalid edit revealed that the layer write was
committing even when the subsequent compile was rejected — leaving a policy
layer version in the database with no bundle to match it, a silent
inconsistency between "what was requested" and "what's actually enforced."
Fixed by wrapping both writes in one transaction, so a rejected compile rolls
back the layer write too. Full writeup:
[Decisions §2](../../.agents/decisions/DECISIONS.md).

## API surface

| Method | Path | Does |
|---|---|---|
| `GET` `/policies/org` | Latest org layer for the authenticated organization |
| `POST` `/policies/org` | New org-layer version; recompiles every agent |
| `GET` `/agents/{id}/policy` | Latest agent layer + bundle |
| `POST` `/agents/{id}/policy` | New agent-layer version; recompiles that agent. Body accepts optional `promote` (default `true`) and `label` — see below |
| `GET` `/agents/{id}/bundles` | Every compiled version for this agent, newest first — label, hash, `is_production`, `compiled_at` |
| `POST` `/agents/{id}/bundles/{bundle_id}/promote` | Flips `is_production` to this version, transactionally unsetting whatever was production before |

All ownership-checked against the caller's JWT — an organization can only ever
read or compile its own agents.

## Multi-version bundles and `is_production`

Added for the learning plane (`.agents/Learning_Plane_PRD_Draft.md` §1.1):
`bundles` gained `is_production BOOLEAN` and `label TEXT`. Multiple compiled
versions can exist per agent at once — not just a linear "latest wins"
history — and the data plane's `POST /check` loads whichever one is flagged
`is_production`, never simply the most recent row.

The two existing policy-edit endpoints above still **auto-promote** by
default (`promote: true`), so editing policy through the ordinary screens
keeps today's behavior — compiling makes it live immediately. The one caller
that passes `promote: false` is the learning plane's calibration flow: a
calibration-proposed version is stored and addressable, but doesn't affect
live traffic until a human explicitly promotes it via `POST
/agents/{id}/bundles/{bundle_id}/promote`. Full mechanics, and how this
composes with shadow deploy, are in
[`docs/learning-plane/README.md`](../learning-plane/README.md).

## What's explicitly out of scope for V1

- **A jurisdiction layer.** V1 has exactly two layers (org, agent). A third
  layer (e.g. EU-specific rules sitting between org and agent) is a real,
  named extension point in the PRD, just not built.
- **Diffing two versions.** Every layer and bundle version is kept (nothing
  overwrites), and promoting an older version is effectively a rollback, but
  there's no endpoint that diffs two versions' fields directly — shadow
  deploy (learning plane) answers a narrower, decision-level version of this
  question instead ("what would change if we switched"), not a raw field diff.
- **Real approval workflow.** A clamp event is recorded when a lock blocks an
  override, but nothing routes that to a human for review; it's visible in the
  API response and the bundle's `clamp_events`, not surfaced anywhere else yet.
