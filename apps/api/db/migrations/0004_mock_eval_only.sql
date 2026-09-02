-- Learning Plane PRD revision: the 100-case synthetic set is now 100% mock
-- data (pre-authored mock_response + mock_signals) — no live pipeline
-- execution, ever, in the eval/calibration/metrics path. That removes the
-- reason learning_eval_runs/learning_eval_cases existed: they recorded real
-- pipeline runs (real ledger request_id, real latency) which this design no
-- longer produces. Calibration/FN-rate/metrics now recompute directly from
-- the static JSON fixture + real fusion logic on every request — nothing to
-- persist, so nothing to store a run history of.
--
-- The data these tables held was itself a product of the disallowed design
-- (a handful of real Gemini-backed rows from before this revision) — dropped
-- along with the tables, not migrated.

DROP TABLE IF EXISTS learning_eval_cases;
DROP TABLE IF EXISTS learning_eval_runs;
