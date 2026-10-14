"""
Source registry for the Giving Tuesday Datamart.

A `SourceSpec` describes one logical table we ingest (e.g. "irs_990_basic_fields")
independently of which file currently represents it. For GivingTuesday's
sources the resolver lists the public S3 bucket and picks the latest matching
file by date, so consumers never hardcode a dated URL; for plain HTTPS files
republished in place (the IRS BMF) it HEADs the URLs and versions the set by
the newest Last-Modified.
"""

from givingtuesday_datamart.sources.spec import ColumnSpec, DedupeSpec, IndexSpec, SourceSpec
from givingtuesday_datamart.sources.registry import REGISTRY, get_source
from givingtuesday_datamart.sources.resolver import (
    BucketListing,
    ResolvedVersion,
    SourceFile,
    list_bucket,
    resolve_direct,
    resolve_latest,
    resolve_source,
)

__all__ = [
    "ColumnSpec",
    "DedupeSpec",
    "IndexSpec",
    "SourceSpec",
    "REGISTRY",
    "get_source",
    "BucketListing",
    "ResolvedVersion",
    "SourceFile",
    "list_bucket",
    "resolve_direct",
    "resolve_latest",
    "resolve_source",
]
