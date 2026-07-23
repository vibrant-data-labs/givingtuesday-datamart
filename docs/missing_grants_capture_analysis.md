# Missing Grants: How Much Grant Money Is Invisible in the Datamart, and Why

*Vibrant Data Labs — July 2026. Based on the April 24, 2026 refresh of
the Giving Tuesday (GT) 990 datamart; matching numbers reflect the
matcher fixes deployed in the July 23, 2026 matching rerun.*

---

## TL;DR

Nonprofit tax filings tell us two things about grantmaking: a **total**
("we gave $X this year") and an **itemized list** ("here's who got it").
When the itemized list is missing, incomplete, or can't be connected to
recipients, grant dollars become invisible to any analysis built on this
data — funding maps, funder profiles, ecosystem studies.

We measured that gap for every grantmaker in the datamart, 2020–2024, by
comparing each filer's self-declared totals against what actually appears
in the extracted grant tables. Headline findings:

- **$174B** of 990-filer grants (donor-advised-fund sponsors like
  Fidelity and Schwab, hospital systems, intermediaries) have **zero
  itemized rows** — mostly
  filings whose grant lists live in "Additional Data" attachments that
  didn't get extracted, plus year-specific extraction gaps — provably in
  tax year 2022, probably also 2021, undetermined for 2023–24.
- **$43B** of private-foundation grants hide behind **placeholder rows**
  ("SEE ATTACHMENT") that carry the full dollar total but name no
  recipients.
- **$26B** of 990 grants are fully itemized but carry **no recipient
  EIN** (the IRS ID number that uniquely identifies an organization) —
  the filer left that column blank — so they can't be connected to
  recipients by ID.
- On the private-foundation side, roughly **$132B** of itemized,
  US-based, nameable grants had failed VDL's recipient-matching step —
  our problem, not the data's. Two systematic causes were identified
  **and fixed during this work**: the matcher only compared
  organizations sharing a zip code, and its search list omitted private
  foundations and small organizations that file the short-form 990-EZ.
  The July 2026 matching rerun recovered **~$51B** of it; **~$81B**
  remains for further matcher improvement.
- A further **~$82B** is *structurally* unmapped no matter what anyone
  does: grants to individuals (scholarships, patient assistance), foreign
  organizations (WHO), and non-filers (Pfizer, government units).

The practical outputs are two prioritized lists (one for the GT Data
team, one for VDL's matching backlog), the SQL that generates them, and
an interactive explorer notebook for drilling from any flagged funder
down to the underlying filing and its source XML.

**→ GT Data team: see [the section addressed to you](#for-the-gt-data-team) below.**

---

## Background: the data and the problem

The datamart loads IRS e-file extracts published by Giving Tuesday.
The tables that matter here, in plain terms:

| table | what it is |
|---|---|
| `basic_fields` | One row per **990 filing** (public charities, DAF sponsors, hospitals): header info + financial summary, including total grants paid. |
| `basic_fields_pf` | Same, for **990-PF filings** (private foundations). |
| `grants_to_domestic_organizations` | The itemized grant list from 990 **Schedule I Part II** — one row per grant to a US organization. |
| `privategrants` | The itemized grant list from 990-PF **Part XV** ("grants paid during the year") — one row per grant. |
| `privategrants_w_recipients` | The subset of `privategrants` rows that VDL's matching algorithm successfully connected to a recipient organization. (990-PF grant rows have **no recipient EIN column** — the IRS form doesn't ask for one — so recipients must be matched by name and address.) |
| `unioned_grants` | The combined funder→recipient grant table downstream products use. |

The original exploration
(`givingtuesday_datamart/exploratory/debug_missing_grantors.py` and the
"Missing Grants Exploration" write-up) compared these tables against a
**labeled dataset** of ~76K known funder→recipient pairs and found many
funders missing. But that only covers funders we happen to have labels
for. The question this analysis answers: **how do we find *every* funder
whose grants are missing, without labels?**

## The key idea: filings audit themselves

Every filing self-declares its grand total of grants paid:

- **990:** Part IX line 1, "Grants to domestic organizations and
  governments" (datamart column `graallpaitot`), plus a Part IV checkbox
  (`grantoororga`) where the filer confirms Schedule I is required.
- **990-PF:** Part I line 25 column (d), "Contributions, gifts, grants
  paid" (datamart column `arecgpdcprps`) — the number that feeds the
  foundation's legally-required 5% payout.

The itemized list is supposed to add up to that total. So for every
funder and year — a "funder-year," the unit every table below counts in —
we compute:

> **declared** (what they said they gave) vs. **itemized** (what the
> grant tables contain) vs. **mapped** (what can be connected to a
> recipient)

and classify the gap. The filer's own numbers are the ground truth — no
labels needed. The labeled dataset then becomes a *validator*: 91% of
labeled private-foundation funders with 2020+ grants get flagged by at
least one of the checks below, confirming the approach catches what the
labels catch (and much more).

A note on reading the SQL below: the GT extract columns have compressed
names (`graallpaitot` = "GRAnts ALL PAId TOTal"); every column used here
is translated in prose. The database stores every value as text, so the
queries first confirm a value looks like a number before doing math on
it — that's what the `~ '^-?[0-9]…'` patterns are for.

---

## Part 1: 990 filers (Schedule I)

### The core check: declared grants with no itemized rows

The core query — filers who checked "yes, Schedule I required," declared
more than $5,000 of grants, but have zero rows in the Schedule I table
that year:

```sql
WITH itemized AS (
    SELECT filerein, taxyear::text AS taxyear, COUNT(*) AS n_rows
    FROM grants_to_domestic_organizations
    WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020'
    GROUP BY 1, 2
),
declared AS (
    -- one row per filing-year; DISTINCT ON drops amended-filing duplicates
    SELECT DISTINCT ON (filerein, taxyear)
           filerein, filername1, taxyear::text AS taxyear,
           CASE WHEN graallpaitot ~ '^-?[0-9]+(\.[0-9]+)?$'
                THEN graallpaitot::numeric END AS declared_amt,
           lower(grantoororga) IN ('true', '1', 'x', 'yes') AS sched_i_required
    FROM basic_fields
    WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020'
    ORDER BY filerein, taxyear, _ingested_at DESC, filesha256
)
SELECT d.*
FROM declared d
LEFT JOIN itemized i USING (filerein, taxyear)
WHERE d.sched_i_required
  AND d.declared_amt > 5000
  AND COALESCE(i.n_rows, 0) = 0;
```

**Result by tax year** (funders required to file Schedule I with >$5k
declared):

| tax year | required filers | missing all rows | % missing | declared $B | missing $B |
|---|---|---|---|---|---|
| 2020 | 43,193 | 630 | 1.5% | 155.4 | 13.9 |
| 2021 | 45,563 | 2,726 | 6.0% | 172.5 | 23.2 |
| 2022 | 48,149 | 19,429 | **40.4%** | 180.0 | 73.7 |
| 2023 | 45,575 | 7,237 | 15.9% | 159.3 | 55.9 |
| 2024 | 8,462 | 6,697 | 79.1% | 8.0 | 7.4 |

The year pattern is itself a finding, and the reasoning is worth spelling
out. Processing lag (filings that simply haven't been extracted yet) can
only get *worse* for more recent years. So:

- **2022 is provably a gap**: at 40.4% missing it is *worse than the
  younger 2023* (15.9%) — impossible under lag alone. Something skipped a
  large share of 2022 Schedule I data.
- **2021 is probably a smaller gap**: at this snapshot (April 2026),
  tax-year-2021 filings are ~4 years old — lag should be near the 2020
  baseline of 1.5%, yet 2021 sits at 6.0%. The excess (~2,000 filers) is
  most likely also missing extraction, not lag.
- **2023 and 2024 can't be attributed yet**: their rates mix real gaps
  with genuine processing lag, and this table alone can't separate the
  two. Treat 15.9% / 79.1% as upper bounds, not verdicts.

The steady-state "genuinely missing" floor is the 2020 level: ~1.5% of
filers — but **$13.9B**, because the missing set is dominated by the
largest DAF sponsors.

### Year-over-year patterns: separating real failures from processing lag

Grouping each funder's yearly hit/miss pattern:

- **Always missing** (every year they filed): 4,920 funders, $49.1B —
  true capture failures. Fidelity Charitable ($31.7B over 2020–22) and
  Schwab Charitable ($15.3B) lead. Their filings say "See Additional
  Data" in Schedule I and put the actual grant list (hundreds of
  thousands of grants) in an attachment.
- **Intermittent** (missing years sandwiched between good years):
  16,496 funders, $72.2B — provable extraction gaps, since neighboring
  years extracted fine.
- **Recent years only** (called `tail_missing` in the data files):
  10,720 funders, $52.7B — mostly processing lag; expected to resolve
  itself as newer filings get processed.

Verified against source documents: One Earth Philanthropy's 2022 filing
declares $2.18M with zero extracted rows while its 2020/2021/2023 filings
reconcile **to the dollar** — same filer, same format, so the 2022 gap is
on the extraction side.

### Second discovery: itemized grants with no recipient EIN

Row presence isn't the whole story. Schedule I has a recipient-EIN column
(`rteinorecipi`), and **~7–9% of all Schedule I rows (40–60k rows/year)
leave it blank** — concentrated by filer, not random. The check:

```sql
SELECT taxyear, COUNT(*) AS rows,
       ROUND(100.0 * COUNT(*) FILTER (
           WHERE NULLIF(TRIM(rteinorecipi), '') IS NULL) / COUNT(*), 1)
           AS pct_ein_missing
FROM grants_to_domestic_organizations
GROUP BY 1 ORDER BY 1;
```

Case study: the **Dollar General Literacy Foundation** itemized
1,650–2,042 grants/year through 2020, reconciling to the declared total
to the dollar — and not one row, in nine years, has a recipient EIN. We
pulled their source XML to settle it: the filing itself contains 1,839
`<RecipientTable>` blocks and **zero** `RecipientEIN` elements. The
filer simply doesn't report EINs (their grantees are largely public
schools, which often don't have their own). GT's extraction is faithful;
the data never existed. These grants — **$26B over 2020–24** — can only
be connected to recipients via name/address matching, the same machinery
the 990-PF side already requires.

### The unified 990 metric

Combining both: for every required funder-year,
**unmapped dollars = declared − (dollars in rows with a valid EIN)**.
That single number is the full declared amount for Fidelity (no rows)
*and* for Dollar General (rows, no EINs), and ~0 for healthy filers.
Full query: [`data/exploratory/sched_i_capture_priority.sql`](../data/exploratory/sched_i_capture_priority.sql).
Decomposition 2020–2024:

| issue | funder-years | declared | mapped by EIN | unmapped | % unmapped |
|---|---|---|---|---|---|
| `no_rows` — nothing itemized | 36,719 | $174.0B | $0 | $174.0B | 100% |
| `partial_rows` — itemized < 90% of declared | 40,019 | $59.9B | $32.8B | $27.1B | 45% |
| `ein_missing` — itemized but no EINs | 17,839 | $35.0B | $8.9B | $26.1B | 75% |
| `ok` — captured essentially completely | 96,365 | $406.2B | $402.1B | $4.1B | 1% |
| **total** | **190,942** | **$675.1B** | **$443.8B** | **$231.3B** | **34%** |

The `ok` row is the healthy baseline: 96k funder-years where the extract
accounts for the declared total almost entirely — the $4.1B still
unaccounted for is mostly the many small grants under $5,000, which
Schedule I doesn't require listing individually. The bottom line is the
total row: **a third of all declared Schedule I dollars since 2020 can't
currently be mapped to a recipient by EIN.**

---

## Part 2: Private foundations (990-PF)

The PF side needed different detectors, because of three things we
learned the hard way.

### Lesson 1: placeholder rows carry the full dollar amount

A naive "does itemized sum to declared?" check **passes** for exactly the
foundations that are missing. The Siegel Family Endowment's entire 2023
grant list in the data is one row — recipient name "SEE Attachment 15" —
whose amount, $26,907,603, equals the declared total to the dollar.
Placeholders must be detected by *shape* and *name pattern*, not by
totals:

```sql
-- placeholder detection: NAME fields only. "AVAILABLE UPON REQUEST" in
-- the ADDRESS field (e.g. Cigna) marks a real grant with an unmatchable
-- address — a matching problem, not a placeholder.
COUNT(*) FILTER (
    WHERE concat_ws(' ', sigocpyrpnam, sigocpyrbnbn1, sigocpyrbnbn2)
    ~* '(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|^\s*see\s*$'
) AS placeholder_rows
```

### Lesson 2: much of the money was never mappable in the first place

Sorting the unmatched pool by dollars initially put the Gates Foundation
and pharmaceutical patient-assistance foundations on top — and both are
red herrings:

- **Grants to individuals.** 990-PF grant rows can name a *person*
  (scholarships, patient assistance). Disney's 2022 "grants" are
  scholarship recipients; Bristol-Myers Squibb's patient-assistance
  foundation moves billions this way. A person can never match an
  organization. (**$54.5B**, 2020–24.)
- **Foreign recipients and non-filers.** The World Health Organization
  (Geneva, zip `1211`) and Pfizer Inc (`NC: NON-EXEMPT` in the filing's
  own recipient-status column) cannot appear in IRS 990 data. Detected
  via US-zip shape and the Part XV column (c) status codes. (**$32.7B**
  unmapped, 2020–24.)

This produces the fair yardstick, **matchable dollars**: grants that
name an organization, at a US address, where the recipient's own type
says it could file a 990 (a charity, rather than a government body,
foreign organization, or company), and that aren't placeholder rows.
Everything downstream is judged against that, and
**recoverable dollars = matchable − matched** is what matching work can
actually win back. Gates Foundation 2022 illustrates why this matters:
$5.75B declared shrinks to $2.2B matchable — and after the matcher fixes
below, $2.37B actually matched (slightly above our deliberately
conservative matchable estimate) — so the remaining "missing" ~$3.4B is
almost entirely foreign grants nobody can map to a 990.

### Lesson 3: when matching does fail, the causes are systematic

Full query: [`data/exploratory/pf_capture_priority.sql`](../data/exploratory/pf_capture_priority.sql).
Final decomposition (2020–2024, all PFs with declared grants, **after**
the July 2026 matcher fixes — before them, `unmatched_recipients` held
$132.4B recoverable and `ok` covered only $59.7B declared):

| issue | funder-years | declared | unmapped | recoverable |
|---|---|---|---|---|
| `unmatched_recipients` — matchable but unmatched | 202,343 | $159.7B | $86.7B | **$81.3B** |
| `individual_grants` | 70,550 | $54.7B | $54.5B | $0.1B |
| `aggregate_placeholder` | 9,872 | $43.2B | $42.7B | ~0 |
| `foreign_or_nonfiler` | 42,778 | $56.1B | $27.7B | $0.6B |
| `no_rows` — nothing itemized | 3,183 | $0.5B | $0.5B | $0.0B |
| `partial_rows` — itemized < 90% of declared | 2,313 | $1.2B | $0.9B | $0.1B |
| `ok` — matched essentially completely | 67,107 | $131.6B | $13.0B | $2.3B |
| **total** | **398,146** | **$447.0B** | **$226.0B** | **$84.4B** |

Reading note: the issue class labels each funder-year by its *dominant*
problem (over 50% of dollars), while the dollar columns are added up
grant by grant — so a funder-year with mixed giving contributes dollars
outside its label. That's why `foreign_or_nonfiler` shows $0.6B
recoverable: funders like Gates are labeled foreign-dominant, but the
minority of their grants that go to matchable US organizations count
toward matched and recoverable independently of the label. The
recoverable column is exact regardless of label; the label says where to
look first.

Note the contrast with the 990 side: PF **extraction** is nearly complete
($0.5B in `no_rows` vs. $174B for 990s); the PF losses are in
**matching**. Drilling into the matcher gap (originally $132B) identified
two systematic causes in VDL's own pipeline
(`givingtuesday_datamart/grant_matching.py`):

1. **The zip-code shortcut misses multi-campus organizations.** To keep
   the search fast, the matcher only compares funder-listed recipients
   against organizations *in the same 5-digit zip code*. The Bloomberg
   Family Foundation's grants to Johns Hopkins list the campus address
   (3400 N Charles St, zip 21218); JHU's 990 lists its headquarters
   (3910 Keswick Rd, zip 21211). Different zip codes, so the pair was
   never even considered, despite a perfect name. Bloomberg's ~$140M/yr
   of misses were essentially all this.
2. **The matcher's search list leaves out private foundations and small
   filers.** Recipients are only looked up among regular 990 filers
   (`basic_fields`). But foundation-to-foundation grants are legal and
   common — at least **$37B** of 2020+ grants go to recipients whose own
   status column says they are private foundations. The single largest:
   the Gates Foundation **Trust** transfers ~$6.7B/yr to the Gates
   Foundation (perfect name, perfect address, US zip) and matched
   nothing, because the Foundation files a 990-PF and was therefore not
   on the search list. Small organizations that file the short-form
   990-EZ are a third gap — GT publishes a 990-EZ extract we don't
   currently load.

Both are VDL-side issues — **not** data problems — and both were fixed
**on branch `match-pf-recipients`** (commit `af4354b`: private
foundations added to the search list, plus a second comparison pass that
pairs records with identical normalized names across different zips,
gated on same state; commit `12857f7`: name normalization for leading
"The" and punctuation). The matching rerun with these fixes completed
July 23, 2026, and **every matching number in this document now reflects
it**: matched rows grew from 5.40M to 6.04M (+11.9%), the Gates Trust
transfer and Bloomberg→Johns Hopkins now match, and recoverable dollars
fell from $132.4B to $81.3B. Known gaps remaining after these fixes:
990-EZ recipients (extract not loaded), out-of-state lockbox addresses
(the cross-zip pass requires same state), non-identical name variants at
non-matching zips, and missing/placeholder addresses (Cigna's
"AVAILABLE UPON REQUEST" rows still mostly fail).

---

## Validation: checking against known funder→recipient pairs

The labeled dataset (~76K funders, **646,167 distinct funder→recipient
pairs** from Candid) is an independent ground truth: every pair is a
grant relationship we know existed. How many appear in `unioned_grants`?

| funder type | labeled pairs | covered | % covered |
|---|---|---|---|
| 990-PF | 366,398 | 226,253 | 61.8% |
| 990 | 270,365 | 228,916 | 84.7% |
| not in e-file | 9,404 | 0 | 0% |
| **total** | **646,167** | **455,169** | **70.4%** |

(Before the July 2026 matcher fixes, PF coverage was 57.3% and the total
67.9% — the rerun recovered 16,265 known pairs in one shot.)

The 990-vs-PF split independently confirms the whole analysis: 990
coverage is high because extraction losses concentrate in a few huge
funders, while PF coverage is low because matching losses spread across
tens of thousands of funders.

Each of the 190,998 still-missing pairs was then classified by **direct
evidence** — does the funder itemize rows at all, and if so, does a row
naming *this specific recipient* exist? (Each recipient's official name
comes from its own tax filing; names are simplified — lowercased,
punctuation removed — and compared on whether they start the same way or
one contains the other.)

| cause | pairs | reading |
|---|---|---|
| No row found for the recipient (PF + 990) | 102,303 | Ambiguous mix: partial capture, grants predating e-file coverage (the labeled pairs carry no dates), and limits of the name test |
| Recipient's row **present, match failed** (PF) | 31,237 | Hard evidence of matcher failures — the row is sitting in `privategrants` with the recipient's name on it. Was 46,842 before the July 2026 fixes |
| Funder has only placeholder rows (PF) | 17,789 | "SEE ATTACHMENT" filings |
| Funder has zero itemized rows | 15,262 | Fidelity-style attachment filings + the 2021–22 gaps |
| Funder not in e-file | 9,404 | Paper filers, government entities |
| Row present but recipient EIN blank/mismatched (990) | 8,678 | Dollar-General-style filer omissions and EIN typos |
| Recipient name unknown (no e-file header) | 6,325 | Couldn't test — recipient never filed |

Two sanity checks worth noting: the classification found **zero** pairs
where a Schedule I row carries the correct recipient EIN yet the pair is
missing (if the EIN were there, the pair would be covered — the classes
are internally consistent), and the row-present-but-unmatched pairs are
the same matcher failures quantified in Part 2, seen one pair at a time.
This validation ran before and after the matcher fixes, which is the
cleanest measure of what they accomplished: PF pair coverage climbed
from 57.3% to 61.8%, and the row-present-but-unmatched pool shrank by a
third (46,842 → 31,237). The ~31k that remain are the harder residue —
missing addresses, name variants below the thresholds — and further
matcher work can be measured the same way.

## What it means: three buckets, three owners

| bucket | dollars (2020–24) | owner | fix |
|---|---|---|---|
| Data capture: attachments not extracted, placeholder rows, partial extractions, year-specific Schedule I gaps (2022 proven, 2021 probable) | **~$242B** at issue ($195B excluding probable lag) | **GT Data team** | Parse "Additional Data" attachments; investigate the 2021–2023 batches |
| Recipient matching: formatting edge cases, missing addresses, name variants (the zip-code shortcut and incomplete search list are already fixed — $51B recovered July 2026) | **~$81B** recoverable | **VDL** | Further matcher improvements |
| Structural: individuals, foreign orgs, non-filers, EIN-less recipients that don't file | **~$82B+** | nobody (inherent) | Report as coverage caveat, don't chase |

Any funding-flow number built on `unioned_grants` undercounts by these
amounts, non-uniformly — DAF sponsors and the largest private
foundations are the most affected, which biases any "who funds what"
analysis against precisely the biggest funders.

---

## For the GT Data team

This section is self-contained — everything you need is in two CSV files
and this explanation.

### What we're asking

The datamart's Schedule I and 990-PF grant extracts are missing itemized
grant data that the filings themselves say should exist. We built a
prioritized list of the specific filer-EIN × tax-year combinations where
**the extracted rows don't account for the filing's declared grant
total**. We filtered it to include *only* cases that look like source
data / extraction issues — we explicitly removed everything caused by our
own recipient-matching step, so this list should not contain noise from
our side.

### The files

- **`data/exploratory/gt_team_priority_by_funder.csv`** — the headline
  list: 45,540 EINs, one row per funder, with affected years, issue
  type(s), whether the funder appears in the Candid labeled data
  (`in_labeled_set` — true for 15,318 EINs carrying $158.1B of the
  dollars at issue, meaning known funder→recipient pairs exist to
  verify a re-extraction against), and total dollars at issue ($195.3B).
  Sorted by dollars — working top-down maximizes recovered dollars per
  filing examined.
- **`data/exploratory/gt_team_priority.csv`** — the same list broken out
  to one row per funder per tax year (92,106 rows), including a
  `lag_risk` flag on
  ~9.5k rows ($46.6B) from tax years 2023–24 that are probably just
  filings you haven't processed yet (excluded from the headline list).

### The three issue types, with verified examples

**`no_rows`** — the filing declares grants (and checks the "Schedule I
required" box) but the extract has no grant rows for that year.
- *Fidelity Investments Charitable Gift Fund (EIN 110303001)*: ~$10B/yr
  declared, 0 rows, every year. Schedule I Part II in the e-file says
  "See Additional Data"; the itemized list is in the Additional Data
  attachment.
- *One Earth Philanthropy (852588841)*: extracts perfectly in 2020, 2021
  and 2023 (reconciles to the dollar) but has 0 rows in 2022 — evidence
  for a year-specific gap rather than filer behavior. **The missing rate
  by tax year is 1.5% (2020), 6.0% (2021), 40.4% (2022), 15.9% (2023).
  2022 is provably a gap — it is worse than the *younger* 2023, which
  processing lag alone cannot produce. 2021's elevation over the 2020
  baseline also looks like missing extraction (those filings are ~4 years
  old — too old for lag). 2023's rate is some mix of lag and gap we
  can't separate from our side — worth checking against your processing
  logs for all three years.**

**`partial_rows`** — rows exist but sum to less than 90% of the declared
total.
- *Dollar General Literacy Foundation (621546736)*: ~1,900 rows/yr
  through 2020 reconciling exactly; from 2021 on, row counts collapse to
  ~450–490 and totals run ~$3.5M short, with 2022 and 2024 at zero. It
  looks like the long tail of their grant list moved into an attachment
  that is sometimes partially parsed.

**`aggregate_placeholder`** (990-PF only) — the extracted "grant list" is
one or two rows whose recipient name is a placeholder ("SEE Attachment
15", "SEE ATTACHED", "VARIOUS - SEE ATTACHED") carrying the entire
declared amount.
- *Siegel Family Endowment (451742989)*: one row/yr, name "SEE
  Attachment 15", amount = declared total exactly.
- *Genentech Patient Foundation (460500266)*: $12.8B over four years
  behind placeholders — the largest single placeholder filer.

### How each number was computed

For every filer-year: take the filing's own declared total (990 Part IX
line 1 / 990-PF Part I line 25d), subtract what the extracted rows sum
to. `dollars_at_issue` is the declared total when there are no usable
rows (including placeholder rows), or the shortfall when rows are
partial. The full, runnable SQL is in
`data/exploratory/sched_i_capture_priority.sql` and
`data/exploratory/pf_capture_priority.sql`; the numbers above reproduce
from those two files against the datamart tables.

### Verifying any row yourself

Every flagged filing's `url` (in the underlying capture CSVs) points to
the source XML in the data lake. The pattern to check: does the XML's
Schedule I / Part XV table contain real recipient rows (extraction gap on
the extract side), or does it contain "See Additional Data" with the real
list in an `<AdditionalData>` section or paper attachment (parsing gap)?
Fidelity's and Siegel's XMLs are the fastest confirmations of the second
pattern; One Earth 2022 vs 2023 is the fastest confirmation of the first.

### One more heads-up (not in the priority list)

~7–9% of Schedule I rows have no recipient EIN. We verified (Dollar
General's XML) that this is usually the *filer* omitting it, not the
extract — so we did not include it as an extraction issue. Flagging it
here in case you see the same pattern and wonder.

---

## Artifacts and reproduction

All paths relative to the repo root.

| artifact | what it is |
|---|---|
| `data/exploratory/sched_i_capture_priority.sql` / `.csv` | The 990 analysis: every required funder-year with declared vs itemized vs EIN-mapped dollars and issue class (190,942 rows). |
| `data/exploratory/pf_capture_priority.sql` / `.csv` | The 990-PF analysis: declared vs itemized vs matchable vs matched vs recoverable, with the full set of issue types (398,146 rows). |
| `data/exploratory/gt_team_priority.csv` / `gt_team_priority_by_funder.csv` | The GT-facing lists (data-capture classes only, lag-flagged). |
| `data/exploratory/labeled_missing_pairs_classified.csv` | Every labeled funder→recipient pair missing from `unioned_grants`, classified by per-pair evidence (see Validation). |
| `data/exploratory/missing_990_ein_year_priority.sql` / `.csv` | Earlier 990-only list with the always/intermittent/recent-only year patterns. |
| `givingtuesday_datamart/exploratory/build_capture_priority.py` | **The single reproduction entry point.** Rebuilds every CSV above and prints every summary table in this document. |
| `givingtuesday_datamart/exploratory/explore_sched_i_capture.py` | Marimo explorer: three tabs (990, 990-PF, GT hand-off). Filter → click a funder-year → see the filing row, declared amounts, source-XML link, and every grant line item anchored to that year. Run from the repo root: `marimo edit givingtuesday_datamart/exploratory/explore_sched_i_capture.py` (env: `pyenv activate vdl-tools-312`). |

To regenerate everything after a data refresh (from the repo root, env
`vdl-tools-312`):

```bash
python -m givingtuesday_datamart.exploratory.build_capture_priority
```

Steps run in order: `sched` and `pf` execute the two `.sql` files in the
repo (the source of truth for the logic) and rebuild their CSVs; `gt`
derives the GT-facing lists (data-capture issue types,
`dollars_at_issue`, the year-over-year patterns, `lag_risk`, and the
per-funder summary); `labeled` re-measures labeled-set pair coverage and
re-classifies every missing pair. Use `--steps sched,pf` etc. to run a
subset. The `labeled` step takes ~15 minutes (it checks ~650K known
pairs against `unioned_grants` and scans both grant tables); the others
a few minutes each.

Supporting infrastructure added during this work: `filerein` indexes on
`grants_to_domestic_organizations` and `privategrants` (declared in
`givingtuesday_datamart/sources/registry.py` so the data-loading
pipeline recreates them) and on `privategrants_w_recipients` (created
manually — recreate after matching reruns, since that table is rebuilt
by the matching pipeline).

## Caveats and limitations

- **Thresholds are tunable.** "Partial" = itemized < 90% of declared;
  placeholder/individual/foreign classes trigger at 50% of declared
  dollars. Edge cases near the thresholds exist; the labeled-set check —
  91% of known private-foundation grantmakers get flagged — says the
  defaults are reasonable.
- **Declared totals are self-reported.** Filers make errors; the small
  leftover gap in the `ok` class ($4–8B) reflects legitimate small
  grants under the $5,000 listing threshold, rounding, and amended
  filings (we keep only the latest filing per funder per year).
- **Tax-year 2023–24 rows are provisional.** Processing lag inflates
  recent-year misses; the `lag_risk` flag marks them but the boundary is
  a judgment call.
- **The pharma patient-assistance foundations** (J&J PA, Sanofi Cares)
  file business-style recipient rows for what is likely donated medicine
  (valued in dollars, but not cash grants to organizations); they appear
  in the VDL matching bucket with billions "recoverable" that probably
  isn't. Inspect before acting on them.
- **Foreign recipients are detected by zip-code format** (five digits =
  US), because the country column is blank even for heavy international
  granters (verified for Gates). Foreign recipients whose postal codes
  happen to look like US zips slip through.
- **Negative amounts** (grant refunds/adjustments, e.g. Gates Trust) are
  included in every total and reduce it.
