"""Validate the matcher's winner order on frozen inputs, with the shipped code.

Every order comes from ``grant_matching.all_matches`` itself. "weighted",
the order of every run up to 2026-10-27, is the control and must reproduce
production's winner for every grant tuple that names a recipient. The
orders under test are "geographic" (geography before an exact name) and
"exact_first" (an exact name before geography, except against a state that
contradicts the grant's); ``disagreements.csv`` lists every tuple they send
to different EINs. Scores come from the shipped scorer (``candidate_pairs``,
``score_pairs``), cached under a key of the scoring code alone, so a change
to an order reuses them and a change to the scorer cannot.

``known`` reruns the inspected pools of 2026-10-28 (the ranking proof's
recipients and funder lists, and the fresh sample the geography prototype
was validated on): labeled relationship coverage, movements, location
flags. ``freeze`` draws a new sample from production, read-only, with a new
hash seed and none of the recipients of those pools, and records the
digest of the orders' code. ``fresh`` refuses to run when that code has
changed since the freeze. The criteria, fixed in the run plan before
either was run (docs/data-refresh-concordance-2026-10.md, decision 1):

- the control reproduces production's winners;
- labeled coverage at least 469 of 647 relationships, none lost;
- no strong location-loss flag where the grant's city and ZIP agree with
  the old winner and the new one is in another city or state; every other
  flag listed and reviewed.

A strong location-loss flag is the audit's of 2026-10-28
(``geographic_rank_audit``), over every winner that changed, not only
same-name ones: the old winning pair's address score at least 0.90, the
new one's at least 0.20 lower, and an agreement on city, state or ZIP lost.
A changed EIN is a movement, not a measured correction.

    python -m givingtuesday_datamart.proofs.ranking_validation known \\
        --pool ~/logs/refresh/rank-policy-proof-20261028 \\
        --extra ~/logs/refresh/geo-policy-fresh-20261028/grants.parquet \\
        --out ~/logs/refresh-concordance/ranking-validation-known --workers 16
    python -m givingtuesday_datamart.proofs.ranking_validation freeze \\
        --pool ~/logs/refresh/rank-policy-proof-20261028 \\
        --extra ~/logs/refresh/geo-policy-fresh-20261028/grants.parquet \\
        --out ~/logs/refresh-concordance/ranking-validation-fresh --seed 7
    python -m givingtuesday_datamart.proofs.ranking_validation fresh \\
        --pool ~/logs/refresh/rank-policy-proof-20261028 \\
        --out ~/logs/refresh-concordance/ranking-validation-fresh --workers 16
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import logging
import multiprocessing
import subprocess
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart._internal import address_cleaning
from givingtuesday_datamart._internal.logger import logger

CONTROL = "weighted"
# The default first: every other order is also compared with it tuple by tuple.
CANDIDATES = ("geographic", "exact_first", "named_first")
LABELED_FLOOR = 469
STRONG_ADDRESS, STRONG_DROP = 0.90, 0.20
CHUNK = 50_000
FAMILIES = ("johns hopkins university", "duke university", "progressive animal welfare society",
            "notre dame high school", "bates college", "weta public television and radio")
_STATE: dict = {}


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _code_digest(objects, constants) -> str:
    parts = [inspect.getsource(o) for o in objects] + [repr(c) for c in constants]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def scoring_digest() -> str:
    """The code a pair's scores depend on: cleaning, blocking, scoring and
    the chunk-stage rules."""
    return _code_digest(
        [gm.prepare, gm.add_compare_addr, gm.clean_zip, gm.normalize_org_name, gm.clean_name, gm.join_name,
         gm.candidate_pairs, gm._block_positions, gm._blocked_pairs, gm.pair_scores, gm.score_pairs,
         gm.filter_match_rules, address_cleaning],
        [gm.CHUNK_FILTER_RULES, gm._LEGAL_ENDINGS])


def ranking_digest() -> str:
    """The code that picks the winner from scored pairs, every order. Not
    which order is the default: choosing it after a fresh run keeps the run."""
    return _code_digest(
        [gm.resolve_matches, gm._one_filer_refused, gm.location_agreement, gm.geographic_support, gm._codes,
         gm._city_tails, gm._line_tails, gm._place, gm._state, gm._zip, gm._positions, gm._names_held,
         gm.placeholder_names, gm.name_only_matches, gm.all_matches, gm.guard_government_names, gm._by_address],
        [gm.RANKINGS, gm._ORDERS, gm.LOCAL_ADDRESS_MIN, gm.ALIAS_SOURCES, gm.FINAL_FILTER_RULES,
         gm.PLACEHOLDER_REGEX, gm._CITY_TAIL_WORDS, gm.ONE_FILER_IN_STATE, gm._STREET])


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(gm.__file__).parent, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# --- Inputs ---------------------------------------------------------------------

def check_pool(pool: Path) -> dict:
    """The pool's frozen files are the ones its manifest names."""
    manifest = json.loads((pool / "inputs.json").read_text())
    for name, info in manifest["files"].items():
        if _digest(pool / name) != info["sha256"]:
            raise RuntimeError(f"Frozen input changed: {pool / name}")
    return manifest


def universe_as_shipped(universe: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """The frozen universe under the shipped DISTINCT ON order: a BMF row
    whose key tuple a correction repeats is the correction's row now. Only
    ``source`` changes; the rows and their order do not."""
    from givingtuesday_datamart.corrections_preflight import _keyed_corrections
    keys = ["filerein_key"] + gm._KEYS
    corrections = _keyed_corrections(gm._read_corrections_csv())[keys].fillna("").drop_duplicates()
    framed = universe[keys].fillna("")
    repeated = pd.MultiIndex.from_frame(framed).isin(pd.MultiIndex.from_frame(corrections))
    relabel = repeated & universe["source"].eq("eo_bmf").to_numpy()
    universe = universe.copy()
    universe.loc[relabel, "source"] = "correction"
    return universe, int(relabel.sum())


def load_grants(pool: Path, extra: list[Path], production: pd.DataFrame) -> pd.DataFrame:
    frames = [pd.read_parquet(pool / "grants.parquet").assign(pool="ranking_proof")]
    if (pool / "funder_supplement.parquet").exists():
        frames.append(pd.read_parquet(pool / "funder_supplement.parquet").assign(pool="funder_supplement"))
    frames += [pd.read_parquet(path).assign(pool=path.parent.name) for path in extra]
    grants = pd.concat(frames, ignore_index=True)
    grants = grants.drop_duplicates(gm._KEYS).reset_index(drop=True)
    for column in ("labeled_funder", "general_sample", "targeted", "high_value"):
        grants[column] = grants[column].fillna(False).astype(bool) if column in grants else False
    return attach_production(grants, production)


def attach_production(grants: pd.DataFrame, production: pd.DataFrame) -> pd.DataFrame:
    production = production[gm._KEYS + ["production_ein"]].fillna("")
    if production.duplicated(gm._KEYS).any():
        raise RuntimeError("Production has two winners for a recipient tuple")
    grants = grants.drop(columns=["production_ein"], errors="ignore").merge(
        production, on=gm._KEYS, how="left", validate="one_to_one")
    grants["production_ein"] = grants["production_ein"].fillna("")
    return grants


# --- Scores ----------------------------------------------------------------------

def _score_chunk(number: int) -> str:
    path = _STATE["directory"] / f"{number:06d}.parquet"
    if not path.exists():
        start = number * CHUNK
        features = gm.score_pairs(_STATE["pairs"][start:start + CHUNK], _STATE["universe"], _STATE["grants"])
        tmp = path.with_suffix(".tmp")
        features.to_parquet(tmp, index=False)
        tmp.replace(path)
    return str(path)


def score(universe: pd.DataFrame, grants: pd.DataFrame, out: Path, workers: int, inputs: str) -> pd.DataFrame:
    """The scores of every candidate pair that passes the chunk rules, as a
    run's checkpoints hold them. Cached under ``out/scores``, keyed by the
    inputs and the scoring code."""
    directory = out / "scores"
    directory.mkdir(parents=True, exist_ok=True)
    pairs = gm.candidate_pairs(universe, grants)
    total = (len(pairs) + CHUNK - 1) // CHUNK
    signature = {"inputs": inputs, "scoring": scoring_digest(), "pairs": len(pairs), "chunk": CHUNK}
    stamp = directory / "signature.json"
    if stamp.exists() and json.loads(stamp.read_text()) != signature:
        raise RuntimeError("The score cache belongs to other inputs or another scorer; use a new --out")
    stamp.write_text(json.dumps(signature, indent=2) + "\n")
    logger.info("Scoring %s candidate pairs in %s chunks", f"{len(pairs):,}", total)
    _STATE.update(directory=directory, pairs=pairs, universe=universe, grants=grants)
    try:
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork")) as pool:
                paths = list(pool.map(_score_chunk, range(total), chunksize=4))
        else:
            paths = [_score_chunk(number) for number in range(total)]
    finally:
        _STATE.clear()
    if not paths:
        return pd.DataFrame(columns=gm.INDEX_COLS + gm.SCORE_COLS)
    return pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)


# --- Outcomes --------------------------------------------------------------------

def winners(matches: pd.DataFrame, universe: pd.DataFrame, grants: pd.DataFrame, ranking: str) -> pd.DataFrame:
    """One row per grant tuple: the winner's universe row, EIN, name, source,
    tier and scores, '' or NaN where the tuple is unmatched."""
    found = matches.set_index(gm.INDEX_COLS[1]).reindex(grants.index)
    row = found[gm.INDEX_COLS[0]].astype("Int64")
    return pd.DataFrame({
        f"{ranking}_row": row,
        f"{ranking}_ein": row.map(universe["filerein_key"]).fillna(""),
        f"{ranking}_name": row.map(universe["full_name"]).fillna(""),
        f"{ranking}_source": row.map(universe["source"]).fillna(""),
        f"{ranking}_tier": found["match_tier"].fillna(""),
        f"{ranking}_name_score": found["name_score"],
        f"{ranking}_addr_score": found["addr_score"],
    }, index=grants.index)


def control_parity(results: pd.DataFrame) -> dict:
    named = ~results["placeholder_now"]
    mismatch = named & results[f"{CONTROL}_ein"].ne(results["production_ein"])
    placeholder = results["placeholder_now"]
    matched_now = pd.concat([results[f"{r}_ein"].ne("") for r in (CONTROL, *CANDIDATES)], axis=1).any(axis=1)
    return {
        "tuples": int(named.sum()),
        "mismatches": int(mismatch.sum()),
        "placeholder_tuples": int(placeholder.sum()),
        "placeholder_tuples_production_matched": int((placeholder & results["production_ein"].ne("")).sum()),
        "placeholder_tuples_matched_now": int((placeholder & matched_now).sum()),
    }


def _agreement(results: pd.DataFrame, ranking: str, universe: pd.DataFrame, grants: pd.DataFrame,
               side: str) -> pd.DataFrame:
    """Where the winner of ``ranking`` agrees with each grant tuple of
    ``results`` (all matched under it), as columns prefixed ``side``."""
    pairs = pd.DataFrame({gm.INDEX_COLS[0]: results[f"{ranking}_row"].astype("int64").to_numpy(),
                          gm.INDEX_COLS[1]: results.index.to_numpy(),
                          "addr_score": results[f"{ranking}_addr_score"].to_numpy()}, index=results.index)
    agreement = gm.location_agreement(pairs, universe, grants)
    return agreement.add_prefix(f"{side}_")


def location_audit(results: pd.DataFrame, universe: pd.DataFrame, grants: pd.DataFrame,
                   candidate: str) -> pd.DataFrame:
    """Every tuple whose winner changed from the control to ``candidate``,
    both matched, with where each winner agrees with the grant and the
    flags."""
    before_ein, after_ein = results[f"{CONTROL}_ein"], results[f"{candidate}_ein"]
    changed = results[before_ein.ne("") & after_ein.ne("") & before_ein.ne(after_ein)].copy()
    changed = changed.join(_agreement(changed, CONTROL, universe, grants, "before"))
    changed = changed.join(_agreement(changed, candidate, universe, grants, "after"))
    for dimension in ("city", "state", "zip"):
        changed[f"lost_{dimension}"] = changed[f"before_same_{dimension}"] & ~changed[f"after_same_{dimension}"]
    before = changed[f"{CONTROL}_addr_score"]
    after = changed[f"{candidate}_addr_score"]
    lost_any = changed[["lost_city", "lost_state", "lost_zip"]].any(axis=1)
    changed["strong_location_loss"] = before.ge(STRONG_ADDRESS) & (before - after).ge(STRONG_DROP) & lost_any
    changed["strong_city_state_loss"] = changed["strong_location_loss"] & (changed["lost_city"] | changed["lost_state"])
    changed["criterion_flag"] = (changed["strong_city_state_loss"]
                                 & changed["before_same_city"] & changed["before_same_zip"])
    # The failure the criterion was written for (Americus IL 31709): the
    # grant's city and ZIP point at the old winner and only its state at
    # the new one.
    changed["state_typo_flag"] = (changed["criterion_flag"] & ~changed["before_same_state"]
                                  & changed["after_same_state"])
    changed["same_name"] = (changed[f"{CONTROL}_name"].eq(changed["full_name"])
                            & changed[f"{candidate}_name"].eq(changed["full_name"]))
    changed["before_holds_grant_name"] = _holds(changed["full_name"], changed[f"{CONTROL}_name"])
    changed["after_holds_grant_name"] = _holds(changed["full_name"], changed[f"{candidate}_name"])
    return changed


def _holds(name: pd.Series, winner: pd.Series) -> pd.Series:
    """Does the winner's cleaned name hold every word of the grant's? A
    review aid ("habitat for humanity" in "habitat for humanity of sangamon
    county"), not a rule."""
    return pd.Series([bool(set(n.split())) and set(n.split()) <= set(w.split())
                      for n, w in zip(name, winner)], index=name.index)


def disagreements(results: pd.DataFrame, universe: pd.DataFrame, grants: pd.DataFrame,
                  other: str) -> pd.DataFrame:
    """Every tuple the default order and ``other`` send to different EINs,
    both matched, with each winner's geography, name and source. The kind
    ``fuzzy_local_vs_exact_in_state`` is where the orders were built to
    differ: the default keeps a closer address under another name in the
    grant's city, ``other`` an exact name elsewhere in its state."""
    first = CANDIDATES[0]
    a, b = results[f"{first}_ein"], results[f"{other}_ein"]
    split = results[a.ne("") & b.ne("") & a.ne(b)].copy()
    exact = gm.FINAL_FILTER_RULES["exact_name_name_min"]
    for ranking in (first, other):
        split = split.join(_agreement(split, ranking, universe, grants, f"{ranking}_geo"))
        split[f"{ranking}_exact"] = split[f"{ranking}_name_score"].ge(exact)
        split[f"{ranking}_holds_grant_name"] = _holds(split["full_name"], split[f"{ranking}_name"])
    split["kind"] = "other"
    built_for = (~split[f"{first}_exact"] & split[f"{first}_geo_tier"].eq(3)
                 & split[f"{other}_exact"] & split[f"{other}_geo_tier"].eq(2))
    split.loc[built_for, "kind"] = "fuzzy_local_vs_exact_in_state"
    return split


def movements(results: pd.DataFrame, candidate: str) -> pd.DataFrame:
    before, after = results[f"{CONTROL}_ein"], results[f"{candidate}_ein"]
    change = pd.Series("unmatched_both", index=results.index)
    change[before.ne("") & after.eq(before)] = "same_ein"
    change[before.ne("") & after.ne("") & after.ne(before)] = "changed_ein"
    change[before.ne("") & after.eq("")] = "lost"
    change[before.eq("") & after.ne("")] = "gained"
    return (results.assign(change=change).groupby(["pool", "change"])
            .agg(tuples=("n_rows", "size"), rows=("n_rows", "sum"), dollars=("dollars", "sum"),
                 positive_dollars=("positive_dollars", "sum")).reset_index().assign(order=candidate))


def labeled_coverage(results: pd.DataFrame, pool: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Known PF funder/recipient relationships covered on the complete lists
    of the pool's labeled funders: by production, the control and each order
    under test. Labels validate relationships, not grant rows."""
    edges = pd.read_parquet(pool / "funder_tuples.parquet")
    labels = pd.read_parquet(pool / "labels.parquet")
    output = pd.read_parquet(pool / "output_pairs.parquet")
    answer = set(map(tuple, labels[["funder_ein", "recip_ein"]].drop_duplicates().to_numpy()))
    # Relationships the matcher does not decide (990 Schedule I rows).
    fixed_rows = output[~output["row_source"].isin(("privategrants_current", "placeholder_recovery"))]
    fixed = set(map(tuple, fixed_rows[["funder_ein", "recip_ein"]].to_numpy()))
    expected = answer & set(map(tuple, output[["funder_ein", "recip_ein"]].to_numpy()))
    found = {}
    for policy in ("production", CONTROL, *CANDIDATES):
        column = f"{policy}_ein"
        e = edges.merge(results[gm._KEYS + [column]], on=gm._KEYS, how="left", validate="many_to_one")
        predicted = set(map(tuple, e[e[column].fillna("").ne("")][["funder_ein", column]].to_numpy()))
        found[policy] = answer & (predicted | fixed)
    if found["production"] != expected:
        raise RuntimeError("The pool's funder lists do not reproduce production's labeled coverage")
    rows, details = [], []
    for policy, covered in found.items():
        rows.append({"policy": policy, "labeled": len(answer), "covered": len(covered),
                     "lost_vs_production": len(found["production"] - covered),
                     "gained_vs_production": len(covered - found["production"])})
    for funder, recipient in sorted(answer):
        details.append({"funder_ein": funder, "recip_ein": recipient,
                        **{policy: (funder, recipient) in covered for policy, covered in found.items()}})
    return pd.DataFrame(rows), pd.DataFrame(details)


def families(results: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    selected = results[results["full_name"].isin(FAMILIES)]
    for ranking in ("production", CONTROL, *CANDIDATES):
        ein = f"{ranking}_ein"
        pieces.append(selected.groupby(["full_name", ein]).agg(rows=("n_rows", "sum"), dollars=("dollars", "sum"))
                      .reset_index().rename(columns={ein: "ein"}).assign(ranking=ranking))
    return pd.concat(pieces, ignore_index=True)[["full_name", "ranking", "ein", "rows", "dollars"]]


def evaluate(universe: pd.DataFrame, grants: pd.DataFrame, out: Path, workers: int, inputs: str) -> dict:
    """Score, resolve under every order, and write the outcomes."""
    logger.info("Preparing %s identities and %s grant tuples", f"{len(universe):,}", f"{len(grants):,}")
    gm.prepare(universe)
    gm.prepare(grants)
    grants["placeholder_now"] = gm.placeholder_names(grants)
    features = score(universe, grants, out, workers, inputs)
    results = grants.copy()
    for ranking in (CONTROL, *CANDIDATES):
        logger.info("Resolving under %s", ranking)
        results = results.join(winners(gm.all_matches(features, universe, grants, ranking=ranking),
                                       universe, grants, ranking))
    columns = gm._KEYS + ["pool", "n_rows", "dollars", "production_ein", f"{CONTROL}_ein", f"{CONTROL}_name"]
    named = ~results["placeholder_now"]
    results[named & results[f"{CONTROL}_ein"].ne(results["production_ein"])][columns].to_csv(
        out / "control_mismatches.csv", index=False)
    summary = {"control_parity": control_parity(results), "candidate_pairs_scored": len(features), "orders": {}}
    for candidate in CANDIDATES:
        audit = location_audit(results, universe, grants, candidate)
        keep = (gm._KEYS + ["pool", "full_name", "n_rows", "dollars", "positive_dollars"]
                + [f"{r}_{c}" for r in (CONTROL, candidate) for c in ("ein", "name", "source", "tier",
                                                                     "name_score", "addr_score")]
                + [c for c in audit.columns if c.startswith(("before_", "after_", "lost_"))]
                + ["strong_location_loss", "strong_city_state_loss", "criterion_flag", "state_typo_flag", "same_name",
                   "before_holds_grant_name", "after_holds_grant_name"])
        audit[keep].sort_values("positive_dollars", ascending=False).to_csv(out / f"changes_{candidate}.csv",
                                                                            index=False)
        audit[audit["strong_location_loss"]][keep].to_csv(out / f"location_flags_{candidate}.csv", index=False)
        flagged = audit[audit["criterion_flag"]]
        summary["orders"][candidate] = {
            "changed_winners": len(audit),
            "same_name_changes": int(audit["same_name"].sum()),
            "strong_location_loss": int(audit["strong_location_loss"].sum()),
            "strong_city_state_loss": int(audit["strong_city_state_loss"].sum()),
            "criterion_flags": len(flagged),
            "criterion_flag_rows": int(flagged["n_rows"].sum()),
            "criterion_flags_from_a_winner_lacking_the_grant_name": int(
                (~flagged["before_holds_grant_name"] & flagged["after_holds_grant_name"]).sum()),
            "criterion_flags_from_a_winner_holding_the_grant_name": int(flagged["before_holds_grant_name"].sum()),
            "state_typo_flags": int(audit["state_typo_flag"].sum()),
        }
    summary["disagreements"] = {}
    for other in CANDIDATES[1:]:
        split = disagreements(results, universe, grants, other)
        pair = (CANDIDATES[0], other)
        split_columns = (gm._KEYS + ["pool", "full_name", "n_rows", "dollars", "positive_dollars", "production_ein",
                                     "kind"]
                         + [f"{r}_{c}" for r in pair for c in ("ein", "name", "source", "name_score", "addr_score",
                                                              "exact", "holds_grant_name")]
                         + [f"{r}_geo_{c}" for r in pair for c in ("same_city", "same_state", "same_zip", "tier")])
        split[split_columns].sort_values("positive_dollars", ascending=False).to_csv(
            out / f"disagreements_{other}.csv", index=False)
        summary["disagreements"][other] = disagreement_breakdown(split, other).to_dict("records")
    results.drop(columns=["compare_addr"], errors="ignore").to_parquet(out / "results.parquet", index=False)
    pd.concat([movements(results, c) for c in CANDIDATES]).to_csv(out / "movements.csv", index=False)
    families(results).to_csv(out / "families.csv", index=False)
    summary["_results"] = results
    return summary


def _finish(out: Path, summary: dict, criteria: dict) -> None:
    summary = {k: v for k, v in summary.items() if not k.startswith("_")}
    summary.update(criteria=criteria, passed={order: all(c.values()) for order, c in criteria.items()},
                   ranking_digest=ranking_digest(), scoring_digest=scoring_digest(), commit=_commit(),
                   finished_at=datetime.now(timezone.utc).isoformat())
    (out / "validation.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(json.dumps(summary, indent=2, default=str))


def known(pool: Path, extra: list[Path], out: Path, workers: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    check_pool(pool)
    universe, relabelled = universe_as_shipped(pd.read_parquet(pool / "universe.parquet"))
    grants = load_grants(pool, extra, pd.read_parquet(pool / "production.parquet"))
    inputs = hashlib.sha256(json.dumps(
        [_digest(pool / "inputs.json")] + [_digest(path) for path in extra]).encode()).hexdigest()
    summary = evaluate(universe, grants, out, workers, inputs)
    coverage, details = labeled_coverage(summary.pop("_results"), pool)
    coverage.to_csv(out / "labeled_coverage.csv", index=False)
    details.to_csv(out / "labeled_relationships.csv", index=False)
    print(coverage.to_string(index=False))
    labeled = coverage.set_index("policy")
    summary.update(universe_rows_relabelled_correction=relabelled, labeled=labeled.to_dict("index"))
    parity = summary["control_parity"]["mismatches"] == 0
    _finish(out, summary, {order: {
        "control_reproduces_production": parity,
        "labeled_covered_at_least_469": int(labeled.loc[order, "covered"]) >= LABELED_FLOOR,
        "no_labeled_relationship_lost": int(labeled.loc[order, "lost_vs_production"]) == 0,
        "no_criterion_location_flag": summary["orders"][order]["criterion_flags"] == 0,
    } for order in CANDIDATES})


def freeze(pool: Path, extra: list[Path], out: Path, seed: int, one_in: int) -> None:
    """A new recipient sample, read-only from production, none of it in a
    pool already inspected; the digest of the orders' code is recorded so
    ``fresh`` refuses after any change to it."""
    from givingtuesday_datamart._internal.db import get_session
    from givingtuesday_datamart.ingestion import datamart_config
    from givingtuesday_datamart.proofs.bmf_policy_subset import selection_sql
    if seed == 0:
        raise ValueError("seed 0 is the hash of the inspected pools")
    out.mkdir(parents=True, exist_ok=True)
    if (out / "manifest.json").exists():
        raise FileExistsError("A sample is frozen here already; use a new --out")
    query = selection_sql().replace("::text, 0)", f"::text, {int(seed)})")
    params = {"one_in": one_in, "cohort": "a^", "placeholder": gm.PLACEHOLDER_REGEX}
    with get_session(config=datamart_config()) as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        for setting in ("work_mem = '64MB'", "max_parallel_workers_per_gather = 0", "statement_timeout = '10min'",
                        "lock_timeout = '5s'", "temp_file_limit = '4GB'",
                        "application_name = 'ranking_validation_freeze'"):
            session.execute(text("SET LOCAL " + setting))
        plan = [row[0] for row in session.execute(text("EXPLAIN " + query), params)]
        sample = pd.read_sql_query(text(query), session.connection(), params=params)
    seen = [pd.read_parquet(pool / "grants.parquet", columns=gm._KEYS)]
    if (pool / "funder_supplement.parquet").exists():
        seen.append(pd.read_parquet(pool / "funder_supplement.parquet", columns=gm._KEYS))
    seen += [pd.read_parquet(path, columns=gm._KEYS) for path in extra]
    seen = pd.concat(seen, ignore_index=True).drop_duplicates()
    inspected = pd.MultiIndex.from_frame(sample[gm._KEYS]).isin(pd.MultiIndex.from_frame(seen))
    sample = sample[~inspected].reset_index(drop=True)
    sample.to_parquet(out / "grants.parquet", index=False)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "seed": seed, "one_in": one_in,
                "tuples": len(sample), "rows": int(sample["n_rows"].sum()), "excluded_inspected": int(inspected.sum()),
                "pool_inputs": _digest(pool / "inputs.json"), "extra": {str(p): _digest(p) for p in extra},
                "grants_sha256": _digest(out / "grants.parquet"), "ranking_digest": ranking_digest(),
                "commit": _commit(), "plan": plan}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: v for k, v in manifest.items() if k != "plan"}, indent=2))


def fresh(pool: Path, out: Path, workers: int) -> None:
    manifest = json.loads((out / "manifest.json").read_text())
    if manifest["grants_sha256"] != _digest(out / "grants.parquet"):
        raise RuntimeError("The frozen sample changed")
    if manifest["ranking_digest"] != ranking_digest():
        raise RuntimeError("The winner orders changed after the sample was frozen; freeze a new sample")
    if manifest["pool_inputs"] != _digest(pool / "inputs.json"):
        raise RuntimeError("Another pool than the one the sample excludes")
    check_pool(pool)
    universe, relabelled = universe_as_shipped(pd.read_parquet(pool / "universe.parquet"))
    grants = pd.read_parquet(out / "grants.parquet").assign(pool="fresh")
    grants = attach_production(grants, pd.read_parquet(pool / "production.parquet"))
    summary = evaluate(universe, grants, out, workers, manifest["grants_sha256"])
    summary.pop("_results")
    summary.update(universe_rows_relabelled_correction=relabelled, sample=manifest["tuples"])
    parity = summary["control_parity"]["mismatches"] == 0
    _finish(out, summary, {order: {
        "control_reproduces_production": parity,
        "no_criterion_location_flag": summary["orders"][order]["criterion_flags"] == 0,
    } for order in CANDIDATES})


def disagreement_breakdown(split: pd.DataFrame, other: str) -> pd.DataFrame:
    """The disagreements of the default order and ``other`` by kind and by
    whether each winner holds every word of the grant's name, with their
    rows, dollars and how many keep production's winner."""
    pair = (CANDIDATES[0], other)
    split = split.assign(**{f"{r}_is_production": split[f"{r}_ein"].eq(split["production_ein"]) for r in pair})
    keys = ["kind"] + [f"{r}_holds_grant_name" for r in pair]
    return (split.groupby(keys).agg(tuples=("n_rows", "size"), rows=("n_rows", "sum"),
                                    positive_dollars=("positive_dollars", "sum"),
                                    **{f"{r}_is_production": (f"{r}_is_production", "sum") for r in pair})
            .reset_index())


def review(out: Path, top: int) -> None:
    """Print a finished run's summary, and for each order compared with the
    default, the disagreement breakdown and the largest disagreements, from
    the run's own outputs."""
    summary = json.loads((out / "validation.json").read_text())
    print(json.dumps({k: summary[k] for k in ("control_parity", "orders", "criteria")
                      + (("labeled",) if "labeled" in summary else ())}, indent=1))
    for order in CANDIDATES:
        flags = pd.read_csv(out / f"location_flags_{order}.csv", dtype=str, keep_default_na=False)
        flags = flags[flags["criterion_flag"].eq("True")]
        flags["positive_dollars"] = pd.to_numeric(flags["positive_dollars"])
        print(f"\n{order}: {len(flags)} criterion flags, {flags['n_rows'].astype(int).sum()} rows")
        columns = ["full_name", "addresscity_key", "addressstate_key", "addresszip_key", "n_rows", "positive_dollars",
                   f"{CONTROL}_name", f"{CONTROL}_addr_score", f"{order}_name", f"{order}_addr_score",
                   "lost_city", "lost_state", "lost_zip"]
        with pd.option_context("display.width", 260, "display.max_colwidth", 38):
            print(flags.sort_values("positive_dollars", ascending=False)[columns].head(top).to_string(index=False))
    for other in CANDIDATES[1:]:
        split = pd.read_csv(out / f"disagreements_{other}.csv", dtype=str, keep_default_na=False)
        for column in ("n_rows", "positive_dollars"):
            split[column] = pd.to_numeric(split[column])
        for column in [c for c in split if c.endswith("holds_grant_name")]:
            split[column] = split[column].eq("True")
        print(f"\n{CANDIDATES[0]} against {other}: {len(split)} tuples")
        with pd.option_context("display.width", 250, "display.max_colwidth", 40):
            print(disagreement_breakdown(split, other).to_string(index=False))
            columns = ["full_name", "addresscity_key", "addressstate_key", "n_rows", "positive_dollars"] + [
                f"{r}_{c}" for r in (CANDIDATES[0], other) for c in ("name", "name_score", "addr_score")]
            print(split.sort_values("positive_dollars", ascending=False)[columns].head(top).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("known", "freeze", "fresh", "review"))
    parser.add_argument("--pool", type=Path, help="the ranking proof's frozen directory")
    parser.add_argument("--extra", type=Path, nargs="*", default=[],
                        help="other frozen grant samples already inspected (grants.parquet files)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--one-in", type=int, default=100)
    parser.add_argument("--top", type=int, default=25, help="review: how many of the largest disagreements to print")
    args = parser.parse_args()
    if args.command != "review" and args.pool is None:
        parser.error(f"{args.command} needs --pool")
    if args.command == "review":
        review(args.out, args.top)
    elif args.command == "known":
        known(args.pool, args.extra, args.out, args.workers)
    elif args.command == "freeze":
        freeze(args.pool, args.extra, args.out, args.seed, args.one_in)
    else:
        fresh(args.pool, args.out, args.workers)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    main()
