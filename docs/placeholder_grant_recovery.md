# Recovering the Grants Behind "SEE ATTACHMENT"

*Vibrant Data Labs — September 21, 2026. Work done against GivingTuesday's
Combined Grants Datamart delivery of September 15, 2026, and the IRS's own
PDF images of the same filings.*

---

## TL;DR

Thousands of 990-PF filings itemise nothing. Part XV carries a single row
reading "SEE Attachment 22" whose amount is the filer's entire grant total.
GT investigated and concluded the lists "were never submitted in
machine-readable form." For the **XML** that is true.

It is not true of the **PDF**. The IRS renders an image of the full
submission, attachments included, and the grant lists are in it.

We built the recovery path end to end and measured it on a stratified sample
of 100 filings. **46.0% of sampled placeholder dollars came back — 8,809
itemised grants with names, addresses and amounts.** A further 21.6% is
behind extraction work we own. The remaining 30.5% is unreachable by anyone:
the IRS either serves no PDF, or serves one with no grants section in it.

The method rests on one property: **the placeholder row states the answer.**
Every extraction is checked against the filer's own declared total, so a
filing either reconciles or is flagged. Nothing unverified enters the data.

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
21417445 / 22309568 separates original from amendment. Coverage is uneven —
100% for 2022 and 2024, ~97% 2023, ~56% 2025.

### The PDFs are images

Every page is a CCITT fax-encoded bitonal scan. No fonts, no text layer,
zero extractable characters. Resolution is 264–328 DPI, which is good for
OCR. **So this is an OCR problem, not a parsing problem.**

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
$1.76B. The sample is therefore stratified, with band A a **census**.

---

## Part 5: how selection works

Full implementation:
[`attachment_grants.py`](../givingtuesday_datamart/attachment_grants.py).

Nothing labels the grant list reliably. The XML says "SEE Attachment 22", the
PDF says "STATEMENT 22"; the next filer says "SEE ATTACHMENT C" over
"CHARITABLE LISTING"; a third says "SEE ATTACHED" and names nothing. Wells
Fargo uses four phrasings in four years.

So selection ignores labels as a *key*. **The placeholder amount states what
the answer sums to**, which turns selection into a search: find the set of
tables that reconciles to the declared total. Selection and validation are
the same mechanism.

Tables are grouped by the heading they sit under — a schedule is routinely
split over dozens of pages beneath one heading — and the search runs over
subsets of statements. Contiguous page runs cannot express *"these two
sections but not the one between them"*, which is exactly the shape of Cohen
(paid vs approved-for-future-payment) and Bezos (cash vs non-cash).

### Guards, each from a filing that broke it

| guard | what it stops |
|---|---|
| placeholder rows excluded, **no leading `\b`** | OCR concatenates cells, so the Part XV line reads `…during the yearSEE ATTACHED SCHEDULE1759 R ST…`; `\bsee\b` never matches inside `yearSEE` |
| rows equal to the declared total are totals | Wyss's schedule ends `['', '', '', 125722675]` — unlabeled, so no text pattern reaches it, and the schedule summed to exactly 2× declared |
| heading gate | Wyss's Legal Fees + Other Assets + Other Decreases + Other Expenses sum to within **0.16%** of its $125.7M grant total; the real schedule sat six pages later |
| `x(?:iv\|v)` not `x[iv]+` | the greedy form also matches Parts XI, XII, XIII — cost seven reconciliations |
| amount column name as evidence | `Grant Amount`, `Payment Amount` survive a heading that OCR'd badly |
| majority of amounts under $100 → form lines | J&J's Part I reads `Interest on savings` = 3, `Dividends` = 4; the search assembled **62,684** of them |
| median name length > 110 → prose | Schusterman's Part VIII-A narrates activities with dollar figures beside them |
| amount column by **header**, never position | non-cash sections carry book value beside fair market value — $155M against $340M given |

**Two guards were tried and reverted** for costing more than they saved: a
50% recipient-name share, and a form-label test that also rejects `1199SEIU`,
`4-H` and `100 Black Men of America`. Together they removed $430M of false
positives and $845M of real recovery.

---

## Part 6: results

100 filings, 4,746 pages, 27 minutes of OCR.

| outcome | A | B | C | D | **TOTAL** |
|---|---|---|---|---|---|
| **recovered** | 12 / 2,232.6 | 17 / 532.5 | 6 / 22.8 | 1 / 0.0 | **36 / $2,788.0M** |
| PDF ok, section missed | 5 / 960.5 | 13 / 329.8 | 6 / 18.6 | 1 / 0.3 | **25 / $1,309.2M** |
| PDF ok, section partial | — | 3 / 121.4 | — | — | **3 / $121.4M** |
| PDF ok, no grants section | 2 / 534.1 | 5 / 109.7 | 6 / 19.1 | 8 / 1.5 | **21 / $664.5M** |
| IRS lists a PDF, serves 404 | 2 / 692.5 | 4 / 180.0 | — | — | **6 / $872.5M** |
| IRS has no PDF at all | 1 / 251.5 | 2 / 40.7 | 4 / 15.8 | 2 / 0.8 | **9 / $308.9M** |
| **ALL** | 22 / 4,671.2 | 44 / 1,314.2 | 22 / 76.4 | 12 / 2.6 | **100 / $6,064.4M** |

*(cells are `filings / $M declared`)*

**8,809 grants recovered, 46.0% of sampled dollars.** Band A is a census of
every addressable filing over $100M, so its 55% is exact, not estimated.

Rolls up to:

- **Recovered — $2,788M (46.0%)**
- **Ours to fix — $1,431M (23.6%)** — the list is in the document
- **Unreachable — $1,846M (30.4%)** — $1,181M no PDF served, $665M no grants
  section in the PDF

The practical ceiling is **69.6%**.

---

## Part 7: what is a ceiling and what is a backlog

The distinction matters more than the headline, and the report now emits it
(`diagnose()`), because an absent attachment and a selector bug look
identical without it.

**Ceiling.** Bezos 2021 carries `ATTACHMENT A` (charitable activities) and
`ATTACHMENT B` (expenditure responsibility) and simply omits Attachment C.
Its 2024 filing has it. No extraction method recovers what isn't there.

**IRS-side loss is the larger half of the ceiling** — $1,181M vs $665M — and
it splits two ways. Nine filings where TEOS lists no image at all, and six
where TEOS lists a `STATICFILEPATH` that the IRS then 404s. Schusterman's
2020 990-PF is indexed at `731312965_202012_990PF_2022102620583278.pdf` and
is not served. **The second kind looks like a bug on the IRS's side and is
worth reporting**; it costs $872.5M in this sample alone, including two
band-A filings. All 15 are tax years 2020–2023, 11 of them 2020.

**Backlog.** 25 filings, $1,309M, where the list is in the parse and the
selector missed it. Band A's share is 5 filings worth $960.5M — fixing those
five is worth more than bands B, C and D contain in total.

---

## Part 8: open questions

- **Heuristics or a model?** Three passes in, every fix has been general
  rather than per-filer. But two "fixes" shipped in this session made things
  worse before being caught, so the tail is not obviously getting easier. The
  reconciliation gate is provider-agnostic and is what makes an LLM *safe* to
  use — the sound design is deterministic first, model behind the gate for
  the residual, same gate applied to its answer.
- **The gate has never rejected a wrong answer on its own.** Every false
  positive here was found by going looking. Wyss "reconciled" at 0.16% on
  expense schedules and the gate was satisfied. This is the weakness to
  harden before running unattended on 9,518 filings.
- **Band D is 12 filings covering 0.2% of its stratum's dollars.** Its 8%
  rate has very wide error bars; do not project from it.
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
| `givingtuesday_datamart/attachment_grants.py` | table selection and the reconciliation gate |
| `givingtuesday_datamart/exploratory/placeholder_recovery.py` | `sample` / `stage` / `report` |
| `tests/test_attachment_grants.py` | 16 tests over two real OCR results |
| `data/exploratory/placeholder_sample_100.csv` | the frame (regenerates, seed 20260921) |
| `s3://zein-990pf-unstructured-source/` | `source_files/` PDFs, `output_files/` parses |

```bash
python -m givingtuesday_datamart.exploratory.placeholder_recovery sample
python -m givingtuesday_datamart.exploratory.placeholder_recovery stage
#   ... run the Unstructured job: source_files/ -> output_files/ ...
python -m givingtuesday_datamart.exploratory.placeholder_recovery report --results <dir>
```

## Caveats

- **n = 100, and bands C and D are thin.** Band A is a census and exact;
  everything else is extrapolated from small samples.
- **Recovery credits the declared amount, not the OCR sum**, for filings that
  reconcile. Within-tolerance error (≤0.5%) is absorbed, not tracked.
- **Recovered grants have not been through the matcher.** Recipient names and
  addresses are recovered; whether they *match* to EINs at the usual rate is
  untested and will be lower than for clean data.
- **OCR quality was measured on two filings by hand.** Siegel reconciles to
  $1 over 110 grants and Bezos to $602 over 279; the other 34 are trusted on
  the gate alone.
- **The 15 unavailable PDFs were checked once.** IRS availability may vary.
