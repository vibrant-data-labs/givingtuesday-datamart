"""--skip-rebuild takes the _current relations that carry the stamp of the
source versions and input shape the checkpoint prefix is keyed on, and
rebuilds the rest: chunks computed on relations from an older drop, or under
older dedup rules, must never be written under a newer prefix. A run builds
and stamps each relation in a transaction of its own
(``current_grants.build_current_relations``), recreating in it the views the
build's DROP ... CASCADE took, so a relation carries a stamp only when it is
complete and the views are never seen gone. The sessions are faked; the real
session helper commits each one or rolls it back."""
import json
from functools import partial

import pandas as pd
import pytest

pytest.importorskip("recordlinkage")

from givingtuesday_datamart import current_grants as cg  # noqa: E402
from givingtuesday_datamart import grant_matching as gm  # noqa: E402
from givingtuesday_datamart._internal import db  # noqa: E402
from test_grant_matching import Session, shows  # noqa: E402
from test_pf_current_rules import _Connection as _BuildConnection, _Result  # noqa: E402

VERSIONS = {"irs_990pf_grants": "2026_10_25", "irs_990_basic_fields": "2026_10_25",
            "irs_990pf_basic_fields": "2026_10_25", "irs_eo_bmf": "2026_10_13"}
TABLES = [t for t, _ in gm._CURRENT_TABLES]
STAMP = gm._current_stamp(VERSIONS, "build-1")


class _Connection(_BuildConnection):
    """What a build, its stamp, the views and the run's bookkeeping need of
    a connection, over the build fake: the catalog's comments (one dict
    shared by every session of a rebuild, written by COMMENT ON TABLE,
    taken by DROP TABLE, read by obj_description), the policy the view
    shows, the source versions, and a statement to fail on."""

    def __init__(self, catalog, fail_on=None, shown=None, versions=None):
        super().__init__([])
        self.catalog, self.fail_on, self.shown, self.versions = catalog, fail_on, shown, versions

    def execute(self, statement, params=None):
        sql = str(statement)
        if self.fail_on and sql.lstrip().startswith(self.fail_on):
            self.statements.append(sql)
            raise RuntimeError("could not extend file: No space left on device")
        if sql.startswith("COMMENT ON TABLE "):
            self.statements.append(sql)
            self.catalog[sql.split()[3]] = params["stamp"]
            return _Result()
        if sql.lstrip().startswith("DROP TABLE IF EXISTS public."):
            self.catalog.pop(sql.split()[4], None)
        if "obj_description(to_regclass(:name)" in sql:
            self.statements.append(sql)
            return _Result(value=self.catalog.get(params["name"]))
        if "pg_get_viewdef" in sql:
            self.statements.append(sql)
            return _Result(value=shows(self.shown) if self.shown else None)
        if "FROM datamart_meta.ingest_runs" in sql:
            self.statements.append(sql)
            return _Result(rows=list(self.versions.items()))
        return super().execute(statement, params)


def sessions_of(monkeypatch, catalog, module=cg, versions_after=None, **connection) -> list[Session]:
    """Every session ``module``'s get_session opens, in order, each on a
    connection over the shared catalog and run by the real helper with its
    engine replaced; ``versions_after`` answers the source versions from
    the second session on."""
    opened: list[Session] = []

    def open_session(config=None):
        answers = dict(connection)
        if versions_after is not None and opened:
            answers["versions"] = versions_after
        session = Session(_Connection(catalog, **answers), state=catalog)
        session.config = config
        opened.append(session)
        return db.get_session(session=session)

    monkeypatch.setattr(module, "get_session", open_session)
    return opened


def hook(policy="v3", stamp=STAMP):
    return partial(gm.after_current_build, recovered_policy=policy, stamp=stamp)


# --- one transaction per relation ---------------------------------------------


def test_a_rebuild_builds_and_stamps_each_relation_in_a_transaction_of_its_own(monkeypatch):
    catalog = {}
    opened = sessions_of(monkeypatch, catalog)
    assert list(cg.build_current_relations({"host": "scratch"}, after=hook())) == TABLES
    assert [session.outcome for session in opened] == ["committed"] * len(TABLES)
    assert [session.config for session in opened] == [{"host": "scratch"}] * len(TABLES)   # the caller's database
    for session, table in zip(opened, TABLES):
        statements = session.connection().statements
        ddl = [sql for sql in statements if sql.lstrip().startswith(("DROP TABLE IF EXISTS public.",
                                                                     "CREATE TABLE public."))]
        assert [sql.split()[4 if sql.lstrip().startswith("DROP") else 2] for sql in ddl] == [f"public.{table}"] * 2
        # last, after the indexes, the ANALYZE and the count: nothing is stamped before it is complete
        assert statements[-1] == f"COMMENT ON TABLE public.{table} IS :stamp"
    assert json.loads(catalog["public.privategrants_current"]) == {
        "source_versions": VERSIONS, "shape": gm.MATCHING_INPUT_SHAPE_VERSION, "build_id": "build-1"}
    assert sorted(catalog) == sorted(f"public.{t}" for t in TABLES)
    assert gm._unstamped_current_relations(_Connection(catalog), VERSIONS) == []       # a restart takes them


def test_the_views_the_drop_takes_come_back_in_the_same_transaction(monkeypatch):
    catalog = {}
    opened = sessions_of(monkeypatch, catalog)
    cg.build_current_relations({}, after=hook("v3"))
    by_table = dict(zip(TABLES, opened))
    for table in cg._MATCHING_VIEW_TABLES:
        statements = by_table[table].connection().statements
        drop = next(i for i, sql in enumerate(statements)
                    if sql.lstrip().startswith(f"DROP TABLE IF EXISTS public.{table}"))
        views = [i for i, sql in enumerate(statements) if "VIEW " in sql]
        assert views and drop < min(views) < statements.index(f"COMMENT ON TABLE public.{table} IS :stamp")
        assert any("policy_version = 'v3'" in sql for sql in statements)   # the recovered view, for the policy read before
    for table in set(TABLES) - set(cg._MATCHING_VIEW_TABLES):
        assert not any("VIEW " in sql for sql in by_table[table].connection().statements)


def test_a_failure_leaves_each_relation_complete_and_stamped_or_untouched(monkeypatch):
    older = gm._current_stamp({**VERSIONS, "irs_990pf_grants": "2026_06_16"}, "build-0")
    catalog = {f"public.{t}": older for t in TABLES}                                   # the last run's stamps
    failing = TABLES[3]
    opened = sessions_of(monkeypatch, catalog, fail_on=f"CREATE TABLE public.{failing}")
    with pytest.raises(RuntimeError, match="No space left"):                           # match_records marks the build failed
        cg.build_current_relations({}, after=hook())
    assert [session.outcome for session in opened] == ["committed"] * 3 + ["rolled back"]
    assert not any(sql.startswith("COMMENT ON") for sql in opened[3].connection().statements)
    assert catalog[f"public.{failing}"] == older                                       # as it was: the DROP rolled back
    assert gm._unstamped_current_relations(_Connection(catalog), VERSIONS) == TABLES[3:]  # what the restart rebuilds


def test_a_rebuild_outside_a_run_stamps_nothing(monkeypatch):
    # the current_grants CLI and the own-extract release: a new table, no stamp, so the next run rebuilds it
    catalog = {f"public.{t}": STAMP for t in TABLES}
    opened = sessions_of(monkeypatch, catalog)
    counts = cg.build_current_relations({}, [cg._TABLES[-1]], after=hook(stamp=None))
    assert counts == {"privategrants_future_current": 16_648_094}
    assert len(opened) == 1 and not any(sql.startswith("COMMENT ON") for sql in opened[0].connection().statements)
    assert gm._unstamped_current_relations(_Connection(catalog), VERSIONS) == ["privategrants_future_current"]


# --- the stamp ----------------------------------------------------------------


def test_a_relation_stamped_from_another_drop_or_under_another_shape_is_rebuilt():
    catalog = {f"public.{t}": STAMP for t in TABLES}
    # an ingest since the rebuild: the prefix would name the new drop
    assert gm._unstamped_current_relations(
        _Connection(catalog), {**VERSIONS, "irs_990pf_grants": "2026_11_01"}) == TABLES
    # a dedup-rule change since the rebuild: the prefix names the new shape
    catalog["public.privategrants_current"] = json.dumps(
        {"source_versions": VERSIONS, "shape": gm.MATCHING_INPUT_SHAPE_VERSION - 1, "build_id": "build-0"})
    # a stamp from before the shape joined it
    catalog["public.basic_fields_ez_current"] = json.dumps({"source_versions": VERSIONS, "build_id": "build-0"})
    # sources refresh rebuilt one relation: a new table, no stamp
    del catalog["public.basic_fields_current"]
    assert gm._unstamped_current_relations(_Connection(catalog), VERSIONS) == [
        "basic_fields_current", "basic_fields_ez_current", "privategrants_current"]


@pytest.mark.parametrize("comment", [None, "", "built by hand", "[1, 2]", '{"build_id": "x"}'])
def test_anything_but_a_stamp_is_no_stamp(comment):
    assert gm._stamp_of(comment) is None
    assert gm._unstamped_current_relations(_Connection({f"public.{t}": comment for t in TABLES}), VERSIONS) == TABLES


def test_a_rebuild_of_the_paid_relation_takes_the_future_relation_with_it():
    assert [t for t, _ in cg.with_dependents(["privategrants_current"])] == [
        "privategrants_current", "privategrants_future_current"]
    assert [t for t, _ in cg.with_dependents(["basic_fields_pf_current", "basic_fields_current"])] == [
        "basic_fields_current", "basic_fields_pf_current"]
    assert cg.with_dependents([]) == []


# --- the run's database phases ------------------------------------------------


def run_inputs(monkeypatch, catalog, *, skip_rebuild=False, versions_after=None, after_build=None):
    """``_load_inputs`` with the sessions faked and the builds recorded:
    the calls in order (each build with the outcomes of the sessions open at
    the time, each hook with the statements it ran) and the sessions."""
    calls: list[tuple] = []
    opened = sessions_of(monkeypatch, catalog, module=gm, shown="v3", versions=VERSIONS, versions_after=versions_after)

    def build(config, tables, *, after):
        calls.append(("build", [t for t, _ in tables], [s.outcome for s in opened]))
        for table, _ in tables:                          # the hook, on a connection of its own, as the loop runs it
            connection = _Connection(catalog)
            after(connection, table)
            calls.append(("after", table, [sql.split()[0] for sql in connection.statements]))
        if after_build is not None:
            after_build()

    monkeypatch.setattr(gm, "datamart_config", lambda: {})
    monkeypatch.setattr(gm, "build_current_relations", build)
    monkeypatch.setattr(gm, "apply_build_memory", lambda connection: calls.append(("memory",)))
    monkeypatch.setattr(gm, "_load_corrections", lambda connection: calls.append(("corrections",)))
    monkeypatch.setattr(gm, "create_or_replace_views",
                        lambda connection, **kw: calls.append(("views", kw["recovered_policy"])))
    monkeypatch.setattr(gm, "recovered_digest",
                        lambda connection, policy: {"policy_version": policy, "rows": 5, "digest": "0a1b2c3d"})
    monkeypatch.setattr(gm, "_stamp_recovered", lambda build_id, recovered: calls.append(("recovered", build_id)))
    monkeypatch.setattr(pd, "read_sql_query", lambda sql, connection: calls.append(("read",)) or pd.DataFrame())
    universe, grants, prefix = gm._load_inputs(None, None, None, "build-1", skip_rebuild)
    return calls, opened, prefix


def test_a_run_reads_the_versions_once_rebuilds_between_its_sessions_and_reads_at_the_builds_budget(monkeypatch):
    catalog = {}
    calls, opened, prefix = run_inputs(monkeypatch, catalog)
    bookkeeping = opened[0].connection().statements
    assert not any(sql.lstrip().startswith(("CREATE", "DROP", "TRUNCATE", "COMMENT")) for sql in bookkeeping)
    assert calls[0] == ("build", TABLES, ["committed"])                   # after the bookkeeping committed, before the reads' session
    hooks = [c for c in calls if c[0] == "after"]
    assert [c[1] for c in hooks] == TABLES and all(c[2][-1] == "COMMENT" for c in hooks)
    assert calls.count(("views", "v3")) == len(cg._MATCHING_VIEW_TABLES) + 1    # in those relations' transactions, then once more
    assert calls[calls.index(("memory",)):] == [
        ("memory",), ("corrections",), ("views", "v3"), ("recovered", "build-1"), ("read",), ("read",)]
    assert [s.outcome for s in opened] == ["committed", "committed"]
    assert sum("obj_description" in sql for sql in opened[1].connection().statements) == len(TABLES)   # the stamps, checked again
    assert prefix.startswith(f"grant_matching_checkpoints/pg_{VERSIONS['irs_990pf_grants']}/")
    assert prefix.endswith(f"/rec_v3_0a1b2c3d/shape_v{gm.MATCHING_INPUT_SHAPE_VERSION}")


def test_skip_rebuild_takes_the_stamped_relations_and_rebuilds_the_rest_with_what_reads_them(monkeypatch):
    older = gm._current_stamp({**VERSIONS, "irs_990pf_grants": "2026_06_16"}, "build-0")
    catalog = {f"public.{t}": (STAMP if t in TABLES[:4] else older) for t in TABLES}    # stopped at privategrants_current
    calls, opened, _ = run_inputs(monkeypatch, catalog, skip_rebuild=True)
    assert calls[0] == ("build", ["privategrants_current", "privategrants_future_current"], ["committed"])
    assert calls.count(("read",)) == 2
    catalog = {f"public.{t}": STAMP for t in TABLES}                                       # a restart after a complete run
    calls, opened, _ = run_inputs(monkeypatch, catalog, skip_rebuild=True)
    assert not [c for c in calls if c[0] == "build"]
    assert calls[calls.index(("memory",)):] == [
        ("memory",), ("corrections",), ("views", "v3"), ("recovered", "build-1"), ("read",), ("read",)]


def test_a_source_ingested_during_the_rebuild_stops_the_run(monkeypatch):
    with pytest.raises(RuntimeError, match="ingested while the run rebuilt"):
        run_inputs(monkeypatch, {}, versions_after={**VERSIONS, "irs_990pf_grants": "2026_11_01"})


def test_a_relation_rebuilt_elsewhere_during_the_run_stops_the_run(monkeypatch):
    catalog = {}
    with pytest.raises(RuntimeError, match="basic_fields_current do not carry the stamp"):
        run_inputs(monkeypatch, catalog, after_build=lambda: catalog.pop("public.basic_fields_current"))
