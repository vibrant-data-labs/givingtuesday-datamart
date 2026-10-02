"""The 990-PF line-item rule of ``current_grants`` on small fixtures: the
DDL ``_pf_current_ddl`` renders, run as it ships against temp tables, in its
paid and its future form.

Each case is one filer-year under its own EIN, so one build a side answers
them all. The tables are the session's own (``pg_temp``): nothing under
``public`` is read or written. The cases need Postgres (``ctid``, ``DISTINCT
ON``, the regex operator), carry the ``db`` marker and are skipped where the
datamart cannot be reached within a few seconds: ``pytest -m "not db"`` runs
the rest, the tests of the rendered text at the end among them, without a
database.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import pytest
from sqlalchemy import text

from givingtuesday_datamart import current_grants as cg

PAID, FUTURE = "paid", "future"
# side -> (content columns, every column, the recipient-name column, the amount column, the purpose column)
COLUMNS = {
    PAID: (cg._PF_CONTENT_COLS, cg._PF_ALL_COLS, "sigocpyrbnbn1", "sigocpyamoun", "sigocpypogoc"),
    FUTURE: (cg._PF_FUTURE_CONTENT_COLS, cg._PF_FUTURE_ALL_COLS, "sigocaffrbnb1", "sigocaffamou", "sigocaffpogo"),
}
A, B = ("ALPHA FUND", 1000), ("BETA TRUST", 250)
EMPTY = ("", 0)                               # the nameless $0 row of a filing with no grants
# The per-field fill: the real group, and the row a group that holds only the rest of a text comes out as.
# One name, one amount, another purpose: two tuples
A1, A2 = ("ALPHA FUND", 1000, "GENERAL SUPPORT OF THE"), ("ALPHA FUND", 1000, "MISSION")
NAMED_ZERO = ("GAMMA SOCIETY", 0)             # a group of its own that names a recipient and pays nothing


@dataclass(frozen=True)
class Case:
    """One filer-year: its rows in the extract, its ``basic_fields_pf`` rows
    and line 25, and what each relation must keep of it."""
    name: str
    rows: tuple                               # (recipient, amount[, purpose]), in the extract's order
    paid: tuple                               # (rows kept, dedup_rule) in the paid relation
    future: tuple                             # the same in the future one
    line25_d: int = 0                         # Part I line 25 column (d), in cash
    line25_a: int | None = None               # column (a), per books: (d) unless given
    basic_shas: tuple = ("s1",)               # one basic row per sha named: ("s1", "s1") is a repeated row
    older_url_rows: tuple = field(default=())  # rows under an earlier url of the filer-year
    older_line25: int | None = None           # line 25 of that earlier version, in a basic row whose sha sorts first


CASES = (
    Case("a doubled block", (A, B, A, B), (2, "pair_collapse"), (2, "pair_collapse"),
         line25_d=1250, basic_shas=("s1", "s1")),
    Case("a doubled block with a legitimate repeat", (A, A, B, A, A, B), (3, "pair_collapse"), (3, "pair_collapse"),
         line25_d=2250, basic_shas=("s1", "s1")),
    # line 25 is 0 while the filing itemizes (Okumura): the repeated row decides, whatever line 25 says
    Case("a doubled block whose line 25 is 0", (A, B, A, B), (2, "pair_collapse"), (2, "pair_collapse"),
         line25_d=0, basic_shas=("s1", "s1")),
    # no repeated row, no halving, even where line 25 is half the block: the line-25 test was taken out on
    # 2026-10-01, and with it the one difference between the paid and the future pair_collapse. None is known
    Case("an even block whose basic row is single", (A, B, A, B), (4, "passthrough"), (4, "passthrough"),
         line25_d=1250),
    Case("an amended copy under the original url", (A, B, A, B), (2, "pair_collapse"), (2, "pair_collapse"),
         line25_d=1250, basic_shas=("s1", "s2")),
    Case("a doubled empty row", (EMPTY, EMPTY), (1, "pair_collapse"), (1, "pair_collapse"),
         basic_shas=("s1", "s1")),
    Case("a doubled single-grant filing", (A, A), (1, "pair_collapse"), (1, "pair_collapse"),
         line25_d=1000, basic_shas=("s1", "s1")),
    # forward fill: paid only. The future rows stay as they are
    Case("a forward fill, N even", (A, A, A, A), (1, "forward_fill"), (4, "passthrough"), line25_d=1000),
    Case("a forward fill, N odd", (A, A, A), (1, "forward_fill"), (3, "passthrough"), line25_d=1000),
    Case("a forward fill, N = 2", (A, A), (1, "forward_fill"), (2, "passthrough"), line25_d=1000),
    Case("a forward fill line 25 states per books only", (A, A, A), (1, "forward_fill"), (3, "passthrough"),
         line25_d=0, line25_a=1000),
    Case("a forward fill in a doubled filing", (A, A, A, A, A, A), (1, "pair_collapse+forward_fill"),
         (3, "pair_collapse"), line25_d=1000, basic_shas=("s1", "s1")),
    Case("a legitimate repeat", (A, A), (2, "passthrough"), (2, "passthrough"), line25_d=2000),
    Case("a legitimate repeat, N odd", (A, A, A), (3, "passthrough"), (3, "passthrough"), line25_d=3000),
    Case("a legitimate repeat in a doubled filing", (A, A, A, A), (2, "pair_collapse"), (2, "pair_collapse"),
         line25_d=2000, basic_shas=("s1", "s1")),
    Case("a repeat among singletons", (A, A, A, B), (4, "passthrough"), (4, "passthrough"), line25_d=3250),
    Case("an odd block under a repeated row", (A, A, A, B), (4, "passthrough"), (4, "passthrough"),
         line25_d=3250, basic_shas=("s1", "s1")),
    Case("a single grant", (A,), (1, "passthrough"), (1, "passthrough"), line25_d=1000),
    Case("two versions of the return", (A, B), (2, "latest_url"), (2, "latest_url"), line25_d=1250,
         older_url_rows=(A, B, B)),
    # line 25 is the kept version's own. The original declared one grant, the amended return two equal ones:
    # read from the original (its sha sorts first) the amended list would lose a row
    Case("an amended list of two equal grants", (A, A), (2, "latest_url"), (2, "latest_url"), line25_d=2000,
         older_url_rows=(A,), older_line25=1000),
    # and the other way: the kept version is the forward fill, whatever the earlier one declared
    Case("a forward fill whose earlier version declares another total", (A, A), (1, "latest_url+forward_fill"),
         (2, "latest_url"), line25_d=1000, older_url_rows=(B,), older_line25=250),
    # a kept url with no basic row states no line 25, and another version's is not borrowed
    Case("a repeat whose kept version has no basic row", (A, A), (2, "latest_url"), (2, "latest_url"),
         basic_shas=(), older_url_rows=(A,), older_line25=1000),
    # field fill: paid only, as forward fill is. The copies of one grant differ in a field, or rows without an
    # amount stand beside them, so the block is not one tuple. Line 25 states the grant once
    Case("a field fill", (A1, A2), (1, "field_fill"), (2, "passthrough"), line25_d=1000),
    Case("a field fill, a text over three groups", (A1, A2, A2), (1, "field_fill"), (3, "passthrough"),
         line25_d=1000),
    Case("a field fill line 25 states per books only", (A1, A2), (1, "field_fill"), (2, "passthrough"),
         line25_d=0, line25_a=1000),
    # the rows without an amount are groups of their own and stay: one copy of the grant beside them
    Case("a field fill beside a $0 row", (A1, A2, EMPTY, A2), (2, "field_fill"), (4, "passthrough"), line25_d=1000),
    Case("identical copies beside a named $0 row", (NAMED_ZERO, A, A, A), (2, "field_fill"), (4, "passthrough"),
         line25_d=1000),
    # after the halving, on what the filing itself holds
    Case("a field fill in a doubled filing", (A1, A2, EMPTY, A1, A2, EMPTY), (2, "pair_collapse+field_fill"),
         (3, "pair_collapse"), line25_d=1000, basic_shas=("s1", "s1")),
    # one grant and a $0 group, in the extract twice: the filing holds the amount once, and halving is all there is
    Case("a doubled single grant beside a $0 row", (NAMED_ZERO, A, NAMED_ZERO, A), (2, "pair_collapse"),
         (2, "pair_collapse"), line25_d=1000, basic_shas=("s1", "s1")),
    Case("a field fill whose earlier version declares another total", (A1, A2), (1, "latest_url+field_fill"),
         (2, "latest_url"), line25_d=1000, older_url_rows=(B,), older_line25=250),
    # what the field fill must leave alone
    Case("a legitimate repeat whose copies differ", (A1, A2), (2, "passthrough"), (2, "passthrough"), line25_d=2000),
    Case("one amount under two names", (A, ("BETA TRUST", 1000)), (2, "passthrough"), (2, "passthrough"),
         line25_d=1000),
    Case("one name under two amounts", (A1, ("ALPHA FUND", 250, "MISSION")), (2, "passthrough"), (2, "passthrough"),
         line25_d=1000),
    Case("one grant beside a $0 row", (A, NAMED_ZERO), (2, "passthrough"), (2, "passthrough"), line25_d=1000),
)
TAX_YEAR = "2024"
# A filer-year whose paid rows are held under a later url than its future rows
LEFT_BEHIND = "900000099"


def _ein(index: int) -> str:
    return f"9{index:08d}"


def _url(ein: str, version: int = 2) -> str:
    return f"https://example.test/{ein}_v{version}_public.xml"


def _grant_rows(side: str) -> list[dict]:
    _content, every, name, amount, purpose = COLUMNS[side]
    rows = []
    for index, case in enumerate(CASES):
        ein = _ein(index)
        for version, grants in ((1, case.older_url_rows), (2, case.rows)):
            for recipient, dollars, *text_of in grants:
                row = dict.fromkeys(every, None)
                row.update(filerein=ein, taxyear=TAX_YEAR, url=_url(ein, version), filername1=case.name,
                           filesha256=f"s{version - 1}", **{name: recipient, amount: str(dollars)},
                           **{purpose: text_of[0] for _ in text_of})
                rows.append(row)
    if side == FUTURE:
        row = dict.fromkeys(every, None)
        row.update(filerein=LEFT_BEHIND, taxyear=TAX_YEAR, url=_url(LEFT_BEHIND, 1), **{name: A[0], amount: "1000"})
        rows.append(row)
    return rows


def _basic_rows() -> list[dict]:
    """``basic_fields_pf``: the kept url's rows, and the earlier version's where the case has one. One
    ``_ingested_at`` throughout, as in the table: a refresh replaces it in one run."""
    rows = []
    for index, case in enumerate(CASES):
        ein = _ein(index)
        versions = [(_url(ein), sha, case.line25_d, case.line25_a) for sha in case.basic_shas]
        if case.older_line25 is not None:
            versions.append((_url(ein, 1), "s0", case.older_line25, None))
        rows += [dict(filerein=ein, taxyear=TAX_YEAR, url=url, filesha256=sha, arecgpdcprps=str(in_cash),
                      arecprexpnss=str(in_cash if per_books is None else per_books), _ingested_at="2026-06-16")
                 for url, sha, in_cash, per_books in versions]
    return rows


def _load(session, table: str, columns: list[str], rows: list[dict]) -> None:
    session.execute(text(f"CREATE TEMP TABLE {table} ({', '.join(f'{c} text' for c in columns)})"))
    session.execute(text(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(f':{c}' for c in columns)})"),
                    rows)


def _build(session, side: str) -> dict[str, list]:
    """The relation ``side``'s DDL builds from the fixtures: its rows by EIN."""
    content, every, _name, amount, _purpose = COLUMNS[side]
    _load(session, f"t_{side}_src", every, _grant_rows(side))
    differences = ({"line25_amount": amount, "recipient_cols": cg._PF_NAME_COLS} if side == PAID
                   else {"paid": "pg_temp.t_paid_held"})
    ddl = cg._pf_current_ddl(table=f"pg_temp.t_{side}_out", source=f"pg_temp.t_{side}_src", content_cols=content,
                             all_cols=every, basic="pg_temp.t_basic", **differences)
    for statement in [s for s in ddl.split(";") if s.strip()]:
        session.execute(text(statement))
    kept: dict[str, list] = {}
    for row in session.execute(text(f"SELECT * FROM pg_temp.t_{side}_out")).mappings():
        kept.setdefault(row["filerein"], []).append(row)
    return kept


CONNECT_SECONDS = "5"           # libpq's connect timeout: an unreachable database is a skip, not a wait
STATEMENT_TIMEOUT = "30s"       # no statement here takes one second; behind a busy database it fails, not hangs


@pytest.fixture(scope="module")
def built():
    """Both relations over the fixtures, and the watch over the paid one, in
    one session that leaves nothing behind."""
    before = os.environ.get("PGCONNECT_TIMEOUT")
    os.environ["PGCONNECT_TIMEOUT"] = CONNECT_SECONDS             # read by libpq when the helper's engine connects
    try:
        from givingtuesday_datamart._internal.db import get_session
        from givingtuesday_datamart.ingestion import datamart_config
        manager = get_session(config=datamart_config())
        session = manager.__enter__()
        session.execute(text(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'"))
    except Exception as error:                                   # no config, no network: nothing to run against
        pytest.skip(f"the datamart's Postgres is not reachable: {type(error).__name__}")
    finally:
        os.environ.pop("PGCONNECT_TIMEOUT") if before is None else os.environ.update(PGCONNECT_TIMEOUT=before)
    try:
        _load(session, "t_basic", ["filerein", "taxyear", "url", "filesha256", "arecgpdcprps", "arecprexpnss",
                                   "_ingested_at"], _basic_rows())
        # what the future build reads of the paid relation: the url each filer-year is held under
        _load(session, "t_paid_held", ["filerein", "taxyear", "url"],
              [dict(filerein=_ein(i), taxyear=TAX_YEAR, url=_url(_ein(i))) for i in range(len(CASES))]
              + [dict(filerein=LEFT_BEHIND, taxyear=TAX_YEAR, url=_url(LEFT_BEHIND, 2))])
        found = {side: _build(session, side) for side in (PAID, FUTURE)}
        found["watch"] = cg.unhalved_doubles(session, table="pg_temp.t_paid_out", basic="pg_temp.t_basic")
        yield found
    finally:
        session.rollback()
        manager.__exit__(None, None, None)


@pytest.mark.db
@pytest.mark.parametrize("side", (PAID, FUTURE))
@pytest.mark.parametrize("index", range(len(CASES)), ids=[case.name for case in CASES])
def test_a_filer_year_keeps_the_rows_the_rule_gives_it(built, side, index):
    case = CASES[index]
    rows, rule = getattr(case, side)
    kept = built[side].get(_ein(index), [])
    assert (len(kept), {row["dedup_rule"] for row in kept}) == (rows, {rule})
    assert {row["url"] for row in kept} == {_url(_ein(index))}                  # the latest url, and no other


@pytest.mark.db
@pytest.mark.parametrize("side", (PAID, FUTURE))
def test_halving_keeps_half_of_each_tuple_and_a_forward_fill_one_row(built, side):
    name, amount = COLUMNS[side][2:4]
    by_case = {case.name: built[side][_ein(index)] for index, case in enumerate(CASES)}
    recipients = lambda case: sorted(row[name] for row in by_case[case])           # noqa: E731
    assert recipients("a doubled block") == ["ALPHA FUND", "BETA TRUST"]
    assert recipients("a doubled block with a legitimate repeat") == ["ALPHA FUND", "ALPHA FUND", "BETA TRUST"]
    assert recipients("a legitimate repeat in a doubled filing") == ["ALPHA FUND", "ALPHA FUND"]
    assert [row[amount] for row in by_case["a doubled empty row"]] == ["0"]
    filled = by_case["a forward fill, N odd"]
    assert [row[amount] for row in filled] == (["1000"] if side == PAID else ["1000"] * 3)


@pytest.mark.db
def test_a_field_fill_keeps_the_first_copy_of_the_grant_and_the_rows_without_an_amount(built):
    name, amount, purpose = COLUMNS[PAID][2:]
    by_case = {case.name: built[PAID][_ein(index)] for index, case in enumerate(CASES)}
    kept = lambda case: sorted((row[name], row[amount], row[purpose]) for row in by_case[case])  # noqa: E731
    first = tuple(str(value) for value in A1)                   # the real group's own row, purpose and all
    assert kept("a field fill") == kept("a field fill, a text over three groups") == [first]
    assert kept("a field fill beside a $0 row") == [("", "0", None), first]
    one_and_the_named_zero = [("ALPHA FUND", "1000", None), ("GAMMA SOCIETY", "0", None)]
    assert kept("identical copies beside a named $0 row") == one_and_the_named_zero
    # under a repeated row the $0 rows are halved with the rest, and the grant is still held once
    assert kept("a field fill in a doubled filing") == [("", "0", None), first]
    assert kept("a doubled single grant beside a $0 row") == one_and_the_named_zero
    # the future relation has no fill rule: its rows are as the extract has them
    assert len(built[FUTURE][_ein([case.name for case in CASES].index("a field fill beside a $0 row"))]) == 4


@pytest.mark.db
@pytest.mark.parametrize("side", (PAID, FUTURE))
def test_every_row_carries_its_provenance_after_the_files_columns(built, side):
    every = COLUMNS[side][1]
    row = built[side][_ein(0)][0]
    assert list(row)[:len(every)] == every                          # staging's columns, in staging's order
    assert list(row)[len(every):] == ["n_urls_for_year", "n_filings_for_year", "dedup_rule"]
    by_case = {case.name: built[side][_ein(index)][0] for index, case in enumerate(CASES)}
    assert by_case["an amended copy under the original url"]["n_filings_for_year"] == 2
    assert by_case["two versions of the return"]["n_urls_for_year"] == 2
    assert by_case["a doubled block"]["n_filings_for_year"] == 1    # a repeated row is one filing, twice


@pytest.mark.db
def test_the_future_relation_leaves_out_a_filer_year_the_paid_one_holds_under_a_later_url(built):
    assert LEFT_BEHIND not in built[FUTURE]
    assert len(built[FUTURE]) == len(CASES)                         # and every other filer-year is there


@pytest.mark.db
def test_the_watch_finds_the_one_block_the_old_line_25_test_would_have_halved(built):
    # every tuple even, more than one tuple, no repeated row, half the block on line 25: held whole, and reported
    names = {_ein(index): case.name for index, case in enumerate(CASES)}
    assert [(names[ein], year) for ein, year in built["watch"]] == [("an even block whose basic row is single", TAX_YEAR)]
    # a legitimate repeat is one tuple, and a block already halved or filled is not whole: neither is reported


# ---------------------------------------------------------------------------
# The rendered text: no database
# ---------------------------------------------------------------------------

# What the two relations share, each written once in ``_pf_current_ddl``.
SHARED = (
    "SELECT DISTINCT ON (filerein, taxyear)\n           filerein, taxyear, url,",        # the kept url
    "ORDER BY filerein, taxyear, url DESC",
    "CREATE TEMP TABLE _pf_repeated AS",                                                 # the repeated-row signal
    "HAVING COUNT(*) > 1;",
    "PARTITION BY h.filerein, h.taxyear, h._h ORDER BY h._ctid",                         # the tuple and its copies
    "BOOL_AND(c._n_copies % 2 = 0) OVER fy AS _all_even",                                # the all-even test
    "(c._n_copies = COUNT(*) OVER fy) AS _one_tuple",                                    # the forward-fill shape
    "(b._all_even AND b._repeated) AS _pair",                                            # pair_collapse, whole
    "CASE WHEN n_urls_for_year > 1 THEN 'latest_url' END",                               # the dedup_rule values
    "CASE WHEN _pair THEN 'pair_collapse' END",
    "CASE WHEN _fill THEN 'forward_fill' END",
    "CASE WHEN _field_fill THEN 'field_fill' END",
    "WHERE CASE WHEN _fill THEN _copy_rank = 1",                                         # what is kept
    "WHEN _pair THEN _copy_rank <= _n_copies / 2",
)
# The differences: line 25 on the paid side (the two fill rules), the paid relation's url on the future side.
PAID_ONLY = ("_pf_line25", "arecgpdcprps", "arecprexpnss", "_amt", "_line25_d", "_line25_a",
             "_n_amt", "_first_amt", "_grant_amt", "_one_grant")
FUTURE_ONLY = ("CREATE TEMP TABLE _pf_paid AS", "LEFT JOIN _pf_paid p", "WHERE p.url IS NULL OR p.url <= g.url")


def test_both_relations_are_one_definition():
    paid, future = cg._PF_CURRENT_DDL, cg._PF_FUTURE_CURRENT_DDL
    for fragment in SHARED:
        assert paid.count(fragment) == future.count(fragment) == 1, fragment
    assert all(fragment in paid and fragment not in future for fragment in PAID_ONLY)
    assert all(fragment in future and fragment not in paid for fragment in FUTURE_ONLY)
    # what the rule reads beside the rows is gathered first, never joined as a CTE (the builder's docstring)
    assert "\npaid AS (" not in future and "declared AS (" not in paid
    assert cg._content_hash(cg._PF_CONTENT_COLS) in paid and cg._content_hash(cg._PF_FUTURE_CONTENT_COLS) in future


def _rewritten(shared: list[str], other: list[str]) -> list[str]:
    """The lines of ``shared`` that ``other`` does not keep: neither as they
    are nor with something after them (a comma, a statement on the line)."""
    return [line.strip() for line in shared if not any(kept.startswith(line) for kept in other)]


def test_each_difference_only_adds_to_the_shared_rule():
    # the same columns under each option: the text with neither is the shared rule
    args = dict(table="public.t", source="public.s", content_cols=cg._PF_CONTENT_COLS, all_cols=cg._PF_ALL_COLS)
    shared = cg._pf_current_ddl(**args).splitlines()
    with_paid = cg._pf_current_ddl(**args, paid="public.p").splitlines()
    with_line25 = cg._pf_current_ddl(**args, line25_amount="sigocpyamoun").splitlines()
    with_names = cg._pf_current_ddl(**args, line25_amount="sigocpyamoun",
                                    recipient_cols=cg._PF_NAME_COLS).splitlines()
    assert not [f for f in PAID_ONLY + FUTURE_ONLY if any(f in line for line in shared)]
    # the paid relation's url: lines added, none rewritten
    assert _rewritten(shared, with_paid) == [] and len(with_paid) > len(shared)
    # line 25: lines added, and the one line that decides forward fill rewritten
    assert _rewritten(shared, with_line25) == ["FALSE AS _fill,"]
    assert len(with_line25) > len(shared)
    # the recipient's name beside it: lines added, and the one line that decides field fill rewritten
    assert _rewritten(with_line25, with_names) == ["FALSE AS _field_fill"]
    assert len(with_names) > len(with_line25)
    # the names alone add nothing: field fill leans on line 25 as forward fill does
    assert cg._pf_current_ddl(**args, recipient_cols=cg._PF_NAME_COLS).splitlines() == shared


def test_line_25_decides_the_two_fills_and_nothing_else():
    ddl = cg._PF_CURRENT_DDL
    # pair_collapse is the repeated row alone, on both sides: no test of the block's sum against line 25
    assert "ABS(" not in ddl and "_full_sum" not in ddl
    rule = ddl[ddl.index("ruled AS ("):ddl.index("SELECT ruled.filerein")]
    assert rule.count("_line25") == 4 and "AS _pair" in rule
    # forward fill: one tuple, more than once in the filing itself, one copy on line 25 in either column
    assert "(p._one_tuple AND p._n_copies / CASE WHEN p._pair THEN 2 ELSE 1 END >= 2" in ddl
    assert "AND p._amt > 0 AND (p._amt = p._line25_d OR p._amt = p._line25_a)) AS _fill," in ddl
    # field fill: not one tuple, the rows with an amount one grant, two or more of them in the filing itself,
    # and the same line-25 test on the grant's amount
    assert "(NOT p._one_tuple AND p._one_grant AND p._n_amt / CASE WHEN p._pair THEN 2 ELSE 1 END >= 2" in ddl
    assert ("AND p._grant_amt > 0 AND (p._grant_amt = p._line25_d OR p._grant_amt = p._line25_a)) AS _field_fill"
            in ddl)
    # one grant: one amount and one name, as the three name columns have it, among the rows with an amount
    name = "concat_ws(' ', c.sigocpyrpnam, c.sigocpyrbnbn1, c.sigocpyrbnbn2)"
    assert ddl.count(name) == 2 and ddl.count("FILTER (WHERE c._amt <> 0) OVER fy") == 7
    # it removes the later rows with the amount, and no other: a row without one is left to the lines below it
    assert "WHEN _field_fill AND _amt <> 0 THEN _ctid = _first_amt\n           WHEN _pair THEN" in ddl
    future = cg._PF_FUTURE_CURRENT_DDL
    assert "_field_fill AND _amt" not in future and "FALSE AS _field_fill" in future
    assert (cg.PAIR_COLLAPSE, cg.FORWARD_FILL, cg.FIELD_FILL) == ("pair_collapse", "forward_fill", "field_fill")


def test_line_25_is_the_kept_urls_own():
    # raw basic_fields_pf, the row of the url the rows are kept under: never another version of the return's.
    # (Until 2026-10-01 it was one row a filer-year, the lowest sha: the table has one _ingested_at.)
    ddl = cg._PF_CURRENT_DDL
    line25 = ddl[ddl.index("CREATE TEMP TABLE _pf_line25"):ddl.index("CREATE INDEX ON _pf_line25")]
    assert "FROM public.basic_fields_pf\n" in line25 and "basic_fields_pf_current" not in ddl
    assert "SELECT DISTINCT ON (url) url," in line25 and "ORDER BY url, filesha256;" in line25
    assert "_ingested_at" not in line25 and "CREATE INDEX ON _pf_line25 (url);" in ddl
    assert "LEFT JOIN _pf_line25 l ON l.url = g.url" in ddl               # g.url is the kept url
    assert cg._line25_by_url("public.basic_fields_pf") in line25


class _Result:
    def __init__(self, rows=None):
        self.rows, self.returns_rows = rows or [], rows is not None

    def scalar_one(self):
        return 16_648_094

    def fetchall(self):
        return self.rows


class _Connection:
    """What ``_build_one`` needs of a connection: it records the statements,
    and answers the watch's SELECT with ``unhalved``."""

    def __init__(self, unhalved):
        self.unhalved, self.statements = unhalved, []

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        return _Result(self.unhalved if sql.lstrip().startswith("SELECT t.filerein") else None)


@pytest.mark.parametrize("unhalved, level", [([], logging.INFO), ([("900000001", "2026"), ("900000002", "2026")],
                                                                  logging.WARNING)])
def test_every_paid_build_runs_the_watch_and_logs_its_count(caplog, unhalved, level):
    connection = _Connection(unhalved)
    with caplog.at_level(logging.INFO):
        assert cg._build_one(connection, "privategrants_current", "SELECT 1") == 16_648_094
    watch = [sql for sql in connection.statements if "_pf_unhalved" in sql]
    assert len(watch) == 5 and watch[0].lstrip().startswith("CREATE TEMP TABLE _pf_unhalved")
    assert "FROM public.privategrants_current g" in watch[0] and "FROM public.basic_fields_pf" in watch[0]
    assert connection.statements.index(watch[0]) > max(
        i for i, sql in enumerate(connection.statements) if "ix_pg_current" in sql)           # after the indexes
    said = [r for r in caplog.records if "look doubled" in r.getMessage()]
    assert [r.levelno for r in said] == [level]
    assert f"{len(unhalved)} filer-years" in said[0].getMessage()
    assert all(f"{ein}/{year}" in said[0].getMessage() for ein, year in unhalved)


def test_the_watch_is_the_old_test_on_the_blocks_held_whole_and_changes_no_row():
    sql = cg._PF_UNHALVED_SQL
    assert "dedup_rule IN ('passthrough', 'latest_url')" in sql                  # whole blocks only
    assert "HAVING COUNT(*) % 2 = 0 AND COUNT(*) >= 4" in sql                    # even, and room for two tuples
    assert "ABS(f.full_sum / 2 - l.line25_d) < ABS(f.full_sum - l.line25_d)" in sql and "l.line25_d > 0" in sql
    assert "HAVING BOOL_AND(t.n_copies % 2 = 0) AND COUNT(*) > 1" in sql         # every tuple even, more than one
    assert not [word for word in ("INSERT", "UPDATE", "DELETE", "ALTER") if word in sql]
    assert sql.count("CREATE") == 2 and "CREATE TEMP TABLE _pf_unhalved" in sql and "DROP TABLE _pf_unhalved" in sql
    # the builds of the other relations do not run it
    connection = _Connection([])
    cg._build_one(connection, "privategrants_future_current", "SELECT 1")
    assert not [s for s in connection.statements if "_pf_unhalved" in s]


@pytest.mark.parametrize("ddl", (cg._PF_CURRENT_DDL, cg._PF_FUTURE_CURRENT_DDL))
def test_a_build_splits_into_its_statements_and_drops_its_temp_tables(ddl):
    # _build_one splits on ';': a ';' in a comment would cut a statement in two
    assert all(";" not in line.split("--", 1)[1] for line in ddl.splitlines() if "--" in line)
    # a second build in the same session would otherwise fail on CREATE TEMP TABLE
    created = [line.split()[3] for line in ddl.splitlines() if line.startswith("CREATE TEMP TABLE")]
    assert created and all(f"DROP TABLE {name};" in ddl for name in created)
    assert ddl.count("DROP TABLE IF EXISTS public.") == 1 and ddl.count("CREATE TABLE public.") == 1


def test_a_scratch_copy_is_the_production_ddl_under_another_name():
    from givingtuesday_datamart.exploratory import pf_current_scratch as scratch

    for side, production, ddl in ((PAID, "privategrants_current", cg._PF_CURRENT_DDL),
                                  (FUTURE, "privategrants_future_current", cg._PF_FUTURE_CURRENT_DDL)):
        name = scratch.scratch_name(side, "x")
        assert name.startswith("public.scratch_pg") and name != f"public.{production}"
        rendered = scratch.scratch_ddl(side, "x")
        assert rendered.replace(name, f"public.{production}") == ddl
        assert f"DROP TABLE IF EXISTS public.{production}" not in rendered      # no production table is dropped
    # the future copy can read the paid copy in place of the production paid relation
    assert "FROM public.scratch_pgc_x g" in scratch.scratch_ddl(FUTURE, "x", paid_scratch=True)
