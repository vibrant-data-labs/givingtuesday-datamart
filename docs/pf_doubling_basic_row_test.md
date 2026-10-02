# The doubled basic row as a second signal for `pair_collapse`

Measured 2026-09-30 on batch `2026_06_16`, read-only (temp tables; nothing
rebuilt, no matcher run). Script:
`python -m givingtuesday_datamart.exploratory.pf_doubling_basic_rows`
(about 15 minutes) and its `xml OID ...` form for the spot checks. This
doc is a proposal; changing `pair_collapse` moves matcher inputs and needs
Zein's decision and the regression gate (`docs/matching-regression-runbook.md`).

**Built on 2026-09-30** (sections 1 to 5 are the measurement as it stood
before): the repeated row as the halving test and a forward-fill rule are
in `current_grants.py`, the paid and the future relation are built from
one definition, and the whole is proved on scratch copies. It was built as
option B; on 2026-10-01 Zein took the line-25 test out, which on this
batch changes no row (6.2), so `pair_collapse` is now the same test on
both sides. Section 6 has what was built, what it changes and what happens
next. The production tables are not rebuilt yet.

**Built on 2026-10-01** on top of it: the fill 6.6 left, copies of one
grant that differ in a field, is a second fill rule, `field_fill`, proved
on a scratch copy the same way. Section 7 has the rule, its design points,
the XML checks and what it changes, and 7.8 a larger shape of the same
defect that neither fill reaches.

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

## 6. The repairs, as built (2026-09-30)

### 6.1 One definition for both relations

`privategrants_current` and `privategrants_future_current` are rendered by
one function, `current_grants._pf_current_ddl`. The kept url, the tuple and
its multiplicity, the repeated-row signal, the all-even test, keeping half
of each tuple, the forward-fill shape and the `dedup_rule` values are each
written once. The function takes the table names, so a scratch copy or a
test's temp table is the same DDL under another name. Two differences are
left, each an argument of that function (the module docstring has the
reasons):

| difference | side | why |
|---|---|---|
| `forward_fill` | paid | the rule leans on line 25, and no column holds a total approved for future payment (6.4 has what was tried on the future side) |
| a filer-year whose paid rows are held under a later url is left out | future | the paid relation has nothing to be behind |

`pair_collapse` is one test on both sides: every tuple even and the
filing's url in `basic_fields_pf` more than once. A third difference, a
line-25 test beside it on the paid side, was built on September 30 and
taken out on October 1 (6.2).

The refactor itself changed nothing. Proved on scratch copies
(`python -m givingtuesday_datamart.exploratory.pf_current_scratch build|compare`),
every column compared, `dedup_rule` included, per filer-year:

| scratch copy | against | filer-years | differ | rows | dollars |
|---|---|---:|---:|---:|---:|
| future, from the shared definition | `privategrants_future_current` | 25,800 | 0 | 441,358 = 441,358 | $181,296,385,866 on both |
| future again, reading the PAID scratch copy | `privategrants_future_current` | 25,800 | 0 | 441,358 = 441,358 | the same: the paid change does not reach it |
| paid, shared definition and the two new rules | `privategrants_current` | 1,007,142 | 2,703 | 16,651,426 -> 16,648,094 | $833,063,559,153 -> $832,962,530,575 |

The paid proof was run three times: on September 30 with the line-25 test
still in the paid rule, on October 1 without it, and on October 1 again
with line 25 read from the kept url's own basic row (the text that ships;
6.3 has why). The three runs give the same tables, to the row and the
dollar, here and below. The first future proof was run on September 30
and on October 1, of the text that ships. The second is of September 30
alone: it shows that the paid copy holds every filer-year under the url
production holds it under, which the later copies, equal in every count,
do too.

Every one of the 2,703 paid filer-years that differ is one the two new
rules take (6.2, 6.3). The other 1,004,439 are identical row for row.

| `dedup_rule` today | in the scratch copy | filer-years | rows today | rows after | dollars leaving |
|---|---|---:|---:|---:|---:|
| `passthrough` | `pair_collapse` | 2,480 | 5,748 | 2,874 | $23,727,731 |
| `latest_url` | `latest_url+pair_collapse` | 23 | 162 | 81 | $583,858 |
| `passthrough` | `forward_fill` | 103 | 403 | 103 | $63,864,652 |
| `pair_collapse` | `forward_fill` | 94 | 164 | 94 | $12,619,857 |
| `latest_url+pair_collapse` | `latest_url+forward_fill` | 1 | 1 | 1 | $0 |
| `pair_collapse` | `pair_collapse+forward_fill` | 1 | 3 | 1 | $4,440 |
| `passthrough` | `pair_collapse+forward_fill` | 1 | 6 | 1 | $228,040 |
| | | **2,703** | **6,487** | **3,155** | **$101,028,578** |

3,332 rows leave. 65 of the filer-years (64 + 1) keep their one row and
change label only: a two-copy forward fill that halving had brought to one
row by accident is now called what it is.

`privategrants_current` by rule, today and after:

| `dedup_rule` | filer-years today | rows today | filer-years after | rows after |
|---|---:|---:|---:|---:|
| `passthrough` | 986,206 | 15,797,603 | 983,622 | 15,791,446 |
| `latest_url` | 10,677 | 664,328 | 10,654 | 664,166 |
| `pair_collapse` | 10,164 | 188,149 | 12,549 | 190,856 |
| `latest_url+pair_collapse` | 95 | 1,346 | 117 | 1,426 |
| `forward_fill` | | | 197 | 197 |
| `pair_collapse+forward_fill` | | | 2 | 2 |
| `latest_url+forward_fill` | | | 1 | 1 |
| | 1,007,142 | 16,651,426 | 1,007,142 | 16,648,094 |

The paid scratch build took 22 minutes with the shared DDL (windows over
one sort, where the old DDL wrote its hashed rows to disk and read them
twice), and 32 the third time, with other sessions on the database and
line 25 looked up by url; the future one 78 seconds.

One change beyond the sharing, on the future side, found by the second
proof above. Reading the paid scratch copy, the future build did not
finish: the planner takes the join to the kept url for one row (it cannot
estimate `IS NOT DISTINCT FROM`), and nested a loop over the paid
relation's urls, a CTE of 265,000 rows, for each of 460,000 rows. Against
the production paid table the same text had happened to get a hash join.
It is the trap PR #55 met with `basic_fields_pf` and cured with a temp
table, and the cure is now the same for everything the rule reads beside
the rows: the repeated urls, line 25 and the paid relation's urls are each
an indexed temp table, looked up per row. Both future proofs above are of
that DDL. Without it, step 2 of 6.7 could have hung on the rebuilt paid
table.

### 6.2 The repeated row as the halving test

Halve when every tuple is even AND the filing's url is in
`basic_fields_pf` more than once, one sha or two: the future relation's
own test. As built on September 30 this was option B, the line-25 test OR
the repeated row; the end of this section has why the line-25 test went.

| | expected (section 5) | built | difference |
|---|---:|---:|---|
| filer-years newly halved | 2,496 | 2,504 | 8 amended copies under one url (two shas) |
| rows leaving | 2,924 | 2,958 | 34 rows of those 8 |
| of which rows of filer-years with no dollars | 2,388 | 2,395 | 7 |
| of which rows carrying dollars | 536 | 563 | 27 |
| dollars counted twice | $24.4M | $24,448,413 | $34,303 |

The 8 are the difference between the two signals: section 5's option B
counted a doubled basic row as two rows under ONE sha, and the shared
signal counts a url repeated under one sha or two. Seven of the 8 are a
nameless $0 row twice. The eighth is EIN 223734672 / tax year 2020
(`202230399349100803`), 54 rows whose XML has 27 groups: a real double,
which the line-25 test missed because line 25(d) is 0, and whose future
block the future relation already halves (it is the Kiwanis Club of Cape
May filing of section 5). One of the 2,504, EIN 467406524 / tax year 2024,
is also a forward fill and ends at one row (6.3); halving alone would have
left it three, which is where 2,958 comes from against the 2,955 the table
above shows for the two halving-only lines.

**The line-25 test, taken out on 2026-10-01.** With the repeated row
beside it and forward fill decided first, the line-25 test alone halved
nothing on this batch: 0 filer-years carried `pair_collapse` without a
repeated row, and all 95 it had halved without one were forward fills.
Zein took it out. The paid scratch copy built without it differs from
production in the same 2,703 filer-years, by the same rows and dollars
under every rule (the tables of 6.1 are of that build), and 32 filings
were checked against their XML in it. What is given up is a guard with
nothing to guard today: a batch that doubles the grants and NOT the basic
row would now pass unhalved, where the test caught it when the filing
itemizes more than 2/3 of line 25. The watch for it is in the build, where
it cannot be skipped (review of October 1): after every build of
`privategrants_current`, `current_grants.unhalved_doubles` counts the
filer-years held whole in which every tuple is even, there is more than
one tuple, and half the block is closer to line 25(d) than the whole. It
changes no row; the count is logged, and a non-zero count is a WARNING
that names the first of them. On the scratch copy it is 0, in 81 seconds.
Run on the production table as it stands, it finds three: 546053504 /
2024, 631263667 / 2024 and 870442164 / 2025, blocks in the extract twice
(66, 26 and 30 rows) that the old rule holds whole because it read line
25 from another version of the return. The kept url's own line 25 is
exactly half of each block's sum, and the new rule halves all three under
their repeated row. So the watch finds what it is for. This doc's own
script shows the same by hand, as a "multi tuple" line of
`halved_without_doubled_row_shape`: section 2 is that query on this batch,
and it has no such line.

### 6.3 Forward fill

**The rule.** Under the kept url, a block that is ONE tuple, held N >= 2
times by the filing itself, with one copy's amount above zero and EQUAL to
line 25 in column (d) or column (a), keeps ONE row and carries
`forward_fill`.

**The design points.**

- *It must not take a legitimate repeat.* Of the 322 one-tuple blocks
  with dollars and no repeated row, 198 have one copy equal to a column of
  line 25 to the dollar, 122 have EVERY copy on line 25 to the dollar
  (legitimate repeats, the two named ones among them: 954536657 / 2020 and
  264647256 / 2015, both $4,000,000 on line 25 for two grants of
  $2,000,000), and 2 state neither (line 25 is 0 in both columns for one,
  above every copy for the other). Nothing sits between the two kinds.
- *Tolerance: none.* The two filer-years section 2 had "within 1%" of
  column (d) (823392014 / 2022 and 386087554 / 2024) match column (a)
  exactly. No one-tuple block is within 5% of a column without being equal
  to one.
- *Column (a) as well as (d).* 134 of the 198 match column (d) (and then
  (a) too). 64 match column (a) only: 62 leave column (d) at 0, and 2 are
  the pair above. 11 of the 64 were checked against the XML, and all 11
  are one real group plus empty ones.
- *Which row of `basic_fields_pf`.* The kept url's own, since the review
  of October 1. As first built the rule read the filer-year's row the old
  line-25 test read, `ORDER BY _ingested_at DESC, filesha256`, which is
  the lowest sha (the table holds one `_ingested_at`): an arbitrary
  version of the return. 1,775 filer-years hold versions that state line
  25 differently. On this batch the two readings decide every block the
  same way: forward fill fires on the same 200 filer-years under either.
  Where they state different totals for a one-tuple block (18
  filer-years), 17 are one grant twice under a repeated row, halved to one
  row before forward fill is asked, and the other matches under both. But
  the old reading could make a wrong collapse: an original that paid one
  grant X, amended to two grants of X, would lose a row of the amended
  list if the original's sha sorted first. Every kept url has a basic row
  (1,007,142 of 1,007,142), so no fallback to another version is built: a
  url without one states no line 25, and none is borrowed.
- *Order against `pair_collapse`.* The forward-fill test comes AFTER the
  repeated row: a filing in the extract twice
  holds half the copies, so one tuple twice under a repeated row is a
  doubled single-grant filing (1,851 of them halved today, line 25 equal
  to one copy) and stays `pair_collapse`, and one tuple six times under a
  repeated row is three groups doubled, halved and then filled to one
  (`pair_collapse+forward_fill`; 2 filer-years, 467406524 / 2024 and
  311791537 / 2024, both one real group and two empty ones in the XML).
- *Lineage.* `forward_fill` is a `dedup_rule` value of its own.

**What it takes.**

| forward fill, by what the block is today | filer-years | raw rows | rows today | rows after | raw $ | $ today | $ after |
|---|---:|---:|---:|---:|---:|---:|---:|
| N even, halved today to N/2 | 95 | 330 | 165 | 95 | $43,270,804 | $21,635,402 | $9,015,545 |
| N odd, untouched today | 59 | 267 | 267 | 59 | $71,270,280 | $71,270,280 | $10,578,708 |
| N even, untouched today (line 25(d) is 0) | 44 | 136 | 136 | 44 | $5,833,836 | $5,833,836 | $2,660,756 |
| under a repeated row (N = 6) | 2 | 12 | 9 | 2 | $286,968 | $280,308 | $47,828 |
| | **200** | **745** | **577** | **200** | **$120,661,888** | **$99,019,826** | **$22,302,837** |

377 rows and $76.7M leave these 200 filer-years.

Against the numbers expected from section 2:

| expected | built | why |
|---|---|---|
| N even: 98 filer-years, 332 rows, $43.7M raw, $9.2M real | 93 of the 98 become `forward_fill`; 5 stay `pair_collapse` at one row | the 5 have N = 2 and two shas under the url: an amended copy. Either reading gives one row |
| "N odd": 46 filer-years, 204 rows, $69.7M raw, $10.1M real | 41 become `forward_fill` | the class was "not halved today", not "odd": 42 odd and 4 even. 5 of the 46 are $0 blocks with line 25 at 0 (the rule wants an amount): 2 are halved by 6.2, 3 are left (7 rows, no dollars) |
| | 64 more through column (a) | not in section 2, which tested column (d) |
| | 2 more under a repeated row | section 2 looked only where the basic row was single |

**XML checks**, the scratch copy read in place of the production table
(`pf_doubling_basic_rows xml OID ... --current scratch_pgc_rules`):

| EIN / tax year | raw rows | XML real | XML empty | line 25 (d) / (a) | rows today | rows after | rule after |
|---|---:|---:|---:|---|---:|---:|---|
| 472107200 / 2024 (Brin) | 340 | 170 | 0 | 698,939,667 / 688,727,682 | 170 | 170 | `pair_collapse` |
| 137184401 / 2024 (Helmsley) | 1,140 | 570 | 0 | 440,927,206 / 346,765,830 | 570 | 570 | `pair_collapse` |
| 270790491 / 2024 (Okumura) | 64 | 32 | 0 | 0 / 466,000 | 64 | 32 | `pair_collapse` |
| 981599776 / 2024 | 2 | 1 | 0 | 0 / 13,144,687 | 2 | 1 | `pair_collapse` |
| 232202414 / 2023 | 20 | 10 | 0 | 6,144,424 | 20 | 10 | `pair_collapse` |
| 262655299 / 2024 | 24 | 12 | 0 | 1,575,272 / 787,636 | 24 | 12 | `pair_collapse` |
| 820561001 / 2024 (Glass Family) | 2 | 0 | 0 | 0 | 2 | 1 | `pair_collapse` (a $0 row, once) |
| 223734672 / 2020 (two shas) | 54 | 27 | 0 | 0 / 34,303 | 54 | 27 | `pair_collapse` |
| 981084156 / 2023 (two shas) | 128 | 64 | 0 | 3,977,489 | 64 | 64 | `pair_collapse` |
| 831156894 / 2023 (two shas) | 20 | 10 | 0 | 77,847 | 10 | 10 | `pair_collapse` |
| 396040395 / 2018 | 21 | 1 | 20 | 1,069,215 | 21 | 1 | `forward_fill` |
| 481210113 / 2016 | 14 | 1 | 13 | 489,885 | 7 | 1 | `forward_fill` |
| 205905161 / 2023 | 5 | 1 | 4 | 1,210,000 | 5 | 1 | `forward_fill` |
| 260640175 / 2016 | 5 | 1 | 4 | 1,153,932 | 5 | 1 | `forward_fill` |
| 300075287 / 2023 (column (a)) | 2 | 1 | 1 | 0 / 1,525,095 | 2 | 1 | `forward_fill` |
| 742876837 / 2018 (column (a)) | 13 | 1 | 12 | 0 / 18,319 | 13 | 1 | `forward_fill` |
| 386087554 / 2024 (column (a)) | 6 | 1 | 5 | 1,231 / 1,206 | 3 | 1 | `forward_fill` |
| 467406524 / 2024 (repeated row) | 6 | 1 | 2 | 0 / 45,608 | 6 | 1 | `pair_collapse+forward_fill` |
| 436801146 / 2024 (repeated row) | 4 | 2 | 0 | 183,986 / 185,520 | 2 | 2 | `pair_collapse` (a legitimate repeat, doubled) |
| 954536657 / 2020 | 2 | 2 | 0 | 4,000,000 | 2 | 2 | `passthrough` |
| 264647256 / 2015 | 2 | 2 | 0 | 4,000,000 | 2 | 2 | `passthrough` |

In every line the rows after equal the XML's real groups (one $0 row for
the filing with none). Checked beside these and not shown: eight more
column-(a) matches and 311791537 / 2024, all one real group plus empty
ones.

### 6.4 The future-payment table

Left as it is, with the reason. 13 filings hold one tuple N >= 2 times with
no repeated basic row: $7,324,062 on their rows. All 13 were checked
against their XML (`GrantOrContriApprvForFutGrp`):

| EIN / tax year | object id | table rows | XML real | XML empty | on the rows | XML total (3b) | what it is |
|---|---|---:|---:|---:|---:|---:|---|
| 561781568 / 2016 | `201810469349100601` | 3 | 3 | 0 | 3,187,500 | 3,187,500 | genuine repeat |
| 561781568 / 2017 | `201900369349100305` | 2 | 2 | 0 | 2,125,000 | 2,125,000 | genuine repeat |
| 863694650 / 2021 | `202330139349100413` | 2 | 2 | 0 | 708,500 | 708,500 | genuine repeat |
| 346500595 / 2020 | `202112959349100711` | 2 | 2 | 0 | 557,562 | 820,243 | two amount-only groups, $541,462 and $278,781, read as $278,781 twice: $262,681 too LITTLE |
| 475268267 / 2021 | `202233189349105123` | 3 | 1 | 2 | 226,500 | 75,500 | forward fill, $151,000 over |
| 546481375 / 2017 | `201821289349101032` | 2 | 2 | 0 | 150,000 | 150,000 | genuine repeat |
| 466175348 / 2023 | `202423179349102672` | 2 | 1 | 1 | 100,000 | 50,000 | forward fill, $50,000 over |
| 237339430 / 2023 | `202540919349100319` | 2 | 2 | 0 | 82,000 | 82,000 | genuine repeat |
| 273004256 / 2015 | `201611319349100826` | 2 | 2 | 0 | 80,000 | 80,000 | genuine repeat |
| 061245402 / 2021 | `202233009349100203` | 2 | 2 | 0 | 50,000 | 50,000 | genuine repeat |
| 832575728 / 2021 | `202243189349102279` | 4 | 4 | 0 | 22,000 | 22,000 | genuine repeat |
| 832426404 / 2021 | `202201319349104460` | 4 | 4 | 0 | 20,000 | 20,000 | genuine repeat |
| 832426404 / 2022 | `202341329349102514` | 3 | 3 | 0 | 15,000 | 15,000 | genuine repeat |

10 genuine repeats, 2 forward fills ($201,000 counted too often) and one
filing where the fill lost dollars. The signals that need no future total:

| signal | hits on the 2 forward fills | false hits on the other 11 | usable |
|---|---:|---:|---|
| (a) the paid block of the same filing is forward-filled | 0 | 0 | no: it misses both. Sodhani's paid block is ten rows (nine real groups and an empty one), Hodges's is one grant; the paid rule calls all 13 `passthrough` |
| (b) the XML holds one real group and empty ones | 2 | 0 | it holds on every case, and it is the XML: no extract carries it, so the DDL cannot test it |
| the shape alone (one tuple, N >= 2) | 2 | 11 | no: it would take ten genuine repeats down to one pledge |

No rule is proposed. The future relation has no forward-fill repair, the
docstring says so, and the two filings (`202233189349105123`,
`202423179349102672`) stay at N copies. Signal (b) could be applied as a
short list of object ids kept beside the corrections registry; for
$201,000 it is not worth a mechanism.

The filings section 5 named are unchanged in the scratch copy, as the
row-for-row comparison already says: Kiwanis Club of Cape May 2020 halved
(2 rows to 1, $17,000), and the four largest halved blocks, all tax year
2024: Helmsley 970 rows to 485 ($482,120,897), Kern 46 to 23
($159,644,612), MacArthur 660 to 330 ($147,761,082), Sergey Brin 90 to 45
($144,791,778).

### 6.5 Placeholder recovery: measured, not applied

`python -m givingtuesday_datamart.exploratory.pf_current_scratch placeholders`.
On `pf_placeholder_filings`, 36 filings hold one placeholder amount N
times with one copy equal to line 25: $82,217,263 on their rows,
$11,203,855 real, all 36 `placeholder_exceeds_declared`, none loaded.
Read from the paid scratch copy, as the work list would read it:

| filer (EIN) | filings | `placeholder_paid` today | from the scratch copy | still over line 25 | why |
|---|---:|---:|---:|---|---|
| 205905161, 2020 to 2024 | 5 | $29,287,205 | $5,857,441 | no | one tuple five times |
| 481210113, 2013 to 2016 | 4 | $12,796,077 | $1,828,011 | no | one tuple fourteen times |
| 276529735, ten years | 10 | $540,665 | $258,351 | no | one tuple three or four times |
| 562046037, 2021, 2022, 2024 | 3 | $357,000 | $71,400 | no | one tuple five times |
| 481210113, 2017 to 2022 | 6 | $38,571,468 | $38,571,468 | yes | the copies differ in a field (6.6) |
| 382831525, 2014 to 2016 | 3 | $619,740 | $619,740 | yes | the same |
| 136193966, 2016 to 2020 | 5 | $45,108 | $45,108 | yes | the same |
| | **36** | **$82,217,263** | **$47,251,519** | 14 of 36 | |

22 filings come down to one copy ($42,980,947 to $8,015,203) and lose the
flag. The 11 with a readable attachment, through the loader's own
selection on the stored readings (nothing written, nothing bought; the
same under policies v2 and v3):

| object id | EIN / tax year | corrected amount | does a list add up to it |
|---|---|---:|---|
| `202423199349102497` | 205905161 / 2023 | $1,210,000 | yes, 32 rows |
| `202303199349102575` | 205905161 / 2022 | $1,195,000 | yes, 29 rows |
| `202543179349101899` | 205905161 / 2024 | $1,178,000 | yes, 27 rows |
| `202123159349101632` | 205905161 / 2020 | $1,169,925 | yes, 29 rows |
| `202203149349101535` | 205905161 / 2021 | $1,104,516 | yes, 28 rows |
| `202531899349101308` | 562046037 / 2024 | $31,400 | no: the readers found no table on its pages |
| `202323179349102652` | 562046037 / 2022 | $20,800 | no: the same |
| `202213079349100601` | 562046037 / 2021 | $19,200 | no: the same |
| `201712579349100321` | 276529735 / 2016 | $27,000 | not read yet (tax year 2016) |
| `202343119349101864` | 481210113 / 2022 | not corrected | no, neither to $7,180,966 nor to one copy, $552,382 |
| `201732619349100303` | 382831525 / 2016 | not corrected | not read yet (tax year 2016) |

So five filings, 145 rows and $5,857,441, would load once the production
table and the work list are rebuilt. Nothing was loaded and the work list
was not rebuilt.

### 6.6 What the rule does not reach: copies that differ in a field

The extract's fill is per field. A group that holds only the rest of a
long text comes out as a second row with the first group's name and
amount, and the two rows are then two tuples. The one-tuple rule cannot
see it. Three shapes, all checked against the XML:

- 133703640 / 2021 (`202203449349100000`): group 1 is a grant to the
  Metropolitan Museum of Art, $64,350,000, status "501(C)(3)"; group 2
  holds only "PUBLIC CHAR", the rest of the status. The table has the
  grant twice: **$128.7M where $64,350,000 was paid.**
- 430666753 / 2016 (`201723209349100112`): one grant of $6,794,381 whose
  purpose runs over three groups. Three rows, $20.4M.
- 481210113 / 2022 (`202343119349101864`): one real group, one holding
  only the status "+-", twelve empty. Thirteen rows of $552,382 in two
  tuples and a $0 row.

Measured with the signature that carries over (no repeated row; two or
more rows with an amount, all with ONE amount and ONE recipient name; more
than one tuple; line 25, either column, equal to that amount once):

| | filer-years | rows with the amount | on the rows | one copy | counted too often |
|---|---:|---:|---:|---:|---:|
| line 25 = one copy | 45 | 216 | $205.98M | $79.73M | **$126.2M** |
| line 25 = every copy (legitimate repeats) | 155 | 507 | $14.61M | | |
| neither | 7 | 32 | $0.16M | | |

Seven of seven checked are the defect. That is more dollars than 6.3
repairs, most of them in one filing, and it covers the 14 placeholder
filings 6.5 leaves. It is not built here: which row to keep is a choice
when the copies differ (the real group is first in these and last in the
future-payment cases of 6.4), and it changes the matcher's input beyond
what this session was asked to build. Six more filer-years have the shape
under a repeated row, and 53 have one amount under several names with line
25 equal to one copy ($28.1M on the rows), not checked.

Built and checked on 2026-10-01: section 7.

### 6.7 After the merge

Nothing here was run. The steps, and who runs them. The numbers to expect
are of the rules of this section alone: with `field_fill` merged before the
rebuild, they are the ones of 7.7.

1. **Rebuild `privategrants_current`.** The matcher does it at the start
   of its next run. Alone, which Zein runs or approves:
   `python -m givingtuesday_datamart.current_grants --only privategrants_current`
   (25 to 35 minutes, the watch's minute and a half included). Its
   `DROP ... CASCADE` takes the matching views
   and `privategrants_current_w_recovered`; since PR #56 the command reads
   the policy the view shows before the rebuild and creates them all again
   for that policy. Expect 16,648,094 rows, the rule counts of 6.1, and
   the watch's line: `0 filer-years look doubled without a repeated basic
   row` (6.2). A WARNING there is to be looked at before the matcher runs.
2. **Then the future relation**, which reads the paid one:
   `python -m givingtuesday_datamart.current_grants --only privategrants_future_current`
   (under two minutes). Expect it unchanged: 441,358 rows.
3. **Then `work-list`, `load`, `check`** of `placeholder_recovery`, the
   last two under the policy the view shows (v2 on October 1; v3 after
   the reload Zein plans). Expect 22 filings with a smaller
   `placeholder_paid` and without `placeholder_exceeds_declared`, whatever
   the policy, and five more filings loading (6.5: measured on the stored
   readings under v2's verdicts and under v3's, the same five).
4. **`privategrants_w_recipients` and `unioned_grants`**, which products
   read, change only when the matcher reruns. That rerun is Zein's, about
   eight hours, and PR #56 (merged October 1) needs the same one: one
   rerun carries both.
5. **The regression gate** (`matching_regression_checks`) after it.

What the gate will see, from this change alone. Every tuple keeps at least
one copy, so the matcher's distinct name keys are the same and so are its
matches. The matched table loses exactly the matched rows among the 3,332:

| check | today | expected | why |
|---|---:|---:|---|
| `privategrants_w_recipients` rows | 7,578,795 | 7,578,518 | 277 matched rows leave, in 59 of the 2,703 filer-years (199 + 20 halved, 58 forward-filled); $29.2M of matched dollars |
| `unioned_grants` rows | | 277 fewer | the same rows |
| placeholder rows matched (ceiling 349) | 349 | 349 | none of the rows that leave is a matched placeholder row. The count does not fall: forward-filled "See Attached" rows were never matched |
| self-matches (ceiling 3,641) | 3,641 | 3,640 | one leaves |
| corporate, foreign, person rows | 14 / 2 / 0 | the same | none among the 277 |
| sentinels (Michael J. Fox, Johns Hopkins, Gates Trust) | | the same | none among the 277 |
| labeled-pair coverage | 63.1% / 94.5% | the same | no matched tuple leaves whole, so no funder-recipient pair is lost |
| matched rows and dollars against raw (hard rules) | 0 / 0 | 0 / 0 | rows only leave |

PR #56 moves the same checks for its own reasons; these are the moves to
subtract from what the rerun shows.

### 6.8 Scratch objects

Made for this proof and dropped at its end, each time it was run:
`public.scratch_pgc_rules` (the paid copy, 10 GB),
`public.scratch_pgfc_rules` (the future copy), `public.scratch_pgc_fy`,
`public.scratch_pgc_bf`, `public.scratch_pgc_fut` and
`public.scratch_pgc_changed` (the filer-year summaries the tables above
were counted from). `pf_current_scratch build` makes the first two
again.

## 7. Field fill, as built (2026-10-01)

6.6 left a fill the one-tuple rule cannot see: the copies of one grant
differ in a field. It is built as a second fill rule, `field_fill`, in
`current_grants._pf_current_ddl`, paid only, and proved on a scratch copy.
Nothing was rebuilt: `privategrants_current` holds the rows of the rule
before section 6, and no matcher ran.

### 7.1 The counts of 6.6, measured again

`python -m givingtuesday_datamart.exploratory.pf_doubling_basic_rows measure --sql data/exploratory/pf_field_fill.sql`
(about four minutes: one pass over `public.privategrants`, temp tables
only). Of 1,007,142 filer-years, 49,961 hold two or more rows with an
amount, all of them with one amount. Of those, the blocks that are not one
tuple, under the kept url, by what line 25 says of the amount:

| repeated row | names on the rows with the amount | line 25 | filer-years | rows with the amount | on the rows | one copy | counted too often |
|---|---|---|---:|---:|---:|---:|---:|
| no | one | = one copy | 45 | 216 | $205,981,712 | $79,729,241 | **$126,252,471** |
| no | one | = every copy (legitimate repeats) | 156 | 513 | $14,632,739 | | |
| no | one | neither | 5 | 11 | $125,000 | | |
| no | several | = one copy | 53 | 175 | $28,087,148 | $8,340,102 | |
| yes | one | = one copy | 6 | 18 | $438,204 | | |

The 45, the 53 and the 6 are 6.6's, to the dollar. 6.6 had 155 and 7 where
this has 156 and 5. Line 25 is now the kept url's own, and EIN 821352282 /
tax year 2022 states every copy there, where the row 6.6 read states
neither. And the name is now the three name columns as they are: one
filer-year whose names differ only in their case counts as several names.
Neither change touches the 45.

### 7.2 What the extract does

The mechanism, read off the XML group by group. A group of Part XV that
holds an `Amt` **and** anything beside it comes out as it is. Every other
group comes out as **one same row for the whole filing**: each field the
last value any group of the filing gives it, and the amount the block's
own total, `TotalGrantOrContriPdDurYrAmt`, which follows the groups (where
the filing pays one grant, that grant's amount). "Every other group" is
three kinds:

| the group holds | example | comes out as |
|---|---|---|
| `<Amt>0</Amt>` and nothing else | the empty groups of 6.3 | the filled row |
| no `Amt`, and a text | the rest of a status or purpose that runs over two groups: "PUBLIC CHAR" after "501(C)(3)" | the filled row, the text included: it is the last value of its field |
| an `Amt` and nothing else | EIN 844092575 / tax year 2024: a named group at `<Amt>0</Amt>`, then a group that is `<Amt>14000</Amt>` alone | the filled row |
| an `Amt` and a text or a name | a recipient the filer lists at $0. A group that holds a status and `<Amt>0</Amt>` | as it is: a $0 row, with only what the group holds |

So the filled rows of a filing are identical to each other, and a filing
that pays one grant holds it as at most two tuples: the real group's own
row, once, and the filled row, once per filled group. They are one tuple
where no later group changes a field (section 6.3's forward fill) and two
where one does (this section).

`pf_doubling_basic_rows xml` now states this beside each filing:
`xml_amounts` (groups with an amount of their own), `xml_filled` (groups
the extract fills), and `as_predicted`, whether the table's rows are, by
name and amount, the ones this reading gives (`extract_rows`). Run on
every filing checked for this section:

| checked | blocks | groups | as predicted, in the groups' order | in another order | twice (a doubled filing) | not as predicted |
|---|---:|---:|---:|---:|---:|---:|
| paid (3a) | 177 | 2,422 | 169 | 2 | 6 | 0 |
| future (3b) | 16 | 37 | 16 | 0 | 0 | 0 |

The future extract fills the same way with one difference: its filled row
takes the last amount a group states, not the block's total (EIN 346500595
/ tax year 2020, section 6.4: two amount-only groups, the second amount on
both rows). The names the 2012 schema gives the two blocks are read too
(`_OLDER_NAMES`): at least five of the filings checked use them.

The order of the rows is the groups' order in all but two blocks
(`202323189349101432`, `202020509349100212`), which hold the same rows in
another order.

### 7.3 The rule and its design points

Under the kept url, where the block is **not** one tuple: the rows with an
amount (a number other than 0) all carry ONE amount and ONE recipient
name, the filing itself holds two or more of them, and that amount, above
zero, EQUALS line 25 in column (d) or column (a). The filer-year keeps ONE
of those rows and carries `field_fill`.

It is forward fill's test on a wider shape, and the two never meet: a
block is one tuple or it is not. Forward fill's own text in the DDL is not
touched.

- *Which row is kept when the copies differ.* The first in the extract's
  order (`ctid`). In all 46 fills the rule takes, that row is the real
  group's own, every field of it. The order alone would not be proof (two
  blocks of 177 are out of order), and it does not have to be: by 7.2 the
  rows with the amount are at most two tuples (two in 39 of the 46, one in
  7), they carry one name and one amount by the rule's own condition, and
  the address is the same on every copy of all 47. What the choice decides
  is which status, purpose or relationship text the kept row shows. An
  alternative was measured and not built: "the tuple held once, else the
  first" names the real group wherever it stands. It agrees with "the
  first" on all 46, and costs a second window over the block.
- *The $0 rows beside the copies stay* (halved under a repeated row, as
  every row is). They are not copies of the grant. Each is a group of its
  own that the extract did not fill: 71 rows in 26 of the filer-years, 35
  of which name a recipient the filer listed at $0 (28 of them in one
  filing, EIN 263352699 / tax year 2012) and 36 of which name no one (a
  group that holds a text such as "+-" or "*" and `<Amt>0</Amt>`).
- *The name must match.* 53 filer-years hold one amount under several
  names with line 25 equal to one copy. All 53 were checked against the
  XML: 31 are fills (a named group without an amount of its own is filled,
  and the filled row carries the filing's last name: $18,580,222 counted
  too often) and **22 are N grants to N recipients**, each group with its
  own amount, in a return whose line 25 states one of them in one column
  (EIN 206197750 / tax year 2023: two grants of $500,000, column (d)
  $500,000, column (a) $1,000,000). Nothing in the table tells the two
  kinds apart, so the rule leaves all 53. And among the 31 fills the first
  row is the real group's in 24 only: without one name there is also no
  safe row to keep. The name is the three name columns as they are;
  folding the case changes no filer-year the rule takes.
- *Under a repeated row.* After `pair_collapse`, on what the filing itself
  holds, as forward fill. Of the 6 filer-years of 7.1, two are fills in a
  doubled filing: halved and then filled to one row
  (`pair_collapse+field_fill`: EIN 934026763 / tax year 2024, three groups,
  six rows; EIN 042133872 / tax year 2024, two groups, four rows). The
  other four are a doubled filing with ONE row with an amount beside $0
  rows: the filing holds the amount once, and they stay `pair_collapse`.
- *Legitimate repeats are untouched.* 156 filer-years have the shape with
  line 25 equal to every copy, to the dollar, and 5 state neither. None
  changes in the scratch copy.
- *Lineage.* `field_fill` is a `dedup_rule` value of its own, `+`-joined
  after `latest_url` and `pair_collapse` like the others.
- *One of the 47 is not the extract's doing.* EIN 320702437 / tax year 2024
  (`202501309349100310`) lists one recipient twice at $27,366, in two
  groups that each hold their own amount, with the purpose worded two ways.
  Part XV totals $54,732. Line 25 states $27,366 in column (a) and 0 in
  column (d). The rule keeps one row: what Part I says was paid, and not
  what Part XV lists. The table cannot tell it from a fill (two tuples, one
  name, one amount, line 25 equal to one). It is also a self-match in the
  matcher's output: the filer names itself.

### 7.4 The XML checks

Every filer-year the rule takes, with the scratch copy read in place of
the production table
(`pf_doubling_basic_rows xml OID ... --current scratch_pgc_fieldfill`).
Rows after = one row with the amount and the rows without one.

| EIN / tax year | object id | raw rows | with the amount | XML groups with an amount | XML groups filled | the amount | line 25 (d) / (a) | the copies differ in | rows after | with the amount | rule after |
|---|---|---:|---:|---:|---:|---:|---|---|---:|---:|---|
| 133703640 / 2021 | `202203449349100000` | 2 | 2 | 1 | 1 | 64,350,000 | 64,350,000 / 64,350,000 | status | 1 | 1 | `field_fill` |
| 430666753 / 2016 | `201723209349100112` | 3 | 3 | 1 | 2 | 6,794,381 | 6,794,381 / 6,794,381 | purpose | 1 | 1 | `field_fill` |
| 481210113 / 2022 | `202343119349101864` | 14 | 13 | 1 | 12 | 552,382 | 552,382 / 552,382 | status | 2 | 1 | `field_fill` |
| 481210113 / 2021 | `202222209349101422` | 14 | 13 | 1 | 12 | 522,546 | 522,546 / 522,546 | status | 2 | 1 | `field_fill` |
| 481210113 / 2018 | `201902279349100800` | 14 | 13 | 1 | 12 | 480,767 | 480,767 / 480,767 | status | 2 | 1 | `field_fill` |
| 481210113 / 2020 | `202101949349100200` | 14 | 13 | 1 | 12 | 477,322 | 477,322 / 477,322 | status | 2 | 1 | `field_fill` |
| 481210113 / 2019 | `202032109349100408` | 14 | 13 | 1 | 12 | 475,183 | 475,183 / 475,183 | status | 2 | 1 | `field_fill` |
| 481210113 / 2017 | `201822139349100802` | 14 | 13 | 1 | 12 | 458,836 | 458,836 / 458,836 | status | 2 | 1 | `field_fill` |
| 571217214 / 2020 | `202141349349100214` | 29 | 28 | 1 | 27 | 183,802 | 183,802 / 183,802 | relationship | 2 | 1 | `field_fill` |
| 900581077 / 2022 | `202342199349100214` | 2 | 2 | 1 | 1 | 3,675,000 | 3,675,000 / 3,675,000 | purpose | 1 | 1 | `field_fill` |
| 310983188 / 2013 | `201411419349100101` | 5 | 4 | 1 | 3 | 202,503 | 202,503 / 202,503 | purpose | 2 | 1 | `field_fill` |
| 310983188 / 2016 | `201731279349100153` | 5 | 4 | 1 | 3 | 188,295 | 0 / 188,295 | purpose | 2 | 1 | `field_fill` |
| 310983188 / 2014 | `201501239349100300` | 5 | 4 | 1 | 3 | 172,490 | 0 / 172,490 | purpose | 2 | 1 | `field_fill` |
| 310983188 / 2015 | `201621389349100417` | 5 | 4 | 1 | 3 | 169,017 | 0 / 169,017 | purpose | 2 | 1 | `field_fill` |
| 310983188 / 2017 | `201821259349100402` | 5 | 4 | 1 | 3 | 162,050 | 0 / 162,050 | purpose | 2 | 1 | `field_fill` |
| 310983188 / 2018 | `201902519349100300` | 5 | 4 | 1 | 3 | 131,925 | 0 / 131,925 | purpose | 2 | 1 | `field_fill` |
| 900581077 / 2023 | `202430269349100403` | 2 | 2 | 1 | 1 | 284,164 | 284,164 / 284,164 | purpose | 1 | 1 | `field_fill` |
| 382831525 / 2015 | `201602099349100500` | 9 | 3 | 1 | 2 | 76,040 | 76,040 / 76,040 | purpose | 7 | 1 | `field_fill` |
| 382831525 / 2014 | `201502179349100505` | 9 | 3 | 1 | 2 | 75,500 | 75,500 / 75,500 | purpose | 7 | 1 | `field_fill` |
| 934026763 / 2024 | `202543399349101109` | 6 | 6 | 1 | 2 | 60,000 | 60,000 / 60,000 | purpose | 1 | 1 | `pair_collapse+field_fill` |
| 382831525 / 2016 | `201732619349100303` | 9 | 3 | 1 | 2 | 55,040 | 55,040 / 55,040 | purpose | 7 | 1 | `field_fill` |
| 113040576 / 2024 | `202520769349100912` | 4 | 3 | 1 | 2 | 18,525 | 18,525 / 18,525 | relationship | 2 | 1 | `field_fill` |
| 201640639 / 2024 | `202531349349103308` | 6 | 2 | 1 | 1 | 35,000 | 0 / 35,000 | nothing | 5 | 1 | `field_fill` |
| 042921512 / 2019 | `202032679349100203` | 3 | 2 | 1 | 1 | 31,000 | 31,000 / 31,000 | nothing | 2 | 1 | `field_fill` |
| 042921512 / 2020 | `202230739349100218` | 3 | 2 | 1 | 1 | 29,000 | 29,000 / 29,000 | nothing | 2 | 1 | `field_fill` |
| 320702437 / 2024 | `202501309349100310` | 2 | 2 | 2 | 0 | 27,366 | 0 / 27,366 | purpose | 1 | 1 | `field_fill` |
| 461274956 / 2022 | `202441979349100569` | 2 | 2 | 1 | 1 | 19,849 | 19,849 / 19,849 | purpose | 1 | 1 | `field_fill` |
| 811849962 / 2018 | `201903199349105520` | 5 | 4 | 1 | 3 | 3,450 | 0 / 3,450 | status | 2 | 1 | `field_fill` |
| 042133872 / 2020 | `202133149349101698` | 2 | 2 | 1 | 1 | 9,000 | 9,000 / 9,000 | purpose | 1 | 1 | `field_fill` |
| 461274956 / 2021 | `202341809349101009` | 2 | 2 | 1 | 1 | 8,699 | 8,699 / 8,699 | purpose | 1 | 1 | `field_fill` |
| 042133872 / 2021 | `202222979349100422` | 2 | 2 | 1 | 1 | 8,500 | 8,500 / 8,500 | purpose | 1 | 1 | `field_fill` |
| 042133872 / 2022 | `202312969349100631` | 2 | 2 | 1 | 1 | 8,500 | 8,500 / 8,500 | purpose | 1 | 1 | `field_fill` |
| 042133872 / 2023 | `202442899349100959` | 2 | 2 | 1 | 1 | 8,000 | 8,000 / 8,000 | purpose | 1 | 1 | `field_fill` |
| 042133872 / 2024 | `202513149349101936` | 4 | 4 | 1 | 1 | 8,000 | 8,000 / 8,000 | purpose | 1 | 1 | `pair_collapse+field_fill` |
| 201902617 / 2022 | `202332139349100703` | 10 | 9 | 1 | 8 | 1,000 | 0 / 1,000 | nothing | 2 | 1 | `field_fill` |
| 546299823 / 2018 | `201902439349100500` | 6 | 5 | 1 | 4 | 2,000 | 0 / 2,000 | nothing | 2 | 1 | `field_fill` |
| 136193966 / 2018 | `201903179349100225` | 3 | 3 | 1 | 2 | 3,650 | 3,650 / 3,650 | purpose | 1 | 1 | `field_fill` |
| 136193966 / 2017 | `201833189349103608` | 3 | 3 | 1 | 2 | 3,550 | 3,550 / 3,550 | purpose | 1 | 1 | `field_fill` |
| 136193966 / 2019 | `202023179349103117` | 3 | 3 | 1 | 2 | 3,500 | 3,500 / 3,500 | purpose | 1 | 1 | `field_fill` |
| 136193966 / 2016 | `201713189349102721` | 3 | 3 | 1 | 2 | 2,836 | 2,836 / 2,836 | purpose | 1 | 1 | `field_fill` |
| 263297255 / 2024 | `202520669349100202` | 2 | 2 | 1 | 1 | 5,000 | 5,000 / 5,000 | purpose | 1 | 1 | `field_fill` |
| 363739087 / 2020 | `202212799349100226` | 2 | 2 | 1 | 1 | 5,000 | 5,000 / 5,000 | purpose | 1 | 1 | `latest_url+field_fill` |
| 461274956 / 2023 | `202531919349100303` | 2 | 2 | 1 | 1 | 3,393 | 3,393 / 3,393 | purpose | 1 | 1 | `field_fill` |
| 136193966 / 2020 | `202200259349100115` | 3 | 3 | 1 | 2 | 1,500 | 1,500 / 1,500 | purpose | 1 | 1 | `field_fill` |
| 462878283 / 2016 | `201741249349101129` | 3 | 2 | 1 | 1 | 2,500 | 0 / 2,500 | nothing | 2 | 1 | `field_fill` |
| 562093339 / 2014 | `201532049349100003` | 3 | 2 | 1 | 1 | 1,000 | 1,000 / 1,000 | purpose | 2 | 1 | `field_fill` |
| 263352699 / 2012 | `201323179349100107` | 30 | 2 | 1 | 1 | 180 | 0 / 180 | nothing | 29 | 1 | `field_fill` |

In 46 of the 47 the rows with the amount after equal the XML's groups with
an amount, one. The 47th is the filer's own double entry of 7.3 (two
groups with an amount, one row after). 30 differ in the purpose, 8 in the
status and 2 in the relationship. In 7 the copies are identical and the
block is more than one tuple only because $0 rows stand beside them.

Also checked: the four doubled filings that hold the amount once (EIN
272520016, 844092575 and 853214297 / tax year 2024, 853780748 / tax year
2025), all `pair_collapse` and one row with the amount after, and all 53
several-names filer-years, all `passthrough` or `latest_url` and unchanged.

### 7.5 The proof on a scratch copy

`pf_current_scratch build paid --suffix fieldfill` (29 minutes), then
`compare paid --suffix fieldfill`: every column of every row,
`dedup_rule` included, per filer-year, against the production table,
which still holds the rows of the rule before section 6. So the
comparison shows section 6's change and this one together:

| `dedup_rule` today | in the scratch copy | filer-years | rows today | rows after | dollars leaving | |
|---|---|---:|---:|---:|---:|---|
| `passthrough` | `pair_collapse` | 2,480 | 5,748 | 2,874 | $23,727,731 | section 6, as in 6.1 |
| `latest_url` | `latest_url+pair_collapse` | 23 | 162 | 81 | $583,858 | section 6 |
| `passthrough` | `forward_fill` | 103 | 403 | 103 | $63,864,652 | section 6 |
| `pair_collapse` | `forward_fill` | 94 | 164 | 94 | $12,619,857 | section 6 |
| `latest_url+pair_collapse` | `latest_url+forward_fill` | 1 | 1 | 1 | $0 | section 6 |
| `pair_collapse` | `pair_collapse+forward_fill` | 1 | 3 | 1 | $4,440 | section 6 |
| `passthrough` | `pair_collapse+forward_fill` | 1 | 6 | 1 | $228,040 | section 6 |
| `passthrough` | `field_fill` | 44 | 285 | 115 | $126,247,471 | **new** |
| `latest_url` | `latest_url+field_fill` | 1 | 2 | 1 | $5,000 | **new** |
| `pair_collapse` | `pair_collapse+field_fill` | 2 | 5 | 2 | $128,000 | **new** |
| | | **2,750** | **6,779** | **3,273** | **$227,409,049** | |

The seven lines of section 6 are 6.1's, to the row and the dollar, and the
other 1,004,392 filer-years are identical row for row. What this rule
changes, and nothing else:

| | filer-years | rows before | rows after | rows leaving | dollars before | dollars after | dollars leaving |
|---|---:|---:|---:|---:|---:|---:|---:|
| `field_fill` | 44 | 285 | 115 | 170 | $205,971,712 | $79,724,241 | $126,247,471 |
| `latest_url+field_fill` | 1 | 2 | 1 | 1 | $10,000 | $5,000 | $5,000 |
| `pair_collapse+field_fill` | 2 | 5 | 2 | 3 | $196,000 | $68,000 | $128,000 |
| | **47** | **292** | **118** | **174** | **$206,177,712** | **$79,797,241** | **$126,380,471** |

The 118 rows after are 47 rows with the amount and the 71 rows without
one. Checked row for row beside the count: the filer-years that carry
`field_fill` in the scratch copy are exactly the 47, each holds one row
with the amount and line 25 as its total, and the rows kept are the first
row with the amount and every row without one (106 tuples of 106). The two
filer-years under a repeated row are halved today by the old line-25 test
(6 rows to 3, 4 to 2), which is why their "rows before" are 5.

`privategrants_current` by rule, after sections 6 and 7:

| `dedup_rule` | filer-years | rows |
|---|---:|---:|
| `passthrough` | 983,578 | 15,791,161 |
| `latest_url` | 10,653 | 664,164 |
| `pair_collapse` | 12,547 | 190,851 |
| `latest_url+pair_collapse` | 117 | 1,426 |
| `forward_fill` | 197 | 197 |
| `field_fill` | 44 | 115 |
| `pair_collapse+field_fill` | 2 | 2 |
| `pair_collapse+forward_fill` | 2 | 2 |
| `latest_url+forward_fill` | 1 | 1 |
| `latest_url+field_fill` | 1 | 1 |
| | 1,007,142 | 16,647,920 |

16,651,426 rows today, 16,648,094 after section 6, 16,647,920 after this.
The watch of 6.2 finds nothing on the scratch copy. The future relation is
not touched: its DDL gains a constant `FALSE AS _field_fill`, and a
scratch copy built from it is `privategrants_future_current` row for row
(25,800 filer-years, 441,358 rows, $181,296,385,866 on both).

By filer, the dollars that leave: $64,350,000 the Metropolitan Museum
grant (EIN 133703640), $35,604,432 EIN 481210113 (six years of "See
Attached list", 72 rows), $13,588,762 EIN 430666753, $4,962,654 EIN
571217214, $3,959,164 EIN 900581077, $3,078,840 EIN 310983188, and
$836,619 over the other 17 filers.

### 7.6 Placeholder recovery: measured, not applied

`pf_current_scratch placeholders --suffix fieldfill`. The 14 work-list
filings 6.5 left over line 25 come down to one copy:

| filer (EIN) | filings | `placeholder_paid` today | from the scratch copy | still over line 25 |
|---|---:|---:|---:|---|
| 481210113, 2017 to 2022 | 6 | $38,571,468 | $2,967,036 | no |
| 382831525, 2014 to 2016 | 3 | $619,740 | $206,580 | no |
| 136193966, 2016 to 2020 | 5 | $45,108 | $15,036 | no |
| the 22 of 6.5 | 22 | $42,980,947 | $8,015,203 | no |
| | **36** | **$82,217,263** | **$11,203,855** | 0 of 36 |

All 36 would lose `placeholder_exceeds_declared` once the production table
and the work list are rebuilt. No list loads beyond the five of 6.5: of
the 14, two have a readable attachment. `202343119349101864` (481210113 /
2022) has no list that adds up to $552,382, and `201732619349100303`
(382831525 / 2016) is not read yet. Nothing was loaded and the work list
was not rebuilt.

### 7.7 After the merge, and what the regression gate will see

The steps are 6.7's, in the same order and by the same hands. With this
rule merged before the rebuild, expect in step 1 **16,647,920 rows** and
the rule counts of 7.5, in step 2 the future relation unchanged, and in
step 3 **36** work-list filings with a smaller `placeholder_paid` and
without `placeholder_exceeds_declared` (22 under section 6 alone), the
same five loading.

The gate, from this rule alone, on top of 6.7's table. Every row that
leaves has the name and the address of the row that stays, so the
matcher's keys are the same and so are its matches: a matched grant keeps
one row, and its copies leave.

| check | today | after section 6 | after this rule | why |
|---|---:|---:|---:|---|
| `privategrants_w_recipients` rows | 7,578,795 | 7,578,518 | 7,578,476 | 42 matched rows leave, in 12 of the 47 filer-years: $86,981,346 of matched dollars, $64,350,000 of it the Metropolitan Museum grant |
| `unioned_grants` rows | | 277 fewer | 42 fewer again | the same rows |
| placeholder rows matched (ceiling 349) | 349 | 349 | 349 | none of the 42 is one: the "See Attached" copies were never matched |
| self-matches (ceiling 3,641) | 3,641 | 3,640 | 3,639 | one leaves: EIN 320702437 / tax year 2024, the filer's own double entry (7.3) |
| corporate, foreign, person rows | 14 / 2 / 0 | the same | the same | none among the 42 |
| sentinels (Michael J. Fox, Johns Hopkins, Gates Trust) | | the same | the same | none among the 42 |
| labeled-pair coverage | 63.1% / 94.5% | the same | the same | every matched grant keeps a row, so no funder-recipient pair is lost |
| matched rows and dollars against raw (hard rules) | 0 / 0 | 0 / 0 | 0 / 0 | rows only leave |

Counted on the matcher's output as it stands, which holds every copy of
these 47 filer-years today (none is touched by section 6, and the two
under a repeated row are not matched). The 5 matched $0 rows of these
filer-years stay.

Second-order, through placeholder recovery: none measured. The 14 filings
of 7.6 lose a flag and load nothing new, so the recovered rows the matcher
reads (PR #56) are the same.

### 7.8 What neither fill reaches

- **A fill in a filing that pays more than one amount. This is the large
  one.** By 7.2 each filled group is one more row carrying the block's
  TOTAL, under the name of the filing's last group. With one grant that
  total is the grant, which is the two fills of 6.3 and 7.3. With several,
  the table holds the list, and then the whole list's total again once per
  filled group. Measured on 2026-10-01 with a signature that needs no XML
  (no repeated row; more than one amount; k >= 1 rows whose amount equals
  line 25 in either column; the other rows add up to that same amount):

  | rows carrying the total | filer-years | such rows | on those rows | line 25 |
  |---|---:|---:|---:|---:|
  | one | 585 | 585 | $298,222,916 | $298,222,916 |
  | two or more | 746 | 4,963 | $2,125,984,686 | $377,131,257 |
  | | **1,331** | **5,548** | **$2,424,207,602** | $675,354,173 |

  $2.42B counted too often where $675M was paid, tax years 2013 to 2024,
  the largest single filer-year $194.9M. 88 of the 1,331 were checked
  against the XML: 60 drawn across the list (30 with one such row, 30 with
  more) and the largest filer-year of each of the 28 largest filers. **87
  are the fill. One is not:** The Colorado Trust, tax year 2019
  (`202033189349104058`), whose XML has 373 groups, each with an amount of
  its own, one of them ("Zarlengo Foundation", $9,589,893) the sum of the
  other 372. The return itself carries the total as a grant, and the
  signature takes it all the same. Also the fill, and outside the
  signature: EIN 475268267 / tax year 2021, whose line 25 differs from its
  Part XV total. PR #62's description lists 20 of the 87 with their XML,
  their TEOS image and the queries that show them.
  **The matcher has matched 2,279 of the 5,548 rows, $1.34B**, in 509
  filer-years, to whatever the filing's last group names. Not built here:
  it is another rule with its own design points (no one-name condition
  can hold, a total the return itself carries looks the same in the table
  (one found, above), the same shape under a repeated row is not measured)
  and a much larger move of the matcher's
  input. It wants its own measurement, XML sample and proof.
- **Fills under several names** (7.3): 31 filer-years, $18,580,222. They
  are the same thing seen from the other side: named groups without an
  amount of their own.
- **The filer's own double entry** (7.3): taken by the rule, one
  filer-year, $27,366.
- **The future relation** (6.4): unchanged, two filings, $201,000.

### 7.9 Scratch objects

Made for this section and dropped at its end: `public.scratch_pgc_fieldfill`
(the paid copy), `public.scratch_pgfc_fieldfill` (the future copy), and
nine summary tables named `public.scratch_pgc_ff_*`. The suffix is this
section's own: `pf_current_scratch build` drops the table it is about to
build, and its default suffix is shared by every session on the database.

## 8. Files

- `givingtuesday_datamart/exploratory/pf_doubling_basic_rows.py` and
  `data/exploratory/pf_doubling_basic_rows.sql`: the measurement, one
  session, temp tables only; substitutes the rule's own content hashes so
  the tuples are the rule's tuples. `xml OID ...` is the spot check, and
  since section 7 it also says whether the table's rows are the ones the
  fill of 7.2 predicts.
- `data/exploratory/pf_field_fill.sql`: section 7.1's counts and the object
  ids of 7.4, run by the same script (`measure --sql`).
- `tests/test_pf_doubling_basic_rows.py`: the XML group classifier, the
  predicted rows and the SQL rendering.
- `givingtuesday_datamart/exploratory/pf_current_scratch.py`: the proof of
  section 6. `build` renders the production DDL under a scratch name,
  `compare` sets the copy beside production row for row, `placeholders` is
  6.5, `drop` removes the copies.
- `tests/test_pf_current_rules.py`: the rule's cases as fixtures, run
  against the paid and the future form of the one definition.
- This doc. `gt-duplication-report.md` section B is the upstream write-up
  the basic-row signal extends, and its section D is the forward fill;
  `current_grants.py`'s docstring is the rule.
