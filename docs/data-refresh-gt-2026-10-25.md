# Data refresh: GT's 2026_10_25 release, the new concordance, the matcher's policy

The run after the October refresh (`docs/data-refresh-2026-10.md`). It takes
GivingTuesday's release of 2026_10_25 into production, reloads our IRS
extract under the concordance PR #81 merged and patches it over the new
release, loads the placeholder lists read since the last load, and runs the
matcher once, under a policy decided before it starts, to a gate that
passes. The October run's gate failure and close-out are finished here.

Plan written 2026-10-28 after PRs #81 and #83 merged. Each step is checked
in with Zein before it runs; the boxes are ticked and the log filled in as
they do.

## Where things stand on 2026-10-28

| what | state at 22:00 UTC |
|---|---|
| GivingTuesday's releases | production holds the June release (`2026_06_16`; Schedule I and O `2026_06_25`). GT published `2026_10_25` on 2026-10-27 for all ten sources we load (and the two officer tables a refresh skips). It was measured against our extract on a scratch copy on 2026-10-28 (`docs/own-extract.md`, *GT's 2026_10_25 release*) and not ingested |
| the IRS's batches | 38, `2024_TEOS_XML_01A` to `2026_TEOS_XML_09A`; nothing newer (the next is due about 2026-11-18) |
| our extract in production | all 38 batches loaded under concordance `3d9b48d6e5e7`. `main` carries `ce054c032e56` (990-PF recipients' foreign addresses; the group rows of 49 returns written with an `irs:` prefix), so `own_extract patch` refuses until `own_extract reload` has run |
| production staging | the June release with our patch of 2026-10-26 (under `3d9b48d6e5e7`) |
| basic-fields `_current`, canonical, grants `_current` | built 2026-10-26 to 10-28 over June plus the patch. None carries the source stamp the matcher writes since #83, so the next matcher run rebuilds them; `--skip-rebuild` refuses until then |
| placeholder recovery | the view shows `v3`. Loaded under `v3` on 2026-10-27: 4,611 paid filings, 582,471 rows, $16.21B; 173 future filings, 6,531 rows. The read of 2026-10-27 stored verdicts for 530 new pages that are not loaded yet, and left 74 pages without a verdict (Qwen's provider was down) |
| the matcher's output | the run of 2026-10-28 (`privategrants_w_recipients` 10,927,644 rows, `unioned_grants` 19,879,313), live on the site. Its gate failed: placeholder rows matched 415 against a ceiling of 349, and the Johns Hopkins sentinel 706 against 1,735, whose EIN turned out to be the Johns Hopkins Club, not the university. Not accepted; no baseline moved |
| the ranking proofs (#83) | the placeholder exclusion is clean (all 415 rows out, nothing else moves). Removing BMF alias lines loses real aliases (Bates College, WETA) and leaves the university's grants on the club. Geography-aware ranking is the best tested: labeled relationships 464 to 469 of 647 with none lost; rows naming Johns Hopkins University to the university 191 to 3,834 and none to the club; Duke 1,890 to 5,221; PAWS and Notre Dame kept. It failed fresh validation on a grant whose state contradicts its own city and ZIP. None of it is in the matcher |
| the instances | the shared instance is still the large class of 2026-10-27 (about $0.48 an hour; 63.6 GB free, no swap); the box is still at 256 GB of memory (about $2 an hour); both idle. The large-server builder knobs (`bigdb.env`) are on the box |
| undo | the snapshot `gt-datamart-before-irs-patch-20261026` (June, before the patch) |
| who reads the instance | this datamart's site (pages revalidate hourly; the CDN serves stale up to a day) and the public trackers, which query their own database on the same instance live |

## Decisions before we start

1. **GT's 2026_10_25 as production's release.** It is a test of the parser
   and concordance changes GT made after our defect reports
   (`docs/own-extract.md`). Measured on our
   batches' returns it fixes the doubled rows and blocks, the filled grant
   amounts and the lost numbered columns, and adds 2026's 05A, 06A and 07A;
   its new defects (the American Online Giving Foundation's doubled 2024
   Schedule I, AmazonSmile's missing relationship, dropped empty grant
   groups) sit in returns our patch replaces. What it changes before 2024,
   where its rows stay, is not measured; the census of step 0 records it.
   My recommendation: take it, unless GT has said a corrected release is
   days away. The alternative is to reload and patch over June (steps 1
   and 3 without step 2), and take GT's next release later.
2. **The matcher's policy.** It gates step 9 only; steps 0 to 7 run either
   way.
   - **A.** The placeholder exclusion alone, and the gate's Johns Hopkins
     sentinel moved to the university's EIN, 520595110. Ready in an hour.
     The university's grants stay on the BMF records of its affiliates
     (about $2.2B on the October output), which the accepting commit then
     states as a known error.
   - **B.** A, plus the geography-aware winner order with one revision: a
     grant whose state contradicts its own city and ZIP counts as
     conflicting evidence instead of as its state. About a day of work
     beside steps 1 to 7. Validated on a fresh sample frozen in step 0,
     before step 2 changes the inputs, against criteria fixed now: the
     current policy reproduces production's winners on the sample;
     labeled coverage at least 469 of 647 with none lost; no strong
     location-loss flag where the grant's city and ZIP agree with the old
     winner; every other flag listed and reviewed. If B does not pass, A
     runs.

   My recommendation: B.
3. **The instances.** My recommendation: keep both as they are through step
   9 and scale both back right after the gate. Every class change of the
   shared instance is a restart of five to ten minutes, which the public
   trackers feel, and the reload, the proofs and the matcher use the box's
   cores and memory. Shrinking the box between steps saves about $1.85 an
   hour against two restarts and two address changes.
4. **The read's cap.** $300, as in October. The projection should be small:
   the 74 pages and whatever filings join the work list.
5. **The quiet window.** Steps 2 to 4 and step 9 in the evening, Pacific;
   steps 0, 1 and 8 any time.
6. **Not in this run** unless decided: the 2015-2019 read ($436 projected),
   PR #80 (`irs_source` reading the XML from the IRS archive), the officer
   tables.

## Rules

- One writer at a time on the shared instance. The long steps run on the
  box, each in its own `tmux -L refresh` session, logging to
  `~/logs/refresh-gt-2026-10-25/`.
- A watchdog over the instance through every heavy step: stop under 35 GB
  of free storage, on a rise of more than 0.5 GB of swap from the watch's
  start (the metric keeps reporting pages swapped out earlier), under 0.3 GB
  of freeable memory; on a breach it cancels the box's backends. New in this
  run: the watchdog is repo code and runs on the box, not a script on a
  laptop whose connection drops.
- The builder's memory comes from `bigdb.env` only while the instance is
  the large class. On the medium class the builder refuses a budget past its
  defaults (#83), so a forgotten file stops before any DDL.
- A snapshot before step 2: the undo for the refresh and the patch.
- Never load recovered rows while the matcher runs. No `--skip-rebuild` in
  this run.
- TEOS: urllib's own identity, and the fetch stops on five 403s in a row;
  never another User-Agent, a rotation or a retry of a 403.
- The paid read, the instance classes and the matcher's policy are Zein's
  calls.
- Baselines move only on `GATE: PASS`, in the commit that accepts the run.

## Checklist

### 0. Preflight (the box and the laptop, about an hour; nothing written to production)

- [ ] The box's clone on this branch and reinstalled:
      `git fetch && git switch zeclaude/data-refresh-gt-2026-10-25 && pip install -e '.[ingest]'`.
- [ ] `python -m givingtuesday_datamart.sources status`: `2026_10_25` for
      the ten sources, the BMF at `2026_10_13`.
      `python -m givingtuesday_datamart.own_extract batches 2026`: `09A`
      the latest.
- [ ] No backend on the instance but ours (appendix); free storage at
      least 60 GB; the classes as decided; `bigdb.env` on the box only if
      the instance is the large class.
- [ ] `python -m givingtuesday_datamart.placeholder_recovery status`
      logged.
- [ ] The watchdog as repo code, with tests, started on the box for each
      heavy step.
- [ ] The census before: for each relation production reads (the
      basic-fields and grants `_current` relations, `nonprofit_canonical`,
      the recovered rows, the matcher's two tables), rows and dollars by
      tax year, as a small rerunnable module; the CSV stays with the run's
      logs, the table goes in this log. Run again in step 11, it says what
      moved where, which the accepting commit must explain.
- [ ] For decision B: the fresh validation sample frozen now, read-only,
      with a new hash seed and none of the recipients any proof has
      inspected.

### 1. Our extract under the new concordance (the box, about an hour)

Only `irs_extract` changes: production's staging and the site are as they
were. If anything here looks wrong we stop with production untouched.

- [ ] The eleven `irs_extract` tables truncated (appendix). `reload` takes
      its list of batches from the run records, not from the tables, and
      replaces a batch's rows in one transaction, so without the truncate
      the old rows would be 12 GB of dead tuples; `patch` refuses any batch
      not reloaded.
- [ ] `python -m givingtuesday_datamart.own_extract reload --jobs 4`: each
      batch from our S3 copy, checked against the SHA-256 its first load
      stamped, nothing fetched from the IRS; 38 batches report, none fails.
- [ ] `python -m givingtuesday_datamart.own_extract checks`: 990-PF grants
      against line 25 about 255,649 of 263,734; Schedule I against Part IX
      line 1 about 134,075 of 137,301 (the scratch copy's figures under
      `ce054c032e56`); no batch 2 points below the others.
      `irs_extract.unread_paths` lists nothing inside a group.

### 2. GT's 2026_10_25 release (the box, quiet window, two to three hours; snapshot first)

- [ ] A manual snapshot of the instance, status *available*.
- [ ] `python -m givingtuesday_datamart.sources refresh`: the ten sources
      (the officer tables are skipped by default, the BMF is current). Each
      staging table is dropped and loaded again from GT's file, our patch
      rows with it, and the basic-fields `_current` relations are rebuilt
      over GT's release. Until step 3 ends, the site's basic-fields
      relations show GT's release without the returns only our extract has,
      so steps 2 and 3 run back to back. The views built on the staging
      tables go with them; the grants builder (step 5) and the matcher
      (step 9) create them again.
- [ ] Ten `ingest_runs` rows at `2026_10_25`; rows per table against June
      logged. This is the first full refresh run from the box, so its time
      goes in the log.

### 3. The patch, ours first (the box, right after step 2, about an hour with the vacuum)

- [ ] `python -m givingtuesday_datamart.own_extract patch`: GT's rows under
      our urls out, ours in, in one transaction; ten `irs_extract_patch`
      runs under `irs_extract@ce054c032e56`; the basic-fields relations
      rebuilt.
- [ ] The counts per table logged. They will differ from October's
      (16,662,030 out, 20,109,675 in): GT's release now holds 2026's 05A to
      07A and none of its doubled rows, and ours gains the 49 returns' 257
      group rows.
- [ ] `VACUUM (ANALYZE)` on the ten staging tables (appendix).
- [ ] Checks: no url holds both GT's rows and ours on `basic_fields_pf`
      and `privategrants` (appendix); the filer-years with a tax period
      ending in 2025 or later are all ours.

### 4. The canonical build (the box, about 15 minutes)

- [ ] `python -m givingtuesday_datamart.sources build-canonical`.
- [ ] A `success` row in `datamart_meta.canonical_builds`; its lineage
      names GT's `2026_10_25` runs and the patch run.

### 5. The grants relations (the box, about 45 minutes)

- [ ] `python -m givingtuesday_datamart.current_grants --only privategrants_current`,
      then `--only privategrants_future_current`.
- [ ] The paid build's last line reads `0 filer-years look doubled without
      a repeated basic row`; otherwise the filings it names are checked
      against their XML before step 9.
- [ ] `status` shows the three line-25 rules; the rows by rule logged.

### 6. The work list and the read (the box; the read on Zein's go, cap $300)

- [ ] `python -m givingtuesday_datamart.placeholder_recovery work-list`;
      counts by tax year and band logged against October's 25,612.
- [ ] The projection:
      `python -m givingtuesday_datamart.placeholder_recovery run --policy v3 --tax-years 2020-2025 --keep-list --dry-run --no-fetch`.
- [ ] Qwen's provider answers before the read starts (it failed most calls
      on 2026-10-27).
- [ ] The read, in `tmux -L frame`:
      `python -m givingtuesday_datamart.placeholder_recovery run --policy v3 --tax-years 2020-2025 --keep-list --cache /data/irs_index --cap 300`.
      Spend logged; no page left without a verdict, or the reason.

### 7. The load, the view and the checks under v3 (the box, about 40 minutes; never while the matcher runs)

- [ ] `python -m givingtuesday_datamart.placeholder_recovery load --policy v3`
      (it takes the verdicts stored on 2026-10-27 and today's).
- [ ] `python -m givingtuesday_datamart.placeholder_recovery view --policy v3`.
- [ ] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3`
      passes; rows and dollars by target logged.

### 8. The matcher's policy (the laptop and the box, beside steps 1 to 7)

- [ ] A: `PLACEHOLDER_REGEX` moves into the matcher; the grants view
      leaves out the rows it matches, and the gate imports it from there;
      `MATCHING_INPUT_SHAPE_VERSION` 5 to 6, so no chunk of an earlier
      shape is resumed; tests.
- [ ] B, if chosen: the geography-aware winner order in `grant_matching.py`
      with the revised state rule; tests; the policy hashed and frozen
      before the sample of step 0 is scored; the regression pool and the
      fresh sample against the criteria above; a proof document; Zein's
      review of the diff and the proof.
- [ ] The gate: the Johns Hopkins sentinel on 520595110, its floor set from
      the accepted run; the placeholder check a hard zero.

### 9. The matcher (the 256 GB box, quiet window, Zein's go; about seven hours)

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

### 10. The accepting commit (laptop)

- [ ] The baselines updated in one commit, every number that moved
      explained with the census, the gate rerun on `PASS`
      (`docs/matching-regression-runbook.md`, *Updating the baselines*).

### 11. Close out

- [ ] `status` once more and the census after, against the census before.
- [ ] `docs/own-extract.md`: production on `2026_10_25` and `ce054c032e56`;
      `docs/data-refresh-2026-10.md`: its open gate and step 8 pointed here.
- [ ] Spot checks on the site, once the caches turn over or after a
      redeploy: Fidelity Charitable (110303001), Johns Hopkins University
      (520595110), Duke University (560532129), the Michael J. Fox
      Foundation.
- [ ] The shared instance back to the medium class in a quiet window, then
      `bigdb.env` deleted on the box; the box back to its usual size.
- [ ] This log complete; this branch's PR merged.

## Afterwards

- A new IRS batch: `own_extract load 2026 --batch <new>`, `own_extract patch`,
  then steps 4 to 11.
- GT's next release: this sequence from step 2.
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
  three ranking proofs, and the fixes of its review. Read from the instance
  and the registry the same evening: the state table above; GT's
  `2026_10_25` upstream for every source we load; nothing newer from the
  IRS or the BMF; no backend running.
