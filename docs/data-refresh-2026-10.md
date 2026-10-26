# Data refresh, October 2026

GivingTuesday has released nothing since June 2026. PR #79 (merged
2026-10-26) reads the IRS's own 2024-2026 batches into `irs_extract` and
patches them into production's staging tables, ours first. This is the
runbook for that refresh: the state production was in when it was decided,
the rules the steps run under, a checklist per step with the commands and
what to expect, and a log filled in as each step runs. It follows
`docs/own-extract.md` (*In what order, on production*) and
`docs/placeholder_recovery_operations.md` (*The next GivingTuesday drop*),
on the state below. Each step is checked off with its numbers; nothing in
it is run without checking in first (decided 2026-10-26).

## Where things stood on 2026-10-26

| what | state that evening |
|---|---|
| GivingTuesday's releases | the June release (`2026_06_16`, Schedule I and O `2026_06_25`) is the latest in the bucket and the one loaded (ingested 2026-07-29 to 09-29); the BMF of `2026_10_13` is loaded |
| the IRS's batches | 38 archives, `2024_TEOS_XML_01A` to `2026_TEOS_XML_09A` (posted 2026-10-21), all loaded under concordance `3d9b48d6e5e7`; the next archive is due about 2026-11-18 |
| production staging | GT's June release, unpatched: no `irs:` rows, no `irs_extract_patch` run |
| basic-fields `_current`, canonical | rebuilt 2026-10-15, with the BMF; `nonprofit_canonical` 1,981,967 rows |
| grants `_current`, `privategrants_w_recipients`, `unioned_grants` | the matcher run of 2026-08-04 (7,578,795 and 16,044,328 rows); no `match_tier`. The three line-25 rules, the recovered grants, the name cleaner, the name-only tier, the BMF arm and the government guard are in the code and not in the tables |
| placeholder recovery | the view on `v2`; 589,396 recovered rows; work list of 24,518 filings; tax years 2020-2025 read (their `v3` verdicts are stored); 2015-2019 not read ($436 projected) |
| the shared instance | 300 GB, 70 GB free; the database 104 GB |
| the frontend | pages and API routes revalidate hourly; the CDN serves stale up to a day |
| parity | not rerun, by decision: the report of 2026-10-23 measured data identical in every column production reads (the final reload changed Schedule I's filer name line 2 and the unread `_0` columns only), and nothing upstream or in production has changed since. A rerun would add the Schedule I and 3b same-total counts under the counting fix of 2026-10-24, which moved the 990-PF count by three |

So `sources refresh` has nothing to do, and the patch goes onto the staging
tables as they are.

## Rules

- One writer on the shared instance at a time, the heavy steps in the
  evening (Pacific), under the watchdog of the own-extract doc: stop under
  35 GB of free disk, over 0.5 GB of swap, under 0.3 GB of free memory.
- Terminate orphaned backends before a heavy step: a killed client's query
  keeps running on the server (SQL in the appendix).
- The shared-instance steps run from the box, not a laptop: a laptop's
  connection stalled and killed a COPY on 2026-10-23. Each in its own
  `tmux -L refresh` session, logging to `~/logs/refresh/`.
- Never load recovered rows while the matcher runs: the run's checkpoint
  reads a digest of the rows and starts over.
- The patch's undo is the snapshot taken before it. `sources refresh
  --force` works too but downloads 33 GB of GT's files again.
- The paid read (step 5) and the matcher (step 7) are started by Zein: one
  buys model calls, the other needs a box of its own.

## Checklist

### 0. Preflight (the box, evening, about 20 minutes)

- [ ] The box is up and reachable, nothing else running on it. (On the
      evening of 2026-10-26 it did not answer at its last address.)
- [ ] The clone on `main` at `cb6a004` or later:
      `git fetch origin && git checkout main && git pull --ff-only`
- [ ] The environment reinstalled: `pip install -e '.[ingest]'`;
      `python -c 'import urllib3; print(urllib3.__version__)'` prints 2.x
      (the archive download relies on it raising on a body cut short).
- [ ] `source ~/frame.env`, then
      `python -m givingtuesday_datamart.sources status` shows nothing newer
      than `2026_06_16` / `2026_06_25`. If it does: `sources refresh`
      first; the patch then goes on top of the new release.
- [ ] `python -m givingtuesday_datamart.own_extract batches 2026` ends at
      `09A`. If not: `own_extract load 2026 --batch <new>` first.
- [ ] A manual snapshot of the shared instance, status *available*.
- [ ] Free storage at least 60 GB (CloudWatch `FreeStorageSpace`); no
      orphaned backends.
- [ ] `python -m givingtuesday_datamart.placeholder_recovery status`
      (holds a share lock on `privategrants_current` for about a minute):
      no line-25 rule, view on `v2`, recovered rows under `v2`, no
      `match_tier`. Paste the screen into the log.

### 1. The patch (the box, quiet window, about an hour, then the vacuum)

- [ ] `tmux -L refresh new -s patch 'python -m givingtuesday_datamart.own_extract patch 2>&1 | tee ~/logs/refresh/patch.log'`
- [ ] Per table, GT's rows out and ours in: expect about 16,662,030 out and
      20,109,675 in over the ten tables, earlier patch rows 0; the three
      basic-fields `_current` relations rebuilt (the command prints them).
- [ ] `VACUUM (ANALYZE)` on the ten staging tables (appendix): the deleted
      rows' space becomes reusable (the files do not shrink).
- [ ] Checks: ten `irs_extract_patch` rows in `datamart_meta.ingest_runs`
      dated today; no url holds both GT's rows and ours, on
      `basic_fields_pf` and `privategrants` (appendix);
      `basic_fields_pf_current` holds filer-years with a tax period ending
      in 2025 that GT never had.
- [ ] Free storage after, expected about 11 GB less.

### 2. The canonical build (the box, about 25 minutes)

- [ ] `python -m givingtuesday_datamart.sources build-canonical`
- [ ] A `success` row in `datamart_meta.canonical_builds` dated today;
      `nonprofit_canonical` rows logged (was 1,981,967).

### 3. The grants relations (the box, about 40 minutes)

- [ ] `python -m givingtuesday_datamart.current_grants --only privategrants_current`
      (it reads the view's policy before the rebuild and creates the views
      again for it; 25 to 40 minutes)
- [ ] `python -m givingtuesday_datamart.current_grants --only privategrants_future_current`
      (about 2 minutes)
- [ ] The paid build's last line reads `0 filer-years look doubled without
      a repeated basic row`; otherwise the filings it names are checked
      against their XML before step 7.
- [ ] `status` shows the three line-25 rules; the rows by rule logged.

### 4. The work list (the box, minutes)

- [ ] `python -m givingtuesday_datamart.placeholder_recovery work-list`
- [ ] Counts by tax year and band logged. Expect about 1,150 filings to
      join (measured 2026-10-26 on `irs_extract`: $3.05B of placeholders,
      1,109 of them returns GT never had) and every loaded list to keep
      its place.

### 5. The read of the new filings under v3 (the box, Zein's go)

- [ ] The projection, from what is stored:
      `python -m givingtuesday_datamart.placeholder_recovery run --policy v3 --tax-years 2020-2025 --keep-list --dry-run --no-fetch`
      (pages and dollars logged; the rough figure is $200 to $250)
- [ ] Zein's go on that figure. The cap stays $800 a run.
- [ ] `tmux -L frame new -s frame 'python -m givingtuesday_datamart.placeholder_recovery run --policy v3 --tax-years 2020-2025 --keep-list --cache /data/irs_index'`
      One fetcher at a time against the IRS. Follow it with
      `page_readings status`; Ctrl-C in the pane is safe and the same
      command resumes.
- [ ] Spend logged: the difference in the `$` column before and after.
- [ ] Tax years 2015-2019 ($436 projected) are not part of this refresh
      unless decided separately.

### 6. The load, the view and the checks under v3 (the box, minutes; never while the matcher runs)

- [ ] `python -m givingtuesday_datamart.placeholder_recovery load --policy v3`
- [ ] `python -m givingtuesday_datamart.placeholder_recovery view --policy v3`
- [ ] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3` passes; rows and dollars by target logged.

### 7. The matcher (a box with 256 GB of memory, Zein)

The BMF arm doubles the pairs (PR #68), so the read box does not do.

- [ ] The big box set up as *Set up a box once* in the operations doc, on `main`.
- [ ] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3`
- [ ] `python -m givingtuesday_datamart.corrections_preflight` (hours on a laptop, minutes on a box)
- [ ] `python -m givingtuesday_datamart.grant_matching` (about eight hours;
      it rebuilds the `_current` relations itself and reads the policy the
      view shows; the checkpoint prefix is new, so every chunk is computed)
- [ ] `python -m givingtuesday_datamart.matching_regression_checks`:
      `GATE: PASS`, or the diagnosis of what moved.
- [ ] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3`
- [ ] The accepting commit: the baselines updated, every number that moved
      explained, the gate rerun on `PASS`
      (`docs/matching-regression-runbook.md`, *Updating the baselines*).

### 8. Close out (laptop)

- [ ] `status` once more: the three rules, the view on `v3`, the loaded
      rows under `v3`, `match_tier` in the matcher's output.
- [ ] The data-problems doc: rows that read "merged" read "live".
- [ ] `docs/own-extract.md`: the patch's production numbers; a line in the
      parity section that the Schedule I and 3b same-total counts are the
      ones before the counting fix.
- [ ] The frontend redeployed, so the hour's cache and the day's stale
      pages go.
- [ ] Spot checks on the site: Fidelity Charitable (EIN 110303001), the
      Michael J. Fox Foundation's corrected grants, and a 2025 990-PF GT
      never had.
- [ ] This log complete; this document's PR merged.

## Afterwards, monthly

When the IRS posts an archive: `own_extract load 2026 --batch <new>`, then
steps 1 to 8 (step 5 only when new placeholder filings join; step 7 as the
big box allows). When GT's next release lands: `sources refresh` replaces
the staging tables and drops the patch, and step 1 puts it back.

## Appendix: SQL

Orphaned backends (then `SELECT pg_terminate_backend(<pid>)` for each):

```sql
SELECT pid, usename, state, query_start, left(query, 100)
FROM pg_stat_activity
WHERE state <> 'idle' AND pid <> pg_backend_pid();
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

No url with both sources (the proof's check; a full read of the table):

```sql
SELECT count(*) FROM (
  SELECT url FROM public.basic_fields_pf GROUP BY url
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

- **2026-10-26.** Plan decided after PR #79 merged. The parity rerun is left
  out (see the table above). `sources status` and `own_extract batches
  2026` run from the laptop: nothing new on either side. The box did not
  answer at its last address that evening; step 0 starts with it.
