# Giving Tuesday Datamart

VDL's internal data backbone for IRS 990 / 990-PF filings. Pulls the Giving
Tuesday Datamart CSVs from S3, ingests them into PostgreSQL with full lineage
tracking, and feeds downstream analyses and the React explorer in `frontend/`.

## Overview

- **Source**: `gt990datalake-analytics-and-datamarts` S3 bucket (public-read;
  no credentials needed).
- **Target**: `gt_datamart` database on the shared VDL RDS host. Dedicated
  database (not shared with the rest of the VDL Postgres) so a bad refresh
  can't damage unrelated data, and so we can drop/recreate cheaply during
  early-phase work.
- **Schemas in `gt_datamart`**:
  - `public.*` — all 9 staging tables (`basic_fields`, `mission_statements`,
    `programs`, `schedule_o`, `grants_to_domestic_organizations`,
    `privategrants`, `basic_fields_pf`, `officers`, `officers_pf`).
  - `datamart_meta.ingest_runs` — one row per ingest, records what was
    pulled, when, and how it went.
- **Every staging row** gets 4 lineage columns stamped at COPY time:
  `_source_version`, `_source_url`, `_ingested_at`, `_ingest_run_id`.
- **Every staging column is `TEXT`**. Zero-padded EINs (`012345678`), ZIPs
  (`01234`), and phones round-trip intact. Consumers cast at query time
  (`SUM(totrevcuryea::bigint)`).

## Setup

### 1. Python environment

Use the `givingtuesday` pyenv (has the required deps — `polars` and friends):

```bash
GT_PY=~/.pyenv/versions/3.12.11/envs/givingtuesday/bin/python
```

### 2. VDL config

Standard VDL `config.ini` with a `[postgres]` section pointing at the RDS
host. See `vdl-tools` setup if you don't already have one.

### 3. Create the `gt_datamart` database (one-time)

The ingestion path deliberately does **not** create databases. Before the
first refresh:

```bash
psql -h <rds-host> -U <vdl-user> -d postgres -c "CREATE DATABASE gt_datamart;"
```

(Or `createdb gt_datamart` if your libpq env is set.)

## Commands

All via the sources CLI:

```bash
# What's currently in S3 vs. what we know about?
$GT_PY -m givingtuesday_datamart.sources status
```

Prints a table: logical name, target staging table, latest S3 version date,
filename, size. Fast (S3 list only, no DB, no downloads).

```bash
# What tables have been loaded into gt_datamart, at what version, how long ago?
$GT_PY -m givingtuesday_datamart.sources loaded
```

Queries `datamart_meta.ingest_runs` and shows the latest run per source:
status (`success` / `failed` / `skipped` / `never`), version, row count,
when it finished, and age. Failed runs are re-surfaced as warnings below
the table. Hits the DB only, no S3.

```bash
# Ingest everything (~32 GB of CSV total; run overnight)
$GT_PY -m givingtuesday_datamart.sources refresh

# Single source
$GT_PY -m givingtuesday_datamart.sources refresh --source irs_990_missions

# Multiple sources in one command
$GT_PY -m givingtuesday_datamart.sources refresh \
    --source irs_990_basic_fields \
    --source irs_990_missions

# Force re-ingest (bypass idempotency; drops + recreates the staging table)
$GT_PY -m givingtuesday_datamart.sources refresh --source irs_990_missions --force
```

### Idempotency

`refresh` is idempotent on `(logical_name, source_version)`. If a successful
run already exists in `datamart_meta.ingest_runs` for the same source +
version, the refresh is skipped with status `skipped`. Use `--force` to
override (drops the staging table and reingests).

A `failed` run does **not** block a retry — rerun the same command and it
will try again.

### Recommended first-run order

Smallest to largest, so errors surface fast:

```bash
$GT_PY -m givingtuesday_datamart.sources refresh --source irs_990pf_basic_fields   # ~730 MB
$GT_PY -m givingtuesday_datamart.sources refresh --source irs_990pf_officers       # ~1.0 GB
$GT_PY -m givingtuesday_datamart.sources refresh --source irs_990_missions         # ~1.0 GB
$GT_PY -m givingtuesday_datamart.sources refresh --source irs_schedule_i_grants    # ~1.9 GB
$GT_PY -m givingtuesday_datamart.sources refresh --source irs_990_basic_fields     # ~2.1 GB
$GT_PY -m givingtuesday_datamart.sources refresh --source irs_990_programs         # ~2.2 GB
$GT_PY -m givingtuesday_datamart.sources refresh --source irs_990pf_grants         # ~4.4 GB
$GT_PY -m givingtuesday_datamart.sources refresh --source irs_990_officers         # ~8.6 GB
$GT_PY -m givingtuesday_datamart.sources refresh --source irs_schedule_o           # ~10  GB
```

## Verifying a refresh

Connect to `gt_datamart` and check the run table and the lineage columns:

```sql
-- All runs, newest first
SELECT run_id, logical_name, source_version, status, row_count,
       finished_at - started_at AS duration, error
FROM datamart_meta.ingest_runs
ORDER BY started_at DESC;

-- Most recent run per source
SELECT DISTINCT ON (logical_name)
    logical_name, source_version, status, row_count, finished_at
FROM datamart_meta.ingest_runs
ORDER BY logical_name, finished_at DESC;

-- Lineage stamped on every row (exactly one version per staging table)
SELECT DISTINCT _source_version, _ingest_run_id
FROM public.mission_statements;

-- Row count parity with ingest_runs
SELECT COUNT(*) FROM public.mission_statements;
```

## Adding a new source

Edit [`givingtuesday_datamart/sources/registry.py`](givingtuesday_datamart/sources/registry.py)
and append a new `_spec(...)` entry:

```python
_spec(
    logical_name="irs_new_thing",
    staging_table_name="public.new_thing",
    form_type="990",  # or "990-PF"
    description="Short human-readable description of what this table is.",
    filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_NewThingPattern\.csv$",
),
```

The **first capture group** of `filename_regex` must capture `YYYY_MM_DD` so
the resolver can pick the newest matching file.

Verify with `status` before running `refresh`:

```bash
$GT_PY -m givingtuesday_datamart.sources status
```

If the new source shows `NOT FOUND`, the regex doesn't match anything in the
bucket — fix it before running `refresh`.

## Design notes

- **All-TEXT staging.** Every column created by `_create_table_from_columns`
  is `TEXT`. Zero-padded EINs, ZIPs, and phones round-trip intact. Consumers
  cast at query time (`::bigint`, `::double precision`, etc.). Typed
  canonical views are a Phase 2 concern.
- **Streaming ingestion.** No disk cache, no pandas in the hot path. CSV is
  parsed via `csv.reader` over an `io.TextIOWrapper(newline="")` on top of a
  `BufferedReader` wrapping `response.iter_content()`. Quoted multi-line
  fields (mission statements, Schedule O narratives) are preserved — a
  naive `iter_lines` split would mangle them.
- **COPY in batches of 50,000.** Each batch is stamped with the 4 lineage
  columns in the same COPY command — no second pass.
- **Idempotency is enforced in the app layer**, not via DB constraints.
  Before creating a run row, we check `datamart_meta.ingest_runs` for an
  existing successful `(logical_name, source_version)`. Simple and easy to
  reason about; future work could add a unique constraint if needed.
- **Database isolation.** `gt_datamart` is a separate database on the same
  RDS host as the main VDL DB. Threaded via `get_session(config=datamart_config())`
  rather than a parallel connection module.

## Troubleshooting

**"Skipping" when you want to re-ingest.** That's idempotency working. Pass
`--force` to drop the staging table and re-run.

**"database 'gt_datamart' does not exist".** Do the one-time `createdb
gt_datamart` step above.

**"No module named 'polars'" (or pandas, psycopg2, etc.).** You're not in the
`givingtuesday` pyenv — `vdl-tools-312` is missing polars. Use
`~/.pyenv/versions/3.12.11/envs/givingtuesday/bin/python`.

**"NOT FOUND" for a source in `status`.** Giving Tuesday occasionally
renames files (e.g., `990PFPart7p1Officers.csv` vs `990PFPart7p1-Officers.csv`).
The `filename_regex` in the registry needs an update; make the pattern
tolerant (`-?`, character classes) rather than exact.

**Staging columns showing up as `bigint` / `double precision`.** You're
looking at a table that was ingested by the deleted standard (pandas
`to_sql`) path, before we switched to streaming-only. `--force` re-ingest
and it'll come out as all-`TEXT`.

## Repo layout

```
givingtuesday_datamart/
  sources/
    spec.py          # SourceSpec / ColumnSpec dataclasses
    registry.py      # One SourceSpec per ingested table
    resolver.py      # Picks the latest matching file from S3 (boto3 unsigned)
    __main__.py      # CLI: status, refresh
  ingestion.py       # ingest_source() + ingest_latest(), ingest_runs tracking
  write_data_to_sql.py  # Streaming CSV -> COPY (all-TEXT columns, lineage stamping)
  sql_queries/       # Downstream analytical SQL (build canonical views, grants unions, etc.)
  matching_records_experiment.py  # recordlinkage pipeline (Phase 3 work)
scripts/
  create_tables.py   # Thin wrapper around `sources refresh`
frontend/            # Next.js app (reads from the old VDL DB today; migration to gt_datamart TBD)
docs/
  backbone-plan.md   # Phased plan for the full data backbone (ingestion, query surfaces, matching)
```

## The full plan

[`docs/backbone-plan.md`](docs/backbone-plan.md) has the phased roadmap for
this work — all three phases (ingestion + lineage; query surfaces and
canonical entity views; matching, classification, and person dedup), the
architecture decisions behind them, critical files, and verification
criteria per phase. Read it before starting anything non-trivial.
