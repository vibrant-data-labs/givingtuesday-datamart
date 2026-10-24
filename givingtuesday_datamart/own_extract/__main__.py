"""Command line for our own extract of the IRS e-file XML.

    python -m givingtuesday_datamart.own_extract batches 2026
    python -m givingtuesday_datamart.own_extract load 2026 [--batch 2026_TEOS_XML_09A ...] [--keep-zips]
    python -m givingtuesday_datamart.own_extract parity [--batch ...] [--report PATH]
    python -m givingtuesday_datamart.own_extract discover ZIP [--out PATH]

``batches`` lists the archives the IRS serves for a year (it asks the
server; the index does not name 05B). ``load`` fetches each archive into
``--cache`` (stamped with its Last-Modified, size and SHA-256), parses it,
replaces its rows in ``irs_extract``, copies the archive to our bucket
(``batches.archive``) and, unless ``--keep-zips``, deletes the local copy:
a year of archives is 4-5 GB. ``parity`` measures
the loaded batches against GT's tables and writes a markdown report.
``discover`` proposes the concordance from an archive GT has released
(GT's rows for its returns, read by EIN, about 10 minutes for a 12,000-return
archive); review the proposal against ``data/own_extract/concordance.csv``.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.own_extract import batches, discover, extract, load, report

DEFAULT_CACHE = Path.home() / ".cache" / "irs_batches"


def _batches(args) -> int:
    for batch in batches.discover(args.year):
        print(f"{batch.batch_id}\t{batch.content_length / 1e6:8.1f} MB\t{batch.last_modified}")
    return 0


def _load(args) -> int:
    concordance = extract.load()
    found = batches.discover(args.year)
    if args.batch:
        found = [b for b in found if b.batch_id in set(args.batch)]
    if args.reverse:
        found.reverse()
    for batch in found:
        if args.returns_only:
            path = batches.fetch(batch, args.cache)
            with get_session(config=datamart_config()) as session:
                n = load.load_returns(session, path)
            print(f"{batch.batch_id}: {n} returns written to {load.SCHEMA}.{load.RETURNS}", flush=True)
            if not args.keep_zips:
                path.unlink()
            continue
        if not args.force:
            with get_session(config=datamart_config()) as session:
                if load.already_loaded(session, batch, concordance):
                    print(f"{batch.batch_id}: loaded already (same Last-Modified, same concordance)",
                          flush=True)
                    continue
        started = time.time()
        path = batches.fetch(batch, args.cache)
        members, deflate64 = batches.member_count(path)
        with get_session(config=datamart_config()) as session:
            manifest = load.load_batch(session, path, concordance)
        rows = manifest["rows"]
        if manifest.get("skipped"):
            print(f"{batch.batch_id}: {manifest['skipped']}", flush=True)
            if not args.keep_zips:
                path.unlink()
            continue
        kept = batches.archive(path) if args.archive else "not archived"
        print(f"{batch.batch_id}: {members} returns ({deflate64} Deflate64), "
              + ", ".join(f"{t} {rows.get(t, 0)}" for t in concordance)
              + f"  [{time.time() - started:.0f} s] -> {kept}", flush=True)
        if not args.keep_zips:
            path.unlink()
    return 0


def _config(host: str | None) -> dict:
    """The datamart config, pointed at another host (a restored copy) when given."""
    config = datamart_config()
    return {**config, "postgres": {**config["postgres"], "host": host}} if host else config


def _parity(args) -> int:
    # The measures read every GT staging table in full: run them on a restored
    # copy, or on the shared instance only in a quiet window agreed beforehand
    # (2026-10-23: they filled its disk with temp files and pushed it into an
    # OOM restart; the settings and per-section commits since bound both).
    with get_session(config=_config(args.host)) as session:
        if args.schema_checks:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report.schema_checks(session))
        else:
            report.run(session, args.report, args.batch or None, args.table or None)   # writes as it goes
    print(f"wrote {args.report}")
    return 0


def _discover(args) -> int:
    lines = []
    with get_session(config=datamart_config()) as session:
        lines = discover.propose(session, args.zip)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=discover.CONCORDANCE_FIELDS)
        writer.writeheader()
        writer.writerows(lines)
    print(f"wrote {len(lines)} lines to {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="own_extract", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("batches", help="list the IRS archives for a year")
    p.add_argument("year")
    p.set_defaults(func=_batches)

    p = sub.add_parser("load", help="fetch, parse and load a year's archives")
    p.add_argument("year")
    p.add_argument("--batch", action="append", help="only this batch id (repeatable)")
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    p.add_argument("--keep-zips", action="store_true")
    p.add_argument("--returns-only", action="store_true",
                   help="write only irs_extract.returns (form, schema version) for these batches")
    p.add_argument("--reverse", action="store_true",
                   help="newest batch first (a second loader can work from the other end)")
    p.add_argument("--force", action="store_true",
                   help="reload a batch already loaded at the same Last-Modified and concordance")
    p.add_argument("--no-archive", dest="archive", action="store_false",
                   help="do not copy the archive to s3://givingtuesday-datamart/irs/xml_batches/")
    p.set_defaults(func=_load)

    p = sub.add_parser("parity", help="measure loaded batches against GT's tables")
    p.add_argument("--batch", action="append", help="only this batch id (repeatable)")
    p.add_argument("--table", action="append", choices=sorted(extract.TABLES),
                   help="only this table and the measures that read it (repeatable)")
    p.add_argument("--report", type=Path,
                   default=extract.REPO_ROOT / "data" / "own_extract" / "parity_report.md")
    p.add_argument("--host", default=None,
                   help="database host to measure on, e.g. a restored copy of the shared instance")
    p.add_argument("--schema-checks", action="store_true",
                   help="only the checks for the newest schema versions (minutes, not an hour)")
    p.set_defaults(func=_parity)

    p = sub.add_parser("discover", help="propose the concordance from an archive GT has released")
    p.add_argument("zip", type=Path)
    p.add_argument("--out", type=Path,
                   default=extract.REPO_ROOT / "data" / "own_extract" / "concordance_proposed.csv")
    p.set_defaults(func=_discover)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
