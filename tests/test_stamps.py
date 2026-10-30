"""The stamps (docs/current-relations-stamp.md). A staging table's is written
where its rows become final: an ingest's success, the own-extract patch, the
scratch-host release. A ``_current`` relation's is written by the builder
last in the relation's transaction and names its inputs' stamps and its rules.
Then the states the stamps give, and the adoption of what was loaded and built
before them. The sessions are faked; nothing opens a database."""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("recordlinkage")

from givingtuesday_datamart import current_grants as cg  # noqa: E402
from givingtuesday_datamart import grant_matching as gm  # noqa: E402
from givingtuesday_datamart import ingestion  # noqa: E402
from givingtuesday_datamart._internal import db  # noqa: E402
from givingtuesday_datamart.own_extract import patch, release  # noqa: E402
from test_current_stamp import VERSIONS, _Connection, sessions_of  # noqa: E402
from test_grant_matching import Session  # noqa: E402
from test_pf_current_rules import _Result  # noqa: E402

STAGING = sorted({n for inputs in cg._INPUTS.values() for n in inputs if n not in cg._INPUTS})


def staged(**overrides) -> dict:
    """A catalog in which every staging input carries the stamp of a run of its own."""
    catalog = {f"public.{n}": json.dumps(ingestion.staging_stamp(f"run-{n}", "2026_06_16")) for n in STAGING}
    catalog.update(overrides)
    return catalog


def stamp_in(catalog, name):
    return json.loads(catalog[f"public.{name}"])


@pytest.fixture(autouse=True)
def default_budget(monkeypatch):
    for name in ("GT_BUILD_WORK_MEM", "GT_BUILD_MAINTENANCE_WORK_MEM", "GT_BUILD_PARALLEL_WORKERS"):
        monkeypatch.delenv(name, raising=False)


# --- what a relation is built from ---------------------------------------------


def test_the_inputs_are_what_each_ddl_reads():
    for table, ddl in cg._TABLES:
        assert set(re.findall(r"(?:FROM|JOIN)\s+public\.(\w+)", ddl)) - {table} == set(cg._INPUTS[table]), table
    assert set(cg._INPUTS) == {t for t, _ in cg._TABLES}
    assert cg._READS_CURRENT == {t: n for t, inputs in cg._INPUTS.items() for n in inputs if n in cg._INPUTS}
    assert cg._PATCH_RUNS == patch.PATCH


def test_a_build_locks_its_inputs_first_and_stamps_the_relation_with_them_last(monkeypatch):
    catalog = staged()
    opened = sessions_of(monkeypatch, catalog)
    cg.build_current_relations({}, built_by="sources refresh")
    for session, (table, _ddl) in zip(opened, cg._TABLES):
        statements = session.connection().statements
        lock = f"LOCK TABLE {', '.join(f'public.{n}' for n in cg._INPUTS[table])} IN ACCESS SHARE MODE"
        drop = next(i for i, sql in enumerate(statements)
                    if sql.lstrip().startswith(f"DROP TABLE IF EXISTS public.{table}"))
        assert statements.index(lock) < drop                                        # locked before anything is dropped
        assert statements[-1] == f"COMMENT ON TABLE public.{table} IS :stamp"       # last: only a complete relation is stamped
        stamp = stamp_in(catalog, table)
        assert (stamp["stamp"], stamp["ddl"], stamp["built_by"]) == (1, cg.ddl_hash(table), "sources refresh")
        assert stamp["inputs"] == {n: {"id": stamp_in(catalog, n)["id"]} if n in cg._INPUTS else stamp_in(catalog, n)
                                   for n in cg._INPUTS[table]}
    assert len({stamp_in(catalog, t)["id"] for t, _ in cg._TABLES}) == len(cg._TABLES)


# --- the states -------------------------------------------------------------------


def test_the_states_follow_the_stamps(monkeypatch):
    catalog = staged()
    sessions_of(monkeypatch, catalog)
    assert {state for state, _ in cg.relation_states(_Connection(catalog)).values()} == {"stale"}   # nothing built yet
    cg.build_current_relations({})
    assert {t: s for t, (s, _) in cg.relation_states(_Connection(catalog)).items()} == {t: "current" for t, _ in cg._TABLES}
    # a reload of privategrants: the paid relation and the future one that reads it are stale, nothing else
    catalog["public.privategrants"] = json.dumps(ingestion.staging_stamp("run-2", "2026_10_25"))
    states = cg.relation_states(_Connection(catalog))
    assert {t for t, (s, _) in states.items() if s != "current"} == {"privategrants_current", "privategrants_future_current"}
    assert states["privategrants_current"] == ("stale", "built from another privategrants")
    assert states["privategrants_future_current"] == ("stale", "built from another privategrants_current")
    # a load of basic_fields_pf under way, its new table without a stamp: what reads it is blocked, not stale
    del catalog["public.basic_fields_pf"]
    states = cg.relation_states(_Connection(catalog))
    assert states["basic_fields_pf_current"] == ("blocked", "basic_fields_pf has no stamp")
    assert states["privategrants_future_current"] == ("blocked",
                                                      "basic_fields_pf has no stamp; privategrants_current is blocked")
    assert states["basic_fields_current"] == ("current", "")


def test_a_change_of_rules_makes_the_relations_built_under_the_old_ones_stale(monkeypatch):
    catalog = staged()
    sessions_of(monkeypatch, catalog)
    cg.build_current_relations({}, cg.basic_fields_relations())
    monkeypatch.setitem(cg._INDEX_DDL, "basic_fields_pf_current",
                        [*cg._INDEX_DDL["basic_fields_pf_current"], "CREATE INDEX ON public.basic_fields_pf_current (url)"])
    states = cg.relation_states(_Connection(catalog))
    assert states["basic_fields_pf_current"] == ("stale", "built under other rules")
    assert states["basic_fields_current"] == ("current", "")


def test_a_relation_the_matcher_stamped_before_the_builder_did_is_stale():
    catalog = staged(**{"public.basic_fields_current": json.dumps(gm._current_stamp(VERSIONS, "build-0"))})
    assert cg.relation_states(_Connection(catalog))["basic_fields_current"] == (
        "stale", "no stamp: missing, or not built since the stamps")


# --- the staging stamps -------------------------------------------------------------


def test_an_ingest_stamps_its_table_in_the_transaction_that_records_its_success(monkeypatch):
    catalog, opened = {}, []

    def open_session(config=None):
        opened.append(Session(_Connection(catalog), state=catalog))
        return db.get_session(session=opened[-1])

    monkeypatch.setattr(ingestion, "get_session", open_session)
    monkeypatch.setattr(ingestion, "datamart_config", lambda: {})
    stamp = ingestion.staging_stamp("run-1", "2026_06_16")
    ingestion._finalize_run(run_id="run-1", finished_at=datetime.now(timezone.utc), status="success",
                            stamp=("public.basic_fields", stamp))
    statements = opened[0].connection().statements
    assert statements[0].lstrip().startswith("UPDATE datamart_meta.ingest_runs")
    assert statements[-1] == "COMMENT ON TABLE public.basic_fields IS :stamp"
    assert len(opened) == 1 and opened[0].outcome == "committed" and stamp_in(catalog, "basic_fields") == stamp


@dataclass
class _File:
    url: str


def ingest(monkeypatch, validation_fails=False):
    """``ingest_source`` with the load and the run records faked: its result
    and the keywords of every ``_finalize_run`` call."""
    finals = []

    def validate(**kw):
        if validation_fails:
            raise ingestion.ValidationError("too few rows", results=[])
        return [], ["filerein"]

    monkeypatch.setattr(ingestion, "ensure_meta_schema", lambda: None)
    monkeypatch.setattr(ingestion, "find_successful_run", lambda name, version: None)
    monkeypatch.setattr(ingestion, "_insert_started_run", lambda **kw: None)
    monkeypatch.setattr(ingestion, "_files_of", lambda resolved: (_File(url="https://example.org/bf.csv"),))
    monkeypatch.setattr(ingestion, "datamart_config", lambda: {})
    monkeypatch.setattr(ingestion, "stream_csv_url_to_table", lambda **kw: SimpleNamespace(rows=10, bytes=100, sha256="ab"))
    monkeypatch.setattr(ingestion, "_apply_post_ingest_dedupe", lambda spec, files, config: 0)
    monkeypatch.setattr(ingestion, "_apply_post_ingest_indexes", lambda spec, config: None)
    monkeypatch.setattr(ingestion, "validate_ingest", validate)
    monkeypatch.setattr(ingestion, "_finalize_run", lambda **kw: finals.append(kw))
    spec = SimpleNamespace(logical_name="irs_990_basic_fields", staging_table_name="public.basic_fields",
                           spool_to_disk=False)
    return ingestion.ingest_source(spec, SimpleNamespace(version_date="2026_06_16", url="https://example.org/bf.csv")), finals


def test_a_successful_ingest_has_its_table_stamped_and_a_failed_one_does_not(monkeypatch):
    result, finals = ingest(monkeypatch)
    assert result.status == "success"
    assert finals[-1]["stamp"] == ("public.basic_fields", ingestion.staging_stamp(result.run_id, "2026_06_16"))
    result, finals = ingest(monkeypatch, validation_fails=True)
    assert result.status == "failed" and "stamp" not in finals[-1]          # its new table has none: blocked until reloaded


def test_the_patch_records_its_run_on_a_stamped_table_and_leaves_an_unstamped_one_to_adoption():
    catalog = staged()
    connection = _Connection(catalog)
    assert patch.stamp_patched(connection, "public.privategrants", "patch-1")
    assert stamp_in(catalog, "privategrants") == ingestion.staging_stamp("run-privategrants", "2026_06_16", "patch-1")
    del catalog["public.basic_fields"]
    assert not patch.stamp_patched(connection, "public.basic_fields", "patch-1") and "public.basic_fields" not in catalog


class _PatchSession(_Connection):
    """The patch's session: row counts for its statements, and the runs it records."""

    def __init__(self, catalog):
        super().__init__(catalog)
        self.runs = []

    def execute(self, statement, params=None):
        if "INSERT INTO datamart_meta.ingest_runs" in str(statement):
            self.runs.append(params)
        result = super().execute(statement, params)
        result.rowcount = 3
        return result


def test_the_patch_stamps_each_table_with_the_run_it_records_for_it(monkeypatch):
    monkeypatch.setattr(patch.extract, "load", lambda: SimpleNamespace(sha256="c" * 64))
    monkeypatch.setattr(patch, "batch_runs", lambda session, sha, source: [
        {"batch": "2026_TEOS_XML_01A", "run_id": "load-1", "source_url": "s3://archive/2026_TEOS_XML_01A.zip"}])
    monkeypatch.setattr(patch, "staging_columns", lambda session, table, source, target: ["filerein"])
    monkeypatch.setattr(patch, "delete_sql", lambda table, source, target: ("DELETE ours", "DELETE theirs"))
    monkeypatch.setattr(patch, "insert_sql", lambda table, columns, source, target: "INSERT ours")
    catalog = staged()
    session = _PatchSession(catalog)
    patch.apply(session, tables=("privategrants", "basic_fields"))
    assert [(r["table"], r["id"]) for r in session.runs] == [
        (f"public.{t}", stamp_in(catalog, t)["patch_run_id"]) for t in ("privategrants", "basic_fields")]
    assert stamp_in(catalog, "basic_fields")["run_id"] == "run-basic_fields"   # the ingest run it sits on, kept


def test_a_release_loaded_on_a_scratch_host_is_stamped_with_its_run(monkeypatch):
    catalog, lineage = {}, {}

    def stream(**kw):
        lineage.update(kw["extra_columns"])
        return SimpleNamespace(rows=10, bytes=100, sha256="ab" * 32)

    monkeypatch.setattr(release, "stream_csv_url_to_table", stream)
    monkeypatch.setattr(release, "_apply_post_ingest_indexes", lambda spec, config: None)
    opened = sessions_of(monkeypatch, catalog, module=release)
    release.load_table({"postgres": {"host": "scratch"}}, "2026_10_25", "privategrants", "https://example.org/pf.csv")
    assert stamp_in(catalog, "privategrants") == ingestion.staging_stamp(lineage["_ingest_run_id"], "2026_10_25")
    assert opened[0].outcome == "committed"
    assert opened[0].connection().statements[-1] == "COMMENT ON TABLE public.privategrants IS :stamp"


# --- the adoption -------------------------------------------------------------------


class _Adopt(_Connection):
    """The adoption's reads: the table's runs, and what a sample of its rows carries."""

    def __init__(self, catalog, runs=(), seen=(), first=(), **kw):
        super().__init__(catalog, **kw)
        self.runs, self.seen, self.first = list(runs), list(seen), list(first)

    def execute(self, statement, params=None):
        sql = str(statement)
        if "WHERE staging_table = :name" in sql:
            self.statements.append(sql)
            return _Result(rows=self.runs)
        if "_ingest_run_id = :run" in sql:
            self.statements.append(sql)
            return _Result(rows=self.first if "LIMIT 1000" in sql else self.seen)
        return super().execute(statement, params)


INGEST = ("run-1", "irs_990pf_grants", "2026_06_16", "success")
PATCHED = ("patch-1", "irs_extract_patch", "irs_extract@abc", "success")


@pytest.mark.parametrize("runs, seen, patch_run, says", [
    ([INGEST], [(True, False, 120)], None, "adopted run run-1 (2026_06_16); 120 rows checked"),
    ([INGEST, PATCHED], [(True, False, 90), (False, True, 30)], "patch-1", "patch run patch-1; 120 rows checked"),
    ([INGEST, ("run-2", "irs_990pf_grants", "2026_10_25", "failed")], [], None, "its last ingest, run run-2, is failed"),
    ([INGEST, ("run-2", "irs_990pf_grants", "2026_10_25", "started")], [], None, "is started"),
    ([INGEST], [(True, False, 100), (False, False, 3)], None, "3 of the rows read carry another run than run-1"),
    ([INGEST], [(True, False, 90), (False, True, 30)], None, "30 of the rows read"),     # patched rows, no patch run
    ([], [], None, "no ingest run"),
])
def test_adoption_takes_a_tables_last_successful_ingest_when_its_rows_agree(runs, seen, patch_run, says):
    catalog = {}
    line = cg.adopt_staging(_Adopt(catalog, runs=runs, seen=seen), "public.privategrants")
    assert says in line
    if "NOT adopted" in line:
        assert "public.privategrants" not in catalog
    else:
        assert stamp_in(catalog, "privategrants") == ingestion.staging_stamp("run-1", "2026_06_16", patch_run,
                                                                             adopted=True)


def test_adoption_samples_pages_reads_a_small_tables_first_rows_and_with_verify_every_row():
    connection = _Adopt({}, runs=[INGEST], first=[(True, False, 7)])
    assert "7 rows checked" in cg.adopt_staging(connection, "public.privategrants")
    sampled, first = [sql for sql in connection.statements if "_ingest_run_id = :run" in sql]
    assert "TABLESAMPLE SYSTEM (0.1)" in sampled and "LIMIT 1000" in first
    connection = _Adopt({}, runs=[INGEST], seen=[(True, False, 120)])
    cg.adopt_staging(connection, "public.privategrants", verify=True)
    (read,) = [sql for sql in connection.statements if "_ingest_run_id = :run" in sql]
    assert "TABLESAMPLE" not in read and "LIMIT" not in read
    catalog = {"public.privategrants": json.dumps(ingestion.staging_stamp("run-0", "2026_06_16"))}
    assert cg.adopt_staging(_Adopt(catalog, runs=[INGEST]), "public.privategrants") == "public.privategrants: stamped already"


def test_adoption_stamps_the_basic_fields_relations_the_scan_finds_built_from_their_staging_run():
    catalog = staged()
    del catalog["public.basic_fields_ez"]
    connection = _Connection(catalog, stale={"basic_fields_pf_current"})
    assert cg.adopt_relations(connection) == [
        "public.basic_fields_current: adopted",
        "public.basic_fields_pf_current: NOT adopted: built from another run; the canonical build rebuilds it",
        "public.basic_fields_ez_current: NOT adopted: its staging table has no stamp; the canonical build rebuilds it"]
    stamp = stamp_in(catalog, "basic_fields_current")
    assert stamp["adopted"] and stamp["inputs"] == {"basic_fields": stamp_in(catalog, "basic_fields")}
    assert cg.relation_states(connection)["basic_fields_current"] == ("current", "")


# --- the command line ---------------------------------------------------------------


def test_the_command_line_prints_the_states_and_exits_1_unless_every_one_is_current(monkeypatch, capsys):
    catalog = staged()
    sessions_of(monkeypatch, catalog)
    monkeypatch.setattr(cg, "datamart_config", lambda: {})
    cg.build_current_relations({})
    assert cg.main(["--check"]) == 0
    assert capsys.readouterr().out.count(" current") == len(cg._TABLES)
    del catalog["public.basic_fields_ez"]
    assert cg.main(["--check"]) == 1
    assert "basic_fields_ez_current" in (out := capsys.readouterr().out) and "blocked  basic_fields_ez has no stamp" in out


def test_the_command_line_adopts_each_table_in_a_transaction_of_its_own_then_the_relations(monkeypatch, capsys):
    calls = []
    opened = sessions_of(monkeypatch, {})
    monkeypatch.setattr(cg, "datamart_config", lambda: {})
    monkeypatch.setattr(cg, "adoptable_staging_tables", lambda connection: ["public.basic_fields", "public.privategrants"])
    monkeypatch.setattr(cg, "adopt_staging", lambda connection, table, verify: calls.append((table, verify))
                        or f"{table}: adopted")
    monkeypatch.setattr(cg, "adopt_relations", lambda connection: ["public.basic_fields_current: adopted"])
    assert cg.main(["--adopt", "--verify"]) == 0
    assert calls == [("public.basic_fields", True), ("public.privategrants", True)]
    assert len(opened) == 4 and all(s.outcome == "committed" for s in opened)
    assert all(s.connection().settings["max_parallel_workers_per_gather"] == "0" for s in opened[1:])   # the budget
    assert capsys.readouterr().out.splitlines() == [
        "public.basic_fields: adopted", "public.privategrants: adopted", "public.basic_fields_current: adopted"]
