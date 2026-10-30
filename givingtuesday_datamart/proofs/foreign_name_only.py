"""The name-only tier for a foreign address, proved on a subset before a run.

    python -m givingtuesday_datamart.proofs.foreign_name_only extract --out DIR [--one-in 500]
    python -m givingtuesday_datamart.proofs.foreign_name_only compare --out DIR [--workers 8]

Since 2026-10-30 the name-only tier takes a grant tuple with no US address
(``grant_matching.no_us_address``: a state key that is no US state, or a
row naming a country other than the US) where it took one with no state
and no zip. A foreign address fills the US address columns, so the Global
Fund's grants, keyed "ge" and "1218", had a state for the tier and no US
state for any other. This proof matches, against the whole filer universe,
every tuple the new rule can reach that the old could not, and says what
the next full run will match that the run of 2026-10-30 did not.

**The tuples**, chosen by key alone, with every row of each counted
(eligibility is 2015+, the write joins every year):

==============  ===========================================================
``pool``        the 1,199 names the run of 2026-10-28 matched on the name
                alone with empty address keys and the run of 2026-10-30
                left unmatched once the foreign address filled the keys
                (``data/proofs/name_only_lost_names_2026_10_30.csv``)
``foreign``     every tuple with no US address that has a state or a zip
                key, or a row naming another country: what the new rule
                makes eligible and the old did not
``gate``        the gate's named classes, ``PFIZER%`` and ``WORLD HEALTH
                ORGANI%``, so the proof says what its ceilings will see
``sample``      a 1-in-N hash sample of every tuple, the control: the
                rule must change nothing there but foreign tuples
==============  ===========================================================

``extract`` reads the universe, the tuples, the join table of the last
run and the matched rows per recipient in one read-only repeatable-read
transaction (64 MB work_mem, no parallel workers, a 25-minute statement
timeout) and freezes them under ``--out`` with their digests. ``compare``
scores the frozen tuples once with the matcher's own blocking and
scoring, then resolves five rules: ``before`` (no state and no zip),
``keys`` (the state key alone), ``country`` (the old rule plus the
country column), ``after`` (production's rule: both signals and the
one-word guard, ``grant_matching.one_word_namesakes``) and ``unguarded``
(production's rule without the guard). The rule before must reproduce the
last run's winner for every tuple, and no rule may lose or move a match:
the tier only adds. ``refused.csv`` lists what the guard refuses. The reports go beside
the frozen files; ``docs/foreign-recipients-name-only-2026-10-30.md``
holds the findings.

``corrections --out DIR [--csv FILE]`` measures the corrections registry on
the same frozen files, with nothing written to the database. It adds the
registry's rows that the frozen universe lacks, keyed as
``corrections_unique_names_view`` keys them, and matches again every
selected tuple a new row can change: one it blocks with, or one of its
name, since a new row is a new owner of its name for the name-only tier.
It matches them against every universe row that can block with those
tuples (``corrections_preflight`` has the argument). The same slice without
the new rows must reproduce ``compare``'s matches; ``corrections.csv`` lists
the tuples that move and ``corrections_rows.csv`` what each new row takes.
Tuples outside the selection that share a new row's zip or name are not
measured: for a row at a foreign address, those are tuples of its name at
a US address, which no tier lets a foreign row take.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.matching_regression_checks import AMT
from givingtuesday_datamart.placeholder_recovery.view import FROM_RECOVERY
from givingtuesday_datamart.proofs import bmf_policy_subset as base

POOL = Path(__file__).resolve().parents[2] / "data" / "proofs" / "name_only_lost_names_2026_10_30.csv"
POLICIES = ("before", "keys", "country", "after", "unguarded")
COHORTS = ("pool", "foreign", "gate", "sample")
GATE_PATTERNS = ("pfizer%", "%world health organi%")
GLOBAL_FUND = "980380092"
SENTINEL_SHARE = 0.95
OUR_ROWS = "_source_version LIKE '%TEOS_XML%'"
FOREIGN_COUNTRY = "UPPER(TRIM(COALESCE(sigocaffrfaco, ''))) NOT IN ('', 'US', 'USA')"
KIND_FOREIGN = "98- (the recipient's own registration)"
KIND_DOMESTIC = "domestic EIN (an affiliate or a namesake)"


def selection_sql() -> str:
    """The selected tuples, one row per tuple and tax year, with the
    tuple's flags repeated on each: the keys, the rows, dollars and
    recovered rows of the year, how many rows are our extract's, and the
    cohorts. The filters are the matcher's view's (a business name, not a
    placeholder); eligibility is one row of 2015 or later; the foreign
    cohort is ``no_us_address`` in SQL, less what the old rule took."""
    keys = ", ".join(gm._KEYS)
    keyed = ", ".join("g." + k for k in gm._KEYS)
    name = "concat_ws(' ', g.name1_key, g.name2_key)"
    amount = AMT.format(col="sigocpyamoun")
    sample = f"mod(abs(hashtextextended(jsonb_build_array({keyed})::text, 0)), :one_in) = 0"
    us_state = "lower(trim(g.addressstate_key)) = ANY(CAST(:us_keys AS text[]))"
    no_keys = "trim(g.addressstate_key) = '' AND trim(g.addresszip_key) = ''"
    gate = " OR ".join(f"sigocpyrbnbn1 ILIKE '{pattern}'" for pattern in GATE_PATTERNS)
    return f"""
        WITH pool AS (
            SELECT * FROM jsonb_to_recordset(CAST(:pool AS jsonb)) AS p(name1_key text, name2_key text)
        ),
        tuples AS (
            SELECT {keyed},
                   bool_or(taxyear::int >= 2015 AND {FOREIGN_COUNTRY}) AS foreign_country,
                   bool_or(p.name1_key IS NOT NULL) AS pool,
                   bool_or({gate}) AS gate,
                   ({sample}) AS sample
            FROM public.privategrants_w_column_keys_view g
            LEFT JOIN pool p ON p.name1_key = g.name1_key AND p.name2_key = g.name2_key
            WHERE NOT (trim(g.name1_key) = '' AND trim(g.name2_key) = '')
              AND NOT ({name} ~* :placeholder)
            GROUP BY {keyed}
            HAVING bool_or(taxyear::int >= 2015)
               AND (bool_or(p.name1_key IS NOT NULL) OR bool_or({gate}) OR ({sample})
                    OR (NOT ({us_state}) AND NOT ({no_keys}))
                    OR bool_or(taxyear::int >= 2015 AND {FOREIGN_COUNTRY}))
        )
        SELECT {', '.join('t.' + k for k in gm._KEYS)}, t.foreign_country, t.pool, t.gate, t.sample,
               g.taxyear, count(*)::bigint AS n_rows,
               coalesce(sum({amount}), 0)::float8 AS dollars,
               coalesce(sum(greatest({amount}, 0)), 0)::float8 AS positive_dollars,
               count(*) FILTER (WHERE row_source = '{FROM_RECOVERY}')::bigint AS recovered_rows,
               count(*) FILTER (WHERE {OUR_ROWS})::bigint AS our_rows
        FROM tuples t JOIN public.privategrants_w_column_keys_view g USING ({keys})
        GROUP BY {', '.join('t.' + k for k in gm._KEYS)}, t.foreign_country, t.pool, t.gate, t.sample, g.taxyear
        ORDER BY {', '.join('t.' + k for k in gm._KEYS)}, g.taxyear
    """


def tuples_of(years: pd.DataFrame) -> pd.DataFrame:
    """One row per tuple from the tuple-year rows: the keys and flags, the
    rows and dollars of every year, and ``foreign`` for the tuples the
    rule after takes that the rule before did not."""
    flags = ["foreign_country", "pool", "gate", "sample"]
    metrics = ["n_rows", "dollars", "positive_dollars", "recovered_rows", "our_rows"]
    tuples = years.groupby(gm._KEYS, sort=True, as_index=False).agg(
        {**{flag: "first" for flag in flags}, **{metric: "sum" for metric in metrics}})
    for flag in flags:
        tuples[flag] = tuples[flag].astype(bool)
    tuples["foreign"] = gm.no_us_address(tuples) & ~before_rule(tuples)
    return tuples


def before_rule(grants: pd.DataFrame) -> pd.Series:
    """The name-only tier's eligibility up to 2026-10-30: no state and no zip."""
    return (grants["addressstate_key"].fillna("").str.strip() == "") & (grants["addresszip_key"].fillna("").str.strip() == "")


def eligibility(grants: pd.DataFrame, policy: str) -> pd.Series:
    """The name-only tier's eligibility under each rule compared."""
    if policy == "before":
        return before_rule(grants)
    if policy == "keys":
        return gm.no_us_address(grants.drop(columns=["foreign_country"], errors="ignore"))
    if policy == "country":
        return before_rule(grants) | grants.get("foreign_country", pd.Series(False, index=grants.index)).eq(True)
    if policy in ("after", "unguarded"):
        return gm.no_us_address(grants)
    raise ValueError(policy)


def extract(out: Path, one_in: int = 500, pool: Path = POOL) -> None:
    if one_in < 1:
        raise ValueError("one_in must be positive")
    out.mkdir(parents=True, exist_ok=True)
    if (out / "inputs.json").exists():
        raise FileExistsError("Inputs already frozen; use another directory for a new snapshot")
    names = pd.read_csv(pool, dtype=str).fillna("")
    started = time.monotonic()
    params = {"one_in": one_in, "placeholder": gm.PLACEHOLDER_REGEX, "us_keys": sorted(gm.US_STATE_KEYS),
              "pool": json.dumps(names[["name1_key", "name2_key"]].to_dict("records"))}
    queries = {
        "universe": gm.universe_sql(),
        "production": "SELECT " + ", ".join(gm._KEYS) + ", recipeint_ein_key AS production_ein, match_source AS "
                      "production_source, match_tier AS production_tier, match_name AS production_name "
                      "FROM public.pf_grant_matching_temp_table",
        "matched_rows": "SELECT recipeint_ein_key AS ein, count(*)::bigint AS rows_now "
                        "FROM public.privategrants_w_recipients GROUP BY 1",
        "grant_years": selection_sql(),
    }
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "one_in": one_in, "pool": str(pool.name),
                "pool_names": len(names), "pool_sha256": base._digest(pool), "policies": list(POLICIES),
                "placeholder_regex": gm.PLACEHOLDER_REGEX, "matching_shape": gm.MATCHING_INPUT_SHAPE_VERSION,
                "files": {}, "plans": {}}
    with get_session(config=datamart_config()) as session:
        c = session.connection()
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        for setting in ("work_mem = '64MB'", "max_parallel_workers_per_gather = 0",
                        "statement_timeout = '25min'", "lock_timeout = '5s'",
                        "temp_file_limit = '8GB'", "application_name = 'foreign_name_only_proof'"):
            c.execute(text("SET LOCAL " + setting))
        manifest["transaction_read_only"] = c.execute(text("SHOW transaction_read_only")).scalar_one()
        manifest["builds"] = [dict(r) for r in c.execute(text(
            "SELECT build_id, status, started_at, finished_at "
            "FROM datamart_meta.canonical_builds ORDER BY started_at DESC LIMIT 3")).mappings()]
        for name, query in queries.items():
            logger.info("Extracting %s", name)
            c = c.execution_options(stream_results=False)
            manifest["plans"][name] = [r[0] for r in c.execute(text("EXPLAIN " + query), params)]
            chunks = pd.read_sql_query(text(query), c.execution_options(stream_results=True),
                                       params=params, chunksize=100_000)
            frame = pd.concat(chunks, ignore_index=True)
            if name == "grant_years":
                frame[gm._KEYS] = frame[gm._KEYS].fillna("")
            path = out / f"{name}.parquet"
            frame.to_parquet(path, index=False)
            manifest["files"][path.name] = {"rows": len(frame), "sha256": base._digest(path)}
            logger.info("Frozen %s: %s rows", name, f"{len(frame):,}")
    tuples = tuples_of(pd.read_parquet(out / "grant_years.parquet"))
    manifest["tuples"] = {cohort: int(tuples[cohort].sum()) for cohort in COHORTS}
    manifest["tuples"]["all"] = len(tuples)
    manifest["seconds"] = round(time.monotonic() - started, 1)
    (out / "inputs.json").write_text(json.dumps(manifest, default=str, indent=2) + "\n")
    logger.info("Selected tuples: %s", manifest["tuples"])


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def resolve(features: pd.DataFrame, universe: pd.DataFrame, grants: pd.DataFrame, policy: str) -> pd.DataFrame:
    """The matcher's winners under ``ranking`` as it stands, and the
    name-only tier under the rule of ``policy``: the one-word guard holds
    for every rule but ``unguarded``."""
    return gm.all_matches(features, universe, grants, eligible=eligibility(grants, policy),
                          one_word_guard=policy != "unguarded")


def outcomes(grants: pd.DataFrame, universe: pd.DataFrame, matches: pd.DataFrame, policy: str) -> pd.DataFrame:
    """The grant tuples with the rule's match beside them: the EIN, the
    filer's name, arm, city, state and zip of the universe row it won
    with, and the tier."""
    result = base.comparison_rows(grants, universe, matches, policy)
    row = matches.set_index(gm.INDEX_COLS[1])[gm.INDEX_COLS[0]].reindex(result.index)
    for column, source in (("filer_city", "addresscity_key"), ("filer_state", "addressstate_key"),
                           ("filer_zip", "addresszip_key")):
        result[column] = row.map(universe[source]).fillna("")
    return result


def kind_of(ein: pd.Series) -> pd.Series:
    """A 98- EIN is the recipient's own foreign registration; any other is
    a domestic EIN, the recipient's US affiliate or a namesake."""
    return pd.Series(KIND_DOMESTIC, index=ein.index).where(~ein.str.startswith("98"), KIND_FOREIGN).where(ein.ne(""), "")


def changes(results: pd.DataFrame) -> pd.Series:
    before, after = results["baseline_ein"], results["ein"]
    change = pd.Series("unmatched_both", index=results.index)
    change.loc[before.ne("") & after.eq(before)] = "same_ein"
    change.loc[before.ne("") & after.ne("") & after.ne(before)] = "changed_ein"
    change.loc[before.ne("") & after.eq("")] = "lost"
    change.loc[before.eq("") & after.ne("")] = "gained"
    return change


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    """Tuples, rows and dollars by policy, cohort and what became of the
    match. The cohorts overlap and are never summed."""
    tables = []
    rows = results.assign(change=changes(results))
    for policy, frame in rows.groupby("policy", sort=False):
        for cohort in ("all_selected",) + COHORTS:
            selected = frame if cohort == "all_selected" else frame[frame[cohort]]
            table = selected.groupby("change").agg(
                tuples=("n_rows", "size"), rows=("n_rows", "sum"), dollars=("dollars", "sum"),
                positive_dollars=("positive_dollars", "sum"), recovered_rows=("recovered_rows", "sum"),
                our_rows=("our_rows", "sum")).reset_index()
            table.insert(0, "cohort", cohort)
            table.insert(0, "policy", policy)
            tables.append(table)
    return pd.concat(tables, ignore_index=True)


def gained_by_year(gained: pd.DataFrame, years: pd.DataFrame) -> pd.DataFrame:
    """The rows and dollars of the tuples gained under the rule after, by
    tax year: all of them, the pool's, and our extract's rows."""
    # The year rows repeat the tuple's flags: the tuple's own come from ``gained``.
    found = years.drop(columns=[flag for flag in ("foreign_country", "pool", "gate", "sample") if flag in years]).merge(
        gained[gm._KEYS + ["pool", "ein"]], on=gm._KEYS, how="inner")
    table = found.groupby("taxyear").agg(tuples=("ein", "size"), rows=("n_rows", "sum"), dollars=("dollars", "sum"),
                                        our_rows=("our_rows", "sum"), recovered_rows=("recovered_rows", "sum"))
    pool = found[found["pool"]].groupby("taxyear").agg(pool_rows=("n_rows", "sum"), pool_dollars=("dollars", "sum"))
    table = table.join(pool).fillna(0)
    table.loc["all"] = table.sum()
    table.loc["all", "tuples"] = len(gained)        # a tuple with rows in several years is one tuple
    return table.reset_index()


def gained_by_kind(gained: pd.DataFrame) -> pd.DataFrame:
    """The gains by the kind of EIN, whether the filer's universe row has
    a US state, and the arm of the universe."""
    kind = gained.assign(kind=kind_of(gained["ein"]),
                         filer=gained["filer_state"].map(lambda s: "a US state" if s in gm.US_STATE_KEYS
                                                         else ("no state" if s == "" else "a province")))
    return kind.groupby(["kind", "filer", "source"]).agg(
        tuples=("ein", "size"), recipients=("ein", "nunique"), rows=("n_rows", "sum"),
        dollars=("dollars", "sum"), our_rows=("our_rows", "sum")).reset_index()


def recipients(gained: pd.DataFrame, matched_rows: pd.DataFrame, top: int = 60) -> pd.DataFrame:
    """The recipients gained the most, by dollars: the EIN, its kind, the
    name it won under, its rows today and the rows the next run should
    show."""
    table = gained.assign(kind=kind_of(gained["ein"])).groupby(["ein", "kind", "matched_name", "source"]).agg(
        tuples=("n_rows", "size"), rows=("n_rows", "sum"), dollars=("dollars", "sum"),
        our_rows=("our_rows", "sum")).reset_index()
    table = table.merge(matched_rows, on="ein", how="left")
    table["rows_now"] = table["rows_now"].fillna(0).astype(int)
    table["rows_expected"] = table["rows_now"] + table["rows"]
    return table.sort_values("dollars", ascending=False).head(top)


def examples(gained: pd.DataFrame, top: int = 60) -> pd.DataFrame:
    """The largest gains to a domestic EIN, for review: the grant's name
    and address against the filer's."""
    domestic = gained[kind_of(gained["ein"]) == KIND_DOMESTIC]
    columns = ["name1_key", "name2_key", "address1_key", "addresscity_key", "addressstate_key", "addresszip_key",
               "foreign_country", "n_rows", "dollars", "ein", "matched_name", "source", "filer_city", "filer_state",
               "filer_zip", "tier"]
    return domestic.sort_values("dollars", ascending=False).head(top)[columns]


def signals(results: pd.DataFrame) -> pd.DataFrame:
    """What each signal adds: the tuples, rows and dollars gained under
    the state key alone, under the country column alone, and under both
    (the rule after); and for each single signal, what it alone finds."""
    gained = results[changes(results).eq("gained")]
    keyed = {policy: set(map(tuple, frame[gm._KEYS].to_numpy())) for policy, frame in gained.groupby("policy")}
    after = gained[gained["policy"] == "after"].set_index(gm._KEYS)

    def totals(keys: set) -> dict:
        found = after.loc[sorted(keys)] if keys else after.iloc[0:0]
        return {"tuples": len(found), "rows": int(found["n_rows"].sum()), "dollars": float(found["dollars"].sum())}

    other = {"keys": "country", "country": "keys"}
    rows = []
    for policy in ("keys", "country", "after"):
        found = keyed.get(policy, set())
        alone = found - keyed.get(other[policy], set()) if policy in other else set()
        rows.append({"signal": policy, **totals(found),
                     **{f"{k}_this_signal_alone": v for k, v in totals(alone).items()}})
    return pd.DataFrame(rows)


def sentinel(gained: pd.DataFrame, matched_rows: pd.DataFrame, ein: str = GLOBAL_FUND) -> dict:
    """The recipient sentinel's floor: 95% of the rows the next run should
    show for the EIN, which are its rows today and the rows gained."""
    now = matched_rows.set_index("ein")["rows_now"].get(ein, 0)
    found = gained[gained["ein"] == ein]
    expected = int(now) + int(found["n_rows"].sum())
    return {"ein": ein, "rows_now": int(now), "tuples_gained": len(found), "rows_gained": int(found["n_rows"].sum()),
            "dollars_gained": float(found["dollars"].sum()), "rows_expected": expected,
            "floor": math.floor(SENTINEL_SHARE * expected)}


def compare(out: Path, workers: int = 8) -> None:
    started = time.monotonic()
    manifest = json.loads((out / "inputs.json").read_text())
    for name, info in manifest["files"].items():
        if base._digest(out / name) != info["sha256"]:
            raise RuntimeError(f"Frozen input changed: {name}")
    universe = pd.read_parquet(out / "universe.parquet")
    production = pd.read_parquet(out / "production.parquet").fillna("")
    matched_rows = pd.read_parquet(out / "matched_rows.parquet")
    years = pd.read_parquet(out / "grant_years.parquet")
    grants = tuples_of(years)
    if production.duplicated(gm._KEYS).any():
        raise RuntimeError("Production has multiple winners for a recipient tuple")
    grants = grants.merge(production, on=gm._KEYS, how="left", validate="one_to_one")
    grants["production_ein"] = grants["production_ein"].fillna("")
    logger.info("Preparing %s universe identities and %s grant tuples", f"{len(universe):,}", f"{len(grants):,}")
    gm.prepare(universe)
    gm.prepare(grants)
    # Pair order is the matcher's deterministic tie-break.
    features = base.score(universe, grants, out, workers).sort_values(gm.INDEX_COLS, kind="mergesort")
    results = []
    for policy in POLICIES:
        logger.info("Resolving the rule %s", policy)
        results.append(outcomes(grants, universe, resolve(features, universe, grants, policy), policy))
    baseline = results[0]["ein"]
    mismatch = baseline.ne(grants["production_ein"])
    parity = {"tuples": len(grants), "mismatches": int(mismatch.sum()),
              "affected_rows": int(grants.loc[mismatch, "n_rows"].sum())}
    (out / "parity.json").write_text(json.dumps(parity, indent=2) + "\n")
    results[0].loc[mismatch, gm._KEYS + ["production_ein", "ein", "matched_name", "source", "n_rows"]].to_csv(
        out / "parity_mismatches.csv", index=False)
    for frame in results:
        frame["baseline_ein"] = baseline
    combined = pd.concat(results, ignore_index=True)
    moved = combined[changes(combined).isin(("lost", "changed_ein"))]
    if not moved.empty:
        moved.to_csv(out / "moved.csv", index=False)
        raise RuntimeError(f"{len(moved)} matches lost or moved: the tier only adds, so the harness is wrong")
    write_reports(combined, years, matched_rows, out)
    (out / "comparison.json").write_text(json.dumps({
        "seconds": round(time.monotonic() - started, 1), "policies": list(POLICIES), "parity": parity,
        "candidate_scores": len(features), "sentinel": sentinel(gained_after(combined), matched_rows)},
        indent=2) + "\n")
    print(json.dumps(parity))
    if parity["mismatches"]:
        raise RuntimeError("The rule before does not reproduce production; diagnose before reading the gains")


def gained_after(combined: pd.DataFrame, policy: str = "after") -> pd.DataFrame:
    after = combined[combined["policy"] == policy]
    return after[changes(after).eq("gained")]


def refused(combined: pd.DataFrame) -> pd.DataFrame:
    """The tuples the one-word guard refuses: gained without it, not with
    it, with the filer they would have gone to."""
    kept = set(map(tuple, gained_after(combined)[gm._KEYS].to_numpy()))
    unguarded = gained_after(combined, "unguarded")
    lost = unguarded[[key not in kept for key in map(tuple, unguarded[gm._KEYS].to_numpy())]]
    columns = ["name1_key", "name2_key", "address1_key", "addresscity_key", "addressstate_key", "addresszip_key",
               "foreign_country", "n_rows", "dollars", "our_rows", "ein", "matched_name", "source", "filer_city",
               "filer_state", "filer_zip"]
    return lost.sort_values("dollars", ascending=False)[columns]


def write_reports(combined: pd.DataFrame, years: pd.DataFrame, matched_rows: pd.DataFrame, out: Path) -> None:
    combined.to_parquet(out / "results.parquet", index=False)
    gained = gained_after(combined)
    tables = {
        "summary": summarize(combined),
        "signals": signals(combined),
        "by_year": gained_by_year(gained, years),
        "by_kind": gained_by_kind(gained),
        "recipients": recipients(gained, matched_rows),
        "examples": examples(gained),
        "refused": refused(combined),
        "gate": combined[combined["gate"]].groupby(["policy", "ein", "matched_name"]).agg(
            tuples=("n_rows", "size"), rows=("n_rows", "sum"), dollars=("dollars", "sum")).reset_index(),
    }
    for name, table in tables.items():
        table.to_csv(out / f"{name}.csv", index=False)
        print(f"\n== {name} ==")
        with pd.option_context("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 48,
                               "display.float_format", "{:,.0f}".format):
            print(table.to_string(index=False))
    print("\n== sentinel ==")
    print(json.dumps(sentinel(gained, matched_rows), indent=2))


# ---------------------------------------------------------------------------
# corrections
# ---------------------------------------------------------------------------

UNIVERSE_KEYS = ["filerein_key"] + gm._KEYS
SOURCE_RANK = {"basic_fields": 1, "basic_fields_pf": 2, "correction": 3, "eo_bmf": 4}


def keyed_corrections(csv: pd.DataFrame) -> pd.DataFrame:
    """The registry's rows keyed as ``corrections_unique_names_view`` keys
    them (lower case; the zip's first five characters), one row per key
    tuple, with ``line``, the CSV line of its first row."""
    lower = {column: csv[column].fillna("").str.lower() for column in csv.columns}
    keyed = pd.DataFrame({
        "filerein_key": lower["recipient_ein"], "name1_key": lower["name1"], "name2_key": lower["name2"],
        "address1_key": lower["address1"], "address2_key": lower["address2"], "addresscity_key": lower["city"],
        "addressstate_key": lower["state"], "addresszip_key": csv["zip"].fillna("").str[:5].str.lower(),
        "line": range(2, len(csv) + 2), "source": "correction"})
    return keyed.drop_duplicates(UNIVERSE_KEYS).reset_index(drop=True)


def with_corrections(universe: pd.DataFrame, rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """The universe with the correction rows it lacks, as the read's
    DISTINCT ON would hold them. A row whose key tuple a filing has
    collapses into the filing; one a BMF line has takes the line's place
    and index; any other comes after the frozen rows, with a new index.
    Returns the universe and, on its index, the CSV line of each row a
    correction supplies (missing elsewhere)."""
    frozen = universe.copy()
    keys = list(zip(*[frozen[k].fillna("") for k in UNIVERSE_KEYS]))
    first = {}
    for label, key in zip(frozen.index, keys):
        first.setdefault(key, label)
    line = pd.Series(pd.NA, index=frozen.index, dtype="Int64")
    added = []
    for row in rows.itertuples(index=False):
        key = tuple(getattr(row, k) for k in UNIVERSE_KEYS)
        if key in first:
            label = first[key]
            source = frozen.at[label, "source"]
            if SOURCE_RANK[source] > SOURCE_RANK["correction"]:
                frozen.at[label, "source"] = "correction"
            if SOURCE_RANK[source] >= SOURCE_RANK["correction"]:
                line[label] = row.line
            continue
        added.append({**{k: getattr(row, k) for k in UNIVERSE_KEYS}, "source": "correction", "_line": row.line})
    if added:
        new = pd.DataFrame(added)
        start = int(frozen.index.max()) + 1
        new.index = range(start, start + len(new))
        line = pd.concat([line, new.pop("_line").astype("Int64")])
        frozen = pd.concat([frozen, new])
    return frozen, line


def _blocks(df: pd.DataFrame) -> pd.DataFrame:
    """The two blocking keys of each row, without ``prepare``'s slow
    address string: the clean zip where the row has an address, and the
    cleaned name."""
    parts = df[["address1_key", "address2_key", "addresscity_key", "addressstate_key"]].fillna("").astype(str)
    has_address = (parts.agg("".join, axis=1).str.replace(r"[^\w]", "", regex=True) != "")
    zips = df["addresszip_key"].fillna("").astype(str).map(gm.clean_zip)
    names = [gm.clean_name(gm.join_name(a, b)) for a, b in zip(df["name1_key"].fillna(""), df["name2_key"].fillna(""))]
    return pd.DataFrame({"block_zip": zips.where(has_address), "full_name": names}, index=df.index)


def reached(grants: pd.DataFrame, rows: pd.DataFrame) -> pd.Series:
    """The grant tuples a correction row can change: one it blocks with
    (the same clean zip, both with an address, or the same cleaned name)."""
    g, r = _blocks(grants), _blocks(rows)
    same_zip = g["block_zip"].notna() & g["block_zip"].isin(set(r["block_zip"].dropna()))
    return same_zip | g["full_name"].isin(set(r["full_name"]))


def corrections(out: Path, csv: Path | None = None) -> dict:
    manifest = json.loads((out / "inputs.json").read_text())
    for name in ("universe.parquet", "grant_years.parquet"):
        if base._digest(out / name) != manifest["files"][name]["sha256"]:
            raise RuntimeError(f"Frozen input changed: {name}")
    universe = pd.read_parquet(out / "universe.parquet")
    grants = tuples_of(pd.read_parquet(out / "grant_years.parquet"))
    after = pd.read_parquet(out / "results.parquet", columns=gm._KEYS + ["policy", "ein"])
    after = after[after["policy"] == "after"].drop(columns="policy").rename(columns={"ein": "compare_ein"})
    return measure_corrections(universe, grants, after, keyed_corrections(gm._read_corrections_csv(csv)), out)


def measure_corrections(universe: pd.DataFrame, grants: pd.DataFrame, after: pd.DataFrame, rows: pd.DataFrame,
                        out: Path | None = None) -> dict:
    """The tuples the correction rows move, on frozen frames: ``after`` holds
    each selected tuple's match under the rule as it stands
    (``compare_ein``). Writes the two reports under ``out`` when given."""
    new_universe, line = with_corrections(universe, rows)
    fresh = new_universe.loc[line.dropna().index]
    hit = grants[reached(grants, fresh)].copy()
    logger.info("Selected tuples the registry's rows can change: %s", len(hit))
    hit_blocks = _blocks(hit)
    zips, names = set(hit_blocks["block_zip"].dropna()), set(hit_blocks["full_name"])
    winners = {}
    for label, frame in (("without", universe), ("with", new_universe)):
        b = _blocks(frame)
        part = frame[b["block_zip"].isin(zips) | b["full_name"].isin(names)].copy()
        gm.prepare(part)
        if label == "without":
            gm.prepare(hit)
        matches = gm.all_matches(gm.score_slice(part, hit), part, hit)
        winners[label] = matches.set_index(gm.INDEX_COLS[1])[gm.INDEX_COLS[0]].reindex(hit.index)
        hit[f"ein_{label}"] = winners[label].map(part["filerein_key"]).fillna("")
    hit["line"] = winners["with"].map(line).astype("Int64")
    hit = hit.merge(after, on=gm._KEYS, how="left")
    hit["compare_ein"] = hit["compare_ein"].fillna("")
    mismatch = hit["ein_without"].ne(hit["compare_ein"])
    columns = gm._KEYS + ["n_rows", "dollars", "our_rows", "compare_ein", "ein_without", "ein_with", "line"]
    if mismatch.any():
        if out is not None:
            hit.loc[mismatch, columns].to_csv(out / "corrections_parity_mismatches.csv", index=False)
        raise RuntimeError(f"{int(mismatch.sum())} tuples: the slice without the new rows does not reproduce compare")
    moved = hit[hit["ein_with"].ne(hit["ein_without"])].sort_values("dollars", ascending=False)
    lines = sorted(set(line.dropna().astype(int)))
    taken = hit[hit["line"].notna()].groupby("line").agg(
        ein=("ein_with", "first"), tuples=("n_rows", "size"), rows=("n_rows", "sum"), dollars=("dollars", "sum"))
    taken = taken.reindex(lines).fillna({"tuples": 0, "rows": 0, "dollars": 0.0})
    if out is not None:
        moved[columns].to_csv(out / "corrections.csv", index=False)
        taken.to_csv(out / "corrections_rows.csv")
    summary = {"tuples_reached": len(hit), "parity_mismatches": 0, "tuples_moved": len(moved),
               "rows_moved": int(moved["n_rows"].sum()), "dollars_moved": float(moved["dollars"].sum())}
    print(json.dumps(summary))
    with pd.option_context("display.width", 250, "display.max_colwidth", 40, "display.max_rows", 200):
        print(moved[columns].to_string(index=False))
        print(taken.to_string())
    return {"summary": summary, "moved": moved[columns], "taken": taken}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("extract", "compare", "corrections"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--one-in", type=int, default=500)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--csv", type=Path, default=None, help="corrections: the registry to measure (default: the repo's)")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.command == "extract":
        extract(args.out, args.one_in)
    elif args.command == "corrections":
        corrections(args.out, args.csv)
    else:
        compare(args.out, args.workers)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
