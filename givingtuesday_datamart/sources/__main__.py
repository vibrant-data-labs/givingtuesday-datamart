"""
``python -m givingtuesday_datamart.sources <command>``

Subcommands:

* ``status`` — print each registered source alongside the newest matching file
  currently in S3. Answers "is our pinned ingestion stale?" without running a
  full refresh.
* ``refresh`` — resolve the newest file for each registered source and ingest
  it into its staging table, stamping lineage columns on every row and
  recording the run in ``datamart_meta.ingest_runs``. Idempotent: re-running
  against the same source version is a no-op unless ``--force`` is passed.
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
        help="Show latest available version for each registered source.",
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

    args = parser.parse_args(argv)
    if args.command == "status":
        return cmd_status()
    if args.command == "refresh":
        return cmd_refresh(args.sources, force=args.force)
    return 2


if __name__ == "__main__":
    sys.exit(main())
