---
name: placeholder-recovery-storage-part-a
description: "State of the placeholder-recovery storage layer after Session 1 — Part A merged (PR #43 → placeholder-recovery, 184387e), what exists in the datamart and S3, retry semantics, and where Session 2 starts"
metadata:
  node_type: memory
  type: project
  originSessionId: d5e1ff3f-7b28-4a52-871b-34a151503306
  modified: 2026-09-23T03:05:20.050Z
---

Session 1 (Part A of docs/placeholder_storage_spec.md) was built on
2026-09-22 and merged into `placeholder-recovery` on 2026-09-23 as
184387e (PR #43, branch `zeclaude/filing-images-part-a`). Nothing of the
recovery pipeline is on `main` yet; `main` gets one review once Part B
and the 1,000-filing run exist.

State outside the repo, as of 2026-09-23:
- `gt_datamart.public.filing_images` holds the 610-filing frame: 373 fetched,
  144 no_attachment, 55 no_teos_image, 38 pdf_unavailable:404 (all 38 images
  generated in 2022); every failure had attempts = 3 after the by-hand runs.
- `s3://givingtuesday-datamart/irs/pdf/<object_id>.pdf`: 517 objects, 1.15 GB,
  each with the SHA-256 as its S3 checksum. The spec's "~700 PDFs / ~12 GB"
  was a guess; the cache had exactly the 517 fetched.
- Backfilled fetched rows have NULL index_year / teos_url (needs TEOS); the
  failures got theirs from the live re-tries. A `--refetch` would fill the
  rest and is now safe: a failed re-fetch keeps a fetched row's object.
- Readings: loaded into `page_readings` by Session 2 on 2026-09-22 (PR #44,
  see [[placeholder-recovery-storage-part-b]]); the JSON folders in
  `~/.cache/irs_index/vlm/` are still on disk and the backfill is idempotent.

**Why:** Session 2 (page_readings) keys on `filing_images.sha256` and reads
PDFs through `filing_images.local_pdf`; the table and objects must exist
first, and they do. Session 2 branched from `placeholder-recovery` at
184387e and is PR #44.

**How to apply:** don't re-run the Part A backfill (it is a one-off seed that
skips rows already in the table, so it is harmless but pointless); use
`python -m givingtuesday_datamart.filing_images status` to see the table.
Retry semantics after Zein's 2026-09-22 decision, recorded in the spec's
decisions table: `no_teos_image` is permanent (never retried without
`refetch`, since TEOS's listing is definitive on the day); `pdf_unavailable`
and the other failures retry until attempts = 3 because the 2022 404 batch
may come back. Failure statuses that are ours, not the IRS's, and retry the
same way: `teos_failed:<exc>`, `widths_failed:<exc>`, `upload_failed:<exc>`.
`fetch_filings` needs `pdfimages` on PATH and fails fast without it. The
review of #43 was posted as one GitHub review with inline comments and
answered commit-per-finding; that shape worked, reuse it for Sessions 2–3.
Related: [[worktree-editable-install]].
