"""
Resolve the current version of a Datamart source.

Two resolvers, one per way a ``SourceSpec`` locates its files:

* :func:`resolve_latest` lists GivingTuesday's public S3 bucket (boto3 with
  anonymous, unsigned credentials — the bucket is public-read and
  public-list) and picks the newest dated filename.
* :func:`resolve_direct` HEADs each plain HTTPS URL of a source republished
  in place and takes the newest ``Last-Modified`` as the version.

Both return a :class:`ResolvedVersion`; ingestion treats them alike.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import boto3
import requests
from botocore import UNSIGNED
from botocore.config import Config

from givingtuesday_datamart.sources.spec import SourceSpec


@dataclass(frozen=True)
class BucketObject:
    key: str
    size: int
    last_modified: str


@dataclass(frozen=True)
class BucketListing:
    bucket: str
    prefix: str
    objects: tuple[BucketObject, ...]


@dataclass(frozen=True)
class SourceFile:
    """One file of a resolved version, as the server described it before
    download. ``size`` is None when the server sends no usable
    Content-Length (irs.gov does not); ingestion records the bytes actually
    streamed in the run's manifest."""

    url: str
    size: int | None
    last_modified: str | None


@dataclass(frozen=True)
class ResolvedVersion:
    source: SourceSpec
    version_date: str
    filename: str
    url: str
    size: int | None
    # Every file that makes up this version, in ingest order. One entry for
    # an S3 source; one per URL for a direct source.
    files: tuple[SourceFile, ...] = ()


def list_bucket(bucket: str, prefix: str) -> BucketListing:
    client = boto3.client("s3", config=Config(signature_version=UNSIGNED))
    objects: list[BucketObject] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            objects.append(
                BucketObject(
                    key=obj["Key"],
                    size=obj["Size"],
                    last_modified=obj["LastModified"].isoformat(),
                )
            )
    return BucketListing(bucket=bucket, prefix=prefix, objects=tuple(objects))


def resolve_latest(
    source: SourceSpec,
    *,
    listing: BucketListing | None = None,
) -> ResolvedVersion | None:
    """Pick the newest filename for `source` out of the bucket listing.

    "Newest" is determined by the YYYY_MM_DD date captured by the first group
    of `source.filename_regex`. Returns None if no matching file is found.
    Pass an existing `listing` to avoid re-listing the bucket when resolving
    many sources in one pass.
    """
    if listing is None:
        listing = list_bucket(source.s3_bucket, source.s3_prefix)
    pattern = source.compiled_regex()
    best: tuple[str, BucketObject, str] | None = None
    for obj in listing.objects:
        if not obj.key.startswith(source.s3_prefix):
            continue
        filename = obj.key[len(source.s3_prefix):]
        match = pattern.match(filename)
        if not match:
            continue
        version_date = match.group(1)
        if best is None or version_date > best[0]:
            best = (version_date, obj, filename)
    if best is None:
        return None
    version_date, obj, filename = best
    url = source.url_for(filename)
    return ResolvedVersion(
        source=source,
        version_date=version_date,
        filename=filename,
        url=url,
        size=obj.size,
        files=(SourceFile(url=url, size=obj.size, last_modified=obj.last_modified),),
    )


def last_modified_version(last_modified: list[str | None]) -> str:
    """The newest of several HTTP ``Last-Modified`` values as ``YYYY_MM_DD``,
    the same shape as the dated S3 filenames, so ``source_version`` sorts
    and displays the same way for every source. Raises if no file carried
    the header: without it there is no version to record, and a run that
    cannot be told apart from the previous one must not be stamped."""
    dates = [parsedate_to_datetime(v) for v in last_modified if v]
    if not dates:
        raise LookupError("no Last-Modified header on any file; cannot version the source")
    return max(dates).strftime("%Y_%m_%d")


def resolve_direct(
    source: SourceSpec,
    *,
    http: requests.Session | None = None,
    timeout: int = 60,
) -> ResolvedVersion:
    """HEAD each URL of a direct-HTTPS source and version the set by its
    newest ``Last-Modified``. ``filename`` lists the file basenames;
    ``url`` is the first file (``ingest_runs.source_url`` holds one URL,
    the per-file manifest holds them all)."""
    http = http or requests.Session()
    files: list[SourceFile] = []
    for url in source.urls:
        head = http.head(url, timeout=timeout, allow_redirects=True)
        head.raise_for_status()
        # A HEAD's Content-Length describes the encoded body the server
        # would send, not the file: irs.gov answers HEAD with a gzip-encoded
        # empty body, so it reads 20. Only trust it unencoded.
        length = head.headers.get("Content-Length")
        encoded = bool(head.headers.get("Content-Encoding"))
        files.append(
            SourceFile(
                url=url,
                size=int(length) if length and not encoded else None,
                last_modified=head.headers.get("Last-Modified"),
            )
        )
    sizes = [f.size for f in files if f.size is not None]
    return ResolvedVersion(
        source=source,
        version_date=last_modified_version([f.last_modified for f in files]),
        filename=", ".join(posixpath.basename(urlparse(f.url).path) for f in files),
        url=files[0].url,
        size=sum(sizes) if len(sizes) == len(files) else None,
        files=tuple(files),
    )


def resolve_source(
    source: SourceSpec,
    *,
    listing: BucketListing | None = None,
) -> ResolvedVersion | None:
    """Resolve whichever way ``source`` is configured: direct URLs by
    Last-Modified, otherwise the newest dated file in the S3 listing (None
    if nothing matches)."""
    if source.urls:
        return resolve_direct(source)
    return resolve_latest(source, listing=listing)
