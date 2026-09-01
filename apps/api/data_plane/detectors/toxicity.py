"""Real toxicity classifier (PRD §4.4), replacing the keyword stub. DistilBERT-
based, small enough to run warm in ~20-30ms on CPU and to warm up fast at
startup — chosen specifically to avoid taking on a second external API/vendor
dependency (Perspective API, OpenAI moderation) on top of the still-undecided
LLM vendor question; see docs/DECISIONS.md for the tradeoff.
"""

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

_MODEL_NAME = "martin-ha/toxic-comment-model"

_tokenizer = None
_model = None
_toxic_label_idx = None


def _load() -> None:
    global _tokenizer, _model, _toxic_label_idx
    if _model is not None:
        return
    _tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
    _model = AutoModelForSequenceClassification.from_pretrained(_MODEL_NAME)
    _model.eval()
    _toxic_label_idx = next(
        i for i, label in _model.config.id2label.items() if label.lower() == "toxic"
    )


def warm_up() -> None:
    """First inference on a freshly loaded model costs real time; call once at
    app startup (PRD §4.4: 'models warmed at startup')."""
    _load()
    scan("warm up")


def scan(text: str) -> list[dict]:
    _load()
    inputs = _tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        logits = _model(**inputs).logits
    score = torch.softmax(logits, dim=-1)[0][_toxic_label_idx].item()
    return [{"type": "toxicity", "score": score, "span": None, "detail": _MODEL_NAME}]
