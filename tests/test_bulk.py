"""``_internal.bulk``: the collector, the one-statement upsert and the
batch key lookup the storage modules share, on their own."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from givingtuesday_datamart._internal import bulk


class _Store:
    def __init__(self):
        self.rows: list = []
        self.commits: list[int] = []

    def upsert(self, rows):
        self.rows.extend(rows)
        self.commits.append(len(rows))


def test_upsert_as_done_commits_in_chunks_and_raises_a_worker_failure_last(caplog):
    def work(n):
        if n == 7:
            raise RuntimeError("worker 7 broke")
        return n

    store = _Store()
    with ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(work, n) for n in range(25)]
        with pytest.raises(RuntimeError, match="worker 7"):
            list(bulk.upsert_as_done(store, futures, chunk=10))
    assert sorted(store.rows) == [n for n in range(25) if n != 7] and store.commits == [10, 10, 4]
    assert "a worker raised; its row is not recorded this run" in caplog.text


def test_upsert_as_done_cancels_the_queued_jobs_and_records_the_ones_in_flight_when_interrupted(monkeypatch, caplog):
    """Ctrl-C while waiting on the pool: the jobs not yet started are
    cancelled, the ones in flight are waited for (the pool's exit waits for
    them anyway) and recorded, and everything collected is committed —
    rather than the pool running every queued job on the way out with the
    results thrown away, or the in-flight pages being bought again on
    resume."""
    gate, started = threading.Semaphore(0), []

    def work(n):
        started.append(n)
        gate.acquire()
        return n

    def interrupted(futures_):
        for i, future in enumerate(as_completed(futures_)):
            if i == 4:                                    # job 4 is done and not yet yielded
                deadline = time.monotonic() + 5
                while len(started) < 6:                   # job 5 has been picked up and is in flight
                    if time.monotonic() > deadline:
                        pytest.fail("job 5 never started")
                    time.sleep(0.001)
                threading.Timer(0.2, gate.release).start()    # job 5 finishes while the stop waits for it
                raise KeyboardInterrupt
            yield future
            gate.release()                                # let the next job finish

    monkeypatch.setattr(bulk, "as_completed", interrupted)
    store = _Store()
    with ThreadPoolExecutor(1) as pool:
        futures = [pool.submit(work, n) for n in range(25)]
        gate.release()
        with pytest.raises(KeyboardInterrupt):
            list(bulk.upsert_as_done(store, futures, chunk=10))
    assert store.rows == [0, 1, 2, 3, 4, 5] and store.commits == [6]
    assert started == [0, 1, 2, 3, 4, 5] and sum(f.cancelled() for f in futures) == 19
    assert "stopping: 19 queued jobs cancelled before they started; 2 in flight are waited for and recorded" in caplog.text


def test_upsert_as_done_calls_on_stop_before_waiting_and_logs_a_job_cancelled_underneath(monkeypatch, caplog):
    """``on_stop`` runs before the in-flight results are waited for, and a
    job whose own wait is cancelled by it is logged, not recorded."""
    gate, hold, order = threading.Semaphore(0), threading.Semaphore(0), []
    inner = ThreadPoolExecutor(1)
    inner.submit(hold.acquire)                            # keeps ``victim`` pending until it is cancelled
    victim = inner.submit(lambda: None)

    def work(n):
        if n == 1:
            return victim.result()                        # raises CancelledError once on_stop cancels it
        gate.acquire()
        return n

    def interrupted(futures_):
        deadline = time.monotonic() + 5
        while sum(f.running() for f in futures_) < 2:
            if time.monotonic() > deadline:
                pytest.fail("the two jobs never started")
            time.sleep(0.001)
        raise KeyboardInterrupt
        yield                                             # noqa: unreachable, makes this a generator

    def on_stop():
        order.append(victim.cancel())                     # job 1 fails fast
        gate.release()                                    # job 0 finishes and is recorded

    monkeypatch.setattr(bulk, "as_completed", interrupted)
    store = _Store()
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(work, n) for n in range(4)]
        with pytest.raises(KeyboardInterrupt):
            list(bulk.upsert_as_done(store, futures, chunk=10, on_stop=on_stop))
    hold.release()
    inner.shutdown()
    assert order == [True] and store.rows == [0] and store.commits == [1]
    assert "stopping: 2 queued jobs cancelled before they started; 2 in flight are waited for and recorded" in caplog.text
    assert caplog.text.count("a job in flight was cancelled underneath") == 1


def test_multi_row_insert_binds_each_row_by_index_and_casts_json_columns():
    sql = bulk.multi_row_insert("t", ("k1", "k2", "a", "j"), ("k1", "k2"), 2, json_columns=("j",))
    assert sql == ("INSERT INTO t (k1, k2, a, j) VALUES (:k1_0, :k2_0, :a_0, CAST(:j_0 AS jsonb)), "
                   "(:k1_1, :k2_1, :a_1, CAST(:j_1 AS jsonb)) "
                   "ON CONFLICT (k1, k2) DO UPDATE SET a = EXCLUDED.a, j = EXCLUDED.j")
    params = bulk.multi_row_params([{"k1": 1, "k2": "x", "a": None, "j": {"b": [1, 2]}},
                                    {"k1": 2, "k2": "y", "a": 3.5, "j": None}], ("k1", "k2", "a", "j"), json_columns=("j",))
    assert params == {"k1_0": 1, "k2_0": "x", "a_0": None, "j_0": '{"b": [1, 2]}',
                      "k1_1": 2, "k2_1": "y", "a_1": 3.5, "j_1": None}


def test_keyed_select_binds_each_key_column_once_as_an_array():
    sql = bulk.keyed_select("t", ("k1", "k2", "a"), ("k1", "k2"), ("text", "integer"))
    assert sql == ("SELECT k1, k2, a FROM t WHERE (k1, k2) IN ("
                   "SELECT * FROM unnest(CAST(:k1 AS text[]), CAST(:k2 AS integer[])))")
    assert bulk.keyed_params([("x", 1), ("y", 2), ("x", 3)], ("k1", "k2")) == {"k1": ["x", "y", "x"], "k2": [1, 2, 3]}
    assert bulk.keyed_params([], ("k1", "k2")) == {"k1": [], "k2": []}
    narrowed = bulk.keyed_select("t", ("k1", "a"), ("k1",), ("text",)) + " AND v = :v"
    assert narrowed.endswith("unnest(CAST(:k1 AS text[]))) AND v = :v")
