"""Shared ranking table for ordered-enum lock fields (PRD §3.4.1).

New ordered-enum fields register here, not as a bespoke comparison — this is
the one place `resolve()` needs to touch when one is added.
"""

ENUM_RANKS = {
    "toxicity_mild_action": {"flag_visible": 0, "regenerate": 1},
    "fail_mode": {"open": 0, "closed": 1},
}


def is_stricter_or_equal(path: str, new, old) -> bool:
    if path.endswith("_threshold"):
        return new <= old  # lower = fires sooner = stricter

    if path in ENUM_RANKS:
        rank = ENUM_RANKS[path]
        return rank[new] >= rank[old]  # only allowed to move up the ranking

    return new == old  # boolean check-toggle: locked value is fixed, no "tighten" direction
