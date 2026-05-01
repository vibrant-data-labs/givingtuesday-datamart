"""Parquet I/O for Datamart pipeline checkpoints.

Pared-down replacement for ``vdl_tools.shared_tools.parquet_cache``. The
only live caller is ``grant_matching`` writing chunk checkpoints to S3;
the read path uses direct boto3 in ``grant_matching._fast_read_chunk`` to
avoid fsspec's per-call session overhead. To keep the dependency surface
small, this module is symmetric with that read path: writes go through
``boto3.put_object`` on an in-memory pyarrow buffer.

Same on-disk metadata contract as the original so existing checkpoint
files round-trip cleanly: the ``vdl_json_columns`` schema metadata key
lists JSON-encoded columns, decoded back to dict/list/tuple on read.

AWS credentials come from boto3's default chain (env vars, ``~/.aws/
credentials``, instance profile). The historical ``[aws]`` section of
``config.ini`` is no longer read here; operators relying on it should
export the keys or move them to ``~/.aws/credentials``.
"""

from __future__ import annotations

import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from givingtuesday_datamart._internal.logger import logger


_JSON_COLS_KEY = b"vdl_json_columns"
_LINEAGE_KEY = b"vdl_lineage"
_SAMPLE_SIZE = 100  # non-null values per column to check when scanning


def _is_s3(uri: str) -> bool:
    return uri.startswith("s3://")


def _split_s3_uri(uri: str) -> tuple[str, str]:
    """Return ``(bucket, key)`` for an ``s3://bucket/key`` URI."""
    without_scheme = uri[len("s3://"):]
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


def _scan_columns(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Classify object columns by sampling up to ``_SAMPLE_SIZE`` non-null values.

    Returns ``(json_cols, mixed_cols)``:
    - ``json_cols``: any sampled value is a dict/list/tuple → JSON-encode
    - ``mixed_cols``: >1 scalar type seen (no dict/list) → coerce to string
    """
    json_cols, mixed_cols = [], []
    for col in df.columns:
        if df[col].dtype != object:
            continue
        sample = df[col].dropna().head(_SAMPLE_SIZE)
        if sample.empty:
            continue
        types = {type(v) for v in sample}
        if types & {dict, list, tuple}:
            json_cols.append(col)
        elif len(types) > 1:
            mixed_cols.append(col)
    return json_cols, mixed_cols


def _is_null(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _encode_json(v):
    return None if _is_null(v) else json.dumps(v, default=str, ensure_ascii=False)


def _decode_json(v):
    return None if _is_null(v) else json.loads(v)


def _to_string(v):
    return None if _is_null(v) else str(v)


def write_dataframe(
    df: pd.DataFrame,
    uri: str | Path,
    *,
    lineage: dict | None = None,
) -> str:
    """Write ``df`` to ``uri`` as ZSTD-compressed Parquet.

    - Dict/list columns are JSON-encoded; the ``vdl_json_columns`` schema
      metadata records which ones so reads can decode them.
    - Object columns with mixed scalar types are coerced to string (with a
      warning) so pyarrow has a single type per column.
    - ``lineage`` is stored in the file footer under ``vdl_lineage`` with
      an automatic ``created_at`` timestamp.
    - For ``s3://`` URIs, the buffer is uploaded via ``boto3.put_object``;
      the bucket must already exist.
    """
    uri = str(uri)
    json_cols, mixed_cols = _scan_columns(df)

    if json_cols or mixed_cols:
        df = df.copy()
    for col in json_cols:
        df[col] = df[col].map(_encode_json)
    if mixed_cols:
        logger.warning("Coercing mixed-type columns to string: %s", mixed_cols)
        for col in mixed_cols:
            df[col] = df[col].map(_to_string)

    table = pa.Table.from_pandas(df, preserve_index=False)
    meta = dict(table.schema.metadata or {})
    meta[_JSON_COLS_KEY] = json.dumps(json_cols).encode()
    meta[_LINEAGE_KEY] = json.dumps(
        {"created_at": datetime.now(timezone.utc).isoformat(), **(lineage or {})},
        default=str,
    ).encode()
    table = table.replace_schema_metadata(meta)

    if _is_s3(uri):
        bucket, key = _split_s3_uri(uri)
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="zstd", compression_level=3)
        boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
    else:
        Path(uri).parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, uri, compression="zstd", compression_level=3)

    logger.info("Wrote %d rows → %s (%d json cols)", len(df), uri, len(json_cols))
    return uri
