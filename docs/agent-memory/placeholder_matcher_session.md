---
name: placeholder-matcher-session
description: "The matcher session (2026-09-29): the recovered grants into the matcher, clean_name as an extension of the normaliser, the name-only tier, output names as parameters, input shape 3; proved on a subset beside production (matching_subset), never run; what was found on the way"
metadata:
  node_type: memory
  type: project
  originSessionId: 554ac960-622d-4441-b25f-c81f1469a5f4
  modified: 2026-09-30T00:58:52.224Z
---

Session 6, 2026-09-29, branch `zeclaude/placeholder-matcher` off
`placeholder-recovery` (7cb5e44, merged up to 2ff2e97 = PR #53), PR #56
(https://github.com/vibrant-data-labs/givingtuesday-datamart/pull/56) against
`placeholder-recovery`, open; Zein merges and Zein runs the rerun. $0 in model calls. Nothing of production's
was written: every database object created started with `scratch_matcher_`
and was dropped at the end.

**What was built (grant_matching.py, corrections_preflight.py,
matching_regression_checks.py, match_explainer.py, new matching_subset.py,
view.py gained name parameters):** the keys view reads
`privategrants_current_w_recovered`; `Relations` (prefix for everything a
run creates, `grants`, `schedule_i`) so the preflight, the gate and the
subset tool run beside production; `clean_name` in grant_matching, the
name-only tier (`match_source = 'name_only'`, `match_name_words`);
`MATCHING_INPUT_SHAPE_VERSION = 3`; `RECOVERED_POLICY = "v2"` with a
digest of the recovered rows in the checkpoint prefix and in
`canonical_builds.source_runs`; the matcher creates the recovered view
after `build_current_relations` drops it; `unioned_grants` rounds the
amount and carries nine new columns (match_name_words, row_source,
page_verdict, filer_marked_individual, placeholder_exceeds_declared,
recovered_object_id/page/row_ordinal).

**Found on the way, none of it in the brief:**
- The output join compares keys with `=` and the keys view left city,
  state and zip NULL where the column was, while pandas sends '': a row
  with any of the three NULL never reached `privategrants_w_recipients`
  (0 of 7.58M rows has one; 783 matched tuples in the join table have an
  empty state). Every recovered row has a NULL city. Fixed with COALESCE
  in the keys view.
- Scoring the CLEANED names (replace) moves every fuzzy score: JW rewards
  short names, so "mit" vs "mit womens independent group" goes 0.70 ->
  0.79 and matches on a shared address ($52M in the sample); "new
  america" -> "sdm of america"; "american heart association" -> "ment
  ltd". So the cleaner EXTENDS the normaliser: the same cleaned name
  scores 1 (`same_name` exact compare), everything else scores on
  `normalize_org_name` as before. Measured under replace: 14,088 rows
  gained on an equal cleaned name vs 2,247 gained / 822 lost / 959 moved
  on jitter.
- `unioned_grants` is a UNION (distinct): 77,078 recovered rows ($117M)
  repeat another row of their list and would collapse; the recovered
  row's key in unioned_grants keeps them apart. The same UNION already
  collapses 130,157 regular matched rows ($2.84B) — flagged, not changed.
- Two rows without a state passed the exact-name tier ('' = ''); now the
  state is compared as missing (`compare_state`).
- A read of every grant (any full-view scan, ~7-9 min) holds ACCESS SHARE
  on `privategrants_recovered`; another session's loader runs `ALTER
  TABLE ... ADD COLUMN IF NOT EXISTS` at every load and waited on my
  scratch join for 6 min. `matching_subset` therefore copies the
  recovered rows into `scratch_matcher_recovered` first and reads a view
  of its own over the copy.
- The corrections preflight is heavy since the registry grew to 17
  filers: the ASPCA family alone is 108K tuples / 38M pairs under the old
  code (40M under the new), ~13 min a family on the laptop; hours in all.
  Pre-existing, not caused by this change.
- The added load of the recovered grants on the rerun: 78,081 distinct
  tuples, 49.6M pairs (+5% on ~982M), 40.3M of them from address-less
  tuples blocked on the empty zip '00000' against 931 universe rows with
  no zip, which can never match by address; ~25 min at the last run's
  rate. Dropping the empty-zip block for address-less tuples would save
  most of it (also 32M regular pairs today); not done, flagged.
- The subset harness reproduces production's last full run on every
  regular tuple it holds (194,120 of 194,120 the same as
  `pf_grant_matching_temp_table`).

**Numbers (subset of 268,440 tuples / 2.32M rows, `matching_subset`):**
recovered grants 73.5% of rows / 66.5% of dollars matched (41,829 rows by
zip and name $3.51B; 7,648 by exact name and state $0.61B; 113,802 on the
name alone $0.58B; one-word name-only 3,219 rows / $27.5M). Sampled
foundations (1 in 25 of the labeled set, 347,933 rows / $54.3B): +14,109
rows on an equal cleaned name ($679.8M), +1,670 never-joined rows, +1,009
name-only, 767 rows moved to a filer with the same cleaned name, 0 lost;
labeled coverage 62.7% -> 67.1% (15,226 pairs, 2,068 foundations). Gate on
the scratch output: every hard rule and sentinel PASS (placeholder rows
349 -> 346, MJFF 3,204 -> 3,333); the two coverage floors FAIL on a subset
by construction. Preflight before (base code, scratch names): PASS, 1,749
tuples / $1.927B won. J&J 2022: 52,643 of 65,342 rows match, all name-only;
Musk 2022's 29 named grants stay; Fidelity Schedule I identical in the
scratch unioned_grants. Run time of build: ~40 min with 3 workers; the
laptop was swapping (other sessions), a 3-worker preflight died of it.

**Why:** the next session (Zein's rerun, then the read of the work list)
builds on these; the traps above are not derivable from the code.
**How to apply:** rerun commands are in the operations doc (*The matcher
rerun*); the gate's baselines move in the commit that accepts the run;
never run the full matcher, preflight or a full-view scan while a load is
running. Related: [[placeholder-session5-work-list-loader]],
[[corrections-registry-design]].
