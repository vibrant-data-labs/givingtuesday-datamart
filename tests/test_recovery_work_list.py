"""``placeholder_recovery.work_list`` with no Postgres in the loop: the
query's rows as dicts, the exclusions as a dict, the in-memory store."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from givingtuesday_datamart.placeholder_recovery import classifier
from givingtuesday_datamart.placeholder_recovery import work_list as wl
from givingtuesday_datamart.placeholder_recovery.exclusions import Exclusion

BUILT = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
SIEGEL, GENENTECH = "451742989", "460500266"
EXCLUDED = {GENENTECH: Exclusion(GENENTECH, "Genentech Patient Foundation", "donated medicine", date(2026, 9, 21))}


def _url(object_id):
    return f"https://gt990datalake-rawdata.s3.amazonaws.com/EfileData/XmlFiles/{object_id}_public.xml"


def _row(object_id="202223169349100737", ein=SIEGEL, year="2021", paid="24452401", declared="24452401", **over):
    """One row of the query, as the database returns it."""
    return {"filerein": ein, "taxyear": year, "declared_paid": Decimal(declared), "declared_books": Decimal(declared),
            "placeholder_paid": Decimal(paid),
            "placeholder_rows": 1, "placeholder_texts": ["SEE Attachment 15"], "url": _url(object_id), "urls": 1,
            "filer_name": "Siegel Family Endowment", "taxperend": "2021-12-31", "source_version": "2026_06_16",
            "filer_marked_individual": False, **over}


def test_a_query_row_becomes_a_work_list_row():
    filing = wl.filing_from_row(_row(), BUILT)
    assert filing == wl.PlaceholderFiling(
        object_id="202223169349100737", filerein=SIEGEL, filer_name="Siegel Family Endowment", taxyear=2021,
        taxperend=date(2021, 12, 31), declared_paid=Decimal("24452401"), declared_books=Decimal("24452401"),
        placeholder_paid=Decimal("24452401"), placeholder_rows=1, placeholder_texts=["SEE Attachment 15"],
        placeholder_future=Decimal(0), placeholder_future_rows=0, band="B",
        filer_marked_individual=False, placeholder_exceeds_declared=False,
        classifier_version=classifier.CLASSIFIER_VERSION, source_version="2026_06_16", built_at=BUILT)


def test_a_filing_carries_the_future_amount_on_its_placeholder_rows_of_line_3b():
    kenan = wl.filing_from_row(_row(paid="31571000", declared="31571000", placeholder_future=Decimal("20012000"),
                                    placeholder_future_rows=1), BUILT)
    assert (kenan.placeholder_future, kenan.placeholder_future_rows) == (Decimal("20012000"), 1)
    none = wl.filing_from_row(_row(placeholder_future=None, placeholder_future_rows=None), BUILT)
    assert (none.placeholder_future, none.placeholder_future_rows) == (Decimal(0), 0)


def test_the_future_amount_decides_neither_the_list_nor_the_band():
    rows = [_row(paid="900000", declared="900000"),
            _row("202311019349101326", year="2022", paid="900000", declared="900000",
                 placeholder_future=Decimal("250000000"), placeholder_future_rows=2)]
    found = wl.build(rows, {}, BUILT)
    assert [(f.band, f.placeholder_future) for f in found.filings] == [("D", Decimal(0)), ("D", Decimal("250000000"))]
    assert "WHERE d.declared_paid > 0 AND p.placeholder_paid > 0 AND p.placeholder_paid >= 0.5 * d.declared_paid" in (
        wl.QUERY)
    assert "placeholder_future" not in wl.QUERY[wl.QUERY.index("listed AS"):wl.QUERY.index("future AS")]


def test_the_first_two_distinct_placeholder_texts_are_kept_in_the_order_given():
    texts = ["SEE STATEMENT 12", "SEE STATEMENT 12", None, "SEE STATEMENT 13", "SEE STATEMENT 14"]
    assert wl.filing_from_row(_row(placeholder_texts=texts), BUILT).placeholder_texts == [
        "SEE STATEMENT 12", "SEE STATEMENT 13"]


@pytest.mark.parametrize("paid, band", [
    ("100000000", "A"), ("99999999.99", "B"), ("10000000", "B"), ("9999999.99", "C"), ("1000000", "C"),
    ("999999.99", "D"), ("0.01", "D"),
])
def test_the_band_is_cut_on_the_paid_placeholder_amount(paid, band):
    assert wl.band(Decimal(paid)) == band
    assert wl.filing_from_row(_row(paid=paid, declared="150000000"), BUILT).band == band   # never on the declared total


def test_a_filing_marked_individual_stays_on_the_list_and_carries_the_mark():
    found = wl.build([_row(filer_marked_individual=True), _row("202311019349101326", year="2022")], EXCLUDED, BUILT)
    assert [row.filer_marked_individual for row in found.filings] == [True, False]
    assert found.excluded == [] and found.unlisted == []


@pytest.mark.parametrize("paid, cash, books, exceeds", [
    ("18571800", "13360800", "13360800", True),       # Eden Hall 2022: grants approved entered beside grants paid
    ("25219477", "1583543", "25219477", False),       # King Street 2021: gifts not in cash are in column (a) only
    ("6070206", "5929795", "2803854", True),          # over the larger of the two
    ("1004", "1000", "1000", False),                  # inside the tolerance a list is held to
    ("1006", "1000", "1000", True),
    ("1006", "1000", None, True),                     # column (a) is not a number: column (d) alone
    ("900", "1000", "1200", False),
])
def test_a_placeholder_amount_over_both_columns_of_line_25_is_marked(paid, cash, books, exceeds):
    books = Decimal(books) if books else None
    assert wl.exceeds_declared(Decimal(paid), Decimal(cash), books) is exceeds
    row = _row(paid=paid, declared=cash, declared_books=books)
    filing = wl.filing_from_row(row, BUILT)
    assert (filing.placeholder_exceeds_declared, filing.declared_books) == (exceeds, books)
    assert wl.build([row], {}, BUILT).filings == [filing]                # marked, and on the list all the same


def test_an_excluded_filer_is_left_out_by_ein_whatever_its_name():
    rows = [_row(), _row("202301359349101405", ein=GENENTECH, filer_name="Any Name At All", paid="3000000000",
                         declared="3000000000"),
            _row("202341359349103204", ein="942278431", filer_name="Genentech Foundation", year="2022")]
    found = wl.build(rows, EXCLUDED, BUILT)
    assert [row.filerein for row in found.filings] == [SIEGEL, "942278431"]      # the name decides nothing
    assert [row.filerein for row in found.excluded] == [GENENTECH]


def test_a_url_without_an_object_id_is_set_aside_not_guessed(caplog):
    found = wl.build([_row(url="https://example.org/no-id.xml"), _row()], {}, BUILT)
    assert len(found.filings) == 1 and found.unlisted == [
        (SIEGEL, "2021", "no 18-digit IRS OBJECT_ID found in 'https://example.org/no-id.xml'")]
    assert "left off the work list" in caplog.text


def test_two_filings_under_one_object_id_stop_the_build():
    with pytest.raises(ValueError, match="is the filing of 451742989 2021 and of 451742989 2022"):
        wl.build([_row(), _row(year="2022")], {}, BUILT)


def test_rebuild_replaces_the_table_whole():
    store = wl.MemoryStore([wl.filing_from_row(_row("202011111111111111", year="2019"), BUILT)])
    found = wl.rebuild(None, exclusions=EXCLUDED, store=store, built_at=BUILT,
                       rows=[_row(), _row("202301359349101405", ein=GENENTECH)])
    assert sorted(store.rows) == ["202223169349100737"] and store.commits == [1]
    assert store.get(["202223169349100737", "202301359349101405"]) == {"202223169349100737": found.filings[0]}
    assert len(found.excluded) == 1


def test_select_filters_on_tax_years_and_takes_the_largest_first():
    filings = [wl.filing_from_row(_row(f"20222316934910073{i}", year=year, paid=paid), BUILT)
               for i, (year, paid) in enumerate([("2019", "500"), ("2020", "100"), ("2021", "900"), ("2022", "300")])]
    assert [f.taxyear for f in wl.select(filings)] == [2021, 2019, 2022, 2020]
    assert [f.taxyear for f in wl.select(filings, tax_years={2020, 2021, 2022})] == [2021, 2022, 2020]
    assert [f.taxyear for f in wl.select(filings, tax_years={2020, 2021, 2022}, limit=2)] == [2021, 2022]


def test_summary_counts_the_list_the_excluded_and_the_marked():
    rows = [_row(), _row("202301359349101405", ein=GENENTECH, paid="3000000000", declared="3000000000"),
            _row("201811111111111111", ein="010342663", year="2017", paid="32000", declared="40000",
                 filer_marked_individual=True)]
    text = wl.summary(wl.build(rows, EXCLUDED, BUILT))
    lines = {line.split("  ")[1].strip(): line.split() for line in text.splitlines() if line.startswith("  ")}
    assert lines["every placeholder filing"][-4:] == ["3", "3", "$3.02B", "$3.02B"]
    assert lines["the excluded filers"][-4:] == ["1", "1", "$3.00B", "$3.00B"]
    assert lines["the work list"][-4:] == ["2", "2", "$0.02B", "$0.02B"]
    assert "of which marked individual" in text and "2026_06_16 (2 filings)" in text
    over = next(line for line in text.splitlines() if "of which over line 25" in line)
    assert over.split()[-4:] == ["0", "0", "$0.00B", "$0.00B"]
    assert "of which with a future amount" in text
    assert [line.split() for line in text.splitlines() if line.strip().startswith(("2017", "2021"))] == [
        ["2017", "1", "$0.00B", "1"], ["2021", "1", "$0.02B", "0"]]


def test_the_future_summary_counts_the_list_and_what_is_not_on_it():
    rows = [_row(placeholder_future=Decimal("20012000"), placeholder_future_rows=1),
            _row("201811111111111111", ein="010342663", year="2017", placeholder_future=Decimal("5000000"),
                 placeholder_future_rows=2),
            _row("202311019349101326", year="2022")]
    without = [{"why": "no paid placeholder", "filings": 34, "filers": 20, "dollars": Decimal("270776834")}]
    lines = [line.split() for line in wl.future_summary(wl.build(rows, {}, BUILT), without).splitlines()]
    assert lines[1] == ["on", "the", "work", "list", "2", "2", "$0.03B"]
    assert lines[2] == ["tax", "years", "2020", "on", "1", "1", "$0.02B"]
    assert lines[3] == ["not", "on", "it:", "no", "paid", "placeholder", "34", "20", "$0.27B"]


def test_the_future_amount_is_read_with_the_same_pattern_from_the_same_version_of_the_return():
    future = wl.FUTURE_PLACEHOLDERS
    assert "FROM privategrants_future_current g" in future and "g.sigocaffamou" in future
    assert classifier.pointer_sql("n.name") in future
    assert classifier.prefilter_sql("g", classifier.FUTURE_NAMES) in future
    assert f"SELECT {classifier.name_sql('g', classifier.FUTURE_NAMES)} AS name" in future
    assert future in wl.QUERY and future in wl.FUTURE_WITHOUT_PAID
    assert "LEFT JOIN future u ON u.filerein = l.filerein AND u.taxyear = l.taxyear AND u.url = f.url" in wl.QUERY
    assert "coalesce(u.placeholder_future, 0) AS placeholder_future" in wl.QUERY


def test_the_new_columns_reach_a_table_made_before_them():
    added = [statement for statement in wl.DDL if "ADD COLUMN IF NOT EXISTS" in statement]
    assert any("placeholder_future numeric(18,2) NOT NULL DEFAULT 0" in statement for statement in added)
    assert any("placeholder_future_rows integer NOT NULL DEFAULT 0" in statement for statement in added)
    assert all(f"{column} " in wl.DDL[0] for column in wl.COLUMNS)


def test_the_query_reads_the_loaded_tables_with_the_half_rule_and_no_year_floor():
    assert "FROM privategrants_current g" in wl.QUERY and "FROM basic_fields_pf_current" in wl.QUERY
    assert "arecgpdcprps" in wl.QUERY and "arecprexpnss" in wl.QUERY          # line 25, columns (d) and (a)
    assert "p.placeholder_paid > 0 AND p.placeholder_paid >= 0.5 * d.declared_paid" in wl.QUERY
    assert ">= '2020'" not in wl.QUERY
    assert classifier.pointer_sql("n.name") in wl.QUERY and classifier.prefilter_sql("g") in wl.QUERY
