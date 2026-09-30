"""Prove a matcher change on a subset, beside production.

    python -m givingtuesday_datamart.matching_subset build
    python -m givingtuesday_datamart.matching_subset report
    python -m givingtuesday_datamart.matching_subset drop

Every worktree shares one database, and products read the matcher's
output. ``grant_matching.match_records`` rebuilds the ``_current`` tables
and replaces ``privategrants_w_recipients`` and ``unioned_grants``, so a
change to the matcher cannot be tried by running it. ``build`` runs the
matcher's own pieces on a subset of the grant tuples and writes under a
prefix, ``scratch_matcher_``. It reads production's relations and writes to
none of them.

It reads the recovered grants from a copy it takes first,
``scratch_matcher_recovered``, through a view of its own with the
definition of ``privategrants_current_w_recovered``. A read of every grant
takes minutes, and a load of recovered grants waits for the whole of any
read of its table; the copy takes seconds. It also holds the numbers to one
state of the table while loads go on.

**The subset** is chosen by grant tuple, the matcher's unit (the seven
keys of a recipient):

==================  ========================================================
``recovered``       the tuples of the recovered grants, and of the grants
                    named beside them in the loaded filings
``gate``            the tuples the regression gate counts by name: the
                    placeholder pattern, ``PFIZER%``, ``WORLD HEALTH ORGANI%``
``family``          the tuples whose name is within the matcher's lowest
                    name score of a name of a sentinel filer (the gate's
                    two recipients, and Fidelity Charitable): every tuple
                    that can match the filer
``funder``          every tuple of a sample of the private foundations of
                    the labeled set, one in ``--one-in``, and of the gate's
                    funder sentinel
==================  ========================================================

Each tuple is matched against the whole universe, so the match it gets is
the match the full run gives it (the argument is in
``corrections_preflight``). It is matched twice. **Before** is the matcher
up to input shape 2: only the same normalized name is the same name, two
rows without a state agree on it, no name-only tier, no recovered grants,
and no row with a NULL city, state or zip, since such a row never joined.
**After** is the matcher as it stands.

**What it writes**, all under the prefix: ``recovered``, the copy, and
``grants``, the view over it; the seven views and the corrections table of
a run; ``schedule_i_subset``, the Schedule I rows of Fidelity Charitable,
the 990 side's canary; the join table,
``privategrants_w_recipients`` and ``unioned_grants`` of the matches after.
The output tables hold every row of a matched tuple of the subset, whichever
funder it belongs to. ``build`` also writes the tuples with both matches to
``~/.cache/matching_subset/tuples.parquet``, which ``report`` reads; the
report's tables go to ``data/exploratory/placeholder_matching_subset_*.csv``.

The gate reads the output with ``matching_regression_checks --prefix
scratch_matcher_``. Its name counts and its two recipient sentinels are
those of a full run, since the subset holds every tuple they count; its
coverage floors are not, and ``report`` gives coverage on the sampled
funders.

``drop`` drops every relation under the prefix and says what it dropped.

The scoring is the matcher's, one process a share of the tuples
(``--workers``): a pair's scores do not depend on the pairs beside it.
"""

from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import jellyfish
import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.corrections_preflight import FAMILY_NAME_JW_MIN
from givingtuesday_datamart.grant_matching import (
    FINAL_FILTER_RULES,
    INDEX_COLS,
    NAME_ONLY,
    RECOVERED_POLICY,
    Relations,
    _KEYS,
    _load_corrections,
    add_compare_addr,
    all_matches,
    create_or_replace_views,
    matched_tuples,
    prepare,
    resolve_matches,
    score_slice,
    universe_sql,
    write_matches,
)
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.matching_regression_checks import (
    AMT,
    FUNDER_SENTINEL_FLOORS,
    PLACEHOLDER_REGEX,
    RECIPIENT_SENTINEL_FLOORS,
)
from givingtuesday_datamart.placeholder_recovery import loader as recovered_loader
from givingtuesday_datamart.placeholder_recovery.view import FROM_RECOVERY, view_sql

PREFIX = "scratch_matcher_"
RECOVERED = "recovered"
GRANTS = "grants"
SCHEDULE_I = "schedule_i_subset"
FIDELITY = "110303001"
ONE_IN = 25
TUPLES = Path.home() / ".cache" / "matching_subset" / "tuples.parquet"
REPORT = Path("data/exploratory/placeholder_matching_subset_report.csv")
EXAMPLES = Path("data/exploratory/placeholder_matching_subset_examples.csv")

# The tiers, in the order a match is credited to them.
ADDRESS, STATE = "zip and name", "state and name"
TIERS = (ADDRESS, STATE, NAME_ONLY.replace("_", " "))
ADDRESS_KINDS = ("state and zip", "state, no zip", "no state")


def relations(prefix: str = PREFIX) -> Relations:
    if not prefix.startswith("scratch_"):
        raise ValueError(f"{prefix!r}: a subset is written under a prefix that starts with scratch_")
    return Relations(prefix=prefix, grants=f"{prefix}{GRANTS}", schedule_i=f"{prefix}{SCHEDULE_I}")


# ---------------------------------------------------------------------------
# The tuples
# ---------------------------------------------------------------------------


def tuples_sql(names: Relations) -> str:
    """Every grant tuple the matcher reads, with its rows and dollars and
    what puts it in the subset. The filter is the one of
    ``privategrants_unique_names_view``, so the tuples are its tuples."""
    amount = AMT.format(col="sigocpyamoun")
    recovered = f"row_source = '{FROM_RECOVERY}'"
    return f"""
        WITH loaded AS (
            SELECT DISTINCT filerein, taxyear::text AS taxyear FROM public.{names.prefix}{RECOVERED}
        ),
        funders AS (
            SELECT DISTINCT funder_ein AS filerein FROM grantor_recipient_labeled_set
            WHERE filing_type = '990PF' AND abs(hashtext(funder_ein)) % :one_in = 0
            UNION
            SELECT unnest(CAST(:sentinels AS text[]))
        )
        SELECT {', '.join(_KEYS)},
               count(*) AS n_rows,
               coalesce(sum({amount}), 0)::float8 AS dollars,
               count(*) FILTER (WHERE {recovered}) AS recovered_rows,
               coalesce(sum({amount}) FILTER (WHERE {recovered}), 0)::float8 AS recovered_dollars,
               count(*) FILTER (WHERE f.filerein IS NOT NULL) AS funder_rows,
               coalesce(sum({amount}) FILTER (WHERE f.filerein IS NOT NULL), 0)::float8 AS funder_dollars,
               bool_or(l.filerein IS NOT NULL) AS in_loaded,
               bool_or(concat_ws(' ', sigocpyrpnam, sigocpyrbnbn1, sigocpyrbnbn2) ~* '{PLACEHOLDER_REGEX}'
                       OR sigocpyrbnbn1 ILIKE 'pfizer%' OR sigocpyrbnbn1 ILIKE '%world health organi%') AS in_gate
        FROM public.{names.of('privategrants_w_column_keys_view')} g
        LEFT JOIN loaded l ON l.filerein = g.filerein AND l.taxyear = g.taxyear
        LEFT JOIN funders f ON f.filerein = g.filerein
        WHERE g.taxyear::int >= 2015
          AND NOT (TRIM(name1_key) = '' AND TRIM(name2_key) = '')
        GROUP BY {', '.join(_KEYS)}
        ORDER BY {', '.join(_KEYS)}
    """


def families(universe: pd.DataFrame, grants: pd.DataFrame, eins: list[str]) -> pd.Series:
    """Is a grant tuple in the name family of one of ``eins``? Its compared
    name is within the lowest name score any tier accepts of a name the
    filer has in the universe, or its cleaned name is one of the filer's."""
    theirs = universe[universe["filerein_key"].isin(eins)]
    names = pd.Series(grants["compare_name"].unique())
    family: set[str] = set()
    for name in sorted(set(theirs["compare_name"])):
        scores = names.map(lambda other: jellyfish.jaro_winkler_similarity(name, other))
        family.update(names[scores >= FAMILY_NAME_JW_MIN])
    return grants["compare_name"].isin(family) | grants["full_name"].isin(set(theirs["full_name"]))


def as_before(df: pd.DataFrame) -> pd.DataFrame:
    """A prepared frame as the matcher prepared it up to input shape 2:
    the same name is the same normalized name, and the state is compared
    as it stands, so that two rows without one agree."""
    before = df.copy()
    before["full_name"] = before["compare_name"]
    before["compare_state"] = before["addressstate_key"]
    return before


def score(universe: pd.DataFrame, grants: pd.DataFrame, workers: int) -> pd.DataFrame:
    """``score_slice`` over ``grants`` in ``workers`` processes, each with a
    share of the tuples and the whole universe."""
    if workers <= 1 or len(grants) < workers:
        return score_slice(universe, grants)
    shares = [grants.iloc[start::workers] for start in range(workers)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        scored = list(pool.map(score_slice, [universe] * workers, shares))
    return pd.concat(scored, ignore_index=True)


def tier(matches: pd.DataFrame) -> pd.Series:
    """The tier a match is credited to: the address tiers where any of them
    accepts the pair, else the exact name with the state, else the name
    alone."""
    rules = FINAL_FILTER_RULES
    name, address = matches["name_score"], matches["addr_score"]
    by_address = (((name >= rules["near_perfect_name_name_min"]) & (address >= rules["near_perfect_name_addr_min"]))
                  | ((name >= rules["near_perfect_addr_name_min"]) & (address >= rules["near_perfect_addr_addr_min"]))
                  | ((name >= rules["good_enough_name_name_min"]) & (address >= rules["good_enough_name_addr_min"])))
    found = pd.Series(STATE, index=matches.index).where(~by_address, ADDRESS)
    return found.where(matches["match_name_words"].isna(), TIERS[2])


def address_kind(df: pd.DataFrame) -> pd.Series:
    has_zip = df["addresszip_key"].str.strip() != ""
    has_state = df["addressstate_key"].str.strip() != ""
    return pd.Series(ADDRESS_KINDS[2], index=df.index).where(~has_state, ADDRESS_KINDS[1]).where(
        ~(has_state & has_zip), ADDRESS_KINDS[0])


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def build(prefix: str = PREFIX, policy: str = RECOVERED_POLICY, one_in: int = ONE_IN, workers: int = 1,
          out: Path = TUPLES) -> None:
    names = relations(prefix)
    sentinels = list(RECIPIENT_SENTINEL_FLOORS) + [FIDELITY]
    copy = f"{prefix}{RECOVERED}"
    with get_session(config=datamart_config()) as session:
        connection = session.connection()
        connection.execute(text(f"DROP TABLE IF EXISTS public.{copy} CASCADE"))
        connection.execute(text(f"""
            CREATE TABLE public.{copy} AS
            SELECT * FROM public.{recovered_loader.TABLE}
            WHERE policy_version = :policy AND target = '{recovered_loader.PAID}'
        """), {"policy": policy})
        connection.execute(text(f"ANALYZE public.{copy}"))
        connection.execute(text(view_sql(policy, name=names.grants, recovered=copy)))
        session.commit()
        connection = session.connection()
        _load_corrections(connection, names)
        create_or_replace_views(connection, names, recovered_policy=policy)
        connection.execute(text(f"DROP TABLE IF EXISTS public.{names.schedule_i}"))
        connection.execute(text(f"""
            CREATE TABLE public.{names.schedule_i} AS
            SELECT * FROM public.grants_to_domestic_organizations_current WHERE filerein = '{FIDELITY}'
        """))
        session.commit()
        connection = session.connection()
        logger.info("Reading the universe")
        universe = pd.read_sql_query(text(universe_sql(names)), connection)
        logger.info("Reading every grant tuple, with its rows and dollars")
        grants = pd.read_sql_query(text(tuples_sql(names)), connection, params={
            "one_in": one_in, "sentinels": list(FUNDER_SENTINEL_FLOORS)})
        production = pd.read_sql_query(text(
            f"SELECT {', '.join(_KEYS)}, recipeint_ein_key AS production_ein FROM public.pf_grant_matching_temp_table"),
            connection)
    logger.info(f"universe rows: {len(universe):,}; grant tuples: {len(grants):,}")

    prepare(universe)
    prepare(grants, addresses=False)
    grants["in_family"] = families(universe, grants, sentinels)
    subset = grants[(grants["recovered_rows"] > 0) | grants["in_loaded"] | grants["in_gate"] | grants["in_family"]
                    | (grants["funder_rows"] > 0)].copy()
    add_compare_addr(subset)
    logger.info(f"the subset: {len(subset):,} tuples of {len(grants):,}, {int(subset['n_rows'].sum()):,} rows")

    # Before: no recovered grant, and no row with a NULL city, state or zip.
    regular = subset[subset["n_rows"] > subset["recovered_rows"]]
    logger.info(f"Matching before: {len(regular):,} tuples")
    before = resolve_matches(score(as_before(universe), as_before(regular), workers))
    joined = (subset[["addresscity_key", "addressstate_key", "addresszip_key"]] != "").all(axis=1)
    before = before[before[INDEX_COLS[1]].map(joined)]
    logger.info(f"Matching after: {len(subset):,} tuples")
    after = all_matches(score(universe, subset, workers), universe, subset)
    # The join table, while the matches and the tuples share an index:
    # the merge below gives the tuples a new one.
    join_table = matched_tuples(universe, subset, after)

    ein = universe["filerein_key"]
    subset["before_ein"] = before.set_index(INDEX_COLS[1])[INDEX_COLS[0]].map(ein)
    subset["before_name"] = before.set_index(INDEX_COLS[1])[INDEX_COLS[0]].map(universe["compare_name"])
    found = after.set_index(INDEX_COLS[1])
    subset["after_ein"] = found[INDEX_COLS[0]].map(ein)
    subset["after_name"] = found[INDEX_COLS[0]].map(universe["compare_name"])
    subset["same_name"] = subset["full_name"] == found[INDEX_COLS[0]].map(universe["full_name"]).reindex(subset.index)
    subset["after_state"] = found[INDEX_COLS[0]].map(universe["addressstate_key"])
    subset["tier"] = tier(found)
    subset["match_name_words"] = found["match_name_words"]
    subset = subset.merge(production.fillna("").drop_duplicates(_KEYS), on=_KEYS, how="left")
    subset["address_kind"] = address_kind(subset)

    out.parent.mkdir(parents=True, exist_ok=True)
    subset.drop(columns=["clean_zip", "compare_state"]).to_parquet(out, index=False)
    logger.info(f"wrote {out}")

    with get_session(config=datamart_config()) as session:
        matched_rows, unioned_rows = write_matches(session.connection(), join_table, names)
    print(f"{len(subset):,} tuples matched twice; {prefix}privategrants_w_recipients {matched_rows:,} rows, "
          f"{prefix}unioned_grants {unioned_rows:,} rows")


# ---------------------------------------------------------------------------
# drop
# ---------------------------------------------------------------------------


def created(session, prefix: str) -> list[tuple[str, str]]:
    """The relations under the prefix: name and kind (r table, v view)."""
    found = session.execute(text("""
        SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v') AND left(c.relname, :n) = :prefix
        ORDER BY c.relkind DESC, c.relname
    """), {"prefix": prefix, "n": len(prefix)})
    return [(name, kind) for name, kind in found]


def drop(prefix: str = PREFIX) -> list[str]:
    relations(prefix)                       # refuses a prefix that is not scratch
    with get_session(config=datamart_config()) as session:
        found = created(session, prefix)
        for name, kind in found:
            session.execute(text(f"DROP {'VIEW' if kind == 'v' else 'TABLE'} IF EXISTS public.{name} CASCADE"))
        session.commit()
        left = created(session, prefix)
    if left:
        raise RuntimeError(f"still there: {left}")
    return [name for name, _ in found]


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _share(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole else ""


def recovered_by_tier(subset: pd.DataFrame) -> list[dict]:
    """The recovered grants by what their address gives and the tier that
    matched them, in rows and dollars."""
    rows = subset[subset["recovered_rows"] > 0]
    table = []
    for kind in ADDRESS_KINDS + ("all",):
        of_kind = rows if kind == "all" else rows[rows["address_kind"] == kind]
        n, dollars = of_kind["recovered_rows"].sum(), of_kind["recovered_dollars"].sum()
        line = {"address": kind, "tuples": len(of_kind), "rows": int(n), "dollars": round(dollars)}
        for name in TIERS:
            hit = of_kind[of_kind["tier"] == name]
            line[f"{name}, rows"] = int(hit["recovered_rows"].sum())
            line[f"{name}, dollars"] = round(hit["recovered_dollars"].sum())
        hit = of_kind[of_kind["after_ein"].notna()]
        line["matched rows"] = _share(hit["recovered_rows"].sum(), n)
        line["matched dollars"] = _share(hit["recovered_dollars"].sum(), dollars)
        table.append(line)
    return table


def regular_changes(subset: pd.DataFrame) -> tuple[list[dict], pd.DataFrame]:
    """What the change does to the grants that were there before it: the
    tuples of the sampled funders, by what became of their match, with the
    funders' rows and dollars; and the tuples that changed."""
    rows = subset[subset["funder_rows"] > 0].copy()
    before, after = rows["before_ein"].fillna(""), rows["after_ein"].fillna("")
    never_joined = (rows[["addresscity_key", "addressstate_key", "addresszip_key"]] == "").any(axis=1)
    rows["change"] = "unmatched, both"
    rows.loc[(before != "") & (after == before), "change"] = "matched, the same filer"
    rows.loc[(before != "") & (after != "") & (after != before), "change"] = "matched, another filer"
    rows.loc[(before != "") & (after == ""), "change"] = "lost"
    rows.loc[(before == "") & (after != ""), "change"] = "gained"
    rows.loc[(before == "") & (after != "") & never_joined, "change"] = "gained, a row that never joined"
    rows.loc[(before == "") & (after != "") & (rows["tier"] == TIERS[2]), "change"] = "gained, on the name alone"
    total_rows, total_dollars = rows["funder_rows"].sum(), rows["funder_dollars"].sum()
    table = [{"change": change, "tuples": len(found), "rows": int(found["funder_rows"].sum()),
              "dollars": round(found["funder_dollars"].sum()),
              "of rows": _share(found["funder_rows"].sum(), total_rows),
              "of dollars": _share(found["funder_dollars"].sum(), total_dollars)}
             for change, found in sorted(rows.groupby("change"), key=lambda item: -item[1]["funder_rows"].sum())]
    return table, rows[~rows["change"].isin(("unmatched, both", "matched, the same filer"))]


def examples(changed: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """The ``n`` largest tuples of each change, by the funders' dollars."""
    columns = ["change", "name1_key", "name2_key", "address1_key", "addresscity_key", "addressstate_key",
               "addresszip_key", "funder_rows", "funder_dollars", "before_ein", "before_name", "after_ein",
               "after_name", "after_state", "tier"]
    return (changed.sort_values("funder_dollars", ascending=False).groupby("change").head(n)
            .sort_values(["change", "funder_dollars"], ascending=[True, False])[columns])


def against_production(subset: pd.DataFrame) -> dict:
    """The harness against the last full run: of the regular tuples, how
    many get before what ``pf_grant_matching_temp_table`` holds for them."""
    rows = subset[subset["n_rows"] > subset["recovered_rows"]]
    joined = (rows[["addresscity_key", "addressstate_key", "addresszip_key"]] != "").all(axis=1)
    rows = rows[joined]
    before, production = rows["before_ein"].fillna(""), rows["production_ein"].fillna("")
    return {"tuples": len(rows), "the same": int((before == production).sum()),
            "matched here only": int(((before != "") & (production == "")).sum()),
            "matched in production only": int(((before == "") & (production != "")).sum()),
            "another filer": int(((before != "") & (production != "") & (before != production)).sum())}


def _table(rows: list[dict], title: str) -> None:
    if not rows:
        return
    print(f"\n{title}\n")
    print("| " + " | ".join(rows[0]) + " |")
    print("|" + "|".join("---" for _ in rows[0]) + "|")
    for row in rows:
        print("| " + " | ".join(f"{v:,}" if isinstance(v, int) else str(v) for v in row.values()) + " |")


def report(tuples: Path = TUPLES, out: Path = REPORT, examples_out: Path = EXAMPLES) -> None:
    subset = pd.read_parquet(tuples)
    by_tier = recovered_by_tier(subset)
    changes, changed = regular_changes(subset)
    agreement = against_production(subset)
    _table(by_tier, "the recovered grants, by address and tier")
    _table(changes, "the sampled funders' grants, before and after")
    _table([agreement], "before, against the last full run")
    pd.concat([pd.DataFrame(by_tier).assign(table="recovered"), pd.DataFrame(changes).assign(table="regular"),
               pd.DataFrame([agreement]).assign(table="production")]).to_csv(out, index=False)
    examples(changed).to_csv(examples_out, index=False)
    print(f"\nwrote {out} and {examples_out}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="match the subset before and after, and write the output under the prefix")
    p.add_argument("--policy", default=RECOVERED_POLICY)
    p.add_argument("--one-in", type=int, default=ONE_IN, help="the share of the labeled set's foundations sampled")
    p.add_argument("--workers", type=int, default=1, help="processes that score")
    sub.add_parser("report", help="the tables, from the tuples `build` wrote")
    sub.add_parser("drop", help="drop every relation under the prefix")
    for command in sub.choices.values():
        command.add_argument("--prefix", default=PREFIX)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("recordlinkage").setLevel(logging.WARNING)
    started = time.monotonic()
    if args.command == "build":
        build(args.prefix, args.policy, args.one_in, args.workers)
    elif args.command == "report":
        report()
    else:
        dropped = drop(args.prefix)
        print("dropped: " + (", ".join(dropped) if dropped else "nothing, there was nothing under the prefix"))
    print(f"{time.monotonic() - started:.0f} s")


if __name__ == "__main__":
    main()
