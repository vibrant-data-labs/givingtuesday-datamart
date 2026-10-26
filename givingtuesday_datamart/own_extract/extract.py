"""Turn a parsed return into rows shaped like GT's staging tables.

The column names are GT's, so anything that reads ``public.basic_fields_pf``
can read ``irs_extract.basic_fields_pf``, and a patch for returns GT has not
released is a UNION. What differs is the key: every row also carries the
IRS object id and, in the group tables, the group's position in the return
(``group_ordinal``, 0-based), which GT's rows never had (``current_grants``
falls back on ``ctid``). Each row also names the batch it came from.

Which xpath fills which column is ``data/own_extract/concordance.csv``,
found on returns both sides hold (``discover``) and measured by ``parity``.
A column lists its paths in order, separated by `` | ``; the first one the
return holds wins. Paths in a group table are relative to the group unless
they start with ``/``, which reads the return's own value.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from givingtuesday_datamart.irs_source import GT_DATALAKE
from givingtuesday_datamart.own_extract.parse import Filing

REPO_ROOT = Path(__file__).resolve().parents[2]
CONCORDANCE = REPO_ROOT / "data" / "own_extract" / "concordance.csv"

# Every table's url is GT's own pattern for a filing's XML
# (``irs_source.GT_DATALAKE``): downstream code reads the object id out of
# it and orders versions by it (``MAX(url)`` in ``current_grants``), so our
# rows carry the same string.

# The repeating group each group table has one row per occurrence of.
GROUPS: dict[str, str] = {
    "privategrants": "ReturnData/IRS990PF/SupplementaryInformationGrp/GrantOrContributionPdDurYrGrp",
    "privategrants_future": "ReturnData/IRS990PF/SupplementaryInformationGrp/GrantOrContriApprvForFutGrp",
    "grants_to_domestic_organizations": "ReturnData/IRS990ScheduleI/RecipientTable",
    "schedule_o": "ReturnData/IRS990ScheduleO/SupplementalInformationDetail",
    "programs_ez": "ReturnData/IRS990EZ/ProgramSrvcAccomplishmentGrp",
}

# Which returns feed which table. Schedule O is filed with a 990 or a 990-EZ
# and GT keeps both in one table.
TABLES: dict[str, tuple[str, ...]] = {
    "basic_fields": ("990",),
    "mission_statements": ("990",),
    "programs": ("990",),
    "grants_to_domestic_organizations": ("990",),
    "schedule_o": ("990", "990EZ"),
    "basic_fields_pf": ("990PF",),
    "privategrants": ("990PF",),
    "privategrants_future": ("990PF",),
    "basic_fields_ez": ("990EZ",),
    "programs_ez": ("990EZ",),
}

# Columns no xpath fills: computed from the file itself.
COMPUTED = ("url", "filesha256")
KEY_COLUMNS = ("object_id", "group_ordinal", "_batch_id")


@dataclass(frozen=True)
class Mapping:
    table: str
    column: str
    paths: tuple[str, ...]
    occurrence: int = 0


class Concordance(dict):
    """The mapping by table, and the SHA-256 of the file bytes it was read from."""

    sha256: str = ""


def load(path: Path = CONCORDANCE) -> Concordance:
    """The concordance, by table, in file order (which is GT's column order).

    The SHA-256 is of the bytes parsed, so a load stamps the mapping it
    actually used even if the file changes while it runs.
    """
    raw = path.read_bytes()
    by_table = Concordance()
    by_table.sha256 = hashlib.sha256(raw).hexdigest()
    with io.StringIO(raw.decode("utf-8"), newline="") as handle:
        for row in csv.DictReader(handle):
            paths = tuple(p.strip() for p in row["paths"].split("|") if p.strip())
            by_table.setdefault(row["table"], []).append(Mapping(
                row["table"], row["column"], paths, int(row["occurrence"] or 0)))
    return by_table


# Every return is parsed for every group: a form has no element of another
# form's groups, and reading the form first (a byte scan of the header)
# could disagree with the parser and drop a return's groups unseen.
GROUP_PATHS: tuple[str, ...] = tuple(GROUPS.values())


def _value(filing: Filing, group: dict[str, list[str]] | None, mapping: Mapping) -> str | None:
    for path in mapping.paths:
        if group is not None and not path.startswith("/"):
            values = group.get(path)
        else:
            values = filing.values.get(path.lstrip("/"))
        if values and len(values) > mapping.occurrence and values[mapping.occurrence]:
            return values[mapping.occurrence]
    return None


def rows(filing: Filing, table: str, mappings: list[Mapping], batch_id: str) -> list[dict]:
    """GT-shaped rows of ``table`` for one return; [] when the return has none.

    An empty text is NULL, as in GT's tables. A group table gets one row per
    occurrence and none for a return without the group: GT's synthetic row
    for a 990-PF with no grant groups (no recipient, the Part XV total as
    the amount) has no counterpart here.
    """
    if filing.return_type not in TABLES[table]:
        return []
    base = {
        "url": GT_DATALAKE.format(oid=filing.object_id),
        "filesha256": filing.sha256,
        "object_id": filing.object_id,
        "_batch_id": batch_id,
    }
    group_path = GROUPS.get(table)
    occurrences = filing.groups.get(group_path, []) if group_path else [None]
    out = []
    for ordinal, group in enumerate(occurrences):
        row = dict(base, group_ordinal=ordinal if group_path else None)
        for mapping in mappings:
            if mapping.column not in COMPUTED:
                row[mapping.column] = _value(filing, group, mapping)
        out.append(row)
    return out
