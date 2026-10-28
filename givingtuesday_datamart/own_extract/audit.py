"""The elements of a return the concordance does not read.

GT's tables can show where our parse and GT's differ, never what both
miss: an element the IRS renames or adds in a new schema version, read by
neither, leaves the two in agreement. This counts, for every parsed return,
the leaf elements no concordance path reads, in two places:

* inside a group a table reads (``extract.GROUPS``): every element of every
  occurrence, since a grant row is small and every part of it matters;
* beside an element a return-level table reads (the same parent): where a
  renamed element lands, the 2025v4 rename of a grant recipient's
  foundation status (``RecipientFoundationStatusTxt`` to ``...Cd``) being
  the shape looked for.

Counted by form, schema version and path, as ``{(form, version, scope,
path): [returns, occurrences]}``; ``scope`` is the group table, or
``return`` for the return-level elements. ``load`` stores a batch's counts
in ``irs_extract.unread_paths``; ``checks`` reports the candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from givingtuesday_datamart.own_extract import extract
from givingtuesday_datamart.own_extract.parse import Filing

RETURN_SCOPE = "return"

# The IRS's element names end in a type: a rename that keeps the meaning
# usually changes only this (Txt to Cd, Amt to Num).
SUFFIXES = ("Desc", "Txt", "Cd", "Amt", "Ind", "Nm", "Num", "Dt", "Pct", "Cnt", "Grp", "Ts", "Yr", "Rt")


def stem(path: str) -> str:
    """The last element's name without its type suffix (``...StatusCd`` -> ``...Status``)."""
    name = path.rsplit("/", 1)[-1]
    for suffix in SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


@dataclass
class Coverage:
    """What the concordance reads, arranged for :func:`unread`."""

    group_read: dict[str, set[str]] = field(default_factory=dict)    # group table -> paths in the group
    return_read: dict[str, set[str]] = field(default_factory=dict)   # form -> paths in the return
    return_parents: dict[str, set[str]] = field(default_factory=dict)


def coverage(concordance: dict[str, list[extract.Mapping]]) -> Coverage:
    cov = Coverage()
    for table, mappings in concordance.items():
        relative = {p for m in mappings for p in m.paths if not p.startswith("/")}
        absolute = {p.lstrip("/") for m in mappings for p in m.paths if p.startswith("/")}
        if table in extract.GROUPS:
            cov.group_read[table] = relative
        else:
            absolute |= relative            # a return-level table's paths are all in the return
        for form in extract.TABLES[table]:
            cov.return_read.setdefault(form, set()).update(absolute)
    for form, paths in cov.return_read.items():
        cov.return_parents[form] = {p.rpartition("/")[0] for p in paths}
    return cov


def unread(filing: Filing, cov: Coverage) -> dict[tuple[str, str], int]:
    """``{(scope, path): occurrences}`` of the elements of one return no path reads."""
    form = filing.return_type
    found: dict[tuple[str, str], int] = {}
    groups = [t for t in cov.group_read if form in extract.TABLES[t]]
    inside = tuple(extract.GROUPS[t] + "/" for t in groups)
    read, parents = cov.return_read.get(form, set()), cov.return_parents.get(form, set())
    for path, values in filing.values.items():
        if path in read or path.rpartition("/")[0] not in parents or path.startswith(inside):
            continue
        found[(RETURN_SCOPE, path)] = len(values)
    for table in groups:
        group_path = extract.GROUPS[table]
        for occurrence in filing.groups.get(group_path, []):
            for relative, values in occurrence.items():
                if relative not in cov.group_read[table]:
                    key = (table, f"{group_path}/{relative}")
                    found[key] = found.get(key, 0) + len(values)
    return found


def add(counts: dict, filing: Filing, cov: Coverage) -> None:
    """Add one return's unread elements to a batch's ``counts``."""
    for (scope, path), occurrences in unread(filing, cov).items():
        entry = counts.setdefault((filing.return_type, filing.version, scope, path), [0, 0])
        entry[0] += 1
        entry[1] += occurrences


def renamed_sibling(path: str, read: set[str]) -> str | None:
    """A read path beside ``path`` whose name differs from it only in the type suffix."""
    parent, s = path.rpartition("/")[0], stem(path)
    for other in sorted(read):
        if other != path and other.rpartition("/")[0] == parent and stem(other) == s:
            return other
    return None
