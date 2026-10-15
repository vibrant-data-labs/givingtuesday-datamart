"""
Ingestion entrypoint for registered Datamart sources.

Consumes a ``SourceSpec`` + a ``ResolvedVersion`` from the sources module,
streams every file of that version into the staging table, stamps every
ingested row with lineage metadata, and records the run (with a per-file
manifest) in ``datamart_meta.ingest_runs``. Re-running for a ``(logical_name, source_version)``
that already has a successful run is a no-op unless ``force=True``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import text

from givingtuesday_datamart._internal.config import get_configuration
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.sources.resolver import (
    ResolvedVersion,
    SourceFile,
    resolve_source,
)
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
        # operators work cleanly in ad-hoc queries. source_manifest came with
        # the multi-file (direct HTTPS) sources: one entry per file streamed,
        # with the server's Last-Modified and the bytes, sha256 and rows
        # actually loaded. source_url keeps holding one URL (the first file).
        session.execute(
            text(
                f"""
                ALTER TABLE {INGEST_RUNS_TABLE}
                ADD COLUMN IF NOT EXISTS column_list JSONB,
                ADD COLUMN IF NOT EXISTS validation JSONB,
                ADD COLUMN IF NOT EXISTS source_manifest JSONB
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
                    source_url, started_at, status, source_manifest
                ) VALUES (
                    :run_id, :logical_name, :staging_table, :source_version,
                    :source_url, :started_at, 'started', CAST(:source_manifest AS JSONB)
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
                "source_manifest": json.dumps([asdict(f) for f in _files_of(resolved)]),
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
    source_manifest: list[dict] | None = None,
) -> None:
    """Write the terminal state of an ingest run.

    ``column_list`` and ``validation`` are JSONB on the DB side; we cast the
    bound parameter explicitly so sqlalchemy/psycopg2 doesn't try to infer.
    Either can be None — on a COPY failure (before validation runs) both
    will be, which is the right signal that validation never got a chance.
    ``source_manifest`` replaces the pre-download manifest written at start
    with the per-file bytes, sha256 and rows actually streamed; None leaves
    the start-of-run version in place.
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
                    validation = CAST(:validation AS JSONB),
                    source_manifest = COALESCE(CAST(:source_manifest AS JSONB), source_manifest)
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
                "source_manifest": (
                    json.dumps(source_manifest) if source_manifest is not None else None
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


def dedupe_sql(spec: SourceSpec) -> str:
    """The DELETE that keeps one row per ``spec.dedupe.key``: first under
    the spec's ``order_by``, then by the source file's position in the
    ingest order (bound as ``:urls``), then by physical row id."""
    assert spec.dedupe is not None
    key = ", ".join(f'"{c}"' for c in spec.dedupe.key)
    return f"""
        DELETE FROM {spec.staging_table_name} t
        USING (
            SELECT ctid,
                   ROW_NUMBER() OVER (
                       PARTITION BY {key}
                       ORDER BY {spec.dedupe.order_by},
                                array_position(CAST(:urls AS text[]), _source_url),
                                ctid
                   ) AS rn
            FROM {spec.staging_table_name}
        ) ranked
        WHERE t.ctid = ranked.ctid AND ranked.rn > 1
    """


def _apply_post_ingest_dedupe(spec: SourceSpec, files: tuple[SourceFile, ...], db_config: dict) -> int | None:
    """Collapse ``spec.staging_table_name`` to one row per ``spec.dedupe.key``.

    Keeps the first row per key under the spec's ``order_by``, then the
    source file's position in the ingest order (``_source_url`` says which
    file each row came from), then the physical row id, so the survivor is
    deterministic. Returns the number of rows deleted, or None when the
    spec declares no dedupe. Runs before the indexes so a declared UNIQUE
    index can build.
    """
    if spec.dedupe is None:
        return None
    with get_session(config=db_config) as session:
        removed = session.execute(text(dedupe_sql(spec)), {"urls": [f.url for f in files]}).rowcount
    logger.info(
        "%s: removed %s duplicate rows on (%s), kept first by %s then file order",
        spec.staging_table_name, f"{removed:,}", ", ".join(spec.dedupe.key), spec.dedupe.order_by,
    )
    return removed


def _files_of(resolved: ResolvedVersion) -> tuple[SourceFile, ...]:
    """The files to stream for this version, in order. Older resolvers
    only set ``url``; treat that as a one-file version."""
    return resolved.files or (SourceFile(url=resolved.url, size=resolved.size, last_modified=None),)


def _lineage_values(run_id: str, version: str, url: str, ingested_at: datetime) -> dict[str, str]:
    return {
        "_source_version": version,
        "_source_url": url,
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
    Otherwise creates a new run record, streams every file of the version
    into the staging table in order (the first file recreates the table, the
    rest append), stamps lineage columns on every row (``_source_url`` is
    the file the row came from), applies the spec's dedupe and indexes, and
    finalizes the run record with status, row count and the per-file
    manifest (Last-Modified, bytes, sha256, rows).
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
    files = _files_of(resolved)

    logger.info(
        "Starting ingest: logical_name=%s version=%s run_id=%s table=%s",
        spec.logical_name,
        resolved.version_date,
        run_id,
        spec.staging_table_name,
    )

    db_cfg = datamart_config()
    manifest: list[dict] = []
    row_count = 0
    try:
        for i, f in enumerate(files):
            streamed = stream_csv_url_to_table(
                url=f.url,
                table_name=spec.staging_table_name,
                overwrite=(i == 0),
                extra_columns=_lineage_values(run_id, resolved.version_date, f.url, started_at),
                db_config=db_cfg,
                spool_to_disk=spec.spool_to_disk,
            )
            row_count += streamed.rows
            manifest.append({**asdict(f), "bytes": streamed.bytes, "sha256": streamed.sha256, "rows": streamed.rows})
        dedupe_removed = _apply_post_ingest_dedupe(spec, files, db_cfg)
        if dedupe_removed:
            row_count -= dedupe_removed
    except Exception as err:
        finished_at = datetime.now(timezone.utc)
        _finalize_run(
            run_id=run_id,
            finished_at=finished_at,
            status="failed",
            error=str(err)[:4000],
            source_manifest=manifest or None,
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
            dedupe_removed=dedupe_removed,
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
            source_manifest=manifest,
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
        source_manifest=manifest,
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
    """Resolve the newest version of ``spec`` (S3 listing or direct URLs)
    and ingest it.

    Raises ``LookupError`` if no file in the bucket matches ``spec.filename_regex``.
    """
    resolved = resolve_source(spec)
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
    "dedupe_sql",
    "ensure_meta_schema",
    "find_successful_run",
    "ingest_source",
    "ingest_latest",
]
