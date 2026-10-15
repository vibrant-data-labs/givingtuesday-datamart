"""What the IRS BMF alias arm adds to grant matching, proved on a slice.

The full matcher needs a 128 GB host and six hours and rebuilds production
tables, so the question "what do the BMF aliases win, and are the wins
right?" is answered here the way corrections_preflight answers it for the
corrections registry: on a slice that contains the complete competitive set
for every grant tuple in it, scored with the matcher's own functions and
thresholds. Nothing is written to the database except the matching views
(the same start-of-run side effect match_records and the preflight have).

    python -m givingtuesday_datamart.exploratory.bmf_alias_gain                # 2,000 tuples
    python -m givingtuesday_datamart.exploratory.bmf_alias_gain --sample 5000

Method:

1. Read the filer universe with the eo_bmf arm (the matcher's exact SQL, via
   corrections_preflight._UNIVERSE_SQL) and every grant tuple.
2. The currently unmatched tuples are the grant tuples absent from the last
   run's privategrants_w_recipients (7-key join). Take a deterministic
   pseudo-random sample of them (ORDER BY md5 of the keys).
3. Slice the universe to every row that blocks with a sampled tuple (same
   clean_zip, or same full_name), run blocking -> Compare -> both filter
   stages -> precision guards -> winner resolution, and read who won each
   tuple and from which arm (source). Resolve the same scored pairs once
   more without the guards, to see what they refuse.
4. Report wins by source, the grant rows and dollars behind the eo_bmf wins,
   the name-score distribution of those wins by BMF line (NAME / SORT_NAME),
   the wins the precision guards refused, the SORT_NAME wins below 0.85, and
   a hand-checkable sample of grant name vs BMF alias. Scale the sampled win
   rate to the unmatched population for the expected gain of a full rerun.

Why the slice is faithful: every decision that picks a tuple's winner is
per-pair and deterministic, and the slice holds every universe row any
sampled tuple can block with, so the winner computed here is the winner the
full run computes (the argument is spelled out in corrections_preflight).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.corrections_preflight import (
    _GRANTS_SQL,
    _KEY7,
    _KEY8,
    _UNIVERSE_SQL,
    _clean_like_run,
    _resolve_winners,
    _score_slice,
)
from givingtuesday_datamart.grant_matching import _load_corrections, create_or_replace_views
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.matching_regression_checks import AMT

DEFAULT_REPORT = Path("data/exploratory/bmf_alias_gain_report.md")

# Grant tuples the last completed run matched, on the 7-key identity the
# pipeline's SELECT INTO joins on. Everything in privategrants_unique_names_view
# but not here is currently unmatched.
_MATCHED_TUPLES_SQL = f"""
    SELECT DISTINCT {", ".join(_KEY7)} FROM public.privategrants_w_recipients
"""

# Which BMF line an eo_bmf universe row came from, for the report: NAME,
# SORT_NAME, or both when the two are equal after lowercasing.
_ALIAS_KIND_SQL = """
    SELECT LOWER(ein) AS filerein_key, LOWER(name) AS name1_key, 'name' AS alias_kind FROM public.eo_bmf
    UNION ALL
    SELECT LOWER(ein), LOWER(sort_name), 'sort_name' FROM public.eo_bmf
    WHERE sort_name IS NOT NULL AND LOWER(sort_name) <> LOWER(name)
"""


def rows_dollars_by_tuple(conn, tuples: pd.DataFrame) -> pd.DataFrame:
    """Grant rows and dollars behind each key tuple, via the same 7-key join
    the pipeline's SELECT INTO uses. The tuples go into a temp table that is
    ANALYZEd so the planner hash-joins them in one pass over the 17M-row
    keys view; a VALUES list of a thousand tuples was planned as a nested
    loop and ran past the connection's patience."""
    if tuples.empty:
        return pd.DataFrame(columns=_KEY7 + ["n_rows", "dollars"])
    conn.execute(text("DROP TABLE IF EXISTS pg_temp.won_tuples"))
    tuples[_KEY7].drop_duplicates().to_sql("won_tuples", conn, schema="pg_temp", index=False)
    conn.execute(text("ANALYZE pg_temp.won_tuples"))
    on = " AND ".join(f"t.{c} = pg.{c}" for c in _KEY7)
    keys = ", ".join(f"t.{c}" for c in _KEY7)
    return pd.read_sql_query(text(f"""
        SELECT {keys}, COUNT(*) AS n_rows, SUM({AMT.format(col='sigocpyamoun')}) AS dollars
        FROM public.privategrants_w_column_keys_view pg
        JOIN pg_temp.won_tuples t ON {on}
        GROUP BY {keys}"""), conn)


def unmatched_sample(grants: pd.DataFrame, matched: pd.DataFrame, n: int) -> pd.DataFrame:
    """The grant tuples not in ``matched`` (7-key anti-join), then a
    deterministic pseudo-random ``n`` of them: ordered by the MD5 of the
    concatenated keys, so two runs sample the same tuples."""
    import hashlib
    merged = grants.merge(matched.assign(_m=1), on=_KEY7, how="left")
    unmatched = merged[merged["_m"].isna()].drop(columns="_m")
    keys = unmatched[_KEY7].astype(str).agg("|".join, axis=1)
    order = keys.map(lambda k: hashlib.md5(k.encode()).hexdigest())
    sample = unmatched.assign(_h=order).sort_values("_h", kind="mergesort").head(n).drop(columns="_h")
    sample.attrs["unmatched_total"] = len(unmatched)
    return sample.reset_index(drop=True)


def slice_universe(universe: pd.DataFrame, sample: pd.DataFrame) -> pd.DataFrame:
    """Every universe row that can block with a sampled tuple: same clean_zip
    or same full_name (the matcher's two blocking keys)."""
    return universe[
        universe["clean_zip"].isin(set(sample["clean_zip"]))
        | universe["full_name"].isin(set(sample["full_name"]))
    ].reset_index(drop=True)


def wins_by_source(winners: pd.DataFrame, universe_slice: pd.DataFrame, sample: pd.DataFrame) -> pd.DataFrame:
    """One row per won sampled tuple with the winning universe row's keys and source."""
    return winners.merge(
        universe_slice[_KEY8 + ["source", "full_name"]].add_suffix("_univ"),
        left_on="basic_fields_df_index", right_index=True,
    ).merge(
        sample[_KEY7 + ["clean_zip", "full_name"]],
        left_on="private_foundations_df_index", right_index=True,
    )


def guard_refusals(unguarded: pd.DataFrame, guarded: pd.DataFrame) -> pd.DataFrame:
    """The unguarded wins the precision guards took away: tuples no longer
    won, or won by a different universe row (an exact pair of another EIN
    that the refused pair had outscored)."""
    if unguarded.empty:
        return unguarded
    now = guarded[["private_foundations_df_index", "basic_fields_df_index"]].rename(
        columns={"basic_fields_df_index": "_now"})
    merged = unguarded.merge(now, on="private_foundations_df_index", how="left")
    return merged[merged["_now"].isna() | (merged["_now"] != merged["basic_fields_df_index"])].drop(columns="_now")


_BANDS = ([0, 0.85, 0.95, 0.99, 1.0001], ["0.70–0.85", "0.85–0.95", "0.95–0.99", "0.99–1.00"])


def _win_line(r) -> str:
    return (f"- {r.full_name!r} @ {r.clean_zip} -> {r.full_name_univ!r} ({r.filerein_key_univ}, "
            f"{getattr(r, 'alias_kind', r.source_univ)}) name {r.name_score:.3f} addr {r.addr_score:.3f}")


def render_report(sample_n: int, unmatched_total: int, matched_tuples: int, universe_rows: int,
                  slice_rows: int, wu: pd.DataFrame, bmf_rows_dollars: tuple[int, float],
                  all_rows_dollars: tuple[int, float], tagged: pd.DataFrame, examples: pd.DataFrame,
                  refused: pd.DataFrame) -> str:
    by_source = wu["source_univ"].value_counts()
    bmf = wu[wu["source_univ"] == "eo_bmf"]
    lines = ["# BMF alias gain on a slice of currently unmatched grant tuples", "",
             f"Grant tuples: {matched_tuples + unmatched_total:,} ({matched_tuples:,} matched by the last run, "
             f"{unmatched_total:,} unmatched). Sample: {sample_n:,} unmatched tuples. "
             f"Universe with the eo_bmf arm: {universe_rows:,} rows; slice: {slice_rows:,} rows.", "",
             "## Wins on the sample", "",
             "| source of the winning row | tuples won |", "|---|---|"]
    for src, n in by_source.items():
        lines.append(f"| {src} | {n:,} |")
    lines += ["", f"{len(wu):,} of {sample_n:,} sampled tuples now match ({len(wu) / sample_n:.1%}); "
              f"{len(bmf):,} ({len(bmf) / sample_n:.1%}) through a BMF alias.", ""]
    if len(bmf):
        rows, dollars = bmf_rows_dollars
        arows, adollars = all_rows_dollars
        scale = unmatched_total / sample_n
        lines += ["## BMF wins", "",
                  f"Behind the {len(bmf):,} BMF-won tuples: {rows:,} grant rows, ${dollars:,.0f} "
                  f"(all wins: {arows:,} rows, ${adollars:,.0f}).", "",
                  f"Scaled to the {unmatched_total:,} unmatched tuples: about {len(bmf) * scale:,.0f} tuples, "
                  f"{rows * scale:,.0f} grant rows, ${dollars * scale / 1e9:,.2f}B through BMF aliases "
                  f"(all sources: {arows * scale:,.0f} rows, ${adollars * scale / 1e9:,.2f}B).", "",
                  "Name score of the BMF wins, by which BMF line won (NAME or SORT_NAME):", "",
                  "| alias | " + " | ".join(_BANDS[1]) + " | all |", "|---|" + "---|" * (len(_BANDS[1]) + 1)]
        bands = pd.cut(tagged["name_score"], _BANDS[0], right=False, labels=_BANDS[1])
        tab = pd.crosstab(tagged["alias_kind"], bands, margins=True, margins_name="all")
        for kind, row in tab.iterrows():
            lines.append(f"| {kind} | " + " | ".join(f"{int(row.get(b, 0)):,}" for b in _BANDS[1]) + f" | {int(row['all']):,} |")
        low_sort = tagged[(tagged["alias_kind"] == "sort_name") & (tagged["name_score"] < 0.85)].sort_values("name_score")
        lines += ["", f"SORT_NAME wins below name 0.85 ({len(low_sort)}), for the per-alias floor question:", ""]
        lines += [_win_line(r) for r in low_sort.itertuples()]
        lines += ["", f"Sample of {len(examples)} BMF wins (grant name @ zip -> BMF alias, EIN, name_score, addr_score):", ""]
        lines += [_win_line(r) for r in examples.itertuples()]
    lines += ["", "## Precision guards", "",
              f"Resolving the same scored pairs without the guards wins {len(wu) + len(refused):,} tuples; "
              f"the guards refuse {len(refused):,} of those wins"
              + (f" ({dict(refused['source_univ'].value_counts())})" if len(refused) else "") + ".", ""]
    lines += [_win_line(r) for r in refused.sort_values("name_score").itertuples()]
    return "\n".join(lines) + "\n"


def run(sample_n: int = 2000, report_path: Path = DEFAULT_REPORT, examples: int = 40) -> str:
    config = datamart_config()
    with get_session(config=config) as session:
        conn = session.connection()
        _load_corrections(conn)
        create_or_replace_views(conn)
        logger.info("Reading filer universe (with the eo_bmf arm) and grant tuples")
        universe = pd.read_sql_query(text(_UNIVERSE_SQL), conn)
        grants = pd.read_sql_query(text(_GRANTS_SQL), conn)
        matched = pd.read_sql_query(text(_MATCHED_TUPLES_SQL), conn).fillna("")
        alias_kind = pd.read_sql_query(text(_ALIAS_KIND_SQL), conn)
    logger.info("universe %s rows (%s), grants %s tuples, matched %s tuples",
                f"{len(universe):,}", dict(universe["source"].value_counts()), f"{len(grants):,}", f"{len(matched):,}")
    _clean_like_run(universe)
    _clean_like_run(grants)
    sample = unmatched_sample(grants, matched, sample_n)
    unmatched_total = sample.attrs["unmatched_total"]
    logger.info("unmatched %s tuples; sampled %s", f"{unmatched_total:,}", f"{len(sample):,}")
    u_slice = slice_universe(universe, sample)
    logger.info("slice: %s universe rows", f"{len(u_slice):,}")
    features = _score_slice(u_slice, sample)
    winners = _resolve_winners(features, u_slice, sample)
    refused = guard_refusals(_resolve_winners(features, u_slice, sample, guards=False), winners)
    empty = pd.DataFrame(columns=["source_univ", "name_score"])
    wu = wins_by_source(winners, u_slice, sample) if len(winners) else empty
    refused = wins_by_source(refused, u_slice, sample) if len(refused) else empty
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if len(wu):
        # The expensive part is done; keep it before the dollars query.
        wu.to_csv(report_path.with_name(report_path.stem + "_wins.csv"), index=False)
    if len(refused):
        refused.to_csv(report_path.with_name(report_path.stem + "_refused.csv"), index=False)
    bmf = wu[wu["source_univ"] == "eo_bmf"] if len(wu) else wu
    with get_session(config=config) as session:
        per_tuple = rows_dollars_by_tuple(session.connection(), wu) if len(wu) else pd.DataFrame()
    def _rd(frame):
        if frame.empty or per_tuple.empty:
            return 0, 0.0
        hit = frame[_KEY7].merge(per_tuple, on=_KEY7, how="left")
        return int(hit["n_rows"].fillna(0).sum()), float(hit["dollars"].fillna(0).astype(float).sum())
    bmf_rd, all_rd = _rd(bmf), _rd(wu)
    tagged = pd.DataFrame(columns=["alias_kind", "name_score"])
    ex = pd.DataFrame()
    if len(bmf):
        tagged = bmf.merge(alias_kind, left_on=["filerein_key_univ", "name1_key_univ"],
                           right_on=["filerein_key", "name1_key"], how="left")
        tagged["alias_kind"] = tagged["alias_kind"].fillna("name")
        ex = tagged.sample(min(examples, len(tagged)), random_state=7).sort_values("name_score")
    report = render_report(len(sample), unmatched_total, len(matched), len(universe), len(u_slice),
                           wu, bmf_rd, all_rd, tagged, ex, refused)
    report_path.write_text(report)
    logger.info("Report written to %s", report_path)
    return report


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sample", type=int, default=2000, help="unmatched grant tuples to score")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--examples", type=int, default=40, help="BMF wins to list for hand-checking")
    args = parser.parse_args(argv)
    print(run(args.sample, args.report, args.examples))
    return 0


if __name__ == "__main__":
    sys.exit(main())
