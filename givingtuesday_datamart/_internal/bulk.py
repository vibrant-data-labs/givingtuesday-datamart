"""Two pieces the storage-layer modules share: collecting worker results into
chunked commits, and one INSERT statement for many rows.

``filing_images`` and ``page_readings`` both run a thread pool whose workers
return rows rather than raising, collect them on the main thread and upsert
them a chunk at a time. ``upsert_as_done`` is that loop; ``multi_row_insert``
and ``multi_row_params`` are the one-statement upsert, because psycopg2's
``executemany`` sends one statement per row and a round trip each — the
first backfill of 7,626 readings spent ten minutes waiting on the network
that way.
"""

from __future__ import annotations

import json
from concurrent.futures import Future, as_completed
from typing import Any, Iterator, Mapping, Protocol, Sequence, TypeVar

from givingtuesday_datamart._internal.logger import logger

Row = TypeVar("Row")


class Store(Protocol[Row]):
    def upsert(self, rows: Sequence[Row]) -> None: ...


def upsert_as_done(store: Store[Row], futures: Sequence[Future], chunk: int) -> Iterator[Row]:
    """Collect worker results on the main thread and commit every ``chunk``.

    Workers return rows rather than raising; one that raises anyway is
    logged and re-raised only after every other result has been collected
    and committed. Whatever is buffered when the loop stops, for any
    reason, is committed first.
    """
    buffer: list[Row] = []
    failures: list[Exception] = []
    try:
        for future in as_completed(futures):
            try:
                row = future.result()
            except Exception as exc:                      # noqa: BLE001
                logger.exception("a worker raised; its row is not recorded this run")
                failures.append(exc)
                continue
            buffer.append(row)
            if len(buffer) >= chunk:
                ready, buffer = buffer, []
                store.upsert(ready)
            yield row
    finally:
        if buffer:
            store.upsert(buffer)
    if failures:
        raise failures[0]


def multi_row_insert(table: str, columns: Sequence[str], key_columns: Sequence[str], n: int, *,
                     json_columns: Sequence[str] = ()) -> str:
    """``INSERT INTO table (…) VALUES (row 0), … (row n-1) ON CONFLICT (key)
    DO UPDATE SET`` every non-key column from ``EXCLUDED``. Row ``i``'s
    values bind as ``:<column>_<i>``; a JSON column binds through
    ``CAST(… AS jsonb)``. A chunk of 200 rows of 18 columns is 3,600
    parameters, well inside Postgres's 65,535."""
    def placeholder(column: str, i: int) -> str:
        return f"CAST(:{column}_{i} AS jsonb)" if column in json_columns else f":{column}_{i}"

    rows = ", ".join("(" + ", ".join(placeholder(c, i) for c in columns) + ")" for i in range(n))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in key_columns)
    return (f"INSERT INTO {table} ({', '.join(columns)}) VALUES {rows} "
            f"ON CONFLICT ({', '.join(key_columns)}) DO UPDATE SET {updates}")


def multi_row_params(rows: Sequence[Mapping[str, Any]], columns: Sequence[str], *,
                     json_columns: Sequence[str] = ()) -> dict[str, Any]:
    """The bind parameters for ``multi_row_insert``: ``{"<column>_<i>": value}``,
    JSON columns serialised (``None`` stays NULL)."""
    params: dict[str, Any] = {}
    for i, row in enumerate(rows):
        for column in columns:
            value = row[column]
            if column in json_columns and value is not None:
                value = json.dumps(value)
            params[f"{column}_{i}"] = value
    return params
