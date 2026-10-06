---
name: ez-filings-vs-combined-datamart
description: "990-EZ gap: add EZ as its own raw+canonical source; do NOT switch to GT's Combined DataMart (48 cols, drops 24 in active use, 990N rows lack FileSha256/URL)."
metadata: 
  node_type: memory
  type: project
  originSessionId: 47ebcab4-e058-4d24-b1e3-1d83e4f47877
  modified: 2026-08-05T00:54:52.319Z
---

Decision (2026-08-04, branch `claude/990-ez-filings-basic-fields`): **add
`irs_990ez_basic_fields` + `irs_990ez_programs` as new sources; do NOT
repoint 990/990-PF at the Combined DataMart.**

**BUILT AND VERIFIED 2026-08-04** (commit 6848d24, not yet in a PR — the
branch's PR #40 covers only the unrelated `_current` bloat fix 087483c).
Ingested: `basic_fields_ez` 2,192,426 rows, `basic_fields_ez_current`
2,150,438, `programs_ez` 3,317,308. `nonprofit_canonical` 479,255 ->
**793,106** (+313,851, matching the predicted count exactly);
`nonprofit_text` 793,105. **Text-EINs-with-no-canonical-row went 55,710 ->
0** — the NULL-identity search bug is gone. source_form split: 990 433,394 /
990-EZ 359,712, with 45,861 orgs that filed 990 historically now winning on
a more recent EZ year. FTS "food pantry": 6,071 hits, 2,154 (35%) from
EZ-sourced orgs. Matcher views verified unchanged, no view references
`basic_fields_ez` -> no `MATCHING_INPUT_SHAPE_VERSION` bump. DB 83 -> 90 GB.

**Why not the combined file** (`*_990_990ez_990pf_990n_Combined_DataMart.csv`):
- Column counts, `2026_06_16` release: 990=164, 990-PF=137, 990-EZ=95,
  **Combined=48**. It is a lowest-common-denominator schema.
- **24 columns in active use today are absent from it.** Notably the whole PF
  financial panel (`areterexpnss`, `arecrrexpnss`, `anreextoreex`,
  `arecgpdcprps`, `sudichacdees1-4`, `sudichacexxp1-4`, `suprrein*`,
  `spriaopritot`), the 990 revenue breakdown (`governgrants`, `alloothecont`,
  `noncascontri`, `fundraevents`, `federacampai`, `relateorgani`), and
  `incarenm` + `formationorm` which feed `nonprofit_canonical`.
- **990N rows carry no `URL` and no `FileSha256`** (0/5,403 in a sampled
  block) → breaks the filing-version tiebreak [[basic-fields-dedup-34]]
  depends on.
- Release cadence lags the per-form files (2025: EZ shipped 10-17 + 10-28,
  combined's nearest was 11-18).

**But use it as the crosswalk oracle.** GT already harmonized EZ columns into
990 names there, so it is the authoritative mapping spec even though we don't
ingest it: `CONGIFGRAETC`→`totacashcont`, `TOTALRREVENU`→`totrevcuryea`,
`TOTALEEXPENS`→`totexpcuryea`, `DONOADVIFNDS`→`donoadvifund`,
`WEBSITADDRES`→`websitsiteit`. Verified populated on EZ rows (2,051/2,090 for
`totrevcuryea`). EZ has no `dbanbnline11/22`, `incarenm`, or `formationorm`.

**Size of the gap** (sampled 11,601 distinct EZ EINs across the file, checked
against live DB): only **25.1% are already in `basic_fields`**, 0.4% in
`basic_fields_pf`, 43.0% in `nonprofit_text`, and **74.5% are absent from
`nonprofit_canonical`**. Est. ~430K distinct EZ EINs total → adds ~320-340K
orgs to a 479K universe. Explains the 55,710 EINs in `nonprofit_text` with no
canonical row (the NULL-identity search hits documented at client.py:169) —
EZ filers account for essentially all of them.

**Traps for implementation:**
- `FormType` in sources/spec.py is `Literal["990", "990-PF"]` — needs a
  `"990-EZ"` member.
- EZ has `FileSha256` + `URL` + `AMENDERETURN`, so the #34 dedup rule ports
  over unchanged; give it a `_current` table from day one.
- `990EZPart3Programs` is **long-shaped** (one row per activity,
  `PSADPSACCOM` + `PRIMEXEMPURP`), not wide like `public.programs` — needs its
  own arm in the `nonprofit_text` UNION ALL.
- **Keep EZ out of the matcher universe for v1.** Adding ~340K EINs to
  `basic_fields_unique_names_view` bumps `MATCHING_INPUT_SHAPE_VERSION` and
  forces another full rerun ([[matching-rerun-pending]] was 7.8h). Likely
  improves recall a lot — small grant recipients file EZ — but cost it
  separately.
- Disk is no longer the blocker: EZ ≈ 2.0 GB + Part3Programs ≈ 2.3 GB in PG,
  against 83 GB used after the bloat fix.
