---
name: Datamart → internal backbone — active plan
description: Multi-phase plan to convert the Giving Tuesday Datamart prototype into a production-grade internal data backbone. MIGRATION COMPLETE — merged to main via PR #7; Track A (vdl-tools cutover) and Track B (frontend) both done. Remaining items are deferred backlog, not blockers.
type: project
originSessionId: 7b6fb6f8-76da-436c-8045-62704cc32415
modified: 2026-07-22T21:38:31.041Z
---
## STATUS UPDATE (2026-07-22): migration complete

Verified against the repo on 2026-07-22 — everything below this block is historical context, not live state:

- `zein/raw_notes` **merged to main** via PR #7 ("Cutover to Productionized GT Datamart Ingest, Search Client, Frontend"). The branch no longer exists.
- **Track A done:** vdl-tools' `query_prepare_givingtuesday.py` imports `GtDatamartClient` from `givingtuesday_datamart.client` and pins to `gt_datamart`.
- **Track B done** (shipped pre-merge). Staging indexes plumbed + verified (see [[staging-index-todo]]).
- PR #18 **removed the vdl-tools dependency from this repo** (vendored logger/config/db/address-cleaning) — the datamart is standalone now.
- Post-merge work on main is feature development on the client/frontend: eligibility filters, DAF indicator, EIN search, server-side grant summaries, nonprofit_text dedup (#14–#29).
- `docs/backbone-plan.md` is stale (still says "not yet merged to main", dated 2026-04-29).
- Still-deferred backlog (unchanged): person_canonical/org_person_role, officers re-ingest, schedule_o staging drop, 990-PF narrative FTS coverage, column typing.

---

The full plan lives **in the repo** at `docs/backbone-plan.md` (copied from `~/.claude/plans/i-have-this-initial-silly-shell.md` on 2026-04-24). Read it first when resuming — phased scope, architecture decisions, verification criteria.

## Strategic framing (approved 2026-04-24)

- **Bet:** internal data backbone, ongoing program
- **Primary consumers:** vdl-tools pipelines + VDL analysts (architecture must not foreclose external product)
- **Primary store:** PostgreSQL — **dedicated `gt_datamart` database** on the shared VDL RDS host (not the default VDL DB). Blast-radius isolation, clean drop-whole-thing-fast semantics during Phase 1, granular backup/restore. Threaded via `get_session(config=datamart_config())` — no parallel connection utility.
- **Fuzzy matching:** keep `recordlinkage` (deliberate choice, not Splink)
- **Historical migration:** none — re-ingest from scratch on new schema
- **Where it runs:** EC2 (sized for ingestion + matching)
- **Staging schema:** everything in `public.*` (not `irs_filings.*`) since the whole DB is already isolated for this work.

## Current state (2026-04-29)

**Branch:** `zein/raw_notes` — head `79a8862` (Track B fast-forwarded in 2026-04-29). **Not yet merged to main.** Verification gates: grant matching diff cleared 2026-04-29 ("they look good"); Track B frontend parity diff cleared 2026-04-29 (6 EINs, 4 byte-identical, 2 with fresher gt_datamart data — strict superset, no regression).

**Merge posture (updated 2026-04-29):** Track B frontend cutover ✅ shipped to `zein/raw_notes`. Two open blockers before `zein/raw_notes` → main:
1. **Track A (vdl-tools client module)** — `query_prepare_givingtuesday.py` cutover.
2. **Staging-table index plumbing** — Track B applied four `CREATE INDEX … (filerein)` out-of-band on gt_datamart for perf; not yet captured in `SourceSpec`. Next re-ingest will drop them and silently regress the profile page. See `staging_index_todo.md`.

Track A branches off `zein/raw_notes` (current head `79a8862`).

What "full parity" means concretely:
- **vdl-tools consumer:** `vdl_tools/scrape_enrich/givingtuesday/query_prepare_givingtuesday.py` cuts over from the old VDL DB to the new client module. Verify by running it against a representative keyword set on both old and new and confirming the EIN sets + downstream pipeline outputs are equivalent (modulo expected FTS-vs-in-memory-keyword-search differences, which should be small; flag any large divergence).
- **Frontend:** Profile pages cut over from old VDL DB to `gt_datamart`. At minimum the post-cutover page must surface everything the pre-cutover page surfaced (the expansion to richer 990 sections is upside on top of parity).
- **Grant matching outputs:** ✅ already cleared (diff on `unioned_grants` 2026-04-29).

**Measured perf wins (2026-04-29):**
- Grant matching resume from S3: **27 seconds** for 12,972 chunks at 472 it/s with 31 workers (direct-boto3 fast reader). Prior fsspec path was 4.35 it/s (would have been ~50 min). Per-chunk effective ~2 ms with 31-way parallelism on EC2.
- Matching outputs at sources `pg_2025_10_28` / `bf_2025_10_18`: 648M ZIP-blocked candidate pairs → 1,993,316 raw matches (post-stage-1) → 1,098,084 unique privategrants → recipient mappings (post-multi-match resolution). `public.unioned_grants` UNION-ed with 990 Schedule I grants from `public.grants_to_domestic_organizations`.

**Recent commits (newest first):**
- `7ddd3f2` — Update backbone plan to reflect current state (docs/backbone-plan.md sync)
- `f51a4cf` — Replace fsspec parquet reader on resume hot-path with direct boto3 (sized connection pool, preserves vdl_json_columns decoding contract; ~80x per-chunk speedup measured 2026-04-29: 27s resume vs. ~50min projected on fsspec path)
- `57bd606` — Parallelize grant matching chunk resume + expose chunk_size CLI flag (one S3 LIST replaces N HEADs; ThreadPoolExecutor)
- `cc8291c` — Add --limit test mode to grant matching (namespaces test chunks under `_test_limit_<N>/` to avoid polluting production)
- `56fdec7` — Preserve recordlinkage MultiIndex through parquet checkpoints (write index columns explicitly; chunks dropped MultiIndex on write before this)
- `767b596` — Fix grant matching post-processing KeyError on chunk-resumed runs
- `71fb3fa` — Skip officers from default refresh (skip_default_refresh=True on registry; preserves explicit re-ingest path)
- `7afd82c` — Make grant matching input row order deterministic (ORDER BY in views + read queries)
- `1d55262` — Nest grant matching checkpoint prefix by source version (`pg_<v>/bf_<v>/`)
- `f7ea4aa` — Stamp grant matching builds in `datamart_meta.canonical_builds`
- `738a953` — Key grant matching S3 checkpoint prefix on source-data lineage
- `c9fd658` — Port grant matching pipeline to gt_datamart
- `4263c4a` — Make person_canonical + org_person_role opt-in via --with-people
- `3822733` — Add Phase 2 canonical layer
- `6f6897f` — Add post-ingest validation step

**Phase 1 scorecard (COMPLETE except intentional deferral):**
- ✅ Source registry (9 sources; `SourceSpec` + `ColumnSpec` in `givingtuesday_datamart/sources/`)
- ✅ Officers tables as first-class (both `officers` and `officers_pf`)
- ✅ Lineage columns on every row (`_source_version`, `_source_url`, `_ingested_at`, `_ingest_run_id`) stamped during COPY, no second pass
- ✅ Idempotent ingestion (`datamart_meta.ingest_runs` app-level check on `(logical_name, source_version)`; `--force` to override)
- ✅ Single entrypoint (`python -m givingtuesday_datamart.sources refresh [--source NAME] [--force]`)
- ✅ Observability (`datamart_meta.ingest_runs` table; `status` CLI for S3 freshness; `loaded` CLI for DB state)
- ✅ Streaming-only ingest (no disk cache, no pandas in hot path) with quote-aware parsing so multi-line mission statements / Schedule O narratives survive
- ✅ `gt_datamart` database isolation (via `datamart_config()` override to `get_session`)
- ✅ All-TEXT staging (preserves zero-padded EINs/ZIPs/phones; consumers cast at query time)
- ✅ End-to-end refresh on EC2 (all 9 sources successfully ingested; confirmed 2026-04-24)
- ✅ Validation step (5 checks: row_count_positive, lineage_columns, required_columns, schema_drift, row_count_delta; hard-fail vs warn; results stored in `ingest_runs.validation` JSONB; surfaced in `loaded`)
- ✅ README documenting the whole flow
- ❌ Typed schemas per `ColumnSpec` — **intentionally deferred** (all-TEXT works; typing hundreds of cryptic IRS columns needs domain knowledge). Not a blocker.
- ❌ Versioned snapshots + `_current` views — **intentionally skipped** (overwrite-on-success + validation provides the safety net; revisit only if we hit a real need for rollback).

**Validation JSONB state note:** only `irs_990_missions` has populated `column_list` + `validation` on `ingest_runs` so far (the `--force` verification run). The other 8 sources ingested pre-validation and have NULL in those columns. They'll populate naturally on their next real refresh. Do NOT re-run `--force` just to backfill — Zein ruled this out (too expensive).

**Phase 2 (query surfaces) — not started.**
**Phase 3 (matching pipeline, classification, person entity dedup) — not started.**

## Phase 2 first-milestone scorecard (as of 2026-04-24)

**Tables live on gt_datamart (build `be14378f` completed):**
- ✅ `public.schedule_o_part_iii` (964,637 rows) — Part III continuations filtered from 29M raw schedule_o via ILIKE 'FORM 990…PART III…' (handles roman + arabic numerals, bare-990 variants, excludes SCHEDULE X PART III)
- ✅ `public.nonprofit_canonical` (465,124 rows) — DISTINCT ON (ein) from basic_fields, ordered by taxyear → taxperend → ingested_at. PK on ein.
- ✅ `public.nonprofit_text` (510,998 rows) — FTS surface with tsvector + GIN index. Includes 46K 990-EZ EINs not in basic_fields (verified: 100% of sample had `FORM 990-EZ, PART III` in sidfalrdesc).
- ✅ `public.funder_canonical` (156,509 rows) — DISTINCT ON (ein) from basic_fields_pf. Funder type/classification deferred to Phase 3.
- 🛑 `public.person_canonical` — **indefinitely deferred** (2026-04-27). Was opt-in via `--with-people`; with officers staging dropped, can't be built without re-ingest. No active downstream consumer.
- 🛑 `public.org_person_role` — same status as person_canonical.
- ✅ `datamart_meta.canonical_builds` lineage table tracks ingest_run_ids that fed each build (and grant_matching builds, via build_kind discriminator).

**FTS perf measured:** "climate & renewable" query ≈ 651ms total, GIN index scan 0.8ms, 918 matches. Replaces vdl-tools' in-memory keyword search pattern.

**Known gaps flagged during verification:**
- Org names + DBAs not in nonprofit_text — likely why climate/renewable numbers felt low to Zein. Easy to add.
- 990-PF narratives not in any text table — ~156K funders have no FTS coverage.
- nonprofit_canonical excludes the 46K 990-EZ EINs → FTS hit on those returns NULL identity.

## RDS storage situation (2026-04-27)

Shared RDS instance is the binding constraint. gt_datamart hit 68 GB and the grant matching final JOIN DiskFull'd on `pgsql_tmp` despite the same query succeeding 2 days earlier on the same DB. Largest tables before cleanup:

| table | size | status |
|---|---|---|
| `public.officers` | 18 GB | **DROPPED** 2026-04-27 to free space |
| `public.schedule_o` | 17 GB | **drop after verifying schedule_o_part_iii + nonprofit_text don't depend on it** |
| `public.privategrants` | 8 GB | required (matching reads it) |
| `public.unioned_grants` | 4.4 GB | required (downstream consumer-facing) |
| `public.grants_to_domestic_organizations` | 3.6 GB | required (matching feeds unioned_grants) |
| `public.privategrants_w_recipients` | 3.6 GB | required (matching output) |
| `public.basic_fields` | 3.1 GB | required |
| `public.programs` | 3.0 GB | required (nonprofit_text source) |
| `public.officers_pf` | 2.0 GB | **DROPPED** 2026-04-27 (paired with officers) |
| `public.mission_statements` | 1.9 GB | required (nonprofit_text source) |
| `public.nonprofit_text` | 1.7 GB | required (FTS surface) |

**Decisions driven by this (2026-04-27):**

- **Person canonical layer (`person_canonical` + `org_person_role`) — indefinitely on hold.** Was already gated behind `--with-people`; with officers staging dropped, `--with-people` builds will fail at the `_build_person_canonical` step until officers is re-ingested. Re-ingest is intentionally not on the near-term roadmap. No active downstream consumer needs people-level data; revisit only when one does.
- **Officers staging tables dropped.** Frees 20 GB. Lineage rows in `datamart_meta.ingest_runs` for `irs_990_officers` and `irs_990pf_officers` left in place (they document the historical ingest); future canonical builds with `--with-people` will need a fresh `python -m givingtuesday_datamart.sources refresh --source irs_990_officers` (and `irs_990pf_officers`) to reconstitute the staging.
- **Schedule O staging — next drop candidate.** TODO: drop `public.schedule_o` (17 GB) once verified that `public.schedule_o_part_iii` (already built, 872 MB) and `public.nonprofit_text` (already built, 1.7 GB) cover all current consumers. Schedule O is staging-only — once the part-III filter has run and FTS is materialized, the raw 29M rows are dead weight. Same lineage-row preserve pattern as officers.

These are tactical drops, not strategic deletions. Pre-processing approach for when officers needs to come back (and same playbook for any future big-staging table):
1. Year filter at ingest (`taxyear >= 2015` cuts long pre-2015 tail; matching pipeline already filters there).
2. Column pruning post-ingest (raw IRS rows have 50+ TEXT columns; canonical layer uses ~10).
3. Stream source CSV → canonical shape directly, no staging table at all (the right shape long-term).

## Two parallel tracks (set 2026-04-29 after the diff cleared)

Zein wants to run these concurrently in separate sessions. For current Codex work, use managed worktrees and the frontend preview instructions in MEMORY.md. Both tracks read from the same canonical SQL surface — coordinate via the schema, not via shared code (the languages diverge; Python client vs. Next.js API routes).

### Track A — vdl-tools client module

**Goal:** Python API in `givingtuesday_datamart/client/` so `vdl_tools.scrape_enrich.givingtuesday.query_prepare_givingtuesday` stops loading the joined-text table into memory.

- Methods (minimum viable):
  - `search_nonprofits(keywords: list[str], filters: dict | None = None) -> list[NonprofitHit]` — runs Postgres FTS over `public.nonprofit_text.text_tsv` with `to_tsquery('english', ...)`, default OR semantics + `ts_rank` ordering. Returns EIN + canonical name/address from `nonprofit_canonical` joined in.
  - `get_nonprofit(ein: str) -> Nonprofit | None` — joins `nonprofit_canonical` + relevant `basic_fields` + `nonprofit_text.unique_text` (when consumer wants full text).
  - `get_grants(ein: str, role: Literal['granter','grantee'] = 'grantee') -> list[Grant]` — reads from `public.unioned_grants` indexed on granter_ein/grantee_ein.
- Pattern: use `get_session(config=datamart_config())` for connections, parameterized SQL via `text()`, dataclasses (or pydantic) for return types so consumers get typed results.
- **The whole point is to hide SQL from `vdl_tools` consumers.** Once this exists, schema changes are bounded.
- Cut over `vdl_tools/scrape_enrich/givingtuesday/query_prepare_givingtuesday.py` as the proof-of-life consumer.

Suggested branch: `zein/client-module` off main (after merge) or off `zein/raw_notes`. Touches only `givingtuesday_datamart/client/*` + a vdl-tools cutover, so won't conflict with Track B.

### Track B — Frontend cutover + organization profile expansion

**Status (2026-04-29):** ✅ Three commits merged to `zein/raw_notes` head (fast-forward, 2026-04-29):
- `0baa2fd` — pure cutover (irs_filings.* → public.* on gt_datamart). Profile + grants verified byte-identical or superset on 6 EINs. Search rewritten as a tiered hybrid: ILIKE on canonical names + DBAs (rank tier 10⁶), exact-EIN match (2·10⁶), FTS over `nonprofit_text.text_tsv` via `websearch_to_tsquery('english', q)` with raw `ts_rank_cd` score. Tier separation guarantees name matches sort above narrative-only matches; the unbounded ts_rank_cd was the bug we hit on the first naive `1.0` boost (Yale University ranked at position 13 because narrative-heavy orgs scored higher than 1.0).
- `3b3f958` — expansion. New `OrgIdentityCard` (DBAs / care_of / formation year / country / website), website link in `OrgHeader`, three-section `OrgNarrative` (Mission / Program activities / Schedule O Part III, all collapsible, "Show N earlier filings" + per-entry "Read more"), `LineageFooter` showing `source_version` + 8-char `source_run_id` prefix.
- `26fb8c7` — search mode toggle (`name` / `narrative` / `both`, default `both`) on the home page via a `SearchModeToggle` segmented control + `mode` URL query param. Instructions block describes the FTS boolean syntax (AND default, `OR`, leading `-` for negation, `"phrase quotes"`, plus stemming and stop-word semantics from `english` config). Each claim verified empirically before shipping. `name_hits` and `fts_hits` CTEs gate on the mode-derived boolean; `ein_hits` always runs (paste-an-EIN should resolve regardless of mode).
- `79a8862` — backbone-plan doc updates marking Track B complete + flagging the staging-index plumbing as a merge-gate blocker.

**Out-of-band DDL applied to gt_datamart (NOT yet plumbed into Python — TODO):**
```sql
CREATE INDEX IF NOT EXISTS ix_basic_fields_filerein         ON public.basic_fields (filerein);
CREATE INDEX IF NOT EXISTS ix_basic_fields_pf_filerein      ON public.basic_fields_pf (filerein);
CREATE INDEX IF NOT EXISTS ix_programs_filerein             ON public.programs (filerein);
CREATE INDEX IF NOT EXISTS ix_mission_statements_filerein   ON public.mission_statements (filerein);
```
Without these the profile times out (basic_fields cold seq scan = 6s × 4 parallel scans = >30s). Already-indexed: `nonprofit_canonical.ein` (PK), `nonprofit_text.ein` (PK), `funder_canonical.ein` (PK), `schedule_o_part_iii(filerein)` via `ix_schedule_o_part_iii_ein`. **Future re-ingest of any of those four staging tables will drop the table + lose the index.** Plumbing target: either `givingtuesday_datamart/sources/ingestion.py` (add `CREATE INDEX` after COPY) or `givingtuesday_datamart/sources/registry.py` (add an `indexes: tuple[str, ...]` field to `SourceSpec`). The latter is cleaner.

Measured profile p95 with indexes: ~165ms cold first hit on a brand-new EIN, ~10ms warm. Foundation Center (rich filings) renders in ~96ms warm.

Files modified:
- `frontend/src/lib/db.ts` — `Database` interface modeling `public.*` tables
- `frontend/src/lib/queries/orgs.ts` — canonical identity + narrative + lineage fetchers
- `frontend/src/lib/queries/grants.ts` — schema repoint only
- `frontend/src/lib/queries/search.ts` — tiered hybrid (ILIKE + FTS)
- `frontend/src/types/org.ts` — extended `OrgProfile` with `dba1/2`, `careOf`, `country`, `website`, `formationYear`, `narrative`, `lineage`
- `frontend/src/components/org/OrgHeader.tsx` — website inline
- `frontend/src/components/org/OrgIdentityCard.tsx` — new
- `frontend/src/components/org/OrgNarrative.tsx` — new
- `frontend/src/components/org/LineageFooter.tsx` — new
- `frontend/src/app/orgs/[ein]/OrgPageClient.tsx` — new sections wired
- `.claude/worktrees/profile-expansion/.claude/launch.json` — port 3001
- `.claude/worktrees/profile-expansion/frontend/.env.local` — `PG_DATABASE=gt_datamart` (gitignored)

Funders have no narrative surface (`nonprofit_text` is 990-only). `OrgNarrative` returns null when bundle is empty; `OrgIdentityCard` hides itself when there's no extra identity to show — clean degradation for 990-PFs.

People section still deferred (gated on `person_canonical`).

Search compromise: `firstYear`/`lastYear` in search rows both come from canonical `latest_taxyear` to avoid a cold MIN/MAX scan over `basic_fields` per match. The profile page still shows the true year range.

**Next-up follow-ups:**
- 🔴 **Plumb the four index DDL statements into the Python ingestion module** — see `staging_index_todo.md` memory file. This is a **merge-gate blocker** (Track B perf depends on indexes that the next re-ingest will silently drop). Cleanest target: add `indexes: tuple[str, ...] = ()` to `SourceSpec` in `givingtuesday_datamart/sources/registry.py` and emit `CREATE INDEX IF NOT EXISTS` after the COPY in `givingtuesday_datamart/sources/ingestion.py`.
- Add org names + DBAs to `nonprofit_text` so name search has FTS recall for them too (currently only narrative columns are tsvectored).
- 990-PF narrative coverage (no funder_text yet) — narrative-mode search returns nothing for foundations.
- Restore true MIN/MAX `firstYear`/`lastYear` in search rows once the index is plumbed (it's now cheap; profile already shows the true range).

### Branching

Track B is now on `zein/raw_notes` head (`79a8862`). Track A branches off the same head — touches `givingtuesday_datamart/client/*` plus a vdl-tools cutover, no expected conflicts with Track B's frontend changes. The staging-index plumbing should land on `zein/raw_notes` directly (small commit on a quick branch, or piggyback on Track A) before merging to main.

## Backlog (not active, ordered by likely-next pull)

- **Drop `public.schedule_o` raw staging** — 17 GB recovery, same playbook as officers (drop staging, leave `ingest_runs` lineage row, re-ingest only when needed). No urgency now that diff passed; do when next opportunistic.
- **Column typing from Google Sheets dictionary** — 9 dictionary CSVs at `/tmp/dict_*.csv`. Sheet has only (column_name, description) so inference is keyword-based. Populate `SourceSpec.columns` with `ColumnSpec(pg_type, description)`. Probably a sub-module (`sources/dictionaries/<source>.py`) to keep `registry.py` readable.
- **Add 990-PF narratives to the text surface** — funders currently have zero FTS coverage. Would require identifying the narrative columns in `basic_fields_pf` (e.g., `streacsiasir*` per the sheet) and either a new `funder_text` table or extension of `nonprofit_text`'s contract.
- **Person canonical layer (`person_canonical`, `org_person_role`)** — indefinitely deferred (RDS sizing). Officers staging dropped, no consumer pull. Revisit when a use case appears.
- **Pre-process officers ingest for re-introduction** — year filter + column prune + (eventually) stream-direct-to-canonical. Only relevant once person_canonical is back on the roadmap.
- Org names + DBAs in nonprofit_text (recall improvement)
- 990-EZ filers in nonprofit_canonical (identity for the 46K)
- Regression harness for grant matching threshold tuning (the 6 thresholds in `filter_match_rules`)
- Memory/streaming optimization to drop the r7a.4xlarge (128 GiB-class) dependency for matching — dependency got FIRMER 2026-07-22: PR #30 (PF filers in universe + blank-name prune + cross-zip name block) grows candidate pairs 778M → ~1.0B (+24%); Zein runs matching on r7i/r7a.4xlarge
- Phase 3 items: funder classification, attachment-grant extraction, cross-org person dedup, canonical name sanitization

**Branch posture**: matching diff + Track B parity diff cleared. Full-parity merge gate is half-open: only Track A (vdl-tools client cutover) and the staging-index plumbing remain before `zein/raw_notes` → main.

## Open items / context

- **Column typing from Google Sheets dictionary**: Zein wants to populate SourceSpec.columns with inferred pg_types from the per-source description tabs. In progress — have the 9 dictionary CSVs downloaded to /tmp/dict_*.csv (mission/programs/schedule_o/officers/etc.). The dictionary has only (name, description) — no explicit type column — so inference from keywords in the description is the approach. Waiting on confirmation of scope before mechanical generation.
- **`text_tsv` stemming is working** — FTS hits > ILIKE hits (918 vs 607 for climate+renewable) because English stemmer matches climat*, renew* variants.

## How to resume

**Track A (vdl-tools client module) — pending; own session + worktree:**
```bash
cd /Users/zeintawil/dev/vdl/givingtuesday-datamart
# zein/client-module worktree already exists at .claude/worktrees/client-module/
# but its branch points at the pre-Track-B head (b4d7310). Rebase first:
git -C .claude/worktrees/client-module rebase zein/raw_notes
~/.pyenv/versions/3.12.11/envs/givingtuesday/bin/python -m givingtuesday_datamart.sources loaded  # sanity-check DB state
# Build out givingtuesday_datamart/client/ — see "Track A" in this memory
# Parity check: query_prepare_givingtuesday.py before/after cutover should produce equivalent EIN sets
```

**Staging-index plumbing — small follow-up:**
```bash
# Either piggyback on Track A or do a focused branch off zein/raw_notes.
# Touch: givingtuesday_datamart/sources/spec.py (add indexes field to SourceSpec),
#        givingtuesday_datamart/sources/registry.py (add CREATE INDEX statements
#        to the four sources: irs_990_basic_fields, irs_990pf_basic_fields,
#        irs_990_programs, irs_990_missions),
#        givingtuesday_datamart/ingestion.py (emit CREATE INDEX IF NOT EXISTS
#        + ANALYZE after the COPY).
# Verify: drop one of the staging tables, re-ingest --source <NAME>, confirm
#         the index reappears (\d public.<table> in psql).
```

**Track B (frontend) — done.** Worktree at `.claude/worktrees/profile-expansion/` is on `zein/profile-expansion` (= `zein/raw_notes` head). Safe to keep or delete.

**Common verification head** (whichever branch you're on):
```bash
git log --oneline -5
# Expected zein/raw_notes head: 79a8862 Update backbone plan to reflect Track B shipping
```

Use the `givingtuesday` pyenv (see `python_env.md`): `~/.pyenv/versions/3.12.11/envs/givingtuesday/bin/python`.

Check ingest state (on EC2, or from anywhere with `gt_datamart` DB creds in VDL config):
```sql
SELECT logical_name, source_version, status, row_count,
       finished_at - started_at AS duration
FROM datamart_meta.ingest_runs
ORDER BY started_at DESC;
```

**Don't redesign** the sources module or the ingestion path — they are stable. The boto3-unsigned S3 listing pattern is the convention. Quote-aware CSV parsing (`csv.reader` over `TextIOWrapper(newline="")`) + iter_content-based streaming is deliberate — documented in `write_data_to_sql.py`.
