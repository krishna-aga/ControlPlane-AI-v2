"""Replay mechanic for shadow deploy (PRD §1.1): take already-stored
`contributing_signals` from a real ledger row and re-run them through the
*same* pure fusion functions the live pipeline uses, under a different
bundle's field values. No detector is ever re-invoked and no new decision
logic is written here — this is exactly the `data_plane.fusion` module,
just fed stored signals instead of fresh ones.

Not used by the calibration sweep (`learning_plane/metrics.py`) — that's
pure dataset arithmetic over the mock set's own per-check signals, with no
ledger rows involved at all.
"""

from data_plane import fusion


def replay_output_decision(
    t0_signals: list[dict], t1_signals: list[dict], bundle_fields: dict, retried: bool = False
) -> dict:
    """Reproduces what `decide_output_fusion` would have returned for this
    request had `bundle_fields` been the resolved bundle at the time,
    given the same detector signals that were actually recorded."""
    t0_action = fusion.decide_t0_output(t0_signals)
    t1_action, t1_review_needed = fusion.decide_t1_output(t1_signals, bundle_fields, retried=retried)
    return fusion.decide_output_fusion(t0_action, t1_action, t1_review_needed, bundle_fields)


def split_signals(contributing_signals: list[dict]) -> tuple[list[dict], list[dict]]:
    """Output-stage ledger rows store t0 + t1 signals concatenated
    (`t0_signals + t1_signals`, see data_plane/pipeline.py) — split them back
    by source prefix so they can be fed to the right fusion function."""
    t0 = [s for s in contributing_signals if s["source"].startswith("t0_")]
    t1 = [s for s in contributing_signals if s["source"].startswith("t1_")]
    return t0, t1
