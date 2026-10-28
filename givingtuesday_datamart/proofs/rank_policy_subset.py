"""Larger, read-only ranking proof with risk strata and an unseen holdout.

No production matcher code, view, or output table is changed. The policies
are fixed here before reading the holdout outcomes. A fresh extract keeps
all candidate identities, a 1/50 general recipient sample, diagnostic names,
high-value rows, risk-stratified recipients, and complete lists from 100
hash-selected PF funders with independent recipient labels.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.matching_regression_checks import AMT, PLACEHOLDER_REGEX
from givingtuesday_datamart.proofs import bmf_policy_subset as base

RANKINGS = ("current", "filing_tie", "filing_bonus_005", "filing_first", "name_then_filing")
POLICIES = ("current", "placeholders_only") + RANKINGS[1:] + ("conservative",)
PATTERN = base.COHORT_PATTERN + r"|stanford|notre dame|vermont|detroit mercy|michael.*fox|fidelity charitable"
RISKS = ("bmf_secondary", "bmf_without_filing", "filed_fuzzy", "sparse", "name_collision")
METRICS = ["n_rows", "dollars", "positive_dollars", "recovered_rows"]


def key_hash(frame: pd.DataFrame) -> pd.Series:
    return pd.util.hash_pandas_object(frame[gm._KEYS], index=False)


def assign_splits(grants: pd.DataFrame, previous: pd.DataFrame) -> pd.Series:
    known = pd.MultiIndex.from_frame(grants[gm._KEYS]).isin(pd.MultiIndex.from_frame(previous[gm._KEYS]))
    holdout = key_hash(grants).mod(2).eq(1) & ~known
    return pd.Series("calibration", index=grants.index).where(~holdout, "holdout")


def risk_sample(production: pd.DataFrame, bmf: pd.DataFrame, universe: pd.DataFrame,
                per_risk: int = 5000) -> pd.DataFrame:
    """Select risk cases without inspecting any experimental policy outcome."""
    p = production.copy()
    meta = bmf.drop_duplicates("filerein_key").set_index("filerein_key")
    primary = meta["name"].fillna("").map(gm.clean_name)
    filed = universe[universe.source.isin(("basic_fields", "basic_fields_pf"))].copy()
    names = [gm.clean_name(gm.join_name(a, b)) for a, b in zip(filed.name1_key, filed.name2_key)]
    owners = filed.assign(full_name=names)[["full_name", "filerein_key"]].drop_duplicates()
    counts = owners.groupby("full_name").filerein_key.nunique()
    first = owners.drop_duplicates("full_name").set_index("full_name").filerein_key
    is_bmf = p.production_source.eq("eo_bmf")
    matched_name = p.production_name.fillna("")
    collision = (matched_name.map(counts).fillna(0).gt(1)
                 | (matched_name.map(first).notna() & matched_name.map(first).ne(p.production_ein)))
    masks = {
        "bmf_secondary": is_bmf & matched_name.ne(p.production_ein.map(primary)),
        "bmf_without_filing": is_bmf & ~p.production_ein.isin(set(filed.filerein_key)),
        "filed_fuzzy": p.production_source.isin(("basic_fields", "basic_fields_pf")) & pd.to_numeric(p.production_name_score, errors="coerce").lt(.99),
        "sparse": p.addressstate_key.eq("") | p.addresszip_key.eq(""),
        "name_collision": is_bmf & collision,
    }
    h = key_hash(p)
    chosen = pd.Series(False, index=p.index)
    sampled = {}
    for name, mask in masks.items():
        idx = h[mask].sort_values(kind="mergesort").head(per_risk).index
        flag = pd.Series(False, index=p.index)
        flag.loc[idx] = True
        sampled[name] = flag
        chosen |= flag
    result = p.loc[chosen, gm._KEYS].copy()
    for name, flag in sampled.items():
        result["risk_" + name] = flag.loc[result.index]
    return result.reset_index(drop=True)


def grants_query() -> str:
    keys = ", ".join(gm._KEYS)
    keyed = ", ".join("g." + k for k in gm._KEYS)
    name = "concat_ws(' ', name1_key, name2_key)"
    amount = AMT.format(col="sigocpyamoun")
    sample = f"mod(abs(hashtextextended(jsonb_build_array({keys})::text, 0)), :one_in) = 0"
    return f"""
        WITH risk AS (
            SELECT * FROM jsonb_to_recordset(CAST(:risk_keys AS jsonb)) AS r(
                {', '.join(k + ' text' for k in gm._KEYS)})
        ), selected AS (
            SELECT {keys} FROM public.privategrants_w_column_keys_view
            WHERE NOT (trim(name1_key) = '' AND trim(name2_key) = '')
              AND ((taxyear::int >= 2015 AND (({sample}) OR {name} ~* :cohort OR {name} ~* :placeholder
                   OR ({amount}) >= :high_value)) OR filerein = ANY(CAST(:funders AS text[])))
            GROUP BY {keys}
            UNION SELECT {keys} FROM risk
        )
        SELECT {keyed}, count(*)::bigint AS n_rows,
               coalesce(sum({amount}), 0)::float8 AS dollars,
               coalesce(sum(greatest({amount}, 0)), 0)::float8 AS positive_dollars,
               count(*) FILTER (WHERE row_source = 'placeholder_recovery') AS recovered_rows,
               ({sample}) AS general_sample, ({name} ~* :cohort) AS targeted,
               ({name} ~* :placeholder) AS placeholder,
               bool_or(({amount}) >= :high_value) AS high_value,
               bool_or(filerein = ANY(CAST(:funders AS text[]))) AS labeled_funder
        FROM public.privategrants_w_column_keys_view g JOIN selected USING ({keys})
        GROUP BY {keyed}
        HAVING bool_or(taxyear::int >= 2015)
        ORDER BY {keyed}
    """


def _freeze(c, query: str, params: dict, out: Path, name: str, manifest: dict) -> pd.DataFrame:
    logger.info("Reading %s", name)
    c = c.execution_options(stream_results=False)
    manifest["plans"][name] = [r[0] for r in c.execute(text("EXPLAIN " + query), params)]
    chunks = pd.read_sql_query(text(query), c.execution_options(stream_results=True), params=params, chunksize=100_000)
    frame = pd.concat(chunks, ignore_index=True)
    path = out / f"{name}.parquet"
    frame.to_parquet(path, index=False)
    manifest["files"][path.name] = {"rows": len(frame), "sha256": base._digest(path)}
    logger.info("Frozen %s: %s rows", name, f"{len(frame):,}")
    return frame


def extract(out: Path, previous: Path, one_in: int = 50, funder_count: int = 100, per_risk: int = 5000) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if (out / "inputs.json").exists():
        raise FileExistsError("Use a fresh directory for new inputs")
    old = pd.read_parquet(previous / "grants.parquet", columns=gm._KEYS)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "policies": list(POLICIES),
                "one_in": one_in, "per_risk": per_risk, "funder_count": funder_count,
                "high_value": 5_000_000, "cohort_pattern": PATTERN, "files": {}, "plans": {},
                "previous_manifest": base._digest(previous / "inputs.json"),
                "proof_source": base._digest(Path(__file__)), "pandas_version": pd.__version__,
                "policy_hash": hashlib.sha256(inspect.getsource(ranked_matches).encode()).hexdigest()}
    with get_session(config=datamart_config()) as session:
        c = session.connection()
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        for setting in ("work_mem = '64MB'", "max_parallel_workers_per_gather = 0", "statement_timeout = '10min'",
                        "lock_timeout = '5s'", "temp_file_limit = '4GB'", "application_name = 'ranking_policy_proof'"):
            c.execute(text("SET LOCAL " + setting))
        manifest["transaction_read_only"] = c.execute(text("SHOW transaction_read_only")).scalar_one()
        u = _freeze(c, gm.universe_sql(), {}, out, "universe", manifest)
        b = _freeze(c, "SELECT lower(ein) AS filerein_key, lower(name) AS name, lower(sort_name) AS sort_name, "
                    "affiliation FROM public.eo_bmf", {}, out, "bmf", manifest)
        production = _freeze(c, "SELECT " + ", ".join(gm._KEYS) + ", recipeint_ein_key AS production_ein, "
                             "match_source AS production_source, match_name AS production_name, "
                             "match_name_score AS production_name_score, match_addr_score AS production_addr_score, "
                             "match_tier AS production_tier FROM public.pf_grant_matching_temp_table",
                             {}, out, "production", manifest).fillna("")
        risks = risk_sample(production, b, u, per_risk)
        risks.to_parquet(out / "risk_keys.parquet", index=False)
        manifest["files"]["risk_keys.parquet"] = {"rows": len(risks), "sha256": base._digest(out / "risk_keys.parquet")}
        c = c.execution_options(stream_results=False)
        funders = [r[0] for r in c.execute(text("SELECT DISTINCT funder_ein FROM grantor_recipient_labeled_set "
                   "WHERE filing_type = '990PF' ORDER BY funder_ein"))]
        # Fixed hash order; selecting funders never depends on policy outcomes.
        funders = sorted(funders, key=lambda e: hashlib.sha256(e.encode()).hexdigest())[:funder_count]
        params = {"one_in": one_in, "cohort": PATTERN, "placeholder": PLACEHOLDER_REGEX,
                  "risk_keys": json.dumps(risks[gm._KEYS].to_dict("records")), "funders": funders,
                  "high_value": manifest["high_value"]}
        grants = _freeze(c, grants_query(), params, out, "grants", manifest)
        grants = grants.merge(risks, on=gm._KEYS, how="left", validate="one_to_one")
        for name in RISKS:
            grants["risk_" + name] = grants["risk_" + name].fillna(False).astype(bool)
        grants["evaluation_split"] = assign_splits(grants, old)
        grants.to_parquet(out / "grants.parquet", index=False)
        manifest["files"]["grants.parquet"] = {"rows": len(grants), "sha256": base._digest(out / "grants.parquet")}
        keys = ", ".join(gm._KEYS)
        _freeze(c, f"SELECT filerein AS funder_ein, {keys}, count(*)::bigint AS n_rows "
                   f"FROM public.privategrants_w_column_keys_view WHERE filerein = ANY(CAST(:funders AS text[])) "
                   f"AND NOT (trim(name1_key) = '' AND trim(name2_key) = '') GROUP BY filerein, {keys}",
                params, out, "funder_tuples", manifest)
        _freeze(c, "SELECT DISTINCT funder_ein, recip_ein FROM grantor_recipient_labeled_set "
                   "WHERE filing_type = '990PF' AND funder_ein = ANY(CAST(:funders AS text[]))",
                params, out, "labels", manifest)
        _freeze(c, "SELECT DISTINCT granter_ein AS funder_ein, grantee_ein AS recip_ein, row_source "
                   "FROM public.unioned_grants WHERE granter_ein = ANY(CAST(:funders AS text[])) "
                   "AND grantee_ein IS NOT NULL AND grantee_ein <> ''",
                params, out, "output_pairs", manifest)
    manifest["split_counts"] = grants.evaluation_split.value_counts().to_dict()
    manifest["funders"] = funders
    (out / "inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    logger.info("Snapshot complete: %s", manifest["split_counts"])


def ranked_matches(features: pd.DataFrame, u: pd.DataFrame, g: pd.DataFrame, ranking: str) -> pd.DataFrame:
    """Same thresholds, guards and ambiguity rules; experimental winner order only.

    Kept inside proofs so production's resolver is untouched. Offline tests
    require the 'current' order to equal gm.all_matches on adversarial cases.
    """
    if ranking not in RANKINGS:
        raise ValueError(ranking)
    m = gm.filter_match_rules(features, **gm.FINAL_FILTER_RULES).copy()
    m = gm.guard_government_names(m, g.full_name, u.full_name).drop_duplicates()
    m["match_tier"] = pd.Series(gm.STATE_TIER, index=m.index).where(~gm._by_address(m), gm.ADDRESS_TIER)
    exact_state = m[(m.name_score >= gm.FINAL_FILTER_RULES["exact_name_name_min"]) & m.state_score.eq(1)]
    owners = exact_state.assign(_ein=exact_state[gm.INDEX_COLS[0]].map(u.filerein_key)).groupby(gm.INDEX_COLS[1])._ein.nunique()
    m["_score"] = 2 * m.name_score + m.addr_score
    m["_preferred"] = m[gm.INDEX_COLS[0]].map(u.source).ne("eo_bmf")
    m["_exact"] = m[gm.INDEX_COLS[0]].map(u.full_name).eq(m[gm.INDEX_COLS[1]].map(g.full_name))
    order = ["_score"]
    if ranking == "filing_tie":
        order = ["_score", "_preferred"]
    elif ranking == "filing_bonus_005":
        m["_score"] += .05 * m._preferred
    elif ranking == "filing_first":
        order = ["_preferred", "_score"]
    elif ranking == "name_then_filing":
        order = ["_exact", "_preferred", "_score"]
    # Stable sorting retains the original candidate order on complete ties.
    m = m.sort_values(order, ascending=False, kind="mergesort").drop_duplicates(gm.INDEX_COLS[1])
    shared = m.match_tier.eq(gm.STATE_TIER) & m[gm.INDEX_COLS[1]].map(owners).fillna(1).gt(1)
    lines = g.address1_key + " " + g.address2_key
    shared &= m[gm.INDEX_COLS[1]].map(~lines.str.contains(gm._STREET)).astype(bool)
    m = m[~shared].drop(columns=["_score", "_preferred", "_exact"])
    name_only = gm.name_only_matches(u, g, m[gm.INDEX_COLS[1]]).assign(name_score=1., addr_score=float("nan"))
    result = pd.concat([m.assign(match_name_words=pd.NA), name_only], ignore_index=True)
    result["match_name_words"] = result.match_name_words.astype("Int64")
    return result


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    cohorts = ("all_selected", "general_sample", "high_value", "labeled_funder", "targeted", "placeholder") + tuple("risk_"+r for r in RISKS)
    for split, frame in results.groupby("evaluation_split", sort=False):
        for cohort in cohorts:
            selected = frame if cohort == "all_selected" else frame[frame[cohort]]
            if selected.empty:
                continue
            b, a = selected.baseline_ein, selected.ein
            change = pd.Series("unmatched_both", index=selected.index)
            change.loc[b.ne("") & a.eq(b)] = "same_ein"
            change.loc[b.ne("") & a.ne("") & a.ne(b)] = "changed_ein"
            change.loc[b.ne("") & a.eq("")] = "lost"
            change.loc[b.eq("") & a.ne("")] = "gained"
            t = selected.assign(change=change).groupby(["policy", "change"]).agg(
                tuples=("ein", "size"), rows=("n_rows", "sum"), dollars=("dollars", "sum"),
                positive_dollars=("positive_dollars", "sum")).reset_index()
            t.insert(0, "cohort", cohort); t.insert(0, "evaluation_split", split)
            pieces.append(t)
    return pd.concat(pieces, ignore_index=True)


def labeled_coverage(results: pd.DataFrame, edges: pd.DataFrame, labels: pd.DataFrame,
                     output_pairs: pd.DataFrame | None = None, detail_out: Path | None = None) -> pd.DataFrame:
    """Known PF funder/recipient pair coverage on complete sampled funder lists.

    Labels validate relationships, not each grant row. No precision claim.
    Whole-funder coverage cannot be split by recipient without changing its
    meaning, so it is reported separately from the recipient holdout.
    """
    before = None
    rows = []
    details = []
    answer = set(map(tuple, labels[["funder_ein", "recip_ein"]].drop_duplicates().to_numpy()))
    fixed = set()
    expected = None
    if output_pairs is not None:
        fixed_rows = output_pairs[~output_pairs.row_source.isin(("privategrants_current", "placeholder_recovery"))]
        fixed = set(map(tuple, fixed_rows[["funder_ein", "recip_ein"]].to_numpy()))
        expected = answer & set(map(tuple, output_pairs[["funder_ein", "recip_ein"]].to_numpy()))
    for policy, frame in results.groupby("policy", sort=False):
        e = edges.merge(frame[gm._KEYS + ["ein"]], on=gm._KEYS, how="left", validate="many_to_one")
        predicted = set(map(tuple, e[e.ein.fillna("").ne("")][["funder_ein", "ein"]].drop_duplicates().to_numpy()))
        found = answer & (predicted | fixed)
        if policy == "current":
            before = found
            if expected is not None and found != expected:
                raise RuntimeError("Sampled labeled coverage does not reproduce the full production output")
        if before is None:
            raise ValueError("Current policy must be first")
        rows.append({"policy": policy, "labeled_pairs": len(answer), "covered": len(found),
                     "coverage_pct": 100 * len(found) / len(answer) if answer else None,
                     "known_pairs_lost": len(before-found), "known_pairs_gained": len(found-before)})
        for funder, recipient in sorted(answer):
            was, now = (funder,recipient) in before, (funder,recipient) in found
            details.append({"policy":policy,"funder_ein":funder,"recip_ein":recipient,
                            "baseline_covered":was,"covered":now,
                            "change":"lost" if was and not now else "gained" if now and not was else "same"})
    if detail_out is not None:
        pd.DataFrame(details).to_csv(detail_out,index=False)
    return pd.DataFrame(rows)


def compare(out: Path, workers: int = 8) -> None:
    manifest = json.loads((out / "inputs.json").read_text())
    for name, info in manifest["files"].items():
        if base._digest(out / name) != info["sha256"]:
            raise RuntimeError(f"Frozen input changed: {name}")
    if manifest["policies"] != list(POLICIES):
        raise RuntimeError("Policy list changed after freezing the holdout")
    if manifest["policy_hash"] != hashlib.sha256(inspect.getsource(ranked_matches).encode()).hexdigest():
        raise RuntimeError("Ranking rules changed after freezing the holdout")
    u, g, b, p = [pd.read_parquet(out / f"{n}.parquet") for n in ("universe", "grants", "bmf", "production")]
    g = g.merge(p[gm._KEYS+["production_ein"]], on=gm._KEYS, how="left", validate="one_to_one")
    g.production_ein = g.production_ein.fillna("")
    logger.info("Preparing %s identities / %s tuples", len(u), len(g))
    gm.prepare(u); gm.prepare(g)
    flags = base.identity_flags(u, b)
    features = base.score(u, g, out, workers)
    features = features.sort_values(gm.INDEX_COLS, kind="mergesort")
    frames = []
    for policy in POLICIES:
        logger.info("Resolving %s", policy)
        if policy == "current":
            matches = gm.all_matches(features, u, g)
        elif policy in ("placeholders_only", "conservative"):
            matches = base.resolve_policy(features, u, g, flags, policy)
        else:
            open_ = g[~g.placeholder]
            f = features[features[gm.INDEX_COLS[1]].isin(open_.index)]
            matches = ranked_matches(f, u, open_, policy)
        frame = base.comparison_rows(g, u, matches, policy)
        if policy == "current":
            baseline = frame.ein
            mismatch = baseline.ne(g.production_ein)
            parity = {"tuples": len(g), "mismatches": int(mismatch.sum())}
            (out / "parity.json").write_text(json.dumps(parity, indent=2)+"\n")
            if mismatch.any():
                raise RuntimeError("Baseline does not reproduce production")
            logger.info("Checking proof resolver against production resolver")
            control = ranked_matches(features,u,g,"current")
            copied = base.comparison_rows(g,u,control,"control").ein
            resolver_parity = {"tuples":len(g),"mismatches":int(copied.ne(baseline).sum())}
            (out / "resolver_parity.json").write_text(json.dumps(resolver_parity,indent=2)+"\n")
            if resolver_parity["mismatches"]:
                raise RuntimeError("Proof resolver changed a decision before changing ranking")
        frame["baseline_ein"] = baseline
        frames.append(frame)
        frame.to_parquet(out / f"result_{policy}.parquet", index=False)
    result = pd.concat(frames, ignore_index=True)
    write_outputs(out,result)
    print(json.dumps(parity))


def write_outputs(out: Path, result: pd.DataFrame) -> None:
    result.to_parquet(out / "results.parquet", index=False)
    summarize(result).to_csv(out / "summary.csv", index=False)
    coverage = labeled_coverage(result, pd.read_parquet(out / "funder_tuples.parquet"),
                                pd.read_parquet(out / "labels.parquet"), pd.read_parquet(out / "output_pairs.parquet"),
                                out / "labeled_pair_changes.csv")
    coverage.to_csv(out / "labeled_coverage.csv", index=False)
    changed = result[result.ein.ne(result.baseline_ein)]
    changed[gm._KEYS+["evaluation_split", "policy", "n_rows", "dollars", "baseline_ein", "ein", "matched_name", "source", "tier"]].to_csv(out / "changes.csv", index=False)
    result[result.full_name.isin(("johns hopkins university", "duke university", "bates college", "weta public television and radio"))].groupby(
        ["policy", "full_name", "ein"]).agg(tuples=("ein", "size"),rows=("n_rows", "sum"),dollars=("dollars", "sum")).to_csv(out / "diagnostics.csv")
    print(coverage.to_string(index=False))


def repair_funder_pool(out: Path) -> None:
    """Supplement old funder keys omitted by the initial 2015-only selector.

    Existing outcomes/holdout stay fixed. Additions go to calibration and
    use the same frozen universe, policies, guards and production control.
    """
    g=pd.read_parquet(out/'grants.parquet')
    e=pd.read_parquet(out/'funder_tuples.parquet')
    missing=e[gm._KEYS].drop_duplicates().merge(g[gm._KEYS],on=gm._KEYS,how='left',indicator=True)
    missing=missing[missing._merge.eq('left_only')][gm._KEYS]
    keys=', '.join(gm._KEYS); keyed=', '.join('g.'+k for k in gm._KEYS)
    amount=AMT.format(col='sigocpyamoun')
    name="concat_ws(' ', name1_key, name2_key)"
    # The flags come from the same expressions and patterns as grants_query's,
    # so a supplement tuple is a placeholder or targeted exactly when it
    # would have been one in the frozen pool.
    q=f'''WITH selected AS (SELECT * FROM jsonb_to_recordset(CAST(:keys AS jsonb)) AS x(
        {', '.join(k+' text' for k in gm._KEYS)}))
        SELECT {keyed}, count(*)::bigint AS n_rows, coalesce(sum({amount}),0)::float8 AS dollars,
        coalesce(sum(greatest({amount},0)),0)::float8 AS positive_dollars,
        count(*) FILTER (WHERE row_source='placeholder_recovery') AS recovered_rows,
        ({name} ~* :cohort) AS targeted, ({name} ~* :placeholder) AS placeholder
        FROM public.privategrants_w_column_keys_view g JOIN selected USING ({keys})
        GROUP BY {keyed} HAVING bool_or(taxyear::int>=2015) ORDER BY {keyed}'''
    cohort=json.loads((out/'inputs.json').read_text())['cohort_pattern']
    with get_session(config=datamart_config()) as s:
        s.execute(text('SET TRANSACTION READ ONLY'))
        for setting in ("work_mem='64MB'","max_parallel_workers_per_gather=0","statement_timeout='10min'","temp_file_limit='4GB'"):
            s.execute(text('SET LOCAL '+setting))
        extra=pd.read_sql_query(text(q),s.connection(),params={'keys':json.dumps(missing.to_dict('records')),
                                                               'cohort':cohort,'placeholder':PLACEHOLDER_REGEX})
    for col in ['general_sample','high_value']+['risk_'+r for r in RISKS]:extra[col]=False
    extra['labeled_funder']=True;extra['evaluation_split']='calibration'
    extra.to_parquet(out/'funder_supplement.parquet',index=False)
    u=pd.read_parquet(out/'universe.parquet');b=pd.read_parquet(out/'bmf.parquet');p=pd.read_parquet(out/'production.parquet')
    extra=extra.merge(p[gm._KEYS+['production_ein']],on=gm._KEYS,how='left',validate='one_to_one')
    extra.production_ein=extra.production_ein.fillna('')
    gm.prepare(u);gm.prepare(extra)
    f=gm.score_slice(u,extra);flags=base.identity_flags(u,b);frames=[]
    for policy in POLICIES:
        if policy=='current':m=gm.all_matches(f,u,extra)
        elif policy in ('placeholders_only','conservative'):m=base.resolve_policy(f,u,extra,flags,policy)
        else:
            open_=extra[~extra.placeholder]
            m=ranked_matches(f[f[gm.INDEX_COLS[1]].isin(open_.index)],u,open_,policy)
        frame=base.comparison_rows(extra,u,m,policy)
        if policy=='current':
            baseline=frame.ein
            if baseline.ne(extra.production_ein).any():raise RuntimeError('Supplement baseline differs from production')
        frame['baseline_ein']=baseline;frames.append(frame)
    result=pd.concat([pd.read_parquet(out/'results.parquet')]+frames,ignore_index=True)
    write_outputs(out,result)
    (out/'funder_supplement.json').write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat(),
        'tuples':len(extra),'rows':int(extra.n_rows.sum()),'sha256':base._digest(out/'funder_supplement.parquet'),
        'production_mismatches':0,'split':'calibration'},indent=2)+'\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("extract", "compare"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.command == "extract":
        if args.previous is None:
            parser.error("extract requires --previous to exclude previously seen tuples from the holdout")
        extract(args.out, args.previous)
    else:
        compare(args.out, args.workers)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
