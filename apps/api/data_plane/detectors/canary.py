# Exact string match against a token planted in the system prompt (PRD §4.3).
# V1: single fixed token, no per-bundle/per-request canary provisioning yet.
CANARY_TOKEN = "cnry_7f3a9b"


def scan(text: str) -> list[dict]:
    idx = text.find(CANARY_TOKEN)
    if idx == -1:
        return []
    return [{"type": "canary_leak", "score": 1.0, "span": (idx, idx + len(CANARY_TOKEN)), "detail": "canary"}]
