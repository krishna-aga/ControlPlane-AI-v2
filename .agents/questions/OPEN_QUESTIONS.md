# Open Questions

Things surfaced during implementation that are genuinely unresolved — not decisions
already made (those live in `.agents/decisions/DECISIONS.md`), but real forks where
someone needs to pick a direction. Update this file as questions get resolved (move
the resolution into DECISIONS.md and remove the entry here) or as new ones surface.

## 1. Real parallelism for T1 detectors isn't actually achieved

PRD §4.4 calls for T1 detectors to "run in parallel (`asyncio.gather`)". That's wired
up (`routers/check.py`'s `_run_checks`/`_run_detector`), and it does correctly deliver
two things: the event loop stays unblocked during a request, and a hung detector now
genuinely times out under a hard deadline instead of hanging forever.

What it does **not** deliver is wall-clock speedup: benchmarked directly, running
`toxicity.scan` and `pii.scan` concurrently via `asyncio.gather(asyncio.to_thread(...))`
was 0.99x vs. sequential — no improvement. Python's GIL means two threads both running
GIL-bound Python code (spaCy's pipeline, the HF tokenizer) don't run any faster
together than one after another. Real concurrency for this kind of CPU-bound Python
work needs separate processes, not threads — which means a second copy of each model
(~250MB+ each) loaded per worker process.

**What to do:** is thread-based non-blocking + real timeout enforcement good enough
for V1 (the actual PRD requirement is arguably about the timeout/deadline behavior,
not raw throughput), or is genuine multi-process parallelism worth the memory cost for
the demo/production story? Nobody's decided this yet.

## 2. `compile()` still doesn't reject a null toxicity threshold

Carried over from the PRD's own open question (§3.4, §8): `toxicity_regenerate_threshold`
/ `toxicity_block_threshold` can be left `null` in a compiled bundle, even though
`decide_t1_output()` needs a real number to compare a score against. Flagged as a
`TODO` in `control_plane/compile.py`, not resolved.

**What to do:** should `compile()` reject a bundle with either left `null` (forcing
every policy author to set them explicitly), or should there be a sane V1 default that
applies only when neither layer sets one? Nobody's decided this yet.

## 3. The real toxicity classifier's scoring may not exercise the PRD's mid-band

`martin-ha/toxic-comment-model` scores fairly bimodally in practice — clear
threats/profanity near 1.0, mild insults near 0.0, not much in between. The PRD's
threshold design assumes real content sometimes lands between
`toxicity_regenerate_threshold` (0.5) and `toxicity_block_threshold` (0.85), which is
exactly the band that picks between `regenerate` and `flag_visible`. If real traffic
rarely lands there with this model, that whole code path is functionally dead weight.

**What to do:** re-calibrate the thresholds against this model's actual score
distribution, swap to a model with more graded outputs, or accept it for a V1 demo and
revisit once there's real traffic to calibrate against (PRD §6's calibration sweep is
explicitly meant for this, but isn't built yet). Nobody's decided this yet.

## 4. Leaked Neon credential — rotate only, or also rewrite git history?

`apps/api/.env` (with a real `DATABASE_URL`, password included) was committed in the
repo's first commit and pushed to `origin/main` on GitHub before `.gitignore` existed.
The password should be rotated regardless. Separately: if the repo is, or ever
becomes, public, the old password stays visible in history forever unless it's
rewritten (`git filter-repo` + force-push).

**What to do:** rotate only, or also do the history rewrite? Depends on whether the
repo is/will be public — nobody's confirmed that or decided yet.

