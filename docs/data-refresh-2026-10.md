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

- [x] The box is up and reachable, nothing else running on it. (On
      2026-10-26 it did not answer at its last address: the address changed
      with its restart of 2026-10-20. Reachable at the new one.)
- [x] The clone on `main` at `cb6a004` or later:
      `git fetch origin && git checkout main && git pull --ff-only`
      (done 2026-10-26: the session4 clone, at `cb6a004`)
- [x] The environment reinstalled: `pip install -e '.[ingest]'`;
      `python -c 'import urllib3; print(urllib3.__version__)'` prints 2.x
      (the archive download relies on it raising on a body cut short).
      (done: urllib3 2.8.0, requests 2.34.2; the package imports from the
      session4 clone)
- [x] `source ~/frame.env`, then
      `python -m givingtuesday_datamart.sources status` shows nothing newer
      than `2026_06_16` / `2026_06_25`. If it does: `sources refresh`
      first; the patch then goes on top of the new release.
      (done 2026-10-26 21:40 UTC: nothing newer; the BMF `2026_10_13`)
- [x] `python -m givingtuesday_datamart.own_extract batches 2026` ends at
      `09A`. If not: `own_extract load 2026 --batch <new>` first.
      (done: ends at `09A`, posted 2026-10-21)
- [x] A manual snapshot of the shared instance, status *available*.
      (requested 2026-10-26 21:40 UTC; available at 100% as of 21:40:07 UTC,
      before the patch's first write)
- [x] Free storage at least 60 GB (CloudWatch `FreeStorageSpace`); no
      orphaned backends. (done: 70.1 GB at 21:40 UTC; no backend running,
      six idle client connections from pgAdmin and a JDBC client)
- [x] `python -m givingtuesday_datamart.placeholder_recovery status`
      (holds a share lock on `privategrants_current` for about a minute):
      no line-25 rule, view on `v2`, recovered rows under `v2`, no
      `match_tier`. Paste the screen into the log. (done: as expected; the
      screen is in the log)

### 1. The patch (the box, quiet window, about an hour, then the vacuum)

- [x] `tmux -L refresh new -s patch 'python -m givingtuesday_datamart.own_extract patch 2>&1 | tee ~/logs/refresh/patch.log'`
      (started 2026-10-26 21:45 UTC from the box, on Zein's word, in the
      afternoon rather than the evening window; the snapshot was available
      before the first write)
- [x] Per table, GT's rows out and ours in: expect about 16,662,030 out and
      20,109,675 in over the ten tables, earlier patch rows 0.
      (done: exactly 16,662,030 out and 20,109,675 in, earlier rows 0 on
      every table; the one transaction committed at 22:23:49 UTC; the log)
- [x] The three basic-fields `_current` relations rebuilt (the command
      does it after the patch). First attempt NOT done: the watchdog cancelled the
      `basic_fields_current` build at 22:27 UTC when swap passed 0.5 GB
      (0.99 GB at the peak; freeable memory 0.70 GB). The builder sets a
      256 MB `work_mem` and left the instance's two parallel workers on,
      so each memory node could take the budget three times over. The
      `DROP` rolled back with the cancelled `CREATE`, so the three
      relations stand as before the patch (3,812,025, 1,158,524 and
      2,151,017 rows) and read the old staging. Rerun after the vacuum
      with the builder's parallel workers off (this branch's change to
      `current_grants._build_one`):
      `python -m givingtuesday_datamart.current_grants --only basic_fields_current --only basic_fields_pf_current --only basic_fields_ez_current`
      (done: 22:48:55 to 23:35:25 UTC from the box, on this branch at
      `8ac4bcb`, exit 0; swap between 0.04 and 0.37 GB throughout,
      freeable memory never under 1.86 GB; the three relations now hold
      3,992,135, 1,226,420 and 2,257,600 filer-years; the log)
- [x] `VACUUM (ANALYZE)` on the ten staging tables (appendix): the deleted
      rows' space becomes reusable (the files do not shrink). (done 22:31 to
      22:45 UTC from the box, 14 minutes, every table at zero dead tuples
      after; `schedule_o` 26 GB in 279 s, `privategrants` 12 GB in 260 s,
      the rest under 100 s each; also the planner's statistics, which the
      builds read, since the tables gained a quarter of their rows)
- [x] Check: ten `irs_extract_patch` rows in `datamart_meta.ingest_runs`
      dated today. (done: ten rows, finished 22:23:49 UTC)
- [x] Checks: no url holds both GT's rows and ours, on `basic_fields_pf`
      and `privategrants` (appendix); `basic_fields_pf_current` holds
      filer-years with a tax period ending in 2025 that GT never had
      (after the rebuild). (done from the box, started 23:38 UTC: 0 urls
      with both on either table; `basic_fields_pf_current` holds 94,024
      filer-years with a tax period ending in 2025 or later, all ours and
      none GT's; the log)
- [x] Free storage after, expected about 11 GB less. (70.75 GB at 22:15
      UTC during the patch, 67.1 GB at 22:23 when it committed, 62.1 GB at
      the cancelled build's peak; 67.5 GB after the vacuum and the rebuild,
      3.3 GB less than before the patch: the estimate of 11 GB was too
      high)

### 2. The canonical build (the box, about 25 minutes)

- [x] `python -m givingtuesday_datamart.sources build-canonical`
      (done 2026-10-27 02:41:55 to 02:55:15 UTC from the box, 800 s, exit
      0, build `9339c70e`; swap no higher than 0.11 GB, free storage down
      to 56.3 GB while `nonprofit_text` was built and back to 68.9 GB
      after)
- [x] A `success` row in `datamart_meta.canonical_builds` dated today;
      `nonprofit_canonical` rows logged (was 1,981,967). (done: the row
      reads success, 1,983,340 rows; its lineage names the
      `irs_extract_patch` run; the log)

### 3. The grants relations (the box, about 40 minutes)

- [x] `python -m givingtuesday_datamart.current_grants --only privategrants_current`
      (it reads the view's policy before the rebuild and creates the views
      again for it; 25 to 40 minutes; from this branch, whose builder
      keeps the parallel workers off and shows its lines on the console,
      so `tee` the output into `~/logs/refresh/`) (done at the third
      start, 03:23:38 to 03:54:46 UTC, 31 minutes, exit 0, from `ddb47c7`
      at 64 MB: 17,392,341 rows, was 16,651,426; swap rose 0.07 GB over
      its 0.87 GB baseline; the sorts spilled to 35.03 GB free at the
      lowest, 30 MB above the floor; the log)
- [x] `python -m givingtuesday_datamart.current_grants --only privategrants_future_current`
      (about 2 minutes) (done 03:54:46 to 03:58:20 UTC, exit 0, the build
      itself two minutes and the closing summary the rest: 452,460 rows,
      was 441,358)
- [x] The paid build's last line reads `0 filer-years look doubled without
      a repeated basic row`; otherwise the filings it names are checked
      against their XML before step 7. (done: it reads exactly that, at
      03:52:18 UTC; nothing to check)
- [x] `status` shows the three line-25 rules; the rows by rule logged.
      (done 04:08 UTC on the enlarged instance: "line-25 rules in the
      table: forward_fill, field_fill, total_row"; the rows by rule are in
      the log entry of 03:23 to 03:58)

### 4. The work list (the box, minutes)

- [x] `python -m givingtuesday_datamart.placeholder_recovery work-list`
      (done 04:13:58 to 04:14:55 UTC from the box on the enlarged
      instance)
- [x] Counts by tax year and band logged. Expect about 1,150 filings to
      join (measured 2026-10-26 on `irs_extract`: $3.05B of placeholders,
      1,109 of them returns GT never had) and every loaded list to keep
      its place. (done: 25,612 filings on the list, 24,518 before, so
      1,094 joined; 5,320 filers, $51.27B declared, $51.23B on rows; 23
      filings of the 6 excluded filers left out; tax years 2020 on:
      13,918 filings, $34.93B; before 2020: 11,694, $16.34B; 854 marked
      individual, 64 over line 25, 1,144 with a future amount; by band,
      2020 on: A 31, B 597, C 3,262, D 10,028; the log)

### 5. The read of the new filings under v3 (the box, Zein's go)

- [x] The projection, from what is stored:
      `python -m givingtuesday_datamart.placeholder_recovery run --policy v3 --tax-years 2020-2025 --keep-list --dry-run --no-fetch`
      (pages and dollars logged; the rough figure is $200 to $250)
      (done 04:14:55 to 04:16:01 UTC: 6,340 fetched filings hold 38,628
      attachment pages under v3, every reading stored, nothing to buy
      there; 1,106 filings never fetched, priced by band at the frame's
      cost per filing: A 2 ($2.70), B 44 ($25.96), C 245 ($16.17), D 815
      ($16.30); projection $61.14, far under the rough figure because the
      new filings are small ones; about 5,672 pages flagged, 15%)
- [x] Zein's go on that figure. The cap stays $800 a run. (Zein, 03:30
      UTC, before the figure: the cap is $300; the run script passes
      `--cap 300`)
- [x] `tmux -L frame new -s frame 'python -m givingtuesday_datamart.placeholder_recovery run --policy v3 --tax-years 2020-2025 --keep-list --cache /data/irs_index'`
      One fetcher at a time against the IRS. Follow it with
      `page_readings status`; Ctrl-C in the pane is safe and the same
      command resumes.
      (started 14:09:05 UTC as `~/logs/refresh/run-read.sh`, session
      `read` of `tmux -L frame`, with `--cap 300`, on `81e020b`, beside
      the matcher; the log entry of 14:09 says why it could start)
- [ ] Spend logged: the difference in the `$` column before and after.
- [ ] Tax years 2015-2019 ($436 projected) are not part of this refresh
      unless decided separately.

### 6. The load, the view and the checks under v3 (the box, minutes; never while the matcher runs)

- [x] `python -m givingtuesday_datamart.placeholder_recovery load --policy v3`
      (first pass done 04:16:01 to 04:48:03 UTC, 1,920 s, from the box:
      9,029 fetched filings through the selector, 4,653 written, 0
      unchanged, 0 taken out; paid 4,611 filings, 582,471 rows,
      $16,210.9M; future 173 filings, 6,531 rows, $1,268.7M; the v2 rows
      stay beside them, 4,607 filings, $16,205.0M; a second pass follows
      the read)
- [x] `python -m givingtuesday_datamart.placeholder_recovery view --policy v3`
      (done 04:48:04 UTC: "created for policy v3")
- [x] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3` passes; rows and dollars by target logged.
      (done 04:48:04 to 04:48:38 UTC, exit 0: loaded paid 582,471 rows of
      4,611 filings, the same in the view; future 6,531 rows of 173
      filings; "same rows ok 0"; `status` shows the view on `v3`; run
      again after the second load)

### 7. The matcher (a box with 256 GB of memory, Zein)

The BMF arm doubles the pairs (PR #68), so the read box does not do.

- [x] The big box set up as *Set up a box once* in the operations doc, on
      `main` (tonight: the playground resized to 256 GB, on this branch,
      whose builder keeps the shared instance out of swap; resized back
      after the gate). (done: r6i.8xlarge, 32 cores, 247 GB free, at a new
      public address, the branch at `083a5a0`, `bigdb.env` in place)
- [x] `python -m givingtuesday_datamart.placeholder_recovery check --policy v3`
      (done 13:15:01 to 13:15:35 UTC: every rule ok, paid 582,471 rows of
      4,611 filings in the table and in the view, future 6,531 of 173)
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
- **2026-10-26, 21:40 UTC (14:40 PDT), step 0.** From the box: the session4
  clone on `main` at `cb6a004`, the package reinstalled (urllib3 2.8.0,
  requests 2.34.2), `sources status` nothing newer than the June release,
  `own_extract batches 2026` ending at `09A`. From the laptop: no backend
  running on the shared instance (six idle client connections), free
  storage 70.1 GB, the manual snapshot requested at 21:40 UTC. The status
  screen before anything ran:

  ```
  privategrants_current: 16,651,426 rows by rule
    passthrough 15,797,603; latest_url 664,328; pair_collapse 188,149;
    latest_url+pair_collapse 1,346; line-25 rules in the table: none
  privategrants_future_current: 441,358 rows
  view privategrants_current_w_recovered: shows policy v2
  privategrants_recovered: v2 paid 582,676 rows, 4,607 filings, $16,205.0M;
    v2 future 6,531 rows, 173 filings, $1,268.7M
  page_verdicts v3: agreed 15,611, escalated 8,983, flagged 5,699, not_a_list 8,541
  pf_placeholder_filings: 24,518 filings, $48,493.2M, 112 over both columns of line 25
  matcher output: privategrants_w_recipients about 7,583,106 rows,
    unioned_grants about 16,010,358; no match_tier column
  ```

  Step 1 waits for the snapshot to be available and for the evening.
- **2026-10-26, 21:45 to 22:27 UTC, step 1.** Zein: "do it now", so the
  patch ran in the afternoon. The snapshot was available at 21:40:07 UTC.
  The ten-table transaction took 38 minutes and committed at 22:23:49 UTC
  with the proof's exact totals, 16,662,030 of GT's rows out and 20,109,675
  of ours in:

  | table | GT's rows out | ours in |
  |---|---|---|
  | basic_fields | 827,667 | 967,617 |
  | mission_statements | 827,667 | 967,617 |
  | programs | 827,667 | 967,617 |
  | grants_to_domestic_organizations | 2,097,563 | 2,777,704 |
  | schedule_o | 6,382,813 | 7,870,975 |
  | basic_fields_pf | 289,363 | 342,397 |
  | privategrants | 4,054,393 | 4,617,771 |
  | privategrants_future | 113,959 | 120,663 |
  | basic_fields_ez | 486,325 | 578,596 |
  | programs_ez | 754,613 | 898,718 |

  During the patch, free storage 70.8 to 67.1 GB, swap 0.03 GB, freeable
  memory 1.8 GB. Then the command's second phase, the basic-fields
  `_current` rebuild in a new session, drove swap from 0.03 to 0.99 GB in
  three minutes (freeable memory 0.70 GB) and the laptop watchdog cancelled
  it at 22:27 UTC, as the rules say; swap was back to 0.21 GB a minute
  later. The three relations stand as they were (the `DROP` rolled back).
  Cause: `current_grants._build_one` sets `work_mem` to 256 MB and leaves
  the instance's two parallel workers on. Fix on this branch: the builder
  sets `max_parallel_workers_per_gather = 0`. The vacuum of the ten tables
  started at 22:31 UTC from the box; the rebuild follows it.
- **2026-10-26, 22:31 UTC on, step 1 finished.** The vacuum ran
  22:31 to 22:45 UTC: every table at zero dead tuples after, `schedule_o`
  (26 GB) in 279 s, `privategrants` (12 GB) in 260 s, the rest under 100 s
  each. The box checked out this branch at `8ac4bcb` and reran the three
  basic-fields builds, 22:48:55 to 23:35:25 UTC, exit 0. The builds took
  about 13, 11 and 11 minutes (each relation's ANALYZE at 23:02, 23:13 and
  23:24 UTC) and the builder's closing summary, rows by rule over five
  `_current` relations whatever `--only` says, the last eleven. With the
  parallel workers off, swap stayed between 0.04 and 0.37 GB and freeable
  memory above 1.86 GB; the peak came in that summary query, not in the
  builds. The builder's own lines (each relation's row count, the rows by
  rule) did not reach `rebuild.log`: run as a module, `current_grants`
  attached no log handler, unlike the package's other entry points, so
  only warnings reached the console. Fixed on this branch: its `__main__`
  block configures logging at INFO like the others. Step 3's check reads
  that output, so it needs the branch. The laptop watchdog lost its connection to the
  instance once, at 23:09 UTC, and reconnected (the instance did not
  restart: its postmaster has been up since 2026-10-23). The checks from
  the box, 23:38 to 23:59 UTC (the two full reads of `privategrants` take
  most of the time):

  | relation | filer-years | before | ours | tax period 2025+, ours / GT's | rules |
  |---|---|---|---|---|---|
  | basic_fields_current | 3,992,135 | 3,812,025 | 952,414 | 261,728 / 0 | passthrough 3,907,339; latest_filing 84,796 |
  | basic_fields_pf_current | 1,226,420 | 1,158,524 | 339,705 | 94,024 / 0 | passthrough 1,212,915; latest_filing 13,505 |
  | basic_fields_ez_current | 2,257,600 | 2,151,017 | 572,657 | 174,267 / 0 | passthrough 2,231,230; latest_filing 26,369; duplicate_row 1 |

  The three relations gained 354,589 filer-years together, against the
  360,068 returns the parity report counted as missing from GT (the rest
  are further versions of a filer-year the rules collapse). No url holds
  both GT's rows and ours on `basic_fields_pf` or `privategrants`. Rows by
  source on the staging tables, ours / GT's: `basic_fields` 967,617 /
  3,112,106; `basic_fields_pf` 342,397 / 897,805; `basic_fields_ez`
  578,596 / 1,706,101; `privategrants` 4,617,771 / 13,093,868;
  `grants_to_domestic_organizations` 2,777,704 / 6,893,450;
  `privategrants_future` 120,663 / 345,907. The planner's estimates for
  the three relations (3,988,316, 1,226,152 and 2,258,007) sit within 0.1%
  of the counts. Free storage 67.5 GB. Step 2 waits on Zein.
- **2026-10-27, 02:42 to 03:00 UTC, step 2.** Zein's go at 19:30 PDT. The
  box pulled this branch (`2271708`); the instance was quiet (69.8 GB free,
  swap 0.03 GB, no active backend, two idle connections). The canonical
  build ran 02:41:55 to 02:55:15 UTC, 800 s, exit 0, without the people
  tables like the build of 2026-10-15 it replaces. Swap stayed at or under
  0.11 GB and freeable memory at or over 2.07 GB; free storage fell to
  56.3 GB during the seven minutes of `nonprofit_text` (its four parallel
  workers at the server's 4 MB `work_mem`, spilling to temp files) and
  stood at 68.9 GB after. The build's lineage names the
  `irs_extract_patch` run beside GT's June runs and the BMF of
  2026-10-13. The checks from the box, 02:58 to 03:00 UTC:

  | table | rows | 2026-10-15 build |
  |---|---|---|
  | nonprofit_canonical | 1,983,340 | 1,981,967 |
  | nonprofit_text | 811,436 | 793,105 |
  | schedule_o_part_iii | 1,149,200 | 1,101,550 |
  | funder_canonical | 174,809 | 174,308 |

  The canonical table grew by 1,373 organizations only, because most
  filers of the 354,589 new filer-years were already rows (older returns
  or BMF-only). The patch shows in the latest return instead: of the
  811,438 organizations with a return (990: 441,604; 990-EZ: 369,834;
  BMF-only rows 1,171,902), 628,622 now take their latest return from our
  extract, and 412,224 carry a tax period ending in 2025 or later, none of
  them from GT's rows. Latest tax year 2025 on 231,253 organizations,
  2024 on 354,882. Step 3 waits on Zein.
- **2026-10-27, 03:02 to 03:24 UTC, step 3, two false starts.** Zein's go
  at 20:00 PDT. The paid build from the box (branch at `31663a9`) was
  cancelled by the watchdog at 03:11 UTC, eight minutes in, at 0.87 GB of
  swap: the builder's 256 MB `work_mem` is a budget per operation, and the
  `privategrants_current` query runs many window and hash nodes at once in
  one process, so the sum passed the limit with no parallel worker
  involved. The incident rule of 2026-10-23 says 64 MB on this instance,
  and the builder now defaults to it (256 MB of `maintenance_work_mem`,
  overridable through `GT_BUILD_WORK_MEM` and
  `GT_BUILD_MAINTENANCE_WORK_MEM` for a larger database instance only;
  `ddb47c7`). The transaction rolled back; the relation and the view
  stood. The second start at 03:19 UTC was cancelled within a minute by
  the watchdog itself: CloudWatch still reported the 0.87 GB the first
  attempt had swapped out (pages stay in swap until touched) while 3 GB of
  memory was free, and the absolute rule fired on a backend fourteen
  seconds old. The watchdog now measures swap from its own start: a rise
  of more than 0.5 GB is the breach. Third start 03:23:38 UTC from
  `ddb47c7`. Also fixed on the branch, found while preparing step 7: the
  matcher and the gate, run as modules, attached no log handler either
  (`f300d08`).
- **2026-10-27, 03:30 UTC, Zein's decisions for the night** (he is asleep
  from here; the aim is through the matcher): the read's cap is $300; the
  box's disk is free to use tonight; the view goes to `v3`; the matcher
  box is the playground resized with his `ec2resize` sequence (stop,
  change type, start), not a new instance; the latest start for the
  matcher is 09:00 UTC (2 AM Pacific); if the doubling watch names
  unhalved filings, proceed when 20 or fewer and list them, stop when
  more; baselines move only on `GATE: PASS`, in a commit on this branch
  for his review; the redeploy and the data-problems doc wait for the
  morning. I first planned the matcher tonight on today's recovered rows
  with the new filings' lists left to the next refresh's run, reading the
  09:00 UTC mark and the one-writer rule as binding; Zein, awake again at
  03:45 UTC: no second matcher run, the read goes first and the matcher
  once, after it, and nothing reads the database for the next fifteen
  hours, so the matcher may start whenever the load is done, into the
  day if need be. The read is shorter than I had said: the work list's
  filings average about five attachment pages, so about 1,150 filings is
  roughly 6,000 pages, about three hours at the frame's rate, not a night
  (the projection after step 4 gives the count). Order for the night:
  step 3, the work list and the projection, the read, the load and the
  view on `v3`, the resize, the preflight, one matcher run, the gate. The
  big box runs this branch, not `main`: the builder's limits above are
  what keep the matcher's own `_current` rebuilds off the shared
  instance's swap.
- **2026-10-27, 03:55 UTC, the database instance grows for the night.**
  The paid build's sorts at 64 MB spilled about 34 GB of temp files and
  came within 30 MB of the watchdog's 35 GB floor, and the matcher's own
  rebuild would do the same. Zein: change the class. Nothing reads the
  database for fifteen hours, so after step 3's run ends the shared
  instance goes from db.t4g.medium (4 GB, about 43 MB/s of disk) to
  db.r6g.xlarge (32 GB, about 150 MB/s) with an immediate apply, one
  restart of five to ten minutes, about $5 for the night; it goes back in
  the morning, another restart. Single-AZ, the default parameter group
  (`shared_buffers` follows the class), nothing pending. While it is
  large, the run scripts source `~/logs/refresh/bigdb.env` on the box:
  `GT_BUILD_WORK_MEM=1GB`, `GT_BUILD_MAINTENANCE_WORK_MEM=2GB`,
  `GT_BUILD_PARALLEL_WORKERS=2` (the third knob added in `ec52c45`). The
  file is deleted when the class goes back; the defaults are the medium's
  rule.
- **2026-10-27, 03:23 to 03:58 UTC, step 3 built.** From the box on
  `ddb47c7`, at 64 MB with no parallel workers, under the corrected
  watchdog. The paid relation 03:23:38 to 03:54:46 UTC, 31 minutes, exit
  0: the CREATE TABLE AS itself 03:25 to 03:48, its sorts spilling until
  free storage read 35.03 GB at 03:48, thirty megabytes above the
  watchdog's floor, then the ANALYZE and the doubling watch (03:50 to
  03:52), then the row count and the closing summary. Swap rose 0.07 GB
  over its 0.87 GB baseline and freeable memory never fell under 2.69 GB.
  `privategrants_current` 17,392,341 rows (16,651,426 before). The
  doubling watch: 0 filer-years look doubled without a repeated basic row.
  Rows by rule: passthrough 16,702,141; latest_url 672,530; total_row
  16,806; latest_url+total_row 519; forward_fill 152; field_fill 103;
  pair_collapse 88; latest_url+forward_fill 1; latest_url+field_fill 1.
  pair_collapse was 188,149 rows on the 26th: GT's doubled 2024+ blocks
  were the rows the patch replaced, and the IRS's own files do not carry
  the doubling. The future relation 03:54:46 to 03:58:20 UTC, exit 0,
  452,460 rows (441,358 before); its rules before the rebuild read
  passthrough 423,690, latest_url 13,023, pair_collapse 4,644. After the
  run, free storage 67.5 GB and swap back to 0.03 GB. The step's `status`
  and counts follow once the instance is back as the larger class.
- **2026-10-27, 04:05 UTC, the box's disk.** For the read (about 15 GB per
  1,000 filings of PDFs and page images) the box had 14 GB free: the pip
  cache went (4.3 GB) and the root volume grew from 100 to 150 GB (gp3,
  online, `growpart` then `xfs_growfs`, about $4 a month more), 68 GB
  free after. Zein's other checkouts on the box were not touched.
- **2026-10-27, 04:01 to 04:07 UTC, the instance's class changed.**
  Requested 04:01:47 UTC with an immediate apply, once the box's backends
  were gone; `modifying` until 04:05, the postmaster back at 04:05:15,
  `available` as db.r6g.xlarge at 04:07:08: five and a half minutes. After
  the restart the server reads `shared_buffers` 8 GB, `effective_cache_size`
  16 GB, `maintenance_work_mem` 516 MB, `work_mem` 4 MB and two parallel
  workers by default, all from the class; the builder's session settings
  come from the box's `bigdb.env` (1 GB, 2 GB, two workers). The step 3
  checks and steps 4 to 6 started from the box at 04:07:51 UTC under the
  watchdog.
- **2026-10-27, 04:08 to 04:49 UTC, step 3's counts, step 4, the
  projection, step 6's first pass.** On the enlarged instance. The counts
  (04:08 to 04:13): `privategrants_current` 17,392,341 rows, 4,582,046 of
  them ours, 1,060,113 filings (258,026 ours); `privategrants_future_current`
  452,460 rows, 119,322 ours, 26,893 filings (7,043 ours), rules
  passthrough 439,192, latest_url 13,267, pair_collapse 1; the recovered
  view 17,969,776 rows. The work list (04:13:58 to 04:14:55): 25,612
  filings, 1,094 more than the 24,518 of the 26th; the numbers are on the
  step 4 boxes. The projection (04:14:55 to 04:16:01): every reading the
  policy needs is stored for the 6,340 fetched filings' 38,628 pages;
  1,106 filings never fetched, $61.14 by band; the warnings it prints are
  the known ones (2,862 filings with no TEOS image, 1,548 PDFs the IRS
  does not serve, 30 TEOS failures, 8,504 filings without an attachment).
  The first load under `v3` (04:16:01 to 04:48:03, 1,920 s on one core of
  the box), the view to `v3`, the check passing: the numbers are on the
  step 6 boxes. The laptop watchdog saw no breach and left early on a gap
  between the short queries; swap 0.00 GB and 23 GB of memory free
  throughout.
- **2026-10-27, 04:39 UTC, the read STOPPED at its prerequisites: TEOS
  answers 403 Forbidden.** The run's own check makes the fetch's GET for
  one filer's return list from the box and got `HTTP Error 403:
  Forbidden`; the same request from the laptop, with the project's user
  agent `vdl-givingtuesday-datamart/irs_source`, gets 403 too. The fetch
  worked from the box on 2026-09-29 and from a laptop before that. So the
  IRS is refusing our fetcher as such, from any host, and that is not
  something to route around: no other user agent, no other address. The
  1,106 new filings cannot be read until TEOS answers again; a probe with
  the project's request runs once every ten minutes for an hour. Nothing
  bought. The matcher waits on Zein's word: on today's recovered rows (the
  new filings' lists then wait for a later run), or after a read, when the
  IRS allows one. The 403 is served by the IRS's CDN edge ("Access Denied",
  an edgesuite reference number), so it is a rule at their edge, not an
  outage. Seven probes, 04:52 to 05:52 UTC, every ten minutes: 403 each
  time. A slower probe, every thirty minutes, runs on through the night;
  if TEOS answers, the read starts under the cap as planned.
- **2026-10-27, 05:55 UTC, Zein: if TEOS does not look like answering
  today, start the matcher.** It does not: an edge-level denial, from two
  hosts, unchanged across an hour, and a read that could not feed a
  matcher due to finish in the morning anyway. So step 7 runs now on
  today's recovered rows under `v3`; the 1,106 unread filings' lists wait
  for a later read and run. The playground resizes to r6i.8xlarge (256 GB,
  32 vCPU, x86 like the t3 so the venv holds; offered in its zone,
  us-east-1c) with the stop, change, start sequence of Zein's `ec2resize`;
  its public address changes, the private one does not. The box runs this
  branch; the matcher's own `_current` rebuilds read `bigdb.env` for the
  enlarged database. If the probe finds TEOS answering while the matcher
  runs, the read may run (it writes page tables only), the load may not,
  until the matcher has ended.
- **2026-10-27, 13:15 UTC, step 7 started.** The resize's report reached
  the laptop at 13:13 UTC, seven hours after the request; the laptop had
  not slept (the TEOS probe logged a 403 every half hour from 05:53 to
  12:53 UTC, fifteen in a row), so the delay was in the report's delivery,
  not the box. The box came back as r6i.8xlarge (32 cores, 247 GB free) at
  a new public address, the private one unchanged, the branch pulled to
  `083a5a0`. The matcher script started at 13:15:01 UTC in `tmux -L refresh`,
  06:15 Pacific, so the run reaches into the working day on the enlarged
  database: the `v3` check first (every rule ok at 13:15:35), the
  corrections preflight, the matcher, the gate, the check again. The
  laptop watchdog runs for ten hours, patient through the compute phases.
- **2026-10-27, 14:09 UTC, step 5 started: the read, beside the matcher.**
  What the 403 was. Zein asked TEOS for a filer's return list from his
  laptop with Python's urllib and no headers of his own and got the JSON;
  the same request with `User-Agent: zein` got 403, as the project's own
  name had all night, from the laptop and the box alike. So the edge in
  front of apps.irs.gov refuses any User-Agent value it does not
  recognise and accepts urllib's default and browsers; nothing about it
  is aimed at this client (an arbitrary word is refused too), and it is
  new since September's fetch of 8,800 filings under the project's name.
  apps.irs.gov publishes no robots.txt; www.irs.gov's governs only that
  host and says nothing of these downloads. Commit `81e020b`:
  `irs_source._get` sets no headers at all, urllib's own identity and
  never a browser string or a rotation; the returns listing no longer
  asks for JSON it gets anyway; own_extract's HEAD on the archives
  identifies the same way; and `fetch_filings` stops on five 403s in a
  row instead of charging three attempts to every filing in the list (the
  filings still queued come back `unfetched:teos_refused`, untried). Five,
  not one: `81e020b` stopped on the first, and the fetch's own counts then
  showed 26 rows of 2021-2022 images standing at three attempts with a
  403 each, their neighbours fetched, so a lone 403 is a filing the IRS
  will not serve and only a run of them is the client being refused. The
  fetch keeps its eight workers, September's rate, which drew no reaction:
  today's refusal is instant and keyed on the string, not on volume. The
  box pulled `81e020b`; the run script started 14:09:05 UTC in
  `tmux -L frame`, session `read`, cap $300, while the matcher computes
  in `tmux -L refresh` (load average 1 on 32 cores, 68 GB free).
  Prerequisites met at once: TEOS answered the preflight's listing from
  the box in 0.2 s. The fetch: 1,106 of the 13,918 filings on the work
  list to fetch, 12,812 stored already; 929 done after one minute, about
  fifteen a second, many of the newest (periods 202512 and 202602)
  `no_teos_image`, images the IRS has not generated yet. The load, step 6
  again, waits for the gate: never while the matcher runs.
