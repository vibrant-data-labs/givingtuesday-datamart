"""What the recovered rows carry for the matcher, measured on a frame.

    python -m givingtuesday_datamart.proofs.recovered_rows address --policy v2 \
        --sample data/placeholder_recovery/placeholder_sample_1000.csv
    python -m givingtuesday_datamart.proofs.recovered_rows names --policy v2 \
        --sample data/placeholder_recovery/placeholder_sample_1000.csv

The rows are the ones a load would write for the frame's filings: the rows
of every reconciled paid list, derived again from ``page_verdicts`` and
``page_readings`` under the policy, at no model cost. The selection is the
loader's (``placeholder_recovery.loader.select_paid``): the pages read are
its whole input and the frame's paid amount its only target.

``address`` says what each row has for the matcher to work with. The
matcher narrows its search by zip, or by an exact name when the state is
the same, and scores the address as one string; it never needs the street
split from the city. So the question is state and zip, not parsing:
``state_zip`` reads them off the end of the address, and ``split_name``
takes them from a name printed as "Stanford University, Stanford, CA".
Both live with the loader, in ``placeholder_recovery.address``.

``names`` matches each row's name, exactly, against the names of the
matcher's universe, at four levels of cleaning, and counts a match only
when the name belongs to one organization (or the row's state picks one
among several). Where a row has a state, the matched organization's state
is the check on the name-only match a row without an address would get.
The level chosen is the matcher's ``clean_name`` since input shape 3.
"""

from __future__ import annotations

import argparse
import collections
import csv
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from givingtuesday_datamart.grant_matching import clean_name, normalize_org_name
from givingtuesday_datamart.page_readings import frame_pages
from givingtuesday_datamart.page_verdicts import accepted_readings, load_policy
from givingtuesday_datamart.placeholder_recovery.address import split_name, state_zip
from givingtuesday_datamart.placeholder_recovery.loader import select_paid

logger = logging.getLogger(__name__)

FRAME = Path("data/placeholder_recovery/placeholder_sample_1000.csv")
ADDRESS_OUT = Path("data/placeholder_recovery/placeholder_recovered_address.csv")
NAMES_OUT = Path("data/placeholder_recovery/placeholder_recovered_names.csv")
UNIVERSE_VIEWS = ("basic_fields_unique_names_view", "basic_fields_pf_unique_names_view",
                  "corrections_unique_names_view")

# --- name cleaning -----------------------------------------------------------
ABBREVIATIONS = {
    "univ": "university", "assn": "association", "assoc": "association", "fdn": "foundation",
    "fndn": "foundation", "fnd": "foundation", "ctr": "center", "cntr": "center", "centre": "center",
    "natl": "national", "intl": "international", "inst": "institute", "hosp": "hospital", "soc": "society",
    "dept": "department", "svc": "service", "svcs": "service", "services": "service", "cmty": "community",
    "cmnty": "community", "sch": "school", "elem": "elementary", "st": "saint", "mt": "mount", "ft": "fort",
    "amer": "american", "mem": "memorial", "med": "medical", "cncl": "council", "org": "organization",
    "comm": "community", "coll": "college", "acad": "academy", "min": "ministries", "ministry": "ministries",
    "schools": "school", "programs": "program", "centers": "center", "churches": "church"}
STOPWORDS = frozenset({"the", "of", "for", "and", "a", "an", "at", "in", "on", "to"})


def _before(name: str) -> str:
    return normalize_org_name(" ".join((name or "").lower().split()))


def _abbreviated(name: str) -> str:
    return " ".join(ABBREVIATIONS.get(word, word) for word in clean_name(name).split())


def _no_stopwords(name: str) -> str:
    return " ".join(word for word in _abbreviated(name).split() if word not in STOPWORDS)


CHOSEN = "cleaned"
LEVELS: tuple[tuple[str, Callable[[str], str]], ...] = (
    ("the matcher's normaliser before input shape 3", _before), (CHOSEN, clean_name),
    ("cleaned, abbreviations expanded", _abbreviated), ("cleaned, abbreviations, stopwords out", _no_stopwords))


# --- the rows ----------------------------------------------------------------
@dataclass(frozen=True)
class Row:
    """One attachment row of a reconciled paid list."""

    band: str
    object_id: str
    filerein: str
    page: int
    name: str
    address: str
    amount: float


def recovered_rows(session, sample: Path, policy: dict, *,
                   filing_store=None, reading_store=None) -> tuple[list[Row], int]:
    """The rows a load would write for ``sample`` under ``policy``, and the
    number of filings they come from."""
    frame = list(csv.DictReader(sample.open()))
    pages = frame_pages(filing_store if filing_store is not None else session, [r["object_id"] for r in frame])
    by_filing: dict[str, list[int]] = collections.defaultdict(list)
    for oid, page in pages:
        by_filing[oid].append(page)
    accepted = accepted_readings(session, pages, policy, filing_store=filing_store, reading_store=reading_store)
    rows: list[Row] = []
    filings = 0
    for r in frame:
        oid = r["object_id"]
        readings = {page: accepted[(oid, page)][1] for page in by_filing.get(oid, ())
                    if accepted.get((oid, page), (None, None))[1] is not None}
        if not readings:
            continue
        found = select_paid(readings, float(r["placeholder_paid"]))
        if not found.reconciled:
            continue
        filings += 1
        rows.extend(Row(r["stratum"], oid, r["filerein"], row.page, row.name, row.cells[1], row.amount)
                    for row in found.rows)
    return rows, filings


@dataclass(frozen=True)
class Located:
    """A row with what the matcher can use: the name without its place, the
    state, the zip, and where the state came from."""

    row: Row
    name: str
    state: str | None
    zip5: str | None
    source: str          # address | name | ""

    @property
    def kind(self) -> str:
        if self.zip5:
            return "state and zip"
        if self.state:
            return "state only" if self.source == "address" else "no address, state in the name"
        return "address text, no US state" if self.row.address.strip() else "no address"


KINDS = ("state and zip", "state only", "no address, state in the name", "address text, no US state", "no address")


def locate(row: Row) -> Located:
    state, zip5 = state_zip(row.address)
    if state:
        return Located(row, row.name, state, zip5, "address")
    if not row.address.strip():
        name, state = split_name(row.name)
        if state:
            return Located(row, name, state, None, "name")
    return Located(row, row.name, None, None, "")


def address_coverage(rows: Sequence[Row]) -> tuple[list[dict], list[dict]]:
    """Rows and dollars by what the address gives, and the filings by how
    much of their list has no address at all."""
    count: collections.Counter = collections.Counter()
    dollars: collections.Counter = collections.Counter()
    empty: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for row in rows:
        kind = locate(row).kind
        count[kind] += 1
        dollars[kind] += row.amount
        empty[row.object_id][0] += 1
        empty[row.object_id][1] += not row.address.strip()
    total, money = sum(count.values()), sum(dollars.values())
    table = [{"address": kind, "rows": count[kind], "dollars": round(dollars[kind], 2),
              "rows_pct": round(100 * count[kind] / total, 1), "dollars_pct": round(100 * dollars[kind] / money, 1)}
             for kind in KINDS]
    shares: collections.Counter = collections.Counter()
    for n, without in empty.values():
        share = without / n
        shares["every row has an address" if not without else "under 10% without" if share < 0.1
               else "10% to 90% without" if share < 0.9 else "over 90% without"] += 1
    filings = [{"filings": label, "n": shares[label]} for label in
               ("every row has an address", "under 10% without", "10% to 90% without", "over 90% without")]
    return table, filings


# --- the names ---------------------------------------------------------------
OUTCOMES = ("one organization, state agrees", "several, the state picks one", "one organization, no state to check",
            "one organization, state disagrees", "several, unresolved", "no exact name")
MATCHED = OUTCOMES[:3]


def universe(session):
    """The names the matcher matches against, with each filer's states."""
    import pandas as pd
    from sqlalchemy import text

    frame = pd.concat([pd.read_sql(text(
        f"SELECT filerein_key, name1_key, name2_key, addressstate_key FROM public.{view}"), session.connection())
        for view in UNIVERSE_VIEWS], ignore_index=True).fillna("")
    frame["name"] = [" ".join(part.strip() for part in pair if part.strip())
                     for pair in zip(frame.name1_key, frame.name2_key)]
    return [(ein, name, state.upper()) for ein, name, state in
            zip(frame.filerein_key, frame.name, frame.addressstate_key) if name]


def name_index(names: Iterable[tuple[str, str, str]], clean: Callable[[str], str]) -> dict[str, frozenset[str]]:
    index: dict[str, set[str]] = collections.defaultdict(set)
    for ein, name, _ in names:
        key = clean(name)
        if key:
            index[key].add(ein)
    return {key: frozenset(eins) for key, eins in index.items()}


def states_of(names: Iterable[tuple[str, str, str]]) -> dict[str, frozenset[str]]:
    states: dict[str, set[str]] = collections.defaultdict(set)
    for ein, _, state in names:
        if state:
            states[ein].add(state)
    return {ein: frozenset(found) for ein, found in states.items()}


def outcome(key: str, state: str | None, index: Mapping[str, frozenset[str]],
            states: Mapping[str, frozenset[str]]) -> tuple[str, str | None]:
    """What an exact match on the cleaned name gives, and the EIN when it
    gives one. A name several organizations share is never matched unless
    the row's state picks exactly one of them."""
    eins = index.get(key)
    if not eins:
        return "no exact name", None
    if len(eins) == 1:
        (ein,) = eins
        if not state:
            return "one organization, no state to check", ein
        return ("one organization, state agrees" if state in states.get(ein, ()) else
                "one organization, state disagrees"), ein
    same = [ein for ein in eins if state and state in states.get(ein, ())]
    return ("several, the state picks one", same[0]) if len(same) == 1 else ("several, unresolved", None)


def name_match(rows: Sequence[Row], names: Sequence[tuple[str, str, str]]) -> dict[str, list[dict]]:
    """Exact matches by cleaning level, the state check, and both by the
    number of words in the cleaned name at the chosen level."""
    located = [locate(row) for row in rows]
    states = states_of(names)
    groups = (("with a state", lambda item: bool(item.state)), ("no state", lambda item: not item.state))
    levels, outcomes, words = [], [], []
    for label, clean in LEVELS:
        index = name_index(names, clean)
        tally: dict = collections.defaultdict(lambda: [0, 0.0])
        length: dict = collections.defaultdict(lambda: [0, 0, 0, 0.0])    # checkable, agree, name-only rows, dollars
        for item in located:
            key = clean(item.name)
            found, _ = outcome(key, item.state, index, states)
            group = groups[0][0] if item.state else groups[1][0]
            tally[(group, found)][0] += 1
            tally[(group, found)][1] += item.row.amount
            n = min(len(key.split()), 4)
            if found in ("one organization, state agrees", "one organization, state disagrees"):
                length[n][0] += 1
                length[n][1] += found == "one organization, state agrees"
            elif found == "one organization, no state to check":
                length[n][2] += 1
                length[n][3] += item.row.amount
        checked = sum(v[0] for v in length.values())
        agreed = sum(v[1] for v in length.values())
        for group, _ in groups:
            n = sum(v[0] for (g, _), v in tally.items() if g == group)
            money = sum(v[1] for (g, _), v in tally.items() if g == group)
            matched = [tally[(group, o)] for o in MATCHED]
            levels.append({
                "cleaning": label, "rows": group, "n": n, "dollars": round(money, 2),
                "matched_rows_pct": round(100 * sum(m[0] for m in matched) / n, 1),
                "matched_dollars_pct": round(100 * sum(m[1] for m in matched) / money, 1),
                "several_pct": round(100 * tally[(group, "several, unresolved")][0] / n, 1),
                "state_agrees_pct": round(100 * agreed / checked, 1), "state_checked": checked})
            if label == CHOSEN:
                outcomes.extend({"rows": group, "outcome": o, "n": tally[(group, o)][0],
                                 "dollars": round(tally[(group, o)][1], 2),
                                 "rows_pct": round(100 * tally[(group, o)][0] / n, 1),
                                 "dollars_pct": round(100 * tally[(group, o)][1] / money, 1)} for o in OUTCOMES)
        if label == CHOSEN:
            words = [{"words": f"{n}{' or more' if n == 4 else ''}", "state_checked": length[n][0],
                      "state_agrees_pct": round(100 * length[n][1] / length[n][0], 1) if length[n][0] else "",
                      "name_only_rows": length[n][2], "name_only_dollars": round(length[n][3], 2)}
                     for n in sorted(length) if n]
    return {"levels": levels, "outcomes": outcomes, "words": words}


# --- output ------------------------------------------------------------------
def _table(rows: Sequence[Mapping], title: str) -> None:
    if not rows:
        return
    print(f"\n{title}\n")
    columns = list(rows[0])
    print("| " + " | ".join(columns) + " |")
    print("|" + "|".join("---" for _ in columns) + "|")
    for row in rows:
        print("| " + " | ".join(f"{v:,}" if isinstance(v, int) else f"{v:,.2f}" if isinstance(v, float) and v > 1000
                                else str(v) for v in row.values()) + " |")


def _write(path: Path, tables: Mapping[str, Sequence[Mapping]]) -> None:
    columns = ["table"] + list(dict.fromkeys(column for rows in tables.values() for row in rows for column in row))
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for name, rows in tables.items():
            writer.writerows({"table": name, **row} for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for command, out in (("address", ADDRESS_OUT), ("names", NAMES_OUT)):
        p = sub.add_parser(command)
        p.add_argument("--sample", type=Path, default=FRAME)
        p.add_argument("--policy", default="v2")
        p.add_argument("--out", type=Path, default=out)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("givingtuesday_datamart").setLevel(logging.ERROR)   # else one line a filing with no PDF

    from givingtuesday_datamart._internal.db import get_session
    from givingtuesday_datamart.ingestion import datamart_config

    started = time.monotonic()
    with get_session(config=datamart_config()) as session:
        rows, filings = recovered_rows(session, args.sample, load_policy(args.policy))
        names = universe(session) if args.command == "names" else []
    print(f"{filings} reconciled paid lists, {len(rows):,} rows, "
          f"${sum(row.amount for row in rows) / 1e9:.2f}B")
    if args.command == "address":
        table, by_filing = address_coverage(rows)
        _table(table, "what the address gives")
        _table(by_filing, "filings by the share of their rows with no address")
        _write(args.out, {"address": table, "filings": by_filing})
    else:
        print(f"universe: {len(names):,} names of {len({ein for ein, _, _ in names}):,} filers")
        found = name_match(rows, names)
        _table(found["levels"], "exact matches on the name, by cleaning level")
        _table(found["outcomes"], f"outcomes at the chosen level ({CHOSEN})")
        _table(found["words"], "by words in the cleaned name")
        _write(args.out, found)
    print(f"\nwrote {args.out} in {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    main()
