---
name: placeholder-older-renderers
description: "The older IRS renderers' page widths measured (2026-09-29), the 5,547 older images re-cut in the shared database, tax years 2015-2019 projected at $598; PR open, paid read still waits on Zein's go"
metadata:
  node_type: memory
  type: project
  originSessionId: 6584aaad-95ce-4947-963e-7748d0548dad
  modified: 2026-09-30T00:53:38.576Z
---

Session of 2026-09-29/30, chip task_8afdedba, branch `zeclaude/pre2021-widths` (PR #54,
rebased onto `placeholder-recovery` once PR #53 merged as 2ff2e97), against
`placeholder-recovery`. Follows [[placeholder-production-read]].

**The finding:** the first page of every TEOS image is the IRS's form page 1
and its width names the renderer. Six renderers: 2246 (June 2021 on, the
five widths known before), and 2800, 2224, 2256, 2432, 2240 (December 2016
to January 2021, multiples of 16). `irs_source.RENDERED_WIDTHS` is a dict
keyed by first-page width; `attachment_start` reads it. 2544 is the filer's
letter page on the older images. One union set would be wrong (3072/3056 are
the IRS's under 2240, a filer's list under 2246); a date would not do (17
older-renderer images generated 2021-01-11/12; the first frame's 516 rows
have no `image_generated`). Paper returns scanned whole (first page
2560/2576/2592, 18 images) stay cut at page 1.

**Measured with:** 968 PDFs from S3 (no IRS request, no model), OCR of each
page's top strip (pdfimages + tesseract, `exploratory/renderer_widths.py`,
CSVs under `data/placeholder_recovery/`), every page speaking against a cut
looked at on contact sheets. `binaryAttachmentCnt` in the public XML is
always 0: dead end.

**Applied to the shared database (2026-09-30T00:40Z):** `filing_images
recut` changed 5,547 rows (all five older renderers), 147,547 pages that
were all the filer's are 19,483 in 2,104 images; 0 rows of the 2246
renderer, 0 of the 553 read filings. A second pass changes nothing.

**Projection 2015-2019 (dry run, nothing bought):** 2,686 filings, 22,827
pages, $597.59 (about $435 by band), about nine hours; plus 2020-on $757 =
two runs, two decisions. NOT bought; the paid read waits on Zein's explicit go.

**Open question for the record:** images generated 2018-2020 hold an
attachment on only 22-35% (2017: 62%, current renderer: 60%), even for
filers who attach on every 2021-on image; 48 checked end on IRS pages. Cause
unknown, cannot be told from stored data.

**How to apply:** after any change to `RENDERED_WIDTHS`, run
`filing_images recut --dry-run` then `recut`; never re-cut a read filing
(the function skips them). The laptop is shared with other sessions' heavy
jobs; tesseract over ~30K pages takes about 20 minutes on 8 processes with
`pdfimages -tiff` extraction (rendering with pdftoppm was 20x slower).
