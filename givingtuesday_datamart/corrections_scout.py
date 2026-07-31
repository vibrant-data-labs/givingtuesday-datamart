"""Scout for corrections-registry candidates: high-dollar near misses.

Inverts corrections_preflight: instead of proving authored corrections win,
it finds the grant tuples a correction *would* win — the worklist for
data/corrections/org_identities.csv, worked top-down by dollars
(docs/corrections-plan.md). Run it any time; read-only.

    python -m givingtuesday_datamart.corrections_scout [--top N] [--out PATH]
    python -m givingtuesday_datamart.corrections_scout --candid [--out PATH]

Two detectors:

  default — top-dollar tuple sweep (unknown-unknowns, method below);
  --candid — ground-truth sweep over the Candid labeled set
      (grantor_recipient_labeled_set, 990PF side): for each labeled
      (funder_ein, recip_ein) pair, score the funder's grant tuples
      against the recipient EIN's universe identity rows (falling back to
      Candid's gm_name when the EIN isn't in the universe). Pairs no
      current rule can reach whose best tuple still names the recipient
      at JW >= 0.80 become correction drafts with the TRUE EIN attached —
      near-zero verification cost. The covered/uncovered call here is a
      per-pair reachability approximation (blockable + FINAL tier logic,
      no winner competition); corrections_preflight stays the ground
      truth for authored rows.

Method:
  1. Aggregate privategrants_w_column_keys_view by the 7-key recipient
     tuple (2015+, org-named, placeholder names excluded — the same
     population the matcher sees) and take the top N tuples by dollars.
  2. Slice-match them against the current filer universe with the
     matcher's own scoring (corrections_preflight._score_slice) — tuples
     with any FINAL_FILTER_RULES-surviving pair already match; drop them.
  3. For each unmatched tuple, look for the identity a correction would
     assert, in the two geometries blocking can never pair:
       * exact_name_cross_state — a universe row with the identical
         normalized name in another state (the MJFF Hagerstown-lockbox
         pattern; the exact-name tier requires same state);
       * state_name_variant — best Jaro-Winkler >= 0.80 among same-state
         universe rows, cross-zip (the MJFF suffix-variant pattern,
         JW 0.90 — blocking pairs only same-zip or identical-name);
       * zip_near_miss — a same-zip candidate that narrowly failed the
         filter tiers (best blocked pair's name/addr scores reported);
       * no_candidate — nothing plausible; manual-research bucket
         (foreign orgs and non-filers land here — structurally
         uncorrectable, don't spend verification time on them).
  4. Write a ranked CSV of unmatched tuples with the best candidate EIN,
     scores, a ProPublica evidence URL, and the verbatim raw name/address
     values a correction row needs — verify top-down, paste accepted rows
     into org_identities.csv, then prove them with corrections_preflight.

Caveats:
  * Dollar ranks are computed on staging as-is; the issue #33 duplication
    (whole PF blocks doubled in 2024+ batches) inflates some totals until
    its fix lands. Ranks may reshuffle modestly; candidates stay valid.
  * Recurrence rule (docs/corrections-plan.md): if many candidates share
    one failure shape (e.g. a suffix style), that's a matcher
    normalization/tier change, not many CSV rows. The per-candidate-EIN
    cluster counts in the output make this visible.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import jellyfish
import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart._internal.address_cleaning import create_clean_address
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.corrections_preflight import (
    _UNIVERSE_SQL,
    _clean_like_run,
    _score_slice,
)
from givingtuesday_datamart.grant_matching import (
    CHUNK_FILTER_RULES,
    FINAL_FILTER_RULES,
    create_full_name,
    filter_match_rules,
)
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.matching_regression_checks import AMT, PLACEHOLDER_REGEX

# Same-state fuzzy floor for calling a universe row a plausible identity of
# an unmatched tuple. Deliberately below the matcher's 0.99 exact-name tier
# (these are the pairs blocking never scores) and above its 0.70 fuzzy
# floor: MJFF's documented variant sat at JW 0.90.
NEAR_MISS_JW_MIN = 0.80

_US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc", "pr", "vi", "gu", "mp", "as",
}

_DEFAULT_OUT = (
    Path(__file__).resolve().parents[1]
    / "data" / "exploratory" / "corrections_scout_candidates.csv"
)

# Scout-side junk filter, ranking hygiene only — never used by the matcher.
# Patient-assistance and attachment-reference pseudo-names that
# PLACEHOLDER_REGEX doesn't cover ("VARIOUS NEEDY PATIENTS", "HIPPA
# REGULATIONS PREVENT THE LISTING OF NAMES", "ATCH 4", ...). These are the
# structurally-unmatchable bucket of docs/missing_grants_capture_analysis.md:
# no correction can ever route them, and their long strings attract spurious
# JW-0.8 candidates. Filtering them frees top-N slots for real candidates.
_SCOUT_JUNK_REGEX = (
    r"(\yvarious\y)"
    r"|(\yhipp?aa?\y)"
    r"|(health insurance portability)"
    r"|(\y(individual|needy|ill|indigent|eligible|qualif\w*|qual)\y[\s\w]*\ypatients?\y)"
    r"|(\ypatients?\y[\s\w]*\y(programs?|assistance)\y)"
    r"|(\yprescription drugs?\y)"
    r"|(\ydrugs?\s*(and|&)\s*medicines\y)"
    r"|(\yatch\y)"
    r"|(\y(grants?|donations?|contributions?) to (numerous|multiple|individuals)\y)"
)

# Top-dollar recipient tuples, same population as privategrants_unique_names_view
# (2015+, org-named) minus placeholder pseudo-names, with raw verbatim
# exemplar values (MIN per group — groups differ only in case / zip+4) for
# drafting correction rows.
_TOP_TUPLES_SQL = f"""
    SELECT
        name1_key, name2_key, address1_key, address2_key,
        addresscity_key, addressstate_key, addresszip_key,
        COUNT(*) AS n_rows,
        SUM({AMT.format(col="sigocpyamoun")}) AS dollars,
        MIN(sigocpyrbnbn1) AS raw_name1,
        MIN(sigocpyrbnbn2) AS raw_name2,
        MIN(sigocpyrfaal1) AS raw_address1,
        MIN(sigocpyrfaal2) AS raw_address2,
        MIN(sigocpyrfaci)  AS raw_city,
        MIN(sigocpyrfapo)  AS raw_state,
        MIN(sigocpyrfapc)  AS raw_zip
    FROM public.privategrants_w_column_keys_view
    WHERE taxyear::int >= 2015
      AND NOT (TRIM(name1_key) = '' AND TRIM(name2_key) = '')
      AND concat_ws(' ', name1_key, name2_key) !~* '{PLACEHOLDER_REGEX}'
      AND concat_ws(' ', name1_key, name2_key) !~* '{_SCOUT_JUNK_REGEX}'
    GROUP BY name1_key, name2_key, address1_key, address2_key,
             addresscity_key, addressstate_key, addresszip_key
    ORDER BY dollars DESC NULLS LAST
    LIMIT :top
"""

_CAND_COLS = [
    "filerein_key", "full_name", "address1_key", "address2_key",
    "addresscity_key", "addressstate_key", "addresszip_key", "source",
]


def _best_same_state(universe: pd.DataFrame, name: str, state: str) -> tuple[float, int | None]:
    """(best JW, universe index) over same-state universe rows. Linear scan;
    fine at scout scale because (name, state) lookups are memoised by caller."""
    grp = universe.index[universe["addressstate_key"] == state]
    if not len(grp):
        return 0.0, None
    sims = [
        jellyfish.jaro_winkler_similarity(name, n)
        for n in universe.loc[grp, "full_name"]
    ]
    best_pos = max(range(len(sims)), key=sims.__getitem__)
    return sims[best_pos], grp[best_pos]


def run_scout(top: int, out_path: Path) -> int:
    config = datamart_config()
    with get_session(config=config) as session:
        conn = session.connection()
        logger.info(f"Aggregating top {top:,} recipient tuples by dollars ...")
        tuples = pd.read_sql_query(text(_TOP_TUPLES_SQL), conn, params={"top": top})
        logger.info("Reading filer universe ...")
        universe = pd.read_sql_query(text(_UNIVERSE_SQL), conn)

    # Numeric cols first: _clean_like_run's frame-wide fillna("") would turn
    # a NULL dollars sum into '' and poison the float cast.
    tuples["dollars"] = tuples["dollars"].astype(float).fillna(0.0)
    _clean_like_run(universe)
    _clean_like_run(tuples)

    # --- which top tuples already match? -------------------------------
    u_slice = universe[
        universe["clean_zip"].isin(set(tuples["clean_zip"]))
        | universe["full_name"].isin(set(tuples["full_name"]))
    ]
    logger.info(f"universe slice for top tuples: {len(u_slice):,} rows")
    features = _score_slice(u_slice, tuples)
    surviving = filter_match_rules(features_df=features, **CHUNK_FILTER_RULES)
    surviving = filter_match_rules(features_df=surviving, **FINAL_FILTER_RULES)
    matched_idx = set(surviving["private_foundations_df_index"])
    matched = tuples.index.isin(matched_idx)
    logger.info(
        f"top {len(tuples):,} tuples: {matched.sum():,} already match "
        f"(${tuples.loc[matched, 'dollars'].sum():,.0f}); "
        f"{(~matched).sum():,} unmatched (${tuples.loc[~matched, 'dollars'].sum():,.0f})"
    )

    # Best blocked-but-filtered pair per unmatched tuple (zip_near_miss info).
    best_blocked = (
        features.sort_values("name_score", ascending=False, kind="mergesort")
        .drop_duplicates(subset=["private_foundations_df_index"], keep="first")
        .set_index("private_foundations_df_index")
    )

    # Exact normalized name elsewhere in the universe (cross-state geometry).
    by_name = universe.groupby("full_name").groups

    # --- classify each unmatched tuple ---------------------------------
    logger.info("Scanning unmatched tuples for near-miss identities ...")
    state_scan_cache: dict[tuple[str, str], tuple[float, int | None]] = {}
    rows = []
    for idx, t in tuples[~matched].iterrows():
        status, cand_idx, cand_jw = "no_candidate", None, 0.0

        exact_rows = by_name.get(t["full_name"])
        if exact_rows is not None:
            cand_idx = exact_rows[0]
            cand_jw = 1.0
            status = "exact_name_cross_state"
        else:
            key = (t["full_name"], t["addressstate_key"])
            if key not in state_scan_cache:
                state_scan_cache[key] = _best_same_state(universe, *key)
            jw, u_idx = state_scan_cache[key]
            if jw >= NEAR_MISS_JW_MIN and u_idx is not None:
                status, cand_idx, cand_jw = "state_name_variant", u_idx, jw

        blocked = best_blocked.loc[idx] if idx in best_blocked.index else None
        if status == "no_candidate" and blocked is not None and blocked["name_score"] >= 0.70:
            status = "zip_near_miss"
            cand_idx = blocked["basic_fields_df_index"]
            cand_jw = blocked["name_score"]

        cand = universe.loc[cand_idx, _CAND_COLS] if cand_idx is not None else None
        rows.append(
            {
                "dollars": t["dollars"],
                "n_rows": t["n_rows"],
                "status": status,
                "state_is_us": t["addressstate_key"] in _US_STATES,
                "raw_name1": t["raw_name1"],
                "raw_name2": t["raw_name2"],
                "raw_address1": t["raw_address1"],
                "raw_address2": t["raw_address2"],
                "raw_city": t["raw_city"],
                "raw_state": t["raw_state"],
                "raw_zip": t["raw_zip"],
                "candidate_ein": cand["filerein_key"] if cand is not None else "",
                "candidate_name": cand["full_name"] if cand is not None else "",
                "candidate_address": (
                    " ".join(
                        p for p in (cand["address1_key"], cand["address2_key"]) if p
                    )
                    if cand is not None else ""
                ),
                "candidate_city": cand["addresscity_key"] if cand is not None else "",
                "candidate_state": cand["addressstate_key"] if cand is not None else "",
                "candidate_zip": cand["addresszip_key"] if cand is not None else "",
                "candidate_source": cand["source"] if cand is not None else "",
                "candidate_name_jw": round(cand_jw, 4),
                "blocked_best_name": round(float(blocked["name_score"]), 4) if blocked is not None else "",
                "blocked_best_addr": round(float(blocked["addr_score"]), 4) if blocked is not None else "",
                "evidence_url": (
                    f"https://projects.propublica.org/nonprofits/organizations/{cand['filerein_key']}"
                    if cand is not None else ""
                ),
            }
        )

    report = pd.DataFrame(rows).sort_values("dollars", ascending=False)
    report.insert(0, "rank", range(1, len(report) + 1))

    # Cluster counts: one EIN attracting many unmatched tuples is either a
    # rich correction family or (recurrence rule) a matcher-change signal.
    cluster = (
        report[report["candidate_ein"] != ""]
        .groupby("candidate_ein")
        .agg(tuples=("candidate_ein", "size"), dollars=("dollars", "sum"))
        .sort_values("dollars", ascending=False)
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(out_path, index=False)
    logger.info(f"Wrote {len(report):,} candidates to {out_path}")

    print(f"\n=== corrections scout: top {len(tuples):,} tuples by dollars ===")
    print(
        f"already matched: {matched.sum():,} tuples "
        f"(${tuples.loc[matched, 'dollars'].sum():,.0f})"
    )
    print(report["status"].value_counts().to_string())
    print(f"\nunmatched dollars by status:")
    print(report.groupby("status")["dollars"].sum().sort_values(ascending=False)
          .map("${:,.0f}".format).to_string())
    print("\n=== top 20 candidates ===")
    cols = ["rank", "dollars", "status", "raw_name1", "raw_city", "raw_state",
            "candidate_ein", "candidate_name", "candidate_name_jw"]
    with pd.option_context("display.width", 250, "display.max_colwidth", 40):
        print(report.head(20)[cols].to_string(index=False))
    print("\n=== top candidate-EIN clusters (recurrence check) ===")
    print(cluster.head(10).to_string())
    print(f"\nWorklist: {out_path}")
    return 0


# --- Candid labeled-pair detector ---------------------------------------

_DEFAULT_CANDID_OUT = (
    Path(__file__).resolve().parents[1]
    / "data" / "exploratory" / "corrections_candid_candidates.csv"
)

_LABELED_PAIRS_SQL = """
    SELECT funder_ein, recip_ein, MIN(gm_name) AS gm_name
    FROM grantor_recipient_labeled_set
    WHERE filing_type = '990PF'
    GROUP BY funder_ein, recip_ein
"""

# Funder-side tuples for every labeled 990-PF funder. Same population rules
# as the matcher; keys only (raw values are LOWER()-ed into these keys, so a
# correction drafted from them is functionally identical — re-case from the
# filing if preferred).
_LABELED_FUNDER_TUPLES_SQL = f"""
    SELECT
        filerein::text AS funder_ein,
        name1_key, name2_key, address1_key, address2_key,
        addresscity_key, addressstate_key, addresszip_key,
        COUNT(*) AS n_rows,
        SUM({AMT.format(col="sigocpyamoun")}) AS dollars
    FROM public.privategrants_w_column_keys_view
    WHERE taxyear::int >= 2015
      AND NOT (TRIM(name1_key) = '' AND TRIM(name2_key) = '')
      AND concat_ws(' ', name1_key, name2_key) !~* '{PLACEHOLDER_REGEX}'
      AND concat_ws(' ', name1_key, name2_key) !~* '{_SCOUT_JUNK_REGEX}'
      AND filerein::text IN (
          SELECT DISTINCT funder_ein FROM grantor_recipient_labeled_set
          WHERE filing_type = '990PF'
      )
    GROUP BY filerein::text, name1_key, name2_key, address1_key, address2_key,
             addresscity_key, addressstate_key, addresszip_key
"""

_LABELED_RECIP_UNIVERSE_SQL = f"""
    SELECT * FROM ({_UNIVERSE_SQL}) u
    WHERE filerein_key IN (
        SELECT DISTINCT recip_ein FROM grantor_recipient_labeled_set
        WHERE filing_type = '990PF'
    )
"""


_DEFAULT_CANDID_DIGEST_OUT = (
    Path(__file__).resolve().parents[1]
    / "data" / "exploratory" / "corrections_candid_digest.csv"
)

# Trailing tokens whose absence/presence alone should NOT spend a correction
# row: per the recurrence rule these belong to a future normalize_org_name
# change, and they dominate the high-JW band (~60% of it, ~$4.8B measured
# 2026-07-30).
_SUFFIX_ONLY_RE = re.compile(r"\s+(incorporated|inc|the)$")


def _write_candid_digest(
    report: pd.DataFrame, out_path: Path, jw_min: float = 0.95
) -> pd.DataFrame:
    """Distill the pair-grained candid report into the file a curator can
    actually work: one row per recipient EIN, high-confidence tuples only
    (name_jw >= jw_min), suffix-only variants excluded, ranked by dollars.

    The raw report stays on disk as the audit trail; this is the worklist.
    """
    df = report.copy()
    df["dollars"] = df["dollars"].astype(float)
    df["name_jw"] = df["name_jw"].astype(float)
    for col in ("name1_key", "name2_key", "recip_name"):
        df[col] = df[col].fillna("")
    df["tuple_name"] = [
        create_full_name({"name1_key": a, "name2_key": b})
        for a, b in zip(df["name1_key"], df["name2_key"])
    ]
    suffix_only = [
        a != b and _SUFFIX_ONLY_RE.sub("", a) == _SUFFIX_ONLY_RE.sub("", b)
        for a, b in zip(df["tuple_name"], df["recip_name"])
    ]
    kept = df[(df["name_jw"] >= jw_min) & ~pd.Series(suffix_only, index=df.index)]
    kept = kept.sort_values("dollars", ascending=False)
    digest = (
        kept.groupby(["recip_ein", "recip_name"], sort=False)
        .agg(
            dollars=("dollars", "sum"),
            tuples=("tuple_name", "size"),
            funders=("funder_ein", "nunique"),
            best_jw=("name_jw", "max"),
            geometries=("geometry", lambda s: ",".join(sorted(set(s)))),
            example_tuple=("tuple_name", "first"),
            example_city=("addresscity_key", "first"),
            example_state=("addressstate_key", "first"),
            evidence_url=("evidence_url", "first"),
        )
        .reset_index()
        .sort_values("dollars", ascending=False)
    )
    digest.insert(0, "rank", range(1, len(digest) + 1))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    digest.to_csv(out_path, index=False)
    return digest


def _lev_sim(s1: str, s2: str) -> float:
    """recordlinkage's levenshtein_similarity: 1 - dist/max(len)."""
    longest = max(len(s1), len(s2))
    if not longest:
        return 0.0
    return 1 - jellyfish.levenshtein_distance(s1, s2) / longest


def _passes_final(name: float, addr: float, state_eq: bool) -> bool:
    """FINAL_FILTER_RULES tier logic for one already-blockable pair."""
    r = FINAL_FILTER_RULES
    return (
        (name >= r["near_perfect_name_name_min"] and addr >= r["near_perfect_name_addr_min"])
        or (name >= r["near_perfect_addr_name_min"] and addr >= r["near_perfect_addr_addr_min"])
        or (name >= r["good_enough_name_name_min"] and addr >= r["good_enough_name_addr_min"])
        or (name >= r["exact_name_name_min"] and state_eq)
    )


def _tuple_addr(t, cache: dict) -> str:
    if t.Index not in cache:
        cache[t.Index] = create_clean_address(
            {
                "address1_key": t.address1_key,
                "address2_key": t.address2_key,
                "addresscity_key": t.addresscity_key,
                "addressstate_key": t.addressstate_key,
            }
        )
    return cache[t.Index]


def run_candid_scout(out_path: Path, jw_min: float = NEAR_MISS_JW_MIN) -> int:
    config = datamart_config()
    with get_session(config=config) as session:
        conn = session.connection()
        logger.info("Reading Candid 990-PF labeled pairs ...")
        labeled = pd.read_sql_query(text(_LABELED_PAIRS_SQL), conn)
        logger.info(f"{len(labeled):,} labeled pairs; aggregating funder tuples ...")
        tuples = pd.read_sql_query(text(_LABELED_FUNDER_TUPLES_SQL), conn)
        logger.info(f"{len(tuples):,} funder tuples; reading recip universe rows ...")
        identities = pd.read_sql_query(text(_LABELED_RECIP_UNIVERSE_SQL), conn)

    tuples["dollars"] = tuples["dollars"].astype(float).fillna(0.0)
    _clean_like_run(tuples)
    _clean_like_run(identities)
    identities["compare_addr"] = identities.apply(create_clean_address, axis=1)
    logger.info(
        f"{len(identities):,} universe identity rows cover "
        f"{identities['filerein_key'].nunique():,} of "
        f"{labeled['recip_ein'].nunique():,} labeled recipients"
    )

    tuples_by_funder = dict(tuple(tuples.groupby("funder_ein")))
    ids_by_recip = dict(tuple(identities.groupby("filerein_key")))
    good_floor = FINAL_FILTER_RULES["good_enough_name_name_min"]

    counts = {"covered_now": 0, "near_miss": 0, "no_tuple": 0, "funder_no_tuples": 0}
    rows = []
    addr_cache: dict = {}
    for i, pair in enumerate(labeled.itertuples()):
        if i and i % 50000 == 0:
            logger.info(f"... {i:,}/{len(labeled):,} pairs scanned")
        tf = tuples_by_funder.get(pair.funder_ein)
        if tf is None:
            counts["funder_no_tuples"] += 1
            continue

        ids = ids_by_recip.get(pair.recip_ein)
        if ids is not None:
            id_rows = list(ids.itertuples())
            recip_name = id_rows[0].full_name
            evidence = "universe"
        else:
            gm = create_full_name(
                {"name1_key": str(pair.gm_name or "").lower(), "name2_key": ""}
            )
            if len(gm) < 4:
                counts["no_tuple"] += 1
                continue
            id_rows, recip_name, evidence = [], gm, "gm_name"

        # Best JW per unique tuple name against every identity of this recip.
        uniq_names = tf["full_name"].unique()
        id_names = [r.full_name for r in id_rows] or [recip_name]
        jw = {
            n: max(jellyfish.jaro_winkler_similarity(n, idn) for idn in id_names)
            for n in uniq_names
        }
        good_names = {n for n in uniq_names if jw[n] >= good_floor}
        if not good_names:
            counts["no_tuple"] += 1
            continue

        sub = tf[tf["full_name"].isin(good_names)]

        # Reachability now: some blockable (same zip / identical name) pair
        # passes the FINAL tiers against some identity row.
        covered = False
        for t in sub.itertuples():
            for idr in id_rows:
                name_score = jellyfish.jaro_winkler_similarity(t.full_name, idr.full_name)
                blockable = (
                    t.clean_zip == idr.clean_zip or t.full_name == idr.full_name
                )
                if not blockable or name_score < good_floor:
                    continue
                addr_score = _lev_sim(_tuple_addr(t, addr_cache), idr.compare_addr)
                if _passes_final(
                    name_score, addr_score,
                    t.addressstate_key == idr.addressstate_key,
                ):
                    covered = True
                    break
            if covered:
                break
        if covered:
            counts["covered_now"] += 1
            continue

        counts["near_miss"] += 1
        cands = sub[sub["full_name"].map(jw) >= jw_min]
        if cands.empty:
            # reachable-name floor met (0.70+) but below draft confidence;
            # still worth a stub row so the pair isn't silently dropped.
            cands = sub.sort_values("dollars", ascending=False).head(1)
        for t in cands.sort_values("dollars", ascending=False).head(5).itertuples():
            best_id = (
                max(id_rows, key=lambda r: jellyfish.jaro_winkler_similarity(t.full_name, r.full_name))
                if id_rows else None
            )
            if best_id is None:
                geometry = "recip_not_in_universe"
            elif t.clean_zip == best_id.clean_zip:
                geometry = "same_zip"
            elif t.addressstate_key == best_id.addressstate_key:
                geometry = "cross_zip_same_state"
            else:
                geometry = "cross_state"
            rows.append(
                {
                    "dollars": t.dollars,
                    "n_rows": t.n_rows,
                    "funder_ein": pair.funder_ein,
                    "recip_ein": pair.recip_ein,
                    "recip_name": recip_name,
                    "evidence": evidence,
                    "geometry": geometry,
                    "name_jw": round(jw[t.full_name], 4),
                    "name1_key": t.name1_key,
                    "name2_key": t.name2_key,
                    "address1_key": t.address1_key,
                    "address2_key": t.address2_key,
                    "addresscity_key": t.addresscity_key,
                    "addressstate_key": t.addressstate_key,
                    "addresszip_key": t.addresszip_key,
                    "evidence_url": (
                        "https://projects.propublica.org/nonprofits/organizations/"
                        + pair.recip_ein
                    ),
                }
            )

    report = pd.DataFrame(rows).sort_values("dollars", ascending=False)
    report.insert(0, "rank", range(1, len(report) + 1))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(out_path, index=False)

    cluster = (
        report.groupby(["recip_ein", "recip_name"])
        .agg(tuples=("recip_ein", "size"), dollars=("dollars", "sum"))
        .sort_values("dollars", ascending=False)
    )

    print("\n=== candid scout: 990-PF labeled pairs vs current matcher reach ===")
    for k, v in counts.items():
        print(f"{k:>18}: {v:,}")
    print(f"\ncandidate tuples: {len(report):,} "
          f"(${report['dollars'].sum():,.0f} at stake)")
    print("\n=== top 20 candidates ===")
    cols = ["rank", "dollars", "geometry", "name_jw", "recip_ein", "recip_name",
            "name1_key", "addresscity_key", "addressstate_key"]
    with pd.option_context("display.width", 250, "display.max_colwidth", 40):
        print(report.head(20)[cols].to_string(index=False))
    print("\n=== top recipient clusters ===")
    print(cluster.head(15).to_string())

    digest = _write_candid_digest(report, _DEFAULT_CANDID_DIGEST_OUT)
    print(
        f"\n=== digest: {len(digest):,} recipients at jw>=0.95, suffix-only "
        f"variants excluded (${digest['dollars'].sum():,.0f}) ==="
    )
    with pd.option_context("display.width", 250, "display.max_colwidth", 40):
        print(digest.head(15).to_string(index=False))
    print(f"\nRaw pair report (audit trail): {out_path}")
    print(f"Curator worklist: {_DEFAULT_CANDID_DIGEST_OUT}")
    return 0


def main() -> None:
    # The package logger ships without handlers (operators attach their own);
    # as a CLI, attach one so multi-minute runs aren't silent.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--candid", action="store_true",
                        help="Run the Candid labeled-pair detector instead of the "
                             "top-dollar tuple sweep")
    parser.add_argument("--top", type=int, default=1500,
                        help="How many top-dollar tuples to scout (default 1500)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output CSV path (defaults per detector)")
    args = parser.parse_args()
    if args.candid:
        sys.exit(run_candid_scout(out_path=args.out or _DEFAULT_CANDID_OUT))
    sys.exit(run_scout(top=args.top, out_path=args.out or _DEFAULT_OUT))


if __name__ == "__main__":
    main()
