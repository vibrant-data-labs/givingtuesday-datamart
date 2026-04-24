"""SourceSpec / ColumnSpec dataclasses for registered Datamart sources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


FormType = Literal["990", "990-PF"]


@dataclass(frozen=True)
class ColumnSpec:
    source_name: str
    sql_name: str
    pg_type: str = "TEXT"
    nullable: bool = True
    description: str | None = None


@dataclass(frozen=True)
class SourceSpec:
    """One logical Datamart source (a stable name for a table we ingest).

    The CSV file itself is dated and reissued periodically; `filename_regex`
    is what the resolver uses to find the latest matching file in the bucket.
    The first capture group of `filename_regex` must capture the YYYY_MM_DD
    release date so the resolver can pick the newest.
    """

    logical_name: str
    staging_table_name: str
    form_type: FormType
    description: str

    s3_bucket: str
    s3_prefix: str
    filename_regex: str

    columns: tuple[ColumnSpec, ...] = ()
    primary_key: tuple[str, ...] | None = None
    needs_streaming: bool = True

    def compiled_regex(self) -> re.Pattern[str]:
        return re.compile(self.filename_regex)

    def key_for(self, filename: str) -> str:
        return f"{self.s3_prefix}{filename}"

    def url_for(self, filename: str) -> str:
        return f"https://{self.s3_bucket}.s3.us-east-1.amazonaws.com/{self.key_for(filename)}"
