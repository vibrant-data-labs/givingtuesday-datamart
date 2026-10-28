"""Command line for our own extract of the IRS e-file XML.

    python -m givingtuesday_datamart.own_extract batches 2026
    python -m givingtuesday_datamart.own_extract load 2026 [--batch 2026_TEOS_XML_09A ...] [--keep-zips]
    python -m givingtuesday_datamart.own_extract parity [--batch ...] [--report PATH] [--host HOST]
    python -m givingtuesday_datamart.own_extract gt-release 2026_10_25 --host HOST [--jobs N]
    python -m givingtuesday_datamart.own_extract checks [--batch ...] [--report PATH] [--host HOST]
    python -m givingtuesday_datamart.own_extract audit [--batch ...] [--jobs N] [--host HOST]
    python -m givingtuesday_datamart.own_extract reload [--batch ...] [--jobs N] [--host HOST]
    python -m givingtuesday_datamart.own_extract discover ZIP [--out PATH]

``batches`` lists the archives the IRS serves for a year (it asks the
server; the index does not name 05B). ``load`` fetches each archive into
``--cache`` (stamped with its Last-Modified, size and SHA-256), parses it,
replaces its rows in ``irs_extract``, copies the archive to our bucket
(``batches.archive``) and, unless ``--keep-zips``, deletes the local copy:
a year of archives is 4-5 GB. ``parity`` measures
the loaded batches against GT's tables and writes a markdown report.
``gt-release`` loads a GT release into a scratch host holding a copy of our
extract, under our batches' returns only, for ``parity --host`` there
(``release``). ``checks`` measures the loaded batches against their filings'
own totals and lists the elements no concordance path reads, without GT
(``checks``, ``audit``); ``load`` counts those elements as it parses and
prints each batch's checks, ``audit`` counts them for batches loaded before.
``reload`` loads the loaded batches again under the current concordance, from
our copy of each archive, checked against the SHA-256 its first load stamped.
``discover`` proposes the concordance from an archive GT has released
(GT's rows for its returns, read by EIN, about 10 minutes for a 12,000-return
archive); review the proposal against ``data/own_extract/concordance.csv``.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import sys
import time
from pathlib import Path

from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.own_extract import (batches, checks, discover, extract, load, parity, patch,
                                                proof, release, report)

DEFAULT_CACHE = Path.home() / ".cache" / "irs_batches"


def _batches(args) -> int:
    for batch in batches.discover(args.year):
        print(f"{batch.batch_id}\t{batch.content_length / 1e6:8.1f} MB\t{batch.last_modified}")
    return 0


def _load(args) -> int:
    concordance = extract.load()
    found = batches.discover(args.year)
    if args.batch:
        unknown = sorted(set(args.batch) - {b.batch_id for b in found})
        if unknown:
            print(f"no such archive for {args.year}: {', '.join(unknown)}", file=sys.stderr)
            return 2
        found = [b for b in found if b.batch_id in set(args.batch)]
    if args.reverse:
        found.reverse()
    for batch in found:
        claim = batches.claim(batch, args.cache)
        if claim is None:
            print(f"{batch.batch_id}: another loader sharing this cache has it", flush=True)
            continue
        with claim:
            _load_one(args, batch, concordance)
    return 0


def _load_one(args, batch, concordance) -> None:
    if args.returns_only:
        path = batches.fetch(batch, args.cache)
        with get_session(config=datamart_config()) as session:
            n = load.load_returns(session, path)
        print(f"{batch.batch_id}: {n} returns written to {load.SCHEMA}.{load.RETURNS}", flush=True)
        if not args.keep_zips:
            path.unlink()
        return
    if not args.force:
        with get_session(config=datamart_config()) as session:
            if load.already_loaded(session, batch, concordance):
                print(f"{batch.batch_id}: loaded already (same Last-Modified, same concordance)",
                      flush=True)
                return
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
        return
    kept = batches.archive(path) if args.archive else "not archived"
    print(f"{batch.batch_id}: {members} returns ({deflate64} Deflate64), "
          + ", ".join(f"{t} {rows.get(t, 0)}" for t in concordance)
          + f"  [{time.time() - started:.0f} s] -> {kept}", flush=True)
    with get_session(config=datamart_config()) as session:
        print(f"{batch.batch_id} checks: {checks.batch_line(session, batch.batch_id)}", flush=True)
    if not args.keep_zips:
        path.unlink()


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


def _gt_release(args) -> int:
    # GT's release into a scratch host's public.<table>, for `parity --host`:
    # production's staging holds our rows since the patch.
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    for summary in release.load(_config(args.host), args.version, args.table or None, args.jobs):
        print(f"{summary['table']}: {summary['kept']:,} of GT's {summary['gt_rows']:,} rows kept "
              f"({summary['file']}, {summary['bytes'] / 1e9:.2f} GB, sha256 {summary['sha256'][:12]})",
              flush=True)
    return 0


def _checks(args) -> int:
    # Reads our tables only, a batch at a time, at parity's session settings.
    with get_session(config=_config(args.host)) as session:
        checks.run(session, args.report, args.batch or None)
    print(f"wrote {args.report}")
    return 0


def _archived(stamp: dict, batch_id: str, cache: Path):
    """``(lock, path, downloaded)``: our copy of a loaded batch's archive, claimed
    in ``cache`` the way ``load`` claims one (``batches.claim``; close the lock
    when done), or None while another loader holds it. Raises for a batch whose
    load stamped no SHA-256."""
    if not stamp.get("sha256"):
        raise ValueError(f"{batch_id}: its load stamped no archive SHA-256 (fetched by hand?); "
                         f"load it with `load` instead")
    lock = batches.claim(batches.Batch(stamp.get("year") or batch_id[:4], batch_id, stamp.get("url", ""),
                                       stamp.get("last_modified", ""), stamp.get("content_length", 0)),
                         cache)
    if lock is None:
        return None
    downloaded = not (cache / f"{batch_id}.zip").exists()
    try:
        return lock, batches.fetch_archived(batch_id, stamp["sha256"], cache), downloaded
    except Exception:
        lock.close()
        raise


def _audit_one(config: dict, batch_id: str, stamp: dict, cache: Path, keep: bool) -> str:
    held = _archived(stamp, batch_id, cache)
    if held is None:
        return f"{batch_id}: another loader sharing this cache has it"
    lock, path, downloaded = held
    with lock:
        with get_session(config=config) as session:
            n = load.audit_batch(session, path, batch_id, extract.load(), stamp["sha256"])
        if downloaded and not keep:
            path.unlink()
    return f"{batch_id}: {n:,} unread paths"


def _reload_one(config: dict, batch_id: str, stamp: dict, cache: Path, keep: bool) -> str:
    concordance = extract.load()
    with get_session(config=config) as session:
        if load._loaded(session, f"{batch_id}@{stamp.get('last_modified', '')}", concordance):
            return f"{batch_id}: loaded already under this concordance"
    held = _archived(stamp, batch_id, cache)
    if held is None:
        return f"{batch_id}: another loader sharing this cache has it"
    lock, path, downloaded = held
    with lock:
        path.with_name(f"{batch_id}.json").write_text(json.dumps(stamp, indent=2))   # the load records it
        with get_session(config=config) as session:
            manifest = load.load_batch(session, path, concordance)
        if downloaded and not keep:
            path.unlink()
    with get_session(config=config) as session:
        line = checks.batch_line(session, batch_id)
    rows = manifest["rows"]
    loaded = manifest.get("skipped") or ", ".join(f"{t} {rows.get(t, 0)}" for t in concordance)
    return f"{batch_id}: {loaded}; checks: {line}"


def _each_batch(args, job) -> int:
    """``job(config, batch_id, stamp, cache, keep)`` for each loaded batch, ``--jobs``
    at a time (in this process for one); a batch that fails is reported and the
    others go on. 1 if any failed."""
    config = _config(args.host)
    with get_session(config=config) as session:
        stamps = checks.loaded(session, args.batch or None)
    work = [(b, (config, b, stamp, args.cache, args.keep_zips))
            for b, stamp in zip(stamps["batch"], stamps["stamp"])]
    failed = []

    def report(batch_id: str, outcome) -> None:
        try:
            print(outcome(), flush=True)
        except Exception as err:  # noqa: BLE001 - one batch's failure is reported, the rest go on
            failed.append(batch_id)
            print(f"{batch_id}: FAILED: {err}", flush=True)

    if args.jobs <= 1:
        for batch_id, params in work:
            report(batch_id, lambda params=params: job(*params))
    else:
        with cf.ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(job, *params): batch_id for batch_id, params in work}
            for done in cf.as_completed(futures):
                report(futures[done], done.result)
    if failed:
        print(f"{len(failed)} of {len(work)} batches failed: {', '.join(sorted(failed))}", flush=True)
    return 1 if failed else 0


def _audit(args) -> int:
    # Re-reads each loaded batch from our copy of its archive, checked against
    # the SHA-256 its load stamped; a load from now on counts them itself.
    return _each_batch(args, _audit_one)


def _reload(args) -> int:
    # A concordance change gives every batch a new stamp, and `patch` refuses
    # batches loaded under different ones: reload them all, from the bytes
    # first loaded rather than whatever the IRS serves under the name today.
    # Run `patch` only once every batch reports.
    return _each_batch(args, _reload_one)


def _discover(args) -> int:
    lines = []
    with get_session(config=datamart_config()) as session:
        lines = discover.propose(session, args.zip, per_form=args.per_form)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=discover.CONCORDANCE_FIELDS)
        writer.writeheader()
        writer.writerows(lines)
    print(f"wrote {len(lines)} lines to {args.out}")
    return 0


def _patch(args) -> int:
    # After `sources refresh`: GT's rows under our urls out, ours in, in one
    # transaction; then the basic-fields relations the client reads, as a
    # refresh rebuilds them. The grants relations are the matcher run's.
    from givingtuesday_datamart.current_grants import build_basic_fields_current

    with get_session(config=datamart_config()) as session:
        parity.prepare(session)
        session.execute(text("SET statement_timeout = 0"))
        counts = patch.apply(session)
    for table, c in counts.items():
        print(f"{table}: GT's {c['gt_release']} less {c['gt_rows']:,} rows, earlier patch rows "
              f"{c['earlier_patch_rows']:,} out, ours {c['inserted']:,} in", flush=True)
    with get_session(config=datamart_config()) as session:
        for table, n in build_basic_fields_current(session.connection()).items():
            print(f"public.{table} rebuilt: {n:,} rows", flush=True)
    return 0


def _prove_patch(args) -> int:
    with get_session(config=datamart_config()) as session:
        parity.prepare(session)
        session.execute(text("SET statement_timeout = 0"))
        checks = proof.prove(session, args.modulus)
        if not args.keep:
            proof.drop(session)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("".join(f"## {name}\n\n{report._md(frame)}\n\n"
                                   for name, frame in checks.items()))
    print(f"wrote {args.report}")
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

    p = sub.add_parser("gt-release", help="a GT release beside our extract on a scratch host, for parity --host")
    p.add_argument("version", help="the release's date, as in its file names (2026_10_25)")
    p.add_argument("--host", required=True, help="the scratch instance; never the shared one")
    p.add_argument("--table", action="append", choices=sorted(extract.TABLES),
                   help="only this table (repeatable); the _current relations are rebuilt either way")
    p.add_argument("--jobs", type=int, default=1, help="tables loaded at once")
    p.set_defaults(func=_gt_release)

    p = sub.add_parser("checks", help="the loaded batches against their filings' own totals; unread elements")
    p.add_argument("--batch", action="append", help="only this batch id (repeatable)")
    p.add_argument("--report", type=Path,
                   default=extract.REPO_ROOT / "data" / "own_extract" / "checks_report.md")
    p.add_argument("--host", default=None, help="database host to read, e.g. a scratch copy")
    p.set_defaults(func=_checks)

    p = sub.add_parser("audit", help="count the unread elements of batches loaded before load counted them")
    p.add_argument("--batch", action="append", help="only this batch id (repeatable)")
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    p.add_argument("--keep-zips", action="store_true")
    p.add_argument("--jobs", type=int, default=1, help="batches read at once")
    p.add_argument("--host", default=None, help="database host to write to, e.g. a scratch copy")
    p.set_defaults(func=_audit)

    p = sub.add_parser("reload", help="load the loaded batches again under the current concordance, "
                                      "from our copies of their archives")
    p.add_argument("--batch", action="append", help="only this batch id (repeatable)")
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    p.add_argument("--keep-zips", action="store_true")
    p.add_argument("--jobs", type=int, default=1, help="batches loaded at once")
    p.add_argument("--host", default=None, help="database host to load into, e.g. a scratch copy")
    p.set_defaults(func=_reload)

    p = sub.add_parser("discover", help="propose the concordance from an archive GT has released")
    p.add_argument("zip", type=Path)
    p.add_argument("--out", type=Path,
                   default=extract.REPO_ROOT / "data" / "own_extract" / "concordance_proposed.csv")
    p.add_argument("--per-form", type=int, default=discover.PER_FORM,
                   help="returns of each form searched at most (memory grows with it)")
    p.set_defaults(func=_discover)

    p = sub.add_parser("patch", help="our rows into production's staging tables, ours first (after a refresh)")
    p.set_defaults(func=_patch)

    p = sub.add_parser("prove-patch", help="the patch on a scratch copy of a sample of filers, with the relations")
    p.add_argument("--modulus", type=int, default=997, help="filers whose EIN hashes to 0 modulo this")
    p.add_argument("--report", type=Path,
                   default=extract.REPO_ROOT / "data" / "own_extract" / "patch_proof.md")
    p.add_argument("--keep", action="store_true", help="leave the scratch schemas for inspection")
    p.set_defaults(func=_prove_patch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
