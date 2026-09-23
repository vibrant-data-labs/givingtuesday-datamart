"""Filing images: every IRS PDF the recovery pipeline reads, in S3, with one
``filing_images`` row per filing saying where it came from, what it holds,
and what went wrong when nothing did.

    python -m givingtuesday_datamart.filing_images fetch data/exploratory/placeholder_sample_expanded.csv
    python -m givingtuesday_datamart.filing_images backfill --dry-run
    python -m givingtuesday_datamart.filing_images status
    python -m givingtuesday_datamart.filing_images verify

Part A of ``docs/placeholder_storage_spec.md``. The measurements behind
every choice here are in ``docs/placeholder_recovery_pipeline.md``.

**Fetching.** ``fetch_filings`` resolves each object id through the IRS
index and TEOS (``irs_source``), downloads the newest image first with older
ones as fallbacks — the loop that ``exploratory/placeholder_recovery.stage``
ran, moved here with its status strings — hashes it, reads the page widths
with ``pdfimages`` to find where the filer's attachment starts, uploads the
PDF to ``s3://givingtuesday-datamart/irs/pdf/<object_id>.pdf`` and upserts
the row. Downloads and uploads run in a thread pool; the database session is
touched only from the main thread, in chunks of 50 rows per commit, so a
crash loses at most one chunk and a re-run picks up where it stopped.

**Statuses.** ``fetched`` and ``no_attachment`` (fetched, but every page is
the IRS's own rendering of the XML) are the two successful outcomes and are
never fetched again unless ``refetch``; a fetched row that fails a re-fetch
keeps its status and its object. The IRS's failures are ``no_teos_image``
(TEOS lists no image), ``pdf_unavailable:<http code>`` (listed, not served —
every one seen so far is an image the IRS generated in 2022), ``not_a_pdf``
(the bytes served, or poppler reading them, say so) and
``lookup_failed:<exception>`` (in no index CSV). Ours are
``teos_failed:<exception>`` (the listing request itself),
``widths_failed:<exception>`` (poppler failed rather than refused) and
``upload_failed:<exception>`` (S3, credentials or disk after a good
download). Every failure keeps ``attempts`` and ``last_error``.
``no_teos_image`` is permanent: TEOS's own listing said so, and an image
that appears months later is fetched with ``refetch``, not by retrying.
Every other failure is re-tried until it has three attempts, then left
alone until ``refetch``; a 404 keeps its attempts because the 2022 batch
may come back. The backfill re-does only its own ``upload_failed`` and
``widths_failed`` rows. The unreachable set, the 404s by image year and the
no-attachment filings are therefore queries on this table, not files.

**Reading back.** ``local_pdf`` (a session and an id) and ``materialise``
(a row already read, for worker threads) are the only ways renders and
readings get a PDF: from the cache directory when the cached file's hash
matches the row, else downloaded from S3 and verified. TEOS is never
consulted again for a fetched filing, and a re-issued image (a new
``sha256`` on the row) makes the stale cached file miss.

**Tests.** The store is a small interface — ``PostgresStore`` over a
SQLAlchemy session, ``MemoryStore`` for tests — and the network edges
(``irs_source`` and the boto3 client) are plain module attributes and an
injected client, so nothing here needs Postgres, S3 or the IRS to be
exercised.
"""

from __future__ import annotations

import argparse
import base64
import csv
import functools
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, NamedTuple, Sequence

from sqlalchemy import text

from givingtuesday_datamart import irs_source
from givingtuesday_datamart._internal.bulk import multi_row_insert, multi_row_params, upsert_as_done
from givingtuesday_datamart._internal.logger import logger

BUCKET = "givingtuesday-datamart"
PREFIX = "irs/pdf"
CACHE = Path.home() / ".cache" / "irs_index"
MAX_ATTEMPTS = 3
CHUNK = 50
FETCHED = ("fetched", "no_attachment")
# Not retried at all without refetch: TEOS's own listing said there is no
# image, and one that appears months later is a refetch, not a retry. A 404
# keeps its attempts because the 2022 batch may come back (Zein, 2026-09-22).
PERMANENT = ("no_teos_image",)

STAGING_CSVS = (Path("data/exploratory/placeholder_staging.csv"),
                Path("data/exploratory/placeholder_staging_expanded.csv"))
IMAGE_404_CSV = Path("data/exploratory/placeholder_404_images_expanded.csv")

DDL = (
    """
    CREATE TABLE IF NOT EXISTS filing_images (
        object_id        text PRIMARY KEY,
        filerein         text NOT NULL,
        taxyear          integer,
        index_year       text,                 -- which IRS index listed it
        teos_url         text,                 -- the STATICFILEPATH fetched
        image_generated  date,                 -- from the PDF token, for the 404 report
        status           text NOT NULL,
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
    )
    """,
    "CREATE INDEX IF NOT EXISTS filing_images_status ON filing_images (status)",
)


@dataclass
class FilingImage:
    """One ``filing_images`` row. Field order is the table's column order."""

    object_id: str
    filerein: str
    taxyear: int | None
    index_year: str | None
    teos_url: str | None
    image_generated: date | None
    status: str
    attempts: int = 0
    last_error: str | None = None
    fetched_at: datetime | None = None
    s3_key: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    pages: int | None = None
    page_widths: list[int] | None = None
    attachment_from: int | None = None
    attachment_pages: int | None = None

    @property
    def fetched(self) -> bool:
        return self.status in FETCHED


COLUMNS = tuple(f.name for f in fields(FilingImage))

_SELECT_ALL = f"SELECT {', '.join(COLUMNS)} FROM filing_images"
_SELECT = f"{_SELECT_ALL} WHERE object_id = ANY(:ids)"


class Filing(NamedTuple):
    """What a caller may know about a filing before it is fetched. The EIN and
    tax year come from the IRS index when not given; a frame CSV gives both."""

    object_id: str
    filerein: str | None = None
    taxyear: int | None = None


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class FilingImageStore(ABC):
    """Rows in, rows out. ``upsert`` writes one chunk and commits it."""

    @abstractmethod
    def ensure_table(self) -> None: ...

    @abstractmethod
    def get(self, object_ids: Iterable[str]) -> dict[str, FilingImage]: ...

    @abstractmethod
    def upsert(self, rows: Sequence[FilingImage]) -> None: ...

    @abstractmethod
    def all(self) -> list[FilingImage]: ...


class PostgresStore(FilingImageStore):
    def __init__(self, session) -> None:
        self.session = session

    def ensure_table(self) -> None:
        for statement in DDL:
            self.session.execute(text(statement))
        self.session.commit()

    def get(self, object_ids: Iterable[str]) -> dict[str, FilingImage]:
        ids = list(object_ids)
        if not ids:
            return {}
        found = self.session.execute(text(_SELECT), {"ids": ids}).mappings().all()
        return {row["object_id"]: FilingImage(**{c: row[c] for c in COLUMNS}) for row in found}

    def upsert(self, rows: Sequence[FilingImage]) -> None:
        """One statement per chunk: psycopg2's ``executemany`` would send one
        per row, a round trip each."""
        if not rows:
            return
        self.session.execute(text(multi_row_insert("filing_images", COLUMNS, ("object_id",), len(rows))),
                             multi_row_params([asdict(row) for row in rows], COLUMNS))
        self.session.commit()

    def all(self) -> list[FilingImage]:
        found = self.session.execute(text(_SELECT_ALL)).mappings().all()
        return [FilingImage(**{c: row[c] for c in COLUMNS}) for row in found]


class MemoryStore(FilingImageStore):
    """A dict, plus the size of every committed chunk, for tests."""

    def __init__(self) -> None:
        self.rows: dict[str, FilingImage] = {}
        self.commits: list[int] = []

    def ensure_table(self) -> None:
        pass

    def get(self, object_ids: Iterable[str]) -> dict[str, FilingImage]:
        return {oid: self.rows[oid] for oid in object_ids if oid in self.rows}

    def upsert(self, rows: Sequence[FilingImage]) -> None:
        if not rows:
            return
        for row in rows:
            self.rows[row.object_id] = row
        self.commits.append(len(rows))

    def all(self) -> list[FilingImage]:
        return list(self.rows.values())


def _store(session) -> FilingImageStore:
    return session if isinstance(session, FilingImageStore) else PostgresStore(session)


def ensure_table(session) -> None:
    """Create ``filing_images`` and its index if they do not exist."""
    _store(session).ensure_table()


# ---------------------------------------------------------------------------
# Fetching one filing
# ---------------------------------------------------------------------------


class Fetched(NamedTuple):
    """What TEOS gave for one filing: a status, never an exception."""

    status: str
    row: irs_source.IndexRow | None
    image: irs_source.Image | None     # the image served, or on failure the newest one listed
    payload: bytes | None
    error: str | None


def fetch_image(object_id: str, cache_dir: Path = CACHE, *,
                index: Mapping[str, irs_source.IndexRow] | None = None) -> Fetched:
    """Download one filing's image, newest TEOS image first with older ones
    as fallbacks.

    A filing can fail to reach transcription for reasons that have nothing
    to do with it, and they must not be scored as extraction failures. TEOS
    lists no image for some filings, and for others it lists a
    ``STATICFILEPATH`` the IRS no longer serves — Schusterman's 2020 990-PF
    is indexed and returns a 302 to an error page. Both are concentrated in
    older tax years.

    ``index`` is the frame's rows from ``_index_frame``; without it the
    filing is looked up on its own, a scan of the index CSVs.
    """
    try:
        if index is None:
            row = irs_source.lookup(object_id, cache_dir)
        elif (row := index.get(object_id)) is None:
            raise LookupError(f"{object_id} not in index_{object_id[:4]}.csv or any other year")
    except Exception as exc:                              # noqa: BLE001 — not in any index CSV
        return Fetched(f"lookup_failed:{type(exc).__name__}", None, None, None, str(exc)[:500])
    try:
        images = irs_source.images(row)
    except Exception as exc:                              # noqa: BLE001 — TEOS down or malformed
        return Fetched(f"teos_failed:{type(exc).__name__}", row, None, None, str(exc)[:500])
    if not images:
        return Fetched("no_teos_image", row, None, None,
                       f"TEOS lists no {row.return_type} image for EIN {row.ein}, period {row.tax_period}")
    status, errors = "pdf_unavailable", []
    for image in reversed(images):         # newest first; older ones are fallbacks
        try:
            payload = irs_source._get(image.url)
        except Exception as exc:                          # noqa: BLE001
            status = f"pdf_unavailable:{getattr(exc, 'code', type(exc).__name__)}"
            errors.append(f"{exc} ({image.url})")
            continue
        if payload[:4] != b"%PDF":
            status = "not_a_pdf"
            errors.append(f"{payload[:40]!r} ({image.url})")
            continue
        return Fetched("fetched", row, image, payload, None)
    # Nothing served: the row names the newest image, and the error every one
    # of them gave, newest first.
    return Fetched(status, row, images[-1], None, "; ".join(errors))


def _taxyear(tax_period: str) -> int | None:
    """IRS TAX_PERIOD is the period's end, YYYYMM; the tax year is the year
    the period begins, which for a fiscal-year filer is the year before."""
    if len(tax_period) != 6 or not tax_period.isdigit():
        return None
    year, month = int(tax_period[:4]), int(tax_period[4:])
    return year if month == 12 else year - 1


def generated_on(url: str | None) -> date | None:
    """The date in a TEOS image filename's trailing token."""
    token = irs_source._PDF_TOKEN.search(url or "")
    if not token:
        return None
    ymd = token.group(1)
    try:
        return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
    except ValueError:
        return None


def _require_pdfimages() -> None:
    """Fail before any download on a box without poppler: the attachment
    boundary is read from ``pdfimages -list`` and nothing else gives it."""
    if shutil.which("pdfimages") is None:
        raise RuntimeError("pdfimages is not on PATH; install poppler-utils (apt) or poppler (brew)")


def pdf_path(cache_dir: Path, object_id: str) -> Path:
    return cache_dir / "pdfs" / f"{object_id}.pdf"


def _stage(path: Path, payload: bytes) -> Path:
    """Write the payload beside ``path`` under a pid-tagged name; the caller
    moves it into place once it is known to be a PDF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    return tmp


def _write_atomic(path: Path, payload: bytes) -> None:
    _stage(path, payload).replace(path)


def attachment_span(widths: Sequence[int]) -> tuple[int | None, int]:
    """Where the filer's pages start (1-based) and how many there are;
    ``(None, 0)`` when every page is the IRS's rendering."""
    start = irs_source.attachment_start(widths)
    return start, (len(widths) - start + 1 if start else 0)


def _describe(row: FilingImage, payload: bytes, widths: Sequence[int], key: str,
              fetched_at: datetime) -> FilingImage:
    """The fetched fields, in one place: what the object is, where it is,
    and where the filer's pages start."""
    start, attached = attachment_span(widths)
    row.status = "fetched" if start else "no_attachment"
    row.last_error = None
    row.fetched_at = fetched_at
    row.s3_key, row.sha256, row.bytes = key, hashlib.sha256(payload).hexdigest(), len(payload)
    row.pages, row.page_widths = len(widths), list(widths)
    row.attachment_from, row.attachment_pages = start, attached
    return row


def _upload(s3, bucket: str, key: str, payload: bytes, digest: bytes) -> None:
    """S3 verifies the SHA-256 on the way in, and keeps it on the object."""
    s3.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/pdf",
                  ChecksumSHA256=base64.b64encode(digest).decode("ascii"))


def _fetch_one(filing: Filing, prior: FilingImage | None, *, cache_dir: Path,
               bucket: str, prefix: str, s3, index: Mapping[str, irs_source.IndexRow] | None = None) -> FilingImage:
    """Runs in a worker thread: TEOS, hash, widths, S3. Never the session."""
    got = fetch_image(filing.object_id, cache_dir, index=index)
    index = got.row
    base = prior if prior is not None else FilingImage(
        object_id=filing.object_id, filerein="", taxyear=None, index_year=None,
        teos_url=None, image_generated=None, status=got.status)
    row = replace(
        base,
        filerein=filing.filerein or (index.ein if index else None) or base.filerein or "",
        taxyear=filing.taxyear or (_taxyear(index.tax_period) if index else None) or base.taxyear,
        index_year=(index.index_year if index else None) or base.index_year,
        teos_url=got.image.url if got.image else base.teos_url,
        image_generated=generated_on(got.image.url) if got.image else base.image_generated,
        attempts=base.attempts + 1,
        last_error=got.error,
    )
    if got.status != "fetched":
        return _failed(row, prior, got.status, got.error)

    payload = got.payload
    key = f"{prefix}/{filing.object_id}.pdf"
    try:
        # The widths are read from the staged file, and it enters the cache
        # directory only once poppler has read it: stage (still live) treats
        # any non-empty file there as a PDF.
        tmp = _stage(pdf_path(cache_dir, filing.object_id), payload)
        try:
            widths = irs_source.page_widths(tmp)
        except subprocess.CalledProcessError as exc:      # poppler rejected the bytes
            tmp.unlink(missing_ok=True)
            return _failed(row, prior, "not_a_pdf", f"pdfimages: {exc.stderr or exc}"[:500])
        except Exception as exc:                          # noqa: BLE001 — poppler itself failed
            tmp.unlink(missing_ok=True)
            return _failed(row, prior, f"widths_failed:{type(exc).__name__}", str(exc)[:500])
        tmp.replace(pdf_path(cache_dir, filing.object_id))
        _upload(s3, bucket, key, payload, hashlib.sha256(payload).digest())
    except Exception as exc:                              # noqa: BLE001
        # S3, credentials, disk: nothing about the filing. The row records
        # the attempt and stays retryable; the run goes on.
        return _failed(row, prior, f"upload_failed:{type(exc).__name__}", str(exc)[:500])
    return _describe(row, payload, widths, key, datetime.now(timezone.utc))


def _failed(row: FilingImage, prior: FilingImage | None, status: str, error: str | None) -> FilingImage:
    """Record a failed attempt. A row that was fetched before keeps its
    status, its object and the URL that object came from; only attempts and
    last_error move."""
    row.last_error = error
    if prior is not None and prior.fetched:
        row.status = prior.status
        row.teos_url, row.image_generated = prior.teos_url, prior.image_generated
    else:
        row.status = status
    return row


# ---------------------------------------------------------------------------
# Fetching a frame
# ---------------------------------------------------------------------------


def _filings(object_ids: Iterable[str | Filing]) -> list[Filing]:
    """Deduplicate, keeping first occurrence and the first identity given."""
    seen: dict[str, Filing] = {}
    for item in object_ids:
        filing = item if isinstance(item, Filing) else Filing(str(item))
        seen.setdefault(filing.object_id, filing)
    return list(seen.values())


def _retryable(row: FilingImage | None) -> bool:
    return row is None or (not row.fetched and row.status not in PERMANENT and row.attempts < MAX_ATTEMPTS)


def _warm_index(cache_dir: Path) -> None:
    """Download any missing ``index_<year>.csv`` before the pool starts, so
    eight workers do not race to write the same 75 MB file."""
    for year in irs_source.INDEX_YEARS:
        next(irs_source.index_rows(year, cache_dir), None)


def _index_frame(object_ids: Iterable[str], cache_dir: Path) -> dict[str, irs_source.IndexRow]:
    """The frame's index rows in one pass per index CSV.

    ``irs_source.lookup`` streams a 54–93 MB CSV per call — 0.6 s for a hit
    in the id's own year, about 5 s for a miss over all six — and it is
    CPU-bound under the GIL, so a pool of workers serialises on it. Reading
    each CSV once for every id in the frame is about ten seconds in all.
    The id's own year is read first, as ``lookup`` does, so a filing listed
    twice resolves the same way; the pass stops when nothing is missing.
    """
    wanted = set(object_ids)
    own = {oid[:4] for oid in wanted}
    found: dict[str, irs_source.IndexRow] = {}
    for year in sorted(irs_source.INDEX_YEARS, key=lambda y: (y not in own, y)):
        if not wanted - found.keys():
            break
        for row in irs_source.index_rows(year, cache_dir):
            if row.object_id in wanted and row.object_id not in found:
                found[row.object_id] = row
    return found


@functools.lru_cache(maxsize=None)
def _s3_client(workers: int = 8):
    """Built once per process and pool size: credential chain, region and
    endpoint resolution cost about 100 ms, and Part B calls local_pdf once
    per filing."""
    import boto3                                          # the ``ingest`` extra
    from botocore.config import Config
    return boto3.client("s3", config=Config(max_pool_connections=workers + 4))


def fetch_filings(session, object_ids: Iterable[str | Filing], *, bucket: str = BUCKET,
                  prefix: str = PREFIX, workers: int = 8, refetch: bool = False,
                  cache_dir: Path = CACHE, s3=None) -> dict[str, str]:
    """Fetch every filing not yet fetched; return ``object_id -> status``.

    Rows already ``fetched`` or ``no_attachment`` are skipped, as are
    failures that have used their three attempts, unless ``refetch``. A
    re-fetch that returns a different image updates the row's ``sha256``;
    readings keyed on the old hash stay where they are.

    ``session`` is a SQLAlchemy session (``_internal.db.get_session``) or a
    ``FilingImageStore``; ``s3`` is a boto3 client, built when not given.
    """
    _require_pdfimages()
    store = _store(session)
    store.ensure_table()
    filings = _filings(object_ids)
    prior = store.get(f.object_id for f in filings)
    todo = [f for f in filings if refetch or _retryable(prior.get(f.object_id))]
    pending = {f.object_id for f in todo}
    statuses = {f.object_id: prior[f.object_id].status for f in filings if f.object_id not in pending}
    logger.info("fetch_filings: %d filings, %d to fetch, %d already fetched, permanent or out of attempts",
                len(filings), len(todo), len(statuses))
    if not todo:
        return statuses

    _warm_index(cache_dir)
    index = _index_frame((f.object_id for f in todo), cache_dir)
    logger.info("fetch_filings: %d of %d filings found in the IRS index", len(index), len(todo))
    s3 = s3 if s3 is not None else _s3_client(workers)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_fetch_one, f, prior.get(f.object_id), cache_dir=cache_dir,
                               bucket=bucket, prefix=prefix, s3=s3, index=index) for f in todo]
        for row in upsert_as_done(store, futures, CHUNK):
            done += 1
            statuses[row.object_id] = row.status
            if row.last_error:
                # Any failed attempt, including one on a fetched row that
                # kept its status and object: the status says what the row
                # holds, the error says what this attempt did.
                logger.warning("[%d/%d] %s %s (attempt %d): %s", done, len(todo), row.object_id,
                               row.status, row.attempts, row.last_error)
            else:
                logger.info("[%d/%d] %s %s: %d pages, %d attached, %.1f MB", done, len(todo),
                            row.object_id, row.status, row.pages, row.attachment_pages, row.bytes / 1e6)
    return statuses


def local_pdf(session, object_id: str, cache_dir: Path = CACHE, *, bucket: str = BUCKET,
              s3=None) -> Path:
    """The filing's PDF on local disk, matching the row's ``sha256``: the
    row from the table, then ``materialise``. Raises ``LookupError`` for a
    filing that is not fetched — nothing here ever goes back to TEOS."""
    row = _store(session).get([object_id]).get(object_id)
    if row is None or not row.fetched or not row.s3_key or not row.sha256:
        raise LookupError(f"{object_id} is not fetched ({row.status if row else 'no row'})")
    return materialise(row, cache_dir, bucket=bucket, s3=s3)


def materialise(row: FilingImage, cache_dir: Path = CACHE, *, bucket: str = BUCKET, s3=None) -> Path:
    """The row's PDF on local disk: served from ``cache_dir`` when the cached
    file hashes to the row's ``sha256``, else downloaded from S3, verified
    and cached. Takes the row and never the session, so a caller that has
    already read the rows can run this in a worker thread."""
    if not row.fetched or not row.s3_key or not row.sha256:
        raise LookupError(f"{row.object_id} is not fetched ({row.status})")
    path = pdf_path(cache_dir, row.object_id)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == row.sha256:
        return path
    s3 = s3 if s3 is not None else _s3_client()
    started = time.monotonic()
    payload = s3.get_object(Bucket=bucket, Key=row.s3_key)["Body"].read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != row.sha256:
        raise ValueError(f"s3://{bucket}/{row.s3_key} hashes to {digest[:12]}…, "
                         f"the row says {row.sha256[:12]}…")
    _write_atomic(path, payload)
    # One line per download, so a run log says how many PDFs came from S3
    # and how long they took (the empty-cache path the EC2 box runs).
    logger.info("materialise: %s from s3://%s/%s, %.1f MB in %.1f s", row.object_id, bucket, row.s3_key,
                len(payload) / 1e6, time.monotonic() - started)
    return path


# ---------------------------------------------------------------------------
# Backfill from the exploratory staging run
# ---------------------------------------------------------------------------


def _transient(status: str) -> bool:
    """A failure of ours, not the IRS's: worth another go without ``refetch``."""
    return status.startswith(("upload_failed:", "widths_failed:"))


def _backfill_one(staged: dict, *, pdf_dir: Path, bucket: str, prefix: str, s3) -> FilingImage:
    """One cached PDF into S3 and a row, from the staging CSV's line for it.
    The widths are read from the PDF, not copied, so the CSV's
    ``attachment_from`` is checked rather than trusted."""
    object_id = staged["object_id"]
    local = pdf_dir / f"{object_id}.pdf"
    key = f"{prefix}/{object_id}.pdf"
    row = FilingImage(object_id=object_id, filerein=staged["filerein"], taxyear=int(staged["taxyear"]),
                      index_year=None, teos_url=None, image_generated=None, status="fetched", attempts=1)
    try:
        payload = local.read_bytes()
        try:
            widths = irs_source.page_widths(local)
        except subprocess.CalledProcessError as exc:
            return _failed(row, None, "not_a_pdf", f"pdfimages: {exc.stderr or exc}"[:500])
        except Exception as exc:                          # noqa: BLE001
            return _failed(row, None, f"widths_failed:{type(exc).__name__}", str(exc)[:500])
        _upload(s3, bucket, key, payload, hashlib.sha256(payload).digest())
    except Exception as exc:                              # noqa: BLE001
        return _failed(row, None, f"upload_failed:{type(exc).__name__}", str(exc)[:500])
    start, attached = attachment_span(widths)
    if (str(start or ""), str(attached)) != (staged["attachment_from"], staged["attachment_pages"]):
        logger.warning("%s: pdf says attachment_from=%s pages=%s, staging CSV says %s/%s", object_id,
                       start, attached, staged["attachment_from"], staged["attachment_pages"])
    return _describe(row, payload, widths, key, datetime.fromtimestamp(local.stat().st_mtime, tz=timezone.utc))


def backfill(session, *, staging_csvs: Sequence[Path] = STAGING_CSVS,
             image_404_csv: Path | None = IMAGE_404_CSV, pdf_dir: Path = CACHE / "pdfs",
             bucket: str = BUCKET, prefix: str = PREFIX, workers: int = 8, s3=None,
             dry_run: bool = False) -> dict[str, str]:
    """Load the exploratory ``stage`` results — cached PDFs and the staging
    CSVs — into the table and S3 without a single TEOS request.

    A one-off seed: a filing already in the table is left exactly as it is,
    since a live fetch may have moved its ``attempts`` and filled the TEOS
    fields the CSVs cannot supply. The exception is a row an earlier
    backfill left as ``upload_failed`` or ``widths_failed``, which is done
    again. The dry run sizes the whole upload without consulting the table.

    ``staged`` and ``cached`` become ``fetched`` or ``no_attachment`` by the
    PDF's own widths. Failures keep their status; ``attempts`` counts the
    staging passes that observed the failure (one per CSV the id appears
    in, plus the pass that produced the 404 list, which also gives those
    rows their ``teos_url`` and ``image_generated``). The EIN and tax year
    come from the CSVs; ``index_year`` and ``teos_url`` for fetched rows
    would need TEOS, so they stay NULL until a ``refetch``.
    """
    _require_pdfimages()
    staged: dict[str, dict] = {}
    passes: dict[str, int] = {}
    for path in staging_csvs:
        with path.open(newline="") as handle:
            for line in csv.DictReader(handle):
                staged[line["object_id"]] = line
                passes[line["object_id"]] = passes.get(line["object_id"], 0) + 1
    listed_404: dict[str, dict] = {}
    if image_404_csv is not None and image_404_csv.exists():
        with image_404_csv.open(newline="") as handle:
            listed_404 = {line["object_id"]: line for line in csv.DictReader(handle)}

    to_upload = [line for line in staged.values() if line["status"] in ("staged", "cached")]
    failures = [line for line in staged.values() if line["status"] not in ("staged", "cached")]
    missing = [line["object_id"] for line in to_upload if not (pdf_dir / f"{line['object_id']}.pdf").exists()]
    if missing:
        raise FileNotFoundError(f"{len(missing)} staged PDFs are not in {pdf_dir}: {missing[:5]}")
    total = sum((pdf_dir / f"{line['object_id']}.pdf").stat().st_size for line in to_upload)
    logger.info("backfill: %d filings in %d CSVs; %d PDFs, %.2f GB, to s3://%s/%s/; %d failure rows",
                len(staged), len(staging_csvs), len(to_upload), total / 1e9, bucket, prefix, len(failures))
    if dry_run:
        return {line["object_id"]: line["status"] for line in staged.values()}

    store = _store(session)
    store.ensure_table()
    prior = store.get(staged)
    seeded = {oid for oid, row in prior.items() if not _transient(row.status)}
    if seeded:
        logger.info("backfill: %d filings are already in the table and are left as they are", len(seeded))
    failures = [line for line in failures if line["object_id"] not in seeded]
    to_upload = [line for line in to_upload if line["object_id"] not in seeded]
    statuses: dict[str, str] = {oid: prior[oid].status for oid in seeded}

    rows = []
    for line in failures:
        oid = line["object_id"]
        url = listed_404[oid]["pdf_url"] if oid in listed_404 else None
        rows.append(FilingImage(
            object_id=oid, filerein=line["filerein"], taxyear=int(line["taxyear"]),
            index_year=None, teos_url=url, image_generated=generated_on(url),
            status=line["status"], attempts=passes[oid] + (1 if oid in listed_404 else 0),
            last_error=f"{line['status']} ({url})" if url else line["status"],
        ))
        statuses[oid] = line["status"]
    for start in range(0, len(rows), CHUNK):
        store.upsert(rows[start:start + CHUNK])

    s3 = s3 if s3 is not None else _s3_client(workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_backfill_one, line, pdf_dir=pdf_dir, bucket=bucket, prefix=prefix, s3=s3)
                   for line in to_upload]
        for n, row in enumerate(upsert_as_done(store, futures, CHUNK), 1):
            statuses[row.object_id] = row.status
            if n % CHUNK == 0 or n == len(futures):
                logger.info("backfill: %d/%d PDFs done", n, len(futures))
    return statuses


# ---------------------------------------------------------------------------
# Reports and verification
# ---------------------------------------------------------------------------


def status_report(session) -> str:
    """Counts by status, and the unserved images by the year the IRS generated them."""
    lines = [f"{'status':<26}{'n':>6}{'GB':>8}{'pages':>9}{'attached':>10}"]
    for status, n, size, pages, attached in session.execute(text(
            "SELECT status, count(*), coalesce(sum(bytes), 0)::bigint, coalesce(sum(pages), 0)::bigint, "
            "coalesce(sum(attachment_pages), 0)::bigint FROM filing_images GROUP BY 1 ORDER BY 2 DESC")):
        lines.append(f"{status:<26}{n:>6}{int(size) / 1e9:>8.2f}{int(pages):>9,}{int(attached):>10,}")
    lines.append("")
    lines.append("unserved images (status pdf_unavailable:*) by the year the IRS generated them:")
    for year, n in session.execute(text(
            "SELECT extract(year FROM image_generated)::int, count(*) FROM filing_images "
            "WHERE status LIKE 'pdf_unavailable%' GROUP BY 1 ORDER BY 1")):
        lines.append(f"  {year or 'unknown'}: {n}")
    return "\n".join(lines)


def verify(session, *, bucket: str = BUCKET, workers: int = 8, s3=None) -> list[str]:
    """Download every stored object and hash it; return the ids whose object
    does not hash to the row's ``sha256`` (or is missing)."""
    rows = [row for row in _store(session).all() if row.s3_key]
    s3 = s3 if s3 is not None else _s3_client(workers)

    def check(row: FilingImage) -> tuple[str, bool]:
        try:
            payload = s3.get_object(Bucket=bucket, Key=row.s3_key)["Body"].read()
        except Exception as exc:                          # noqa: BLE001
            logger.warning("%s: %s", row.s3_key, exc)
            return row.object_id, False
        return row.object_id, hashlib.sha256(payload).hexdigest() == row.sha256

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(check, rows))
    bad = [oid for oid, ok in results if not ok]
    logger.info("verify: %d objects, %d match the row's sha256, %d do not", len(rows), len(rows) - len(bad), len(bad))
    return bad


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_frame(token: str) -> list[Filing]:
    """A frame CSV (``object_id`` plus optional ``filerein``, ``taxyear``), or one bare id."""
    path = Path(token)
    if not path.exists():
        return [Filing(irs_source.parse_object_id(token))]
    with path.open(newline="") as handle:
        return [Filing(line["object_id"], line.get("filerein") or None,
                       int(line["taxyear"]) if line.get("taxyear") else None)
                for line in csv.DictReader(handle)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bucket", default=BUCKET)
    parser.add_argument("--prefix", default=PREFIX)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--cache", type=Path, default=CACHE, help="index CSVs and pdfs/ live here")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="fetch every filing in a frame CSV, or the ids given")
    fetch.add_argument("filings", nargs="+", help="frame CSV path(s) or object ids")
    fetch.add_argument("--refetch", action="store_true", help="fetch even rows already fetched or out of attempts")

    back = sub.add_parser("backfill", help="load the exploratory staging CSVs and cached PDFs; no TEOS")
    back.add_argument("--staging", type=Path, nargs="+", default=list(STAGING_CSVS))
    back.add_argument("--images-404", type=Path, default=IMAGE_404_CSV)
    back.add_argument("--pdfs", type=Path, default=None, help="default: <cache>/pdfs")
    back.add_argument("--dry-run", action="store_true", help="count and size the upload, then stop")

    sub.add_parser("status", help="rows by status; unserved images by generated year")
    sub.add_parser("verify", help="hash every object in S3 against its row")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    from givingtuesday_datamart._internal.db import get_session
    from givingtuesday_datamart.ingestion import datamart_config

    with get_session(config=datamart_config()) as session:
        if args.command == "fetch":
            filings = [f for token in args.filings for f in _read_frame(token)]
            statuses = fetch_filings(session, filings, bucket=args.bucket, prefix=args.prefix,
                                     workers=args.workers, refetch=args.refetch, cache_dir=args.cache)
            counts: dict[str, int] = {}
            for status in statuses.values():
                counts[status] = counts.get(status, 0) + 1
            for status, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                print(f"  {status:<26}{n:>6}")
        elif args.command == "backfill":
            backfill(session, staging_csvs=args.staging, image_404_csv=args.images_404,
                     pdf_dir=args.pdfs or args.cache / "pdfs", bucket=args.bucket,
                     prefix=args.prefix, workers=args.workers, dry_run=args.dry_run)
            if not args.dry_run:
                print(status_report(session))
        elif args.command == "status":
            print(status_report(session))
        elif args.command == "verify":
            bad = verify(session, bucket=args.bucket, workers=args.workers)
            if bad:
                sys.exit(f"{len(bad)} objects do not match their row: {bad[:10]}")
            print("every object hashes to its row's sha256")


if __name__ == "__main__":
    main()
