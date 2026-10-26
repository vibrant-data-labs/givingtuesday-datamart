"""Load a batch's GT-shaped rows into the ``irs_extract`` schema.

One table per GT staging table, same name, GT's columns in GT's order,
ahead of them our key (``object_id``, ``group_ordinal``) and the batch the
row came from. Loading a batch replaces that batch's rows in every table
in one transaction, so a rerun after a concordance change is safe and a
failed load leaves the previous rows in place.

Each load is a run in ``datamart_meta.ingest_runs`` (logical name
``irs_extract``): the batch url as ``source_url``, ``<batch>@<Last-Modified>``
as ``source_version``, and in ``source_manifest`` the archive's SHA-256 and
size, the concordance file's SHA-256, and the rows per table.
"""

from __future__ import annotations

import csv
import json
import tempfile
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from givingtuesday_datamart.ingestion import INGEST_RUNS_TABLE, ensure_meta_schema
from givingtuesday_datamart.own_extract import batches, extract, parse

SCHEMA = "irs_extract"
LOGICAL_NAME = "irs_extract"
# One row per return of every form in the archive (990-T included): which
# schema version it was filed under, so a measure can be read per version.
RETURNS = "returns"
RETURNS_COLUMNS = ("object_id", "_batch_id", "return_type", "return_version", "filesha256",
                   "filerein", "taxperend")


def columns_of(table: str, mappings: list[extract.Mapping]) -> list[str]:
    return [*extract.KEY_COLUMNS, *[m.column for m in mappings]]


def ensure_tables(session, concordance: dict[str, list[extract.Mapping]]) -> None:
    """Create the schema, tables and indexes that are missing; touch nothing else.

    Only a missing table gets its DDL: ``CREATE INDEX IF NOT EXISTS`` on a
    table that exists still takes a share lock on it, and two loaders that
    each held one deadlocked on 2026-10-23 when each went on to delete.
    """
    session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    if session.execute(text(f"SELECT to_regclass('{SCHEMA}.{RETURNS}')")).scalar() is None:
        session.execute(text(
            f"CREATE TABLE {SCHEMA}.{RETURNS} ({', '.join(c + ' text' for c in RETURNS_COLUMNS)})"))
        session.execute(text(f"CREATE INDEX ix_returns_object_id ON {SCHEMA}.{RETURNS} (object_id)"))
        session.execute(text(f"CREATE INDEX ix_returns_batch ON {SCHEMA}.{RETURNS} (_batch_id)"))
    for table, mappings in concordance.items():
        if session.execute(text(f"SELECT to_regclass('{SCHEMA}.{table}')")).scalar() is not None:
            continue
        cols = ", ".join(
            f"{c} {'integer' if c == 'group_ordinal' else 'text'}" for c in columns_of(table, mappings))
        session.execute(text(f"CREATE TABLE {SCHEMA}.{table} ({cols})"))
        for name, column in (("object_id", "object_id"), ("batch", "_batch_id"),
                             ("filerein", "filerein")):
            session.execute(text(
                f"CREATE INDEX ix_{table}_{name} ON {SCHEMA}.{table} ({column})"))


def extract_batch(path: Path, batch_id: str, concordance: dict[str, list[extract.Mapping]],
                  out_dir: Path) -> tuple[dict[str, Path], Counter]:
    """Parse every return of an archive into one CSV file per table in ``out_dir``.

    Files, not memory: the largest archive (2024 11A, 1.2 GB) holds about
    6 GB of XML.
    """
    tables = [*concordance, RETURNS]
    files = {t: out_dir / f"{t}.csv" for t in tables}
    counts: Counter = Counter()
    with ExitStack() as stack:
        writers = {t: csv.writer(stack.enter_context(files[t].open("w", newline=""))) for t in tables}
        for object_id, xml in batches.iter_members(path):
            try:
                filing = parse.parse(xml, object_id, extract.GROUP_PATHS)
            except ET.ParseError as err:
                raise ValueError(f"{batch_id}: return {object_id} does not parse: {err}") from err
            counts[f"returns_{filing.return_type or 'unknown'}"] += 1
            counts[f"version_{filing.version or 'unknown'}"] += 1     # a new IRS schema shows up here
            writers[RETURNS].writerow([_csv(v) for v in (
                object_id, batch_id, filing.return_type, filing.version, filing.sha256,
                filing.first("ReturnHeader/Filer/EIN"), filing.first("ReturnHeader/TaxPeriodEndDt"))])
            for table, mappings in concordance.items():
                order = columns_of(table, mappings)
                for row in extract.rows(filing, table, mappings, batch_id):
                    writers[table].writerow([_csv(row.get(c)) for c in order])
                    counts[table] += 1
    return files, counts


def _csv(value) -> str:
    # COPY ... CSV reads an unquoted empty field as NULL; csv.writer quotes
    # an empty string only if asked, so None and "" both arrive as NULL.
    return "" if value is None else str(value)


def _replace(session, files: dict[str, Path], batch_id: str, columns: dict[str, list[str]]) -> None:
    """Replace the batch's rows of each table (and of ``returns``) from its CSV file."""
    raw = session.connection().connection
    with raw.cursor() as cursor:
        for table, cols in {**columns, RETURNS: list(RETURNS_COLUMNS)}.items():
            cursor.execute(f"DELETE FROM {SCHEMA}.{table} WHERE _batch_id = %s", (batch_id,))
            with files[table].open(newline="") as handle:
                cursor.copy_expert(
                    f"COPY {SCHEMA}.{table} ({', '.join(cols)}) FROM STDIN WITH (FORMAT csv)", handle)


def load_returns(session, path: Path) -> int:
    """Write only the ``returns`` rows of an archive (a backfill for batches
    loaded before the table existed); the other tables are not touched."""
    stamp = batches.stamp_of(path)
    batch_id = stamp.get("batch_id") or path.stem
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        files, counts = extract_batch(path, batch_id, {}, Path(tmp))
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": f"{LOGICAL_NAME}:{batch_id}"})
        ensure_tables(session, {})
        _replace(session, files, batch_id, {})
    session.commit()
    return sum(v for k, v in counts.items() if k.startswith("returns_"))


def already_loaded(session, batch: batches.Batch, concordance) -> bool:
    """Whether this archive, at this Last-Modified, was loaded under this concordance."""
    return _loaded(session, f"{batch.batch_id}@{batch.last_modified}", concordance)


def _loaded(session, version: str, concordance) -> bool:
    if session.execute(text(f"SELECT to_regclass('{INGEST_RUNS_TABLE}')")).scalar() is None:
        return False
    return session.execute(text(
        f"""SELECT EXISTS (SELECT 1 FROM {INGEST_RUNS_TABLE}
                           WHERE logical_name = :name AND status = 'success'
                             AND source_version = :version
                             AND source_manifest->>'concordance_sha256' = :sha)"""),
        {"name": LOGICAL_NAME, "version": version,
         "sha": getattr(concordance, "sha256", "")}).scalar()


def load_batch(session, path: Path, concordance: dict[str, list[extract.Mapping]]) -> dict:
    """Extract one archive and replace its rows in ``irs_extract``."""
    stamp = batches.stamp_of(path)
    batch_id = stamp.get("batch_id") or path.stem
    started = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        files, counts = extract_batch(path, batch_id, concordance, Path(tmp))
        manifest = _copy(session, files, batch_id, concordance, stamp, counts, run_id, started)
    return manifest


def _copy(session, files, batch_id, concordance, stamp, counts, run_id, started) -> dict:
    manifest = {
        "batch": stamp,
        "concordance_sha256": getattr(concordance, "sha256", ""),
        "rows": dict(counts),
    }

    # ensure_meta_schema() ALTERs ingest_runs, which waits on any open
    # reader of it and queues every later reader behind it; only call it
    # when the table is missing.
    # Two loaders on one batch would both delete (nothing committed yet) and
    # both copy: the batch twice. The lock makes the second wait, and the
    # check under it lets the second see the first's run and stand down.
    version = f"{batch_id}@{stamp.get('last_modified', '')}"
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"{LOGICAL_NAME}:{batch_id}"})
    if _loaded(session, version, concordance):
        session.rollback()
        return {**manifest, "skipped": "loaded meanwhile by another loader"}
    if session.execute(text(f"SELECT to_regclass('{INGEST_RUNS_TABLE}')")).scalar() is None:
        ensure_meta_schema()
    ensure_tables(session, concordance)
    _replace(session, files, batch_id, {t: columns_of(t, m) for t, m in concordance.items()})
    session.execute(text(
        f"""INSERT INTO {INGEST_RUNS_TABLE} (run_id, logical_name, staging_table, source_version,
                source_url, started_at, finished_at, status, row_count, source_manifest)
            VALUES (:run_id, :name, :table, :version, :url, :started, :finished, 'success',
                    :rows, CAST(:manifest AS JSONB))"""),
        {"run_id": run_id, "name": LOGICAL_NAME, "table": f"{SCHEMA}.*",
         "version": version,
         "url": stamp.get("url") or batch_id, "started": started,
         "finished": datetime.now(timezone.utc),
         "rows": sum(counts[t] for t in concordance), "manifest": json.dumps(manifest)})
    session.commit()
    return manifest
