# Recovering the Grants Behind "SEE ATTACHMENT"

Vibrant Data Labs, September 22, 2026.

Status: measured on a sample of 100 filings. Not yet in the pipeline.
The build plan is in [placeholder_recovery_pipeline.md](placeholder_recovery_pipeline.md).

## Summary

- About 9,500 private-foundation filings report their grants as one row:
  "SEE ATTACHMENT", with the year's total. That is $25B of grants with no
  recipients.
- The XML has no list. The IRS PDF of the same filing usually does,
  because the filer attached it.
- We read those pages with a vision model. We keep a list only when it
  adds up to the declared total and looks like a grant list.
- On the sample, this recovers 42 of 100 filings and 49% of the dollars.
  Model cost for the sample: about $16.
- 39% of the dollars cannot be recovered this way. The IRS serves no PDF,
  or the PDF has no list in it.
- Next step: run both models on every page and accept pages where they
  agree.

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
| cost for 1,782 pages | $3.57 | $12.37 |
| time per page | 33 s | 8 s |

The first approach, Unstructured OCR, is no longer used. It cost more and
lost rows on long lists.

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

The XML sometimes itemises a few grants beside the placeholder, and the
expenditure-responsibility statement lists more. Those rows are exact and
go into the search too. Hall 2023 reconciles only with them.

## Results

100 filings, best of the two models. Dollars are declared totals.

| outcome | filings | dollars | share |
|---|---|---|---|
| reconciled | 42 | $3,144M | 52% |
| list found, rows 88% to 118% of target | 10 | $349M | 6% |
| list found, non-cash page misread (Bezos 2023) | 1 | $157M | 3% |
| attachment present, no money table | 3 | $36M | 1% |
| attachment is not the list | 3 | $490M | 8% |
| PDF has no attachment | 26 | $707M | 12% |
| IRS serves no PDF | 15 | $1,181M | 19% |

Credited recovery is $2,977M, 49.1% of the sample. Credit is the declared
amount of each reconciled target, so a filing's paid list can count while
its future list does not.

By engine:

| engine | reconciled | dollars credited |
|---|---|---|
| Unstructured OCR, the baseline | 29 | 30.4% |
| Qwen3-VL instruct | 35 | 44.0% |
| Gemini 3.5 Flash Lite | 38 | 44.3% |
| either model | 42 | 49.1% |

The two models fail on different filings. Eleven filings reconcile under
only one of them. Where a model fails on a list that is present, the
cause is a transcription slip: a duplicated row, a contact name read as a
grantee, a continuation page labelled as the wrong list, or the non-cash
page where fair market value sits next to book value.

Projected across all 9,518 addressable filings at these rates: $7B to
$8B.

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

## Next steps

1. Run both models on every page. Accept a page when they agree on its
   amounts. Send the rest to a third model.
2. Carry a grant heading onto heading-less continuation pages, as the OCR
   path already did.
3. Revise the prompt: one section per list on a page, and a contact name
   stays inside its grantee's row.
4. Ground truth: 15 filings with rows counted by hand. This sets the
   row-level precision and the acceptance policy for near-misses.
5. Run band B in full with the broader classifier.
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
| `tests/test_attachment_grants.py` | 38 tests |
| `data/exploratory/placeholder_sample_100.csv` | the sample frame |
| `data/exploratory/placeholder_sample_xml_rows.csv` | rows the XML itemises for the sampled filings |
| `data/exploratory/placeholder_staging.csv` | which filings have a PDF, and where the attachments start |
| `data/exploratory/placeholder_report_*.csv` | per-filing outcomes under each engine |
| `data/exploratory/placeholder_engine_differences.csv` | filings where the engines differ |
| `data/exploratory/placeholder_unreachable.csv` | the 45 out-of-reach filings, with links |
| `~/.cache/irs_index/` | PDFs, page renders, and each model's per-page JSON |

```bash
python -m givingtuesday_datamart.exploratory.placeholder_recovery sample
python -m givingtuesday_datamart.exploratory.placeholder_recovery stage
python -m givingtuesday_datamart.exploratory.placeholder_recovery transcribe --model alibaba/qwen3-vl-instruct --workers 40
python -m givingtuesday_datamart.exploratory.placeholder_recovery report --results ~/.cache/irs_index/vlm/alibaba__qwen3-vl-instruct --out qwen.csv
python -m givingtuesday_datamart.exploratory.placeholder_recovery compare unstructured=baseline.csv qwen=qwen.csv
```

## Caveats

- n = 100. Band A is exact. Bands C and D are thin.
- Credit is the declared amount, not the transcribed sum.
- No ground truth yet. Accepted lists were checked by inspection, not by
  counting rows in the PDF.
- Recovered grants have not been through the matcher.
