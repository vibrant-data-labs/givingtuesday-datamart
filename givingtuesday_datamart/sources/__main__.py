"""
`python -m givingtuesday_datamart.sources status`

Prints each registered source alongside the newest matching file currently in
S3. Useful for answering "is our pinned ingestion stale?" without running a
full refresh.
"""

from __future__ import annotations

import argparse
import sys

from botocore.exceptions import BotoCoreError, ClientError
from vdl_tools.shared_tools.tools.logger import logger

from givingtuesday_datamart.sources.registry import REGISTRY, S3_BUCKET, S3_PREFIX
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m givingtuesday_datamart.sources",
        description="Inspect registered Datamart sources.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Show latest available version for each registered source.")
    args = parser.parse_args(argv)
    if args.command == "status":
        return cmd_status()
    return 2


if __name__ == "__main__":
    sys.exit(main())
