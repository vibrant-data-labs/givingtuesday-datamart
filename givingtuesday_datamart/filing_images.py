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
never fetched again unless ``refetch``. ``no_teos_image`` (TEOS lists no
image), ``pdf_unavailable:<http code>`` (listed, not served — every one seen
so far is an image the IRS generated in 2022), ``not_a_pdf`` and
``lookup_failed:<exception>`` are failures: the row keeps ``attempts`` and
``last_error``, and after three failed attempts the filing is left alone
until ``refetch``. The unreachable set, the 404s by image year and the
no-attachment filings are therefore queries on this table, not files.

**Reading back.** ``local_pdf`` is the only way renders and readings get a
PDF: from the cache directory when the cached file's hash matches the row,
else downloaded from S3 and verified. TEOS is never consulted again for a
fetched filing, and a re-issued image (a new ``sha256`` on the row) makes
the stale cached file miss.

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
import hashlib
import logging
import os
import sys
from abc import ABC, abstractmethod
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple, Sequence

from sqlalchemy import text

from givingtuesday_datamart import irs_source
from givingtuesday_datamart._internal.logger import logger

BUCKET = "givingtuesday-datamart"
PREFIX = "irs/pdf"
CACHE = Path.home() / ".cache" / "irs_index"
MAX_ATTEMPTS = 3
CHUNK = 50
FETCHED = ("fetched", "no_attachment")

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

_SELECT = f"SELECT {', '.join(COLUMNS)} FROM filing_images WHERE object_id = ANY(:ids)"
_UPSERT = (
    f"INSERT INTO filing_images ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in COLUMNS)}) "
    "ON CONFLICT (object_id) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c != "object_id")
)


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
        if not rows:
            return
        self.session.execute(text(_UPSERT), [asdict(row) for row in rows])
        self.session.commit()


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


def fetch_image(object_id: str, cache_dir: Path = CACHE) -> Fetched:
    """Download one filing's image, newest TEOS image first with older ones
    as fallbacks.

    A filing can fail to reach transcription for reasons that have nothing
    to do with it, and they must not be scored as extraction failures. TEOS
    lists no image for some filings, and for others it lists a
    ``STATICFILEPATH`` the IRS no longer serves — Schusterman's 2020 990-PF
    is indexed and returns a 302 to an error page. Both are concentrated in
    older tax years.
    """
    row = None
    try:
        row = irs_source.lookup(object_id, cache_dir)
        images = irs_source.images(row)
    except Exception as exc:                              # noqa: BLE001
        return Fetched(f"lookup_failed:{type(exc).__name__}", row, None, None, str(exc)[:500])
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


def pdf_path(cache_dir: Path, object_id: str) -> Path:
    return cache_dir / "pdfs" / f"{object_id}.pdf"


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)


def _upload(s3, bucket: str, key: str, payload: bytes, digest: bytes) -> None:
    """S3 verifies the SHA-256 on the way in, and keeps it on the object."""
    s3.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/pdf",
                  ChecksumSHA256=base64.b64encode(digest).decode("ascii"))


def _fetch_one(filing: Filing, prior: FilingImage | None, *, cache_dir: Path,
               bucket: str, prefix: str, s3) -> FilingImage:
    """Runs in a worker thread: TEOS, hash, widths, S3. Never the session."""
    got = fetch_image(filing.object_id, cache_dir)
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
        if row.fetched:
            # A fetched row that fails a re-fetch keeps its status, its object
            # and the URL that object came from; only the attempt is recorded.
            row.teos_url, row.image_generated = base.teos_url, base.image_generated
        else:
            row.status = got.status
        return row

    payload = got.payload
    local = pdf_path(cache_dir, filing.object_id)
    _write_atomic(local, payload)
    try:
        widths = irs_source.page_widths(local)
    except Exception as exc:                              # noqa: BLE001
        row.status, row.last_error = "not_a_pdf", f"pdfimages: {exc}"[:500]
        return row

    digest = hashlib.sha256(payload)
    key = f"{prefix}/{filing.object_id}.pdf"
    _upload(s3, bucket, key, payload, digest.digest())

    start = irs_source.attachment_start(widths)
    row.status = "fetched" if start else "no_attachment"
    row.fetched_at = datetime.now(timezone.utc)
    row.s3_key, row.sha256, row.bytes = key, digest.hexdigest(), len(payload)
    row.pages, row.page_widths = len(widths), list(widths)
    row.attachment_from = start
    row.attachment_pages = len(widths) - start + 1 if start else 0
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
    return row is None or (not row.fetched and row.attempts < MAX_ATTEMPTS)


def _warm_index(cache_dir: Path) -> None:
    """Download any missing ``index_<year>.csv`` before the pool starts, so
    eight workers do not race to write the same 75 MB file."""
    for year in irs_source.INDEX_YEARS:
        next(irs_source.index_rows(year, cache_dir), None)


def _s3_client(workers: int = 8):
    import boto3                                          # the ``ingest`` extra
    from botocore.config import Config
    return boto3.client("s3", config=Config(max_pool_connections=workers + 4))


def _upsert_as_done(store: FilingImageStore, futures: Sequence[Future],
                    chunk: int = CHUNK) -> Iterator[FilingImage]:
    """Collect worker results on the main thread and commit every ``chunk``."""
    buffer: list[FilingImage] = []
    for future in as_completed(futures):
        row = future.result()
        buffer.append(row)
        if len(buffer) >= chunk:
            store.upsert(buffer)
            buffer = []
        yield row
    if buffer:
        store.upsert(buffer)


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
    store = _store(session)
    store.ensure_table()
    filings = _filings(object_ids)
    prior = store.get(f.object_id for f in filings)
    todo = [f for f in filings if refetch or _retryable(prior.get(f.object_id))]
    pending = {f.object_id for f in todo}
    statuses = {f.object_id: prior[f.object_id].status for f in filings if f.object_id not in pending}
    logger.info("fetch_filings: %d filings, %d to fetch, %d already fetched or out of attempts",
                len(filings), len(todo), len(statuses))
    if not todo:
        return statuses

    _warm_index(cache_dir)
    s3 = s3 if s3 is not None else _s3_client(workers)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_fetch_one, f, prior.get(f.object_id), cache_dir=cache_dir,
                               bucket=bucket, prefix=prefix, s3=s3) for f in todo]
        for row in _upsert_as_done(store, futures):
            done += 1
            statuses[row.object_id] = row.status
            if row.fetched:
                logger.info("[%d/%d] %s %s: %d pages, %d attached, %.1f MB", done, len(todo),
                            row.object_id, row.status, row.pages, row.attachment_pages, row.bytes / 1e6)
            else:
                logger.warning("[%d/%d] %s %s (attempt %d): %s", done, len(todo), row.object_id,
                               row.status, row.attempts, row.last_error)
    return statuses


def local_pdf(session, object_id: str, cache_dir: Path = CACHE, *, bucket: str = BUCKET,
              s3=None) -> Path:
    """The filing's PDF on local disk, matching the row's ``sha256``.

    Served from ``cache_dir`` when the cached file hashes to the row's
    ``sha256``, else downloaded from S3, verified and cached. Raises
    ``LookupError`` for a filing that is not fetched — nothing here ever
    goes back to TEOS.
    """
    row = _store(session).get([object_id]).get(object_id)
    if row is None or not row.fetched or not row.s3_key or not row.sha256:
        raise LookupError(f"{object_id} is not fetched ({row.status if row else 'no row'})")
    path = pdf_path(cache_dir, object_id)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == row.sha256:
        return path
    s3 = s3 if s3 is not None else _s3_client()
    payload = s3.get_object(Bucket=bucket, Key=row.s3_key)["Body"].read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != row.sha256:
        raise ValueError(f"s3://{bucket}/{row.s3_key} hashes to {digest[:12]}…, "
                         f"the row says {row.sha256[:12]}…")
    _write_atomic(path, payload)
    return path


# ---------------------------------------------------------------------------
# Backfill from the exploratory staging run
# ---------------------------------------------------------------------------


def _backfill_one(staged: dict, prior: FilingImage | None, *, pdf_dir: Path, bucket: str,
                  prefix: str, s3) -> FilingImage:
    """One cached PDF into S3 and a row, from the staging CSV's line for it.
    The widths are read from the PDF, not copied, so the CSV's
    ``attachment_from`` is checked rather than trusted."""
    object_id = staged["object_id"]
    local = pdf_dir / f"{object_id}.pdf"
    payload = local.read_bytes()
    digest = hashlib.sha256(payload)
    key = f"{prefix}/{object_id}.pdf"
    if not (prior and prior.sha256 == digest.hexdigest() and prior.s3_key == key):
        _upload(s3, bucket, key, payload, digest.digest())
    widths = irs_source.page_widths(local)
    start = irs_source.attachment_start(widths)
    attached = len(widths) - start + 1 if start else 0
    if (str(start or ""), str(attached)) != (staged["attachment_from"], staged["attachment_pages"]):
        logger.warning("%s: pdf says attachment_from=%s pages=%s, staging CSV says %s/%s", object_id,
                       start, attached, staged["attachment_from"], staged["attachment_pages"])
    return FilingImage(
        object_id=object_id, filerein=staged["filerein"], taxyear=int(staged["taxyear"]),
        index_year=None, teos_url=None, image_generated=None,
        status="fetched" if start else "no_attachment", attempts=1, last_error=None,
        fetched_at=datetime.fromtimestamp(local.stat().st_mtime, tz=timezone.utc),
        s3_key=key, sha256=digest.hexdigest(), bytes=len(payload), pages=len(widths),
        page_widths=list(widths), attachment_from=start, attachment_pages=attached,
    )


def backfill(session, *, staging_csvs: Sequence[Path] = STAGING_CSVS,
             image_404_csv: Path | None = IMAGE_404_CSV, pdf_dir: Path = CACHE / "pdfs",
             bucket: str = BUCKET, prefix: str = PREFIX, workers: int = 8, s3=None,
             dry_run: bool = False) -> dict[str, str]:
    """Load the exploratory ``stage`` results — cached PDFs and the staging
    CSVs — into the table and S3 without a single TEOS request.

    ``staged`` and ``cached`` become ``fetched`` or ``no_attachment`` by the
    PDF's own widths. Failures keep their status; ``attempts`` counts the
    staging passes that observed the failure (one per CSV the id appears
    in, plus the pass that produced the 404 list, which also gives those
    rows their ``teos_url`` and ``image_generated``). The EIN and tax year
    come from the CSVs; ``index_year`` and ``teos_url`` for fetched rows
    would need TEOS, so they stay NULL until a ``refetch``.
    """
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
    statuses: dict[str, str] = {}

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
        futures = [pool.submit(_backfill_one, line, prior.get(line["object_id"]), pdf_dir=pdf_dir,
                               bucket=bucket, prefix=prefix, s3=s3) for line in to_upload]
        for n, row in enumerate(_upsert_as_done(store, futures), 1):
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
    rows = [FilingImage(**{c: r[c] for c in COLUMNS}) for r in session.execute(text(
        f"SELECT {', '.join(COLUMNS)} FROM filing_images WHERE s3_key IS NOT NULL")).mappings()]
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
