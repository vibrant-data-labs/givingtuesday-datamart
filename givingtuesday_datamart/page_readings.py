"""Page readings: one ``page_readings`` row per reading of one attachment page
by one model under one prompt, and ``read_pages``, the bulk cache-or-run that
reads only what is missing.

    python -m givingtuesday_datamart.page_readings read data/exploratory/placeholder_sample_expanded.csv --model alibaba/qwen3-vl-instruct --workers 40
    python -m givingtuesday_datamart.page_readings backfill --dry-run
    python -m givingtuesday_datamart.page_readings status

``docs/placeholder_recovery_operations.md`` (Part B of the storage spec it
was built from, now in git history), the readings half; the
verdicts are ``page_verdicts`` (``agree``), which reads through here. The
measurements behind the choices here are in
``docs/placeholder_recovery_pipeline.md``.

**The key.** A reading is identified by the page (``object_id``, ``page``),
the PDF it was rendered from (``image_sha256``, the ``filing_images`` hash at
read time, so a re-issued image makes its pages misses and leaves the old
readings where they are), the render (``dpi``), the reader (``model``,
``prompt_version``) and the request settings (``request_hash``: the SHA-256
of the canonical JSON of ``{json_mode, extras}`` — ``vlm_transcription``'s
``JSON_MODE`` and ``REQUEST_EXTRAS`` for the model). The max-tokens ladder
is a retry detail and does not key, and ``prompt_version`` is a key
column of its own, so the hash covers only the settings.

**Reading.** ``read_pages`` is ``_bulk_get_cache_or_run`` from vdl-tools with
images: one query for the batch's keys, then a thread pool of
``vlm_transcription.transcribe`` over the misses, the results upserted in
chunks of 200 from the main thread (``_internal.bulk.upsert_as_done``, the
collector ``filing_images`` uses; workers never touch the session). A
result carrying ``error`` or ``parse_error`` increments ``errors`` and sets
``last_error`` instead of ``response``, keeping whatever stamps it has; a row
at ``max_errors`` is skipped and listed, not read again. A success keeps a
prior error row's ``errors`` and ``last_error``, so the row still says the
page took two failed runs to read. ``last_error`` holds a parse failure's
full text, as the JSON folders did, and ``read_pages`` re-parses it with
today's ``_parse`` before buying the page again (``reparse``), so a parser
fix recovers every page it applies to at no cost — the bare-amount rule
did that for the sample from the folders.

**Rendering.** PNGs go to ``<cache_dir>/pages<DPI>/<object_id>/pNNN.png``,
the layout ``exploratory/placeholder_recovery.transcribe`` already uses, so
pages rendered there are reused. Rendering runs in its own small pool, one
job per chunk of ``RENDER_CHUNK`` pages of a filing: ``pdftoppm`` is a
subprocess, so the GIL is not the limit; bounding it to ``RENDER_WORKERS``
keeps forty Qwen workers from starting forty poppler processes; and each
read job waits on its own chunk's render, so reading overlaps rendering
instead of the pool idling through an hour of it. Chunks, not whole
filings, because a render is one single-threaded poppler process: on the
EC2 box (four cores, two seconds a page against the laptop's 0.4) a
1,089-page filing rendered whole held every read job behind it for the
better part of an hour while the other render workers sat on the next
giants (the frame run, 2026-09-23); in chunks of 20 the read pool consumes
each chunk as it lands and a giant spreads across the workers. The render job also
materialises the filing's PDF (``filing_images.materialise``, from the row
already read, so no worker touches the session), so on a box with an
empty cache the S3 downloads pipeline with the renders and reads rather
than running one after another before the first gateway call.

**Tests.** The store is the same small interface as ``filing_images`` —
``PostgresStore`` over a session, ``MemoryStore`` for tests — and the
gateway client is injected, so nothing here needs Postgres, poppler or the
gateway to be exercised.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy import text

from givingtuesday_datamart import filing_images, vlm_transcription
from givingtuesday_datamart._internal.bulk import (SystemicFailure, keyed_params, keyed_select, multi_row_insert,
                                                   multi_row_params, upsert_as_done)
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.filing_images import CACHE
from givingtuesday_datamart.vlm_transcription import DPI, PROMPT_VERSION

CHUNK = 200
# The circuit breaker (``_systemic``): a run whose first results are all
# error rows across this many filings is a broken box, not bad pages.
BREAKER_ROWS = 50
BREAKER_FILINGS = 3
RENDER_WORKERS = 4
RENDER_CHUNK = 20        # pages per render job; a 20-page chunk is about 40 s of pdftoppm on the box
MAX_ERRORS = 3
RESPONSE_KEYS = ("page_kind", "heading", "rows", "totals")
VLM_DIR = CACHE / "vlm"

DDL = (
    """
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
        usage           jsonb,                -- {in, out}: every call's tokens summed (from 2026-09-23; the kept call's before)
        finish          text,
        attempts        integer,
        json_mode       boolean,
        seconds         real,
        errors          integer NOT NULL DEFAULT 0,
        last_error      text,
        read_at         timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (object_id, page, image_sha256, dpi, model, prompt_version, request_hash)
    )
    """,
    "CREATE INDEX IF NOT EXISTS page_readings_model ON page_readings (model, prompt_version)",
)

Page = tuple[str, int]
Key = tuple[str, int, str, int, str, str, str]


@dataclass
class PageReading:
    """One ``page_readings`` row. Field order is the table's column order;
    the first seven fields are the primary key."""

    object_id: str
    page: int
    image_sha256: str
    dpi: int
    model: str
    prompt_version: str
    request_hash: str
    request: dict
    response: dict | None = None
    partial: bool = False
    usage: dict | None = None
    finish: str | None = None
    attempts: int | None = None
    json_mode: bool | None = None
    seconds: float | None = None
    errors: int = 0
    last_error: str | None = None
    read_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def key(self) -> Key:
        return (self.object_id, self.page, self.image_sha256, self.dpi, self.model,
                self.prompt_version, self.request_hash)


COLUMNS = tuple(f.name for f in fields(PageReading))
KEY_COLUMNS = COLUMNS[:7]
_KEY_TYPES = ("text", "integer", "text", "integer", "text", "text", "text")
JSON_COLUMNS = ("request", "response", "usage")

# One query for a batch of primary keys, seven parameters however many
# keys (``_internal.bulk.keyed_select``).
_SELECT = keyed_select("page_readings", COLUMNS, KEY_COLUMNS, _KEY_TYPES)


# ---------------------------------------------------------------------------
# The request settings and the key
# ---------------------------------------------------------------------------


def request(model: str) -> dict:
    """The request settings a reading made today runs under, for audit:
    whether JSON mode is asked for first, and the model's extra request
    fields."""
    return {"json_mode": vlm_transcription.JSON_MODE.get(model, True),
            "extras": vlm_transcription.REQUEST_EXTRAS.get(model, {})}


def settings_hash(settings: dict) -> str:
    """SHA-256 of the canonical JSON (sorted keys, no whitespace) of a
    ``{json_mode, extras}`` settings dict. The max-tokens ladder does not key."""
    canonical = json.dumps(settings, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def request_hash(model: str) -> str:
    """``settings_hash`` of today's ``request(model)``: what a reading made
    now is keyed under. The prompt version is a key column of its own."""
    return settings_hash(request(model))


def reading_key(object_id: str, page: int, image_sha256: str, model: str,
                prompt_version: str = PROMPT_VERSION, dpi: int = DPI, *, settings: dict | None = None) -> Key:
    """The primary key of a reading; under today's settings unless
    ``settings`` says what the reading actually ran under."""
    settings = request(model) if settings is None else settings
    return (object_id, page, image_sha256, dpi, model, prompt_version, settings_hash(settings))


UNEVIDENCED = {"json_mode": None, "extras": None}


def asked_json_first(stamps: Iterable[bool | None]) -> bool:
    """Whether a run asked for JSON mode first, from its files' ``_json_mode``
    stamps (the mode each kept answer came back under). ``transcribe`` only
    ever falls back out of JSON mode, so one answer in it proves the run
    asked for it; a run whose every answer is out of it did not. No stamps
    at all is the v2 sample, which ran with JSON mode on."""
    stamps = list(stamps)
    return any(stamp is True for stamp in stamps) or not any(stamp is False for stamp in stamps)


def file_settings(model: str, data: dict, json_first: bool) -> dict:
    """The request settings a stored JSON reading evidences, for the backfill.

    ``json_mode`` is the run's first-call mode (``asked_json_first``), which
    is what ``request`` records for a live reading, so a page that fell back
    out of JSON mode keys with the rest of its run. ``extras`` are the
    model's ``REQUEST_EXTRAS``, which the runs that produced the files used;
    the files do not stamp them. A file with no stamps at all never got an
    answer (a transport error, before ``_ask`` stamps anything), so it
    evidences nothing: both fields are None, the row keeps a hash of its
    own, and no live key inherits its strike.
    """
    if not any(stamp in data for stamp in ("_json_mode", "_usage", "_finish")):
        return dict(UNEVIDENCED)
    return {"json_mode": json_first, "extras": vlm_transcription.REQUEST_EXTRAS.get(model, {})}


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class PageReadingStore(ABC):
    """Rows in, rows out. ``upsert`` writes one chunk and commits it."""

    @abstractmethod
    def ensure_table(self) -> None: ...

    @abstractmethod
    def get(self, keys: Iterable[Key]) -> dict[Key, PageReading]: ...

    @abstractmethod
    def upsert(self, rows: Sequence[PageReading]) -> None: ...


class PostgresStore(PageReadingStore):
    def __init__(self, session) -> None:
        self.session = session

    def ensure_table(self) -> None:
        for statement in DDL:
            self.session.execute(text(statement))
        self.session.commit()

    def get(self, keys: Iterable[Key]) -> dict[Key, PageReading]:
        wanted = list(dict.fromkeys(keys))
        if not wanted:
            return {}
        found = self.session.execute(text(_SELECT), keyed_params(wanted, KEY_COLUMNS)).mappings().all()
        rows = [PageReading(**{c: row[c] for c in COLUMNS}) for row in found]
        return {row.key: row for row in rows}

    def upsert(self, rows: Sequence[PageReading]) -> None:
        """One statement per chunk of 200 (3,600 parameters): psycopg2's
        ``executemany`` sends one per row, and the first backfill of 7,626
        readings spent ten minutes waiting on the network that way."""
        if not rows:
            return
        self.session.execute(
            text(multi_row_insert("page_readings", COLUMNS, KEY_COLUMNS, len(rows), json_columns=JSON_COLUMNS)),
            multi_row_params([asdict(row) for row in rows], COLUMNS, json_columns=JSON_COLUMNS))
        self.session.commit()


class MemoryStore(PageReadingStore):
    """A dict, plus the size of every committed chunk, for tests."""

    def __init__(self) -> None:
        self.rows: dict[Key, PageReading] = {}
        self.commits: list[int] = []

    def ensure_table(self) -> None:
        pass

    def get(self, keys: Iterable[Key]) -> dict[Key, PageReading]:
        return {key: self.rows[key] for key in keys if key in self.rows}

    def upsert(self, rows: Sequence[PageReading]) -> None:
        if not rows:
            return
        for row in rows:
            self.rows[row.key] = row
        self.commits.append(len(rows))


def _store(session) -> PageReadingStore:
    return session if isinstance(session, PageReadingStore) else PostgresStore(session)


def ensure_table(session) -> None:
    """Create ``page_readings`` and its index if they do not exist."""
    _store(session).ensure_table()


# ---------------------------------------------------------------------------
# One result into one row
# ---------------------------------------------------------------------------


def reading_from_result(key: Key, request_settings: dict, data: dict, prior: PageReading | None = None, *,
                        read_at: datetime | None = None, json_mode_default: bool | None = None) -> PageReading:
    """A ``transcribe`` result (or a stored JSON file of one) as a row.

    A result with ``error`` or ``parse_error`` is an error row: ``errors`` is
    the prior row's plus one, ``last_error`` the message (a parse failure's
    full text, so it can be re-parsed later), ``response`` NULL, and the
    stamps it carries (``_usage``, ``_seconds``, ``_attempts``…) are kept.
    Anything else is a success: ``response`` is the page_kind, heading, rows
    and totals part; a prior error row's ``errors`` and ``last_error`` stay.
    """
    row = PageReading(*key, request=request_settings,
                      partial=bool(data.get("_partial")), usage=data.get("_usage"), finish=data.get("_finish"),
                      attempts=data.get("_attempts"), json_mode=data.get("_json_mode", json_mode_default),
                      seconds=data.get("_seconds"))
    if read_at is not None:
        row.read_at = read_at
    if "error" in data or "parse_error" in data:
        row.errors = (prior.errors if prior is not None else 0) + 1
        row.last_error = data["error"] if "error" in data else data["parse_error"]
    else:
        row.response = {k: data[k] for k in RESPONSE_KEYS if k in data}
        if prior is not None:
            row.errors, row.last_error = prior.errors, prior.last_error
    return row


def reparse(row: PageReading) -> PageReading | None:
    """A stored parse failure read again with today's parser: the row as a
    success when the text now yields a page (``errors`` and ``last_error``
    kept, so the row still says what happened; ``read_at`` kept, since the
    model call is what it dates), else None. A transport error's message
    never yields a page, and neither does a gateway error body."""
    if row.response is not None or not row.last_error:
        return None
    data = vlm_transcription._parse(row.last_error)
    if "parse_error" in data or "page_kind" not in data or not isinstance(data.get("rows"), list):
        return None
    return replace(row, response={k: data[k] for k in RESPONSE_KEYS if k in data}, partial=bool(data.get("_partial")))


def _same(a: PageReading, b: PageReading) -> bool:
    """Equal as stored: ``seconds`` is a Postgres ``real``, so it comes back
    at float32 precision and is compared to that."""
    x, y = asdict(a), asdict(b)
    sa, sb = x.pop("seconds"), y.pop("seconds")
    if x != y:
        return False
    if sa is None or sb is None:
        return sa is sb
    return math.isclose(sa, sb, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# read_pages
# ---------------------------------------------------------------------------


@dataclass
class ReadResult:
    """What ``read_pages`` gives back. Every page asked for lands in exactly
    one: ``responses`` (a hit, or read this run), ``skipped`` (at
    ``max_errors`` before the run; the row that gated it), ``failed`` (read
    this run and errored; the row as stored, ``errors`` incremented).
    ``bought`` is what the run paid for: pages sent to the model, rows and
    errors back, partial pages, and the tokens in and out (zero throughout
    when every page was stored)."""

    responses: dict[Page, dict] = field(default_factory=dict)
    skipped: dict[Page, PageReading] = field(default_factory=dict)
    failed: dict[Page, PageReading] = field(default_factory=dict)
    bought: dict[str, int] = field(default_factory=lambda: {"pages": 0, "rows": 0, "errors": 0, "partial": 0,
                                                            "in": 0, "out": 0})


def page_dir(cache_dir: Path, object_id: str) -> Path:
    return cache_dir / f"pages{DPI}" / object_id


def _readable(row: filing_images.FilingImage | None) -> bool:
    return row is not None and row.fetched and bool(row.sha256)


def _one_page_pdf() -> bytes:
    """A valid one-page PDF, blank, 72 by 72 points: the fixture
    ``_prove_pdftoppm`` renders. Built with its cross-reference table so no
    poppler version has to reconstruct one."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _prove_pdftoppm() -> None:
    """Render the one-page fixture into a temporary directory, so a poppler
    that is installed but cannot render (a broken library, a bad build)
    fails here and not on the frame's first chunk."""
    tmp = Path(tempfile.mkdtemp(prefix="pdftoppm-check-"))
    try:
        pdf = tmp / "one.pdf"
        pdf.write_bytes(_one_page_pdf())
        pngs = vlm_transcription.render(pdf, 1, 1, tmp / "out")
        if not pngs or not pngs[0].exists():
            raise RuntimeError("no PNG came out")
    except Exception as exc:                              # noqa: BLE001
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        raise RuntimeError("pdftoppm is installed but cannot render a page: "
                           f"{detail.strip()[:300]}") from exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _require_pdftoppm() -> None:
    """Fail before any render or gateway call on a box without a working
    poppler: not on PATH, or installed and unable to render the fixture."""
    if shutil.which("pdftoppm") is None:
        raise RuntimeError("pdftoppm is not on PATH; install poppler-utils (apt) or poppler (brew)")
    _prove_pdftoppm()


class MissingReadings(LookupError):
    """``read_pages`` with ``buy=False`` (or under settings that are not
    today's) found pages without a reading of the model: nothing was read.
    Carries what a caller needs without parsing the message."""

    def __init__(self, model: str, prompt_version: str, pages: list, why: str) -> None:
        self.model, self.prompt_version, self.pages, self.why = model, prompt_version, list(pages), why
        super().__init__(f"{self.summary}: {self.pages[:5]}")

    @property
    def summary(self) -> str:
        return f"{len(self.pages)} pages have no reading of {self.model} {self.prompt_version} and {self.why}"


class FilingUnreadable(Exception):
    """The render job could not produce a filing's PNGs — the PDF could not
    be materialised, or pdftoppm failed. The message is the pages' error."""


class _PdfOnce:
    """``materialise`` once per filing per run, however many chunk jobs the
    filing has: the first job to ask downloads (or hashes the cached file)
    while the others wait on its future, so a 55-chunk filing is one S3
    GET and one hash rather than four concurrent downloads and 55 hashes.
    The in-process half of a per-chunk render lock; the base readers, as
    two processes, still each do it once."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.futures: dict[str, Future] = {}

    def pdf(self, row: filing_images.FilingImage, cache_dir: Path, s3) -> Path:
        with self.lock:
            future = self.futures.get(row.object_id)
            first = future is None
            if first:
                future = self.futures[row.object_id] = Future()
        if first:
            try:
                future.set_result(filing_images.materialise(row, cache_dir, s3=s3))
            except BaseException as exc:                  # noqa: BLE001 — the waiters get it too
                future.set_exception(exc)
                raise
        return future.result()


def _render_filing(row: filing_images.FilingImage, first: int, last: int, cache_dir: Path, s3,
                   pdfs: _PdfOnce) -> list[Path]:
    """Runs in the render pool: the filing's PDF onto local disk (from the
    cache when it hashes to the row, else S3 through the client the main
    thread built; once per filing, through ``pdfs``), then its span of
    pages to PNGs. Takes the row, never the session. A failure at either
    step is logged once here and raised as ``FilingUnreadable`` for the
    filing's read jobs to store as error rows."""
    try:
        pdf = pdfs.pdf(row, cache_dir, s3)
    except Exception as exc:                              # noqa: BLE001
        raise _unreadable(row.object_id, first, last, "pdf", exc) from exc
    started = time.monotonic()
    try:
        pngs = vlm_transcription.render(pdf, first, last, page_dir(cache_dir, row.object_id))
    except Exception as exc:                              # noqa: BLE001
        raise _unreadable(row.object_id, first, last, "render", exc) from exc
    # One line per filing, so a run log carries the render time of the
    # empty-cache path (``materialise`` logs the S3 download the same way).
    logger.info("render: %s pages %d-%d, %d PNGs in %.1f s", row.object_id, first, last, len(pngs),
                time.monotonic() - started)
    return pngs


def _unreadable(object_id: str, first: int, last: int, stage: str, exc: BaseException) -> FilingUnreadable:
    """``<stage>: <type>: <detail>`` — poppler's stderr when it has one, so
    the row says what pdftoppm said rather than "exit status 1"."""
    detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
    if isinstance(detail, bytes):
        detail = detail.decode(errors="replace")
    message = f"{stage}: {type(exc).__name__}: {detail.strip()[:300]}"
    logger.error("%s: pages %d-%d cannot be read this run: %s", object_id, first, last, message)
    return FilingUnreadable(message)


def _systemic(rows: list[PageReading]) -> str | bool:
    """``upsert_as_done``'s breaker for a reading run. A failed render or
    gateway call is one error on its page, and ``run`` retries a page up to
    ``MAX_ERRORS`` times in one invocation, so one run on a box with a
    broken poppler or a dead key would leave every page's base rows at the
    limit and the next run, on the fixed box, would read the whole frame
    through the escalation readers. So: while every result is an error,
    nothing is written; once ``BREAKER_ROWS`` of them span
    ``BREAKER_FILINGS`` filings the run stops with nothing written and the
    first error named; the first page read releases the run. A filing of
    its own that cannot be read keeps its attempts, since its errors never
    reach three filings."""
    if any(row.response is not None for row in rows):
        return True
    filings = {row.object_id for row in rows}
    if len(rows) < BREAKER_ROWS or len(filings) < BREAKER_FILINGS:
        return False
    first = rows[0]
    return (f"the first {len(rows)} results of this run are all errors, from {len(filings)} filings: "
            "that looks systemic (poppler, the gateway, S3, disk), not the pages', so nothing is written "
            f"and every page keeps its attempts. The first: {first.object_id} p{first.page:03d}: "
            f"{(first.last_error or '')[:200]}")


def _read_one(client, model: str, rendered: Future, key: Key, request_settings: dict,
              prior: PageReading | None, pages_dir: Path) -> PageReading:
    """Runs in a worker thread: wait for the filing's render, read the page.
    Never the session. A failed render is this page's error result, so a
    filing pdftoppm cannot draw costs its pages one error each, not the run."""
    png = pages_dir / f"p{key[1]:03d}.png"
    try:
        rendered.result()
    except FilingUnreadable as exc:                       # logged once by the render job
        data = {"error": str(exc)}
    else:
        if png.exists():
            data = vlm_transcription.transcribe(client, model, png)
        else:                                             # the one page pdftoppm left out of its span
            data = {"error": f"render: no PNG for the page ({png.name}); the rest of its span rendered"}
    return reading_from_result(key, request_settings, data, prior)


def read_pages(session, pages: Sequence[Page], model: str, *, workers: int,
               prompt_version: str = PROMPT_VERSION, max_errors: int = MAX_ERRORS,
               cache_dir: Path = CACHE, client=None, filing_store=None, s3=None,
               settings: dict | None = None, buy: bool = True) -> ReadResult:
    """Read ``pages`` (``(object_id, page)`` pairs) through ``model``, from the
    table where a reading exists and through the gateway where it does not.

    1. Each page's ``image_sha256`` comes from ``filing_images``; a page of
       an unfetched filing raises ``LookupError`` and nothing is read.
    2. One query finds the batch's rows. A row with a response is a hit. An
       error row whose stored text parses today (``reparse``) becomes a hit
       and is stored, at no cost, whatever its ``errors``. A row at
       ``max_errors`` is skipped and listed; anything else is a miss.
    3. Misses are rendered in a pool of ``RENDER_WORKERS``, one job per
       chunk of ``RENDER_CHUNK`` pages of a filing: its PDF materialised
       from the cache or S3 (from the row read in step 1, never the
       session), then the chunk's pages to PNGs. Each read job waits on its
       own chunk's render, so download, render and read pipeline per chunk
       while the read pool of ``workers`` calls ``vlm_transcription.transcribe``.
    4. The main thread collects the results and upserts them 200 at a time.
    5. The result carries page → response for hits and successful reads, the
       skipped pages with the rows that gated them, and the pages that
       errored this run.

    ``settings`` are the request settings to look the readings up under:
    today's ``request(model)`` by default, which is also the only setting a
    page can be *read* under, so a miss under any other raises
    ``LookupError`` before a render or a call (the sample's Qwen v3 rows,
    keyed on the JSON mode their run asked for, are reached this way). With
    ``buy`` false a miss under any settings raises the same way: the run is
    a re-derivation from stored readings and must cost nothing.

    ``session`` is a SQLAlchemy session or a ``PageReadingStore``;
    ``filing_store`` a ``FilingImageStore`` when the readings and the
    filings are not on the same session (tests); ``client`` the gateway
    client and ``s3`` the boto3 client, each built when not given.
    """
    store = _store(session)
    store.ensure_table()
    filings = filing_images._store(filing_store if filing_store is not None else session)
    wanted: list[Page] = list(dict.fromkeys((str(oid), int(page)) for oid, page in pages))
    images = filings.get({oid for oid, _ in wanted})
    unfetched = sorted({oid for oid, _ in wanted if not _readable(images.get(oid))})
    if unfetched:
        raise LookupError(f"{len(unfetched)} filings are not fetched, nothing read: {unfetched[:5]}")
    outside = [(oid, page) for oid, page in wanted if not 1 <= page <= (images[oid].pages or 0)]
    if outside:
        raise ValueError(f"{len(outside)} pages are outside their filing: {outside[:5]}")

    settings = request(model) if settings is None else settings
    keys = {(oid, page): reading_key(oid, page, images[oid].sha256, model, prompt_version, settings=settings)
            for oid, page in wanted}
    existing = store.get(keys.values())
    result = ReadResult()
    misses: list[tuple[Page, PageReading | None]] = []
    reparsed: list[PageReading] = []
    for page, key in keys.items():
        row = existing.get(key)
        if row is not None and row.response is not None:
            result.responses[page] = row.response
        elif row is not None and (fixed := reparse(row)) is not None:
            reparsed.append(fixed)
            result.responses[page] = fixed.response
        elif row is not None and row.errors >= max_errors:
            result.skipped[page] = row
        else:
            misses.append((page, row))
    for start in range(0, len(reparsed), CHUNK):
        store.upsert(reparsed[start:start + CHUNK])
    logger.info("read_pages: %s %s: %d pages, %d stored (%d of them parse failures re-parsed at no cost), "
                "%d to read, %d skipped at %d errors", model, prompt_version, len(wanted), len(result.responses),
                len(reparsed), len(misses), len(result.skipped), max_errors)
    for (oid, page), row in result.skipped.items():
        logger.warning("%s p%03d skipped after %d errors: %s", oid, page, row.errors, (row.last_error or "")[:120])
    if not misses:
        return result
    if not buy or settings != request(model):
        why = "the run buys nothing" if not buy else "the settings asked for are not today's, so nothing can be read under them"
        raise MissingReadings(model, prompt_version, [page for page, _ in misses], why)
    _require_pdftoppm()

    # One render job per chunk of a filing's pages, keyed on the chunk the
    # page falls in, so a read job waits on its own chunk and not on the
    # whole filing.
    spans: dict[tuple[str, int], tuple[int, int]] = {}
    for (oid, page), _ in misses:
        chunk = (oid, page // RENDER_CHUNK)
        first, last = spans.get(chunk, (page, page))
        spans[chunk] = (min(first, page), max(last, page))
    client = client if client is not None else vlm_transcription.client()
    # Built here, once: boto3 documents constructing clients from the
    # default session as not thread-safe, and on an empty cache the render
    # workers would otherwise each build one through the unlocked lru_cache.
    s3 = s3 if s3 is not None else filing_images._s3_client(RENDER_WORKERS)
    tally = result.bought
    with ThreadPoolExecutor(max_workers=RENDER_WORKERS) as renders, ThreadPoolExecutor(max_workers=workers) as pool:
        pdfs = _PdfOnce()
        rendered = {chunk: renders.submit(_render_filing, images[chunk[0]], first, last, cache_dir, s3, pdfs)
                    for chunk, (first, last) in spans.items()}
        futures = [pool.submit(_read_one, client, model, rendered[(page[0], page[1] // RENDER_CHUNK)], keys[page],
                               settings, prior, page_dir(cache_dir, page[0])) for page, prior in misses]
        def stop() -> None:
            # On the way out for any reason but completion (an interrupt in
            # the wait, the caller raising): the renders not yet started
            # are dropped before ``upsert_as_done`` waits for the reads in
            # flight, so a read waiting on one of them fails fast instead of
            # holding the stop through the render queue; a filing's
            # five-minute render is not worth finishing for pages nobody
            # will read this run.
            if sum(future.cancel() for future in rendered.values()):
                logger.warning("stopping: the renders not yet started are cancelled")

        for row in upsert_as_done(store, futures, CHUNK, on_stop=stop, breaker=_systemic):
            page = (row.object_id, row.page)
            tally["pages"] += 1
            if row.response is None:
                result.failed[page] = row
                tally["errors"] += 1
                logger.warning("%s p%03d error %d: %s", row.object_id, row.page, row.errors, (row.last_error or "")[:120])
            else:
                result.responses[page] = row.response
                tally["rows"] += len(row.response.get("rows") or [])
                tally["partial"] += int(row.partial)
            tally["in"] += (row.usage or {}).get("in") or 0
            tally["out"] += (row.usage or {}).get("out") or 0
            if tally["pages"] % 25 == 0 or tally["pages"] == len(futures):
                spent = vlm_transcription.cost(model, tally["in"], tally["out"])
                logger.info("read_pages: %d/%d pages, %d rows, %d errors, %d partial%s", tally["pages"], len(futures),
                            tally["rows"], tally["errors"], tally["partial"],
                            f", ${spent:.2f}" if spent is not None else "")
    return result


def frame_pages(filing_store, object_ids: Iterable[str]) -> list[Page]:
    """Every attachment page of each fetched filing, from ``filing_images``.
    Filings not fetched, or without an attachment, are logged and left out."""
    ids = list(dict.fromkeys(object_ids))
    rows = filing_images._store(filing_store).get(ids)
    pages: list[Page] = []
    for oid in ids:
        row = rows.get(oid)
        if not _readable(row):
            logger.warning("%s is not fetched (%s); left out", oid, row.status if row else "no row")
        elif not row.attachment_from:
            logger.info("%s has no attachment; left out", oid)
        else:
            pages.extend((oid, page) for page in range(row.attachment_from, row.attachment_from + row.attachment_pages))
    return pages


# ---------------------------------------------------------------------------
# Backfill from the JSON folders
# ---------------------------------------------------------------------------


_PAGE_FILE = re.compile(r"^p(\d+)\.json$")
_FOLDER = re.compile(r"^(?P<model>.+?)(?:-(?P<version>v\d+))?$")
# Experiments, not readings to keep (the spec's "What exists today").
_SKIPPED_FOLDERS = (
    (re.compile(r"-v\d+b$"), "a second read under identical settings: the key cannot hold two, "
                             "and same-model agreement was rejected"),
    (re.compile(r"-max-v\d+$"), "Sonnet at maximum reasoning, a 21-page experiment"),
)


def skipped_folder(name: str) -> str | None:
    """Why a folder is not backfilled, or None."""
    for pattern, reason in _SKIPPED_FOLDERS:
        if pattern.search(name):
            return reason
    return None


def folder_reader(name: str) -> tuple[str, str]:
    """``<model with '/' as '__'>[-v<N>]`` → (model, prompt version); no
    suffix is the sample's first prompt, v2."""
    match = _FOLDER.match(name)
    return match.group("model").replace("__", "/"), match.group("version") or "v2"


def backfill(session, *, vlm_dir: Path = VLM_DIR, dry_run: bool = False, filing_store=None) -> dict:
    """Load every ``<vlm_dir>/<folder>/<object_id>/pNNN.json`` into the table
    without a gateway call.

    Folder → reader by ``folder_reader``; the ``-v4b`` repeat reads and the
    Sonnet maximum-reasoning folder are skipped and logged. Per file: the page
    from the filename, ``image_sha256`` from ``filing_images`` (a filing not
    in the table is listed under ``missing``, not a crash), ``dpi`` 200, the
    stamps as ``reading_from_result`` reads them, ``read_at`` the file's
    mtime, and ``request``/``request_hash`` from what the folder and the
    file evidence (``asked_json_first``, ``file_settings``): the v2 files
    carry only ``_attempts``, ``_finish``, ``_max_tokens``, ``_model``,
    ``_seconds``, ``_usage`` and ran with JSON mode on, and the v3 Qwen run
    asked for it first too (468 of its answers came back in it), so those
    rows are keyed apart from Qwen v4, which ran without it. Idempotent: a
    second run reads the stored rows back, finds nothing new or changed and
    writes nothing. The folders are left where they are.

    Returns ``{"readers": {(model, version): {files, rows, new, changed,
    errors}}, "skipped": {folder: reason}, "missing": [object_id…],
    "unreadable": [path…]}`` — ``unreadable`` the files that are not JSON
    (an empty or half-written file a killed run left), listed and passed
    over rather than aborting the run; a dry run stops after counting files
    and touches no table.
    """
    plan: list[tuple[str, str, list[Path]]] = []
    skipped: dict[str, str] = {}
    for folder in sorted(p for p in vlm_dir.iterdir() if p.is_dir()):
        if reason := skipped_folder(folder.name):
            skipped[folder.name] = reason
            logger.info("backfill: skipping %s: %s", folder.name, reason)
            continue
        model, version = folder_reader(folder.name)
        files = sorted(path for path in folder.glob("*/p*.json") if _PAGE_FILE.match(path.name))
        if model not in vlm_transcription.PRICES:
            logger.warning("backfill: %s → %s is not a priced model; loaded, cost unknown", folder.name, model)
        logger.info("backfill: %s → %s %s, %d files", folder.name, model, version, len(files))
        plan.append((model, version, files))
    readers: dict[tuple[str, str], dict] = {(model, version): {"files": len(files)} for model, version, files in plan}
    if dry_run:
        return {"readers": readers, "skipped": skipped, "missing": [], "unreadable": []}

    store = _store(session)
    store.ensure_table()
    filings = filing_images._store(filing_store if filing_store is not None else session)
    object_ids = {path.parent.name for _, _, files in plan for path in files}
    images = filings.get(object_ids)
    missing = sorted(oid for oid in object_ids if not _readable(images.get(oid)))
    for oid in missing:
        logger.error("backfill: %s is not a fetched filing_images row; its readings are not loaded", oid)

    unreadable: list[str] = []
    for model, version, files in plan:
        readings: dict[Path, dict] = {}
        for path in files:
            try:
                readings[path] = json.loads(path.read_text())
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.error("backfill: %s is not a JSON reading, passed over: %s", path, exc)
                unreadable.append(str(path))
        json_first = asked_json_first(data.get("_json_mode") for data in readings.values())
        rows = []
        for path, data in readings.items():
            oid, page = path.parent.name, int(_PAGE_FILE.match(path.name).group(1))
            if oid in missing:
                continue
            settings = file_settings(model, data, json_first)
            key = reading_key(oid, page, images[oid].sha256, model, version, settings=settings)
            rows.append(reading_from_result(key, settings, data, json_mode_default=settings["json_mode"],
                                            read_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)))
        existing = store.get(row.key for row in rows)
        # Only what the table lacks or holds differently is written: the
        # idempotent re-run moves nothing over the wire.
        to_write = [row for row in rows if row.key not in existing or not _same(existing[row.key], row)]
        summary = readers[(model, version)]
        summary.update(rows=len(rows), new=sum(row.key not in existing for row in rows),
                       changed=sum(row.key in existing for row in to_write),
                       errors=sum(row.response is None for row in rows))
        for start in range(0, len(to_write), CHUNK):
            store.upsert(to_write[start:start + CHUNK])
        logger.info("backfill: %s %s: %d rows (%d new, %d changed, %d errors); %d written", model, version,
                    len(rows), summary["new"], summary["changed"], summary["errors"], len(to_write))
    return {"readers": readers, "skipped": skipped, "missing": missing, "unreadable": unreadable}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def status_report(session) -> str:
    """Rows by model and prompt version: readings with a response, rows
    without one, partial pages, and the cost from ``usage`` at ``PRICES``."""
    lines = [f"{'model':<32}{'prompt':<8}{'rows':>7}{'hits':>7}{'failed':>8}{'partial':>9}{'$':>9}"]
    total = 0.0
    for model, version, n, hits, partial, tokens_in, tokens_out in session.execute(text(
            "SELECT model, prompt_version, count(*), count(response), count(*) FILTER (WHERE partial), "
            "coalesce(sum(CAST(usage->>'in' AS bigint)), 0), coalesce(sum(CAST(usage->>'out' AS bigint)), 0) "
            "FROM page_readings GROUP BY 1, 2 ORDER BY 1, 2")):
        spent = vlm_transcription.cost(model, int(tokens_in), int(tokens_out))
        total += spent or 0.0
        n, hits, partial = int(n), int(hits), int(partial)
        dollars = f"{spent:.2f}" if spent is not None else "?"
        lines.append(f"{model:<32}{version:<8}{n:>7,}{hits:>7,}{n - hits:>8,}{partial:>9,}{dollars:>9}")
    lines.append(f"{'total':<32}{'':<8}{'':>7}{'':>7}{'':>8}{'':>9}{total:>9.2f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cache", type=Path, default=CACHE, help="pdfs/ and pages200/ live here")
    sub = parser.add_subparsers(dest="command", required=True)

    read = sub.add_parser("read", help="read every attachment page of the fetched filings in a frame CSV, or of the ids given")
    read.add_argument("filings", nargs="+", help="frame CSV path(s) or object ids")
    read.add_argument("--model", required=True, help="gateway model id, e.g. alibaba/qwen3-vl-instruct")
    read.add_argument("--workers", type=int, required=True, help="Qwen 80, Flash Lite 24, 3.8 Flash and Sonnet 24 (page_verdicts.WORKERS)")
    read.add_argument("--prompt-version", default=PROMPT_VERSION)
    read.add_argument("--max-errors", type=int, default=MAX_ERRORS)

    back = sub.add_parser("backfill", help="load the JSON reading folders; no gateway call")
    back.add_argument("--vlm-dir", type=Path, default=VLM_DIR)
    back.add_argument("--dry-run", action="store_true", help="count files per (model, prompt version), then stop")

    sub.add_parser("status", help="rows by model and prompt version, with cost")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.command == "backfill" and args.dry_run:
        _print_backfill(backfill(None, vlm_dir=args.vlm_dir, dry_run=True))
        return

    from givingtuesday_datamart._internal.db import get_session
    from givingtuesday_datamart.ingestion import datamart_config

    with get_session(config=datamart_config()) as session:
        if args.command == "read":
            ids = [f.object_id for token in args.filings for f in filing_images._read_frame(token)]
            pages = frame_pages(session, ids)
            try:
                result = read_pages(session, pages, args.model, workers=args.workers,
                                    prompt_version=args.prompt_version, max_errors=args.max_errors,
                                    cache_dir=args.cache)
            except SystemicFailure as exc:
                print(f"STOPPED: {exc}", file=sys.stderr)
                sys.exit(1)
            print(f"{len(pages)} pages: {len(result.responses)} with a reading, {len(result.failed)} errored this run, "
                  f"{len(result.skipped)} skipped at {args.max_errors} errors")
            for (oid, page), row in sorted(result.skipped.items()):
                print(f"  skipped {oid} p{page:03d}: {row.errors} errors, last: {(row.last_error or '')[:100]}")
        elif args.command == "backfill":
            _print_backfill(backfill(session, vlm_dir=args.vlm_dir))
            print(status_report(session))
        elif args.command == "status":
            print(status_report(session))


def _print_backfill(result: dict) -> None:
    for folder, reason in result["skipped"].items():
        print(f"skipped {folder}: {reason}")
    for (model, version), summary in sorted(result["readers"].items()):
        detail = "".join(f"  {k} {v}" for k, v in summary.items() if k != "files")
        print(f"{model:<32}{version:<6}{summary['files']:>7} files{detail}")
    if result["missing"]:
        print(f"{len(result['missing'])} filings are not in filing_images; their readings were not loaded: "
              f"{result['missing'][:10]}")
    for path in result["unreadable"]:
        print(f"not a JSON reading, passed over: {path}")


if __name__ == "__main__":
    main()
