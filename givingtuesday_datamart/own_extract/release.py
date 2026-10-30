"""A GT release beside our extract on a scratch host, for ``parity``.

Since the patch (2026-10-26), production's staging tables hold our rows for
every batch we read, so ``parity`` on the shared instance compares ours with
ours. A new GT release is measured on a scratch Postgres instead, which holds
two things:

* our extract, copied from the shared instance: ``irs_extract`` and
  ``datamart_meta.ingest_runs`` (``pg_dump``, then ``pg_restore`` there;
  ``docs/own-extract.md`` gives the commands);
* GT's release, loaded here by :func:`load`: each table's CSV through
  production's own ingest path (``stream_csv_url_to_table``: the column
  names, text and lineage stamps a ``sources refresh`` writes), kept only
  under the returns of our loaded batches and in the file's order, then the
  ``_current`` relations built over those rows by production's DDL.

``parity --host`` then reads GT's side as it always does: ``public.<table>``
and the ``_current`` relations. Keeping only our batches' returns is what
``parity`` reads anyway (GT's rows under our urls), and it makes the copy a
fraction of GT's tables. The one thing it changes is the version a
``_current`` relation keeps for a filer-year whose other versions sit in a
batch we do not read: 19 of the 206,340 990-PF returns on 2026-10-24.

Never against the shared instance: the load replaces ``public.<table>``.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import socket
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart.current_grants import build_current_relations
from givingtuesday_datamart.ingestion import _apply_post_ingest_indexes, _lineage_values, datamart_config
from givingtuesday_datamart.irs_source import GT_DATALAKE
from givingtuesday_datamart.own_extract import extract
from givingtuesday_datamart.own_extract.load import SCHEMA
from givingtuesday_datamart.sources.registry import REGISTRY
from givingtuesday_datamart.sources.resolver import BucketListing, list_bucket
from givingtuesday_datamart.sources.spec import SourceSpec
from givingtuesday_datamart.write_data_to_sql import stream_csv_url_to_table

logger = logging.getLogger(__name__)

RAW = "gt_release_raw"          # the whole CSV lands here first, dropped once filtered
URLS = f"{RAW}.urls"            # every return of our loaded batches, as GT's url


def specs() -> dict[str, SourceSpec]:
    """Production's source spec for each table ``parity`` measures."""
    by_table = {s.staging_table_name.split(".")[-1]: s for s in REGISTRY}
    return {table: by_table[table] for table in extract.TABLES}


def files(version: str, listing: BucketListing | None = None) -> dict[str, str]:
    """The url of each table's CSV in GT's release ``version`` (``2026_10_25``)."""
    found = specs()
    if listing is None:
        any_spec = next(iter(found.values()))
        listing = list_bucket(any_spec.s3_bucket, any_spec.s3_prefix)
    out = {}
    for table, spec in found.items():
        names = [o.key[len(spec.s3_prefix):] for o in listing.objects if o.key.startswith(spec.s3_prefix)]
        hits = [n for n in names if (m := spec.compiled_regex().match(n)) and m.group(1) == version]
        if len(hits) != 1:
            raise LookupError(f"{table}: {len(hits)} files in release {version}")
        out[table] = spec.url_for(hits[0])
    return out


def _addresses(host: str) -> set[str]:
    try:
        return {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return set()


def refuse_shared(config: dict) -> None:
    """Stop unless ``config`` points somewhere other than the shared instance:
    by name in any case, and by address, so its IP or another name for it fails too."""
    host = config["postgres"]["host"].strip().lower()
    shared = datamart_config()["postgres"]["host"].strip().lower()
    if host == shared or _addresses(host) & _addresses(shared):
        raise SystemExit("gt-release replaces public.<table>: give --host, a scratch instance, "
                         "never the shared one")


def prepare_urls(config: dict) -> int:
    """``URLS``: GT's url for every return in ``irs_extract.returns``, any form."""
    with get_session(config=config) as session:
        session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {RAW}"))
        session.execute(text(f"DROP TABLE IF EXISTS {URLS}"))
        session.execute(text(
            f"""CREATE TABLE {URLS} AS SELECT DISTINCT :prefix || object_id || '_public.xml' AS url
                FROM {SCHEMA}.returns"""), {"prefix": GT_DATALAKE.split("{")[0]})
        session.execute(text(f"CREATE UNIQUE INDEX ON {URLS} (url)"))
        session.execute(text(f"ANALYZE {URLS}"))
        return session.execute(text(f"SELECT count(*) FROM {URLS}")).scalar_one()


def load_table(config: dict, version: str, table: str, url: str) -> dict:
    """GT's CSV for ``table`` into ``public.<table>``, only under ``URLS``.

    Streams the whole file into ``RAW`` as a refresh would into staging (a
    fresh run id, the release as ``_source_version``), then keeps the rows
    under our returns in ``ctid`` order, the file's: the order
    ``current_grants`` reads GT's rows in.
    """
    raw = f"{RAW}.{table}"
    streamed = stream_csv_url_to_table(
        url=url, table_name=raw, overwrite=True, db_config=config,
        extra_columns=_lineage_values(str(uuid.uuid4()), version, url, datetime.now(timezone.utc)))
    with get_session(config=config) as session:
        session.execute(text(f"DROP TABLE IF EXISTS public.{table} CASCADE"))
        session.execute(text(
            f"""CREATE TABLE public.{table} AS SELECT r.* FROM {raw} r
                WHERE r.url IN (SELECT url FROM {URLS}) ORDER BY r.ctid"""))
        session.execute(text(f"DROP TABLE {raw}"))
        kept = session.execute(text(f"SELECT count(*) FROM public.{table}")).scalar_one()
    _apply_post_ingest_indexes(specs()[table], config)
    logger.info("public.%s: %s of GT's %s rows kept", table, f"{kept:,}", f"{streamed.rows:,}")
    return {"table": table, "file": url.rsplit("/", 1)[-1], "gt_rows": streamed.rows,
            "kept": kept, "bytes": streamed.bytes, "sha256": streamed.sha256}


def load(config: dict, version: str, tables: list[str] | None = None, jobs: int = 1) -> list[dict]:
    """Load GT's release ``version`` for ``tables`` (default all) and build the
    ``_current`` relations; returns one summary per table, largest file first."""
    refuse_shared(config)
    urls = files(version)
    wanted = [t for t in extract.TABLES if tables is None or t in tables]
    logger.info("%s returns of our batches to keep GT's rows for", f"{prepare_urls(config):,}")
    # Schedule O first: GT's is the biggest file by far, and the rest load beside it.
    wanted.sort(key=lambda t: t != "schedule_o")
    with cf.ProcessPoolExecutor(max_workers=jobs) as pool:
        loaded = list(pool.map(load_table, [config] * len(wanted), [version] * len(wanted),
                               wanted, [urls[t] for t in wanted]))
    build_current_relations(config)
    return loaded
