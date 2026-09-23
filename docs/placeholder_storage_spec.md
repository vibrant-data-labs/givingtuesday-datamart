# Placeholder Recovery: Storage Layer Spec

Filing images and model readings for the "SEE ATTACHMENT" recovery
pipeline. Two parts, meant to be built in separate sessions:

- **Part A — Filing image store.** IRS PDFs in S3, one `filing_images`
  row per filing recording where it came from, what it contains, and
  what went wrong when nothing did.
- **Part B — Model reading cache.** One `page_readings` row per reading
  of one page by one model under one prompt, a bulk cache-or-run that
  reads what is missing in parallel, and `page_verdicts` derived from
  readings under a versioned policy.

Vibrant Data Labs, 2026-09-22. Status: Part A built and its criteria
run (the pipeline doc's *Order of operations*, item 4); Part B is
spec. Background and the measurements every decision below rests on
are in [placeholder_recovery_pipeline.md](placeholder_recovery_pipeline.md);
the short report is [placeholder_grant_recovery.md](placeholder_grant_recovery.md).

## Problem statement

The recovery pipeline reads about 20,000 attachment pages through two to
four vision models and keeps only what the models agree on. Today every
artifact lives on one laptop: PDFs in `~/.cache/irs_index/pdfs/`, page
readings as JSON files in `~/.cache/irs_index/vlm/<model>-<prompt>/`,
fetch failures in three exploratory CSVs. Nothing can be re-run by
anyone else, a re-run of a subset means re-reading its pages through
every model, and no loaded row can name the readings it came from. The
matcher pipeline had the same shape and it is the thing about that
pipeline we most want not to repeat.

## Goals

1. **Re-derivation costs no model calls.** Changing the selector, the
   agreement policy or the acceptance rule for flagged pages re-runs
   from stored readings only. Measured as API calls made by `report`
   and `agree` on a frame whose readings exist: zero.
2. **A re-run of any subset touches only that subset's pages, and only
   the readers not yet stored.** A completed stage re-run makes zero API
   calls. A new prompt version reads only under the new version.
3. **Every loaded row is traceable** to its page, image hash, the two
   readings that agreed on it, the policy version and the selector
   version, by join alone.
4. **The 1,000-filing run is one command per stage**, resumable at page
   granularity, with the base pair finishing about 10,000 pages in under
   six hours of wall time and the whole design costing within 20% of the
   ground-truth estimate ($360 for 9,347 pages).
5. **Fetch failures are a query, not a file.** The unreachable set, the
   404s by image year and the no-attachment filings come from
   `filing_images` and feed the IRS report directly.

## Non-goals

- **Not a general LLM cache.** vdl-tools has `PromptResponseCacheSQL`
  for text prompts against OpenAI; this repo does not depend on
  vdl-tools (removed in #18) and this cache takes images through the
  Vercel gateway. The pattern is copied, the code is not imported.
- **Not storing renders.** A 200 DPI PNG is derived from the PDF in
  under a second and re-rendered on a cache miss.
- **Not moving readings to S3.** At about 15 KB a reading and three
  readings a page, the whole population is about 1 GB of JSONB, which
  Postgres holds. The key is designed so a later move to S3 changes
  storage, not identity.
- **Not the selector, the matcher pass or the load into
  `privategrants_current`.** The selector already runs from page JSON
  (`attachment_grants.page_tables`); it will read from `page_verdicts`
  instead of folders, and that rewiring is in scope, but its logic is
  not.
- **Not future-payment lists.** They are read and stored like any page
  but are captured, not loaded (see the pipeline doc, stage 5).

## Decisions already made

These were settled by measurement in the September 2026 sessions. A
fresh session should not reopen them.

| decision | value | why |
|---|---|---|
| bucket | `givingtuesday-datamart`, the VDL bucket the matcher's checkpoints use | GT's `gt990datalake-analytics-and-datamarts` is read-only to us |
| PDF key | `irs/pdf/<object_id>.pdf` | flat; identity is the hash on the row, not the path |
| base readers | `alibaba/qwen3-vl-instruct`, `google/gemini-3.5-flash-lite` | cheapest pair; cross-model agreement exact on 107 of 107 ground-truth pages |
| escalation | `google/gemini-3.8-flash`, then `anthropic/claude-sonnet-5` | 94% and 97.5% pair precision; neither repeats Flash Lite's errors |
| prompt | `vlm_transcription.PROMPT`, version `v4` | inside run-to-run noise for Gemini, a small gain for Qwen |
| per-model settings | `JSON_MODE`, `REQUEST_EXTRAS` in `vlm_transcription` | Qwen never in JSON mode; Sonnet and the GPT line at reasoning effort none |
| agreement | equal (name key, amount) multisets, at least one row | amounts alone hide shifted names; two empty readings agreed once and were wrong |
| same-model repeats | never count as agreement | Gemini wrong on 6 of 57 self-agreements, Qwen on 14 of 51 |
| DPI | 200 | 130 misread digits; 300 costs more for nothing |
| page comparison key | name lower-cased, non-alphanumerics stripped, first 14 characters; amount exact | what the ground-truth scorer uses |
| flagged pages | load Sonnet's single reading, marked `flagged` | Zein, 2026-09-22; Sonnet alone right on 11 of 23, maximum reasoning changed nothing |
| PDF retention | indefinite | Zein, 2026-09-22; ~300 GB for the population is cheap against re-fetching, and the IRS loses images |
| tables | raw `CREATE TABLE IF NOT EXISTS`, no migration tool | the repo's convention (`ingestion.py`, `canonical/build.py`) |
| tests | no database in unit tests: a store interface with a Postgres implementation and an in-memory one, plus the fake client | the repo's convention — the client tests build on a `sqlite://` engine that never connects |

## What exists today

Reuse these; do not rewrite them.

| where | what |
|---|---|
| `irs_source.lookup`, `.images`, `._get` | object id → TEOS index row → image URLs → bytes |
| `irs_source.page_widths`, `.attachment_start` | `pdfimages -list` widths; first page the IRS did not render |
| `exploratory/placeholder_recovery._fetch_pdf` | the fetch loop with its status strings; moves into Part A |
| `vlm_transcription.render` | pages → 200 DPI PNGs, pid-safe temp names |
| `vlm_transcription.transcribe` | one page through one model: retries, JSON-mode fallback, salvage, `_usage`, `_attempts`, `_json_mode`, `_prompt` |
| `vlm_transcription.cost`, `PRICES`, `REQUEST_EXTRAS`, `JSON_MODE` | per-model price and request settings |
| `attachment_grants.page_tables` | page JSON → selector tables |
| `_internal/db.get_session(config)` | transactional session; `ingestion.datamart_config()` builds the config |
| `exploratory/placeholder_ground_truth.py` | the scorer; `POLICIES`, `PAIRS`, `_pairs`, `_key` define comparison and the policy shapes |
| `~/.cache/irs_index/pdfs/*.pdf` | the 517 fetched PDFs of the 610-filing frame, 1.15 GB; uploaded by the Part A backfill |
| `~/.cache/irs_index/vlm/<folder>/<oid>/pNNN.json` | readings to backfill: `<model>` = prompt v2, `-v3`, `-v4`; candidates `google__gemini-3.8-flash-v4`, `anthropic__claude-sonnet-5-v4`, `openai__gpt-5.6-*-v4` |
| `data/exploratory/placeholder_staging*.csv`, `placeholder_404_images_expanded.csv`, `placeholder_unreachable.csv` | fetch statuses to backfill into `filing_images` |

Folders `-v4b` (the repeat read) and `-max-v4` (Sonnet at maximum
reasoning) are experiments; do not backfill them.

## User stories

- As the pipeline operator, I want to fetch every filing in a frame once
  and know for each one whether the IRS served it, so that the
  unreachable set is a query and the IRS report writes itself.
- As the operator, I want to read a frame's pages through a model with
  one command that skips what is already read, so that a crashed or
  interrupted run resumes without paying again.
- As the operator, I want a changed selector or policy to re-score a
  frame from stored readings in minutes, so that method iteration is
  free.
- As the data engineer, I want every loaded grant row to name its page,
  image hash, the two readings that agreed and the policy version, so
  that any row can be audited back to the PDF.
- As the operator, I want a re-issued IRS image to invalidate only that
  filing's readings, so that a corrected PDF gets re-read and nothing
  else does.
- As a reviewer, I want flagged pages listed with all their readings, so
  that the load-or-drop decision can be checked against the image.

## Part A — Filing image store

### Data model

```sql
CREATE TABLE IF NOT EXISTS filing_images (
    object_id        text PRIMARY KEY,
    filerein         text NOT NULL,
    taxyear          integer,
    index_year       text,                 -- which IRS index listed it
    teos_url         text,                 -- the STATICFILEPATH fetched
    image_generated  date,                 -- from the PDF token, for the 404 report
    status           text NOT NULL,        -- see below
    attempts         integer NOT NULL DEFAULT 0,
    last_error       text,
    fetched_at       timestamptz,
    s3_key           text,                 -- irs/pdf/<object_id>.pdf
    sha256           text,
    bytes            bigint,
    pages            integer,
    page_widths      integer[],            -- pdfimages -list, one per page
    attachment_from  integer,              -- NULL when no filer pages
    attachment_pages integer
);
CREATE INDEX IF NOT EXISTS filing_images_status ON filing_images (status);
```

`status` values, the ones `_fetch_pdf` already produces plus the
post-fetch outcomes: `fetched`, `no_attachment` (fetched, zero filer
pages), `no_teos_image`, `pdf_unavailable:<http code>`, `not_a_pdf`,
`lookup_failed:<exception>`. One row per filing; a re-fetch that returns
a different `sha256` updates the row, and the old hash's readings stay
in `page_readings` untouched.

### Behaviour

`fetch_filings(session, object_ids, *, bucket, prefix="irs/pdf", workers=8, refetch=False)`:

1. Skip ids whose row is `fetched` unless `refetch`.
2. Look up, download newest image first with older as fallback (as
   `_fetch_pdf` does), hash, upload to S3, run `page_widths` and
   `attachment_start`, upsert the row.
3. Failures upsert a row with the status and `last_error`; `attempts`
   increments; an id with three failed attempts is skipped unless
   `refetch`.
4. Downloads and uploads run in a thread pool; the session is touched
   only from the main thread, in chunks of 50 rows per commit.

`local_pdf(session, object_id, cache_dir)` returns a local path, from
the cache directory if present, else downloaded from S3. Renders and
readings go through this, never through TEOS again.

### Acceptance criteria

- [x] Fetching the 610-filing frame twice makes zero TEOS requests and
  zero uploads the second time.
- [x] The 93 no-PDF and 144 no-attachment filings in
  `placeholder_staging_expanded.csv` are reproduced by a `status` query,
  and the 38 rows of `placeholder_404_images_expanded.csv` by
  `status LIKE 'pdf_unavailable%' AND image_generated BETWEEN '2022-01-01' AND '2022-12-31'`.
- [x] `attachment_from` and `attachment_pages` match the staging CSVs on
  every fetched filing.
- [x] A one-off backfill loads the ~700 cached PDFs and both staging
  CSVs without a network fetch.
- [x] A row's `sha256` equals the SHA-256 of the object at `s3_key`.

## Part B — Model reading cache

### Data model

```sql
CREATE TABLE IF NOT EXISTS page_readings (
    object_id       text NOT NULL,
    page            integer NOT NULL,     -- original PDF page number
    image_sha256    text NOT NULL,        -- filing_images.sha256 at read time
    dpi             integer NOT NULL,
    model           text NOT NULL,        -- gateway id, e.g. alibaba/qwen3-vl-instruct
    prompt_version  text NOT NULL,        -- vlm_transcription.PROMPT_VERSION
    request_hash    text NOT NULL,        -- sha256 of canonical JSON of the request settings
    request         jsonb NOT NULL,       -- {json_mode, extras}, for audit
    response        jsonb,                -- page_kind, heading, rows, totals; NULL on error
    partial         boolean NOT NULL DEFAULT false,
    usage           jsonb,                -- {in, out, reasoning}
    finish          text,
    attempts        integer,
    json_mode       boolean,
    seconds         real,
    errors          integer NOT NULL DEFAULT 0,
    last_error      text,
    read_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (object_id, page, image_sha256, dpi, model, prompt_version, request_hash)
);
CREATE INDEX IF NOT EXISTS page_readings_model ON page_readings (model, prompt_version);

CREATE TABLE IF NOT EXISTS page_verdicts (
    object_id         text NOT NULL,
    page              integer NOT NULL,
    image_sha256      text NOT NULL,
    policy_version    text NOT NULL,
    verdict           text NOT NULL,      -- agreed | escalated | flagged | unreadable
    accepted_model    text,               -- the reading to use, with its
    accepted_hash     text,               --   request_hash; NULL when flagged
    matched_models    text[],             -- the two that agreed
    readers_consulted integer NOT NULL,
    decided_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (object_id, page, image_sha256, policy_version)
);
```

The reading key deliberately hashes the PDF, not the PNG: rendering is
deterministic for a given PDF, page and DPI, and hashing the PDF avoids
rendering just to look a page up. `request_hash` covers `json_mode` and
`REQUEST_EXTRAS[model]` (reasoning effort); the max-tokens ladder is a
retry detail and does not key.

### `read_pages`

```python
def read_pages(session, pages: Sequence[tuple[str, int]], model: str, *,
               workers: int, prompt_version: str = PROMPT_VERSION,
               max_errors: int = 3, cache_dir: Path = CACHE) -> dict[tuple[str, int], dict]
```

Copied from vdl-tools' `_bulk_get_cache_or_run`, with images:

1. Resolve each page's `image_sha256` from `filing_images`; pages of
   unfetched filings raise.
2. One query finds existing rows for the batch's keys. Rows with a
   response are hits. Rows with `errors >= max_errors` are skipped and
   reported. Everything else is a miss.
3. Misses are rendered (`vlm_transcription.render`, from `local_pdf`)
   and read in a thread pool of `workers` calling
   `vlm_transcription.transcribe`. Workers never touch the session.
4. The main thread collects results and upserts in chunks of 200 with
   one `INSERT ... ON CONFLICT DO UPDATE` per chunk; an error result
   increments `errors` and sets `last_error` instead of `response`.
5. Returns page → response for hits and successful misses.

Worker counts that held on the sample: Qwen 40, Flash Lite 12, 3.8
Flash and Sonnet 8; the escalation stages run as their own pools.

### `agree` and the policy

```python
POLICY_V1 = {
    "version": "v1",
    "base": ["alibaba/qwen3-vl-instruct", "google/gemini-3.5-flash-lite"],
    "escalation": ["google/gemini-3.8-flash", "anthropic/claude-sonnet-5"],
    "prompt_version": "v4",
    "flagged": "load_single",     # the last escalation reader's reading, marked; "leave_out" is the alternative
}

def agree(session, pages, policy=POLICY_V1, *, workers: dict[str, int]) -> dict[tuple[str, int], Verdict]
```

Per page: read with both base models (`read_pages`, which is a no-op for
stored readings); equal non-empty pair multisets → `agreed`. Otherwise
read the disputed subset with each escalation model in turn, accepting
on the first reading that equals any earlier one (`escalated`). No match
after the last → `flagged`; under `"flagged": "load_single"` the row
still carries `accepted_model` and `accepted_hash` for the last
escalation reader's reading, with `matched_models` empty, so the load
takes the reading and the verdict travels with it as the mark. A page
one base reader could not read at all after `max_errors` →
`unreadable`. Verdicts are upserted under `policy_version`; a policy
change is a new version and only readers not yet stored cost anything.
The comparison uses the scorer's `_pairs` and `_key`, moved into a
shared module so the scorer and `agree` cannot drift.

### Rewiring

- `placeholder_recovery.py transcribe` becomes a thin call to
  `read_pages` per model and `agree`; the `--results` folder goes away.
- `report` reads `page_verdicts` joined to `page_readings` for the
  accepted reading, builds `page_tables` from it, and adds the verdict
  mix per filing to its output.
- Backfill: a one-off loads the existing JSON folders into
  `page_readings` (folder suffix → prompt version; `_json_mode`,
  `_usage`, `_attempts`, `_partial` from each file) so the sample's four
  reads and the candidate reads are never bought again.

### Acceptance criteria

- [ ] `read_pages` on a batch already stored makes zero gateway calls;
  on a batch half stored makes exactly the missing half.
- [ ] A page stored under prompt `v4` is a miss under `v5` and a hit
  under `v4` again.
- [ ] Changing `filing_images.sha256` for a filing makes all its pages
  misses; the old readings remain.
- [ ] A page that has errored three times is skipped and listed, not
  retried.
- [ ] `agree` on the 83 ground-truth pages under `POLICY_V1` reproduces
  `placeholder_ground_truth.py policies` for design C: 58 right, 2
  wrong, 23 flagged, with zero gateway calls once the backfill has run.
- [ ] `report` on the 100-filing sample from `page_verdicts` under the
  stored v4 base readings gives the same filing outcomes as the
  folder-based report on the same readings.
- [ ] A flagged page under `load_single` has `accepted_model` set to
  the last escalation reader and `verdict = 'flagged'`; under
  `leave_out` it has no accepted reading.
- [ ] Unit tests use the fake client from
  `tests/test_vlm_transcription.py` and the in-memory store; no test
  connects to Postgres or makes a gateway call. The criteria above that
  need real data are run by hand against the datamart and their output
  recorded in the pipeline doc.

## Requirements summary

**P0**
- `filing_images` with fetch, statuses, S3 upload, widths and
  attachment start; backfill of the cached PDFs and staging CSVs.
- `page_readings` with `read_pages`: bulk lookup, thread pool, chunked
  upserts, error gating; backfill of existing JSON.
- `page_verdicts` with `agree` under `POLICY_V1`, flagged pages loaded
  from the last reader's reading with the verdict as the mark;
  `transcribe` and `report` rewired; tests.

**P1**
- A `flagged` listing command: page, every reading's row count and sum,
  printed totals, for review against the image.
- Per-run cost and throughput summary from `usage` and `seconds`.
- `refetch` for a filing whose TEOS image changed.

**P2, design for but do not build**
- Readings in S3 with `page_readings` as the index: the key already
  names the object.
- A second frame (band C and D top-up to 1,000 filings) as a plain
  list of object ids; nothing in the tables is frame-specific.

## Success metrics

| metric | target | how measured |
|---|---|---|
| API calls on re-derivation | 0 | gateway call count during `report` / `agree` on a stored frame |
| resume cost of a completed stage | 0 calls | re-run `read_pages` on the frame |
| base-pair wall time, 10,000 pages | under 6 h | run log |
| design cost, 9,347-page frame | $300–$430 | sum of `usage` × `PRICES` |
| traceability | every loaded row joins to its verdict and two readings | a query with no orphans |
| ground-truth reproduction | 58 / 2 / 23 | `agree` vs `policies` command |

## Open questions

None. The four that were open were decided on 2026-09-22 and are in the
decisions table: flagged pages are loaded from Sonnet's reading with
the verdict as the mark; the bucket is `givingtuesday-datamart` under
`irs/pdf/`; PDFs are kept indefinitely; unit tests use an in-memory
store and the fake client, with the data-dependent criteria run by
hand.

## Phasing and session briefs

Each part is one session. Start by reading this spec and the two
documents it links; the pipeline doc's *Ground truth* section carries
the numbers the acceptance criteria quote.

**Session 1 — Part A.** Deliver `givingtuesday_datamart/filing_images.py`
(`fetch_filings`, `local_pdf`, the DDL), a backfill command for the
cached PDFs and staging CSVs, and tests. Done when the Part A criteria
pass and the 610-filing frame is in S3 and the table.

**Session 2 — Part B, readings.** Deliver
`givingtuesday_datamart/page_readings.py` (`read_pages`, the DDL, the
request hash), the JSON backfill, and tests with the fake client. Done
when the first four Part B criteria pass and the sample's readings are
in the table.

**Session 3 — Part B, verdicts.** Deliver `agree`, `POLICY_V1`,
`page_verdicts`, the shared pair-comparison module, the rewired
`transcribe` and `report`, and tests. Done when the last three Part B
criteria pass.

**Session 4 — the 1,000-filing run.** Top up bands C and D, fetch, run
the stages, report by band, and update the two documents. Budget about
$400 in model cost and a day of wall time. It can run on the EC2 box
once Parts A and B exist, since every artifact is then in S3 or the
database; before that it must not, or the readings split across
machines. The box needs `poppler-utils` from the system package manager
(`pdftoppm` and `pdfimages` are not pip-installable; `pdf2image` still
needs it and `pypdfium2` cannot give the page widths the attachment cut
reads), the Python environment, the gateway key in the environment, an
instance role with read and write on the bucket, about 15 GB of disk
for the PDF cache, and a tmux or nohup session. The JSON backfill runs
from the laptop first, where the existing readings live.
