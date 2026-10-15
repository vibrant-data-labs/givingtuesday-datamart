"""Three pieces the storage-layer modules share: collecting worker results
into chunked commits, one INSERT statement for many rows, and one SELECT
for a batch of composite keys.

``filing_images`` and ``page_readings`` both run a thread pool whose workers
return rows rather than raising, collect them on the main thread and upsert
them a chunk at a time. ``upsert_as_done`` is that loop; ``multi_row_insert``
and ``multi_row_params`` are the one-statement upsert, because psycopg2's
``executemany`` sends one statement per row and a round trip each — the
first backfill of 7,626 readings spent ten minutes waiting on the network
that way. ``keyed_select`` and ``keyed_params`` are the batch lookup
``page_readings`` and ``page_verdicts`` share: the key columns go over as
parallel arrays through ``unnest``, so the statement has one parameter per
key column however many keys are looked up (a VALUES list would hit
Postgres's 65,535-parameter limit at about 9,000 pages).
"""

from __future__ import annotations

import json
from concurrent.futures import CancelledError, Future, as_completed
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence, TypeVar

from givingtuesday_datamart._internal.logger import logger

Row = TypeVar("Row")


class SystemicFailure(RuntimeError):
    """``upsert_as_done``'s breaker tripped: the run's first results say the
    failure is the machine's, not the pages', and nothing was written."""


class Store(Protocol[Row]):
    def upsert(self, rows: Sequence[Row]) -> None: ...


def upsert_as_done(store: Store[Row], futures: Sequence[Future], chunk: int, *,
                   on_stop: Callable[[], None] | None = None,
                   breaker: Callable[[list[Row]], str | bool] | None = None) -> Iterator[Row]:
    """Collect worker results on the main thread and commit every ``chunk``.

    ``breaker`` is a circuit breaker on the run's first results: while it is
    deciding, rows are held back from the store and it is asked after each
    one, with every row so far. ``True`` releases the run (commits as usual
    from here); a string trips it — the queued jobs are cancelled, the jobs
    in flight are waited for, nothing held or collected is written, and
    ``SystemicFailure`` is raised with the string; ``False`` keeps deciding.

    Workers return rows rather than raising; one that raises anyway is
    logged and re-raised only after every other result has been collected
    and committed. When the loop stops for any other reason — an interrupt
    in the wait, the caller raising — the jobs not yet started are
    cancelled (a pool's ``with`` exit otherwise runs every queued job to
    completion on the way out and throws the results away, which for a
    reading pool is paying for pages nobody stores: the rehearsal run,
    Ctrl-C with 800 pages queued), ``on_stop`` is called (``read_pages``
    cancels the renders not yet started there, so a read in flight waiting
    on one fails fast instead of waiting through the render queue), and
    the results of the jobs in flight are collected: the pool's exit waits
    for them regardless, so recording them costs nothing and a resume does
    not buy them again. Whatever is buffered is then committed.
    """
    buffer: list[Row] = []
    failures: list[Exception] = []
    collected: set[Future] = set()
    completed = tripped = False
    deciding = breaker is not None
    try:
        for future in as_completed(futures):
            collected.add(future)
            try:
                row = future.result()
            except Exception as exc:                      # noqa: BLE001
                logger.exception("a worker raised; its row is not recorded this run")
                failures.append(exc)
                continue
            buffer.append(row)
            if deciding:
                verdict = breaker(buffer)
                if isinstance(verdict, str):
                    tripped = True
                    raise SystemicFailure(verdict)
                deciding = not verdict
            if not deciding and len(buffer) >= chunk:
                ready, buffer = buffer, []
                store.upsert(ready)
            yield row
        completed = True
    finally:
        if not completed:
            cancelled = sum(future.cancel() for future in futures)    # True only for a job not yet started
            in_flight = [future for future in futures if future not in collected and not future.cancelled()]
            logger.warning("stopping: %d queued jobs cancelled before they started; %d in flight are waited for "
                           "and recorded", cancelled, len(in_flight))
            if on_stop is not None:
                on_stop()
            for future in in_flight:
                try:
                    result = future.result()
                    if not tripped:
                        buffer.append(result)
                except CancelledError:
                    logger.warning("stopping: a job in flight was cancelled underneath (its render, say); "
                                   "its row is not recorded")
                except Exception:                         # noqa: BLE001
                    logger.exception("stopping: a job in flight raised; its row is not recorded")
        if tripped:
            logger.error("the breaker tripped: %d rows held back and not written", len(buffer))
            buffer.clear()
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


def keyed_select(table: str, columns: Sequence[str], key_columns: Sequence[str], key_types: Sequence[str]) -> str:
    """``SELECT columns FROM table WHERE (key columns) IN (SELECT * FROM
    unnest(CAST(:k1 AS t1[]), …))``: one statement for a batch of composite
    keys, each key column bound once as an array of its ``key_types`` type.
    A further condition appends as ``… AND column = :name``."""
    arrays = ", ".join(f"CAST(:{c} AS {t}[])" for c, t in zip(key_columns, key_types))
    return (f"SELECT {', '.join(columns)} FROM {table} WHERE ({', '.join(key_columns)}) IN ("
            f"SELECT * FROM unnest({arrays}))")


def keyed_params(keys: Sequence[Sequence[Any]], key_columns: Sequence[str]) -> dict[str, list]:
    """The bind parameters for ``keyed_select``: one list per key column,
    the keys' values in order."""
    return {column: [key[i] for key in keys] for i, column in enumerate(key_columns)}
