import re

# Vendored from PRD §4.2, adapted from detect-secrets patterns (not a runtime
# dependency). Reused verbatim at T0 output (§4.3) against the model's response.
SECRET_PATTERNS = {
    "aws_key": r"(?:A3T[A-Z0-9]|ABIA|ACCA|AKIA|ASIA)[0-9A-Z]{16}",
    "github_token": r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36}",
    "slack_token": r"xox(?:a|b|p|o|s|r)-(?:\d+-)+[a-z0-9]+",
    "openai_key": r"sk-[A-Za-z0-9-_]*[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}",
    "jwt": r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*?",
    "stripe_key": r"(?:r|s)k_live_[0-9a-zA-Z]{24}",
    "private_key_header": r"-----BEGIN (RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----",
}

_COMPILED = {name: re.compile(pattern) for name, pattern in SECRET_PATTERNS.items()}


def scan(text: str) -> list[dict]:
    """Pure detector — returns raw matches (with spans, for redaction), never
    an action. type='secrets', score=1.0 (deterministic, no partial confidence)."""
    matches = []
    for name, pattern in _COMPILED.items():
        for m in pattern.finditer(text):
            matches.append({"type": "secrets", "score": 1.0, "span": m.span(), "detail": name})
    return matches
