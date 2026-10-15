"""The filers the work list leaves out, from the one file that names them.

``data/placeholder_recovery/exclusions.csv`` is tracked as plain text, so a
pull request shows the row added and its evidence. Exclusion is by EIN,
never by a pattern on the name: of fifteen placeholder filers named like a
patient-assistance program, nine are ordinary corporate foundations.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

EXCLUSIONS_CSV = Path(__file__).resolve().parents[2] / "data" / "placeholder_recovery" / "exclusions.csv"
COLUMNS = ("filerein", "filer_name", "evidence", "excluded_on")
_EIN = re.compile(r"\d{9}")


@dataclass(frozen=True)
class Exclusion:
    """One filer left out, and why."""

    filerein: str
    filer_name: str
    evidence: str
    excluded_on: date


def load_exclusions(path: Path = EXCLUSIONS_CSV) -> dict[str, Exclusion]:
    """The exclusions by EIN. Raises ``ValueError`` on a file that is not the
    registry: other columns, an EIN that is not nine digits or is listed
    twice, a row without evidence, a date that is not one."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"{path} has columns {reader.fieldnames}, not {list(COLUMNS)}")
        found: dict[str, Exclusion] = {}
        for number, line in enumerate(reader, start=2):
            row = _exclusion(line, f"{path}, line {number}")
            if row.filerein in found:
                raise ValueError(f"{path}, line {number}: EIN {row.filerein} is listed twice")
            found[row.filerein] = row
    return found


def _exclusion(line: dict[str, str], where: str) -> Exclusion:
    ein, evidence = line["filerein"].strip(), line["evidence"].strip()
    if not _EIN.fullmatch(ein):
        raise ValueError(f"{where}: {ein!r} is not a nine-digit EIN")
    if not evidence:
        raise ValueError(f"{where}: EIN {ein} is excluded without evidence")
    try:
        excluded_on = date.fromisoformat(line["excluded_on"].strip())
    except ValueError as exc:
        raise ValueError(f"{where}: {line['excluded_on']!r} is not a date") from exc
    return Exclusion(ein, line["filer_name"].strip(), evidence, excluded_on)
