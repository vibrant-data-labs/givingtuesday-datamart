# Duplicate grant line items in the 990 grants extracts

**To:** GivingTuesday Data Commons team
**From:** Vibrant Data Labs (Zein Tawil)
**Date:** July 31, 2026
**Extracts reviewed:** Schedule I grants ("Grants to Domestic Organizations") batch `2026_06_25`; 990-PF grants ("Current Grants" / Part XV) batch `2026_06_16`

## Summary

While reconciling grant totals against filers' own declared totals, we
found that both grants extracts contain large volumes of duplicated line
items — the same grant appearing two (sometimes four) times. Three
distinct mechanisms are involved, and they are additive (a fourth, found
in September 2026 and not a duplication of filings, is section D):

1. **Every filing version is included.** When a filer amends a return,
   the extracts carry the grant line items of *both* the original and
   the amended filing for the same tax year, with no column that marks
   which filing is current.
2. **Whole blocks are emitted twice.** Some filer-years' entire grant
   block appears twice verbatim. On the 990-PF side this affects
   thousands of filers in the most recent batches and is *not related to
   amendments* — it looks like a processing defect introduced around the
   taxyear-2024 batch.
3. **One provenance error.** In at least one case (Fidelity Charitable's
   2021 Schedule I) the original and amended filings' blocks are both
   present but both stamped with the *original* filing's `url` and
   `filesha256`, so they cannot be separated using the file metadata.

Across the Schedule I extract this inflates 2020+ grant dollars by
roughly **$36B (~5%)**; on the 990-PF side the recent-batch doubling
alone adds roughly **$7B**, concentrated in taxyear 2024. Per-filer, the
distortion is much larger — an affected filer's totals read at exactly
2× reality.

We have working per-filer detectors and complete lists of affected
filer-years for every category, and are happy to share them.

## How we found it

We load the extracts into Postgres without modification and routinely
reconcile the summed line items for a filer-year against the total the
filer itself declared on the return (990 Part IX line 1; 990-PF Part I
line 25, column (d)). Two observations started the investigation:

- **Fidelity Charitable, tax year 2021**: the Schedule I line items sum
  to ~$20B in cash grants against a declared total of $11.39B — almost
  exactly double.
- After removing duplicate rows, dozens of large filer-years reconciled
  to their declared totals **to the dollar**, which rules out
  coincidence and filer error.

We first verified the duplication is present in the source extracts, not
introduced by our loading: our loaded row counts match the extract files
exactly (e.g. 8,991,013 Schedule I rows in batch `2026_06_25`), and the
duplicate rows are visible in the raw files themselves.

For the Fidelity case we went one step further and pulled the two
underlying IRS e-file XMLs — `202321309349304807_public.xml` (original)
and `202430459349302913_public.xml` (amended, `AmendedReturnInd` set).
The extract's two physical blocks match the original and amended XML
exactly on (recipient EIN, cash amount) multisets — proving the extract
contains both filings' line items while labeling both with the original
filing's `url`/`filesha256`.

## What we found, in detail

### A. Multiple filing versions per filer-year (both extracts)

When both an original and an amended return exist for a filer-year, the
extracts include the grant line items of each, usually distinguishable
only because each version carries its own `url`.

- Schedule I: **3,084 filer-years** with 2+ urls.
- 990-PF grants: **10,772 filer-years** with 2+ urls (largest:
  EIN 46-2626883, tax year 2023 — 363,676 rows).

Any consumer that sums line items by (EIN, tax year) — the natural
grouping — double-counts these filers. Nothing in the data flags that a
version supersedes another.

### B. Verbatim block doubling under a single filing

Some filer-years' entire block appears exactly twice under one `url`.

- **Schedule I: 634 filer-years (~$18B of raw line-item dollars).**
  Nearly all of the larger cases (377 of 378 with ≥40 rows) have an
  amended filing on record whose content is identical to the original —
  consistent with the amendment's identical block being emitted under
  the original's label.
- **990-PF: 6,277 filer-years, ~$7.0B of inflation — and only 5 of the
  6,277 have any amended filing on record.** This is a different
  mechanism from (A): the doubling is concentrated in the newest
  batches — **5,506 of the filer-years are tax year 2024** ($6.59B),
  545 are 2025, 190 are 2023, and years ≤2022 have single-digit counts.
  That pattern points to a defect in recent batch processing rather
  than anything filers did.

  The decisive evidence that these are spurious: half the block's sum
  equals the filer's declared total *to the dollar*. Examples:

  | Filer EIN | Tax year | Line items sum to | Filer declared |
  |---|---|---|---|
  | 23-7093598 | 2024 | $712,181,958 | **$356,090,979** (= exactly half) |
  | 52-1512330 | 2024 | $374,001,262 | **$187,000,631** (= exactly half) |
  | 47-2107200 | 2024 | $1,397,879,334 | **$698,939,667** (= exactly half) |

  The last case (Sergey Brin Family Foundation) also shows that the
  doubling copies the whole block: line items the filer legitimately
  listed twice appear four times.

### C. Provenance mislabeling (confirmed once, may exist elsewhere)

Fidelity Charitable (EIN 11-0303001), tax year 2021: both the original
filing's block (64,828 rows) and the amended filing's block (65,494
rows) are present, and **both carry the original filing's `url` and
`filesha256`**. Unlike categories A and B, this cannot be repaired from
the extract alone — the version labels are wrong at the source. This is
the case we verified against the raw IRS XMLs.

### D. Empty grant groups emitted as copies of a real one (990-PF, both grant extracts)

*Added September 30, 2026. Batch `2026_06_16`, the 3A (paid) and 3B
(approved for future payment) extracts.*

Some returns enter one real grant group in Part XV and then a run of
empty groups: `<Amt>0</Amt>` and nothing else. In the extract every one of
those groups comes out as a full row carrying the real group's values,
amount included, so the grant is in the table once per group. This is not
categories A to C: the filing is in the extract once, under one url and
one sha, and nothing was amended.

Three filings to open (the XML is the copy in your own data lake,
`EfileData/XmlFiles/<object id>_public.xml`):

| Filer EIN / tax year | Object id | In the XML (`GrantOrContributionPdDurYrGrp`) | `TotalGrantOrContriPdDurYrAmt` and line 25(d) | In the 3A extract |
|---|---|---|---|---|
| 39-6040395 / 2018 | `201911339349100431` | 1 group, Greater Milwaukee Foundation, $1,069,215; then 20 groups of `<Amt>0</Amt>` | $1,069,215 | 21 identical rows of $1,069,215: **$22,453,515** |
| 48-1210113 / 2016 | `201712869349100601` | 1 group, "See Attached list", $489,885; then 13 empty groups | $489,885 | 14 identical rows: **$6,858,390** |
| 20-5905161 / 2023 | `202423199349102497` | 1 group, "SEE ATTACHED SCHEDULE", $1,210,000; then 4 empty groups | $1,210,000 | 5 identical rows: **$6,050,000** |

How far it reaches, measured on the paid extract by the one signature
that is safe to test from the tables alone: a filer-year whose rows are
one line item repeated N times while the return's own line 25 (column (d),
or column (a) where (d) is 0) equals ONE copy. **200 filer-years, 745
rows where 200 belong, about $98M of grant dollars that were never
paid.** We checked 24 of them against the XML and all 24 are one real
group plus empty ones. The same signature separates them cleanly from
filers that really made N identical grants: those declare all N on line
25 (122 filer-years, to the dollar).

Three things about the mechanism that may help find it:

- **The fill is per field, not per row, and a continuation line sets it
  off.** Where a group carries one field of its own, the row takes that
  field and copies the rest. The largest case we found is 13-3703640 /
  2021 (`202203449349100000`): group 1 is a grant to the Metropolitan
  Museum of Art, $64,350,000, status "501(C)(3)"; group 2 holds only
  "PUBLIC CHAR", the rest of the status text. The extract has the grant
  twice, once with each status: **$128,700,000 where $64,350,000 was
  paid.** 43-0666753 / 2016 (`201723209349100112`) is one grant of
  $6,794,381 whose purpose runs over three groups, and comes out as three
  rows. 48-1210113 / 2022 (`202343119349101864`) has one real group ("See
  Attached list", $552,382), one group holding only a status, and 12 empty
  ones: 13 rows of $552,382. Such filings do not show as one repeated line
  item, so the count above leaves them out. Counted on their own (every
  row with an amount carries the same amount and the same recipient,
  while line 25 equals that amount once): 45 more filer-years and about
  $126M more, 7 of 7 checked.
- **It does not depend on the order of the groups.** In the 3B extract
  the empty groups come first: 47-5268267 / 2021
  (`202233189349105123`) has two empty groups and then one real one
  (SEWA International, $75,500), and the extract has the real one three
  times ($226,500). 46-6175348 / 2023 (`202423179349102672`) is the same
  with one empty group.
- **A real amount can be overwritten.** 34-6500595 / 2020
  (`202112959349100711`), 3B: two groups that carry only an amount,
  $541,462 and $278,781 (the return's total is $820,243). The extract has
  $278,781 twice: $557,562, which is $262,681 too little. So the defect
  can lose dollars as well as add them.

A group-by-group emit, with a field left empty when the group does not
carry it, would remove all three.

## What would fix it upstream

In order of value to consumers:

1. **Emit one filing version per filer-year (the current one), or add an
   explicit version/amended indicator column** so consumers can filter
   deterministically. Today the only signal is the `url`, and nothing
   marks which url is current.
2. **Investigate the 990-PF batch doubling (category B).** Its
   concentration in the 2024-batch, its independence from amendments,
   and the exact-half reconciliation should make it findable in the
   extract-generation step. This is the highest-dollar recent issue and
   presumably affects every downstream consumer of the PF grants
   extract.
3. **Fix version labeling (category C)** so each block carries the
   `url`/`filesha256` of the filing it actually came from.
4. **Emit each grant group with its own fields only (category D).** A
   group that holds `<Amt>0</Amt>` alone should come out as a $0 row, or
   not at all, and never as a copy of another group.

We can provide: complete affected filer-year lists for every category
(CSV), the SQL detectors we use, and before/after totals for
verification. We've also implemented interim dedup rules on our side and
are glad to share the methodology and edge cases (e.g. distinguishing
spurious doubles from filers that legitimately repeat identical grants).

## Appendix: compact detector (Schedule I)

Flags filer-years with multiple filing versions, verbatim doubles, or
sums far above the declared total. Column names as in the extract.

```sql
WITH g AS (
    SELECT filerein, taxyear, COUNT(*) AS n_rows,
           COUNT(DISTINCT md5(ROW(rteinorecipi, rtrnbbnline11, retaamofcagr,
                                  rtaoncassist, rectabaddcit, rectabaddsta,
                                  retapuofgrra)::text)) AS n_distinct,
           COUNT(DISTINCT url) AS n_urls,
           SUM(CASE WHEN retaamofcagr ~ '^-?[0-9]+(\.[0-9]+)?$'
                    THEN retaamofcagr::numeric ELSE 0 END) AS cash
    FROM grants_to_domestic_organizations GROUP BY 1, 2
), b AS (
    SELECT filerein, taxyear, COUNT(DISTINCT filesha256) AS n_filings,
           MAX(CASE WHEN graallpaitot ~ '^-?[0-9]+(\.[0-9]+)?$'
                    THEN graallpaitot::numeric END) AS declared
    FROM basic_fields GROUP BY 1, 2
)
SELECT g.*, b.n_filings, b.declared
FROM g LEFT JOIN b USING (filerein, taxyear)
WHERE g.n_urls > 1                                      -- multiple versions
   OR (g.n_rows = 2 * g.n_distinct AND g.n_rows >= 40)  -- verbatim double
   OR (g.n_urls = 1 AND b.n_filings >= 2                -- mislabeled version
       AND g.cash > 1.5 * b.declared);
```

The 990-PF detector is analogous (declared total = Part I line 25
column (d)); for the category-B doubling, the discriminating test is
that **half** the block sum reconciles with the declared total better
than the full sum does.
