# Organization and funder name-field audit — 2026-09-30

User wants businessname1, businessname2, and DBA returned separately for organizations and funders in the Python client and vdl-tools. This session audited local code only; no database schema/data verification or implementation was performed.

- `canonical/build.py` maps `filername1/2` to `name/name_secondary` in both canonicals. Nonprofit canonical also carries `dbanbnline11/22` as `dba_1/2`; funder canonical has no DBA columns. The current nonprofit builder includes 990-EZ, with NULL DBA fields for an EZ winner (older memory describing EZ exclusion is stale for this checkout).
- `client/models.py`: Nonprofit and CanonicalIdentity expose DBA lines; NonprofitHit, IdentityHit, and BasicFieldsRow do not. Identity searches can match nonprofit DBAs without returning them. The identity-universe funder arm explicitly emits NULL DBAs.
- `get_grants` returns granter_name and granter_name2 with canonical fallbacks, but no DBA. `get_grant_summaries` retains only primary funder names, aggregating distinct EINs and names independently; these arrays cannot safely be zipped.
- `/Users/zeintawil/dev/vdl/vdl-tools/vdl_tools/scrape_enrich/givingtuesday/query_prepare_givingtuesday.py` builds identity from the latest get_basic_fields row, renames name to Organization, explicitly drops name_secondary, and independently unions Funders and Funder_Names across years.
- Proposed additive contract: businessname1, businessname2, dba_name; preserve existing names for compatibility, derive a single DBA from the two raw DBA lines while retaining the raw lines. Resolve funder identities by EIN into structured records rather than independently aggregated arrays. Verify PF source DBA availability before proposing ingestion changes; missing DBA stays NULL and must not be inferred from name line 2.

## Implemented locally, 2026-09-30

- Client version bumped to 0.2.0. Added serialized business-name aliases and joined DBA across identity/profile/basic-field results and grant names. `GrantSummary.granters` replaces parallel public EIN/name lists. Bulk `get_funder_identities` prefers PF identity, returns null names for absent canonical records, and rejects malformed EINs.
- Coordinated edits in sibling vdl-tools, ed_tracker, and dashboard-climate-landscape. Adapter carries `granters`; ED relationship collector uses yearly summaries, retaining taxyear and all names with no attributed amounts. Cleaning unions granters and retains source_gt_eins. Catalog/profile export is EIN-based; unresolved legacy IDs are reported. Catalog, profile JSON, API, and UI retain secondary names/DBA.
- Serving org_funder now retains taxyear; the existing last_active_year SQL field includes GT years while the UI says Last Funded. Distinct portfolios and known amounts remain unaffected. Added staging parity checks and a schema-parameterized frontend release checker.
- User explicitly chose: prepare/test code now; run full refresh separately. No S3 regeneration, staging build, profile publication, serving configuration switch, or commits were performed in this implementation session.
- Bounded live checks used read-only credentials: client basic fields, funder identities, forced nonprofit hits, summaries, and grants succeeded. Actual funder-serving SELECT tested against CTE fixtures: repeated years did not inflate a unique portfolio or attributed amount; an unrelated 2026 recipient event did not override the funder's 2024 latest grant year.
- Updated run-order documentation in ed_tracker_ph1/dashboard_v2/FUNDER_PROFILES.md. Important next-run requirement: GT cleaned-data URI is shared across project versions; choose a new URI as well as new results/schema names to preserve rollback.

## PRs opened, 2026-09-30

User requested separate latest-main branches for the three downstream repos,
then requested PRs. Created and pushed:

- givingtuesday-datamart #59, zecodex/dba_name_1_2, commit 1ee5ff0.
- vdl-tools #216, zecodex/structured-granters, commit 9d60687.
- ed_tracker #47, zecodex/structured-granters, commit 24238c0.
- dashboard-climate-landscape #341, zecodex/structured-granters, commit ee36cbf.

PR descriptions link the coordinated changes. No merges or refresh/publication
performed. Branch transfer retained backup stashes. Imported memory files and
unrelated untracked files were not staged into these commits.

## Review fixes, 2026-09-30

Four independent Bugbot-style reviews found one P3: organization table tooltip
metadata was not forwarded. Fixed in dashboard PR #341 commit 5fa2444, with
legacy/canonical and explicit/fallback column regression tests; 312 dashboard
tests and TypeScript passed.

VDL CI initially failed importing FunderIdentity because its unpinned Git
dependency still installed datamart main before #59 merged. Fixed PR #216 in
1966ef0 by pinning datamart to 1ee5ff08642ce2ddbee6b9cdc78a49e52038b4a8.
All 7 vdl-tools tests passed with a freshly installed wheel from that exact
remote commit in /tmp, independent of the sibling checkout. Both pushed fixes
triggered new GitHub checks; those checks were pending at the last observation.

## Avoid duplicate summary fetch, 2026-09-30

User's preparation log showed one 78,982-EIN query (400,490 yearly rows), then
another pass in 500-recipient batches. Fixed vdl-tools #216 (0ee7d1d) to expose
`on_grant_summaries`, called once with summaries restricted to returned recipients.
ed_tracker #47 (602eaf4) writes the yearly artifact from that callback through
`relationships_from_summaries`, without querying again. Standalone collector remains
available. Both changes pushed; 24 focused tests passed including exclusion, empty
results, yearly relationship equivalence and parquet nulls. Update both consumers
together. Existing running Python processes keep the old implementation. User's
local vdl-tools __main__ test-path edits and ed_tracker paths.ini were preserved
and excluded from commits. No refresh was started by the agent.
