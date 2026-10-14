"""SourceSpec / ColumnSpec / IndexSpec / DedupeSpec dataclasses for registered Datamart sources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


# "EO-BMF" is the IRS Exempt Organizations Business Master File: not a return
# form but the IRS's register of exempt orgs, one row per EIN.
FormType = Literal["990", "990-EZ", "990-PF", "EO-BMF"]


@dataclass(frozen=True)
class ColumnSpec:
    source_name: str
    sql_name: str
    pg_type: str = "TEXT"
    nullable: bool = True
    description: str | None = None


@dataclass(frozen=True)
class IndexSpec:
    """A btree index to (re)create on the staging table after COPY.

    Staging tables are recreated on every refresh (DROP TABLE + COPY), so any
    index applied out-of-band is lost on the next ingest. Declaring indexes
    here makes them part of the refresh contract: ingestion recreates them
    after each successful COPY. ``unique`` enforces a one-row-per-key
    contract (only meaningful together with a ``DedupeSpec``).
    """

    name: str
    columns: tuple[str, ...]
    unique: bool = False

    def create_sql(self, table: str) -> str:
        col_list = ", ".join(self.columns)
        kind = "UNIQUE INDEX" if self.unique else "INDEX"
        return f"CREATE {kind} IF NOT EXISTS {self.name} ON {table} ({col_list})"


@dataclass(frozen=True)
class DedupeSpec:
    """Collapse the staging table to one row per ``key`` after COPY.

    Staging is otherwise byte-faithful to the source; this is the one
    declared exception, for sources that repeat a natural key across their
    files (the IRS BMF lists territories in both ``eo4`` and ``eo_pr`` /
    ``eo_xx``). ``order_by`` is a SQL ``ORDER BY`` fragment over the staging
    columns that chooses the row to keep (first wins). Ingestion appends two
    tie-breaks so the choice is deterministic: the source file's position in
    the spec's URL list, then the physical row id. Losing rows are deleted;
    the count is logged and recorded in the run's validation JSON.
    """

    key: tuple[str, ...]
    order_by: str


@dataclass(frozen=True)
class SourceSpec:
    """One logical Datamart source (a stable name for a table we ingest).

    Two ways to locate the file(s):

    * **S3** (GivingTuesday's public bucket): the CSV is dated and reissued
      periodically; ``filename_regex`` is what the resolver uses to find the
      latest matching file under ``s3_prefix``. Its first capture group must
      capture the ``YYYY_MM_DD`` release date so the resolver can pick the
      newest.
    * **Direct HTTPS** (``urls``): plain files with no version in their
      names, republished in place (the IRS BMF). The server's
      ``Last-Modified`` is the version: the resolver HEADs every URL and
      stamps the run with the newest date. All files land in the same
      staging table, in list order; each row's ``_source_url`` says which.

    Exactly one of the two must be configured.
    """

    logical_name: str
    staging_table_name: str
    form_type: FormType
    description: str

    s3_bucket: str = ""
    s3_prefix: str = ""
    filename_regex: str = ""
    urls: tuple[str, ...] = ()

    columns: tuple[ColumnSpec, ...] = ()
    primary_key: tuple[str, ...] | None = None

    # Columns the validator requires to be present and mostly non-null on every
    # ingest. Empty tuple = skip the required-column check for this source
    # (safe default — used when we haven't yet committed to a key column set).
    required_columns: tuple[str, ...] = ()

    # When True, this source is excluded from the default `refresh` (no
    # --source filter). It can still be ingested explicitly via
    # `refresh --source <logical_name>`. Used to keep the registry truthful
    # about what we've configured to ingest while keeping bulky-but-deferred
    # sources from being re-ingested by accident. Lineage rows in
    # datamart_meta.ingest_runs are unaffected.
    skip_default_refresh: bool = False

    # Indexes recreated on the staging table after each successful COPY.
    # Applied + ANALYZEd inline so downstream consumers (e.g. the frontend
    # profile page) don't regress to seq-scan latencies on re-ingest.
    indexes: tuple[IndexSpec, ...] = ()

    # One-row-per-key collapse applied after COPY and before the indexes
    # (see DedupeSpec). None = staging keeps every source row.
    dedupe: DedupeSpec | None = None

    # Download each file to a temp file before parsing, instead of streaming
    # straight from the response. For servers that close a connection the
    # COPY pace leaves idle (irs.gov does; S3 does not). Costs the file's
    # size in temp disk for the duration of one file.
    spool_to_disk: bool = False

    def __post_init__(self) -> None:
        s3 = bool(self.s3_bucket and self.filename_regex)
        if s3 == bool(self.urls):
            raise ValueError(
                f"{self.logical_name}: configure either s3_bucket + filename_regex "
                "or urls, not both and not neither"
            )

    def compiled_regex(self) -> re.Pattern[str]:
        return re.compile(self.filename_regex)

    def key_for(self, filename: str) -> str:
        return f"{self.s3_prefix}{filename}"

    def url_for(self, filename: str) -> str:
        return f"https://{self.s3_bucket}.s3.us-east-1.amazonaws.com/{self.key_for(filename)}"
