# Placeholder recovery: operations

Vibrant Data Labs, September 25, 2026. How the placeholder-recovery
pipeline is run and what it writes: the three tables, the policies, the
one command, how to watch and stop a run, and what a run costs. The
findings are in [placeholder_grant_recovery.md](placeholder_grant_recovery.md);
the engineering log, with the measurements behind every choice here, is
[placeholder_recovery_pipeline.md](placeholder_recovery_pipeline.md).
This document absorbed the storage-layer spec and the EC2 runbook of
September 22 to 24, both in git history.

## The shape

Everything a run produces lands in S3 or in three datamart tables, so a
laptop and a box share state through them and nothing is copied between
machines. Every stage reads what the tables lack and buys only that, so
the same command run again resumes, a completed stage makes no model
call, and a change of rule re-derives from stored readings for free.

| table | one row per | key | written by |
|---|---|---|---|
| `filing_images` | filing | `object_id` | `filing_images.fetch_filings` |
| `page_readings` | page read by one model under one prompt and one request setting | `(object_id, page, image_sha256, dpi, model, prompt_version, request_hash)` | `page_readings.read_pages` |
| `page_verdicts` | page decided under one policy | `(object_id, page, image_sha256, policy_version)` | `page_verdicts.agree` |

The PDFs sit in `s3://givingtuesday-datamart/irs/pdf/<object_id>.pdf`,
kept indefinitely, with the SHA-256 on the row and as the S3 checksum.
Rendered pages (`<cache>/pages200/<object_id>/pNNN.png`) are a local
cache, not stored: one render on the box serves every reader.

## The tables

**`filing_images`.** The fetch outcome for a filing: `filerein`,
`taxyear`, the IRS index year and TEOS URL, the date the IRS generated
the image (for the 404 report), `status`, `attempts`, `last_error`,
`fetched_at`, `s3_key`, `sha256`, `bytes`, `pages`, `page_widths` (from
`pdfimages -list`, one per page), `attachment_from` (the first page the
IRS did not render; NULL when the filer attached nothing) and
`attachment_pages`. Statuses: `fetched`; `no_teos_image` (TEOS lists no
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
named policy: `verdict` (`agreed`, `escalated`, `flagged`, `unreadable`),
`accepted_model` and `accepted_hash` (the reading that is loaded),
`matched_models` (the readings that agreed with it), `readers_consulted`
and `decided_at`. A flagged page carries Sonnet's reading as accepted
under the `load_single` rule; the mark is the verdict. Verdicts under
`<version>-<rule>` are the same policy with the flagged rule overridden,
derived without a model call.

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

Same-model repeats never count as agreement. Amounts alone never count:
the pairs must match, because dense pages slide amounts against names
while the sum barely moves.

## Policies

A policy names the readers, their order, the prompt version, each
reader's request settings and the flagged rule. Results are always tied
to a policy version so runs stay comparable, and a change of rule is a
new version, never an edit of an old one.

| policy | readers | prompt | settings | flagged |
|---|---|---|---|---|
| `POLICY_V1` | Qwen3-VL + Gemini 3.5 Flash Lite, then Gemini 3.8 Flash, then Claude Sonnet 5 | v4 | 3.8 Flash at its default reasoning effort, pinned; can buy nothing | `load_single` |
| `POLICY_V2`, the frame's | the same | v4 | 3.8 Flash at `low` reasoning effort; Sonnet with thinking off; Qwen never in JSON mode | `load_single` |

A JSON file with the same keys is a policy too (`--policy path.json`);
`load_policy` refuses one that reuses a registered version with different
rules. `--flagged leave_out` derives `<version>-leave_out` from the
stored verdicts. Worker counts per reader (`page_verdicts.WORKERS`):
Qwen 80, Flash Lite 24, 3.8 Flash 24, Sonnet 24; nothing in 24,131 page
reads found the gateway's limit.

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
tmux -L frame new -s frame 'python -m givingtuesday_datamart.exploratory.placeholder_recovery run --policy v2 --sample data/exploratory/placeholder_sample_1000.csv --cache /data/irs_index'
```

`-L frame` starts a tmux server of its own, which inherits the shell's
environment; a session opened on a server that is already running would
not see the variables.

`run` checks every prerequisite first (poppler, with `pdftoppm -v`
printed and a one-page render tried; the gateway key and the datamart
config; an HTTPS request to TEOS; a head and a small put on the bucket;
15 GB free under the cache) and stops at the first thing missing, with a
message saying what. Then, each stage under a timestamped banner:

1. **fetch**: `fetch_filings` on the frame; a frame fetched from the
   laptop already makes zero TEOS requests, and the log says so.
2. **the cost gate**: the stored-only pass, which names the pages without
   a reading and buys nothing, then the projection by reader at the
   measured per-page prices and the frame's dispute and resolution rates;
   past $400 (`--cap`) the run stops. `--dry-run` stops here, so the
   projection can be committed before the box runs for real.
3. **the base readers**, as child processes of the `page_readings` CLI,
   each with its own log. Nothing spends before a render has succeeded,
   so their first chunk proves within a minute that the box can
   download, render, call and parse.
4. **transcribe**: the escalation readers and the verdicts; again if any
   page was left without a verdict.
5. **the final check**: the stored-only pass must buy nothing and write
   nothing.
6. **status**: `page_readings status` and `page_verdicts status`.

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
page all-in. The remaining 8,515 filings of bands C and D are about
14,800 pages, about $270 and a day; the full population is about 24,700
pages and $575, of which $304 is spent. The server is a few dollars a
run and the S3 storage under a dollar a month.

## Success metrics, as measured on the frame run

| metric | target | measured |
|---|---|---|
| model calls on re-derivation | 0 | 0: the final stored-only pass bought nothing and wrote nothing; the reports ran from the tables |
| resume cost of a completed stage | 0 calls | 0: `run` again after the run, 42 s, every stage on stored readings |
| base-pair wall time, 10,000 pages | under 6 h | 2 h 16 min for 9,926 pages in parallel |
| cost of the frame | $300 to $430 as designed | $221.64 under v2 |
| traceability | every loaded row joins to its verdict and two readings | 9,926 verdicts, 0 orphans |
| ground-truth reproduction | 58 / 2 / 23 on the 83 sample pages | 58 / 2 / 23 under v2, the same two wrong pages |

## Housekeeping

- A bucket-scoped instance role for the box, in place of the account's
  keys in `~/.aws`.
- A per-chunk render lock, so the base pair never renders a chunk twice
  when both start on the same filing.
- Flash Lite at 24 workers is untested at scale; watch its error rows in
  the first minutes of the next run.
- `report` should show paid and future lists as separate columns and
  name the future-only reconciliations (15 filings, 800 rows on the
  frame).

## Commands

```bash
# the frame and the population
python -m givingtuesday_datamart.exploratory.placeholder_recovery sample --expand-1000
python -m givingtuesday_datamart.exploratory.placeholder_population

# fetch, read, decide, by hand
python -m givingtuesday_datamart.filing_images fetch data/exploratory/placeholder_sample_1000.csv
python -m givingtuesday_datamart.filing_images status
python -m givingtuesday_datamart.page_readings read data/exploratory/placeholder_sample_1000.csv --model alibaba/qwen3-vl-instruct --workers 80
python -m givingtuesday_datamart.page_readings status
python -m givingtuesday_datamart.page_verdicts agree data/exploratory/placeholder_sample_1000.csv --policy v2
python -m givingtuesday_datamart.page_verdicts status --policy v2

# the whole run, the cost gate alone, the stored-only re-derivation, the report
python -m givingtuesday_datamart.exploratory.placeholder_recovery run --policy v2 --sample data/exploratory/placeholder_sample_1000.csv --cache /data/irs_index [--dry-run]
python -m givingtuesday_datamart.exploratory.placeholder_recovery estimate --policy v2 --sample data/exploratory/placeholder_sample_1000.csv
python -m givingtuesday_datamart.exploratory.placeholder_recovery transcribe --policy v2 --sample data/exploratory/placeholder_sample_1000.csv --stored-only
python -m givingtuesday_datamart.exploratory.placeholder_recovery report --policy v2 --sample data/exploratory/placeholder_sample_1000.csv --out data/exploratory/placeholder_report_v2_1000.csv [--flagged leave_out]

# the scorer
python -m givingtuesday_datamart.exploratory.placeholder_ground_truth verdicts --policy v2
python -m givingtuesday_datamart.exploratory.placeholder_ground_truth flagged --policy v2 --out data/exploratory/placeholder_flagged_check.csv
```
