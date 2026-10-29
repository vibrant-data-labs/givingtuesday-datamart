# Data refresh: the new concordance, the October read, the matcher's fix

The run after the October refresh (`docs/data-refresh-2026-10.md`). It
reloads our IRS extract under the concordance PR #81 merged and patches
production with it, loads the placeholder lists read since the last load,
and runs the matcher once, with a fix for the errors October's gate caught,
to a gate that passes. October's open gate and close-out are finished here.

GivingTuesday's `2026_10_25` files are not loaded. GT wrote on 2026-10-28
that they test its parser and concordance changes, without the IRS's two
newest batches, and that a release with them would follow "over the next
day or so". Production stays on GT's June release, with our rows for every
batch we read.

Plan written 2026-10-28 after PRs #81 and #83 merged. Each step is checked
in with Zein before it runs; the boxes are ticked and the log filled in as
they do.

## Where things stand on 2026-10-28

| what | state at 22:00 UTC |
|---|---|
| GivingTuesday's releases | production holds the June release (`2026_06_16`; Schedule I and O `2026_06_25`). `2026_10_25` is upstream for every source we load and stays out, as above; it was measured on a scratch copy (`docs/own-extract.md`, *GT's 2026_10_25 release*) |
| the IRS's batches | 38, `2024_TEOS_XML_01A` to `2026_TEOS_XML_09A`; nothing newer (the next is due about 2026-11-18) |
| our extract in production | all 38 batches loaded under concordance `3d9b48d6e5e7`. `main` carries `ce054c032e56`, so `own_extract patch` refuses until `own_extract reload` has run |
| production staging | the June release with our patch of 2026-10-26, under `3d9b48d6e5e7` |
| basic-fields `_current`, canonical, grants `_current` | built 2026-10-26 to 10-28 over that staging. None carries the source stamp the matcher writes since #83, so the next matcher run rebuilds them; `--skip-rebuild` refuses until then |
| placeholder recovery | the view shows `v3`. Loaded under `v3` on 2026-10-27: 4,611 paid filings, 582,471 rows, $16.21B; 173 future filings, 6,531 rows. The read of 2026-10-27 stored verdicts for 530 new pages that are not loaded yet, and left 74 pages without a verdict (Qwen's provider was down) |
| the matcher's output | the run of 2026-10-28 (`privategrants_w_recipients` 10,927,644 rows, `unioned_grants` 19,879,313), live on the site. Its gate failed: placeholder rows matched 415 against a ceiling of 349, and the Johns Hopkins sentinel 706 against 1,735, whose EIN turned out to be the Johns Hopkins Club, not the university. Not accepted; no baseline moved |
| the ranking proofs (#83) | the placeholder exclusion is clean (all 415 rows out, nothing else moves). Removing BMF alias lines loses real aliases (Bates College, WETA) and leaves the university's grants on the club. Geography-aware ranking is the best tested: labeled relationships 464 to 469 of 647 with none lost; rows naming Johns Hopkins University to the university 191 to 3,834 and none to the club; Duke 1,890 to 5,221; PAWS and Notre Dame kept. It failed fresh validation on a grant whose state contradicts its own city and ZIP. None of it is in the matcher |
| the instances | the shared instance stays the large class through this run and goes back to the medium after it (decided 2026-10-28 in the review of #83): 63.6 GB free, no swap. The box is still at 256 GB of memory (about $2 an hour). Both idle. The large-server builder knobs (`bigdb.env`) are on the box |
| undo | the snapshot `gt-datamart-before-irs-patch-20261026` is June before any patch; step 2 takes a new one |
| who reads the instance | this datamart's site (pages revalidate hourly; the CDN serves stale up to a day) and the public trackers, which query their own database on the same instance live |

## What this run changes

- Our rows in production staging, patched again under `ce054c032e56`. A
  foreign recipient's address fills the 990-PF grant tables' address
  columns, as GT writes it (about 61,600 grants on 12,025 returns). The
  group rows of 49 returns written with an `irs:` prefix come in (230
  Schedule O notes, 22 990-EZ programs, 3 Schedule I grants of $566,494, 2
  990-PF grants of $4,402). The return tables do not change.
- The placeholder lists read on 2026-10-27 whose verdicts are stored but
  not loaded (530 pages), loaded under `v3`. No new work list and no new
  read this run (decided 2026-10-29): nothing has been filed or read
  since.
- The matcher's fix (decision 1), in its first full run since #83: the
  integer index, the chunk pool, the source stamps.

## Decisions before we start

1. **The matcher's fix.** The PR you planned for it on 2026-10-28. It gates
   step 8 only; steps 0 to 6 run either way.
   - **A.** The placeholder exclusion alone, and the gate's Johns Hopkins
     sentinel moved to the university's EIN, 520595110. Ready in an hour.
     The university's grants stay on the BMF records of its affiliates
     (about $2.2B on the October output), which the accepting commit then
     states as a known error.
   - **B.** A, plus the geography-aware winner order with one revision: a
     grant whose state contradicts its own city and ZIP counts as
     conflicting evidence instead of as its state. About a day of work
     beside steps 1 to 6. Validated on a fresh sample frozen in step 0,
     before step 2 changes the inputs, against criteria fixed now: the
     current policy reproduces production's winners on the sample;
     labeled coverage at least 469 of 647 with none lost; no strong
     location-loss flag where the grant's city and ZIP agree with the old
     winner; every other flag listed and reviewed. If B does not pass, A
     runs.

   My recommendation: B. **Decided 2026-10-29: B, with `named_first`.** The
   fresh sample of step 0 (39,178 tuples) found neither new order passes
   every criterion as written: `named_first` raises 113 location flags,
   `geographic` covers 467 labeled relationships, and A is the worst of
   the three on the reviewed changes. Every flag was reviewed (67
   corrections, 23 errors), so the location criterion becomes "every flag
   reviewed, the order a net correction", and `named_first` is PR #84's
   default (`docs/recipient-ranking-production-2026-10-28.md`, *The
   decision*).
2. **GT's next release.** If the release GT promised lands before step 8, I
   measure it on a scratch copy as #81 did, and we decide then whether to
   take it in before the matcher or leave it for the next run. The matcher
   runs once in this run either way.
3. **The box.** My recommendation: keep it at 256 GB through step 8 and
   shrink it right after the gate. The reload, the proofs and the matcher
   use its cores and memory; shrinking it between steps saves about $1.85
   an hour against two restarts and two address changes.
4. **The read's cap.** $300, as in October. The projection should be small:
   the 74 pages and whatever filings join the work list.
5. **The quiet window.** Steps 2 to 4 and step 8 in the evening, Pacific;
   steps 0, 1 and 5 to 7 any time.
6. **Not in this run** unless decided: GT's `2026_10_25`, the 2015-2019
   read ($436 projected), PR #80 (`irs_source` reading the XML from the IRS
   archive), the officer tables.

## Rules

- One writer at a time on the shared instance. The long steps run on the
  box, each in its own `tmux -L refresh` session, logging to
  `~/logs/refresh-concordance/`.
- A watchdog over the instance through every heavy step: stop under 35 GB
  of free storage, on a rise of more than 0.5 GB of swap from the watch's
  start (the metric keeps reporting pages swapped out earlier), under 0.3 GB
  of freeable memory; on a breach it cancels the box's backends. New in this
  run: the watchdog is repo code and runs on the box, not a script on a
  laptop whose connection drops.
- The builder's memory comes from `bigdb.env` only while the instance is
  the large class. On the medium class the builder refuses a budget past its
  defaults (#83), so a forgotten file stops before any DDL.
- A snapshot before step 2: the undo for the patch and what follows.
- Never load recovered rows while the matcher runs. No `--skip-rebuild` in
  this run.
- TEOS: urllib's own identity, and the fetch stops on five 403s in a row;
  never another User-Agent, a rotation or a retry of a 403.
- The paid read, the instance classes and the matcher's fix are Zein's
  calls.
- Baselines move only on `GATE: PASS`, in the commit that accepts the run.

## Checklist

### 0. Preflight (the box and the laptop, about an hour; nothing written to production)

- [x] The box's clone on this branch and reinstalled:
      `git fetch && git switch zeclaude/data-refresh-concordance && pip install -e '.[ingest]'`.
      Done 2026-10-29 16:45 UTC at f995657; the install had nothing new to
      add.
- [x] `python -m givingtuesday_datamart.sources status`: GT's newest is
      `2026_10_25` (a newer one is decision 2); the BMF at `2026_10_13`.
      `python -m givingtuesday_datamart.own_extract batches 2026`: `09A`
      the latest. Done 16:48 UTC, as expected on every line
      (`preflight-status.log`).
- [x] No backend on the instance but ours (appendix); free storage at
      least 60 GB; the instance the large class and `bigdb.env` on the box.
      Done 16:50 UTC: no active backend but my own (ten idle, the site's
      pool); 63.5 GB free, swap 0.00 GB, 23.0 GB of memory free; the large
      class; `bigdb.env` where the October run left it on the box.
- [x] `python -m givingtuesday_datamart.placeholder_recovery status`
      logged. Done 16:48 UTC (`preflight-status.log`): every figure as
      read on 2026-10-28.
- [x] The watchdog as repo code, with tests, started on the box for each
      heavy step. PR #85 (36a116e): `python -m givingtuesday_datamart.watchdog
      --log FILE -- COMMAND` runs the step and watches; it ran first over
      the census below, from the box's worktree of that branch. Each heavy
      step's run script wraps its command with it.
- [x] The census before: for each relation production reads (the
      basic-fields and grants `_current` relations, `nonprofit_canonical`,
      the recovered rows, the matcher's two tables), rows and dollars by
      tax year, as a small rerunnable module; the CSV stays with the run's
      logs, the table goes in this log. Run again in step 10, it says what
      moved where, which the accepting commit must explain. Done 17:03 to
      17:10 UTC: `census take` (PR #85) from the box under the watchdog,
      `census-before.csv` beside the logs, the table in the log entry.
      Step 10 runs `census compare` against that file.
- [x] For decision B: the fresh validation sample frozen now, read-only,
      with a new hash seed and none of the recipients any proof has
      inspected. Done 2026-10-29 04:37 UTC: seed 7, 1 in 100, 39,178
      tuples in 2.5 minutes with the instance quiet; run and reviewed the
      same day (the log).

### 1. Our extract under the new concordance (the box, about an hour)

Only `irs_extract` changes: production's staging and the site are as they
were. If anything here looks wrong we stop with production untouched.

- [x] The eleven `irs_extract` tables truncated (appendix). `reload` takes
      its list of batches from the run records, not from the tables, and a
      batch's load replaces its rows in all eleven, so without the truncate
      the old rows would be 12 GB of dead tuples; `patch` refuses any batch
      not reloaded. Done 2026-10-29 19:00 UTC, in a second; free storage
      63.4 to 75.8 GB.
- [x] `python -m givingtuesday_datamart.own_extract reload --jobs 4`: each
      batch from our S3 copy, checked against the SHA-256 its first load
      stamped, nothing fetched from the IRS; 38 batches report, none fails.
      Done 19:00 to 20:54 UTC under the watchdog: 38 reported, none failed,
      38 runs under `ce054c032e56`; against the previous load's run records
      the rows are identical on every table but the four the concordance
      change reaches (+230 `schedule_o`, +22 `programs_ez`, +3
      `grants_to_domestic_organizations`, +2 `privategrants`: the 257 rows
      expected).
- [x] `python -m givingtuesday_datamart.own_extract checks`: 990-PF grants
      against line 25 about 255,649 of 263,734; Schedule I against Part IX
      line 1 about 134,075 of 137,301 (the scratch copy's figures under
      `ce054c032e56`); no batch 2 points below the others.
      `irs_extract.unread_paths` lists nothing inside a group. Done 20:54
      UTC: both totals the scratch copy's to the row (96.9% and 97.7%); no
      batch 2 points below the median; nothing unread inside a group
      (`checks-report.md` beside the logs).

### 2. The patch again (the box, quiet window, about an hour with the vacuum; snapshot first)

- [ ] A manual snapshot of the instance, status *available*.
- [ ] `python -m givingtuesday_datamart.own_extract patch`: in one
      transaction our earlier rows (under `3d9b48d6e5e7`) out and the new
      ones in; GT's rows go only under urls our tables hold for the first
      time (the 49 returns'); ten `irs_extract_patch` runs under
      `irs_extract@ce054c032e56`; the basic-fields relations rebuilt.
- [ ] The counts per table logged: about 20.11 million of our rows out and
      back in on the ten tables, ours 257 rows more.
- [ ] `VACUUM (ANALYZE)` on the ten staging tables (appendix).
- [ ] Checks: no url holds both GT's rows and ours on `basic_fields_pf`
      and `privategrants` (appendix); the filer-years with a tax period
      ending in 2025 or later are all ours.

### 3. The canonical build (the box, about 15 minutes)

- [ ] `python -m givingtuesday_datamart.sources build-canonical`.
- [ ] A `success` row in `datamart_meta.canonical_builds`; its lineage
      names the new patch run. `nonprofit_canonical` should hold the same
      rows as on 2026-10-27 (1,983,340); the text tables gain the 49
      returns' notes and programs.

### 4. The grants relations (the box, about 45 minutes)

- [ ] `python -m givingtuesday_datamart.current_grants --only privategrants_current`,
      then `--only privategrants_future_current`.
- [ ] The paid build's last line reads `0 filer-years look doubled without
      a repeated basic row`; otherwise the filings it names are checked
      against their XML before step 8.
- [ ] `status` shows the three line-25 rules; the rows by rule logged
      against October's (17,392,341 paid, 452,460 future).

### 5. The work list and the read (skipped this run; decided 2026-10-29)

Nothing has been filed or read since the work list and the read of 27
October, so neither is repeated this run: the work list of that day
stands, no read is bought, and the verdicts stored on 2026-10-27 go in at
step 6. The classifier's new arm (v3) reaches the work list and the read
in the next run; until then a recipient line of "SEE." leaves matching
unrecovered.

- [x] Skipped: `python -m givingtuesday_datamart.placeholder_recovery work-list`;
      counts by tax year and band logged against October's 25,612.
- [x] Skipped: the projection,
      `python -m givingtuesday_datamart.placeholder_recovery run --policy v3 --tax-years 2020-2025 --keep-list --dry-run --no-fetch`.
- [x] Skipped: Qwen's provider answers before the read starts (it failed
      most calls on 2026-10-27).
- [x] Skipped: the read, in `tmux -L frame`,
      `python -m givingtuesday_datamart.placeholder_recovery run --policy v3 --tax-years 2020-2025 --keep-list --cache /data/irs_index --cap 300`;
      spend logged; no page left without a verdict, or the reason.

### 6. The load, the view and the checks under v3 (the box, about 40 minutes; never while the matcher runs)

- [ ] `python -m givingtuesday_datamart.placeholder_recovery load --policy v3`
      (it takes the verdicts stored on 2026-10-27 over the October work
      list, which the loader reads as it stands).
- [ ] `python -m givingtuesday_datamart.placeholder_recovery view --policy v3`.
- [ ] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3`
      passes; rows and dollars by target logged.

### 7. The matcher's fix (the laptop and the box, beside steps 1 to 6)

- [x] A: `PLACEHOLDER_REGEX` moves into the matcher; the grants view
      leaves out the rows it matches, and the gate imports it from there;
      `MATCHING_INPUT_SHAPE_VERSION` 5 to 6, so no chunk of an earlier
      shape is resumed; tests. Done in PR #84 (merged b65477e, 2026-10-29
      19:18 UTC): the shape went 5 to 7 (6 the exclusion, 7 the punctuated
      "see" and a correction row before a BMF row in the universe's
      DISTINCT ON); the bare-"see" arm is one constant shared with the
      recovery classifier (`SEE_ALONE`, classifier v3), so a row the view
      leaves out of matching is a row the work list counts.
- [x] B, if chosen: the geography-aware winner order in `grant_matching.py`
      with the revised state rule; tests; the policy hashed and frozen
      before the sample of step 0 is scored; the regression pool and the
      fresh sample against the criteria above; a proof document; Zein's
      review of the diff and the proof. Done as `named_first` (decision 1):
      the orders' code hashed before the fresh sample was scored, the
      inspected pools and the fresh sample measured (the log of decision
      1), `docs/recipient-ranking-production-2026-10-28.md`, the review of
      2026-10-29 (ten comments) answered in 7359ad7 and merged.
- [x] The gate: the Johns Hopkins sentinel on 520595110, its floor set from
      the accepted run; the placeholder check a hard zero. Done in PR #84
      with a provisional floor of 190 (the university's 191 rows on
      October's output); the accepting commit of step 9 sets the floor
      from the accepted run.

### 8. The matcher (the 256 GB box, quiet window, Zein's go; about seven hours)

- [ ] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3`.
- [ ] `python -m givingtuesday_datamart.corrections_preflight` (2 h 50 min
      in October).
- [ ] `python -m givingtuesday_datamart.grant_matching --workers 12`: a
      full run, which rebuilds the `_current` relations and stamps them.
      In October the integer index took about an hour and the pool an hour
      and a half.
- [ ] `python -m givingtuesday_datamart.matching_regression_checks`:
      `GATE: PASS`, or the diagnosis of what moved.
- [ ] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3`.

### 9. The accepting commit (laptop)

- [ ] The baselines updated in one commit, every number that moved
      explained with the census, the gate rerun on `PASS`
      (`docs/matching-regression-runbook.md`, *Updating the baselines*).

### 10. Close out

- [ ] `status` once more and the census after, against the census before.
- [ ] `docs/own-extract.md`: production on `ce054c032e56`;
      `docs/data-refresh-2026-10.md`: its open gate and step 8 pointed here.
- [ ] Spot checks on the site, once the caches turn over or after a
      redeploy: Fidelity Charitable (110303001), Johns Hopkins University
      (520595110), Duke University (560532129), the Michael J. Fox
      Foundation.
- [ ] The shared instance back to the medium class in a quiet window, then
      `bigdb.env` deleted on the box; the box back to its usual size.
- [ ] This log complete; this branch's PR merged.

## Afterwards

- GT's next release, the one with the newest batches: measured on a scratch
  copy first (`own_extract gt-release`, `own_extract parity --host`), then
  `sources refresh`, `own_extract patch`, and steps 3 to 10.
- A new IRS batch: `own_extract load 2026 --batch <new>`, `own_extract patch`,
  then steps 3 to 10.
- The BMF's monthly file: `sources refresh --source irs_eo_bmf`, the
  canonical build, the matcher.
- Worth building after this run: one `refresh` command with resumable steps
  and the census at both ends; incremental matching, which rescores only the
  recipient tuples that changed.

## Appendix: SQL

Backends running (then `SELECT pg_terminate_backend(<pid>)` for an orphan):

```sql
SELECT pid, usename, client_addr, state, query_start, left(query, 100)
FROM pg_stat_activity
WHERE state <> 'idle' AND pid <> pg_backend_pid();
```

The extract's tables, before the reload:

```sql
TRUNCATE irs_extract.basic_fields, irs_extract.basic_fields_pf, irs_extract.basic_fields_ez,
         irs_extract.mission_statements, irs_extract.programs, irs_extract.programs_ez,
         irs_extract.schedule_o, irs_extract.grants_to_domestic_organizations,
         irs_extract.privategrants, irs_extract.privategrants_future, irs_extract.returns;
```

The vacuum after the patch, one statement per table:

```sql
VACUUM (ANALYZE) public.basic_fields;
VACUUM (ANALYZE) public.mission_statements;
VACUUM (ANALYZE) public.programs;
VACUUM (ANALYZE) public.grants_to_domestic_organizations;
VACUUM (ANALYZE) public.schedule_o;
VACUUM (ANALYZE) public.basic_fields_pf;
VACUUM (ANALYZE) public.privategrants;
VACUUM (ANALYZE) public.privategrants_future;
VACUUM (ANALYZE) public.basic_fields_ez;
VACUUM (ANALYZE) public.programs_ez;
```

No url with both sources (a full read of the table):

```sql
SELECT count(*) FROM (
  SELECT url FROM public.privategrants GROUP BY url
  HAVING bool_or(_source_version LIKE 'irs:%') AND bool_or(_source_version NOT LIKE 'irs:%')
) mixed;
```

The patch's runs:

```sql
SELECT staging_table, row_count, source_manifest->>'gt_rows' AS gt_rows_out, finished_at
FROM datamart_meta.ingest_runs
WHERE logical_name = 'irs_extract_patch' ORDER BY finished_at DESC;
```

## Log

- **2026-10-28, the plan.** PRs #81 and #83 merged to `main` at 21:37 and
  21:44 UTC. #81: GT's `2026_10_25` measured against our extract on a
  scratch copy, the checks that need no GT (`own_extract checks`, the
  unread-element audit), the 990-PF recipients' foreign address read as GT
  reads it (concordance `ce054c032e56`), and `own_extract reload`. #83: the
  October refresh branch (the builder's memory knobs and guard, the log
  handlers, the integer candidate pairs, the chunk pool, `--skip-rebuild`
  with its stamp check, the TEOS identity and the stop on five 403s), the
  three ranking proofs, and the fixes of its review. Earlier that day the
  production reload was on hold until the matcher fix; Zein: start a new
  run. GT's `2026_10_25` stays out (GT's email of 2026-10-28). Read from the
  instance and the registry the same evening: the state table above;
  nothing newer from the IRS or the BMF; no backend running.
- **2026-10-29, decision 1.** The fresh sample for B frozen read-only at
  04:37 UTC (seed 7, 1 in 100, 39,178 recipient tuples and 134,512 rows,
  2.5 minutes, work_mem 64 MB, no parallel workers, the instance quiet) and
  run under PR #84's three orders; the old order reproduces production on
  every tuple. All 558 winners `geographic` or `named_first` change were
  reviewed: against production `geographic` corrects 283 and regresses 32,
  `named_first` 395 and 51; where they disagree `named_first` is right 126
  times to 33. Each misses one criterion as written (`named_first` 113
  location flags, 67 of them corrections and 23 errors; `geographic` 467
  labeled). Decided: `named_first`, with the location criterion amended to
  "every flag reviewed, the order a net correction". The other orders left
  the code the same day; PR #84 awaits review and merge. Steps 1 to 6 do
  not wait on it.
- **2026-10-29, step 0.** 16:45 to 17:10 UTC. The box's clone on f995657
  and reinstalled. `sources status` as expected: GT's `2026_10_25`
  upstream for every source and not loaded, the BMF at `2026_10_13`, `09A`
  of 21 October the latest batch. No active backend but my own (ten idle,
  the site's pool); 63.5 GB free, 0.00 GB of swap, 23.0 GB of memory
  free; the large class; `bigdb.env` in place. `placeholder_recovery
  status` as read on 10-28 (`preflight-status.log` in the run's log
  directory). The watchdog and the census are repo code on PR #85
  (36a116e, f3c831e), each with tests; the census before ran from the
  box's worktree of that branch under the watchdog: ten relations in 6
  minutes 23 seconds, one sequential scan each with no parallel workers
  (`privategrants_current` 3 minutes 22 seconds), the instance flat at
  63.5 GB free and 0.00 GB of swap throughout; the watchdog saw the
  census's one backend every poll and ended with its exit code. The CSV is
  `census-before.csv` (171 relation-years); `nonprofit_canonical` counts
  by the organization's latest tax year, and `privategrants_w_recipients`
  holds matched rows only, so every row of it has an EIN.

  | relation | rows | with EIN | $B | $B with EIN | rows 2023 | rows 2024 | rows 2025 |
  |---|---|---|---|---|---|---|---|
  | basic_fields_current | 3,992,135 |  | 1,733.59 |  | 352,807 | 357,856 | 118,380 |
  | basic_fields_pf_current | 1,226,420 |  | 859.07 |  | 124,404 | 123,982 | 71,937 |
  | basic_fields_ez_current | 2,257,600 |  | 15.69 |  | 206,611 | 206,541 | 112,934 |
  | grants_to_domestic_organizations_current | 9,190,418 |  | 1,452.08 |  | 967,650 | 1,035,351 | 174,223 |
  | privategrants_current | 17,392,341 |  | 868.94 |  | 2,039,606 | 1,586,112 | 710,304 |
  | privategrants_future_current | 452,460 |  | 185.05 |  | 51,521 | 49,712 | 7,104 |
  | nonprofit_canonical | 1,983,340 |  |  |  | 35,687 | 354,882 | 231,253 |
  | privategrants_current_w_recovered | 582,471 |  | 16.21 |  | 143,802 | 123,392 | 1,803 |
  | privategrants_w_recipients | 10,927,644 | 10,927,644 | 488.79 | 488.79 | 1,510,723 | 1,100,948 | 443,416 |
  | unioned_grants | 19,879,313 | 19,259,196 | 1,937.37 | 1,876.49 | 2,448,508 | 2,122,550 | 613,541 |

  Step 0 is complete. Step 1 (the reload of our extract) is next, any
  time, with the watchdog over it.
- **2026-10-29, step 7.** PR #84 merged to main as b65477e at 19:18 UTC,
  after its review of the same day: ten comments, answered in 7359ad7 (one
  of them a real gap: the matcher's widened placeholder pattern had left a
  recipient line of "SEE." neither matched nor recovered; the arm is now
  one constant the matcher and the recovery classifier share, and the
  classifier is v3), a warning pinned as an error in the ranking tests in
  71d7697. The classifier's change reaches production through steps 5 (the
  work list) and 6 (the view), so those two run on code that carries the
  merge. The box's worktree for the steps is PR #85's branch with main
  merged in (19828ac), which has both; the detached worktree the ranking
  proofs ran from is obsolete. Step 8 waits only on steps 1 to 6.
- **2026-10-29, step 1.** 19:00 to 20:54 UTC, from the box under the
  watchdog (`run-reload.sh` beside the logs). The eleven tables truncated
  in a second (free storage 63.4 to 75.8 GB); `reload --jobs 4` loaded the
  38 batches from our S3 copies, none failed, 38 runs under `ce054c032e56`
  recorded 19:00 to 20:41 UTC. Against the previous load's run records the
  rows per table are identical except on the four tables the concordance
  change reaches: +230 `schedule_o`, +22 `programs_ez`, +3
  `grants_to_domestic_organizations`, +2 `privategrants`, the 257 rows the
  plan expected (the 49 returns' `irs:` group rows). `checks`: 990-PF
  grants against line 25 255,649 of 263,734 (96.9%), Schedule I against
  Part IX line 1 134,075 of 137,301 (97.7%), both the scratch copy's
  figures to the row; no batch 2 points below the median; nothing unread
  inside a group and no rename candidates; the elements first seen in a
  later schema version are return-header flags. `irs_extract` is 12.35 GB
  as before; free storage 62.1 GB after. The watchdog read the instance
  flat (swap 0.00 GB throughout) on 113 polls with our backend active and
  ended with the script's code. Slower than the hour planned because of
  the checks line written after each batch's load, up to twenty minutes
  on the largest batches; an index on `_batch_id` or one pass at the end
  would fix it (afterwards). Also this evening: PR #85 merged (a71844b) and
  main merged into this branch (285701d), so the box's clone on this
  branch runs every later step; the two detached worktrees are gone. Step
  2, the patch, is next: snapshot first, then on the go-ahead.
- **2026-10-29, step 5 skipped; step 2 started.** Nothing has been filed
  or read since the work list and the read of 27 October, so neither is
  repeated this run (decided while step 2 ran): step 6 loads the 530
  pages' verdicts stored that day over the October work list, which the
  loader reads as it stands. The classifier's new arm therefore reaches the
  work list and the read in the next run only; a recipient line of "SEE."
  leaves matching unrecovered until then. Step 2 started at 21:30 UTC under
  the watchdog, after the manual snapshot `gt-datamart-before-repatch-20261029`
  reported available at 21:16 UTC.
