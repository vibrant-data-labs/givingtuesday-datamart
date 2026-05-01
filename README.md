# Giving Tuesday Datamart

A data backbone for IRS Form 990 / 990-PF filings. It pulls Giving Tuesday's
public Datamart CSVs from S3, lands them in PostgreSQL with full lineage,
materializes canonical entity tables and a Postgres full-text-search surface
on top, runs grant-recipient matching, and exposes a read-only Python
client. A Next.js explorer (`peerlo`) sits in [`frontend/`](frontend/).

```
S3 (Giving Tuesday public bucket)
        │
        ▼
   Staging tables          ── public.basic_fields, public.mission_statements, ...
   (all-TEXT, lineage-stamped)
        │
        ▼
   Canonical tables        ── public.nonprofit_canonical, public.nonprofit_text (FTS), ...
   (typed, one row per real entity)
        │
        ▼
   Matched grants          ── public.unioned_grants
   (private-grants ↔ recipient EIN, plus Schedule I)
        │
        ▼
   GtDatamartClient        ── read-only Python client, used by the frontend
                              and by any downstream consumer
```

## What's in `gt_datamart`

A dedicated Postgres database, broken into four layers:

- **Staging** (`public.*`) — direct ingest of the Giving Tuesday CSVs. One
  table per source (e.g. `basic_fields`, `mission_statements`, `programs`,
  `schedule_o`, `grants_to_domestic_organizations`, `privategrants`,
  `basic_fields_pf`). Every column is `TEXT` so zero-padded EINs
  (`012345678`), ZIPs (`01234`), and phones round-trip intact; cast at
  query time (`SUM(totrevcuryea::bigint)`). Every row carries four lineage
  columns: `_source_version`, `_source_url`, `_ingested_at`,
  `_ingest_run_id`.
- **Canonical** (`public.*`) — typed materializations rebuilt from
  staging. One row per real entity (`nonprofit_canonical`,
  `funder_canonical`, optionally `person_canonical`); a near-duplicate-
  collapsed narrative per EIN with two GIN-indexed `tsvector` columns
  for full-text search (`nonprofit_text` — `text_tsv_compact` for
  stemmed/relevance-ranked matching, `text_tsv_compact_simple` for
  exact-term matching); a filtered Schedule O Part III narrative table
  (`schedule_o_part_iii`).
- **Matched grants** — `unioned_grants` is the consumer-facing grants
  table: 990-PF grants joined to their matched recipient EINs (via
  `recordlinkage`) UNION'd with Schedule I grants. Indexed on both
  granter and grantee EIN.
- **Lineage** (`datamart_meta.*`) — `ingest_runs` records every staging
  ingest; `canonical_builds` records every canonical rebuild together
  with the `ingest_run_id` of every staging input it read.

## Setup

### 1. Install

The package is pip-installable. Two profiles:

```bash
# Read-only client only — for downstream consumers (notebooks, app
# backends). Dependency-light: just sqlalchemy + psycopg2-binary.
pip install -e .

# Full pipeline — ingestion, canonical build, grant matching.
# Pulls boto3, polars, recordlinkage, pyarrow, etc.
pip install -e '.[ingest]'
```

Requires Python ≥ 3.10.

### 2. Postgres

You need an empty Postgres database called `gt_datamart`. The pipeline
deliberately does not create databases for you — having a human do this
once means a typo in config can never silently provision a new database.

```bash
createdb gt_datamart
# or:
psql -h <host> -U <user> -d postgres -c "CREATE DATABASE gt_datamart;"
```

Configure connection details in either:

- A `config.ini` file with a `[postgres]` section pointing at your host
  — read by [`vdl-tools`](https://github.com/vibrant-data-labs/vdl-tools)
  (a separate library, pulled in as a dependency of the `[ingest]`
  install profile).
- Or the `GT_DATAMART_PG_*` environment variables, used by the
  read-only client. See [Read-only Python client](#read-only-python-client).

The ingest path uses `config.ini`; the read-only client uses the
environment variables. They are independent — set whichever you need.

### 3. Source data

Source CSVs live in the public-read S3 bucket
`gt990datalake-analytics-and-datamarts` (no AWS credentials required).
The pipeline lists the bucket and picks the newest matching file for
each registered source automatically.

## Usage

All commands run as Python modules. Output is plain text tables, easy to
pipe or eyeball.

### Inspect

```bash
# What's in S3 right now? (no DB connection)
python -m givingtuesday_datamart.sources status

# What's loaded into gt_datamart, at what version, how long ago?
python -m givingtuesday_datamart.sources loaded
```

`status` lists each registered source alongside the newest matching file
in S3 — version date, filename, size. `loaded` queries the run-history
table and shows the latest run per source: status (`success` / `failed` /
`skipped` / `never`), version, row count, last-ingested-at, age, and a
count of validation warnings. Failed runs and validation warnings are
re-printed below the table so they don't disappear into JSONB.

### Refresh staging

```bash
# Refresh every default source. Skips a successful (source, version)
# pair — it's already loaded — and reports it as `skipped`.
python -m givingtuesday_datamart.sources refresh

# A single source.
python -m givingtuesday_datamart.sources refresh --source irs_990_missions

# Multiple specific sources.
python -m givingtuesday_datamart.sources refresh \
    --source irs_990_basic_fields \
    --source irs_990_missions

# Bypass the (source, version) idempotency check — drops and recreates
# the staging table from scratch.
python -m givingtuesday_datamart.sources refresh --source irs_990_missions --force
```

Refresh streams CSVs straight from S3 to Postgres `COPY`, in batches of
50,000 rows, stamping the four lineage columns inline. Every successful
ingest runs a small validation pass (see
[Validation](#validation)). A failed run is recorded and does not block
retries — just rerun the same command.

### Build canonical tables

```bash
# Rebuild the canonical layer from current staging.
# DROP + CREATE inside a transaction; idempotent.
python -m givingtuesday_datamart.sources build-canonical
```

Run this after a successful `refresh`. It rebuilds:

- `public.nonprofit_canonical` — one row per EIN. Identity fields
  (name, DBAs, address) and the latest filing's identifiers, picked via
  `DISTINCT ON (filerein)` ordered by tax year desc, tax-period-end
  desc, ingested_at desc as deterministic tiebreaks.
- `public.nonprofit_text` — one row per EIN. Five source fields (mission
  statement, Part III program activities 1/2/3, Schedule O Part III
  narrative) across every filed year are collapsed into a single
  near-duplicate-deduped narrative + two FTS surfaces. Each snippet is
  normalized (lowercased, with 4-digit years, dollar amounts,
  percentages, comma-grouped numbers, punctuation, and whitespace
  stripped — bare digits intentionally preserved so "5 victims" and
  "50 victims" remain distinct), md5-hashed into a `norm_key`, and
  clustered cross-source. Each cluster picks one representative (most
  recent year wins, longest text as tiebreak); representatives feed
  `unique_text_compact` (the display surface). The token-union of
  *every* cluster member's original text — not just representatives —
  feeds two GIN-indexed `tsvector` columns: `text_tsv_compact`
  (`english` config: Snowball stemming + stopword removal, for
  relevance-ranked search) and `text_tsv_compact_simple` (`simple`
  config: lowercase + tokenize, for exact-term search). Tokens that
  only appeared in older filings still match — the FTS index isn't
  shortened to the representative's vocabulary. Per-EIN
  `n_compact_snippets` and `n_raw_snippets` columns expose the
  compression ratio for monitoring.
- `public.funder_canonical` — analogous to `nonprofit_canonical` but
  built from the 990-PF universe.
- `public.schedule_o_part_iii` — Schedule O rows filtered to Form
  990 / 990-EZ Part III continuation narratives only, via
  case-insensitive word-boundary regex on `sidfalrdesc`.

The build records itself in `datamart_meta.canonical_builds` together
with the `ingest_run_id` of every staging input — so a downstream
consumer can always answer "which source versions is this canonical
table derived from?".

### Officers tables (opt-in)

Two staging tables — `irs_990_officers` and `irs_990pf_officers` — are
flagged `skip_default_refresh=True` because the underlying CSVs are
large (~18 GB hot for the 990 side). They are not part of the default
`refresh` and the canonical layer that depends on them
(`person_canonical`, `org_person_role`) is built only when explicitly
requested:

```bash
# Explicit ingest:
python -m givingtuesday_datamart.sources refresh --source irs_990_officers
python -m givingtuesday_datamart.sources refresh --source irs_990pf_officers

# Then build the people canonical tables:
python -m givingtuesday_datamart.sources build-canonical --with-people
```

To make officers part of the default refresh, flip
`skip_default_refresh=False` for those entries in
[`registry.py`](givingtuesday_datamart/sources/registry.py).

### Match grants

```bash
# Match private-foundation grants to recipient EINs and rebuild
# public.unioned_grants. Resumable from S3-stored chunk checkpoints.
python -m givingtuesday_datamart.grant_matching

# Test mode: cap input row counts; chunks are written to a quarantined
# subdirectory so they can't be confused with production checkpoints.
python -m givingtuesday_datamart.grant_matching --limit 10000

# Recompute from scratch, ignoring any existing chunks.
python -m givingtuesday_datamart.grant_matching --no-resume

# Tune chunk size or resume parallelism.
python -m givingtuesday_datamart.grant_matching --chunk-size 50000 --resume-workers 32
```

Uses [`recordlinkage`](https://recordlinkage.readthedocs.io/) to compare
private-grant recipient names + addresses against `nonprofit_canonical`.
Candidate-pair chunks are written to S3 keyed on the source-data lineage
(`(privategrants version, nonprofit_canonical version)`), so reruns
against the same inputs resume from existing chunks.

### Recommended first run

Refresh smallest-to-largest so that errors surface fast:

```bash
python -m givingtuesday_datamart.sources refresh --source irs_990pf_basic_fields  # ~730 MB
python -m givingtuesday_datamart.sources refresh --source irs_990_missions        # ~1.0 GB
python -m givingtuesday_datamart.sources refresh --source irs_schedule_i_grants   # ~1.9 GB
python -m givingtuesday_datamart.sources refresh --source irs_990_basic_fields    # ~2.1 GB
python -m givingtuesday_datamart.sources refresh --source irs_990_programs        # ~2.2 GB
python -m givingtuesday_datamart.sources refresh --source irs_990pf_grants        # ~4.4 GB
python -m givingtuesday_datamart.sources refresh --source irs_schedule_o          # ~10  GB

python -m givingtuesday_datamart.sources build-canonical
python -m givingtuesday_datamart.grant_matching
```

End-to-end this is a multi-hour job; plan for an overnight run.

## Validation

Every successful staging refresh runs a validation pass before the run is
finalized ([`validation.py`](givingtuesday_datamart/validation.py)):

- **Hard-fail** — the run is marked `failed` and rolled back if: the row
  count is zero; lineage columns are missing on any row; or any required
  column has more than 1% NULL.
- **Soft-warn** — the run stays `success`, but warnings are recorded in
  the `validation` JSONB column when: the schema drifts from the prior
  successful run; or row count falls outside [0.8×, 2.0×] of the prior
  successful run.

Warnings show up in the `loaded` command's `warns` column and are
printed below the table.

## Read-only Python client

`GtDatamartClient` is a SQLAlchemy-based read-only client over the
canonical surface. It has no dependency on the heavier `[ingest]` extra
— `pip install -e .` is enough — so downstream consumers don't need to
pull in `boto3`, `polars`, etc. just to query the database.

```python
from givingtuesday_datamart.client import GtDatamartClient

# Connection precedence:
#   1. engine=...         (pre-built SQLAlchemy Engine)
#   2. url="postgresql://..."
#   3. host/port/user/password/database kwargs
#   4. GT_DATAMART_PG_HOST / _PORT / _USER / _PASSWORD / _DATABASE env vars
#      (port and database have sensible defaults)
client = GtDatamartClient()

hits = client.search_nonprofits(["climate", "wildfire"], limit=20)
# Exact-term + phrase match (no stemming, tokens adjacent in order):
#   "tutoring"     matches only "tutoring" (not "tutor" / "tutored")
#   "needs based"  matches the literal phrase, NOT "meet your needs ... based here"
exact = client.search_nonprofits(["needs based"], search_mode="exact", limit=20)
profile = client.get_nonprofit(ein="123456789")
years = client.get_basic_fields(eins=["123456789"], min_taxyear=2018)
grants_received = client.get_grants(eins=["123456789"], role="grantee")
grants_made = client.get_grants(eins=["123456789"], role="granter")
```

The methods return frozen dataclasses (`NonprofitHit`, `Nonprofit`,
`BasicFieldsRow`, `Grant`); the client deliberately does not depend on
pandas. Callers that want a DataFrame:

```python
from dataclasses import asdict
import pandas as pd
df = pd.DataFrame.from_records([asdict(h) for h in hits])
```

## Frontend (`peerlo`)

A Next.js explorer that reads `gt_datamart` directly via its own pg pool
([`frontend/src/lib/db.ts`](frontend/src/lib/db.ts)).

- **Search** — Postgres FTS over `nonprofit_text.text_tsv_compact`
  (stemmed/relevance-ranked), with a mode toggle (search by name, by
  narrative, or both).
- **Org profile** — canonical identity, narrative, lineage, multi-year
  basic fields, and server-side paginated grants tables on both sides
  (received and made).
- **`/about`** — explainer page.

Local development:

```bash
cd frontend
cp .env.local.example .env.local   # then fill in PG_HOST / PG_USER / PG_PASSWORD
npm install
npm run dev
```

The frontend reads `PG_HOST` / `PG_PORT` / `PG_DATABASE` / `PG_USER` /
`PG_PASSWORD` from `.env.local`.

## Verifying a refresh

Connect to `gt_datamart` to inspect the lineage and validation tables
directly:

```sql
-- All ingest runs, newest first, with validation JSONB
SELECT run_id, logical_name, source_version, status, row_count,
       finished_at - started_at AS duration, error, validation
FROM datamart_meta.ingest_runs
ORDER BY started_at DESC;

-- Most recent run per source
SELECT DISTINCT ON (logical_name)
    logical_name, source_version, status, row_count, finished_at
FROM datamart_meta.ingest_runs
ORDER BY logical_name, finished_at DESC;

-- Lineage stamped on every row of a staging table
SELECT DISTINCT _source_version, _ingest_run_id
FROM public.mission_statements;

-- Canonical build lineage: which staging runs fed the latest rebuild?
SELECT build_id, started_at, finished_at, source_runs
FROM datamart_meta.canonical_builds
ORDER BY started_at DESC LIMIT 5;
```

## Adding a new source

Edit
[`givingtuesday_datamart/sources/registry.py`](givingtuesday_datamart/sources/registry.py)
and append a new `_spec(...)` entry:

```python
_spec(
    logical_name="irs_new_thing",
    staging_table_name="public.new_thing",
    form_type="990",  # or "990-PF"
    description="Short human-readable description.",
    filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_NewThingPattern\.csv$",
    required_columns=("filerein",),
    indexes=(IndexSpec("ix_new_thing_filerein", ("filerein",)),),
),
```

Two non-obvious requirements:

1. The **first capture group** of `filename_regex` must capture
   `YYYY_MM_DD` so the resolver can pick the newest matching file.
2. `required_columns` are checked by validation; `indexes` are recreated
   by ingestion after every COPY (so they can never drift from
   declaration).

Verify before running `refresh`:

```bash
python -m givingtuesday_datamart.sources status
```

If the new source shows `NOT FOUND`, the regex doesn't match anything in
the bucket — fix it before running `refresh`. Source-CSV filenames are
slightly inconsistent (e.g. `990PFPart7p1Officers.csv` vs
`990PFPart7p1-Officers.csv`); make patterns tolerant (`-?`, character
classes) rather than exact.

## Design notes

- **All-TEXT staging.** Every staging column is `TEXT`. Zero-padded
  identifiers round-trip intact. Consumers cast at query time, or read
  typed columns from the canonical / unioned tables.
- **Streaming ingestion.** No on-disk cache, no pandas in the hot path.
  CSV is parsed via `csv.reader` over an `io.TextIOWrapper(newline="")`
  on a `BufferedReader` wrapping `response.iter_content()`. Quoted
  multi-line fields (mission statements, Schedule O narratives) are
  preserved — a naive `iter_lines` split would mangle them.
- **`COPY` in batches of 50,000.** Each batch is stamped with the four
  lineage columns inside the same `COPY` command — no second pass.
- **Indexes are declarative.** `SourceSpec.indexes` is the single source
  of truth; ingestion recreates indexes from the spec after every COPY.
- **Idempotency in the application layer**, not via DB constraints.
  Before creating a run row, we check `datamart_meta.ingest_runs` for an
  existing successful `(logical_name, source_version)`. Easy to reason
  about; easy to override with `--force`.
- **Database isolation.** `gt_datamart` is its own database, separate
  from any other Postgres database that might exist on the same host.
  A bad refresh can never damage unrelated data.

## Troubleshooting

**A `refresh` is reporting `skipped` and you want to actually re-ingest.**
Idempotency working as designed. Pass `--force` to drop the staging
table and re-run.

**`database "gt_datamart" does not exist`.** Run the one-time
`createdb gt_datamart` step in [Setup](#2-postgres).

**A source shows `NOT FOUND` in `status`.** The `filename_regex` in
`registry.py` doesn't match anything in the bucket. Check what's
actually there with `aws s3 ls
s3://gt990datalake-analytics-and-datamarts/EfileDataMarts/ --no-sign-request`,
then loosen the regex (`-?`, `[A-Z_]?`) rather than hard-coding the new
filename.

**A validation warning showed up in `loaded`.** Inspect the JSONB:

```sql
SELECT validation
FROM datamart_meta.ingest_runs
WHERE run_id = '<run_id from loaded output>';
```

The blob lists every check, its status, and a free-form `detail` field.

**`build-canonical` fails complaining about a missing staging table.**
The canonical layer reads from staging; if a source it depends on is
empty (e.g. `programs`, `mission_statements`, `schedule_o`), refresh
that source first.

**`GtDatamartClient` raises "missing connection components".** Either
pass `host` / `user` / `password` to the constructor, or set
`GT_DATAMART_PG_HOST`, `GT_DATAMART_PG_USER`, and `GT_DATAMART_PG_PASSWORD`
in the environment.

## Repo layout

```
givingtuesday_datamart/
  sources/
    spec.py             # SourceSpec / ColumnSpec / IndexSpec dataclasses
    registry.py         # One SourceSpec per ingested table
    resolver.py         # Picks the latest matching file from S3 (boto3 unsigned)
    __main__.py         # CLI: status, loaded, refresh, build-canonical
  ingestion.py          # ingest_source(); ingest_runs tracking; index rebuild
  write_data_to_sql.py  # Streaming CSV -> COPY (all-TEXT cols, lineage stamping)
  validation.py         # Hard-fail + soft-warn checks run after each refresh
  canonical/
    build.py            # Canonical layer: nonprofit_canonical, nonprofit_text (FTS),
                        # funder_canonical, schedule_o_part_iii, optional people
  client/
    client.py           # GtDatamartClient — read-only, no extra dependencies
    models.py           # Frozen dataclasses returned by the client
  grant_matching.py     # recordlinkage pipeline + S3-checkpointed resume;
                        # rebuilds public.unioned_grants
frontend/               # Next.js peerlo app (reads gt_datamart via pg pool)
docs/
  backbone-plan.md      # Detailed architecture + decision history
pyproject.toml          # pip-installable; [ingest] extra for the heavy path
```

## More

[`docs/backbone-plan.md`](docs/backbone-plan.md) is a deeper architecture
document — the phased roadmap, the trade-offs behind each layer, and
verification criteria. Useful when extending the pipeline beyond what's
covered here.
