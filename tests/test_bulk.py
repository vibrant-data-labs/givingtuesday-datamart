"""``_internal.bulk``: the collector, the one-statement upsert and the
batch key lookup the storage modules share, on their own."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

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
