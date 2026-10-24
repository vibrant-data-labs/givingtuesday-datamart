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
  IRS XML the way GT has them: 27 of the 29 columns production reads on the
  990 (785,185 returns), 32 of 33 on the 990-PF (273,504), 15 of 16 on the
  990-EZ (469,853), all of missions and programs. The misses are GT rules
  since added to the concordance. On the grants, our 990-PF rows give the
  same total per return as production's repaired `privategrants_current` on
  99.1% of returns, and 99.99% of `privategrants_future_current`'s rows; 99.5%
  of GT's Schedule I rows have an identical row of ours.
- **The newest schema.** 71% of the returns GT never released are filed
  under 2025v4. On the 73,125 2025v4 returns GT does hold, the 990-PF return
  table agrees on all 33 read columns; one IRS rename (a grant recipient's
  foundation status) is now in the concordance, and GT's extract misses it.
- **What GT is missing.** In the 2024-2026 batches: 182,432 Form 990s,
  68,893 990-PFs and 108,743 990-EZs, 98% of them for tax year 2024 or
  later; with them 753,933 private-foundation grants ($39.2B), 677,379
  Schedule I grants ($120.1B) and 11,351 grants approved for future payment
  ($4.0B).
- **New GT defect found.** GT blanks every Schedule I grant whose recipient
  has a second name line ("HARVARD UNIVERSITY" / "SCHOOL OF DENTAL
  MEDICINE"): the amount, recipient EIN, address and purpose are all NULL.
  That is 44,525 rows in `grants_to_domestic_organizations_current`, tax
  years 2013 to 2025, every such row GT has. In the first two 2026 batches
  alone that is 297 grants and $28.8M of cash, 259 of them with a recipient
  EIN GT dropped.
- **Recommendation.** Patch now: put our rows in for the returns GT does
  not have (the design is below), run the `_current` rebuilds and the
  matcher, and keep GT's rows wherever GT has the return until we decide
  whether ours replace them for 2024 on. Measure on a copy of the database,
  never on the shared instance (below).

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
| `discover` | proposes the concordance: for each GT column, the xpath that reproduces it on the most returns both sides hold |
| `parity`, `report` | measure `irs_extract` against GT's tables, in SQL, and write the report below |

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
```

### What was loaded

Every archive the IRS serves for 2024, 2025 and 2026, loaded on
2026-10-23, each once (the run records in `datamart_meta.ingest_runs` and
the generated report's batch table carry each archive's Last-Modified and
SHA-256):

| year | archives | Form 990 | 990-PF | 990-EZ | schema versions (share of returns) |
|---|---|---|---|---|---|
| 2024 | 12 | 363,100 | 126,982 | 215,355 | 2023v5 68%, 2022v5 22%, 2023v4 4%, older 6% |
| 2025 | 16 | 376,920 | 130,347 | 217,102 | 2024v5 75%, 2023v6 22%, older 3% |
| 2026 | 10 | 227,597 | 85,068 | 146,139 | 01A-04A: 2025v4 51%, 2024v5 47%, 2023v6 2%; 05A-09A: 2025v4 71%, 2024v5 26%, 2025v6 1% |

2026's count is the IRS index's exactly (472,357 returns of every form,
990-T included). In the database the extract is 11.8 GB, Schedule O 4.4 GB
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

Two corrections to what the search proposed, both measured by `parity` on
every loaded batch: 34 columns GT left empty on every return of 01A had no
path, and take their sibling's (name line 2 beside line 1, the foreign
address beside the US one; 11 stay unmapped, none read by production); and
GT's numbered columns (`_0`, `_1` ...) are the n-th occurrence of one
element, where the search had taken occurrence 1 for some `_0`s.

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
| basic_fields (990) | 22,388 | 27 of 29: DBA line 2 empty in ours on 30, foreign country on 22 |
| basic_fields_pf (990-PF) | 15,215 | 33 of 33 |
| basic_fields_ez (990-EZ) | 35,522 | 15 of 16: foreign country empty in ours on 7 |
| mission_statements, programs | 22,388 | all |

Across every mapped field of the group tables, batch 07A shows one more
shift between 2024v5 and 2025v4 with no new element beside it: Schedule I's
valuation method and non-cash description (about 80% filled to 7-10%), so
2025v4 lets a filer leave them out; neither is read by production.

## Parity with GT

Measured on 2026-10-23 by `own_extract parity` over all 38 batches (the
concordance as loaded, SHA-256 `a6523dd0969c`; the fixes found by this run
are in the file since and apply from the next load). The report lands in
`data/own_extract/parity_report.md` (git-ignored); the numbers here are its.

### Return tables

Over the returns both hold. GT's doubled rows are identical, so its first
row per url stands for the return.

| table | returns both hold | read columns equal on every return | all columns |
|---|---|---|---|
| basic_fields (990) | 785,185 | 27 of 29 | 157 of 164 |
| mission_statements | 785,185 | 3 of 3 | 9 of 9 |
| programs | 785,185 | 5 of 5 | 23 of 23 |
| basic_fields_pf (990-PF) | 273,504 | 32 of 33 | 111 of 137 |
| basic_fields_ez (990-EZ) | 469,853 | 15 of 16 | 83 of 95 |

The misses, each explained:

- **990 foreign address** (`filerforctry` and its siblings, 420 to 722
  returns): GT fills them from the form body's foreign address when the
  header has none, the counterpart of the territory rule. In the
  concordance now.
- **990 DBA line 2** (`dbanbnline22`, 702 returns) and **990-EZ foreign
  address** (170): columns GT left empty on every return of 01A, so the
  search had nothing to find; 34 such columns now take their sibling's path
  (11 are left unmapped, none read by production).
- **990-PF `filesha256`** (28 returns): GT's file under that url is not the
  bytes of the IRS archive's member. Not investigated.
- Columns production does not read: GT's numbered columns (`_0`, `_1`) are
  the n-th occurrence of an element and the search took the wrong one for
  some `_0`s (fixed); a state list (`streacstfiwi`, 97%) and two rental
  income amounts (94%) GT reads differently, left as they are.

### Group tables

| table | returns GT lacks | rows GT lacks | amount GT lacks | on the returns both hold |
|---|---|---|---|---|
| privategrants (3a) | 53,466 | 753,933 | $39.2B | same total as `privategrants_current` on 204,583 of 206,340 returns (99.1%); GT doubled 10,328 returns' blocks (190,555 rows twice) |
| privategrants_future (3b) | 1,111 | 11,351 | $4.0B | 108,205 of `privategrants_future_current`'s 108,219 rows identical (99.99%); same total on 99.0% of returns |
| grants_to_domestic_organizations | 25,810 | 677,379 | $120.1B | 2,086,348 of GT's 2,097,563 rows identical (99.5%); same total on 97.7%; ours $5.1B more on the same returns |
| programs_ez | 108,735 | 169,205 | | 96.1% of GT's rows identical once its doubles (16,472 returns) are set aside |

Only 67-70% of GT's 3a rows have an identical row of ours, because the
comparison includes the filer's name line 2, which had no path when the
batches were loaded (18.7% of our 3a rows come from a foundation with one);
the doubled blocks and GT's fills account for the rest. The totals per
return, which do not depend on the name, are the measure above.

### GT defects this found

| defect | where | how much |
|---|---|---|
| Schedule I rows blanked when the recipient has a name line 2: amount, EIN, address, purpose all NULL | every tax year 2013-2025 | 44,525 rows in `grants_to_domestic_organizations_current`; 297 rows / $28.8M in 2026 01A-02A alone |
| 990-EZ program descriptions: one description repeated on every row of a return, the others lost | about 3% of 990-EZ returns (100 of 3,194 in 01A) | |
| the 2025v4 rename of a grant recipient's foundation status not followed: column NULL | every 2025v4 990-PF | 42 returns in 01A |
| Schedule I rows missing | 308 returns | 2,762 grants |
| whole batches not processed though mirrored | 2025 09A and 10A, most of them | |

Known before and seen again: the doubled 990-PF blocks, GT's synthetic
grant row from the Part XV total, the doubled 990-EZ program lists.

### Not measured

The run was stopped on Schedule O (its temp tables were filling the shared
instance's disk; see the next section), before three measures: Schedule O
itself (4 columns read by production), the Schedule I name-line-2 rows
priced over all batches, and `privategrants_current`'s repair rules run
over our rows. All three need a copy of the database.

### Where to run it

On 2026-10-23 the first full run filled the shared RDS instance's disk with temp
files (78 to 26 GB free) and, rerun beside a cancelled query that kept
running, exhausted its 4 GB of memory: Postgres restarted and every client
on the instance lost its connection for several minutes. The code now runs
with `work_mem` at 64 MB, no parallel workers, GT's table read once per
table and every section written as it finishes. The run that measured the
numbers above still drove free disk from 72 to 33 GB before it was stopped.
So `parity` (and `discover`) read every GT staging table in full and belong
on a restored copy of the instance (`--host`), not on the shared one.

A copy is a point-in-time restore of the shared instance, and two things make
it slow. It replays every change since the night's snapshot: on 2026-10-23,
33 GB of WAL, 20 GB of it the day's loads. And it must keep the source's
300 GB: a restore at 400 GB, asked for to stripe the volume, set RDS moving
the data onto four volumes, which its documentation says can take hours;
that copy was deleted unused after 50 minutes. Restore at the same size,
the evening before.

## The patch for recent filings

What it would add is the table "What GT does not have" above: every row
of ours under a url GT's table lacks.

**How it would reach production.** Not built; this is the design. The
`_current` builders read `public.<table>` by name and lean on `ctid` (the
extract's order), so a view over GT's rows and ours cannot stand in for the
staging table, and a combined copy of each staging table would need another
50 GB on an instance with about 60 GB free once the extract is loaded. The
patch that fits both:

1. after `sources refresh` (which recreates each staging table from GT's
   release), `own_extract patch` appends to each `public.<table>` the rows
   of `irs_extract.<table>` whose url the table lacks, in group order, with
   the lineage columns stamped `_source_version = 'irs:<batch>'`,
   `_source_url = <IRS archive url>` and the load's `_ingest_run_id`, so
   every patched row says where it came from and `DELETE ... WHERE
   _source_version LIKE 'irs:%'` undoes it;
2. the `_current` rebuilds, the canonical build and the matcher run as in
   *The next GivingTuesday drop*, unchanged.

When GT's next release lands, `sources refresh` drops the patch with the
old staging table, and the patch step adds back only what GT still lacks.
Monthly, between GT releases, it is the same two steps after
`own_extract load <year> --batch <new batch>`.

It breaks one invariant on purpose: staging is no longer byte-faithful to
GT's file. The lineage stamp keeps the two apart.

**GT first or ours first.** The design above keeps GT's rows wherever GT
has the return and adds ours only where it does not, so every number
clients already see stays where it is and the matcher's baseline moves only
by the new returns. The alternative, ours for every batch we read (2024 on)
and GT's for older years, also repairs GT's defects in those years (the
blanked Schedule I rows above), at the price of moving numbers clients
have already seen and a larger matcher diff to review.

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
