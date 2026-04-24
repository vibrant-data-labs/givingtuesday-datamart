"""
Ingestion entrypoint for registered Datamart sources.

Consumes a ``SourceSpec`` + a ``ResolvedVersion`` from the sources module,
stamps every ingested row with lineage metadata, and records the run in
``datamart_meta.ingest_runs``. Re-running for a ``(logical_name, source_version)``
that already has a successful run is a no-op unless ``force=True``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import text
from vdl_tools.shared_tools.database_cache.database_utils import get_session
from vdl_tools.shared_tools.tools.config_utils import get_configuration
from vdl_tools.shared_tools.tools.logger import logger

from givingtuesday_datamart.sources.resolver import ResolvedVersion, resolve_latest
from givingtuesday_datamart.sources.spec import SourceSpec
from givingtuesday_datamart.write_data_to_sql import (
    stream_csv_url_to_table,
    write_csv_url_to_table_standard,
)


# Dedicated database on the shared VDL RDS host. Kept separate from the main
# VDL database so a bad refresh can never damage unrelated data, backup/restore
# is granular, and we can DROP DATABASE during Phase 1 re-ingests without
# coordinating. Must be created manually on the RDS host before the first
# refresh — the ingestion path does not create databases.
DATAMART_DATABASE = "gt_datamart"

META_SCHEMA = "datamart_meta"
INGEST_RUNS_TABLE = f"{META_SCHEMA}.ingest_runs"


def datamart_config() -> dict:
    """Return a vdl-tools config dict pointing at the datamart database.

    Clones the default configuration and swaps ``postgres.database`` to
    ``DATAMART_DATABASE``. Passed as ``config=`` to ``get_session`` and as
    ``db_config=`` to the ``write_data_to_sql`` helpers.
    """
    cfg = get_configuration()
    return {**cfg, "postgres": {**cfg["postgres"], "database": DATAMART_DATABASE}}

# Lineage columns stamped on every row of every ingested staging table.
# Stored as TEXT on staging (matches the staging convention of all-TEXT); the
# ingest_runs table carries the authoritative, properly-typed copy.
LINEAGE_COLUMN_NAMES: tuple[str, ...] = (
    "_source_version",
    "_source_url",
    "_ingested_at",
    "_ingest_run_id",
)

Status = Literal["success", "skipped", "failed"]


@dataclass(frozen=True)
class IngestResult:
    run_id: str
    logical_name: str
    staging_table: str
    source_version: str
    source_url: str
    status: Status
    row_count: int | None
    started_at: datetime
    finished_at: datetime
    error: str | None = None


def ensure_meta_schema() -> None:
    """Create ``datamart_meta`` schema and ``ingest_runs`` table if missing."""
    with get_session(config=datamart_config()) as session:
        session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {META_SCHEMA}"))
        session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {INGEST_RUNS_TABLE} (
                    run_id UUID PRIMARY KEY,
                    logical_name TEXT NOT NULL,
                    staging_table TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    finished_at TIMESTAMPTZ,
                    status TEXT NOT NULL,
                    row_count BIGINT,
                    error TEXT
                )
                """
            )
        )
        session.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS ix_ingest_runs_logical_version
                ON {INGEST_RUNS_TABLE} (logical_name, source_version)
                """
            )
        )


def find_successful_run(logical_name: str, source_version: str) -> str | None:
    """Return the run_id of the most recent successful run for this source + version, or None."""
    with get_session(config=datamart_config()) as session:
        row = session.execute(
            text(
                f"""
                SELECT run_id FROM {INGEST_RUNS_TABLE}
                WHERE logical_name = :logical_name
                  AND source_version = :source_version
                  AND status = 'success'
                ORDER BY finished_at DESC
                LIMIT 1
                """
            ),
            {"logical_name": logical_name, "source_version": source_version},
        ).first()
        return str(row[0]) if row is not None else None


def _insert_started_run(
    *,
    run_id: str,
    spec: SourceSpec,
    resolved: ResolvedVersion,
    started_at: datetime,
) -> None:
    with get_session(config=datamart_config()) as session:
        session.execute(
            text(
                f"""
                INSERT INTO {INGEST_RUNS_TABLE} (
                    run_id, logical_name, staging_table, source_version,
                    source_url, started_at, status
                ) VALUES (
                    :run_id, :logical_name, :staging_table, :source_version,
                    :source_url, :started_at, 'started'
                )
                """
            ),
            {
                "run_id": run_id,
                "logical_name": spec.logical_name,
                "staging_table": spec.staging_table_name,
                "source_version": resolved.version_date,
                "source_url": resolved.url,
                "started_at": started_at,
            },
        )


def _finalize_run(
    *,
    run_id: str,
    finished_at: datetime,
    status: str,
    row_count: int | None = None,
    error: str | None = None,
) -> None:
    with get_session(config=datamart_config()) as session:
        session.execute(
            text(
                f"""
                UPDATE {INGEST_RUNS_TABLE}
                SET finished_at = :finished_at,
                    status = :status,
                    row_count = :row_count,
                    error = :error
                WHERE run_id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "finished_at": finished_at,
                "status": status,
                "row_count": row_count,
                "error": error,
            },
        )


def _lineage_values(run_id: str, resolved: ResolvedVersion, ingested_at: datetime) -> dict[str, str]:
    return {
        "_source_version": resolved.version_date,
        "_source_url": resolved.url,
        "_ingested_at": ingested_at.isoformat(),
        "_ingest_run_id": run_id,
    }


def ingest_source(
    spec: SourceSpec,
    resolved: ResolvedVersion,
    *,
    force: bool = False,
) -> IngestResult:
    """Ingest a single source into its staging table with lineage + run tracking.

    If a prior successful run exists for the same ``(logical_name, source_version)``
    and ``force`` is False, returns a ``skipped`` result without touching the DB.
    Otherwise creates a new run record, ingests the CSV (streaming or standard
    based on ``spec.needs_streaming``), stamps lineage columns on every row, and
    finalizes the run record with status + row count.
    """
    ensure_meta_schema()
    started_at = datetime.now(timezone.utc)

    existing_run_id = find_successful_run(spec.logical_name, resolved.version_date)
    if existing_run_id and not force:
        logger.info(
            "Skipping %s: already ingested for version %s (run_id=%s)",
            spec.logical_name,
            resolved.version_date,
            existing_run_id,
        )
        return IngestResult(
            run_id=existing_run_id,
            logical_name=spec.logical_name,
            staging_table=spec.staging_table_name,
            source_version=resolved.version_date,
            source_url=resolved.url,
            status="skipped",
            row_count=None,
            started_at=started_at,
            finished_at=started_at,
        )

    run_id = str(uuid.uuid4())
    _insert_started_run(run_id=run_id, spec=spec, resolved=resolved, started_at=started_at)
    lineage = _lineage_values(run_id, resolved, started_at)

    logger.info(
        "Starting ingest: logical_name=%s version=%s run_id=%s table=%s",
        spec.logical_name,
        resolved.version_date,
        run_id,
        spec.staging_table_name,
    )

    db_cfg = datamart_config()
    try:
        if spec.needs_streaming:
            row_count = stream_csv_url_to_table(
                url=resolved.url,
                table_name=spec.staging_table_name,
                overwrite=True,
                extra_columns=lineage,
                db_config=db_cfg,
            )
        else:
            row_count = write_csv_url_to_table_standard(
                url=resolved.url,
                table_name=spec.staging_table_name,
                overwrite=True,
                extra_columns=lineage,
                db_config=db_cfg,
            )
    except Exception as err:
        finished_at = datetime.now(timezone.utc)
        _finalize_run(
            run_id=run_id,
            finished_at=finished_at,
            status="failed",
            error=str(err)[:4000],
        )
        logger.exception("Ingest failed for %s", spec.logical_name)
        return IngestResult(
            run_id=run_id,
            logical_name=spec.logical_name,
            staging_table=spec.staging_table_name,
            source_version=resolved.version_date,
            source_url=resolved.url,
            status="failed",
            row_count=None,
            started_at=started_at,
            finished_at=finished_at,
            error=str(err),
        )

    finished_at = datetime.now(timezone.utc)
    _finalize_run(run_id=run_id, finished_at=finished_at, status="success", row_count=row_count)
    logger.info(
        "Ingest success: %s rows=%s duration=%.1fs",
        spec.logical_name,
        row_count,
        (finished_at - started_at).total_seconds(),
    )
    return IngestResult(
        run_id=run_id,
        logical_name=spec.logical_name,
        staging_table=spec.staging_table_name,
        source_version=resolved.version_date,
        source_url=resolved.url,
        status="success",
        row_count=row_count,
        started_at=started_at,
        finished_at=finished_at,
    )


def ingest_latest(spec: SourceSpec, *, force: bool = False) -> IngestResult:
    """Resolve the newest file for ``spec`` and ingest it.

    Raises ``LookupError`` if no file in the bucket matches ``spec.filename_regex``.
    """
    resolved = resolve_latest(spec)
    if resolved is None:
        raise LookupError(
            f"No file in s3://{spec.s3_bucket}/{spec.s3_prefix} matches "
            f"regex {spec.filename_regex!r} for source {spec.logical_name!r}"
        )
    return ingest_source(spec, resolved, force=force)


__all__ = [
    "DATAMART_DATABASE",
    "LINEAGE_COLUMN_NAMES",
    "META_SCHEMA",
    "INGEST_RUNS_TABLE",
    "IngestResult",
    "Status",
    "datamart_config",
    "ensure_meta_schema",
    "find_successful_run",
    "ingest_source",
    "ingest_latest",
]
