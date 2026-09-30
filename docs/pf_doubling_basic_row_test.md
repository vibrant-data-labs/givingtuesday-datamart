# The doubled basic row as a second signal for `pair_collapse`

Measured 2026-09-30 on batch `2026_06_16`, read-only (temp tables; nothing
rebuilt, no matcher run). Script:
`python -m givingtuesday_datamart.exploratory.pf_doubling_basic_rows`
(about 15 minutes) and its `xml OID ...` form for the spot checks. This
doc is a proposal; changing `pair_collapse` moves matcher inputs and needs
Zein's decision and the regression gate (`docs/matching-regression-runbook.md`).

## Summary

GivingTuesday's 2025 and 2026 batches emit some 990-PF filings twice, and
the doubling is visible in `basic_fields_pf` on its own: the same `url`,
one `filesha256`, two identical rows. There are 15,845 such filings, all
with object ids of 2025 (12,659, 10.3% of that year's filings) or 2026
(3,186, 14.0%); no earlier batch has a single one.

`privategrants_current` halves a filer-year only when every line-item
tuple is repeated an even number of times AND halving moves the itemized
sum toward Part I line 25(d). Crossed with the doubled basic rows:

| Doubled basic rows (urls)                                   | filer-years | raw rows | raw $ |
|-------------------------------------------------------------|------------:|---------:|------:|
| with paid rows under the filer-year's kept url               | 12,650      |          |       |
| of which every paid tuple is even                            | 12,650      |          |       |
| of which halved by the rule today                            | 10,154      | 378,382  | $46.54B |
| of which all even and NOT halved                             | 2,496       | 5,848    | $48.8M |
| of those, filings with no paid grants (a nameless $0 row, doubled) | 2,383 | 4,776    | $0    |
| of those, filings with dollars                               | 113         | 1,072    | $48.8M |
| with paid rows only under a url that `latest_url` drops      | 63          |          |       |
| with no paid rows at all (10 have paid rows under another url) | 3,132     |          |       |

Every doubled basic row that has paid rows has an all-even paid block; there
is no counter-example. The other direction has 105 exceptions: filer-years
the rule halves although their basic row is single. They are not the
2025-26 defect (their batch years run 2013 to 2025) and they split two ways:

- **10 carry two shas under one url**: an amended return's block emitted
  under the original's url (category C of `gt-duplication-report.md`).
  Real doubles, halved correctly; 2 of 2 checked against the XML.
- **95 are one tuple repeated N times with line 25(d) equal to ONE copy.**
  The filing's own XML has one real paid group and N-1 empty groups
  (`<Amt>0</Amt>` and nothing else), and the extract emits each empty group
  as a verbatim copy of the real one, amount included. Not a double: the
  right row count is 1. Halving leaves N/2 copies of the total (right by
  accident when N = 2, wrong for N = 4, 6, 8, 14); 8 of 8 checked.

**Proposal: keep the line-25 test and add the doubled basic row beside it
(option B below).** It halves the 2,496 doubled filer-years the line-25 test
cannot see (2,383 of them a doubled empty row), keeps halving the amended-copy cases, and changes nothing
else: 2,924 rows leave `privategrants_current`, 2,388 of them nameless $0
rows, and $24.4M of over-count goes with the other 536. Replacing the
line-25 test (option A) would in addition restore the 95 forward-filled
filings to N copies of their total, which is wrong in the other direction.

The forward-fill shape is a third defect, larger in dollars than what B
fixes: 139 filer-years without a doubled basic row carry one grant N
times with line 25(d) equal to one copy, about $72M of over-count today
(section 2). It wants its own rule, not `pair_collapse`.

## 1. Reproduction

Counts in the task brief were by url; the ones here are by the filer-year's
kept url, which is what `pair_collapse` sees. The 63 doubled urls with paid
rows that are not the kept url account for every difference (12,713 =
12,650 + 63; 2,559 = 2,496 + 63; 3,195 = 3,132 + 63). `latest_url` drops
those 63 urls whole, so they change nothing downstream.

The re-derived rule agrees with the live table to the filer-year: 10,259
filer-years halved (10,164 `pair_collapse` + 95 `latest_url+pair_collapse`),
378,990 raw rows kept as 189,495.

Doubled basic rows by batch year (object-id year) and tax year:

| batch year | tax year 2022 | 2023 | 2024 | 2025 |
|-----------:|-----:|-----:|------:|------:|
| 2025       | 104  | 685  | 11,870 |       |
| 2026       |      | 89   | 1,164  | 1,933 |

Only one source version is loaded (`2026_06_16`), so batch year here means
the IRS processing year in the object id, not a GT drop.

## 2. The 105 halved without a doubled basic row

All 105 urls are in `basic_fields_pf`: 94 as a single row, 10 as two rows
with two shas, 1 with a second url for the filer-year. By batch year:
2013-2019: 41, 2020-2022: 36, 2023: 10, 2024: 15, 2025: 11 (a flat trickle,
unlike the 2025-26 wall). Shapes:

| shape | filer-years | raw rows | raw $ | multiplicity |
|---|---:|---:|---:|---|
| one tuple, line 25(d) = one copy (forward fill) | 93 | 322 | $43.0M | 2 to 14 |
| two shas under one url (amended copy under the original's url; the paid table carries both shas too) | 10 | 278 | $16.7M | 2 to 4 |
| one tuple, line 25(d) within 1% of one copy | 2 | 8 | $0.3M | 2 to 6 |

Spot checks against the filing's own XML (`GrantOrContributionPdDurYrGrp`
groups; "empty" = the group holds only `<Amt>0</Amt>`):

| EIN / tax year | table rows | current rows | XML real | XML empty | line 25(d) | verdict |
|---|---:|---:|---:|---:|---:|---|
| 981084156 / 2023 (two shas) | 128 | 64 | 64 | 0 | 3,977,489 | real double, halved right |
| 831156894 / 2023 (two shas) | 20 | 10 | 10 | 0 | 77,847 | real double, halved right |
| 481210113 / 2016 | 14 | 7 | 1 | 13 | 489,885 | forward fill; "See Attached list" x14, halved to 7 copies of the total |
| 271534575 / 2019 | 8 | 4 | 1 | 7 | 147,772 | forward fill; "VARIOUS ORGANIZATIONS" |
| 386087554 / 2021 | 6 | 3 | 1 | 5 | 1,729 | forward fill |
| 276529735 / 2014 | 4 | 2 | 1 | 3 | 33,123 | forward fill; "See Attached List" |
| 300075287 / 2024 | 2 | 1 | 1 | 1 | 1,990,000 | forward fill; halving lands on the right count by accident |
| 861924970 / 2023 | 2 | 1 | 1 | 1 | 1,104,371 | forward fill |
| 586326331 / 2014 | 2 | 1 | 1 | 1 | 409,069 | forward fill; "See Attached List" |
| 264280056 / 2017 | 2 | 1 | 1 | 1 | 53,000 | forward fill |

Three of the eight forward-filled filings are placeholder rows ("See
Attached list", "VARIOUS ORGANIZATIONS"): the placeholder's aggregate
amount is in the table N times. The forward-fill shape across the whole
paid table (one tuple, N >= 2 copies):

| one tuple, N >= 2 copies, no doubled basic row | filer-years | raw rows | raw $ | one-copy $ | halved today |
|---|---:|---:|---:|---:|---|
| line 25(d) = one copy, N even | 98 (93 above + 5 two-sha with N = 2) | 332 | $43.7M | $9.2M | yes, to N/2 copies |
| line 25(d) = one copy, N odd | 46 | 204 | $69.7M | $10.1M | no (odd fails the even test) |
| line 25(d) != one copy | 193 | 529 | $26.3M | $11.9M | 2 yes, 191 no |

Spot checks of the odd class: 396040395 / 2018 has 21 rows of $1,069,215
(one real group, Greater Milwaukee Foundation, plus 20 empty ones): $22.5M
in the table, $21.4M over, and nothing halves it. 205905161 / 2023 is
"SEE ATTACHED SCHEDULE" five times; 260640175 / 2016 is Valparaiso
University five times. Two of the "line 25(d) != one copy" class are
legitimate repeats (954536657 / 2020: two $2,000,000 grants to Seattle
Children's; 264647256 / 2015: two to UCSF), so the signature separates
the two. Over-count today from the signature class alone: about $59.5M
(odd, unhalved) plus $12.6M (even, halved to N/2 copies), $72M over 139
filer-years, three times what the 2,496 carry.

## 3. The 2,496 all even, doubled basic row, not halved

Why the line-25 test does not fire:

| why | filer-years | raw rows | raw $ | over-count |
|---|---:|---:|---:|---:|
| the filing has no paid grants; its single nameless $0 row is doubled (Glass Family) | 2,383 | 4,776 | $0 | $0 |
| line 25(d) is 0 while the filing itemizes (Okumura, 981599776) | 91 | 718 | $36.2M | $18.1M |
| full sum closer to line 25(d) than half (232202414, 262655299, 592750694) | 22 | 354 | $12.7M | $6.3M |

Almost all of the 2,496 are the first row: 2,438 have exactly two rows,
and 2,383 of those are $0. Of the zero-amount rows these filers hold in
`privategrants_current`, 11,918 of 11,981 carry no recipient, so halving
them is invisible to the matcher. The dollars sit in 113 filer-years.

Spot checks, largest first, plus the four named filings:

| EIN / tax year | table rows | current rows | XML real | line 25(d) | why the rule missed it | verdict |
|---|---:|---:|---:|---:|---|---|
| 981599776 / 2024 | 2 | 2 | 1 | 0 | declared 0; the one row is "VARIOUS ORGANIZATIONS / SEE ATTACHED SCHEDULE" for $13,144,687 | real double, $13.1M counted twice |
| 232202414 / 2023 | 20 | 20 | 10 | 6,144,424 | line 25(d) is ~2x the itemized 3a total ($3,166,500), so the full sum sits closer | real double |
| 510439076 / 2023 | 2 | 2 | 1 | 1,318,476 | the declared lookup took another basic row of the filer-year that says 0 | real double |
| 872081402 / 2024 | 196 | 196 | 98 | 0 | declared 0 (multiplicities 2 and 4) | real double |
| 262655299 / 2024 | 24 | 24 | 12 | 1,575,272 | the filer's own line 25(d) is exactly 2x its 3a total ($787,636); full sum equals declared | real double |
| 631263667 / 2024 | 26 | 26 | 13 | 517,462 | two urls for the filer-year; declared came from the other version ($1,235,918) | real double |
| 261969309 / 2024 | 42 | 42 | 21 | 1,865,700 | line 25(d) far above the itemized total ($365,700) | real double |
| 383862623 / 2024 | 28 | 28 | 14 | 0 | declared 0 | real double |
| 592750694 / 2024 | 2 | 2 | 1 | 952,503 | full ($1,044,444) closer to declared than half ($522,222) | real double |
| 270790491 / 2024 (Okumura) | 64 | 64 | 32 | 0 | declared 0 every year since 2020 while itemizing $0.4-0.7M | real double, $425,000 counted twice |
| 820561001 / 2024 (Glass Family) | 2 | 2 | 0 | 0 | no paid grants; the table carries a $0 row twice | doubled empty row, no dollar effect |
| 472107200 / 2024 (Brin) | 340 | 170 | 170 | 698,939,667 | (halved today) | real double, halved right |
| 137184401 / 2024 (Helmsley) | 1,140 | 570 | 570 | 440,927,206 | (halved today) | real double, halved right |

Eleven of eleven unhalved filings checked are real doubles. Four of the
misses are structural, not a matter of a better threshold: a line 25(d)
that includes non-itemized amounts, a filer whose line 25(d) is itself 2x
its 3a total, and two where `declared` was read from a different
`basic_fields_pf` row than the doubled one.

## 4. Schedule I

Done; it costs about four minutes on top of the PF part. `basic_fields`
shows the same 2025-26 defect: 42,482 urls with two rows under one sha
(32,917 of 2025, 9,565 of 2026, none earlier), beside 24,268 urls with two
shas (amended returns under one url, the population `amend_distinct`
handles). The grant blocks did not follow:

| doubled `basic_fields` urls | 42,482 |
|---|---:|
| with Schedule I rows under the filer-year's kept url | 7,235 |
| of which NOT all even (the block is singletons) | 7,216 |
| of which all even | 19, 198 rows, $0 (one $0 tuple 2 or 12 times, the empty-row shape) |

The largest doubled-basic-row filers itemize thousands of singletons
(311774905 / 2024: 7,424 rows, 7,424 tuples). Across every 2025-26
Schedule I filer-year, all-even blocks are 212 of 59,734 whether or not
the basic row is doubled. So on the 990 side the batch defect doubled
`basic_fields` but not the Schedule I blocks; `amend_distinct`'s
two-sha condition is not being bypassed, and nothing is proposed for
`grants_to_domestic_organizations_current`.

## 5. Proposal

Three candidate rules, all keeping the all-even precondition and the
keep-half-of-each-tuple mechanics:

| rule | halves | cannot catch | changes to `privategrants_current` |
|---|---|---|---|
| today: all even AND line 25 | 10,259 filer-years | the 2,496 doubles with a 0, absent, mis-read or non-itemized line 25(d); halves the 95 forward-filled filings to N/2 copies | none |
| A: all even AND doubled basic row | 12,650 | the 10 amended-copy doubles under one url (two shas, single-row-per-sha); any future batch that doubles grants without doubling the basic row (none seen: 0 counter-examples in 12,650) | -2,924 rows (2,496 filer-years newly halved, $24.4M) and +304 rows (105 filer-years un-halved, $30.0M restored, wrongly for the 95 forward fills); net 16,651,426 -> 16,648,806 |
| A': all even AND the url repeated in `basic_fields_pf`, one sha or two (the future table's form) | 12,660 | the 95 forward fills restored to N copies, as A | as A, minus the 10 amended copies, which stay halved |
| B: all even AND (line 25 OR doubled basic row) | 12,755 | the 95 forward fills stay halved to N/2 (wrong either way; a separate rule); a double whose basic row is single and whose line 25 is 0 (none seen) | -2,924 rows only (2,388 nameless $0 rows, 536 rows carrying $24.4M); 16,651,426 -> 16,648,502 |

B is the recommendation. What it does not do, and what should follow it:

1. **The forward-fill shape wants its own rule**, outside `pair_collapse`:
   one tuple, N >= 2 copies, line 25(d) equal to one copy, keep one row.
   Halving never produces that for N > 2, and the odd cases are not
   touched at all today. It is the larger defect in dollars ($72M over
   139 filer-years, section 2, against B's $24.4M) and reaches into
   placeholder recovery: several of the forward-filled rows are "See
   Attached" pointers, so their aggregate is in the table N times. For N
   = 2 the signature cannot tell a forward fill from a doubled
   single-grant filing, but both resolve to one row.
2. **Declared lookup.** Two of the eleven misses came from `declared`
   reading a different `basic_fields_pf` row than the kept url's. The
   docstring of `current_grants` explains why the rule reads raw
   `basic_fields_pf` rather than `_current`; joining `declared` on the kept
   url (not on filer-year) would remove that class without moving the gate
   on its own. Not needed under B, since the basic-row test covers those
   filings anyway.
3. **Gate.** Under B the matcher's input loses 2,924 rows across 2,496
   filer-years, of which 536 rows and $24.4M are real grants, almost all
   tax year 2024-2025. Small next to the $46.5B the rule already halves,
   but it is a matcher-input change and goes through the regression gate
   like any other.

### The future-payment table

`privategrants_future_current` (PR #55) faced the same doubling with no
line 25 to test against, and since September 30 halves a future block
when every tuple is even AND the filing's url appears more than once in
`basic_fields_pf`, one sha or two: the A shape, on its own, in a form
that also catches the amended copy under one url. Measured there after
this doc: 315 filings halved under the kept url (314 batch doubles plus
Kiwanis Club of Cape May 2020, the one pre-2025 paired filing, an
amended copy with two shas), 4,645 rows removed, $2.03B, 441,358 rows
kept. The 11 other pre-2025 paired filings have one basic row and are
genuine repeats. Under B the paid and future rules agree on every batch
double and on the amended copies, and differ only on the 95 forward
fills, which the future side carries rarely: one-tuple-times-N blocks
without a repeated basic row are about 13 filings and a few million
dollars, noted there as unrepaired and not XML-checked. The future rule
no longer borrows the paid verdict, so B does not cascade into it and the
two rebuild independently.

## 6. Files

- `givingtuesday_datamart/exploratory/pf_doubling_basic_rows.py` and
  `data/exploratory/pf_doubling_basic_rows.sql`: the measurement, one
  session, temp tables only; substitutes the rule's own content hashes so
  the tuples are the rule's tuples. `xml OID ...` is the spot check.
- `tests/test_pf_doubling_basic_rows.py`: the XML group classifier and the
  SQL rendering.
- This doc. `gt-duplication-report.md` section B is the upstream write-up
  the basic-row signal extends; `current_grants.py`'s docstring is the
  rule.
