"""Government-named recipients in the last matching run: what the
government-name guard (grant_matching.guard_government_names) refuses,
measured on the output of a run made before the guard existed.

    python -m givingtuesday_datamart.exploratory.government_name_guard
    python -m givingtuesday_datamart.exploratory.government_name_guard --report path.md

Method:

1. Every privategrants_w_recipients row whose matcher-normalized full_name
   (FULL_NAME_SQL) starts with a government or public-institution prefix
   (GOVERNMENT_NAME_SQL_RE), by prefix x match_source: rows, distinct names,
   winning EINs, dollars. The whole pattern, exact wins included.
2. The distinct (grant tuple, winning EIN, match_source) behind those rows,
   with the rows and dollars each carries.
3. The winning pair of each tuple, reproduced: the universe rows of the
   winning EINs from the arms the run used (the match_source values present
   in its output), the matcher's blocking (same clean_zip or same full_name),
   Compare, both filter stages and the best-combined-score choice, all
   restricted to the winning EIN's rows. The run's winner was the best
   passing pair over the whole universe and belongs to this EIN, so it is
   also the best among this EIN's rows: the reproduced name_score is the
   winning pair's. An output that already carries match_name_score (runs
   after the guard) is read directly instead.
4. The guard's decision (grant_matching.government_refusal) on each tuple's
   winning pair, next to the plain alternative "refuse every win below
   GOVERNMENT_NAME_EXACT_MIN". The report gives both counts; for the guard's
   refusals, tuples / rows / dollars by prefix and by match_source, how many
   of them have an exact-name row of some EIN in the universe (the guard runs
   before winner resolution, so those are re-routed, not lost), the largest
   refused names for hand-checking and as corrections-registry candidates,
   and a check that the SQL and Python forms of the prefix rule agree on
   every name.

Nothing is written to the database; the temp table of winning EINs lives in
pg_temp for the session.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
import recordlinkage
from sqlalchemy import text

from givingtuesday_datamart._internal.address_cleaning import create_clean_address
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.corrections_preflight import _KEY7, _KEY8, _clean_like_run
from givingtuesday_datamart.grant_matching import (
    CHUNK_FILTER_RULES,
    create_full_name,
    FINAL_FILTER_RULES,
    FULL_NAME_SQL,
    GOVERNMENT_NAME_EXACT_MIN,
    GOVERNMENT_NAME_SQL_RE,
    filter_match_rules,
    government_refusal,
    is_government_name,
)
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.matching_regression_checks import AMT

DEFAULT_REPORT = Path("data/exploratory/government_name_guard_report.md")

_KEYS = ", ".join(_KEY7)
_PREFIX = f"substring({FULL_NAME_SQL} from '{GOVERNMENT_NAME_SQL_RE}')"

# 1. The whole pattern in the output, by prefix x match_source.
_PATTERN_SQL = f"""
    SELECT match_source, {_PREFIX} AS prefix,
           COUNT(*) AS n_rows, COUNT(DISTINCT {FULL_NAME_SQL}) AS n_names,
           COUNT(DISTINCT recipeint_ein_key) AS n_eins,
           SUM({AMT.format(col='sigocpyamoun')}) AS dollars
    FROM public.privategrants_w_recipients
    WHERE {FULL_NAME_SQL} ~ '{GOVERNMENT_NAME_SQL_RE}'
    GROUP BY 1, 2
"""

# 2. The matched tuples behind it. {score_col} adds match_name_score and
# match_name when the output carries them (a run after the guard), else "".
_TUPLES_SQL = f"""
    SELECT {_KEYS}, recipeint_ein_key, match_source, {_PREFIX} AS prefix,
           COUNT(*) AS n_rows, SUM({AMT.format(col='sigocpyamoun')}) AS dollars{{score_col}}
    FROM public.privategrants_w_recipients
    WHERE {FULL_NAME_SQL} ~ '{GOVERNMENT_NAME_SQL_RE}'
    GROUP BY {_KEYS}, recipeint_ein_key, match_source, prefix
"""

# 3. The winning EINs' universe rows, arm by arm, deduped the way the
# matcher's universe read dedups (DISTINCT ON the key tuple, lowest rank wins).
_ARMS = [
    ("basic_fields", 1, "public.basic_fields_unique_names_view"),
    ("basic_fields_pf", 2, "public.basic_fields_pf_unique_names_view"),
    ("correction", 3, "public.corrections_unique_names_view"),
    ("eo_bmf", 4, "public.eo_bmf_unique_names_view"),
]


def _universe_sql(sources: set[str]) -> str:
    arms = "\n        UNION ALL\n".join(
        f"        SELECT u.*, '{src}' AS source, {rank} AS source_rank\n"
        f"        FROM {view} u JOIN pg_temp.won_eins e ON e.ein = u.filerein_key"
        for src, rank, view in _ARMS if src in sources
    )
    key8 = ", ".join(_KEY8)
    return f"""
    SELECT {key8}, source
    FROM (
        SELECT DISTINCT ON ({key8}) *
        FROM (
{arms}
        ) arms
        ORDER BY {key8}, source_rank
    ) filers
    """


# A refused tuple whose own full_name exists in the universe with its state
# has an exact-name pair (the cross-zip exact tier needs the state to agree)
# that winner resolution now picks instead: re-routed, not lost.
def exact_name_rows(conn, sources: set[str]) -> pd.DataFrame:
    """Distinct (full_name, addressstate_key) over the whole universe, the
    arms the run used."""
    arms = "\n        UNION ALL\n".join(
        f"        SELECT name1_key, name2_key, addressstate_key FROM {view}"
        for src, _, view in _ARMS if src in sources
    )
    names = pd.read_sql_query(text(f"""
        SELECT DISTINCT name1_key, name2_key, addressstate_key FROM (
{arms}
        ) arms"""), conn).fillna("")
    names["full_name"] = [
        create_full_name({"name1_key": a, "name2_key": b})
        for a, b in zip(names["name1_key"], names["name2_key"])
    ]
    return names[["full_name", "addressstate_key"]].drop_duplicates()


def reproduce_winning_pairs(universe: pd.DataFrame, tuples: pd.DataFrame) -> pd.DataFrame:
    """The matcher's winning pair per tuple, restricted to the tuple's winning
    EIN. Returns ``tuples`` with name_score / addr_score / universe full_name
    of that pair (NaN where no pair of that EIN passes the final rules any
    more: the universe moved since the run)."""
    u = _clean_like_run(universe.copy())
    g = _clean_like_run(tuples.copy())
    for df in (u, g):
        df["compare_addr"] = df.apply(create_clean_address, axis=1)
    indexer = recordlinkage.Index()
    indexer.block(left_on="filerein_key", right_on="recipeint_ein_key")
    links = indexer.index(u, g)
    compare = recordlinkage.Compare()
    compare.exact("clean_zip", "clean_zip", label="zip_score")
    compare.string("full_name", "full_name", method="jarowinkler", label="name_score")
    compare.string("compare_addr", "compare_addr", method="levenshtein", label="addr_score")
    compare.exact("addressstate_key", "addressstate_key", label="state_score")
    features = compare.compute(links, u, g)
    features.index = features.index.set_names(["basic_fields_df_index", "private_foundations_df_index"])
    features = features.reset_index()
    # The run only scored blocked pairs: same clean_zip or same full_name.
    same_name = (
        u.loc[features["basic_fields_df_index"], "full_name"].to_numpy()
        == g.loc[features["private_foundations_df_index"], "full_name"].to_numpy()
    )
    features = features[(features["zip_score"] == 1) | same_name]
    features = filter_match_rules(features_df=features, **CHUNK_FILTER_RULES)
    features = filter_match_rules(features_df=features, **FINAL_FILTER_RULES).copy()
    features["_combined"] = features["name_score"] * 2 + features["addr_score"]
    best = (features.sort_values("_combined", ascending=False, kind="mergesort")
            .drop_duplicates(subset="private_foundations_df_index", keep="first"))
    best = best.merge(u[["full_name"]].rename(columns={"full_name": "full_name_univ"}),
                      left_on="basic_fields_df_index", right_index=True)
    out = g.merge(best[["private_foundations_df_index", "name_score", "addr_score", "full_name_univ"]],
                  left_index=True, right_on="private_foundations_df_index", how="left")
    return out.drop(columns="private_foundations_df_index").reset_index(drop=True)


def _table(df: pd.DataFrame, cols: list[str]) -> list[str]:
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df[cols].itertuples(index=False):
        cells = [f"{v:,.0f}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v) for v in r]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _sum_line(label: str, frame: pd.DataFrame) -> str:
    return (f"{label}: {len(frame):,} tuples, {int(frame['n_rows'].sum()):,} grant rows, "
            f"${float(frame['dollars'].sum()):,.0f}")


def render_report(pattern: pd.DataFrame, scored: pd.DataFrame, source_of_score: str, examples: int) -> str:
    refused = scored[scored["refused"]]
    strict = scored[scored["refused_strict"]]
    unreproduced = int(scored["name_score"].isna().sum())
    lines = ["# Government-named recipients in the last matching run", "",
             f"Winning name_score from: {source_of_score}.", "",
             "## 1. The pattern in the output (exact wins included)", ""]
    by_src = pattern.groupby("match_source")[["n_rows", "dollars"]].sum().reset_index()
    lines += _table(by_src, ["match_source", "n_rows", "dollars"]) + [""]
    by_prefix = (pattern.groupby("prefix")[["n_rows", "n_names", "n_eins", "dollars"]].sum()
                 .sort_values("n_rows", ascending=False).reset_index())
    lines += _table(by_prefix, ["prefix", "n_rows", "n_names", "n_eins", "dollars"]) + [""]
    lines += ["## 2. What the guard refuses", "",
              f"{len(scored):,} matched tuples carry the pattern; {unreproduced:,} could not be "
              "reproduced (no pair of the winning EIN passes the final rules against today's "
              "universe) and are left out.", "",
              f"- {_sum_line('Non-exact wins (name_score below ' + str(GOVERNMENT_NAME_EXACT_MIN) + '), the plain rule would refuse all of these', strict)}",
              f"- {_sum_line('The guard refuses (filer row says something the grant row does not)', refused)}",
              f"- {_sum_line('The guard keeps', strict[~strict['refused']])}",
              f"- {_sum_line('Of the refused, tuples with an exact-name row of some EIN in the universe (re-routed by winner resolution, not lost)', refused[refused['rerouted']])}",
              ""]
    for col in ("match_source", "prefix"):
        agg = (scored.assign(refused_rows=scored["n_rows"].where(scored["refused"], 0),
                             refused_dollars=scored["dollars"].where(scored["refused"], 0))
               .groupby(col).agg(tuples=("refused", "size"), refused_tuples=("refused", "sum"),
                                 rows=("n_rows", "sum"), refused_rows=("refused_rows", "sum"),
                                 dollars=("dollars", "sum"), refused_dollars=("refused_dollars", "sum"))
               .sort_values("rows", ascending=False).reset_index())
        lines += [f"By {col}:", ""] + _table(agg, [col, "tuples", "refused_tuples", "rows", "refused_rows", "dollars", "refused_dollars"]) + [""]
    bands = pd.cut(scored["name_score"].dropna(), [0, 0.85, 0.95, 0.99, 1.0001], right=False,
                   labels=["0.70-0.85", "0.85-0.95", "0.95-0.99", "0.99-1.00"])
    band_tab = (scored.loc[bands.index].assign(band=bands).groupby("band", observed=False)
                .agg(tuples=("n_rows", "size"), rows=("n_rows", "sum"), dollars=("dollars", "sum")).reset_index())
    lines += ["Winning name_score of the pattern's tuples:", ""] + _table(band_tab, ["band", "tuples", "rows", "dollars"]) + [""]
    names = (refused.groupby(["full_name", "full_name_univ", "recipeint_ein_key"])
             .agg(tuples=("n_rows", "size"), rows=("n_rows", "sum"), dollars=("dollars", "sum"),
                  name_score=("name_score", "max"), rerouted=("rerouted", "any"))
             .sort_values("dollars", ascending=False).reset_index().head(examples))
    lines += [f"## 3. Refused names, largest {len(names)} by dollars (grant name -> filer name, EIN; "
              "name score; tuples / rows / $; whether an exact-name row exists elsewhere)", "",
              "A legal name longer than the common one (\"university of notre dame du lac\") shows "
              "up here with no exact-name row: that is a corrections-registry candidate.", ""]
    for r in names.itertuples():
        lines.append(f"- {r.full_name!r} -> {r.full_name_univ!r} ({r.recipeint_ein_key}) name {r.name_score:.3f}; "
                     f"{r.tuples:,} / {r.rows:,} / ${r.dollars:,.0f}; "
                     + ("exact-name row elsewhere" if r.rerouted else "no exact-name row"))
    kept = strict[~strict["refused"]].sort_values("dollars", ascending=False).head(examples)
    lines += ["", f"## 4. Non-exact wins the guard keeps, largest {len(kept)} by dollars (grant name @ zip -> filer name, EIN, name score, rows, $)", ""]
    for r in kept.itertuples():
        lines.append(f"- {r.full_name!r} @ {r.clean_zip} -> {r.full_name_univ!r} ({r.recipeint_ein_key}) "
                     f"name {r.name_score:.3f}; {r.n_rows:,} rows, ${r.dollars:,.0f}")
    return "\n".join(lines) + "\n"


def run(report_path: Path = DEFAULT_REPORT, examples: int = 60) -> str:
    with get_session(config=datamart_config()) as session:
        conn = session.connection()
        has_score = conn.execute(text("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'privategrants_w_recipients'
              AND column_name = 'match_name_score'""")).scalar() > 0
        logger.info("Government-prefixed rows in privategrants_w_recipients, by prefix x match_source ...")
        pattern = pd.read_sql_query(text(_PATTERN_SQL), conn)
        logger.info("... %s rows in %s prefix x source groups; the tuples behind them ...",
                    f"{int(pattern['n_rows'].sum()):,}", len(pattern))
        score_col = ", MIN(match_name_score) AS match_name_score, MIN(match_name) AS match_name" if has_score else ""
        tuples = pd.read_sql_query(text(_TUPLES_SQL.format(score_col=score_col)), conn).fillna("")
        tuples["n_rows"] = tuples["n_rows"].astype(int)
        tuples["dollars"] = tuples["dollars"].astype(float)
        logger.info("... %s tuples. Universe rows of their %s winning EINs ...",
                    f"{len(tuples):,}", f"{tuples['recipeint_ein_key'].nunique():,}")
        sources = set(tuples["match_source"].unique())
        conn.execute(text("DROP TABLE IF EXISTS pg_temp.won_eins"))
        tuples[["recipeint_ein_key"]].drop_duplicates().rename(columns={"recipeint_ein_key": "ein"}) \
            .to_sql("won_eins", conn, schema="pg_temp", index=False)
        conn.execute(text("ANALYZE pg_temp.won_eins"))
        universe = pd.read_sql_query(text(_universe_sql(sources)), conn).fillna("")
        logger.info("... %s universe rows from arms %s; every exact name in the universe ...",
                    f"{len(universe):,}", sorted(sources))
        exact_names = exact_name_rows(conn, sources)
        logger.info("... %s distinct (name, state)", f"{len(exact_names):,}")

    # SQL and Python forms of the prefix rule must agree on every selected name.
    tuples_named = _clean_like_run(tuples.copy())
    disagree = tuples_named[~tuples_named["full_name"].map(is_government_name)]
    if len(disagree):
        raise RuntimeError(
            f"{len(disagree)} tuples match GOVERNMENT_NAME_SQL_RE in SQL but not "
            f"GOVERNMENT_NAME_RE in Python, e.g. {disagree['full_name'].head(5).tolist()}")

    if has_score:
        scored = tuples_named.copy()
        scored["name_score"] = scored.pop("match_name_score").astype(float)
        scored["addr_score"] = float("nan")
        scored["full_name_univ"] = scored.pop("match_name").fillna("")
        source_of_score = "privategrants_w_recipients.match_name_score / match_name (the run carried them)"
    else:
        logger.info("Reproducing the winning pair of every tuple against its winning EIN ...")
        scored = reproduce_winning_pairs(universe, tuples)
        source_of_score = "reproduced (the run predates match_name_score)"
    scored["refused_strict"] = scored["name_score"].notna() & (scored["name_score"] < GOVERNMENT_NAME_EXACT_MIN)
    scored["refused"] = [
        bool(strict) and government_refusal(g, u, score)
        for strict, g, u, score in zip(scored["refused_strict"], scored["full_name"],
                                       scored["full_name_univ"].fillna(""), scored["name_score"])
    ]
    exact = set(zip(exact_names["full_name"], exact_names["addressstate_key"]))
    scored["rerouted"] = [
        (g, st) in exact for g, st in zip(scored["full_name"], scored["addressstate_key"])
    ] & scored["refused"].to_numpy()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    scored[scored["refused_strict"]].sort_values("dollars", ascending=False).to_csv(
        report_path.with_name(report_path.stem + "_nonexact.csv"), index=False)
    report = render_report(pattern, scored, source_of_score, examples)
    report_path.write_text(report)
    logger.info("Report written to %s", report_path)
    return report


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--examples", type=int, default=60, help="refused wins to list for hand-checking")
    args = parser.parse_args(argv)
    print(run(args.report, args.examples))
    return 0


if __name__ == "__main__":
    sys.exit(main())
