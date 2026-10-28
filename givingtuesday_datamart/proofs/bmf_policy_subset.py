"""Compare BMF identity policies on fixed inputs, without database writes.

``extract --out logs/bmf-proof`` reads the current universe, a deterministic
recipient-tuple sample plus named diagnostic cohorts, BMF name provenance,
and production winners in one read-only repeatable-read transaction. It
records query plans and freezes the inputs as local Parquet files.

``compare --out logs/bmf-proof --workers 4`` uses the real matcher. It scores
the complete current competitive universe once, then resolves those scores
after removing identities for each policy. Removing rows preserves their
original indices and relative order; no production checkpoints are reused.
The name-only tier sees the whole eligible universe under each policy.

Rows/dollars describe the selected tuples, never a population estimate.
Changed EINs are changes, not automatically corrections. The raw inputs
and scores stay out of git; summary CSVs can be archived with the findings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.matching_regression_checks import AMT, PLACEHOLDER_REGEX

POLICIES = ("current", "placeholders_only", "no_sort", "conservative", "no_bmf")
# Textual cohorts, not a promise to include every fuzzy spelling of an EIN.
COHORT_PATTERN = (
    r"hopkin|jhpiego|peabody|\mduke\M|heritage|scout|fraternal order|"
    r"\mpta\M|parent.*teacher|doctors.*without|river fund"
)
WATCH_EINS = ("520595110", "520368135", "223454231", "520591627", "237424444",
              "521539714", "526055764", "560532129", "237327730")
_STATE: dict = {}


def selection_sql() -> str:
    """All years of a selected seven-key tuple, including previously unmatched.

    Selection depends only on the tuple's keys. The general cohort is a
    stable 1/N hash sample of tuples, not grant rows or only current wins.
    Eligibility is 2015+, but the production write joins every year of an
    eligible tuple. HAVING keeps that distinction out of the row weights.
    """
    keys = ", ".join(gm._KEYS)
    name = "concat_ws(' ', name1_key, name2_key)"
    sample = "mod(abs(hashtextextended(jsonb_build_array(" + keys + ")::text, 0)), :one_in) = 0"
    placeholder = f"{name} ~* :placeholder"
    targeted = f"{name} ~* :cohort"
    amount = AMT.format(col="sigocpyamoun")
    return f"""
        SELECT {keys}, count(*)::bigint AS n_rows,
               coalesce(sum({amount}), 0)::float8 AS dollars,
               coalesce(sum(greatest({amount}, 0)), 0)::float8 AS positive_dollars,
               ({sample}) AS general_sample, ({targeted}) AS targeted,
               ({placeholder}) AS placeholder,
               count(*) FILTER (WHERE row_source = 'placeholder_recovery') AS recovered_rows
        FROM public.privategrants_w_column_keys_view
        WHERE NOT (trim(name1_key) = '' AND trim(name2_key) = '')
          AND (({sample}) OR ({targeted}) OR ({placeholder}))
        GROUP BY {keys}
        HAVING bool_or(taxyear::int >= 2015)
        ORDER BY {keys}
    """


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def recount(out: Path) -> None:
    """Correct row weights of older snapshots without altering scoring inputs.

    Matching selects identities from 2015+ but writes ALL grant rows sharing
    their keys. A separate read-only snapshot records all-year row weights.
    We require exactly the frozen tuples in exactly the same order, and bind
    the supplement to the immutable input manifest. No score cache changes.
    """
    original = pd.read_parquet(out / "grants.parquet")
    with get_session(config=datamart_config()) as session:
        c = session.connection()
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        for setting in ("work_mem = '64MB'", "max_parallel_workers_per_gather = 0",
                        "statement_timeout = '10min'", "lock_timeout = '5s'",
                        "temp_file_limit = '4GB'", "application_name = 'bmf_policy_proof'"):
            c.execute(text("SET LOCAL " + setting))
        manifest = json.loads((out / "inputs.json").read_text())
        params = {"one_in": manifest["one_in"], "placeholder": manifest["placeholder_regex"],
                  "cohort": manifest["cohort_pattern"]}
        plan = [r[0] for r in c.execute(text("EXPLAIN " + selection_sql()), params)]
        updated = pd.read_sql_query(text(selection_sql()), c, params=params)
    if not updated[gm._KEYS].equals(original[gm._KEYS]):
        raise RuntimeError("Recipient tuples changed; take a fresh snapshot instead of reweighting")
    path = out / "all_year_weights.parquet"
    updated.to_parquet(path, index=False)
    (out / "all_year_weights.json").write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(), "inputs": _digest(out / "inputs.json"),
        "sha256": _digest(path), "plan": plan, "tuples": len(updated),
        "old_rows": int(original.n_rows.sum()), "all_year_rows": int(updated.n_rows.sum())}, indent=2) + "\n")
    if (out / "results.parquet").exists():
        combined = reweight_results(pd.read_parquet(out / "results.parquet"), updated)
        write_reports(combined, out)
    logger.info("All-year row weights: %s -> %s grant rows", int(original.n_rows.sum()), int(updated.n_rows.sum()))


def reweight_results(results: pd.DataFrame, weights: pd.DataFrame) -> pd.DataFrame:
    """Change row metrics, never identities, winners, policy, or tuple order."""
    metrics = ["n_rows", "dollars", "positive_dollars", "recovered_rows"]
    merged = results.drop(columns=metrics).merge(weights[gm._KEYS + metrics], on=gm._KEYS,
                                                how="left", sort=False, validate="many_to_one")
    if merged[metrics].isna().any().any():
        raise RuntimeError("Some result tuples have no row weights")
    return merged


def extract(out: Path, one_in: int = 500) -> None:
    if one_in < 1:
        raise ValueError("one_in must be positive")
    out.mkdir(parents=True, exist_ok=True)
    if (out / "inputs.json").exists():
        raise FileExistsError("Inputs already frozen; use another directory for a new snapshot")
    started = time.monotonic()
    params = {"one_in": one_in, "placeholder": PLACEHOLDER_REGEX, "cohort": COHORT_PATTERN}
    queries = {
        "universe": gm.universe_sql(),
        "bmf": "SELECT lower(ein) AS filerein_key, lower(name) AS name, "
               "lower(sort_name) AS sort_name, affiliation FROM public.eo_bmf",
        "grants": selection_sql(),
        "production": "SELECT " + ", ".join(gm._KEYS) +
                      ", recipeint_ein_key AS production_ein FROM public.pf_grant_matching_temp_table",
    }
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "one_in": one_in,
                "cohort_pattern": COHORT_PATTERN, "placeholder_regex": PLACEHOLDER_REGEX,
                "matching_shape": gm.MATCHING_INPUT_SHAPE_VERSION, "files": {}, "plans": {}}
    with get_session(config=datamart_config()) as session:
        c = session.connection()
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        for setting in ("work_mem = '64MB'", "max_parallel_workers_per_gather = 0",
                        "statement_timeout = '10min'", "lock_timeout = '5s'",
                        "temp_file_limit = '4GB'", "application_name = 'bmf_policy_proof'"):
            c.execute(text("SET LOCAL " + setting))
        manifest["transaction_read_only"] = c.execute(text("SHOW transaction_read_only")).scalar_one()
        manifest["builds"] = [dict(r) for r in c.execute(text(
            "SELECT build_id, status, started_at, finished_at "
            "FROM datamart_meta.canonical_builds ORDER BY started_at DESC LIMIT 3")).mappings()]
        for name, query in queries.items():
            logger.info("Extracting %s", name)
            # Connection.execution_options mutates the SQLAlchemy connection;
            # PostgreSQL cannot DECLARE a server-side cursor over EXPLAIN.
            c = c.execution_options(stream_results=False)
            plan = [r[0] for r in c.execute(text("EXPLAIN " + query), params)]
            manifest["plans"][name] = plan
            # Stream the result: don't duplicate millions of DBAPI rows in RAM.
            chunks = pd.read_sql_query(text(query), c.execution_options(stream_results=True),
                                       params=params, chunksize=100_000)
            frame = pd.concat(chunks, ignore_index=True)
            path = out / f"{name}.parquet"
            frame.to_parquet(path, index=False)
            manifest["files"][path.name] = {"rows": len(frame), "sha256": _digest(path)}
            logger.info("Frozen %s: %s rows", name, f"{len(frame):,}")
    manifest["seconds"] = round(time.monotonic() - started, 1)
    (out / "inputs.json").write_text(json.dumps(manifest, default=str, indent=2) + "\n")


def identity_flags(universe: pd.DataFrame, bmf: pd.DataFrame) -> pd.DataFrame:
    """Keep NAME when it is also SORT_NAME; suppress only BMF-sourced rows.

    The conservative rule additionally suppresses a group subordinate's
    NAME if its cleaned name equals a filed name of a DIFFERENT EIN.
    A shared name within the same EIN is harmless. This is a candidate
    policy for evaluation, not a claim that all such collisions are wrong.
    """
    meta = bmf.fillna("").drop_duplicates().set_index("filerein_key")
    if not meta.index.is_unique:
        raise ValueError("BMF has conflicting name provenance for the same EIN")
    is_bmf = universe["source"].eq("eo_bmf")
    recorded_name = universe["filerein_key"].map(meta["name"])
    if (is_bmf & recorded_name.isna()).any():
        raise ValueError("A BMF-sourced universe identity has no BMF provenance")
    primary = universe["name1_key"].eq(recorded_name)
    subordinate = universe["filerein_key"].map(meta["affiliation"]).eq("9")
    filed = universe[universe["source"].isin(("basic_fields", "basic_fields_pf"))]
    owners = filed[["full_name", "filerein_key"]].drop_duplicates()
    counts = owners.groupby("full_name")["filerein_key"].nunique()
    first = owners.drop_duplicates("full_name").set_index("full_name")["filerein_key"]
    collision = (universe["full_name"].map(counts).fillna(0).gt(1)
                 | (universe["full_name"].map(first).notna()
                    & universe["full_name"].map(first).ne(universe["filerein_key"])))
    return pd.DataFrame({"bmf": is_bmf, "sort_only": is_bmf & ~primary,
                         "subordinate_collision": is_bmf & primary & subordinate & collision},
                        index=universe.index)


def eligible(flags: pd.DataFrame, policy: str) -> pd.Series:
    if policy not in POLICIES:
        raise ValueError(policy)
    keep = pd.Series(True, index=flags.index)
    if policy in ("no_sort", "conservative"):
        keep &= ~flags["sort_only"]
    if policy == "conservative":
        keep &= ~flags["subordinate_collision"]
    if policy == "no_bmf":
        keep &= ~flags["bmf"]
    return keep


def resolve_policy(features: pd.DataFrame, universe: pd.DataFrame, grants: pd.DataFrame,
                   flags: pd.DataFrame, policy: str) -> pd.DataFrame:
    u = universe[eligible(flags, policy)]
    g = grants if policy == "current" else grants[~grants["placeholder"]]
    f = features[features[gm.INDEX_COLS[0]].isin(u.index)
                 & features[gm.INDEX_COLS[1]].isin(g.index)]
    # Pair order is the production matcher's deterministic tie-break.
    f = f.sort_values(gm.INDEX_COLS, kind="mergesort")
    return gm.all_matches(f, u, g)


def _score_chunk(number: int) -> str:
    path = _STATE["directory"] / f"{number:06d}.parquet"
    if not path.exists():
        start = number * _STATE["chunk_size"]
        f = gm.score_pairs(_STATE["pairs"][start:start + _STATE["chunk_size"]],
                           _STATE["universe"], _STATE["grants"])
        tmp = path.with_suffix(".tmp")
        f.to_parquet(tmp, index=False)
        tmp.replace(path)
    return str(path)


def score(universe: pd.DataFrame, grants: pd.DataFrame, out: Path, workers: int) -> pd.DataFrame:
    directory = out / "scores"
    directory.mkdir(exist_ok=True)
    pairs = gm.candidate_pairs(universe, grants)
    logger.info("Candidate pairs: %s", f"{len(pairs):,}")
    chunk_size = 50_000
    total = (len(pairs) + chunk_size - 1) // chunk_size
    # Refuse a stale cache after either the frozen inputs or scoring code changes.
    signature = {"inputs": _digest(out / "inputs.json"), "chunk_size": chunk_size,
                 "pairs": len(pairs), "matcher": _digest(Path(gm.__file__))}
    metadata = directory / "signature.json"
    if metadata.exists() and json.loads(metadata.read_text()) != signature:
        raise RuntimeError("Scoring cache belongs to different inputs/code; use a new output directory")
    metadata.write_text(json.dumps(signature, indent=2) + "\n")
    _STATE.update(directory=directory, pairs=pairs, chunk_size=chunk_size, universe=universe, grants=grants)
    try:
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers,
                                     mp_context=multiprocessing.get_context("fork")) as pool:
                paths = list(pool.map(_score_chunk, range(total), chunksize=4))
        else:
            paths = [_score_chunk(i) for i in range(total)]
    finally:
        _STATE.clear()
    if not paths:
        return pd.DataFrame(columns=gm.INDEX_COLS + gm.SCORE_COLS)
    return pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)


def comparison_rows(grants: pd.DataFrame, universe: pd.DataFrame,
                    matches: pd.DataFrame, policy: str) -> pd.DataFrame:
    result = grants.copy()
    found = matches.set_index(gm.INDEX_COLS[1])
    row = found[gm.INDEX_COLS[0]].reindex(result.index)
    for column, source in (("ein", "filerein_key"), ("matched_name", "full_name"), ("source", "source")):
        result[column] = row.map(universe[source]).fillna("")
    result["tier"] = found["match_tier"].reindex(result.index).fillna("")
    result["policy"] = policy
    return result


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    tables = []
    for policy, rows in results.groupby("policy", sort=False):
        rows = rows.copy()
        before, after = rows["baseline_ein"], rows["ein"]
        rows["change"] = "unmatched_both"
        rows.loc[before.ne("") & after.eq(before), "change"] = "same_ein"
        rows.loc[before.ne("") & after.ne("") & after.ne(before), "change"] = "changed_ein"
        rows.loc[before.ne("") & after.eq(""), "change"] = "lost"
        rows.loc[before.eq("") & after.ne(""), "change"] = "gained"
        for cohort in ("all_selected", "general_sample", "general_nonplaceholder", "targeted", "placeholder"):
            if cohort == "all_selected":
                selected = rows
            elif cohort == "general_nonplaceholder":
                selected = rows[rows["general_sample"] & ~rows["placeholder"]]
            else:
                selected = rows[rows[cohort]]
            table = selected.groupby("change").agg(tuples=("n_rows", "size"), rows=("n_rows", "sum"),
                                                  dollars=("dollars", "sum"),
                                                  positive_dollars=("positive_dollars", "sum")).reset_index()
            table.insert(0, "cohort", cohort)
            table.insert(0, "policy", policy)
            tables.append(table)
    return pd.concat(tables, ignore_index=True)


def compare(out: Path, workers: int = 4) -> None:
    started = time.monotonic()
    manifest = json.loads((out / "inputs.json").read_text())
    for name, info in manifest["files"].items():
        if _digest(out / name) != info["sha256"]:
            raise RuntimeError(f"Frozen input changed: {name}")
    universe, grants, bmf, production = [pd.read_parquet(out / f"{n}.parquet")
                                        for n in ("universe", "grants", "bmf", "production")]
    if (out / "all_year_weights.json").exists():
        weights = json.loads((out / "all_year_weights.json").read_text())
        if weights["inputs"] != _digest(out / "inputs.json") or weights["sha256"] != _digest(out / "all_year_weights.parquet"):
            raise RuntimeError("Row weights do not belong to these frozen inputs")
        updated = pd.read_parquet(out / "all_year_weights.parquet")
        if not updated[gm._KEYS].equals(grants[gm._KEYS]):
            raise RuntimeError("Row weights changed recipient tuples")
        grants = updated
    production = production.fillna("")
    if production.duplicated(gm._KEYS).any():
        raise RuntimeError("Production has multiple winners for a recipient tuple")
    grants = grants.merge(production, on=gm._KEYS, how="left", validate="one_to_one")
    grants["production_ein"] = grants["production_ein"].fillna("")
    logger.info("Preparing %s universe identities and %s grant tuples", len(universe), len(grants))
    gm.prepare(universe)
    gm.prepare(grants)
    flags = identity_flags(universe, bmf)
    flags.sum().to_json(out / "identity_counts.json", indent=2)
    features = score(universe, grants, out, workers)
    results = []
    for policy in POLICIES:
        logger.info("Resolving policy %s", policy)
        matches = resolve_policy(features, universe, grants, flags, policy)
        results.append(comparison_rows(grants, universe, matches, policy))
    baseline = results[0]["ein"]
    mismatch = baseline.ne(grants["production_ein"])
    parity = {"tuples": len(grants), "mismatches": int(mismatch.sum()),
              "affected_rows": int(grants.loc[mismatch, "n_rows"].sum())}
    (out / "parity.json").write_text(json.dumps(parity, indent=2) + "\n")
    results[0].loc[mismatch, gm._KEYS + ["production_ein", "ein", "matched_name", "source", "n_rows"]].to_csv(
        out / "parity_mismatches.csv", index=False)
    for frame in results:
        frame["baseline_ein"] = baseline
        frame["baseline_name"] = results[0]["matched_name"]
        frame["baseline_source"] = results[0]["source"]
        frame["baseline_tier"] = results[0]["tier"]
    combined = pd.concat(results, ignore_index=True)
    write_reports(combined, out)
    (out / "comparison.json").write_text(json.dumps({"seconds": round(time.monotonic()-started, 1),
        "policies": list(POLICIES), "parity": parity, "candidate_scores": len(features)}, indent=2) + "\n")
    print(json.dumps(parity))
    print(summarize(combined).to_string(index=False))
    if parity["mismatches"]:
        raise RuntimeError("Baseline does not reproduce production; diagnose before interpreting policy differences")


def write_reports(combined: pd.DataFrame, out: Path) -> None:
    combined.to_parquet(out / "results.parquet", index=False)
    summarize(combined).to_csv(out / "summary.csv", index=False)
    columns = gm._KEYS + ["n_rows", "dollars", "positive_dollars", "general_sample", "targeted", "placeholder",
                           "production_ein", "baseline_ein", "baseline_name", "baseline_source", "baseline_tier",
                           "policy", "ein", "matched_name", "source", "tier"]
    changed = combined[combined["ein"].ne(combined["baseline_ein"])]
    changed[columns].sort_values("positive_dollars", ascending=False).to_csv(out / "changes.csv", index=False)
    combined[combined["ein"].isin(WATCH_EINS)].groupby(["policy", "ein"]).agg(
        tuples=("ein", "size"), rows=("n_rows", "sum"), dollars=("dollars", "sum")).to_csv(out / "watched_eins.csv")
    # Keep each target family separate: overlapping cohorts are never summed.
    family = combined["full_name"].str.extract(r"(hopkin|duke|heritage|scout)", expand=False)
    combined.assign(family=family).dropna(subset=["family"]).groupby(
        ["policy", "family", "ein", "matched_name", "source"]).agg(
            tuples=("ein", "size"), rows=("n_rows", "sum"), dollars=("dollars", "sum")).to_csv(out / "families.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("extract", "compare", "recount"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--one-in", type=int, default=500)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.command == "extract":
        extract(args.out, args.one_in)
    elif args.command == "recount":
        recount(args.out)
    else:
        compare(args.out, args.workers)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
