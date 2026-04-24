"""
``python -m givingtuesday_datamart.sources <command>``

Subcommands:

* ``status`` — print each registered source alongside the newest matching file
  currently in S3. Answers "is our pinned ingestion stale?" without running a
  full refresh. S3-only; no DB connection.
* ``loaded`` — print the most recent run per source from
  ``datamart_meta.ingest_runs``. Answers "what tables have been loaded, at
  what version, and how long ago?" Hits the DB; no S3.
* ``refresh`` — resolve the newest file for each registered source and ingest
  it into its staging table, stamping lineage columns on every row and
  recording the run in ``datamart_meta.ingest_runs``. Idempotent: re-running
  against the same source version is a no-op unless ``--force`` is passed.
* ``build-canonical`` — (re)build the Phase 2 canonical tables
  (``public.nonprofit_canonical`` + ``public.nonprofit_text``) from current
  staging. DROP + CREATE, idempotent. Run after a successful ``refresh``.
"""

from __future__ import annotations

import argparse
import sys

from botocore.exceptions import BotoCoreError, ClientError
from vdl_tools.shared_tools.tools.logger import logger

from givingtuesday_datamart.sources.registry import REGISTRY, S3_BUCKET, S3_PREFIX, get_source
from givingtuesday_datamart.sources.resolver import list_bucket, resolve_latest
from givingtuesday_datamart.sources.spec import SourceSpec


def _format_size(size: int) -> str:
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 ** 3):.2f} GB"
    if size >= 1024 * 1024:
        return f"{size / (1024 ** 2):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def cmd_status() -> int:
    try:
        listing = list_bucket(S3_BUCKET, S3_PREFIX)
    except (BotoCoreError, ClientError) as err:
        logger.error("Failed to list s3://%s/%s: %s", S3_BUCKET, S3_PREFIX, err)
        return 2

    headers = ("logical_name", "staging_table", "version_date", "filename", "size")
    rows: list[tuple[str, str, str, str, str]] = []
    missing: list[SourceSpec] = []
    for spec in REGISTRY:
        resolved = resolve_latest(spec, listing=listing)
        if resolved is None:
            missing.append(spec)
            rows.append((spec.logical_name, spec.staging_table_name, "—", "NOT FOUND", ""))
            continue
        rows.append(
            (
                spec.logical_name,
                spec.staging_table_name,
                resolved.version_date,
                resolved.filename,
                _format_size(resolved.size),
            )
        )

    widths = [
        max(len(row[i]) for row in (*rows, headers))
        for i in range(len(headers))
    ]

    def fmt_row(row: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    print(fmt_row(headers))
    print(fmt_row(tuple("-" * w for w in widths)))
    for row in rows:
        print(fmt_row(row))
    if missing:
        logger.warning(
            "%d source(s) did not match any file in S3: %s",
            len(missing),
            ", ".join(f"{s.logical_name} (regex={s.filename_regex})" for s in missing),
        )
        return 1
    return 0


def _select_specs(source_names: list[str] | None) -> list[SourceSpec]:
    if not source_names:
        return list(REGISTRY)
    return [get_source(name) for name in source_names]


def _format_age(delta) -> str:
    """Format a timedelta as a short, human-readable age string."""
    if delta is None:
        return "—"
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "—"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def cmd_loaded() -> int:
    """Show the latest run per source from datamart_meta.ingest_runs."""
    # Imports are deferred so `status` does not pay the SQLAlchemy init cost.
    from datetime import datetime, timezone

    from sqlalchemy import text

    from givingtuesday_datamart.ingestion import (
        INGEST_RUNS_TABLE,
        datamart_config,
        ensure_meta_schema,
    )
    from vdl_tools.shared_tools.database_cache.database_utils import get_session

    # Make sure the meta table exists so the query below doesn't 42P01 when the
    # database exists but no refresh has run yet.
    ensure_meta_schema()

    # Most recent run per logical_name (by started_at), regardless of status.
    # Showing failed/skipped runs too — a failed run is useful information,
    # not something to hide.
    query = text(
        f"""
        SELECT DISTINCT ON (logical_name)
            logical_name, staging_table, source_version, status, row_count,
            started_at, finished_at, error, validation
        FROM {INGEST_RUNS_TABLE}
        ORDER BY logical_name, started_at DESC
        """
    )
    with get_session(config=datamart_config()) as session:
        rows = session.execute(query).all()

    latest_by_name = {r.logical_name: r for r in rows}
    now = datetime.now(timezone.utc)

    def _warn_count(validation) -> int:
        """Pull warning count from the validation JSONB blob (tolerant of None/str)."""
        import json as _json
        if validation is None:
            return 0
        if isinstance(validation, str):
            try:
                validation = _json.loads(validation)
            except (ValueError, TypeError):
                return 0
        checks = validation.get("checks") if isinstance(validation, dict) else None
        if not checks:
            return 0
        return sum(1 for c in checks if isinstance(c, dict) and c.get("status") == "warn")

    headers = (
        "logical_name", "staging_table", "status",
        "version", "rows", "last_ingested_at", "age", "warns",
    )
    out_rows: list[tuple[str, ...]] = []
    for spec in REGISTRY:
        run = latest_by_name.get(spec.logical_name)
        if run is None:
            out_rows.append(
                (spec.logical_name, spec.staging_table_name, "never", "—", "—", "—", "—", "—")
            )
            continue
        # Age from finished_at if we have one, else from started_at.
        ref = run.finished_at or run.started_at
        age = _format_age(now - ref) if ref is not None else "—"
        last = ref.strftime("%Y-%m-%d %H:%M UTC") if ref else "—"
        rows_str = f"{run.row_count:,}" if run.row_count is not None else "—"
        warns = _warn_count(run.validation)
        warns_str = str(warns) if warns else "—"
        out_rows.append(
            (
                spec.logical_name,
                spec.staging_table_name,
                run.status,
                run.source_version or "—",
                rows_str,
                last,
                age,
                warns_str,
            )
        )

    widths = [max(len(r[i]) for r in (*out_rows, headers)) for i in range(len(headers))]

    def fmt(r: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r))

    print(fmt(headers))
    print(fmt(tuple("-" * w for w in widths)))
    for r in out_rows:
        print(fmt(r))

    # Surface any failed runs loudly since the table format can lose them in the noise.
    failed = [r for r in latest_by_name.values() if r.status == "failed"]
    if failed:
        print()
        for r in failed:
            err = (r.error or "").splitlines()[0] if r.error else ""
            logger.warning(
                "Latest run FAILED for %s (version %s): %s",
                r.logical_name, r.source_version, err[:200],
            )

    # Surface validation warnings on successful runs so they don't hide in the JSONB.
    import json as _json
    warning_lines: list[tuple[str, str]] = []
    for r in latest_by_name.values():
        if r.status != "success":
            continue
        validation = r.validation
        if isinstance(validation, str):
            try:
                validation = _json.loads(validation)
            except (ValueError, TypeError):
                continue
        if not isinstance(validation, dict):
            continue
        for c in validation.get("checks") or []:
            if isinstance(c, dict) and c.get("status") == "warn":
                warning_lines.append((r.logical_name, f"[{c.get('name')}] {c.get('detail')}"))
    if warning_lines:
        print()
        for logical_name, line in warning_lines:
            logger.warning("Validation warn for %s: %s", logical_name, line)

    if failed:
        return 1
    return 0


def cmd_build_canonical() -> int:
    """Rebuild the Phase 2 canonical tables from current staging."""
    from givingtuesday_datamart.canonical.build import build_canonical

    try:
        result = build_canonical()
    except Exception as err:
        logger.error("Canonical build failed: %s", err)
        return 1

    duration = (result.finished_at - result.started_at).total_seconds()
    print()
    print(f"Canonical build {result.build_id} success ({duration:.1f}s):")
    print(f"  schedule_o_part_iii: {result.schedule_o_part_iii_rows:,} rows")
    print(f"  nonprofit_canonical: {result.nonprofit_canonical_rows:,} rows")
    print(f"  nonprofit_text:      {result.nonprofit_text_rows:,} rows")
    print(f"  funder_canonical:    {result.funder_canonical_rows:,} rows")
    print(f"  person_canonical:    {result.person_canonical_rows:,} rows")
    print(f"  org_person_role:     {result.org_person_role_rows:,} rows")
    print()
    print("Source lineage (logical_name → run_id@source_version):")
    for name in sorted(result.source_runs):
        print(f"  {name}: {result.source_runs[name] or '—'}")
    return 0


def cmd_refresh(source_names: list[str] | None, *, force: bool) -> int:
    # Import here so `status` does not pay for the SQLAlchemy / vdl-tools init cost.
    from givingtuesday_datamart.ingestion import ingest_source

    try:
        specs = _select_specs(source_names)
    except KeyError as err:
        logger.error(str(err))
        return 2

    try:
        listing = list_bucket(S3_BUCKET, S3_PREFIX)
    except (BotoCoreError, ClientError) as err:
        logger.error("Failed to list s3://%s/%s: %s", S3_BUCKET, S3_PREFIX, err)
        return 2

    exit_code = 0
    summary_rows: list[tuple[str, str, str, str]] = []
    for spec in specs:
        resolved = resolve_latest(spec, listing=listing)
        if resolved is None:
            logger.error(
                "No file matching regex=%r in s3://%s/%s for %s — skipping.",
                spec.filename_regex,
                spec.s3_bucket,
                spec.s3_prefix,
                spec.logical_name,
            )
            summary_rows.append((spec.logical_name, "—", "missing_source", ""))
            exit_code = 1
            continue

        result = ingest_source(spec, resolved, force=force)
        summary_rows.append(
            (
                result.logical_name,
                result.source_version,
                result.status,
                str(result.row_count) if result.row_count is not None else "",
            )
        )
        if result.status == "failed":
            exit_code = 1

    headers = ("logical_name", "version_date", "status", "row_count")
    widths = [max(len(row[i]) for row in (*summary_rows, headers)) for i in range(len(headers))]

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    print()
    print(fmt(headers))
    print(fmt(tuple("-" * w for w in widths)))
    for row in summary_rows:
        print(fmt(row))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m givingtuesday_datamart.sources",
        description="Inspect and refresh registered Datamart sources.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "status",
        help="Show latest available version in S3 for each registered source.",
    )

    subparsers.add_parser(
        "loaded",
        help="Show the latest ingest run per source from datamart_meta.ingest_runs.",
    )

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Ingest the newest file for each source (or a subset) into Postgres.",
    )
    refresh_parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Logical name of a single source to refresh. May be passed multiple times. "
             "If omitted, refreshes every source in the registry.",
    )
    refresh_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest even if a successful run already exists for this (source, version).",
    )

    subparsers.add_parser(
        "build-canonical",
        help="Rebuild Phase 2 canonical tables (nonprofit_canonical + nonprofit_text) from staging.",
    )

    args = parser.parse_args(argv)
    if args.command == "status":
        return cmd_status()
    if args.command == "loaded":
        return cmd_loaded()
    if args.command == "refresh":
        return cmd_refresh(args.sources, force=args.force)
    if args.command == "build-canonical":
        return cmd_build_canonical()
    return 2


if __name__ == "__main__":
    sys.exit(main())
