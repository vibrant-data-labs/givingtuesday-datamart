---
name: Don't touch historical comparison artifacts when migrating APIs
description: Frozen verification notebooks/scripts (e.g. `gt_new_pipeline_comparison.py`) document a point-in-time check and should stay broken-but-historical rather than be updated to chase API renames.
type: feedback
originSessionId: 9e3e0185-377f-4002-875d-e31351e00f8c
---
When renaming kwargs or changing call signatures across a multi-repo refactor, do not "helpfully" update notebooks or scripts whose purpose is to document a specific verification run against a frozen prior version.

**Example:** `ed_tracker/notebooks/gt_new_pipeline_comparison.py` exists to compare the new gt_datamart-backed pipeline against `previous_query_prepare_givingtuesday.py` (the legacy snapshot). When I renamed the `query_process_givingtuesday_data` kwargs, I updated the notebook to match — Zein pushed back: that notebook is a historical artifact, not living code. It documents what was true at the comparison date. Updating it corrupts the record.

**Why:** Comparison notebooks and one-time verification scripts are dated evidence, not code that needs to keep compiling. They sit alongside frozen reference modules (the `previous_*.py` sibling here is the giveaway — when there's a "previous" copy nearby, the comparison harness pairs with it as a snapshot).

**How to apply:** When you rename an API and start grep-replacing call sites, exclude:
- files whose name contains `comparison`, `previous`, `legacy`, `_old`, `frozen`, `snapshot`
- files that import a sibling `previous_*` module (they're pinned to a specific prior version on purpose)
- one-off notebook files in `notebooks/` whose top-of-file comment describes a specific dated check

If unsure, ask before touching them. The cost of leaving a stale notebook is zero — nobody re-runs it. The cost of updating it is destroying the record of what was verified.
