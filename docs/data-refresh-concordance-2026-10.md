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
  of free storage (10 GB for step 8's second attempt, my decision of
  2026-10-30 after the first attempt stopped at 35: the matcher's rebuild
  and its write phase are each one transaction and dip about 30 GB, and no
  one else was on the instance), on a rise of more than 0.5 GB of swap from
  the watch's start (the metric keeps reporting pages swapped out earlier),
  under 0.3 GB of freeable memory; on a breach it cancels the box's
  backends. New in this
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

- [x] A manual snapshot of the instance, status *available*. Done:
      `gt-datamart-before-repatch-20261029`, requested 21:10 UTC, available
      21:16 UTC.
- [x] `python -m givingtuesday_datamart.own_extract patch`: in one
      transaction our earlier rows (under `3d9b48d6e5e7`) out and the new
      ones in; GT's rows go only under urls our tables hold for the first
      time (the 49 returns'); ten `irs_extract_patch` runs under
      `irs_extract@ce054c032e56`; the basic-fields relations rebuilt. Done
      21:30 to 22:00 UTC under the watchdog: the transaction to 21:51, GT's
      142 rows out under the 49 returns' urls (129 Schedule O, 11 990-EZ
      programs, 2 990-PF grants), the ten runs recorded, the three relations
      rebuilt with the same filer-year counts (3,992,135 / 1,226,420 /
      2,257,600).
- [x] The counts per table logged: about 20.11 million of our rows out and
      back in on the ten tables, ours 257 rows more. Done: 20,109,675 out,
      20,109,932 in (`patch.log`).
- [x] `VACUUM (ANALYZE)` on the ten staging tables (appendix). Done 22:00 to
      22:13 UTC, no dead row left; `schedule_o` took ten of the thirteen
      minutes.
- [x] Checks: no url holds both GT's rows and ours on `basic_fields_pf`
      and `privategrants` (appendix); the filer-years with a tax period
      ending in 2025 or later are all ours. Done 22:18 UTC: 0 and 0 mixed
      urls; 2025+ filer-years 261,728 / 94,024 / 174,267 ours, GT's 0, as in
      October.

### 3. The canonical build (the box, about 15 minutes)

- [x] `python -m givingtuesday_datamart.sources build-canonical`. Done
      22:21 to 22:35 UTC under the watchdog (821 s), build a0879ba0.
- [x] A `success` row in `datamart_meta.canonical_builds`; its lineage
      names the new patch run. `nonprofit_canonical` should hold the same
      rows as on 2026-10-27 (1,983,340); the text tables gain the 49
      returns' notes and programs. Done: `success`, lineage
      `irs_extract_patch` 38cce5de under `ce054c032e56` and the reload's
      last run; `nonprofit_canonical` 1,983,340, 628,622 with their latest
      return from our extract and 412,224 with a 2025+ tax period (GT's 0),
      as on 10-27; `schedule_o_part_iii` 1,149,203 (+3), `nonprofit_text`
      811,437 (+1), `funder_canonical` 174,809 (same).

### 4. The grants relations (the box, about 45 minutes)

- [x] `python -m givingtuesday_datamart.current_grants --only privategrants_current`,
      then `--only privategrants_future_current`. Done 22:35 to 23:03 UTC
      under the watchdog: the paid relation in 24 minutes, the future one
      in a minute; the recovered view recreated for policy `v3` and the
      matcher's first view with it.
- [x] The paid build's last line reads `0 filer-years look doubled without
      a repeated basic row`; otherwise the filings it names are checked
      against their XML before step 8. Done: 0.
- [x] `status` shows the three line-25 rules; the rows by rule logged
      against October's (17,392,341 paid, 452,460 future). Done 23:10 UTC:
      17,392,341 paid and 452,460 future, every rule's count October's to
      the row (passthrough 16,702,141, latest_url 672,530, total_row
      16,806, the rest as logged); the view shows `v3` and holds 17,970,006
      rows, 582,471 of them recovered.

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

- [x] `python -m givingtuesday_datamart.placeholder_recovery load --policy v3`
      (it takes the verdicts stored on 2026-10-27 over the October work
      list, which the loader reads as it stands). Done 23:12 to 23:25 UTC
      under the watchdog (762 s): 56 filings written, 4,653 unchanged, no
      row taken out; paid 4,664 filings, 584,684 rows, $16,313.2M (from
      4,611, 582,471, $16,210.9M); future 177 filings, 6,671 rows,
      $1,295.1M (from 173, 6,531, $1,268.7M).
- [x] `python -m givingtuesday_datamart.placeholder_recovery view --policy v3`.
      Done 23:25 UTC: the view shows the rows loaded under `v3`.
- [x] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3`
      passes; rows and dollars by target logged. Done 23:25 UTC: the nine
      checks pass with 0 each; 584,684 paid rows of 4,664 filings in the
      view, the dollars by target as above (`step6.log`).

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

- [x] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3`.
- [x] `python -m givingtuesday_datamart.corrections_preflight` (2 h 50 min
      in October; 2 h 49 min on 10-29, `GATE: PASS`).
- [x] `python -m givingtuesday_datamart.grant_matching --workers 12`: a
      full run, which rebuilds the `_current` relations and stamps them.
      In October the integer index took about an hour and the pool an hour
      and a half. The first attempt of 10-30 was stopped by the watchdog
      at the 35 GB storage floor during the rebuild (see the log): a full
      run dips about 30 GB below its resting level twice, once in the
      rebuild and once in the write phase, each a single transaction. The
      second attempt, under a 10 GB floor, ran 04:15 to 08:06 UTC on 10-30
      (3 h 51 min; build c6aee914, lineage `success`); the two troughs were
      31.4 and 33.9 GB.
- [x] `python -m givingtuesday_datamart.matching_regression_checks`:
      `GATE: PASS` at 08:12 UTC on 10-30: 18 PASS, 2 WARN (the arms against
      the August baselines; the recovered rows of lists marked as grants to
      individuals, October's numbers), no FAIL.
- [x] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3`:
      9 of 9 ok at 08:13 UTC on 10-30.

### 9. The accepting commit (laptop)

- [x] The baselines updated in one commit, every number that moved
      explained with the census, the gate rerun on `PASS`
      (`docs/matching-regression-runbook.md`, *Updating the baselines*).
      Done as 66b4f76 on 10-30: the gate rerun with the new baselines 19
      PASS, 1 WARN, no FAIL (13:41 to 13:48 UTC); the census after at
      13:54 UTC, three relations moved, each explained in the commit and
      the log.

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
- The matcher's rebuild of the `_current` relations and its write phase
  committing per relation, as the standalone rebuild does, so that a full
  run needs about 12 GB of headroom on the shared instance rather than the
  27 GB the two troughs of 10-30 took, and runs under the 35 GB floor by
  day. The write phase is one transaction by design (a failure leaves no
  stale `unioned_grants`); committing after `privategrants_w_recipients`
  trades that for the headroom, a decision for the PR. PR #87 (open on
  10-30): the rebuild commits one relation at a time, and the write
  phase's two transactions are an opt-in flag, `--commit-between-outputs`,
  off by default; its headroom after the change is arithmetic on the
  relation sizes, not measured, so the next full run measures it.
- A corrections row for Johns Hopkins University at the campus address
  (3400 N Charles Street, Baltimore). The club's filing there still takes
  the 428 grants that name the university or one of its schools inexactly
  ("johns hopkins", "johns hopkins u", "johns hopkins medicine") at that
  address, where no filer name is exact and the address decides; the gate's
  ceiling on the club is that count, from the run of 10-30. A row of the
  registry giving the university a filer row at the campus address would
  take them under `named_first`.
- Foreign recipients on the name-only tier. The tier takes a grant tuple
  with no state and no zip whose cleaned name belongs to one filer; a
  foreign address, which GT and now our extract put in the US address
  columns, has a postal code and often a province, so the Global Fund,
  Oxford, AGRA, CIMMYT and 1,280 other recipients whose own EIN is in the
  universe never reach it (the step 9 correction: $1.77B of our 2023 to
  2025 rows matched in October and not now; $3.77B of GT's 2009 to 2022
  rows never matched). A foreign address should count as no US address
  for the tier; the proof splits the names by whether the filer is the
  recipient's foreign registration (a 98- EIN) or a domestic namesake, and
  the gate gets a floor on a foreign recipient. Then a matcher run.

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
- **2026-10-29, step 2.** 21:30 to 22:18 UTC under the watchdog
  (`run-patch.sh` beside the logs), the box's clone on 285701d. The patch's
  one transaction ran to 21:51: 20,109,675 of our rows out and 20,109,932
  in, the 257 more the plan expected; GT's rows left only under the 49
  returns' urls, 142 of them (129 Schedule O notes, 11 990-EZ programs, 2
  990-PF grants); ten `irs_extract_patch` runs under
  `irs_extract@ce054c032e56`. The three basic-fields `_current` relations
  were rebuilt by 22:00 with the builder's large-class knobs, same
  filer-year counts as before. `VACUUM (ANALYZE)` of the ten tables ran
  22:00 to 22:13 and left no dead row. Checks: no url with both sources on
  `basic_fields_pf` or `privategrants`; every filer-year with a tax period
  ending in 2025 or later is ours (261,728 / 94,024 / 174,267, GT's 0),
  October's figures exactly. The watchdog saw our backend on 47 polls, no
  breach, swap 0.00 GB throughout; free storage dipped to 50.0 GB during the
  transaction and stands at 57.8 GB after. Production's staging now carries
  our rows under `ce054c032e56`; the site reads them as its caches turn
  over. Steps 3 and 4 are next.
- **2026-10-29, steps 3 and 4.** 22:21 to 23:10 UTC in one session under
  the watchdog (`run-steps34.sh` beside the logs). The canonical build
  a0879ba0 took 821 s and succeeded with the same 1,983,340 organizations
  as on 10-27, 628,622 of them with their latest return from our extract
  and 412,224 with a 2025+ tax period; its lineage names the patch run of
  step 2 and the reload's last run; the text tables moved by the 49
  returns' notes and programs (`schedule_o_part_iii` +3, `nonprofit_text`
  +1). `privategrants_current` rebuilt in 24 minutes to 17,392,341 rows
  with October's rule counts to the row and a doubling watch of 0;
  `privategrants_future_current` to 452,460; the recovered view came back
  under `v3` with 17,970,006 rows, 582,471 of them recovered, so the lists
  loaded on 10-27 ride into the matcher. The watchdog saw our backend on
  47 polls, no breach, swap 0.00 GB; free storage dipped to 45.3 GB during
  the paid build's temp spill and stands at 57.8 GB after. Step 6 is next:
  the load of the 10-27 verdicts, the view and the check under `v3`.
- **2026-10-29, step 6.** 23:12 to 23:27 UTC under the watchdog
  (`run-step6.sh` beside the logs). `load --policy v3` over the October
  work list (25,612 filings, 9,154 with a fetched attachment) wrote 56
  filings from the verdicts stored on 10-27 and left the 4,653 already
  loaded as they were: the paid lists now 4,664 filings, 584,684 rows,
  $16,313.2M (53 filings, 2,213 rows and $102.3M more than on 10-27), the
  future lists 177 filings, 6,671 rows, $1,295.1M (4, 140 and $26.4M
  more); 24 filings whose list adds up only to paid and future together
  stay out ($346.7M). The view was recreated under `v3` and `check` passes
  its nine checks with 0 each; the status screen shows the same figures.
  The watchdog saw our backend on 14 polls, no breach, swap 0.00 GB, free
  storage 57.8 GB throughout. Everything the matcher reads is in place:
  step 8 is next, on the go-ahead, from the staged `run-matcher.sh`.
- **2026-10-29 to 10-30, step 8, first attempt.** 23:29 to 02:50 UTC under
  the watchdog (`run-matcher.sh` beside the logs), the box's clone on
  285701d. `check --policy v3` passed at 23:29. The corrections preflight
  ran 23:29 to 02:19, 2 h 49 min, October's time to the minute: all 27
  correction rows survive the universe dedup, every verbatim tuple wins to
  its EIN at a name score of 1.00 from the correction source, every row
  wins at least one grant tuple, and the same two advisory warnings as in
  October (rows 8 and 26 were authored without a sighted grant row);
  `GATE: PASS`. The matcher started at 02:19 as build 8112f05d and rebuilt
  the `_current` relations in one transaction: the three basic-fields
  relations by 02:24 (3,992,135 / 1,226,420 / 2,257,600 rows, the counts of
  step 2), `grants_to_domestic_organizations_current` by 02:30 (9,190,418),
  and it was 19 minutes into `privategrants_current` when free storage
  touched 34.99 GB; the watchdog cancelled the backend and stopped the run
  (exit 2). The cancel rolled the whole rebuild back: every `_current`
  relation is the one steps 2 and 4 built (their statistics timestamps are
  unchanged), nothing is half-built, no backend was left behind, and free
  storage was back at 57.9 GB within the minute. Lineage records the build
  as `failed` at 02:50:28. Why this dip is deeper than step 4's 15 GB: the
  matcher rebuilds the six relations inside one transaction, and a table
  dropped inside a transaction gives its space back only at commit, so the
  old and new copies of all six coexist, about 26 GB, plus the sort spill;
  the standalone rebuild of step 4 commits per relation. CloudWatch shows
  the same shape on October's complete run of the same code: the rebuild
  dipped from 69.9 to 39.5 GB (10-27, 16:00 to 16:50) and the write phase,
  which swaps its two output tables in one transaction by design, from
  69.7 to 41.7 GB (10-28, 03:30 to 03:50). The run now starts from 57.9 GB
  rather than 69.9 (October's outputs are 10 GB larger than August's), so
  the rebuild's trough falls near 29 GB and the write's between 28 and
  38 GB: under the 35 GB floor, and 25 GB or more above empty on the 300 GB
  volume, consumed at no more than a gigabyte a minute against a poll every
  minute. My proposal for the second attempt, Zein's call: `run-matcher-2.sh`
  (beside the logs) under the watchdog with `--min-free-gb 25`, starting at
  the matcher, since the check and the preflight passed on this very data
  and nothing has been written since; about four hours. For afterwards: the
  rebuild and the write committing per relation, so a full run no longer
  needs 30 GB of headroom (a PR, not a change mid-run).
- **2026-10-30, step 8, second attempt started.** 04:15 UTC,
  `run-matcher-2.sh` under the watchdog with `--min-free-gb 10`, my call
  rather than the 25 GB proposed: the first attempt showed the shape of the
  dips, and no one else uses the instance tonight. It starts at the
  matcher; the check and the preflight stand from the first attempt, with
  nothing written in between. About four hours.
- **2026-10-30, step 8, second attempt, done: `GATE: PASS`.** 04:15 to
  08:13 UTC, `run-matcher-2.sh` under the watchdog with `--min-free-gb 10`,
  the box's clone on 285701d; build c6aee914, lineage `success` at
  08:06:40, its `source_runs` naming the reload of step 1 (batch 09A), the
  patch run of step 2, the BMF of 2026-10-13 and the ranking `named_first`.
  The matcher took 3 h 51 min. The rebuild of the six `_current` relations
  ran 04:15 to 04:52 (37 min) and gave the counts of steps 2 and 4 to the
  row (3,992,135 / 1,226,420 / 2,257,600 / 9,190,418 / 17,392,341 /
  452,460; doubled filer-years 0); the universe and grants reads and the
  cleaning took to 05:04; the integer index 05:04 to 05:52 (48 min;
  2,304,254,598 candidate pairs in 46,086 chunks); the pool 05:52 to 07:41
  (1 h 48 min, twelve workers, about seven chunks a second; no chunk was
  reusable, the checkpoint prefix carries the ranking's new shape); the
  ranking itself two minutes (27,663,249 scored pairs; the
  one-filer-in-the-state rule refused 254,871 pairs of 4,609 grant tuples
  and left 3,310 of them unmatched; 2,554,447 pairs after resolution;
  40,394 tuples on the name-only tier); the write phase 07:43 to 08:06
  (23 min: the temp table, `privategrants_w_recipients`, `unioned_grants`,
  the index and the statistics). Outputs: `privategrants_w_recipients`
  10,925,841 rows (October's run 10,927,644), `unioned_grants` 19,877,536
  (19,879,313). The gate ran 08:06 to 08:12: 18 PASS, 2 WARN, no FAIL. The
  two failures of October's run are gone: placeholder rows matched 0
  (October 415 against a ceiling of 349; the ceiling is the hard zero of
  input shape 6 now) and Johns Hopkins University 3,852 rows (200 in
  October, when the club held 706; the club now holds 428 under its
  ceiling of 706). Self-matches 3,419 (ceiling 3,641; October's 3,661 was
  a WARN); the Michael J. Fox Foundation 4,667; the Gates Trust 10; the
  government guard refused nothing of 8,059 non-exact government-named
  rows; the fan-out and subset invariants all hold; labeled-pair coverage
  PF 71.9% (October 71.2%, floor 63.1%), 990 94.7% (floor 94.5%). The
  first WARN is the rows per arm against the August baselines:
  basic_fields 7,679,858 (+2.7% on 7,478,348), basic_fields_pf 69,348
  (-18.5% on 85,071), eo_bmf 3,160,323 (the arm's first accepted run; its
  baseline is 0), correction 16,312 (+6.1% on 15,376). Against October's
  run under the old order the arms moved as the new order means them to:
  1.96 million rows from eo_bmf (5,132,662) to basic_fields (5,718,304),
  the recipient's own filing winning over the BMF line that carries its
  name, and the PF arm up from 60,316. The PF arm's fall from August is a
  move between arms, not a loss: the recipients that file a 990-PF hold
  111,596 matched rows in this run, 69,348 through their PF filing (13,783
  recipients), 21,877 through a BMF line carrying their name (3,631) and
  20,371 through a 990 filing of theirs (1,528); the first two alone exceed
  August's 85,071, and the PF labeled-pair coverage is up. The second WARN,
  recovered rows of
  lists marked as grants to individuals, is 2,972 rows, 494 of them on
  the name alone: October's two numbers. `check --policy v3` after: 9 of
  9 ok, 584,684 rows of 4,664 filings in the view, as step 6 left them.
  The watchdog: 238 polls, no breach; swap flat at 0.04 to 0.05 GB,
  freeable memory never under 20.8 GB; free storage 58.7 GB at the start,
  the rebuild's trough 31.4 GB at 04:54 (27.2 GB down; it held while the
  reads ran inside the same transaction and was released at 04:57), the
  write's trough 33.9 GB at 08:02 (25.7 GB down), 57.5 GB at rest
  afterwards, where it was before. Both troughs sat above the 25 GB floor
  I had proposed; the 35 GB default would have stopped both phases, as it
  stopped the first attempt. The default stays 35 for the shared instance
  by day; until the rebuild and the write commit per relation
  (Afterwards), a full run takes the quiet window and a lower floor. Step 8
  took 7 h 18 min over its two attempts, the seven hours planned. No
  backend but ours was on the database when it ended. Next: step 9, the
  accepting commit, with the census after as its evidence.
- **2026-10-30, step 9: the accepting commit, `GATE: PASS`.** The box's
  part 13:41 to 13:54 UTC under the watchdog at its default floor
  (`run-step9.sh`, `step9.log` and `watchdog-step9.log` beside the logs:
  14 polls, no breach, free storage 61.9 to 61.0 GB, swap flat at
  0.04 GB), all of it read-only. The baselines of
  `matching_regression_checks.py` moved to the run's numbers by the
  runbook's rules, every move explained in the commit (66b4f76,
  *Regression baselines: accept the 2026-10-30 run*): the coverage floors
  PF 71.9 and 990 94.7; the Michael J. Fox Foundation's floor 4,434 (95%
  of 4,667; 3,204 in August and 4,668 on 10-28, so the rise is the BMF
  arm's 958 rows, 272 recovered rows and 297 rows of 2025 filer-years,
  not the winner order); Johns Hopkins University 3,659 (95% of 3,852,
  all on its own filing, none left to a BMF line); the club's ceiling
  428, the count itself, since no grant row names the club: its 428 rows
  name the university or one of its schools inexactly ("johns hopkins",
  "johns hopkins u", "johns hopkins medicine") at 3400 N Charles Street,
  where no filer name is exact and the address decides (Afterwards: a
  corrections row); self-matches 3,419; the rows per arm as observed, the
  BMF arm's first accepted run, the PF arm's fall the move between arms
  the step 8 entry describes; placeholder, corporate, foreign and person
  rows, the Gates Trust and the government guard unchanged. The gate
  rerun on the box with the edited module copied into the clone's working
  tree, the commit waiting on its result as the runbook asks: 19 PASS, 1
  WARN (the recovered rows of lists marked as grants to individuals, a
  count to look at by design), no FAIL. The census after against the
  census before of 10-29 17:10 UTC, which preceded steps 1 to 8: 34
  relation-years moved, all in the three relations the run wrote; the
  seven source relations unchanged to the row and the dollar in every tax
  year, so the reload under the new concordance and the patch changed
  what the columns hold, not which rows exist.

  | relation | rows before | rows after | moved | $B before | $B after | moved |
  |---|---|---|---|---|---|---|
  | privategrants_current_w_recovered | 582,471 | 584,684 | +2,213 | 16.21 | 16.31 | +0.10 |
  | privategrants_w_recipients | 10,927,644 | 10,925,841 | -1,803 | 488.79 | 486.39 | -2.40 |
  | unioned_grants | 19,879,313 | 19,877,536 | -1,777 | 1,937.37 | 1,934.98 | -2.39 |

  The recovered view's rows are step 6's load (53 filings, 2,213 rows,
  $102.3M, in 2023 and 2024). The matcher's two tables against October's
  output under the old order: the 415 placeholder rows October matched are
  out of the grants view since input shape 6, and their aggregate amounts
  are the dollars (-$1.01B in 2023 and -$0.96B in 2024, where the pattern
  rows of `privategrants_current` carry $7M a row on average); the 2,213
  new recovered rows add matches (389,807 of the 584,684 loaded rows
  match, 66.7%); the name-only tier matched 40,394 grant tuples against
  41,221; the one-filer-in-the-state rule left 3,310 tuples unmatched in
  both runs. October's output is gone, so the split of the 1,803 rows
  among these is not measured; the 1.96 million rows `named_first` moved
  between arms change no count. The offline suite passes (860).
  `tests/test_precision_guards.py` no longer pins the BMF arm's baseline
  at 0; the runbook's pointer to the worked example is fixed (`4131d27`,
  not a hash from before a rebase) and names 66b4f76 as the second. The
  box's clone pulled to the commit afterwards. The matcher's per-relation
  commits are PR #87 (Afterwards). Next: step 10.
- **2026-10-30, step 9, correction: where the dollars went.** The entry
  above and the commit message of 66b4f76 put the matched tables' -$2.40B
  on the 415 placeholder rows. They carry $0.63B (the BMF policy proof's
  summary of 10-28,
  `data/placeholder_recovery/bmf_policy_subset_summary_2026_10_28.csv`,
  and the diff below). The rest is the foreign address. PR #81's
  concordance fills the recipient address columns from the foreign address
  when there is no US one, as GT does; our rows of October left them
  empty. A grant tuple with a postal code or a province in its keys is not
  for the name-only tier, so the grants of our extract's filings to
  foreign recipients that October matched on the name alone to the
  recipient's own EIN — the Global Fund (980380092), the University of
  Oxford (986001062), AGRA, CIMMYT, the University of Manitoba, CGIAR, the
  International Potato Center, 1,284 names in all — are unmatched now:
  2,595 rows and $1.77B of 2023 to 2025. Measured by diffing the two
  outputs' matched tuples (the ranking proof of 10-28 kept October's in
  its `production.parquet`): 2,594,962 tuples then, 2,594,841 now,
  2,593,649 in both (60,707 with another EIN, the arms' move), 1,313
  matched in October only — 114 placeholder tuples (413 rows, $0.63B) and
  1,199 tuples with all five address keys empty, every one matched in
  October on the name-only tier (593 to a BMF line, 572 to a 990 filing,
  33 to a 990-PF, 1 to a correction), whose rows now carry the foreign
  address — and 1,192 matched now only (1,424 rows, $0.12B; 803 of the
  rows recovered grants). The same gap has always held for GT's rows,
  which carry the foreign address: under those 1,284 names alone, 6,862
  rows and $3.77B of 2009 to 2022 are unmatched, as they were in August
  and in October. So: no regression against the accepted August run; a
  loss against October's output of $1.77B that the site showed until
  08:06 UTC; and a measured recall gap of at least $5.6B behind it, with
  the recipients' own EINs in the universe. The fix is the name-only
  tier's eligibility — a foreign address is no US address — in a PR of its
  own with a proof (the 1,284 names as the pool, split by whether the
  filer's EIN is a foreign registration or a domestic namesake) and a
  sentinel for a foreign recipient, then a matcher run (Afterwards). The
  baselines of 66b4f76 are the run's counts and stand. Scripts and parquet
  files beside the logs: `step9_output_diff.py`, `step9_moved_rows.py`,
  `step9_foreign_rows.py`, `step9-diff/`.
