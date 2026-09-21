"""Recover itemised grants from the PDF attachment behind a placeholder row.

Thousands of 990-PF filings itemise nothing: Part XV carries a single row
reading "SEE Attachment 22" whose amount is the filer's entire grant total
(9,518 filings, $25.05B once patient-assistance programs are set aside). The
e-file XML genuinely has no list — but the IRS's own PDF image does, because
the filer attached it. OCR the image and the grants come back.

This module owns the step after OCR: given an Unstructured result for one
filing, decide **which tables are the grant list** and return their rows.

The hard part is that nothing labels them reliably. The XML says "SEE
Attachment 22" and the PDF says "STATEMENT 22"; the next filer says
"SEE ATTACHMENT C" and "CHARITABLE LISTING"; a third just says "SEE ATTACHED"
and names nothing at all. Wells Fargo alone uses four phrasings across four
years. Matching labels is a losing game.

So we don't. **The placeholder amount already states what the right answer
sums to**, which turns selection into a search: find the contiguous run of
tables whose amount column reconciles to the declared total. Selection and
validation collapse into one mechanism — a filing either reconciles or it is
flagged, and there is no separate quality gate to keep honest.

Three constraints, each of which comes from a way this got it wrong:

* **Placeholder rows can't be candidates.** The Part XV line itself carries
  the declared amount, so the first version "reconciled" at 0.0000% error by
  selecting one row — the very row we are trying to replace.
* **Error dominates row count.** Preferring the run with the most rows lets
  the search bolt an unrelated table onto a correct run and stay inside
  tolerance (a foundation's travel-expense page, sitting one page before its
  grant list).
* **The amount column is chosen by header, never by position.** Non-cash
  sections carry both fair market value and book value; taking the last money
  cell silently grabs the cost basis — $155M against $340M actually given.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence

# Ordered by preference. Fair market value must beat book value: a non-cash
# section lists both, and book value is the cost basis, not the gift.
AMOUNT_HEADERS: tuple[str, ...] = (
    r"fair market value",
    r"value of contribution",
    r"^amount$",
    r"amount of (?:grant|contribution)",
    r"\bamount\b",
)
NAME_HEADERS: tuple[str, ...] = (
    r"name and address",
    r"name of recipient",
    r"\bgrantee\b",
    r"name of org",
    r"\brecipient\b",
    r"^name\b",
    r"\bname\b",
)

# The same detector that flags a filing as placeholder-only upstream.
PLACEHOLDER = re.compile(
    r"((?:see|refer)\w*[\s,–-]*(?:attach|addition|schedul|statement|stmt|list))"
    r"|^\s*see\s*$",
    re.I,
)
_TOTAL_ROW = re.compile(r"\btotals?\b", re.I)

# Reconciliation alone is not evidence. These filings carry dozens of money
# tables, and over a 0.5% tolerance some combination of them will hit almost
# any target: the Wyss Foundation's Legal Fees, Other Assets, Other Decreases
# and Other Expenses schedules sum to within 0.16% of its $125.7M grant total,
# while its actual "Grants Paid Schedule" sits six pages later.
#
# What rescues it is that these documents label every schedule. The heading
# is not reliable enough to *find* the grant list (a filer may call it
# "STATEMENT 22", "CHARITABLE LISTING" or nothing at all), but it is reliable
# enough to rule tables out, which is all we need to stop the search wandering.
GRANT_CONTEXT = re.compile(
    r"grants?\s+paid|grants?\s+and\s+contributions|contributions?\s+paid"
    r"|charitable\s+listing|cash\s+contributions|grants?\s+approved"
    r"|\bgrantee\b|grant.*schedule"
    # Parts XIV and XV are the 990-PF supplementary-information sections that
    # carry the grant tables, and filers head them without the word "grant" —
    # Wells Fargo's 217 grant tables all sit under "FORM 990-PF PART XV WELLS
    # FARGO FOUNDATION". Parts XV-A and XV-B are income analysis, not grants.
    r"|part\s+x(?:iv|v)\b(?![-\u2013\s]*[ab]\b)",
    re.I,
)

# The column name is evidence in its own right, and survives a heading that
# OCR'd badly or a header row misread as data.
GRANT_AMOUNT_HEADER = re.compile(
    r"grant\s+amount|payment\s+amount|amount\s+of\s+(?:grant|contribution)"
    r"|value\s+of\s+contribution", re.I)

# Part XV separates grants *paid* from grants *approved for future payment*.
# They are different declared totals, so a run must not mix them blindly.
FUTURE_PAYMENT = re.compile(r"future\s+(?:grant|payment)|approved\s+for\s+future", re.I)
NON_GRANT_CONTEXT = re.compile(
    r"\b(legal\s+fees|accounting\s+fees|other\s+expenses|other\s+assets|other\s+income"
    r"|other\s+decreases|other\s+increases|other\s+liabilities|taxes|depreciation"
    r"|investments?|corporate\s+stock|land|balance\s+sheet|compensation|officers?"
    r"|capital\s+gains?|dividends|interest|professional\s+fees|program\s+expenses)\b",
    re.I,
)
_MONEY = re.compile(r"^\$?\s?[\d,]+(?:\.\d{2})?$")

DEFAULT_TOLERANCE = 0.005
MIN_ROWS = 3


@dataclass(frozen=True)
class GrantRow:
    """One recovered grant."""

    page: int | None
    name: str
    amount: float
    cells: tuple[str, ...]


@dataclass(frozen=True)
class CandidateTable:
    page: int | None
    amount_header: str
    rows: tuple[GrantRow, ...]
    totals: tuple[float, ...]
    context: str = ""

    @property
    def grant_context(self) -> bool:
        """Does the heading, or the amount column's own name, say grants?"""
        if GRANT_AMOUNT_HEADER.search(self.amount_header or ""):
            return True
        return bool(GRANT_CONTEXT.search(self.context)) and not NON_GRANT_CONTEXT.search(self.context)

    @property
    def group(self) -> str:
        """Which statement this table belongs to.

        Filers split one schedule across dozens of pages under a repeated
        heading, so the heading identifies the statement, not the table.
        """
        key = re.sub(r"\s+", " ", (self.context or self.amount_header or "")).strip().lower()
        key = re.sub(r"\bpage\s*\d+( of \d+)?\b", "", key).strip()
        return f"{'future' if FUTURE_PAYMENT.search(key) else 'paid'}|{key[:60]}"

    @property
    def sum(self) -> float:
        return sum(row.amount for row in self.rows)


@dataclass(frozen=True)
class Extraction:
    """What the selector concluded for one filing."""

    outcome: str                      # see OUTCOMES
    declared: float
    extracted: float = 0.0
    error: float | None = None
    pages: tuple[int, ...] = ()
    rows: tuple[GrantRow, ...] = ()
    amount_headers: tuple[str, ...] = ()
    candidate_tables: int = 0
    stated_totals: tuple[float, ...] = field(default=())

    @property
    def reconciled(self) -> bool:
        return self.outcome == "reconciled"


OUTCOMES = ("reconciled", "no_reconciling_run", "no_candidate_tables", "no_declared_amount")

# A failure to reconcile says nothing about whose fault it is. These three
# split it: whether the filing's grant list is in the document at all.
# Without the split, an absent attachment and a selector bug look identical,
# and the first is a ceiling while the second is a backlog.
DIAGNOSES = ("list_present", "list_partial", "list_absent")

def diagnose(elements: Sequence[dict], declared: float,
             min_rows: int = 8, name_share: float = 0.6) -> str:
    """For a filing that did not reconcile, say whether its list is there.

    ``list_absent`` means no table anywhere names recipients — the attachment
    is not in the IRS image, and no amount of extraction work recovers it
    (Bezos 2021 carries Attachments A and B and simply omits the charitable
    listing). ``list_partial`` means recipients are named but the money found
    is under half the declared total. ``list_present`` means the list is
    sitting in the parse and the selector failed to pick it.
    """
    tables = candidate_tables(elements, declared)
    grantish = [
        table for table in tables
        if len(table.rows) >= min_rows
        and sum(1 for row in table.rows if _recipient_like(row.name)) >= name_share * len(table.rows)
    ]
    if not grantish:
        return "list_absent"
    if sum(table.sum for table in grantish) < declared * 0.5:
        return "list_partial"
    return "list_present"


_FORM_LABEL = re.compile(r"^\s*(part\s|[0-9]+[a-z]?\s|line\s|total|sub-?total|\(|see\b|check\b)", re.I)


def _recipient_like(name: str) -> bool:
    """Does this row name an organisation rather than a form line?

    Capital-gains and revenue-analysis tables are long and full of money, so
    row count alone mistakes them for grant lists.
    """
    text = (name or "").strip()
    if len(text) < 6 or _FORM_LABEL.match(text):
        return False
    return len(re.findall(r"[A-Za-z]{2,}", text)) >= 2



class _TableParser(HTMLParser):
    """Unstructured serialises tables as ``text_as_html``; pull out the cells."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            if self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_money(cell: str) -> float | None:
    """A cell is money only if it is *entirely* a number — never a substring."""
    text = (cell or "").strip()
    if not _MONEY.fullmatch(text):
        return None
    try:
        return float(re.sub(r"[^\d.]", "", text))
    except ValueError:
        return None


def _pick_column(header: Sequence[str], patterns: Iterable[str]) -> int | None:
    lowered = [h.lower() for h in header]
    for pattern in patterns:
        for index, head in enumerate(lowered):
            if re.search(pattern, head):
                return index
    return None


def candidate_tables(elements: Sequence[dict],
                     declared: float | None = None) -> list[CandidateTable]:
    """Every table carrying an amount column, in page order.

    A table with no header match falls back to its most money-dense column,
    which is what rescues attachments whose header row OCR'd badly.

    ``declared`` lets a grand-total row be recognised by its value. Text
    matching is not enough: the Wyss Foundation's grant schedule ends with
    ``['', '', '', 125722675]`` — every label cell empty — and counting it as
    a gift makes the schedule sum to exactly twice the declared total.
    """
    found: list[CandidateTable] = []
    context = ""
    for element in elements:
        metadata = element.get("metadata") or {}
        if element.get("type") in ("Title", "Header", "FigureCaption"):
            # Elements arrive in document order, so the last heading seen is
            # the one this table sits under.
            text = " ".join((element.get("text") or "").split())
            if text and not text.startswith("efile GRAPHIC"):
                context = text
            continue
        if element.get("type") != "Table":
            continue
        parser = _TableParser()
        parser.feed(metadata.get("text_as_html") or "")
        if len(parser.rows) < 2:
            continue

        header = parser.rows[0]
        amount_ix = _pick_column(header, AMOUNT_HEADERS)
        if amount_ix is None:
            width = max(len(row) for row in parser.rows)
            density = [
                (sum(1 for row in parser.rows[1:] if i < len(row) and parse_money(row[i]) is not None), i)
                for i in range(width)
            ]
            best, amount_ix = max(density) if density else (0, None)
            if amount_ix is None or best < 2:
                continue
        name_ix = _pick_column(header, NAME_HEADERS)

        rows: list[GrantRow] = []
        totals: list[float] = []
        for row in parser.rows[1:]:
            amount = parse_money(row[amount_ix]) if amount_ix < len(row) else None
            if amount is None:
                continue
            if _TOTAL_ROW.search(" ".join(row)):
                totals.append(amount)
                continue
            if declared and abs(amount - declared) <= max(1.0, declared * 1e-6):
                totals.append(amount)   # the grand total, however it is labelled
                continue
            if amount == 0:
                continue   # worksheet zeros pad a run toward MIN_ROWS for free
            name = row[name_ix] if (name_ix is not None and name_ix < len(row)) else max(row, key=len)
            if PLACEHOLDER.search(name):
                continue  # a pointer to the attachment, not a grant in it
            rows.append(GrantRow(metadata.get("page_number"), name, amount, tuple(row)))

        # A table of form lines is still "money in a column": J&J's Part I
        # reads Interest on savings = 3, Dividends = 4 — those are line
        # numbers, and 62,684 of them once the search is free to combine
        # statements. Two cheap shape tests throw them out.
        if rows:
            tiny = sum(1 for row in rows if row.amount < 100)
            if tiny > len(rows) / 2:
                rows = []
        if rows:
            # Prose, not names. Schusterman's Part VIII-A reads "1Reality
            # Trips: Reality is a weeklong journey in Israel for..." — a
            # narrative of charitable activities with dollar figures beside
            # it. Recipient cells are names and addresses, and stay short.
            lengths = sorted(len(row.name) for row in rows)
            if lengths and lengths[len(lengths) // 2] > 110:
                rows = []
        if rows:
            found.append(
                CandidateTable(
                    page=metadata.get("page_number"),
                    amount_header=header[amount_ix] if amount_ix < len(header) else "",
                    rows=tuple(rows),
                    totals=tuple(totals),
                    context=context,
                )
            )
    return sorted(found, key=lambda table: (table.page or 0))


def extract(elements: Sequence[dict], declared: float,
            tolerance: float = DEFAULT_TOLERANCE) -> Extraction:
    """Find the run of tables reconciling to ``declared``.

    Runs must be contiguous in page order — a grant list is not interleaved
    with other schedules — which keeps the search quadratic over a few dozen
    tables rather than exponential over their subsets.
    """
    if not declared or declared <= 0:
        return Extraction(outcome="no_declared_amount", declared=declared)

    tables = candidate_tables(elements, declared)
    if not tables:
        return Extraction(outcome="no_candidate_tables", declared=declared)

    # Search only tables under a grant heading when any exist. Falling back to
    # everything keeps filings whose headings OCR'd badly in play, but they
    # then have to clear the same reconciliation bar on their own.
    labelled = [t for t in tables if t.grant_context]
    if labelled:
        tables = labelled

    def _finish(run: list[CandidateTable], running: float) -> Extraction | None:
        error = abs(running - declared) / declared
        row_count = sum(len(table.rows) for table in run)
        if error > tolerance or row_count < MIN_ROWS:
            return None
        return Extraction(
            outcome="reconciled", declared=declared, extracted=running, error=error,
            pages=tuple(sorted({t.page for t in run if t.page is not None})),
            rows=tuple(row for table in run for row in table.rows),
            amount_headers=tuple(sorted({t.amount_header for t in run if t.amount_header})),
            candidate_tables=len(tables),
            stated_totals=tuple(v for table in run for v in table.totals),
        )

    # Statements first. A schedule is routinely split over dozens of pages
    # under one heading, and a filing may carry several statements at once
    # (cash and non-cash; paid and approved-for-future-payment). Choosing
    # whole statements answers "which schedules add up to the declared
    # total" directly, where a contiguous page run cannot express "these two
    # sections but not the one between them".
    groups: dict[str, list[CandidateTable]] = {}
    for table in tables:
        groups.setdefault(table.group, []).append(table)
    best: Extraction | None = None
    if 1 <= len(groups) <= 14:
        keys = list(groups)
        for mask in range(1, 1 << len(keys)):
            run = [t for i, key in enumerate(keys) if mask >> i & 1 for t in groups[key]]
            candidate = _finish(run, sum(t.sum for t in run))
            if candidate and (best is None or candidate.error < best.error):
                best = candidate
    if best is not None:
        return best

    for start in range(len(tables)):
        running = 0.0
        for end in range(start, len(tables)):
            running += tables[end].sum
            run = tables[start : end + 1]
            error = abs(running - declared) / declared
            row_count = sum(len(table.rows) for table in run)
            if error <= tolerance and row_count >= MIN_ROWS:
                if best is None or (error, -row_count) < (best.error, -len(best.rows)):
                    best = Extraction(
                        outcome="reconciled",
                        declared=declared,
                        extracted=running,
                        error=error,
                        pages=tuple(sorted({t.page for t in run if t.page is not None})),
                        rows=tuple(row for table in run for row in table.rows),
                        amount_headers=tuple(sorted({t.amount_header for t in run if t.amount_header})),
                        candidate_tables=len(tables),
                        stated_totals=tuple(v for table in run for v in table.totals),
                    )
            if running > declared * (1 + tolerance):
                break  # adding more tables can only overshoot further

    if best is not None:
        return best
    return Extraction(outcome="no_reconciling_run", declared=declared, candidate_tables=len(tables))


def load_elements(path: str | Path) -> list[dict]:
    """Read an Unstructured result, accepting both the bare list and the
    job-wrapped ``{"elements": [...]}`` shapes."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        return data.get("elements") or []
    return data
