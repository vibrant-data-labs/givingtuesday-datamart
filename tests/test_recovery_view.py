"""``placeholder_recovery.view`` and ``checks``: the SQL they build. The
view itself is checked against the database, by ``check``."""

from __future__ import annotations

import pytest

from givingtuesday_datamart.placeholder_recovery import checks, classifier, loader, view


def test_the_view_shows_privategrants_currents_columns_then_its_own():
    sql = view.view_sql("v2")
    assert sql.strip().startswith(f"CREATE OR REPLACE VIEW public.{view.VIEW} AS")
    kept, recovered = sql.split("UNION ALL")
    current = ", ".join(f"g.{column}" for column in view.CURRENT_COLUMNS)
    assert f"SELECT {current}, 'privategrants_current'::text AS row_source, NULL::text AS page_kind" in kept
    assert "'placeholder_recovery'::text AS row_source, r.page_kind::text AS page_kind" in recovered
    assert [name for name, _, _ in view.ADDED] == [
        "row_source", "page_kind", "page_verdict", "filer_marked_individual", "state_source",
        "recovered_object_id", "recovered_policy_version", "recovered_page", "recovered_row_ordinal"]


def test_a_recovered_row_fills_the_recipient_columns_and_takes_the_filings_from_the_row_it_replaces():
    recovered = view.view_sql("v2").split("UNION ALL")[1]
    for column, value in (("sigocpyrbnbn1", "r.match_name"), ("sigocpyrfaal1", "r.match_address"),
                          ("sigocpyrfapo", "r.state"), ("sigocpyrfapc", "r.zip5"),
                          ("sigocpyamoun", "trim_scale(r.amount)::text"), ("sigocpypogoc", "r.purpose"),
                          ("sigocpyrfsta", "r.recipient_status")):
        assert f"{value} AS {column}" in recovered
    assert "NULL::text AS sigocpyrfaci" in recovered and "NULL::text AS sigocpyrpnam" in recovered
    assert all(f"f.{column}" in recovered for column in ("filerein", "taxyear", "url", "filername1", "taxperend",
                                                         "_source_version", "dedup_rule"))
    assert set(view.RECIPIENT) | set(view.RECIPIENT_NULL) | set(view.FILING) == set(view.CURRENT_COLUMNS)


def test_the_view_drops_the_placeholder_rows_of_loaded_filings_only():
    kept = view.view_sql("v2").split("UNION ALL")[0]
    pointer = classifier.pointer_sql(classifier.name_sql("g"))
    assert "LEFT JOIN filing f ON f.filerein = g.filerein AND f.taxyear = g.taxyear AND f.url = g.url" in kept
    assert f"WHERE CASE WHEN f.object_id IS NULL THEN true ELSE NOT {pointer} END" in kept
    assert "position(l.object_id in g.url) > 0" in kept               # the version read is the version loaded


def test_the_pattern_never_runs_over_the_whole_of_privategrants_current():
    """Outside the CASE, which tests the rows of loaded filings, the pattern
    sits in one place: the lookup of a loaded filing's row by its index."""
    sql = view.view_sql("v2")
    pointer = classifier.pointer_sql(classifier.name_sql("g"))
    assert sql.count(pointer) == 2
    lookup = sql[sql.index("CROSS JOIN LATERAL"):sql.index(") p")]
    assert "g.filerein = l.filerein AND g.taxyear = l.taxyear" in lookup and pointer in lookup
    assert lookup.rstrip().endswith("LIMIT 1")


def test_the_view_shows_one_policys_paid_rows():
    sql = view.view_sql("v2-leave_out")
    assert sql.count("policy_version = 'v2-leave_out' AND target = 'paid'") == 1
    assert sql.count("r.policy_version = 'v2-leave_out' AND r.target = 'paid'") == 1
    with pytest.raises(ValueError, match="cannot be written into the view"):
        view.view_sql("v2'; DROP TABLE privategrants_current; --")


def test_every_check_reads_the_loaded_rows_of_one_policy():
    assert list(checks.CHECKS) == ["reading", "verdict", "work list", "adds up", "double count", "placeholder"]
    assert all(":version" in sql and loader.TABLE in sql for sql in checks.CHECKS.values())
    assert view.VIEW in checks.CHECKS["double count"] and view.VIEW in checks.CHECKS["placeholder"]


def test_the_checks_summary_says_what_failed():
    found = [checks.Check("reading", 0), checks.Check("double count", 2)]
    text = checks.summary(found, {"rows": 10, "filings": 2, "view_rows": 10, "view_filings": 2})
    assert "reading       ok  0" in text and "double count  FAILED  2" in text
    assert [check.passed for check in found] == [True, False]


class _Session:
    """Answers the view's definition and records what is executed."""

    def __init__(self, definition):
        self.definition, self.executed, self.commits = definition, [], 0

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append(sql)
        outer = self

        class Result:
            def scalar(self):
                return outer.definition

            def __iter__(self):
                return iter([(column,) for column in view.CURRENT_COLUMNS])
        return Result()

    def commit(self):
        self.commits += 1


def _created(session):
    return [sql for sql in session.executed if "CREATE OR REPLACE VIEW" in sql]


def test_a_load_creates_the_view_when_there_is_none():
    session = _Session(None)
    assert view.ensure_view(session, "v2") is True
    assert len(_created(session)) == 1 and "policy_version = 'v2'" in _created(session)[0]


def test_a_load_under_another_policy_leaves_the_view_as_it_is(caplog):
    session = _Session(" WITH loaded AS ( SELECT ... WHERE policy_version = 'v2'::text AND target = 'paid'::text)")
    assert view.shown_policy(session) == "v2"
    assert view.ensure_view(session, "v2-leave_out") is False and _created(session) == []
    assert "shows policy v2 and is left as it is" in caplog.text
    assert view.ensure_view(session, "v2") is True and len(_created(session)) == 1


def test_the_view_is_not_built_on_a_table_that_lacks_its_columns():
    class Short(_Session):
        def execute(self, statement, params=None):
            result = super().execute(statement, params)
            result.__class__.__iter__ = lambda self: iter([("filerein",), ("taxyear",)])
            return result
    with pytest.raises(LookupError, match="lacks"):
        view.create_view(Short(None), "v2")
