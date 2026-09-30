"""Pre-run verification of the corrections registry (data/corrections/org_identities.csv).

Checks that each correction row blocks, scores, and wins as intended,
without running the full matching pipeline. Run after any edit to the CSV
and before match_records():

    python -m givingtuesday_datamart.corrections_preflight

Exit code 0 = all checks pass; 1 = at least one FAIL.

For each CSV row it verifies, using the matcher's own functions (``prepare``
and ``match_slice``: the run's cleaning, blocking, scores, the CHUNK/FINAL
threshold sets, the best-per-recipient-tuple winner resolution and the
name-only tier) over the matcher's own SQL against the real views:

  (a) the row survives the DISTINCT ON universe dedup — if it collapses
      into an organic filing with the same key tuple, the correction does
      nothing and should be pruned from the CSV;
  (b) at least one grant tuple's *winning* universe row is that correction
      row, routing the tuple to its recipient_ein — and where the
      correction's verbatim tuple exists in the grant view, that tuple is
      won to recipient_ein (expected at name_score 1.0);

and reports tuples / grant rows / dollars won per correction row, via the
same 7-key join the pipeline's SELECT INTO uses to build
privategrants_w_recipients.

Why a slice is faithful to the full run: every decision that picks a grant
tuple's winner is per-pair and deterministic. Blocking pairs a universe row
u with a grant tuple g iff clean_zip(u) == clean_zip(g) OR full_name(u) ==
full_name(g) (both exact keys), and every filter tier requires name_score
>= FAMILY_NAME_JW_MIN — so a correction can only ever win grant tuples
whose compared name is within that Jaro-Winkler floor of one of its own
names, or whose cleaned name is one of its own, which scores 1
(its "name family"). Per correction family (CSV rows sharing
recipient_ein), the slice takes:

  grants  G — every tuple in the name family: a complete superset of every
      tuple any of the family's correction rows could win;
  universe U — every universe row whose clean_zip is among G's zips (or the
      corrections' own) or whose full_name is among G's names (or the
      corrections' own): every row that can block with any g in G.

For each g in G the full competitive set is therefore present, and the
winner computed here is the winner the full run computes. Global-run
effects the slice doesn't replicate — chunking, the categorical dtype
spanning the full universe, and input order on exact combined-score ties —
don't change per-pair scores. The name-only tier asks that a name belong
to one filer of the universe; every universe row under a family name is in
the slice, so the slice answers as the universe does.

Side effects: the same ones match_records() performs at start of run —
loads the CSV into public.corrections_org_identities and refreshes the
matching views — so the preflight reads the same state a run would. With
``--prefix`` it creates its table and views under that prefix and leaves
the run's own as they are:

    python -m givingtuesday_datamart.corrections_preflight --prefix scratch_matcher_
"""

from __future__ import annotations

import argparse
import logging
import sys

import jellyfish
import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.grant_matching import (
    FINAL_FILTER_RULES,
    Relations,
    _load_corrections,
    _read_corrections_csv,
    create_or_replace_views,
    grants_sql,
    match_slice,
    prepare,
    score_slice,
    universe_sql,
)
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.matching_regression_checks import AMT, Gate

# The lowest name_score any final filter tier accepts. A universe row can
# never win a grant tuple below it, so it bounds the name family a
# correction could possibly reach. Derived from FINAL_FILTER_RULES so a
# matcher threshold change re-scopes the preflight automatically.
FAMILY_NAME_JW_MIN = min(
    FINAL_FILTER_RULES[k]
    for k in (
        "near_perfect_name_name_min",
        "near_perfect_addr_name_min",
        "good_enough_name_name_min",
        "exact_name_name_min",
    )
)

_KEY7 = [
    "name1_key",
    "name2_key",
    "address1_key",
    "address2_key",
    "addresscity_key",
    "addressstate_key",
    "addresszip_key",
]
_KEY8 = ["filerein_key"] + _KEY7

# The read of the filer universe, under production's names.
_UNIVERSE_SQL = universe_sql()


def _clean_like_run(df: pd.DataFrame) -> pd.DataFrame:
    """The cleaning _do_match_records applies before blocking, less
    compare_addr, which is left to the slice (it's row-local, so slice-time
    values are identical)."""
    return prepare(df, addresses=False)


def _keyed_corrections(csv_df: pd.DataFrame) -> pd.DataFrame:
    """One row per CSV line, carrying the *_key values
    corrections_unique_names_view derives from it.

    Python-side replica of the view's key expressions, used only to
    attribute universe rows and wins back to CSV lines — the authoritative
    normalization is the view itself, which feeds _UNIVERSE_SQL.
    """
    keyed = pd.DataFrame(
        {
            "line_no": range(2, len(csv_df) + 2),  # line 1 is the header
            "filerein_key": csv_df["recipient_ein"].str.lower(),
            "name1_key": csv_df["name1"].str.lower(),
            "name2_key": csv_df["name2"].str.lower(),
            "address1_key": csv_df["address1"].str.lower(),
            "address2_key": csv_df["address2"].str.lower(),
            "addresscity_key": csv_df["city"].str.lower(),
            "addressstate_key": csv_df["state"].str.lower(),
            "addresszip_key": csv_df["zip"].str[:5].str.lower(),
        }
    )
    return _clean_like_run(keyed)


def _score_slice(universe_slice: pd.DataFrame, grants_slice: pd.DataFrame) -> pd.DataFrame:
    """match_records' blocking → Compare on a slice: one row per candidate
    pair with the four scores, unfiltered."""
    return score_slice(universe_slice, grants_slice, chunk_rules=False)


def _rows_dollars_won(conn, tuples: pd.DataFrame, relations: Relations = Relations()) -> tuple[int, float]:
    """Grant rows / dollars behind a set of won recipient tuples, via the
    same 7-key join the pipeline's SELECT INTO uses to build
    privategrants_w_recipients."""
    if tuples.empty:
        return 0, 0.0
    params: dict[str, str] = {}
    values = []
    for i, row in enumerate(tuples[_KEY7].itertuples(index=False)):
        for j, val in enumerate(row):
            params[f"v{i}_{j}"] = val
        values.append("(" + ", ".join(f":v{i}_{j}" for j in range(7)) + ")")
    on = " AND ".join(f"t.{c} = pg.{c}" for c in _KEY7)
    result = pd.read_sql_query(
        text(f"""
            SELECT COUNT(*) AS n_rows,
                   SUM({AMT.format(col='sigocpyamoun')}) AS dollars
            FROM public.{relations.of('privategrants_w_column_keys_view')} pg
            JOIN (VALUES {", ".join(values)}) t({", ".join(_KEY7)}) ON {on}
        """),
        conn,
        params=params,
    ).iloc[0]
    return int(result.n_rows), float(result.dollars or 0.0)


def _verbatim_mask(df: pd.DataFrame, line) -> pd.Series:
    """Grant tuples equal to the correction's own keyed tuple (zip compared
    post-clean_zip, so a 9-digit filing zip still matches its 5-digit key)."""
    mask = df["clean_zip"] == line.clean_zip
    for col in _KEY7[:-1]:
        mask &= df[col] == getattr(line, col)
    return mask


def run_preflight(relations: Relations = Relations()) -> int:
    gate = Gate()
    csv_df = _read_corrections_csv()  # raises loudly on structural problems
    keyed = _keyed_corrections(csv_df)
    keyed["label"] = [
        f"CSV line {ln} ({ein} @ {z})"
        for ln, ein, z in zip(keyed.line_no, keyed.filerein_key, keyed.addresszip_key)
    ]

    config = datamart_config()
    with get_session(config=config) as session:
        conn = session.connection()
        # Same start-of-run side effects as match_records: CSV load into
        # corrections_org_identities + view refresh.
        _load_corrections(conn, relations)
        create_or_replace_views(conn, relations)
        logger.info(
            "Reading filer universe: basic_fields_unique_names_view "
            "∪ basic_fields_pf_unique_names_view ∪ corrections_unique_names_view"
        )
        universe = pd.read_sql_query(text(universe_sql(relations)), conn)
        logger.info("Reading public.privategrants_unique_names_view")
        grants = pd.read_sql_query(text(grants_sql(relations)), conn)

    logger.info(f"universe rows: {len(universe):,}; grant tuples: {len(grants):,}")
    logger.info("Cleaning (clean_zip + full_name) ...")
    _clean_like_run(universe)
    _clean_like_run(grants)

    # --- (a) DISTINCT ON dedup survival --------------------------------
    # Post-dedup the universe holds at most one row per key tuple, so this
    # left-merge yields exactly one row per CSV line.
    surv = keyed.merge(universe[_KEY8 + ["source"]], on=_KEY8, how="left")
    for row in surv.itertuples():
        if pd.isna(row.source):
            gate.check(
                f"{row.label}: in deduped universe", False,
                "key tuple absent from the universe read — load/view problem",
            )
        else:
            gate.check(
                f"{row.label}: survives universe dedup",
                row.source == "correction",
                "source='correction'" if row.source == "correction" else
                f"collapsed into organic '{row.source}' row — redundant, prune it",
            )

    # --- (b) slice-match each correction family ------------------------
    uniq_grant_names = pd.Series(grants["compare_name"].unique())
    logger.info(f"unique grant names: {len(uniq_grant_names):,}")
    report_rows = []
    with get_session(config=config) as session:
        conn = session.connection()
        for ein, fam in keyed.groupby("filerein_key", sort=True):
            fam_names = set(fam["full_name"])
            fam_zips = set(fam["clean_zip"])
            family_names: set[str] = set()
            for cname in sorted(set(fam["compare_name"])):
                sims = uniq_grant_names.map(
                    lambda n: jellyfish.jaro_winkler_similarity(cname, n)
                )
                family_names.update(uniq_grant_names[sims >= FAMILY_NAME_JW_MIN])
            g_slice = grants[
                grants["compare_name"].isin(family_names) | grants["full_name"].isin(fam_names)
            ]
            u_slice = universe[
                universe["clean_zip"].isin(set(g_slice["clean_zip"]) | fam_zips)
                | universe["full_name"].isin(set(g_slice["full_name"]) | fam_names)
            ]
            logger.info(
                f"family {ein}: {len(fam)} correction rows, "
                f"{len(g_slice):,} name-family grant tuples, "
                f"{len(u_slice):,} universe rows in slice"
            )
            winners = match_slice(u_slice, g_slice)
            wu = winners.merge(
                universe[_KEY8 + ["source"]].add_suffix("_univ"),
                left_on="basic_fields_df_index",
                right_index=True,
            ).merge(
                grants[_KEY7 + ["clean_zip"]],
                left_on="private_foundations_df_index",
                right_index=True,
            )
            n_corr_won = int((wu["source_univ"] == "correction").sum())
            logger.info(
                f"family {ein}: {len(wu):,} family tuples matched; "
                f"corrections won {n_corr_won}"
            )

            for line in fam.itertuples():
                label = line.label

                # Verbatim tuple: the correction was (normally) authored from
                # a sighted grant row, so its own tuple should exist in the
                # grant view and be won to recipient_ein at name 1.0.
                n_verbatim = int(_verbatim_mask(g_slice, line).sum())
                if n_verbatim == 0:
                    gate.check(
                        f"{label}: verbatim tuple in grant view", False,
                        "not found — correction not authored from a sighted "
                        "grant row?", warn_only=True,
                    )
                else:
                    vb = wu[_verbatim_mask(wu, line)]
                    won_ok = (
                        len(vb) == n_verbatim
                        and (vb["filerein_key_univ"] == line.filerein_key).all()
                    )
                    detail = f"{len(vb)}/{n_verbatim} won to {ein}"
                    if len(vb):
                        detail += (
                            f", min name_score {vb['name_score'].min():.2f}, "
                            f"winner source(s) {sorted(set(vb['source_univ']))}"
                        )
                    gate.check(f"{label}: verbatim tuple won to its EIN", won_ok, detail)

                # Wins attributed to this exact correction row (the winning
                # universe row carries source='correction' and the row's own
                # key tuple).
                won_mask = wu["source_univ"] == "correction"
                for col in _KEY8:
                    won_mask &= wu[f"{col}_univ"] == getattr(line, col)
                won = wu[won_mask]
                n_rows, dollars = _rows_dollars_won(conn, won, relations)
                gate.check(
                    f"{label}: wins >=1 grant tuple",
                    len(won) >= 1,
                    f"{len(won)} tuples, {n_rows:,} grant rows, ${dollars:,.0f}",
                )
                report_rows.append(
                    {
                        "line": line.line_no,
                        "recipient_ein": ein,
                        "zip": line.addresszip_key,
                        "family_tuples_matched": len(wu),
                        "tuples_won": len(won),
                        "grant_rows_won": n_rows,
                        "dollars_won": dollars,
                    }
                )

    report = pd.DataFrame(report_rows)
    print("\n=== corrections registry: wins per CSV row ===")
    print(report.to_string(index=False))
    total_rows = report["grant_rows_won"].sum()
    total_dollars = report["dollars_won"].sum()
    print(
        f"\ncorrections total: {report['tuples_won'].sum()} tuples won, "
        f"{total_rows:,} grant rows, ${total_dollars:,.0f}"
    )
    return gate.report()


def main() -> None:
    # The package logger ships without handlers (operators attach their own);
    # as a CLI, attach one so the multi-minute run isn't silent.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--prefix", default="",
                        help="create the corrections table and the views under this prefix")
    parser.add_argument("--grants", default=Relations().grants,
                        help="the relation the grants are read from")
    args = parser.parse_args()
    sys.exit(run_preflight(Relations(prefix=args.prefix, grants=args.grants)))


if __name__ == "__main__":
    main()
