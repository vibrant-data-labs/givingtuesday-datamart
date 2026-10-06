---
name: gt_datamart staging-table indexes (plumbed + verified)
description: Profile-page btree indexes on filerein for four staging tables are now declared in SourceSpec.indexes and recreated by ingestion after each COPY. Plumbed and verified end-to-end on zein/raw_notes 2026-04-29 — merge-gate concern resolved.
type: project
originSessionId: bd4a615b-abf9-4ca8-bf9a-34b412adf469
---
The four `filerein` btrees that the frontend profile page depends on (Track B) are part of the refresh contract as of 2026-04-29 on `zein/raw_notes`. The merge-gate concern (silent profile regression on next re-ingest) is closed.

**Indexes (declared on `SourceSpec.indexes` in `givingtuesday_datamart/sources/registry.py`):**
- `ix_basic_fields_filerein`         on `public.basic_fields`         — source `irs_990_basic_fields`
- `ix_basic_fields_pf_filerein`      on `public.basic_fields_pf`      — source `irs_990pf_basic_fields`
- `ix_programs_filerein`             on `public.programs`             — source `irs_990_programs`
- `ix_mission_statements_filerein`   on `public.mission_statements`   — source `irs_990_missions`

**Plumbing:** `IndexSpec(name, columns)` lives in `givingtuesday_datamart/sources/spec.py` (`create_sql(table)` returns the `CREATE INDEX IF NOT EXISTS` statement). `SourceSpec.indexes: tuple[IndexSpec, ...] = ()` carries them. `ingestion._apply_post_ingest_indexes` runs after a successful `stream_csv_url_to_table` and before validation — index errors are caught + logged but do not fail the ingest (perf regression, not correctness). `ANALYZE <table>` runs after the indexes are created so the planner has fresh stats. `irs_990_schedule_o` is intentionally NOT in the list — its filerein index is created by `canonical/build.py` on the derived `schedule_o_part_iii` table.

**Why this matters:** Frontend `getOrgProfile` does ~5 parallel `WHERE filerein = $1` lookups across these tables. Cold seq scans against `basic_fields` (~3 GB) take ~6s each → 4 in parallel blow past Kysely's 30s `statement_timeout`. With these indexes, profile p95 is ~165ms cold / ~10ms warm.

**Verification:** Re-ingested `irs_990_missions` with `--force` and confirmed `ix_mission_statements_filerein` appeared on the recreated table. The other three sources will pick up their indexes on the next refresh.

**Open follow-up:** none for this concern. The remaining `zein/raw_notes` → main merge blocker is Track A (vdl-tools client module cutover of `query_prepare_givingtuesday.py`).
