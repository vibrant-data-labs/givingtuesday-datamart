# Duplicate grant line items in the 990 grants extracts

**To:** GivingTuesday Data Commons team
**From:** Vibrant Data Labs (Zein Tawil)
**Date:** July 31, 2026
**Extracts reviewed:** Schedule I grants ("Grants to Domestic Organizations") batch `2026_06_25`; 990-PF grants ("Current Grants" / Part XV) batch `2026_06_16`

## Summary

While reconciling grant totals against filers' own declared totals, we
found that both grants extracts contain large volumes of duplicated line
items — the same grant appearing two (sometimes four) times. Three
distinct mechanisms are involved, and they are additive:

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
