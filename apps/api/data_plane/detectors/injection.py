import re

# Vendored from PRD §4.2, hand-rolled, no library.
INJECTION_PATTERNS = [
    r"ignore (all|your|the) (previous|prior|above) instructions",
    r"disregard (the|all|your) (above|previous|prior)",
    r"you are now (a|an)?",
    r"print your system prompt",
    r"reveal your (instructions|system prompt|prompt)",
    r"forget (everything|all) (you|that)",
    r"new instructions:",
    r"act as (if|though)",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def scan(text: str) -> list[dict]:
    matches = []
    for pattern in _COMPILED:
        for m in pattern.finditer(text):
            matches.append({"type": "injection", "score": 1.0, "span": m.span(), "detail": pattern.pattern})
    return matches
