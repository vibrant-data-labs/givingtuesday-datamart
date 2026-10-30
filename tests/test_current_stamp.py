"""--skip-rebuild takes the _current relations only when a matcher run that
rebuilt them stamped them with the source versions the checkpoint prefix is
keyed on now: chunks computed on relations from an older drop must never be
written under a newer drop's prefix. A run builds and stamps each relation
in a transaction of its own (``current_grants.build_current_relations``), so
a relation carries a stamp only when it is complete, and a failure leaves
the relations after it untouched. The sessions are faked; the real session
helper commits each one or rolls it back."""
import json

import pytest

pytest.importorskip("recordlinkage")

from givingtuesday_datamart import current_grants as cg  # noqa: E402
from givingtuesday_datamart import grant_matching as gm  # noqa: E402
from givingtuesday_datamart._internal import db  # noqa: E402

VERSIONS = {"irs_990pf_grants": "2026_10_25", "irs_990_basic_fields": "2026_10_25",
            "irs_990pf_basic_fields": "2026_10_25", "irs_eo_bmf": "2026_10_13"}
TABLES = [t for t, _ in gm._CURRENT_TABLES]
STAMP = gm._current_stamp(VERSIONS, "build-1")


class _Result:
    def __init__(self, rows=None, row=None, scalar=None):
        self.rows, self.returns_rows, self.row, self.scalar = rows or [], rows is not None, row, scalar

    def scalar_one(self):
        return self.scalar

    def fetchall(self):
        return self.rows

    def one(self):
        return self.row


class _Catalog:
    """The table comments, as COMMENT ON TABLE writes and obj_description
    reads them, shared by every session of a rebuild."""

    def __init__(self, comments=None):
        self.comments = dict(comments or {})


class _Connection:
    """What a build and its stamp need of a connection: it records the
    statements, answers the build's reads, writes and reads the catalog's
    comments, and fails on the statement it is told to."""

    def __init__(self, catalog, fail_on=None):
        self.catalog, self.fail_on, self.statements = catalog, fail_on, []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if self.fail_on and sql.lstrip().startswith(self.fail_on):
            raise RuntimeError("could not extend file: No space left on device")
        if sql.startswith("COMMENT ON TABLE "):
            self.catalog.comments[sql.split()[3]] = params["comment"]
            return _Result()
        if "obj_description(to_regclass(:name)" in sql:
            return _Result(scalar=self.catalog.comments.get(params["name"]))
        if "pg_size_bytes(current_setting('work_mem'))" in sql:
            return _Result(row=(64 * 2**20, 256 * 2**20, 0, 2**30))
        if sql.lstrip().startswith("SELECT COUNT(*)"):
            return _Result(scalar=1)
        return _Result(rows=[] if sql.lstrip().startswith("SELECT t.filerein") else None)


class _Session:
    """A session whose outcome the real helper decides: committed after a
    clean block, rolled back after an exception."""

    def __init__(self, catalog, fail_on=None):
        self._connection, self.outcome = _Connection(catalog, fail_on), None

    def connection(self):
        return self._connection

    @property
    def statements(self):
        return self._connection.statements

    def commit(self):
        self.outcome = "committed"

    def rollback(self):
        self.outcome = "rolled back"

    def close(self):
        pass


def sessions_of(monkeypatch, catalog, fail_on=None) -> list[_Session]:
    """Every session the build opens, in order, each run by the real helper
    with its engine replaced."""
    opened: list[_Session] = []

    def open_session(config=None, session=None):
        opened.append(_Session(catalog, fail_on))
        return db.get_session(session=opened[-1])

    monkeypatch.setattr(cg, "get_session", open_session)
    return opened


def test_a_rebuild_builds_and_stamps_each_relation_in_a_transaction_of_its_own(monkeypatch):
    catalog = _Catalog()
    opened = sessions_of(monkeypatch, catalog)
    assert list(cg.build_current_relations({}, comment=STAMP)) == TABLES
    assert [session.outcome for session in opened] == ["committed"] * len(TABLES)
    for session, table in zip(opened, TABLES):
        ddl = [sql for sql in session.statements if sql.lstrip().startswith(("DROP TABLE IF EXISTS public.",
                                                                              "CREATE TABLE public."))]
        assert [sql.split()[4 if sql.lstrip().startswith("DROP") else 2] for sql in ddl] == [f"public.{table}"] * 2
        # last, after the indexes, the ANALYZE and the count: nothing is stamped before it is complete
        assert session.statements[-1] == f"COMMENT ON TABLE public.{table} IS :comment"
    assert json.loads(catalog.comments["public.privategrants_current"]) == {
        "source_versions": VERSIONS, "build_id": "build-1"}
    assert sorted(catalog.comments) == sorted(f"public.{t}" for t in TABLES)
    assert gm._unstamped_current_relations(_Connection(catalog), VERSIONS) == []       # a restart takes them


def test_a_failure_leaves_each_relation_complete_and_stamped_or_untouched(monkeypatch):
    older = gm._current_stamp({**VERSIONS, "irs_990pf_grants": "2026_06_16"}, "build-0")
    catalog = _Catalog({f"public.{t}": older for t in TABLES})                          # the last run's stamps
    failing = TABLES[3]
    opened = sessions_of(monkeypatch, catalog, fail_on=f"CREATE TABLE public.{failing}")
    with pytest.raises(RuntimeError, match="No space left"):                           # match_records marks the build failed
        cg.build_current_relations({}, comment=STAMP)
    assert [session.outcome for session in opened] == ["committed"] * 3 + ["rolled back"]
    assert not any(sql.startswith("COMMENT ON") for sql in opened[3].statements)
    assert catalog.comments[f"public.{failing}"] == older                             # as it was: its DROP rolled back
    assert gm._unstamped_current_relations(_Connection(catalog), VERSIONS) == TABLES[3:]  # so the restart is refused


def test_a_rebuild_outside_a_run_stamps_nothing(monkeypatch):
    # the current_grants CLI and the own-extract release: a new table, no stamp, so the next run rebuilds it
    catalog = _Catalog()
    opened = sessions_of(monkeypatch, catalog)
    assert cg.build_current_relations({}, [cg._TABLES[-1]]) == {"privategrants_future_current": 1}
    assert len(opened) == 1 and not any(sql.startswith("COMMENT ON") for sql in opened[0].statements)
    assert gm._unstamped_current_relations(_Connection(catalog), VERSIONS) == TABLES


def test_a_newer_drop_or_a_relation_rebuilt_elsewhere_refuses_the_restart(monkeypatch):
    catalog = _Catalog()
    sessions_of(monkeypatch, catalog)
    cg.build_current_relations({}, comment=STAMP)
    # an ingest since the rebuild: the prefix would name the new drop
    assert gm._unstamped_current_relations(
        _Connection(catalog), {**VERSIONS, "irs_990pf_grants": "2026_11_01"}) == TABLES
    # sources refresh rebuilt one relation: a new table, no stamp
    del catalog.comments["public.basic_fields_current"]
    assert gm._unstamped_current_relations(_Connection(catalog), VERSIONS) == ["basic_fields_current"]


@pytest.mark.parametrize("comment", [None, "", "built by hand", "[1, 2]", '{"build_id": "x"}'])
def test_anything_but_a_stamp_is_no_stamp(comment):
    assert gm._stamped_versions(comment) is None
    catalog = _Catalog({f"public.{t}": comment for t in TABLES})
    assert gm._unstamped_current_relations(_Connection(catalog), VERSIONS) == TABLES
