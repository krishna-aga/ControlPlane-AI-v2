"""The only place an action is ever decided (PRD core rule). No detector import,
no branching beyond scoring signals into an action. Ported from PRD §4.2/4.3/4.4/4.5.
"""


def decide_input(signals: list[dict], bundle: dict) -> tuple[str, bool]:
    active = [s for s in signals if s["status"] != "disabled"]

    if any(s["type"] == "secrets" and s["score"] is not None and s["score"] >= 1.0 for s in active):
        return "block", False
    if any(s["type"] == "injection" and s["score"] is not None and s["score"] >= 1.0 for s in active):
        return "block", False

    pii_signals = [s for s in active if s["type"] == "PII"]
    max_score = max((s["score"] for s in pii_signals if s["score"] is not None), default=0)

    if max_score > bundle["input_pii_redact_threshold"]:
        return "redact", False
    elif max_score > bundle["input_pii_review_threshold"]:
        return "allow", True
    else:
        return "allow", False


def decide_t0_output(signals: list[dict]) -> str:
    active = [s for s in signals if s["status"] != "disabled"]
    if any(s["type"] == "canary_leak" and s["score"] is not None and s["score"] >= 1.0 for s in active):
        return "block"
    if any(s["type"] == "secrets" and s["score"] is not None and s["score"] >= 1.0 for s in active):
        return "redact"
    return "allow"


def decide_t1_output(signals: list[dict], bundle: dict, retried: bool = False) -> tuple[str, bool]:
    action = "allow"
    review_needed = False

    tox_signals = [s for s in signals if s["type"] == "toxicity" and s["status"] != "disabled"]
    tox_score = max((s["score"] for s in tox_signals if s["score"] is not None), default=0)
    tox_unknown = any(s["status"] == "timeout" for s in tox_signals)

    if tox_unknown:
        action = "block" if retried else "regenerate"
    elif tox_score >= bundle["toxicity_block_threshold"]:
        action = "block"
    elif tox_score >= bundle["toxicity_regenerate_threshold"]:
        if bundle["toxicity_mild_action"] == "flag_visible":
            action = "flag_visible"
        else:
            action = "block" if retried else "regenerate"

    if action == "allow":
        pii_signals = [s for s in signals if s["type"] == "PII" and s["status"] != "disabled"]
        pii_score = max((s["score"] for s in pii_signals if s["score"] is not None), default=0)

        if pii_score > bundle["output_pii_redact_threshold"]:
            action = "redact"
        elif pii_score > bundle["output_pii_review_threshold"]:
            review_needed = True

    return action, review_needed


def decide_output_fusion(t0_action: str, t1_action: str, t1_review_needed: bool, bundle: dict) -> dict:
    if t0_action == "block" or t1_action == "block":
        return {"action": "block", "modifiers": []}
    if t1_action == "regenerate":
        return {"action": "regenerate", "modifiers": []}

    modifiers = []
    if t0_action == "redact" or t1_action == "redact":
        modifiers.append("redact")
    if t1_action == "flag_visible":
        modifiers.append("flag_visible")
    if t1_review_needed:
        modifiers.append("flag")

    return {"action": "allow", "modifiers": modifiers}
