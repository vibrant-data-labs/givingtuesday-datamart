# Giving Tuesday Datamart → Internal Data Backbone

## Context

The Giving Tuesday Datamart integration has served as a prototype: it works end-to-end, but the team cannot keep it fresh reliably, cannot trust what version of the data a result came from, and pays a memory/compute tax every time a downstream consumer (vdl-tools' `query_prepare_givingtuesday`) runs. Strategic decision: invest in this as the **internal source-of-truth data backbone** for all VDL nonprofit/funder work, with an architecture that serves VDL pipelines + analysts first without foreclosing an external-facing product later.

**Primary pain points driving this work** (from the PM):
1. Freshness + lineage — nobody can confidently re-run ingestion, and nobody can tell which Datamart version a given result came from.
2. Slow / memory-hungry queries — keyword search loads the entire joined-text table into Python memory; the web app is slow; matching requires an r7a.4xlarge.

**What is explicitly out of scope:**
- Migrating off `recordlinkage` (deliberate choice, stays).
- Introducing DuckDB as a primary store (team doesn't use it regularly).
- Replacing the vdl-tools integration pattern (this repo remains vdl-tools-native).

## Strategic framing

**Bet:** Internal data backbone, ongoing program.
**Primary consumers:** vdl-tools pipelines + VDL analysts. Architecture must not foreclose an external product later.
**Primary store:** PostgreSQL (continues as today, but with real schemas, indexes, and lineage). S3 for raw/archival + matching checkpoints (continues pattern).
**Orchestration:** Start with a clean Python entrypoint (`refresh` command) that's idempotent and cron-triggerable on an EC2 host sized for the ingestion + matching load. Defer Airflow/Prefect/Dagster until the refresh cadence justifies it.

## Phased plan

### Phase 1 — Ingestion + lineage reset *(highest priority; solves pain #1)*

**Goal:** One command refreshes the entire datamart from source, every record knows where it came from, and running it twice is a no-op.

Key work:
- **Source registry.** Replace the hardcoded dated S3 URLs in `scripts/create_tables.py:*` with a named-source registry (YAML or Python module) that maps logical table names (`irs_990_basic_fields`, `irs_990pf_grants`, `irs_990_officers`, `irs_990pf_officers`, etc.) to a URL pattern + version resolver. No consumer should ever hardcode a dated URL again.
- **Add officers/board members as a first-class source.** Ingest both `officers` Datamart tables (990 and 990-PF) — not just as an attribute on nonprofits, but as input to a `person_canonical` entity layer (see Phase 2). These are the raw records behind board members, officers, directors, trustees, and key employees.
- **Typed schemas.** For each of the ~9 core Datamart tables (7 existing + the two officers tables), define a typed schema (column → Postgres type + nullability + human name). Stop inferring from the first 100 rows. Column names become human-readable + documented. The two officer tables likely have different column shapes (990 vs 990-PF) — type them separately, then union in a canonical view downstream.
- **Lineage columns.** Every ingested table gets `_source_version` (the Datamart drop identifier), `_source_url`, `_ingested_at`, `_ingest_run_id`. These are the breadcrumbs that let any downstream consumer answer "what version of the data am I looking at?"
- **Idempotent ingestion.** Re-running ingestion for the same source version is a no-op. New versions create a new snapshot (see next bullet).
- **Versioned snapshots.** Each table is physically partitioned or tagged by `_source_version`. A canonical view (e.g., `nonprofit_basic_fields_current`) always points at the latest good version. Rolling back = flipping the view.
- **Validation step.** After ingestion: row count diff vs prior version, key fields non-null, schema drift detection. Hard fail the refresh if a validation check fails.
- **Single entrypoint.** `python -m givingtuesday_datamart refresh` (full) and `python -m givingtuesday_datamart refresh --source <name>` (single table). The `scripts/` directory becomes thin CLI wrappers, not the orchestrator.
- **Observability.** Ingestion run table in Postgres: `ingest_runs(run_id, started_at, finished_at, source_version, status, row_counts_json, errors)`. Enough for an analyst to open a notebook and answer "is today's data fresh?"

### Phase 2 — Query surfaces *(solves pain #2)*

**Goal:** Kill in-memory keyword search. Make the web app fast. Give vdl-tools a fast, indexed surface to call.

Key work:
- **Real indexes + keys.** Primary keys on EINs where appropriate, BTree indexes on tax year, foreign-key-style indexes across filing/programs/schedule-O tables. Get the basic OLTP query patterns off sequential scans.
- **Canonical entity views.** Three entity types, not two:
  - `nonprofit_canonical(ein, name, address, ntee, ...)` — one row per EIN, latest clean record.
  - `funder_canonical(ein, name, type, ...)` — one row per grant-making EIN.
  - `person_canonical(person_id, name, ...)` — one row per officer/director/trustee/key employee/board member, derived from the union of the two officers sources. Start with a conservative dedup key (normalized name + org EIN + year) so each filing's record is preserved; cross-org person deduplication (same human on multiple boards) moves to Phase 3 as a recordlinkage problem on the people side.
  - `org_person_role(person_id, ein, tax_year, role, compensation, ...)` — the link table joining people to nonprofits with their role and tenure. This is where the "who sits on what board" network queries come from.

  Aliases and history (for nonprofits, funders, and people) preserved in sibling tables but not blocking the canonical lookup. This is also where "first-row naïve selection" currently used by the vdl-tools consumer gets fixed.
- **Postgres FTS for nonprofit text.** Replace the `non_profit_joins_for_text` wide-concatenated-text table pattern with a properly-built FTS column + GIN index. **The source text must be a per-EIN *deduplicated* union across Mission Statement, Activities/Programs, and Schedule O Part III** — not the current lazy string concatenation. Duplicate sentences (same mission copy-pasted every year, same program description across activities 1/2/3, overlap between Activities and Schedule O) get collapsed before being fed to `tsvector`. Output: one `(ein, unique_text, tsvector)` row per nonprofit with a GIN index. The vdl-tools consumer switches from "load table into memory, run keyword search in Python" to "execute a parameterized SQL FTS query" — dramatically cheaper in time and memory.
- **API shape for vdl-tools.** A small Python client module inside `givingtuesday_datamart/` that vdl-tools imports. Methods: `search_nonprofits(keywords, filters)`, `get_nonprofit(ein)`, `get_grants(ein)`. The client hides the SQL from consumers so schema changes don't cascade. Release/versioning coupling with vdl-tools handled separately.
- **Web app speedup.** Comes largely for free once indexes and canonical views exist. No architectural change to the React frontend yet.
- **Frontend: richer organization profiles.** Current profiles only surface name + contributions/grants — a small fraction of what the 990 actually contains. Expand organization detail views to include (at minimum):
  - Identity block: canonical name, EIN, NTEE classification, address, founding/ruling date, website
  - Mission Statement + Program descriptions + Schedule O Part III narrative (the same deduped text now powering FTS — surface it here too so users see what matched)
  - Financial snapshot across years: revenue, expenses, net assets, employees, volunteers (trend sparklines, not just latest)
  - People: officers, directors, trustees, key employees, and board members — sourced from `org_person_role` joined to `person_canonical`. Each person name is clickable and leads to a person profile showing every nonprofit they've been associated with.
  - Governance/compliance flags worth surfacing (public charity status, 501(c) subsection, etc.)
  - Grants given + received (already there, but driven off `funder_canonical` / `nonprofit_canonical` so names and EINs are consistent and clickable)
  - Lineage footer: which Datamart version and tax years power this profile (drives trust + debugging)

  Implementation note: the goal is "show the analyst or investigator what the 990 actually says about this org at a glance," not a pretty-but-shallow card. Design the backend query as a single `get_nonprofit_profile(ein)` call that returns everything needed for the view, to keep the frontend simple and the backend auditable.

### Phase 3 — Ongoing capability upgrades

These run as a rolling backlog once Phases 1–2 make iteration safe.
- Funder classification (DAF / community foundation / government / corporate / family) on `funder_canonical`. Candid classes available as seed data.
- Attachment-grant extraction (the grants listed in 990-PF attachments, currently missed).
- Matching threshold tuning — write a regression harness for `matching_records_experiment.py` so the hard-coded thresholds (name ≥0.95, addr ≥0.35) become tunable and measured. Rename "experiment" → "pipeline" when it has tests.
- **Cross-org person deduplication.** Move `person_canonical` from "one row per (name + org + year)" to "one row per unique human," using recordlinkage on name variants + co-occurring orgs + address overlap. This is the version that enables "show me everyone who sits on both Ford Foundation's board and any climate-focused nonprofit" queries.
- Canonical name/address sanitization — title-casing, latest-version selection — pushed upstream from vdl-tools into `nonprofit_canonical` so every consumer benefits.

## Architecture decisions

| Decision | Choice | Why |
|---|---|---|
| Primary store | PostgreSQL | Already in place, serves web app, vdl-tools uses `get_session()`. No reason to migrate. |
| Archival / raw | S3 (existing pattern) | Matching already checkpoints there; ingestion can write a versioned raw snapshot alongside. |
| Search | Postgres FTS (`tsvector` + GIN) | One system, no new ops surface. Re-evaluate OpenSearch/pgvector only if FTS limits bite. |
| Orchestration | Python entrypoint + cron on EC2 (initially) | Airflow/Prefect are premature until refresh cadence + fanout justify them. EC2 sized large enough to run ingestion + matching in one place. |
| Historical migration | None — re-ingest from scratch on new schema | Migrating TEXT-typed legacy data isn't worth the effort; clean slate on new lineage-tracked schema. |
| Fuzzy matching | Keep `recordlinkage` | Deliberate choice per PM; out of scope for this work. |
| Column typing | Explicit typed schema per source | Replaces CSV inference + all-TEXT columns. Types live in code, reviewed like any other interface. |
| Schema evolution | Versioned snapshots + current views | Lets us roll forward/back without blocking consumers during a refresh. |

## Critical files

Files that will change materially:
- [scripts/create_tables.py](scripts/create_tables.py) — shrinks to a thin CLI wrapper around the new refresh entrypoint.
- [givingtuesday_datamart/write_data_to_sql.py](givingtuesday_datamart/write_data_to_sql.py) — splits into `ingestion/download.py`, `ingestion/load.py`, `ingestion/validate.py`. Streaming mode stays (it's good). Schema inference goes.
- [givingtuesday_datamart/sql_queries/non_profit_text_joins.sql](givingtuesday_datamart/sql_queries/non_profit_text_joins.sql) — rewritten to build FTS-ready columns + GIN index, not an in-memory-ready wide text table.
- [givingtuesday_datamart/sql_queries/unique_fields_for_grants.sql](givingtuesday_datamart/sql_queries/unique_fields_for_grants.sql) — refactored against canonical entity views.
- [givingtuesday_datamart/matching_records_experiment.py](givingtuesday_datamart/matching_records_experiment.py) — renamed to `matching/pipeline.py` with a regression harness in Phase 3.

New files / modules:
- `givingtuesday_datamart/sources/registry.py` — named-source → URL resolver.
- `givingtuesday_datamart/sources/schemas/*.py` — typed schema per Datamart table.
- `givingtuesday_datamart/ingestion/` — download, load, validate, lineage stamping.
- `givingtuesday_datamart/canonical/` — canonical entity resolution for nonprofits, funders, and people (+ `org_person_role` link table).
- `givingtuesday_datamart/client/` — Python client consumed by vdl-tools.
- `givingtuesday_datamart/cli.py` — `refresh`, `status`, `validate` commands.

## Verification

**Phase 1 done when:**
- `python -m givingtuesday_datamart refresh` ingests all 7 sources from scratch on a clean Postgres.
- Running it a second time without a new source version is a no-op (idempotent).
- Every row in every ingested table has `_source_version`, `_source_url`, `_ingested_at`, `_ingest_run_id` populated.
- `ingest_runs` table shows the run with row counts and status.
- Validation failures abort the refresh and emit a clear error.
- Documented refresh runbook checked into the repo.

**Phase 2 done when:**
- vdl-tools' `query_prepare_givingtuesday.py` imports the new client and uses FTS — no more loading the joined text table into memory. Measured: keyword search latency and peak memory drop materially vs. baseline.
- `nonprofit_canonical` and `funder_canonical` views exist, return one row per EIN, and are used by at least one downstream surface.
- The React frontend organization profile page renders the expanded detail set (identity, mission/programs/Schedule O text, multi-year financials, people, grants given/received, lineage footer) via the new `get_nonprofit_profile(ein)` call.
- Same profile query renders noticeably faster than baseline on the post-index schema.

**Phase 3 done when:**
- Rolling — measured per capability, not as a single milestone.

## Open questions for follow-up

1. **Refresh cadence.** Quarterly? Aligned to a specific IRS drop? This needs a conversation with Giving Tuesday before we commit to a rhythm. The plan works for any cadence but ops details (cron schedule, notification surface, EC2 sizing) depend on it.
