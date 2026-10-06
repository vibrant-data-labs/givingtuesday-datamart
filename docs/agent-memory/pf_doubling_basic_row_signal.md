---
name: pf-doubling-basic-row-signal
description: "Measured 2026-09-30: the doubled basic_fields_pf row (one url, one sha, two rows; 15,845 filings, 2025-26 batches only) marks every batch-doubled PF paid block; pair_collapse misses 2,496 of them (113 with $24.4M), halves 95 forward-filled filings wrongly; forward fill (one real group + N-1 empty groups emitted as copies) is a third defect worth ~$72M; Schedule I blocks NOT doubled; PR #57 (docs + script) against placeholder-recovery; ZEIN CHOSE OPTION B on 2026-09-30 (rule change + gate still to do); future table (PR #55) uses the repeated row alone and no longer borrows the paid verdict"
metadata:
  type: project
---

PR #57 (branch `zeclaude/blissful-germain-e901ef`, base `placeholder-recovery`)
holds `docs/pf_doubling_basic_row_test.md`, the read-only script
`givingtuesday_datamart.exploratory.pf_doubling_basic_rows` (+ SQL, ~15 min,
temp tables only; `xml OID ...` counts a filing's own paid groups against
its table rows) and 3 tests. NOT merged; no rebuild, no matcher run.

Facts (batch 2026_06_16, object-id year = "batch year"; one source version):
- 15,845 doubled `basic_fields_pf` urls, all 2025 (12,659) / 2026 (3,186).
  12,650 have paid rows under the kept url; ALL are all-even. 10,154 halved
  today ($46.54B raw). 2,496 all-even NOT halved = 2,383 doubled nameless
  $0 rows + 113 with $24.4M over-count (line 25(d) = 0, or full sum closer:
  a filer whose 25(d) is 2x its own 3a, 25(d) with non-itemized amounts,
  `declared` read from another basic row). 11/11 XML-checked are doubles.
- 105 halved WITHOUT a doubled basic row: 10 amended copies under one url
  (two shas in both tables; real doubles) + 95 FORWARD FILLS: XML has one
  real `GrantOrContributionPdDurYrGrp` + N-1 groups holding only
  `<Amt>0</Amt>`, and GT emits each empty group as a verbatim copy of the
  real one, amount included (8/8 + 3/3 odd cases). Signature: one tuple,
  N >= 2, line 25(d) = one copy. 139 such filer-years without a doubled
  basic row, ~$72M over-counted today (odd N is never halved: 396040395/2018
  = 21 x $1,069,215). Filers with 25(d) = full sum are legit repeats (2/2).
  Several forward fills are "See Attached" placeholder rows (N x aggregate).
- Schedule I: 42,482 doubled `basic_fields` urls, but only 19 of 7,235
  Schedule I blocks under them are all-even (all $0). Blocks not doubled.
- The task brief's url-level counts (12,713 / 2,559 / 3,132) differ from the
  kept-url counts by exactly the 63 doubled urls `latest_url` drops.

**Why:** Zein asked whether `pair_collapse` should use the repeated basic
row instead of / beside the line-25 test, and on 2026-09-30 chose option B
(beside); the code change and the gate are still to be done. B: -2,924 rows (2,388 are $0 nameless), $24.4M; option A
(replace) would un-halve the 95 forward fills (wrong the other way).

**How to apply:** the rule change is decided (B) but not written; it still
needs the regression gate ([[corrections-registry-design]]). The forward-fill
shape needs its OWN rule (keep one row), not pair_collapse; it touches
placeholder recovery ([[placeholder-session5-work-list-loader]]). Rerun the
script after any GT drop; the `rule_vs_live` result must show zero
disagreement or staging moved since the last rebuild. `privategrants_future_current` (PR #55, session
"Load future-payment lists from GivingTuesday's 3B datamart") halves on
all-even AND url repeated in basic_fields_pf (COUNT(*) > 1, sha-agnostic, so
it catches the amended-copy shape too: 315 halved, 441,358 rows kept,
forward fill rare there) since 2026-09-30 and cites #57; B
does not cascade into it; the two docs cross-reference each other, and a
message with the decision was sent to that session. Related:
[[placeholder-session6-future-lists]], [[sched-i-amended-return-duplication]],
[[gt-column-decodes]].
