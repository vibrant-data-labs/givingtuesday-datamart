"""Checks of our extract that need no GT: each filing's own totals, and the
elements of the XML the concordance does not read.

GT's tables are a second parser and serve as a diff: where they agree with
ours, two parsers read the XML alike; where they do not, the XML decides.
They prove nothing where we follow GT's conventions, where both miss an
element, or for a return GT does not hold (209,879 in our batches on
2026-10-28). These checks do not lean on GT.

* **Totals.** A filing states its own totals. A 990-PF's grants paid add up
  to line 25 of Part I, column (d) or (a); a 990's Schedule I lists every
  grant of more than $5,000 to a domestic organization, so its cash and
  non-cash amounts add up to no more than Part IX line 1, column (A). Each
  return is classified from our tables alone. On 2026-10-28, 96.9% of the
  990-PFs with grants matched line 25 where GT holds the return and 97.1%
  where it does not; the rest is a pile to sample against the XML, not our
  error by default (grants listed in an attachment, a line 25 that is not
  the grant list, a filer's slip). A batch well below the others is the
  mark of a parse that went wrong in it.
* **Unread elements** (``audit``, stored by ``load`` in
  ``irs_extract.unread_paths``), on a share of their returns: inside a
  group a table reads (on 2026-10-28 the foreign address of 990-PF grant
  recipients, 61,612 grants, the gap GT's release had shown that morning);
  beside a read element with the same name but for the type suffix (a
  rename); first appearing in a later schema version (an addition).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart.own_extract import audit, extract, parity
from givingtuesday_datamart.own_extract.load import LOGICAL_NAME, SCHEMA, UNREAD
from givingtuesday_datamart.own_extract.report import _md

PF_LINE25 = "990-PF grants against line 25"
SCHEDULE_I = "Schedule I against Part IX line 1"
PASSING = {PF_LINE25: "equal", SCHEDULE_I: "within line 1"}
NOT_APPLICABLE = "no grants"
LOW_POINTS = 2.0          # a batch this many points below the median of the others is listed
MIN_RETURNS = 200         # ... when the check applies to at least this many of its returns
LISTED_SHARE = 0.005      # an unread element is listed from 0.5% of its form's (version's) returns
SUBSTANTIAL = 1_000       # the first version of a form counted is the first with this many returns

_PF_SQL = f"""
    SELECT b._batch_id AS batch, b.object_id, r.return_version AS version,
           nullif(b.arecgpdcprps, '')::numeric AS col_d, nullif(b.arecprexpnss, '')::numeric AS col_a,
           coalesce(s.n, 0) AS grant_rows, s.v AS ours
    FROM {SCHEMA}.basic_fields_pf b
    JOIN {SCHEMA}.returns r ON r.object_id = b.object_id AND r._batch_id = b._batch_id
    LEFT JOIN (SELECT object_id, count(*) AS n, sum(nullif(sigocpyamoun, '')::numeric) AS v
               FROM {SCHEMA}.privategrants WHERE _batch_id = :b AND group_ordinal IS NOT NULL
               GROUP BY 1) s ON s.object_id = b.object_id
    WHERE b._batch_id = :b"""

_SCHEDULE_I_SQL = f"""
    SELECT b._batch_id AS batch, b.object_id, r.return_version AS version,
           nullif(b.graallpaitot, '')::numeric AS line1, s.n AS grant_rows, s.cash, s.noncash
    FROM {SCHEMA}.basic_fields b
    JOIN {SCHEMA}.returns r ON r.object_id = b.object_id AND r._batch_id = b._batch_id
    JOIN (SELECT object_id, count(*) AS n, sum(nullif(retaamofcagr, '')::numeric) AS cash,
                 sum(nullif(rtaoncassist, '')::numeric) AS noncash
          FROM {SCHEMA}.grants_to_domestic_organizations WHERE _batch_id = :b AND group_ordinal IS NOT NULL
          GROUP BY 1) s ON s.object_id = b.object_id
    WHERE b._batch_id = :b"""


def _num(value) -> float:
    return 0.0 if value is None or pd.isna(value) else float(value)


def pf_status(grant_rows: int, ours, col_d, col_a) -> str:
    """A 990-PF's grant rows against its line 25, column (d) or (a)."""
    d, a, v = _num(col_d), _num(col_a), _num(ours)
    if not grant_rows:
        return "line 25, no grant rows" if max(d, a) > 0 else NOT_APPLICABLE
    if abs(v - d) < 1 or abs(v - a) < 1:
        return "equal"
    return "rows below line 25" if v < max(d, a) else "rows above line 25"


def schedule_i_status(cash, noncash, line1) -> str:
    """A 990's Schedule I grants (cash and non-cash) against Part IX line 1."""
    return "within line 1" if _num(cash) + _num(noncash) <= _num(line1) + 1 else "above line 1"


def loaded(session, batch_ids: list[str] | None = None) -> pd.DataFrame:
    """The latest successful load of each batch: its archive's SHA-256, the concordance's,
    and the archive's whole fetch stamp (``batches.fetch``)."""
    frame = pd.read_sql(text(
        f"""SELECT DISTINCT ON (source_url) split_part(source_version, '@', 1) AS batch,
                   source_manifest->'batch'->>'sha256' AS zip_sha256,
                   source_manifest->>'concordance_sha256' AS concordance_sha256,
                   source_manifest->'batch' AS stamp
            FROM datamart_meta.ingest_runs
            WHERE logical_name = '{LOGICAL_NAME}' AND status = 'success'
            ORDER BY source_url, finished_at DESC"""), session.connection())
    session.commit()          # release ingest_runs: a loader ALTERs it
    if batch_ids:
        unknown = sorted(set(batch_ids) - set(frame["batch"]))
        if unknown:
            raise ValueError(f"not loaded: {', '.join(unknown)}")
        frame = frame[frame["batch"].isin(batch_ids)]
    return frame.sort_values("batch").reset_index(drop=True)


def totals(session, batch_ids: list[str]) -> pd.DataFrame:
    """One row per 990-PF and per 990 with Schedule I rows: batch, object id,
    version, check, status, our amount and the stated one (``NOT_APPLICABLE``
    for a 990-PF with no grants, left out by ``rates``). Read a batch at a time."""
    parity.prepare(session)
    out = []
    for batch in batch_ids:
        pf = pd.read_sql(text(_PF_SQL), session.connection(), params={"b": batch})
        if not pf.empty:
            pf["status"] = [pf_status(*r) for r in
                            pf[["grant_rows", "ours", "col_d", "col_a"]].itertuples(index=False)]
            pf["stated"] = [max(_num(d), _num(a)) for d, a in zip(pf["col_d"], pf["col_a"])]
            out.append(pf.assign(check=PF_LINE25)[["batch", "object_id", "version", "check", "status",
                                                   "ours", "stated"]])
        si = pd.read_sql(text(_SCHEDULE_I_SQL), session.connection(), params={"b": batch})
        if not si.empty:
            si["status"] = [schedule_i_status(*r) for r in
                            si[["cash", "noncash", "line1"]].itertuples(index=False)]
            si["ours"] = [_num(c) + _num(n) for c, n in zip(si["cash"], si["noncash"])]
            out.append(si.rename(columns={"line1": "stated"}).assign(check=SCHEDULE_I)[
                ["batch", "object_id", "version", "check", "status", "ours", "stated"]])
    session.commit()
    if not out:
        return pd.DataFrame(columns=["batch", "object_id", "version", "check", "status", "ours", "stated"])
    frame = pd.concat(out, ignore_index=True)
    frame["ours"] = frame["ours"].map(_num)
    frame["stated"] = frame["stated"].map(_num)
    return frame


def rates(frame: pd.DataFrame, by: str) -> pd.DataFrame:
    """Per ``by`` (``batch`` or ``version``) and check: the returns it applies to,
    the share passing, and the count of each other status."""
    applies = frame[frame["status"] != NOT_APPLICABLE]
    if applies.empty:
        return pd.DataFrame(columns=[by, "check", "returns", "passing_pct"])
    counts = applies.groupby([by, "check", "status"]).size().unstack(fill_value=0)
    counts.columns.name = None
    counts = counts.reset_index()
    status_cols = [c for c in counts.columns if c not in (by, "check")]
    counts["returns"] = counts[status_cols].sum(axis=1)
    counts["passing_pct"] = [round(100 * row[PASSING[row["check"]]] / row["returns"], 1)
                             if PASSING[row["check"]] in counts.columns else 0.0
                             for _, row in counts.iterrows()]
    return counts[[by, "check", "returns", "passing_pct", *status_cols]]


def low_batches(by_batch: pd.DataFrame) -> pd.DataFrame:
    """Batches whose share passing is ``LOW_POINTS`` below the median of the
    others for that check, among those it applies to on ``MIN_RETURNS`` returns."""
    rows = []
    for check, part in by_batch[by_batch["returns"] >= MIN_RETURNS].groupby("check"):
        for _, row in part.iterrows():
            others = part.loc[part["batch"] != row["batch"], "passing_pct"]
            if len(others) and row["passing_pct"] < others.median() - LOW_POINTS:
                rows.append({"check": check, "batch": row["batch"], "returns": row["returns"],
                             "passing_pct": row["passing_pct"], "median_of_others": others.median()})
    return pd.DataFrame(rows, columns=["check", "batch", "returns", "passing_pct", "median_of_others"])


def largest_gaps(frame: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    """Per check, the failing returns with the largest gap between ours and the stated total."""
    failing = frame[(frame["status"] != NOT_APPLICABLE)
                    & (frame["status"] != frame["check"].map(PASSING))].copy()
    failing["gap"] = (failing["ours"] - failing["stated"]).round(0)
    failing = failing.reindex(failing["gap"].abs().sort_values(ascending=False).index)
    return failing.groupby("check", group_keys=False).head(n)[
        ["check", "batch", "object_id", "version", "status", "ours", "stated", "gap"]]


def unread_candidates(session, concordance) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """From ``unread_paths``, the elements on ``LISTED_SHARE`` of their returns or
    more: inside a group (all versions), renamed beside a read one, and new in
    a later schema version (by form and major version, ``2025v4``)."""
    unread = pd.read_sql(text(
        f"""SELECT return_type AS form, left(return_version, 6) AS version, scope, path,
                   sum(returns) AS returns
            FROM {SCHEMA}.{UNREAD} GROUP BY 1, 2, 3, 4"""), session.connection())
    per_version = pd.read_sql(text(
        f"""SELECT return_type AS form, left(return_version, 6) AS version, count(*) AS form_returns
            FROM {SCHEMA}.returns GROUP BY 1, 2"""), session.connection())
    session.commit()
    if unread.empty:
        empty = pd.DataFrame()
        return empty, empty, empty
    cov = audit.coverage(concordance)
    per_form = per_version.groupby("form", as_index=False)["form_returns"].sum()
    inside = (unread[unread["scope"] != audit.RETURN_SCOPE]
              .groupby(["form", "scope", "path"], as_index=False)["returns"].sum()
              .merge(per_form, on="form"))
    inside["share_pct"] = (100 * inside["returns"] / inside["form_returns"]).round(2)
    inside = inside[inside["share_pct"] >= 100 * LISTED_SHARE].sort_values(
        ["scope", "returns"], ascending=[True, False])[["form", "scope", "path", "returns", "share_pct"]]

    unread = unread.merge(per_version, on=["form", "version"], how="left")
    unread["share_pct"] = (100 * unread["returns"] / unread["form_returns"]).round(2)
    common = unread["share_pct"] >= 100 * LISTED_SHARE

    def read_beside(row) -> set[str]:
        if row["scope"] == audit.RETURN_SCOPE:
            return cov.return_read.get(row["form"], set())
        return {f"{extract.GROUPS[row['scope']]}/{p}" for p in cov.group_read.get(row["scope"], set())}

    unread["read_sibling"] = [audit.renamed_sibling(r["path"], read_beside(r))
                              for _, r in unread.iterrows()]
    # A code beside its amount (ExclusionCd, ExclusionAmt) shares a stem too, on a few
    # returns; a rename takes the element's place on most returns of the version.
    renamed = unread[unread["read_sibling"].notna() & common].sort_values(["form", "path", "version"])

    first = (per_version[per_version["form_returns"] >= SUBSTANTIAL]
             .groupby("form")["version"].min().rename("first_version"))
    seen = unread.groupby(["form", "scope", "path"])["version"].min().rename("first_seen")
    unread = unread.join(seen, on=["form", "scope", "path"]).join(first, on="form")
    # An unread path is never read in another version (one concordance), so the
    # first version it is unread in is the first its element appears in.
    first_here = unread["version"] == unread["first_seen"]
    after_first = unread["first_seen"] > unread["first_version"]
    new = unread[first_here & after_first & common].sort_values(
        ["form", "version", "returns"], ascending=[True, True, False])
    cols = ["form", "version", "scope", "path", "returns", "share_pct"]
    return inside, renamed[[*cols, "read_sibling"]], new[cols]


def batch_line(session, batch_id: str) -> str:
    """One line for a load's log: the batch's share passing each check."""
    by = rates(totals(session, [batch_id]), "batch")
    return "; ".join(f"{r['check']}: {r['passing_pct']}% of {r['returns']:,}" for _, r in by.iterrows()) \
        or "no return the checks apply to"


def run(session, out: Path, batch_ids: list[str] | None = None) -> str:
    """The checks report over the loaded batches (or ``batch_ids``), as markdown."""
    stamps = loaded(session, batch_ids)
    frame = totals(session, list(stamps["batch"]))
    by_batch = rates(frame, "batch")
    by_version = rates(frame.assign(version=frame["version"].str[:6]), "version")   # 2025v4.0 -> 2025v4
    inside, renamed, new = unread_candidates(session, extract.load())

    def per_check(by: pd.DataFrame) -> list[str]:
        out = []
        for check in (PF_LINE25, SCHEDULE_I):
            part = by[by["check"] == check].drop(columns="check") if not by.empty else by
            out += [f"**{check}**\n", _md(part.loc[:, (part != 0).any()] if not part.empty else part)]
        return out

    parts = [
        "# Our IRS extract against its own filings\n",
        "Generated by `python -m givingtuesday_datamart.own_extract checks`. No GT table is read.\n",
        "## Totals the filings state\n",
        f"- **{PF_LINE25}**: a 990-PF's grant rows add up to line 25 of Part I, column (d) or (a).",
        f"- **{SCHEDULE_I}**: a 990's Schedule I grants (cash and non-cash) add up to no more than "
        "Part IX line 1, column (A); Schedule I lists only grants over $5,000.\n",
        "A return a check does not apply to (no grant rows, nothing stated) is not counted.\n",
        "### By batch\n", *per_check(by_batch),
        f"### Batches {LOW_POINTS:g} points or more below the median of the others\n",
        _md(low_batches(by_batch)),
        "### By schema version\n", *per_check(by_version),
        "### The largest gaps, to sample against the XML\n", _md(largest_gaps(frame)),
        "## Elements no concordance path reads (`irs_extract.unread_paths`)\n",
        f"Listed from {100 * LISTED_SHARE:g}% of the form's returns (of the version's, by version).\n",
        "### Inside a group a table reads, all versions\n", _md(inside),
        "### Beside a read element, the same name but for its type suffix (a rename?)\n", _md(renamed),
        "### First seen in a later schema version\n", _md(new),
    ]
    report = "\n".join(parts)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    return report
