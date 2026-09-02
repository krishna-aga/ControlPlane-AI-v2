"""Runs the 100-case mock test set (PRD §5) through the real fusion logic —
`decide_input`, `decide_t0_output`, `decide_t1_output`, `decide_output_fusion`
— and nothing else. No detector call, no LLM call, no ledger write, ever.

Each case ships pre-authored `mock_signals` in the real signal shape
(`{source, type, score, status}`), so the only "execution" here is feeding
those stored values through the same pure decision functions the live
pipeline uses. This is what replaced the earlier design, which ran all 100
cases through the real `/check` pipeline (real Gemini calls) and burned
through a provider's free-tier daily quota across two attempts without
completing — see `.agents/questions/OPEN_QUESTIONS.md`.
"""

import json
from pathlib import Path

from data_plane import fusion

DATASET_PATH = Path(__file__).parent / "data" / "synthetic_test_set_100.json"


def load_dataset() -> list[dict]:
    return json.loads(DATASET_PATH.read_text())


def _sig(entry: dict) -> dict:
    return {"type": entry["type"], "score": entry["score"], "status": entry["status"]}


def evaluate_case(case: dict, bundle_fields: dict) -> tuple[str, list[str]]:
    """Maps one case's `mock_signals` — one real entry per one of the 7 V1
    checks, keyed by `source` (PRD §5.1) — onto the real fusion functions'
    expected signal shapes, then runs the exact decision sequence
    `data_plane/pipeline.py` runs for a live request: input gate first (a
    block there short-circuits everything), then T0/T1 output fusion.
    Returns the same `(action, modifiers)` shape `/check` returns at its
    top level.
    """
    by_source = {s["source"]: s for s in case["mock_signals"]}

    input_signals = [_sig(by_source["input_secrets"]), _sig(by_source["input_injection"]), _sig(by_source["input_pii"])]
    input_action, _ = fusion.decide_input(input_signals, bundle_fields)
    if input_action == "block":
        return "block", []

    t0_signals = [_sig(by_source["output_secrets"]), _sig(by_source["output_canary"])]
    t1_signals = [_sig(by_source["output_toxicity"]), _sig(by_source["output_pii"])]

    t0_action = fusion.decide_t0_output(t0_signals)
    t1_action, t1_review_needed = fusion.decide_t1_output(t1_signals, bundle_fields, retried=False)
    fused = fusion.decide_output_fusion(t0_action, t1_action, t1_review_needed, bundle_fields)
    return fused["action"], fused["modifiers"]


def run_mock_eval(bundle_fields: dict) -> list[dict]:
    """Evaluates every case in the dataset against one bundle's resolved
    fields. Pure, synchronous, no I/O beyond reading the static JSON file —
    safe to call on every request."""
    results = []
    for case in load_dataset():
        action, modifiers = evaluate_case(case, bundle_fields)
        results.append({
            "case_id": case["id"],
            "prompt": case["prompt"],
            "category_planted": case["category_planted"],
            "ground_truth_risky": any(case["ground_truth_checks"].values()),
            "correct_base_action": case["correct_base_action"],
            "correct_modifiers": case["correct_modifiers"],
            "actual_action": action,
            "actual_modifiers": modifiers,
        })
    return results
