# Recovering the Grants Behind "SEE ATTACHMENT"

Vibrant Data Labs, September 25, 2026; updated September 28.

Status: measured on a frame of 1,000 filings under the page gate on
September 23 and 24, 2026, for $222 in model calls, after a sample of 100
filings read four times. On September 28 the rules for loading were
settled, and the work list moved from GivingTuesday's one-off extract to
the loaded tables, where it is 24,518 filings across tax years 2009 to
2025. The work-list command and the loader were built the same day. The
frame's lists are loaded: 398 filings, 221,969 grants, $7.03B. The
matcher does not read them yet, and no filing outside the frame has been
fetched.

This is the findings document. How the method was built and measured,
step by step, is the engineering log,
[placeholder_recovery_pipeline.md](placeholder_recovery_pipeline.md). How
to run it, and the tables it writes, is
[placeholder_recovery_operations.md](placeholder_recovery_operations.md).
For readers outside the team there is an
[executive summary](https://claude.ai/code/artifact/2544935f-55dc-4744-921c-ff2dd00aacc4)
and the [production diagram](https://whimsical.com/FXWZBu4FE9RzpMYqWmupqd).

## Summary

- Private foundations must list every grant on Form 990-PF. From 2020
  to 2025, 12,265 filings put a "see attached" row there instead: 2.7%
  of foundations that report grants paid, 7.2% of the dollars on their
  Part I line 25, $40.1B. The list exists only as pages in the IRS's scanned image of
  the return.
- The method fetches that image, has vision-language models read the
  attached pages, and keeps a list only when it adds up to the total the
  foundation declared. A page is loaded only when two different models
  agree on its rows; four are tried in turn.
- On the 1,000-filing frame, every filing over $10M plus a 6% sample of
  the rest, it recovered the paid lists of 401 filings: $7.13B of the
  $14.57B of paid grants on the frame's placeholder rows, 49.0%. That is
  under the loading rule of September 28: the pages read are the only
  source, and the paid amount the only target. Under the rule the frame
  was first measured by, which also took rows from GivingTuesday's
  extract and credited future-payment lists, it was 407 filings and
  48.3% of $16.6B. Leaving out every page no two readers agreed on, the
  floor is 327 filings and 34.9%. Bands A and B, every filing over $10M,
  are a census and are done: 212 of 460 filings, $6.93B.
- What stops recovery is the data, not the readers. 447 of the 1,000
  filings, 34% of the dollars, have nothing to read: the IRS serves no
  image (169) or the PDF is the IRS's own rendering with nothing
  attached (278). Of the 553 filings with an attachment, 73% reconcile,
  and 70% to 88% in every band.
- Two cheap models agree on 39.5% of pages. Gemini 3.8 Flash settles 42%
  of the disputes and Claude Sonnet 5 another 18%. The 28.7% of pages
  left flagged are loaded from Sonnet alone with a mark; checked by hand
  on 57 such pages from the small filings, Sonnet had 40 pages exactly
  right and 94% of the dollars.
- The work list now comes from the loaded tables, not from
  GivingTuesday's one-off extract, which held a quarter fewer filings.
  After six patient-assistance programs are left out it is 24,518
  filings, $48.7B, across tax years 2009 to 2025. For tax years 2020 on,
  11,830 filings are still to read: about $475 in model calls, for an
  estimated 4,550 filings and $14.9B of the $32.2B on their placeholder
  rows. The 11,696 filings before 2020 would cost up to $510; whether the
  IRS still serves their images is not yet measured.
- The rules for loading were settled on September 28. A list loads when
  it adds up within 0.5%. Every loaded row comes from a page that was
  read. Rows from pages labelled expenditure responsibility load only
  where a list needs them, and carry the label. Filings marked as grants
  to individuals are read and labelled, not skipped.
- The frame is loaded. `privategrants_recovered` holds 221,969 grants of
  398 filings, each with the page it was read from, the verdict on that
  page and the reading it came from. The other three of the 401 lists
  are a second version of a return whose other version is loaded: the
  extract listed both, and the loaded tables keep one. A view shows the
  recovered grants in place of the placeholder rows, so no dollar is
  counted twice.
- Still open: the 3,091 "various" filings ($35.5B) the classifier never
  sends to the PDF, the images of tax years before 2020, the read of the
  work list, and the matcher pass that turns the recovered rows into
  EINs, which is designed and not yet built.

## Terms

| term | meaning |
|---|---|
| placeholder filing | a 990-PF whose grant list in the form is a "see attached" row (or similar) instead of the grants themselves; the real list is in an attachment |
| work list | every placeholder filing in the loaded tables, less the excluded filers; what a run fetches and reads |
| declared total | the amount the foundation wrote on its "see attached" row in the grants table, Part XV, with line 3a (paid) and line 3b (approved for future payment) kept separate; every recovered list has to add up to it within 0.5%, it is what gets credited, and the bands and every dollar figure below are cut on it |
| grants paid, Part I line 25 | the whole year's grants from the form's top section, column (d); used only in the population table, where a filing is fetched when its pointer rows hold at least half of it; it differs from the declared total because it also counts the grants the filer itemised in the form and leaves out future payments |
| IRS image | the PDF of the whole return the IRS publishes on its Tax Exempt Organization Search site (TEOS); fetching it is free |
| attachment | the filer's own pages at the end of the image, after the IRS-rendered form pages; only these are sent to the models |
| readable filing | a filing whose image the IRS served and that has an attachment |
| bands | filings grouped by declared total: A over $100M, B $10M to $100M, C $1M to $10M, D under $1M; on the work list the paid amount alone decides |
| the sample, the frame | the first 100 filings, drawn to develop the method; the 1,000 the method was tested on, which contain them |
| readers | the vision-language models; the base pair (Qwen3-VL, Gemini 3.5 Flash Lite) reads every page, the escalation readers (Gemini 3.8 Flash, then Claude Sonnet 5) only the pages the base pair disagrees on |
| page verdict | agreed (the base pair matched), escalated (an escalation reader matched an earlier reading), or flagged (no two readers agree) |
| reconciled | a filing whose rows, read from its attachment, add up to its declared total within 0.5% and are labelled as grants; only then is it recovered, and the declared total is what is credited |
| loaded | a reconciled list written to `privategrants_recovered`, one row a grant; the target is the paid amount on the work list |
| policy | the named, versioned set of rules a run uses: which readers, in what order, at what settings; `POLICY_V2` is the frame's |

## The problem

Part XV of Form 990-PF lists grants paid. Many filers put one row there.
Siegel Family Endowment's XML, for example:

```xml
<RecipientBusinessName>SEE Attachment 22</RecipientBusinessName>
<Amt>27792259</Amt>
```

GivingTuesday concluded these lists "were never submitted in
machine-readable form". For the XML that is true.

The PDF is different. The IRS renders the whole submission as an image,
attachments included. Siegel's PDF has the list on pages 31 to 38. It
ends with a total of $27,792,259, the XML amount to the dollar.

Each row in the attachment has a name, an address, a status and a
purpose. 990-PF grants carry no recipient EIN, so name plus address is
what our matcher needs.

## Which filings need their PDF

The rule, measured on the loaded datamart (`privategrants_current`
joined to grants paid, Part I line 25, in `basic_fields_pf_current`):
fetch the PDF when pointer rows hold at least half of that figure, the
whole year's grants from the form's top section rather than the amount
on the pointer row. A pointer
row reads like "SEE ATTACHED", "STATEMENT 25" or "SCHEDULE ATTACHED". The
query is `data/exploratory/placeholder_population_by_year.sql`, run by
`placeholder_population.py`; the classes and the pattern's precision and
recall are measured in `placeholder_classifier_assessment.sql`.

| tax year | PF filings with grants | grants paid, Part I line 25 | placeholder filings | their grants paid | share of filings | share of dollars |
|---|---|---|---|---|---|---|
| 2020 | 88,737 | $94.8B | 2,809 | $6.71B | 3.2% | 7.1% |
| 2021 | 90,444 | $103.4B | 2,673 | $7.69B | 3.0% | 7.4% |
| 2022 | 92,018 | $115.1B | 2,471 | $8.28B | 2.7% | 7.2% |
| 2023 | 92,400 | $121.8B | 2,238 | $9.17B | 2.4% | 7.5% |
| 2024 | 85,470 | $117.6B | 1,924 | $8.13B | 2.3% | 6.9% |
| 2025, partial | 10,043 | $3.2B | 150 | $0.12B | 1.5% | 3.9% |
| all | 459,112 | $555.9B | 12,265 | $40.10B | 2.7% | 7.2% |

The share of dollars is flat across years; the share of filings falls as
e-filing software itemises more lists. 12,841 filings hold placeholder
rows; 576 of them ($24.2B, almost all of it three patient-assistance
foundations giving medicine to individuals) are excluded by EIN or by
the filing's own status flag, never by name pattern, because a name
pattern also catches ordinary grantmakers like Genentech Foundation.

Left aside by the rule, and never tested: 3,091 filings ($35.5B) whose
only grant row says "various"; 917 that withhold the list "upon
request"; 420 where pointer rows hold less than half of the total.
Twenty PDFs from the "various" class would say whether it belongs in the
population.

Grants paid (line 3a) and grants approved for future payment (line 3b)
are separate totals and separate statements. Each is reconciled on its
own.

The frame was drawn from GivingTuesday's 2020 to 2024 extract of grant
rows, a one-off CSV, where every filing with a pointer row counts, with
no half test, and the pointer rows themselves carry $25.0B across 9,515
filings. The frame's results are measured on that population, and its
dollars are declared totals in the sense above. The table above is
larger because it counts each filing's whole Part I line 25 and is
measured on the loaded datamart, which also holds part of 2025.

### The work list, from the loaded tables

Decided on September 28: the list of filings to read comes from the
loaded tables, never from the one-off CSV, and it covers every tax year
they hold. The CSV lacked a quarter of the filings. For 2020 to 2024 the
loaded tables hold 12,115 placeholder filings against its 9,515, and
none of the 40 largest missing ones appears anywhere in the file. Of the
frame's 1,000 filings, 992 are on the work list under the same object
id, every one with the same paid amount on its placeholder rows. Seven
of the other 8 are in the loaded tables as another version of the same
return. For six the other version is on the work list, and in the frame
too: the extract listed both versions of those returns, so the frame's
1,000 filings are 994 returns. AmazonSmile Foundation 2023 amended its
return to name 363,675 grants. The eighth, Goldie Anna Charitable Trust
2023, points at an attachment and offers addresses "upon request",
which the pattern reads as a list withheld.

| tax years | placeholder filings | on their placeholder rows | still to read | estimated model cost |
|---|---|---|---|---|
| before 2020 (2009 to 2019) | 11,696 | $16.3B | 11,696 | up to $510 |
| 2020 on | 12,822 | $32.2B | 11,830 | about $475 |
| all | 24,518 | $48.5B | 23,526 | |

The list is the table `pf_placeholder_filings`, built by `python -m
givingtuesday_datamart.placeholder_recovery work-list` and again at the
start of each run. The command reproduces every figure here, which a
one-time query had measured first, in a minute and a half: the query
makes one pass over the 16.6 million grant rows, and a cheap test for
the words a placeholder needs leaves 1% of them for the pattern. The
count rises from 42 filings for tax year 2009 to 2,925 for 2020 and
falls to 2,012 for 2024. Whether the IRS still serves the images of the
earlier years is not measured: every filing read so far was processed by
the IRS in 2021 or later.

Six filers are left out, by EIN. All are drug-makers' patient-assistance
programs. They give medicine to patients, so there is no recipient
organization to find. They hold 21 filings and $32.7B, 40% of all
placeholder dollars.

| filer | EIN | tax years | declared | left out since | evidence on the filing |
|---|---|---|---|---|---|
| Genentech Patient Foundation | 460500266 | 2020 to 2024 | $16.21B | the sample | placeholder row "Eligible Patients", purpose "Provide Prescription Drugs" |
| Sanofi Cares North America | 431614543 | 2021 to 2023 | $6.16B | Sept 28 | purpose "to provide free drugs to ill, needy or infant patients" |
| GlaxoSmithKline Patient Access | 200031992 | 2019 to 2024 | $4.23B | the sample | purpose "for the care of the ill and needy" |
| Boehringer Ingelheim Cares Foundation | 311810072 | 2020 to 2024 | $3.92B | the sample | donated medicine, from the capture analysis of August |
| Merck Patient Assistance Program | 010575520 | 2024 | $1.91B | Sept 28 | its four itemised years list individuals only |
| Novartis Patient Assistance Foundation | 262502555 | 2010 | $0.24B | Sept 28 | purpose "to provide single sourced, life sustaining medications" |

The six are in `data/placeholder_recovery/exclusions.csv`, the one place
they are named, each with its evidence and the date it was left out.
Exclusion stays by EIN, with the evidence recorded, never by a pattern
on the name. Of 15 placeholder filers with "Cares", "Patient" or
"Assistance" in the name, nine are ordinary corporate foundations such
as Amcor Cares and Caleres Cares. A second signal was tried and set
aside: the share of a filer's rows whose name sits in the form's person
field. Some filing software puts organization names there, and the
Howard G. Buffett Foundation, an ordinary grantmaker, scores 92% on it.

Filings whose placeholder row is marked "I", for individual, are no
longer skipped. There are 743 of them, $0.39B. Two thirds are
scholarship funds; the rest include ordinary grantmakers such as CSX
Foundation 2024. They are read like any other filing and labelled at
the load. About 1,330 more scholarship filings, $0.45B, carry no such
mark and are on the list in any case.

## Where the documents come from

Code: `givingtuesday_datamart/irs_source.py` and `filing_images.py`.

- PDF: from the IRS TEOS service, by EIN and tax period. Where the IRS
  index carries a RETURN_ID, the exact image is pinned. Every fetched
  PDF is stored once in `s3://givingtuesday-datamart/irs/pdf/` with its
  hash on the `filing_images` row, and every reading is keyed on that
  hash.
- XML: from GivingTuesday's mirror, verified byte-identical to the IRS
  copy.

Every PDF is a scanned image with no text layer. It is two documents in
one: the IRS rendering of the XML first, then the filer's attachments.
The IRS pages come at five fixed image widths, so the boundary is read
from `pdfimages -list` without rendering a page. On the sample 62% of
pages were IRS-rendered and never sent to a model.

## The frame

The population is violently top-heavy: 22 filings hold $4.67B of the
$25.0B. So the frame is stratified by declared dollars, bands A and B
are a census, and C and D are sampled at one fraction, 6%. Seed
20260921; the 100-filing sample regenerates identically inside the
610-filing expansion, which regenerates inside the 1,000.

| band | declared per filing | in population | in frame | PDF served | with an attachment |
|---|---|---|---|---|---|
| A | over $100M | 22 | 22 | 19 | 17 |
| B | $10M to $100M | 438 | 438 | 374 | 281 |
| C | $1M to $10M | 2,296 | 137 | 109 | 64 |
| D | under $1M | 6,759 | 403 | 329 | 191 |
| all | | 9,515 | 1,000 | 831 | 553 (9,926 pages) |

Two things the frame settled about the images:

- **Every 404 is a 2022 image.** All 66 indexed-but-unserved PDFs were
  generated by the IRS in 2022. None generated in 2021 or 2023 onward
  fails. This is the IRS report, with the list in
  `placeholder_404_images_expanded.csv`.
- **Unreachability is a tax-year-2020 problem.** 40% of 2020 filings
  have no usable PDF, 11% of 2021, 5–7% of 2022 and 2023, none of 2024.

## Reading the pages

Each attachment page is rendered at 200 DPI and sent to a vision model,
which returns the page's heading, its kind, one row per grant, and any
printed totals. Under `POLICY_V2`, the frame's policy:

| stage | reader | rule | on the frame | cost per page |
|---|---|---|---|---|
| base pair | Qwen3-VL instruct and Gemini 3.5 Flash Lite | both read every page; equal name-and-amount pairs keep the page | 39.5% of pages agreed, 60.5% disputed | $0.002 and $0.007 |
| first escalation | Gemini 3.8 Flash, low reasoning effort | reads the disputed pages; a reading equal to either base reading keeps the page | resolves 42% of disputes | $0.009 |
| second escalation | Claude Sonnet 5, thinking off | reads what is still open; a reading equal to any earlier one keeps the page | resolves 18% of what reaches it | $0.046 |
| flagged | none agree | loaded from Sonnet's reading with a mark on every row | 28.7% of pages, 13% of loaded rows | |

Three findings changed how the readers are asked:

- Qwen returned no rows for 401 dense pages, twice each, when asked for
  JSON output mode. The same pages read fully without it. An empty or
  cut-off list page is asked again without JSON mode and the fuller
  answer kept; Qwen is never asked in JSON mode now.
- The prompt is versioned. Version 4, the current one, defines rows by
  the amount column: every printed amount is a row, and a line with no
  amount belongs to the row above it. Version 3 had said a wrapped name
  is still one row, and the models then merged neighbouring recipients.
- Reasoning effort means different things per provider. On Gemini 3.8
  Flash, `none` and `minimal` map to an unbounded thinking budget and
  made it slower and worse; `low` is a small bounded budget, 58% cheaper
  than the default with no loss on the ground-truth pages, and is what
  the frame ran under. Sonnet needs thinking off; its maximum reasoning
  changed nothing at twice the price.

The first approach, Unstructured OCR, is no longer used. It cost more
and lost rows on long lists. The bake-off that chose the readers is in
the engineering log.

## Choosing the list

Code: `givingtuesday_datamart/attachment_grants.py`. Every guard has a
test in `tests/test_attachment_grants.py`.

Nothing labels the list reliably. But the placeholder amount says what
the list must sum to. So selection is a search: find the pages whose
amounts add up to the declared total, within 0.5%.

Adding up is not enough on its own. A filing has dozens of money tables,
and some combination of them reaches almost any number. The first version
accepted capital-gains schedules and revenue lines this way. So every
accepted list also needs a second piece of evidence:

- a heading, footer, column name or page label that says grants; or
- a run of consecutive pages whose rows are mostly organisation names,
  with no revenue, asset or expense schedule inside it.

Rows equal to a declared total, rows labelled total or subtotal, and rows
that point at an attachment are never counted as grants.

A list that runs onto a page with no title continues the list before it.
The models cannot tell a paid continuation from a future one, so such a
page takes the label of the page before it. The one exception: if that
earlier page ended with a total equal to a declared amount, its list is
closed, and the next page starts a new one.

The XML sometimes itemises a few grants beside the placeholder, and the
expenditure-responsibility statement lists more. On the frame those rows
went into the search too, taken from GivingTuesday's one-off extract.
Decided on September 28: they leave the search with the extract, and
they have. The pages read are the selector's whole input. Without the
extract's rows the frame goes from 407 reconciled lists to 406. Three
reconciled lists had used such rows, six rows in all: The Wege
Foundation 2020 and Marion and Henry Bloch Family Foundation 2022 still
add up with one row fewer, and Elbridge Stuart Foundation 2021, $22.3M,
does not. Eight filings that had not reconciled changed the reason why.

The paid amount is the only target. The loaded tables hold no
future-payment amount, and the frame's came from the extract. Taking it
away costs five more lists, 406 to 401, and adds none:

| filing | paid | future | with both amounts | with the paid amount alone |
|---|---|---|---|---|
| William R. Kenan Jr. Charitable Trust 2020 | $31.6M | $20.0M | paid and future lists both reconciled | nothing eligible: the future list comes first, and without its total the selector cannot see where it ends, so the paid pages after it continue it |
| T.L.L. Temple Foundation 2020 | $17.9M | $5.6M | reconciled against the two together | the list is there at 102% of the paid amount |
| Wilbur and Hilda Glenn Family Foundation 2023 | $5.5M | $7.9M | reconciled against the two together | nothing eligible |
| Northfield Bank Foundation 2022 | $0.6M | $0.1M | reconciled against the two together | nothing eligible |
| The Mitchelson Foundation 2021 | $0.1M | $0.07M | reconciled against the two together | nothing eligible |

Four of the five never had a paid list that added up on its own. No
list that reconciles both ways changed its pages or its error.
`report --with-future` runs the selection with both amounts, for this
comparison.

Pages the readers label as an expenditure-responsibility statement are a
group of their own. They join a filing's list only when the paid pages
do not add up without them. On the frame 287 such pages hold 1,955 rows
as read, in 39 filings. Of those rows, 67 are loaded, $3.2M in 4
filings. The statement is a ledger that repeats a grant every year until
the grantee has spent it, so the rest must not load as grants, and does
not.

## Results on the frame

Recovery falls with filing size because availability does. The readers
reconcile 70% to 88% of readable filings in every band, but 77% of band
A has a readable attachment against 47% of bands C and D. Dollars are
paid grants, the amount on the placeholder rows of line 3a; credit is
the declared amount of each reconciled list, never the transcribed sum.
The figures are under the loading rule: the pages read are the only
source, and the paid amount the only target.

| band | in frame | readable | reconciled | of frame | of readable | paid grants declared | recovered | dollars, of frame | dollars, of readable |
|---|---|---|---|---|---|---|---|---|---|
| A | 22 | 17 | 15 | 68% | 88% | $4,134M | $2,507M | 60.6% | 85.8% |
| B | 438 | 281 | 197 | 45% | 70% | $9,909M | $4,419M | 44.6% | 69.5% |
| C | 137 | 64 | 51 | 37% | 80% | $429M | $173M | 40.4% | 77.9% |
| D | 403 | 191 | 138 | 34% | 72% | $99M | $36M | 36.1% | 72.8% |
| all | 1,000 | 553 | 401 | 40% | 73% | $14,571M | $7,135M | 49.0% | 74.7% |

| measure | filings | dollars |
|---|---|---|
| recovered, paid lists | 401 of 1,000 (40%) | $7.13B of $14.57B (49.0%) |
| with flagged pages left out, the floor | 327 (33%): A 12, B 144, C 42, D 129 | $5.09B (34.9%) |
| the 100-filing sample, for comparison | 47 (47%) | 55.7% |
| the 390 filings added in bands C and D | 130 (33%) | 36.2% |
| loaded into `privategrants_recovered` | 398 | $7.03B |

How the frame's figure moved on September 28, step by step:

| rule | filings | credited |
|---|---|---|
| as first measured: rows from the extract in the search, paid and future targets, future lists credited | 407 | $8.03B of $16.65B (48.3%) |
| the same, counting 15 filings whose future-payment list alone reconciled | 422 | $8.18B (49.1%) |
| the same 407, in paid grants | 407 | $7.21B of $14.57B (49.5%) |
| without the extract's rows | 406 | $7.19B (49.3%) |
| with the paid amount as the only target | 401 | $7.13B (49.0%) |
| loaded: one version of each return, the one the loaded tables hold | 398 | $7.03B |

The three lists that reconcile and do not load are Caterpillar
Foundation 2023 (7,515 rows, $43.5M), The Comcast NBCUniversal
Foundation 2021 (606 rows, $41.9M) and Triad Foundation 2021 (562 rows,
$15.3M). Each is one of two versions of a return, both in the frame,
both read and both reconciled. The loaded tables keep one version of a
return, and that one is loaded. So nothing is missing from the load: the
frame had counted three returns twice, $100.8M.

How closely the 401 lists add up to the declared paid total:

| the rows are off by | filings | paid grants |
|---|---|---|
| under $1 | 311 (78%) | $3.64B |
| under $100 | 5 | $0.01B |
| under 0.1% | 39 | $2.34B |
| 0.1% to 0.5% | 46 | $1.15B |

A list that closes only inside the tolerance can be a coincidence. Three
on the frame are. Bader Philanthropies 2022, Paso del Norte 2020 and
Freeman 2020 each reach their total with the last few pages of an
expenditure-responsibility ledger, off by $8,600 to $40,000, and those
pages hold grants paid in earlier years. Field Family 2023 is the
opposite case: its whole list is filed under that heading and adds up to
the dollar. The 0.5% tolerance stays, decided on September 28, and the
three are accepted as the price of it. The 58 filings whose list is
present and covers 90% to 110% of the total, $855M of paid grants, do
not load.

What is not recovered, in paid grants:

- **447 filings with nothing to read, $5.02B (34.4%).** 278 whose PDF is
  the IRS rendering and nothing else ($2.71B); 66 whose image the IRS
  returns as 404 ($1.30B); 103 with no image on the IRS site ($1.01B).
- **152 readable filings that do not reconcile, $2.42B.** 77 where the
  list is present but the rows are off by more than 0.5% ($1.19B); 15
  where a dense page came back partial ($0.59B); 57 whose attachment
  holds no table that can be the paid list ($0.61B); 3 absent. The
  first two are the readers' to improve; the third is the filer's,
  apart from four lists the paid-only target gave up.

The recovered rows of the 401 lists: 230,652 grants with name, address,
purpose and amount, 35,959 of them (16%) from flagged pages and marked
as such. Every row carries the page verdict and the readings it came
from, so any rule change re-derives from the tables at no model cost.

What is loaded, 221,969 rows of 398 filings, by the labels on the rows:

| label | value | rows | dollars |
|---|---|---|---|
| the page, as the reader labelled it | paid list | 221,894 | $7,029.9M |
| | expenditure responsibility | 67 | $3.2M |
| | future-payment list | 8 | $0.9M |
| the verdict on the page | agreed | 110,443 | $3,979.6M |
| | escalated | 82,906 | $1,768.0M |
| | flagged | 28,620 | $1,286.4M |
| the filer marked its grants as to individuals | yes | 117 | $0.2M |
| | no | 221,852 | $7,033.7M |
| the placeholder amount passes both columns of line 25 | yes | 195 | $24.7M |
| | no | 221,774 | $7,009.3M |

The 67 rows from expenditure-responsibility pages are in the four
filings named above: Bader Philanthropies 2022 (28 rows), Paso del Norte
Health Foundation 2020 (19), Field Family Foundation 2023 (19) and The
Freeman Foundation 2020 (1). The frame holds one filing marked as grants
to individuals that loads, 117 rows.

The 8 rows from a page labelled as a future-payment list are Eden Hall
Foundation 2022, and they show what the target can hold. The filer
entered two placeholder rows on line 3a: "grants paid", $13.36M, and
"grants approved", $5.21M. The target is the two together, $18.57M, and
the list that adds up to it is both schedules: 27 of its rows, $5.2M,
are grants approved and not paid. The reader labelled the first of those
three pages a future-payment list; the two after it carry no heading and
took the label of a paid list.

Nothing in the rule stops a placeholder amount from passing grants paid
on Part I line 25. The rule reads column (d) of that line, which is on a
cash basis. On 219 filings of the work list the placeholder amount
passes it by more than 0.5%, $254M in all. For 100 of them it equals
column (a), the expenses per books, and nothing is wrong: The King
Street Charitable Trust 2021 gave $7.8M in cash and $17.4M not in cash,
column (a) and the placeholder rows both say $25.2M, column (d) says
$1.6M, and the list adds up to the rows. 112 filings pass both columns,
by $111M.

Of the loaded filings 9 pass column (d), and 7 of those equal column
(a). Two pass both: Eden Hall 2022, by the $5.2M above, and Genentech
Foundation 2021, by $0.1M. The view holds what `privategrants_current`
held for these filings, no more. Decided on September 29: such a list
loads, and every row of it carries `placeholder_exceeds_declared`, so a
consumer can leave it out. The work list carries the same mark on the
112 filings, with both columns of line 25 beside the placeholder
amount.

The sample, read first, gave 47 filings and 55.7%; the frame added 540
small filings the sample under-represented, and the rate came down.
Loading flagged pages rather than leaving them out is worth 74 filings
and 14 points on the frame. Bands C and D flag half their pages, so
their result depends most on the flagged rule.

The run itself: $221.64 against a $241.50 projection and a $400 cap,
3 h 43 min on one t3.xlarge including two stops, the base pair in
parallel in 2 h 16 min, 3.8 Flash 45 min, Sonnet 36 min. Every reading
the frame's pages ever needed, the sample's earlier reads included, cost
$304. No refusal or timeout from the model API in 24,131 page reads.

## How the readers do

Two reads of the same page agree on every name and amount 89% to 97% of
the time when the page has fewer than 25 rows. With 25 rows or more, 31%
to 38% of the time. Checked against the page image, the errors are a
merged pair of rows that shifts every amount below it, a split row that
shifts them the other way, or cents read as dollars. The page sum barely
moves, so reconciliation cannot catch them. Page-level agreement has to
compare names with their amounts, not amounts alone.

**Ground truth.** 138 pages were read from the page image by hand: 83
drawn from the sample across row density and reader agreement plus every
page of seven near-miss filings, and 57 flagged pages from bands C and D
of the frame (two already in the sample's draw). The truth is
`data/exploratory/placeholder_ground_truth.csv`; the scorer is
`placeholder_ground_truth.py`, and every number below reproduces from it.

On the 83 sample pages:

- When the two base models agree on a page, the page is right 37 times
  in 38. Two different models agreeing were right 107 times in 107 across
  the repeat reads. The same model read twice agreed with itself on 57
  pages and was wrong on 6 of them: a model repeats its own mistakes, so
  agreement only counts between different models.
- When they disagree, Gemini is right on 16 pages of 45, Qwen on 3, and
  neither on 26. A disagreeing page needs a third reading.
- Every near-miss filing's true rows sum exactly to the declared total.
  The lists are complete on the page. The readers misplace rows; they do
  not miss lists.
- Four candidates read the same pages as third reader: Claude Sonnet 5
  97% of rows right and 67 pages exact at 5.3 cents a page, Gemini 3.8
  Flash 94% and 59 at 2.2 cents, GPT 5.6 Terra 82%, GPT 5.6 Luna 79%.
  Neither Sonnet nor 3.8 Flash ever repeated Flash Lite's mistake. The
  design sends a disputed page to 3.8 Flash first and only what is still
  open to Sonnet; on the 83 pages it accepts 58 rightly, 2 wrongly and
  flags 23, and the frame reproduces 58 / 2 / 23 under `POLICY_V2`. The
  two wrong pages are future-payment schedules where three readers chose
  the approved column and the declared figure is the balance column; no
  paid page was accepted wrongly.

**Sonnet on the flagged pages.** A flagged page is loaded from Sonnet's
reading alone, so its accuracy there is the accuracy of 13% of the
loaded rows. On the sample's 23 flagged pages, bands A and B, Sonnet
alone was right on 11 with 93% precision and 92% recall. The frame's
flagged pages are mostly the small filings', so every flagged page with a
grants table in bands C and D was checked (49), plus eight pages every
reader called non-grant, all correctly (`placeholder_ground_truth
flagged --policy v2`; per page in `placeholder_flagged_check.csv`):

| checked flagged pages | pages | exact (plus spelling-only) | rows right, precision / recall | dollars right |
|---|---|---|---|---|
| sample, bands A and B | 23 | 11 | 93.4% / 91.9% | |
| frame, band C | 30 | 22 (23) | 94.6% / 93.9% | 95.1% |
| frame, band D | 27 | 12 (17) | 82.9% / 82.8% | 85.2% |
| frame, C and D together | 57 | 34 (40) | 89.9% / 89.5% | 94.0% |

The 17 misses have four shapes:

- **Two payment columns on one line** (Chisholm, 4 pages): Sonnet read
  one column and dropped the other, losing 5 of every 8 dollars on those
  pages. Every reader did the same.
- **Amounts sliding one row** (Businessolver 2022, Klotz, Toledo, East
  Chicago, Sturm): a blank amount or a tight single-spaced list shifts
  every amount below it onto the wrong name. On Businessolver, 62 of 123
  rows carry the wrong amount while the page sum is off by 1.6%.
- **Sign convention** (Tennant, 2 pages): a ledger that prints every gift
  as a credit; Sonnet kept the minus signs, the other readers did not.
- **A few rows misread** (Weeden, CEMEX, France Stone's blurred fax,
  Independence): 1 to 7 rows of 24 to 65, mostly a slid amount or a
  page-boundary duplicate.

Band D is worse than band C because its pages are these shapes more
often.

## What the rows carry for matching

A recovered grant has no EIN. The matcher finds one from the name and
the place. It narrows its search by zip, or by an exact name when the
state is the same, and it scores the address as one string. So a row
needs a zip or a state. It never needs the street split from the city.

Measured on the frame's 401 reconciled paid lists: 230,652 rows read
from attachments, $7.13B. Code: `placeholder_recovered_rows.py`, commands
`address` and `names`, from the tables at no model cost. The loader
writes the same five classes for the 398 filings it loads, and prints
them.

| what the address gives | rows | of rows | dollars | of dollars | loaded rows | loaded dollars |
|---|---|---|---|---|---|---|
| state and zip | 59,360 | 25.7% | $4.41B | 61.9% | 50,877 | $4.32B |
| state, no zip | 12,464 | 5.4% | $0.70B | 9.8% | 12,461 | $0.70B |
| no address, state printed in the name | 900 | 0.4% | $0.15B | 2.1% | 900 | $0.15B |
| address text with no US state | 3,966 | 1.7% | $0.87B | 12.2% | 3,776 | $0.87B |
| no address | 153,962 | 66.8% | $1.00B | 14.0% | 153,955 | $1.00B |
| all | 230,652 | | $7.13B | | 221,969 | $7.03B |

Before September 28 the same command gave 407 lists, 231,743 rows and
$7.23B. The 1,091 rows fewer are the six lists that no longer
reconcile. The 8,683 rows between the frame's and the loaded are the
three returns the frame holds in two versions, nearly all of them with
a state and a zip.

- Rows with a zip or a state hold 74% of the dollars. They go through
  the matcher as it is.
- The missing addresses are the filer's doing, not the readers'. Of the
  401 lists, 132 print a name and an amount and nothing else, and 207
  give an address on every row. The large filers of band A print
  addresses throughout.
- Some filers print the place inside the name, "Mayo Clinic, Rochester,
  MN". The state is read from there and the name kept without it.
- Address text with no US state is mostly foreign grantees, and gifts of
  stock where the list prints the shares in the address column.

### Matching on the name

Each row's name was matched, exactly, against the 1,075,565 names of the
matcher's universe, 602,763 filers. A match counts only when the name
belongs to one filer, or when the row's state picks one among several.
A name several filers share is never guessed.

| name cleaning | rows with a state, matched | rows without, matched | their dollars | state agrees |
|---|---|---|---|---|
| the matcher's normaliser today | 54.2% | 61.2% | 26.6% | 96.5% |
| punctuation, "&", "the" and legal endings removed | 66.6% | 72.5% | 34.5% | 96.6% |
| and abbreviations expanded | 67.0% | 72.9% | 34.7% | 96.6% |
| and stopwords dropped | 67.5% | 73.0% | 34.9% | 96.6% |

One level of cleaning is the whole gain: "The River Fund, Inc." becomes
"river fund". Going further adds half a point and more shared names.

The last column is the check on a name-only match. Where a row has a
state, the matched filer's state should be the same, and it is 96.6% of
the time. The misses mix true collisions (EPIC, Open Door Ministries)
with national bodies listed at a local office (American Cancer Society,
National Audubon Society), so the real rate is higher.

| words in the cleaned name | matches with a state to check | state agrees | name-only matches | their dollars |
|---|---|---|---|---|
| 1 | 1,484 | 92.7% | 3,265 | $30M |
| 2 | 7,996 | 95.3% | 18,115 | $126M |
| 3 | 13,362 | 95.2% | 25,882 | $152M |
| 4 or more | 24,460 | 98.1% | 67,300 | $336M |

What it comes to, with the cleaned name:

| rows | matched on the exact name | shared name, unresolved | no exact name |
|---|---|---|---|
| with a state: 72,724 rows, $5.27B | 66.6% of rows, 66.9% of dollars | 1.3%, 1.5% | 30.0%, 29.8% |
| without: 157,928 rows, $1.87B | 72.5% of rows, 34.5% of dollars | 3.7%, 2.3% | 23.7%, 63.3% |

Rows with a zip and no exact name still go to the matcher's scoring of
name and address, so their figure is a floor. Rows without a state have
no other route: $0.64B matches on the name alone, and $1.18B does not.
That remainder is foreign grantees, donor-advised fund accounts,
catch-all lines such as "other 501(c)(3) organizations", and
organizations that file no return.

The design that follows is stage 5 of the engineering log. The loader
reads the state and zip and parses nothing else: that part is built.
Rows without an address match on a name that belongs to exactly one
filer and are labelled as such, and the name cleaner goes into the main
matcher: that part is the matcher's, and is next.

## Shipping the rest

The first estimate, made on GivingTuesday's extract, was 8,515 filings
still to read, about $270, and a projected total of 3,400 filings and
$11.4B. The work list from the loaded tables is larger and replaces it.
For tax years 2020 on, at the frame's rates in paid grants:

| band | on the work list | on their placeholder rows | still to read | estimated cost | frame rate, filings / dollars | estimated filings recovered | estimated dollars |
|---|---|---|---|---|---|---|---|
| A | 29 | $6.96B | 11 | $15 | 68% / 60.6% | 20 | $4.2B |
| B | 554 | $13.42B | 162 | $96 | 45% / 44.6% | 249 | $6.0B |
| C | 3,029 | $9.45B | 2,851 | $188 | 37% / 40.4% | 1,128 | $3.8B |
| D | 9,210 | $2.36B | 8,806 | $176 | 34% / 36.1% | 3,154 | $0.9B |
| all | 12,822 | $32.19B | 11,830 | about $475 | | about 4,550 | $14.9B |

The cost column is what `run --tax-years 2020-2025 --dry-run --no-fetch`
prints: $474.72, with no request made of the IRS and no page read. The
cap is $800 since September 29; the frame's was $400.

Bands here are cut on the paid amount of the placeholder row, so they
differ a little from the frame's, which counted future payments too. The
173 large filings still to read were never in the extract. They include
the Howard G. Buffett Foundation for five years and the 2024 filings of
Musk, Bezos, Schusterman and Wyss. The A and B rates were measured on
other large filers, not on these.

Tax years before 2020 are 11,696 filings and $16.3B on their placeholder
rows. Reading them would cost up to $510 and, if the IRS serves their
images as it does recent ones, recover about 4,100 filings and $7.1B.
That condition is untested. Fetching about 30 filings from each of
those years costs nothing in model calls and would settle it.

The C and D rates rest on 137 and 403 sampled filings, so they carry
about 8 and 5 points of sampling error at 95%; the dollar estimates for
C and D are about $3.1B to $4.6B and $0.7B to $1.0B. Cost does not
scale with filings because the small filings are small in every way the
readers pay for:

| | A | B | C | D |
|---|---|---|---|---|
| paid grants declared per filing | $188M | $23M | $3.1M | $0.24M |
| filings with a readable attachment | 77% | 64% | 47% | 47% |
| attachment pages per readable filing | 34 | 30 | 6.6 | 2.7 |
| grant rows per reconciled filing | 922 | 1,050 | 80 | 42 |
| pages flagged | 24% | 27% | 52% | 38% |
| model cost per filing in the frame | $1.35 | $0.59 | $0.066 | $0.020 |

The 2020-on work list, at the frame's rates: about 10,400 PDFs served,
32,500 attachment pages of which 9,926 are read; about $780 in model
calls in all, of which $304 is spent; about 4 hours of reading per
10,000 pages on one t3.xlarge, so about 9 hours for the pages left;
about 500,000 grant rows.

## What is out of reach

On the frame, 447 filings and $5.02B of paid grants. On the sample the
list with links is `data/exploratory/placeholder_unreachable.csv`.

- No PDF served: 169 filings. 66 are indexed but return an error page,
  all generated in 2022; 103 have no image listed.
- PDF with no attachment: 278 filings. Wells Fargo 2022 and 2023 ($534M)
  are 27-page images ending with a blank Schedule B. Their 2020 image is
  235 pages with the list in it.
- Attachment that is not the list: 57 filings on the frame. Schusterman
  2023 attached only its expenditure-responsibility statement. Bezos 2021
  attached a narrative. Roberts 2021 attached one page of a longer list.

Other sources were checked on the sample:

- The XML gives no sign that an attachment exists.
- ProPublica has 2 of the sample's 15 missing PDFs, both from the IRS's
  older image program.
- The New York charities registry serves the full filing, but behind a
  CAPTCHA. It is a manual step. Six of the 15 are New York filers.
- Foundation websites list recipients without amounts.

Recovering these is a data-acquisition problem, separate from the
readers, and should not hold up loading what is recovered.

Left out by decision, among what was read:

- Lists that are present and do not add up within 0.5%: 77 filings on
  the frame, 58 of them within 10% of the total, $855M of paid grants.
- Rows on expenditure-responsibility ledgers that no list needs: 1,888
  of the 1,955 rows read from such pages on the frame. They stay in
  `page_readings`.
- Rows from GivingTuesday's one-off extract: none is loaded, and none is
  used in the search. One list, $22.3M, went with them.
- Future-payment lists: none loads yet, since the loaded tables hold no
  future amount to add up to. Decided on September 29: the amount will
  come from GivingTuesday's future-payment datamart, which we have not
  loaded, and the lists will load marked `future`. On the frame 56 had
  reconciled beside
  their paid list, $0.81B, and 15 alone. Four lists that added up only
  against paid and future together, and one that needed the future
  total to find where its paid list began, went with them.
- A second version of a return: the loaded tables keep one, and the
  load follows them. Three on the frame, 8,683 rows, each the double of
  a list that is loaded.

## What we learned

The findings that shaped the method, each measured, in the order they
were found:

1. The attachment starts at the first page whose image width is not one
   of the IRS renderer's five; 62% of pages are the IRS's and cost
   nothing to skip.
2. A list that sums to the declared total is not enough: capital-gains
   schedules sum to anything. Every accepted list needs a grants heading
   or a run of organisation-name pages as well.
3. A continuation page inherits the heading before it unless that page
   closed on a declared total.
4. Qwen returns empty pages on dense lists in JSON mode; the retry
   without it is what recovers them.
5. Rows are defined by the amount column. A prompt that said a wrapped
   name is one row merged neighbouring recipients.
6. Dense pages slide amounts against names while the page sum barely
   moves. The gate compares name-and-amount pairs, never sums.
7. Agreement only counts between different models: two models agreeing
   were right 107 of 107 times; a model read twice repeats its own
   mistakes.
8. Near-miss lists are complete on the page. Readers misplace rows; they
   do not miss lists.
9. One escalation reader is not enough on the pages the base pair
   disputes; two, the cheaper first, resolve 60% of them, and Sonnet's
   maximum reasoning adds nothing.
10. Reasoning effort is per provider: on Gemini `none` and `minimal`
    think more, `low` thinks less and reads no worse; Sonnet needs
    thinking off.
11. Every unserved IRS image is a 2022 batch; 40% of tax-year 2020
    filings have no usable PDF.
12. The flagged pages are the small filings' scanned letters, odd
    formats and two-column tables, not Johnson & Johnson's dense
    matching-gift pages, which flag at 11%.
13. Sonnet on the small filings' flagged pages: 40 of 57 exact, 94% of
    the dollars, and the misses are two-column tables, sliding amounts, a
    credit ledger's signs and a few misread rows.
14. Placeholder filings are 2.7% of filers and 7.2% of grants paid on
    Part I line 25, flat across years; that line and the placeholder
    row are different denominators, and the projections use the latter.
15. Paid and future-payment lists reconcile separately. Counting the 15
    filings whose future-payment list alone reconciled added a point to
    the first headline. None loads now: see 28.
16. The classifier excludes patient assistance and individuals by EIN and
    status flag, never by name, and leaves the "various" class untested.
17. Operationally: render in chunks of 20 pages, never share a temp-file
    prefix between concurrent renders, and stop a run whose first fifty
    results are all errors before the attempt counter turns a broken box
    into a $550 re-read; Ctrl-C is safe and the same command resumes.
18. The matcher needs a zip or a state, not a parsed address. Rows with
    one hold 74% of the recovered dollars; two thirds of the rows have
    no address at all, because the filer printed none.
19. One level of name cleaning lifts exact matches by eleven points.
    Abbreviations and stopwords add half a point.
20. A name that belongs to one filer is in the row's state 96.6% of the
    time, and 98.1% for names of four words or more.
21. The one-off extract the frame was drawn from lacked a quarter of the
    placeholder filings in the loaded tables, 176 of them over $10M. The
    work list has to come from the loaded tables.
22. The loaded tables reach back to tax year 2009. Nearly half of all
    placeholder filings are from before 2020.
23. An expenditure-responsibility statement is a ledger across years,
    not a list of the year's grants: of 282 such rows from filers seen
    in several years, 179 repeat in another year's statement.
24. The 0.5% tolerance can close on a coincidence. 77% of reconciled
    lists close to the dollar; the three coincidences found all sit in
    the 12% that close between 0.1% and 0.5%.
25. The first headline credited future-payment lists too. In paid grants
    alone the same 407 lists were 49.5%; under the loading rule the
    frame recovers 401 lists and 49.0%.
26. Six patient-assistance programs hold 40% of all placeholder dollars.
    A name pattern cannot find them: nine of fifteen filers named like
    them are ordinary foundations.
27. Most lists print no status. The readers returned one for 21% of
    rows on paid-list pages, and "I" for 406 rows. Nothing in the tables
    says a row names a person; the matcher, which a person's name cannot
    pass, is the backstop.
28. The future-payment amount did more than credit future lists. Four
    lists added up only against paid and future together, and one paid
    list began where a future list ended on its total. With the paid
    amount alone the frame loses those five and gains none.
29. The extract listed every version of a return. Six returns are in
    the frame twice, three of them reconciled twice. The load follows
    the loaded tables, which keep one version.
30. A placeholder amount can pass grants paid on Part I line 25, column
    (d): 219 filings on the work list. Column (d) is on a cash basis,
    and for 100 of them the amount is column (a), gifts not in cash
    included. 112 pass both columns. A filer can enter its grants
    approved as a second row on line 3a, as Eden Hall 2022 did, and the
    list that adds up then holds both schedules.
31. Finding the placeholder rows is one pass over 16.6 million grant
    rows. A temporary table updated twice took 12 to 15 minutes; one
    query, with a cheap test for the words a placeholder needs run
    before the pattern, takes a minute and a half.
32. A view that tests the pattern on every row costs minutes a query.
    The view looks up each loaded filing's placeholder row through the
    index, and tests the rows of loaded filings only.

## Decisions and their evidence

| decision | evidence | when |
|---|---|---|
| base readers Qwen3-VL and Gemini 3.5 Flash Lite | cheapest pair; agreement exact on 107 of 107 ground-truth pages | Sept 22 |
| escalation to Gemini 3.8 Flash, then Claude Sonnet 5 | 94% and 97% of rows right; neither repeats Flash Lite's mistakes | Sept 22 |
| 3.8 Flash at low reasoning effort (`POLICY_V2`) | $0.0093 a page against $0.0220, 17 of 46 disputes resolved against 16, no loss on the ground truth | Sept 23 |
| agreement is equal name-and-amount multisets, at least one row | amounts alone hide shifted names; two empty readings agreed once and were wrong | Sept 22 |
| same-model repeats never count | wrong on 6 of 57 self-agreements | Sept 22 |
| prompt v4, 200 DPI | v4 inside run-to-run noise for Gemini, a small gain for Qwen; 130 DPI misread digits, 300 cost more for nothing | Sept 22 |
| flagged pages loaded from Sonnet's reading, marked | 11 of 23 right on the sample; 40 of 57 and 94% of dollars on the small filings; leave-out gives 327 filings and 34.9% of paid grants | Sept 22, measured Sept 24 and 28 |
| a base reader out of attempts is absent for the page, which goes through the dispute path | a reader timing out three times on a dense page must not kill a page three other readers can decide | Sept 23 |
| paid and future lists reconciled separately; the headline counts the filings whose paid list reconciled | 15 frame filings reconcile only their future list | Sept 24 |
| the frame's C and D split, 137 and 403 at one sampling fraction | rates set by availability, not count | Sept 24 |
| PDFs kept indefinitely in S3, every reading keyed on the image hash and the request settings | the IRS loses images; a policy change is a new version and re-derives at no cost | Sept 22 |
| the loader reads the state and zip off each address and parses nothing else | the matcher narrows by zip, or by exact name with the same state, and scores the address whole; 74% of dollars carry a zip or a state | Sept 28 |
| rows with no address match on a name that belongs to exactly one filer, labelled; one-word names included | 114,562 rows and $0.64B; the state agrees 96.6% where it can be checked, 92.7% on one-word names, which are 3,265 rows and $30M | Sept 28 |
| the name cleaner goes into the main matcher, on the rerun that takes the recovered rows | exact matches on rows with a state go from 54.2% to 66.6% | Sept 28 |
| the work list is built from the loaded tables at the start of each run, over every tax year they hold | the extract held 9,515 placeholder filings for 2020 to 2024, the loaded tables 12,115; 992 of the frame's 1,000 are found by object id | Sept 28 |
| nothing from GivingTuesday's one-off extract is loaded or used in the search | 406 of the frame's 407 lists reconcile without its rows; one filing, $22.3M, is lost | Sept 28, measured the same day |
| the target is the paid amount on the work list; no future-payment list loads, until the future amount is in the loaded tables | the loaded tables hold no future amount; 5 of 406 lists are lost and none gained | Sept 28, measured the same day |
| the future amount comes from GivingTuesday's future-payment datamart, `990PFPart14Grants3B`, loaded like the paid one; future lists then load marked `future`, outside the view | the datamart is published at the same version as the paid one and was never loaded; the filing's own XML is not the source | Sept 29 |
| six patient-assistance programs are left out, by EIN | $32.7B, 40% of placeholder dollars; the purpose on the placeholder row says donated medicine; a name pattern misfires on nine filers of fifteen | Sept 28 |
| filings marked as grants to individuals are read, and labelled at the load | the mark removes 743 filings and $0.39B, ordinary grantmakers among them; reading them costs about $20 | Sept 28 |
| rows from pages labelled expenditure responsibility load only where a list needs them, with the label; ledger pages no list needs never load | 67 rows and $3.2M in 4 filings, loaded; the other 1,888 rows read are a ledger across years, held for some filers and not others | Sept 28 |
| the 0.5% tolerance stays; lists within 10% of the total do not load | a wider band on a $100M filing lets in $5M of unexplained rows; 58 filings and $855M stay out; a tolerance by filer size is a later question | Sept 28 |
| the view drops a loaded filing's placeholder rows, and shows one policy's rows | the rows carry the whole amount; on the 398 loaded filings the view holds $7,163.2M where `privategrants_current` holds $7,163.5M | Sept 28 |

## Next steps

Done on September 28: the work-list command, `run` over the work list,
the loader, the view and the checks; the extract's rows out of the
search; the frame loaded.

1. Load GivingTuesday's future-payment datamart, `990PFPart14Grants3B`,
   as a source; give the work list a future amount from it; load the
   future lists marked `future`, outside the view.
   Still to confirm: whether the view stays an object of its own.
2. Put the view into the matcher, with the name cleaner and the
   name-only tier. One matcher rerun takes all of it; the matching
   input-shape version goes to 3. The regression gate and the
   corrections preflight are the checks.
3. Fetch about 30 filings from each tax year before 2020, to learn
   whether the IRS still serves their images. No model is called.
4. Read the 2020-on work list: 11,830 filings, about $475, about nine
   hours of reading on the box, inside the cap of $800. Then `load` and
   `check`.
5. Label the rows that name a person. A pass over the stored names
   reads no page.
6. Test the "various" class: twenty PDFs would say whether its $35.5B
   belongs on the work list.
7. Report the 2022 image batch to the IRS, with Wells Fargo as the
   example.

## For the GivingTuesday data team

1. The placeholder lists are in the images you already link to. This is
   worth doing once upstream.
2. Ship a version-ordering key. "Most recent by ingestion date" is not
   executable with the columns as shipped.
3. The amended-filing count is 665 in the one-pager and 804 in the file.
4. The 990-PF block doubling in 2024 batches is a separate mechanism from
   amendments.
5. `non_cash_amount` is hardcoded to 0 across the PF sources. Bezos gives
   97% of its grants in stock.

## Files

| path | what |
|---|---|
| `givingtuesday_datamart/irs_source.py` | object id to IRS PDF and XML; where the attachments start |
| `givingtuesday_datamart/filing_images.py`, `page_readings.py`, `page_verdicts.py` | three of the five tables: fetched images, readings, verdicts under a policy |
| `givingtuesday_datamart/vlm_transcription.py` | attachment page to page JSON, with retries and repairs |
| `givingtuesday_datamart/reading_pairs.py` | the page comparison the gate and the scorer share |
| `givingtuesday_datamart/attachment_grants.py` | choosing the list: reconciliation plus evidence |
| `givingtuesday_datamart/placeholder_recovery/` | the production package: `work-list`, `run`, `load`, `view`, `check`; the pointer pattern as SQL, the exclusions, the state and zip of an address |
| `data/placeholder_recovery/exclusions.csv` | the six filers left out, by EIN, each with its evidence and date; plain text, not LFS |
| `givingtuesday_datamart/exploratory/placeholder_recovery.py` | `sample`, `estimate`, `run`, `transcribe`, `report`, `compare` |
| `givingtuesday_datamart/exploratory/placeholder_ground_truth.py` | the hand-checked pages and the scorer: `verdicts`, `flagged`, `score` |
| `givingtuesday_datamart/exploratory/placeholder_population.py` | the population by tax year |
| `givingtuesday_datamart/exploratory/placeholder_recovered_rows.py` | what the recovered rows carry for the matcher: `address`, `names` |
| `data/exploratory/placeholder_sample_1000.csv` | the frame, drawn from GivingTuesday's one-off extract |
| `data/exploratory/placeholder_report_v2_1000.csv` | per-filing outcomes on the frame under `POLICY_V2` and the loading rule: the pages read, the paid amount; regenerated on 2026-09-28, the earlier one is in git history |
| `data/exploratory/placeholder_ground_truth.csv`, `placeholder_gt_pages.csv`, `placeholder_flagged_check.csv` | the 138 pages read from the image, the draw, and Sonnet's score on the flagged ones |
| `data/exploratory/placeholder_population_by_year.sql`, `.csv` | the population by tax year |
| `data/exploratory/placeholder_recovered_address.csv`, `placeholder_recovered_names.csv` | the frame's recovered rows by what their address gives, and their exact-name matches by cleaning level |
| `data/exploratory/placeholder_classifier_assessment.sql` | the classifier's classes, precision and recall |
| `data/exploratory/placeholder_404_images_expanded.csv`, `placeholder_unreachable.csv` | the IRS's unserved images; the sample's out-of-reach filings with links |
| `s3://givingtuesday-datamart/placeholder-recovery/archive/placeholder-sample-era-data-2026-09-25.zip` | the sample-era reads and frames (the single-read reports, the 610-filing frame and its manifest, the engine differences), archived on 2026-09-25 with a manifest of hashes; the engineering log discusses their numbers |
| `s3://givingtuesday-datamart/placeholder-recovery/archive/placeholder-sample-files-2026-09-28.zip` | the 100-filing sample's own files (the sample, its XML rows, its fetch manifest, its reports under both policies, the two single-reader policies), archived on 2026-09-28; the frame contains the sample |
| `s3://givingtuesday-datamart/placeholder-recovery/archive/placeholder-frame-xml-rows-2026-09-28.zip` | `placeholder_sample_1000_xml_rows.csv`, the rows GivingTuesday's extract itemised for 139 of the frame's filings (571 rows, $564.9M), archived on 2026-09-28 with a manifest of hashes when they left the search |
| `s3://givingtuesday-datamart/placeholder-recovery/archive/placeholder-vlm-bakeoff-2026-09-28.zip` | the raw responses of the 20 model configurations of the bake-off of 2026-09-21, 280 files; the harness and the result table are in `givingtuesday_datamart/exploratory/vlm_bakeoff/` |

## Caveats

- Bands A and B are exact. Bands C and D are a 6% sample: about 8 and 5
  points of error on their rates.
- Credit is the declared amount, not the transcribed sum. The frame's
  49.0% is in paid grants, under the loading rule. The first headline,
  48.3%, credited future-payment lists and used rows from the extract.
- The frame counts six returns twice, in two versions each; three of
  them reconcile twice. The load holds one version.
- The work list's costs and recoveries come from the frame's rates. The
  large filings new to the list were never sampled, and no filing before
  tax year 2020 has been fetched.
- A loaded list adds up to the amount on the filing's placeholder rows.
  On 2 loaded filings that amount passes both columns of line 25, by
  $5.4M in all, and one of the two lists holds grants approved for
  future payment. Their 195 rows are marked
  `placeholder_exceeds_declared`.
- Nothing in the tables says whether a row names a person. Scholarship
  lists will load students' names, which the matcher leaves unmatched.
- Ground truth measures the readers on 138 pages, not the selector's
  filing-level decisions.
- Recovered grants have not been through the matcher. The name matches
  in *What the rows carry for matching* are exact matches on a cleaned
  name, a floor for rows with a zip, which the matcher also scores.
- The "various" class is outside the population and untested.
