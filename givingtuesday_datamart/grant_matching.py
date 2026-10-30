import concurrent.futures as cf
import hashlib
import io
import json
import multiprocessing
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import boto3
import botocore.config
import numpy as np
import pandas as pd
import jellyfish
import pyarrow.parquet as pq
import recordlinkage
from sqlalchemy import text
from tqdm import tqdm

from givingtuesday_datamart._internal import parquet_cache as pqc
from givingtuesday_datamart._internal.address_cleaning import ADDRESS_PATTERN, USPS_MAP, create_clean_address
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart._internal.parquet_cache import _decode_json as _pqc_decode_json
from givingtuesday_datamart.current_grants import (
    _MATCHING_VIEW_TABLES,
    _TABLES as _CURRENT_TABLES,
    apply_build_memory,
    build_current_relations,
    with_dependents,
)
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.canonical.build import (
    CANONICAL_BUILDS_TABLE,
    ensure_canonical_meta,
    latest_successful_run_ids,
)
from givingtuesday_datamart.placeholder_recovery import address as recovery_address
from givingtuesday_datamart.placeholder_recovery import classifier as recovery_classifier
from givingtuesday_datamart.placeholder_recovery import loader as recovered_loader
from givingtuesday_datamart.placeholder_recovery import view as recovered_view

# Derived from USPS Publication 28 - Postal Addressing Standards


# --- 1. SQL DDL HELPERS ---

# Government and public-institution name prefixes. Two readers: the BMF
# alias view below leaves out SORT_NAME lines that are one (the IRS puts the
# host institution on a chapter's record: "UNIVERSITY OF KANSAS" is the
# SORT_NAME of a Psi Chi chapter), and the precision guard further down
# refuses non-exact wins for grant rows that are one. Both regexes come from
# this tuple: the Python one for create_full_name's output, the SQL one for
# Postgres (\M = end of word).
GOVERNMENT_NAME_PREFIXES = (
    "city",
    "county",
    "town",
    "village",
    "borough",
    "township",
    "parish",
    "municipality",
    "state",
    "commonwealth",
    "government",
    "department",
    "university",
    "univ",
    "regents of the university",
    "board of regents of the university",
    "trustees of the university",
    "board of trustees of the university",
)
_GOVERNMENT_NAME_PATTERN = (
    "^(district of columbia|(" + "|".join(GOVERNMENT_NAME_PREFIXES) + ") of)"
)
GOVERNMENT_NAME_RE = re.compile(_GOVERNMENT_NAME_PATTERN + r"\b")
GOVERNMENT_NAME_SQL_RE = _GOVERNMENT_NAME_PATTERN + r"\M"

# Shared with the gate. SQL uses PostgreSQL word boundaries; the Python
# twin below uses the same phrases on raw business-name fields. The second
# arm is a name that is the word "see" with nothing else but punctuation:
# "SEE", "SEE.", "(SEE)", "SEE -". October matched 59 bare "SEE" rows to
# the BMF organizations named SEE (850645402, 854054788), and the
# punctuated spellings clean to the same name. The arm is the recovery
# classifier's (``SEE_ALONE``), so a row the view leaves out of matching is
# a row the work list counts and the recovery reads. The pattern decides
# which grant tuples the view holds, so an edit to it moves row positions:
# MATCHING_INPUT_SHAPE_VERSION and the pin in
# tests/test_production_recipient_ranking.py move with it.
PLACEHOLDER_REGEX = (
    r"(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|" + recovery_classifier.SEE_ALONE
)
_PLACEHOLDER_NAME_RE = re.compile(PLACEHOLDER_REGEX.replace(r"\y", r"\b"), re.IGNORECASE)
# These were previously in sql_queries/unique_fields_for_grants.sql. Lifted into
# Python so the matching pipeline owns its own preconditions and postconditions
# in one place. The views must exist before matching reads them; unioned_grants
# must be rebuilt after matching writes privategrants_w_recipients.

# 8 views built on top of `public.privategrants_current_w_recovered`,
# `public.basic_fields`, `public.basic_fields_pf`, `public.eo_bmf` and
# `public.corrections_org_identities`. The `_w_column_keys_view` views
# normalize names/addresses (lowercase, 5-digit zip) into `*_key` columns the
# matching pipeline blocks/joins on. The `_unique_names_view` views collapse
# to DISTINCT (key tuple) for orgs that filed in 2015+.
#
# The recipient-match universe is the UNION of 990 filers (basic_fields),
# 990-PF filers (basic_fields_pf), the IRS Business Master File's names
# (eo_bmf) and the corrections registry. PF-to-PF grants are legal and common
# (e.g. the Gates Trust's ~$6.8B/yr transfer to the Gates Foundation), and a
# 990-only universe makes every PF-recipient grant structurally unmatchable —
# ~$32B of 2020+ grant dollars carry a "PF:" recipient status alone.

# The recovered grants the matcher takes in are those of one policy, the one
# the view of current and recovered grants shows: it is written into the
# view's definition, and nowhere else. A run never guesses it. The rows are
# a matching input like the staging tables, so the policy and a digest of
# the rows are in the checkpoint prefix.
# What a matching run creates: eight views, the corrections table, the join
# table and the two output tables.
_CREATED = (
    "privategrants_w_column_keys_view",
    "privategrants_unique_names_view",
    "basic_fields_w_column_keys_view",
    "basic_fields_unique_names_view",
    "basic_fields_pf_w_column_keys_view",
    "basic_fields_pf_unique_names_view",
    "eo_bmf_unique_names_view",
    "corrections_unique_names_view",
    "corrections_org_identities",
    "pf_grant_matching_temp_table",
    "privategrants_w_recipients",
    "unioned_grants",
)


@dataclass(frozen=True)
class Relations:
    """What a matching run reads its grants from, and what it names the
    relations it writes. The defaults are production's.

    ``prefix`` goes before the name of everything the run creates: the
    eight views, the corrections table, the join table,
    ``privategrants_w_recipients`` and ``unioned_grants``. A run with a
    prefix leaves production's relations alone, so a change can be proved
    on a subset beside them (``matching_subset``).

    ``grants`` is what the first view reads: ``privategrants_current`` with
    the recovered grants in place of the placeholder rows. ``schedule_i`` is
    the 990 side of ``unioned_grants``. Both are inputs and take no prefix.
    """

    prefix: str = ""
    grants: str = recovered_view.VIEW
    schedule_i: str = "grants_to_domestic_organizations_current"

    def __post_init__(self):
        for name in (self.prefix or "_", self.grants, self.schedule_i):
            recovered_view.check_identifier(name)

    def of(self, name: str) -> str:
        """The run's name for one of the relations it creates."""
        if name not in _CREATED:
            raise KeyError(f"{name} is not a relation a matching run creates")
        return f"{self.prefix}{name}"


_VIEW_DDL = [
    # DROP first, not CREATE OR REPLACE: this view's column set changes
    # with the relation it reads (the provenance columns of
    # privategrants_current, then the labels of the recovered grants), and
    # CREATE OR REPLACE VIEW cannot reorder or insert columns. The CASCADE
    # takes privategrants_unique_names_view with it; both are recreated by
    # the entries below.
    "DROP VIEW IF EXISTS public.{privategrants_w_column_keys_view} CASCADE",
    """
    CREATE OR REPLACE VIEW public.{privategrants_w_column_keys_view} AS (
        -- Reads the filing-version-deduped relation (issue #33), NOT raw
        -- staging: raw privategrants carries every filing version's block
        -- plus GT's 2024-batch whole-block doubles. Reads it with the
        -- recovered grants in place of the placeholder rows
        -- (placeholder_recovery.view). current_grants.py rebuilds
        -- privategrants_current at the start of every matching run, and
        -- the view of current and recovered grants is created after it.
        --
        -- The city, state and zip keys are '' where the column is NULL,
        -- as the name and address keys are. The matches come back from
        -- pandas with '' for a missing value and join on equality, so a
        -- row with a NULL key never joined: no row of
        -- privategrants_w_recipients had one, and every recovered grant
        -- has a NULL city.
        SELECT
            *,
            CASE WHEN sigocpyrbnbn1 IS NULL THEN '' ELSE LOWER(sigocpyrbnbn1) END name1_key,
            CASE WHEN sigocpyrbnbn2 IS NULL THEN '' ELSE LOWER(sigocpyrbnbn2) END name2_key,
            CASE WHEN sigocpyrfaal1 IS NULL THEN '' ELSE LOWER(sigocpyrfaal1) END address1_key,
            CASE WHEN sigocpyrfaal2 IS NULL THEN '' ELSE LOWER(sigocpyrfaal2) END address2_key,
            COALESCE(LOWER(sigocpyrfaci), '') addresscity_key,
            COALESCE(LOWER(sigocpyrfapo), '') addressstate_key,
            COALESCE(LOWER(LEFT(sigocpyrfapc, 5)), '') addresszip_key
        FROM public.{grants}
    )
    """,
    """
    CREATE OR REPLACE VIEW public.{privategrants_unique_names_view} AS (
        SELECT
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key,
            -- A row of the tuple whose country is not the US: the
            -- recipient's foreign address, which fills the US address
            -- columns (a province in the state key, a postal code in the
            -- zip key). Our extract writes the IRS country code on every
            -- foreign row; GT's rows carry USA on domestic rows or nothing
            -- at all, so on them the state key alone says (no_us_address).
            -- The name-only tier takes a tuple with no US address.
            bool_or(UPPER(TRIM(COALESCE(sigocaffrfaco, ''))) NOT IN ('', 'US', 'USA')) AS foreign_country
        FROM public.{privategrants_w_column_keys_view}
        WHERE taxyear::int >= 2015
          -- Rows with no business name (person-named scholarship / patient
          -- assistance grants) cannot pass any match rule: every filter tier
          -- requires name_score >= 0.70, and Jaro-Winkler against an empty
          -- string is 0. Pruning them here drops ~16% of recipient tuples
          -- (~12% of candidate pairs) with zero recall impact.
          AND NOT (TRIM(name1_key) = '' AND TRIM(name2_key) = '')
          AND NOT (concat_ws(' ', name1_key, name2_key) ~* '{placeholder_regex}')
        GROUP BY
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
        -- ORDER BY makes row order deterministic so chunk checkpoints
        -- (which key on integer DataFrame position) stay valid across
        -- re-runs against the same upstream data.
        ORDER BY
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
    )
    """,
    """
    CREATE OR REPLACE VIEW public.{basic_fields_w_column_keys_view} AS (
        SELECT
            *,
            CASE WHEN filerein IS NULL THEN '' ELSE LOWER(filerein::text) END filerein_key,
            CASE WHEN filername1 IS NULL THEN '' ELSE LOWER(filername1) END name1_key,
            CASE WHEN filername2 IS NULL THEN '' ELSE LOWER(filername2) END name2_key,
            CASE WHEN filerus1 IS NULL THEN '' ELSE LOWER(filerus1) END address1_key,
            CASE WHEN filerus2 IS NULL THEN '' ELSE LOWER(filerus2) END address2_key,
            LOWER(fileruscity) addresscity_key,
            LOWER(filerusstate) addressstate_key,
            LOWER(LEFT(fileruszip::text, 5)) addresszip_key
        FROM public.basic_fields
    )
    """,
    # basic_fields_pf shares the filer header column names with basic_fields,
    # so the PF views mirror the 990 ones verbatim apart from the source table.
    """
    CREATE OR REPLACE VIEW public.{basic_fields_pf_w_column_keys_view} AS (
        SELECT
            *,
            CASE WHEN filerein IS NULL THEN '' ELSE LOWER(filerein::text) END filerein_key,
            CASE WHEN filername1 IS NULL THEN '' ELSE LOWER(filername1) END name1_key,
            CASE WHEN filername2 IS NULL THEN '' ELSE LOWER(filername2) END name2_key,
            CASE WHEN filerus1 IS NULL THEN '' ELSE LOWER(filerus1) END address1_key,
            CASE WHEN filerus2 IS NULL THEN '' ELSE LOWER(filerus2) END address2_key,
            LOWER(fileruscity) addresscity_key,
            LOWER(filerusstate) addressstate_key,
            LOWER(LEFT(fileruszip::text, 5)) addresszip_key
        FROM public.basic_fields_pf
    )
    """,
    """
    CREATE OR REPLACE VIEW public.{basic_fields_pf_unique_names_view} AS (
        SELECT
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
        FROM public.{basic_fields_pf_w_column_keys_view}
        WHERE taxyear::int >= 2015
        GROUP BY
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
        -- ORDER BY makes row order deterministic so chunk checkpoints
        -- (which key on integer DataFrame position) stay valid across
        -- re-runs against the same upstream data.
        ORDER BY
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
    )
    """,
    """
    CREATE OR REPLACE VIEW public.{basic_fields_unique_names_view} AS (
        SELECT
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
        FROM public.{basic_fields_w_column_keys_view} bf
        WHERE taxyear::int >= 2015
        GROUP BY
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
        -- ORDER BY makes row order deterministic so chunk checkpoints
        -- (which key on integer DataFrame position) stay valid across
        -- re-runs against the same upstream data.
        ORDER BY
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
    )
    """,
    # IRS Business Master File aliases (docs/bmf-assessment.md): the IRS name
    # of record and, where set, the SORT_NAME line, each as its own alias row
    # at the BMF address of record, keyed by EIN. Two rows rather than one
    # concatenated name because the two lines are different names for the
    # org, not two lines of one name: for group-exemption subordinates NAME
    # is the parent ("FRATERNAL ORDER OF EAGLES") and SORT_NAME the unit
    # ("3741 AERIE"); for DBAs SORT_NAME is the trading name. Jaro-Winkler
    # weights the prefix, so a grant row naming the unit would never reach a
    # concatenation that starts with the parent. SORT_NAME lines that name a
    # trustee or a bank ("WELLS FARGO BANK NA TTEE": 3% of them) are left
    # out — a grant row naming a bank must not resolve to a trust's EIN.
    # Two more cuts, both for the same reason — the IRS writes the HOST
    # institution on a record, and through the cross-zip exact-name tier
    # every grant to the host in the state would resolve to that record at
    # name 1.0, which no score rule can catch (docs/matching-precision-guards.md):
    #   * SORT_NAME lines that are a government or public-institution name
    #     (GOVERNMENT_NAME_PREFIXES; 1,154 of 400,614, nearly all a bare
    #     "UNIVERSITY OF KANSAS" on a Psi Chi chapter's, a Beta Gamma Sigma
    #     chapter's or a Marine Corps League department's record);
    #   * NAME lines that are a government or public-institution name on a
    #     record whose own name is the SORT_NAME: group-exemption
    #     subordinates (affiliation 9 — "REGENTS OF THE UNIVERSITY OF
    #     MINNESOTA" / "GOLDEN GOPHERS 4-H CLUB", "DEPARTMENT OF NEW YORK VFW
    #     AUXILIARY" / "BREWSTER POST 672") and records outside 501(c)(1)/(3)
    #     ("UNIVERSITY OF WASHINGTON" / "PROFESSIONAL STAFF ORGANIZATION", a
    #     (c)(5) union); 4,004 rows. The institutions themselves are
    #     independent (c)(3) records or group parents and keep their NAME
    #     row: "UNIVERSITY OF UTAH", "REGENTS OF THE UNIVERSITY OF
    #     CALIFORNIA" / "OFFICE OF THE PRESIDENT", "STATE OF WEST VIRGINIA" /
    #     "WEST VIRGINIA UNIVERSITY" (affiliation 6).
    # No taxyear filter: every BMF row is a currently recognized org. The
    # same key expressions as basic_fields_w_column_keys_view; the matcher's
    # clean_zip pads and truncates on top.
    f"""
    CREATE OR REPLACE VIEW public.{{eo_bmf_unique_names_view}} AS (
        SELECT
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
        FROM (
            SELECT
                LOWER(ein) filerein_key,
                LOWER(name) name1_key,
                '' name2_key,
                CASE WHEN street IS NULL THEN '' ELSE LOWER(street) END address1_key,
                '' address2_key,
                LOWER(city) addresscity_key,
                LOWER(state) addressstate_key,
                LOWER(LEFT(zip, 5)) addresszip_key
            FROM public.eo_bmf
            WHERE name IS NOT NULL
              AND NOT (
                    sort_name IS NOT NULL
                    AND (affiliation = '9' OR COALESCE(subsection, '') NOT IN ('01', '03'))
                    AND regexp_replace(LOWER(name), '^the ', '') ~ '{GOVERNMENT_NAME_SQL_RE}'
              )
            UNION ALL
            SELECT
                LOWER(ein),
                LOWER(sort_name),
                '',
                CASE WHEN street IS NULL THEN '' ELSE LOWER(street) END,
                '',
                LOWER(city),
                LOWER(state),
                LOWER(LEFT(zip, 5))
            FROM public.eo_bmf
            WHERE sort_name IS NOT NULL
              AND sort_name <> name
              AND sort_name !~ '\\m(TTEE|TRUSTEE|TRUSTEES|BANK|N A)\\M'
              AND regexp_replace(LOWER(sort_name), '^the ', '') !~ '{GOVERNMENT_NAME_SQL_RE}'
        ) keyed
        GROUP BY
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
        ORDER BY
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
    )
    """,
    # Corrections registry (docs/corrections-plan.md): human-verified identity
    # rows ("this EIN is also known by this name at this address") UNIONed into
    # the filer universe alongside the unique-names views above. The CSV
    # stores raw verbatim values; ALL normalization happens here, using the
    # same key expressions as basic_fields_w_column_keys_view — keep the two
    # in lockstep so curator-entered rows can't silently normalize differently
    # from filed rows.
    """
    CREATE OR REPLACE VIEW public.{corrections_unique_names_view} AS (
        SELECT
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
        FROM (
            SELECT
                CASE WHEN recipient_ein IS NULL THEN '' ELSE LOWER(recipient_ein) END filerein_key,
                CASE WHEN name1 IS NULL THEN '' ELSE LOWER(name1) END name1_key,
                CASE WHEN name2 IS NULL THEN '' ELSE LOWER(name2) END name2_key,
                CASE WHEN address1 IS NULL THEN '' ELSE LOWER(address1) END address1_key,
                CASE WHEN address2 IS NULL THEN '' ELSE LOWER(address2) END address2_key,
                LOWER(city) addresscity_key,
                LOWER(state) addressstate_key,
                LOWER(LEFT(zip, 5)) addresszip_key
            FROM public.{corrections_org_identities}
        ) keyed
        GROUP BY
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
        ORDER BY
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
    )
    """,
]


def _view_ddl(relations: "Relations") -> list[str]:
    """The eight views, under the run's names."""
    names = {name: relations.of(name) for name in _CREATED}
    return [ddl.format(grants=relations.grants, placeholder_regex=PLACEHOLDER_REGEX, **names) for ddl in _VIEW_DDL]


# --- 1b. CORRECTIONS REGISTRY LOADER ---
# The authoring surface is a CSV in git (PR review = audit trail); the
# pipeline replaces public.corrections_org_identities from it at the start
# of every matching run. See docs/corrections-plan.md.

_CORRECTIONS_CSV_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "corrections" / "org_identities.csv"
)

_CORRECTIONS_COLUMNS = [
    "recipient_ein",
    "name1",
    "name2",
    "address1",
    "address2",
    "city",
    "state",
    "zip",
    "evidence_url",
    "added_by",
    "added_date",
    "note",
]

# All-TEXT, matching the staging-table convention. Created lazily (IF NOT
# EXISTS) rather than dropped/recreated: corrections_unique_names_view
# depends on it, and DROP TABLE would take the view down with it.
_CORRECTIONS_TABLE = "corrections_org_identities"
_CORRECTIONS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS public.{table} (
    recipient_ein TEXT,
    name1 TEXT,
    name2 TEXT,
    address1 TEXT,
    address2 TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    evidence_url TEXT,
    added_by TEXT,
    added_date TEXT,
    note TEXT
)
"""


def _corrections_content_hash() -> str:
    """First 8 hex chars of the SHA-256 of the corrections CSV's raw bytes.

    Joins the checkpoint prefix (see _resolve_checkpoint_prefix): chunk
    checkpoints reference dataframe rows by integer position, and correction
    rows sort into the deterministic ORDER BY and shift positions — so ANY
    edit to the CSV must force a clean recompute, same as a source re-ingest.
    """
    return hashlib.sha256(_CORRECTIONS_CSV_PATH.read_bytes()).hexdigest()[:8]


def _read_corrections_csv(path: Path | None = None) -> pd.DataFrame:
    """Read and validate the corrections CSV (``path``, the registry by
    default). Raises on any bad row.

    Values are raw and verbatim (normalization is the view's job), but
    structural mistakes — a truncated EIN, a missing provenance field —
    should kill the run loudly rather than author a dead or wrong row.

    The state is a two-letter code, or, for a recipient abroad, its
    province or country as the grant row writes it ("OXFORDSHIRE"): the
    keys of a foreign grant hold what the filer typed (``no_us_address``).
    A US state spelled out or punctuated ("New York", "N.Y.") is refused,
    as a dead row: its grants carry the code.
    """
    path = _CORRECTIONS_CSV_PATH if path is None else path
    if not path.exists():
        raise RuntimeError(
            f"Corrections CSV not found at {path}. "
            "It is tracked in git (data/corrections/org_identities.csv) — "
            "a missing file means a broken checkout, not 'no corrections'."
        )
    # keep_default_na=False: values are curator-entered strings; an org
    # legitimately named "NA" (or an empty note) must stay a string, not
    # become NaN.
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(df.columns) != _CORRECTIONS_COLUMNS:
        raise RuntimeError(
            f"Corrections CSV columns {list(df.columns)} != expected "
            f"{_CORRECTIONS_COLUMNS} (order matters; see docs/corrections-plan.md)"
        )

    errors = []
    # start=2: line numbers as seen in the file (line 1 is the header).
    for line_no, row in enumerate(df.itertuples(index=False), start=2):
        if not re.fullmatch(r"\d{9}", row.recipient_ein.strip()):
            errors.append(
                f"line {line_no}: recipient_ein {row.recipient_ein!r} is not 9 digits"
            )
        if not row.name1.strip():
            errors.append(f"line {line_no}: name1 is empty")
        state = row.state.strip()
        if not (re.fullmatch(r"[A-Za-z]{2}", state) or (state and not us_state(pd.Series([state])).iloc[0])):
            errors.append(
                f"line {line_no}: state {row.state!r} is not a 2-letter code or a foreign province as written"
            )
        if not row.zip.strip():
            errors.append(f"line {line_no}: zip is empty")
        # Provenance discipline: a correction without evidence/author/date is
        # unauditable — reject it even though the matcher wouldn't care.
        for col in ("evidence_url", "added_by", "added_date"):
            if not getattr(row, col).strip():
                errors.append(f"line {line_no}: {col} is empty")
        if row.added_date.strip() and not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", row.added_date.strip()
        ):
            errors.append(
                f"line {line_no}: added_date {row.added_date!r} is not YYYY-MM-DD"
            )
    if errors:
        raise RuntimeError(
            "Corrections CSV failed validation:\n" + "\n".join(errors)
        )
    return df


def _load_corrections(connection, relations: Relations = Relations()) -> int:
    """Replace public.corrections_org_identities from the in-repo CSV.

    TRUNCATE + append rather than to_sql(if_exists="replace"): "replace"
    would DROP the table, which fails once corrections_unique_names_view
    depends on it. Returns the number of correction rows loaded.
    """
    df = _read_corrections_csv()
    table = relations.of(_CORRECTIONS_TABLE)
    connection.execute(text(_CORRECTIONS_TABLE_DDL.format(table=table)))
    connection.execute(text(f"TRUNCATE public.{table}"))
    df.to_sql(
        table,
        connection,
        schema="public",
        if_exists="append",
        index=False,
    )
    logger.info(
        f"Loaded {len(df)} correction rows into public.{table} "
        f"(content hash {_corrections_content_hash()})"
    )
    return len(df)


# Union of (a) matched private foundation grants written to
# public.privategrants_w_recipients and (b) Schedule I grants from 990 filers.
# Column aliases preserved verbatim from the original SQL so downstream
# consumers see the same shape; the columns added with input shape 3 come
# last.
#
# The amount is rounded to the dollar. No amount in privategrants_current
# has a decimal point, and a plain ::bigint cast of the text fails on the
# first recovered grant that has one: the attachments print cents on 10,359
# of the rows loaded from the frame. Every amount in this table is whole
# dollars, so the recovered ones are rounded to match; the cents stay in
# privategrants_recovered.
#
# What a product can tell about a row: where it came from (`row_source`:
# privategrants_current, placeholder_recovery, or the Schedule I relation),
# and for a recovered grant whether its page was flagged (`page_verdict`),
# whether the filer marked the list as grants to individuals, and whether
# the list holds more than the grants paid on line 25. `match_source` is
# the arm of the universe the matched filer's row came from, and
# `match_tier` what the match rests on: the address and the name, the exact
# name and the state, or the name alone, where `match_name_words` is the
# number of words in the name.
#
# A recovered grant also carries its key in privategrants_recovered (the
# filing's object id, the page, the line of the reading), which leads to the
# page it was read from. The key keeps its rows apart too: the UNION below
# keeps one of several identical rows, and a list repeats a row for every
# payment of the same amount to the same recipient, 77,078 times on the
# frame. The page's kind and where the state came from stay in
# privategrants_w_recipients.
_UNIONED_GRANTS_DDL = """
DROP TABLE IF EXISTS public.{unioned_grants};
SELECT *
INTO public.{unioned_grants}
FROM (
    SELECT
        filerein::text AS granter_ein,
        filername1 AS granter_name,
        filername2 AS granter_name2,
        filesha256,
        url,
        taxyear::int AS taxyear,
        taxperbegin::timestamp AS taxperbegin,
        taxperend::timestamp AS taxperend,
        recipeint_ein_key::text AS grantee_ein,
        sigocpyrpnam AS grantee_person_name,
        sigocpyrbnbn1 AS grantee_organization_name1,
        sigocpyrbnbn2 AS grantee_organization_name2,
        sigocpyrfaal1 AS grantee_address1,
        sigocpyrfaal2 AS grantee_address2,
        sigocpyrfaci AS grantee_city,
        sigocpyrfapo AS grantee_state,
        sigocpyrfapc AS grantee_zip,
        ROUND(sigocpyamoun::numeric)::bigint AS grant_amount,
        sigocpypogoc AS grant_purpose,
        sigocpyrfsta AS grant_status,
        sigocpyrrela AS grant_relationship,
        match_source,
        match_tier,
        match_name_words,
        row_source,
        page_verdict,
        filer_marked_individual,
        placeholder_exceeds_declared,
        recovered_object_id,
        recovered_page,
        recovered_row_ordinal
    FROM public.{privategrants_w_recipients}

    UNION

    SELECT
        filerein::text AS granter_ein,
        filername1 AS granter_name,
        filername2 AS granter_name2,
        NULL AS filesha256,
        url,
        taxyear::int AS taxyear,
        taxperbegin::timestamp AS taxperbegin,
        taxperend::timestamp AS taxperend,
        rteinorecipi::text AS grantee_ein,
        NULL AS grantee_person_name,
        rtrnbbnline11 AS grantee_organization_name1,
        rtrnbbnline22 AS grantee_organization_name2,
        retaadadliin1 AS grantee_address1,
        retaadadliin2 AS grantee_address2,
        rectabaddcit AS grantee_city,
        rectabaddsta AS grantee_state,
        rtazipcode::text AS grantee_zip,
        retaamofcagr::bigint AS grant_amount,
        retapuofgrra AS grant_purpose,
        NULL AS grant_status,
        NULL AS grant_relationship,
        -- Schedule I rows carry the filer-reported recipient EIN directly;
        -- no matching happened, so no match_source.
        NULL AS match_source,
        NULL AS match_tier,
        NULL::bigint AS match_name_words,
        '{schedule_i}' AS row_source,
        NULL AS page_verdict,
        NULL::boolean AS filer_marked_individual,
        NULL::boolean AS placeholder_exceeds_declared,
        NULL AS recovered_object_id,
        NULL::integer AS recovered_page,
        NULL::integer AS recovered_row_ordinal
    -- Filing-version-deduped relation (issue #33) — raw staging would
    -- double-count amended filer-years by ~$36B.
    FROM public.{schedule_i}
) sub;
"""


_UNIONED_GRANTS_INDEXES = [
    # Composite (ein, taxyear) so taxyear is an Index Cond rather than a
    # post-index Filter on the very common "grants for EIN list since year Y"
    # access pattern. A btree on (a, b) also serves WHERE a = X queries, so
    # the single-column ein indexes are redundant — drop them on rebuild.
    "DROP INDEX IF EXISTS idx_{unioned_grants}_granter_ein",
    "DROP INDEX IF EXISTS idx_{unioned_grants}_grantee_ein",
    "DROP INDEX IF EXISTS idx_{unioned_grants}_granter_ein_taxyear",
    "DROP INDEX IF EXISTS idx_{unioned_grants}_grantee_ein_taxyear",
    "DROP INDEX IF EXISTS idx_{unioned_grants}_taxyear",
    "CREATE INDEX IF NOT EXISTS idx_{unioned_grants}_granter_ein_taxyear ON public.{unioned_grants} (granter_ein, taxyear)",
    "CREATE INDEX IF NOT EXISTS idx_{unioned_grants}_grantee_ein_taxyear ON public.{unioned_grants} (grantee_ein, taxyear)",
    "CREATE INDEX IF NOT EXISTS idx_{unioned_grants}_taxyear ON public.{unioned_grants} (taxyear)",
]


def recovered_digest(connection, policy_version: str) -> dict:
    """The recovered grants the view shows under the policy: how many, and
    eight characters that change whenever a filing's rows are written. A
    load rewrites a filing's rows only when they differ, with a new
    ``loaded_at``, so the count, the sum and the latest ``loaded_at`` move
    with the rows."""
    rows, digest = connection.execute(text(f"""
        SELECT count(*),
               md5(concat_ws(':', count(*), sum(amount), max(loaded_at)))
        FROM public.{recovered_loader.TABLE}
        WHERE policy_version = :version AND target = '{recovered_loader.PAID}'
    """), {"version": policy_version}).one()
    return {"policy_version": policy_version, "rows": int(rows), "digest": digest[:8]}


def recovered_policy_of(shown: str | None, asked: str | None) -> str:
    """The policy whose recovered grants a run takes: the one the view
    shows, or the one asked for when there is no view.

    Which policy the grants come from is a decision (``placeholder_recovery
    view --policy``), and the view's definition is the one place it is
    written. So a run changes nothing and guesses nothing: it raises when
    the view shows another policy than the one asked for, and when there is
    no view and no policy was named.
    """
    if shown is None and asked is None:
        raise LookupError(
            f"{recovered_view.VIEW} is absent, so the policy its grants come from is not known. "
            "Name it (--recovered-policy <version>), or create the view first: "
            "`python -m givingtuesday_datamart.placeholder_recovery view --policy <version>`."
        )
    if shown is not None and asked is not None and shown != asked:
        raise RuntimeError(
            f"{recovered_view.VIEW} shows policy {shown} and the run was asked for {asked}. "
            f"Run `python -m givingtuesday_datamart.placeholder_recovery view --policy {asked}`, "
            f"or ask for the policy the view shows."
        )
    return shown if shown is not None else asked


def _ensure_recovered_view(connection, policy_version: str | None) -> str:
    """The view of current and recovered grants, created when it is gone;
    returns the policy it shows.

    ``build_current_relations`` rebuilds ``privategrants_current`` with
    DROP ... CASCADE, which takes the view with it, so after a rebuild
    there is none and it is created here, before the views that read it,
    for the policy the caller read from the view before the rebuild. A
    view that is there is left as it is.
    """
    shown = recovered_view.shown_policy(connection)
    policy = recovered_policy_of(shown, policy_version)
    if shown is None:
        logger.info(f"Creating {recovered_view.VIEW} for policy {policy}")
        connection.execute(text(recovered_view.view_sql(policy)))
    return policy


def create_or_replace_views(
    connection,
    relations: Relations = Relations(),
    recovered_policy: str | None = None,
):
    """Idempotently (re)create the 8 views the matching pipeline reads from.

    Safe to call on every run: `CREATE OR REPLACE VIEW` updates definitions
    in place without touching dependent objects. The corrections table is
    created first (IF NOT EXISTS) because corrections_unique_names_view
    references it — without this, view creation would fail on a fresh
    database before the loader ever runs. So is the view of current and
    recovered grants, when the run reads its grants from it: by a
    production run only, since a run under a prefix creates nothing of
    production's. ``recovered_policy`` is the policy to create it for when
    it is gone: what the caller read from the view before the rebuild that
    dropped it. With no view and no policy this raises.
    """
    logger.info(f"Creating/replacing grant matching views in public.{relations.prefix}*")
    connection.execute(text(_CORRECTIONS_TABLE_DDL.format(table=relations.of(_CORRECTIONS_TABLE))))
    if relations.grants == recovered_view.VIEW and not relations.prefix:
        _ensure_recovered_view(connection, recovered_policy)
    for ddl in _view_ddl(relations):
        connection.execute(text(ddl))


def rebuild_unioned_grants(connection, relations: Relations = Relations()):
    """Drop and rebuild public.unioned_grants from the matched PF grants
    and the 990 Schedule I grants. Recreates the 3 EIN/year indexes after.
    """
    names = dict(
        unioned_grants=relations.of("unioned_grants"),
        privategrants_w_recipients=relations.of("privategrants_w_recipients"),
        schedule_i=relations.schedule_i,
    )
    logger.info(f"Rebuilding public.{names['unioned_grants']}")
    connection.execute(text(_UNIONED_GRANTS_DDL.format(**names)))
    for stmt in _UNIONED_GRANTS_INDEXES:
        connection.execute(text(stmt.format(**names)))


# Logical names of the four staging tables the matching pipeline reads from.
# These are the only data dependencies whose freshness affects checkpoint
# validity. Schedule-I grants feed unioned_grants but not the matching itself.
_KEYS = [
    'name1_key',
    'name2_key',
    'address1_key',
    'address2_key',
    'addresscity_key',
    'addressstate_key',
    'addresszip_key',
]

_MATCHING_INPUT_LOGICAL_NAMES = (
    "irs_990pf_grants",
    "irs_990_basic_fields",
    "irs_990pf_basic_fields",
    "irs_eo_bmf",
)

# Version of the SHAPE of matching's inputs — bump on ANY change that can
# shift row positions in the input views for unchanged source data: the
# _current dedup rules (current_grants.py), the view definitions above,
# normalize_org_name, or the blocking scheme. Chunk checkpoints key on
# integer row positions, and the prefix otherwise hashes only source
# versions + the corrections CSV, so without this token such a change
# would let a resume silently stitch features onto the wrong rows.
#   v2 (2026-07-30): matching reads privategrants_current instead of raw
#   privategrants (issue #33 filing-version dedup). v1 = everything prior.
#   v3 (2026-09-29): matching reads privategrants_current_w_recovered (the
#   recovered grants in place of the placeholder rows); rows block on the
#   name under clean_name, and the same cleaned name scores 1; the city,
#   state and zip keys of a grant are '' where they were NULL; two rows
#   without a state do not agree on it; the name-only tier; a row with no
#   address is not in the zip block; the exact-name tier asks for one
#   filer in the state of a row with no street address.
#   v4 (BMF stack): BMF names and host-institution exclusions.
#   v5 (2026-10-15): combines the BMF universe with recovered grants and
#   cleaned-name scoring from main; no earlier stack checkpoint is reusable.
#   v6 (2026-10-28): placeholder recipients are excluded before indexing.
#   v7 (2026-10-28): the placeholder pattern also takes "see" between
#   punctuation ("SEE.", "(SEE)"), and a correction row outranks a BMF row
#   with the same key tuple in the universe's DISTINCT ON. A test pins the
#   view DDL and the two reads to this number, so the next edit to them
#   cannot resume chunks against shifted rows without moving it.
#   v8 (2026-10-30): the grants view carries foreign_country, whether a row
#   of the tuple names a country other than the US, and the name-only tier
#   takes a tuple with no US address (no_us_address) instead of one with
#   no state and no zip. The seven keys, the filter and the order are v7's,
#   so row positions are the same; the pin moves with the DDL.
MATCHING_INPUT_SHAPE_VERSION = 8


def _insert_started_build(build_id: str, started_at: datetime, source_runs: dict) -> None:
    """Stamp a 'started' row in datamart_meta.canonical_builds.

    Mirrors the Phase 2 canonical-build pattern: insert a breadcrumb so a
    crash mid-run leaves a 'started'-status row that's distinguishable from
    a successful or failed build.
    """
    with get_session(config=datamart_config()) as session:
        session.execute(
            text(
                f"""
                INSERT INTO {CANONICAL_BUILDS_TABLE} (
                    build_id, build_kind, started_at, status, source_runs
                ) VALUES (
                    :build_id, 'grant_matching', :started_at, 'started',
                    CAST(:source_runs AS JSONB)
                )
                """
            ),
            {
                "build_id": build_id,
                "started_at": started_at,
                "source_runs": json.dumps(source_runs),
            },
        )


def _stamp_recovered(build_id: str, recovered: dict) -> None:
    """Add the recovered grants a run took (the policy, the rows, their
    digest) to its build's ``source_runs``."""
    with get_session(config=datamart_config()) as session:
        session.execute(
            text(
                f"""
                UPDATE {CANONICAL_BUILDS_TABLE}
                SET source_runs = source_runs || CAST(:recovered AS JSONB)
                WHERE build_id = :build_id
                """
            ),
            {"build_id": build_id, "recovered": json.dumps({recovered_loader.TABLE: recovered})},
        )


def _finalize_build_failed(build_id: str, error: BaseException) -> None:
    """Mark a grant matching build as failed in canonical_builds."""
    finished_at = datetime.now(timezone.utc)
    with get_session(config=datamart_config()) as session:
        session.execute(
            text(
                f"""
                UPDATE {CANONICAL_BUILDS_TABLE}
                SET finished_at = :finished_at,
                    status = 'failed',
                    error = :error
                WHERE build_id = :build_id
                """
            ),
            {
                "build_id": build_id,
                "finished_at": finished_at,
                "error": str(error)[:4000],
            },
        )


def _finalize_build_success(
    build_id: str,
    privategrants_w_recipients_rows: int,
    unioned_grants_rows: int,
) -> None:
    """Mark a grant matching build as successful and stamp the row counts
    of the two output tables."""
    finished_at = datetime.now(timezone.utc)
    with get_session(config=datamart_config()) as session:
        session.execute(
            text(
                f"""
                UPDATE {CANONICAL_BUILDS_TABLE}
                SET finished_at = :finished_at,
                    status = 'success',
                    privategrants_w_recipients_rows = :pg_rows,
                    unioned_grants_rows = :ug_rows
                WHERE build_id = :build_id
                """
            ),
            {
                "build_id": build_id,
                "finished_at": finished_at,
                "pg_rows": privategrants_w_recipients_rows,
                "ug_rows": unioned_grants_rows,
            },
        )


def _resolve_checkpoint_prefix(
    connection,
    recovered: dict,
    base_prefix: str = "grant_matching_checkpoints",
    versions: dict[str, str] | None = None,
) -> str:
    """Build an S3 prefix that's keyed on the source versions of all four
    upstream staging tables plus the content hash of the corrections CSV
    and the digest of the recovered grants.
    A new ingest of any source — or any edit to the corrections file, or a
    load of recovered grants — produces a fresh prefix, so old checkpoints
    can never silently be reused against new data. (Correction rows and
    recovered grants sort into the deterministic ORDER BY and shift integer
    row positions, which chunk checkpoints key on; resuming old chunks
    against a shifted dataframe would silently produce wrong matches.)

    ``recovered`` is ``recovered_digest``'s answer, read once by the run
    and stamped on its build, so the prefix and the build name the same
    rows. ``versions`` is the run's one read of the source versions
    (``_source_versions``), the read the ``_current`` relations are
    stamped from, so the prefix and the stamps name the same drop; read
    here when the caller has none.

    Resolves the latest successful (status='success') ingest_run per
    logical_name from datamart_meta.ingest_runs. Raises if any source has
    no successful run on record.
    """
    versions = _source_versions(connection) if versions is None else versions
    # Nested rather than flat (`pg_X__bf_Y`) so the prefix is browseable
    # with `aws s3 ls`: each level narrows scope, and a single source
    # version's chunks can be `aws s3 rm --recursive`'d in one shot.
    return (
        f"{base_prefix}/"
        f"pg_{versions['irs_990pf_grants']}/"
        f"bf_{versions['irs_990_basic_fields']}/"
        f"bfpf_{versions['irs_990pf_basic_fields']}/"
        f"bmf_{versions['irs_eo_bmf']}/"
        f"corr_{_corrections_content_hash()}/"
        f"rec_{recovered['policy_version']}_{recovered['digest']}/"
        f"shape_v{MATCHING_INPUT_SHAPE_VERSION}"
    )


def _source_versions(connection) -> dict[str, str]:
    """The source_version of the latest successful ingest_run of each
    matching input (``_MATCHING_INPUT_LOGICAL_NAMES``): what the checkpoint
    prefix is keyed on and what a rebuild stamps on the ``_current``
    relations. Raises if any source has no successful run on record."""
    rows = connection.execute(
        text("""
            SELECT DISTINCT ON (logical_name) logical_name, source_version
            FROM datamart_meta.ingest_runs
            WHERE logical_name = ANY(:names)
              AND status = 'success'
            ORDER BY logical_name, finished_at DESC
        """),
        {"names": list(_MATCHING_INPUT_LOGICAL_NAMES)},
    ).fetchall()
    versions = {logical_name: source_version for logical_name, source_version in rows}
    missing = set(_MATCHING_INPUT_LOGICAL_NAMES) - versions.keys()
    if missing:
        raise RuntimeError(
            f"No successful ingest run on record for: {sorted(missing)}. "
            "Run `python -m givingtuesday_datamart.sources refresh` first."
        )
    return versions


# A run that rebuilds the ``_current`` relations writes on each of them
# (COMMENT ON TABLE, in the transaction that builds it) the source versions
# and the input shape the checkpoint prefix is keyed on, from the one read
# of the versions the prefix is resolved from: a relation carries the stamp
# only when it is complete, and the stamp and the prefix cannot disagree.
# ``--skip-rebuild`` takes the relations that carry the stamp the prefix
# names now and rebuilds the rest: without that, chunks computed on
# relations built from an older drop, or under older dedup rules, would be
# written under a newer prefix, and a later run would resume them against
# shifted rows. A relation rebuilt anywhere else (``sources refresh``, the
# current_grants CLI) is a new table without the stamp, so the next run
# rebuilds it. A stamp is a catalog read, where comparing the relations
# with staging (``stale_basic_fields_current``) scans every row of both.
def _current_stamp(versions: dict[str, str], build_id: str | None) -> str:
    return json.dumps({"source_versions": versions, "shape": MATCHING_INPUT_SHAPE_VERSION, "build_id": build_id},
                      sort_keys=True)


def _stamp_of(comment: str | None) -> dict | None:
    """A relation's stamp, parsed from its comment, or None when it has
    none (never stamped, or a comment of another kind)."""
    try:
        stamp = json.loads(comment) if comment else None
    except ValueError:
        return None
    return stamp if isinstance(stamp, dict) and "source_versions" in stamp else None


def _unstamped_current_relations(connection, versions: dict[str, str]) -> list[str]:
    """The ``_current`` relations that do not carry ``versions`` under the
    current input shape: missing, rebuilt outside a matcher run, built
    from another drop, or built under other dedup rules."""
    stale = []
    for table, _ddl in _CURRENT_TABLES:
        comment = connection.execute(
            text("SELECT obj_description(to_regclass(:name), 'pg_class')"), {"name": f"public.{table}"}
        ).scalar_one()
        stamp = _stamp_of(comment)
        if stamp is None or stamp["source_versions"] != versions or stamp.get("shape") != MATCHING_INPUT_SHAPE_VERSION:
            stale.append(table)
    return stale


def after_current_build(connection, table: str, recovered_policy: str, stamp: str | None = None) -> None:
    """What commits with a ``_current`` relation's build or not at all
    (``build_current_relations``'s hook): the views the DROP ... CASCADE of
    the relations the matching views are defined over took with it, back
    in the same transaction (the view of current and recovered grants for
    ``recovered_policy``, then the matching views over it), and the
    matcher's ``stamp``, when there is one, last of all."""
    if table in _MATCHING_VIEW_TABLES:
        create_or_replace_views(connection, recovered_policy=recovered_policy)
    if stamp is not None:
        connection.execute(text(f"COMMENT ON TABLE public.{table} IS :stamp"), {"stamp": stamp})


# Filenames the chunk loop writes/reads. Used by the resume listing to
# distinguish chunk parquets from any other objects that might land at the
# same S3 prefix (none today, but cheap insurance).
_CHUNK_FILENAME_RE = re.compile(r"chunk_(\d+)\.parquet$")


def _list_existing_chunk_indices(s3_bucket: str, s3_prefix: str) -> set[int]:
    """One paginated S3 LIST replaces N per-chunk HEAD requests.

    Returns the set of chunk indices for which ``chunk_<idx>.parquet`` already
    exists at ``s3://<bucket>/<prefix>/``. At chunk_size=50K with ~13K chunks
    that's a 13K → ~13 round-trip reduction.

    ``Delimiter='/'`` scopes the listing to the immediate level, so chunks
    inside ``_test_limit_<N>/`` subdirectories don't accidentally show up
    when running without --limit.
    """
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    list_prefix = s3_prefix.rstrip("/") + "/"
    found: set[int] = set()
    for page in paginator.paginate(Bucket=s3_bucket, Prefix=list_prefix, Delimiter="/"):
        for obj in page.get("Contents", ()):
            m = _CHUNK_FILENAME_RE.search(obj["Key"])
            if m:
                found.add(int(m.group(1)))
    return found


# ``pqc.write_dataframe`` stamps this metadata key with a JSON array of
# column names that were JSON-encoded on write (because they held
# dict/list/tuple values). Kept in sync with the writer so the read path
# below decodes them back on read.
_VDL_JSON_COLS_KEY = b"vdl_json_columns"


def _fast_read_chunk(s3_client, bucket: str, key: str) -> pd.DataFrame:
    """Direct boto3 → BytesIO → pyarrow.

    The serial-write side (``pqc.write_dataframe``) goes through the same
    path; this is the parallel read complement. Direct boto3 with a sized
    connection pool measured ~10× faster than going through fsspec/s3fs
    on the resume hot path (~7s/chunk in parallel vs ~80ms/chunk serially
    via fsspec at 32+ workers).

    Decodes JSON columns flagged in the file's ``vdl_json_columns`` schema
    metadata back to dict/list/tuple. For grant_matching's chunks today
    that list is empty, so the decode loop is a no-op — but the contract
    is kept in case future writes start using JSON columns.
    """
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    table = pq.read_table(io.BytesIO(body))
    meta = table.schema.metadata or {}
    json_cols = set(json.loads(meta.get(_VDL_JSON_COLS_KEY) or b"[]"))
    df = table.to_pandas()
    for col in json_cols & set(df.columns):
        df[col] = df[col].map(_pqc_decode_json)
    return df


def _read_chunks_parallel(
    s3_bucket: str,
    indexed_keys: list[tuple[int, str]],
    max_workers: int,
) -> dict[int, pd.DataFrame]:
    """Parallel S3 GETs via ThreadPoolExecutor — pure I/O, GIL-friendly.

    Returns ``{chunk_idx: df}`` so the caller can preserve order downstream.
    Reads happen out-of-order; reordering is the caller's job.
    """
    out: dict[int, pd.DataFrame] = {}
    if not indexed_keys:
        return out
    # Pool is sized to comfortably hold all worker connections in flight.
    # boto3's default is 10 — far below the worker count we want for resume.
    s3 = boto3.client(
        "s3",
        config=botocore.config.Config(max_pool_connections=max_workers + 16),
    )
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_fast_read_chunk, s3, s3_bucket, key): idx
            for idx, key in indexed_keys
        }
        for fut in tqdm(cf.as_completed(futures), total=len(futures), desc="Resuming"):
            idx = futures[fut]
            out[idx] = fut.result()
    return out


# --- 2. CLEANING FUNCTIONS ---

def clean_year(series):
    # Force to string, remove decimals (e.g. 2018.0 -> 2018), handle NaNs
    return series.astype(str).str.replace(r'\.0$', '', regex=True).fillna('0')

def clean_zip(address_zip):
    length = len(address_zip)
    if length < 5:
        # Pad with zeros
        num_zeros = 5 - length
        return "0" * num_zeros + address_zip
    if length == 5:
        return address_zip
    if length > 5 and '-' in address_zip:
        address_zip = address_zip.split("-")[0]
        return clean_zip(address_zip)
    if length == 9:
        return address_zip[:5]
    if length < 9 and '-' in address_zip:
        num_zeros = 9 - length
        zero_padded_zip = "0" * num_zeros + address_zip
        return zero_padded_zip[:5]
    return None

def normalize_org_name(name: str) -> str:
    """Normalize an org name for comparison: the name two rows are scored
    on when they are not the same name (``clean_name`` says when they are).

    Grant rows and filings disagree on leading articles and punctuation for
    the same org (Stanford files as "THE BOARD OF TRUSTEES OF THE LELAND
    STANFORD" + "JUNIOR UNIVERSITY"; grant rows say "BOARD OF TRUSTEES OF
    THE LELAND STANFORD JUNIOR UNIVERSITY" — Jaro-Winkler 0.83 raw, 1.0
    normalized). Strips apostrophes/periods/commas/quotes, collapses
    whitespace, and drops one leading "the ". Falls back to the un-stripped
    form if normalization would empty the name (a bare "the") so blocking
    never keys on ''.
    """
    name = re.sub(r"[',\.\"]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    if name.startswith("the ") and len(name) > 4:
        name = name[4:]
    return name


_LEGAL_ENDINGS = r"(?:inc|incorporated|corp|corporation|co|company|ltd|llc|nfp|pc|plc|lp|llp)"


def clean_name(name: str) -> str:
    """Clean an org name to say when two rows have the same name: lower
    case, apostrophes out, "&" to "and", every other mark to a space, a
    leading or trailing "the" dropped, and the legal endings (Inc, Corp,
    LLC) dropped. "The River Fund, Inc." and "RIVER FUND INC" are both
    "river fund".

    One level of cleaning, and no more. Measured on the recovered grants of
    the frame (the findings doc, *Matching on the name*), it lifts exact
    matches on a name that belongs to one filer from 61.2% to 72.5% of the
    rows with no address; expanding abbreviations and dropping stopwords
    adds half a point and more shared names. Across all grants, $4.8B of
    high-similarity misses differed from the filed name only by a trailing
    "inc" or a leading "the" (``corrections_scout``).

    It extends ``normalize_org_name`` and does not replace it. Two rows
    with the same cleaned name block together and score 1 on the name; any
    other pair is scored on the normalized names, as before input shape 3.
    Scoring the cleaned names instead moved every score: Jaro-Winkler
    rewards a short name, and with the endings gone "mit" passed the
    lowest name score against "mit womens independent group" (0.79, from
    0.70), and matched it on a shared address. On the subset of 2026-09-29
    that was 2,247 rows matched, 822 lost and 959 moved to another filer
    beside the 14,088 the same name gained.

    A name that is only a "the" or only a legal ending stays as it is, so
    a name with a letter or a digit in it never cleans to ''.
    """
    text_ = re.sub(r"['’`]", "", (name or "").lower()).replace("&", " and ")
    text_ = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text_)).strip()
    text_ = re.sub(r" the$", "", re.sub(r"^the ", "", text_))
    return re.sub(r"(?: " + _LEGAL_ENDINGS + r")+$", "", text_).strip()


def join_name(name1: str, name2: str) -> str:
    """The two name lines as one name, a space between them."""
    return " ".join(part.strip() for part in (name1, name2) if part.strip())


def create_full_name(row):
    return clean_name(join_name(row['name1_key'], row['name2_key']))


def prepare(df: pd.DataFrame, addresses: bool = True) -> pd.DataFrame:
    """The columns a run blocks and scores on, added in place to either
    side: ``clean_zip``, ``full_name``, ``compare_name``, ``compare_state``
    and ``compare_addr``.

    ``full_name`` is the cleaned name, which says when two rows have the
    same name; ``compare_name`` the normalized one, which the rest are
    scored on. ``compare_state`` is the state, and missing where there is
    none: two rows without a state do not have the same state.
    ``compare_addr`` is the slow one and is local to its row, so a caller
    that scores slices leaves it out (``addresses``) and adds it to each
    slice.
    """
    df.fillna("", inplace=True)
    df['clean_zip'] = df['addresszip_key'].apply(clean_zip)
    names = [join_name(name1, name2) for name1, name2 in zip(df['name1_key'], df['name2_key'])]
    df['full_name'] = [clean_name(name) for name in names]
    df['compare_name'] = [normalize_org_name(name) for name in names]
    df['compare_state'] = df['addressstate_key'].where(df['addressstate_key'].str.strip() != "")
    if addresses:
        add_compare_addr(df)
    return df


def add_compare_addr(df: pd.DataFrame) -> pd.DataFrame:
    """``compare_addr``, the address as one normalized string, added in
    place. (``apply`` over no rows gives a frame, not a column.)"""
    df['compare_addr'] = df.apply(create_clean_address, axis=1) if len(df) else ""
    return df


def filter_match_rules(
    features_df: pd.DataFrame,
    near_perfect_name_name_min: float,
    near_perfect_name_addr_min: float,
    near_perfect_addr_name_min: float,
    near_perfect_addr_addr_min: float,
    good_enough_name_name_min: float,
    good_enough_name_addr_min: float,
    exact_name_name_min: float | None = None,
) -> pd.DataFrame:
    if features_df.empty:
        return features_df

    near_perfect_name_matches = (
        (features_df['name_score'] >= near_perfect_name_name_min)
        & (features_df['addr_score'] >= near_perfect_name_addr_min)
    )
    near_perfect_addr_matches = (
        (features_df['name_score'] >= near_perfect_addr_name_min)
        & (features_df['addr_score'] >= near_perfect_addr_addr_min)
    )
    good_enough_name_matches = (
        (features_df['name_score'] >= good_enough_name_name_min)
        & (features_df['addr_score'] >= good_enough_name_addr_min)
    )

    keep = near_perfect_name_matches | near_perfect_addr_matches | good_enough_name_matches

    # Cross-zip tier: (near-)exact name + same state, no address requirement.
    # Exists for recipients listed at a different address than the filer's
    # 990 header (e.g. Gates Trust lists the Gates Foundation at its street
    # address, zip 98109; the Foundation files from a PO Box in 98102).
    # These pairs come from the full_name block, so the address columns are
    # expected to disagree — the state equality gate is what holds precision.
    # The state is scored as missing where a row has none (``prepare``), so
    # two rows without a state never pass here: a row with no state and no
    # zip has the name-only tier, which asks that the name belong to one
    # filer.
    if exact_name_name_min is not None and 'state_score' in features_df.columns:
        exact_name_matches = (
            (features_df['name_score'] >= exact_name_name_min)
            & (features_df['state_score'] == 1)
        )
        keep = keep | exact_name_matches

    return features_df[keep]


# The two threshold sets passed to filter_match_rules. Chunk-stage is
# looser so checkpointed chunks survive a later tightening of the final
# rules; final-stage decides matches.
# corrections_preflight imports both — change thresholds here, never inline,
# so the preflight can't drift from the real matcher.
CHUNK_FILTER_RULES = dict(
    near_perfect_name_name_min=0.95,
    near_perfect_name_addr_min=0.35,
    near_perfect_addr_name_min=0.75,
    near_perfect_addr_addr_min=0.75,
    good_enough_name_name_min=0.55,
    good_enough_name_addr_min=0.85,
    exact_name_name_min=0.95,
)
FINAL_FILTER_RULES = dict(
    near_perfect_name_name_min=0.99,
    near_perfect_name_addr_min=0.50,
    near_perfect_addr_name_min=0.85,
    near_perfect_addr_addr_min=0.85,
    good_enough_name_name_min=0.70,
    good_enough_name_addr_min=0.90,
    exact_name_name_min=0.99,
)


# --- Precision guards ---------------------------------------------------
# Applied to the FINAL_FILTER_RULES survivors, before winner resolution, by
# every reader of the matcher: the run itself (_do_match_records),
# corrections_preflight._resolve_winners (which bmf_alias_gain reuses) and
# match_explainer._score_pairs. Keep the three in lockstep — tests check it.
#
# Governments and public institutions are in no arm of the filer universe:
# a city, a county, a state agency or a public university files no 990, and
# the IRS Business Master File lists it only where it holds its own
# 501(c)(3) ruling (the BMF has "City of Hope" and "Regents of the University
# of California", not "City of New York" or "University of Michigan"). So a
# grant row naming one can only ever be won by some charity within name 0.70
# at the same address — "city of marietta" by a firefighters' local at city
# hall, "university of utah" by its Asia campus, "university of iowa" by its
# facilities corporation, "city of rock valley" by "friends of rock valley".
# The BMF arm adds exactly the small orgs that share a government's address
# (union locals, PTAs, booster clubs, alumni chapters), which makes this
# likelier.
#
# The rule: a grant tuple whose normalized full_name starts with a
# government or public-institution prefix may win below an exact name
# (name_score < GOVERNMENT_NAME_EXACT_MIN) only if the filer row says
# nothing the grant row does not — every substantive token of the filer
# name is covered by a grant token (equal; a prefix of it or extended by it,
# for truncations; the same state or word abbreviation; or a Jaro-Winkler
# twin, for typos). What survives is the org present under a variant of the
# grant's own name: "university of texas foundation" -> "university of texas
# foundation inc", "university of relands" -> "university of redlands",
# "university of new haven fbo sydney field" -> "university of new haven",
# "university of pennsylvania trustees" -> "trustees of the university of
# pennsylvania". What goes is the row that adds a word: "university of
# cincinnati" -> "university of cincinnati foundation", "university of
# washington" -> "university of washington club". A legal name longer than
# the common one ("university of notre dame du lac", "university of detroit
# mercy") is refused too; that is what a corrections-registry row is for.
# Measured before and after on the August 4, 2026 output, and proved on the
# BMF slice: docs/matching-precision-guards.md. The plain rule "no non-exact
# win at all" was measured there as well and refuses about four times the
# right matches for a fifth more wrong ones removed.
# The prefixes and both regexes (GOVERNMENT_NAME_PREFIXES, GOVERNMENT_NAME_RE,
# GOVERNMENT_NAME_SQL_RE) are defined with the view DDL above, which shares
# them; the SQL form over FULL_NAME_SQL is what the regression gate and the
# exploratory measurement select with, so they see exactly the tuples the
# guard sees.
# A win at or above this name_score is exact: the near-perfect-name and the
# cross-zip exact-name tiers both start here, so an org present under the
# grant's own name still wins above it, whatever the rule says.
GOVERNMENT_NAME_EXACT_MIN = FINAL_FILTER_RULES["near_perfect_name_name_min"]

# Tokens that carry no identity: function words and legal forms
# ("foundation" and "foundation inc" are the same org).
_NAME_STOP_TOKENS = frozenset({
    "the", "of", "and", "a", "an", "at", "in", "for", "to", "on",
    "inc", "incorporated", "corp", "corporation", "co", "llc", "ltd",
})
_STATE_ABBREVIATIONS = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa",
    "ks": "kansas", "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
    "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada", "nh": "new hampshire",
    "nj": "new jersey", "nm": "new mexico", "ny": "new york", "nc": "north carolina",
    "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania",
    "ri": "rhode island", "sc": "south carolina", "sd": "south dakota", "tn": "tennessee",
    "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington",
    "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
}
_TERRITORY_ABBREVIATIONS = {
    "pr": "puerto rico", "vi": "virgin islands", "gu": "guam", "as": "american samoa",
    "mp": "northern mariana islands",
}
# The state keys that name a US state, the District or a territory: the
# USPS code or the name spelled out, as the keys are lower case. A grant
# tuple whose state key is none of these gives no US address: GT and our
# extract fill the state column from a foreign address's province when the
# recipient has no US address ("ontario", "on", "england", "geneva", "11";
# docs/own-extract.md), and a domestic address always carries its code.
US_STATE_KEYS = frozenset(
    key for table in (_STATE_ABBREVIATIONS, _TERRITORY_ABBREVIATIONS) for key in (*table, *table.values())
)


def us_state(states: pd.Series) -> pd.Series:
    """Which state keys name a US state or territory (``US_STATE_KEYS``),
    read with letters and single spaces only: a filer who typed "N.Y.",
    "D.C." or "NY 10001" gave a US state."""
    bare = (states.fillna('').astype(str).str.lower().str.replace(r'[^a-z ]', '', regex=True)
            .str.replace(r'\s+', ' ', regex=True).str.strip())
    return bare.isin(US_STATE_KEYS)


# Abbreviations and truncations grant rows use for words the filer spells
# out ("univ of florida fndtn", "umass foundat", "mt st vincent"); each
# pattern matches the whole token.
_TOKEN_ABBREVIATIONS = [
    (re.compile(r"^f(?:ou)?(?:nd(?:a(?:t(?:i(?:on?)?)?)?|n|tn)?|dt?n)$"), "foundation"),
    (re.compile(r"^ass(?:n|oc?|ocia(?:t(?:i(?:on?)?)?)?)$"), "association"),
    (re.compile(r"^alum$"), "alumni"),
    (re.compile(r"^univ$"), "university"),
    (re.compile(r"^st$"), "saint"),
    (re.compile(r"^mt$"), "mount"),
    (re.compile(r"^ft$"), "fort"),
    (re.compile(r"^ctr$"), "center"),
    (re.compile(r"^dept$"), "department"),
    (re.compile(r"^intl$"), "international"),
    (re.compile(r"^natl$"), "national"),
    (re.compile(r"^hosp$"), "hospital"),
    (re.compile(r"^med$"), "medical"),
    (re.compile(r"^inst$"), "institute"),
]
# A typo twin: one edit away, or Jaro-Winkler 0.9, between tokens of six
# letters or more ("charlseston" / "charleston", "relands" / "redlands",
# "missippi" / "mississippi"); shorter tokens are too easily look-alikes
# ("campus" / "camps", "wood" / "word").
_TOKEN_TWIN_MIN_LEN = 6
_TOKEN_TWIN_JW_MIN = 0.9

# SQL twin of create_full_name/clean_name over the already-lowercased keys.
# Keep the normalization in lockstep with prepare; the db parity test covers it.
FULL_NAME_SQL = "concat_ws(' ', NULLIF(trim(name1_key), ''), NULLIF(trim(name2_key), ''))"
FULL_NAME_SQL = f"regexp_replace({FULL_NAME_SQL}, '[''’`]', '', 'g')"
FULL_NAME_SQL = f"replace({FULL_NAME_SQL}, '&', ' and ')"
FULL_NAME_SQL = f"trim(regexp_replace(regexp_replace({FULL_NAME_SQL}, '[^a-z0-9 ]+', ' ', 'g'), '\\s+', ' ', 'g'))"
FULL_NAME_SQL = f"regexp_replace(regexp_replace({FULL_NAME_SQL}, '^the ', ''), ' the$', '')"
# Capturing groups avoid SQLAlchemy treating the :inc in (?:inc|...) as a bind.
_LEGAL_ENDINGS_SQL = _LEGAL_ENDINGS.replace("(?:", "(")
FULL_NAME_SQL = f"trim(regexp_replace({FULL_NAME_SQL}, '( {_LEGAL_ENDINGS_SQL})+$', ''))"


def is_government_name(full_name: str) -> bool:
    """True when a normalized recipient name (create_full_name's output)
    starts with a government or public-institution prefix."""
    return GOVERNMENT_NAME_RE.match(full_name) is not None


def name_tokens(full_name: str) -> list[str]:
    """The substantive tokens of a normalized name: split on anything but
    letters and digits, stop tokens dropped, abbreviations spelled out."""
    tokens = []
    for token in re.split(r"[^a-z0-9]+", full_name):
        if not token or token in _NAME_STOP_TOKENS:
            continue
        for pattern, word in _TOKEN_ABBREVIATIONS:
            if pattern.match(token):
                token = word
                break
        if token in _STATE_ABBREVIATIONS:
            tokens.extend(_STATE_ABBREVIATIONS[token].split())
        else:
            tokens.append(token)
    return tokens


def _token_covered(token: str, grant_tokens: list[str]) -> bool:
    for g in grant_tokens:
        if g == token:
            return True
        if len(token) >= 3 and len(g) >= 3 and (g.startswith(token) or token.startswith(g)):
            return True
        if len(token) >= _TOKEN_TWIN_MIN_LEN and len(g) >= _TOKEN_TWIN_MIN_LEN and (
            jellyfish.damerau_levenshtein_distance(g, token) <= 1
            or jellyfish.jaro_winkler_similarity(g, token) >= _TOKEN_TWIN_JW_MIN
        ):
            return True
    return False


def government_refusal(grant_name: str, universe_name: str, name_score: float,
                       exact_min: float = GOVERNMENT_NAME_EXACT_MIN) -> bool:
    """The guard's decision for one scored pair: True = refuse. Both names
    are create_full_name output; the rule is spelled out above."""
    if name_score >= exact_min or not is_government_name(grant_name):
        return False
    grant_tokens = name_tokens(grant_name)
    return any(not _token_covered(t, grant_tokens) for t in name_tokens(universe_name))


def guard_government_names(
    matches: pd.DataFrame,
    grant_full_name: pd.Series,
    universe_full_name: pd.Series,
    grant_index_col: str = "private_foundations_df_index",
    universe_index_col: str = "basic_fields_df_index",
    exact_min: float = GOVERNMENT_NAME_EXACT_MIN,
) -> pd.DataFrame:
    """Drop the pairs government_refusal refuses.

    ``matches`` is a filter_match_rules survivor frame; the two index
    columns hold the grant and universe frames' index *labels*
    (recordlinkage pairs carry labels, which equal positions in the full run
    but not in a slice), and the two Series are those frames' full_name
    columns. Pure and order-preserving; call it before winner resolution so
    a tuple whose best-scoring pair is refused still falls to an exact-name
    pair of another EIN when one exists.
    """
    if matches.empty:
        return matches
    grant_names = grant_full_name.loc[matches[grant_index_col].to_numpy()].to_numpy()
    # Only government-named, non-exact pairs need the per-pair decision.
    government = pd.Series(grant_names, index=matches.index).str.match(GOVERNMENT_NAME_RE).to_numpy()
    candidates = matches[government & (matches["name_score"].to_numpy() < exact_min)]
    if candidates.empty:
        return matches
    refused = [
        government_refusal(g, u, score, exact_min)
        for g, u, score in zip(
            grant_full_name.loc[candidates[grant_index_col].to_numpy()].to_numpy(),
            universe_full_name.loc[candidates[universe_index_col].to_numpy()].to_numpy(),
            candidates["name_score"].to_numpy(),
        )
    ]
    return matches.drop(index=candidates.index[refused])


def universe_sql(relations: Relations = Relations(), limit_clause: str = "") -> str:
    """The read of the filer universe.

    Explicit ORDER BY (redundant with the views' own ORDER BY, but
    contractual): row order MUST be deterministic across runs because
    chunk checkpoints reference DataFrame rows by integer position.
    If row order shifts between runs, resumed chunks would point at
    the wrong rows and produce silently-incorrect matches.
    The filer universe is 990 + 990-PF filers + correction rows, each
    arm tagged with a `source` column that rides through matching into
    privategrants_w_recipients as match_source.
    Tagging the arms breaks plain UNION dedup (rows identical but for
    `source` no longer collapse), so dedup is done explicitly:
    UNION ALL + DISTINCT ON (key tuple), with source_rank as the
    tie-break. An org whose key tuple appears in multiple arms keeps
    one row: a filing first, then a correction, then a BMF line. So a
    correction that duplicates a real filing collapses away, and
    match_source='correction' only appears where the correction row
    itself supplied the winning identity (the prunability signal:
    docs/corrections-plan.md). A correction that repeats a BMF line
    keeps its own row: the winner order prefers it to the BMF line
    among exact names (``ALIAS_SOURCES``), so it is not redundant.
    Which row survives changes only ``source``, never the key tuple, so
    row positions are the same either way. source_rank in the DISTINCT
    ON ORDER BY keeps the winner deterministic; the outer ORDER BY keeps
    row order deterministic over the whole universe.
    """
    of = relations.of
    return f"""
        SELECT filerein_key, name1_key, name2_key, address1_key,
               address2_key, addresscity_key, addressstate_key,
               addresszip_key, source
        FROM (
            SELECT DISTINCT ON (filerein_key, name1_key, name2_key,
                                address1_key, address2_key,
                                addresscity_key, addressstate_key,
                                addresszip_key)
                *
            FROM (
                SELECT *, 'basic_fields' AS source, 1 AS source_rank
                FROM public.{of('basic_fields_unique_names_view')}
                UNION ALL
                SELECT *, 'basic_fields_pf' AS source, 2 AS source_rank
                FROM public.{of('basic_fields_pf_unique_names_view')}
                UNION ALL
                SELECT *, 'correction' AS source, 3 AS source_rank
                FROM public.{of('corrections_unique_names_view')}
                UNION ALL
                SELECT *, 'eo_bmf' AS source, 4 AS source_rank
                FROM public.{of('eo_bmf_unique_names_view')}
            ) arms
            ORDER BY filerein_key, name1_key, name2_key, address1_key,
                     address2_key, addresscity_key, addressstate_key,
                     addresszip_key, source_rank
        ) filers
        ORDER BY filerein_key, name1_key, name2_key, address1_key,
                 address2_key, addresscity_key, addressstate_key,
                 addresszip_key
        {limit_clause}
    """


def grants_sql(relations: Relations = Relations(), limit_clause: str = "") -> str:
    """The read of the grant tuples, in the same contractual order."""
    return f"""
        SELECT * FROM public.{relations.of('privategrants_unique_names_view')}
        ORDER BY name1_key, name2_key, address1_key, address2_key,
                 addresscity_key, addressstate_key, addresszip_key
        {limit_clause}
    """


# The (universe row, grant tuple) positions of a pair, as columns.
INDEX_COLS = ['basic_fields_df_index', 'private_foundations_df_index']
SCORE_COLS = ['zip_score', 'name_score', 'addr_score', 'state_score']
# match_tier: what a match rests on. The address tiers of filter_match_rules
# (a name and an address that agree), the exact name with the same state,
# or the name alone. match_source says which arm of the universe the
# matched filer's row came from, whatever the tier.
ADDRESS_TIER = "address_and_name"
STATE_TIER = "state_and_name"
NAME_ONLY = "name_only"
# The arms of the universe whose rows are names the IRS Business Master
# File lists for an EIN (its primary and sort names), not a name the
# organization filed under or a researcher confirmed for a grant. Among
# candidates that carry a grant's exact name, every other arm, a new one
# included, wins over these (resolve_matches); elsewhere the arm decides
# nothing.
ALIAS_SOURCES = ("eo_bmf",)


def candidate_pairs(universe_df: pd.DataFrame, grants_df: pd.DataFrame) -> pd.MultiIndex:
    """The pairs to score. Two blocking passes, unioned by indexer.index():
      1. exact zip — the primary block (~1B pairs)
      2. exact full_name — cross-zip candidates for recipients listed at a
         different address than the filer's header (e.g. Gates Trust →
         Gates Foundation, zip 98109 vs 98102). Measured at ~681K pairs
         (+0.07%), but it reaches ~$25B of PF-recipient dollars that zip
         blocking alone can never pair. These pairs match via the
         exact-name + same-state tier in filter_match_rules.

    A row with no address at all is left out of the zip block. Its
    ``compare_addr`` (street, city and state as one string) is empty, so it
    scores 0 on the address against anything and has no state: no address
    tier and no state tier can accept a pair it is in. A missing zip pads
    to 00000, and that block paired every such grant tuple with the 931
    filers that have no zip: 117.8M pairs for the recovered grants loaded
    on 2026-09-30, and 32M for the regular ones. The name block still
    pairs it with the filers of its own name, and the name-only tier does
    not go through pairs.
    """
    # Make sure the zip codes are Pandas Categorical types for faster matching
    union_cats = pd.concat([universe_df['clean_zip'], grants_df['clean_zip']]).unique()
    cat_type = pd.CategoricalDtype(categories=union_cats, ordered=False)
    for df in (universe_df, grants_df):
        df['clean_zip'] = df['clean_zip'].astype(cat_type)
        # Missing where there is no address: a block leaves a missing key out.
        df['block_zip'] = df['clean_zip'].where(df['compare_addr'] != "")

    # The same two blocks recordlinkage.Index().block(...) makes, with the
    # same result: the union of the two, deduplicated, sorted by the pair
    # (pandas' MultiIndex.union sorts, and the stable tie-break in
    # resolve_matches depends on that order). In integers rather than
    # tuples: on 2026-10-27 the union of about a billion pairs ran more
    # than five hours inside MultiIndex.drop_duplicates on one core of an
    # r6i.8xlarge; packed as one int64 a pair, numpy.unique sorts and
    # deduplicates the same pairs in minutes.
    return _blocked_pairs(universe_df, grants_df, ["block_zip", "full_name"])


def _block_positions(left_key: pd.Series, right_key: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Row positions of every (left, right) whose keys are equal and not
    missing: the pairs one recordlinkage block makes."""
    codes, _ = pd.factorize(pd.concat([left_key, right_key], ignore_index=True), use_na_sentinel=True)
    n_left = len(left_key)
    left = pd.DataFrame({"code": codes[:n_left], "l": np.arange(n_left, dtype=np.int64)})
    right = pd.DataFrame({"code": codes[n_left:], "r": np.arange(len(right_key), dtype=np.int64)})
    pairs = left[left["code"] >= 0].merge(right[right["code"] >= 0], on="code")
    return pairs["l"].to_numpy(), pairs["r"].to_numpy()


def _blocked_pairs(universe_df: pd.DataFrame, grants_df: pd.DataFrame, keys: list[str]) -> pd.MultiIndex:
    """The union of the blocks on ``keys`` as a MultiIndex of the two frames'
    index labels, deduplicated and sorted by (left, right)."""
    n_right = len(grants_df)
    packed = [
        left.astype(np.int64) * n_right + right.astype(np.int64)
        for left, right in (_block_positions(universe_df[k], grants_df[k]) for k in keys)
    ]
    uniq = np.unique(np.concatenate(packed)) if packed else np.empty(0, dtype=np.int64)
    left_labels = universe_df.index.to_numpy()[uniq // n_right]
    right_labels = grants_df.index.to_numpy()[uniq % n_right]
    return pd.MultiIndex.from_arrays([left_labels, right_labels],
                                     names=[universe_df.index.name, grants_df.index.name])


def pair_scores(pairs: pd.MultiIndex, universe_df: pd.DataFrame, grants_df: pd.DataFrame) -> pd.DataFrame:
    """The four scores of each pair (``SCORE_COLS``), indexed by the pair."""
    compare = recordlinkage.Compare()
    # compare.exact('clean_year', 'clean_year', label='year_score')
    compare.exact('clean_zip', 'clean_zip', label='zip_score')
    compare.string('compare_name', 'compare_name', method='jarowinkler', label='name_score')
    compare.string('compare_addr', 'compare_addr', method='levenshtein', label='addr_score')
    # State equality gates the cross-zip exact-name match tier.
    compare.exact('compare_state', 'compare_state', label='state_score')
    compare.exact('full_name', 'full_name', label='same_name')
    features = compare.compute(pairs, universe_df, grants_df)
    # The same cleaned name is the same name, unless it is no name at all
    # (a name with no letter or digit cleans to ''). Any other pair keeps
    # the score of its normalized names.
    named = grants_df['full_name'].reindex(features.index.get_level_values(1)).to_numpy() != ""
    features['name_score'] = features['name_score'].where(~((features['same_name'] == 1) & named), 1.0)
    return features[SCORE_COLS]


def score_pairs(pairs: pd.MultiIndex, universe_df: pd.DataFrame, grants_df: pd.DataFrame,
                chunk_rules: bool = True) -> pd.DataFrame:
    """The scores of ``pairs``, with the pair's two positions as the
    columns ``INDEX_COLS``; those that pass the chunk-stage rules, or all
    of them."""
    features = pair_scores(pairs, universe_df, grants_df)
    if chunk_rules:
        # Keep chunk checkpoints generous so reruns can still tighten final rules later.
        features = filter_match_rules(features_df=features, **CHUNK_FILTER_RULES)
    features.index = features.index.set_names(INDEX_COLS)
    return features.reset_index()


def _by_address(features: pd.DataFrame) -> pd.Series:
    """Does a pair pass one of the address tiers of the final rules?"""
    rules = FINAL_FILTER_RULES
    name, address = features['name_score'], features['addr_score']
    return (
        ((name >= rules['near_perfect_name_name_min']) & (address >= rules['near_perfect_name_addr_min']))
        | ((name >= rules['near_perfect_addr_name_min']) & (address >= rules['near_perfect_addr_addr_min']))
        | ((name >= rules['good_enough_name_name_min']) & (address >= rules['good_enough_name_addr_min']))
    )


# Which grant tuples the one-filer-in-the-state rule holds: those with no
# street address ("street"), every tuple ("all"), or none ("none", the
# matcher up to input shape 2, for a before-and-after measurement).
ONE_FILER_IN_STATE = "street"
# A street address has a number in it, of a building or of a box. An
# address line without a digit is a city, a department or a person: a
# recovered grant has no city column, so its city, when the list prints
# one, is in its address line.
_STREET = r"\d"


def placeholder_names(grants_df: pd.DataFrame) -> pd.Series:
    """Which grant tuples name no recipient: a pointer phrase ("SEE
    ATTACHED STATEMENT") or "see" alone (``PLACEHOLDER_REGEX``), tested on
    the business-name lines as the view tests them. One boolean per row of
    ``grants_df``, on its index, an empty frame included."""
    if 'name1_key' in grants_df:
        empty = pd.Series('', index=grants_df.index)
        names = grants_df['name1_key'].fillna('') + ' ' + grants_df.get('name2_key', empty).fillna('')
    else:
        names = grants_df.get('full_name', pd.Series('', index=grants_df.index)).fillna('')
    return pd.Series([bool(_PLACEHOLDER_NAME_RE.search(str(name))) for name in names],
                     index=grants_df.index, dtype=bool)


# How resolve_matches picks the winner of a grant tuple among the pairs the
# final rules accept (docs/recipient-ranking-production-2026-10-28.md):
#   "named_first", the default since 2026-10-29: a candidate in a state that
#     contradicts the grant's last; then a named candidate, one whose name
#     is exact (name_score at the exact-name threshold) or holds every word
#     of the grant's (``_names_held``: "jewish community foundation of los
#     angeles" is named for a grant to "jewish community foundation",
#     "stanford review" is not for one to "stanford university"); then
#     geographic support (location_agreement's tier); then an exact name;
#     then, among exact names, any arm of the universe before a BMF line
#     (ALIAS_SOURCES); then 2*name_score + addr_score. The
#     one-filer-in-the-state rule refuses its pairs before the winner is
#     picked. Two orders measured beside it left the code when it was
#     chosen: "geographic" (geography before an exact name; 467 of 647
#     labeled relationships) and "exact_first" (an exact name before
#     geography, whatever the local candidate's name).
#   "weighted": 2*name_score + addr_score alone, then the one-filer rule on
#     the winner: the order of every run up to 2026-10-27, kept to measure
#     the default against and to fall back to.
RANKING = "named_first"
_ORDERS = {
    "named_first": ['_not_conflict', '_named', '_geography', '_exact', '_exact_preferred', '_combined_score'],
    "weighted": ['_combined_score'],
}
RANKINGS = tuple(_ORDERS)
# The address score at which a shared ZIP is local evidence by itself.
LOCAL_ADDRESS_MIN = 0.90
# How many of the last words of an address line may be a city.
_CITY_TAIL_WORDS = 3


def _place(value: str) -> str:
    """A city, or an address line, as the address score writes it
    (``create_clean_address``): upper case, the USPS abbreviations (FORT is
    FT, MOUNT is MT), punctuation dropped, single spaces."""
    value = ADDRESS_PATTERN.sub(lambda m: USPS_MAP[m.group().upper().replace('.', '')], value.upper())
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', value)).strip()


def _state(value: str) -> str:
    return re.sub(r'[^0-9A-Z]', '', value.upper())


def _zip(value: str) -> str:
    """The scorer's five-digit ZIP (``clean_zip``), or '' where there is
    none: never the '00000' it blocks an absent ZIP under."""
    zip_code = clean_zip(value)
    return '' if zip_code in (None, '00000') else zip_code


def _tails_of(words: list[str]) -> list[str]:
    return [' '.join(words[-k:]) if len(words) >= k else '' for k in range(1, _CITY_TAIL_WORDS + 1)]


def _line_tails(line: str, state: str) -> list[str]:
    """The last one, two and three words of an address line, after a ZIP
    and then a state code printed at its end: the tuple's own ``state``,
    or, for a tuple that carries none, any US state code ("15305 44th ave
    w lynnwood wa 98087" on a recovered row with no state of its own). A
    code the tuple did not vouch for may end the city instead ("rocky
    mount" is ROCKY MT after the USPS abbreviation), so for such a line
    the tails with the code kept come too."""
    words = _place(line).split()
    while words and words[-1].isdigit():
        words.pop()
    if state:
        if words and words[-1] == state:
            words.pop()
        return _tails_of(words)
    if words and words[-1] in recovery_address.USPS:
        kept = _tails_of(words)
        words.pop()
        return _tails_of(words) + kept
    return _tails_of(words)


def _column(frame: pd.DataFrame, name: str) -> pd.Series:
    return frame[name] if name in frame else pd.Series('', index=frame.index, dtype=object)


def _codes(columns: list[pd.Series], normalize) -> list[np.ndarray]:
    """Integer codes of ``normalize(value)`` for the values of several
    columns, shared between them, so equal codes mean equal normalized
    values; -1 where the normalized value is empty. ``normalize`` runs once
    per distinct value, not once per pair: a full run compares about 28
    million pairs over a few hundred thousand distinct cities."""
    raw = pd.concat(columns, ignore_index=True).fillna('').astype(str)
    codes, distinct = pd.factorize(raw)
    normalized = np.array([normalize(value) for value in distinct], dtype=object)
    shared, _ = pd.factorize(normalized)
    shared = np.where(normalized == '', -1, shared)
    out = shared[codes]
    bounds = np.cumsum([0] + [len(column) for column in columns])
    return [out[start:end] for start, end in zip(bounds[:-1], bounds[1:])]


def _positions(index: pd.Index, labels: pd.Series) -> np.ndarray:
    """The positions of ``labels`` in ``index``: a label it lacks is an
    error, never its last row."""
    positions = index.get_indexer(labels.to_numpy())
    if (positions < 0).any():
        raise KeyError("a pair names a row its frame does not have")
    return positions


def _pair_positions(pairs: pd.DataFrame, universe_df: pd.DataFrame,
                    grants_df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """The positions of each pair's two rows in their frames."""
    return _positions(universe_df.index, pairs[INDEX_COLS[0]]), _positions(grants_df.index, pairs[INDEX_COLS[1]])


def _city_tails(grants_df: pd.DataFrame, rows: np.ndarray) -> list[pd.Series]:
    """For the grant tuples at positions ``rows`` that have no city, the
    candidates for it at the end of the address line (``_line_tails``); ''
    elsewhere. A recovered grant has no city column, and its list prints
    the city there: "15305 44th ave w lynnwood", "150 west 65th street, new
    york", "p.o. box 6281 richmond"."""
    # Twice _CITY_TAIL_WORDS: a line ending in a state code the tuple did
    # not vouch for gives its tails with the code and without it.
    tails = [np.full(len(grants_df), '', dtype=object) for _ in range(2 * _CITY_TAIL_WORDS)]
    no_city = _column(grants_df, 'addresscity_key').fillna('').astype(str).str.strip().eq('').to_numpy()
    rows = rows[no_city[rows]]
    if len(rows):
        frame = grants_df.iloc[rows]
        lines = (_column(frame, 'address1_key').fillna('').astype(str) + ' '
                 + _column(frame, 'address2_key').fillna('').astype(str))
        states = _column(frame, 'addressstate_key').fillna('').astype(str)
        for row, line, state in zip(rows, lines, states):
            for k, tail in enumerate(_line_tails(line, _state(state))):
                tails[k][row] = tail
    return [pd.Series(tail, index=grants_df.index) for tail in tails]


def location_agreement(pairs: pd.DataFrame, universe_df: pd.DataFrame, grants_df: pd.DataFrame,
                       positions: tuple[np.ndarray, np.ndarray] | None = None) -> pd.DataFrame:
    """Where the two locations of each pair agree, on the pairs' index:
    ``same_state``, ``same_city`` and ``same_zip`` (False where either side
    lacks the value), and the pair's geographic ``tier``:

    - 3, local: the same city in the same state; or the same city and the
      same ZIP, whatever the states say (a grant to Americus IL 31709 at a
      Georgia street address is in Americus GA); or the same ZIP with an
      address score of at least ``LOCAL_ADDRESS_MIN``;
    - 2, state: the same state, nothing local;
    - 1, unknown: nothing agrees and nothing contradicts;
    - 0, conflict: two states that differ, nothing local.

    Cities compare as the address score writes them (``_place``: FT
    LAUDERDALE is FORT LAUDERDALE). A grant tuple with no city takes the
    candidate's city as its own when its address line ends with it
    (``_city_tails``): that is where a recovered grant's list prints it. A
    missing value is unknown, never a contradiction, and an absent ZIP is
    never local evidence. Each value is normalized once per distinct
    value, and the pairs compare integer codes. ``positions`` are the
    pairs' row positions in the two frames (``_pair_positions``) when the
    caller has them already.
    """
    u_pos, g_pos = positions if positions is not None else _pair_positions(pairs, universe_df, grants_df)
    u_state, g_state = _codes([_column(universe_df, 'addressstate_key'), _column(grants_df, 'addressstate_key')],
                              _state)
    u_zip, g_zip = _codes([_column(universe_df, 'addresszip_key'), _column(grants_df, 'addresszip_key')], _zip)
    tails = _city_tails(grants_df, np.unique(g_pos))
    u_city, g_city, *g_tails = _codes(
        [_column(universe_df, 'addresscity_key'), _column(grants_df, 'addresscity_key'), *tails], _place)

    us, gs = u_state[u_pos], g_state[g_pos]
    uz, gz = u_zip[u_pos], g_zip[g_pos]
    uc, gc = u_city[u_pos], g_city[g_pos]
    same_state = (us >= 0) & (us == gs)
    same_zip = (uz >= 0) & (uz == gz)
    in_line = np.zeros(len(pairs), dtype=bool)
    for tail in g_tails:
        in_line |= uc == tail[g_pos]
    same_city = (uc >= 0) & ((uc == gc) | ((gc < 0) & in_line))
    address = (pairs['addr_score'].to_numpy(dtype=float) if 'addr_score' in pairs
               else np.zeros(len(pairs)))
    local = (same_city & (same_state | same_zip)) | (same_zip & (address >= LOCAL_ADDRESS_MIN))
    conflict = (us >= 0) & (gs >= 0) & (us != gs) & ~local
    tier = np.ones(len(pairs), dtype=np.int8)
    tier[same_state] = 2
    tier[local] = 3
    tier[conflict] = 0
    return pd.DataFrame({'same_state': same_state, 'same_city': same_city, 'same_zip': same_zip, 'tier': tier},
                        index=pairs.index)


def _names_held(pairs: pd.DataFrame, universe_df: pd.DataFrame, grants_df: pd.DataFrame,
                positions: tuple[np.ndarray, np.ndarray] | None = None) -> np.ndarray:
    """Does each pair's filer name hold every word of the grant's cleaned
    name? The word sets are built once per row the pairs name;
    ``positions`` as in ``location_agreement``."""
    u_pos, g_pos = positions if positions is not None else _pair_positions(pairs, universe_df, grants_df)
    u_names, g_names = universe_df['full_name'].to_numpy(), grants_df['full_name'].to_numpy()
    u_words = {p: frozenset(str(u_names[p]).split()) for p in np.unique(u_pos)}
    g_words = {p: frozenset(str(g_names[p]).split()) for p in np.unique(g_pos)}
    return np.fromiter((bool(g_words[g]) and g_words[g] <= u_words[u] for u, g in zip(u_pos, g_pos)),
                       dtype=bool, count=len(u_pos))


def _one_filer_refused(matches: pd.DataFrame, grants_df: pd.DataFrame, filers_in_state: pd.Series,
                       one_filer_in_state: str) -> pd.Series:
    """The pairs the one-filer-in-the-state rule refuses: pairs only the
    exact-name tier accepts, of a grant tuple whose name several filers of
    its state carry, held to tuples with no street address ("street") or
    to every tuple ("all")."""
    shared = (matches['match_tier'] == STATE_TIER) & (
        matches[INDEX_COLS[1]].map(filers_in_state).fillna(1) > 1)
    if one_filer_in_state == "street":
        lines = grants_df['address1_key'] + " " + grants_df['address2_key']
        shared &= matches[INDEX_COLS[1]].map(~lines.str.contains(_STREET)).eq(True)
    return shared


def resolve_matches(features: pd.DataFrame, universe_df: pd.DataFrame, grants_df: pd.DataFrame,
                    one_filer_in_state: str = ONE_FILER_IN_STATE, *, guards: bool = True,
                    ranking: str = RANKING, placeholder: pd.Series | None = None) -> pd.DataFrame:
    """The final rules, then one universe row per grant tuple, with the
    tier the match rests on (``match_tier``).

    A grant tuple that names no recipient (``placeholder_names``, or
    ``placeholder`` when the caller has that mask already) matches
    nothing: the view leaves such tuples out of a run, and this holds the
    same for a slice. The government-name guard refuses its pairs before
    any winner is picked.

    **The winner** (``ranking``). Under "named_first", among the pairs the
    rules accept for a grant tuple:

    1. a candidate whose state contradicts the grant's comes last
       (``location_agreement``'s conflict tier): "saint pauls episcopal
       church" at the grant's street in Kansas City MO, name 0.87, beats
       an exact "st pauls episcopal church" across the state line;
    2. then a named candidate: an exact name, ``name_score`` at the
       threshold of the exact-name tier, so a name that scores 1 is exact
       whatever its punctuation ("us fund for unicef", "u.s. fund for
       unicef"), or a name that holds every word of the grant's
       (``_names_held``). A named candidate elsewhere in the state beats
       a closer address under another name in the grant's city: "medical
       teams international" in Tigard takes a Portland PO box grant from
       "mt hood medical center foundation" at a box string one digit
       away, and "jewish community foundation of los angeles", at the
       grant's address, keeps a Los Angeles grant to "jewish community
       foundation" from the exact namesake in San Diego;
    3. then the best geographic support: a candidate in the grant's own
       city before one elsewhere in its state, before one with no
       location to compare;
    4. then an exact name;
    5. then, among exact names only, a filing or a correction before a BMF
       line (``ALIAS_SOURCES``): the Johns Hopkins University's own filing
       before the BMF line of a student chapter that carries its name.
       Below an exact name the source decides nothing, and the closer name
       wins: "friends of the springfield public library" (0.93), not a
       filing named "springfield public library foundation" (0.74);
    6. then 2*name_score + addr_score;
    7. then the order of the pairs: the sort is stable, and chunks
       concatenate in index order.

    Under "weighted", step 6 alone decides, as it did in every run up to
    2026-10-27.

    **One filer in the state.** A pair that only the exact-name tier
    accepts has the name and the state for it and nothing else. When
    several filers of the state carry the name, such a pair cannot say
    which of them a grant tuple with no street address (no digit in its
    address lines) meant (``ONE_FILER_IN_STATE``). Under every order but
    "weighted" the rule refuses those pairs before the winner is
    picked, so a candidate
    the address tiers accept can still win the tuple; when none does, the
    tuple is left unmatched. Under "weighted" it looks at the winner only
    and leaves its tuple unmatched, as it did. A tuple with a street
    address keeps the pick of the address: held to every tuple, the rule
    took 359 rows and $52M of the sampled foundations' itemised grants on
    2026-10-01, most of them to a university or a hospital at its own
    street address.
    """
    if ranking not in RANKINGS:
        raise ValueError(f"ranking must be one of {RANKINGS}, not {ranking!r}")
    matches = filter_match_rules(features_df=features, **FINAL_FILTER_RULES)
    if placeholder is None:
        placeholder = placeholder_names(grants_df)
    # A boolean array: an empty column maps to float64, which would select
    # columns instead of rows.
    named_nothing = matches[INDEX_COLS[1]].map(placeholder).eq(True).to_numpy()
    # filter_match_rules and the mask give boolean-mask slices (views). One
    # explicit copy after both, so the assignments below do not raise
    # SettingWithCopyWarning on a view.
    matches = matches[~named_nothing].copy()
    if guards:
        matches = guard_government_names(matches, grants_df["full_name"], universe_df["full_name"])
    logger.info(f"Found {len(matches)} matches.")
    matches = matches.drop_duplicates()
    matches['match_tier'] = pd.Series(STATE_TIER, index=matches.index).where(~_by_address(matches), ADDRESS_TIER)
    # The filers of the tuple's state that carry its name: the pairs the
    # exact-name tier accepts.
    exact_min = FINAL_FILTER_RULES['exact_name_name_min']
    by_state = matches[(matches['name_score'] >= exact_min) & (matches['state_score'] == 1)]
    filers_in_state = (
        by_state.assign(_ein=by_state[INDEX_COLS[0]].map(universe_df['filerein_key']))
        .groupby(INDEX_COLS[1])['_ein'].nunique()
    )
    if ranking != "weighted" and one_filer_in_state != "none":
        refused = _one_filer_refused(matches, grants_df, filers_in_state, one_filer_in_state)
        tuples = pd.Index(matches.loc[refused, INDEX_COLS[1]].unique())
        matches = matches[~refused]
        left = int((~tuples.isin(matches[INDEX_COLS[1]])).sum())
        logger.info(
            f"One filer in the state: {int(refused.sum())} pairs of {len(tuples)} grant tuples refused, "
            f"their name shared by several filers of their state; {left} of the tuples left unmatched."
        )

    pre_resolve_count = len(matches)
    matches = matches.assign(_combined_score=matches['name_score'] * 2 + matches['addr_score'])
    if ranking != "weighted":
        sources = (universe_df['source'] if 'source' in universe_df
                   else pd.Series('', index=universe_df.index))
        exact = matches['name_score'] >= exact_min
        positions = _pair_positions(matches, universe_df, grants_df)
        matches['_geography'] = location_agreement(matches, universe_df, grants_df, positions)['tier'].to_numpy()
        matches['_not_conflict'] = matches['_geography'] > 0
        matches['_exact'] = exact
        matches['_exact_preferred'] = exact & ~matches[INDEX_COLS[0]].map(sources).isin(ALIAS_SOURCES)
        matches['_named'] = exact.to_numpy() | _names_held(matches, universe_df, grants_df, positions)
    # Stable sorting so complete ranking ties resolve by input
    # order (which is deterministic: chunks concat in index order). Matters
    # more now that correction rows can tie with organic universe rows for
    # the same recipient tuple.
    matches = matches.sort_values(_ORDERS[ranking], ascending=False, kind='mergesort')
    matches = matches.drop_duplicates(subset=[INDEX_COLS[1]], keep='first')
    matches = matches.drop(columns=['_combined_score', '_geography', '_not_conflict', '_named', '_exact',
                                    '_exact_preferred'], errors='ignore')
    logger.info(
        f"Resolved multi-matches ({ranking}): {pre_resolve_count} -> {len(matches)} "
        f"pairs after keeping the best basic_fields match per private_foundations record."
    )

    if ranking == "weighted" and one_filer_in_state != "none":
        shared = _one_filer_refused(matches, grants_df, filers_in_state, one_filer_in_state)
        logger.info(
            f"One filer in the state: {int(shared.sum())} grant tuples left unmatched, "
            f"their name shared by several filers of their state."
        )
        matches = matches[~shared]
    return matches


def no_us_address(grants_df: pd.DataFrame) -> pd.Series:
    """Which grant tuples give no US address: the state key is not a US
    state or territory (``US_STATE_KEYS``: empty, or a foreign province),
    or a row of the tuple names a country other than the US
    (``foreign_country``, the grants view's column, where the frame has
    it). One boolean per row, on the frame's index.

    A foreign address fills the US address columns, so until 2026-10-30
    the Global Fund's grants, keyed "ge" and "1218", were a tuple with a
    state for the tiers: the exact-name tier asks for the state to agree
    with a filer's, which a province never does, and the name-only tier
    asked for no state at all. Measured on the rows of 2026-10-30: every
    domestic row of our extract carries a state code, and GT's rows with an
    empty state key are foreign (Paris, Jerusalem, Nairobi, Beijing),
    whatever their zip key; the country column is set on every foreign row
    of our extract and on 7 of GT's, which carry USA on 1.5M domestic rows
    and nothing on the rest (docs/foreign-recipients-name-only-2026-10-30.md).
    """
    foreign = ~us_state(grants_df['addressstate_key'])
    if 'foreign_country' in grants_df:
        foreign |= grants_df['foreign_country'].eq(True)
    return foreign


def one_word_namesakes(grants_df: pd.DataFrame, filers_df: pd.DataFrame) -> np.ndarray:
    """The one-word guard of the name-only tier: which name-only matches it
    refuses. ``grants_df`` holds the matched grant tuples and ``filers_df``
    the universe row each would match, row for row. A match is refused when
    all four hold:

    - the tuple has a foreign address in its keys: no US address
      (``no_us_address``) and a state or a zip key. A tuple with no address
      at all is matched as before 2026-10-30;
    - its cleaned name is one word;
    - the filer's row is at a US address (``US_STATE_KEYS``);
    - the filer's EIN is not a 98- EIN, the IRS's prefix for a foreign
      organization, which is the recipient's own registration.

    A name that one filer alone carries is strong evidence when it has
    several words and weak when it has one: unrelated groups share an
    acronym or a short word. The proof of the rule found grants to CNRS in
    Paris going to a CNRS in Nebraska, to BRAC in Dhaka to a BRAC in
    Nebraska, to FAU in Erlangen to an FAU in South Carolina, and to the UK
    charities Crisis, Catch22 and Foxglove to namesakes in Michigan and
    Texas: 246 tuples, 400 rows and $44M, most of them another
    organization. The guard refuses a few right ones with them (EarthEnable,
    a US nonprofit working in Rwanda, $1.5M). Zein chose it on 2026-10-30
    (docs/foreign-recipients-name-only-2026-10-30.md). A longer namesake
    (the Hospital for Sick Children in Washington for SickKids in Toronto)
    is for the corrections registry.
    """
    keyed = (grants_df['addressstate_key'].fillna('').str.strip() != '') | (
        grants_df['addresszip_key'].fillna('').str.strip() != '')
    abroad = (no_us_address(grants_df) & keyed).to_numpy()
    one_word = (grants_df['full_name'].str.split().str.len() == 1).to_numpy()
    filer_in_us = us_state(filers_df['addressstate_key']).to_numpy()
    domestic = ~filers_df['filerein_key'].astype(str).str.startswith('98').to_numpy()
    return abroad & one_word & filer_in_us & domestic


def name_only_matches(universe_df: pd.DataFrame, grants_df: pd.DataFrame, matched,
                      placeholder: pd.Series | None = None, eligible: pd.Series | None = None,
                      one_word_guard: bool = True) -> pd.DataFrame:
    """The name-only tier: a grant tuple with no US address, which no other
    tier matched, and whose cleaned name belongs to exactly one filer of
    the universe. ``placeholder`` is the mask of ``placeholder_names`` when
    the caller has it already; ``eligible`` is the mask of the tuples the
    tier may take, ``no_us_address`` unless a proof passes another.

    A name several filers share is never matched, whatever its length:
    that is what rules out most one-word names. A tuple with a US state is
    not for this tier; the exact-name tier matches it where the state
    agrees, and nothing matches it where it does not. A tuple with a
    foreign address (a province, a postal code, a country other than the
    US) has no US state to agree with anything: it is for this tier, as a
    tuple with no address is, but for a one-word name whose only filer is
    a domestic organization at a US address (``one_word_namesakes``;
    ``one_word_guard`` sets the guard aside to measure it).

    Returns one row per tuple matched: the two positions (``INDEX_COLS``;
    the universe row is the filer's first row under the name) and
    ``match_name_words``, the number of words in the cleaned name, so a
    consumer can be stricter than the rule.
    """
    open_ = grants_df[
        (no_us_address(grants_df) if eligible is None else eligible)
        & (grants_df['full_name'] != "")
        & ~(placeholder_names(grants_df) if placeholder is None else placeholder)
        & ~grants_df.index.isin(matched)
    ]
    named = universe_df.loc[universe_df['full_name'].isin(set(open_['full_name'])), ['full_name', 'filerein_key']]
    owners = named.groupby('full_name')['filerein_key'].nunique()
    first = named[named['full_name'].isin(owners[owners == 1].index)].drop_duplicates('full_name')
    row_of = pd.Series(first.index, index=first['full_name'])
    found = open_[open_['full_name'].isin(row_of.index)]
    rows = row_of.reindex(found['full_name']).to_numpy()
    if one_word_guard and len(found):
        refused = one_word_namesakes(found, universe_df.loc[rows])
        if refused.any():
            logger.info(f"Name-only tier: {int(refused.sum())} grant tuples with a foreign address refused, "
                        "their one-word name a domestic filer's at a US address.")
        found, rows = found[~refused], rows[~refused]
    return pd.DataFrame({
        INDEX_COLS[0]: rows,
        INDEX_COLS[1]: found.index.to_numpy(),
        'match_name_words': found['full_name'].str.split().str.len().to_numpy(),
    }).astype('int64').assign(match_tier=NAME_ONLY)


def all_matches(features: pd.DataFrame, universe_df: pd.DataFrame, grants_df: pd.DataFrame,
                one_filer_in_state: str = ONE_FILER_IN_STATE, *, guards: bool = True,
                ranking: str = RANKING, eligible: pd.Series | None = None,
                one_word_guard: bool | None = None) -> pd.DataFrame:
    """Every match of a run, one row per grant tuple matched: the winners
    of the scored pairs under ``ranking``, then the name-only tier on what
    they left. ``match_tier`` says which; ``match_name_words`` is missing
    but on a name-only match. ``eligible`` and ``one_word_guard`` are the
    name-only tier's (``name_only_matches``): a proof passes them to
    measure another rule. ``guards`` sets the precision guards aside for a
    gain measurement, the one-word guard with them unless
    ``one_word_guard`` says otherwise."""
    one_word_guard = guards if one_word_guard is None else one_word_guard
    placeholder = placeholder_names(grants_df)  # one pass of the pattern over the grant tuples
    matches = resolve_matches(features, universe_df, grants_df, one_filer_in_state, guards=guards,
                              ranking=ranking, placeholder=placeholder)
    name_only = name_only_matches(universe_df, grants_df, matches[INDEX_COLS[1]], placeholder, eligible=eligible,
                                  one_word_guard=one_word_guard)
    logger.info(f"Name-only tier: {len(name_only)} grant tuples matched on a name that belongs to one filer.")
    name_only = name_only.assign(name_score=1.0, addr_score=float("nan"))
    matches = pd.concat([matches.assign(match_name_words=pd.NA), name_only], ignore_index=True)
    matches['match_name_words'] = matches['match_name_words'].astype('Int64')
    return matches


def score_slice(universe_slice: pd.DataFrame, grants_slice: pd.DataFrame, chunk_rules: bool = True) -> pd.DataFrame:
    """Blocking and the scores on a slice that fits in memory, without
    chunks or checkpoints. The frames are copied, so a slice of a larger
    frame is left as it was; ``compare_addr`` is added here when the frame
    was prepared without it."""
    u = universe_slice.copy()
    g = grants_slice.copy()
    for df in (u, g):
        if 'compare_addr' not in df:
            add_compare_addr(df)
    pairs = candidate_pairs(u, g)
    logger.info(f"slice candidate pairs: {len(pairs):,}")
    return score_pairs(pairs, u, g, chunk_rules=chunk_rules)


def match_slice(universe_slice: pd.DataFrame, grants_slice: pd.DataFrame,
                ranking: str = RANKING) -> pd.DataFrame:
    """A run on a slice: block, score, the two stages of rules, the winner
    of each grant tuple, the name-only tier. One row per matched grant
    tuple, as ``all_matches`` gives them.

    The decisions that pick a grant tuple's winner are per pair, so a
    slice gives what the full run gives for its grant tuples when it holds
    every universe row that can block with them (``corrections_preflight``
    has the argument in full).
    """
    return all_matches(score_slice(universe_slice, grants_slice), universe_slice, grants_slice, ranking=ranking)


def matched_tuples(universe_df: pd.DataFrame, grants_df: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """The join table: the seven keys of each matched grant tuple, the EIN
    it matched, ``match_source``, ``match_tier`` and ``match_name_words``."""
    # `source` comes from the basic_fields (filer-universe) side of the merge:
    # which arm of the universe union supplied the winning identity row. It
    # is the arm whatever the tier: a name-only match on a name only a
    # correction row carries says `correction`, the sign that row earns its
    # place (docs/corrections-plan.md).
    found = matches[INDEX_COLS + ['match_tier', 'match_name_words', 'name_score', 'addr_score']].merge(
        universe_df[['filerein_key', 'source', 'full_name']].rename(columns={'full_name': 'match_name'}),
        left_on=INDEX_COLS[0], right_index=True,
    ).merge(
        grants_df[_KEYS], left_on=INDEX_COLS[1], right_index=True,
    )
    found = found[['filerein_key'] + _KEYS + ['source', 'match_tier', 'match_name_words', 'match_name', 'name_score', 'addr_score']].drop_duplicates()
    return found.rename(columns={
        'filerein_key': 'recipeint_ein_key',
        'source': 'match_source',
        'name_score': 'match_name_score',
        'addr_score': 'match_addr_score',
    })


def _write_privategrants_w_recipients(connection, full_data_df: pd.DataFrame,
                                      relations: Relations = Relations()) -> int:
    """Write the join table, then build ``privategrants_w_recipients``
    from it, with its filerein index and planner statistics, under the
    run's names. Returns its row count."""
    temp_join_table_name = relations.of("pf_grant_matching_temp_table")
    keys_view = relations.of("privategrants_w_column_keys_view")
    private_grants_w_recipient_table_name = relations.of("privategrants_w_recipients")

    logger.info(f"Writing to database: {temp_join_table_name}")
    full_data_df.to_sql(
        temp_join_table_name,
        connection,
        schema="public",
        if_exists="replace"
    )

    # A table just written has no statistics, and the join below is
    # planned on what the planner guesses of it.
    connection.execute(text(f"ANALYZE public.{temp_join_table_name}"))

    logger.info(f"Dropping table: {private_grants_w_recipient_table_name}")
    connection.execute(text(f"DROP TABLE IF EXISTS public.{private_grants_w_recipient_table_name}"))
    logger.info(f"Creating table: {private_grants_w_recipient_table_name}")
    # Joins against the *_view rather than a materialized table — only the
    # view exists in gt_datamart's public schema. SELECT INTO materializes
    # the join result into a real table.
    connection.execute(text(f"""
        SELECT
            pg.*,
            pfgm.recipeint_ein_key,
            pfgm.match_source,
            pfgm.match_tier,
            pfgm.match_name_words,
            pfgm.match_name,
            pfgm.match_name_score,
            pfgm.match_addr_score
        INTO public.{private_grants_w_recipient_table_name}
        FROM public.{keys_view} pg
        JOIN public.{temp_join_table_name} pfgm
            ON pfgm.name1_key = pg.name1_key
            AND pfgm.name2_key = pg.name2_key
            AND pfgm.address1_key = pg.address1_key
            AND pfgm.address2_key = pg.address2_key
            AND pfgm.addresscity_key = pg.addresscity_key
            AND pfgm.addressstate_key = pg.addressstate_key
            AND pfgm.addresszip_key = pg.addresszip_key
        ;
    """))
    # NOTE: Uncomment this to drop the temporary table but leave for now for debugging
    # connection.execute(text(f"DROP TABLE IF EXISTS public.{temp_join_table_name}"))

    # Post-build maintenance (previously a manual post-rebuild step):
    # SELECT INTO creates tables with no indexes and no planner stats —
    # downstream consumers (e.g. the canonical pf build step) time out
    # without the ANALYZE, and the filerein index doesn't survive the
    # DROP/SELECT INTO cycle. With the table, so that what commits is
    # complete (--commit-between-outputs commits it on its own).
    logger.info("Recreating filerein index and refreshing planner stats")
    connection.execute(text(
        f"CREATE INDEX IF NOT EXISTS ix_{private_grants_w_recipient_table_name}_filerein "
        f"ON public.{private_grants_w_recipient_table_name} USING btree (filerein)"
    ))
    connection.execute(text(f"ANALYZE public.{private_grants_w_recipient_table_name}"))
    # Its row count, read in the session that wrote it, so it is consistent
    # with what commits. Bubbled up to the canonical_builds row.
    return connection.execute(
        text(f"SELECT COUNT(*) FROM public.{private_grants_w_recipient_table_name}")
    ).scalar_one()


def _write_unioned_grants(connection, relations: Relations = Relations()) -> int:
    """Rebuild ``unioned_grants`` from the freshly written
    ``privategrants_w_recipients`` and the 990 Schedule I grants, with its
    planner statistics. Returns its row count, read in the session that
    wrote it."""
    unioned = relations.of("unioned_grants")
    rebuild_unioned_grants(connection, relations)
    connection.execute(text(f"ANALYZE public.{unioned}"))
    return connection.execute(text(f"SELECT COUNT(*) FROM public.{unioned}")).scalar_one()


def write_matches(connection, full_data_df: pd.DataFrame, relations: Relations = Relations()) -> tuple[int, int]:
    """Write the join table, then build ``privategrants_w_recipients`` and
    ``unioned_grants`` from it, under the run's names, on one connection.
    One transaction by design: a mid-pipeline failure rolls both tables
    back to what the last run left, never a new
    ``privategrants_w_recipients`` next to a stale ``unioned_grants``.
    Returns the row counts of the two."""
    pg_rows = _write_privategrants_w_recipients(connection, full_data_df, relations)
    return pg_rows, _write_unioned_grants(connection, relations)


def _record_privategrants_w_recipients(connection, build_id: str, pg_rows: int) -> None:
    """The build row's count of ``privategrants_w_recipients``, written in
    the transaction that replaced the table (``--commit-between-outputs``):
    a failed row with this count and no ``unioned_grants_rows`` says which
    table the build replaced, where ``_finalize_build_failed`` records only
    the error."""
    connection.execute(
        text(f"UPDATE {CANONICAL_BUILDS_TABLE} SET privategrants_w_recipients_rows = :pg_rows "
             "WHERE build_id = :build_id"),
        {"build_id": build_id, "pg_rows": pg_rows},
    )


def _write_outputs(full_data_df: pd.DataFrame, commit_between_outputs: bool = False,
                   build_id: str | None = None) -> tuple[int, int]:
    """The write phase of a production run: ``write_matches`` in one
    transaction, or, with ``commit_between_outputs``,
    ``privategrants_w_recipients`` committed before ``unioned_grants`` is
    rebuilt.

    Postgres frees a dropped table's files only at commit, so one
    transaction holds the old copies of the join table,
    ``privategrants_w_recipients`` and ``unioned_grants`` until it ends
    (README, Storage). The join table, the last run's debugging copy of
    the matched tuples, goes first in a transaction of its own: ``to_sql``
    replaces it moments later, and nothing reads it in between. A commit
    after ``privategrants_w_recipients`` frees its old copy too before
    ``unioned_grants`` is rebuilt, a shallower dip at the cost of
    ``write_matches``'s guarantee: a failure in the second transaction
    leaves a new ``privategrants_w_recipients`` next to a stale
    ``unioned_grants``, the build row failed with the new table's count
    (``_record_privategrants_w_recipients``), until the matcher is rerun
    (``--skip-rebuild``: the chunks are resumed from S3). Off by default.
    """
    relations = Relations()
    with get_session(config=datamart_config()) as session:
        session.connection().execute(
            text(f"DROP TABLE IF EXISTS public.{relations.of('pf_grant_matching_temp_table')}"))
    if not commit_between_outputs:
        with get_session(config=datamart_config()) as session:
            return write_matches(session.connection(), full_data_df)
    with get_session(config=datamart_config()) as session:
        connection = session.connection()
        pg_rows = _write_privategrants_w_recipients(connection, full_data_df)
        if build_id is not None:
            _record_privategrants_w_recipients(connection, build_id, pg_rows)
    with get_session(config=datamart_config()) as session:
        return pg_rows, _write_unioned_grants(session.connection())


def match_records(
    chunk_size: int = 50000,
    s3_bucket: str = "givingtuesday-datamart",
    s3_prefix: str | None = None,
    resume_from_checkpoints: bool = True,
    resume_workers: int = 32,
    limit: int | None = None,
    recovered_policy: str | None = None,
    skip_rebuild: bool = False,
    workers: int = 1,
    ranking: str = RANKING,
    commit_between_outputs: bool = False,
):
    """Run the recordlinkage grant-matching pipeline against gt_datamart.

    ``s3_prefix`` defaults to a lineage-keyed path derived from the latest
    successful ingest_runs of irs_990pf_grants + irs_990_basic_fields +
    irs_990pf_basic_fields, plus the corrections CSV content hash, the
    recovered grants' policy and digest, and the input shape version, e.g.
    ``grant_matching_checkpoints/pg_2026_04_15/bf_2026_04_18/bfpf_2026_04_18/corr_1a2b3c4d/rec_v2_5e6f7a8b/shape_v3``.
    This means
    checkpoints can only be resumed against the exact source versions (and
    corrections file, and recovered grants) that produced them —
    re-ingesting a source, editing the corrections CSV or loading recovered
    grants forces a clean recompute.
    Pass an explicit string to override (e.g. for testing).

    The recovered grants that take the place of the placeholder rows
    (``placeholder_recovery``) are those of the policy the view shows.
    ``recovered_policy`` names it when there is no view to read it from;
    the run stops before it rebuilds anything when there is no view and no
    policy, or when the view shows another policy than the one named.

    ``limit`` (test-only): caps both view reads to the first N rows. Used
    to validate the chunk write/read round-trip end-to-end without paying
    for a full multi-hour run. When set, chunks are namespaced under a
    ``_test_limit_<N>`` subdirectory so they can't be confused with
    production chunks (the row positions differ between subsets and would
    silently produce wrong matches if mixed). The output tables
    ``public.privategrants_w_recipients`` and ``public.unioned_grants``
    are still rebuilt — small data while testing, restored to full data
    on the next non-limited run.

    ``ranking`` names the winner order (``RANKINGS``): "named_first" by
    default, "weighted" for the order of the runs up to 2026-10-27. It
    acts after the checkpoints, so either one resumes the same chunks.

    ``commit_between_outputs`` writes the two output tables in two
    transactions instead of one, ``privategrants_w_recipients`` committed
    before ``unioned_grants`` is rebuilt (``_write_outputs``): less of the
    shared instance's free storage at the deepest point, and the two
    tables from different runs after a failure in the second. Off by
    default.

    Each run is recorded in ``datamart_meta.canonical_builds`` with
    ``build_kind='grant_matching'``, the ingest_run_ids of every staging
    source at run time, the ranking (``source_runs -> 'matcher'``), and
    (on success) the row counts of
    ``public.privategrants_w_recipients`` and ``public.unioned_grants``.
    Consumers can join against ``datamart_meta.ingest_runs`` to detect
    when the matched/unioned tables are stale relative to upstream.
    """
    if ranking not in RANKINGS:
        raise ValueError(f"ranking must be one of {RANKINGS}, not {ranking!r}")
    ensure_canonical_meta()
    build_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    source_runs = latest_successful_run_ids()
    # The corrections registry is a matching input like the staging tables;
    # stamp its content hash next to the ingest run ids. (Validates the CSV
    # as a side effect — a malformed file fails here, before any compute.)
    source_runs["corrections_org_identities"] = {
        "content_hash": _corrections_content_hash(),
        "rows": len(_read_corrections_csv()),
    }
    # The winner order is the matcher's own input: the same sources under
    # another ranking give other tables.
    source_runs["matcher"] = {"ranking": ranking}
    # So are the recovered grants; the run reads them once, after the
    # rebuild, and stamps them on this row (``_stamp_recovered``).
    _insert_started_build(build_id, started_at, source_runs)
    logger.info(f"Starting grant matching build {build_id}")

    try:
        full_data_df, pg_rows, ug_rows = _do_match_records(
            chunk_size=chunk_size,
            s3_bucket=s3_bucket,
            s3_prefix=s3_prefix,
            resume_from_checkpoints=resume_from_checkpoints,
            resume_workers=resume_workers,
            limit=limit,
            recovered_policy=recovered_policy,
            build_id=build_id,
            skip_rebuild=skip_rebuild,
            workers=workers,
            ranking=ranking,
            commit_between_outputs=commit_between_outputs,
        )
    except BaseException as err:
        _finalize_build_failed(build_id, err)
        logger.exception(f"Grant matching build {build_id} failed")
        raise

    _finalize_build_success(build_id, pg_rows, ug_rows)
    duration = (datetime.now(timezone.utc) - started_at).total_seconds()
    logger.info(
        f"Grant matching build {build_id} success: "
        f"privategrants_w_recipients={pg_rows:,}, "
        f"unioned_grants={ug_rows:,}, duration={duration:.1f}s"
    )
    return full_data_df


# What a chunk worker needs, set by the parent right before the pool forks
# so the workers inherit it (copy-on-write): no pickling of the two frames
# and the pair index per task.
_POOL_STATE: dict = {}


def _chunk_job(chunk_idx: int) -> tuple[int, pd.DataFrame]:
    """One chunk in a worker: score it, write its checkpoint, return the rows.
    The same three statements as the serial loop, so a chunk is written only
    when fully computed and filtered, whichever path computed it."""
    st = _POOL_STATE
    start_idx = chunk_idx * st["chunk_size"]
    end_idx = min(start_idx + st["chunk_size"], st["total_pairs"])
    chunk = st["candidate_links"][start_idx:end_idx]
    features = score_pairs(chunk, st["universe_df"], st["grants_df"])
    pqc.write_dataframe(features, f"{st['uri_base']}/chunk_{chunk_idx:05d}.parquet")
    return chunk_idx, features


def _load_inputs(
    s3_prefix: str | None,
    limit: int | None,
    recovered_policy: str | None,
    build_id: str | None,
    skip_rebuild: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """The run's database phases, each a transaction of its own: the
    bookkeeping, the rebuild of the ``_current`` relations (one transaction
    per relation, ``build_current_relations``), then the corrections, the
    views and the two reads. Returns the universe, the grants and the
    checkpoint prefix. Up to 2026-10-30 one transaction held all of it, so
    the old copies of the six relations were freed only when the reads
    ended (README, Storage).
    """
    # 1. The bookkeeping, before anything is rebuilt: the policy the view
    # shows (the rebuild drops the view, and with it what says which policy
    # it showed), the source versions the checkpoint prefix is keyed on (a
    # source with no successful run stops the run here, not after an hour
    # of builds), and which relations carry their stamp.
    with get_session(config=datamart_config()) as session:
        connection = session.connection()
        recovered_policy = recovered_policy_of(recovered_view.shown_policy(connection), recovered_policy)
        versions = _source_versions(connection)
        stale = _unstamped_current_relations(connection, versions)
    # 2. The rebuild of the filing-version-deduped inputs (issues #33/#34):
    # every relation, or (--skip-rebuild) the ones that do not carry the
    # stamp of these versions under this input shape, and the relations
    # built from them. Each relation is stamped, and the views its DROP ...
    # CASCADE took are recreated, in the transaction that builds it
    # (after_current_build), so a failure leaves every relation either
    # complete and stamped or untouched, the views in place, and the build
    # row failed (match_records); the run after it rebuilds what lacks the
    # stamp.
    if skip_rebuild:
        to_build = with_dependents(stale)
        logger.info("--skip-rebuild: %s carry the stamp of the current source versions and input shape and are "
                    "taken as they stand; %s rebuilt",
                    ", ".join(t for t, _ in _CURRENT_TABLES if t not in stale) or "no relations",
                    ", ".join(t for t, _ in to_build) or "none")
    else:
        to_build = list(_CURRENT_TABLES)
    if to_build:
        build_current_relations(
            datamart_config(), to_build,
            after=partial(after_current_build, recovered_policy=recovered_policy,
                          stamp=_current_stamp(versions, build_id)))
    # 3. The corrections, the views and the two reads, one transaction at
    # the builds' memory budget: the TRUNCATE's lock holds the correction
    # rows the prefix's hash names until the universe read has them, as
    # the matcher's one transaction did.
    with get_session(config=datamart_config()) as session:
        connection = session.connection()
        apply_build_memory(connection)
        # The relations read carry the stamp of the versions the prefix
        # names, whichever path built them, and no source was ingested
        # since the versions were read: the per-relation commits hold no
        # lock on staging across the rebuild, where the one transaction did.
        stale = _unstamped_current_relations(connection, versions)
        if stale:
            raise RuntimeError(
                f"{', '.join('public.' + t for t in stale)} do not carry the stamp of the source versions and "
                "input shape the checkpoints are keyed on (rebuilt elsewhere during the run?); run again"
            )
        if _source_versions(connection) != versions:
            raise RuntimeError(
                "A source was ingested while the run rebuilt the _current relations "
                "(datamart_meta.ingest_runs moved); run again"
            )
        _load_corrections(connection)
        create_or_replace_views(connection, recovered_policy=recovered_policy)
        # The recovered grants the run takes, read once: the build's
        # source_runs and the checkpoint prefix name the same rows.
        recovered = recovered_digest(connection, recovered_policy)
        if build_id is not None:
            _stamp_recovered(build_id, recovered)
        if s3_prefix is None:
            s3_prefix = _resolve_checkpoint_prefix(connection, recovered, versions=versions)
            logger.info(f"Resolved checkpoint prefix from lineage: {s3_prefix}")
        else:
            logger.info(f"Using caller-supplied checkpoint prefix: {s3_prefix}")

        # Test mode: namespace chunks so they can't pollute production
        # checkpoints. Row positions in a limited subset don't correspond
        # to row positions in a full run — mixing the two would silently
        # produce wrong matches.
        if limit is not None:
            s3_prefix = f"{s3_prefix}/_test_limit_{int(limit)}"
            logger.info(
                f"limit={limit} (TEST MODE) — chunks namespaced under {s3_prefix}"
            )

        limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""

        logger.info(
            "Reading filer universe: basic_fields_unique_names_view "
            "∪ basic_fields_pf_unique_names_view ∪ eo_bmf_unique_names_view "
            "∪ corrections_unique_names_view"
        )
        basic_fields_df = pd.read_sql_query(text(universe_sql(limit_clause=limit_clause)), connection)
        logger.info("Reading public.privategrants_unique_names_view")
        private_foundations_df = pd.read_sql_query(text(grants_sql(limit_clause=limit_clause)), connection)
    return basic_fields_df, private_foundations_df, s3_prefix


def _do_match_records(
    chunk_size: int,
    s3_bucket: str,
    s3_prefix: str | None,
    resume_from_checkpoints: bool,
    resume_workers: int = 32,
    limit: int | None = None,
    recovered_policy: str | None = None,
    build_id: str | None = None,
    skip_rebuild: bool = False,
    workers: int = 1,
    ranking: str = RANKING,
    commit_between_outputs: bool = False,
):
    """Inner pipeline body. Returns ``(full_data_df, pg_rows, ug_rows)``.

    Split out from ``match_records`` so the canonical_builds lifecycle
    (insert started → try → finalize success/failed) wraps the whole
    pipeline cleanly without indenting 200 lines.
    """
    logger.info("Preparing data...")
    # 1. LOAD DATA
    basic_fields_df, private_foundations_df, s3_prefix = _load_inputs(
        s3_prefix, limit, recovered_policy, build_id, skip_rebuild)

    # --- 3. APPLY CLEANING ---
    logger.info("Cleaning zip codes, names and addresses...")
    prepare(basic_fields_df)
    prepare(private_foundations_df)

    # --- 5. EXECUTE BLOCKING ---
    logger.info("Indexing...")
    candidate_links = candidate_pairs(basic_fields_df, private_foundations_df)
    logger.info(f"Found {len(candidate_links)} pairs to compare.")

    # Chunked Compute
    logger.info("Chunking candidate links...")
    total_pairs = len(candidate_links)
    total_chunks = (total_pairs + chunk_size - 1) // chunk_size if total_pairs else 0
    logger.info(f"Found {total_chunks} chunks to compute.")

    checkpoint_uri_base = f"s3://{s3_bucket}/{s3_prefix}"
    logger.info(f"Using checkpoint directory: {checkpoint_uri_base}")

    # The candidate_links MultiIndex's level positions ARE the row positions
    # in basic_fields_df / private_foundations_df we need to merge back to
    # later. ``pqc.write_dataframe`` calls
    # ``pa.Table.from_pandas(df, preserve_index=False)`` — i.e. it silently
    # drops any index on write. So if we wrote chunks with the MultiIndex
    # intact, the row-position info would be permanently lost on a resumed
    # run (chunks come back with a meaningless RangeIndex). Materialize the
    # MultiIndex as named columns BEFORE every write, and use those columns
    # directly in post-processing — no reset_index / rename gymnastics.

    # --- Resume path: one S3 LIST + parallel GETs ---
    # Replaces the previous N HEAD + N serial GET pattern. At chunk_size=50K
    # with 13K chunks, this drops resume from ~30 minutes to ~1 minute.
    if resume_from_checkpoints:
        logger.info(f"Listing existing chunks under s3://{s3_bucket}/{s3_prefix}/ ...")
        existing = _list_existing_chunk_indices(s3_bucket, s3_prefix)
        in_range = sorted(idx for idx in existing if idx < total_chunks)
        logger.info(
            f"Found {len(in_range)}/{total_chunks} chunks already computed. "
            f"Reading in parallel ({resume_workers} workers)..."
        )
        list_prefix = s3_prefix.rstrip("/") + "/"
        resumed = _read_chunks_parallel(
            s3_bucket,
            [(idx, f"{list_prefix}chunk_{idx:05d}.parquet") for idx in in_range],
            max_workers=resume_workers,
        )
        # Detect chunks that predate the index-preservation fix (no index
        # columns on disk). Their row positions are unrecoverable; recompute.
        bad_idxs = [idx for idx, df in resumed.items() if not set(INDEX_COLS).issubset(df.columns)]
        if bad_idxs:
            logger.warning(
                f"{len(bad_idxs)} resumed chunks predate the index-preservation "
                f"fix (missing {INDEX_COLS}); recomputing those."
            )
            for idx in bad_idxs:
                del resumed[idx]
    else:
        resumed = {}

    results: dict[int, pd.DataFrame] = dict(resumed)
    to_compute = [idx for idx in range(total_chunks) if idx not in results]

    # --- Compute path. recordlinkage's compute() is single-threaded, so the
    # chunks are the unit of parallelism: ``workers`` processes forked from
    # here (the frames and the pair index inherited copy-on-write), each
    # scoring whole chunks and writing each one's checkpoint itself, so
    # every written chunk is fully computed and filtered whichever path made
    # it, and a resume reads them back the same way. Serial by default (one
    # worker): a laptop's memory. On 2026-10-27 the serial loop ran 45,855
    # chunks at 1.6 s each, eighteen hours, on one of an r6i.8xlarge's 32
    # cores; each worker gradually copies the pages of the string objects
    # it touches, a few GB each, so size ``workers`` to the box's memory.
    if to_compute:
        logger.info(f"Computing {len(to_compute)} new chunks with {workers} worker(s)...")
    if workers > 1 and to_compute:
        _POOL_STATE.update(chunk_size=chunk_size, total_pairs=total_pairs, candidate_links=candidate_links,
                           universe_df=basic_fields_df, grants_df=private_foundations_df,
                           uri_base=checkpoint_uri_base)
        try:
            with cf.ProcessPoolExecutor(max_workers=workers,
                                        mp_context=multiprocessing.get_context("fork")) as pool:
                for chunk_idx, features in tqdm(pool.map(_chunk_job, to_compute, chunksize=4),
                                                total=len(to_compute), desc="Computing"):
                    results[chunk_idx] = features
        finally:
            _POOL_STATE.clear()
    else:
        for chunk_idx in tqdm(to_compute, desc="Computing"):
            start_idx = chunk_idx * chunk_size
            end_idx = min(start_idx + chunk_size, total_pairs)
            chunk = candidate_links[start_idx:end_idx]
            # The (basic_fields_idx, privategrants_idx) MultiIndex comes back
            # as regular columns so it survives the parquet round-trip.
            features = score_pairs(chunk, basic_fields_df, private_foundations_df)

            pqc.write_dataframe(features, f"{checkpoint_uri_base}/chunk_{chunk_idx:05d}.parquet")
            results[chunk_idx] = features

    logger.info(
        f"Chunk processing complete: loaded {len(resumed)} from checkpoints, "
        f"computed {len(to_compute)} new chunks."
    )

    if not results:
        final_features = pd.DataFrame(columns=INDEX_COLS + SCORE_COLS)
    else:
        # Concat in chunk_idx order. Order doesn't strictly matter for
        # correctness (the index columns carry row positions), but preserves
        # determinism vs. the previous serial-loop behavior.
        ordered = [results[idx] for idx in sorted(results.keys())]
        final_features = pd.concat(ordered, ignore_index=True)
    logger.info(f"Finished matching")

    # --- 7. FILTER ---
    # The index columns are already regular columns on the features
    # (materialized at chunk-write time), so no reset_index / rename
    # gymnastics needed.
    logger.info(f"Filtering matches (ranking: {ranking})...")
    matches = all_matches(final_features, basic_fields_df, private_foundations_df, ranking=ranking)

    percentage_matched = (
        basic_fields_df.loc[matches[INDEX_COLS[0]].unique(), 'filerein_key'].nunique()
        / basic_fields_df['filerein_key'].nunique()
    )
    logger.info(f'% of filer orgs (990 + 990-PF) matched to a grant recipient name: {percentage_matched}')

    full_data_df = matched_tuples(basic_fields_df, private_foundations_df, matches)

    pg_rows, ug_rows = _write_outputs(full_data_df, commit_between_outputs, build_id)
    return full_data_df, pg_rows, ug_rows


if __name__ == "__main__":
    import argparse
    import logging

    # The package logger has no handler of its own: without this an
    # eight-hour run shows nothing but warnings on the console.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(
        description="Run the grant matching pipeline against gt_datamart."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Test mode: cap both view reads to N rows. Chunks are written "
            "to a `_test_limit_<N>` subdirectory of the lineage-keyed "
            "prefix so they can't be confused with production chunks. "
            "Output tables (privategrants_w_recipients, unioned_grants) "
            "are still rebuilt — small data while testing, restored on "
            "the next non-limited run."
        ),
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help=(
            "Force full recompute by ignoring any existing chunks in S3. "
            "Useful if you suspect the chunks are corrupt or stale."
        ),
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=50000,
        help=(
            "Candidate pairs per chunk (default: 50000). Larger values mean "
            "fewer S3 round-trips on resume but more memory per "
            "compare.compute() call and more lost work if a single chunk "
            "fails. Note: changing this invalidates any existing chunks at "
            "the same prefix — they map to different pair ranges."
        ),
    )
    parser.add_argument(
        "--resume-workers",
        type=int,
        default=32,
        help=(
            "Concurrent S3 GETs when reading existing chunks during resume "
            "(default: 32). Pure I/O — increase on a fat pipe, decrease if "
            "you hit S3 throttling."
        ),
    )
    parser.add_argument(
        "--recovered-policy",
        default=None,
        help=(
            "The policy whose recovered grants take the place of the "
            "placeholder rows. Default: the policy "
            "privategrants_current_w_recovered shows. Needed only when the "
            "view is absent; the run stops when the view shows another."
        ),
    )
    parser.add_argument(
        "--skip-rebuild",
        action="store_true",
        help=(
            "Take the _current relations that carry the stamp of the source "
            "versions and input shape the checkpoints are keyed on as they "
            "stand, and rebuild only the rest (and what reads them): a restart "
            "after a run that rebuilt them, or was stopped partway through "
            "them, with no load or ingest since. The views are still created."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Processes computing chunks (default 1, the serial loop). Forked "
            "from the run, so the frames are shared; each worker still copies "
            "the pages of the strings it touches, a few GB. Size it to the "
            "box's memory: 12 on an r6i.8xlarge."
        ),
    )
    parser.add_argument(
        "--ranking",
        choices=RANKINGS,
        default=RANKING,
        help=(
            f"How the winner of a grant tuple is picked (default {RANKING}): "
            "a candidate in a state that contradicts the grant's last, then "
            "a name that is exact or holds every word of the grant's, then "
            "geographic support, then an exact name, then a filing or "
            "correction over a BMF line among exact names, then the score; "
            "or 'weighted', the score alone, the order of the runs up to "
            "2026-10-27. Stamped on the build; the checkpoints are the same "
            "under either."
        ),
    )
    parser.add_argument(
        "--commit-between-outputs",
        action="store_true",
        help=(
            "Commit privategrants_w_recipients in a transaction of its own "
            "before unioned_grants is rebuilt, so its old copy is freed "
            "first: a shallower dip in the shared instance's free storage "
            "(the README's Storage note has the figures and the watchdog "
            "floor). The cost: a failure in the unioned_grants rebuild leaves "
            "a new privategrants_w_recipients next to a stale unioned_grants, "
            "where the default (one transaction) leaves both as the last run "
            "left them. Rerun the matcher with --skip-rebuild to put them "
            "right: the chunks are resumed from S3, and the reads, the pair "
            "index and the write phase are redone."
        ),
    )
    args = parser.parse_args()

    match_records(
        limit=args.limit,
        resume_from_checkpoints=not args.no_resume,
        chunk_size=args.chunk_size,
        resume_workers=args.resume_workers,
        recovered_policy=args.recovered_policy,
        skip_rebuild=args.skip_rebuild,
        workers=args.workers,
        ranking=args.ranking,
        commit_between_outputs=args.commit_between_outputs,
    )
