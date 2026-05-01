"""
Ingestion entrypoint for registered Datamart sources.

Consumes a ``SourceSpec`` + a ``ResolvedVersion`` from the sources module,
stamps every ingested row with lineage metadata, and records the run in
``datamart_meta.ingest_runs``. Re-running for a ``(logical_name, source_version)``
that already has a successful run is a no-op unless ``force=True``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import text

from givingtuesday_datamart._internal.config import get_configuration
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.sources.resolver import ResolvedVersion, resolve_latest
from givingtuesday_datamart.sources.spec import SourceSpec
from givingtuesday_datamart.validation import ValidationError, validate_ingest
from givingtuesday_datamart.write_data_to_sql import stream_csv_url_to_table


# Dedicated database on the shared VDL RDS host. Kept separate from the main
# VDL database so a bad refresh can never damage unrelated data, backup/restore
# is granular, and we can DROP DATABASE during Phase 1 re-ingests without
# coordinating. Must be created manually on the RDS host before the first
# refresh — the ingestion path does not create databases.
DATAMART_DATABASE = "gt_datamart"

META_SCHEMA = "datamart_meta"
INGEST_RUNS_TABLE = f"{META_SCHEMA}.ingest_runs"


def datamart_config() -> dict:
    """Return a config dict pointing at the datamart database.

    Reads the operator's ``config.ini`` (via :func:`get_configuration`) and
    swaps ``postgres.database`` to ``DATAMART_DATABASE``. Passed as
    ``config=`` to ``get_session`` and as ``db_config=`` to the
    ``write_data_to_sql`` helpers.
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
    """Create ``datamart_meta`` schema and ``ingest_runs`` table if missing.

    Also runs additive ``ADD COLUMN IF NOT EXISTS`` migrations for columns
    introduced after the initial table creation, so existing databases pick
    them up without a separate migration step.
    """
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
        # Added with the validation step. JSONB (not TEXT+cast) so jsonb
        # operators work cleanly in ad-hoc queries.
        session.execute(
            text(
                f"""
                ALTER TABLE {INGEST_RUNS_TABLE}
                ADD COLUMN IF NOT EXISTS column_list JSONB,
                ADD COLUMN IF NOT EXISTS validation JSONB
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
    column_list: list[str] | None = None,
    validation: list[dict] | None = None,
) -> None:
    """Write the terminal state of an ingest run.

    ``column_list`` and ``validation`` are JSONB on the DB side; we cast the
    bound parameter explicitly so sqlalchemy/psycopg2 doesn't try to infer.
    Either can be None — on a COPY failure (before validation runs) both
    will be, which is the right signal that validation never got a chance.
    """
    with get_session(config=datamart_config()) as session:
        session.execute(
            text(
                f"""
                UPDATE {INGEST_RUNS_TABLE}
                SET finished_at = :finished_at,
                    status = :status,
                    row_count = :row_count,
                    error = :error,
                    column_list = CAST(:column_list AS JSONB),
                    validation = CAST(:validation AS JSONB)
                WHERE run_id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "finished_at": finished_at,
                "status": status,
                "row_count": row_count,
                "error": error,
                "column_list": (
                    json.dumps(column_list) if column_list is not None else None
                ),
                "validation": (
                    json.dumps({"checks": validation}) if validation is not None else None
                ),
            },
        )


def _apply_post_ingest_indexes(spec: SourceSpec, db_config: dict) -> None:
    """Recreate every declared index on ``spec.staging_table_name`` and ANALYZE.

    Staging tables are recreated (DROP + COPY) on each refresh, so any prior
    indexes are gone by the time we get here. We issue ``CREATE INDEX IF NOT
    EXISTS`` for each declared index and then ``ANALYZE`` so the planner has
    fresh stats. Best-effort: a failure here is logged but does not fail the
    ingest — the data is loaded; a missing index is a perf regression, not a
    correctness bug.
    """
    if not spec.indexes:
        return
    with get_session(config=db_config) as session:
        for idx in spec.indexes:
            sql = idx.create_sql(spec.staging_table_name)
            logger.info("Creating index for %s: %s", spec.logical_name, sql)
            session.execute(text(sql))
        session.execute(text(f"ANALYZE {spec.staging_table_name}"))


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
        row_count = stream_csv_url_to_table(
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

    # Recreate declared indexes before validation so that (a) downstream
    # readers always see an indexed staging table after a successful COPY,
    # and (b) ANALYZE runs against fresh data so the planner doesn't pick a
    # bad plan on the first warm query. Index failures are logged and
    # swallowed inside the helper — they're a perf regression, not a
    # correctness bug, and shouldn't sink an otherwise-good ingest.
    try:
        _apply_post_ingest_indexes(spec, db_cfg)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to apply post-ingest indexes for %s (continuing)",
            spec.logical_name,
        )

    # Post-ingest validation. Hard-fail checks raise ValidationError → the run
    # is marked 'failed' (the staging table stays as-is; consumers filter by
    # status='success', so failed runs are self-quarantining). Soft warnings
    # are returned inline and recorded in the validation JSONB.
    try:
        check_results, column_list = validate_ingest(
            spec=spec,
            staging_table=spec.staging_table_name,
            source_version=resolved.version_date,
            row_count=row_count,
            db_config=db_cfg,
        )
    except ValidationError as verr:
        finished_at = datetime.now(timezone.utc)
        checks = getattr(verr, "results", None)
        _finalize_run(
            run_id=run_id,
            finished_at=finished_at,
            status="failed",
            row_count=row_count,
            error=str(verr)[:4000],
            validation=[c.to_dict() for c in checks] if checks else None,
        )
        logger.error("Validation failed for %s: %s", spec.logical_name, verr)
        return IngestResult(
            run_id=run_id,
            logical_name=spec.logical_name,
            staging_table=spec.staging_table_name,
            source_version=resolved.version_date,
            source_url=resolved.url,
            status="failed",
            row_count=row_count,
            started_at=started_at,
            finished_at=finished_at,
            error=str(verr),
        )

    finished_at = datetime.now(timezone.utc)
    _finalize_run(
        run_id=run_id,
        finished_at=finished_at,
        status="success",
        row_count=row_count,
        column_list=column_list,
        validation=[c.to_dict() for c in check_results],
    )
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
