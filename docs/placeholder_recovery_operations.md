# Placeholder recovery: operations

Vibrant Data Labs, September 25, 2026; updated September 28 and 29. How the placeholder-recovery
pipeline is run and what it writes: the five tables and the view, the
policies, the one command, how to watch and stop a run, and what a run
costs. The
findings are in [placeholder_grant_recovery.md](placeholder_grant_recovery.md);
the engineering log, with the measurements behind every choice here, is
[placeholder_recovery_pipeline.md](placeholder_recovery_pipeline.md).
This document absorbed the storage-layer spec and the EC2 runbook of
September 22 to 24, both in git history.

## The shape

Everything a run produces lands in S3 or in five datamart tables, so a
laptop and a box share state through them and nothing is copied between
machines. Every stage reads what the tables lack and buys only that, so
the same command run again resumes, a completed stage makes no model
call, and a change of rule re-derives from stored readings for free.

| table | one row per | key | written by |
|---|---|---|---|
| `pf_placeholder_filings` | filing to fetch and read: the work list | `object_id` | `placeholder_recovery.work_list.rebuild` |
| `filing_images` | filing | `object_id` | `filing_images.fetch_filings` |
| `page_readings` | page read by one model under one prompt and one request setting | `(object_id, page, image_sha256, dpi, model, prompt_version, request_hash)` | `page_readings.read_pages` |
| `page_verdicts` | page decided under one policy | `(object_id, page, image_sha256, policy_version)` | `page_verdicts.agree` |
| `privategrants_recovered` | grant of a list that adds up, under one policy | `(object_id, policy_version, target, page, row_ordinal)` | `placeholder_recovery.loader.load` |

Consumers read one view, `privategrants_current_w_recovered`:
`privategrants_current` with the recovered grants in place of the
placeholder rows they replace. It shows grants paid. A future-payment
list is loaded into the table, marked `future`, and is not in the view.

Two relations of the datamart feed the work list, and neither is
written here: `privategrants_current`, the paid rows, and since
September 29 `privategrants_future_current`, the rows approved for
future payment, from GivingTuesday's `990PFPart14Grants3B` (source
`irs_990pf_grants_future`). See *The future amount* below.

The first and the last table, the view and the commands that write them
are the package `givingtuesday_datamart/placeholder_recovery/`, where
the pipeline's production code goes from September 28 on. The three
tables between them are modules of their own, and the frame's commands
are still in `exploratory/`.

The PDFs sit in `s3://givingtuesday-datamart/irs/pdf/<object_id>.pdf`,
kept indefinitely, with the SHA-256 on the row and as the S3 checksum.
Rendered pages (`<cache>/pages200/<object_id>/pNNN.png`) are a local
cache, not stored: one render on the box serves every reader.

## The tables

**`pf_placeholder_filings`.** The work list, one row per placeholder
filing: `object_id` (the 18 digits in the filing's url), `filerein`,
`filer_name`, `taxyear`, `taxperend`, `declared_paid` (grants paid, Part
I line 25 column (d), in cash), `declared_books` (the same line, column
(a), per books), `placeholder_paid` (the amounts on the placeholder
rows, summed; the target a list must add up to), `placeholder_rows`,
`placeholder_texts` (the first two), `placeholder_future` (the amounts
on the filing's placeholder rows of line 3b, in
`privategrants_future_current`, summed; zero with none; the target a
future-payment list must add up to) and `placeholder_future_rows`,
`band` (A at $100M and over, B at
$10M, C at $1M, D below, on `placeholder_paid`),
`filer_marked_individual`, `placeholder_exceeds_declared` (the
placeholder amount passes both columns of line 25 by more than 0.5%),
`classifier_version`, `source_version` (the `_source_version` of the
grant rows it was built from) and `built_at`.
A filing is on the list when its placeholder rows sum to more than zero
and to at least half of `declared_paid`. The future amount decides
nothing about the list; it is read from the rows under the url the paid
rows have, so it never comes from another version of the return. The
command also prints how many filings have a future placeholder and are
not on the list. The command replaces the rows
in one transaction, about a minute and a half for 24,518 filings. The
filers left out are in `data/placeholder_recovery/exclusions.csv`, one
row each with EIN, name, evidence and date, tracked as plain text so a
pull request shows the row added.

**`filing_images`.** The fetch outcome for a filing: `filerein`,
`taxyear`, the IRS index year (NULL for a filing no index lists, found
by the frame's EIN and tax period: the IRS publishes an index from 2017
on) and TEOS URL, the date the IRS generated
the image (for the 404 report), `status`, `attempts`, `last_error`,
`fetched_at`, `s3_key`, `sha256`, `bytes`, `pages`, `page_widths` (from
`pdfimages -list`, one per page), `attachment_from` (the first page the
IRS did not render, by the widths of the renderer the first page names,
`irs_source.RENDERED_WIDTHS`; NULL when the filer attached nothing) and
`attachment_pages`. `filing_images recut` cuts every fetched row again
from its stored widths after a change to the rule and writes the rows
that differ, no PDF opened; a filing with readings keeps its cut. Statuses: `fetched`; `no_teos_image` (TEOS lists no
image, permanent: an image that appears later is a `refetch`, not a
retry); `pdf_unavailable:<http code>` and `not_a_pdf`; and three that
are ours, `teos_failed:<exc>`, `widths_failed:<exc>`, `upload_failed:<exc>`.
Every failure but `no_teos_image` is retried until it has three
attempts, because the 2022 404 batch may come back. A fetched row that
fails a re-fetch keeps its status and its object; a re-fetch that
returns a different hash updates the row, and the old hash's readings
stay untouched.

**`page_readings`.** One model's reading of one page: the request it was
asked with (`request`, whose hash is the key's `request_hash`: the JSON
mode and the extras such as reasoning effort), the parsed `response`
(page kind, heading, rows, totals), whether the answer was `partial`
(salvaged from a cut-off response), `usage` (tokens in and out, summed
across every attempt from 2026-09-23), `finish`, `attempts`,
`json_mode`, `seconds`, and for a failed read `errors` and `last_error`
with `response` NULL. A page at three errors is skipped by later runs
and listed; a reader that hits its limit on a page is absent for that
page, and the page goes through the dispute path. Readings are keyed on
the image hash, so a re-fetched PDF never inherits readings of a
different image, and on the request settings, so a policy that pins a
different reasoning effort reads the page again rather than reusing
the other setting's answer.

**`page_verdicts`.** What the readers settled on for a page under a
named policy: `verdict` (`agreed`, `escalated`, `flagged`, `not_a_list`,
`unreadable`), `accepted_model` and `accepted_hash` (the reading that is
loaded), `matched_models` (the readings that agreed with it),
`readers_consulted` and `decided_at`. A flagged page carries Sonnet's
reading as accepted under the `load_single` rule; the mark is the
verdict. A `not_a_list` page (`POLICY_V3`) names no reading and its
`matched_models` are the base pair, who agreed there is nothing on it.
Verdicts under `<version>-<rule>` are the same policy with the flagged
rule overridden, derived without a model call.

**`privategrants_recovered`.** One grant of a list that adds up within
0.5%, under one policy: a paid list to the filing's `placeholder_paid`,
a future-payment list to its `placeholder_future`. The two are searched
for apart, and a filing can load either or both. A list that adds up
only to the two amounts together is counted by the load and not
written.

| group | columns |
|---|---|
| key | `object_id`, `policy_version`, `target` (`paid` or `future`), `page`, `row_ordinal` |
| content, as read | `recipient_name`, `recipient_address`, `recipient_status`, `purpose`; `amount`, numeric with cents |
| for the matcher | `match_name` (the name, less a place printed at its end), `match_address` (the address, less the state and zip at its end), `state`, `zip5`, `state_source` (`address`, `name`, or NULL with no state) |
| labels | `page_kind` (the reader's label for the row's page), `page_verdict` (`agreed`, `escalated`, `flagged`), `filer_marked_individual`, `placeholder_exceeds_declared` (always false on a future row: line 25 states grants paid) |
| lineage | `filerein`, `taxyear`, `image_sha256`, `dpi`, `prompt_version`, `accepted_model`, `accepted_hash`, `declared_amount` (the amount the list reconciled against), `reconciliation_error`, `work_list_source_version`, `loaded_at` |

`row_ordinal` is the row's index in the accepted reading's `rows`, from
0. So a loaded row joins to its reading on `(object_id, page,
image_sha256, dpi, accepted_model, prompt_version, accepted_hash)`, to
its own line there as `response->'rows'->row_ordinal`, and to its
verdict on `(object_id, page, image_sha256, policy_version)`. A load
buys nothing. For each filing it compares the rows the rule gives today
with the rows stored: equal rows are left alone, with their `loaded_at`;
anything else is deleted and written again in one transaction a filing.
A full load also takes out the rows of a filing that left the work
list.

**`privategrants_current_w_recovered`.** `privategrants_current`'s
28 columns under their own names, then `row_source`
(`privategrants_current` or `placeholder_recovery`), the labels,
`state_source`, and the recovered row's key (`recovered_object_id`,
`recovered_policy_version`, `recovered_page`, `recovered_row_ordinal`).
`placeholder_exceeds_declared` is the view's last column: it was added
on September 29, and a view replaced in place takes new columns at its
end.

- A loaded filing's placeholder rows are left out. They carry the whole
  amount in `privategrants_current`, so beside the recovered rows they
  would count the dollars twice. Grants the filer named in the form
  itself stay.
- A recovered row puts `match_name` in `sigocpyrbnbn1`, `match_address`
  in `sigocpyrfaal1`, `state` in `sigocpyrfapo`, `zip5` in
  `sigocpyrfapc`, the status in `sigocpyrfsta`, the purpose in
  `sigocpypogoc` and the amount in `sigocpyamoun`. The columns that
  describe the filing (filer name, period, url, source version) are
  those of the placeholder row it replaces. So a list shows only while
  the filing it was read from is the version `privategrants_current`
  holds.
- The view shows rows loaded with target `paid`. Rows loaded as
  `future` are grants approved, not paid, and stay in the table.
- The view shows the rows of one policy, written into its definition.
  Only the `view` command changes which. A load under another policy
  leaves the view alone and says so.
- `current_grants` rebuilds `privategrants_current` with `DROP ...
  CASCADE`, which takes the view with it. `load` creates it again when
  it is gone, and so does `view`. The matcher does not read the view
  yet.

## The future amount

Grants approved for future payment are a file of their own in
GivingTuesday's catalog, loaded like any source and then reduced to one
version of each return. In this order, since each step reads the one
before:

```bash
python -m givingtuesday_datamart.sources refresh --source irs_990pf_grants_future
python -m givingtuesday_datamart.current_grants --only privategrants_future_current
python -m givingtuesday_datamart.placeholder_recovery work-list
python -m givingtuesday_datamart.placeholder_recovery load --policy v2
python -m givingtuesday_datamart.placeholder_recovery check --policy v2
```

| step | what it writes | measured, 2026-09-29 |
|---|---|---|
| `sources refresh` | `public.privategrants_future`, as published, with an index on `filerein` | 459,866 rows, 290 MB, 81 seconds; a default refresh takes it too |
| `current_grants --only` | `public.privategrants_future_current` | 441,358 rows, 298 MB, about 90 seconds |
| `work-list` | `placeholder_future` and `placeholder_future_rows` on every filing of the list | 1,104 filings with an amount, $7.10B; 86 seconds |
| `load` | rows with `target = 'future'` beside the paid ones | 71 filings, 3,873 rows on the frame |

`privategrants_future_current` is built in `current_grants.py` after
`privategrants_current`, which it reads, and every full rebuild of the
`_current` relations builds it in that order. `--only` builds it alone
and touches nothing else: `privategrants_current` is not rebuilt, so
the view is not dropped. Its rules: the latest url of a filer-year; no
rows of a filer-year whose paid rows are held under a later url; a
doubled block halved when every row of it is paired and the filing's
own row in `basic_fields_pf` is repeated, which is what GivingTuesday's
doubled batches do to every extract at once, and what an amended copy
stamped with the original url does too (315 filings). It reads
`privategrants_current` for the url only. The
evidence is in the findings doc, *Future-payment lists*, and in the
module's docstring. Since September 30 the paid and the future relation
are built from one definition (`current_grants._pf_current_ddl`), and the
docstring states the rule once and then the three things the two differ
by.

## Rebuilding `privategrants_current` after a rule change

The rule of `privategrants_current` changed on September 30 (the doubling
repairs: `docs/pf_doubling_basic_row_test.md`, section 6). A filer-year is
now also halved when the filing's row in `basic_fields_pf` is repeated,
whatever line 25 says, and a forward-filled block (one grant in the table
N times, line 25 equal to one copy) keeps one row and carries
`forward_fill`. The change was proved on scratch copies. The production
table holds the old rule's rows until it is rebuilt, and nothing below
has been run.

In this order, since each step reads the one before:

```bash
# 1. the paid relation: Zein runs or approves it. About 22 minutes.
python -m givingtuesday_datamart.current_grants --only privategrants_current
# 2. the future relation, which reads the paid one. About 2 minutes.
python -m givingtuesday_datamart.current_grants --only privategrants_future_current
# 3. the work list, the load, the checks
python -m givingtuesday_datamart.placeholder_recovery work-list
python -m givingtuesday_datamart.placeholder_recovery load --policy v2
python -m givingtuesday_datamart.placeholder_recovery check --policy v2
```

| step | what to expect |
|---|---|
| 1 | 16,651,426 rows become 16,648,094; `pair_collapse` on 12,668 filer-years (10,259 today), `forward_fill` on 200. Step 1 is also what the matcher does at the start of a run, so a matcher rerun makes it unnecessary |
| 1, the views | the `DROP ... CASCADE` takes the matching views, which the command creates again, and `privategrants_current_w_recovered`, which it does not: `load` creates it again, and so does `placeholder_recovery view --policy v2`. Until one of them runs, a reader of the view fails |
| 2 | unchanged, 441,358 rows: the future relation reads the paid one for its urls only, and no filer-year's url moves |
| 3, `work-list` | 22 filings whose placeholder amount was in the table N times come down to one copy ($42,980,947 to $8,015,203) and lose `placeholder_exceeds_declared` |
| 3, `load` | five of them load, 145 rows and $5,857,441 (EIN 205905161, tax years 2020 to 2024): their lists add up to the corrected amount and never could to five times it |

What does NOT follow from these steps: `privategrants_w_recipients` and
`unioned_grants`, which the products read, are written by the matcher and
change only when it reruns. That rerun is Zein's, about eight hours, and
PR #56 (the recovered grants into the matcher) needs the same one, so one
rerun carries both. The regression gate runs after it
(`docs/matching-regression-runbook.md`). From this change alone it should
show 277 fewer matched rows (7,578,795 to 7,578,518) and one self-match
fewer (3,641 to 3,640), and nothing else: the placeholder, corporate,
foreign and person counts, the three sentinels, the labeled-pair coverage
and the two hard rules stay where they are. The doubling doc, section
6.7, has the reasoning for each line.

Still wrong after the rebuild, and listed in the doubling doc: 14
work-list filings whose placeholder amount is repeated in rows that
differ in one field (6.6), and two future-payment filings that are
forward-filled (6.4).

## How a page is decided

`page_verdicts.agree(session, pages, policy)` reads each page through the
policy's readers (`page_readings.read_pages`, a no-op for stored
readings), decides a verdict per page and writes the verdicts under the
policy version, flushing after each stage:

1. The base pair read every page. Two readings agree when their multisets
   of (name key, amount) pairs are equal and non-empty; the name key is
   the first fourteen letters and digits, lower-cased. Agreed pages are
   done.
2. A disputed page goes to the escalation readers in turn. The first
   reading equal to any earlier reading is accepted and the page is
   `escalated`.
3. A page no two readers agree on is `flagged`, and the last escalation
   reader's reading is accepted under `load_single` (left out under
   `leave_out`).
4. A page fewer than two readers could read is `unreadable`.
5. Under a `not_a_list` rule (`POLICY_V3`), a page both base readers
   label `other` and return no pair for is `not_a_list` after step 1: no
   escalation reader sees it, no reading is accepted, nothing loads from
   it. To the selector it is an empty `other` page (`loader.selector_input`),
   so a list's continuation ends at it as at any `other` page. Two empty
   readings still do not *agree*; without the rule (v1, v2) such a page
   is a dispute that goes to both escalation readers and ends flagged.

Same-model repeats never count as agreement. Amounts alone never count:
the pairs must match, because dense pages slide amounts against names
while the sum barely moves.

## Policies

A policy names the readers, their order, the prompt version, each
reader's request settings, the flagged rule and, from v3, the
`not_a_list` rule. Results are always tied to a policy version so runs
stay comparable, and a change of rule is a new version, never an edit of
an old one.

| policy | readers | prompt | settings | flagged | not a list |
|---|---|---|---|---|---|
| `POLICY_V1` | Qwen3-VL + Gemini 3.5 Flash Lite, then Gemini 3.8 Flash, then Claude Sonnet 5 | v4 | 3.8 Flash at its default reasoning effort, pinned; can buy nothing | `load_single` | — |
| `POLICY_V2`, the frame's and the 2020-on read's | the same | v4 | 3.8 Flash at `low` reasoning effort; Sonnet with thinking off; Qwen never in JSON mode | `load_single` | — |
| `POLICY_V3`, derived from v2's readings on 2026-09-30, not yet the run's | the same | v4 | the same | `load_single` | `empty_other`: both base readers say `other` and return no row |

A JSON file with the same keys is a policy too (`--policy path.json`);
`load_policy` refuses one that reuses a registered version with different
rules. `--flagged leave_out` derives `<version>-leave_out` from the
stored verdicts. Worker counts per reader (`page_verdicts.WORKERS`):
Qwen 80, Flash Lite 24, 3.8 Flash 24, Sonnet 24; nothing in 24,131 page
reads found the gateway's limit.

`page_verdicts base-pair --policy v2` lists every page decided under a
policy by what the base pair said — both `other` and empty; neither a
list, otherwise; one a list; both a list — with how many reached the
last escalation reader, how many ended flagged and what escalating each
class cost at list prices. It is the measurement v3 came from (the
engineering log, 2026-09-30) and the check to run on the next read.

## What is built, and what is not

| piece | state |
|---|---|
| the work list | built: `work-list` writes `pf_placeholder_filings`, and `run` reads it in place of a frame file, by tax year and with a limit |
| the loader and the view | built: `load` writes `privategrants_recovered` and leaves the view; `check` holds the loaded rows to nine rules |
| the future-payment source | built and loaded on 2026-09-29: `irs_990pf_grants_future`, `privategrants_future_current`, the future amount on the work list, the loader's target `future` |
| the frame's rows, loaded | done under `v2`: paid, 399 filings, 222,105 rows, $7.07B; future, 71 filings, 3,873 rows, $0.95B |
| the 2020-on work list's rows, loaded | first loaded on 2026-09-30, when this branch's rerun found the production read's verdicts in the tables (6,350 filings): paid 4,607 filings, 582,676 rows, $16.2B; future 173 filings, 6,531 rows, $1.27B; 24 filings add up only to the two amounts together; `check` passes on all of it. The read's own session reports it |
| a list that adds up only to paid and future together | not loaded, counted by `load`: 4 filings on the frame; whether it should load is open |
| the extract's rows in the search | gone: the selector reads the pages and nothing else; the file is archived |
| the matcher reading the view | not built: the name cleaner, the name-only tier and input shape version 3 go in on one rerun |
| reading the work list | fetched, not read: tax years 2020 on are fetched and cut (2026-09-29), 28,906 pages to read, projected at $757, inside the cap of $800. Tax years 2015 to 2019 are fetched and cut too (the older renderers' widths measured 2026-09-29), 22,827 pages, projected at $598; 2009 to 2014 are not served |

The rules the work list and the loader follow are in the engineering
log, stages 0 and 5.

## Decisions already made

Settled by measurement in September 2026; the evidence is in the
findings doc's decisions table and the engineering log. Do not reopen
without a new measurement.

| decision | value |
|---|---|
| bucket and key | `givingtuesday-datamart`, `irs/pdf/<object_id>.pdf`; identity is the hash on the row |
| base readers, escalation | Qwen3-VL instruct and Gemini 3.5 Flash Lite; Gemini 3.8 Flash, then Claude Sonnet 5 |
| prompt, DPI | `vlm_transcription.PROMPT` v4; 200 DPI |
| per-model settings | Qwen never in JSON mode; Sonnet and the GPT line at reasoning effort none; 3.8 Flash at `low` (never `none` or `minimal` on a Gemini model: they think more) |
| agreement | equal (name key, amount) multisets, at least one row; never between the same model's reads |
| flagged pages | loaded from Sonnet's reading, marked; `leave_out` derivable |
| a base reader out of attempts on a page | absent for it; the page goes through the dispute path; `unreadable` only when fewer than two readers could read it |
| retries | `no_teos_image` never without `refetch`; everything else three attempts |
| PDF retention | indefinite |
| renders | not stored; one render on the box serves every reader |
| tables | raw `CREATE TABLE IF NOT EXISTS`, no migration tool |
| tests | no database in unit tests: in-memory stores and the fake gateway client; the data-dependent criteria run by hand |
| work list | built from the loaded tables, every tax year; never from GivingTuesday's one-off extract |
| exclusions | six patient-assistance programs, by EIN; filings marked as grants to individuals are read and labelled |
| what loads | lists within 0.5% of the declared total; rows from pages that were read only; rows from pages labelled expenditure responsibility only where a list needs them, labelled |
| targets | the work list's paid amount and, since 2026-09-29, its future amount, from GivingTuesday's future-payment datamart (`990PFPart14Grants3B`); each list is searched for on its own; future lists load marked `future`, outside the view |
| doubled blocks in the future-payment file | halved when every row is paired and the filing's `basic_fields_pf` row is repeated; nothing else is halved. The paid rule still tests against line 25; Zein chose on 2026-09-30 to add the repeated row beside it (PR #57 measured it), a change still to be made and gated |
| a placeholder amount over both columns of line 25 | the filing is read and its list loads, every row labelled `placeholder_exceeds_declared`; a consumer filters on it |
| the view | drops a filing's placeholder row once its list is loaded; shows one policy |

## Set up a box once

1. **poppler and tmux**, from the system package manager (`pdftoppm` and
   `pdfimages` are not pip-installable):

   ```bash
   sudo apt-get update && sudo apt-get install -y poppler-utils tmux git git-lfs
   ```

2. **The repo and a Python 3.12 environment**, with the repo importable and
   the `ingest` extra. Git LFS must be installed before the clone, or the
   frame CSV is a 130-byte pointer; the command checks.

   ```bash
   git lfs install
   git clone git@github.com:vibrant-data-labs/givingtuesday-datamart.git && cd givingtuesday-datamart
   git checkout placeholder-recovery
   python3.12 -m venv ~/venv-frame && source ~/venv-frame/bin/activate
   pip install -e '.[ingest]'
   ```

3. **Two environment variables**, in a file you `source` before starting
   tmux (`~/frame.env`, mode 600), with the venv activated there too:

   ```bash
   export VERCEL_AI_GATEWAY_API_KEY=...                  # the gateway key
   export GT_DATAMART_CONFIG_PATH=$HOME/config.ini      # [postgres] host, port, user, password
   source $HOME/venv-frame/bin/activate
   ```

   The `config.ini` needs only the `[postgres]` section; the database name
   is fixed to `gt_datamart` by `ingestion.datamart_config`. The datamart's
   security group must accept port 5432 from the box.

4. **The instance role, and the network.** The role needs read *and write*
   on `s3://givingtuesday-datamart` (`s3:ListBucket` on the bucket;
   `s3:GetObject`, `s3:PutObject` and `s3:DeleteObject` on `irs/*`): the
   readers download PDFs, the fetch stage uploads them, and the
   prerequisites check does a head on the bucket and puts and deletes a
   small object under `irs/_run_check/`. The box needs HTTPS egress to
   `apps.irs.gov` (TEOS) and to the gateway. `~/.aws/credentials` works in
   place of the role, as on the laptop; a bucket-scoped role is the
   housekeeping item.

5. **The cache directory**, on a disk with 15 GB free per 1,000 filings
   of PDFs and PNGs:

   ```bash
   sudo mkdir -p /data/irs_index && sudo chown "$USER" /data/irs_index
   ```

## The one command

From the repo root, with the environment variables set:

```bash
source ~/frame.env
tmux -L frame new -s frame 'python -m givingtuesday_datamart.placeholder_recovery run --policy v2 --tax-years 2020-2025 --cache /data/irs_index'
```

`-L frame` starts a tmux server of its own, which inherits the shell's
environment; a session opened on a server that is already running would
not see the variables.

`run` rebuilds the work list from the loaded tables (`--keep-list` reads
the table as it stands), takes the filings of `--tax-years` (every year
when not given), largest placeholder amount first, and `--limit` of them
at most. It writes them to `<logs>/work_list.csv` in the frame's
columns, so the log directory holds the list the run covered, and runs
that file through the stages below. `--sample <frame CSV>` runs a frame
file instead, as before, and leaves the work list alone. Filings already
fetched or read are skipped by the tables either way.

`--no-fetch` asks the IRS for nothing, the TEOS check included. The cost
gate then counts the pages of the filings the tables hold and prices the
filings never fetched at what a frame filing of their band cost ($1.35,
$0.59, $0.066, $0.020). With `--dry-run` that is the projection for a
list nobody has approved fetching:

```bash
python -m givingtuesday_datamart.placeholder_recovery run --policy v2 --tax-years 2020-2025 --dry-run --no-fetch
```

On 2026-09-28 it found 12,822 filings, 992 of them fetched, and
projected $474.72 for the 11,830 others. The cap was $400 then, the
frame's, and stopped it. It is $800 since September 29, so the same
command passes the gate and ends as a dry run does, with nothing
bought. On 2026-09-29, after the fetch, the dry run counted 6,345
filings with attachment pages and 38,662 pages, 9,756 of them read, and
projected $756.58 for the 28,906 others.

`run` checks every prerequisite first (poppler, with `pdftoppm -v`
printed and a one-page render tried; the gateway key and the datamart
config; an HTTPS request to TEOS; a head and a small put on the bucket;
15 GB free under the cache) and stops at the first thing missing, with a
message saying what. Then, each stage under a timestamped banner:

1. **fetch**: `fetch_filings` on the frame; a frame fetched from the
   laptop already makes zero TEOS requests, and the log says so.
2. **the cost gate**: the stored-only pass, which names the pages without
   a reading and buys nothing, then the projection by reader at the
   measured per-page prices and the frame's dispute and resolution rates,
   and by band for the filings never fetched; past $800 (`--cap`) the
   run stops. `--dry-run` stops here, so the projection can be committed
   before the box runs for real.
3. **the base readers**, as child processes of the `page_readings` CLI,
   each with its own log. Nothing spends before a render has succeeded,
   so their first chunk proves within a minute that the box can
   download, render, call and parse.
4. **transcribe**: the escalation readers and the verdicts; again if any
   page was left without a verdict.
5. **the final check**: the stored-only pass must buy nothing and write
   nothing.
6. **status**: `page_readings status` and `page_verdicts status`.

The load is a command of its own, run after a run or after a change of
rule: `load --policy v2`, then `check --policy v2`.

Everything printed goes to the pane and to `logs/run-<start>/run.log`;
the readers' logs sit beside it. Detach with `Ctrl-b d`, reattach with
`tmux -L frame attach -t frame`. The pane closes when the command ends;
the log has everything.

## Monitor from the laptop

The stages commit to the datamart as they go (200 readings a commit), so
the laptop reads the same numbers the box does:

```bash
python -m givingtuesday_datamart.page_readings status          # rows, hits, failed, partial, $ by model and prompt
python -m givingtuesday_datamart.page_verdicts status --policy v2
```

The `$` column is the running spend by model from the rows' `usage`; a
run's cost is the difference from the figure before it. Pages a minute
and error rows over the last hour:

```sql
SELECT model, count(*) AS pages, count(*) FILTER (WHERE response IS NULL) AS error_rows,
       count(*) FILTER (WHERE partial) AS partial
FROM page_readings WHERE read_at > now() - interval '1 hour' GROUP BY 1;
```

On the box, `tail -f logs/run-*/qwen3-vl-instruct.log` (or
`gemini-3.5-flash-lite.log`) follows a base reader: a line every 25 pages
with the rows, errors, partial pages and dollars so far; the later stages
log to `run.log`. Zero rows in a reader's first lines means stop and
look.

## Stop, and start again

**Ctrl-C in the pane is safe** (so is `tmux -L frame send-keys -t frame
C-c`): the readers are child processes in the pane's process group, so
each gets the interrupt on its own main thread, cancels the pages not
yet started and records the results of the pages in flight (at most the
workers' count, about a dollar); `run` waits for them, then stops. Do
not `kill -9`: that loses the uncommitted chunk, up to 200 pages. A
render interrupted by the stop leaves a `tmp*/` directory under
`<cache>/pages200/<filing>/`; its pages are rendered again on resume,
and the directory is safe to delete whenever nothing is running.

**Running the same command again resumes.** A completed stage makes no
model call, the base pair's verdicts are unchanged, and only the pages in
flight at the stop are bought twice. A gateway refusal or a timeout is
recovered the same way: the page is listed as `no_verdict`, `run` runs
`transcribe` a second time for it, and a re-run reads it again until it
has three errors, after which that reader is absent for the page and the
others decide it.

**The circuit breaker.** A failed render or gateway call is one error on
its page, and one run retries a page three times, which is the limit. So
a run on a box with a broken poppler or a dead key would leave every
page's base rows at the limit, and the next run, on the fixed box, would
skip the base pair and read the whole frame through 3.8 Flash and Sonnet
at about $550 instead of $90. A reader whose first fifty results are all
errors from three or more filings therefore stops with nothing written,
`STOPPED:` and the first error on its log, and `run` stops with it; fix
the box and run the same command again, every page still with its
attempts. A single filing that cannot be read keeps its error rows as
before.

## Costs and throughput

Per page, under `POLICY_V2`: Qwen $0.0024, Flash Lite $0.0070, 3.8 Flash
at `low` $0.0093, Sonnet $0.0459. All four readers together, about 2 to
3 cents a page on the large filings' dense pages and about 2 cents on the
small filings' short ones.

The 1,000-filing frame, 9,926 attachment pages, 2026-09-23 and 24, on one
t3.xlarge:

| stage | pages bought | wall | pages a minute | cost |
|---|---|---|---|---|
| Qwen v4, 40 then 80 workers | 7,613 | 138 min | 55 | $19.30 |
| Flash Lite v4, 12 workers, in parallel | 7,606 | 93 min | 81 | $46.89 |
| 3.8 Flash at `low`, the disputed pages | 5,959 | 45 min | 131 | $54.21 |
| Sonnet 5, the pages still open | 2,953 | 36 min | 82 | $101.24 |
| all | 24,131 | 3 h 43 min with two stops | | $221.64 |

Rule of thumb: about 2.5 hours per 10,000 pages for the base pair in
parallel and 1.5 hours for the escalation readers; about $0.02 to $0.03 a
page all-in. The work list from the loaded tables, for tax years 2020
on, leaves 11,830 filings to read. Fetched on 2026-09-29, they hold
28,906 attachment pages in 5,795 filings: $757 by the cost gate, about
$530 at the frame's cost per page by band, and 11 to 12 hours of
reading. With the $304 already spent that is $830 to $1,060. A fetch
takes about 15 minutes for 8,800 filings from the box, ten a second,
and four a second from a laptop. The box has 20 GB free, so a fetch
there uses a cache of its own (`--cache /data/irs_fetch`), deleted
afterwards; the read takes each PDF from S3.
Of the 11,696 filings before 2020, the IRS serves tax years 2015 to
2019 (6,472 of 7,944, fetched 2026-09-29) and nothing before 2014. Cut
with the older renderers' widths (measured 2026-09-29), they hold
22,827 attachment pages in 2,686 filings: $598 by the cost gate, about
$435 at the frame's cost per page by band, about nine hours of reading
(`run --policy v2 --tax-years 2015-2019 --keep-list`). The two reads
together are $1,355 by the gate, over the cap of $800, so each is its
own run and its own decision. The server is a few dollars a run and
the S3 storage under a dollar a month.

## Success metrics, as measured on the frame run

| metric | target | measured |
|---|---|---|
| model calls on re-derivation | 0 | 0: the final stored-only pass bought nothing and wrote nothing; the reports ran from the tables |
| resume cost of a completed stage | 0 calls | 0: `run` again after the run, 42 s, every stage on stored readings |
| base-pair wall time, 10,000 pages | under 6 h | 2 h 16 min for 9,926 pages in parallel |
| cost of the frame | $300 to $430 as designed | $221.64 under v2 |
| traceability | every loaded row joins to its verdict and two readings | 9,926 verdicts, 0 orphans |
| a loaded row's lineage | one reading and one verdict a row, and the row's own line in the reading | 225,978 rows of both targets, 0 without either (`check`) |
| a second load | writes nothing | 415 filings unchanged (399 with a paid list, 71 with a future one), 0 written, 0 taken out |
| no dollar counted twice | a loaded filing holds no more in the view than in `privategrants_current`, within 0.5% | 399 filings, 0 over |
| no future row in the view | the view holds paid rows only | 71 filings with future rows, 0 with one in the view (`check`) |
| ground-truth reproduction | 58 / 2 / 23 on the 83 sample pages | 58 / 2 / 23 under v2, the same two wrong pages |

## Housekeeping

- A bucket-scoped instance role for the box, in place of the account's
  keys in `~/.aws`.
- A per-chunk render lock, so the base pair never renders a chunk twice
  when both start on the same filing.
- Flash Lite at 24 workers is untested at scale; watch its error rows in
  the first minutes of the next run.
- The selector's search for a run of pages builds the run's row list
  again for every candidate, so a very large filing that does not add
  up costs minutes: Johnson & Johnson 2021, 836 pages, takes 90 seconds,
  and the frame's load spends 8 of its minutes there. Keeping a running
  count would fix it. The selector was not touched in the loader work.
- `load` and `work-list` run their table's `ALTER TABLE ... ADD COLUMN
  IF NOT EXISTS` at every start, and that asks for an exclusive lock.
  On 2026-09-29 a load waited four minutes behind another session's
  long read of the view, and a reader arriving meanwhile waits behind
  the load. Testing for the column first would avoid the lock.
- `load` walks every fetched filing of the work list, read or not:
  12,477 on 2026-09-29, while the work list was being fetched, against
  the frame's 553. With the database busy the frame's load took 20
  minutes where it had taken 10.
- `load` reads the whole work list's filings before it starts. At
  24,518 filings that is a second; it will want a filter by tax year
  when the list is read in parts.

## Commands

```bash
# the future-payment source, and one version per return of it
python -m givingtuesday_datamart.sources refresh --source irs_990pf_grants_future
python -m givingtuesday_datamart.current_grants --only privategrants_future_current

# the paid relation alone, after a change to its rule (drops the views: see "Rebuilding privategrants_current")
python -m givingtuesday_datamart.current_grants --only privategrants_current
# a rule change proved on scratch copies, the production tables untouched
python -m givingtuesday_datamart.exploratory.pf_current_scratch build paid|future [--suffix S] [--paid-scratch]
python -m givingtuesday_datamart.exploratory.pf_current_scratch compare paid|future [--suffix S]
python -m givingtuesday_datamart.exploratory.pf_current_scratch placeholders [--suffix S] [--policy v2]
python -m givingtuesday_datamart.exploratory.pf_current_scratch drop [--suffix S]

# the work list, the run over it, the load, the view, the checks
python -m givingtuesday_datamart.placeholder_recovery work-list
python -m givingtuesday_datamart.placeholder_recovery run --policy v2 --tax-years 2020-2025 --cache /data/irs_index [--limit N] [--keep-list] [--dry-run] [--no-fetch]
python -m givingtuesday_datamart.placeholder_recovery load --policy v2 [--object-id ID] [--dry-run] [--flagged leave_out]
python -m givingtuesday_datamart.placeholder_recovery view --policy v2
python -m givingtuesday_datamart.placeholder_recovery check --policy v2

# the frame and the population
python -m givingtuesday_datamart.exploratory.placeholder_recovery sample --expand-1000
python -m givingtuesday_datamart.exploratory.placeholder_population

# fetch, read, decide, by hand
python -m givingtuesday_datamart.filing_images fetch data/exploratory/placeholder_sample_1000.csv
python -m givingtuesday_datamart.filing_images status
python -m givingtuesday_datamart.filing_images recut [--dry-run]        # after a change to irs_source.RENDERED_WIDTHS
python -m givingtuesday_datamart.page_readings read data/exploratory/placeholder_sample_1000.csv --model alibaba/qwen3-vl-instruct --workers 80
python -m givingtuesday_datamart.page_readings status
python -m givingtuesday_datamart.page_verdicts agree data/exploratory/placeholder_sample_1000.csv --policy v2
python -m givingtuesday_datamart.page_verdicts status --policy v2
python -m givingtuesday_datamart.page_verdicts base-pair --policy v2

# the whole run, the cost gate alone, the stored-only re-derivation, the report
python -m givingtuesday_datamart.exploratory.placeholder_recovery run --policy v2 --sample data/exploratory/placeholder_sample_1000.csv --cache /data/irs_index [--dry-run]
python -m givingtuesday_datamart.exploratory.placeholder_recovery estimate --policy v2 --sample data/exploratory/placeholder_sample_1000.csv
python -m givingtuesday_datamart.exploratory.placeholder_recovery transcribe --policy v2 --sample data/exploratory/placeholder_sample_1000.csv --stored-only
python -m givingtuesday_datamart.exploratory.placeholder_recovery report --policy v2 --sample data/exploratory/placeholder_sample_1000.csv --out data/exploratory/placeholder_report_v2_1000.csv [--flagged leave_out] [--with-future]

# the scorer
python -m givingtuesday_datamart.exploratory.placeholder_ground_truth verdicts --policy v2
python -m givingtuesday_datamart.exploratory.placeholder_ground_truth flagged --policy v2 --out data/exploratory/placeholder_flagged_check.csv
```
