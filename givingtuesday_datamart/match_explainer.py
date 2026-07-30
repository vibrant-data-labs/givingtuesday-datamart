"""Explain why a privategrants row did (or didn't) match a specific filer EIN.

Two entry points:

* ``explain_row(recipient_ein, sigocpyrbnbn1=..., ...)`` — the workhorse.
  Takes raw ``sigocpy*`` column values exactly as they appear on a
  privategrants row (copy-paste from the table or the capture explorer),
  finds that row's normalized key tuple via
  ``privategrants_w_column_keys_view`` (so normalization is byte-identical
  to what the matcher sees — nothing is re-implemented here), and scores
  it against every identity row the EIN has in the filer universe.
* ``explain_family(recipient_ein, grant_name)`` — same, but for every
  grant tuple whose name matches an ILIKE fragment; useful to see how one
  fix generalizes across a recipient's misspelling family.

For every (grant tuple, filer identity row) pair both report, using the
matching pipeline's own cleaning/scoring code:

  1. Would blocking even generate the pair? (exact clean_zip OR exact
     normalized full_name — there is no state block; state only gates the
     cross-zip exact-name tier.)
  2. The four scores (zip/name/addr/state).
  3. Which final filter tier passes or comes closest, and by how much.

Optionally append a hypothetical corrections row to the filer's identity
rows, so "would this correction fix it?" is answerable before editing
data/corrections/org_identities.csv (corrections_preflight then validates
the committed row against the full competitive universe).

Usage:

    # why didn't this exact row match SONG?
    python -m givingtuesday_datamart.match_explainer 611274170 \
        --row 'SOUTHERNERS ON NEW GROUND||PO BOX 11250||ATLANTA|GA|30310'

    # the whole misspelling family, and a dry-run fix
    python -m givingtuesday_datamart.match_explainer 611274170 \
        --name "southerners on new ground" \
        --try-correction "SOUTHERNERS ON NEW GROUND|PO BOX 11250|ATLANTA|GA|30310"

Scope: pairs against the target EIN only. A pair that matches here can
still lose the final best-match-per-recipient resolution to a
better-scoring row of a *different* EIN (name_score weighted 2x) — the
``current_match_ein`` column shows who actually won in the last completed
matching run.
"""

from __future__ import annotations

import pandas as pd
import recordlinkage
from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart.grant_matching import (
    FINAL_FILTER_RULES,
    clean_zip,
    create_clean_address,
    create_full_name,
    filter_match_rules,
)
from givingtuesday_datamart.ingestion import datamart_config

# Raw privategrants columns a caller can pin a row down with, in the
# name1/name2/address1/address2/city/state/zip order used everywhere else.
RAW_GRANT_COLS = [
    "sigocpyrbnbn1",  # recipient business name line 1
    "sigocpyrbnbn2",  # recipient business name line 2
    "sigocpyrfaal1",  # address line 1
    "sigocpyrfaal2",  # address line 2
    "sigocpyrfaci",   # city
    "sigocpyrfapo",   # state
    "sigocpyrfapc",   # zip
]

_KEY_COLS = ["name1_key", "name2_key", "address1_key", "address2_key",
             "addresscity_key", "addressstate_key", "addresszip_key"]

_UNIVERSE_SQL = """
    SELECT filerein_key, name1_key, name2_key, address1_key, address2_key,
           addresscity_key, addressstate_key, addresszip_key, source
    FROM (
        SELECT *, 'basic_fields' AS source
        FROM public.basic_fields_unique_names_view
        WHERE filerein_key = :ein
        UNION ALL
        SELECT *, 'basic_fields_pf' AS source
        FROM public.basic_fields_pf_unique_names_view
        WHERE filerein_key = :ein
        UNION ALL
        SELECT *, 'correction' AS source
        FROM public.corrections_unique_names_view
        WHERE filerein_key = :ein
    ) arms
"""

# Who won each grant tuple in the last completed matching run. Joined on
# the same row identity the pipeline's SELECT INTO join preserves.
_CURRENT_MATCH_SQL = """
    SELECT w.recipeint_ein_key AS current_match_ein, k.name1_key, k.name2_key,
           k.address1_key, k.address2_key, k.addresscity_key,
           k.addressstate_key, k.addresszip_key
    FROM public.privategrants_w_recipients w
    JOIN public.privategrants_w_column_keys_view k
      ON k.filerein = w.filerein
     AND k.taxyear IS NOT DISTINCT FROM w.taxyear
     AND k.sigocpyamoun IS NOT DISTINCT FROM w.sigocpyamoun
     AND k.sigocpyrbnbn1 IS NOT DISTINCT FROM w.sigocpyrbnbn1
     AND k.sigocpyrpnam IS NOT DISTINCT FROM w.sigocpyrpnam
     AND k.sigocpyrfaal1 IS NOT DISTINCT FROM w.sigocpyrfaal1
    WHERE {where_clause}
    GROUP BY 2, 3, 4, 5, 6, 7, 8, 1
"""


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.fillna("").reset_index(drop=True)
    df["clean_zip"] = df["addresszip_key"].apply(clean_zip)
    df["full_name"] = df.apply(create_full_name, axis=1)
    df["compare_addr"] = df.apply(create_clean_address, axis=1)
    return df


def _tier_report(row: pd.Series) -> str:
    """Human-readable pass/closest-miss summary for one scored pair."""
    r = FINAL_FILTER_RULES
    name, addr, state = row["name_score"], row["addr_score"], row["state_score"]
    tiers = {
        "near_perfect_name": (
            name >= r["near_perfect_name_name_min"] and addr >= r["near_perfect_name_addr_min"],
            f"name {name:.3f}/{r['near_perfect_name_name_min']}, addr {addr:.3f}/{r['near_perfect_name_addr_min']}",
        ),
        "near_perfect_addr": (
            name >= r["near_perfect_addr_name_min"] and addr >= r["near_perfect_addr_addr_min"],
            f"name {name:.3f}/{r['near_perfect_addr_name_min']}, addr {addr:.3f}/{r['near_perfect_addr_addr_min']}",
        ),
        "good_enough": (
            name >= r["good_enough_name_name_min"] and addr >= r["good_enough_name_addr_min"],
            f"name {name:.3f}/{r['good_enough_name_name_min']}, addr {addr:.3f}/{r['good_enough_name_addr_min']}",
        ),
        "exact_name_state": (
            name >= r["exact_name_name_min"] and state == 1,
            f"name {name:.3f}/{r['exact_name_name_min']}, same-state {bool(state)}",
        ),
    }
    passed = [t for t, (ok, _) in tiers.items() if ok]
    if passed:
        return "MATCHES via " + ", ".join(passed)
    return " | ".join(f"{t}: {detail}" for t, (_, detail) in tiers.items())


def _score_pairs(
    universe: pd.DataFrame, grants: pd.DataFrame, current: pd.DataFrame
) -> pd.DataFrame:
    """Score ALL (filer row x grant tuple) pairs and annotate each with
    whether real blocking would have generated it. The real run never
    scores unblocked pairs — scoring them anyway is what lets the
    explainer say "NOT BLOCKED" instead of silently omitting the pair.
    Affordable because the slice is one EIN x one row family.
    """
    universe = _prep(universe)
    grants = _prep(grants)

    indexer = recordlinkage.Index()
    indexer.full()
    links = indexer.index(universe, grants)
    compare = recordlinkage.Compare()
    compare.exact("clean_zip", "clean_zip", label="zip_score")
    compare.string("full_name", "full_name", method="jarowinkler", label="name_score")
    compare.string("compare_addr", "compare_addr", method="levenshtein", label="addr_score")
    compare.exact("addressstate_key", "addressstate_key", label="state_score")
    features = compare.compute(links, universe, grants)
    features.index = features.index.set_names(["universe_idx", "grant_idx"])
    features = features.reset_index()

    name_equal = (
        universe.loc[features["universe_idx"], "full_name"].to_numpy()
        == grants.loc[features["grant_idx"], "full_name"].to_numpy()
    )
    features["blocked_via"] = [
        "zip+name" if (z and n) else "zip" if z else "name" if n else "NOT BLOCKED"
        for z, n in zip(features["zip_score"] == 1, name_equal)
    ]

    kept = filter_match_rules(features_df=features, **FINAL_FILTER_RULES)
    features["passes_filter"] = features.index.isin(kept.index)
    features["would_match"] = features["passes_filter"] & (
        features["blocked_via"] != "NOT BLOCKED"
    )
    features["explanation"] = features.apply(_tier_report, axis=1)

    out = features.merge(
        universe[_KEY_COLS + ["source", "full_name"]].add_prefix("filer_"),
        left_on="universe_idx", right_index=True,
    ).merge(
        grants[_KEY_COLS + ["full_name", "n_grant_rows"]].add_prefix("grant_"),
        left_on="grant_idx", right_index=True,
    ).merge(
        current, how="left",
        left_on=[f"grant_{c}" for c in _KEY_COLS], right_on=_KEY_COLS,
    )
    return out.sort_values(
        ["grant_n_grant_rows", "name_score"], ascending=False
    )[[
        "grant_full_name", "grant_address1_key", "grant_addresscity_key",
        "grant_addressstate_key", "grant_addresszip_key", "grant_n_grant_rows",
        "filer_full_name", "filer_address1_key", "filer_addresszip_key",
        "filer_source", "blocked_via", "zip_score", "name_score", "addr_score",
        "state_score", "would_match", "explanation", "current_match_ein",
    ]]


_HYP_FIELDS = ["name1", "address1", "city", "state", "zip"]


def _parse_hypothetical(hyp: dict | str | None) -> dict | None:
    """Accept the hypothetical correction as either a dict (keys name1/
    address1/city/state/zip, optional name2/address2) or the CLI's
    pipe-delimited string 'NAME|ADDRESS1|CITY|STATE|ZIP'."""
    if hyp is None:
        return None
    if isinstance(hyp, str):
        parts = [p.strip() for p in hyp.split("|")]
        if len(parts) != len(_HYP_FIELDS):
            raise ValueError(
                "hypothetical_correction string needs exactly 5 pipe-separated "
                f"fields 'NAME|ADDRESS1|CITY|STATE|ZIP', got {len(parts)}: {hyp!r}"
            )
        return dict(zip(_HYP_FIELDS, parts))
    missing = set(_HYP_FIELDS) - set(hyp)
    if missing:
        raise ValueError(
            f"hypothetical_correction dict missing keys {sorted(missing)}"
        )
    return hyp


def _load_universe(conn, recipient_ein: str, hypothetical: dict | None) -> pd.DataFrame:
    universe = pd.read_sql_query(
        text(_UNIVERSE_SQL), conn, params={"ein": recipient_ein}
    )
    if hypothetical is not None:
        universe = pd.concat([universe, pd.DataFrame([{
            "filerein_key": recipient_ein,
            "name1_key": hypothetical["name1"].strip().lower(),
            "name2_key": hypothetical.get("name2", "").strip().lower(),
            "address1_key": hypothetical["address1"].strip().lower(),
            "address2_key": hypothetical.get("address2", "").strip().lower(),
            "addresscity_key": hypothetical["city"].strip().lower(),
            "addressstate_key": hypothetical["state"].strip().lower(),
            "addresszip_key": hypothetical["zip"].strip(),
            "source": "hypothetical",
        }])], ignore_index=True)
    if universe.empty:
        raise ValueError(
            f"EIN {recipient_ein} has no identity rows in the filer universe — "
            "it never filed a 990/990-PF in the staging window and has no "
            "correction row. Blocking can never reach it; a corrections row "
            "is the only fix."
        )
    return universe


def explain_row(
    recipient_ein: str,
    hypothetical_correction: dict | str | None = None,
    **raw_cols: str,
) -> pd.DataFrame:
    """Explain one privategrants row (or the set of rows matching the raw
    values you provide) against ``recipient_ein``.

    ``raw_cols``: any subset of RAW_GRANT_COLS with the values exactly as
    they appear on the row (case/whitespace-insensitive). Provided columns
    must match; omitted columns are wildcards. The row's normalized key
    tuple comes from ``privategrants_w_column_keys_view``, so this only
    works for rows that actually exist in privategrants — which is the
    point: you're diagnosing a real row, with the matcher's real
    normalization.
    """
    hypothetical_correction = _parse_hypothetical(hypothetical_correction)
    unknown = set(raw_cols) - set(RAW_GRANT_COLS)
    if unknown:
        raise ValueError(f"Unknown raw columns {sorted(unknown)}; use {RAW_GRANT_COLS}")
    provided = {c: v for c, v in raw_cols.items() if v is not None}
    if not provided:
        raise ValueError("Provide at least one raw column to identify the row(s).")

    conds = " AND ".join(
        f"upper(trim(coalesce({c}, ''))) = upper(trim(:{c}))" for c in provided
    )
    grants_sql = f"""
        SELECT {', '.join(_KEY_COLS)}, COUNT(*) AS n_grant_rows
        FROM public.privategrants_w_column_keys_view
        WHERE {conds}
        GROUP BY {', '.join(str(i + 1) for i in range(len(_KEY_COLS)))}
    """
    key_conds = " AND ".join(f"k.{c} = :k_{c}" for c in _KEY_COLS)

    with get_session(config=datamart_config()) as session:
        conn = session.connection()
        grants = pd.read_sql_query(text(grants_sql), conn, params=provided)
        if grants.empty:
            raise ValueError(
                "No privategrants row matches those values — check them "
                "against the raw table (values are compared "
                "case/whitespace-insensitively)."
            )
        universe = _load_universe(conn, recipient_ein, hypothetical_correction)
        current_parts = [
            pd.read_sql_query(
                text(_CURRENT_MATCH_SQL.format(where_clause=key_conds)), conn,
                params={f"k_{c}": g[c] for c in _KEY_COLS},
            )
            for _, g in grants.iterrows()
        ]
        current = pd.concat(current_parts, ignore_index=True)

    return _score_pairs(universe, grants, current)


def explain_family(
    recipient_ein: str,
    grant_name: str,
    hypothetical_correction: dict | str | None = None,
) -> pd.DataFrame:
    """Explain every grant tuple whose name matches ``grant_name`` (ILIKE,
    %% added) against ``recipient_ein`` — the family view."""
    hypothetical_correction = _parse_hypothetical(hypothetical_correction)
    name_pat = f"%{grant_name}%"
    family_clause = "(k.name1_key || ' ' || k.name2_key) ILIKE :name_pat"
    with get_session(config=datamart_config()) as session:
        conn = session.connection()
        grants = pd.read_sql_query(text(f"""
            SELECT {', '.join(_KEY_COLS)}, COUNT(*) AS n_grant_rows
            FROM public.privategrants_w_column_keys_view
            WHERE (name1_key || ' ' || name2_key) ILIKE :name_pat
            GROUP BY {', '.join(str(i + 1) for i in range(len(_KEY_COLS)))}
        """), conn, params={"name_pat": name_pat})
        if grants.empty:
            raise ValueError(f"No privategrants tuples with name ILIKE '{name_pat}'")
        universe = _load_universe(conn, recipient_ein, hypothetical_correction)
        current = pd.read_sql_query(
            text(_CURRENT_MATCH_SQL.format(where_clause=family_clause)),
            conn, params={"name_pat": name_pat},
        )
    return _score_pairs(universe, grants, current)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Explain why privategrants rows did/didn't match a filer EIN."
    )
    parser.add_argument("recipient_ein", help="filer EIN the grants should map to")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--row",
        help=("one grant row's raw values: 'NAME1|NAME2|ADDR1|ADDR2|CITY|STATE|ZIP' "
              "(leave fields empty to wildcard them)"),
    )
    mode.add_argument("--name", help="recipient name fragment (ILIKE, %% added)")
    parser.add_argument(
        "--try-correction",
        help="hypothetical corrections row: 'NAME|ADDRESS1|CITY|STATE|ZIP'",
    )
    parser.add_argument(
        "--all-pairs", action="store_true",
        help="print every pair (default: best filer row per grant tuple)",
    )
    args = parser.parse_args()

    try:
        hyp = _parse_hypothetical(args.try_correction)
    except ValueError as exc:
        parser.error(str(exc))

    if args.row:
        parts = args.row.split("|")
        if len(parts) != len(RAW_GRANT_COLS):
            parser.error(
                f"--row needs {len(RAW_GRANT_COLS)} pipe-separated fields "
                f"({'|'.join(RAW_GRANT_COLS)}); leave a field empty to wildcard it"
            )
        raw = {c: v.strip() for c, v in zip(RAW_GRANT_COLS, parts) if v.strip()}
        result = explain_row(args.recipient_ein, hypothetical_correction=hyp, **raw)
    else:
        result = explain_family(
            args.recipient_ein, args.name, hypothetical_correction=hyp
        )

    if not args.all_pairs:
        # Best filer row per grant tuple: a matching pair if one exists,
        # else the closest by name so the explanation shows the nearest miss.
        result = result.sort_values(
            ["would_match", "name_score"], ascending=False
        ).drop_duplicates(
            subset=["grant_full_name", "grant_address1_key", "grant_addresszip_key"]
        )

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.max_colwidth", 45)
    print(result.to_string(index=False))
    n_match = result["would_match"].sum()
    n_rows_covered = result.loc[result["would_match"], "grant_n_grant_rows"].sum()
    print(
        f"\n{n_match}/{len(result)} grant tuples would match EIN "
        f"{args.recipient_ein} ({n_rows_covered:,.0f} underlying grant rows)."
    )
