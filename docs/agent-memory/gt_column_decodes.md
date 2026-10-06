---
name: gt-column-decodes
description: Decoded GT datamart column names for grant totals (from the GTDC Data Dictionary sheet) + the gotcha that PF placeholder rows carry the full aggregate amount.
metadata: 
  node_type: memory
  type: reference
  originSessionId: 06cce495-b027-4a2a-8c36-c4c3da481814
  modified: 2026-07-22T00:02:50.443Z
---

**Data dictionary:** "GTDC Data Mart - Data Dictionary" Google Sheet
(id `1UnOtFmbaVz0cWBkjhbclIGmQOZCtWFYy-GeNATJNI3c`), one tab per staging table.
All DB columns are lowercase versions of the dictionary names, all typed `text`
(guard casts with `~ '^-?[0-9]+(\.[0-9]+)?$'`).

Key decodes (verified against gt_datamart 2026-07-20):

- `basic_fields.graallpaitot` — 990 Part IX line 1 col A, grants to **domestic
  orgs & govts** (the Schedule I trigger amount).
- `basic_fields.gratoddomtot` — misleading name: Part IX line 2, grants to
  **individuals**, NOT domestic orgs.
- `basic_fields.grantoororga` — Part IV checkbox ">$5k grants to orgs →
  Schedule I required". Encoding varies by year: 'true' vs '1'.
- `basic_fields.grsiamcyy` — Part I line 13 grants paid summary.
- `basic_fields_pf.arecgpdcprps` — 990-PF Part I line 25 col (d),
  contributions/gifts/grants paid (charitable disbursements) — the canonical
  PF grants-paid total. Col (a) per-books variant: `arecprexpnss`.
- `privategrants.sigocpyamoun` — grant line-item amount.
- `grants_to_domestic_organizations.retaamofcagr` — cash grant amount;
  `rtaoncassist` non-cash; `rteinorecipi` — **recipient EIN is present** on
  Schedule I.

**Gotcha:** 990-PF "SEE ATTACHMENT"-style placeholder rows in `privategrants`
carry the *full aggregate dollar amount* (e.g. Siegel: 1 row = declared total
to the dollar), so itemized-vs-declared coverage ratios do NOT detect them on
the PF side — detect via row shape (n_rows ≤ 2, max row ≥ ~90% of declared) +
placeholder regex on name/address fields. Coverage ratio DOES work for 990
Schedule I (placeholder rows there parse to zero rows, e.g. Fidelity DAF).
Also: `basic_fields` has duplicate filerein×taxyear rows (amended filings) —
dedupe with DISTINCT ON before joining.

**PF-side specifics:** 990-PF Part XV has NO recipient-EIN column — "mapped"
means the row reached `privategrants_w_recipients` via VDL name/address
matching (join back to `privategrants` needs content columns + filesha256;
matched EIN lives in the typo'd `recipeint_ein_key`). Placeholder detection
must use NAME fields only (`sigocpyrpnam`/`sigocpyrbnbn1/2`): "AVAILABLE UPON
REQUEST" in the address field = real grant with unmatchable address (Cigna),
not an aggregate row. Rows with a person name and no business name are
scholarships/patient assistance (Disney 2022, pharma foundations) —
structurally unmappable to orgs, not matcher failures. Country field
(`sigocaffrfaco`) is blank even for heavy international granters (Gates), so
foreign grants can't be split out cleanly.
