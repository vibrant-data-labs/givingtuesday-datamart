---
name: irs-primary-sources
description: Where the IRS serves 990 PDFs and XML, and the RETURN_ID trick that pins an amended filing to its exact PDF.
metadata:
  type: reference
---

Implemented as `givingtuesday_datamart/irs_source.py` (simplified in 29b9941).

The short version, which is all that's needed most of the time:

- **PDF** — `apps.irs.gov/teos/details/returnsSearch/<ein>` returns JSON;
  `STATICFILEPATH` appends to `apps.irs.gov`. Plain curl, no auth.
- **XML** — take GT's data lake mirror. Verified byte-identical to the IRS
  original by SHA-256 on filings from 43 KB to 51 MB.
- **`index_<year>.csv`** under `apps.irs.gov/pub/epostcard/990/xml/<year>/`
  maps OBJECT_ID to EIN, TAX_PERIOD, RETURN_TYPE and RETURN_ID.

**The one real trick:** TEOS keys on (EIN, TAX_PERIOD, RETURN_TYPE), which is
coarser than a filing — an original and its amendment share a tax period. The
image filename's trailing token is `YYYYMMDD` + the index's `RETURN_ID`, so
where RETURN_ID is populated an OBJECT_ID pins exactly one PDF. Coverage:
100% 2022/2024, ~97% 2023, ~56% 2025, ~81% 2026.

Things that cost time to learn and are easy to re-hit:

- The `irs-form-990` S3 bucket is **retired** — still resolves, and is empty.
  Any doc citing `s3.amazonaws.com/irs-form-990/<id>_public.xml` is stale.
- Fetching XML from the IRS directly means opening a 250MB+ batch ZIP, and
  some batches are **Deflate64** (`2025_TEOS_XML_11B`, all 80,283 members),
  which zlib cannot decompress. This is why we use the mirror. Don't rebuild
  the extractor without a strong reason.
- `SUB_DATE` in the index is **year-only**, so it cannot order filings within
  a year — it does not solve the version-selection ask in
  [[combined_grants_datamart]].
- The editable install resolves to the **main repo**, so a module that only
  exists on a worktree branch won't import unless you run from that worktree.

Verified on [[feedback_spot_check_known_eins]] canaries (Siegel, Fidelity),
not random samples.
