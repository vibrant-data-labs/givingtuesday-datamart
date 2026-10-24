"""GT's staging rows for the returns of a batch, read by EIN.

The staging tables have one index, on ``filerein``, and no index on
``url``; a ``url = ANY(...)`` filter over thousands of ids is a scan of
every table (basic_fields is ~3 GB) per call. So rows are fetched for the
batch's EINs, which the index answers, and narrowed to the batch's object
ids here. ``ctid`` comes along: its order is the extract's own order,
the only order GT's rows have (``current_grants`` uses it the same way).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart.ingestion import LINEAGE_COLUMN_NAMES

_CHUNK = 5_000


def rows_for(session, table: str, eins: set[str], object_ids: set[str]) -> pd.DataFrame:
    """Every row of ``table`` whose url is one of ``object_ids``, in extract order.

    Adds ``object_id`` (from the url) and ``ctid_order``; drops lineage
    columns. Doubled rows are kept: how many rows GT has per return is
    part of what is measured.
    """
    frames = []
    ein_list = sorted(eins)
    for start in range(0, len(ein_list), _CHUNK):
        chunk = ein_list[start:start + _CHUNK]
        frame = pd.read_sql(
            text(f"SELECT ctid::text AS _ctid, * FROM public.{table} WHERE filerein = ANY(:eins)"),
            session.connection(), params={"eins": chunk})   # text columns; NULL stays None
        frames.append(frame)
    rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if rows.empty:
        return rows
    rows["object_id"] = rows["url"].str.extract(r"(\d{18})_public", expand=False)
    rows = rows[rows["object_id"].isin(object_ids)].copy()
    block_offset = rows["_ctid"].str.strip("()").str.split(",", expand=True).astype(int)
    rows["ctid_order"] = block_offset[0] * 1_000 + block_offset[1]
    rows = rows.drop(columns=["_ctid", *[c for c in LINEAGE_COLUMN_NAMES if c in rows.columns]])
    return rows.sort_values("ctid_order", kind="stable").reset_index(drop=True)
