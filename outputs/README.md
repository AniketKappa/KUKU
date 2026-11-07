# Test outputs

- `raw_first_pass_lite_audit/`: one complete record per scenario from a live run on Gemini's free tier, plus that run's `SUMMARY.md`. Each record has every tool call, rejected draft, audit verdict and token count. 26 of the 27 scenarios finished. These ran before the newest checks were added, and the main README explains the problems found in them.
- `retrieval_eval.json`: search quality on the queries used for tuning.
- `retrieval_heldout.json`: search quality on queries held back from tuning. This is the honest number.

New runs of `tests/run_scenarios.py` write to `raw/` and generate `SUMMARY.md` here.
