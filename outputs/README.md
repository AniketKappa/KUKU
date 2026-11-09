# Test outputs

- `raw/`: the current live run on Gemini's free tier, with one complete record per scenario. Each has every tool call, rejected draft, audit verdict and token count. All 25 scenarios have finished.
- `SUMMARY.md`: generated from `raw/`.
- `raw_first_pass_lite_audit/`: an earlier run of the same 25 scenarios in which Lite models audited themselves, with its own summary. It's kept as evidence of what that audit missed. The main README explains the problems found.
- `retrieval_eval.json`: search quality on the queries used for tuning.
- `retrieval_heldout.json`: search quality on queries held back from tuning. This is the honest number.
