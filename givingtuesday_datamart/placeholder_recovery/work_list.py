"""The work list: stage 0, the filings a run fetches and reads.

    python -m givingtuesday_datamart.placeholder_recovery work-list

One ``pf_placeholder_filings`` row per placeholder filing, built from the
loaded tables at the start of each run and never from GivingTuesday's
one-off extract.

**The rule.** ``privategrants_current`` joined to ``basic_fields_pf_current``
over every tax year they hold. A filing is on the list when its pointer rows
(``classifier``) sum to more than zero and to at least half of grants paid,
Part I line 25 column (d), ``arecgpdcprps``. The filers in
``data/placeholder_recovery/exclusions.csv`` are left out, by EIN. A filing
whose rows are mostly marked "I", for individual, stays on the list and
carries ``filer_marked_individual``.

**The amount can be too large.** The rule has a floor and no ceiling. Line
25 states grants paid twice: column (a) per books, gifts not in cash
included, and column (d) in cash. A filing whose placeholder rows hold more
than both, by more than the tolerance a list is held to, stays on the list
and carries ``placeholder_exceeds_declared``: a filer can enter its grants
approved for future payment as a second placeholder row, and the list that
adds up then holds both schedules.

**The future amount.** Grants approved for future payment, line 3b, are a
table of their own, ``privategrants_future_current``. A filing on the list
carries the amount on its placeholder rows there (``placeholder_future``,
zero when it has none): what a future-payment list must add up to. The rows
are those of the filing itself, under the url the paid rows have, so an
amount never comes from another version of the return. The amount puts no
filing on the list and takes none off; ``future_without_paid`` counts the
filings that have a future placeholder and are not on the list.

**The build.** One query finds every placeholder filing (``QUERY``, about a
minute), ``build`` turns its rows into the list, and ``rebuild`` replaces
the table's rows in one transaction, so a reader sees the old list or the
new one and never half of each.

**Tests.** The store is the small interface the other tables use:
``PostgresStore`` over a session, ``MemoryStore`` for tests. ``build`` takes
the query's rows as mappings, so nothing here needs Postgres to be exercised.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Collection, Iterable, Mapping, Sequence

from sqlalchemy import text

from givingtuesday_datamart._internal.bulk import multi_row_insert, multi_row_params
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.attachment_grants import DEFAULT_TOLERANCE
from givingtuesday_datamart.irs_source import parse_object_id
from givingtuesday_datamart.placeholder_recovery import classifier
from givingtuesday_datamart.placeholder_recovery.exclusions import Exclusion, load_exclusions

TABLE = "pf_placeholder_filings"
CHUNK = 500
TEXTS_KEPT = 2
# The lower bound of each band, on the paid amount of the placeholder rows.
BANDS: tuple[tuple[str, Decimal], ...] = (
    ("A", Decimal(100_000_000)), ("B", Decimal(10_000_000)), ("C", Decimal(1_000_000)), ("D", Decimal(0)))
# Tax years from here on are the ones the frame measured; the earlier ones
# have never been fetched.
MEASURED_FROM = 2020

DDL = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        object_id               text PRIMARY KEY,         -- the 18 digits in the filing's url
        filerein                text NOT NULL,
        filer_name              text,
        taxyear                 integer NOT NULL,
        taxperend               date,
        declared_paid           numeric(18,2) NOT NULL,   -- grants paid, Part I line 25 column (d), in cash
        declared_books          numeric(18,2),            -- the same line, column (a), per books
        placeholder_paid        numeric(18,2) NOT NULL,   -- the placeholder rows' amounts, summed
        placeholder_rows        integer NOT NULL,
        placeholder_texts       text[] NOT NULL,          -- the first two, largest amount first
        placeholder_future      numeric(18,2) NOT NULL DEFAULT 0,   -- the placeholder rows of line 3b, summed
        placeholder_future_rows integer NOT NULL DEFAULT 0,
        band                    text NOT NULL,            -- A | B | C | D, cut on placeholder_paid
        filer_marked_individual boolean NOT NULL,         -- the filing's rows are mostly marked "I"
        placeholder_exceeds_declared boolean NOT NULL DEFAULT false,   -- placeholder_paid passes both columns of line 25
        classifier_version      text NOT NULL,
        source_version          text NOT NULL,            -- privategrants_current._source_version
        built_at                timestamptz NOT NULL
    )
    """,
    f"CREATE INDEX IF NOT EXISTS {TABLE}_filer_year ON {TABLE} (filerein, taxyear)",
    # For a table made before 2026-09-29, when the two columns were added.
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS declared_books numeric(18,2)",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS placeholder_exceeds_declared boolean NOT NULL DEFAULT false",
    # For a table made before 2026-09-30, when the future amount was added.
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS placeholder_future numeric(18,2) NOT NULL DEFAULT 0",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS placeholder_future_rows integer NOT NULL DEFAULT 0",
)
FUTURE = "privategrants_future_current"

# The placeholder rows of line 3b, one row per filing that has any: the same
# pattern on the future-payment table's name columns.
FUTURE_PLACEHOLDERS = f"""
    SELECT g.filerein, g.taxyear, g.url, coalesce(sum({classifier.amount_sql('g.' + classifier.FUTURE_AMOUNT)}), 0)
               AS placeholder_future, count(*) AS placeholder_future_rows
    FROM {FUTURE} g
    CROSS JOIN LATERAL (SELECT {classifier.name_sql('g', classifier.FUTURE_NAMES)} AS name OFFSET 0) n
    WHERE g.taxyear ~ '{classifier.TAX_YEAR}'
      AND {classifier.prefilter_sql('g', classifier.FUTURE_NAMES)}
      AND {classifier.pointer_sql('n.name')}
    GROUP BY g.filerein, g.taxyear, g.url
"""

# One row per placeholder filing, the excluded filers' among them. The
# pointer rows are found first, in one pass over the grant rows; the columns
# that need every row of a filing (its url, the mark) are then read for the
# filings that passed the rule, through the (filerein, taxyear) index.
QUERY = f"""
WITH pointer_rows AS (
    SELECT g.filerein, g.taxyear, n.name, {classifier.amount_sql('g.sigocpyamoun')} AS amount
    FROM privategrants_current g
    CROSS JOIN LATERAL (SELECT {classifier.name_sql('g')} AS name OFFSET 0) n
    WHERE g.taxyear ~ '{classifier.TAX_YEAR}'
      AND {classifier.prefilter_sql('g')}
      AND {classifier.pointer_sql('n.name')}
),
placeholder AS (
    SELECT filerein, taxyear, coalesce(sum(amount), 0) AS placeholder_paid, count(*) AS placeholder_rows,
           array_agg(name ORDER BY amount DESC NULLS LAST, name) AS placeholder_texts
    FROM pointer_rows
    GROUP BY filerein, taxyear
),
listed AS (
    SELECT p.*, d.declared_paid, d.declared_books
    FROM placeholder p
    JOIN (SELECT filerein, taxyear, {classifier.amount_sql('arecgpdcprps')} AS declared_paid,
                 {classifier.amount_sql('arecprexpnss')} AS declared_books
          FROM basic_fields_pf_current
          WHERE taxyear ~ '{classifier.TAX_YEAR}') d USING (filerein, taxyear)
    WHERE d.declared_paid > 0 AND p.placeholder_paid > 0 AND p.placeholder_paid >= 0.5 * d.declared_paid
),
future AS ({FUTURE_PLACEHOLDERS})
SELECT l.filerein, l.taxyear, l.declared_paid, l.declared_books, l.placeholder_paid, l.placeholder_rows,
       l.placeholder_texts,
       f.url, f.urls, f.filer_name, f.taxperend, f.source_version, f.filer_marked_individual,
       coalesce(u.placeholder_future, 0) AS placeholder_future,
       coalesce(u.placeholder_future_rows, 0) AS placeholder_future_rows
FROM listed l
CROSS JOIN LATERAL (
    SELECT max(g.url) AS url, count(DISTINCT g.url) AS urls, max(g.filername1) AS filer_name,
           max(g.taxperend) AS taxperend, max(g._source_version) AS source_version,
           coalesce((mode() WITHIN GROUP (ORDER BY g.sigocpyrfsta)) = 'I', false) AS filer_marked_individual
    FROM privategrants_current g
    WHERE g.filerein = l.filerein AND g.taxyear = l.taxyear
) f
LEFT JOIN future u ON u.filerein = l.filerein AND u.taxyear = l.taxyear AND u.url = f.url
ORDER BY l.taxyear, l.filerein
"""

# The filings with a future placeholder that the list does not hold, by why:
# the filing is on the list under another url (another version of the
# return), its filer-year has paid placeholder rows and fails the rule or is
# an excluded filer's, or it has no paid placeholder row at all. Reads the
# table, so it follows a rebuild.
FUTURE_WITHOUT_PAID = f"""
WITH future AS ({FUTURE_PLACEHOLDERS}),
paid AS (
    SELECT DISTINCT g.filerein, g.taxyear
    FROM privategrants_current g
    JOIN (SELECT DISTINCT filerein FROM future) u USING (filerein)
    CROSS JOIN LATERAL (SELECT {classifier.name_sql('g')} AS name OFFSET 0) n
    WHERE {classifier.prefilter_sql('g')} AND {classifier.pointer_sql('n.name')}
)
SELECT CASE WHEN w.object_id IS NOT NULL THEN 'on the list under another url'
            WHEN p.filerein IS NOT NULL THEN 'paid placeholder, not on the list'
            ELSE 'no paid placeholder' END AS why,
       count(*) AS filings, count(DISTINCT u.filerein) AS filers, sum(u.placeholder_future) AS dollars
FROM future u
LEFT JOIN {TABLE} w ON w.filerein = u.filerein AND w.taxyear::text = u.taxyear
LEFT JOIN paid p ON p.filerein = u.filerein AND p.taxyear = u.taxyear
WHERE u.placeholder_future > 0
  AND NOT (w.object_id IS NOT NULL AND position(w.object_id in u.url) > 0)
GROUP BY 1
ORDER BY 1
"""


@dataclass(frozen=True)
class PlaceholderFiling:
    """One ``pf_placeholder_filings`` row. Field order is the table's column
    order; the first field is the primary key."""

    object_id: str
    filerein: str
    filer_name: str | None
    taxyear: int
    taxperend: date | None
    declared_paid: Decimal
    declared_books: Decimal | None
    placeholder_paid: Decimal
    placeholder_rows: int
    placeholder_texts: list[str]
    placeholder_future: Decimal
    placeholder_future_rows: int
    band: str
    filer_marked_individual: bool
    placeholder_exceeds_declared: bool
    classifier_version: str
    source_version: str
    built_at: datetime


COLUMNS = tuple(f.name for f in fields(PlaceholderFiling))
KEY_COLUMNS = COLUMNS[:1]
_SELECT = f"SELECT {', '.join(COLUMNS)} FROM {TABLE}"


def band(placeholder_paid: Decimal) -> str:
    """A at $100M and over, B at $10M, C at $1M, D below."""
    return next(label for label, floor in BANDS if placeholder_paid >= floor)


def exceeds_declared(placeholder_paid: Decimal, declared_paid: Decimal, declared_books: Decimal | None,
                     tolerance: float = DEFAULT_TOLERANCE) -> bool:
    """Do the placeholder rows hold more than the filing declares paid, in
    cash and per books both, by more than the tolerance?"""
    declared = max(declared_paid, declared_books if declared_books is not None else declared_paid)
    return placeholder_paid > declared * (1 + Decimal(str(tolerance)))


def _texts(names: Iterable[str | None]) -> list[str]:
    """The first ``TEXTS_KEPT`` distinct placeholder texts, in the order given."""
    distinct = dict.fromkeys(name for name in names if name)
    return list(distinct)[:TEXTS_KEPT]


def _date(value: object) -> date | None:
    """``taxperend`` is text in the loaded tables, "2021-12-31"."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def filing_from_row(row: Mapping, built_at: datetime) -> PlaceholderFiling:
    """One row of ``QUERY`` as a work-list row. Raises ``ValueError`` when the
    url carries no object id, since nothing could fetch the filing."""
    paid, declared = Decimal(row["placeholder_paid"]), Decimal(row["declared_paid"])
    books = Decimal(row["declared_books"]) if row.get("declared_books") is not None else None
    return PlaceholderFiling(
        object_id=parse_object_id(row["url"] or ""),
        filerein=row["filerein"],
        filer_name=row["filer_name"],
        taxyear=int(row["taxyear"]),
        taxperend=_date(row["taxperend"]),
        declared_paid=declared,
        declared_books=books,
        placeholder_paid=paid,
        placeholder_rows=int(row["placeholder_rows"]),
        placeholder_texts=_texts(row["placeholder_texts"] or ()),
        placeholder_future=Decimal(row.get("placeholder_future") or 0),
        placeholder_future_rows=int(row.get("placeholder_future_rows") or 0),
        band=band(paid),
        filer_marked_individual=bool(row["filer_marked_individual"]),
        placeholder_exceeds_declared=exceeds_declared(paid, declared, books),
        classifier_version=classifier.CLASSIFIER_VERSION,
        source_version=row["source_version"],
        built_at=built_at,
    )


@dataclass
class WorkList:
    """What ``build`` made of the query's rows: the list, the filings of the
    excluded filers, and the rows that could not be listed, with why."""

    filings: list[PlaceholderFiling] = field(default_factory=list)
    excluded: list[PlaceholderFiling] = field(default_factory=list)
    unlisted: list[tuple[str, str, str]] = field(default_factory=list)      # filerein, taxyear, why


def build(rows: Iterable[Mapping], exclusions: Mapping[str, Exclusion],
          built_at: datetime | None = None) -> WorkList:
    """The work list from the query's rows: every placeholder filing, less
    the excluded filers'. A row whose url holds no object id is set aside
    and logged. Two filings under one object id raise ``ValueError``: the
    table is keyed on it, and the loaded tables hold one url a filer-year."""
    built_at = built_at or datetime.now(timezone.utc)
    found = WorkList()
    seen: dict[str, PlaceholderFiling] = {}
    for row in rows:
        if int(row.get("urls") or 1) > 1:
            logger.warning("%s %s has %s urls in privategrants_current; the last is taken",
                           row["filerein"], row["taxyear"], row["urls"])
        try:
            filing = filing_from_row(row, built_at)
        except ValueError as exc:
            logger.warning("%s %s left off the work list: %s", row["filerein"], row["taxyear"], exc)
            found.unlisted.append((row["filerein"], str(row["taxyear"]), str(exc)))
            continue
        other = seen.setdefault(filing.object_id, filing)
        if other is not filing:
            raise ValueError(f"object id {filing.object_id} is the filing of {other.filerein} {other.taxyear} "
                             f"and of {filing.filerein} {filing.taxyear}")
        (found.excluded if filing.filerein in exclusions else found.filings).append(filing)
    return found


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class WorkListStore(ABC):
    """Rows in, rows out. ``replace_all`` is one transaction."""

    @abstractmethod
    def ensure_table(self) -> None: ...

    @abstractmethod
    def replace_all(self, rows: Sequence[PlaceholderFiling]) -> None:
        """Make the table hold ``rows`` and nothing else."""

    @abstractmethod
    def all(self) -> list[PlaceholderFiling]: ...

    def get(self, object_ids: Iterable[str]) -> dict[str, PlaceholderFiling]:
        wanted = set(object_ids)
        return {row.object_id: row for row in self.all() if row.object_id in wanted}


class PostgresStore(WorkListStore):
    def __init__(self, session) -> None:
        self.session = session

    def ensure_table(self) -> None:
        for statement in DDL:
            self.session.execute(text(statement))
        self.session.commit()

    def replace_all(self, rows: Sequence[PlaceholderFiling]) -> None:
        """Delete, insert, commit: the list is replaced whole or not at all."""
        try:
            self.session.execute(text(f"DELETE FROM {TABLE}"))
            for start in range(0, len(rows), CHUNK):
                chunk = rows[start:start + CHUNK]
                self.session.execute(text(multi_row_insert(TABLE, COLUMNS, KEY_COLUMNS, len(chunk))),
                                     multi_row_params([asdict(row) for row in chunk], COLUMNS))
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def all(self) -> list[PlaceholderFiling]:
        found = self.session.execute(text(f"{_SELECT} ORDER BY placeholder_paid DESC, object_id")).mappings().all()
        return [_from_table(row) for row in found]

    def get(self, object_ids: Iterable[str]) -> dict[str, PlaceholderFiling]:
        ids = list(dict.fromkeys(object_ids))
        if not ids:
            return {}
        found = self.session.execute(text(f"{_SELECT} WHERE object_id = ANY(:ids)"), {"ids": ids}).mappings().all()
        return {row["object_id"]: _from_table(row) for row in found}


def _from_table(row: Mapping) -> PlaceholderFiling:
    return PlaceholderFiling(**{c: (list(row[c]) if c == "placeholder_texts" else row[c]) for c in COLUMNS})


class MemoryStore(WorkListStore):
    """A dict, plus the size of every replacement, for tests."""

    def __init__(self, rows: Iterable[PlaceholderFiling] = ()) -> None:
        self.rows: dict[str, PlaceholderFiling] = {row.object_id: row for row in rows}
        self.commits: list[int] = []

    def ensure_table(self) -> None:
        pass

    def replace_all(self, rows: Sequence[PlaceholderFiling]) -> None:
        self.rows = {row.object_id: row for row in rows}
        self.commits.append(len(rows))

    def all(self) -> list[PlaceholderFiling]:
        return sorted(self.rows.values(), key=lambda row: (-row.placeholder_paid, row.object_id))


def _store(session) -> WorkListStore:
    return session if isinstance(session, WorkListStore) else PostgresStore(session)


def ensure_table(session) -> None:
    """Create ``pf_placeholder_filings`` and its index if they do not exist."""
    _store(session).ensure_table()


# ---------------------------------------------------------------------------
# Building and reading the table
# ---------------------------------------------------------------------------


def query_filings(session) -> list[Mapping]:
    """Every placeholder filing in the loaded tables, the excluded filers'
    among them: the rows of ``QUERY``."""
    return list(session.execute(text(QUERY)).mappings().all())


def rebuild(session, *, exclusions: Mapping[str, Exclusion] | None = None, rows: Iterable[Mapping] | None = None,
            store: WorkListStore | None = None, built_at: datetime | None = None) -> WorkList:
    """Build the work list from the loaded tables and replace the table with
    it, in one transaction. ``rows`` and ``store`` are for tests; without
    them the query runs on ``session`` and the table is written through it."""
    store = store if store is not None else _store(session)
    store.ensure_table()
    exclusions = exclusions if exclusions is not None else load_exclusions()
    found = build(rows if rows is not None else query_filings(session), exclusions, built_at)
    store.replace_all(found.filings)
    logger.info("work list: %d filings written to %s; %d filings of %d excluded filers left out; %d without an "
                "object id", len(found.filings), TABLE, len(found.excluded),
                len({row.filerein for row in found.excluded}), len(found.unlisted))
    return found


def future_without_paid(session) -> list[Mapping]:
    """The filings with a future placeholder that are not on the list, by
    why: the rows of ``FUTURE_WITHOUT_PAID``. They are counted, never added."""
    return list(session.execute(text(FUTURE_WITHOUT_PAID)).mappings().all())


def select(filings: Iterable[PlaceholderFiling], *, tax_years: Collection[int] | None = None,
           limit: int | None = None) -> list[PlaceholderFiling]:
    """The filings of ``tax_years`` (every year when None), largest
    placeholder amount first, and the first ``limit`` of them."""
    chosen = sorted((row for row in filings if tax_years is None or row.taxyear in tax_years),
                    key=lambda row: (-row.placeholder_paid, row.object_id))
    return chosen if limit is None else chosen[:limit]


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _billions(amount: Decimal) -> str:
    return f"${amount / Decimal(10 ** 9):,.2f}B"


def _line(label: str, rows: Sequence[PlaceholderFiling]) -> str:
    declared = sum((row.declared_paid for row in rows), Decimal(0))
    paid = sum((row.placeholder_paid for row in rows), Decimal(0))
    filers = len({row.filerein for row in rows})
    return f"  {label:<34}{len(rows):>8,}{filers:>8,}{_billions(declared):>12}{_billions(paid):>12}"


def summary(found: WorkList) -> str:
    """The list's counts and dollars: in all, by tax year and by band."""
    listed, excluded = found.filings, found.excluded
    recent = [row for row in listed if row.taxyear >= MEASURED_FROM]
    earlier = [row for row in listed if row.taxyear < MEASURED_FROM]
    lines = [f"  {'':<34}{'filings':>8}{'filers':>8}{'declared':>12}{'on rows':>12}",
             _line("every placeholder filing", listed + excluded),
             _line("the excluded filers", excluded),
             _line("the work list", listed),
             _line("  of which marked individual", [row for row in listed if row.filer_marked_individual]),
             _line("  of which over line 25", [row for row in listed if row.placeholder_exceeds_declared]),
             _line("  of which with a future amount", [row for row in listed if row.placeholder_future > 0]),
             _line(f"  tax years {MEASURED_FROM} on", recent),
             _line(f"  tax years before {MEASURED_FROM}", earlier)]
    versions = Counter(row.source_version for row in listed)
    lines.append("  source version: " + ", ".join(f"{version} ({n:,} filings)" for version, n in versions.items()))

    lines.append(f"\n  {'band':<6}{f'{MEASURED_FROM} on':>10}{'on rows':>12}{f'before {MEASURED_FROM}':>14}{'on rows':>12}")
    for label, _ in BANDS:
        new = [row for row in recent if row.band == label]
        old = [row for row in earlier if row.band == label]
        lines.append(f"  {label:<6}{len(new):>10,}{_billions(sum((r.placeholder_paid for r in new), Decimal(0))):>12}"
                     f"{len(old):>14,}{_billions(sum((r.placeholder_paid for r in old), Decimal(0))):>12}")

    by_year: dict[int, list[PlaceholderFiling]] = defaultdict(list)
    for row in listed:
        by_year[row.taxyear].append(row)
    lines.append(f"\n  {'tax year':<10}{'filings':>8}{'on rows':>12}{'marked individual':>20}")
    for year in sorted(by_year):
        rows = by_year[year]
        lines.append(f"  {year:<10}{len(rows):>8,}{_billions(sum((r.placeholder_paid for r in rows), Decimal(0))):>12}"
                     f"{sum(r.filer_marked_individual for r in rows):>20,}")
    return "\n".join(lines)


def future_summary(found: WorkList, without: Iterable[Mapping] = ()) -> str:
    """The future-payment placeholders: on the list, which is where a future
    list can load, and not on it (the rows of ``future_without_paid``)."""
    lines = [f"  {'future-payment placeholders':<46}{'filings':>8}{'filers':>8}{'on rows':>12}"]
    for label, rows in (("on the work list", found.filings),
                        (f"  tax years {MEASURED_FROM} on", [r for r in found.filings if r.taxyear >= MEASURED_FROM])):
        rows = [row for row in rows if row.placeholder_future > 0]
        dollars = sum((row.placeholder_future for row in rows), Decimal(0))
        lines.append(f"  {label:<46}{len(rows):>8,}{len({row.filerein for row in rows}):>8,}{_billions(dollars):>12}")
    for row in without:
        label = f"not on it: {row['why']}"
        lines.append(f"  {label:<46}{row['filings']:>8,}{row['filers']:>8,}{_billions(Decimal(row['dollars'])):>12}")
    return "\n".join(lines)
