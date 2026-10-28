# Our own extract of the IRS e-file XML

*Vibrant Data Labs, October 23, 2026. IRS batches of 2024, 2025 and 2026
(through `2026_TEOS_XML_09A`, posted 2026-10-21); GivingTuesday's tables at
their 2026_06_16 / 2026_06_25 releases. Reproducible with
`python -m givingtuesday_datamart.own_extract`.*

---

## TL;DR

GivingTuesday (GT) has published no new datamart since June 2026, and its
June extract stops at the IRS's April batch. We read the IRS's own monthly
batches ourselves, for the 2024, 2025 and 2026 batches, into tables shaped
exactly like GT's, and measured them against GT's tables on every return
both hold.

- **Parity.** On the returns both hold, the return tables come out of the
  IRS XML the way GT has them: all 29 columns production reads on the 990
  (785,185 returns), 32 of 33 on the 990-PF (273,504; the other is a file
  hash on 28 returns), all 16 on the 990-EZ (469,853), all of missions,
  programs and Schedule O (every one of GT's 6,382,813 notes). On the
  grants, our 990-PF rows give the same total per return as production's
  repaired `privategrants_current` on 99.1% of returns, and 99.99% of
  `privategrants_future_current`'s rows; 99.5% of GT's Schedule I rows have
  an identical row of ours. Production's repair rules, run over our rows,
  only choose versions (one row aside).
- **The newest schema.** 71% of the returns GT never released are filed
  under 2025v4. On the 73,125 2025v4 returns GT does hold, the 990-PF return
  table agrees on all 33 read columns; one IRS rename (a grant recipient's
  foundation status) is now in the concordance, and GT's extract misses it.
- **What GT is missing.** In the 2024-2026 batches: 182,432 Form 990s,
  68,893 990-PFs and 108,743 990-EZs, 98% of them for tax year 2024 or
  later; with them 753,933 private-foundation grants ($39.2B), 677,379
  Schedule I grants ($120.1B) and 11,351 grants approved for future payment
  ($4.0B).
- **New GT defects found.** GT blanks every Schedule I grant whose
  recipient has a second name line ("HARVARD UNIVERSITY" / "SCHOOL OF
  DENTAL MEDICINE"): the amount, recipient EIN, address and purpose are all
  NULL. That is 44,525 rows in `grants_to_domestic_organizations_current`,
  tax years 2013 to 2025, every such row GT has. In the 2024-2026 batches
  alone it is 10,998 grants and $3.84B of cash, 9,598 of them with a
  recipient EIN GT dropped; ours has every one. And GT loses a 990-PF's
  application address, contact and managers when the return gives only one:
  the application address of 61,563 of the 273,504 990-PFs both hold.
- **The patch, ours first.** Decided on 2026-10-24: for every batch we
  read (2024 on) our rows replace GT's in production's staging tables, and
  GT's stay for the years before. Before deciding, the 1,760 990-PF returns
  whose totals differ from production's were checked against each filing's
  line 25: ours matches it, GT's does not (GT's extract writes the block's
  total into grants, $812.8M of it on 332 returns). `own_extract patch` is
  built and was proven on a scratch copy with production's own `_current`
  DDL; it has not run on production.
- **Checks that need no GT** (2026-10-28). Our grant rows against each
  filing's own totals: 96.9% of 990-PFs with grants match line 25, the
  same where GT holds the return and where it does not; 97.7% of Schedule
  Is fit within Part IX line 1. And the XML elements no concordance path
  reads, per schema version: it found the foreign addresses of 990-PF
  grant recipients we did not read (read since concordance
  `036d62ea8f4a`), and would have caught the 2025v4 rename without GT.

## Why

Our clients need recent filings. GT's release history (the
`EfileDataMarts/` listing, 2026-10-23):

| datamart | releases |
|---|---|
| 990 return table | 2025-08-29, 2025-10-18, 2026-06-16 |
| 990-PF return table | 2024-11-13, 2024-12-10, 2025-02-19, 2025-08-29, 2025-10-28, 2026-06-16 |
| 990-EZ return table | 2025-01-18, 2025-02-19, 2025-03-04, 2025-03-06, 2025-08-29, 2025-10-17, 2025-10-28, 2026-06-16 |
| 990 missions | 2024-03-30, 2024-06-21, 2025-08-29, 2026-06-16 |
| 990-PF grants (3A) | 2025-08-29, 2025-10-28, 2026-06-16 |
| Schedule I grants, Schedule O | 2025-08-29, 2026-06-25 |

Nothing since. The IRS meanwhile posted 2026 batches 05A and 05B (June 17,
the day after GT's cut), 06A, 07A, 08A and 09A (October 21). Of the returns
in the IRS's 2024-2026 batches, GT's extract lacks 360,068 of the three
forms (182,432 Form 990, 68,893 990-PF, 108,743 990-EZ; `parity`, the table
"What GT does not have"), 98% of them for tax year 2024 or later. By batch,
it holds 2026's 01A to 04A and nothing after, and never processed most of
2025's 09A and 10A, which its own raw-XML mirror does hold (a one-off
comparison of the IRS index with GT's urls on 2026-10-23, not in the code).

GT's raw-XML mirror (`gt990datalake-rawdata`) is no substitute: it has not
changed since 2026-08-25, holds 05A but not 05B, and nothing of 08A or 09A.

## What the IRS publishes, and the traps

- **Batches.** `https://apps.irs.gov/pub/epostcard/990/xml/<year>/<year>_TEOS_XML_<MM><L>.zip`,
  about one a month, usually the third Wednesday; 2024 has 12 (4.4 GB),
  2025 has 16 (4.5 GB), 2026 has 10 so far (2.8 GB). Each archive holds one
  `<object_id>_public.xml` per return.
- **The index does not name every batch.** `index_2026.csv` labels the
  168,344 returns of June's two archives all `2026_TEOS_XML_05A`; 05B
  exists only as a file on the server. We find batches by asking the server
  (`own_extract batches 2026`), and a return's batch is the archive it sits
  in.
- **Deflate64, and ZIP64.** 2025 05B and 11B and 2026 05A and 05B are
  compressed with Deflate64, which Python's `zipfile` cannot read. Info-ZIP
  `unzip` can, and `batches.iter_members` unpacks such an archive with it
  once (30 seconds for 05A's 84,172 members). Those archives also hold more
  than 65,535 members, so they are ZIP64, and Info-ZIP 6.0 misreads their
  end record: it warns about "76 extra bytes", "compensates", writes every
  member correctly and exits with status 3. So its status is not trusted
  either way; every member is held to the size and CRC-32 the central
  directory records (all 84,172 of 05A pass). `bsdtar` is no substitute:
  it writes zero-filled files for Deflate64 members without an error.
- **The files are GT's files.** GT's `filesha256` equals the SHA-256 of the
  IRS archive member on all 14,229 GT rows for batch 01A's returns, so a
  row of ours and a row of GT's for the same return come from the same
  bytes.

## What we built

`givingtuesday_datamart/own_extract/`:

| module | does |
|---|---|
| `batches` | finds the archives for a year (HEAD requests), fetches one with a stamp (server Last-Modified and size, the archive's SHA-256), reads its members (Deflate64 through `unzip`), copies it to `s3://givingtuesday-datamart/irs/xml_batches/<year>/` |
| `parse` | flattens a return to `path -> [values]` (namespaces dropped, document order) and reads the repeating groups (a grant, a Schedule O note) one dict per occurrence |
| `extract` | turns a parsed return into rows of GT's ten staging tables, GT's column names and order, from `data/own_extract/concordance.csv` |
| `load` | writes a batch's rows into schema `irs_extract` (one table per GT table, same name), replacing that batch's rows in one transaction, and records the run in `datamart_meta.ingest_runs` (logical name `irs_extract`) with the archive's stamp and the concordance's SHA-256 |
| `discover` | proposes the concordance: for each GT column, the xpath that reproduces it on the most returns both sides hold (at most 8,000 returns of each form, so memory stays bounded) |
| `parity`, `report` | measure `irs_extract` against GT's tables, in SQL, and write the report below |
| `patch` | puts our rows into production's staging tables in place of GT's, ours first (*The patch, ours first*, below) |
| `proof` | runs the patch on a scratch copy of a sample of filers and builds every `_current` relation there with production's DDL |

Every row of ours carries GT's columns plus a key GT's rows never had: the
IRS object id and, in the group tables, the group's position in the return
(`group_ordinal`). The url is GT's own pattern
(`.../EfileData/XmlFiles/<object_id>_public.xml`), because downstream code
reads the object id out of it and orders versions by it.

```bash
python -m givingtuesday_datamart.own_extract batches 2026
python -m givingtuesday_datamart.own_extract load 2026            # every 2026 archive
python -m givingtuesday_datamart.own_extract load 2026 --batch 2026_TEOS_XML_09A
python -m givingtuesday_datamart.own_extract parity               # report of all loaded batches
python -m givingtuesday_datamart.own_extract discover PATH.zip    # re-propose the concordance
python -m givingtuesday_datamart.own_extract prove-patch          # the patch on a scratch copy
python -m givingtuesday_datamart.own_extract patch                # the patch on production
```

### What was loaded

Every archive the IRS serves for 2024, 2025 and 2026, loaded on
2026-10-23 and reloaded twice that evening as the parity runs corrected the
concordance. The tables hold each archive once, loaded with concordance
SHA-256 `3d9b48d6e5e7`; the run records in `datamart_meta.ingest_runs` and
the generated report's batch table carry each archive's Last-Modified and
SHA-256, the same in every run of it, and each reload's row counts equal
the first load's in every table of every archive:

| year | archives | Form 990 | 990-PF | 990-EZ | schema versions (share of returns) |
|---|---|---|---|---|---|
| 2024 | 12 | 363,100 | 126,982 | 215,355 | 2023v5 68%, 2022v5 22%, 2023v4 4%, older 6% |
| 2025 | 16 | 376,920 | 130,347 | 217,102 | 2024v5 75%, 2023v6 22%, older 3% |
| 2026 | 10 | 227,597 | 85,068 | 146,139 | 01A-04A: 2025v4 51%, 2024v5 47%, 2023v6 2%; 05A-09A: 2025v4 71%, 2024v5 26%, 2025v6 1% |

2026's count is the IRS index's exactly (472,357 returns of every form,
990-T included). In the database the extract is 12.4 GB, Schedule O 4.4 GB
of it; a parse is about 0.75 ms a return, and a batch loads in one to eight
minutes from a laptop.

### The concordance

522 GT columns over ten tables, one line each: the xpath (or several,
first present wins), the occurrence for GT's `_0`/`_1` columns, and how
many returns of batch 01A it agreed on. Found by `discover` on 01A (12,245
returns, schema versions 2022v5.0 to 2025v4.0) against GT's rows for the
same returns. GT does not publish its xpaths: its data dictionary names the
form line only, and its parser repositories map to the Nonprofit Open Data
Collective's variable names, not to the datamart columns. Third-party
concordances (NODC's concordance990, IRSx) exist and stop at schema
2024v5.5; we use neither.

GT rules one path does not express, all now in the file:

- **990-PF 3B recipient name** (`privategrants_future.sigocaffrbnb1`): GT
  has no person-name column for 3B and puts the person's name in business
  name line 1 when the group names a person, else the business name.
- **Territory filers' US address** (`basic_fields.filerus*`): a 990 filer in
  Puerto Rico or the Northern Mariana Islands often gives the header a
  foreign address; GT then fills the US address columns from the address in
  the form's body, and the foreign ones from the header.
- **990 foreign address** (`basic_fields.filerfor*`): the counterpart. When
  the header has no foreign address, GT takes the form body's (420 to 722
  of 785,185 returns of 2024-2026).
- **A 990-PF grant recipient's foreign address** (`privategrants.sigocpyrfa*`,
  `privategrants_future.sigocaffrfa*`): GT fills the US address columns
  from the foreign address when there is no US one: lines 1 and 2, the
  city, `ProvinceOrStateNm` as the state and `ForeignPostalCd` as the ZIP
  (measured on 56,104 3a and 8,487 3b foreign grants, 2026-10-28). The
  country stays in `sigocaffrfaco`, which GT leaves empty in 3a. Followed
  since concordance `036d62ea8f4a`: production reads these columns.

Corrections to what the search proposed, each measured by `parity` on every
loaded batch:

- **Columns the search left without a path** (34, most of them empty in GT
  on every return of 01A) take their sibling's: the foreign address beside
  the US one, DBA line 2 beside line 1, the filer's name line 2 in the group
  tables beside line 1. 11 stay unmapped, none read by production.
- **The filer's name line 2 on Schedule I** stays empty. GT fills it beside
  line 1 in the 990-PF grant, 3B and 990-EZ program tables, but on no
  Schedule I row of 2024-2026. Production reads it there: `_current`'s
  Schedule I dedup hashes it to tell two grants apart, and the matcher reads
  it as the granter's name, so a patch that mixes GT's rows and ours must
  agree on it.
- **GT's numbered columns** (`_0`, `_1` ...) are the n-th occurrence of one
  element, filled only when the element repeats (a GT defect, below). Ours
  keeps a single occurrence in `_0` and equals GT wherever GT has a value.

The rule behind the last two: where production reads a column and its
logic depends on GT's convention, ours follows GT; where production does
not read it, ours keeps what the XML holds. So the 990's program activity
codes for lines 4b and 4c (`activicodeo2`, `activicodeo3`), which GT never
fills, are filled in ours on the 497 and 334 returns that report one.

A return header never carries an address line 2 (none of 01A's does). GT's
990 line 2 is the one in the form's body, as ours now is; the 990-PF's and
990-EZ's column reads the header only and is empty on both sides.

GT keeps a filer's literal text "None" as text (four websites in 01A), so
a plain copy is right.

One IRS rename, found by `parity`'s fill-rate comparison of the 2026
batches GT never released against the ones it did:

- **Foundation status of a grant's recipient** (`privategrants.sigocpyrfsta`,
  `privategrants_future.sigocaffrfstat`). Schema 2025v4 renamed
  `RecipientFoundationStatusTxt`, free text, to `RecipientFoundationStatusCd`,
  a code list (`PC`, `NC`, `I`, `POF`, `PF`, `GOV`, `SO I`, ...). On batch
  07A the column fell from 85% filled (2024v5 returns) to 0% (2025v4). GT's
  June extract has the same hole: on the 42 2025v4 foundations of batch 01A
  its column is NULL where the XML holds the code. The concordance now
  reads either name, so ours keeps the field, `I` for grants to individuals
  included, which the placeholder work list reads.

### The newest schema versions

New returns are filed under schema versions the concordance was not found
on: 2026's batches 05A-09A, which GT never released, are 71% 2025v4. GT's
2026 batches 01A-04A hold 73,125 returns filed under 2025v4, which is the
test of the mapping on them (`parity --schema-checks`):

| table | 2025v4 returns | read columns equal to GT on every return |
|---|---|---|
| basic_fields (990) | 22,388 | 29 of 29 |
| basic_fields_pf (990-PF) | 15,215 | 33 of 33 |
| basic_fields_ez (990-EZ) | 35,522 | 16 of 16 |
| mission_statements, programs | 22,388 | all |

The same run's fill-rate check, each column of 05A-09A against 01A-04A,
flags nothing since the foundation-status rename is read (it was the one
column it flagged before: 42.6% filled to 9.2%).

Across every mapped field of the group tables, batch 07A shows one more
shift between 2024v5 and 2025v4 with no new element beside it: Schedule I's
valuation method and non-cash description (about 80% filled to 7-10%), so
2025v4 lets a filer leave them out; neither is read by production.

## Parity with GT

Measured on 2026-10-23 by `own_extract parity` over all 38 batches, in two
runs. The first, on the batches as first loaded (concordance SHA-256
`a6523dd0969c`), found the corrections above. The second, after every batch
was reloaded with them (`c9b00396f60e`), measured everything below in 1 h
20 min; it found the Schedule I name-line-2 rule, and Schedule I alone was
measured again after the last reload (`parity --table`, concordance
`3d9b48d6e5e7`, which changes nothing else). The report lands in
`data/own_extract/parity_report.md` (git-ignored); the numbers here are its.
Its count of returns with the same total counted, until a fix on
2026-10-24, a return GT does not hold and ours has no amount for as the
same; the 990-PF count below is corrected (204,583 to 204,580), the
Schedule I and 3b ones are corrected by the next run.

### Return tables

Over the returns both hold. GT's doubled rows are identical, so its first
row per url stands for the return.

| table | returns both hold | read columns equal on every return | all columns |
|---|---|---|---|
| basic_fields (990) | 785,185 | 29 of 29 | 163 of 164 |
| mission_statements | 785,185 | 3 of 3 | 9 of 9 |
| programs | 785,185 | 5 of 5 | 21 of 23 |
| basic_fields_pf (990-PF) | 273,504 | 32 of 33 | 113 of 137 |
| basic_fields_ez (990-EZ) | 469,853 | 16 of 16 | 88 of 95 |

On the first load the 990 had 27 of 29 and the 990-EZ 15 of 16: the foreign
address from the form body and the DBA line 2, both corrected since. What
is left:

- **990-PF `filesha256`** (28 returns, read): GT's file under that url is
  not the bytes of the IRS archive's member. Not investigated.
- **Columns production does not read.** The numbered columns GT empties when
  the element occurs once (990-PF application address, contact and phone,
  and managers, 40,656 to 73,419 returns; the 990-EZ's other organization
  type, 3,917) and the 990's activity codes for lines 4b and 4c (497 and
  334), all filled in ours by the rule above. Columns whose values differ,
  not investigated: two amounts of the 990-PF's revenue analysis (gross
  profit and net rental income, 94%), the states a return is filed in
  (990-PF 97%, 990-EZ 99.4%), the 990-EZ's foreign-office indicator (98%),
  and a few columns on fewer than 400 returns.

### Group tables

| table | returns GT lacks | rows GT lacks | amount GT lacks | on the returns both hold |
|---|---|---|---|---|
| privategrants (3a) | 53,466 | 753,933 | $39.2B | same total as `privategrants_current` on 204,580 of 206,340 returns (99.1%; the other 1,760 below); 3,338,301 of our 3,863,838 rows there have an identical GT row (86.4%); GT doubled 10,328 returns' blocks (190,555 rows twice) |
| privategrants_future (3b) | 1,111 | 11,351 | $4.0B | 108,205 of `privategrants_future_current`'s 108,219 rows identical (99.99%); same total on 99.0% of returns |
| grants_to_domestic_organizations | 25,810 | 677,379 | $120.1B | 2,086,348 of GT's 2,097,563 rows identical (99.5%); same total on 97.7%; ours $5.1B more on the same returns |
| programs_ez | 108,735 | 169,205 | | 96.1% of GT's rows identical once its doubles (16,472 returns) are set aside |
| schedule_o | 283,505 | 1,488,162 | | all 6,382,813 of GT's rows identical, the same number of notes on every return |

An identical row agrees on every column production reads. On the first load
only 70.5% of our 3a rows had one, because the filer's name line 2 had no
path. The other 13.6% are not broken down by column by the report; GT's
2025v4 foundation-status hole and its filled-in rows are among them. The
totals per return do not depend on them, and they are the measure above.

### The 990-PF returns whose totals differ

Of the 206,340 990-PF returns GT holds, 1,760 have another grant total in
ours than in `privategrants_current`. Line 25 of the return (contributions,
gifts, grants paid, column (d) or (a)) is the test, the figure the
relation's own fill rules check against. Measured on 2026-10-24;
`parity`'s `pf_total_causes` reproduces it in the 990-PF section of the
report:

| cause | returns | ours | `_current` | ours = line 25 | `_current` = line 25 |
|---|---|---|---|---|---|
| the relation keeps a later version of the filer-year | 1,301 | $2,363.5M | | 1,264 | |
| same rows, GT's amounts larger | 332 | $145.4M | $958.3M | 331 | 0 |
| GT doubled the block, not halved | 111 | $24.3M | $48.5M | 67 | 1 |
| GT doubled and filled, the rules undo part of it | 16 | $1.9M | $10.6M | 16 | 0 |

- **Same rows, GT's amounts larger.** GT's extract writes the block's
  total into the amount of a group it reads as having nothing beside its
  amount (the fill `field_fill` repairs where all the copies share one
  recipient). EIN 841384487, tax year 2023: our 24 grants add to
  $10,975,339, line 25 says $10,975,339, and GT's 24 rows add to 16 times
  that. Over the 332 returns GT is $812.8M above line 25 and above ours.
- **Doubled, not halved.** GT emits the block twice and the relation finds
  no repeated `basic_fields_pf` row to halve it by: GT's rows are exactly
  twice ours, so ours is the XML's own count; where ours is not line 25
  (44), the filing's line 25 is zero or not its 3a total.
- **A later version kept.** The relation keeps one url per filer-year,
  always a later one here. It is a return of ours with grant rows for
  1,258, with the same total as ours on 1,211 (on the other 47 GT's is 2
  times ours, once 11); a return of ours with no grant rows for 24, where
  GT keeps an empty row (no recipient, $0) for an amended return whose own
  line 25 is 0; and a return from a batch we do not read for 19, which
  stays GT's.

No return turned up where ours is wrong.

### GT defects this found

| defect | where | how much |
|---|---|---|
| Schedule I rows blanked when the recipient has a name line 2: amount, EIN, address, purpose all NULL | every tax year 2013-2025 | 44,525 rows in `grants_to_domestic_organizations_current`; in the 2024-2026 batches 10,998 rows on 3,300 returns, $3.84B of cash, 9,598 of them with a recipient EIN, every one with its grant in ours |
| numbered columns (`_0`, `_1` ...) empty when the element occurs once: the value is lost, GT having no column without a number | every 990-PF and 990-EZ with one | of the returns both hold: 990-PF application address 61,563, contributing managers 73,419, shareholder managers 40,656; 990-EZ other organization type 3,917. None read by production; the application contact is what a grant seeker needs |
| 990-PF grant amounts filled with the block's total, which `privategrants_current` keeps | 332 returns of 2024-2026 | $812.8M above line 25 |
| 990-PF blocks doubled without a repeated return row, which the relation does not halve | 111 returns, and the kept version of 47 more | $24.3M twice |
| 990-EZ program descriptions: one description repeated on every row of a return, the others lost | about 3% of 990-EZ returns (100 of 3,194 in 01A) | |
| the 2025v4 rename of a grant recipient's foundation status not followed: column NULL | every 2025v4 990-PF | 42 returns in 01A |
| Schedule I rows missing | 308 returns | 2,762 grants |
| whole batches not processed though mirrored | 2025 09A and 10A, most of them | |

Known before and seen again: the doubled 990-PF blocks, GT's synthetic
grant row from the Part XV total, the doubled 990-EZ program lists.

### The `_current` repair rules over our rows

`privategrants_current`'s rules (pair_collapse, forward_fill, field_fill,
total_row, and the version choice) exist for defects of GT's extract. Run
by production's DDL over our 4,617,771 990-PF grant rows, with our
`basic_fields_pf` as the return table:

| rule | rows | filer-years | amount |
|---|---|---|---|
| kept as they are | 4,185,512 | 256,371 | $295.9B |
| version choice (an amended return's rows win) | 397,418 | 1,700 | $2.9B |
| field_fill | 1 | 1 | $27,366 |

The superseded versions' 34,840 rows ($3.0B) drop. Our rows need only the
version choice; the rules that repair GT's defects leave them alone, one
row aside (not looked into).

### Not measured

The report measures every table now. Outside it: which columns make the
13.6% of our 3a rows without an identical GT row, and Schedule O's filer
name line 2 against GT's (its table has no index to sample by; production
does not read the column).

### Where to run it

On 2026-10-23 the first full run filled the shared RDS instance's disk with temp
files (78 to 26 GB free) and, rerun beside a cancelled query that kept
running, exhausted its 4 GB of memory: Postgres restarted and every client
on the instance lost its connection for several minutes. The code now runs
with `work_mem` at 64 MB, no parallel workers, GT's table read once per
table and every section written as it finishes. The run after that still
drove free disk from 72 to 33 GB before it was stopped on Schedule O: the
report ran in one transaction, and Postgres frees a dropped temp table's
files only at commit, so every table's stayed to the end. Each section now
commits.

With that, the run on the reloaded batches took 1 h 20 min on the shared
instance on a Friday evening, with no client sessions open: free disk went
from 67.4 GB to a low of 53.6 GB, swap peaked at 0.24 GB, free memory stayed above
2 GB. A watchdog read those three every minute and would have stopped it
below 35 GB of disk, above 0.5 GB of swap or below 0.3 GB of memory. So
`parity` (and `discover`, which also reads GT's tables in full) runs either
on a restored copy of the instance (`--host`) or on the shared one in a
quiet window agreed beforehand, under such a watchdog; `--table` limits a
rerun to the tables a change touched.

A copy is a point-in-time restore of the shared instance, and two things make
it slow. It replays every change since the night's snapshot: on 2026-10-23,
33 GB of WAL, 20 GB of it the day's loads. And it must keep the source's
300 GB: a restore at 400 GB, asked for to stripe the volume, set RDS moving
the data onto four volumes, which its documentation says can take hours;
that copy was deleted unused after 50 minutes. Restore at the same size,
the evening before.

### Against a new GT release

Since the patch (2026-10-26), production's staging tables hold our rows for
every batch we read, so `parity` on the shared instance would compare ours
with ours. A new GT release is measured on a scratch Postgres that holds a
copy of our extract and GT's release beside it, and nothing else:

```bash
# 1. a scratch instance (2026-10-28: Postgres 16 on the EC2 box, data in /dev/shm, trust on localhost)
initdb -D /dev/shm/pgparity -U postgres --auth=trust -E UTF8 --locale=C.UTF-8
pg_ctl -D /dev/shm/pgparity -l /dev/shm/pgparity.log -w start
psql -h localhost -U postgres -d postgres -c "CREATE DATABASE gt_datamart"
# 2. our extract from the shared instance (PG* variables from config.ini): one read of ~12 GB
pg_dump -Fd -j 4 --compress=lz4 --no-owner --no-privileges -n irs_extract -f /dev/shm/x
pg_dump -Fd --no-owner --no-privileges -t datamart_meta.ingest_runs -f /dev/shm/m   # -t ignores -n
psql -h localhost -U postgres -d gt_datamart -c "CREATE SCHEMA datamart_meta"
pg_restore -h localhost -U postgres -d gt_datamart -j 16 --no-owner /dev/shm/x
pg_restore -h localhost -U postgres -d gt_datamart --no-owner /dev/shm/m
# 3. GT's release, then parity there
python -m givingtuesday_datamart.own_extract gt-release 2026_10_25 --host localhost --jobs 10
python -m givingtuesday_datamart.own_extract parity --host localhost --report data/own_extract/parity_report_2026_10_25.md
```

`gt-release` (module `release`) streams each table's CSV through production's
own ingest path, so the column names, text and lineage stamps are what a
`sources refresh` would write; keeps only the rows under the returns of our
loaded batches, in the file's order; and builds the `_current` relations over
them with production's DDL. It refuses the shared instance's host. Keeping
only our batches' returns is what `parity` reads anyway; the one thing it
changes is the version a `_current` relation keeps for a filer-year whose
other versions are in a batch we do not read (19 of 206,340 990-PF returns
on 2026-10-24). Where GT's release drops or renames a column, `parity`
measures our first occurrence (`x_0`) against GT's unnumbered `x` and lists
the columns either side lacks.

### GT's 2026_10_25 release

GT published new datamarts on 2026-10-27, dated 2026_10_25: a test of
changes to its parser and concordance made after we flagged the defects
above, without the IRS's two most recent batches. Measured on 2026-10-28
as above (load 14 min, `parity` 12 min on the box); the report is
`data/own_extract/parity_report_2026_10_25.md` (git-ignored).

**Coverage.** It adds 2026's 05A, 06A and 07A in full. It holds none of
2026 05B (81,475 returns: the IRS index labels that archive 05A), 08A or
09A, and still 17% of 2025's 09A and 10A. Of the three forms in our 38
batches it lacks 209,879 returns (360,068 in June): 109,313 990s, 37,115
990-PFs, 63,451 990-EZs. With them 423,732 990-PF grants ($22.8B), 385,120
Schedule I grants ($61.3B) and 5,769 future grants ($2.2B).

**On the returns both hold**, the return tables agree as before or better:

| table | returns both hold | read columns equal on every return | June |
|---|---|---|---|
| basic_fields (990) | 858,304 | 29 of 29, and all 164 columns | 29 of 29, 163 of 164 |
| mission_statements, programs | 858,304 | 3 of 3, 5 of 5 | the same |
| basic_fields_pf (990-PF) | 305,282 | 32 of 33 (the file hash, 27 returns) | 32 of 33 (28) |
| basic_fields_ez (990-EZ) | 515,145 | 16 of 16 | 16 of 16 |

What GT fixed, of what we flagged:

| defect | June release | 2026_10_25 |
|---|---|---|
| doubled return rows (990, 990-EZ, 990-PF) | 42,482 / 16,472 / 15,859 rows | 0 / 0 / 14 |
| doubled 990-PF grant blocks | 10,328 returns | 4 |
| 990-PF grant amounts filled with the block's total | 332 returns, $812.8M above line 25 | 28 returns, $7.0M |
| doubled 990-EZ program lists | 16,472 returns | 0 |
| a lone application address, contact, shareholder manager lost (numbered columns) | 77.5% / 78.3% / 85.1% of returns agree | 99.6% / 99.7% / 99.8%: GT now writes them unnumbered |

So 990-PF grant totals agree with production's repaired relation, built
over GT's release, on 229,903 of 231,361 returns (99.4%; June 99.1%), and
Schedule O on every note but one doubled return's.

What it did not fix:

| defect | 2026_10_25 |
|---|---|
| Schedule I rows blanked when the recipient has a name line 2 | every such row: 11,913 in `_current` on 3,578 returns, $3.90B of cash, 10,414 with a recipient EIN; ours has all of them. They are 3,578 of the 3,581 returns whose Schedule I total differs from ours |
| the 2025v4 rename of a grant recipient's foundation status | NULL on all 403,703 grants of the 33,823 2025v4 990-PFs it holds (June: 42 returns; the new batches are mostly 2025v4) |
| one 990-EZ program description repeated on every row | 15,683 returns, 41,683 rows |
| a lone contributing manager (990-PF) and other organization type (990-EZ) lost | 81,942 and 4,311 returns: GT still numbers these |
| 2025 09A and 10A mostly unprocessed | 17.7% and 17.1% held |
| Schedule I rows missing | 331 returns (308) |

New in this release: the American Online Giving Foundation's 2024 Schedule
I (44,583 grants, $1.90B) is doubled, and the `_current` relation's dedup
keeps 50,725 rows of the 89,166; the AmazonSmile Foundation's 2023v6
990-PF (363,675 grants) has the relationship NULL on every row, where the
XML says NONE; and empty grant groups (no recipient, $0) are dropped:
on 329 returns GT has 1,232 fewer rows than ours, with the same total on
324 of them.

Two gaps on our side came out of it, both closed the same day. GT writes a
foreign recipient's address into the 990-PF grant tables' address columns,
which production reads; our concordance took the US address only (57,655
grants on 10,811 returns where GT had an address and ours none, 57,594 of
them with a foreign country in ours). And 49 returns written with an `irs:`
namespace prefix had no group rows in `irs_extract` (230 Schedule O notes,
22 990-EZ programs, 3 Schedule I grants of $566,494, 2 990-PF grants of
$4,402): the loader's form scan missed them until the fix of 2026-10-26,
which came after the batches were last loaded (a re-parse of all 38
batches found these and no others).

The concordance now follows GT's foreign-address convention (*The
concordance*), and `own_extract reload` loaded all 38 batches again on the
scratch copy, from our copies of the archives (8.5 minutes): every foreign
grant matched to GT's row (56,104 in 3a, 8,487 in 3b) has the same five
address columns; our 990-PF rows with an identical GT row went from
3,371,404 to 3,425,313; the 49 returns have their 257 rows, and GT has no
rows under our returns that ours lacks beyond its empty and total-only
990-PF rows. Nothing else moved. The row share with an identical GT row is
otherwise set by GT: 990-PF grants 81.7% (June 86.4%: the foundation-status
rename now over many more returns, and AmazonSmile), Schedule I 99.5% of
GT's rows once the doubled return is set aside (99.5%), future grants all
114,880. Production still holds the rows of the earlier concordance (*A
concordance change*, below).

## Checks that need no GT

GT's tables are a second parser: where they agree with ours, two parsers
read the XML alike, and where they do not, the XML decides. They have
caught our mistakes (the first load's 34 sibling paths, the foreign
addresses and the 49 `irs:` returns above) as often as theirs. But they
prove nothing where we follow GT's conventions, where both parsers miss an
element, or for a return GT does not hold: 209,879 of our batches' returns
in its 2026_10_25 release, and every new batch until GT catches up. Two
checks lean on nothing but the filings:

```bash
python -m givingtuesday_datamart.own_extract checks [--batch ...] [--host HOST]   # data/own_extract/checks_report.md
python -m givingtuesday_datamart.own_extract audit [--jobs N] [--host HOST]       # batches loaded before load counted
```

**Totals the filings state** (module `checks`). A 990-PF's grant rows add
up to line 25 of Part I, column (d) or (a). A 990's Schedule I lists every
grant of more than $5,000 to a domestic organization, so its cash and
non-cash amounts add up to no more than Part IX line 1, column (A). Each
return is classified from `irs_extract` alone, a batch at a time; `load`
prints each batch's rates, and the report lists the rates by batch and by
schema version, any batch 2 points below the median of the others, and the
largest gaps to sample against the XML. A return that fails is not our
error by default: grants listed in an attachment, a line 25 that is not the
grant list, a filer's slip. A batch that falls behind the others is the
mark of a parse that went wrong in it.

**Elements no concordance path reads** (module `audit`, table
`irs_extract.unread_paths`). Every leaf element of a parsed return that no
path reads, where it sits inside a group a table reads or beside an element
a return-level table reads, counted by batch, form, schema version and
path, with the archive's and the concordance's SHA-256. `load` counts them
as it parses; `audit` re-reads batches loaded before, from our copy of each
archive, checked against the SHA-256 its load stamped. The report lists,
from 0.5% of a form's returns: elements inside a group, elements with the
same name as a read one beside them but for the type suffix (`...Txt` to
`...Cd`: a rename), and elements first seen in a later schema version.

First run, 2026-10-28, on the scratch copy of the extract (all 38 batches;
the audit 8.5 minutes, the report 11 seconds; 126,309 rows, 687 paths):

| check | returns it applies to | passing | lowest version, batch |
|---|---|---|---|
| 990-PF grants against line 25 | 263,734 | 255,648 (96.9%) | 2023v4 93.9%; 2026 03A 94.9% |
| Schedule I against Part IX line 1 | 137,300 | 134,074 (97.7%) | 2020v4 92.0% (125 returns); 2024 12A 97.0% |

No batch is 2 points below the others. The rates are the same where GT
holds the return and where it does not (990-PF 96.9% and 97.1%), so ours
do not lean on GT's. Of the 990-PFs that fail, 3,928 state a line 25 and
list no grant: the largest state $2.0B, $1.8B and $1.4B.

The audit lists, inside the 990-PF grant groups, the recipient's foreign
address (line 1 on 61,612 grants of 12,025 returns, city, postal code,
province): the gap GT's 2026_10_25 release showed the same morning, found
without it. It lists no rename. Run over 2026 09A with the concordance as
it stood before the 2025v4 rename was found (the status read as
`RecipientFoundationStatusTxt` only), it lists `RecipientFoundationStatusCd`
beside it on 3,356 of the batch's 4,734 2025v4 990-PFs, so it would have
caught that rename without GT. The elements first seen in a later version
are header indicators no table needs (`IRSResponsiblePrtyInfoCurrInd`, from
2022v5 on 86% of 990s); nothing new inside a group in 2025v4. Once the
concordance took the foreign address and the batches were reloaded, the
list inside groups is empty.

What neither check sees: a value read into the wrong column with the
totals still right, and an element moved under a parent nothing reads (the
audit sees it only as new in its version). A hand-checked sample of returns
per form and schema version, kept as test fixtures, would cover the first.

## The patch, ours first

Decided on 2026-10-24: for every batch we read (2024 on), our rows replace
GT's in production's staging tables; GT's stay for the years before. What
it adds is the table "What GT does not have" above. What it changes on the
returns GT holds is the grants of about 5,700 of them (the 990-PF returns
above, Schedule I's blanked and missing grants); the return tables,
missions, programs and Schedule O agree on every column production reads.

**What `own_extract patch` does.** After `sources refresh` (which recreates
each staging table from GT's release), for each of the ten staging tables,
in one transaction:

1. the patch's own earlier rows (`_source_version` `irs:%`) and GT's rows
   under a url our table holds go;
2. ours go in, in group order, stamped `_source_version = 'irs:<batch>'`,
   `_source_url` the IRS archive, `_ingest_run_id` the batch's load run
   (which records the archive's and the concordance's SHA-256) and
   `_ingested_at` the patch's time;
3. a run per table goes to `datamart_meta.ingest_runs` (logical name
   `irs_extract_patch`), with GT's release it sat on and the counts.

Then the basic-fields `_current` relations are rebuilt, as a refresh
rebuilds them. The patch refuses a batch loaded with another concordance
than the file's.

**Url by url within a table.** Where our table has no row for a return GT
has rows for, GT's rows stay. On 2024-2026 that is 27,321 990-PF grant rows
under 24,925 returns, nearly all the empty row GT writes for a return that
lists no grant ($7,402 in all), and 11 990-EZ program rows under 8
returns; with them the `_current` version choice reads such a filer-year as
it does today (the 24 amended returns above keep their empty row).
Schedule I and 3b have no such rows, and no GT row under a return of ours
sits in another form's table, so no return is counted twice.

**What the relations do with it**, on the patch run over a scratch copy of
759 filers, every year, with production's DDL (`prove-patch`, 2026-10-24):
no url holds both GT's rows and ours, a second run lands on the same
tables, and the relations apply only their version choice to our rows
(`latest_url`, and Schedule I's `amend_distinct` for amended filings). Of
the sample's 990-PF filer-years, 990 of 1,009 have a total equal to line
25, against 905 of 943 before; its 990-PF grants go from $1,809M to $993M
(GT's filled and doubled amounts), its Schedule I grants from $2,205M to
$2,503M (the blanked and missing grants). The basic-fields relations keep
the same filing on every filer-year both sides hold but 4, where the patch
brings an amended return GT never loaded.

**Physical order.** Our rows go in in group order, but a row that fits the
space left at the end of an earlier page lands there (22 of the sample's
1,006 filings with groups). The relations read that order only to choose
among copies of one row, or among rows of one recipient and one amount
(`field_fill`, which took one of our 4.6 million 990-PF rows), so at most it
decides which status or purpose text a kept row shows.

**In what order, on production.** `parity` reads GT's release, so it runs
before the patch: afterwards staging holds our rows under our urls.

1. `own_extract parity`, in a quiet window agreed beforehand (*Where to run
   it*);
2. `own_extract patch`: 16,662,030 of GT's rows out, 20,109,675 of ours in,
   about 11 GB more on disk until a vacuum makes the deleted rows' space
   reusable;
3. `sources build-canonical`;
4. the rest of *The next GivingTuesday drop*
   (`docs/placeholder_recovery_operations.md`), steps 3 to 8: the grants
   `_current` relations, so the work list reads the patched rows; the work
   list, which gains the placeholder filings of the returns GT never had;
   the read of the filings not yet read, under `v3`, on the box and on an
   explicit go, since it buys model calls; the load, the view and the
   checks; the matcher (about eight hours on the big box) and its
   regression gate.

The placeholder lists already loaded keep their place: the view puts a
loaded list in place of the placeholder row of the url
`privategrants_current` holds, and the patch keeps every url, and for
every loaded list in our batches our rows hold the placeholder row.

Monthly, as the IRS posts a batch: `own_extract load <year> --batch
<new>`, then steps 2 to 4. When GT's next release lands, `sources refresh`
replaces the staging tables and drops the patch; step 2 puts it back.

**A concordance change.** A new concordance gives every batch a new stamp,
and `patch` refuses batches loaded under different ones, so all of them are
loaded again, then patched again:

1. `own_extract reload`: every loaded batch from our copy of its archive,
   checked against the SHA-256 its first load stamped, nothing fetched
   from the IRS; it prints each batch's checks. A batch's rows are
   replaced in one transaction, so 12 GB of dead rows wait for a vacuum
   unless `irs_extract`'s tables are truncated first (2026-10-23);
2. `own_extract patch` (snapshot first): our earlier patch rows out, the
   new ones in, the basic-fields relations rebuilt; then a vacuum of the
   ten staging tables;
3. the grants `_current` relations (`current_grants --only` each), then
   `sources build-canonical`, then the matcher and its gate. The matcher
   run is the one already due.

The foreign-address change of 2026-10-28 touches the 990-PF grant
relations (an address on 61,612 more grants, the ZIP column now holding a
foreign postal code where there is one, as GT's rows had before the patch)
and, through the 49 `irs:` returns, Schedule O and the 990-EZ programs.
The return tables do not change.

**Undo.** `sources refresh --force` of the ten sources loads GT's release
again and rebuilds the basic-fields relations; the grants relations and the
matcher as in step 4.

It breaks one invariant on purpose: staging is no longer byte-faithful to
GT's file. The lineage stamp keeps the two apart.

## What doing this ourselves would cost us

What we would own, and what it changes downstream, if our extract became
the source rather than a check.

**The mapping, every year.** The concordance is ours to keep. `discover`
found it on returns GT also holds; once we stop waiting for GT there is no
overlap to search for a new schema version's renamed elements. The IRS's
public schema packages stop at 2024v5.0 (later ones go to registered
e-file providers only), and 2025v4.x returns are already in the 2026
batches. What keeps this cheap: element names have barely moved since the
2013 rewrite, and new returns only use the newest versions. What it needs:
a check at every load for paths that appear under a mapped group or form
part and map to no column, so a rename shows up as a list rather than as
quietly empty columns.

**GT's rules we have to keep, GT's defects we stop inheriting.** Two GT
rules are in the concordance (above). Everything else parity finds is a GT
defect: doubled blocks, the synthetic grant row from the Part XV total,
the field fills, reordered rows, the doubled 990-EZ program lists, the
blanked Schedule I rows, and whole batches GT never processed. The
`_current` repair rules exist for those defects and should find nothing to
do on our rows; that has to be shown on our rows before they feed
production (a scratch `_current` build over them, counting each rule's
hits).

**Downstream assumptions.**

- `MAX(url)` picks the latest version in `current_grants`. Our rows carry
  GT's url pattern, so the ordering holds across both sources.
- `current_grants` uses `ctid` as "the extract's order" (copy rank, the row
  `field_fill` keeps). Our rows are written in group order, so `ctid` order
  is document order; `group_ordinal` says it outright.
- The frontend's filing link fetches the XML from the url, which is GT's
  mirror. For returns GT never mirrored (2026 05B, 08A, 09A) the link would
  404. Our archive holds the batches, not per-file XML: serving a filing
  means either a per-file copy in our bucket (about 470,000 objects for
  2026) or a route that reads the member out of the archive.
- The matcher's input changes (new returns; and, where our rows replace
  GT's, Schedule I rows that now carry an EIN and an amount). That is a
  rerun on the big box, the corrections preflight and the regression gate,
  and the gate's baselines move.

**Operations.** A month is one archive of 200 to 500 MB (June was two of
500 MB each), a few minutes to fetch, parse and load from a laptop; the
database grows by about 6 KB per return (69 MB for 01A's 12,245 returns).
Info-ZIP `unzip` must be on the machine for the Deflate64 archives. Every
archive is copied to our bucket, because the IRS has replaced archives
under the same name and GT's mirror is neither complete nor current.

**History.** We read 2024 to 2026 only. Before 2013 the element names are
different throughout (the concordance would need a second set of paths),
and the older batches are not named in the index before 2024.
