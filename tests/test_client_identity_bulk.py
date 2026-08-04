"""
Unit tests for ``GtDatamartClient.search_identity_bulk`` and
``iter_identity_universe``.

Same approach as ``test_client_identity_search.py``: the SQL is
Postgres-specific, so these cover what the client owns — batching and
chunking, key handling, the ``on_invalid`` contract, conditional CTE
assembly, and the per-key merge/limit. Live SQL behavior is verified
separately against the database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from givingtuesday_datamart.client import (
    CanonicalIdentity,
    GtDatamartClient,
    IdentityQuery,
)


class _FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[dict]:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _FakeConnection:
    def __init__(self, session: "_FakeSession") -> None:
        self._session = session

    def execution_options(self, **kw) -> "_FakeConnection":
        self._session.execution_options.append(kw)
        return self

    def execute(self, clause, params=None) -> _FakeResult:
        return self._session.execute(clause, params)


class _FakeSession:
    def __init__(self, batches: list[list[dict]]) -> None:
        self._batches = list(batches)
        self.calls: list[tuple[str, dict]] = []
        self.execution_options: list[dict] = []

    def execute(self, clause, params=None) -> _FakeResult:
        self.calls.append((clause.text, params or {}))
        return _FakeResult(self._batches.pop(0) if self._batches else [])

    def connection(self) -> _FakeConnection:
        return _FakeConnection(self)

    def close(self) -> None:
        pass


def _client(batches: list[list[dict]] | None = None):
    client = GtDatamartClient(engine=create_engine("sqlite://"))
    session = _FakeSession(batches or [])
    client._sessionmaker = lambda: session
    return client, session


def _row(key: str, ein: str, **overrides) -> dict:
    row = {
        "key": key,
        "ein": ein,
        "name": f"Org {ein}",
        "name_secondary": None,
        "city": "Oakland",
        "state": "CA",
        "latest_taxyear": 2023,
        "rank": 1_000_000.0,
        "signal": "name",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Contract basics
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_dict():
    client, session = _client()
    assert client.search_identity_bulk([]) == {}
    assert session.calls == []


def test_every_key_present_even_with_no_hits():
    client, _ = _client([[], []])
    out = client.search_identity_bulk(
        [IdentityQuery(key="a", name="x"), IdentityQuery(key="b", name="y")]
    )
    assert out == {"a": [], "b": []}


def test_duplicate_keys_rejected():
    client, _ = _client()
    with pytest.raises(ValueError, match="duplicate IdentityQuery.key"):
        client.search_identity_bulk(
            [IdentityQuery(key="a", name="x"), IdentityQuery(key="a", name="y")]
        )


def test_empty_key_rejected():
    client, _ = _client()
    with pytest.raises(ValueError, match="non-empty string"):
        client.search_identity_bulk([IdentityQuery(key="", name="x")])


def test_bad_chunk_size_rejected():
    client, _ = _client()
    with pytest.raises(ValueError, match="chunk_size"):
        client.search_identity_bulk([IdentityQuery(key="a", name="x")], chunk_size=0)


# ---------------------------------------------------------------------------
# on_invalid contract
# ---------------------------------------------------------------------------


def test_invalid_raises_before_any_query_runs():
    client, session = _client()
    with pytest.raises(ValueError, match="invalid identity queries"):
        client.search_identity_bulk(
            [IdentityQuery(key="a", name="ok"), IdentityQuery(key="b", url="N/A")]
        )
    # The whole point of up-front validation: no query time wasted.
    assert session.calls == []


def test_invalid_error_names_offending_keys():
    client, _ = _client()
    with pytest.raises(ValueError) as exc:
        client.search_identity_bulk(
            [IdentityQuery(key="row-7", ein="12-345"), IdentityQuery(key="row-9", url="x")]
        )
    assert "row-7" in str(exc.value)
    assert "row-9" in str(exc.value)


def test_invalid_error_truncates_long_lists():
    client, _ = _client()
    bad = [IdentityQuery(key=f"r{i}", ein="1") for i in range(15)]
    with pytest.raises(ValueError, match=r"\+5 more"):
        client.search_identity_bulk(bad)


def test_skip_drops_only_the_bad_signal():
    client, session = _client([[], []])
    client.search_identity_bulk(
        [IdentityQuery(key="a", name="Good Name", url="N/A")],
        on_invalid="skip",
    )
    # Name survived; the unusable url never reached the SQL.
    _, params = session.calls[0]
    assert params["name_vals"] == ["Good Name"]
    assert params["url_vals"] == []


def test_skip_leaves_signal_less_row_with_empty_result():
    client, session = _client()
    out = client.search_identity_bulk(
        [IdentityQuery(key="a", url="N/A")], on_invalid="skip"
    )
    assert out == {"a": []}
    assert session.calls == []  # nothing to search


# ---------------------------------------------------------------------------
# Batching / SQL assembly
# ---------------------------------------------------------------------------


def test_one_round_trip_per_arm_not_per_row():
    rows = [IdentityQuery(key=f"k{i}", ein=f"1300000{i:02d}") for i in range(50)]
    client, session = _client([[], []])
    client.search_identity_bulk(rows)
    # 50 inputs, 2 arms, 1 chunk -> 2 queries total.
    assert len(session.calls) == 2
    for _, params in session.calls:
        assert len(params["ein_vals"]) == 50


def test_signals_are_bound_as_parallel_key_value_arrays():
    client, session = _client([[], []])
    client.search_identity_bulk(
        [
            IdentityQuery(key="a", name="Alpha"),
            IdentityQuery(key="b", url="https://www.beta.org/x"),
            IdentityQuery(key="c", ein="13-4141945"),
        ]
    )
    sql, params = session.calls[0]
    assert params["name_keys"] == ["a"] and params["name_vals"] == ["Alpha"]
    assert params["url_keys"] == ["b"] and params["url_vals"] == ["beta.org"]
    assert params["ein_keys"] == ["c"] and params["ein_vals"] == ["134141945"]
    assert "unnest(:name_keys ::text[], :name_vals ::text[])" in sql


def test_chunking_splits_into_multiple_round_trips():
    rows = [IdentityQuery(key=f"k{i}", ein=f"1300000{i:02d}") for i in range(10)]
    client, session = _client([[] for _ in range(10)])
    client.search_identity_bulk(rows, chunk_size=4, org_type="nonprofit")
    # 10 rows / chunk 4 -> 3 chunks, 1 arm each.
    assert len(session.calls) == 3
    assert [len(p["ein_vals"]) for _, p in session.calls] == [4, 4, 2]


def test_url_prefix_tier_is_opt_in():
    client, session = _client([[]])
    client.search_identity_bulk(
        [IdentityQuery(key="a", url="example.org")], org_type="nonprofit"
    )
    sql, _ = session.calls[0]
    assert "url_hits" in sql
    assert "url_prefix_hits" not in sql

    client, session = _client([[]])
    client.search_identity_bulk(
        [IdentityQuery(key="a", url="example.org")],
        org_type="nonprofit",
        include_url_prefix=True,
    )
    sql, _ = session.calls[0]
    assert "url_prefix_hits" in sql
    assert "1200000.0::float8" in sql


def test_narrative_can_be_disabled():
    client, session = _client([[]])
    client.search_identity_bulk(
        [IdentityQuery(key="a", name="x")],
        org_type="nonprofit",
        include_narrative=False,
    )
    sql, _ = session.calls[0]
    assert "fts_hits" not in sql
    assert "nonprofit_text" not in sql


def test_funder_arm_skipped_when_chunk_has_only_urls():
    client, session = _client([[]])
    client.search_identity_bulk([IdentityQuery(key="a", url="example.org")])
    # org_type="all", but funders have no URL surface -> nonprofit arm only.
    assert len(session.calls) == 1
    assert "public.funder_canonical" not in session.calls[0][0]


def test_limit_pushed_into_sql_as_row_number_filter():
    client, session = _client([[], []])
    client.search_identity_bulk([IdentityQuery(key="a", name="x")], limit_per_query=5)
    sql, params = session.calls[0]
    assert "ROW_NUMBER() OVER (" in sql
    assert "PARTITION BY m.key" in sql
    assert "rn <= :limit_per_query" in sql
    assert params["limit_per_query"] == 5


def test_limit_none_omits_row_number_filter():
    client, session = _client([[], []])
    client.search_identity_bulk([IdentityQuery(key="a", name="x")], limit_per_query=None)
    sql, params = session.calls[0]
    assert "rn <=" not in sql
    assert "limit_per_query" not in params


# ---------------------------------------------------------------------------
# Per-key merge
# ---------------------------------------------------------------------------


def test_hits_grouped_by_key_and_ordered_within_key():
    nonprofit_rows = [
        _row("a", "100000001", rank=1_000_000.0, latest_taxyear=2020),
        _row("a", "100000002", rank=12.5, signal="narrative"),
        _row("b", "100000003", rank=1_000_000.0),
    ]
    funder_rows = [
        _row("a", "200000001", rank=2_000_000.0, signal="ein"),
    ]
    client, _ = _client([nonprofit_rows, funder_rows])
    out = client.search_identity_bulk(
        [IdentityQuery(key="a", name="x", ein="131234567"), IdentityQuery(key="b", name="y")]
    )
    assert [h.ein for h in out["a"]] == ["200000001", "100000001", "100000002"]
    assert [h.org_type for h in out["a"]] == ["funder", "nonprofit", "nonprofit"]
    assert [h.ein for h in out["b"]] == ["100000003"]


def test_limit_reapplied_after_cross_arm_merge():
    # Each arm independently returned `limit` rows; the union must be
    # re-trimmed or the caller gets 2x what they asked for.
    nonprofit_rows = [_row("a", f"10000000{i}", rank=1_000_000.0) for i in range(3)]
    funder_rows = [_row("a", f"20000000{i}", rank=2_000_000.0) for i in range(3)]
    client, _ = _client([nonprofit_rows, funder_rows])
    out = client.search_identity_bulk(
        [IdentityQuery(key="a", name="x", ein="131234567")], limit_per_query=3
    )
    assert len(out["a"]) == 3
    assert all(h.org_type == "funder" for h in out["a"])


def test_null_identity_columns_preserved():
    rows = [_row("a", "100000009", name=None, city=None, state=None,
                 latest_taxyear=None, rank=3.2, signal="narrative")]
    client, _ = _client([rows])
    out = client.search_identity_bulk(
        [IdentityQuery(key="a", name="tutoring")], org_type="nonprofit"
    )
    assert out["a"][0].name is None
    assert out["a"][0].latest_taxyear is None
    assert out["a"][0].signal == "narrative"


# ---------------------------------------------------------------------------
# iter_identity_universe
# ---------------------------------------------------------------------------


def _universe_row(ein: str, **overrides) -> dict:
    row = {
        "ein": ein,
        "name": f"Org {ein}",
        "name_secondary": None,
        "dba_1": None,
        "dba_2": None,
        "domain": "example.org",
        "city": "Oakland",
        "state": "CA",
        "latest_taxyear": 2023,
    }
    row.update(overrides)
    return row


def test_universe_streams_both_arms_tagged():
    client, _ = _client([[_universe_row("100000001")], [_universe_row("200000001")]])
    got = list(client.iter_identity_universe())
    assert [r.org_type for r in got] == ["nonprofit", "funder"]
    assert isinstance(got[0], CanonicalIdentity)


def test_universe_respects_org_type():
    client, session = _client([[_universe_row("100000001")]])
    list(client.iter_identity_universe(org_type="nonprofit"))
    assert len(session.calls) == 1
    assert "public.nonprofit_canonical" in session.calls[0][0]


def test_universe_uses_server_side_streaming():
    client, session = _client([[], []])
    list(client.iter_identity_universe(batch_size=2_500))
    # Bounded memory is the whole point — assert the options actually get set.
    assert session.execution_options
    assert all(
        o.get("stream_results") is True and o.get("yield_per") == 2_500
        for o in session.execution_options
    )


def test_universe_rejects_bad_batch_size():
    client, _ = _client()
    with pytest.raises(ValueError, match="batch_size"):
        list(client.iter_identity_universe(batch_size=0))


def test_funder_arm_nulls_missing_columns():
    # funder_canonical has no website/DBA columns; the SQL must null them so
    # both arms yield the same dataclass shape.
    client, session = _client([[]])
    list(client.iter_identity_universe(org_type="funder"))
    sql, _ = session.calls[0]
    assert "NULL::text AS dba_1" in sql
    assert "NULL::text AS domain" in sql
