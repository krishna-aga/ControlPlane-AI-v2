"""FN-rate, FP-rate, and per-check calibration — all computed directly from
the 100-case mock dataset (PRD §3/§4/§5/§6). Pure arithmetic and pure
fusion-logic recomputation; no detector call, no LLM call, no database read
beyond the bundle's own resolved fields (used only by the aggregate FN/FP
cards below, not by the per-check calibration, which is pure dataset
arithmetic).

p50/p95 latency is deliberately absent — there is no live execution left to
measure timing from, and fabricating a placeholder number was explicitly
rejected in the PRD revision rather than silently added back.
"""

from learning_plane.mock_eval import load_dataset

# ---- Aggregate FN/FP rate — headline metric cards (PRD §3/§4) ----
# Unaffected by the per-check calibration rework below: still whole-pipeline
# comparisons against the derived `ground_truth_risky` (any check true).


def _is_true_allow(action: str, modifiers: list[str]) -> bool:
    return action == "allow" and not modifiers


def _is_flagged(action: str, modifiers: list[str]) -> bool:
    """Anything the pipeline didn't just silently allow — a block/regenerate
    outright, or an allow with at least one stacked modifier."""
    return action in ("block", "regenerate") or bool(modifiers)


def compute_fn_rate(cases: list[dict]) -> dict:
    """PRD §3: true-allow-only — anything already flagged/redacted/blocked
    was caught, not missed, so it's excluded from the denominator."""
    eligible = [c for c in cases if _is_true_allow(c["actual_action"], c["actual_modifiers"])]
    missed = [c for c in eligible if c["ground_truth_risky"]]
    rate = (len(missed) / len(eligible)) if eligible else None
    return {"fn_rate": rate, "eligible_count": len(eligible), "missed_count": len(missed)}


def compute_fp_rate(cases: list[dict]) -> dict:
    """PRD §4: ground-truth comparison on cases fusion flagged, redacted, or
    blocked."""
    flagged = [c for c in cases if _is_flagged(c["actual_action"], c["actual_modifiers"])]
    false_positives = [c for c in flagged if not c["ground_truth_risky"]]
    rate = (len(false_positives) / len(flagged)) if flagged else None
    return {"fp_rate": rate, "flagged_count": len(flagged), "false_positive_count": len(false_positives)}


def compute_metrics(cases: list[dict]) -> dict:
    return {
        "fn_rate": compute_fn_rate(cases),
        "fp_rate": compute_fp_rate(cases),
        "case_count": len(cases),
    }


# ---- Per-check calibration (PRD §6, revised: isolated per-check comparison) ----
# For a swept check, at each candidate threshold: does that case's own
# mock-signal score for that check cross the threshold ("fires"), compared
# directly against that case's own ground_truth_checks[check] — not the
# whole-row action. A case where ground truth is true and the signal doesn't
# fire is a false negative for that check; ground truth false and it fires
# anyway is a false positive.

# Fields/ranges chosen per-check: output_pii keeps the previously-decided
# 0.8-1.0 range (respects the org-baseline lock on that one field in
# demo_seed.py's DEMO_ORG_POLICY — tighten-only at 0.8). input_pii and
# output_toxicity aren't locked, so they use the PRD's own generic
# illustrative range for an unlocked field (0.3-0.9) instead.
CONTINUOUS_CHECK_SPECS = {
    "output_pii": {"field": "output_pii_redact_threshold", "min": 0.8, "max": 1.0, "step": 0.02, "compare": "gt"},
    "input_pii": {"field": "input_pii_redact_threshold", "min": 0.3, "max": 0.9, "step": 0.05, "compare": "gt"},
    "output_toxicity": {"field": "toxicity_regenerate_threshold", "min": 0.3, "max": 0.9, "step": 0.05, "compare": "gte"},
}

# The remaining 4 of V1's 7 checks fire at a fixed cutoff (PRD §5.1: regex or
# exact-match checks, binary score 0/1) — there's no bundle threshold to
# sweep, so these get a single FN/FP number each instead of a curve.
BINARY_CHECKS = ["input_secrets", "output_secrets", "input_injection", "output_canary"]
_BINARY_CUTOFF = 1.0


def _mock_score(case: dict, check: str) -> float | None:
    return next(s["score"] for s in case["mock_signals"] if s["source"] == check)


def _fires(score: float | None, cutoff: float, compare: str) -> bool:
    if score is None:
        return False
    return score >= cutoff if compare == "gte" else score > cutoff


def _check_rates(dataset: list[dict], check: str, cutoff: float, compare: str) -> dict:
    positive_count = false_negative_count = negative_count = false_positive_count = 0
    for case in dataset:
        ground_truth = case["ground_truth_checks"][check]
        fires = _fires(_mock_score(case, check), cutoff, compare)
        if ground_truth:
            positive_count += 1
            if not fires:
                false_negative_count += 1
        else:
            negative_count += 1
            if fires:
                false_positive_count += 1

    return {
        "fn_rate": (false_negative_count / positive_count) if positive_count else None,
        "positive_count": positive_count,
        "false_negative_count": false_negative_count,
        "fp_rate": (false_positive_count / negative_count) if negative_count else None,
        "negative_count": negative_count,
        "false_positive_count": false_positive_count,
    }


def compute_check_sweep(check: str) -> dict:
    spec = CONTINUOUS_CHECK_SPECS[check]
    dataset = load_dataset()
    points = []
    threshold = spec["min"]
    while threshold <= spec["max"] + 1e-9:
        t = round(threshold, 2)
        points.append({"threshold": t, **_check_rates(dataset, check, t, spec["compare"])})
        threshold += spec["step"]
    return {"field": spec["field"], "points": points}


def compute_binary_check_rates(check: str) -> dict:
    return _check_rates(load_dataset(), check, _BINARY_CUTOFF, "gte")


def compute_full_calibration() -> dict:
    return {
        "continuous": {check: compute_check_sweep(check) for check in CONTINUOUS_CHECK_SPECS},
        "binary": {check: compute_binary_check_rates(check) for check in BINARY_CHECKS},
    }
