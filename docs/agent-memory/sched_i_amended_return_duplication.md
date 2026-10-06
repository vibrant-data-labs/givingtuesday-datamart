---
name: sched-i-amended-return-duplication
description: "GT grants_to_domestic_organizations carries BOTH original and amended filings' Schedule I per filer-year; dedup by (filerein, taxyear) alone double-counts ~$36B (2020+)."
metadata: 
  node_type: memory
  type: project
  originSessionId: 554a62f2-47f2-4142-9152-d1db822f18c9
  modified: 2026-08-03T20:06:38.743Z
---

Confirmed 2026-07-30 (source version 2026_06_25, run 909a3f30): GT's Schedule I
extract emits line items for **every filing version** of a filer-year. Our
ingestion COPY is faithful (ingest_runs row_count == COUNT(*) == 8,991,013;
single run id) — the duplication is in GT's CSV, not our pipeline.

Three shapes in `grants_to_domestic_organizations` (2020+):
- **multi_url** (3,084 filer-years, $30.0B raw): original + amended blocks each
  under their own `url`. Guard: keep one url per (filerein, taxyear).
- **same_url_verbatim_2x** (634 filer-years, $18.1B): identical block twice
  under ONE url. Guard: DISTINCT on line-item tuple.
- **same_url_partial_dup**: Fidelity 110303001/2021 is the ONLY confirmed case
  (2020+, >=40 rows): original + amended blocks BOTH mislabeled with the
  original's url + filesha256. Verified against raw IRS XMLs: half 1 ==
  original 202321309349304807, half 2 == amended 202430459349302913 (exact
  (EIN, cash) multiset match). DISTINCT only partially fixes; residual ~13%
  inflation. The other ~62 partial-dup candidates have NO amendment in
  basic_fields and cash/declared ratio ~1.0 — they are LEGITIMATE repeated
  line items (e.g. 311339322/2021: 20 separate $45,252 grants to YMCA of
  Greater Dayton), so blanket DISTINCT under-counts them 5-25% (conservative
  direction for a priority list: they surface for review, not hide).

Key facts:
- basic_fields has 80,966 filer-years with >1 filesha256 (amended pairs);
  `amendereturn = 'X'` marks the amended filing.
- MAX(url) picks the amended filing in only ~73% of pairs (16% inverted), but
  version choice barely moves totals (Fidelity versions differ 0.04%).
- Guard effect on [[gt-column-decodes]]-style consumers summing by filer-year:
  raw $729.3B → $693.3B. DISTINCT collateral in clean filer-years is only
  $2.4B (0.37%), so latest-url + DISTINCT is safe for rankings.
- Any consumer that GROUPs this table by (filerein, taxyear) without a version
  guard overstates; sched_i_capture_priority.sql masked $2.31B of unmappable
  dollars and misclassified 1,500 filer-years. **Guard applied to that file
  2026-07-30** (latest-url + line-item DISTINCT in `itemized`, url DESC
  tie-break in `declared`) and verified against the RO DB — Fidelity
  110303001/2021 itemized $22.5B → $12.54B.
- Verbatim-2x doubles are amendment-linked 377/378 (basic_fields >=2 shas), so
  the detector for version-doubles is: dup mass under one url AND an amendment
  exists in basic_fields AND raw cash >> declared. Legit repeats fail the
  latter two.
- `privategrants` (990-PF side) confirmed affected: 10,772 multi-url
  filer-years (biggest: 462626883/2023, 363,676 rows) — pf_capture_priority.sql
  needs the same latest-url guard.
- **PF verbatim scan (2026-07-30): a THIRD mechanism.** 6,277 single-url
  filer-years with the whole block exactly doubled (~$7.0B inflated), only
  5/6,277 amendment-linked — a GT batch defect concentrated in taxyear 2024
  (5,506 fys / $6.59B; 545 in 2025, 190 in 2023). Spot-checks: every row in an
  exact pair, half-sum == declared to the dollar (237093598/2024:
  $356,090,979). So the 990 amendment-evidence detector does NOT transfer to
  PF (basic_fields_pf also has no amendereturn column); PF rule = pure
  exact-pair set + declared reconciliation (|half−declared| < |full−declared|;
  only detects when itemization >⅔ of declared — conservative failure mode).
- **FIX LANDED + issue #33 CLOSED 2026-07-31** (commit 656aeaa,
  corrections-registry / PR #31): `grants_to_domestic_organizations_current` +
  `privategrants_current` built + verified (Fidelity $19.98B→$12.41B; global
  sched_i $729.3B→$694.8B; PF 2024 $141.7B→$118.6B; doubled filers halve to
  declared to the dollar). PF rule refined: all-multiplicities-EVEN +
  keep-half (not exactly-2) — Brin 2024 mults 2-4 taught this. Matching reads
  the _current relations; prefix carries `shape_v2`. Deferred (in the closing
  comment): Phase 2 validation warn, exploratory-SQL/frontend migration to
  _current, Fidelity ~13% residual. GT-facing report drafted at
  `docs/gt-duplication-report.md` (unsent). Corrections haul post-dedup =
  $676.8M (was $930.5M — the delta was the duplicates).
