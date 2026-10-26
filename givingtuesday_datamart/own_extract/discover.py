"""Find, from returns both sides hold, which xpath reproduces each GT column.

GT does not publish the xpath behind its columns: its data dictionary
names the form line ("Part 14 Table 3A - Col E - Amount"), and its column
names are abbreviations (``sigocpyamoun``). The overlap answers it: for
each column, every path whose value equals GT's on a return is a candidate,
and the candidate that agrees with GT on the most returns, absent where
GT is empty included, is the mapping. This proposes the mapping;
``concordance.csv`` is what is reviewed and committed, and ``parity``
measures that file, not this search.

It is a tool to keep, not a one-off: each new IRS schema version renames
some elements (2025v4.x is ahead of every public concordance), and
running this over the first batch that carries it finds the new names.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import pandas as pd

from givingtuesday_datamart.own_extract.parse import Filing

# Returns of one form searched at most: a parsed return holds its whole
# tree, so an 84,000-return archive read whole is on the order of 10 GB.
# Batch 01A, which the concordance was found on, holds 7,180 990s.
PER_FORM = 8_000

_HEADER = {"object_id", "ctid_order", "url"}
_CANDIDATES = 25


def norm(value) -> str | None:
    """The comparison form: None for empty, else whitespace collapsed, upper case."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = " ".join(str(value).split()).upper()
    return text or None


def _single(rows: pd.DataFrame) -> pd.DataFrame:
    return rows.drop_duplicates("object_id", keep="first").set_index("object_id")


def scalar(filings: dict[str, Filing], rows: pd.DataFrame,
           columns: list[str] | None = None) -> pd.DataFrame:
    """Best path (and occurrence) per column of a one-row-per-return table.

    Returns one line per (column, candidate) for the top three candidates:
    ``agree`` counts returns where the candidate's value equals GT's,
    both-empty included, over ``n`` returns.
    """
    rows = _single(rows[rows["object_id"].isin(filings)])
    columns = columns or [c for c in rows.columns if c not in _HEADER]
    hits: dict[str, Counter] = defaultdict(Counter)
    for object_id, row in rows.iterrows():
        reverse: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for path, values in filings[object_id].values.items():
            for k, value in enumerate(values):
                key = norm(value)
                if key is not None:
                    reverse[key].append((path, k))
        for column in columns:
            key = norm(row[column])
            if key is not None:
                hits[column].update(reverse.get(key, ()))

    out = []
    for column in columns:
        gt_values = rows[column].map(norm)
        scored = []
        for (path, k), _ in hits[column].most_common(_CANDIDATES):
            ours = pd.Series({oid: _at(filings[oid], path, k) for oid in rows.index})
            scored.append((int((ours.map(norm) == gt_values).sum()
                               + (ours.isna() & gt_values.isna()).sum()), path, k))
        scored.sort(reverse=True)
        n = len(rows)
        non_empty = int(gt_values.notna().sum())
        if not scored:
            out.append({"column": column, "rank": 1, "path": None, "occurrence": None,
                        "agree": int(gt_values.isna().sum()), "n": n, "gt_non_empty": non_empty})
        for rank, (agree, path, k) in enumerate(scored[:3], 1):
            out.append({"column": column, "rank": rank, "path": path, "occurrence": k,
                        "agree": agree, "n": n, "gt_non_empty": non_empty})
    return pd.DataFrame(out)


def _at(filing: Filing, path: str, k: int) -> str | None:
    values = filing.values.get(path)
    return values[k] if values and len(values) > k else None


def grouped(filings: dict[str, Filing], rows: pd.DataFrame, group_path: str,
            columns: list[str]) -> pd.DataFrame:
    """Best path per column of a table with one row per group occurrence.

    A path is relative to the group (``Amt``, ``RecipientUSAddress/CityNm``)
    or, written with a leading ``/``, the return's own first value
    (``/ReturnHeader/TaxYr``, ``/ReturnData/IRS990EZ/PrimaryExemptPurposeTxt``),
    which GT repeats on every row. Candidates are scored on returns where
    GT's row count equals the group count, comparing each column's values
    as a multiset per return, so the extract's own row order does not count
    against a path.
    """
    hits: dict[str, Counter] = defaultdict(Counter)
    aligned: dict[str, tuple[list[dict], list[dict]]] = {}
    for object_id, frame in rows[rows["object_id"].isin(filings)].groupby("object_id", sort=False):
        filing = filings[object_id]
        groups = filing.groups.get(group_path, [])
        if len(groups) != len(frame) or not groups:
            continue
        records = frame.to_dict("records")
        aligned[object_id] = (records, groups)
        # Every value anywhere in the return's groups, and the return's own
        # first values: candidates, whatever row of GT's they sit on.
        reverse: dict[str, set[str]] = defaultdict(set)
        for path, values in filing.values.items():
            if values and (key := norm(values[0])) is not None:
                reverse[key].add("/" + path)
        for group in groups:
            for path, values in group.items():
                if values and (key := norm(values[0])) is not None:
                    reverse[key].add(path)
        for row in records:
            for column in columns:
                if (key := norm(row[column])) is not None:
                    hits[column].update(reverse.get(key, ()))

    out = []
    for column in columns:
        scored = []
        for path, _ in hits[column].most_common(_CANDIDATES):
            agree = n = 0
            for object_id, (records, groups) in aligned.items():
                filing = filings[object_id]
                ours = Counter(norm(group_value(filing, group, path)) for group in groups)
                theirs = Counter(norm(row[column]) for row in records)
                agree += sum((ours & theirs).values())
                n += len(records)
            scored.append((agree, n, path))
        scored.sort(reverse=True)
        for rank, (agree, n, path) in enumerate(scored[:3], 1):
            out.append({"column": column, "rank": rank, "path": path, "agree": agree, "n": n})
        if not scored:
            n = sum(len(records) for records, _ in aligned.values())
            empty = sum(norm(row[column]) is None for records, _ in aligned.values() for row in records)
            out.append({"column": column, "rank": 1, "path": None, "agree": empty, "n": n})
    return pd.DataFrame(out)


def group_value(filing: Filing, group: dict[str, list[str]], path: str) -> str | None:
    """A group-relative path's first value, or the return's for a ``/`` path."""
    if path.startswith("/"):
        return filing.first(path[1:])
    values = group.get(path)
    return values[0] if values else None


CONCORDANCE_FIELDS = ("table", "column", "paths", "occurrence", "agree", "n", "measured_on", "note")


def proposals(table: str, found: pd.DataFrame, measured_on: str) -> list[dict]:
    """The rank-1 candidate of every column as concordance lines.

    A column with no candidate gets an empty ``paths``: it was empty on
    every return measured, and stays NULL until a batch where GT fills it
    is searched. The two columns computed from the file are marked so.
    """
    out = []
    for _, line in found[found["rank"] == 1].iterrows():
        column = line["column"]
        if column in ("url", "filesha256"):
            out.append({"table": table, "column": column, "paths": "", "occurrence": 0,
                        "agree": "", "n": "", "measured_on": measured_on,
                        "note": "computed from the file"})
            continue
        occurrence = line.get("occurrence")
        out.append({
            "table": table, "column": column, "paths": line["path"] or "",
            "occurrence": 0 if occurrence is None or pd.isna(occurrence) else int(occurrence),
            "agree": int(line["agree"]), "n": int(line["n"]), "measured_on": measured_on,
            "note": "" if line["path"] else "empty on every return measured",
        })
    return out


# GT's rules that one path per column does not express, each found on the
# overlap of 2026-10-23 (2026_TEOS_XML_01A). Applied over the search's
# proposal, so ``propose`` reproduces the committed concordance.
OVERRIDES: dict[tuple[str, str], tuple[str, str]] = {
    # 3B has no person-name column: GT puts the person's name in line 1 of
    # the business name when the group names a person (249 of 447 rows).
    ("privategrants_future", "sigocaffrbnb1"): (
        "RecipientPersonNm | RecipientBusinessName/BusinessNameLine1Txt",
        "GT rule: the person's name, else business name line 1"),
}
# The IRS renamed the recipient's foundation status in schema 2025v4,
# RecipientFoundationStatusTxt (free text) to RecipientFoundationStatusCd (a
# code list: PC, NC, I, POF, PF, GOV, SO I ...), in 3a and 3b alike. GT's
# June 2026 extract left the column NULL on 2025v4 returns (42 of them in
# 2026_TEOS_XML_01A); reading the new name too keeps it filled, and keeps
# "I" for grants to individuals, which the placeholder work list reads.
# Found by the fill-rate comparison of 2026_TEOS_XML_07A (85% to 0%).
for _table, _column in (("privategrants", "sigocpyrfsta"),
                        ("privategrants_future", "sigocaffrfstat")):
    OVERRIDES[(_table, _column)] = (
        "RecipientFoundationStatusTxt | RecipientFoundationStatusCd",
        "IRS renamed it in 2025v4 (Txt to Cd); GT's extract has it NULL there")
# A 990 filer in a territory (Puerto Rico, the Northern Mariana Islands)
# often gives the header a foreign address; GT then fills the US address
# columns from the address in the form's body as well as the foreign ones
# from the header (3 of 7,180 returns).
for _column, _leaf in (("filerus1", "AddressLine1Txt"), ("filerus2", "AddressLine2Txt"),
                       ("fileruscity", "CityNm"), ("filerusstate", "StateAbbreviationCd"),
                       ("fileruszip", "ZIPCd")):
    OVERRIDES[("basic_fields", _column)] = (
        f"ReturnHeader/Filer/USAddress/{_leaf} | ReturnData/IRS990/USAddress/{_leaf}",
        "GT rule: the header's US address, else the form body's")

# ...and the same for the foreign address, from the body's ForeignAddress
# when the header has none (722 of 785,185 990s of 2024-2026; the parity
# run of 2026-10-23).
for _column, _leaf in (("filerfor1", "AddressLine1Txt"), ("filerfor2", "AddressLine2Txt"),
                       ("filerforcity", "CityNm"), ("filerforctry", "CountryCd"),
                       ("filerforpost", "ForeignPostalCd"), ("filerforstate", "ProvinceOrStateNm")):
    OVERRIDES[("basic_fields", _column)] = (
        f"ReturnHeader/Filer/ForeignAddress/{_leaf} | ReturnData/IRS990/ForeignAddress/{_leaf}",
        "GT rule: the header's foreign address, else the form body's")

# Columns GT left empty on every return of 2026_TEOS_XML_01A, so the search
# had nothing to match; the path is its sibling column's, one line or one
# group over. The parity run of every loaded batch measures them.
_BY_ANALOGY = {
    ("basic_fields", "dbanbnline22"): "ReturnData/IRS990/DoingBusinessAsName/BusinessNameLine2Txt",
    ("programs", "activicodeo2"): "ReturnData/IRS990/ProgSrvcAccomActy2Grp/ActivityCd",
    ("programs", "activicodeo3"): "ReturnData/IRS990/ProgSrvcAccomActy3Grp/ActivityCd",
    ("grants_to_domestic_organizations", "retaadfoadli2"): "ForeignAddress/AddressLine2Txt",
    ("privategrants", "sigocaffrfaco"): "RecipientForeignAddress/CountryCd",
    ("basic_fields_pf", "filerus2"): "ReturnHeader/Filer/USAddress/AddressLine2Txt",
    ("basic_fields_pf", "filerfor2"): "ReturnHeader/Filer/ForeignAddress/AddressLine2Txt",
    ("basic_fields_ez", "filerus2"): "ReturnHeader/Filer/USAddress/AddressLine2Txt",
}
_APPLICATION = "ReturnData/IRS990PF/SupplementaryInformationGrp/ApplicationSubmissionInfoGrp/RecipientForeignAddress"
_BY_ANALOGY.update({("basic_fields_pf", c): f"{_APPLICATION}/{leaf}" for c, leaf in (
    ("siasirfaalin1", "AddressLine1Txt"), ("siasirfaalin2", "AddressLine2Txt"),
    ("siasirfacity", "CityNm"), ("siasirfacoun", "CountryCd"))})
_BY_ANALOGY.update({("basic_fields_ez", c): f"ReturnHeader/Filer/ForeignAddress/{leaf}" for c, leaf in (
    ("filerfor1", "AddressLine1Txt"), ("filerfor2", "AddressLine2Txt"), ("filerforcity", "CityNm"),
    ("filerforctry", "CountryCd"), ("filerforpost", "ForeignPostalCd"),
    ("filerforstate", "ProvinceOrStateNm"))})
for _key, _path in _BY_ANALOGY.items():
    OVERRIDES[_key] = (_path, "by analogy with its sibling column (GT empty on every 01A return)")

# The filer's name line 2 in the group tables, which the search proposed for
# none of them. GT fills it beside line 1 in the grant and program tables but
# on no Schedule I row (2024-2026 sampled): there ours stays empty too, since
# `_current`'s dedup hashes it and the matcher reads it as the granter's name.
for _table in ("privategrants", "privategrants_future", "programs_ez"):
    OVERRIDES[(_table, "filername2")] = (
        "/ReturnHeader/Filer/BusinessName/BusinessNameLine2Txt",
        "the header's name line 2, as filername1; GT fills it (the search proposed none)")
OVERRIDES[("schedule_o", "filername2")] = (
    "/ReturnHeader/Filer/BusinessName/BusinessNameLine2Txt",
    "the header's name line 2, as filername1; not compared (GT's table has no EIN index to sample by)")
OVERRIDES[("grants_to_domestic_organizations", "filername2")] = (
    "", "GT leaves it empty on every Schedule I row")

# GT's numbered columns (``_0``, ``_1`` ...) are the n-th occurrence of one
# element, filled only when the element repeats: a 990-PF with a single
# application address or manager has all of them empty in GT (61,563 and
# 73,419 of the 273,504 of 2024-2026), so on 01A the search could not place
# ``_0``. Ours keeps a single occurrence in ``_0``; where GT has a value,
# ours equals it.
OCCURRENCES: dict[tuple[str, str], tuple[str, int]] = {}
_SIA = "ReturnData/IRS990PF/SupplementaryInformationGrp"
for _n in (0, 1):
    for _stem, _leaf in (("siasiraaline1", "ApplicationSubmissionInfoGrp/RecipientUSAddress/AddressLine1Txt"),
                         ("siasiracity", "ApplicationSubmissionInfoGrp/RecipientUSAddress/CityNm"),
                         ("siasirastate", "ApplicationSubmissionInfoGrp/RecipientUSAddress/StateAbbreviationCd"),
                         ("siasirazipco", "ApplicationSubmissionInfoGrp/RecipientUSAddress/ZIPCd"),
                         ("siasirname", "ApplicationSubmissionInfoGrp/RecipientPersonNm"),
                         ("siasirpnumbe", "ApplicationSubmissionInfoGrp/RecipientPhoneNum"),
                         ("supinfshaman", "ShareholderManagerNm")):
        OCCURRENCES[("basic_fields_pf", f"{_stem}_{_n}")] = (f"{_SIA}/{_leaf}", _n)
for _n in range(5):
    OCCURRENCES[("basic_fields_pf", f"supinfconman_{_n}")] = (f"{_SIA}/ContributingManagerNm", _n)


def read_filings(zip_path, per_form: int = PER_FORM) -> dict[str, Filing]:
    """The first ``per_form`` returns of each form a table reads, parsed; the
    rest are dropped as they are read, so memory does not grow with the archive."""
    from pathlib import Path

    from givingtuesday_datamart.own_extract import batches, extract, parse

    forms = {form for types in extract.TABLES.values() for form in types}
    kept: Counter = Counter()
    filings: dict[str, Filing] = {}
    for object_id, xml in batches.iter_members(Path(zip_path)):
        filing = parse.parse(xml, object_id, extract.GROUP_PATHS)
        if filing.return_type in forms and kept[filing.return_type] < per_form:
            filings[object_id] = filing
            kept[filing.return_type] += 1
    return filings


def propose(session, zip_path, rows_for=None, per_form: int = PER_FORM) -> list[dict]:
    """Concordance lines for every table, searched on one archive's returns
    (``read_filings``: at most ``per_form`` of each form).

    ``rows_for(session, table, eins, object_ids)`` reads GT's rows
    (``gt.rows_for`` unless given, e.g. a cache of them).
    """
    from pathlib import Path

    from givingtuesday_datamart.own_extract import extract, gt

    filings = read_filings(zip_path, per_form)
    measured_on = Path(zip_path).stem
    lines: list[dict] = []
    for table, types in extract.TABLES.items():
        subset = {o: f for o, f in filings.items() if f.return_type in types}
        eins = {f.first("ReturnHeader/Filer/EIN") for f in subset.values()} - {None}
        rows = (rows_for or gt.rows_for)(session, table, eins, set(subset))
        cols = [c for c in rows.columns if c not in _HEADER]
        if table in extract.GROUPS:
            found = grouped(subset, rows, extract.GROUPS[table], cols)
        else:
            found = scalar(subset, rows, cols)
        # The search skips the url (it is the join key); GT's tables all carry it.
        url = pd.DataFrame([{"column": "url", "rank": 1, "path": None, "agree": 0, "n": 0}])
        for line in proposals(table, pd.concat([found, url], ignore_index=True), measured_on):
            if (table, line["column"]) in OVERRIDES:
                line["paths"], line["note"] = OVERRIDES[(table, line["column"])]
                line["agree"] = line["n"] = ""        # the search scored one path; parity measures the rule
            if (table, line["column"]) in OCCURRENCES:
                line["paths"], line["occurrence"] = OCCURRENCES[(table, line["column"])]
                line["note"] = "the n-th occurrence; GT fills it only when the element repeats"
                line["agree"] = line["n"] = ""
            lines.append(line)
    return lines
