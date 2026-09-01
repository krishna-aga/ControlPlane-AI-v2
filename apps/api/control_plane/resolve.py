from copy import deepcopy

from control_plane.locks import is_stricter_or_equal


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, path))
        else:
            out[path] = v
    return out


def get_path(d: dict, path: str):
    node = d
    for part in path.split("."):
        node = node[part]
    return node


def set_path(d: dict, path: str, value) -> None:
    parts = path.split(".")
    node = d
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def validate_locks(layer: dict, layer_name: str) -> None:
    """compile()/resolve() guard: a locks: entry must resolve to a field
    actually set in that same layer (PRD §3.2, context.md rule 4)."""
    fields = {k: v for k, v in layer.items() if k != "locks"}
    flat = flatten(fields)
    for path in layer.get("locks", []):
        if path not in flat:
            raise ValueError(
                f"{layer_name}: locks entry '{path}' does not resolve to a field set in this layer"
            )


def resolve(org: dict, tenant: dict) -> tuple[dict, list[dict]]:
    validate_locks(org, "org-baseline")
    validate_locks(tenant, "tenant")

    merged = {k: v for k, v in deepcopy(org).items() if k != "locks"}
    events = []
    locks = set(org.get("locks", []))

    tenant_fields = {k: v for k, v in tenant.items() if k != "locks"}
    for path, tenant_value in flatten(tenant_fields).items():
        org_value = get_path(merged, path)

        if path not in locks:
            set_path(merged, path, tenant_value)  # unlocked — tenant is free
            continue

        if is_stricter_or_equal(path, tenant_value, org_value):
            set_path(merged, path, tenant_value)  # tightening a lock is always allowed
        else:
            events.append(
                {
                    "field": path,
                    "requested": tenant_value,
                    "enforced": org_value,
                    "locked_by": "org-baseline",
                }
            )
            # merged keeps org_value — no change

    return merged, events
