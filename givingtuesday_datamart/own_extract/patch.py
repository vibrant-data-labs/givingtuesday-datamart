"""Put our extract into production's staging tables, ours first for every batch we read.

Run after ``sources refresh``, which recreates each staging table from GT's
release. For each of GT's ten staging tables, GT's rows under a url our
table holds are deleted and ours go in their place, in group order, stamped:

* ``_source_version`` ``irs:<batch>``;
* ``_source_url`` the IRS archive;
* ``_ingest_run_id`` the batch's load run in ``datamart_meta.ingest_runs``,
  which holds the archive's SHA-256 and the concordance's;
* ``_ingested_at`` the patch's time.

Url by url within a table: where our table has no row for a return GT has
rows for, GT's rows stay. On 2024-2026 that is 27,321 990-PF grant rows
under 24,925 returns, all but five of them the empty row GT writes for a
return that lists no grant, and 11 990-EZ program rows; with them the
``_current`` version choice reads such a filer-year as it does today. No
return of ours is in another form's table on GT's side, so none is counted
twice.

All ten tables change in one transaction, recorded as one run per table
(logical name ``irs_extract_patch``); GT's own runs are left alone, so a
refresh still knows GT's release. The patch starts from scratch each time:
its earlier rows go first. Undo with ``sources refresh --force`` of the
tables, which loads GT's release again.

Our rows go in in group order, but a row that fits the space left at the
end of an earlier page lands there, so their physical order (``ctid``) is
not the group order on every filing (22 of the 1,006 sampled filings with
groups, ``prove-patch``). The ``_current`` builders read that order only to
choose among copies of one row (``pair_collapse``, Schedule I's
``amend_distinct``) or among rows of one recipient and one amount
(``field_fill``, which took one of our 4.6 million 990-PF rows), so it
decides at most which status or purpose text such a kept row shows.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from givingtuesday_datamart.ingestion import LINEAGE_COLUMN_NAMES, read_stamp, write_stamp
from givingtuesday_datamart.own_extract import batches, extract
from givingtuesday_datamart.own_extract.load import LOGICAL_NAME, SCHEMA

PATCH = "irs_extract_patch"
STAMP = "irs:"
# A patch run's source (``ingest_runs.source_url``, NOT NULL): our copies of
# the IRS archives its rows come from; each row names its own archive.
ARCHIVES = f"s3://{batches.ARCHIVE_BUCKET}/{batches.ARCHIVE_PREFIX}/"


class PatchRefused(RuntimeError):
    """The extract or a staging table is not in a state the patch may copy."""


def batch_runs(session, concordance_sha: str, source: str = SCHEMA) -> list[dict]:
    """Each loaded batch's latest successful load: its run id, archive url and concordance.

    Refuses when a batch was loaded with another concordance than the file's,
    or when the extract holds a batch with no successful load: the stamp on
    every patched row must name the mapping that made it.
    """
    runs = session.execute(text(
        f"""SELECT DISTINCT ON (split_part(source_version, '@', 1))
                   split_part(source_version, '@', 1) AS batch, run_id::text AS run_id, source_url,
                   source_manifest->>'concordance_sha256' AS concordance
            FROM datamart_meta.ingest_runs
            WHERE logical_name = '{LOGICAL_NAME}' AND status = 'success'
            ORDER BY split_part(source_version, '@', 1), finished_at DESC""")).mappings().all()
    loaded = {b for (b,) in session.execute(text(f"SELECT DISTINCT _batch_id FROM {source}.returns")).all()}
    return runs_for(loaded, [dict(r) for r in runs], concordance_sha)


def runs_for(loaded: set[str], runs: list[dict], concordance_sha: str) -> list[dict]:
    """The latest run of each loaded batch, refused unless every one used ``concordance_sha``."""
    by_batch = {r["batch"]: r for r in runs if r["batch"] in loaded}
    missing = sorted(loaded - set(by_batch))
    other = sorted(b for b, r in by_batch.items() if r["concordance"] != concordance_sha)
    if missing or other:
        raise PatchRefused(f"batches with no load run: {missing}; loaded with another concordance than "
                           f"{concordance_sha[:12]}: {other}")
    return sorted(by_batch.values(), key=lambda r: r["batch"])


def staging_columns(session, table: str, source: str = SCHEMA, target: str = "public") -> list[str]:
    """The staging table's columns in its own order, lineage last; refuses a column we lack."""
    def cols(schema):
        return [c for (c,) in session.execute(text(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position"""),
            {"s": schema, "t": table}).all()]
    staged, ours = cols(target), set(cols(source))
    if not staged:
        raise PatchRefused(f"{target}.{table} does not exist")
    lacking = [c for c in staged if c not in ours and c not in LINEAGE_COLUMN_NAMES]
    if lacking:
        raise PatchRefused(f"{target}.{table} has columns {source}.{table} lacks: {lacking}")
    return [c for c in staged if c not in LINEAGE_COLUMN_NAMES]


def insert_sql(table: str, columns: list[str], source: str = SCHEMA, target: str = "public") -> str:
    """Our rows into the staging table, in group order, each stamped with its batch's load."""
    cols = ", ".join(columns)
    picked = ", ".join(f"x.{c}" for c in columns)
    return (
        f"INSERT INTO {target}.{table} ({cols}, {', '.join(LINEAGE_COLUMN_NAMES)})\n"
        f"SELECT {picked}, '{STAMP}' || x._batch_id, r.source_url, :ingested_at, r.run_id\n"
        f"FROM {source}.{table} x JOIN pg_temp.patch_runs r ON r.batch = x._batch_id\n"
        f"ORDER BY x.object_id, x.group_ordinal NULLS FIRST")


def delete_sql(table: str, source: str = SCHEMA, target: str = "public") -> tuple[str, str]:
    """The patch's earlier rows, then GT's rows under a url our table holds.

    Two statements, not one with an ``OR``: an ``IN (subquery)`` under an
    ``OR`` cannot be a semi-join, and the planner hashes its url list only
    while it fits the hash budget (``basic_fields``'s 967,617 urls already
    take 116 of 128 MB at 64 MB ``work_mem``); past it every GT row would
    scan the list. ``USING`` is a hash join that spills to disk.
    """
    return (f"DELETE FROM {target}.{table} WHERE _source_version LIKE '{STAMP}%'",
            f"DELETE FROM {target}.{table} g USING (SELECT DISTINCT url FROM {source}.{table}) u "
            f"WHERE g.url = u.url")


def apply(session, *, source: str = SCHEMA, target: str = "public",
          tables: tuple[str, ...] = extract.TABLES, record: bool = True) -> dict:
    """Patch every table in one transaction (the caller commits); return the counts per table.

    ``target`` and ``source`` let a scratch copy stand in for production;
    ``record`` writes the runs to ``datamart_meta.ingest_runs``, and each
    run on its table's stamp (``stamp_patched``).
    """
    concordance = extract.load()
    runs = batch_runs(session, concordance.sha256, source)
    session.execute(text("DROP TABLE IF EXISTS pg_temp.patch_runs"))
    session.execute(text("CREATE TEMP TABLE patch_runs (batch text PRIMARY KEY, run_id text, source_url text)"))
    session.execute(text("INSERT INTO patch_runs VALUES (:batch, :run_id, :source_url)"),
                    [{k: r[k] for k in ("batch", "run_id", "source_url")} for r in runs])
    started = datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    for table in tables:
        columns = staging_columns(session, table, source, target)
        release = session.execute(text(
            f"SELECT _source_version FROM {target}.{table} WHERE _source_version NOT LIKE '{STAMP}%' LIMIT 1")
        ).scalar()
        earlier, gt_rows = (session.execute(text(sql)).rowcount for sql in delete_sql(table, source, target))
        inserted = session.execute(text(insert_sql(table, columns, source, target)),
                                   {"ingested_at": started.isoformat()}).rowcount
        out[table] = {"gt_release": release, "earlier_patch_rows": earlier, "gt_rows": gt_rows,
                      "inserted": inserted}
    if record:
        manifest = {"concordance_sha256": concordance.sha256, "batches": [r["batch"] for r in runs]}
        finished = datetime.now(timezone.utc)
        for table, counts in out.items():
            patch_run = str(uuid.uuid4())
            session.execute(text(
                """INSERT INTO datamart_meta.ingest_runs
                       (run_id, logical_name, staging_table, source_version, source_url,
                        started_at, finished_at, status, row_count, source_manifest)
                   VALUES (:id, :name, :table, :version, :url, :started, :finished, 'success',
                           :rows, CAST(:manifest AS JSONB))"""),
                {"id": patch_run, "name": PATCH, "table": f"{target}.{table}",
                 "version": f"{SCHEMA}@{concordance.sha256[:12]}", "url": ARCHIVES, "started": started,
                 "finished": finished, "rows": counts["inserted"],
                 "manifest": json.dumps({**manifest, **counts})})
            stamp_patched(session, f"{target}.{table}", patch_run)
    return out


def stamp_patched(session, table: str, patch_run_id: str) -> bool:
    """Record the patch run on ``table``'s stamp, in the patch's
    transaction; False when the table carries none (loaded before the
    stamps, or its last ingest did not finish), where ``current_grants
    --adopt`` reads the patch run from ``ingest_runs`` instead."""
    stamp = read_stamp(session, table)
    if stamp is None:
        return False
    write_stamp(session, table, {**stamp, "patch_run_id": patch_run_id})
    return True
