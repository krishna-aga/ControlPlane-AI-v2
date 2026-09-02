# Redaction & Un-redaction

Redaction is mechanical — it never re-enters fusion (PRD §4.2). Fusion decides
*that* something should be redacted; a separate, dumb function does the actual
masking. Source: `_apply_redaction` / `_unredact` in `apps/api/routers/check.py`.

## Two independent redaction passes, with two different endings

| | Input-stage redaction | Output-stage redaction |
|---|---|---|
| Masks | The user's own PII, before it reaches the LLM | Secrets/PII the *model itself* leaked |
| Purpose | Keep the user's data from the LLM vendor | Keep the model's leak from the user |
| Ends with | **Restored** to the real value before the user sees the final response | **Never restored** — permanent (PRD §4.5 step 4) |

This distinction is the whole reason the feature has two halves, and why it's
easy to build only three-quarters of it without noticing (see below).

## Masking: collect every span first, then replace once

```python
def _apply_redaction(text, matches, prefix="REDACTED"):
    spans = sorted({m["span"] for m in matches if m.get("span")}, key=lambda s: s[0])
    out, cursor = [], 0
    unredact_map = {}
    for i, (start, end) in enumerate(spans):
        if start < cursor:
            continue  # overlapping span from a different detector — skip
        placeholder = f"[{prefix}_{i}]"
        out.append(text[cursor:start]); out.append(placeholder)
        unredact_map[placeholder] = text[start:end]
        cursor = end
    out.append(text[cursor:])
    return "".join(out), unredact_map
```

At the output stage, spans from **both** T0 (secrets) and T1 (PII) detectors
are collected into one list before any masking happens — a sequential
two-pass replace (mask secrets, then separately mask PII) would shift string
offsets out from under the second pass the moment the first replacement
changed the text's length. One combined pass avoids that entirely.

## Restoring: only ever the input side

```python
def _unredact(text, unredact_map):
    for placeholder, original in unredact_map.items():
        text = text.replace(placeholder, original)
    return text
```

Applied as the very last step in `/check`, after any output-stage redaction
and the `flag_visible` disclaimer — never before:

```python
final_response = llm_response
if "redact" in fused["modifiers"]:
    final_response, _ = _apply_redaction(llm_response, t0_secrets_matches + output_pii_matches,
                                          prefix="OUTPUT_REDACTED")   # map discarded — never restored
if "flag_visible" in fused["modifiers"]:
    final_response += "\n\nThis response may have toxic intent, apologies for this."

final_response = _unredact(final_response, input_unredact_map)   # restore the user's own data, last
```

## The gap this used to be

The PRD's redaction execution has four steps: mask → hold an un-redaction map
in memory *for that request only* → send the redacted prompt to the LLM →
**swap placeholders back before returning the response to the user**. Only
the first three existed for a while — `_apply_redaction` returned redacted
text alone, with no map, and nothing anywhere restored a placeholder
afterward. Every scripted test scenario happened to produce a response that
never echoed the placeholder back, so this passed every test that existed —
it took someone actually asking "are placeholders getting swapped back?" to
surface that the fourth step had never been built.

## A collision bug caught while building the fix, before it shipped

Input-stage and output-stage redaction each independently number their
placeholders from `0`. Restoring purely by literal string match, with no
namespace separation, would mean `[REDACTED_0]` from one stage could get
confused with `[REDACTED_0]` from the other if they ever coexisted in the
same text — the input map would overwrite an output-stage placeholder that
happened to share an index, with the user's own data. Fixed with a `prefix`
argument (`INPUT_REDACTED` vs. `OUTPUT_REDACTED`), so the two placeholder
namespaces can never collide, and verified directly with a constructed
collision case rather than just reasoned about.

## What "never written to the ledger" means here

The un-redaction map lives only in a local variable for the lifetime of one
request. It is never passed to `_write_ledger_row` — the ledger only ever
receives `{type, score, status}` signals (see [learning plane](../learning-plane/README.md)),
never the raw values a detector matched. This is the same "types and scores
only, never raw values" rule enforced everywhere else in the system, applied
to redaction's own bookkeeping too.
