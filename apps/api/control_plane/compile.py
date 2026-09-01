import hashlib
import json
from datetime import datetime, timezone

from control_plane.resolve import resolve


class CompileError(ValueError):
    pass


def _hash_fields(fields: dict) -> str:
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
    return f"sha256:{digest}"


def compile_bundle(
    org: dict, tenant: dict, policy_name: str, version: int, source_layers: list[str]
) -> tuple[dict, list[dict]]:
    fields, clamp_events = resolve(org, tenant, org_name=source_layers[0])

    if fields["input_pii_review_threshold"] >= fields["input_pii_redact_threshold"]:
        raise CompileError("input_pii_review_threshold must be < input_pii_redact_threshold")
    if fields["output_pii_review_threshold"] >= fields["output_pii_redact_threshold"]:
        raise CompileError("output_pii_review_threshold must be < output_pii_redact_threshold")

    # Known open gap (PRD §3.4/§8): does NOT reject a null toxicity_regenerate_threshold/
    # toxicity_block_threshold, even though decide_t1_output() needs a real number to
    # compare against. Flagged per context.md rule 7 rather than silently resolved here.

    bundle = {
        "_meta": {
            "policy_name": policy_name,
            "version": version,
            "hash": _hash_fields(fields),
            "source_layers": source_layers,
            "compiled_at": datetime.now(timezone.utc).isoformat(),
        },
        "fields": fields,
    }
    return bundle, clamp_events
