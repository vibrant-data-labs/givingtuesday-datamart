"""The census's SQL, its CSV, the table for the log and the comparison,
with the session faked. No database."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from givingtuesday_datamart import census
from givingtuesday_datamart.placeholder_recovery import view as recovered_view


def test_the_relations_are_the_ones_production_reads():
    from givingtuesday_datamart import grant_matching as gm

    names = [r.name for r in census.RELATIONS]
    assert names[:6] == ["basic_fields_current", "basic_fields_pf_current", "basic_fields_ez_current",
                         "grants_to_domestic_organizations_current", "privategrants_current",
                         "privategrants_future_current"]
    assert "nonprofit_canonical" in names and recovered_view.VIEW in names
    for name in ("privategrants_w_recipients", "unioned_grants"):
        assert name in names and gm.Relations().of(name) == name
    assert len(names) == len(set(names)) == 10


def test_the_amount_cast_is_the_gates():
    from givingtuesday_datamart.matching_regression_checks import AMT

    assert census.AMOUNT is AMT


def test_the_sql_guards_text_amounts_and_takes_numeric_ones_as_they_are():
    pf = census.count_sql(census.RELATIONS[4])
    assert "sum(CASE WHEN sigocpyamoun ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN sigocpyamoun::numeric END) AS dollars" in pf
    assert "FROM public.privategrants_current\nGROUP BY 1" in pf
    assert "NULL::bigint AS matched_rows" in pf and "NULL::numeric AS matched_dollars" in pf
    unioned = census.count_sql(census.RELATIONS[-1])
    assert "sum(grant_amount) AS dollars" in unioned
    assert "count(*) FILTER (WHERE grantee_ein IS NOT NULL) AS matched_rows" in unioned
    assert "sum(grant_amount) FILTER (WHERE grantee_ein IS NOT NULL) AS matched_dollars" in unioned
    matched = census.count_sql(census.RELATIONS[8])
    assert "count(*) FILTER (WHERE recipeint_ein_key IS NOT NULL) AS matched_rows" in matched


def test_the_recovered_rows_are_the_views_and_the_canonical_has_no_dollars():
    recovered = census.count_sql(census.RELATIONS[7])
    assert f"FROM public.{recovered_view.VIEW}\nWHERE row_source = '{recovered_view.FROM_RECOVERY}'" in recovered
    canonical = census.count_sql(census.RELATIONS[6])
    assert "sum(NULL::numeric) AS dollars" in canonical
    assert "CASE WHEN latest_taxyear::text ~ '^[0-9]{4}$' THEN latest_taxyear::text END AS taxyear" in canonical


def test_every_sql_groups_a_four_digit_year_and_the_rest_under_none():
    for relation in census.RELATIONS:
        sql = census.count_sql(relation)
        assert f"CASE WHEN {relation.taxyear}::text ~ '^[0-9]{{4}}$' THEN {relation.taxyear}::text END" in sql
        assert sql.rstrip().endswith("GROUP BY 1\nORDER BY 1 NULLS FIRST")


class Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class Session:
    """Answers each relation's statement from a dict; keeps every statement."""

    def __init__(self, answers):
        self.answers = answers
        self.statements = []

    def execute(self, clause):
        sql = str(clause)
        self.statements.append(sql)
        for name, rows in self.answers.items():
            if f"FROM public.{name}\n" in sql:
                return Result(rows)
        return Result([])


def test_take_counts_each_relation_once_and_names_an_unparseable_year_none():
    session = Session({
        "privategrants_current": [(None, 5, Decimal("10.0"), None, None), ("2024", 7, Decimal("2500000.4"), None, None)],
        "unioned_grants": [("2024", 9, 300, 4, 120)],
    })
    counts = census.take(session, [census.RELATIONS[4], census.RELATIONS[-1]])
    assert counts == [census.Count("privategrants_current", "none", 5, 10, None, None),
                      census.Count("privategrants_current", "2024", 7, 2500000, None, None),
                      census.Count("unioned_grants", "2024", 9, 300, 4, 120)]
    assert session.statements[0] == "SET statement_timeout = '45min'"
    assert session.statements[1] == "SET max_parallel_workers_per_gather = 0"
    assert sum("GROUP BY 1" in s for s in session.statements) == 2


COUNTS = [
    census.Count("privategrants_current", "2023", 100, 1_000_000_000, None, None),
    census.Count("privategrants_current", "2024", 200, 2_500_000_000, None, None),
    census.Count("privategrants_current", "none", 3, 0, None, None),
    census.Count("unioned_grants", "2022", 50, 500_000_000, 40, 400_000_000),
    census.Count("unioned_grants", "2024", 60, 600_000_000, 30, 300_000_000),
    census.Count("nonprofit_canonical", "2025", 7, None, None, None),
]


def test_the_csv_round_trips(tmp_path):
    path = tmp_path / "census.csv"
    census.write_csv(COUNTS, path, datetime(2026, 10, 29, 17, 0, tzinfo=timezone.utc))
    lines = path.read_text().splitlines()
    assert lines[0] == "taken_at,relation,taxyear,rows,dollars,matched_rows,matched_dollars"
    assert lines[1] == "2026-10-29T17:00:00+00:00,privategrants_current,2023,100,1000000000,,"
    assert lines[4] == "2026-10-29T17:00:00+00:00,unioned_grants,2022,50,500000000,40,400000000"
    assert lines[6] == "2026-10-29T17:00:00+00:00,nonprofit_canonical,2025,7,,,"
    assert census.read_csv(path) == COUNTS


def test_the_summary_names_the_newest_years_and_totals_each_relation():
    lines = census.summary(COUNTS).splitlines()
    assert lines[0] == "| relation | rows | with EIN | $B | $B with EIN | rows 2023 | rows 2024 | rows 2025 |"
    assert lines[1] == "|---|---|---|---|---|---|---|---|"
    assert lines[2] == "| privategrants_current | 303 |  | 3.50 |  | 100 | 200 | 0 |"
    assert lines[3] == "| unioned_grants | 110 | 70 | 1.10 | 0.70 | 0 | 60 | 0 |"
    assert lines[4] == "| nonprofit_canonical | 7 |  |  |  | 0 | 0 | 7 |"
    assert census.newest_years(COUNTS, 2) == ["2024", "2025"]


def test_the_comparison_lists_what_moved_and_where():
    after = [
        census.Count("privategrants_current", "2023", 100, 1_000_000_000, None, None),  # as before
        census.Count("privategrants_current", "2024", 260, 3_000_000_000, None, None),  # grew
        census.Count("privategrants_current", "2025", 40, 400_000_000, None, None),     # a new year
        census.Count("unioned_grants", "2022", 50, 500_000_000, 45, 450_000_000),       # more with an EIN
        census.Count("unioned_grants", "2024", 60, 600_000_000, 30, 300_000_000),
        census.Count("nonprofit_canonical", "2025", 7, None, None, None),
    ]  # and the rows without a tax year are gone
    report = census.comparison(COUNTS, after)
    lines = report.splitlines()
    assert lines[0] == ("| relation | tax year | rows before | rows after | rows moved | $B before | $B after "
                        "| $B moved | with EIN moved |")
    assert lines[2:] == [
        "| privategrants_current | 2024 | 200 | 260 | +60 | 2.50 | 3.00 | +0.50 |  |",
        "| privategrants_current | 2025 | 0 | 40 | +40 |  | 0.40 | +0.40 |  |",
        "| privategrants_current | none | 3 | 0 | -3 | 0.00 |  | +0.00 |  |",
        "| **privategrants_current** | all | 303 | 400 | +97 | 3.50 | 4.40 | +0.90 |  |",
        "| unioned_grants | 2022 | 50 | 50 | +0 | 0.50 | 0.50 | +0.00 | +5 |",
        "| **unioned_grants** | all | 110 | 110 | +0 | 1.10 | 1.10 | +0.00 | +5 |",
        "| **nonprofit_canonical** | all | 7 | 7 | +0 |  |  |  |  |",
        "",
        "4 relation-years moved",
    ]


def test_an_unchanged_census_says_so():
    report = census.comparison(COUNTS, COUNTS)
    assert report.endswith("nothing moved in any relation-year")
    assert all(line.startswith("| **") for line in report.splitlines()[2:-2])


def test_a_relation_only_in_the_after_take_is_new():
    after = COUNTS + [census.Count("basic_fields_current", "2025", 12, 5_000_000_000, None, None)]
    lines = census.comparison(COUNTS, after).splitlines()
    assert "| basic_fields_current | 2025 | 0 | 12 | +12 |  | 5.00 | +5.00 |  |" in lines
    assert "| **basic_fields_current** | all | 0 | 12 | +12 |  | 5.00 | +5.00 |  |" in lines
