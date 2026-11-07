# Test outputs

- `raw/`: the current live run on Gemini's free tier, with one complete record per scenario. Each has every tool call, rejected draft, audit verdict and token count. 19 of the 27 scenarios have finished, and running `tests/run_scenarios.py --free` after the daily reset continues with the rest.
- `SUMMARY.md`: generated from `raw/`.
- `raw_first_pass_lite_audit/`: an earlier run of 26 scenarios in which Lite models audited themselves, with its own summary. It's kept as evidence of what that audit missed. The main README explains the problems found.
- `retrieval_eval.json`: search quality on the queries used for tuning.
- `retrieval_heldout.json`: search quality on queries held back from tuning. This is the honest number.
