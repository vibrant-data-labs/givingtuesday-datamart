---
name: placeholder-session5-work-list-loader
description: "Session 5 (2026-09-28) built the work list and the loader in givingtuesday_datamart/placeholder_recovery/; PR #51 MERGED 2026-09-29 as 7cb5e44; frame loaded (398 filings, 221,969 rows, $7.03B); what the build found, the chips spawned after the merge, and a flaky test"
metadata:
  node_type: memory
  type: project
  originSessionId: 6a01b8f8-c562-4ff3-ae3d-0c9e2d6607d8
  modified: 2026-09-29T02:13:35.236Z
---

Session 5, 2026-09-28: stage 0 (work list) and stage 5 (loader) are BUILT, on
branch `zeclaude/work-list-and-loader` off `placeholder-recovery` (ca2fd02), PR #51 (https://github.com/vibrant-data-labs/givingtuesday-datamart/pull/51), open, ten commits, not merged.
$0 in model calls, no IRS fetch, matcher untouched. Supersedes the "not built"
state in [[placeholder-loader-address-name]].

**In the database (gt_datamart), created this session:**
`pf_placeholder_filings` (24,518 rows), `privategrants_recovered` (221,969
rows, 398 filings, policy v2), view `privategrants_current_w_recovered`.

**Commands:** `python -m givingtuesday_datamart.placeholder_recovery
work-list | run | load | view | check`. `work-list` takes 90 s (the scratch
query took 12-15 min). `load` takes 9-10 min on the frame, 8 of them in the
selector. `check` takes 7 s and passes 7 of 7.

**Frame numbers under the loading rule (pages only, paid target only):**
407 -> 406 without the extract's rows (Elbridge Stuart 2021 lost) -> 401 with
paid-only targets (5 lost: Kenan 2020, TLL Temple 2020, Glenn 2023, Northfield
Bank 2022, Mitchelson 2021) -> 398 loaded. Paid recovered $7,135.0M of
$14,571.0M = 49.0%; floor with flagged left out 327 / 34.9%. Rates by band
(filings / dollars): A 68% / 60.6%, B 45% / 44.6%, C 37% / 40.4%, D 34% /
36.1%. 2020-on estimate now about 4,550 filings and $14.9B.

**Non-obvious things found:**
- The frame holds BOTH versions of six returns (the extract listed every
  version), so its 1,000 filings are 994 returns. The 3 reconciled lists that
  do not load (Caterpillar 2023, Comcast NBCUniversal 2021, Triad 2021) are
  the second version of returns whose other version IS loaded. Nothing is
  missing; the frame double-counted $100.8M.
- A placeholder amount can exceed Part I line 25 COLUMN (d) (`arecgpdcprps`,
  cash basis): 219 work-list filings, $254M. CORRECTED 2026-09-29: 100 of them
  equal column (a) (`arecprexpnss`, per books) and are fine (King Street
  Charitable Trust: mostly non-cash gifts). 112 exceed both columns, $111M.
  Loaded: only 2 exceed both, $5.4M (Eden Hall 2022, which entered "grants
  approved" $5.2M as a second row on line 3a; Genentech Foundation 2021).
  Always compare with both columns before calling an amount too large.
- `current_grants` rebuilds `privategrants_current` with DROP ... CASCADE,
  which DROPS THE VIEW. `load` recreates it when gone (`ensure_view`). When
  the matcher reads the view, the matching views must be created after it.
- The view shows ONE policy, written into its definition; only `view
  --policy` changes which. A load under another policy leaves it alone.
- The view's first form ran the pointer regex over all 16.6M rows on every
  query (planner filtered before joining). Fixed with a LATERAL + LIMIT 1
  index lookup per loaded filing. Any new SQL using `classifier.pointer_sql`
  needs the same care, or the prefilter.
- `run --dry-run` FETCHES before it projects. For a work list that would be
  11,830 IRS requests. Use `--no-fetch`; it prints $474.72 for 2020 on and
  passes the cap, which Zein raised from $400 to $800 on 2026-09-29 (COST_CAP; the whole list, about $985, still stops).
- `.gitattributes`: `-diff` marks a file binary; `!filter !diff !merge text`
  is what undoes LFS. The corrections registry still has `-diff` (chip
  spawned).
- The selector's `_search_runs` rebuilds the run's row list per candidate:
  J&J 2021 (836 pages, not reconciled) takes 90 s. Not touched.

**Why:** these decide what the next sessions can assume.
**How to apply:** next is the matcher session (view into the matcher, name
cleaner, name-only tier, input shape version 3, regression gate, Zein's
rerun). Before it, Zein confirms: no future-payment lists load; the view's
name; whether lists over line 25 load. The by-year population table in the
findings doc was measured with three exclusions and was NOT rerun with six.
The hosted document, the Whimsical board and the narrated video are stale against
these numbers; the PR body lists every number that changed.

**Zein's answers, 2026-09-29:** (1) future-payment lists SHOULD load, marked
future, with the target from GivingTuesday's 3B datamart
(`990PFPart14Grants3B`, not ingested yet), NOT from per-filing XML; not built,
see [[feedback-gt-datamarts-first]]. (2) Persons' names loading is fine, the
regular table has them too. (3) Cost cap raised to $800. (4) He asked why the
combined view needs to be a new object at all; open: keep it, or fold it into
the matcher's first view in the matcher session. PR #51 has five commits.

**Flag added 2026-09-29 (Zein: "sure"):** lists whose placeholder amount
passes BOTH columns of line 25 load, labelled `placeholder_exceeds_declared`
(boolean) on `pf_placeholder_filings`, on every `privategrants_recovered` row
and as the LAST column of the view (a view replaced in place takes new columns
at its end only). The work list also stores `declared_books` (column (a)).
112 work-list filings; loaded: Eden Hall 2022 (164 rows) and Genentech
Foundation 2021 (31 rows), 195 rows, $24.7M. New columns on existing tables go
in through `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the module's DDL
tuple. The app quit mid-reload once; the tables were intact, because a filing's
rows are replaced in one transaction.

**For the matcher session (found 2026-09-29):** the chain is privategrants ->
privategrants_current -> privategrants_w_column_keys_view (SELECT * + keys) ->
matcher -> privategrants_w_recipients -> unioned_grants (what products read).
`unioned_grants` casts `sigocpyamoun::bigint`; 10,359 recovered rows in 83
filings carry cents ($354.7M) and would break that cast: round or change the
cast. Labels flow to privategrants_w_recipients through SELECT *, and stop at
unioned_grants, which names its columns. Wiring in = point the keys view's
FROM at privategrants_current_w_recovered (or fold its SQL in).

**View renamed 2026-09-29, final name (Zein):** `privategrants_current_w_recovered`, no `_view` suffix (he first tried `privategrants_current_w_ocred`); renamed in place with ALTER VIEW ... RENAME TO. The table stays `privategrants_recovered`. PR #51 has nine commits. The view tests the pointer pattern, not just 'filing is loaded', because loaded filings also hold NAMED grants beside the placeholder rows: 38 rows, $129.3M, in 8 of the 398 (Musk 2022: 29 named grants, $34.4M; Bishop Fdn 2022: one of $94.7M).

**Checks, 2026-09-29:** seven now, in 7 s (was 4 min: an EXISTS over the view ran the patterns on every row; count inside a LATERAL instead). `same rows` proves the view and the work list use one pattern: 422 rows dropped = 422 counted. PR #51 has ten commits. GitHub over SSH port 22 timed out from Zein's network that day; pushing over HTTPS with `git -c credential.helper='!gh auth git-credential' push https://github.com/...` worked; SSH was back the same day.

**MERGED 2026-09-29:** PR #51 squashed into `placeholder-recovery` as 7cb5e44
(nothing on main). Verified after the merge: 372 tests pass, `check --policy
v2` passes 7 of 7 (221,969 rows of 398 filings, loaded and in the view).
hosted document brought to the merged numbers the same day (rev 82, date chip
2026-09-29, section renamed *What loads: the rules, settled September 28 and
29* with three new rows, new board image blob/bd4ec02f-94a0, asset
bb46f470d3a7950a2fddf22b3a22adf7) and the Whimsical board too (box 5 is
"Accept + load", built; 401 / 49.0%; 13% of loaded rows flagged). The narrated
video is still stale. Headless Chrome needs an ABSOLUTE `--screenshot=` path;
a relative one wrote nothing.

**Chips spawned 2026-09-29 after the merge (all against placeholder-recovery):**
task_4233bf7c future-payment lists from GT's 3B datamart (source, deduplicated
relation, `placeholder_future` on the work list, loader target `future`,
outside the view; replaces task_2d3f516a); task_1c3c6c16 the matcher session
(view into the matcher, `clean_name` into the main matcher, name-only tier,
input shape version 3, cents in the `unioned_grants` cast; must NOT run the
full matcher, that rerun is Zein's); task_387f4aed the read (free fetch test
before 2020, fetch and cut 2020 on, STOP for Zein's go, then about $475 on the
EC2 box, load, check); task_776d0d51 the flaky test below. Review each PR with
`/code-review --comment` when it opens.

**Flaky test found 2026-09-29:** `tests/test_page_readings.py::
test_an_interrupted_run_cancels_the_queued_renders_and_reads_and_records_the_reads_in_flight`
leans on a 0.3 s sleep in the patched render. Alone: 0 failures in 45 runs.
Eight loops in parallel: 58 failures in 96, `assert (5 == 1)` on
`len(render.calls)`. Not touched by PR #51. A red run of this one test on a
busy machine is the test, not the code.
FIXED in PR #52 (MERGED 2026-09-29 into placeholder-recovery as 619ef8a): see
[[test-sleep-is-a-noop-in-page-readings-tests]].

**Running chips side by side (checked in the code 2026-09-29):** every worktree
shares ONE database. (1) `grant_matching.match_records` rebuilds all four
`_current` tables (DROP CASCADE, drops the recovered view) and drops and
recreates `public.privategrants_w_recipients` and `public.unioned_grants`, which
products read: a chip must never call it, even on a subset. The matcher chip was
re-spawned with that rule as task_c5c86dda (task_1c3c6c16 withdrawn). (2)
`sources refresh` rebuilds only the basic-fields `_current` relations of the
sources it reloaded, never the grants ones, so ingesting a new source is safe.
(3) The loader's `replace` deletes by object_id and policy_version, NOT by
target: a load run from older code deletes that filing's `future` rows. (4) A
work-list rebuild from older code blanks columns newer code added. For 3 and 4
the cure is to rerun `work-list` and `load` from the newest merged code, $0.
