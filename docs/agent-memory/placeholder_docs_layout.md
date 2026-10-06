---
name: placeholder-docs-layout
description: "Where the placeholder-recovery knowledge lives after the 2026-09-25 consolidation — three repo docs with fixed roles (findings, operations, engineering log), the exec-summary hosted document and the Whimsical production board; what each must and must not hold"
metadata:
  type: project
---

Consolidated on 2026-09-25 (branch `zeclaude/placeholder-docs-consolidation`,
PR against `placeholder-recovery`), at Zein's request, so critical learnings
survive without a pile of overlapping documents:

- `docs/placeholder_grant_recovery.md` — THE findings doc (554 lines):
  summary, terms table, population by year, the frame, the stages, results
  by band on the paid-only headline (407 / 48.3%; 49.1% with future lists;
  333 / 36.4% without flagged pages), reader accuracy (83 sample pages +
  57 flagged C/D pages), C+D shipping estimate, "What we learned" (17
  numbered findings), decisions-with-evidence table, next steps, files.
  New measurements go here, in the section they belong to.
- `docs/placeholder_recovery_operations.md` (358 lines) — absorbed the
  storage spec and the EC2 runbook (both deleted; git history): tables and
  keys, how a page is decided, policies, decisions, box setup, the one
  command, monitoring, stop/resume, circuit breaker, costs, success
  metrics, housekeeping, CLI reference.
- `docs/placeholder_recovery_pipeline.md` (1,400 lines) — the engineering
  log, marked historical in its header. Append-only history; do not make
  it the current picture again.
- The hosted document executive summary
  (https://claude.ai/code/artifact/2544935f-55dc-4744-921c-ff2dd00aacc4)
  and the Whimsical production board (FXWZBu4FE9RzpMYqWmupqd) mirror the
  findings doc for readers outside the team.
- Reproducibility hooks added: `placeholder_ground_truth flagged --policy
  v2` (scores Sonnet on the checked flagged pages by band; per-page CSV
  `placeholder_flagged_check.csv`), and
  `givingtuesday_datamart/exploratory/placeholder_population.py` +
  `data/exploratory/placeholder_population_by_year.sql` (the by-year
  population; SQL header comment must be stripped per statement).

- Sample-era data files (11: single-read reports v2/v3 + Unstructured, the
  single-reader v3 reports, engine differences, the 610 frame + xml rows +
  staging_expanded) were ARCHIVED 2026-09-25 to
  s3://givingtuesday-datamart/placeholder-recovery/archive/placeholder-sample-era-data-2026-09-25.zip
  (96 KB, MANIFEST.md with sha256s) and removed from the tree (LFS history
  keeps them). Kept: placeholder_staging.csv (the scorer's `_staged()` reads
  it), placeholder_404_images_expanded.csv, placeholder_unreachable.csv.
  `filing_images.backfill` skips a missing staging CSV. The repo has no
  `aws` CLI on PATH under pyenv 3.13; use boto3 from vdl-tools-312.

**Why:** Zein asked "is the doc updated?" and wanted one place per kind of
knowledge; the recovery doc had drifted (sample-era body under a
frame-era summary) and two findings lived only in a hosted document and a
scratch directory.

**How to apply:** when a session produces a new measurement, put it in the
findings doc's section and the operations doc if it changes how to run;
log the how-we-got-there in the pipeline doc. Never re-create a spec or
runbook file. Related: [[placeholder-frame-run-session4]].

**Two "declared" figures (settled 2026-09-25 after Zein found the Terms row
self-contradictory):** in both the findings doc and the hosted document, "declared
total" means ONE thing: the amount on the "see attached" row in Part XV
(3a paid, 3b future, kept separate) = `placeholder_amt` in the frame CSVs,
the bands' cut, the reconciliation target and the credit. The population
table (`placeholder_population_by_year.sql`) uses a different figure,
grants paid on Part I line 25 column (d) (`arecgpdcprps`), and the docs
now call it "grants paid, Part I line 25", never "declared". The frame's
population (9,515 / $25.0B) is every addressable filing with a pointer row
in the GT extract, no half test; only the by-year SQL applies the >= 50%
rule, and it applies it against Part I line 25. Never write "the same rule"
for the two.

**Status:** PR #48 was squash-merged into `placeholder-recovery` as 9c8fd34 on 2026-09-25 (six commits: ground truth + flagged scorer, docs consolidation, population by year, S3 archive, two "declared" fixes). Nothing is on `main` yet.

**Second archive, 2026-09-28 (PR #49):** Zein asked why the 100-filing
sample's files were still committed. They went to
`s3://givingtuesday-datamart/placeholder-recovery/archive/placeholder-sample-files-2026-09-28.zip`
(7 files + manifest): placeholder_sample_100.csv, placeholder_sample_xml_rows.csv,
placeholder_staging.csv, placeholder_report_v1.csv, placeholder_report_v2.csv and
the two single-reader policy JSONs. The frame contains the sample row for row
(`classifier = v1`), so `stage`/`transcribe`/`estimate`/`report` default to the
frame; ground truth's `pick` and `score` exit naming the archive when the
manifest is absent. What stays in data/exploratory is what code and docs read
now: the frame + XML rows, report_v2_1000, ground truth + gt pages + flagged
check, population sql/csv, classifier assessment sql, the 404 and unreachable
lists, the two recovered-rows summaries. Zein prefers data files out of git
when nothing current reads them: say the scope of an archive plainly.

**Dead commands removed, 2026-09-28 (PR #49, commit e8612a5, on Zein's "remove
dead commands"):** `placeholder_recovery stage` (+ `_fetch_pdf`,
`MANIFEST_CSV`), `filing_images backfill` (+ `STAGING_CSVS`, `IMAGE_404_CSV`),
`page_readings backfill` (+ `asked_json_first`, `file_settings`, `UNEVIDENCED`,
`folder_reader`, `skipped_folder`, `_same`, `json_mode_default`), and
`placeholder_ground_truth pick` (+ `_density`). 756 lines, 10 tests; 239 pass.
Remaining commands: filing_images {fetch,status,verify}; page_readings
{read,status}; page_verdicts {agree,status}; placeholder_recovery
{sample,transcribe,estimate,run,report,compare}; ground truth
{add,seed,fix,accept,show,crop,score,tiebreak,policies,verdicts,flagged}.
LEFT for Zein to decide: the ground truth's folder-reading commands (score,
tiebreak, policies and the six entry tools) read `~/.cache/irs_index/vlm`,
which exists only on Zein's laptop; `score` also needs the archived manifest;
`compare` and `report --results` (the Unstructured baseline) have no current
caller. Only `verdicts` and `flagged` read the tables. Backfilled rows stay in
`page_readings` with their settings in the `request` column.

**Third archive, 2026-09-28 (PR #49, commit 0958de8):** Zein asked "but you
left the 100s of xmls?" and meant the bake-off's raw responses: 280 JSON
files in `givingtuesday_datamart/exploratory/vlm_bakeoff/out/` (20 model
folders x 14 pages), inside the PACKAGE, not under data/. Now in
`s3://givingtuesday-datamart/placeholder-recovery/archive/placeholder-vlm-bakeoff-2026-09-28.zip`
(manifest + SHA256SUMS); harness (README, run.py, compare.py,
reference.json) and results_2026-09-21.txt stay; `out/` and `pages*/` are
gitignored. Lesson: when asked to clean up data files, inventory every
tracked file in the repo (`git ls-files | sed 's#/[^/]*$##' | sort | uniq -c`),
not only data/exploratory.
