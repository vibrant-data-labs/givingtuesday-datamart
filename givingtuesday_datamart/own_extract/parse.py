"""Read one e-filed return into paths and values.

A return is flattened to ``path -> [text, ...]``: every leaf element's
path below ``Return``, namespaces dropped, its texts in document order
(``ReturnHeader/Filer/EIN``, ``ReturnData/IRS990PF/...``). The paths are
the NODC concordance's xpaths without the leading ``/Return/``, so the two
can be joined.

A repeating group (a grant, a Schedule O note) is read separately, one
dict per occurrence, with paths relative to the group, because the leaf
lists of the flat view lose which values sat in the same group when a
group leaves a field out.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass
class Filing:
    object_id: str
    sha256: str
    return_type: str
    version: str
    values: dict[str, list[str]]
    groups: dict[str, list[dict[str, list[str]]]] = field(default_factory=dict)

    def first(self, path: str) -> str | None:
        found = self.values.get(path)
        return found[0] if found else None


def _flatten(element: ET.Element, prefix: str, into: dict[str, list[str]]) -> None:
    for child in element:
        path = f"{prefix}/{_local(child.tag)}" if prefix else _local(child.tag)
        if len(child):
            _flatten(child, path, into)
        else:
            into[path].append((child.text or "").strip())


def parse(xml: bytes, object_id: str, group_paths: tuple[str, ...] = ()) -> Filing:
    """Parse a return. ``group_paths`` are read as repeating groups too."""
    root = ET.fromstring(xml)
    values: dict[str, list[str]] = defaultdict(list)
    _flatten(root, "", values)

    groups: dict[str, list[dict[str, list[str]]]] = {}
    for group_path in group_paths:
        parent_path, _, name = group_path.rpartition("/")
        occurrences: list[dict[str, list[str]]] = []
        for parent in _find(root, parent_path.split("/") if parent_path else []):
            for child in parent:
                if _local(child.tag) == name:
                    inner: dict[str, list[str]] = defaultdict(list)
                    _flatten(child, "", inner)
                    occurrences.append(dict(inner))
        groups[group_path] = occurrences

    return_type = values.get("ReturnHeader/ReturnTypeCd", [""])[0]
    return Filing(
        object_id=object_id,
        sha256=hashlib.sha256(xml).hexdigest(),
        return_type=return_type,
        version=root.get("returnVersion", ""),
        values=dict(values),
        groups=groups,
    )


def _find(element: ET.Element, steps: list[str]) -> list[ET.Element]:
    """Elements at ``steps`` below ``element`` by local name, any namespace."""
    current = [element]
    for step in steps:
        current = [child for node in current for child in node if _local(child.tag) == step]
    return current
