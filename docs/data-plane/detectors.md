# Detectors

Every detector is a pure function: `scan(text: str) -> list[dict]`. It returns
raw matches with spans (for redaction), never a score-only signal and never an
action — the orchestrator reduces each detector's matches to one
`{source, type, score, status}` signal before it ever reaches fusion or the
ledger. Source: `apps/api/data_plane/detectors/`.

All five detectors follow the same build order: get
something working fast with regex/keywords, then swap in the real
library/model once the pipeline is proven end-to-end. Three of the five have
now made that swap; two (secrets, injection) were already exactly right as
regex per the PRD, so there was nothing to swap.

## Secrets — regex, vendored, unchanged from V1's first pass

```python
SECRET_PATTERNS = {
    "aws_key": r"(?:A3T[A-Z0-9]|ABIA|ACCA|AKIA|ASIA)[0-9A-Z]{16}",
    "github_token": r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36}",
    "slack_token": r"xox(?:a|b|p|o|s|r)-(?:\d+-)+[a-z0-9]+",
    "jwt": r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*?",
    "stripe_key": r"(?:r|s)k_live_[0-9a-zA-Z]{24}",
    "private_key_header": r"-----BEGIN (RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----",
    # ... (full list in secrets.py, vendored from the PRD verbatim)
}
```

Reused verbatim for both the input gate and T0 output check (same file,
imported twice) — a leaked credential looks the same whether it came from the
user or got echoed back by the model. Score is always `1.0`: a regex match on
a credential pattern isn't a "maybe."

## Injection — regex, hand-rolled, unchanged from V1's first pass

```python
INJECTION_PATTERNS = [
    r"ignore (all|your|the) (previous|prior|above) instructions",
    r"disregard (the|all|your) (above|previous|prior)",
    r"reveal your (instructions|system prompt|prompt)",
    r"forget (everything|all) (you|that)",
    r"new instructions:",
    r"act as (if|though)",
]
```

No library — the PRD specifies hand-rolled patterns, and a full
classifier-based injection detector is explicitly deferred (PRD §4.2: "not
scoped for V1"). This is deliberately coarse; it catches the common
"ignore your instructions" phrasing family, not sophisticated obfuscated
attacks.

## Canary — exact string match

```python
CANARY_TOKEN = "cnry_7f3a9b"

def scan(text: str) -> list[dict]:
    idx = text.find(CANARY_TOKEN)
    ...
```

The token now lives in a real system instruction sent to the model (see
[LLM integration](../cross-cutting/llm-integration.md)), not just simulated by
keyword-matching the user's prompt — if a prompt injection genuinely tricks
the model into repeating it, this check catches a real exfiltration.

## PII — Presidio, swapped from a hand-rolled regex baseline

The very first version was a hand-rolled Luhn-validated credit-card regex plus
plain email/phone patterns — enough to prove the pipeline end-to-end. It was
then replaced with **Presidio** (`presidio-analyzer` + spaCy NER), the PRD's
actual V1 method, exposing the identical `scan(text) -> list[dict]` interface
so nothing above it had to change.

```python
_ENTITIES = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD",
             "US_SSN", "IBAN_CODE", "IP_ADDRESS", "LOCATION"]

def scan(text: str) -> list[dict]:
    results = _get_analyzer().analyze(text=text, language="en", entities=_ENTITIES)
    return [{"type": "PII", "score": r.score, "span": (r.start, r.end), "detail": r.entity_type}
            for r in results]
```

Three real findings shaped this file, each caught by actually running text
through it rather than trusting the library's defaults:

**1. Presidio's own default config silently pulls the wrong model.**
`AnalyzerEngine()` with no arguments defaults to `en_core_web_lg` regardless
of what's installed — caught mid-download (a 400MB spaCy wheel appearing where
a fast NER pass was expected is an obvious tell) and fixed by constructing an
explicit `NlpEngineProvider` config instead of relying on defaults.

**2. `en_core_web_sm` → `en_core_web_lg`, resolved with a benchmark, not a guess.**
The PRD flags this exact tradeoff as unbenchmarked (§8). It's benchmarked now:
`en_core_web_sm` misclassified a real name ("Priya Agarwal") as `ORGANIZATION`
instead of `PERSON` — a known weakness of the small English model on South
Asian names — which the `_ENTITIES` allowlist then silently dropped, since it
only keeps `PERSON`. `en_core_web_lg` gets this right (0.85 confidence,
correctly typed), and warm per-call latency between the two models measured
at 4.4ms vs. 4.2ms — no meaningful difference. The PRD's "~9ms" figure for
`sm` was never actually a live tradeoff at the per-request level; the real
cost of `lg` is a larger download and startup footprint, not per-request time.

**3. `LOCATION`: a real precision/recall tradeoff, put to the user, not decided silently.**
Presidio's `LOCATION` recognizer fires on *any* place name at a flat 0.85
confidence — a country name in a trivia question ("what is the capital of
France?") scores identically to a real street address. Excluding the entity
entirely avoids flagging trivia questions, but was found (via real usage) to
also mean a genuine home address goes completely undetected. This was a real
product-risk decision with no free answer, so it was asked rather than
assumed:

| Option | Tradeoff |
|---|---|
| Exclude `LOCATION` entirely | Never redacts a lone place name; misses real addresses |
| Re-include at full confidence | Catches real addresses; also flags ordinary geography questions |
| Re-include, down-weighted | Middle ground; needs custom scoring logic not yet built |

**Decision: re-include at full confidence.** Catching a real address reliably
was judged more important than avoiding a false positive on a geography
question. Both directions were verified after the change — the address test
case now flags the name *and* "Dehradun"; "capital of France" is confirmed to
still flag "France," which is now a known, accepted cost, not a bug.

## Toxicity — swapped from a keyword stub to a real classifier

The first version was four hardcoded keyword patterns (`toxic`, `idiot`,
`stupid`, `hate you`) — enough to prove `decide_t1_output`'s threshold banding
worked. Replaced with `martin-ha/toxic-comment-model`, a small DistilBERT
classifier run locally via `transformers`:

```python
def scan(text: str) -> list[dict]:
    inputs = _tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        logits = _model(**inputs).logits
    score = torch.softmax(logits, dim=-1)[0][_toxic_label_idx].item()
    return [{"type": "toxicity", "score": score, "span": None, "detail": _MODEL_NAME}]
```

**Why this model specifically:** it runs locally and warms up at startup, like
Presidio — deliberately avoiding a second external API/vendor dependency
(Google's Perspective API, OpenAI's moderation endpoint) on top of the LLM
vendor choice that already exists. A cloud moderation API would be more
accurate but adds network latency and a second quota to manage for what's
supposed to be a cheap, fast check.

**A calibration finding worth knowing, not a bug:** this model scores fairly
bimodally in practice — clear threats/profanity near 1.0, mild insults ("you're
an idiot") near 0.0, without much middle ground. The demo's own scripted toxic
response happens to score 0.96 (correctly triggers `block` against a 0.85
threshold), but the PRD's two-threshold design
(`toxicity_regenerate_threshold` at 0.5, `toxicity_block_threshold` at 0.85)
assumes real content sometimes lands *between* them — that's exactly the band
that picks between `regenerate` and `flag_visible`. If real traffic rarely
lands there with this model, that whole code path sees little use. Not fixed
— recalibrating needs real traffic and the calibration sweep script the
learning plane doesn't have yet (see [learning plane](../learning-plane/README.md)).

## Detector interface, for anyone adding a sixth

```python
def scan(text: str) -> list[dict]:
    """Returns raw matches: [{"type": ..., "score": float, "span": (start, end) | None, "detail": str}, ...]
    Pure function — no side effects, no knowledge of bundles, thresholds, or actions."""
```

`span` is `None` for detectors whose finding isn't a masking target (toxicity
is a property of the whole text, not a substring to redact). Everything else
returns a concrete span so `_apply_redaction()` in `check.py` can mask it.
