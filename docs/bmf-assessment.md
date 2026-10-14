# IRS Exempt Organizations Business Master File: assessment against gt_datamart

*Vibrant Data Labs, October 14, 2026. IRS files dated October 13, 2026
(the generated report below carries the per-file manifest); gt_datamart at
the 2026_06 GivingTuesday source drops. Reproducible with
`givingtuesday_datamart/exploratory/bmf_assessment.py`.*

---

## TL;DR

The question that started this was "do we have NTEE codes?". We do not, and
we cannot get them from the GivingTuesday e-file datamarts: Forms 990, 990-PF
and 990-EZ never ask for an NTEE code, so neither the XML nor anything built
from it carries one. The IRS keeps the code, and the 501(c) subsection, in
its Exempt Organizations Business Master File (EO BMF), a free download of
every organization currently recognized as exempt. We measured what that
file would add to, and where it disagrees with, the datamart.

- **NTEE.** Filled for 70.8% of the 1.98M distinct EINs, 78.9% of 501(c)(3)s,
  33.6% of 501(c)(4)s. Of our `nonprofit_canonical` EINs, 61.9% get a code; of
  `funder_canonical`, 59.0%.
- **Subsection.** The BMF states the 501(c) paragraph for every org. The Form
  990 datamart only records the header checkboxes (501(c)(3) versus "other
  501(c)"), so today we cannot tell a (c)(4) from a (c)(6) in-house.
- **Not a registration snapshot.** The BMF name, address, tax period and
  amounts are maintained from each processed return. For orgs in both, the
  BMF's tax period is newer than our latest 990 in 49.7% of cases, equal in
  49.6%, older in 0.1%. Renames show up in the BMF before our latest 990.
- **Names agree 93.6%** (token overlap after the matcher's normalisation).
  The rest splits three ways: group-exemption subordinates whose BMF name is
  the parent (Rotary International, Elks, union locals; the chapter sits in
  SORT_NAME), renames the BMF already has, and IRS data-entry conventions.
- **Coverage.** 1.98M active orgs against our 793K nonprofits and 161K
  funders. 82.3% of our nonprofits are present; the 140K absent are revoked
  or terminated orgs that still carry historical grants and must be kept.
- **Matcher universe.** $141.9B of matched grants go to 132,895 grantee EINs
  that have no row in our canonical tables but are in the BMF, almost all
  990-N filers and churches, which no e-file datamart can ever contain.
- **Recommendation.** Layer, do not replace: the BMF as the identity spine
  for every active exempt org, the 990-derived canonical for website, mission,
  formation year, officers and the revoked orgs, and both names as matcher
  aliases.

## How to reproduce

```bash
python -m givingtuesday_datamart.exploratory.bmf_assessment
```

Downloads the six IRS files (about 700 MB) into `data/source_data/eo_bmf/`
on first run, reads `gt_datamart` (never writes to it), and prints a markdown
report that it also writes to `data/exploratory/bmf_assessment_report.md`
with detail CSVs beside it. Both locations are git-ignored. Runtime is a few
minutes, most of it the download and the name comparison. `--refresh`
re-downloads; `--dest` and `--report` move the paths; `--sample` sets how
many disagreement examples the report shows.

Lineage: the IRS republishes the files under the same names roughly monthly
with no version in the filename, so the module records each file's size,
sha256 and server `Last-Modified` in `data/source_data/eo_bmf/manifest.json`
and at the top of the report. Two reports are comparable only at the same
stamp. The 1023-EZ, Candid and Form 990 checkbox measurements in the last
section were one-off checks and are not in the module.

## What the file is

Six CSVs at `https://www.irs.gov/pub/irs-soi/`: four regions (`eo1` to
`eo4`), international (`eo_xx`) and Puerto Rico (`eo_pr`). `eo4`, "all other
areas", is a few thousand rows of territories and foreign addresses. One row
per EIN, 28 columns. The ones that matter here:

| Column | Meaning |
|---|---|
| NAME, SORT_NAME | Name of record and a secondary line: DBA, chapter, trustee, or bank |
| SUBSECTION | 501(c) paragraph: 03, 04, 06 ... ; 92 is a 4947(a)(1) non-exempt charitable trust |
| CLASSIFICATION | Refines the subsection (within (c)(4): 3000 social welfare, 1000 civic league) |
| FOUNDATION | Splits (c)(3)s: 15/16 public charity, 10 church, 11 school, 12 hospital, 17 supporting org, 04 private non-operating, 03 private operating |
| NTEE_CD | National Taxonomy of Exempt Entities code, letter plus two digits |
| GROUP | Group exemption number: non-zero means a subordinate under a parent's ruling |
| RULING | Ruling date, YYYYMM: the only registration-time field |
| STATUS | 01 is in good standing |
| FILING_REQ_CD | 01 files 990/990-EZ, 02 files the 990-N postcard, 06 church not required, 07 990-PF |
| TAX_PERIOD, ASSET_AMT, INCOME_AMT, REVENUE_AMT | Latest return the IRS processed, paper or e-file |

The 990 forms ask for none of SUBSECTION's number, FOUNDATION, NTEE_CD or
GROUP, which is why the e-file datamarts lack them.

## Results

The block below is the module's output, pasted verbatim.

Generated 2026-10-14T17:29:59+00:00 by `givingtuesday_datamart.exploratory.bmf_assessment`.

### Snapshot

| name | size | sha256 | last_modified |
|---|---|---|---|
| eo1 | 48,985,524 | ea091ad16b21181a74ce5bdc42ea2df4afce3c64cde51ec7e5088db5a8c2ca0b | Tue, 13 Oct 2026 19:13:10 GMT |
| eo2 | 126,801,089 | 6b5d18849f0fb5324955c342596b9dbfa903001cf5b2a8b0076d02fddd5f6909 | Tue, 13 Oct 2026 19:13:13 GMT |
| eo3 | 166,254,445 | b1a20c75e02f96acc7986b0f9ee8f1efc4c40f77ba54dfbfbc4d5ceede83015f | Tue, 13 Oct 2026 19:13:15 GMT |
| eo4 | 877,106 | dfaf9b96dd4f52ea72a551eadd226ab275f2287430f4e60f1a53b679bf3a656c | Tue, 13 Oct 2026 19:13:18 GMT |
| eo_xx | 423,175 | 1c8821dac0bdefd564aff259b876559033de05e9c72b709478cce576403059b1 | Tue, 13 Oct 2026 19:14:13 GMT |
| eo_pr | 454,192 | cbe1d3b339754df4d39044a4af8ac26ce560038e13a67919a487f428aa647ace | Tue, 13 Oct 2026 19:13:59 GMT |

Rows read 1,980,979; distinct EINs 1,975,975 (duplicates across files resolved by latest TAX_PERIOD, then file order).

### 1. NTEE fill

| subsection | orgs | with_ntee | ntee_fill |
|---|---|---|---|
| all | 1,975,975 | 1,399,764 | 70.8% |

| subsection | orgs | with_ntee | ntee_fill |
|---|---|---|---|
| 03 | 1,650,468 | 1,301,603 | 78.9% |
| 04 | 68,563 | 23,023 | 33.6% |
| 06 | 57,823 | 24,183 | 41.8% |
| 07 | 46,668 | 17,665 | 37.9% |
| 05 | 43,661 | 9,283 | 21.3% |
| 08 | 35,016 | 3,622 | 10.3% |
| 19 | 24,696 | 6,573 | 26.6% |
| 10 | 14,396 | 2,047 | 14.2% |
| 13 | 9,809 | 4,850 | 49.4% |
| 92 | 5,848 | 133 | 2.3% |
| 12 | 5,430 | 1,813 | 33.4% |
| 09 | 5,254 | 2,127 | 40.5% |

Filing requirement (02 = 990-N postcard filers, 01 = 990/990-EZ, 06 = churches not required):

| filing_req_cd | orgs | share |
|---|---|---|
| 02 | 1,016,891 | 51.5% |
| 01 | 514,725 | 26.0% |
| 06 | 288,054 | 14.6% |
| 00 | 142,654 | 7.2% |
| 13 | 9,299 | 0.5% |
| 14 | 2,745 | 0.1% |
| 03 | 808 | 0.0% |
| 07 | 797 | 0.0% |

### 2. Coverage of gt_datamart populations

| population | eins | in_bmf | in_bmf_share | with_ntee | with_ntee_share_of_all | with_ntee_share_of_present | c3 | c4 | c5 | c6 | c7 | other_subsection |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nonprofit_canonical | 793,106 | 653,066 | 82.3% | 490,988 | 61.9% | 75.2% | 518,740 | 26,370 | 18,581 | 35,405 | 18,695 | 35,275 |
| funder_canonical | 161,130 | 126,332 | 78.4% | 95,036 | 59.0% | 75.2% | 120,752 | 7 | 1 | 2 | 3 | 5,567 |
| grantee EINs in unioned_grants | 856,493 | 499,215 | 58.3% | 379,644 | 44.3% | 76.0% | 463,950 | 8,930 | 4,015 | 11,111 | 2,850 | 8,359 |

### 3. Name and address agreement (canonical rows present in the BMF)

#### nonprofit_canonical

| measure | value |
|---|---|
| present | 653,066 |
| name_exact_share | 61.2% |
| name_any_alias_share | 62.5% |
| same_org_jaccard_share | 93.6% |
| zip5_same_share | 89.3% |
| city_same_share | 88.4% |
| state_same_share | 98.8% |
| status_active_share | 99.9% |
| bmf_newer_share | 49.7% |
| same_period_share | 49.6% |
| bmf_older_share | 0.1% |

| classification | rows | share |
|---|---|---|
| same org (formatting/abbreviation) | 611,255 | 93.6% |
| partial overlap: group subordinate (SORT_NAME set) | 13,855 | 2.1% |
| partial overlap: BMF period newer (likely rename) | 11,979 | 1.8% |
| partial overlap: same period (entry conventions, or rename on one side) | 11,839 | 1.8% |
| no token in common: BMF period newer (likely rename) | 2,083 | 0.3% |
| no token in common: same period (entry conventions, or rename on one side) | 1,467 | 0.2% |
| no token in common: group subordinate (SORT_NAME set) | 588 | 0.1% |

Sample of 12 disagreements (ours | BMF name | BMF sort name):

- 'HAGRIDSHOPE' | 'HAGRIDS HOPE' | '' | ours 2024-12-31 vs BMF 202512 | no token in common: BMF period newer (likely rename)
- 'ANNA VAN LOAN TR UA NAT COUN CHUR' | 'ANNA F G VANLOAN TRUST T 3162 4 DTD 6128 FBO NATL COUNCIL OF CHURCHES' | '' | ours 2011-12-31 vs BMF 201912 | partial overlap: BMF period newer (likely rename)
- 'WESTBROOKGLEN GROVE PARENT' | 'ILLINOIS CONGRESS OF PARENTS TEACHERS' | 'WESTBROOK GLEN GROVE PTA' | ours 2025-06-30 vs BMF 202506 | partial overlap: group subordinate (SORT_NAME set)
- 'US SPACE & ROCKET CENTER' | 'U S SPACE & ROCKET CENTER EDUCATION FOUNDATION' | '' | ours 2025-09-28 vs BMF 202509 | partial overlap: same period (entry conventions, or rename on one side)
- 'ARTHUR G JAMES CANCER HOSPITAL' | 'ARTHUR G JAMES CANCER HOSPITAL & RICHARD J SOLOVE RSRCH INST FNDTN' | '' | ours 2024-12-31 vs BMF 202512 | partial overlap: BMF period newer (likely rename)
- 'THE MIRACLE LEAGUE OF COASTAL' | 'MIRACLE LEAGUE AT PLEASURE ISLAND INC' | '' | ours 2024-12-31 vs BMF 202512 | partial overlap: BMF period newer (likely rename)
- 'Pi Kappa Alpha Fraternity Delta Nu Chapter' | 'DELTA NU CHAPTER' | '' | ours 2025-06-30 vs BMF 202506 | partial overlap: same period (entry conventions, or rename on one side)
- 'AMERICAN FEDERATION OF TEACHERS Henry Ford Community College Adjunct Faccul' | 'AMERICAN FEDERATION OF TEACHERS' | '0337 HENRY FORD COMMUNITY COLLEGE A' | ours 2024-06-30 vs BMF 202506 | partial overlap: group subordinate (SORT_NAME set)
- "FREMONT FIREMEN'S ASSOCIATION INC" | 'INTERNATIONAL ASSOCIATION OF FIRE FIGHTERS' | 'L1689 FREMONT' | ours 2025-03-31 vs BMF 202603 | partial overlap: group subordinate (SORT_NAME set)
- 'Youth Reach Out Program Inc' | 'YOUTH REACHOUT PROGRAM INC' | '' | ours 2012-12-31 vs BMF 202512 | partial overlap: BMF period newer (likely rename)
- 'Palm Springs Scottish Rite of Freem' | 'ANCIENT & ACCEPTED SCOTTISH RITE OF FREE MASONRY SO JURIS USA' | 'VALLEY OF PALM SPRINGS & BODIES' | ours 2024-12-31 vs BMF 202412 | partial overlap: group subordinate (SORT_NAME set)
- 'CenterInstitute for legislative' | 'CENTER FOR LEGISLATIVE ANALYSIS INC' | '' | ours 2023-12-31 vs BMF 202312 | partial overlap: same period (entry conventions, or rename on one side)

#### funder_canonical

| measure | value |
|---|---|
| present | 126,332 |
| name_exact_share | 61.4% |
| name_any_alias_share | 61.5% |
| same_org_jaccard_share | 91.1% |
| zip5_same_share | 93.9% |
| city_same_share | 90.3% |
| state_same_share | 98.1% |
| status_active_share | 95.0% |
| bmf_newer_share | 49.4% |
| same_period_share | 50.6% |
| bmf_older_share | 0.0% |

| classification | rows | share |
|---|---|---|
| same org (formatting/abbreviation) | 115,125 | 91.1% |
| partial overlap: BMF period newer (likely rename) | 4,795 | 3.8% |
| partial overlap: same period (entry conventions, or rename on one side) | 3,602 | 2.9% |
| partial overlap: group subordinate (SORT_NAME set) | 1,843 | 1.5% |
| no token in common: same period (entry conventions, or rename on one side) | 465 | 0.4% |
| no token in common: BMF period newer (likely rename) | 430 | 0.3% |
| no token in common: group subordinate (SORT_NAME set) | 72 | 0.1% |

Sample of 12 disagreements (ours | BMF name | BMF sort name):

- 'CAN FOUNDATION' | 'C A N FOUNDATION' | '' | ours 2023-10-31 vs BMF 202510 | no token in common: BMF period newer (likely rename)
- 'THE ERICA WHITE FOUNDATION' | 'ERICA WHITE FOUNDATION AGAINST DOMESTIC VIOLENCE AND ABUSE' | '' | ours 2024-12-31 vs BMF 202412 | partial overlap: same period (entry conventions, or rename on one side)
- "CLEVELAND H UA ST PETER'S CH ET" | 'HAROLD & VICTORINE CLEVELAND CHARITABLE TRUST' | '' | ours 2025-12-31 vs BMF 202512 | partial overlap: same period (entry conventions, or rename on one side)
- 'O W DAVIS TR UW CHAR' | 'ORLANDO W DAVIS TRUST 0129-0006-00 -6' | '' | ours 2024-12-31 vs BMF 202512 | partial overlap: BMF period newer (likely rename)
- 'ALICE F CLARK TRUST' | 'ALICE F CLARK TRUST 4001047-2 C & NATIONAL BANK OF SC' | '' | ours 2024-05-31 vs BMF 202505 | partial overlap: BMF period newer (likely rename)
- 'SC BENJAMIN FOUNDATION' | 'S C BENJAMIN FOUNDATION' | '' | ours 2025-04-30 vs BMF 202504 | partial overlap: same period (entry conventions, or rename on one side)
- 'PEFEIT FOUNDATION INC' | 'P E F E I T FOUNDATION INC' | '' | ours 2024-12-31 vs BMF 202512 | no token in common: BMF period newer (likely rename)
- 'ARNOLD FAMILY FOUNDATION' | 'ARNOLD FAM FOUNDATION' | '' | ours 2025-12-31 vs BMF 202512 | partial overlap: same period (entry conventions, or rename on one side)
- 'F A KORSCHUN FOUNDATION INC' | 'FA KORSCHUN FOUNDATION INC 1025005564-328900800' | '' | ours 2023-12-31 vs BMF 202312 | partial overlap: same period (entry conventions, or rename on one side)
- 'FLORENCE E SINCLAIR MEM T U W' | 'FLORENCE E SINCLAIR MEMORIAL FUND' | '' | ours 2025-03-31 vs BMF 202603 | partial overlap: BMF period newer (likely rename)
- 'CARDIOLOGY FOUNDATION OF NEW YORK' | 'CARDIOLOGY ASSOCIATES FOUNDATION' | '' | ours 2025-09-30 vs BMF 202509 | partial overlap: same period (entry conventions, or rename on one side)
- 'VIT IVANO P IRREV TRUST TAI' | 'IVANO P VIT IRRV TR U-D-T 10151990' | '' | ours 2024-12-31 vs BMF 202512 | partial overlap: BMF period newer (likely rename)

### 4. Matcher universe: grantee EINs in unioned_grants

unioned_grants: 16,044,328 rows, $1,740.2B; rows without a grantee EIN 605,173 ($56.2B).

| in_canonical | in_bmf | eins | rows | dollars |
|---|---|---|---|---|
| no | no | 313,437 | 1,160,251 | $204.4B |
| no | yes | 132,895 | 929,537 | $141.9B |
| yes | no | 43,841 | 470,874 | $37.7B |
| yes | yes | 366,320 | 12,878,493 | $1,300.0B |

Gain cell (not in canonical, in BMF) by filing requirement:

| filing_req_cd | eins | share |
|---|---|---|
| 02 | 63,268 | 47.6% |
| 06 | 60,830 | 45.8% |
| 01 | 4,108 | 3.1% |
| 13 | 2,908 | 2.2% |
| 00 | 855 | 0.6% |
| 14 | 840 | 0.6% |
| 07 | 58 | 0.0% |
| 03 | 28 | 0.0% |


## Interpretation

### Is it just the name at registration?

No. RULING is the registration-time field; everything else is maintained.
The tax-period comparison above is the proof: for the 653K nonprofits in
both sources the IRS has processed the same return as our latest 990 or a
later one in 99.9% of cases. Sampled renames (Borgess Health Alliance to
Beacon Health Alliance, Easter Seal Society of Mahoning to Easterseals of
Northeast Ohio, Policy and Taxation Group to Family Enterprise USA Action)
appear in the BMF while our latest 990 still carries the old name. The lag
runs the other way: the 990 name is whatever the filer typed, the BMF name
is what the IRS has on record after processing the change.

### The group-subordinate trap

About two percent of our nonprofits are subordinates under a group
exemption, and for them the BMF NAME is the parent: every Rotary club is
"ROTARY INTERNATIONAL", every Elks lodge is "BENEVOLENT & PROTECTIVE ORDER OF
ELKS OF THE USA", with the chapter in SORT_NAME. Swapping the 990 name for
the BMF name would collapse thousands of distinct orgs onto their parents.
Both names must be kept, and SORT_NAME must travel with NAME.

### Revoked and terminated orgs

The BMF holds only currently recognized orgs. The 17.7% of
`nonprofit_canonical` and 21.6% of `funder_canonical` absent from it are
orgs that filed between 2010 and now and have since been revoked
(three years without filing) or terminated. They still hold historical
grants and must stay in the identity tables, flagged. The IRS
Auto-Revocation List, published alongside the BMF, would supply revocation
dates.

### IRS entry conventions

Roughly two percent of names differ only by how the IRS keys them: no
apostrophes, spaces inside initials ("R T FOUNDATION"), abbreviations ("FAM
FOUNDATION", "TR UW FBO"), a 70-character cut, and trust names in
surname-first bank style on the 990 but natural order in the BMF. These are
why the BMF names belong in the matcher as additional aliases rather than
as replacements.

## Side measurements not in the module

Measured once on October 14, 2026 by hand; recorded here so they are not
re-derived.

- **GivingTuesday's `All_Years_1023EZ.csv`** (batch 2026_06_09, not
  ingested) carries an applicant-chosen NTEE code for 509,063 EINs, 100%
  filled, signature years 2014 to 2023, all small orgs. They overlap 10.9%
  of `nonprofit_canonical`, 13.4% of `funder_canonical`, 8.5% of grantee
  EINs. The header column is ` nteecode` with a leading space. It adds no
  org the BMF lacks.
- **Candid extract** at
  `shared-data-clean/data/candid/2025_09_08/candid_main.txt` (UTF-16,
  pipe-delimited): `ntee_code` filled for 99.4% of its 82,901 rows, with a
  description column; `vdl_tools/scrape_enrich/prepare_candid_old.py` maps
  the description to an industry code. Coverage is that pull's universe.
- **Form 990 header checkboxes** in `basic_fields`: `organization3` is the
  501(c)(3) box (set on 75% of current rows), `organization` the "other
  501(c)" box (25%), `organization1` the 4947(a)(1) box, `organization7` the
  527 box. The number the filer writes in the "other" box is not in the
  extract.

## Recommendation

Three stacked PRs, proposed as a session on October 14, 2026:

1. **Ingest** the six files as a registered source (`public.eo_bmf`, one row
   per EIN, lineage in `datamart_meta.ingest_runs`, versioned by
   `Last-Modified`).
2. **Canonical spine.** Add BMF columns (IRS name and sort name, subsection,
   classification, foundation code, NTEE, group number, ruling year, filing
   requirement, status, latest period and amounts, an in-BMF flag) to the
   identity tables, and identity rows for the 1.3M active orgs with no 990,
   marked by source. Never overwrite the 990 name; keep the revoked orgs.
3. **Matcher aliases.** BMF NAME and SORT_NAME with city, state and zip as
   extra alias rows keyed by EIN, with their own source value so match
   lineage records which alias won. Prove on a subset; the full rerun stays
   with Zein.
