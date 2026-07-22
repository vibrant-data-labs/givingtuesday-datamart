"""Marimo explorer for the grant capture-priority lists — 990 and 990-PF.

Two tabs, one per form type:

  * "990 · Schedule I"  — data/exploratory/sched_i_capture_priority.csv
    (built by sched_i_capture_priority.sql). Drill-down: basic_fields filing
    row + Schedule I line items.
  * "990-PF"            — data/exploratory/pf_capture_priority.csv
    (built by pf_capture_priority.sql). Drill-down: basic_fields_pf filing
    row + privategrants line items, each tagged placeholder / matched
    (via privategrants_w_recipients).

In both tabs: filter/search the priority list, click a funder-year row, and
the drill-down opens beneath — capture history with the clicked year marked,
the deduped filing row (headline amounts + source-XML link, full row in an
accordion), and the grant line items anchored to the clicked taxyear.

Filters and row selections are kept per-tab, so switching tabs doesn't lose
state. Run from the repo root (so givingtuesday_datamart is importable):

    pyenv activate vdl-tools-312
    marimo edit givingtuesday_datamart/exploratory/explore_sched_i_capture.py
"""

import marimo

__generated_with = "0.23.14"
app = marimo.App(width="full")


@app.cell
def _():
    from pathlib import Path

    import marimo as mo
    import pandas as pd
    from sqlalchemy import text

    from givingtuesday_datamart._internal.db import get_configuration, get_session

    # Prefer the read-only gt_datamart_ro credentials when present; the
    # notebook only ever SELECTs, so it shouldn't hold admin credentials.
    RO_CONFIG = Path.home() / "dev" / "vdl" / "config.gt_datamart_ro.ini"

    def _get_config():
        if RO_CONFIG.exists():
            return get_configuration(configpath=RO_CONFIG)
        config = get_configuration()
        config["postgres"]["database"] = "gt_datamart"
        return config

    def run_query(sql: str, params: dict | None = None) -> pd.DataFrame:
        with get_session(config=_get_config()) as session:
            return pd.read_sql_query(text(sql), session.connection(), params=params)

    REPO_ROOT = Path(__file__).resolve().parents[2]
    DOLLARS = "{:,.0f}"
    # Same placeholder detection as pf_capture_priority.sql: NAME fields only —
    # "AVAILABLE UPON REQUEST" in the address column is a real grant with an
    # unmatchable address, not an aggregate row.
    PLACEHOLDER_NAME_REGEX = (
        r"(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|^\s*see\s*$"
    )
    return DOLLARS, PLACEHOLDER_NAME_REGEX, REPO_ROOT, mo, pd, run_query


@app.cell
def _(REPO_ROOT, pd):
    priority_si = pd.read_csv(
        REPO_ROOT / "data" / "exploratory" / "sched_i_capture_priority.csv",
        dtype={"filerein": str, "taxyear": str},
    )
    priority_pf = pd.read_csv(
        REPO_ROOT / "data" / "exploratory" / "pf_capture_priority.csv",
        dtype={"filerein": str, "taxyear": str},
    )
    gt_rows = pd.read_csv(
        REPO_ROOT / "data" / "exploratory" / "gt_team_priority.csv",
        dtype={"filerein": str, "taxyear": str},
    )
    gt_funders = pd.read_csv(
        REPO_ROOT / "data" / "exploratory" / "gt_team_priority_by_funder.csv",
        dtype={"filerein": str},
    )
    return gt_funders, gt_rows, priority_pf, priority_si


@app.cell
def _(mo):
    mo.accordion({
        "📖 Issue class & dollar column definitions": mo.md(
            """
Every class compares extracted line items against the filing's **declared**
grants-paid total — 990 Part IX line 1 (`graallpaitot`, gated on the Part IV
Schedule I checkbox), 990-PF Part I line 25 col (d) (`arecgpdcprps`).

| class | form | meaning | canonical example |
|---|---|---|---|
| `no_rows` | both | Declared grant dollars, **zero** line items extracted for that year | Fidelity: ~$10B/yr, no rows |
| `partial_rows` | both | Line items exist but sum to **<90%** of declared | Dollar General 2021: 451 rows, $12.9M of $16.4M |
| `aggregate_placeholder` | PF | ≥50% of declared sits in placeholder-**name** rows ("SEE ATTACHMENT") — totals fake-reconcile because the placeholder row carries the full amount | Siegel: 1 row = $26.9M |
| `ein_missing` | 990 | Itemization complete but rows lack recipient EINs (filer omission — not GT-fixable) | Dollar General 2015–20 |
| `individual_grants` | PF | ≥50% of declared goes to named **persons** (scholarships, patient assistance) — structurally unmappable | Disney 2022, BMS |
| `foreign_or_nonfiler` | PF | ≥50% to recipients that can't be 990 filers: non-US zip, `NC:`/`GOV:` status, equivalency determination | Gates Fdn → WHO, Pfizer |
| `unmatched_recipients` | PF | <90% of **matchable** dollars matched — VDL matcher gap (zip-blocking misses, PF-recipient universe gap) | Bloomberg → JHU; Gates Trust |
| `ok` | both | ≥90% of declared (990) / matchable (PF) dollars mapped | |

**Dollar columns** — `unmappable_dollars` = declared − EIN-mapped (990) /
matched (PF): the honest total gap. `matchable_dollars` (PF) = dollars in
rows an org-matcher could hit: org recipient, US-shaped zip, filer-capable
status, non-placeholder. `recoverable_dollars` (PF) = matchable − matched:
what VDL matching work could actually recover. `dollars_at_issue` (GT tab) =
declared for `no_rows`/`aggregate_placeholder`, declared − itemized for
`partial_rows`. `lag_risk` (GT tab) = 990 tail-missing rows in 2023+ —
probably GT processing lag, not a data error.

**Audience split** — GT hand-off tab = data-capture classes only
(`no_rows`, `aggregate_placeholder`, `partial_rows`). Matching
(`unmatched_recipients`), structural (`individual_grants`,
`foreign_or_nonfiler`), and filer-omission (`ein_missing`) classes stay
VDL-internal.
"""
        )
    })
    return


@app.cell
def _(mo):
    # Tab bar only — content renders conditionally below so that switching
    # tabs never re-creates (and therefore never resets) the per-tab UI state.
    form_tab = mo.ui.tabs(
        {"990 · Schedule I": mo.md(""), "990-PF": mo.md(""), "GT hand-off": mo.md("")}
    )
    form_tab
    return (form_tab,)


@app.cell
def _(mo, priority_si):
    issue_si = mo.ui.multiselect(
        options=sorted(priority_si["primary_issue"].unique()),
        value=["no_rows", "partial_rows", "ein_missing"],
        label="primary_issue",
    )
    year_si = mo.ui.multiselect(
        options=sorted(priority_si["taxyear"].unique()),
        value=sorted(priority_si["taxyear"].unique()),
        label="taxyear",
    )
    min_si = mo.ui.number(value=1_000_000, step=100_000, label="min unmappable $")
    search_si = mo.ui.text(placeholder="name or EIN contains…", label="search")
    return issue_si, min_si, search_si, year_si


@app.cell
def _(DOLLARS, issue_si, min_si, mo, priority_si, search_si, year_si):
    filtered_si = priority_si[
        priority_si["primary_issue"].isin(issue_si.value)
        & priority_si["taxyear"].isin(year_si.value)
        & (priority_si["unmappable_dollars"] >= (min_si.value or 0))
    ]
    if search_si.value:
        _needle = search_si.value.strip().lower()
        filtered_si = filtered_si[
            filtered_si["name"].str.lower().str.contains(_needle, na=False)
            | filtered_si["filerein"].str.contains(_needle, na=False)
        ]
    filtered_si = filtered_si.sort_values("unmappable_dollars", ascending=False)

    row_picker_si = mo.ui.table(
        filtered_si[
            ["filerein", "name", "taxyear", "declared_amt", "n_rows",
             "ein_mapped_dollars", "unmappable_dollars", "pct_dollars_unmappable",
             "primary_issue"]
        ],
        selection="single",
        page_size=15,
        format_mapping={
            "declared_amt": DOLLARS,
            "ein_mapped_dollars": DOLLARS,
            "unmappable_dollars": DOLLARS,
        },
        label=f"{len(filtered_si):,} funder-years — click a row to drill down",
    )
    return (row_picker_si,)


@app.cell
def _(DOLLARS, mo, priority_si, row_picker_si, run_query):
    if row_picker_si.value is None or len(row_picker_si.value) == 0:
        sel_ein_si = sel_year_si = None
        detail_si = mo.md("*Click a row above to open the funder drill-down.*")
    else:
        _sel = row_picker_si.value.iloc[0]
        sel_ein_si = str(_sel["filerein"])
        sel_year_si = str(_sel["taxyear"])

        _history = (
            priority_si[priority_si["filerein"] == sel_ein_si]
            .sort_values("taxyear")
            .assign(**{"▶": lambda d: (d["taxyear"] == sel_year_si).map({True: "▶", False: ""})})
        )[
            ["▶", "taxyear", "declared_amt", "n_rows", "itemized_dollars",
             "ein_mapped_dollars", "unmappable_dollars", "pct_dollars_unmappable",
             "primary_issue"]
        ]

        _bf = run_query(
            """
            SELECT * FROM basic_fields
            WHERE filerein = :ein AND taxyear::text = :year
            ORDER BY _ingested_at DESC, filesha256
            LIMIT 1
            """,
            {"ein": sel_ein_si, "year": sel_year_si},
        )
        if _bf.empty:
            _bf_section = mo.md(f"*No basic_fields row for {sel_ein_si} / {sel_year_si}.*")
        else:
            _row = _bf.iloc[0]
            _transposed = _bf.T.reset_index()
            _transposed.columns = ["column", "value"]
            _bf_section = mo.vstack([
                mo.md(
                    f"**basic_fields {sel_year_si}** — declared grants to domestic orgs "
                    f"(`graallpaitot`): **${float(_row['graallpaitot'] or 0):,.0f}** · "
                    f"Schedule I checkbox (`grantoororga`): `{_row['grantoororga']}` · "
                    f"[source XML]({_row['url']})"
                ),
                mo.accordion(
                    {"basic_fields row — all columns":
                     mo.ui.table(_transposed, selection=None, page_size=30)}
                ),
            ])

        detail_si = mo.vstack([
            mo.md(
                f"## {_sel['name']} — EIN {sel_ein_si}\n"
                f"[ProPublica profile]"
                f"(https://projects.propublica.org/nonprofits/organizations/{sel_ein_si})"
            ),
            mo.md("**Capture history (all required years in the priority list)**"),
            mo.ui.table(
                _history,
                selection=None,
                page_size=12,
                format_mapping={
                    "declared_amt": DOLLARS,
                    "itemized_dollars": DOLLARS,
                    "ein_mapped_dollars": DOLLARS,
                    "unmappable_dollars": DOLLARS,
                },
            ),
            _bf_section,
        ])
    return detail_si, sel_ein_si, sel_year_si


@app.cell
def _(mo, run_query, sel_ein_si, sel_year_si):
    if sel_ein_si is None:
        sched_year_si = None
        year_strip_si = None
    else:
        _counts = run_query(
            """
            SELECT taxyear::text AS taxyear, COUNT(*) AS n_rows
            FROM grants_to_domestic_organizations
            WHERE filerein = :ein
            GROUP BY 1 ORDER BY 1
            """,
            {"ein": sel_ein_si},
        )
        _years = _counts["taxyear"].tolist()
        sched_year_si = mo.ui.dropdown(
            options=["all years"] + _years,
            value=sel_year_si if sel_year_si in _years else "all years",
            label="Schedule I taxyear",
        )
        year_strip_si = mo.hstack(
            [
                sched_year_si,
                mo.md(
                    "rows/yr: "
                    + (
                        " · ".join(
                            f"{_y}: {_n:,}"
                            for _y, _n in zip(_counts["taxyear"], _counts["n_rows"])
                        )
                        or "**none — this funder has no Schedule I rows at all**"
                    )
                ),
            ],
            wrap=True,
        )
    return sched_year_si, year_strip_si


@app.cell
def _(DOLLARS, mo, run_query, sched_year_si, sel_ein_si):
    if sel_ein_si is None or sched_year_si is None:
        rows_si = None
    else:
        _year_clause = "" if sched_year_si.value == "all years" else "AND taxyear::text = :year"
        _params = {"ein": sel_ein_si}
        if sched_year_si.value != "all years":
            _params["year"] = sched_year_si.value
        _rows = run_query(
            f"""
            SELECT taxyear::text AS taxyear,
                   rtrnbbnline11 AS recipient_name,
                   rteinorecipi  AS recipient_ein,
                   rectabaddcit  AS city,
                   rectabaddsta  AS state,
                   CASE WHEN retaamofcagr ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN retaamofcagr::numeric END AS cash,
                   CASE WHEN rtaoncassist ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN rtaoncassist::numeric END AS non_cash,
                   retapuofgrra  AS purpose
            FROM grants_to_domestic_organizations
            WHERE filerein = :ein {_year_clause}
            ORDER BY taxyear, cash DESC NULLS LAST
            LIMIT 5000
            """,
            _params,
        )
        _truncated = " (showing first 5,000)" if len(_rows) == 5000 else ""
        rows_si = mo.vstack([
            mo.md(f"**Schedule I rows — {sched_year_si.value}** · {len(_rows):,} rows{_truncated}"),
            (
                mo.ui.table(
                    _rows,
                    selection=None,
                    page_size=25,
                    format_mapping={"cash": DOLLARS, "non_cash": DOLLARS},
                )
                if len(_rows)
                else mo.md(
                    "*No Schedule I rows for this selection — "
                    "which is exactly why this funder-year is on the list.*"
                )
            ),
        ])
    return (rows_si,)


@app.cell
def _(
    detail_si,
    form_tab,
    issue_si,
    min_si,
    mo,
    row_picker_si,
    rows_si,
    search_si,
    year_si,
    year_strip_si,
):
    (
        mo.vstack(
            [
                mo.hstack([issue_si, year_si, min_si, search_si], wrap=True),
                row_picker_si,
                *[
                    _c
                    for _c in (detail_si, year_strip_si, rows_si)
                    if _c is not None
                ],
            ]
        )
        if form_tab.value == "990 · Schedule I"
        else None
    )
    return


@app.cell(hide_code=True)
def _(mo):
    # Metric definitions surfaced at the top of the PF tab — the priority and
    # capture-history tables use these columns. Sourced from
    # data/exploratory/pf_capture_priority.sql (header comments there are the
    # authoritative version).
    defs_pf = mo.md(
        """
    **PF capture metrics** — Part XV has no recipient-EIN column, so *mapped* always means
    *matched by VDL name/address matching into `privategrants_w_recipients`*:

    | metric | meaning |
    |---|---|
    | `declared_amt` | grants paid that the filer itself declared — Part I line 25 col (d) (`arecgpdcprps` in `basic_fields_pf`) |
    | `matchable_dollars` | itemized dollars a matcher could plausibly map to a 990 filer: org name present, numeric amount, US-shaped zip, filer-capable status (not NC / GOV / equivalency), not a placeholder-name row |
    | `matched_dollars` | dollars in rows that actually matched into `privategrants_w_recipients` |
    | `unmappable_dollars` | `declared_amt` − `matched_dollars` — the honest total gap, including dollars nothing could ever map (persons, foreign orgs, placeholders) |
    | `recoverable_dollars` | `max(matchable_dollars − matched_dollars, 0)` — the slice of the gap matching work could actually close; sort by this for matcher triage |
    | `person_dollars` / `nonfiler_foreign_dollars` | structurally unmappable buckets: grants to named individuals (scholarships, patient assistance) / recipients that can never file a 990 (foreign address, NC / GOV status, equivalency determination) |
    """
    )
    return


@app.cell
def _(mo, priority_pf):
    issue_pf = mo.ui.multiselect(
        options=sorted(priority_pf["primary_issue"].unique()),
        value=["no_rows", "aggregate_placeholder", "partial_rows", "unmatched_recipients"],
        label="primary_issue",
    )
    year_pf = mo.ui.multiselect(
        options=sorted(priority_pf["taxyear"].unique()),
        value=sorted(priority_pf["taxyear"].unique()),
        label="taxyear",
    )
    min_pf = mo.ui.number(value=1_000_000, step=100_000, label="min unmappable $")
    search_pf = mo.ui.text(placeholder="name or EIN contains…", label="search")
    return issue_pf, min_pf, search_pf, year_pf


@app.cell
def _(DOLLARS, issue_pf, min_pf, mo, priority_pf, search_pf, year_pf):
    filtered_pf = priority_pf[
        priority_pf["primary_issue"].isin(issue_pf.value)
        & priority_pf["taxyear"].isin(year_pf.value)
        & (priority_pf["unmappable_dollars"] >= (min_pf.value or 0))
    ]
    if search_pf.value:
        _needle = search_pf.value.strip().lower()
        filtered_pf = filtered_pf[
            filtered_pf["name"].str.lower().str.contains(_needle, na=False)
            | filtered_pf["filerein"].str.contains(_needle, na=False)
        ]
    filtered_pf = filtered_pf.sort_values("unmappable_dollars", ascending=False)

    row_picker_pf = mo.ui.table(
        filtered_pf[
            ["filerein", "name", "taxyear", "declared_amt", "n_rows",
             "placeholder_rows", "matchable_dollars", "matched_dollars",
             "unmappable_dollars", "recoverable_dollars", "primary_issue"]
        ],
        selection="single",
        page_size=15,
        format_mapping={
            "declared_amt": DOLLARS,
            "matchable_dollars": DOLLARS,
            "matched_dollars": DOLLARS,
            "unmappable_dollars": DOLLARS,
            "recoverable_dollars": DOLLARS,
        },
        label=f"{len(filtered_pf):,} funder-years — click a row to drill down",
    )
    return (row_picker_pf,)


@app.cell
def _(DOLLARS, mo, priority_pf, row_picker_pf, run_query):
    if row_picker_pf.value is None or len(row_picker_pf.value) == 0:
        sel_ein_pf = sel_year_pf = None
        detail_pf = mo.md("*Click a row above to open the funder drill-down.*")
    else:
        _sel = row_picker_pf.value.iloc[0]
        sel_ein_pf = str(_sel["filerein"])
        sel_year_pf = str(_sel["taxyear"])

        _history = (
            priority_pf[priority_pf["filerein"] == sel_ein_pf]
            .sort_values("taxyear")
            .assign(**{"▶": lambda d: (d["taxyear"] == sel_year_pf).map({True: "▶", False: ""})})
        )[
            ["▶", "taxyear", "declared_amt", "n_rows", "placeholder_rows", "noaddr_rows",
             "person_dollars", "nonfiler_foreign_dollars", "matchable_dollars",
             "matched_dollars", "recoverable_dollars", "primary_issue"]
        ]

        _bf = run_query(
            """
            SELECT * FROM basic_fields_pf
            WHERE filerein = :ein AND taxyear::text = :year
            ORDER BY _ingested_at DESC, filesha256
            LIMIT 1
            """,
            {"ein": sel_ein_pf, "year": sel_year_pf},
        )
        if _bf.empty:
            _bf_section = mo.md(f"*No basic_fields_pf row for {sel_ein_pf} / {sel_year_pf}.*")
        else:
            _row = _bf.iloc[0]
            _transposed = _bf.T.reset_index()
            _transposed.columns = ["column", "value"]
            _bf_section = mo.vstack([
                mo.md(
                    f"**basic_fields_pf {sel_year_pf}** — declared contributions/gifts/grants "
                    f"paid, charitable disbursements (`arecgpdcprps`): "
                    f"**${float(_row['arecgpdcprps'] or 0):,.0f}** · "
                    f"per books (`arecprexpnss`): ${float(_row['arecprexpnss'] or 0):,.0f} · "
                    f"[source XML]({_row['url']})"
                ),
                mo.accordion(
                    {"basic_fields_pf row — all columns":
                     mo.ui.table(_transposed, selection=None, page_size=30)}
                ),
            ])

        detail_pf = mo.vstack([
            mo.md(
                f"## {_sel['name']} — EIN {sel_ein_pf}\n"
                f"[ProPublica profile]"
                f"(https://projects.propublica.org/nonprofits/organizations/{sel_ein_pf})"
            ),
            mo.md("**Capture history (all years with declared grants paid)**"),
            mo.ui.table(
                _history,
                selection=None,
                page_size=12,
                format_mapping={
                    "declared_amt": DOLLARS,
                    "person_dollars": DOLLARS,
                    "nonfiler_foreign_dollars": DOLLARS,
                    "matchable_dollars": DOLLARS,
                    "matched_dollars": DOLLARS,
                    "recoverable_dollars": DOLLARS,
                },
            ),
            _bf_section,
        ])
    return detail_pf, sel_ein_pf, sel_year_pf


@app.cell
def _(mo, run_query, sel_ein_pf, sel_year_pf):
    if sel_ein_pf is None:
        pf_year = None
        year_strip_pf = None
    else:
        _counts = run_query(
            """
            SELECT pg.taxyear, pg.n_rows, COALESCE(w.matched_rows, 0) AS matched_rows
            FROM (
                SELECT taxyear::text AS taxyear, COUNT(*) AS n_rows
                FROM privategrants WHERE filerein = :ein GROUP BY 1
            ) pg
            LEFT JOIN (
                SELECT taxyear::text AS taxyear, COUNT(*) AS matched_rows
                FROM privategrants_w_recipients WHERE filerein = :ein GROUP BY 1
            ) w USING (taxyear)
            ORDER BY 1
            """,
            {"ein": sel_ein_pf},
        )
        _years = _counts["taxyear"].tolist()
        pf_year = mo.ui.dropdown(
            options=["all years"] + _years,
            value=sel_year_pf if sel_year_pf in _years else "all years",
            label="privategrants taxyear",
        )
        year_strip_pf = mo.hstack(
            [
                pf_year,
                mo.md(
                    "rows (matched)/yr: "
                    + (
                        " · ".join(
                            f"{_r.taxyear}: {_r.n_rows:,} ({_r.matched_rows:,})"
                            for _r in _counts.itertuples()
                        )
                        or "**none — this funder has no privategrants rows at all**"
                    )
                ),
            ],
            wrap=True,
        )
    return pf_year, year_strip_pf


@app.cell
def _(DOLLARS, PLACEHOLDER_NAME_REGEX, mo, pf_year, run_query, sel_ein_pf):
    if sel_ein_pf is None or pf_year is None:
        rows_pf = None
    else:
        _year_clause = "" if pf_year.value == "all years" else "AND p.taxyear::text = :year"
        _params = {"ein": sel_ein_pf}
        if pf_year.value != "all years":
            _params["year"] = pf_year.value
        _rows = run_query(
            f"""
            SELECT p.taxyear::text AS taxyear,
                   p.sigocpyrpnam  AS person_name,
                   p.sigocpyrbnbn1 AS business_name,
                   p.sigocpyrfaal1 AS address,
                   p.sigocpyrfaci  AS city,
                   p.sigocpyrfapo  AS state,
                   CASE WHEN p.sigocpyamoun ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN p.sigocpyamoun::numeric END AS amount,
                   p.sigocpypogoc  AS purpose,
                   CASE WHEN concat_ws(' ', p.sigocpyrpnam, p.sigocpyrbnbn1, p.sigocpyrbnbn2)
                        ~* '{PLACEHOLDER_NAME_REGEX}'
                        THEN '⚑ placeholder' ELSE '' END AS placeholder,
                   COALESCE(m.recipeint_ein_key, '') AS matched_recipient_ein
            FROM privategrants p
            LEFT JOIN LATERAL (
                SELECT w.recipeint_ein_key
                FROM privategrants_w_recipients w
                WHERE w.filerein = p.filerein
                  AND w.taxyear IS NOT DISTINCT FROM p.taxyear
                  AND w.sigocpyamoun  IS NOT DISTINCT FROM p.sigocpyamoun
                  AND w.sigocpyrbnbn1 IS NOT DISTINCT FROM p.sigocpyrbnbn1
                  AND w.sigocpyrpnam  IS NOT DISTINCT FROM p.sigocpyrpnam
                  AND w.sigocpyrfaal1 IS NOT DISTINCT FROM p.sigocpyrfaal1
                LIMIT 1
            ) m ON true
            WHERE p.filerein = :ein {_year_clause}
            ORDER BY p.taxyear, amount DESC NULLS LAST
            LIMIT 5000
            """,
            _params,
        )
        _truncated = " (showing first 5,000)" if len(_rows) == 5000 else ""
        rows_pf = mo.vstack([
            mo.md(f"**privategrants rows — {pf_year.value}** · {len(_rows):,} rows{_truncated}"),
            (
                mo.ui.table(
                    _rows,
                    selection=None,
                    page_size=25,
                    format_mapping={"amount": DOLLARS},
                )
                if len(_rows)
                else mo.md(
                    "*No privategrants rows for this selection — "
                    "which is exactly why this funder-year is on the list.*"
                )
            ),
        ])
    return (rows_pf,)


@app.cell
def _(
    detail_pf,
    form_tab,
    issue_pf,
    min_pf,
    mo,
    row_picker_pf,
    rows_pf,
    search_pf,
    year_pf,
    year_strip_pf,
):
    (
        mo.vstack(
            [
                mo.hstack([issue_pf, year_pf, min_pf, search_pf], wrap=True),
                row_picker_pf,
                *[
                    _c
                    for _c in (detail_pf, year_strip_pf, rows_pf)
                    if _c is not None
                ],
            ]
        )
        if form_tab.value == "990-PF"
        else None
    )
    return


# ---------------------------------------------------------------------------
# GT hand-off — data-capture issues only (no matching / structural classes)
# ---------------------------------------------------------------------------


@app.cell
def _(gt_rows, mo):
    form_gt = mo.ui.multiselect(
        options=sorted(gt_rows["form_type"].unique()),
        value=sorted(gt_rows["form_type"].unique()),
        label="form_type",
    )
    issue_gt = mo.ui.multiselect(
        options=sorted(gt_rows["primary_issue"].unique()),
        value=sorted(gt_rows["primary_issue"].unique()),
        label="issue",
    )
    include_lag_gt = mo.ui.checkbox(
        value=False, label="include lag-risk rows (tail-missing 2023+, likely GT processing lag)"
    )
    min_gt = mo.ui.number(value=1_000_000, step=100_000, label="min $ at issue")
    search_gt = mo.ui.text(placeholder="name or EIN contains…", label="search")
    return form_gt, include_lag_gt, issue_gt, min_gt, search_gt


@app.cell
def _(DOLLARS, form_gt, gt_funders, gt_rows, include_lag_gt, issue_gt, min_gt,
      mo, search_gt):
    _rows = gt_rows[
        gt_rows["form_type"].isin(form_gt.value)
        & gt_rows["primary_issue"].isin(issue_gt.value)
    ]
    if not include_lag_gt.value:
        _rows = _rows[~_rows["lag_risk"]]
    _eins = set(_rows["filerein"])

    funders_view = gt_funders[
        gt_funders["filerein"].isin(_eins)
        & gt_funders["form_type"].isin(form_gt.value)
        & (gt_funders["total_at_issue"] >= (min_gt.value or 0))
    ]
    if search_gt.value:
        _needle = search_gt.value.strip().lower()
        funders_view = funders_view[
            funders_view["name"].str.lower().str.contains(_needle, na=False)
            | funders_view["filerein"].str.contains(_needle, na=False)
        ]
    funders_view = funders_view.sort_values("total_at_issue", ascending=False)

    funder_picker_gt = mo.ui.table(
        funders_view[
            ["form_type", "filerein", "name", "years", "issues",
             "funder_pattern", "total_at_issue"]
        ],
        selection="single",
        page_size=15,
        format_mapping={"total_at_issue": DOLLARS},
        label=(
            f"{len(funders_view):,} EINs · "
            f"${funders_view['total_at_issue'].sum()/1e9:.1f}B at issue — "
            f"click an EIN for its funder-year rows"
        ),
    )
    return (funder_picker_gt,)


@app.cell
def _(DOLLARS, funder_picker_gt, gt_rows, mo):
    if funder_picker_gt.value is None or len(funder_picker_gt.value) == 0:
        gt_detail = mo.md(
            "*Click an EIN above to see its funder-year rows. For row-level "
            "drill-down (filing row, grant line items), search the same EIN "
            "in the 990 · Schedule I or 990-PF tab.*"
        )
    else:
        _sel = funder_picker_gt.value.iloc[0]
        _yr = (
            gt_rows[gt_rows["filerein"] == str(_sel["filerein"])]
            .sort_values("taxyear")
        )[
            ["taxyear", "primary_issue", "declared_amt", "n_rows",
             "dollars_at_issue", "funder_pattern", "lag_risk"]
        ]
        gt_detail = mo.vstack([
            mo.md(
                f"### {_sel['name']} — EIN {_sel['filerein']} ({_sel['form_type']})\n"
                f"For row-level drill-down, search this EIN in the "
                f"**{_sel['form_type'] if _sel['form_type'] == '990-PF' else '990 · Schedule I'}** tab."
            ),
            mo.ui.table(
                _yr,
                selection=None,
                page_size=12,
                format_mapping={"declared_amt": DOLLARS, "dollars_at_issue": DOLLARS},
            ),
        ])
    return (gt_detail,)


@app.cell
def _(form_gt, form_tab, funder_picker_gt, gt_detail, include_lag_gt,
      issue_gt, min_gt, mo, search_gt):
    (
        mo.vstack(
            [
                mo.md(
                    "**Data-capture issues only** — `unmatched_recipients` "
                    "(VDL matching), `individual_grants` / `foreign_or_nonfiler` "
                    "(structural), and `ein_missing` (filer omission) are "
                    "excluded by construction."
                ),
                mo.hstack(
                    [form_gt, issue_gt, min_gt, search_gt, include_lag_gt],
                    wrap=True,
                ),
                funder_picker_gt,
                gt_detail,
            ]
        )
        if form_tab.value == "GT hand-off"
        else None
    )
    return


if __name__ == "__main__":
    app.run()
