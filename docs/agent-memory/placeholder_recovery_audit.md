---
name: placeholder-recovery-audit
description: "2026-09-21 placeholder-grant recovery: audit of the OCR pass (46% was inflated), evidence-gated selector, attachment cut, XML ER rows, and the vision-model runs (Qwen3-VL 44.0% / Gemini Flash Lite 44.3% / two-model union 49.1%) with their failure shapes"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5c63bb18-9e65-421e-9da1-204481fd8b45
  modified: 2026-09-22T23:57:33.806Z
---

Audit (2026-09-21) of `docs/placeholder_grant_recovery.md` / `attachment_grants.py`
on branch `placeholder-recovery` (OCR results live only in
`s3://zein-990pf-unstructured-source/output_files/`, ~49 MB; PDFs cached at
`~/.cache/irs_index/pdfs/`).

**What was wrong with the first version (46.0% "recovered")**
- Subset search over *unlabelled* tables reconciled capital-gains schedules
  and Part I form lines (Manton, Thome, Foellinger, Doss, Longwell,
  Waldheim: $74M pure garbage) and padded real lists with subtotals /
  expense rows / a blank-name $278M total (Klarman, both Schusterman years,
  Wells Fargo 2020, MG Johnson: ~$1.1B wrong row sets).
- `\btotals?\b` on the joined row missed "Subtotal …" and dropped real grants
  whose purpose text says "total".
- The frame summed Part XV 3a (paid) + 3b (future) into one target — 22 of
  100 sampled filings, 9 of 22 in band A. Now `placeholder_paid` /
  `placeholder_future` columns; `extract(elements, paid, future)` → `Recovery`.
- `diagnose()` called Schusterman 2023 ($363M) "list_present"; the PDF has
  only the ER statement. It is ceiling, not backlog.
- RETURN_ID coverage claim was wrong: by index year 2021 100%, 2022 95%,
  2023 97%, 2024 62% (990-PF 92%), 2025 55%, 2026 52%.

**Rebuilt selector (evidence-gated)**: a run needs reconciliation PLUS either
a grant label (heading, footer like "Attachment to Part XV, Line 3a", or
column header: grantee/payee/payment amount/purpose of grant/name and
address) or, for unlabelled runs, contiguity + ≥50% recipient-like names and
no vetoed table inside. Context is per-page; only a grant heading carries to
heading-less pages (the form's "…Approved for Future Payment" title leaked
otherwise). Result: 29/100 reconciled, $1,841M = **30.4%**, plus **$1,497M
(24.7% of declared, 14 filings) present at 90–110% coverage** = OCR row loss on long lists (Wells
Fargo 2020/2021, Schusterman 2021, Bezos 2023, J&J), not selection.

**Alternative sources when the IRS PDF is missing** (15 filings, $1.18B):
- XML: zero binary-attachment markers in all 100 XMLs — no presence signal.
- ProPublica API (`/nonprofits/api/v2/organizations/<ein>.json`): 2 of 15,
  both pre-2022 image-program files; newer entries just mirror TEOS.
- All six IRS 404s are images generated 2022-05..2022-11; nothing 2023+ 404s.
- NY Charities Bureau registry (6 of 15 are NY filers) is CAPTCHA-gated →
  manual only. Windgate/Schusterman sites: names, no amounts.

**Classifier (do we need the PDF?) assessed on the DB, 2020+**: PLACEHOLDER
regex precision is fine (94% of flagged filings have flagged $ within 10% of
declared); recall misses bare references ("SCHEDULE ATTACHED", "STATEMENT
25", "ATCH 4" = Sanofi $6.2B) and "SEE <words> ATTACHMENT" → `is_pointer()`
(v2, withheld excluded) adds ~1,750 filings / $11.3B. Filing rule: fetch
when pointer rows ≥ 50% of declared → 12,977 filings / $64.6B (4,565 PAP).
Withheld ("upon request") 788 / $5.9B and "VARIOUS" 3,088 / $35.5B (mostly
PAP) are separate classes. Single-row-named (105K / $78B) is pass-through,
not placeholder. SQL: data/exploratory/placeholder_classifier_assessment.sql.

**Cut PDFs before transcribing** (wired into `stage` 2026-09-21): TEOS
images = IRS-rendered XML pages followed by filer attachments. The IRS
renders at five image widths {2246, 2259, 2440, 3062, 3081} (the wide ones
are supporting statements like the ER statement — NOT attachments; an
earlier "53%" figure counted them as filer pages); `irs_source.attachment_start`
reads `pdfimages -list`. On the 85 cached PDFs 62% of pages are IRS-rendered
(50% A, 60% B, 93% C, 99% D) and 26 filings have zero attachment pages.
Cohen 2020 / Anschutz 2022 OCR reconciliations had used the IRS-rendered
ER statement to fill a page OCR lost — so the XML-itemised rows
(`990PF_EXPENDITURE_RESP` + named 3a/3b rows; `xml_tables`) now feed the
selector as exact tables; Hall 2023 needed them under Qwen.

**VLM bake-off (2026-09-21)**: Unstructured is not needed for attachment
pages. 14 known pages × 19 models via Vercel AI Gateway
(`VERCEL_AI_GATEWAY_API_KEY`, OpenAI-compatible at ai-gateway.vercel.sh/v1).
Qwen3-VL instruct at 200 DPI: Siegel exact to the dollar, WF p233 20/20 to
the cent, 89% amounts matched, $0.0018/page (Unstructured $0.015). Gemini
3.5 Flash Lite similar at $0.0037; Gemini 3.8 Flash best at $0.014. 130 DPI
causes digit misreads; reasoning models (gpt-5-mini) refuse stochastically;
gpt-4.1/Haiku 4.5 invent amounts. Non-cash FMV-vs-book pages are the hard
case; models transcribe printed totals → page-level gate. Harness:
`givingtuesday_datamart/exploratory/vlm_bakeoff/`.

**Sample through both models (2026-09-21)**: `placeholder_recovery.py
transcribe` (per-page JSON under `~/.cache/irs_index/vlm/<model>/<oid>/pNNN.json`,
resumable, repairs malformed amounts and salvages truncated responses as
`_partial`). 1,782 attachment pages: Qwen3-VL instruct 35/100 reconciled =
44.0%, $3.57, 33 s/page median, scale workers to 40 (41 pages/min); Gemini
3.5 Flash Lite 38 = 44.3%, $12.37 ($0.0069/page — denser rows than the
bake-off), 8 s/page. Two-model union 42 = 49.1% (48 = 52.4% adding the 6 only OCR got); they fail on different filings, so
next = page-level agreement between the two + Gemini 3.8 Flash on
disagreements. Measured: identical amounts on 63% of pages outside J&J (11% on J&J's 836 dense pages); a perfect per-page choice between the two readings fixes only 3 of the 10 near-misses (Kenan, First Horizon, Eden Hall 2023) — the rest need a third reader or prompt fixes. Retries (3 per page, already in transcribe) fix same-model randomness (Qwen: 485 of 1,782 pages retried, mostly empty first answers), not systematic misreads. Known VLM failure shapes: continuation pages without a
heading get the wrong kind (Kenan); contact names emitted as rows (Claude
Moore, Gemini); duplicated rows on dense pages; Qwen stops mid-row on J&J's
836 matching-gift pages (55 partial). Reports:
`data/exploratory/placeholder_report_{unstructured,qwen3_vl,gemini_lite}.csv`,
`placeholder_engine_differences.csv`.

**Expanded frame (2026-09-22)**: `sample --expand` → `placeholder_sample_expanded.csv`, 610 filings (100 base + B census 438 + C 100 + D 50), $16.4B; staged in `placeholder_staging_expanded.csv`: 373 have an attachment (9,347 pages, 66% of $), 144 PDF-without-attachment (18%), 93 no PDF (16%). All 38 404s are 2022-generated images (`placeholder_404_images_expanded.csv`); 40% of TY2020 filings unreachable vs 5-7% for 2022-23. Broadened classifier ≈ no change on GT's CSV (additions are patient assistance). NOT yet read: read once, after prompt v3 + 3b, ~$110 for both models. `irs_source.lookup` now searches every index year (Caterpillar/Principal 2021 ids sit in index_2024).

**Prompt v3 + JSON-mode finding (2026-09-22, commit 8281f60)**: Qwen's 401
empty list pages (384 of J&J's 836, 7 of First Horizon's 30) were caused by
`response_format=json_object` — same page returns 0 rows in 5 s with it and
59–87 rows without it (5/5 tried); prompt wording didn't matter. `transcribe`
now retries an empty/partial list page without JSON mode and keeps the fullest
answer. Selector: `page_tables` carries the previous page's heading+kind onto a
heading-less page the model labels a list, unless the previous page closed
with a target-matching total (Kenan p127 total → p128 keeps future; p129
inherits future). Selector alone on stored v2 pages: Qwen 35→36, Gemini 38→40
(Wells Fargo 2021 $207M, Eden Hall 2022, Elbridge Stuart future). Measured: NO
page in the sample holds two lists → "one section per list" dropped. Prompt v3
= heading is the list title (not masthead / recipient line), contact person
stays in the org's address, one row per recipient. Results are versioned:
`~/.cache/irs_index/vlm/<model>-v3/`; v2 folders untouched. 45 tests incl.
new `tests/test_vlm_transcription.py` (fake client).

**Row misalignment = the real dense-page failure (2026-09-22)**: on pages
with 25+ rows, readers disagree on (name, amount) PAIRS 40-60% of the time
(<25 rows: 91-99% agree). Shapes seen with ground truth (page images read by
hand): (1) a MERGE of two adjacent recipients shifts every amount below by one
row while the page sum barely moves (Wyss 2023 p033 under Gemini v3 — caused
by v3's "wrapped name is one row" wording); (2) a SPLIT (address line as its
own row) shifts the other way (Gemini v2 on Roberts 2022 p034, WF 2020 p026);
(3) cents folded into dollars ($838,000.00 → 8,383,000; Gemini v3 on WF
p026). Filing-level reconciliation at 0.5% CANNOT see a shift ($1.5M slack on
WF's $299M). Hence: 3b page agreement must compare name+amount pairs, not
amount lists; ground truth (step 3) must score pairs. Both GT pages so far
favour Qwen (exact on Wyss p033 and WF p026). Prompt v4 (commit after 8281f60)
anchors rows on the amount column; fixed Wyss p033 for both models on a 6-page
test ($0.06); NOT re-run on the 100 (would be another $16) — it is the prompt
for the expanded read, where the 100 base filings get re-read anyway.
`JSON_MODE = {qwen: False}`: under v3, 83% of Qwen pages needed the no-JSON
fallback. Gemini v3 on the 100: 40 reconciled / 48.9% (v2 pages + new
selector: 40 / 48.2%) — a swap: +Bezos 2023 ($157M, non-cash page now right),
Hall 2023, Manton 2021, Kenan future; −Wyss 2023 (merge shift), Roberts 2022,
Edelman 2023 (dense-page row loss). Qwen v3 on the 100: 37 / 46.4% / $4.24
(1,314 of 1,782 pages answered without JSON mode; 65,793 rows vs 51,894 under
v2; J&J coverage 0.88→0.93, First Horizon 0.61→1.02) — +Wyss, Aviv, Pacific
Life, Elbridge future; −Hall 2023 (now 1.26 over: paid = list + matching gifts
+ scholarships), Kenan future, Raskob paid. Unions: v3 pair 42 / 51.8%; all
four vision reads 45 / 52.9% ($3,205M). Same-model reproducibility (v2 vs v3
pages): identical (name, amount) pairs on 89–97% of pages under 25 rows, 31–38%
at 25+; cross-model v3: 80–83% / 15–25%. EVERY READ IS A DIFFERENT DRAW ON
DENSE PAGES — a single read lands at 36–40 filings, the union of draws climbs
with each read; report single-read numbers as the method's rate. Committed
reports: `placeholder_report_{qwen3_vl,gemini_lite}.csv` = v2 pages + current
selector, `..._v3.csv` = v3 pages; `placeholder_engine_differences.csv` = all
five engines. Step 2 total spend ≈ $17 (Gemini $12.21, Qwen $4.24, tests $0.07).

**Ground truth (step 3, 2026-09-22)**: `placeholder_ground_truth.py` (pick /
seed / fix / accept / add / show / crop --rotate auto / score);
`placeholder_gt_pages.csv` = 91-page sample (6 per density×agreement cell +
every page of 7 near-miss filings), `placeholder_ground_truth.csv` = 83 pages
checked against the image (48 transcribed by hand, 35 seeded from the agreed
reading and verified — 0 corrections needed on seeded pages). Pages carry
kind=paid/future/er/other in the note. RESULTS: (1) agreement is a safe
accept — Qwen v3 == Gemini v3 on pairs → exactly right 37/38 pages (the miss
is both returning 0 rows on an ER page, so "agree on nothing" must not count);
all four agree 33/33. (2) Reader pair precision/recall on 83 pages: Gemini v3
90.4/89.9, Gemini v2 87.7/88.3, Qwen v3 81.7/68.0, Qwen v2 80.3/63.7; under 25
rows every reader ≥ ~90%; Gemini ~85% at 25+, Qwen 50-70%. J&J's 75-row
single-column pages: Gemini 99.9% exact-ish, so density alone is not the
problem — wrapped names / multi-column layouts are (Wells Fargo 2021 at 61
tiny rows: all four readers shift). (3) On the 45 disagree pages: Gemini v3
exact 16, Qwen v3 exact 3, neither 26 (15 of the 22 in 25-49, 8 of 9 in 50+);
oracle best-of-two recall only 85% → a third reader is required, a 2-way vote
is not enough. (4) All 7 near-miss filings' true paid rows sum EXACTLY to the
declared paid (ratio 1.0000) — the lists are complete on the page; near
misses are pure transcription error. Bezos 2022/2023 non-cash pages: truth =
FMV column, and cash+FMV = declared to the dollar. Zein's process rule (given
mid-task): verify-not-transcribe — seed from the printed-total or majority
reading, check only disputed rows; 3 pages per differ cell, not 6.
(5) v4 test on the 83 pages ($1.50, folders `<model>-v4` and `-v4b` = same
prompt read twice): Gemini v4 89.0/88.7, v4-again 90.3/90.2 vs v3 90.4/89.9 →
prompt is inside run-to-run noise for Gemini (v3→v4 moved 26 pages by mean
2.5 rows; v4→v4b moved 23 by 2.0); Qwen v4 84.8/72.8 and 82.3/71.5 vs v3
81.7/68.0 → small real gain. v4 stays; the $16 full-sample v4 re-read was NOT
run (it would be another lottery draw, the ground truth already answers the
question). KEY: cross-model agreement exact 107/107 across three pairings;
SAME MODEL TWICE agrees on its own errors — Gemini 51/57, Qwen 37/51; a
2-Gemini+1-Qwen vote 53/59. Rule: accept only on cross-model agreement;
third reader must be a different model; test Gemini 3.8 Flash's
independence from Flash Lite on this set before trusting it.
(6) Third-reader test on the 83 pages (~$10; `tiebreak` / `policies`
commands): Sonnet 5 (thinking OFF via `reasoning_effort: none` in
REQUEST_EXTRAS — with thinking on, 3 dense pages returned empty at 8K/16K/32K
out tokens) 97.5/96.9, 67/83 exact, $0.053/page; Gemini 3.8 Flash 94.2/94.0,
59/83, $0.022; GPT 5.6 Terra 82.3/80.9 (needs `reasoning_effort: none`, rejects
"minimal"); Luna 79.1/77.6. Neither Sonnet nor 3.8 Flash EVER repeated Flash
Lite's error (0/29) — the family worry was empirical noise; OpenAI models are
weakest at any size. DECIDED 3b: Qwen v4 + Flash Lite v4 base; dispute → 3.8
Flash (match either base); still open → Sonnet 5 (match any); else flag. On 83
pages: 58 right / 2 wrong / 23 flagged; the 2 wrong = future-payment 3-column
pages (approved vs balance), 0 wrong on paid pages. Cost ≈ $360 on the
expanded frame at the measured dispute rate (67% all pages, 87% J&J, 49%
elsewhere → 52% blended). OPEN (Zein's call): flagged pages (all four readers
differ) — Sonnet-off alone right 11/23 (NOT 16: that was the match-one
table's 16/30); load with low-confidence tag or drop. Sonnet at
`reasoning_effort: max` + 64K max_tokens on those pages: NO gain (exact 11/21
both ways, 898 vs 897 rows, Kenan p127 16/48 both times after 31K reasoning
tokens; $0.12/page, 2-4x slower, 2 densest non-cash pages never finished in
15 min). Via the gateway, Sonnet thinking is controlled by `reasoning_effort`
("none"/"high"/"max"); Anthropic-native `thinking` params are rejected.
Cached in `anthropic__claude-sonnet-5-max-v4/` (21 pages). Engineering plan
discussed (not built): port vdl-tools' bulk get-or-run pattern into this repo
(no vdl-tools dep) as `page_readings` table keyed (object_id, page, image
sha256+DPI, model, prompt version, request hash) + `read_pages()` thread-pool
bulk cache-or-run with chunked upserts + `agree()` verdict table stamped with
policy version; ~200 lines. SPEC WRITTEN: `docs/placeholder_storage_spec.md`
(Part A filing_images + S3 `givingtuesday-datamart/irs/pdf/`; Part B
page_readings keyed on PDF sha256 not PNG, read_pages, page_verdicts +
POLICY_V1; four session briefs; decisions table so new sessions don't
relitigate). Zein's intent: build each part in a fresh session to save tokens.
Zein's decisions 2026-09-22 (all in the spec's decisions table): flagged pages
= LOAD Sonnet's single reading marked `flagged` (POLICY_V1 "load_single");
bucket `givingtuesday-datamart/irs/pdf/` confirmed; PDFs kept indefinitely;
tests = in-memory store + fake client, no Postgres in unit tests (matches the
client tests' never-connected sqlite:// engine). No open questions remain. Candidate reads live in
`~/.cache/irs_index/vlm/<model>-v4/` for the 83 GT pages only.

**How to apply:** never accept a reconciliation without a second evidence
class; report coverage for unreconciled lists; keep paid and future targets
separate; spot-check Klarman (04-3105768), Wells Fargo 2020 and Hall 2023
(paid = list + matching gifts + scholarships, so the XML target ≠ list total).
Related: [[combined-grants-datamart]], [[feedback-spot-check-known-eins]].
