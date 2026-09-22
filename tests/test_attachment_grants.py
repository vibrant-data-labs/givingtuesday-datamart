"""Tests for recovering grants from OCR'd 990-PF attachments.

Both fixtures are real Unstructured output, trimmed to their ``Table``
elements (the only type the selector reads). They are here because each one
caught a different wrong version of this code:

* **Siegel** — every earlier draft that keyed on the placeholder amount alone
  "reconciled" against the Part XV summary line on page 11, one row, 0.0000%
  error, zero grants recovered.
* **Bezos** — its attachment runs cash then non-cash under different headers,
  the non-cash section carries book value alongside fair market value, and a
  travel-expense table sits one page before the list.

Expected values are the filings' own stated totals, so the assertions are
anchored to the documents rather than to whatever this code happens to emit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from givingtuesday_datamart.attachment_grants import (
    PLACEHOLDER,
    candidate_tables,
    extract,
    extract_tables,
    is_pointer,
    load_elements,
    page_tables,
    parse_money,
    xml_tables,
)
from givingtuesday_datamart.irs_source import attachment_start


def _table(page, header, rows, heading=None):
    """A minimal Unstructured page: an optional heading and one table."""
    html = "<table><tr>" + "".join(f"<td>{h}</td>" for h in header) + "</tr>"
    html += "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    elements = []
    if heading:
        elements.append({"type": "Title", "text": heading, "metadata": {"page_number": page}})
    elements.append({"type": "Table", "metadata": {"page_number": page, "text_as_html": html + "</table>"}})
    return elements

FIXTURES = Path(__file__).parent / "fixtures"

SIEGEL_DECLARED = 27_792_259.0     # XML placeholder "SEE Attachment 22"
BEZOS_DECLARED = 349_936_476.0     # XML placeholder "SEE ATTACHMENT C"


@pytest.fixture(scope="module")
def siegel() -> list[dict]:
    return load_elements(FIXTURES / "siegel_unstructured_tables.json")


@pytest.fixture(scope="module")
def bezos() -> list[dict]:
    return load_elements(FIXTURES / "bezos_unstructured_tables.json")


def test_siegel_reconciles_to_the_dollar(siegel):
    result = extract(siegel, SIEGEL_DECLARED).paid
    assert result.outcome == "reconciled"
    assert len(result.rows) == 110
    # 110 grants, one dollar of rounding across the whole statement.
    assert abs(result.extracted - SIEGEL_DECLARED) <= 1.0


def test_siegel_selects_the_statement_not_the_summary_line(siegel):
    """The Part XV line carries the declared amount and must never win."""
    result = extract(siegel, SIEGEL_DECLARED).paid
    assert result.pages[0] == 31 and result.pages[-1] == 38
    assert 11 not in result.pages
    assert all(not PLACEHOLDER.search(row.name) for row in result.rows)


def test_bezos_spans_cash_and_non_cash_sections(bezos):
    result = extract(bezos, BEZOS_DECLARED).paid
    assert result.outcome == "reconciled"
    assert len(result.rows) == 279
    assert result.error < 0.001
    # Two sections, two different headers for the same concept.
    assert set(result.amount_headers) == {
        "Value of Contribution",
        "Fair Market Value of Contribution",
    }


def test_bezos_excludes_the_adjacent_expense_table(bezos):
    """Page 32 is program expenses (travel), not grants."""
    result = extract(bezos, BEZOS_DECLARED).paid
    assert 32 not in result.pages
    assert not any("TRAVEL -" in row.name.upper() for row in result.rows)


def test_bezos_takes_fair_market_value_not_book_value(bezos):
    """Book value is the cost basis: $155M against $340M actually given."""
    result = extract(bezos, BEZOS_DECLARED).paid
    non_cash = [row for row in result.rows if row.page in (36, 37, 38)]
    assert sum(row.amount for row in non_cash) == pytest.approx(340_665_382.20, abs=5)


def test_wrong_declared_amount_is_flagged_not_forced(siegel):
    """A filing whose attachment doesn't add up must fail loudly."""
    result = extract(siegel, 1_234_567.0).paid
    assert result.outcome == "no_reconciling_run"
    assert result.rows == ()


def test_no_tables_yields_no_candidates():
    assert extract([], 100.0).paid.outcome == "no_candidate_tables"
    assert extract([{"type": "Title", "text": "x"}], 100.0).paid.outcome == "no_candidate_tables"


def test_missing_declared_amount_is_its_own_outcome(siegel):
    assert extract(siegel, 0).paid.outcome == "no_declared_amount"


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("$2,500,000", 2_500_000.0),
        ("$170,219.88", 170_219.88),
        ("1,000", 1_000.0),
        ("963 Shares AMZN", None),      # description column, not money
        ("PC", None),
        ("", None),
        ("Total Cash Contributions $9,271,094.14", None),   # substring never counts
    ],
)
def test_parse_money_only_accepts_whole_cells(cell, expected):
    assert parse_money(cell) == expected


def test_candidate_tables_are_page_ordered(siegel):
    pages = [t.page for t in candidate_tables(siegel) if t.page is not None]
    assert pages == sorted(pages)


# ---- synthetic cases from the audit, one per guard that the audit added ----

GRANTS = [("Alpha Org", "PC", "General support", "1,000"),
          ("Beta Org", "PC", "Program", "2,000"),
          ("Gamma Org", "PC", "Capital", "3,000")]


def test_subtotal_and_blank_name_rows_are_totals_not_grants():
    """Klarman's "Subtotal Healthy Democracy" and Wells Fargo's blank-name
    $277.9M carry-forward were both credited as grants by the first version."""
    rows = GRANTS + [("", "", "Subtotal Program Area", "6,000"), ("", "", "", "6,000")]
    elements = _table(1, ("Grantee", "Status", "Purpose", "Amount"), rows)
    result = extract(elements, 6_000).paid
    assert result.reconciled and len(result.rows) == 3
    assert result.total_stated


def test_pointer_rows_are_not_grants():
    """Hall's Part XV summary: GRANTS PAID REPORT / MATCHING GIFTS REPORT."""
    rows = [("GRANTS PAID REPORT", "XV-3A-2", "52,829,934"),
            ("MATCHING GIFTS REPORT", "XV-3A-3", "140,000"),
            ("SCHOLARSHIP PAYMENTS REPORT", "XV-3A-4", "36,000")]
    elements = _table(1, ("", "See attached statement #", "Amount"), rows,
                      heading="Documentation of adjustments related to contributions, gifts, grants paid")
    assert extract(elements, 53_005_934).paid.outcome == "no_candidate_tables"


def test_status_of_recipient_is_not_the_name_column():
    """Pritzker: every grant was named "501 (c) 3" and the list vanished."""
    rows = [("Academy Foundation", "8949 Wilshire Blvd", "501 (c) 3", "PC", "$ 1,000.00"),
            ("Lincoln Park Zoo", "2001 N Clark St", "501 (c) 3", "PC", "$ 2,000.00"),
            ("Field Museum", "1400 S Lake Shore", "501 (c) 3", "PC", "$ 3,000.00")]
    header = ("Charity", "Address", "Foundation Status of Recipient", "Foundation Status", "Amount")
    result = extract(_table(1, header, rows), 6_000).paid
    assert result.reconciled and [r.name for r in result.rows][:1] == ["Academy Foundation"]


def test_paid_and_future_are_reconciled_separately():
    """A filer with a placeholder on both line 3a and line 3b."""
    paid = _table(1, ("Grantee", "Status", "Purpose", "Amount"), GRANTS, heading="Grants paid during the year")
    future = _table(2, ("Grantee", "Status", "Purpose", "Amount"),
                    [("Delta Org", "PC", "x", "500"), ("Eps Org", "PC", "y", "500"), ("Zeta Org", "PC", "z", "500")],
                    heading="Grants approved for future payment")
    recovery = extract(paid + future, 6_000, 1_500)
    assert recovery.paid.reconciled and recovery.future.reconciled
    assert recovery.recovered == 7_500 and len(recovery.rows) == 6
    # the combined total must not be reachable by mixing the two lists
    assert extract(paid + future, 7_500).paid.outcome == "no_reconciling_run"


def test_unlabelled_run_may_not_cross_a_vetoed_schedule():
    """Anschutz: a bond schedule between two grant sections must not be
    absorbed to close a gap, and form lines alone never reconcile."""
    grants = _table(1, ("Org", "St", "Purpose", "Amt"), GRANTS)
    bonds = _table(2, ("Name of Bond", "Book Value", "Fair Market Value"),
                   [("ACUSHNET", "1", "4,000")], heading="Investments Corporate Bonds Schedule")
    assert extract(grants + bonds, 10_000).paid.outcome == "no_reconciling_run"
    assert extract(grants, 6_000).paid.reconciled


@pytest.mark.parametrize(
    "name,expected",
    [
        ("SEE Attachment 22", True),                 # the frame's own definition
        ("SEE GRANTS PAID ATTACHMENT", True),        # words between see and attachment
        ("SCHEDULE ATTACHED", True),                 # bare reference
        ("STATEMENT 25", True),
        ("ATCH 4", True),                            # Sanofi Cares, $6.2B
        ("GRANTS", True),                            # bare category
        ("SCHEDULE AVAILABLE UPON REQUEST", False),  # withheld: nothing is attached
        ("RIDER UNIVERSITY FBO H SOULE", False),     # a real grantee
        ("Scratch Foundation", False),
        ("Center for Investigative Reporting", False),
    ],
)
def test_is_pointer_matches_the_measured_classes(name, expected):
    assert is_pointer(name) is expected


# --- vision-model pages and XML-itemised rows -------------------------------

def _page(kind, heading, rows, totals=()):
    """A vision model's response for one page, as ``page_tables`` reads it."""
    return {"page_kind": kind, "heading": heading,
            "rows": [{"name": n, "address": "", "status": "PC", "purpose": "", "amount": a} for n, a in rows],
            "totals": [{"label": label, "amount": a} for label, a in totals]}


def test_page_tables_keep_totals_and_pointers_out_of_the_rows():
    pages = {31: _page("grants_paid_list", "STATEMENT 22", [
        ("Scratch Foundation", "2,500,000"), ("Total", 4000000), ("See attached", 1500000),
        ("Brooklyn Museum", 1500000)], totals=[("TOTAL", 4000000)])}
    [table] = page_tables(pages)
    assert [row.name for row in table.rows] == ["Scratch Foundation", "Brooklyn Museum"]
    assert table.rows[0].amount == 2_500_000.0        # "2,500,000" parsed
    assert table.totals == (4_000_000.0, 4_000_000.0)  # the row that was a total, and the totals list
    assert table.labelled and not table.future and table.page == 31


def test_page_kind_is_evidence_when_the_heading_says_nothing():
    pages = {40: _page("grants_future_list", "", [("Alpha Trust", 1000), ("Beta House", 2000), ("Gamma Fund", 3000)]),
             41: _page("other", "", [("Delta Center", 1000), ("Epsilon Clinic", 2000), ("Zeta Library", 3000)])}
    future, plain = page_tables(pages)
    assert future.labelled and future.future
    assert not plain.labelled and not plain.future and not plain.vetoed
    # the future page cannot be spent against the paid target
    recovery = extract_tables([future, plain], paid=6000, future=6000)
    assert recovery.paid.reconciled and recovery.paid.pages == (41,)
    assert recovery.future.reconciled and recovery.future.pages == (40,)


def test_a_continuation_page_inherits_the_list_before_it_until_a_total_closes_it():
    """Kenan 2021: the paid list closes with its total on p127; p128 opens the
    future list under a masthead heading; p129 has no heading and both models
    called it paid. p129 must join p128, and p128 must not join p127."""
    pages = {127: _page("grants_paid_list", "", [("Alpha Trust", 1000), ("Beta House", 2000), ("Gamma Fund", 3000)],
                        totals=[("TOTAL", 6000)]),
             128: _page("grants_future_list", "KENAN CHARITABLE TRUST EIN 13-6192029",
                        [("Delta Center", 100), ("Epsilon Clinic", 200), ("Zeta Library", 300)]),
             129: _page("grants_paid_list", "", [("Eta Hospital", 400), ("Theta School", 500)])}
    tables = page_tables(pages, targets=(6000, 1500))
    assert [t.future for t in tables] == [False, True, True]
    assert tables[2].group == tables[1].group
    recovery = extract_tables(tables, paid=6000, future=1500)
    assert recovery.paid.reconciled and recovery.paid.pages == (127,)
    assert recovery.future.reconciled and recovery.future.pages == (128, 129)


def test_a_page_the_model_calls_other_never_inherits():
    """Waldheim 2022: a heading-less fee schedule follows the grant list."""
    pages = {21: _page("grants_paid_list", "Grants and Contributions Paid During the Year",
                       [("Alpha Trust", 1000), ("Beta House", 2000), ("Gamma Fund", 3000)]),
             22: _page("other", "", [("Lewis Rice LLC", 7992), ("Lewis Rice LLC", 3741), ("Lewis Rice LLC", 4711)])}
    grants, fees = page_tables(pages, targets=(6000,))
    assert grants.labelled and not fees.labelled and not fees.vetoed
    assert extract_tables([grants, fees], paid=6000).paid.pages == (21,)


def test_xml_rows_close_the_gap_the_attachment_leaves():
    """Cohen 2020: the attachment lists $6.5M, the expenditure-responsibility
    statement in the XML holds the $85M, and only together do they reconcile."""
    pages = {28: _page("grants_paid_list", "PART XV - GRANTS AND CONTRIBUTIONS PAID",
                       [("A Moment To Breathe", 10000), ("ACE Programs", 50000), ("Brooklyn Museum", 6_400_000)])}
    xml = [{"source": "990PF_EXPENDITURE_RESP", "recipient_name": "Cohen Veterans Network", "amount": "38000000"},
           {"source": "990PF_EXPENDITURE_RESP", "recipient_name": "COHEN VETERANS NETWORK INC", "amount": "47000000"},
           {"source": "990PF_P14_3A", "recipient_name": "SEE ATTACHMENT", "amount": "91460000"}]
    tables = page_tables(pages) + xml_tables(xml, targets=(91_460_000,))
    assert [t.kind for t in tables] == ["paid", "paid"]
    assert len(tables[1].rows) == 2                    # the placeholder row itself is dropped
    recovery = extract_tables(tables, paid=91_460_000)
    assert recovery.paid.reconciled and recovery.paid.labelled
    assert sum(1 for row in recovery.rows if row.page is None) == 2
    # and without the XML the list is present but short
    assert not extract_tables(tables[:1], paid=91_460_000).paid.reconciled


def test_expenditure_responsibility_page_is_only_used_when_it_reconciles():
    pages = {10: _page("grants_paid_list", "GRANTS PAID", [("Alpha Trust", 1000), ("Beta House", 2000), ("Gamma Fund", 3000)]),
             11: _page("expenditure_responsibility", "", [("Beta House", 2000), ("Omega Institute", 500)])}
    recovery = extract_tables(page_tables(pages), paid=6000)
    assert recovery.paid.reconciled and recovery.paid.pages == (10,)
    recovery = extract_tables(page_tables(pages), paid=8500)
    assert recovery.paid.reconciled and recovery.paid.pages == (10, 11)


@pytest.mark.parametrize("widths, start", [
    ([2246, 2259, 2246, 3081, 2440, 2246, 2550, 2550], 7),   # Siegel-shaped
    ([2246, 2246, 2246], None),                              # all IRS-rendered: nothing attached
    ([2246, 2521, 2246], 2),                                 # a filer page, then the IRS again: keep both
])
def test_attachment_start_is_the_first_page_the_irs_did_not_render(widths, start):
    assert attachment_start(widths) == start
