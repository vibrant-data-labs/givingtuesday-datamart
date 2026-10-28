"""The IRS's monthly e-file batches: find them, fetch them, read them.

The IRS publishes the 990-family XML only inside batch ZIPs,
``apps.irs.gov/pub/epostcard/990/xml/<year>/<year>_TEOS_XML_<MM><L>.zip``,
about one a month (the third Wednesday, usually), with ``index_<year>.csv``
beside them listing every return.

Two things about them cost time to learn:

* **The index does not name every batch.** 2026's June release is two
  archives, ``05A`` and ``05B``, posted the same minute; the index labels
  all 168,344 of their returns ``2026_TEOS_XML_05A``. GT's mirror unpacked
  05A and not 05B, which is the "exactly half of 05A is absent" of
  2026-10-02. So batches are found by asking the server (``discover``), and
  a return's batch is the archive that holds it, not the index's label.
* **Some archives are Deflate64** (compression method 9): 2025 05B, 2026 05A
  and 05B, 2025 11B. Python's ``zipfile`` cannot inflate it; Info-ZIP
  ``unzip`` can (macOS and Amazon Linux both build it with
  ``USE_DEFLATE64``). ``bsdtar`` is no substitute: it writes zero-filled
  files for those members without an error.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from givingtuesday_datamart.irs_source import IRS_XML_BASE, OBJECT_ID, UA
from givingtuesday_datamart.write_data_to_sql import _ByteTally, _spool_response

DEFLATE64 = 9
# A missing archive answers 302 to an error page; 404 is the other "no such
# file". Any other error is the server's, and the walk must not read it as
# the end of a month.
_MISSING = (302, 404)
_HEAD_ATTEMPTS = 3
# A month holds A to D at most so far (2025 11A-11D).
_LETTERS = "ABCDEFGH"


@dataclass(frozen=True)
class Batch:
    """One archive as the server described it: the version stamp of a fetch."""

    year: str
    batch_id: str            # e.g. 2026_TEOS_XML_05B
    url: str
    last_modified: str
    content_length: int

    @property
    def filename(self) -> str:
        return f"{self.batch_id}.zip"


def batch_url(year: str, batch_id: str) -> str:
    return f"{IRS_XML_BASE}/{year}/{batch_id}.zip"


def _head(url: str) -> Batch | None:
    """The archive's stamp, or None when the server says there is no such archive."""
    request = urllib.request.Request(url, method="HEAD", headers=UA)
    opener = urllib.request.build_opener(_NoRedirect)
    for attempt in range(1, _HEAD_ATTEMPTS + 1):
        try:
            with opener.open(request, timeout=60) as response:
                headers = response.headers
            break
        except urllib.error.HTTPError as err:
            if err.code in _MISSING:
                return None
            if attempt == _HEAD_ATTEMPTS:
                raise
        except urllib.error.URLError:
            if attempt == _HEAD_ATTEMPTS:
                raise
        time.sleep(2 * attempt)
    year, batch_id = url.rsplit("/", 2)[-2], url.rsplit("/", 1)[-1].removesuffix(".zip")
    return Batch(year, batch_id, url, headers.get("Last-Modified", ""),
                 int(headers.get("Content-Length") or 0))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def discover(year: str) -> list[Batch]:
    """Every archive the server holds for ``year``, in order.

    Walks months 01-12 and letters A-H, stopping a month at its first
    missing letter. About 30 HEAD requests for a full year.
    """
    found: list[Batch] = []
    for month in range(1, 13):
        for letter in _LETTERS:
            batch = _head(batch_url(year, f"{year}_TEOS_XML_{month:02d}{letter}"))
            if batch is None:
                break
            found.append(batch)
    return found


def fetch(batch: Batch, cache_dir: Path) -> Path:
    """Download an archive once into ``cache_dir`` and stamp it.

    The stamp (``<batch>.json``) holds the server's Last-Modified and
    Content-Length and the archive's SHA-256 and size. A cached archive is
    reused when its stamp matches the server's and the file is the size the
    stamp says; otherwise it is fetched again, because the IRS has replaced
    archives under the same name before. The download is
    ``write_data_to_sql``'s: a body the server cuts short raises and the
    download starts again, so a partial file is never stamped.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / batch.filename
    stamp = cache_dir / f"{batch.batch_id}.json"
    if path.exists() and stamp.exists():
        held = json.loads(stamp.read_text())
        if (held.get("last_modified"), held.get("content_length"), held.get("nbytes")) == (
                batch.last_modified, batch.content_length, path.stat().st_size):
            return path
    tally = _ByteTally()
    partial = path.with_suffix(".zip.part")
    with _spool_response(batch.url, tally) as spool, partial.open("wb") as out:
        shutil.copyfileobj(spool, out, 1 << 20)
    partial.replace(path)
    stamp.write_text(json.dumps(
        {**asdict(batch), "sha256": tally.digest.hexdigest(), "nbytes": tally.nbytes}, indent=2))
    return path


def claim(batch: Batch, cache_dir: Path):
    """An exclusive lock on the batch in ``cache_dir``, or None while another loader holds it.

    Two loaders sharing a cache fetched the same archive to the same path on
    2026-10-23; one loaded and deleted it while the other still read it.
    The lock goes with the returned file, when it is closed.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    handle = (cache_dir / f"{batch.batch_id}.lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def stamp_of(path: Path) -> dict:
    """The fetch stamp written beside a cached archive, or {} for one fetched by hand."""
    stamp = path.with_name(path.stem + ".json")
    return json.loads(stamp.read_text()) if stamp.exists() else {}


def object_id_of(member_name: str) -> str | None:
    match = OBJECT_ID.search(member_name.rsplit("/", 1)[-1])
    return match.group(1) if match else None


def iter_members(path: Path, wanted: set[str] | None = None) -> Iterator[tuple[str, bytes]]:
    """Yield ``(object_id, xml bytes)`` for every return in an archive.

    An archive with Deflate64 members is unpacked once by Info-ZIP ``unzip``
    into a temporary directory beside it and read from there (a process per
    member would reread the 80,000-entry central directory each time);
    ``wanted`` limits the read to those object ids.
    """
    with zipfile.ZipFile(path) as archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        deflate64 = any(i.compress_type == DEFLATE64 for i in infos)
        if not deflate64:
            for info in infos:
                object_id = object_id_of(info.filename)
                if object_id and (wanted is None or object_id in wanted):
                    yield object_id, archive.read(info)
            return
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        status = _unzip_all(path, Path(tmp))
        for info in infos:
            object_id = object_id_of(info.filename)
            if object_id and (wanted is None or object_id in wanted):
                yield object_id, _verified(Path(tmp) / info.filename, info, path, status)


def _unzip_all(path: Path, into: Path) -> str:
    """Unpack with Info-ZIP; returns its exit status and the tail of its messages.

    The status is not trusted either way: 2026 05A (84,172 members, so
    ZIP64) makes Info-ZIP 6.0 warn about "76 extra bytes", "compensate" and
    exit 3 having written every member correctly. Each member is checked
    against the central directory instead (``_verified``).
    """
    unzip = shutil.which("unzip")
    if unzip is None:
        raise RuntimeError(
            f"{path.name} holds Deflate64 members, which zipfile cannot read; "
            "install Info-ZIP unzip (bsdtar is not a substitute: it writes zeros)")
    done = subprocess.run([unzip, "-q", "-o", str(path), "-d", str(into)],
                          capture_output=True, text=True)
    return f"unzip exit {done.returncode}: {done.stderr.strip()[-300:]}"


def _verified(member: Path, info: zipfile.ZipInfo, archive: Path, status: str) -> bytes:
    """A member Info-ZIP wrote, held to the size and CRC-32 the archive records."""
    if not member.exists():
        raise RuntimeError(f"{archive.name}: {info.filename} was not unpacked ({status})")
    data = member.read_bytes()
    if len(data) != info.file_size or zlib.crc32(data) & 0xFFFFFFFF != info.CRC:
        raise RuntimeError(f"{archive.name}: {info.filename} fails its size or CRC ({status})")
    return data


def member_count(path: Path) -> tuple[int, int]:
    """(returns, of which Deflate64) in an archive, from its central directory."""
    with zipfile.ZipFile(path) as archive:
        infos = [i for i in archive.infolist() if not i.is_dir() and object_id_of(i.filename)]
    return len(infos), sum(i.compress_type == DEFLATE64 for i in infos)


ARCHIVE_BUCKET = "givingtuesday-datamart"
ARCHIVE_PREFIX = "irs/xml_batches"


def archive(path: Path, s3=None, bucket: str = ARCHIVE_BUCKET) -> str:
    """Keep our own copy of an archive: ``s3://<bucket>/irs/xml_batches/<year>/<batch>.zip``.

    The IRS has replaced archives under the same name, and GT's mirror
    is neither complete nor current, so the copy is what makes a load
    reproducible. The object carries the stamp's Last-Modified and SHA-256
    as metadata; one already there with the same SHA-256 is left alone.
    """
    stamp = stamp_of(path)
    key = f"{ARCHIVE_PREFIX}/{stamp.get('year') or path.stem[:4]}/{path.name}"
    if s3 is None:
        import boto3                                          # the ``ingest`` extra
        s3 = boto3.client("s3")
    try:
        held = s3.head_object(Bucket=bucket, Key=key).get("Metadata", {})
        if held.get("sha256") == stamp.get("sha256"):
            return f"s3://{bucket}/{key}"
    except s3.exceptions.ClientError:
        pass
    s3.upload_file(str(path), bucket, key, ExtraArgs={
        "ContentType": "application/zip",
        "Metadata": {"sha256": stamp.get("sha256", ""),
                     "irs-last-modified": stamp.get("last_modified", ""),
                     "irs-url": stamp.get("url", "")}})
    return f"s3://{bucket}/{key}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_archived(batch_id: str, sha256: str, cache_dir: Path, s3=None,
                   bucket: str = ARCHIVE_BUCKET) -> Path:
    """Our copy of a loaded archive (``archive``) in ``cache_dir``, checked against
    the SHA-256 its load stamped: a re-read sees the bytes that were loaded,
    not whatever the IRS serves under that name today."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{batch_id}.zip"
    if not path.exists() or sha256_of(path) != sha256:
        if s3 is None:
            import boto3                                          # the ``ingest`` extra
            s3 = boto3.client("s3")
        partial = path.with_suffix(".zip.part")
        s3.download_file(bucket, f"{ARCHIVE_PREFIX}/{batch_id[:4]}/{batch_id}.zip", str(partial))
        partial.replace(path)
        if sha256_of(path) != sha256:
            raise ValueError(f"{batch_id}: our copy is not the archive that was loaded "
                             f"(SHA-256 {sha256_of(path)[:12]}, loaded {sha256[:12]})")
    return path
