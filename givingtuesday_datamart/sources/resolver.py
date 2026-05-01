"""
Resolve the latest version of a Datamart source from the public S3 bucket.

Uses boto3 with anonymous (unsigned) credentials — the bucket is public-read
and public-list, so no AWS credentials are required.
"""

from __future__ import annotations

from dataclasses import dataclass

import boto3
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
class ResolvedVersion:
    source: SourceSpec
    version_date: str
    filename: str
    url: str
    size: int


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
    return ResolvedVersion(
        source=source,
        version_date=version_date,
        filename=filename,
        url=source.url_for(filename),
        size=obj.size,
    )
