"""
Stream a CSV from a URL directly into a PostgreSQL table via COPY.

Parses the HTTP response with ``csv.reader`` over a decoded ``TextIOWrapper``
so quoted newlines inside CSV fields (common in 990 mission statements and
Schedule O narratives) are handled correctly — a naive line-based reader
would split those mid-row and silently produce "malformed" rows.

No disk caching, no in-memory load of the full file: suitable for multi-GB
inputs on memory-constrained hosts. Columns are created as TEXT; caller may
pass ``extra_columns`` to stamp constant values (lineage metadata) on every
row during the same COPY pass.
"""

from __future__ import annotations

import csv
import io
import sys

import requests
from sqlalchemy import text

from vdl_tools.shared_tools.database_cache.database_utils import get_session
from vdl_tools.shared_tools.tools.logger import logger


# Bump the csv module's per-field size limit up front. The stdlib default is
# 128 KB, which is too small for some 990 Schedule O narratives (long
# free-text program descriptions). Do this once at import time.
csv.field_size_limit(sys.maxsize)


# Rows per COPY batch. Balances DB round-trips against memory held in the
# Python-side `batch` list and csv.writer StringIO buffer.
STREAMING_BATCH_SIZE = 50000


def _sanitize_column_name(col: str) -> str:
    """Lowercase and strip non-SQL-safe characters from a single column name."""
    sanitized = col.lower()
    sanitized = sanitized.replace('.', '_')
    sanitized = sanitized.replace(' ', '_')
    sanitized = sanitized.replace('-', '_')
    sanitized = sanitized.replace('(', '')
    sanitized = sanitized.replace(')', '')
    while '__' in sanitized:
        sanitized = sanitized.replace('__', '_')
    sanitized = sanitized.strip('_')
    return sanitized


def _build_column_mapping(columns: list[str]) -> dict[str, str]:
    """Map original CSV headers to sanitized SQL names, disambiguating duplicates."""
    column_mapping: dict[str, str] = {}
    seen_names: dict[str, int] = {}

    for col in columns:
        sanitized = _sanitize_column_name(col)
        if sanitized in seen_names:
            seen_names[sanitized] += 1
            sanitized = f"{sanitized}_{seen_names[sanitized]}"
        else:
            seen_names[sanitized] = 0
        column_mapping[col] = sanitized

    return column_mapping


def _create_table_from_columns(engine, table_name: str, columns: list[str], overwrite: bool) -> None:
    """Create the target table with TEXT columns (drop-if-overwrite)."""
    col_defs = ', '.join([f'"{col}" TEXT' for col in columns])
    with engine.connect() as conn:
        if overwrite:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
        conn.execute(text(f"CREATE TABLE IF NOT EXISTS {table_name} ({col_defs})"))
        conn.commit()
    logger.info(f"Created table {table_name} with {len(columns)} columns")


def _write_batch_to_db(
    cursor,
    table_name: str,
    columns: list[str],
    rows: list[list],
    extra_values: list | None = None,
) -> None:
    """COPY one batch of rows into the target table.

    ``extra_values`` is appended to every row (used to stamp lineage constants
    like ``_source_version`` without a second pass).
    """
    if not rows:
        return

    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    extras: list = list(extra_values) if extra_values else []

    for row in rows:
        csv_row = [val if val is not None else '' for val in row]
        if extras:
            csv_row.extend(extras)
        writer.writerow(csv_row)

    output.seek(0)
    column_names = ', '.join([f'"{col}"' for col in columns])
    cursor.copy_expert(
        f"COPY {table_name} ({column_names}) FROM STDIN WITH (FORMAT csv, NULL '')",
        output,
    )
    output.close()


def stream_csv_url_to_table(
    url: str,
    table_name: str,
    overwrite: bool = True,
    batch_size: int = STREAMING_BATCH_SIZE,
    extra_columns: dict[str, str] | None = None,
    db_config: dict | None = None,
) -> int:
    """Stream a CSV from ``url`` directly into ``table_name`` via COPY.

    Parses the response with ``csv.reader`` over a ``TextIOWrapper`` with
    ``newline=""`` so CSV quoting (including multi-line quoted fields) is
    honored — the csv module controls newline handling, not the wrapper.

    Args:
        url: HTTP(S) URL of a CSV file.
        table_name: Fully-qualified target (``schema.table`` or bare name).
        overwrite: If True, drop the target table before creating.
        batch_size: Rows per COPY batch.
        extra_columns: Optional mapping of column_name -> constant value. Each
            entry adds a TEXT column to the created table and stamps the
            constant on every row at COPY time.
        db_config: Optional vdl-tools config dict override (targets a
            non-default database).

    Returns:
        Number of rows written.
    """
    logger.info(f"Streaming CSV from: {url}")
    logger.info(f"Target table: {table_name}")
    logger.info(f"Batch size: {batch_size:,} rows")

    extra_names: list[str] = list(extra_columns.keys()) if extra_columns else []
    extra_vals: list[str] = [extra_columns[k] for k in extra_names] if extra_columns else []

    try:
        response = requests.get(url, stream=True, timeout=3600)
        response.raise_for_status()
        # Transparently decode gzip/deflate transport encodings if the server
        # applied them. No-op on uncompressed responses.
        response.raw.decode_content = True

        total_size = int(response.headers.get('content-length', 0))
        if total_size > 0:
            logger.info(f"File size: {total_size / (1024 ** 3):.2f} GB (uncompressed server hint)")

        # newline="" is required so the csv module controls newline handling;
        # otherwise TextIOWrapper's universal newline translation would split
        # multi-line quoted fields before csv.reader ever sees them.
        text_stream = io.TextIOWrapper(
            response.raw, encoding='utf-8', errors='replace', newline='',
        )
        reader = csv.reader(text_stream)

        try:
            original_columns = next(reader)
        except StopIteration:
            raise Exception(f"CSV at {url} is empty (no header row)")

        column_mapping = _build_column_mapping(original_columns)
        source_columns = [column_mapping[col] for col in original_columns]
        all_columns = source_columns + extra_names

        logger.info(
            f"Found {len(source_columns)} source columns "
            f"(+ {len(extra_names)} lineage columns)"
        )
        preview = ', '.join(all_columns[:10])
        suffix = '...' if len(all_columns) > 10 else ''
        logger.info(f"Columns: {preview}{suffix}")

        with get_session(config=db_config) as session:
            engine = session.bind
            _create_table_from_columns(engine, table_name, all_columns, overwrite)

            raw_conn = engine.raw_connection()
            try:
                cursor = raw_conn.cursor()
                batch: list[list[str]] = []
                total_rows = 0

                for row in reader:
                    # Pad/trim to header width. Giving Tuesday CSVs occasionally
                    # ship rows with a missing trailing field; truncation on
                    # excess guards against stray delimiters in rare bad rows.
                    if len(row) < len(source_columns):
                        row = row + [''] * (len(source_columns) - len(row))
                    elif len(row) > len(source_columns):
                        row = row[:len(source_columns)]
                    batch.append(row)

                    if len(batch) >= batch_size:
                        _write_batch_to_db(
                            cursor, table_name, all_columns, batch,
                            extra_values=extra_vals,
                        )
                        raw_conn.commit()
                        total_rows += len(batch)
                        batch = []
                        logger.info(f"Rows written: {total_rows:,}")

                if batch:
                    _write_batch_to_db(
                        cursor, table_name, all_columns, batch,
                        extra_values=extra_vals,
                    )
                    raw_conn.commit()
                    total_rows += len(batch)

                cursor.close()
                logger.info(f"Successfully wrote {total_rows:,} rows to '{table_name}'")
                return total_rows
            finally:
                raw_conn.close()

    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to download CSV from URL: {url}. Error: {e}")
    except Exception as e:
        raise Exception(f"Failed to stream CSV to table: {e}")
