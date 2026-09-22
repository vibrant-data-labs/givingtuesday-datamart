# Recovering the Grants Behind "SEE ATTACHMENT"

Vibrant Data Labs, September 22, 2026.

Status: measured on a sample of 100 filings, read four times; an expanded
frame of 610 is staged and waiting to be read. Not yet in the pipeline. The build plan is
in [placeholder_recovery_pipeline.md](placeholder_recovery_pipeline.md).

## Summary

- About 9,500 private-foundation filings report their grants as one row:
  "SEE ATTACHMENT", with the year's total. That is $25B of grants with no
  recipients.
- The XML has no list. The IRS PDF of the same filing usually does,
  because the filer attached it.
- We read those pages with a vision model. We keep a list only when it
  adds up to the declared total and looks like a grant list.
- One read of the sample recovers 36 to 40 of 100 filings and 45% to 49%
  of the dollars, for $4 to $12 in model cost. The sample has been read
  four times (two models, two prompts); together the reads reach 45
  filings and 53%.
- Pages with 25 or more rows come out differently on every read. The
  page total hides it, because names shift against amounts while the sum
  barely moves.
- 39% of the dollars cannot be recovered this way. The IRS serves no PDF,
  or the PDF has no list in it.
- Next step: run both models on every page and accept pages where they
  agree on names and amounts. Send the rest to a third model.

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

## Where the documents come from

Code: `givingtuesday_datamart/irs_source.py`.

- PDF: from the IRS TEOS service, by EIN and tax period. Where the IRS
  index carries a RETURN_ID, the exact image is pinned. No sampled filing
  had two images to choose from.
- XML: from GivingTuesday's mirror, verified byte-identical to the IRS
  copy.

Every PDF is a scanned image with no text layer. It is two documents in
one: the IRS rendering of the XML first, then the filer's attachments.
The IRS pages come at five fixed image widths, so the boundary is read
from `pdfimages -list` without rendering a page.

| pages in the 85 sampled PDFs | count |
|---|---|
| total | 4,746 |
| IRS-rendered, skipped | 2,964 (62%) |
| filer attachments, transcribed | 1,782 (38%) |

26 of the 85 PDFs have no attachment at all. Those filings are "no list"
before any model runs.

## Which filings need their PDF

The rule, measured on the loaded datamart for 2020 to 2025: fetch the PDF
when pointer rows hold at least half of the declared total. A pointer row
reads like "SEE ATTACHED", "STATEMENT 25" or "SCHEDULE ATTACHED".

| class | filings | declared |
|---|---|---|
| fetch the PDF | 12,977 | $64.6B |
| withheld: "available upon request" | 788 | $5.9B |
| "various", no pointer | 3,088 | $35.5B |
| one named row, a pass-through | 105,017 | $78.0B |
| itemised | 332,761 | $360.5B |

Two exclusions. Both are by EIN or by the filing's own status flag, never
by name pattern, because a name pattern also catches ordinary grantmakers
like Genentech Foundation.

- Three patient-assistance programs: Genentech Patient Foundation,
  Boehringer Ingelheim Cares, GlaxoSmithKline Patient Access. $22B of
  medicine given to individuals. 4,565 of the "fetch" filings are patient
  assistance.
- Rows whose recipient status is `I`, meaning individuals.

The narrow pattern the sample was drawn with stays as `PLACEHOLDER`. The
broader one is `is_pointer()`. It adds about 1,750 filings and $11.3B.
The query is `data/exploratory/placeholder_classifier_assessment.sql`.

Grants paid (line 3a) and grants approved for future payment (line 3b)
are separate totals and separate statements. Each is reconciled on its
own.

## The sample

9,518 addressable filings hold $25.05B. 22 of them hold $4.67B. So the
sample is stratified by declared dollars, and band A is a census. Seed
20260921; the frame regenerates identically.

| band | declared per filing | in population | sampled | dollars sampled |
|---|---|---|---|---|
| A | over $100M | 22 | 22 | $4.67B of $4.67B |
| B | $10M to $100M | 442 | 44 | $1.31B of $11.52B |
| C | $1M to $10M | 2,299 | 22 | $0.08B of $7.11B |
| D | under $1M | 6,755 | 12 | $0.00B of $1.76B |

## Reading the pages

Each attachment page is rendered at 200 DPI and sent to a vision model.
The model returns the page's heading, its kind, one row per grant, and
any printed totals. Two models were run on all 1,782 pages. The bake-off
that chose them is in the pipeline doc.

| | Qwen3-VL instruct | Gemini 3.5 Flash Lite |
|---|---|---|
| cost for one read of 1,782 pages | $3.57 to $4.24 | $12.21 to $12.37 |
| time per page | 33 s | 8 s |

The first approach, Unstructured OCR, is no longer used. It cost more and
lost rows on long lists.

One finding from the sample changed the retry rule. Qwen returned no rows
for 401 dense pages, twice each, when asked for JSON output mode. The
same pages read fully without that mode. So an empty or cut-off list
page is now asked again without JSON mode, and the fuller answer is kept.

The prompt is versioned. Version 3 tells the model what a heading is and
keeps a contact person inside their organisation's row. Version 4, the
current one, defines rows by the amount column: every printed amount is
a row, and a line with no amount belongs to the row above it. Version 3
had said a wrapped name is still one row, and the model then merged two
neighbouring recipients into one.

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

## Results

100 filings, best of four reads (two models, two prompt versions).
Dollars are declared totals.

| outcome | filings | dollars | share |
|---|---|---|---|
| reconciled | 45 | $3,346M | 55% |
| list found, rows 88% to 118% of target | 8 | $304M | 5% |
| attachment present, no money table | 3 | $36M | 1% |
| attachment is not the list | 3 | $490M | 8% |
| PDF has no attachment | 26 | $707M | 12% |
| IRS serves no PDF | 15 | $1,181M | 19% |

Credited recovery is $3,205M, 52.9% of the sample. Credit is the declared
amount of each reconciled target, so a filing's paid list can count while
its future list does not.

By read:

| read | reconciled | dollars credited |
|---|---|---|
| Unstructured OCR, the baseline | 29 | 30.4% |
| Qwen3-VL, first read | 36 | 44.6% |
| Gemini Flash Lite, first read | 40 | 48.2% |
| Qwen3-VL, second read | 37 | 46.4% |
| Gemini Flash Lite, second read | 40 | 48.9% |
| any of the four | 45 | 52.9% |

The number to quote is a single read's. The union grows with every read
because dense pages come out differently each time, and a best-of-four
chosen afterwards is not a method.

Two reads of the same page agree on every name and amount 89% to 97% of
the time when the page has fewer than 25 rows. With 25 rows or more,
31% to 38% of the time, and the two models agree with each other on one
such page in six. Checked against the page image, the errors are a
merged pair of rows that shifts every amount below it, a split row that
shifts them the other way, or cents read as dollars. The page sum barely
moves, so reconciliation cannot catch them. Page-level agreement has to
compare names with their amounts, not amounts alone.

## Ground truth

83 pages were read from the page image by hand and scored against each
model's reading, name with amount. The pages were drawn across row
density and across whether the readers agreed, plus every page of seven
near-miss filings.

| reader | rows right, precision | rows right, recall | pages exact |
|---|---|---|---|
| Gemini Flash Lite, second read | 90% | 90% | 53 of 83 |
| Qwen3-VL, second read | 82% | 68% | 40 of 83 |

Three findings:

- When the two models agree on a page, the page is right 37 times in
  38. Agreement can accept a page without a further look.
- When they disagree, Gemini is right on 16 pages of 45, Qwen on 3, and
  neither on 26. Choosing between the two would still get only 85% of
  the rows. A disagreeing page needs a third reading.
- Every near-miss filing's true rows sum exactly to the declared total.
  The lists are complete on the page. The readers misplace rows; they
  do not miss lists.

Pages under 25 rows are read at 90% or better by every model. The pages
no model reads right have wrapped names in many narrow columns, not
simply many rows: Johnson & Johnson's 75-row single-column pages come
out exact.

The 83 pages were also read under the current prompt, version 4, twice.
For Gemini the prompt makes no measurable difference: two runs of the
same prompt differ from each other as much as version 3 differs from
version 4. For Qwen version 4 is a small real gain.

The repeat read settled one more thing. Two different models agreeing on
a page were right 107 times in 107. The same model read twice agreed
with itself on 57 pages and was wrong on 6 of them. A model repeats its
own mistakes, so agreement only counts between different models, and
the third reader must be a third model.

Projected across all 9,518 addressable filings at a single read's rate:
$7B to $8B.

## What is out of reach

44 filings and $2.4B. The full list with links is
`data/exploratory/placeholder_unreachable.csv`.

- No PDF served: 15 filings. Six are indexed but return an error page,
  and all six images were generated in 2022. Nine have no image listed.
- PDF with no attachment: 26 filings. Wells Fargo 2022 and 2023 ($534M)
  are 27-page images ending with a blank Schedule B. Their 2020 image is
  235 pages with the list in it.
- Attachment that is not the list: 3 filings. Schusterman 2023 attached
  only its expenditure-responsibility statement. Bezos 2021 attached a
  narrative. Roberts 2021 attached one page of a longer list.

Other sources were checked:

- The XML gives no sign that an attachment exists.
- ProPublica has 2 of the 15 missing PDFs, both from the IRS's older
  image program.
- The New York charities registry serves the full filing, but behind a
  CAPTCHA. It is a manual step. Six of the 15 are New York filers.
- Foundation websites list recipients without amounts.

## The expanded frame

Band B in full, C to 100 and D to 50, on top of the original 100: 610
filings, $16.4B. Staged on September 22; not yet read. Frame:
`placeholder_sample_expanded.csv`; manifest: `placeholder_staging_expanded.csv`.

| band | filings | has an attachment | PDF, no attachment | no PDF |
|---|---|---|---|---|
| A | 22 | 17 ($3.19B) | 2 ($0.53B) | 3 ($0.94B) |
| B | 438 | 281 ($7.44B) | 93 ($2.37B) | 64 ($1.62B) |
| C | 100 | 51 ($0.17B) | 31 ($0.11B) | 18 ($0.05B) |
| D | 50 | 24 ($0.01B) | 18 ($0.00B) | 8 ($0.00B) |
| all | 610 | 373, 66% of dollars, 9,347 pages | 144, 18% | 93, 16% |

Two things the larger frame settles:

- **Every 404 is a 2022 image.** All 38 indexed-but-unserved PDFs were
  generated by the IRS in 2022. None generated in 2021 or 2023 onward
  fails. This is the IRS report, with the list of 38 in
  `placeholder_404_images_expanded.csv`.
- **Unreachability is a tax-year-2020 problem.** 40% of 2020 filings
  have no usable PDF, 11% of 2021, 5–7% of 2022 and 2023, none of 2024.

Reading the 9,347 attachment pages with both models costs about $110.

## Next steps

1. Run both models on every page. Accept a page when they agree on its
   names and amounts. Send the rest to a third model. A page no two
   readers agree on is flagged, not loaded.
2. ~~Carry a grant heading onto heading-less continuation pages.~~ Done.
   On the stored pages alone it moved Qwen from 35 to 36 filings and
   Gemini from 38 to 40.
3. ~~Revise the prompt.~~ Done, twice: version 3 was read on the sample,
   version 4 fixes the merge it introduced and is the prompt for the next
   read. "One section per list" was dropped: no page in the sample holds
   two lists.
4. ~~Ground truth.~~ Done: 83 pages checked against the image, seven
   near-miss filings in full. When the two models agree on a page's
   names and amounts, the page is right 37 times in 38. When they
   disagree, neither is right on 26 pages of 45, so the third reader is
   not optional. Every near-miss filing's true rows sum exactly to the
   declared total: the lists are complete, the readers misplace rows.
5. Read the expanded frame, once, with the method above in place.
6. Report the 2022 image batch to the IRS, with Wells Fargo as the
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
| `givingtuesday_datamart/vlm_transcription.py` | attachment page to page JSON, with retries and repairs |
| `givingtuesday_datamart/attachment_grants.py` | choosing the list: reconciliation plus evidence |
| `givingtuesday_datamart/exploratory/placeholder_recovery.py` | `sample`, `stage`, `transcribe`, `report`, `compare` |
| `givingtuesday_datamart/exploratory/placeholder_ground_truth.py` | `pick`, `seed`, `fix`, `accept`, `add`, `show`, `crop`, `score` |
| `data/exploratory/placeholder_gt_pages.csv`, `placeholder_ground_truth.csv` | the 91-page draw and the 83 pages read from the image |
| `tests/test_attachment_grants.py`, `tests/test_vlm_transcription.py` | 45 tests |
| `data/exploratory/placeholder_sample_100.csv` | the sample frame |
| `data/exploratory/placeholder_sample_xml_rows.csv` | rows the XML itemises for the sampled filings |
| `data/exploratory/placeholder_staging.csv` | which filings have a PDF, and where the attachments start |
| `data/exploratory/placeholder_report_*.csv` | per-filing outcomes under each read (`_v3` = the second read) |
| `data/exploratory/placeholder_engine_differences.csv` | filings where the engines differ |
| `data/exploratory/placeholder_unreachable.csv` | the 45 out-of-reach filings, with links |
| `~/.cache/irs_index/` | PDFs, page renders, and each model's per-page JSON |

```bash
python -m givingtuesday_datamart.exploratory.placeholder_recovery sample
python -m givingtuesday_datamart.exploratory.placeholder_recovery stage
python -m givingtuesday_datamart.exploratory.placeholder_recovery transcribe --model alibaba/qwen3-vl-instruct --workers 40
python -m givingtuesday_datamart.exploratory.placeholder_recovery report --results ~/.cache/irs_index/vlm/alibaba__qwen3-vl-instruct-v3 --out qwen.csv
python -m givingtuesday_datamart.exploratory.placeholder_recovery compare unstructured=baseline.csv qwen=qwen.csv
```

## Caveats

- n = 100. Band A is exact. Bands C and D are thin.
- Credit is the declared amount, not the transcribed sum.
- Ground truth covers 83 pages. It measures the readers, not the
  selector's filing-level decisions.
- Recovered grants have not been through the matcher.
