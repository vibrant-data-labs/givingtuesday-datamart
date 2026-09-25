# Recovering the Grants Behind "SEE ATTACHMENT"

Vibrant Data Labs, September 25, 2026.

Status: measured on a frame of 1,000 filings under the page gate on
September 23 and 24, 2026, for $222 in model calls, after a sample of 100
filings read four times. Ready to run on the remaining 8,515 filings; not
yet loaded into the datamart.

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
  of foundations that declare grants, 7.2% of the dollars they declare,
  $40.1B. The list exists only as pages in the IRS's scanned image of
  the return.
- The method fetches that image, has vision-language models read the
  attached pages, and keeps a list only when it adds up to the total the
  foundation declared. A page is loaded only when two different models
  agree on its rows; four are tried in turn.
- On the 1,000-filing frame, every filing over $10M plus a 6% sample of
  the rest, it recovered the lists of 407 filings: $8.03B, 48.3% of the
  $16.6B those filings declared (49.1% counting 15 filings whose
  future-payment list reconciled). Leaving out every page no two readers
  agreed on, the floor is 333 filings and 36.4%. Bands A and B, every
  filing over $10M, are a census and are done: 216 of 460 filings,
  $7.82B.
- What stops recovery is the data, not the readers. 447 of the 1,000
  filings, 34% of the dollars, have nothing to read: the IRS serves no
  image (169) or the PDF is the IRS's own rendering with nothing
  attached (278). Of the 553 filings with an attachment, 74% reconcile,
  and 72% to 88% in every band.
- Two cheap models agree on 39.5% of pages. Gemini 3.8 Flash settles 42%
  of the disputes and Claude Sonnet 5 another 18%. The 28.7% of pages
  left flagged are loaded from Sonnet alone with a mark; checked by hand
  on 57 such pages from the small filings, Sonnet had 40 pages exactly
  right and 94% of the dollars.
- Bands C and D in full are 8,515 more filings, about $270 in model
  calls and a day on one small EC2 server, adding an estimated 3,000
  filings and $3.2B. Projected total: about 3,400 filings and $11.4B of
  the $25.0B in placeholder rows.
- Open before loading: the rule for flagged pages (load with a mark, as
  now, or leave out), the 3,091 "various" filings ($35.5B) the classifier
  never sends to the PDF, and the matcher pass that turns 235,622
  recovered rows into EINs.

## Terms

| term | meaning |
|---|---|
| placeholder filing | a 990-PF whose grant list in the form is a "see attached" row (or similar) holding at least half of the declared total; the real list is in an attachment |
| declared total | Part I line 25 of the 990-PF, the grants the foundation says it paid in the year; every recovered list has to add up to it |
| IRS image | the PDF of the whole return the IRS publishes on its Tax Exempt Organization Search site (TEOS); fetching it is free |
| attachment | the filer's own pages at the end of the image, after the IRS-rendered form pages; only these are sent to the models |
| readable filing | a filing whose image the IRS served and that has an attachment |
| bands | filings grouped by declared total: A over $100M, B $10M to $100M, C $1M to $10M, D under $1M |
| the sample, the frame | the first 100 filings, drawn to develop the method; the 1,000 the method was tested on, which contain them |
| readers | the vision-language models; the base pair (Qwen3-VL, Gemini 3.5 Flash Lite) reads every page, the escalation readers (Gemini 3.8 Flash, then Claude Sonnet 5) only the pages the base pair disagrees on |
| page verdict | agreed (the base pair matched), escalated (an escalation reader matched an earlier reading), or flagged (no two readers agree) |
| reconciled | a filing whose rows, read from its attachment, add up to its declared total within 0.5% and are labelled as grants; only then is it recovered, and the declared total is what is credited |
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
joined to the declared totals in `basic_fields_pf_current`): fetch the
PDF when pointer rows hold at least half of the declared total. A pointer
row reads like "SEE ATTACHED", "STATEMENT 25" or "SCHEDULE ATTACHED". The
query is `data/exploratory/placeholder_population_by_year.sql`, run by
`placeholder_population.py`; the classes and the pattern's precision and
recall are measured in `placeholder_classifier_assessment.sql`.

| tax year | PF filings with grants | declared grants | placeholder filings | placeholder declared | share of filings | share of dollars |
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
rows, where the same rule gives 9,515 filings whose placeholder rows
carry $25.0B. That is the population the projections below scale to; the
declared totals above are larger because a placeholder row rarely
carries the whole declared total.

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
| flagged | none agree | loaded from Sonnet's reading with a mark on every row | 28.7% of pages, 15% of loaded rows | |

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
expenditure-responsibility statement lists more. Those rows are exact and
go into the search too. Hall 2023 reconciles only with them.

## Results on the frame

Recovery falls with filing size because availability does. The readers
reconcile 72% to 88% of readable filings in every band, but 77% of band
A has a readable attachment against 47% of bands C and D. Dollars are
declared totals; credit is the declared amount of each reconciled list,
never the transcribed sum.

| band | in frame | readable | reconciled | of frame | of readable | declared | recovered | dollars, of frame | dollars, of readable |
|---|---|---|---|---|---|---|---|---|---|
| A | 22 | 17 | 15 | 68% | 88% | $4,671M | $2,664M | 57.0% | 83.4% |
| B | 438 | 281 | 201 | 46% | 72% | $11,428M | $5,157M | 45.1% | 69.3% |
| C | 137 | 64 | 51 | 37% | 80% | $448M | $176M | 39.3% | 75.5% |
| D | 403 | 191 | 140 | 35% | 73% | $100M | $37M | 36.6% | 73.7% |
| all | 1,000 | 553 | 407 | 41% | 74% | $16,647M | $8,034M | 48.3% | 73.6% |

| measure | filings | dollars |
|---|---|---|
| recovered, paid lists | 407 of 1,000 (41%) | $8.03B of $16.65B (48.3%) |
| adding 15 filings whose future-payment list reconciled | 422 | $8.18B (49.1%) |
| with flagged pages left out, the floor | 333 (33%): A 12, B 148, C 42, D 131 | $6.05B (36.4%) |
| the 100-filing sample, for comparison | 47 (47%) | 53.3% |
| the 390 filings added in bands C and D | 131 (34%) | 35.2% |

What is not recovered:

- **447 filings with nothing to read, $5.73B (34.4%).** 278 whose PDF is
  the IRS rendering and nothing else ($3.08B); 66 whose image the IRS
  returns as 404 ($1.48B); 103 with no image on the IRS site ($1.17B).
- **146 readable filings that do not reconcile, $2.64B.** 77 where the
  list is present but the rows are off by more than 0.5% ($1.38B); 23
  where a dense page came back partial ($0.73B); 44 whose attachment
  holds no grant table ($0.53B); 2 absent. The first two are the
  readers' to improve; the third is the filer's.

The recovered rows: 235,622 grants with name, address, purpose and
amount, 36,442 of them (15%) from flagged pages and marked as such.
Every row carries the page verdict and the readings it came from, so any
rule change re-derives from the tables at no model cost.

The sample, read first, gave 47 filings and 53.3%; the frame added 540
small filings the sample under-represented, and the rate came down.
Loading flagged pages rather than leaving them out is worth 74 filings
and 12 points on the frame, against 12 filings and 16 points on the
sample. Bands C and D flag half their pages, so their result depends most
on the flagged rule.

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
reading alone, so its accuracy there is the accuracy of 15% of the
recovered rows. On the sample's 23 flagged pages, bands A and B, Sonnet
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

## Shipping the rest

Reading the 8,515 filings of bands C and D not yet in the frame costs
about $270 in model calls and a day on the box, and adds an estimated
3,000 filings and $3.2B. With bands A and B a census, the projected total
is about 3,400 filings and $11.4B of the $25.0B in placeholder rows.

| band | population | still to read | estimated cost | frame rate, filings / dollars | estimated filings recovered | estimated dollars |
|---|---|---|---|---|---|---|
| A | 22 | 0, done | | 68% / 57.0% | 15 | $2.66B |
| B | 438 | 0, done | | 46% / 45.1% | 201 | $5.16B |
| C | 2,296 | 2,159 | $142 | 37% / 39.3% | 855 | $2.79B |
| D | 6,759 | 6,356 | $129 | 35% / 36.6% | 2,348 | $0.64B |
| all | 9,515 | 8,515 | about $270 | | about 3,400 | $11.4B |

The C and D rates rest on 137 and 403 sampled filings, so they carry
about 8 and 5 points of sampling error at 95%; the dollar estimates for
C and D are $2.2B to $3.4B and $0.55B to $0.73B. Cost does not scale
with filings because the small filings are small in every way the
readers pay for:

| | A | B | C | D |
|---|---|---|---|---|
| declared grants per filing | $212M | $26M | $3.1M | $0.26M |
| filings with a readable attachment | 77% | 64% | 47% | 47% |
| attachment pages per readable filing | 34 | 30 | 6.6 | 2.7 |
| grant rows per reconciled filing | 929 | 1,053 | 81 | 43 |
| pages flagged | 24% | 27% | 52% | 38% |
| model cost per filing in the frame | $1.35 | $0.59 | $0.066 | $0.020 |

The full population, at the frame's rates: about 7,700 PDFs served (17
GB in S3), 4,600 filings with an attachment, 24,700 pages; about $575 in
model calls, of which $304 is spent; about 4 hours of reading per 10,000
pages on one t3.xlarge; about 400,000 grant rows.

## What is out of reach

On the frame, 447 filings and $5.73B. On the sample the list with links
is `data/exploratory/placeholder_unreachable.csv`.

- No PDF served: 169 filings. 66 are indexed but return an error page,
  all generated in 2022; 103 have no image listed.
- PDF with no attachment: 278 filings. Wells Fargo 2022 and 2023 ($534M)
  are 27-page images ending with a blank Schedule B. Their 2020 image is
  235 pages with the list in it.
- Attachment that is not the list: 44 filings on the frame. Schusterman
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
14. Placeholder filings are 2.7% of filers and 7.2% of declared grant
    dollars, flat across years; the declared total and the placeholder
    row are different denominators, and the projections use the latter.
15. Paid and future-payment lists reconcile separately; a headline that
    mixes them is a point higher than the paid-only one.
16. The classifier excludes patient assistance and individuals by EIN and
    status flag, never by name, and leaves the "various" class untested.
17. Operationally: render in chunks of 20 pages, never share a temp-file
    prefix between concurrent renders, and stop a run whose first fifty
    results are all errors before the attempt counter turns a broken box
    into a $550 re-read; Ctrl-C is safe and the same command resumes.

## Decisions and their evidence

| decision | evidence | when |
|---|---|---|
| base readers Qwen3-VL and Gemini 3.5 Flash Lite | cheapest pair; agreement exact on 107 of 107 ground-truth pages | Sept 22 |
| escalation to Gemini 3.8 Flash, then Claude Sonnet 5 | 94% and 97% of rows right; neither repeats Flash Lite's mistakes | Sept 22 |
| 3.8 Flash at low reasoning effort (`POLICY_V2`) | $0.0093 a page against $0.0220, 17 of 46 disputes resolved against 16, no loss on the ground truth | Sept 23 |
| agreement is equal name-and-amount multisets, at least one row | amounts alone hide shifted names; two empty readings agreed once and were wrong | Sept 22 |
| same-model repeats never count | wrong on 6 of 57 self-agreements | Sept 22 |
| prompt v4, 200 DPI | v4 inside run-to-run noise for Gemini, a small gain for Qwen; 130 DPI misread digits, 300 cost more for nothing | Sept 22 |
| flagged pages loaded from Sonnet's reading, marked | 11 of 23 right on the sample; 40 of 57 and 94% of dollars on the small filings; leave-out gives 333 filings and 36.4% | Sept 22, measured Sept 24 |
| a base reader out of attempts is absent for the page, which goes through the dispute path | a reader timing out three times on a dense page must not kill a page three other readers can decide | Sept 23 |
| paid and future lists reconciled separately; the headline is paid-only | 15 frame filings reconcile only their future list | Sept 24 |
| the frame's C and D split, 137 and 403 at one sampling fraction | rates set by availability, not count | Sept 24 |
| PDFs kept indefinitely in S3, every reading keyed on the image hash and the request settings | the IRS loses images; a policy change is a new version and re-derives at no cost | Sept 22 |

## Next steps

1. Run bands C and D in full: 8,515 filings, about $270, a day on the
   box, through the one command in the operations doc.
2. Decide the flagged rule for the load: load with a mark, as now, or
   leave out. The measurement is above; the rows carry the mark either
   way, so the choice can change after loading at no model cost.
3. Test the "various" class: twenty PDFs would say whether its $35.5B
   belongs in the population.
4. Load the recovered rows behind a view, separate from GT's rows, and
   run the matcher on name and address. Bump the matching input-shape
   version when they enter the matching views.
5. Report the 2022 image batch to the IRS, with Wells Fargo as the
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
| `givingtuesday_datamart/filing_images.py`, `page_readings.py`, `page_verdicts.py` | the three tables: fetched images, readings, verdicts under a policy |
| `givingtuesday_datamart/vlm_transcription.py` | attachment page to page JSON, with retries and repairs |
| `givingtuesday_datamart/reading_pairs.py` | the page comparison the gate and the scorer share |
| `givingtuesday_datamart/attachment_grants.py` | choosing the list: reconciliation plus evidence |
| `givingtuesday_datamart/exploratory/placeholder_recovery.py` | `sample`, `estimate`, `run`, `transcribe`, `report`, `compare` |
| `givingtuesday_datamart/exploratory/placeholder_ground_truth.py` | the hand-checked pages and the scorer: `verdicts`, `flagged`, `score` |
| `givingtuesday_datamart/exploratory/placeholder_population.py` | the population by tax year |
| `data/exploratory/placeholder_sample_1000.csv`, `_xml_rows.csv` | the frame, and the rows the XML itemises for it |
| `data/exploratory/placeholder_report_v2_1000.csv`, `placeholder_report_v2.csv`, `placeholder_report_v1.csv` | per-filing outcomes on the frame and the sample under each policy |
| `data/exploratory/placeholder_ground_truth.csv`, `placeholder_gt_pages.csv`, `placeholder_flagged_check.csv` | the 138 pages read from the image, the draw, and Sonnet's score on the flagged ones |
| `data/exploratory/placeholder_population_by_year.sql`, `.csv` | the population by tax year |
| `data/exploratory/placeholder_classifier_assessment.sql` | the classifier's classes, precision and recall |
| `data/exploratory/placeholder_404_images_expanded.csv`, `placeholder_unreachable.csv` | the IRS's unserved images; the sample's out-of-reach filings with links |
| `data/exploratory/placeholder_staging.csv` | the sample's fetch manifest, still read by the scorer |
| `s3://givingtuesday-datamart/placeholder-recovery/archive/placeholder-sample-era-data-2026-09-25.zip` | the sample-era reads and frames (the single-read reports, the 610-filing frame and its manifest, the engine differences), archived on 2026-09-25 with a manifest of hashes; the engineering log discusses their numbers |

## Caveats

- Bands A and B are exact. Bands C and D are a 6% sample: about 8 and 5
  points of error on their rates.
- Credit is the declared amount, not the transcribed sum; the headline is
  paid lists only.
- Ground truth measures the readers on 138 pages, not the selector's
  filing-level decisions.
- Recovered grants have not been through the matcher.
- The "various" class is outside the population and untested.
