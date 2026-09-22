# Recovering the Grants Behind "SEE ATTACHMENT"

*Vibrant Data Labs — September 21, 2026. Work done against GivingTuesday's
Combined Grants Datamart delivery of September 15, 2026, and the IRS's own
PDF images of the same filings. Revised the same evening after an audit of
the first pass; the audit is Part 7.*

---

## TL;DR

Thousands of 990-PF filings itemise nothing. Part XV carries a single row
reading "SEE Attachment 22" whose amount is the filer's entire grant total.
GT investigated and concluded the lists "were never submitted in
machine-readable form." For the **XML** that is true.

It is not true of the **PDF**. The IRS renders an image of the full
submission, attachments included, and the grant lists are in it.

We built the recovery path end to end and measured it on a stratified sample
of 100 filings. The first pass reported 46% of sampled dollars recovered. An
audit of those 36 "reconciled" filings found that a third of that figure was
false positives or wrong row sets: capital-gains schedules and Part I form
lines that happened to sum to the target, and real lists padded with
subtotals, expense rows and a blank-name $278M total. **Reconciliation alone
is not a gate** — over a 0.5% tolerance, some combination of a filing's
dozens of money tables reaches almost any number.

The selector was rebuilt so that every accepted list carries a second,
independent piece of evidence. On the same 100 filings it now stands at:

- **Reconciled on evidence — 29 filings, $1,841M (30.4%), 4,760 grants**
  with names, addresses and amounts.
- **In the parse but 90–110% covered — 14 filings, $1,552M (25.6%).** The
  list is there and labelled; OCR dropped a few percent of the rows on lists
  running to hundreds of pages. This is the backlog, and it is an OCR
  problem, not a selection problem.
- **Unreachable — $2,372M (39.1%).** The IRS serves no PDF ($1,181M), or the
  PDF has no grants section in it ($1,191M).

---

## Part 1: what GT delivered, and what it answers

The September 15 file (`combined-grants-datamarts-gt_team_priority-20260915.csv`,
895 MB, 2,238,010 rows) is **a reparse of the 93,094 filings on our
`gt_team_priority` list** — GT's response to
[missing_grants_capture_analysis.md](missing_grants_capture_analysis.md) —
not a general refresh.

The format is a breaking change, and a good one: 40 semantic columns
replacing the compressed IRS codes, unified across seven sources keyed by a
`Source` discriminator, including **Schedule F (foreign grants) and 990-PF
Expenditure Responsibility, which we have never had**. The accompanying data
dictionary is a complete old→new crosswalk and flags source peculiarities.

| Source | rows | $ | placeholder rows |
|---|---|---|---|
| SCHEDULE_I_P2 | 1,716,416 | $222.1B | 178 |
| 990PF_P14_3A | 396,936 | $45.7B | **10,488 ($44.7B)** |
| SCHEDULE_F_P2 *(new)* | 71,038 | $28.8B | 0 |
| SCHEDULE_I_P3 | 41,546 | $44.5B | 0 |
| others | 12,074 | $6.5B | 560 |

Against our priority list: **82% of `partial_rows` filer-years recovered,
9% of `no_rows`, 6% of `aggregate_placeholder`.** Fidelity now itemises
(64,291 / 130,512 / 72,437 rows for 2020–22); One Earth's 2022 gap is
closed. The Schedule I extraction problem is substantially fixed.

### What their one-pager answers

1. **Declared totals are not in this file.** Our reconciliation method needs
   a join back to `basic_fields_pf`, at a different grain.
2. **Amended filings — declined.** 665 filer-years, "expected, take the most
   recent by ingestion date." There is no ingestion-date column; only
   `FileSha256` and `URL`. Their count also doesn't reproduce: the file gives
   **804 of 85,605**, identical by either key.
3. **Placeholders — investigated, called source-level.** This is the claim
   this document overturns.

**Unaddressed:** the 990-PF verbatim block doubling (~$7B, 2024-batch) and
the root cause of the 2021/2022 Schedule I gaps.

**Fixed without being claimed:** provenance mislabeling. Fidelity's 2021
original and amendment now carry distinct URLs, so versions are separable.

---

## Part 2: the IRS's own copies

Two artifacts, two different routes. Both are wired up in
[`givingtuesday_datamart/irs_source.py`](../givingtuesday_datamart/irs_source.py).

- **The `irs-form-990` S3 bucket is retired** — it still resolves and is
  empty. Anything citing `s3.amazonaws.com/irs-form-990/<id>_public.xml` is
  stale.
- **XML** now ships only inside 250 MB+ batch ZIPs. We take GT's data-lake
  mirror instead, having verified it **byte-identical to the IRS original by
  SHA-256** on filings from 43 KB to 51 MB, across both compression types.
- **PDFs** come from TEOS (`/teos/details/returnsSearch/<ein>`), whose
  `STATICFILEPATH` appends to `apps.irs.gov`. Plain curl, no auth.

**The one real trick:** TEOS keys on (EIN, TAX_PERIOD, RETURN_TYPE), which is
coarser than a filing — an original and its amendment share a tax period. The
image filename's trailing token is `YYYYMMDD` plus the IRS index's own
`RETURN_ID`, so where RETURN_ID is populated an object id pins exactly one
image. Fidelity's FY2022 is the worked case: two 990 images, and RETURN_ID
21417445 / 22309568 separates original from amendment.

RETURN_ID coverage, measured on the cached indexes (an earlier draft of this
document had these wrong):

| index year | rows | RETURN_ID populated |
|---|---|---|
| 2021 | 589,904 | 100% |
| 2022 | 656,503 | 95% |
| 2023 | 705,156 | 97% |
| 2024 | 728,719 | 62% (990-PF rows: 92%) |
| 2025 | 748,906 | 55% (990-PF rows: 66%) |
| 2026 | 436,239 | 52% |

In the sample it did not matter: 91 filings had exactly one TEOS image and 9
had none. No sampled filing ever had two images to choose between.

### The PDFs are images

Every page is a CCITT fax-encoded bitonal scan. No fonts, no text layer,
zero extractable characters, at 300 DPI, which is good for OCR. **So this
is an OCR problem, not a parsing problem.**

**Only the attachments need OCR, and the boundary is free.** Each image is
two documents stapled together: the IRS's rendering of the XML (which we
already hold as data) and then whatever the filer attached. The IRS renders
its pages at a fixed 2246 px width cropped to content; the filer's pages are
full letter pages (2550×3300) or odd landscape sizes. `pdfimages -list`
reads this without rendering, and pypdf cuts the pages out without
re-encoding. On the 85 cached PDFs the IRS-rendered region is 53% of all
pages — 40% in band A, 90% in band D — and every reconciled list sits
inside the attachment region. It is also where every false positive in
Part 7 came from.

---

## Part 3: the finding

Siegel Family Endowment's FY2024 XML contains exactly one grant element:

```xml
<GrantOrContributionPdDurYrGrp>
  <RecipientBusinessName><BusinessNameLine1Txt>SEE Attachment 22</BusinessNameLine1Txt></RecipientBusinessName>
  <Amt>27792259</Amt>
</GrantOrContributionPdDurYrGrp>
```

682 elements total, no `AdditionalData` section, nothing over 300 characters.
GT is right that the XML has no list.

The PDF has 41 pages. Pages 31–38 are headed **"FORM 990-PF, PART XIV —
GRANTS AND CONTRIBUTIONS PAID DURING THE YEAR, STATEMENT 22"**, and page 38
ends `TOTAL $ 27,792,259` — the placeholder amount to the dollar. Pages
39–41 are Statement 23, grants approved for future payment.

Run through Unstructured's Pipelines job, the rows come back clean:

```
Scratch Foundation, 459 Columbus Avenue Suite 1112, New York NY 10024, PC,
  To provide general operating support. $2,500,000
```

Those addresses matter more than the names: 990-PF grant rows carry no
recipient EIN, so our matcher runs on name + address.

---

## Part 4: the addressable population

9,964 placeholder filings, $47.38B. Two exclusions, on evidence rather than
names:

| | filings | dollars |
|---|---|---|
| Patient assistance (3 EINs) | 14 | $22.11B |
| `recipient_foundation_status = 'I'` | 432 | $0.21B |
| **Organization grantmakers** | **9,518** | **$25.05B** |

Genentech Patient Foundation ($13.1B), Boehringer Ingelheim Cares ($5.2B) and
GlaxoSmithKline Patient Access ($3.8B) move donated medicine to individuals;
Genentech's placeholder text reads `Eligible Patients (See Schedule #2)` and
its rows carry status `I`.

**Exclude by EIN and by the filing's own status flag, never by name pattern.**
A name pattern also catches Amgen Foundation, Genentech Foundation and Ruth
Lilly Foundation — ordinary grantmakers, $216M between them.

The population is violently top-heavy: 22 filings hold $4.67B, 6,755 hold
$1.76B. The sample is therefore stratified, with band A a **census**. The
frame regenerates identically from seed 20260921.

### Does the filing need its PDF? Assessing the classifier

Everything below starts from a row-level pattern on the recipient name —
"see/refer" followed by attach/schedule/statement/list — and the earlier
drafts only ever eyeballed it on GT's CSV. It was assessed on the loaded
datamart instead: `privategrants_current` for 2020 onward (9.58M rows,
$564.6B, 521,065 filer-years) joined to Part I line 25 in
`basic_fields_pf`. The query is
[`placeholder_classifier_assessment.sql`](../data/exploratory/placeholder_classifier_assessment.sql).

**Precision is not the problem.** The pattern flags 12,472 rows, $54.7B,
11,706 filings. The head is what you would expect ("SEE ATTACHED" 1,920
rows and $7.0B, "SEE ATTACHED SCHEDULE" 1,810, "SEE ATTACHED LIST" 1,186,
"SEE ATTACHED STATEMENT" 877), and a random 70 of the names seen only once
are all pointers too. The nearest thing to a false positive is a named
recipient with a pointer beside it — "THE LIEBER INSTITUTE FOR BRAIN
DEV-SEE ATTACHED", "RENAISSANCE CHARITABLE FOUNDATION (SEE ATTACHED)" —
and the attachment exists in those cases as well. An address on the row is
no evidence against: 6,316 flagged rows carry one, and it is the filer's
own or its bank's.

At the filing level the row flag is a decision. Of the 11,706 filings with
a flagged row, 10,989 (94%) have flagged dollars within 10% of the declared
total ($52.0B); 225 have under half, 187 between half and 90%, 46 over
110%, and 259 no declared total. Only 572 filings mix flagged and
unflagged rows ($1.5B against $1.25B).

**Recall is where it loses.** Two probes: a broader pattern, and the
text-independent shape of a placeholder (at most three rows, one of them
at least 90% of the declared total).

- The broader pattern lets words sit between "see" and the attachment word
  ("SEE GRANTS PAID ATTACHMENT", $168M), accepts a bare reference as the
  whole name ("SCHEDULE ATTACHED" in 456 filings, "STATEMENT 25", "ATCH 4",
  "ATTACHMENT B"), and a bare category ("GRANTS" in 44 filings, $1.6B). It
  adds about 1,750 filings and $11.3B, nearly all of it confirmed by shape
  — the row carries the filing's declared total. The single largest
  addition is Sanofi Cares' "ATCH 4" at $6.2B, patient assistance and
  excluded downstream anyway. Two tokens were tried and dropped after
  reading the additions ("rider" is Rider University; a bare "att" is
  people — 524 rows, $15M between them), and anything reading "available
  upon request" is kept out (147 rows, $231M): that is a bare reference to
  nothing.
- The shape probe alone is useless as a rule. 105,017 filings ($78.0B) are
  single-row and named, and the top of that pile is Gates ($31B from the
  trust), Kellogg, and every pass-through foundation that gives its whole
  year to one donor-advised fund sponsor.

**Two classes a PDF cannot help.** 788 filings ($5.9B) withhold the list
outright — "available upon request", "kept on file", HIPAA. 3,088 filings
($35.5B) name "VARIOUS …" with no pointer; $31B of that is patient
assistance ("VARIOUS INDIVIDUALS", "VARIOUS NEEDY PATIENTS"), and whether
the organisational remainder attaches a list anyway is untested — twenty
PDFs would settle it.

The filing-level rule that falls out, over 2020–2025 (measured before the
two corrections above, which move at most 125 filings and $0.23B from the
first row to the withheld row):

| class | filings | declared |
|---|---|---|
| **fetch the PDF** — pointer rows hold ≥ 50% of declared | 12,977 | $64.6B (of which 4,565 filings are patient assistance) |
| mixed — pointer rows under 50% | 649 | $10.8B |
| withheld — no PDF will help | 788 | $5.9B |
| "various", no pointer — untested | 3,088 | $35.5B |
| single row, named — pass-through, not placeholder | 105,017 | $78.0B |
| itemised | 332,761 | $360.5B |
| no declared total | 65,785 | — |

Against the CSV: the priority-list file gave 9,964 placeholder filings and
$47.4B under the original pattern; the full datamart gives 11,706 and
$54.7B. The priority list is about 85% of the population.

The original pattern stays as `PLACEHOLDER` so the sample frame
regenerates identically; the broader one is `is_pointer()` in
[`attachment_grants.py`](../givingtuesday_datamart/attachment_grants.py)
and is what the selector uses to drop pointer rows from OCR'd tables. It
changes nothing on the 100 sampled filings.

**Paid and future are separate targets.** Part XV line 3a (grants paid) and
line 3b (approved for future payment) are separate declared totals and
separate statements in the PDF. The first frame summed both into one
number; 22 of the 100 sampled filings, and 9 of the 22 in band A, have a
placeholder on both lines. The frame now carries `placeholder_paid` and
`placeholder_future`, and each is reconciled on its own.

---

## Part 5: how selection works

Full implementation:
[`attachment_grants.py`](../givingtuesday_datamart/attachment_grants.py).

Nothing labels the grant list reliably. The XML says "SEE Attachment 22", the
PDF says "STATEMENT 22"; the next filer says "SEE ATTACHMENT C" over
"CHARITABLE LISTING"; a third says "SEE ATTACHED" and names nothing. Wells
Fargo uses four phrasings in four years.

So labels are never the *key*. **The placeholder amount states what the
answer sums to**, which turns selection into a search: find the set of
statements whose amount column reconciles to the declared total.

But reconciliation is not sufficient on its own (Part 7 is the evidence).
Every accepted run therefore needs a second piece of evidence:

- **Labelled runs.** Tables under a heading, page footer or column header
  that says grants — "Grants Paid Schedule", "Attachment to Part XV, Line
  3a", a column called "Grantee", "Payee", "Payment Amount" or "Purpose of
  Grant". Only these may be combined freely across the document (a
  statement is routinely split over dozens of pages; cash, non-cash and
  future-payment sections sit apart). Labels are read per page, and a grant
  heading carries over only to the heading-less pages immediately after it.
- **Unlabelled runs.** A contiguous run of pages whose rows are mostly
  organisation names, and which never crosses a table whose heading or
  header row says it is a revenue, asset or expense schedule. This rescues
  attachments whose headings OCR'd badly, and it may extend a labelled list
  onto an unlabelled continuation page — but it may not pad a labelled list
  that already holds most of the money, which is exactly how the first
  version went wrong.

### Guards, each from a filing that broke it

| guard | what it stops |
|---|---|
| placeholder rows excluded, **no leading `\b`** | OCR concatenates cells, so the Part XV line reads `…during the yearSEE ATTACHED SCHEDULE1759 R ST…` |
| rows equal to any declared total are totals | Wyss's schedule ends `['', '', '', 125722675]` — unlabelled, and the schedule summed to exactly 2× declared |
| total rows by **cell**, not by row text; "Subtotal", "Page Total" included | `\btotal\b` on the joined row missed Klarman's nine "Subtotal Healthy Democracy" rows ($65M counted as grants) and dropped real grants whose purpose text says "a total of" |
| money with no words at all is a carried-forward total | Wells Fargo 2020's list ends with a blank-name row of $277,927,126 that the first version credited as a grant |
| pointer names are not grants | Hall's Part XV summary reads `GRANTS PAID REPORT 52,829,934 / MATCHING GIFTS REPORT 140,000` and reconciled at three rows |
| "recipient" as a name column only when it is the whole header | Pritzker's "Foundation Status of Recipient" column named every grant `501 (c) 3`, and the list vanished |
| heading gate: Parts I–IV, XII, XIII and the named IRS schedules veto | Wyss's Legal Fees + Other Assets + Other Decreases + Other Expenses sum to within 0.16% of its grant total |
| `x(?:iv\|v)` not `x[iv]+` | the greedy form also matches Parts XI, XII, XIII |
| amount column by **header**, never position | non-cash sections carry book value beside fair market value — $155M against $340M given |
| majority of amounts under $100 → form lines | J&J's Part I reads `Interest on savings` = 3, `Dividends` = 4 |
| median name length > 110 → prose | Schusterman's Part VIII-A narrates activities with dollar figures beside them |
| a grant heading carries forward; the form's own does not | the Part XV form page is titled "…Paid During the Year or Approved for Future Payment", and carried forward it made every later heading-less page a future-payment list |

The first version tried and reverted a 50% recipient-name share as a
universal guard, because the name column is often misidentified on real
lists. It is back, applied only where it is the *only* evidence — unlabelled
runs.

---

## Part 6: results

100 filings, 4,746 pages, 27 minutes of OCR. Cells are `filings / $M declared`.

| outcome | A | B | C | D | **TOTAL** |
|---|---|---|---|---|---|
| **reconciled on evidence** | 10 / 1,335.5 | 15 / 545.3 | 3 / 9.6 | 1 / 0.0 | **29 / $1,890.5M** |
| list in parse, 90–110% covered | 5 / 1,392.3 | 6 / 149.1 | 2 / 9.8 | 1 / 0.3 | **14 / $1,551.5M** |
| list in parse, other | 1 / 101.7 | 4 / 63.8 | 2 / 3.6 | — | **7 / $169.1M** |
| list partial (< half the money) | — | 3 / 76.2 | 2 / 4.8 | — | **5 / $81.0M** |
| PDF ok, no grants section | 3 / 897.7 | 10 / 259.0 | 9 / 32.7 | 8 / 1.5 | **30 / $1,190.9M** |
| IRS lists a PDF, serves 404 | 2 / 692.5 | 4 / 180.0 | — | — | **6 / $872.5M** |
| IRS has no PDF at all | 1 / 251.5 | 2 / 40.7 | 4 / 15.8 | 2 / 0.8 | **9 / $308.9M** |
| **ALL** | 22 / 4,671.2 | 44 / 1,314.2 | 22 / 76.4 | 12 / 2.6 | **100 / $6,064.4M** |

**Recovered: $1,841.2M (30.4%), 4,760 grants.** Recovery credits the
declared amount of each reconciled target; the $49M gap to the reconciled
row above is four filings whose paid list reconciled and future list did
not (Klarman, Kenan, Dow, Elbridge Stuart). Of the 29, 27 rest on a grant
label and 2 on names alone; 17 also contain a stated total equal to the
declared amount. Band A is a census, so its figures are exact.

Rolls up to:

- **Recovered — $1,841M (30.4%)**
- **Ours to fix — $1,802M (29.7%)** — the list is in the document; $1,552M
  of it is labelled and within 10% of the target
- **Unreachable — $2,372M (39.1%)** — $1,181M no PDF served, $1,191M no
  grants section in the PDF

Projected across the 9,518 addressable filings: **$6.7B** on the reconciled
rate alone.

---

## Part 7: the audit, and what the first pass got wrong

The first pass reported 36 reconciled filings and 46.0%. Its own open
question was that "the gate has never rejected a wrong answer on its own."
Checking the 36 by hand answered it.

**Six were not grant lists at all** ($74M): Manton and Thome reconciled on
capital-gains schedules (3,205 "grants" named `14,148`), Foellinger, Doss and
Longwell on Part I revenue lines plus a land schedule, Waldheim on legal fees
and cost-basis adjustments. All six came from the subset search running over
unlabelled tables: with twenty money tables and a 0.5% window, some subset
hits.

**Five picked the right list and the wrong rows** ($1.1B). Klarman's 235
rows included nine program subtotals worth $65M. Wells Fargo 2020's 409 rows
were the last 13 pages of a 203-page attachment plus a blank-name row
carrying the $277.9M total of the pages before them. Both Schusterman years
padded the list with the taxes and other-expenses schedules; MG Johnson did
the same. The dollars "reconciled" by construction, because the gate credits
the declared amount; the rows — the product — were wrong.

**One "backlog" filing was ceiling.** Schusterman 2023's 43-page image holds
the expenditure-responsibility statement and nothing else; `diagnose()` called
it list_present because the ER tables name organisations. $363.5M moved from
backlog to unreachable.

The rebuild was measured the same way every time: the old and new selector
run over all 100 filings with the outcome of each filing printed side by
side, and every change inspected. Two intermediate versions overcorrected
(to 27%) by letting a stale heading veto the attachment that followed it;
per-page context fixed that.

**What the remaining backlog is.** Five band-A filings are labelled lists
within a few percent of the target: Wells Fargo 2020 (list is complete in
the image — it ends with the $21.3M Community Care section total that closes
the $299.3M line 3a — OCR returns 101.5% of it over 6,478 rows), Wells Fargo
2021 (97.6%), Schusterman 2021 (95.7%), Schusterman 2022, Bezos 2023 (98.7%,
one row lost to a date in the name column). J&J 2021 is 31,830 matching
gifts over 832 pages at 90.3%. These fail the 0.5% gate for OCR reasons and
no selector change reaches them. Two options, both cheap to test: a second
OCR pass on the failing pages, or accepting a labelled list at 90%+ with its
coverage recorded as a column. Hall 2023 is different: its line 3a is the
grant list plus matching gifts plus scholarships, each a separate attached
statement, so the XML target is not the list's total.

---

## Part 8: when the IRS PDF is missing

Fifteen filings ($1,181M) have no usable IRS image, and 30 more have an
image without the grants section. What else could hold the list:

- **The XML.** All 100 sample XMLs were checked for binary-attachment
  markers, `AdditionalData`, and long narrative nodes. Nothing: four carry a
  `GeneralExplanationAttachment`, none over 2,000 characters. The XML gives
  no signal that an attachment exists, let alone its contents.
- **ProPublica Nonprofit Explorer.** Its API lists a PDF for **2 of the 15**
  (Charles Hayden FY2020, Roy A. Hunt FY2020), both from the IRS's older
  bulk-image program (`download990pdf_11_2021_…` paths). Everything newer is
  a mirror of TEOS under an `IRS/` prefix, so ProPublica cannot have what TEOS
  lacks. Download is bot-gated (403 to curl).
- **The IRS 404s have a pattern.** All six indexed-but-unserved images were
  generated between 2022-05 and 2022-11; no image generated 2023 or later
  404s. Worth reporting to the IRS as a batch, since it costs $872M here.
- **State charity regulators.** Six of the 15 are New York filers (Siegel's 2020 filing — its 2021–2024 images all carry the list,
  Charina, Hayden, Elmezzi, Basch, plus Reynolds and Edelman among the
  no-section cases), and the NY Charities Bureau registry serves the full
  CHAR500 package including the 990-PF. Its search is behind a CAPTCHA, so
  it is a manual step, not a pipeline. California's Registry of Charitable
  Trusts is similar; none of the 15 is a California filer.
- **Foundation websites.** Windgate (two band-A filings, $579M) publishes a
  recipients page with names and locations but no amounts or years;
  Schusterman publishes nothing itemised. Not a source for amounts.
- **Candid's 990 Finder** was not tested; its images come from the same IRS
  programs.

Net: for missing PDFs the practical routes are ProPublica's older images
(fiscal-year 2020 filers only) and state registries by hand for the largest
filers. Neither scales; both are worth doing for band A.

---

## Part 9: open questions

- **Heuristics or a model?** The audit is the argument for a model behind
  the gate: every false positive was a *plausibility* failure (a
  capital-gains schedule is obviously not a grant list to a reader) that
  took a hand-written guard to catch. The reconciliation gate plus a label
  check is what makes an LLM safe to use on the residual — same gate, same
  evidence requirement, applied to its answer.
- **The 90–110% bucket needs a policy.** $1.55B sits there. Accepting it
  with coverage recorded is the pragmatic answer; a second OCR pass is the
  clean one.
- **Band D is 12 filings covering 0.2% of its stratum's dollars.** Do not
  project from it.
- **Non-cash is 97% of Bezos' giving and it is AMZN stock** (`963 Shares
  AMZN`). The combined datamart hardcodes `non_cash_amount = 0` for all three
  990-PF sources. For this class of foundation the structured extract reports
  zero for nearly everything they gave — independent of the placeholder
  problem, and worth raising with GT on its own.

## For the GT Data team

1. **The placeholder conclusion needs revisiting.** The lists are in the
   images you already point at. Your sample was randomized across
   $31K–$2.87B; ours was drawn from the top of the dollar distribution, where
   the money is. This is arguably worth doing once upstream rather than by
   every consumer separately.
2. **Ship a version-ordering key** — an amended indicator or an ingestion
   timestamp. "Most recent by ingestion date" is not executable with the 40
   columns as shipped.
3. **665 vs 804.** Which cut produced the one-pager numbers?
4. **Category B is still open** — the 990-PF verbatim block doubling is a
   separate mechanism from amendments and is unmentioned in the packet.
5. **`non_cash_amount` is hardcoded to 0** across the PF sources.

---

## Artifacts

| path | what |
|---|---|
| `givingtuesday_datamart/irs_source.py` | object id → IRS PDF + XML; RETURN_ID pinning |
| `givingtuesday_datamart/attachment_grants.py` | table selection, evidence gating, paid/future reconciliation |
| `givingtuesday_datamart/exploratory/placeholder_recovery.py` | `sample` / `stage` / `report` |
| `tests/test_attachment_grants.py` | 21 tests: two real OCR results plus one synthetic case per guard the audit added |
| `data/exploratory/placeholder_sample_100.csv` | the frame, with paid and future targets (regenerates, seed 20260921) |
| `data/exploratory/placeholder_staging.csv` | which filings reached OCR and why the others did not |
| `s3://zein-990pf-unstructured-source/` | `source_files/` PDFs, `output_files/` parses (the only copy) |

```bash
python -m givingtuesday_datamart.exploratory.placeholder_recovery sample
python -m givingtuesday_datamart.exploratory.placeholder_recovery stage
#   ... run the Unstructured job: source_files/ -> output_files/ ...
aws s3 sync s3://zein-990pf-unstructured-source/output_files/ <dir>/
python -m givingtuesday_datamart.exploratory.placeholder_recovery report --results <dir> --out detail.csv
```

## Caveats

- **n = 100, and bands C and D are thin.** Band A is a census and exact;
  everything else is extrapolated from small samples.
- **Recovery credits the declared amount, not the OCR sum**, for targets
  that reconcile. Within-tolerance error (≤0.5%) is absorbed, not tracked.
- **The audit was by inspection, not by ground truth.** Each of the 29
  accepted lists was checked for label, contiguity and plausible names, and
  three pages were read from the images by eye (S&G 2021, Hall, Wells Fargo
  2020). Nobody has counted the rows in a PDF against the rows recovered.
- **Recovered grants have not been through the matcher.** Recipient names
  and addresses are recovered; whether they *match* to EINs at the usual
  rate is untested and will be lower than for clean data.
- **The 15 unavailable PDFs were checked once.** IRS availability may vary.
