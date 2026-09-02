"""The actual check pipeline — detector orchestration -> fusion -> ledger
writes -> response shape. Extracted from routers/check.py so it has exactly
one caller-independent entry point: the HTTP route (`POST /check`) and the
learning-plane eval runner (`learning_plane/eval_runner.py`) both call
`run_pipeline()` directly, in-process, rather than the eval runner going back
out over HTTP. No behavior changed from the original inline version in
routers/check.py — this is a pure extraction.
"""

import asyncio
import hashlib
import json
import logging
import uuid

from data_plane import fusion
from data_plane.detectors import canary, injection, pii, secrets, toxicity
from data_plane.llm_client import LLMError, call_llm
from db.queries import get_last_ledger_hash, insert_ledger_row

logger = logging.getLogger(__name__)

# Internal engineering constant — a per-detector hard deadline so a hung
# detector produces status="timeout" instead of hanging the whole request
# (PRD §4.4). NOT a tenant-facing latency budget: the PRD is explicit that no
# such field exists anywhere in policy — this is orchestrator plumbing, never
# exposed as a bundle field or configurable per tenant.
DETECTOR_TIMEOUT_SECONDS = 2.0


def _sig(source: str, type_: str, score, status: str = "ok") -> dict:
    return {"source": source, "type": type_, "score": score, "status": status}


def _max_score(matches: list[dict]) -> float:
    return max((m["score"] for m in matches), default=0.0)


async def _run_detector(source: str, type_: str, scan_fn, text: str) -> tuple[dict, list[dict]]:
    """Runs one (synchronous, CPU-bound) detector off the event loop thread,
    under a hard deadline. A hung detector times out rather than blocking the
    whole request — status='timeout', score=None, meaning unknown, not safe."""
    try:
        matches = await asyncio.wait_for(
            asyncio.to_thread(scan_fn, text), timeout=DETECTOR_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return _sig(source, type_, None, "timeout"), []
    return _sig(source, type_, _max_score(matches), "ok"), matches


async def _run_checks(specs: list[tuple[str, str, bool, object, str]]) -> dict[str, tuple[dict, list[dict]]]:
    """specs: (source, type_, enabled, scan_fn, text). Disabled checks resolve
    immediately (never invoked — PRD's actual cost saving); enabled ones run
    concurrently (asyncio.gather), each under its own deadline."""
    results: dict[str, tuple[dict, list[dict]]] = {}
    enabled = [(source, type_, scan_fn, text) for source, type_, en, scan_fn, text in specs if en]
    for source, type_, en, _scan_fn, _text in specs:
        if not en:
            results[source] = (_sig(source, type_, None, "disabled"), [])

    if enabled:
        outcomes = await asyncio.gather(
            *(_run_detector(source, type_, scan_fn, text) for source, type_, scan_fn, text in enabled)
        )
        for (source, _type_, _scan_fn, _text), outcome in zip(enabled, outcomes):
            results[source] = outcome

    return results


def _apply_redaction(text: str, matches: list[dict], prefix: str = "REDACTED") -> tuple[str, dict[str, str]]:
    """Collect spans from every detector first, replace in one pass (avoids
    offset-shifting bugs, PRD §4.5 step 1). Returns the redacted text plus a
    placeholder -> original-value map — the caller decides whether that map
    ever gets used (input-stage un-redaction) or discarded (output-stage
    redaction is final, PRD §4.5 step 4). `prefix` keeps input- and
    output-stage placeholders from colliding, since they're independently
    numbered from 0 in two separate calls."""
    spans = sorted({m["span"] for m in matches if m.get("span")}, key=lambda s: s[0])
    out, cursor = [], 0
    unredact_map: dict[str, str] = {}
    for i, (start, end) in enumerate(spans):
        if start < cursor:
            continue  # overlapping span from a different detector — skip
        placeholder = f"[{prefix}_{i}]"
        out.append(text[cursor:start])
        out.append(placeholder)
        unredact_map[placeholder] = text[start:end]
        cursor = end
    out.append(text[cursor:])
    return "".join(out), unredact_map


def _unredact(text: str, unredact_map: dict[str, str]) -> str:
    for placeholder, original in unredact_map.items():
        text = text.replace(placeholder, original)
    return text


def _ledger_hash(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


async def _write_ledger_row(conn, **fields) -> dict:
    prev_hash = await get_last_ledger_hash(conn)
    hash_ = _ledger_hash({"prev_hash": prev_hash, **fields})
    row = await insert_ledger_row(conn, prev_hash=prev_hash, hash_=hash_, **fields)
    return dict(row)


async def run_pipeline(pool, bundle_row, prompt: str) -> dict:
    """Runs one prompt through the full input -> LLM -> output pipeline
    against an already-loaded bundle row, writing both ledger rows. Returns
    the same shape `POST /check` returns to its caller."""
    bundle_id = bundle_row["id"]
    fields = bundle_row["fields"]
    request_id = str(uuid.uuid4())

    # ---- Input Gate — detectors only, produce signals, decide nothing ----
    checks_in = fields["input_checks_enabled"]
    input_results = await _run_checks([
        ("input_secrets", "secrets", bool(checks_in.get("secrets")), secrets.scan, prompt),
        ("input_injection", "injection", bool(checks_in.get("injection")), injection.scan, prompt),
        ("input_pii", "PII", bool(checks_in.get("pii")), pii.scan, prompt),
    ])
    input_signals = [input_results["input_secrets"][0], input_results["input_injection"][0], input_results["input_pii"][0]]
    input_pii_matches = input_results["input_pii"][1]

    input_action, input_review_needed = fusion.decide_input(input_signals, fields)

    async with pool.acquire() as conn, conn.transaction():
        input_row = await _write_ledger_row(
            conn,
            request_id=request_id,
            stage="input",
            bundle_id=bundle_id,
            tier_reached=None,
            action=input_action,
            review_needed=input_review_needed,
            modifiers=None,
            contributing_signals=input_signals,
        )

    if input_action == "block":
        return {
            "request_id": request_id,
            "action": "block",
            "response": None,
            "stage_reached": "input",
            "ledger": {"input": input_row},
        }

    prompt_for_llm = prompt
    input_unredact_map: dict[str, str] = {}
    if input_action == "redact":
        prompt_for_llm, input_unredact_map = _apply_redaction(
            prompt, input_pii_matches, prefix="INPUT_REDACTED"
        )

    try:
        llm_response = await call_llm(prompt_for_llm)
    except LLMError as e:
        logger.error("LLM call failed: %s", e)
        raise

    # ---- Output checks: T0 -> T1, one regenerate retry (retry cap: 1) ----
    checks_t0 = fields["output_t0_checks_enabled"]
    checks_t1 = fields["output_t1_checks_enabled"]

    retried = False
    while True:
        t0_results = await _run_checks([
            ("t0_canary", "canary_leak", bool(checks_t0.get("canary")), canary.scan, llm_response),
            ("t0_secrets", "secrets", bool(checks_t0.get("secrets")), secrets.scan, llm_response),
        ])
        t0_signals = [t0_results["t0_canary"][0], t0_results["t0_secrets"][0]]
        t0_secrets_matches = t0_results["t0_secrets"][1]
        t0_action = fusion.decide_t0_output(t0_signals)

        # T1: toxicity + PII run concurrently (PRD §4.4: "detectors run in
        # parallel", each under DETECTOR_TIMEOUT_SECONDS)
        t1_results = await _run_checks([
            ("t1_toxicity", "toxicity", bool(checks_t1.get("toxicity")), toxicity.scan, llm_response),
            ("t1_pii", "PII", bool(checks_t1.get("pii")), pii.scan, llm_response),
        ])
        t1_signals = [t1_results["t1_toxicity"][0], t1_results["t1_pii"][0]]
        output_pii_matches = t1_results["t1_pii"][1]
        t1_action, t1_review_needed = fusion.decide_t1_output(t1_signals, fields, retried=retried)

        if t1_action == "regenerate" and not retried:
            retried = True
            try:
                llm_response = await call_llm(prompt_for_llm, retry=True)
            except LLMError as e:
                logger.error("LLM retry call failed: %s", e)
                raise
            continue
        break

    fused = fusion.decide_output_fusion(t0_action, t1_action, t1_review_needed, fields)

    tier_reached = None
    if checks_t1.get("toxicity") or checks_t1.get("pii"):
        tier_reached = "t1"
    elif checks_t0.get("canary") or checks_t0.get("secrets"):
        tier_reached = "t0"

    async with pool.acquire() as conn, conn.transaction():
        output_row = await _write_ledger_row(
            conn,
            request_id=request_id,
            stage="output",
            bundle_id=bundle_id,
            tier_reached=tier_reached,
            action=fused["action"],
            review_needed=None,
            modifiers=fused["modifiers"],
            contributing_signals=t0_signals + t1_signals,
        )

    if fused["action"] == "block":
        return {
            "request_id": request_id,
            "action": "block",
            "response": None,
            "stage_reached": "output",
            "ledger": {"input": input_row, "output": output_row},
        }

    final_response = llm_response
    if "redact" in fused["modifiers"]:
        # Output-stage redaction is final — no un-redaction of its own map
        # (PRD §4.5 step 4), so the map here is intentionally discarded.
        final_response, _ = _apply_redaction(
            llm_response, t0_secrets_matches + output_pii_matches, prefix="OUTPUT_REDACTED"
        )
    if "flag_visible" in fused["modifiers"]:
        final_response += "\n\nThis response may have toxic intent, apologies for this."

    # PRD §4.2 step 4: restore the user's own data before it reaches them —
    # input-stage redaction only ever existed to keep it from the LLM vendor,
    # not from the user themselves. Applied last, after any output-stage
    # redaction/disclaimer, so it can't interfere with those.
    final_response = _unredact(final_response, input_unredact_map)

    return {
        "request_id": request_id,
        "action": fused["action"],
        "modifiers": fused["modifiers"],
        "response": final_response,
        "stage_reached": "output",
        "ledger": {"input": input_row, "output": output_row},
    }
