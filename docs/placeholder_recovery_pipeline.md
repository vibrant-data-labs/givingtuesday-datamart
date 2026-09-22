# Placeholder Grant Recovery — the pipeline, end to end

*Sketch, September 21, 2026. Builds on
[placeholder_grant_recovery.md](placeholder_grant_recovery.md), which holds
the evidence for every choice below. This is the shape of the thing to
build; nothing here is wired up yet beyond stages 1, 3 and 4.*

## The shape

```mermaid
flowchart LR
    DB[(datamart<br/>privategrants_current<br/>basic_fields_pf)]
    C0[0 · classify<br/>pointer rows ≥ 50% of declared]
    C1[1 · resolve<br/>object id → TEOS image + XML]
    C2[2 · cut<br/>keep attachment pages only]
    S3a[(s3 source_files)]
    C3[3 · OCR<br/>Unstructured job]
    S3b[(s3 output_files)]
    C4[4 · select<br/>evidence-gated reconciliation]
    C5[5 · accept + load<br/>privategrants_recovered]
    M[matcher<br/>name + address → EIN]
    R[6 · report + gates]
    DB --> C0 --> C1 --> C2 --> S3a --> C3 --> S3b --> C4 --> C5 --> M
    C4 --> R
    C1 -. no image / 404 .-> Q[queue: ProPublica,<br/>state registry, IRS report]
    C2 -. no attachment pages .-> R
```

Each stage writes one table or one S3 prefix, keyed on the IRS object id,
and never modifies an upstream one. Anything can be rerun from its input.

## Stages

### 0 · Classify — which filings need their PDF

**Input:** `privategrants_current` (2020+) joined to the declared total in
`basic_fields_pf` (Part I line 25, `arecgpdcprps`).
**Output:** `pf_placeholder_filings` — one row per filer-year: object id,
class, paid target, pointer text, classifier version.

The rule, measured in
[placeholder_classifier_assessment.sql](../data/exploratory/placeholder_classifier_assessment.sql):
a filing is **fetch** when pointer-named rows (`is_pointer()`, the broadened
pattern) hold at least half its declared total. Everything else gets a
class that says why not — *mixed*, *withheld* ("available upon request":
no PDF will help), *various* (no pointer; untested), *pass-through* (one
named recipient carrying the whole year), *itemised*. Patient-assistance
EINs and status-`I` rows are excluded here, by EIN and flag, never by name.

Over 2020–2025 that is ~13,000 fetch filings and $64.6B, of which ~4,600
filings are patient assistance. The organisational remainder is the
population: roughly 8,500 filings, $25–30B.

### 1 · Resolve — the IRS's own copies

**Input:** object ids from stage 0.
**Output:** `pf_recovery_manifest` — image URL, generation date, whether
RETURN_ID pinned it, XML URL, status (`staged`, `no_teos_image`,
`pdf_unavailable:404`), filer state.

[`irs_source.py`](../givingtuesday_datamart/irs_source.py) as it stands.
Two additions: pull the XML too (it is the authoritative source of both
targets, `TotalGrantOrContriPdDurYrAmt` and `TotalGrantOrContriApprvFutAmt`,
and of the filer's state), and route the failures. TEOS lists no image
for ~9% of filings and 404s another ~6%, all of the 404s images generated
in 2022. Those go to a queue, not to OCR: ProPublica's API for the older
image program (it had 2 of the sample's 15), the state registry by hand
for the largest (NY is CAPTCHA-gated), and one batch report to the IRS.

### 2 · Cut — send only the attachments

**Input:** the staged PDF.
**Output:** a subset PDF in `s3://…/source_files/<object_id>.pdf`, and a
page map (subset page → original page) in the manifest.

Every TEOS image is the IRS's rendering of the XML followed by the filer's
attachments. The IRS renders at a fixed 2246 px width, cropped; attachments
are full 2550×3300 pages or landscape sizes. `pdfimages -list` reads the
boundary without rendering and pypdf cuts without re-encoding. This is 53%
of pages on the sample (90% in band D), and it removes the exact pages
every false positive came from. A filing with zero attachment pages never
reaches OCR: it is *absent* by construction.

### 3 · OCR

Unstructured's Pipelines job, `source_files/` → `output_files/`, unchanged.
Volumes after the cut, at the sample's ~37 attachment pages per filing in
bands A–B and 2–6 in C–D:

| band | filings | attachment pages | note |
|---|---|---|---|
| A | 22 | done | census |
| B | 442 | ~16,000 | 44 done |
| C | 2,299 | ~14,000 | |
| D | 6,755 | ~14,000 | |

About 45,000 pages for the whole population, ten times the sample, or
roughly four hours of job time at the sample's rate. Uncut it would have
been ten times that again. Get Unstructured's per-page price before C and D;
after the cut it is no longer the reason to skip them.

### 4 · Select — the reconciliation gate

**Input:** `output_files/<object_id>.pdf.json`, the targets from the XML,
the page map.
**Output:** `pf_recovery_results` — per filing and per target (paid,
future): outcome, extracted sum, error, evidence (labelled / names),
whether a stated total matched, coverage, pages in original numbering,
selector version.

[`attachment_grants.py`](../givingtuesday_datamart/attachment_grants.py)
as rebuilt: a run needs reconciliation *and* a second class of evidence.
Paid and future reconcile separately. Failures are diagnosed — list
present, partial, absent — and *present* carries a coverage number, since
the largest failure class is a labelled list that OCR returned at 90–110%
of the target.

### 5 · Accept and load

**Output:** `privategrants_recovered` — one row per grant: filer, tax year,
object id, recipient name, address cells, amount, purpose, status; and
for lineage the PDF URL, original page number, OCR job id, selector
version, target reconciled against, and error. A view unions it with
`privategrants_current` for the matcher, tagged `match_source =
'ocr_recovery'`.

Two policies to set, in order:

1. **Reconciled** (≤ 0.5%): load. 29 of the 100 sample filings.
2. **Labelled, 90–110% covered**: load with coverage recorded, or re-OCR
   the failing pages first. 14 filings and $1.55B in the sample — a quarter
   of the money — and it is an OCR problem, not a selection problem. This
   decision waits on the ground-truth set (stage 6).

Future-payment lists (line 3b) are commitments, not payments. Capture
them; do not load them into a grants table.

### 6 · Report and gates

- **Ground truth:** 15 filings with rows counted from the PDF by hand.
  This is the only measurement of row-level precision the pipeline will
  have, and it sets the coverage policy.
- **Regression gate:** the old-versus-new harness over the 100-filing
  sample, run on every selector change. Two intermediate versions this
  session overcorrected to 27%; the harness is what caught them.
- **Canaries:** Siegel (reconciles to the dollar), Bezos 2022 (cash plus
  non-cash), Klarman (subtotals), Wells Fargo 2020 (blank-name total, OCR
  row loss), Hall (line 3a is list plus matching gifts plus scholarships).
- **The report** by band, plus the unreachable list with its routing.

## Order of operations

1. Ground-truth set and a matcher pass on the 4,760 rows already
   recovered. Together they say whether the output is product-grade.
2. Wire stage 2 into `stage`; rerun the sample subset to confirm the
   selector is unchanged on cut PDFs.
3. Band B in full with the broadened classifier.
4. Decide the coverage policy; re-OCR band A's failing pages.
5. C and D once the per-page price is in hand.
6. Send GT the findings list; report the 2022 image batch to the IRS.

## Open decisions

- Whether recovered rows join `privategrants_current` or stay in their own
  table behind a view (recommended: the view; it keeps GT's data and ours
  separable and the matcher's input-shape version honest).
- Bumping `MATCHING_INPUT_SHAPE_VERSION` when recovered rows enter the
  matching views.
- The coverage acceptance threshold, after ground truth.
- What to do with "various" filers: twenty PDFs would tell.
- Whether GT will run any of this upstream; the lists are in images they
  already link to.
