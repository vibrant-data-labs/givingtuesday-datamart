---
name: basic-fields-dedup-34
description: "Issue #34 basic_fields filing-version dedup: COMPLETE. PRs #35/#38/#39 all merged, _current tables built. Amend flag is NULL not '' — sort with IS NOT DISTINCT FROM; refresh owns the _current rebuild, canonical only guards."
metadata: 
  node_type: memory
  type: project
  originSessionId: bdd54ead-eeb4-4991-a693-1320364fb872
  modified: 2026-08-04T23:44:44.937Z
---

The basic_fields analog of [[sched-i-amended-return-duplication]] (#33): client
summed filing versions per (filerein, taxyear) → contributions inflated
$310.3B / 6.4% (2018+; 101,543 multi-version filer-years).

**Phase 1 MERGED 2026-08-05 — PR #35 → main as ac9b567** (squash; branch
`basic-fields-dedup-client`). Issue #34 stays OPEN for Phase 2. Inline
`DISTINCT ON (filerein, taxyear)` at 3 client.py sites (contrib_eligible CTE,
get_basic_fields, find_eins_with_min_avg_contributions), ordering
`(amendereturn IS NOT DISTINCT FROM 'X') DESC, url DESC, filesha256`.
Validated vs live DB: 5/5 amended spot-checks, eligibility delta at $1M
threshold = −2,871 EINs (74,021→71,155). get_basic_fields now one row per
filer-year (semantic change, no other in-repo consumers).

**Value-present fallback (b5395cb, Zein's call 2026-08-05):** version
selection orders `(NULLIF(col,'') IS NOT NULL) DESC` BEFORE the amend flag,
so a version that doesn't report the metric never wins. Investigating first
disproved the "blank extract" premise — 0/247 winners are blank-extraction
rows; the 247 split into 140 amendment-omits-the-breakdown (same taxperend,
revenue restated) + 107 period-split stubs. Row-level selection, NOT
per-column COALESCE: a composite would blend two accounting periods in the
split-period cases. Result: eligibility delta became monotone (added=0).

**Open, unfiled:** two accounting periods sharing one `taxyear` label —
100 multi-version filer-years (2018+) where the winner reports revenue 0
while another version reports real revenue, 373 where the winner is <10% of
the best. Filer-year is the wrong grain for those orgs; version-selection
only papers over it.

**Traps found (Phase 2 DDL must respect):**
- `amendereturn` is **NULL, not ''**, on non-amended filings. `(amendereturn =
  'X') DESC` sorts NULL first → prefers the ORIGINAL. Use `IS NOT DISTINCT
  FROM 'X'` (current_grants.py is immune via COALESCE(BOOL_OR); issue #34's
  own repro query has the bug).
- ~42K rows duplicated within the same filesha256; 23,365 filer-years have
  multiple shas under ONE url → filesha256 determinism tiebreak required.
- 3,054/80,966 multi-sha years have >1 taxperend (possible legitimate
  short-period pairs) — rule collapses them anyway, consistent with #33.

**Phase 2 DONE 2026-08-05 — PR #38** (`basic-fields-current`, d6ac088),
tables BUILT on the datamart: `basic_fields_current` 3,813,736 rows (4.9 min),
`basic_fields_pf_current` 1,158,259 (6.7 min). DDL uses `b.*` (168/141 cols)
+ UNIQUE (filerein, taxyear) index as a build-time grain assertion. PF gets
NO value-present key (measured 0/26,047 blank). Parity vs Phase 1 inline rule
exact everywhere; $4,836.5B is $1.49B ABOVE issue #34's stated $4,835.0B
because the issue's own repro had the amend-NULL bug and no value-present key.
Frontend was showing 2.5-3.7x inflated revenue (JAR OF HOPE $12.3M→$5.0M,
BELLE PLAINE $25.8M→$7.0M). DAF filter 6,061→6,048 EINs (13 orgs whose DAF
flag lives only on a superseded version). Matcher untouched → no
MATCHING_INPUT_SHAPE_VERSION bump, no gate rerun; matching runs +11.5 min.

**Trap Phase 2 hit + Zein's correction (684d91b):** `_current` are snapshots,
so a staging reload leaves them stale. First fix rebuilt them in
`build_canonical`; Zein asked "why not step 2?" and was right — canonical
isn't the only reader, so client+frontend served the old drop between refresh
and canonical. Now `sources refresh` rebuilds them, gated on which source
reloaded; canonical only GUARDS (compares `_ingest_run_id` staging vs
`_current`, rebuilds on mismatch). Only the 2 basic-fields relations: grants
`_current` are read ONLY by grant_matching, which rebuilds them itself.
Also: raw basic_fields[_pf] removed from the frontend Kysely schema so
per-version reads are a type error.

**Decided WON'T FIX (Zein, 2026-08-05):** `_build_one` does DROP+CTAS in one
transaction, so readers BLOCK (ACCESS EXCLUSIVE) for the 5-7 min rebuild —
the frontend now reads these, so a refresh stalls site queries. Zein: "it's
fine". Acceptable because refresh is infrequent and blocking beats the status
quo (staging commits DROP+CREATE before streaming rows, so today the site
serves empty/partial data mid-refresh). Don't re-raise. If it ever does need
fixing: build-then-swap (build `_new`, index, then DROP + ALTER RENAME in one
txn), which would touch the shared grants build path too.

**Original Phase 2 plan (superseded by the above):** add
`basic_fields_current` + `basic_fields_pf_current` to current_grants.py
(provenance: n_filings_for_year, dedup_rule passthrough|latest_filing);
simplify Phase 1 client queries to plain reads — Phase 1 inline SQL is the
byte-parity oracle; frontend cutover (fetchOrgFromTable SUM double-counts,
fetchRevenueHistory/Details emit dup years; fetchFirstYears/daf_eligible
already safe); point canonical builds at _current (5,315 EINs ambiguous
winner today, 619 with differing identity fields); leave matcher universe
views on raw staging → no MATCHING_INPUT_SHAPE_VERSION bump, document in #34.
