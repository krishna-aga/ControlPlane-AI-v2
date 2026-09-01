"""PII detection via Presidio (PRD §4.2/§4.4) — NER-based, single unified check
reused at both input and output stages.

Model: en_core_web_lg, not the PRD's originally-suggested en_core_web_sm. PRD §8
flagged this as an open, unbenchmarked tradeoff — benchmarked directly (see
docs/DECISIONS.md): sm mislabels South Asian names as ORGANIZATION rather than
PERSON, missing them entirely with the PII entity allowlist below; lg gets this
right, and warm per-call latency between the two is within noise (~4ms either
way — the "9ms" figure was never actually a live-tradeoff blocker). Chosen over
sm on real evidence, not by default.
"""

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

# Presidio's defaults also flag DATE_TIME/URL/NRP — noise for "is this a PII
# leak" purposes. Restricted to entity types that represent genuine PII risk.
# LOCATION is included at full confidence — a deliberate product call, not the
# default: it reliably catches real addresses (e.g. "Dehradun, Uttarakhand" in
# a home address), at the accepted cost of also flagging harmless place
# mentions in ordinary questions ("what is the capital of France?"). See
# OPEN_QUESTIONS.md / DECISIONS.md for the false-positive-vs-missed-address
# tradeoff this was chosen over.
_ENTITIES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "IBAN_CODE",
    "IP_ADDRESS",
    "LOCATION",
]

_analyzer: AnalyzerEngine | None = None


def _get_analyzer() -> AnalyzerEngine:
    global _analyzer
    if _analyzer is None:
        config = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}],
        }
        nlp_engine = NlpEngineProvider(nlp_configuration=config).create_engine()
        _analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
    return _analyzer


def warm_up() -> None:
    """First analyze() call on a fresh spaCy pipeline costs ~4-5s; call this
    once at app startup so no real request pays it (PRD §4.4: 'models warmed
    at startup')."""
    _get_analyzer().analyze(text="warm up", language="en", entities=_ENTITIES)


def scan(text: str) -> list[dict]:
    results = _get_analyzer().analyze(text=text, language="en", entities=_ENTITIES)
    return [
        {"type": "PII", "score": r.score, "span": (r.start, r.end), "detail": r.entity_type}
        for r in results
    ]
