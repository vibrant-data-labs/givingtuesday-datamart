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

## Status (2026-04-29)

All work to date lives on `zein/raw_notes`, not yet merged to main.

**Phase 1 — complete.** Ingestion + lineage shipped end-to-end on `gt_datamart` (a dedicated database on the shared VDL RDS, isolated via `datamart_config()` override to `get_session`). All 9 sources (basic_fields, basic_fields_pf, mission_statements, programs, schedule_o, grants_to_domestic_organizations, privategrants, officers, officers_pf) ingested with full lineage stamping and idempotent re-runs. Two intentional deviations from the original plan: (a) all-TEXT staging instead of typed schemas — typing the IRS columns needs domain knowledge we don't have yet, and the matching pipeline casts at query time; (b) versioned snapshots + `_current` views skipped — overwrite-on-success + validation has been sufficient.

**Phase 2 — partially complete.** Canonical surfaces live: `schedule_o_part_iii` (964K rows), `nonprofit_canonical` (465K), `nonprofit_text` with GIN-indexed `tsvector` (511K), `funder_canonical` (157K). FTS measured at sub-second on multi-token queries with stemming. Lineage table `datamart_meta.canonical_builds` records every build with the upstream `ingest_run_id`s that fed it. **Person canonical layer (`person_canonical`, `org_person_role`) indefinitely deferred** — see RDS storage section below. Two Phase 2 items still active: vdl-tools client module, and the expanded frontend organization profile.

**Grant matching pipeline — ported and resumable.** `matching_records_experiment.py` → `grant_matching.py`, on `gt_datamart` + `public.*`, with lineage-keyed S3 checkpoint prefixes (`grant_matching_checkpoints/pg_<v>/bf_<v>/`), deterministic input ordering, parquet-index-preserving chunk writes, and parallel chunk resume via direct boto3 (bypasses the fsspec layer that was capping throughput). Each run stamps `datamart_meta.canonical_builds` with `build_kind='grant_matching'` plus output rowcounts and source-run IDs.

**Resume perf measured on EC2 (2026-04-29):** 12,972 chunks listed + read in **27 seconds** at 472 it/s with 31 workers — direct boto3 + sized connection pool. Compare to the prior fsspec path which was clocked at 4.35 it/s (would have been ~50 minutes for the same workload). Direct round-trip per chunk on EC2: ~2ms effective with 31-way parallelism.

**Matching result at sources (`pg_2025_10_28`, `bf_2025_10_18`):** 648M candidate ZIP-blocked pairs → 1,993,316 post-stage-1 matches → 1,098,084 unique privategrants → recipient mappings after multi-match resolution. End-to-end correctness check vs. the old `irs_filings` outputs is still pending — the next step before merging this work is a quantitative diff against the prior run to make sure the port produced equivalent matches.

**Active priorities** (in order):
1. Quantitative diff of new vs. old grant matching outputs (correctness verification of the port).
2. Drop `public.schedule_o` raw staging once consumers are confirmed off it (recovers ~17 GB).
3. vdl-tools client module so consumer queries don't go via raw SQL.
4. Frontend organization profile expansion against the new canonical surfaces.

## Strategic framing

**Bet:** Internal data backbone, ongoing program.
**Primary consumers:** vdl-tools pipelines + VDL analysts. Architecture must not foreclose an external product later.
**Primary store:** PostgreSQL (continues as today, but with real schemas, indexes, and lineage). S3 for raw/archival + matching checkpoints (continues pattern).
**Orchestration:** Start with a clean Python entrypoint (`refresh` command) that's idempotent and cron-triggerable on an EC2 host sized for the ingestion + matching load. Defer Airflow/Prefect/Dagster until the refresh cadence justifies it.

## Phased plan

### Phase 1 — Ingestion + lineage reset *(✅ complete — solved pain #1)*

**Goal:** One command refreshes the entire datamart from source, every record knows where it came from, and running it twice is a no-op.

Key work:
- ✅ **Source registry.** Hardcoded URLs replaced by `givingtuesday_datamart/sources/registry.py` (logical name → S3 pattern + version resolver via boto3-unsigned).
- ✅ **Officers/board members ingested as first-class sources** (`public.officers`, `public.officers_pf`). *Note: both tables were dropped on 2026-04-27 to free RDS disk; `skip_default_refresh=True` keeps them out of default refreshes. See RDS storage section.*
- ❌ **Typed schemas** — intentionally deferred. All-TEXT staging with consumer-side casts. Typing hundreds of cryptic IRS columns needs domain knowledge we don't have, and the current shape works.
- ✅ **Lineage columns.** `_source_version`, `_source_url`, `_ingested_at`, `_ingest_run_id` stamped on every row during COPY (no second pass).
- ✅ **Idempotent ingestion.** App-level check on `(logical_name, source_version)` in `datamart_meta.ingest_runs`; `--force` to override.
- ❌ **Versioned snapshots** — intentionally skipped. Overwrite-on-success + validation has been sufficient; revisit only if rollback becomes a real need.
- ✅ **Validation step.** 5 checks (row_count_positive, lineage_columns, required_columns, schema_drift, row_count_delta), hard-fail vs warn, results in `ingest_runs.validation` JSONB.
- ✅ **Single entrypoint.** `python -m givingtuesday_datamart.sources refresh [--source NAME] [--force]`.
- ✅ **Observability.** `datamart_meta.ingest_runs` table; `status` CLI for S3 freshness; `loaded` CLI for DB state.

### Phase 2 — Query surfaces *(🟡 partial — canonical layer + FTS shipped; client + frontend still active)*

**Goal:** Kill in-memory keyword search. Make the web app fast. Give vdl-tools a fast, indexed surface to call.

Key work:
- 🟡 **Real indexes + keys.** PKs on EIN exist on `nonprofit_canonical` and `funder_canonical`. GIN index on `nonprofit_text.text_tsv`. Broader BTrees across staging tables not yet added systematically — fold into the next round once query patterns from the client module surface what's actually slow.
- **Canonical entity views.**
  - ✅ `nonprofit_canonical(ein, name, address, ...)` — DISTINCT ON (ein) from `basic_fields`, ordered by taxyear → taxperend → ingested_at. PK on ein. 465K rows.
  - ✅ `funder_canonical(ein, name, ...)` — same shape from `basic_fields_pf`. 157K rows. Funder type/classification deferred to Phase 3 (needs Candid data).
  - 🛑 **`person_canonical` and `org_person_role` — indefinitely deferred.** Together they materialize ~90M rows + 3 indexes, which the shared RDS doesn't have headroom for. The officers staging tables they read from were dropped on 2026-04-27 to free 20 GB. No active downstream consumer needs people-level data. Revisit when (a) a real consumer pulls on it, AND (b) we either bump RDS storage or pre-process officers to a smaller canonical-direct shape (see RDS storage section).

  Aliases and history not built — preserve revisiting until the canonical lookup hits a real consumer need.
- ✅ **Postgres FTS for nonprofit text.** `public.nonprofit_text` (511K rows) is a per-EIN deduplicated UNION across Mission Statement, Activities 1/2/3, and Schedule O Part III, with `tsvector` + GIN index. Field-level dedup via UNION collapses copy-pasted text across years and across activity slots. FTS measured at sub-second on multi-token queries, ~10ms on simple terms.
- ❌ **API shape for vdl-tools** — still active. A small Python client module inside `givingtuesday_datamart/client/` that vdl-tools imports. Methods: `search_nonprofits(keywords, filters)`, `get_nonprofit(ein)`, `get_grants(ein)`. Hides SQL from consumers so schema changes don't cascade.
- 🟡 **Web app speedup.** Web app still runs on the old VDL DB; will benefit once it cuts over to `gt_datamart` via the client module above.
- ❌ **Frontend: richer organization profiles** — still active. Current profiles only surface name + contributions/grants — a small fraction of what the 990 actually contains. Expand organization detail views to include (at minimum):
  - Identity block: canonical name, EIN, NTEE classification, address, founding/ruling date, website
  - Mission Statement + Program descriptions + Schedule O Part III narrative (the same deduped text now powering FTS — surface it here too so users see what matched)
  - Financial snapshot across years: revenue, expenses, net assets, employees, volunteers (trend sparklines, not just latest)
  - People — *deferred with `person_canonical` / `org_person_role`*. When that layer comes back online, this section returns to scope.
  - Governance/compliance flags worth surfacing (public charity status, 501(c) subsection, etc.)
  - Grants given + received (already there, but driven off `funder_canonical` / `nonprofit_canonical` so names and EINs are consistent and clickable; the matched union lives in `public.unioned_grants`).
  - Lineage footer: which Datamart version and tax years power this profile (drives trust + debugging). Source: `_source_version` on each row plus the relevant build row in `datamart_meta.canonical_builds`.

  Implementation note: the goal is "show the analyst or investigator what the 990 actually says about this org at a glance," not a pretty-but-shallow card. Design the backend query as a single `get_nonprofit_profile(ein)` call that returns everything needed for the view, to keep the frontend simple and the backend auditable.

### Phase 3 — Ongoing capability upgrades

These run as a rolling backlog once Phases 1–2 make iteration safe.
- ✅ **Grant matching pipeline ported to `gt_datamart`.** `matching_records_experiment.py` → `givingtuesday_datamart/grant_matching.py`. Reads from canonical-keyed views (`privategrants_w_column_keys_view`, `basic_fields_w_column_keys_view`, the matching `_unique_names_view` pair). Lineage-keyed S3 checkpoint prefix (`grant_matching_checkpoints/pg_<v>/bf_<v>/`) — re-ingesting either source rotates the prefix, so chunks can never silently be reused against new data. Every run stamps `datamart_meta.canonical_builds` with `build_kind='grant_matching'`, source_run IDs, and output rowcounts. Output tables: `public.privategrants_w_recipients` (matched 990-PF grants → recipient EINs) and `public.unioned_grants` (UNION of matched PF grants + 990 Schedule I grants, indexed on granter/grantee/taxyear).
- **Matching threshold tuning** — still pending. Write a regression harness for `grant_matching.py` so the 6 hard-coded thresholds (name ≥0.95/0.99, addr ≥0.35/0.50/etc.) become tunable and measured.
- **Matching memory footprint** — still pending. Currently requires r7a.4xlarge to hold both `_unique_names_view` DataFrames + the candidate-pairs MultiIndex in memory. Streaming or out-of-core variants would let this run on cheaper hardware.
- Funder classification (DAF / community foundation / government / corporate / family) on `funder_canonical`. Candid classes available as seed data.
- Attachment-grant extraction (the grants listed in 990-PF attachments, currently missed).
- **Cross-org person deduplication.** *Blocked on `person_canonical` coming back online* — see RDS storage section.
- Canonical name/address sanitization — title-casing, latest-version selection — pushed upstream from vdl-tools into `nonprofit_canonical` so every consumer benefits.

## RDS storage constraint

`gt_datamart` is a dedicated database on the **shared** VDL RDS instance. Hit DiskFull twice during this work — once on the initial `person_canonical` build (~12.4M rows in flight when it tipped), and once on the grant matching final JOIN (post-port). Snapshot of largest tables when the second one hit (2026-04-27, 68 GB used in `gt_datamart`):

| table | size | role |
|---|---|---|
| `public.officers` | 18 GB | dropped — staging only, fed person_canonical |
| `public.schedule_o` | 17 GB | candidate for drop — staging only, already filtered into `schedule_o_part_iii` + `nonprofit_text` |
| `public.privategrants` | 8 GB | required (matching reads it) |
| `public.unioned_grants` | 4.4 GB | required (consumer-facing) |
| `public.grants_to_domestic_organizations` | 3.6 GB | required (matching feeds unioned_grants) |
| `public.privategrants_w_recipients` | 3.6 GB | required (matching output) |
| `public.basic_fields` | 3.1 GB | required |
| `public.programs` | 3.0 GB | required (nonprofit_text source) |
| `public.officers_pf` | 2.0 GB | dropped — same status as officers |
| `public.mission_statements` | 1.9 GB | required (nonprofit_text source) |
| `public.nonprofit_text` | 1.7 GB | required (FTS surface) |

**Decisions driven by this:**

- **Officers staging tables dropped** to free ~20 GB. `SourceSpec.skip_default_refresh=True` keeps them out of default refreshes; explicit `--source irs_990_officers` still works for the day they need to come back.
- **Person canonical layer (`person_canonical`, `org_person_role`) indefinitely deferred.** No active downstream consumer; with officers gone, building it would also need a re-ingest first. Revisit when a real consumer appears.
- **`public.schedule_o` raw staging is the next drop candidate.** `schedule_o_part_iii` (the program-narrative subset that feeds `nonprofit_text`) and `nonprofit_text` itself are both built and don't need the 17 GB raw table for steady-state use. Same lineage-row preserve pattern as officers — drop the table, leave the `ingest_runs` row, re-ingest only when needed.

**Open architectural question:** dedicated RDS for `gt_datamart` vs. bumping shared storage. The current "drop staging that isn't strictly needed" approach buys runway but doesn't solve the underlying problem — eventually `person_canonical` or another large derived table will need to come back, and we'll be at the same wall. A dedicated instance gives blast-radius isolation and lets us size to the actual workload. Defer the call until the next consumer pulls on it.

**Pre-process playbook for any future big-staging table:**
1. Year filter at ingest (`taxyear >= 2015` cuts the long pre-2015 tail; matching pipeline already filters there).
2. Column pruning post-ingest (raw IRS rows have 50+ TEXT columns; canonical layer uses ~10).
3. Stream source CSV → canonical shape directly, no staging table at all (the right shape long-term).

## Architecture decisions

| Decision | Choice | Why |
|---|---|---|
| Primary store | PostgreSQL — **dedicated `gt_datamart` database on shared VDL RDS host** | Same RDS instance VDL already uses; isolated database for blast-radius + clean drops. Routed via `get_session(config=datamart_config())`. |
| Archival / raw | S3 (existing pattern) | Matching checkpoints there; ingestion streams from S3, no local raw cache. |
| Search | Postgres FTS (`tsvector` + GIN) | One system, no new ops surface. Working in production (`nonprofit_text`, sub-second multi-token queries with English stemming). |
| Orchestration | Python entrypoint + EC2 (cron deferred) | Airflow/Prefect remain premature. EC2 sized for ingestion + matching (matching still needs r7a.4xlarge until the memory footprint work in Phase 3). |
| Historical migration | None — re-ingest from scratch | Re-ingest done; old `irs_filings.*` schema retired. |
| Fuzzy matching | Keep `recordlinkage` | Deliberate; matching pipeline ported, not rewritten. |
| Column typing | All-TEXT staging, consumer-side casts | Pragmatic deviation from original plan. Typed schemas deferred until the typing effort justifies itself. |
| Schema evolution | Overwrite-on-success + validation (no versioned snapshots) | Pragmatic deviation. Validation has been sufficient as a safety net; revisit if rollback becomes needed. |
| Matching checkpoints | Lineage-keyed S3 prefix `pg_<v>/bf_<v>/` | Re-ingesting either source rotates the prefix; old chunks can't be silently reused against new data. |
| Resume reader | Direct boto3 → BytesIO → pyarrow (not fsspec) | Measured 472 chunks/sec on EC2 with 31 workers vs. 4.35/sec via the fsspec path; pool size tuned to `max_workers + 16` so connections don't bottleneck. JSON-column decode preserved per the pqc contract. |
| Build lineage | `datamart_meta.canonical_builds` (build_kind discriminator) | Records which source `ingest_run_id`s fed each canonical/matching build. Consumers can detect staleness without re-deriving. |

## Critical files

What landed (Phase 1 + Phase 2 canonical + Phase 3 matching):
- [givingtuesday_datamart/sources/registry.py](givingtuesday_datamart/sources/registry.py) — `SourceSpec` per logical source; 9 sources registered, 2 flagged `skip_default_refresh=True`.
- [givingtuesday_datamart/sources/spec.py](givingtuesday_datamart/sources/spec.py), [givingtuesday_datamart/sources/resolver.py](givingtuesday_datamart/sources/resolver.py), [givingtuesday_datamart/sources/__main__.py](givingtuesday_datamart/sources/__main__.py) — dataclass + S3 version resolver + CLI.
- [givingtuesday_datamart/ingestion.py](givingtuesday_datamart/ingestion.py) — `datamart_config()`, `ensure_meta_schema`, `ingest_source`, `ingest_latest`, `ingest_runs` lineage tracking.
- [givingtuesday_datamart/write_data_to_sql.py](givingtuesday_datamart/write_data_to_sql.py) — streaming CSV → COPY with quote-aware parsing + lineage stamping during COPY.
- [givingtuesday_datamart/validation.py](givingtuesday_datamart/validation.py) — 5 post-ingest checks.
- [givingtuesday_datamart/canonical/build.py](givingtuesday_datamart/canonical/build.py) — `build_canonical(include_people=False)`. Materializes `schedule_o_part_iii`, `nonprofit_canonical`, `nonprofit_text` (with GIN tsvector), `funder_canonical`. Person tables gated. Stamps `datamart_meta.canonical_builds`.
- [givingtuesday_datamart/grant_matching.py](givingtuesday_datamart/grant_matching.py) — recordlinkage pipeline (renamed from `matching_records_experiment.py`). Lineage-keyed S3 checkpoints, parallel resume via direct boto3, view DDL inlined as `_VIEW_DDL` + `_UNIONED_GRANTS_DDL`.
- [givingtuesday_datamart/sql_queries/non_profit_text_joins.sql](givingtuesday_datamart/sql_queries/non_profit_text_joins.sql) — *retained as historical/reference, but the canonical build's `_build_nonprofit_text` is the real implementation now*.
- ~~`givingtuesday_datamart/sql_queries/unique_fields_for_grants.sql`~~ — **deleted**; DDL lifted into `grant_matching.py` (`create_or_replace_views`, `rebuild_unioned_grants`).

Still pending:
- `givingtuesday_datamart/client/` — Python client consumed by vdl-tools (the next active priority after grant matching verification).
- Frontend `frontend/src/lib/**` — still reads from old VDL DB; cuts over after the client lands.

## Verification

**Phase 1 — ✅ done.**
- ✅ `python -m givingtuesday_datamart.sources refresh` ingests all 9 (default 7, after officers were dropped) sources end-to-end on EC2 against a clean `gt_datamart`.
- ✅ Re-running with the same source version is a no-op (idempotent via `datamart_meta.ingest_runs` check; `--force` overrides).
- ✅ Every row in every ingested table has `_source_version`, `_source_url`, `_ingested_at`, `_ingest_run_id` populated.
- ✅ `datamart_meta.ingest_runs` table tracks runs with row counts, status, and validation JSONB.
- ✅ Validation failures hard-fail the refresh; per-check warn-vs-fail policy.
- ✅ README documents the flow (`README.md`).

**Phase 2 — 🟡 canonical surfaces shipped; consumer-facing pieces pending.**
- ❌ vdl-tools `query_prepare_givingtuesday.py` cuts over to the new client + FTS — *pending the client module*.
- ✅ `nonprofit_canonical`, `funder_canonical`, `nonprofit_text` exist with the expected shape and rowcounts; lineage in `datamart_meta.canonical_builds`.
- ❌ Frontend organization profile page expanded — *pending the client module + design pass*.
- 🟡 Profile query speedup — measurable once the client + frontend cut over.

**Phase 3 — ongoing, measured per capability.**
- ✅ Grant matching pipeline ported, lineage-stamped, resumable. End-to-end run on EC2 confirmed 2026-04-29 (resume = 27s for 12,972 chunks; produced 1,098,084 unique privategrants → recipient mappings).
- 🟡 Quantitative correctness diff of `public.unioned_grants` vs. the prior `irs_filings.unioned_grants` — pending. Done when the new and old tables agree on key aggregates (granter/grantee EIN counts, total dollars by year, sample of high-volume granter EINs) within an acceptable tolerance, or any divergence is explained.
- ❌ Matching threshold regression harness — pending.
- ❌ Matching memory footprint — still requires r7a.4xlarge.
- ❌ Funder classification, attachment-grant extraction, cross-org person dedup, address sanitization — pending (cross-org person dedup blocked on `person_canonical` returning).

## Open questions for follow-up

1. **Refresh cadence.** Quarterly? Aligned to a specific IRS drop? Needs a conversation with Giving Tuesday before we commit. The plan works for any cadence but ops details (cron schedule, notification surface, EC2 sizing) depend on it.
2. **Dedicated `gt_datamart` RDS vs. shared.** The current "drop staging that isn't strictly needed" approach buys runway but doesn't solve the underlying problem. Eventually `person_canonical` or another large derived table will need to come back, and we hit the wall again. Needs a sizing + cost conversation before the next big build.
3. **Branch strategy.** `zein/raw_notes` accumulating significant work. Merge to main once grant matching verification clears and at least one downstream consumer (vdl-tools client module) starts cutting over.
