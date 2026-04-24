"""
Source registry for the Giving Tuesday Datamart.

A `SourceSpec` describes one logical table we ingest (e.g. "irs_990_basic_fields")
independently of which dated S3 file currently represents it. The resolver hits
the public S3 bucket and picks the latest matching file by date, so consumers
never hardcode a dated URL.
"""

from givingtuesday_datamart.sources.spec import ColumnSpec, SourceSpec
from givingtuesday_datamart.sources.registry import REGISTRY, get_source
from givingtuesday_datamart.sources.resolver import (
    BucketListing,
    ResolvedVersion,
    list_bucket,
    resolve_latest,
)

__all__ = [
    "ColumnSpec",
    "SourceSpec",
    "REGISTRY",
    "get_source",
    "BucketListing",
    "ResolvedVersion",
    "list_bucket",
    "resolve_latest",
]
