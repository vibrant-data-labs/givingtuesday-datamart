"""--skip-rebuild takes the _current relations only when a matcher run that
rebuilt them stamped them with the source versions the checkpoint prefix is
keyed on now: chunks computed on relations from an older drop must never be
written under a newer drop's prefix."""
import json

import pytest

pytest.importorskip("recordlinkage")

from givingtuesday_datamart import grant_matching as gm  # noqa: E402

VERSIONS = {"irs_990pf_grants": "2026_10_25", "irs_990_basic_fields": "2026_10_25",
            "irs_990pf_basic_fields": "2026_10_25", "irs_eo_bmf": "2026_10_13"}
TABLES = [t for t, _ in gm._CURRENT_TABLES]


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Catalog:
    """The table comments, as COMMENT ON TABLE writes and obj_description reads them."""

    def __init__(self, comments=None):
        self.comments, self.statements = dict(comments or {}), []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if sql.startswith("COMMENT ON TABLE "):
            self.comments[sql.split()[3]] = params["stamp"]
            return _Scalar(None)
        assert "obj_description(to_regclass(:name)" in sql
        return _Scalar(self.comments.get(params["name"]))


def test_a_rebuild_stamps_every_relation_and_a_restart_takes_them():
    catalog = _Catalog()
    gm._stamp_current_relations(catalog, VERSIONS, "build-1")
    assert sorted(catalog.comments) == sorted(f"public.{t}" for t in TABLES)
    assert json.loads(catalog.comments["public.privategrants_current"]) == {
        "source_versions": VERSIONS, "build_id": "build-1"}
    assert gm._unstamped_current_relations(catalog, VERSIONS) == []


def test_a_newer_drop_or_a_relation_rebuilt_elsewhere_refuses_the_restart():
    catalog = _Catalog()
    gm._stamp_current_relations(catalog, VERSIONS, "build-1")
    # an ingest since the rebuild: the prefix would name the new drop
    assert gm._unstamped_current_relations(catalog, {**VERSIONS, "irs_990pf_grants": "2026_11_01"}) == TABLES
    # sources refresh rebuilt one relation: a new table, no stamp
    del catalog.comments["public.basic_fields_current"]
    assert gm._unstamped_current_relations(catalog, VERSIONS) == ["basic_fields_current"]


@pytest.mark.parametrize("comment", [None, "", "built by hand", "[1, 2]", '{"build_id": "x"}'])
def test_anything_but_a_stamp_is_no_stamp(comment):
    assert gm._stamped_versions(comment) is None
    assert gm._unstamped_current_relations(_Catalog({f"public.{t}": comment for t in TABLES}), VERSIONS) == TABLES
