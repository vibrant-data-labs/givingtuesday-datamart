"""
Unit tests for ``GtDatamartClient.search_identity``.

The identity-search SQL is Postgres-specific (ILIKE, websearch_to_tsquery,
ARRAY_AGG, ::float8 casts), so these tests don't execute it — they assert
the three things the client actually owns:

1. input validation and signal normalization (EIN digits, URL → domain),
2. SQL assembly (which CTEs are emitted for which signals, per arm),
3. the cross-arm merge: ordering, limit slice, and hit construction.

A fake session records every ``execute(sql, params)`` call and returns
canned row batches, one batch per arm query, in execution order
(nonprofit arm first, funder arm second).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from givingtuesday_datamart.client import GtDatamartClient, IdentityHit


class _FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[dict]:
        return self._rows


class _FakeSession:
    """Records (sql, params) per execute; returns canned batches in order."""

    def __init__(self, batches: list[list[dict]]) -> None:
        self._batches = list(batches)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, clause, params=None) -> _FakeResult:
        self.calls.append((clause.text, params or {}))
        return _FakeResult(self._batches.pop(0) if self._batches else [])

    def close(self) -> None:
        pass


def _client(batches: list[list[dict]] | None = None) -> tuple[GtDatamartClient, _FakeSession]:
    # sqlite:// never connects — the engine only exists so __init__ runs;
    # the fake sessionmaker intercepts every query before any I/O.
    client = GtDatamartClient(engine=create_engine("sqlite://"))
    session = _FakeSession(batches or [])
    client._sessionmaker = lambda: session
    return client, session


def _row(ein: str, **overrides) -> dict:
    row = {
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
# Validation
# ---------------------------------------------------------------------------


def test_requires_at_least_one_signal():
    client, _ = _client()
    with pytest.raises(ValueError, match="at least one of name, url, or ein"):
        client.search_identity()


def test_blank_name_is_not_a_signal():
    client, _ = _client()
    with pytest.raises(ValueError, match="at least one of name, url, or ein"):
        client.search_identity(name="   ")


def test_rejects_malformed_ein():
    client, _ = _client()
    with pytest.raises(ValueError, match="9 digits"):
        client.search_identity(ein="12-345")


def test_rejects_non_domain_url():
    client, _ = _client()
    # Whitespace after stripping → not domain-shaped (frontend parity).
    with pytest.raises(ValueError, match="searchable domain"):
        client.search_identity(url="not a url")
    # Shorter than 3 chars after normalization.
    with pytest.raises(ValueError, match="searchable domain"):
        client.search_identity(url="https://ab")


def test_rejects_bad_org_type():
    client, _ = _client()
    with pytest.raises(ValueError, match="org_type"):
        client.search_identity(name="x", org_type="foundation")


# ---------------------------------------------------------------------------
# Signal normalization → bound parameters
# ---------------------------------------------------------------------------


def test_ein_separators_stripped():
    client, session = _client()
    client.search_identity(ein="13-1234567")
    assert len(session.calls) == 2  # both arms run on an EIN signal
    for _, params in session.calls:
        assert params["ein"] == "131234567"


def test_url_normalized_to_domain_and_prefix():
    client, session = _client()
    client.search_identity(url="https://www.Example.org/about?utm=1")
    (_, params), = session.calls
    assert params["domain"] == "example.org"
    assert params["domain_prefix"] == "example.org%"


def test_name_binds_ilike_and_fts_params():
    client, session = _client()
    client.search_identity(name="Michael J Fox Foundation", org_type="nonprofit")
    (_, params), = session.calls
    assert params["like"] == "%Michael J Fox Foundation%"
    assert params["raw_name"] == "Michael J Fox Foundation"


# ---------------------------------------------------------------------------
# SQL assembly per signal / arm
# ---------------------------------------------------------------------------


def test_nonprofit_arm_emits_all_signal_ctes():
    client, session = _client()
    client.search_identity(
        name="fox", url="example.org", ein="131234567", org_type="nonprofit"
    )
    (sql, params), = session.calls
    for cte in ("name_hits", "fts_hits", "ein_hits", "url_hits"):
        assert cte in sql
    # Tier constants ported from the frontend.
    assert "2000000.0::float8" in sql
    assert "1500000.0::float8" in sql
    assert "1200000.0::float8" in sql
    assert "1000000.0::float8" in sql
    # websearch_to_tsquery + ts_rank_cd (not the plainto/ts_rank pair that
    # search_nonprofits uses).
    assert "websearch_to_tsquery('english'" in sql
    assert "ts_rank_cd" in sql
    # 990-EZ contract: narrative hits outside the canonical keep their row.
    assert "LEFT JOIN public.nonprofit_canonical" in sql
    assert params["limit"] == 25


def test_unused_signals_are_not_emitted():
    client, session = _client()
    client.search_identity(name="fox", org_type="nonprofit")
    (sql, _), = session.calls
    assert "url_hits" not in sql
    assert "ein_hits" not in sql
    # The generated `domain` column may not exist on older canonical
    # builds — it must never be referenced unless url was passed.
    assert "domain" not in sql


def test_include_narrative_false_drops_fts():
    client, session = _client()
    client.search_identity(name="fox", org_type="nonprofit", include_narrative=False)
    (sql, _), = session.calls
    assert "name_hits" in sql
    assert "fts_hits" not in sql
    assert "nonprofit_text" not in sql


def test_funder_arm_is_name_and_ein_only():
    client, session = _client()
    client.search_identity(
        name="fox", url="example.org", ein="131234567", org_type="funder"
    )
    (sql, _), = session.calls
    assert "public.funder_canonical" in sql
    assert "name_hits" in sql
    assert "ein_hits" in sql
    # No URL or narrative surface, and no DBA columns, on funders.
    assert "url_hits" not in sql
    assert "fts_hits" not in sql
    assert "dba_1" not in sql


def test_url_only_skips_funder_arm():
    client, session = _client()
    client.search_identity(url="example.org")  # org_type="all"
    assert len(session.calls) == 1
    assert "public.nonprofit_canonical" in session.calls[0][0]


def test_url_only_funder_search_returns_empty_without_querying():
    client, session = _client()
    assert client.search_identity(url="example.org", org_type="funder") == []
    assert session.calls == []


def test_limit_none_omits_sql_limit():
    client, session = _client()
    client.search_identity(name="fox", org_type="nonprofit", limit=None)
    (sql, params), = session.calls
    assert "LIMIT" not in sql
    assert "limit" not in params


# ---------------------------------------------------------------------------
# Cross-arm merge
# ---------------------------------------------------------------------------


def test_merge_orders_by_rank_then_taxyear_then_name_then_ein():
    nonprofit_rows = [
        _row("100000001", rank=1_000_000.0, latest_taxyear=2020, name="Alpha"),
        _row("100000002", rank=12.5, latest_taxyear=2023, name="Narrative Hit", signal="narrative"),
    ]
    funder_rows = [
        _row("200000001", rank=2_000_000.0, latest_taxyear=2019, name="EIN Winner", signal="ein"),
        _row("200000002", rank=1_000_000.0, latest_taxyear=2020, name="Alpha"),
    ]
    client, _ = _client([nonprofit_rows, funder_rows])
    hits = client.search_identity(name="x", ein="131234567")

    assert [h.ein for h in hits] == [
        "200000001",  # EIN tier beats everything despite oldest taxyear
        "100000001",  # rank tie with 200000002, same year, same name → ein ASC
        "200000002",
        "100000002",  # raw FTS rank sorts below all fixed tiers
    ]
    assert [h.org_type for h in hits] == ["funder", "nonprofit", "funder", "nonprofit"]
    assert hits[0].signal == "ein"
    assert hits[3].signal == "narrative"


def test_none_taxyear_sorts_last_within_tier():
    rows = [
        _row("100000001", latest_taxyear=None),
        _row("100000002", latest_taxyear=2010),
    ]
    client, _ = _client([rows])
    hits = client.search_identity(name="x", org_type="nonprofit")
    assert [h.ein for h in hits] == ["100000002", "100000001"]


def test_limit_applied_after_merge():
    nonprofit_rows = [_row(f"10000000{i}", rank=1_000_000.0 + i) for i in range(3)]
    funder_rows = [_row(f"20000000{i}", rank=2_000_000.0) for i in range(3)]
    client, _ = _client([nonprofit_rows, funder_rows])
    hits = client.search_identity(name="x", limit=4)
    assert len(hits) == 4
    # All three funder EIN-tier rows outrank every nonprofit name-tier row.
    assert [h.org_type for h in hits] == ["funder", "funder", "funder", "nonprofit"]


def test_990ez_null_identity_columns_survive():
    rows = [
        _row(
            "100000009",
            name=None,
            name_secondary=None,
            city=None,
            state=None,
            latest_taxyear=None,
            rank=3.2,
            signal="narrative",
        )
    ]
    client, _ = _client([rows])
    hits = client.search_identity(name="tutoring", org_type="nonprofit")
    assert hits == [
        IdentityHit(
            ein="100000009",
            name=None,
            name_secondary=None,
            city=None,
            state=None,
            latest_taxyear=None,
            org_type="nonprofit",
            rank=3.2,
            signal="narrative",
        )
    ]
