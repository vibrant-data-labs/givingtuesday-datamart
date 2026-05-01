"""
Post-ingest validation for Datamart staging tables.

Runs after ``stream_csv_url_to_table`` returns successfully but before the
``ingest_runs`` row is finalized. Hard-fail checks raise ``ValidationError``
and cause the run to be marked ``failed`` (the caller catches). Soft
warnings are returned in the results list with ``status='warn'`` and do not
raise — the run stays ``success`` but the warnings are recorded in the
``validation`` JSONB column so the ``loaded`` command can surface them.

The first pass (v1) is deliberately narrow: if a check needs domain
knowledge we haven't committed to yet (typed schemas, per-source row-count
bounds), it stays out of this module.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.sources.spec import SourceSpec


# Thresholds for soft-warning checks. Hardcoded in v1; tunable per-source
# later if a given source legitimately fluctuates outside these bounds.
ROWCOUNT_DELTA_MIN_RATIO = 0.80
ROWCOUNT_DELTA_MAX_RATIO = 2.00

# The hard-fail threshold for required-column non-null rate. Generous on
# purpose — Datamart CSVs occasionally contain stray blank rows we don't
# want to reject the whole refresh over, but a column that's >1% null is a
# real problem and worth stopping.
REQUIRED_COLUMN_MIN_NONNULL_RATIO = 0.99

# Lineage columns stamped by ingestion; excluded from the reported column
# list so schema-drift detection only watches the upstream columns.
LINEAGE_COLUMNS: frozenset[str] = frozenset(
    {"_source_version", "_source_url", "_ingested_at", "_ingest_run_id"}
)

# Status of an individual check.
CheckStatus = Literal["pass", "fail", "warn"]


class ValidationError(Exception):
    """Raised when a hard-fail validation check fails.

    Carries the full list of check results (passes + warnings + failures)
    so callers can persist them to ``ingest_runs.validation``.
    """

    def __init__(self, message: str, results: list["CheckResult"] | None = None):
        super().__init__(message)
        self.results: list["CheckResult"] = results or []


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def _split_qualified_table(staging_table: str) -> tuple[str, str]:
    """Split ``schema.table`` into (schema, table). Defaults to ``public``."""
    if "." in staging_table:
        schema, _, name = staging_table.partition(".")
        return schema, name
    return "public", staging_table


def _fetch_prior_successful_run(
    logical_name: str, current_source_version: str, db_config: dict
) -> tuple[int | None, list[str] | None]:
    """Return (row_count, column_list) from the most recent prior successful run.

    "Prior" means a successful run of the same logical_name with a different
    source_version — so re-ingests of the same version via ``--force`` don't
    compare against themselves.
    """
    from givingtuesday_datamart.ingestion import INGEST_RUNS_TABLE

    with get_session(config=db_config) as session:
        row = session.execute(
            text(
                f"""
                SELECT row_count, column_list
                FROM {INGEST_RUNS_TABLE}
                WHERE logical_name = :logical_name
                  AND status = 'success'
                  AND source_version <> :current_version
                ORDER BY finished_at DESC
                LIMIT 1
                """
            ),
            {
                "logical_name": logical_name,
                "current_version": current_source_version,
            },
        ).first()
    if row is None:
        return None, None
    row_count, column_list_json = row
    # column_list is stored as JSONB; SQLAlchemy decodes to list already, but
    # tolerate the defensive case where it comes back as a string.
    if isinstance(column_list_json, str):
        column_list = json.loads(column_list_json)
    else:
        column_list = column_list_json
    return row_count, column_list


def _check_row_count_positive(row_count: int) -> CheckResult:
    if row_count <= 0:
        return CheckResult(
            name="row_count_positive",
            status="fail",
            detail=f"Staging table has {row_count} rows after ingest.",
        )
    return CheckResult(
        name="row_count_positive",
        status="pass",
        detail=f"{row_count:,} rows ingested.",
    )


def _check_lineage_columns(
    staging_table: str, column_list_including_lineage: list[str], db_config: dict
) -> CheckResult:
    """Confirm every lineage column is present AND non-null on every row."""
    missing = LINEAGE_COLUMNS - set(column_list_including_lineage)
    if missing:
        return CheckResult(
            name="lineage_columns",
            status="fail",
            detail=f"Missing lineage columns: {sorted(missing)}",
        )

    # Any NULLs in a lineage column means the stamping pass silently failed.
    null_predicates = " OR ".join(f'"{c}" IS NULL' for c in sorted(LINEAGE_COLUMNS))
    with get_session(config=db_config) as session:
        null_count = session.execute(
            text(f"SELECT COUNT(*) FROM {staging_table} WHERE {null_predicates}")
        ).scalar_one()
    if null_count:
        return CheckResult(
            name="lineage_columns",
            status="fail",
            detail=f"{null_count:,} rows have NULL in at least one lineage column.",
        )
    return CheckResult(
        name="lineage_columns",
        status="pass",
        detail="All 4 lineage columns present and non-null on every row.",
    )


def _check_required_columns(
    spec: SourceSpec,
    staging_table: str,
    column_list: list[str],
    row_count: int,
    db_config: dict,
) -> CheckResult:
    if not spec.required_columns:
        return CheckResult(
            name="required_columns",
            status="pass",
            detail="No required_columns declared for this source (skipped).",
        )

    present = set(column_list)
    missing = [c for c in spec.required_columns if c not in present]
    if missing:
        return CheckResult(
            name="required_columns",
            status="fail",
            detail=f"Declared required columns absent from staging table: {missing}",
        )

    # One query returns the non-null counts for every required column.
    select_parts = ", ".join(
        f'COUNT("{c}") AS c_{i}' for i, c in enumerate(spec.required_columns)
    )
    with get_session(config=db_config) as session:
        row = session.execute(
            text(f"SELECT {select_parts} FROM {staging_table}")
        ).first()
    nonnull_counts = dict(zip(spec.required_columns, row))

    offenders: list[str] = []
    for col, nn in nonnull_counts.items():
        ratio = (nn / row_count) if row_count else 0.0
        if ratio < REQUIRED_COLUMN_MIN_NONNULL_RATIO:
            offenders.append(f"{col}={ratio:.4f}")
    if offenders:
        return CheckResult(
            name="required_columns",
            status="fail",
            detail=(
                f"Required columns below {REQUIRED_COLUMN_MIN_NONNULL_RATIO:.0%} "
                f"non-null: {offenders}"
            ),
        )
    return CheckResult(
        name="required_columns",
        status="pass",
        detail=f"All required columns ≥ {REQUIRED_COLUMN_MIN_NONNULL_RATIO:.0%} non-null.",
    )


def _check_schema_drift(
    column_list: list[str], prior_column_list: list[str] | None
) -> list[CheckResult]:
    """Emit a fail result for removed columns and a warn for added columns."""
    if prior_column_list is None:
        return [
            CheckResult(
                name="schema_drift",
                status="pass",
                detail="No prior successful run; schema-drift check skipped.",
            )
        ]

    current = set(column_list)
    prior = set(prior_column_list)
    removed = sorted(prior - current)
    added = sorted(current - prior)

    results: list[CheckResult] = []
    if removed:
        results.append(
            CheckResult(
                name="schema_drift_removed",
                status="fail",
                detail=f"Columns present in prior successful run are now absent: {removed}",
            )
        )
    if added:
        results.append(
            CheckResult(
                name="schema_drift_added",
                status="warn",
                detail=f"New columns vs prior successful run: {added}",
            )
        )
    if not removed and not added:
        results.append(
            CheckResult(
                name="schema_drift",
                status="pass",
                detail="Column set matches prior successful run.",
            )
        )
    return results


def _check_row_count_delta(row_count: int, prior_row_count: int | None) -> CheckResult:
    if prior_row_count is None or prior_row_count == 0:
        return CheckResult(
            name="row_count_delta",
            status="pass",
            detail="No prior row count; delta check skipped.",
        )
    ratio = row_count / prior_row_count
    if ratio < ROWCOUNT_DELTA_MIN_RATIO or ratio > ROWCOUNT_DELTA_MAX_RATIO:
        return CheckResult(
            name="row_count_delta",
            status="warn",
            detail=(
                f"Row count {row_count:,} is {ratio:.2f}x prior "
                f"{prior_row_count:,} (outside "
                f"[{ROWCOUNT_DELTA_MIN_RATIO:.2f}x, {ROWCOUNT_DELTA_MAX_RATIO:.2f}x])."
            ),
        )
    return CheckResult(
        name="row_count_delta",
        status="pass",
        detail=f"Row count {row_count:,} is {ratio:.2f}x prior {prior_row_count:,}.",
    )


def validate_ingest(
    spec: SourceSpec,
    staging_table: str,
    source_version: str,
    row_count: int,
    db_config: dict,
) -> tuple[list[CheckResult], list[str]]:
    """Run all post-ingest validation checks.

    Returns ``(results, column_list)`` — the full list of check results
    (including passes), plus the non-lineage column list for persisting to
    ``ingest_runs.column_list`` for the next run's drift check.

    Raises ``ValidationError`` if any check status is ``'fail'``. Callers
    catch this and mark the run failed.
    """
    # Fetch column list once (including lineage) for the lineage-column check,
    # then strip lineage for everything downstream.
    schema, name = _split_qualified_table(staging_table)
    with get_session(config=db_config) as session:
        rows = session.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = :schema AND table_name = :name
                ORDER BY ordinal_position
                """
            ),
            {"schema": schema, "name": name},
        ).all()
    all_columns = [r[0] for r in rows]
    column_list = [c for c in all_columns if c not in LINEAGE_COLUMNS]

    prior_row_count, prior_column_list = _fetch_prior_successful_run(
        spec.logical_name, source_version, db_config
    )

    results: list[CheckResult] = []
    results.append(_check_row_count_positive(row_count))
    results.append(_check_lineage_columns(staging_table, all_columns, db_config))
    results.append(
        _check_required_columns(spec, staging_table, column_list, row_count, db_config)
    )
    results.extend(_check_schema_drift(column_list, prior_column_list))
    results.append(_check_row_count_delta(row_count, prior_row_count))

    failures = [r for r in results if r.status == "fail"]
    warnings = [r for r in results if r.status == "warn"]

    for w in warnings:
        logger.warning("Validation warning [%s] %s: %s", spec.logical_name, w.name, w.detail)

    if failures:
        summary = "; ".join(f"[{f.name}] {f.detail}" for f in failures)
        raise ValidationError(
            f"Validation failed for {spec.logical_name}: {summary}",
            results=results,
        )

    return results, column_list


__all__ = [
    "CheckResult",
    "CheckStatus",
    "LINEAGE_COLUMNS",
    "REQUIRED_COLUMN_MIN_NONNULL_RATIO",
    "ROWCOUNT_DELTA_MAX_RATIO",
    "ROWCOUNT_DELTA_MIN_RATIO",
    "ValidationError",
    "validate_ingest",
]
