---
name: identity-search-findings
description: "search_identity/_bulk merged (PR #36); URL-tier index bug still unfixed, and domain/fiscal-sponsor data limits that cap what URL+name matching can reach"
metadata: 
  node_type: memory
  type: project
  originSessionId: 3abfa360-88d7-40fe-a0dc-99b750c626f8
  modified: 2026-08-05T20:00:33.496Z
---

`GtDatamartClient.search_identity` / `search_identity_bulk` / `iter_identity_universe`
merged to main 2026-08-04 (PR #36, squashed as 1707278) for EIN-less nonprofit
resolution in the portfolio-comparison engagement. Design rationale lives in the
docstrings. What isn't in the repo:

**Unfixed: the URL tier doesn't use its index.** `domain = :d OR domain LIKE :p`
seq-scans all 479K rows (84ms). `domain = :d` alone is a 0.037ms bitmap heap scan
— the `OR` is what defeats `ix_nonprofit_canonical_domain`. And `LIKE 'x%'` can
never use that btree because the DB collation is `en_US.UTF-8` (needs
`text_pattern_ops`). This is **production frontend behavior** in
`frontend/src/lib/queries/search.ts`, inherited by the port, not introduced by it.
Fix = split the OR into two UNION-ed branches (exact half) + a `text_pattern_ops`
index (prefix half). Never filed as an issue.

**URL coverage is 61%, not 85%.** 85% of `nonprofit_canonical` has `domain <> ''`,
but 27% of those are junk — 106,441 rows are literally `'n'` (the 990's "no
website"), plus `'see schedule o'`, `'not applicable'`, `'www ibew567.com'`.
Inert as match targets, but don't quote `domain <> ''` as URL-signal coverage.

**Fiscal-sponsor EINs are not grantee EINs.** In the OSP portfolio, 45/121 rows
carried the sponsor's EIN (41/42 exactly equal the `Fiscal Sponsor EIN` column),
collapsing to 34 distinct EINs — one sponsor covered 5 grantees. Any pipeline
treating an EIN column as grantee identity silently merges orgs and pools their
grant dollars. Model sponsor vs grantee as separate columns.

**Measured accuracy** (OSP portfolio, 50 rows with Candid ground truth, name+url):
34/50 top-1, but 10 of those EINs are absent from both canonicals — 85% of what's
reachable. 27 of 38 correct answers came from `url_exact`, so getting websites
onto portfolio rows beats any matcher tuning. The 2 genuine ranking failures were
ILIKE having no similarity notion ("NDN Collective" → "NDN ACTION NETWORK INC");
a Jaro-Winkler tiebreak would fix both — not built.

Narrative FTS was deliberately dropped from identity search (it doubled
wrong-at-rank-1 for 4pts of recall); use `search_nonprofits` for topical
discovery. See [[ez_filings_vs_combined_datamart]] for why 990-EZ filers are
absent from `nonprofit_canonical` in the first place.
