RAG + Grounding — PRD (Working Draft)

Status: V1 build scope. Small, demo-scoped RAG — not a production retrieval system. Grounding was previously listed as deferred, blocked on RAG (PRD_V1_Consolidated.md §4.4/§4.6); RAG is now being built specifically to unblock it.

Standalone doc, same reasoning as the other new-session docs: PRD_V1_Consolidated.md is frozen and not being updated further. This lives here instead.

Scope note: this is a demo-scoped RAG, not a real ingestion/corpus platform — no fixed pre-loaded corpus, no persistence, no caching. A document is attached per-request and processed fresh each time.

1. What this is

Grounding checks whether the LLM's response is actually supported by a reference document, rather than fabricated. Two ways to get a reference document were considered: caller-supplies-context (simple, no retrieval needed) vs. real RAG (system retrieves relevant passages itself). Decided: real RAG — built small and fast for demo purposes.

No fixed corpus. Unlike a typical RAG demo with a pre-loaded document set, this system accepts an arbitrary document per request — "the judge can put any file." The system has to ingest whatever it's handed, on the spot.

2. Document ingestion — decided
Format: PDF only for V1. No .txt, .docx, or other formats.
Size limit: 10MB max per file.
No OCR. Plain text extraction (pypdf/pdfplumber) only reads a PDF's existing text layer — a scanned/image-only PDF has no text layer, so extraction returns empty. Rather than adding OCR (another dependency, more latency, its own accuracy problems), V1 detects near-empty extraction and fails gracefully instead of silently proceeding against an empty corpus.
Threshold, decided: under 50 characters of extracted text = treated as no-text-layer. Comfortably rules out a genuinely empty/scanned PDF while not rejecting a real (if short) document.
Error response, decided: 422 Unprocessable Entity, body {"error": "document_no_extractable_text", "message": "This PDF has no extractable text — please provide a text-based PDF."}. The request is rejected before any retrieval/LLM call — no partial processing.
No separate upload/ingestion endpoint. The PDF rides along in the same request as the prompt — extends the existing POST /check endpoint to accept a multipart request (prompt + optional PDF file) rather than adding a new route. Matches how a file gets attached to a single message in a chat interface, not a persistent corpus a user manages separately.
Request shape, decided:
    POST /check
    Content-Type: multipart/form-data

    prompt: string, required
    bundle_name: string, required   — unchanged, same field the Infra PRD (§5) already specifies
    document: file, optional        — application/pdf, max 10MB
Oversized file, decided: 413 Payload Too Large, body {"error": "document_too_large", "message": "Document exceeds the 10MB limit."}. Checked before any parsing is attempted.
Ingestion is not cached or amortized. Extraction, chunking, and embedding happen fresh on every request that carries a document. Accepted as a V1 limitation, not solved now — a real corpus-management/caching layer (ingest once, reuse across many calls) is future-version work if this ever needs to run at real volume.
3. Document content goes through the same input-stage checks as the prompt — decided

Once extracted, the PDF's text is treated as a second input surface alongside the prompt, and run through the existing input-stage checks (secrets, injection, PII — PRD_V1_Consolidated.md §4.2) before it's used as retrieval context. This closes a gap the frozen PRD explicitly flagged as unsolved: "indirect injection via RAG docs." That gap was hypothetical with no RAG in the system; it's now a direct, demoable attack surface — a judge (or anyone) could attach a PDF containing an injection attempt instead of typing one into the prompt.

Toggle granularity — decided: one shared input_checks_enabled. No separate input_document_checks_enabled set. A single toggle (e.g. input_checks_enabled.secrets) governs the check for both the prompt and any attached document together — a tenant cannot turn document scanning on/off independently of prompt scanning. Simpler schema, one setting to reason about; the tradeoff (no independent control per surface) was accepted deliberately.

Document attachment itself is always permitted for V1 — decided. No bundle-level switch to reject attachments outright; if the endpoint accepts a document, every bundle allows it.

Mechanically (consistent with existing architecture, no new fusion logic needed):

Each existing input-stage detector runs twice per request that carries a document — once against the prompt, once against the extracted document text — producing signals distinguished by source (e.g. source: "input_prompt_secrets" vs. source: "input_document_secrets"), so a reviewer can tell which surface a finding came from. Both runs are gated by the same shared toggle above.
decide_input() is unchanged — it already scans all active signals regardless of source, so a block/redact triggered by document content blocks/redacts the whole request exactly like a prompt-triggered one.
PII found in the document is redacted the same way prompt PII is (§4.2's existing redaction execution), before the document's text is used as retrieval context — not just before the prompt is sent.
If the document itself gets blocked (secrets or injection found in it), the whole request is blocked — the LLM is never called, same as a blocked prompt today.
3.1 Schema addition — decided

Extends the field families in PRD_V1_Consolidated.md §3.3 (frozen doc — change recorded here). No new input-stage fields (§3 above uses the existing input_checks_enabled); grounding is a new T1 check, following the existing per-check-toggle pattern:

yaml
output_t1_checks_enabled:
  pii: true
  toxicity: true
  grounding: false      # new

output_grounding_threshold: 0.5   # decided default — see §5, tune later against real data
4. Retrieval — decided
Chunking, decided: split the extracted, PII-redacted document text on whitespace into words, then slide a window of 150 words per chunk with 30-word overlap (20%). Word-count-based, not token-based — no tokenizer needed, trivial to implement, and comfortably under all-MiniLM-L6-v2's 256-token max sequence length (150 words ≈ 190–220 tokens for typical English text, safe margin).
Embed each chunk with a small local embedding model — sentence-transformers/all-MiniLM-L6-v2, decided. No external embedding API call: dependency-light, no document content leaves the process.
Embed the prompt, cosine similarity against chunk embeddings, take top-3 most relevant chunks — decided default, matches the earlier lean-shape proposal.
In-memory only — a numpy array is enough at this scale; no vector DB needed for a single-document, per-request corpus.
Prompt-construction format, decided:
  Context:
  {chunk_1}

  {chunk_2}

  {chunk_3}

  Question: {prompt}

Chunks joined in retrieval-rank order, separated by a blank line; if fewer than 3 chunks clear a minimum similarity floor (not yet set — start at 0.0, i.e. always include top-3 regardless of how weak the match is, simplest to implement), pass however many were retrieved.

5. Grounding check — decided: semantic similarity scoring
Runs at T1, alongside PII/toxicity, per the original placement in PRD_V1_Consolidated.md §4.4.
Technique: semantic similarity scoring (a.k.a. cosine/embedding similarity) — not NLI. Embed the LLM's response with the same embedding model already loaded for retrieval (§4), embed the retrieved chunks, and measure how close the vectors are via cosine similarity. That closeness score is the grounding score.
NLI considered and rejected for V1. An NLI/entailment model (does the response follow from the retrieved context, sentence by sentence) is more accurate at catching a single fabricated claim buried in an otherwise-supported response, but requires: (a) a second model loaded purely for this check, and (b) splitting the response into individual sentences and running one model call per sentence to be done properly — otherwise a fabricated sentence can get averaged out by the rest of the response and missed anyway. Both add real build time and a new failure point for a live demo, for accuracy gains that aren't the point being demonstrated. Similarity reuses the embedding model already being loaded for retrieval — no second model, less code, less to break live.
Same signal contract as everything else: {source: "grounding", type: "grounding", score, status}. Note the polarity is inverted relative to every other T1 check: for PII/toxicity, a higher score means worse; for grounding, a lower similarity score means worse (less supported). output_grounding_threshold: 0.5 means "flag if score is below 0.5," not above — implementers should not copy-paste the PII/toxicity >= comparison direction.
output_grounding_threshold: 0.5 — decided default. Cosine similarity between unrelated sentence-embedding pairs typically sits well below 0.3, and near-paraphrase pairs typically sit above 0.7 for MiniLM-family models; 0.5 is a reasonable untuned midpoint starting value. Flagged as needing real benchmarking once the 100-case-style set has grounding examples (§6) — treat as a tunable default, not a validated number, same caveat as every other threshold in this project.
Failed grounding check → flag_visible — decided. Below-threshold grounding score means the response is shown to the user together with a disclaimer, same pattern as mild toxicity (PRD_V1_Consolidated.md §4.4).
flag_visible now carries a reason list — decided. Today's PRD has it as a bare modifier with one hardcoded disclaimer string tied to toxicity. That breaks once a second trigger (grounding) exists. New field: flag_visible_reasons: list[str], populated with any of "toxicity" / "grounding" that fired — both can be present at once.

Disclaimer text — decided:

python
DISCLAIMER_TEXT = {
    "toxicity": "This response may have toxic intent, apologies for this.",
    "grounding": "This response may include information not found in the provided document.",
}

def render_disclaimers(flag_visible_reasons: list[str]) -> str:
    return " ".join(DISCLAIMER_TEXT[r] for r in flag_visible_reasons)

If both fire, both sentences are appended (order: toxicity, then grounding — fixed order, matches evaluation order below).

decide_t1_output() — extended (supersedes PRD_V1_Consolidated.md §4.4's version for this build). The frozen version only evaluates PII when toxicity hasn't already set a non-allow action, which silently drops PII/grounding modifiers whenever toxicity's mild band fires. Adding a third source (grounding) makes that gap impossible to ignore, so this version evaluates all three independently and composes their modifiers, consistent with §4.5's own stated principle that redact/flag_visible/flag should compose rather than compete:

python
def decide_t1_output(signals: list[Signal], bundle: Bundle, retried: bool = False) -> dict:
    modifiers = set()
    flag_visible_reasons = []
    review_needed = False

    # --- Toxicity (block/regenerate still preempt everything else outright) ---
    tox_signals = [s for s in signals if s["type"] == "toxicity" and s["status"] != "disabled"]
    tox_score = max((s["score"] for s in tox_signals), default=0)
    tox_unknown = any(s["status"] == "timeout" for s in tox_signals)

    if tox_unknown:
        return {"action": "block" if retried else "regenerate", "modifiers": [], "flag_visible_reasons": [], "review_needed": False}
    if tox_score >= bundle.toxicity_block_threshold:
        return {"action": "block", "modifiers": [], "flag_visible_reasons": [], "review_needed": False}
    if tox_score >= bundle.toxicity_regenerate_threshold:
        if bundle.toxicity_mild_action == "flag_visible":
            modifiers.add("flag_visible")
            flag_visible_reasons.append("toxicity")
        else:
            return {"action": "block" if retried else "regenerate", "modifiers": [], "flag_visible_reasons": [], "review_needed": False}

    # --- PII (always evaluated now — no longer gated behind toxicity's result) ---
    pii_signals = [s for s in signals if s["type"] == "PII" and s["status"] != "disabled"]
    pii_score = max((s["score"] for s in pii_signals), default=0)
    if pii_score > bundle.output_pii_redact_threshold:
        modifiers.add("redact")
    elif pii_score > bundle.output_pii_review_threshold:
        review_needed = True

    # --- Grounding (only evaluated if the check ran at all — bundle toggle off means no signals) ---
    grounding_signals = [s for s in signals if s["type"] == "grounding" and s["status"] != "disabled"]
    if grounding_signals:
        grounding_score = min(s["score"] for s in grounding_signals)   # worst chunk wins; lower = less supported
        grounding_unknown = any(s["status"] == "timeout" for s in grounding_signals)
        if grounding_unknown or grounding_score < bundle.output_grounding_threshold:
            modifiers.add("flag_visible")
            flag_visible_reasons.append("grounding")

    action = "redact" if "redact" in modifiers else "allow"
    return {"action": action, "modifiers": sorted(modifiers), "flag_visible_reasons": flag_visible_reasons, "review_needed": review_needed}

decide_output_fusion() — updated call signature. Takes t1_result (the dict above) instead of separate t1_action/t1_review_needed args:

python
def decide_output_fusion(t0_action: Action, t1_result: dict, bundle: Bundle) -> dict:
    if t0_action == "block" or t1_result["action"] == "block":
        return {"action": "block", "modifiers": [], "flag_visible_reasons": []}
    if t1_result["action"] == "regenerate":
        return {"action": "regenerate", "modifiers": [], "flag_visible_reasons": []}

    modifiers = set(t1_result["modifiers"])
    if t0_action == "redact":
        modifiers.add("redact")
    if t1_result["review_needed"]:
        modifiers.add("flag")

    return {"action": "allow", "modifiers": sorted(modifiers), "flag_visible_reasons": t1_result["flag_visible_reasons"]}

Ledger row shape — extended: output rows (PRD_V1_Consolidated.md §4.5) gain one field:

json
{
  "action": "allow",
  "modifiers": ["flag_visible", "redact"],
  "flag_visible_reasons": ["grounding"],
  "contributing_signals": [
    {"source": "t1_pii", "type": "PII", "score": 0.81, "status": "ok"},
    {"source": "t1_grounding", "type": "grounding", "score": 0.32, "status": "ok"}
  ]
}
6. Learning-plane implications (flag for Learning_Plane_PRD_Draft.md once this stabilizes)
Same shadow-deploy limitation any new detector runs into: this is a new detector — existing ledger rows have no stored grounding signal, so its effect can't be validated retroactively via shadow deploy.
The 100-case synthetic set has zero document/RAG test cases. Every existing case is prompt-only. If grounding needs FN-rate or calibration validation later, new categories (with an attached reference document + planted unsupported claims) would need to be added — none of the existing math in Learning_Plane_PRD_Draft.md §3/§6 currently accounts for a grounding dimension at all.
7. Status: fully decided for a first implementation pass

Every item that was previously open now has a concrete decided value or mechanism, so nothing here should block writing code: near-empty-text threshold (50 chars, §2), request/error shapes (§2), chunk size/overlap (150/30 words, §4), embedding model (all-MiniLM-L6-v2, §4), top-k (3, §4), grounding technique (similarity, §5), output_grounding_threshold default (0.5, §5), grounding-failure action (flag_visible + flag_visible_reasons, §5), and the full decide_t1_output() / decide_output_fusion() code (§5).

What's still genuinely open — not blocking, just not real numbers yet:

Every threshold above (output_grounding_threshold: 0.5, the 50-char extraction floor, the 0.0 similarity floor for chunk inclusion) is an untuned starting default, not a benchmarked value — same caveat that already applies to every other threshold in this project (PII, toxicity). Tune once real traffic/test cases exist.
§6's learning-plane gap (100-case set has no document/grounding cases) is unchanged — still real, still not solved here, tracked in Learning_Plane_PRD_Draft.md.