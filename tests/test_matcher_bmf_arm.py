"""The IRS BMF alias arm of the matcher's filer universe: view definition,
arm order kept in lockstep across the three readers, checkpoint lineage.
Pure text checks and a fake connection; no database."""

import re

from givingtuesday_datamart import corrections_preflight, grant_matching, match_explainer
from givingtuesday_datamart.exploratory import bmf_alias_gain


def _bmf_view_ddl() -> str:
    return next(d for d in grant_matching._VIEW_DDL if "CREATE OR REPLACE VIEW public.eo_bmf_unique_names_view" in d)


def test_bmf_view_emits_name_and_sort_name_rows_with_the_shared_key_columns():
    ddl = " ".join(_bmf_view_ddl().split())
    assert "LOWER(ein) filerein_key, LOWER(name) name1_key, '' name2_key" in ddl
    assert "SELECT LOWER(ein), LOWER(sort_name), ''" in ddl
    assert "LOWER(LEFT(zip, 5)) addresszip_key" in ddl
    assert "FROM public.eo_bmf WHERE name IS NOT NULL" in ddl
    # SORT_NAME rows: only when set, different from NAME, and not a trustee or bank line.
    assert "sort_name <> name" in ddl
    assert "sort_name !~ '\\m(TTEE|TRUSTEE|TRUSTEES|BANK|N A)\\M'" in ddl
    assert "taxyear" not in ddl  # every BMF row is a currently recognized org
    # Same DISTINCT-by-GROUP-BY and deterministic ORDER BY as the other unique-names views.
    assert ddl.count("filerein_key, name1_key, name2_key, address1_key, address2_key, addresscity_key, addressstate_key, addresszip_key") >= 2


def _arm_order(sql: str) -> list[tuple[str, int | None]]:
    """(source, source_rank) per UNION arm, in order of appearance."""
    arms = re.findall(r"'(\w+)' AS source(?:, (\d) AS source_rank)?", sql)
    return [(src, int(rank) if rank else None) for src, rank in arms]


def test_universe_arms_are_in_lockstep_across_matcher_preflight_and_explainer():
    import inspect
    matcher_sql = inspect.getsource(grant_matching._do_match_records) if hasattr(grant_matching, "_do_match_records") \
        else inspect.getsource(grant_matching)
    expected = [("basic_fields", 1), ("basic_fields_pf", 2), ("eo_bmf", 3), ("correction", 4)]
    assert _arm_order(corrections_preflight._UNIVERSE_SQL) == expected
    assert [src for src, _ in _arm_order(match_explainer._UNIVERSE_SQL)] == [src for src, _ in expected]
    assert expected[0] in _arm_order(matcher_sql) and ("eo_bmf", 3) in _arm_order(matcher_sql) and ("correction", 4) in _arm_order(matcher_sql)


def test_bmf_ranks_below_filings_and_above_corrections():
    order = dict(_arm_order(corrections_preflight._UNIVERSE_SQL))
    assert order["basic_fields"] < order["basic_fields_pf"] < order["eo_bmf"] < order["correction"]


def test_shape_version_bumped_for_the_fourth_arm():
    assert grant_matching.MATCHING_INPUT_SHAPE_VERSION == 3
    assert "irs_eo_bmf" in grant_matching._MATCHING_INPUT_LOGICAL_NAMES


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, versions):
        self.versions = versions

    def execute(self, stmt, params):
        names = params["names"]
        return _FakeResult([(n, self.versions[n]) for n in names if n in self.versions])


def test_checkpoint_prefix_carries_the_bmf_version(monkeypatch):
    monkeypatch.setattr(grant_matching, "_corrections_content_hash", lambda: "abcd1234")
    conn = _FakeConnection({
        "irs_990pf_grants": "2026_06_16", "irs_990_basic_fields": "2026_06_16",
        "irs_990pf_basic_fields": "2026_06_16", "irs_eo_bmf": "2026_10_13",
    })
    prefix = grant_matching._resolve_checkpoint_prefix(conn)
    assert prefix == ("grant_matching_checkpoints/pg_2026_06_16/bf_2026_06_16/bfpf_2026_06_16/"
                      "bmf_2026_10_13/corr_abcd1234/shape_v3")


def test_checkpoint_prefix_refuses_without_a_bmf_ingest(monkeypatch):
    import pytest
    monkeypatch.setattr(grant_matching, "_corrections_content_hash", lambda: "abcd1234")
    conn = _FakeConnection({"irs_990pf_grants": "x", "irs_990_basic_fields": "x", "irs_990pf_basic_fields": "x"})
    with pytest.raises(RuntimeError, match="irs_eo_bmf"):
        grant_matching._resolve_checkpoint_prefix(conn)


def test_unmatched_sample_is_deterministic_and_excludes_matched_tuples():
    import pandas as pd
    keys = bmf_alias_gain._KEY7
    grants = pd.DataFrame([dict(zip(keys, [f"org {i}", "", "1 main st", "", "town", "ca", "90001"])) for i in range(10)])
    matched = grants.iloc[:4][keys]
    a = bmf_alias_gain.unmatched_sample(grants, matched, 3)
    b = bmf_alias_gain.unmatched_sample(grants, matched, 3)
    assert a.attrs["unmatched_total"] == 6 and len(a) == 3
    assert a[keys].equals(b[keys])
    assert not set(a["name1_key"]) & set(matched["name1_key"])
