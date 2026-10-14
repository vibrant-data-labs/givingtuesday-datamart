"""
Build the Phase 2 canonical tables from staging.

Two materialized tables (not views — the inputs are multi-million row staging
tables, and we want query-time cost to be zero):

* ``public.nonprofit_canonical`` — one row per EIN, identity + address + the
  latest-filing identifiers, over the UNION of 990 and 990-EZ filers. The
  "winning" row for each EIN is selected via ``DISTINCT ON (filerein)``
  ordered by tax year desc, then tax-period-end desc, then form (990 before
  990-EZ), then ingested_at desc as a deterministic tiebreak. Every row is
  then joined to the IRS Business Master File (``public.eo_bmf``) for the
  IRS name of record, subsection, foundation code, NTEE and the rest of
  the BMF columns, and the 1.2M active orgs the BMF knows but no 990 does
  (990-N filers, churches) are added as rows with ``name_source = 'BMF'``.
  See ``_BMF_COLUMNS_SQL`` for the name rule.

  Built from ``basic_fields_current``, not raw staging: the ordering above
  cannot separate an original filing from its amendment (same taxyear, same
  taxperend, and ``_ingested_at`` is constant within an ingest run), so on
  staging the identity row for 5,315 EINs was an arbitrary pick between
  versions — 619 of them disagreeing on name or address. Reading the deduped
  relation makes that choice the same one every other consumer sees
  (issue #34).

* ``public.nonprofit_text`` — one row per EIN built from the **latest tax
  year only** across mission, programs activities 1/2/3, Schedule O
  Part III narrative, and the 990-EZ Part III accomplishments + primary
  exempt purpose. Plain ``DISTINCT`` collapses byte-identical snippets
  within that filing. Two GIN-indexed ``tsvector`` columns
  (``text_tsv_compact`` ``english`` config, ``text_tsv_compact_simple``
  ``simple`` config) drive the FTS surface that replaces the previous
  in-memory keyword search. Tokens that appeared only in older filings are
  intentionally not indexed — the simpler build is the trade.

Builds are idempotent: DROP + CREATE inside a transaction. Each build
records a row in ``datamart_meta.canonical_builds`` with the ``ingest_run_id``
of every staging table that fed it, so a consumer can always answer
"which source versions is this canonical table derived from?".
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.current_grants import (
    build_basic_fields_current,
    stale_basic_fields_current,
)
from givingtuesday_datamart.ingestion import (
    INGEST_RUNS_TABLE,
    META_SCHEMA,
    datamart_config,
    ensure_meta_schema,
)


CANONICAL_BUILDS_TABLE = f"{META_SCHEMA}.canonical_builds"

NONPROFIT_CANONICAL_TABLE = "public.nonprofit_canonical"
NONPROFIT_TEXT_TABLE = "public.nonprofit_text"
FUNDER_CANONICAL_TABLE = "public.funder_canonical"
PERSON_CANONICAL_TABLE = "public.person_canonical"
ORG_PERSON_ROLE_TABLE = "public.org_person_role"
SCHEDULE_O_PART_III_TABLE = "public.schedule_o_part_iii"

# Filter for rows of the raw ``public.schedule_o`` that are the continuation
# of Form 990 / 990-EZ **Part III** — Statement of Program Service
# Accomplishments. The ``sidfalrdesc`` column carries a free-form pointer
# back to the originating form section, and real data spans many shapes:
#   "FORM 990, PART III, LINE 4A"
#   "FORM 990-EZ, PART III"
#   "FORM 990, PAGE 2, PART III, LINE 4D"
#   "Form 990, Part III, Line 4d: Other Program Services Description"
#   "990 PAGE 2 PART 3"            (bare "990", arabic numeral, no punctuation)
#
# The filter has three conjuncts, each using PG's case-insensitive regex
# operator ``~*`` with word-boundary anchors (``\m`` = start-of-word,
# ``\M`` = end-of-word) so we don't mis-match ``19904``/``PART 30``/``PART 3A``:
#   1. ``\m990\M``                — "990" anywhere as a whole word
#   2. ``\mpart\s*(iii|3)\M``     — "PART III" or "PART 3" with optional space
#   3. ``!~* '\mschedule\s+[a-z]\M'`` — exclude continuations of *other*
#      schedules' Part III (e.g. "SCHEDULE A, PART III") which are not
#      program-service-accomplishments and shouldn't feed FTS.
# Raw string literal so Python doesn't interpret the backslashes.
SCHEDULE_O_PART_III_FILTER = (
    r"sidfalrdesc ~* '\m990\M'"
    r" AND sidfalrdesc ~* '\mpart\s*(iii|3)\M'"
    r" AND sidfalrdesc !~* '\mschedule\s+[a-z]\M'"
)

# The English Postgres FTS configuration. Matches the vast majority of IRS
# nonprofit text. A future enhancement could pick per-row based on a language
# hint, but there isn't one available in the raw data today.
FTS_CONFIG = "english"

# The IRS Exempt Organizations Business Master File, ingested as source
# irs_eo_bmf: one row per EIN for every currently recognized exempt org.
EO_BMF_TABLE = "public.eo_bmf"

# BMF columns carried on both identity tables, projected from an alias ``b``
# over EO_BMF_TABLE (LEFT JOINed for 990 filers, the row itself for
# BMF-only orgs). Typed where the staging TEXT is known-clean: amounts are
# all-digit on every row, RULING is always six digits.
#
# The name rule (settled 2026-10-14, docs/bmf-assessment.md): for any org
# with a 990 in our data, ``name`` / ``name_secondary`` stay the latest-990
# lines and the BMF name of record rides alongside as ``irs_name`` /
# ``irs_sort_name``. The BMF name is never swapped in: for group-exemption
# subordinates the BMF NAME is the parent ("FRATERNAL ORDER OF EAGLES") with
# the unit only in SORT_NAME ("3741 AERIE"), so a swap would misname about
# 4% of our nonprofits. ``name_source`` says which source the name came
# from: '990', '990-EZ', '990-PF' or 'BMF'.
#
# Code columns stay the IRS's codes (subsection '03', foundation '15',
# status '01'); the doc has the legend. GROUP '0000' and RULING '000000'
# mean "none" and become NULL.
_BMF_COLUMNS_SQL = """
                b.name                                  AS irs_name,
                b.sort_name                             AS irs_sort_name,
                b.subsection                            AS subsection,
                b.classification                        AS classification,
                b.foundation                            AS foundation_code,
                b.ntee_cd                               AS ntee_cd,
                NULLIF(b."group", '0000')               AS group_exemption,
                NULLIF(LEFT(b.ruling, 4), '0000')::int  AS ruling_year,
                b.filing_req_cd                         AS filing_req_cd,
                b.status                                AS bmf_status,
                b.tax_period                            AS bmf_tax_period,
                b.asset_amt::bigint                     AS bmf_asset_amt,
                b.income_amt::bigint                    AS bmf_income_amt,
                b.revenue_amt::bigint                   AS bmf_revenue_amt,
                (b.ein IS NOT NULL)                     AS in_bmf"""

# Address and in-care-of lines for a BMF-only row. The IRS keys the
# in-care-of name with a leading "% " (its convention for c/o); the 990's
# incarenm is a plain name, so strip it. Spelled with chr(37) because a
# literal percent sign in a statement that ever gains a bind parameter
# would be read by the driver as a placeholder. ZIP '00000-0000' is the
# IRS blank for a foreign address.
_BMF_ONLY_ADDRESS_SQL = """
                NULLIF(ltrim(b.ico, chr(37) || ' '), '') AS care_of,
                b.street                                AS addr_line_1,
                NULL::text                              AS addr_line_2,
                b.city                                  AS city,
                b.state                                 AS state,
                NULLIF(b.zip, '00000-0000')             AS zip,
                NULL::text                              AS addr_country"""

# A BMF-only org is one with no filing of any form in our data. The IRS's
# own flag decides which identity table it joins: PF_FILING_REQ_CD = '1'
# (a 990-PF return is required: 13K orgs, 98% carrying a private-foundation
# FOUNDATION code) goes to funder_canonical, everything else (990-N filers,
# churches, 990-required orgs we have no return for) to nonprofit_canonical.
_NO_FILING_SQL = """
                NOT EXISTS (SELECT 1 FROM public.basic_fields_current    f WHERE f.filerein = b.ein)
            AND NOT EXISTS (SELECT 1 FROM public.basic_fields_ez_current f WHERE f.filerein = b.ein)
            AND NOT EXISTS (SELECT 1 FROM public.basic_fields_pf_current f WHERE f.filerein = b.ein)"""


@dataclass
class BuildResult:
    build_id: str
    started_at: datetime
    finished_at: datetime
    schedule_o_part_iii_rows: int
    nonprofit_canonical_rows: int
    nonprofit_text_rows: int
    funder_canonical_rows: int
    person_canonical_rows: int
    org_person_role_rows: int
    source_runs: dict[str, str | None] = field(default_factory=dict)


def ensure_canonical_meta() -> None:
    """Create the ``canonical_builds`` lineage table if missing.

    Additive ``ADD COLUMN IF NOT EXISTS`` migrations are applied here so
    existing databases pick up columns introduced after the original
    ``CREATE`` without a separate migration step (matches the pattern in
    ``ensure_meta_schema``).

    The table is shared with the grant matching pipeline
    (``givingtuesday_datamart.grant_matching``); ``build_kind`` discriminates
    Phase 2 canonical builds from grant-matching runs, and the
    ``privategrants_w_recipients_rows`` / ``unioned_grants_rows`` columns
    are populated by grant matching.
    """
    ensure_meta_schema()
    with get_session(config=datamart_config()) as session:
        session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {CANONICAL_BUILDS_TABLE} (
                    build_id UUID PRIMARY KEY,
                    build_kind TEXT NOT NULL DEFAULT 'canonical',
                    started_at TIMESTAMPTZ NOT NULL,
                    finished_at TIMESTAMPTZ,
                    status TEXT NOT NULL,
                    schedule_o_part_iii_rows BIGINT,
                    nonprofit_canonical_rows BIGINT,
                    nonprofit_text_rows BIGINT,
                    funder_canonical_rows BIGINT,
                    person_canonical_rows BIGINT,
                    org_person_role_rows BIGINT,
                    privategrants_w_recipients_rows BIGINT,
                    unioned_grants_rows BIGINT,
                    source_runs JSONB,
                    error TEXT
                )
                """
            )
        )
        session.execute(
            text(
                f"""
                ALTER TABLE {CANONICAL_BUILDS_TABLE}
                ADD COLUMN IF NOT EXISTS build_kind TEXT NOT NULL DEFAULT 'canonical',
                ADD COLUMN IF NOT EXISTS schedule_o_part_iii_rows BIGINT,
                ADD COLUMN IF NOT EXISTS funder_canonical_rows BIGINT,
                ADD COLUMN IF NOT EXISTS person_canonical_rows BIGINT,
                ADD COLUMN IF NOT EXISTS org_person_role_rows BIGINT,
                ADD COLUMN IF NOT EXISTS privategrants_w_recipients_rows BIGINT,
                ADD COLUMN IF NOT EXISTS unioned_grants_rows BIGINT
                """
            )
        )


def latest_successful_run_ids() -> dict[str, str | None]:
    """Map each logical_name → most recent successful run_id (or None).

    Recorded on every canonical build (and every grant matching run) so we
    can later answer "which source versions is this canonical/matched
    table derived from?" without re-deriving. Shared between the Phase 2
    canonical builder and the grant matching pipeline.
    """
    from givingtuesday_datamart.sources.registry import REGISTRY

    with get_session(config=datamart_config()) as session:
        rows = session.execute(
            text(
                f"""
                SELECT DISTINCT ON (logical_name)
                    logical_name, run_id::text, source_version
                FROM {INGEST_RUNS_TABLE}
                WHERE status = 'success'
                ORDER BY logical_name, finished_at DESC
                """
            )
        ).all()
    latest = {r.logical_name: f"{r.run_id}@{r.source_version}" for r in rows}
    # Backfill None for any registered source that has never successfully run.
    for spec in REGISTRY:
        latest.setdefault(spec.logical_name, None)
    return latest


def _build_schedule_o_part_iii(session) -> int:
    """DROP + CREATE public.schedule_o_part_iii as a filtered view-like table.

    The raw ``public.schedule_o`` is ~29M rows, mostly Part VI governance
    narrative that we don't want in the FTS surface. Rebuilding this subset
    as its own materialized table lets both ``nonprofit_text`` and future
    profile views depend on the clean slice without re-filtering 29M rows
    on every query. Preserves the same columns as ``schedule_o`` (including
    lineage) so downstream consumers can treat it as a drop-in replacement.
    """
    logger.info("Building %s…", SCHEDULE_O_PART_III_TABLE)
    session.execute(text(f"DROP TABLE IF EXISTS {SCHEDULE_O_PART_III_TABLE}"))
    session.execute(
        text(
            f"""
            CREATE TABLE {SCHEDULE_O_PART_III_TABLE} AS
            SELECT *
            FROM public.schedule_o
            WHERE {SCHEDULE_O_PART_III_FILTER}
              AND COALESCE(supinfdetexp, '') <> ''
            """
        )
    )
    # Index on (filerein) so per-EIN joins from nonprofit_text and profile
    # lookups scan only that EIN's narratives rather than the whole table.
    session.execute(
        text(
            f"""
            CREATE INDEX ix_schedule_o_part_iii_ein
            ON {SCHEDULE_O_PART_III_TABLE} (filerein)
            """
        )
    )
    count = session.execute(
        text(f"SELECT COUNT(*) FROM {SCHEDULE_O_PART_III_TABLE}")
    ).scalar_one()
    logger.info("%s: %s rows", SCHEDULE_O_PART_III_TABLE, f"{count:,}")
    return count


def _build_nonprofit_canonical(session, table: str = NONPROFIT_CANONICAL_TABLE) -> int:
    """DROP + CREATE + populate public.nonprofit_canonical. Returns rowcount.

    Built from the UNION of 990 (``basic_fields_current``) and 990-EZ
    (``basic_fields_ez_current``) filers. EZ is the short form for small
    organizations, and ~75% of EZ filers never appear on a 990 at all —
    without them the canonical layer misses several hundred thousand orgs
    that still surface in ``nonprofit_text`` via Schedule O, which is what
    produced the NULL-identity search hits.

    Winner per EIN: latest tax year (numeric), then latest taxperend, then
    form (990 beats 990-EZ for the same period — it carries strictly more
    identity fields), then most recently ingested as the final
    deterministic tiebreak.

    An org that dropped from 990 to 990-EZ has an EZ winner, so ``dba_1``,
    ``dba_2``, ``care_of`` and ``formation_year`` come back NULL even
    though an older 990 carried them. That is deliberate: the row
    describes one real filing rather than a composite stitched across
    forms, matching the row-level selection rule in ``current_grants``.
    ``source_form`` says which form the winning row came from.

    Every winner is LEFT JOINed to the BMF (``_BMF_COLUMNS_SQL``); the 990
    name stays, ``in_bmf`` is false for the ~140K revoked or terminated
    orgs the BMF no longer lists (they hold historical grants and stay).
    Then the BMF-only orgs — active, no filing of any form in our data,
    not flagged as 990-PF filers — are appended with the BMF name as
    ``name`` / ``name_secondary``, ``name_source = 'BMF'``, and lineage
    pointing at the eo_bmf ingest run. ``table`` overrides the target for
    a preview build into another schema; production callers leave it.
    """
    logger.info("Building %s…", table)
    session.execute(text(f"DROP TABLE IF EXISTS {table}"))
    # Build as a CTAS; the DISTINCT ON picks the winning row per filerein.
    # NULLIF + CAST keeps row selection robust against blank-string taxyears —
    # they sort last (NULLS LAST), so any row with a real taxyear wins.
    session.execute(
        text(
            f"""
            CREATE TABLE {table} AS
            WITH unified AS (
                SELECT
                    filerein, filername1, filername2,
                    dbanbnline11, dbanbnline22, incarenm,
                    filerus1, filerus2, fileruscity, filerusstate,
                    fileruszip, filerforctry,
                    websitsiteit                        AS website,
                    formationorm,
                    taxyear, taxperend,
                    '990'::text                         AS source_form,
                    1                                   AS source_rank,
                    _ingest_run_id, _source_version, _ingested_at
                FROM public.basic_fields_current
                UNION ALL
                -- 990-EZ under GT's own cross-form naming. The short form
                -- has no DBA lines, no in-care-of name, and no formation
                -- year, so those are NULL for an EZ-sourced winner; the
                -- website column is `websitaddres` there.
                SELECT
                    filerein, filername1, filername2,
                    NULL, NULL, NULL,
                    filerus1, filerus2, fileruscity, filerusstate,
                    fileruszip, filerforctry,
                    websitaddres                        AS website,
                    NULL,
                    taxyear, taxperend,
                    '990-EZ'::text                      AS source_form,
                    2                                   AS source_rank,
                    _ingest_run_id, _source_version, _ingested_at
                FROM public.basic_fields_ez_current
            ),
            winners AS (
                SELECT DISTINCT ON (filerein)
                    filerein                                AS ein,
                    filername1                              AS name,
                    filername2                              AS name_secondary,
                    dbanbnline11                            AS dba_1,
                    dbanbnline22                            AS dba_2,
                    incarenm                                AS care_of,
                    filerus1                                AS addr_line_1,
                    filerus2                                AS addr_line_2,
                    fileruscity                             AS city,
                    filerusstate                            AS state,
                    fileruszip                              AS zip,
                    filerforctry                            AS addr_country,
                    website                                 AS website,
                    formationorm                            AS formation_year,
                    taxyear                                 AS latest_taxyear,
                    taxperend                               AS latest_taxperend,
                    source_form                             AS source_form,
                    _ingest_run_id                          AS source_run_id,
                    _source_version                         AS source_version
                FROM unified
                ORDER BY
                    filerein,
                    CAST(NULLIF(taxyear, '') AS INT) DESC NULLS LAST,
                    taxperend DESC NULLS LAST,
                    source_rank,
                    _ingested_at DESC NULLS LAST
            )
            -- 990 / 990-EZ filers: the filing's identity, the BMF alongside.
            SELECT
                w.ein, w.name, w.name_secondary, w.dba_1, w.dba_2, w.care_of,
                w.addr_line_1, w.addr_line_2, w.city, w.state, w.zip, w.addr_country,
                w.website, w.formation_year, w.latest_taxyear, w.latest_taxperend,
                w.source_form,
                w.source_form                           AS name_source,
                {_BMF_COLUMNS_SQL},
                w.source_run_id, w.source_version,
                NOW() AT TIME ZONE 'UTC'                AS _built_at
            FROM winners w
            LEFT JOIN {EO_BMF_TABLE} b ON b.ein = w.ein
            UNION ALL
            -- BMF-only orgs: no filing of any form in our data, not a
            -- 990-PF filer by the IRS's flag (those go to funder_canonical).
            SELECT
                b.ein,
                b.name                                  AS name,
                b.sort_name                             AS name_secondary,
                NULL::text                              AS dba_1,
                NULL::text                              AS dba_2,
                {_BMF_ONLY_ADDRESS_SQL},
                NULL::text                              AS website,
                NULL::text                              AS formation_year,
                NULL::text                              AS latest_taxyear,
                NULL::text                              AS latest_taxperend,
                NULL::text                              AS source_form,
                'BMF'::text                             AS name_source,
                {_BMF_COLUMNS_SQL},
                b._ingest_run_id                        AS source_run_id,
                b._source_version                       AS source_version,
                NOW() AT TIME ZONE 'UTC'                AS _built_at
            FROM {EO_BMF_TABLE} b
            WHERE b.pf_filing_req_cd IS DISTINCT FROM '1'
              AND {_NO_FILING_SQL}
            """
        )
    )
    # ein is a natural primary key: DISTINCT ON makes the filers unique and
    # the BMF-only arm excludes every EIN with a filing.
    session.execute(
        text(
            f"""
            ALTER TABLE {table}
            ADD CONSTRAINT nonprofit_canonical_pkey PRIMARY KEY (ein)
            """
        )
    )
    # Normalized domain derived from the raw ``website`` column for URL search.
    # GENERATED ALWAYS AS … STORED so it stays in sync with website without
    # explicit backfill; rebuilt automatically because the canonical table is
    # DROP+CREATE on every refresh. v1 normalization is intentionally narrow
    # (regex only — no public-suffix awareness, no IDN punycoding); upgrading
    # to a Python normalizer with tldextract is tracked as a follow-up.
    session.execute(
        text(
            f"""
            ALTER TABLE {table}
            ADD COLUMN domain TEXT GENERATED ALWAYS AS (
                lower(
                    regexp_replace(
                        regexp_replace(
                            regexp_replace(
                                coalesce(website, ''),
                                '^\\s*(https?:)?//', '', 'i'
                            ),
                            '^www\\.', '', 'i'
                        ),
                        '[/?#].*$', ''
                    )
                )
            ) STORED
            """
        )
    )
    # Partial index: orgs with no website contribute nothing to URL search.
    session.execute(
        text(
            f"""
            CREATE INDEX ix_nonprofit_canonical_domain
            ON {table} (domain)
            WHERE domain <> ''
            """
        )
    )
    count = session.execute(
        text(f"SELECT COUNT(*) FROM {table}")
    ).scalar_one()
    logger.info("%s: %s rows", table, f"{count:,}")
    return count


def _build_nonprofit_text(session) -> int:
    """DROP + CREATE + populate public.nonprofit_text with FTS surfaces.

    One row per EIN, built from the latest filing's text fields only:
    mission + program activities 1/2/3 + Schedule O Part III narrative +
    990-EZ Part III accomplishments and primary exempt purpose.
    Plain ``DISTINCT`` collapses byte-identical snippets within that
    filing (e.g. mission pasted verbatim into Schedule O).

    "Latest filing" = strict latest taxyear per EIN. ``DENSE_RANK`` over
    ``(ein ORDER BY taxyear DESC NULLS LAST)`` partitions to ``yr_rank = 1``;
    NULLS LAST means an EIN whose every row has a NULL taxyear still keeps
    its rows — we'd rather index the text we have than drop the EIN.

    Three columns:

    * ``unique_text_compact`` — display text, the same string the FTS
      tsvectors are built from.
    * ``text_tsv_compact`` — ``english`` config (Snowball stemming +
      stopword removal). Best for relevance ranking and stem-tolerant
      matching ("tutoring" matches "tutor").
    * ``text_tsv_compact_simple`` — ``simple`` config (lowercase +
      tokenize, no stemming, no stopwords). Best for exact-term matching
      ("tutoring" matches only "tutoring").

    Trade-off: tokens that appeared only in older filings (e.g. a program
    rename) are not in the FTS index. The simpler build, smaller indexes,
    and non-stale display text are the win.
    """
    logger.info("Building %s…", NONPROFIT_TEXT_TABLE)

    logger.info("Dropping %s…", NONPROFIT_TEXT_TABLE)
    session.execute(text(f"DROP TABLE IF EXISTS {NONPROFIT_TEXT_TABLE}"))

    # Encourage parallel scan execution for the UNION ALL fan-in and the
    # to_tsvector calls. Per-statement only; doesn't leak into the rest
    # of the build's session.
    logger.info("Setting local max_parallel_workers_per_gather = 4")
    session.execute(text("SET LOCAL max_parallel_workers_per_gather = 4"))

    logger.info("Creating %s…", NONPROFIT_TEXT_TABLE)
    session.execute(
        text(
            f"""
            CREATE TABLE {NONPROFIT_TEXT_TABLE} AS
            WITH all_text AS MATERIALIZED (
                -- Every non-empty (ein, txt, taxyear) across the seven
                -- source fields. UNION ALL — DISTINCT happens downstream
                -- after the latest-year filter.
                SELECT filerein AS ein, mission AS txt,
                       NULLIF(taxyear, '')::INT AS taxyear
                FROM public.mission_statements
                WHERE COALESCE(mission, '') <> ''
                UNION ALL
                SELECT filerein, actividescri1, NULLIF(taxyear, '')::INT
                FROM public.programs
                WHERE COALESCE(actividescri1, '') <> ''
                UNION ALL
                SELECT filerein, actividescri2, NULLIF(taxyear, '')::INT
                FROM public.programs
                WHERE COALESCE(actividescri2, '') <> ''
                UNION ALL
                SELECT filerein, actividescri3, NULLIF(taxyear, '')::INT
                FROM public.programs
                WHERE COALESCE(actividescri3, '') <> ''
                UNION ALL
                SELECT filerein, supinfdetexp, NULLIF(taxyear, '')::INT
                FROM {SCHEDULE_O_PART_III_TABLE}
                WHERE COALESCE(supinfdetexp, '') <> ''
                UNION ALL
                -- 990-EZ Part III. Long-shaped, unlike `programs`: one row
                -- per accomplishment rather than three wide activity
                -- columns, so a single arm covers all of them.
                SELECT filerein, psadpsaccom, NULLIF(taxyear, '')::INT
                FROM public.programs_ez
                WHERE COALESCE(psadpsaccom, '') <> ''
                UNION ALL
                SELECT filerein, primexempurp, NULLIF(taxyear, '')::INT
                FROM public.programs_ez
                WHERE COALESCE(primexempurp, '') <> ''
            ),
            ranked AS (
                SELECT ein, txt, taxyear,
                       DENSE_RANK() OVER (
                           PARTITION BY ein
                           ORDER BY taxyear DESC NULLS LAST
                       ) AS yr_rank
                FROM all_text
            ),
            latest_text AS (
                -- yr_rank = 1 picks the latest taxyear per EIN; NULLS LAST
                -- means EINs with only NULL-taxyear rows still pass through.
                SELECT DISTINCT ein, txt
                FROM ranked
                WHERE yr_rank = 1
            ),
            per_ein AS (
                SELECT
                    ein,
                    string_agg(txt, E'\\n\\n' ORDER BY txt) AS unique_text_compact,
                    COUNT(*)                                AS n_snippets
                FROM latest_text
                GROUP BY ein
            )
            SELECT
                ein,
                unique_text_compact,
                n_snippets,
                to_tsvector('{FTS_CONFIG}', unique_text_compact) AS text_tsv_compact,
                to_tsvector('simple',       unique_text_compact) AS text_tsv_compact_simple,
                NOW() AT TIME ZONE 'UTC'                         AS _built_at
            FROM per_ein
            """
        )
    )
    logger.info("Adding primary key to %s…", NONPROFIT_TEXT_TABLE)
    session.execute(
        text(
            f"""
            ALTER TABLE {NONPROFIT_TEXT_TABLE}
            ADD CONSTRAINT nonprofit_text_pkey PRIMARY KEY (ein)
            """
        )
    )
    # GIN indexes named explicitly so re-runs (which drop the parent table)
    # re-create consistent index names.
    logger.info("Creating GIN index on %s…", NONPROFIT_TEXT_TABLE)
    session.execute(
        text(
            f"""
            CREATE INDEX ix_nonprofit_text_tsv_compact
            ON {NONPROFIT_TEXT_TABLE}
            USING GIN (text_tsv_compact)
            """
        )
    )
    logger.info("Creating GIN index on %s…", NONPROFIT_TEXT_TABLE)
    session.execute(
        text(
            f"""
            CREATE INDEX ix_nonprofit_text_tsv_compact_simple
            ON {NONPROFIT_TEXT_TABLE}
            USING GIN (text_tsv_compact_simple)
            """
        )
    )
    logger.info("Counting rows in %s…", NONPROFIT_TEXT_TABLE)
    count = session.execute(
        text(f"SELECT COUNT(*) FROM {NONPROFIT_TEXT_TABLE}")
    ).scalar_one()
    logger.info("%s: %s rows", NONPROFIT_TEXT_TABLE, f"{count:,}")
    return count


def _build_funder_canonical(session, table: str = FUNDER_CANONICAL_TABLE) -> int:
    """DROP + CREATE + populate public.funder_canonical from basic_fields_pf_current.

    Private-foundation identity table — mirrors ``nonprofit_canonical``'s
    selection logic (latest taxyear, then latest taxperend, then latest
    ingested), including its read of the deduped ``_current`` relation
    rather than raw staging. Funder classification
    (DAF / community / corporate / family) is deliberately out of scope for
    v1 — it's a Phase 3 enrichment task that needs external funder-type
    data. v1 carries identity + address + contact only.

    Same BMF treatment as ``nonprofit_canonical``: every 990-PF filer LEFT
    JOINed to the BMF (name kept, ``in_bmf`` false for the ~35K no longer
    listed), then the BMF-only private foundations — no filing of any form
    in our data, PF_FILING_REQ_CD = '1' — appended with ``name_source =
    'BMF'``. ``table`` overrides the target for a preview build.

    Note: many grant-making 501(c)(3)s (community foundations, etc.) file
    990, not 990-PF. They live in ``nonprofit_canonical`` today. A future
    ``funder_canonical`` could be widened to a UNION of grant-makers across
    both form types; keeping the scope narrow here matches the "private
    foundation-centric" grant-making data we already have.
    """
    logger.info("Building %s…", table)
    session.execute(text(f"DROP TABLE IF EXISTS {table}"))
    session.execute(
        text(
            f"""
            CREATE TABLE {table} AS
            WITH winners AS (
                SELECT DISTINCT ON (filerein)
                    filerein                                AS ein,
                    filername1                              AS name,
                    filername2                              AS name_secondary,
                    filerus1                                AS addr_line_1,
                    filerus2                                AS addr_line_2,
                    fileruscity                             AS city,
                    filerusstate                            AS state,
                    fileruszip                              AS zip,
                    filerforctry                            AS addr_country,
                    filerphone                              AS phone,
                    taxyear                                 AS latest_taxyear,
                    taxperend                               AS latest_taxperend,
                    _ingest_run_id                          AS source_run_id,
                    _source_version                         AS source_version
                FROM public.basic_fields_pf_current
                ORDER BY
                    filerein,
                    CAST(NULLIF(taxyear, '') AS INT) DESC NULLS LAST,
                    taxperend DESC NULLS LAST,
                    _ingested_at DESC NULLS LAST
            )
            SELECT
                w.ein, w.name, w.name_secondary,
                w.addr_line_1, w.addr_line_2, w.city, w.state, w.zip, w.addr_country,
                w.phone, w.latest_taxyear, w.latest_taxperend,
                '990-PF'::text                          AS name_source,
                {_BMF_COLUMNS_SQL},
                w.source_run_id, w.source_version,
                NOW() AT TIME ZONE 'UTC'                AS _built_at
            FROM winners w
            LEFT JOIN {EO_BMF_TABLE} b ON b.ein = w.ein
            UNION ALL
            -- BMF-only private foundations: a 990-PF is required by the
            -- IRS's flag but we hold no filing of any form.
            SELECT
                b.ein,
                b.name                                  AS name,
                b.sort_name                             AS name_secondary,
                b.street                                AS addr_line_1,
                NULL::text                              AS addr_line_2,
                b.city                                  AS city,
                b.state                                 AS state,
                NULLIF(b.zip, '00000-0000')             AS zip,
                NULL::text                              AS addr_country,
                NULL::text                              AS phone,
                NULL::text                              AS latest_taxyear,
                NULL::text                              AS latest_taxperend,
                'BMF'::text                             AS name_source,
                {_BMF_COLUMNS_SQL},
                b._ingest_run_id                        AS source_run_id,
                b._source_version                       AS source_version,
                NOW() AT TIME ZONE 'UTC'                AS _built_at
            FROM {EO_BMF_TABLE} b
            WHERE b.pf_filing_req_cd = '1'
              AND {_NO_FILING_SQL}
            """
        )
    )
    session.execute(
        text(
            f"""
            ALTER TABLE {table}
            ADD CONSTRAINT funder_canonical_pkey PRIMARY KEY (ein)
            """
        )
    )
    count = session.execute(
        text(f"SELECT COUNT(*) FROM {table}")
    ).scalar_one()
    logger.info("%s: %s rows", table, f"{count:,}")
    return count


# Normalization expression reused by person_canonical and org_person_role
# so the two tables share a dedup key. Lowercase + collapse internal
# whitespace + trim. Kept deliberately narrow — no honorific stripping,
# no diacritic folding — so v1 behavior is predictable; Phase 3 matching
# can layer more aggressive normalization on top without migrating the
# current tables.
_NAME_NORMALIZE_SQL = "TRIM(REGEXP_REPLACE(LOWER(COALESCE(name, '')), '\\s+', ' ', 'g'))"

# Deterministic surrogate key for a (normalized_name, ein) pair. MD5 over
# the concatenation means rebuilds produce stable IDs as long as the
# normalization function is stable — consumers can cache person_ids.
_PERSON_ID_SQL = (
    f"MD5({_NAME_NORMALIZE_SQL} || '|' || COALESCE(ein, ''))::uuid"
)


def _build_person_canonical(session) -> int:
    """DROP + CREATE + populate public.person_canonical.

    v1 dedup key = (normalized_name, ein) — collapses a person's filings
    for the same org across years, but makes no attempt to match the same
    human across different orgs. That cross-org dedup is Phase 3 work
    (recordlinkage on names + co-occurring orgs + addresses).

    Draws from both ``officers`` (990) and ``officers_pf`` (990-PF); the
    two source tables have different column naming, so each side is
    projected to a common shape before UNION.
    """
    logger.info("Building %s…", PERSON_CANONICAL_TABLE)
    session.execute(text(f"DROP TABLE IF EXISTS {PERSON_CANONICAL_TABLE}"))
    session.execute(
        text(
            f"""
            CREATE TABLE {PERSON_CANONICAL_TABLE} AS
            WITH unified AS (
                SELECT
                    filerein           AS ein,
                    namepeperson       AS name,
                    taxyear            AS taxyear,
                    '990'::text        AS source_form
                FROM public.officers
                WHERE COALESCE(namepeperson, '') <> ''
                UNION ALL
                SELECT
                    filerein           AS ein,
                    odtkeiodtokepn     AS name,
                    taxyear            AS taxyear,
                    '990-PF'::text     AS source_form
                FROM public.officers_pf
                WHERE COALESCE(odtkeiodtokepn, '') <> ''
            ),
            grouped AS (
                SELECT
                    {_PERSON_ID_SQL}                           AS person_id,
                    {_NAME_NORMALIZE_SQL}                      AS name_normalized,
                    MAX(name)                                  AS name_display,
                    ein,
                    MIN(CAST(NULLIF(taxyear, '') AS INT))      AS first_taxyear,
                    MAX(CAST(NULLIF(taxyear, '') AS INT))      AS last_taxyear,
                    COUNT(*)                                   AS n_filings,
                    BOOL_OR(source_form = '990')               AS seen_on_990,
                    BOOL_OR(source_form = '990-PF')            AS seen_on_990pf
                FROM unified
                GROUP BY person_id, name_normalized, ein
            )
            SELECT
                person_id,
                name_display                 AS name,
                name_normalized,
                ein,
                first_taxyear,
                last_taxyear,
                n_filings,
                seen_on_990,
                seen_on_990pf,
                NOW() AT TIME ZONE 'UTC'     AS _built_at
            FROM grouped
            """
        )
    )
    session.execute(
        text(
            f"""
            ALTER TABLE {PERSON_CANONICAL_TABLE}
            ADD CONSTRAINT person_canonical_pkey PRIMARY KEY (person_id)
            """
        )
    )
    # Index on ein to speed up org-side joins (used by the frontend "people
    # at this org" view and by org_person_role's joins).
    session.execute(
        text(
            f"""
            CREATE INDEX ix_person_canonical_ein
            ON {PERSON_CANONICAL_TABLE} (ein)
            """
        )
    )
    # Index on normalized name for Phase 3 cross-org matching work.
    session.execute(
        text(
            f"""
            CREATE INDEX ix_person_canonical_name_norm
            ON {PERSON_CANONICAL_TABLE} (name_normalized)
            """
        )
    )
    count = session.execute(
        text(f"SELECT COUNT(*) FROM {PERSON_CANONICAL_TABLE}")
    ).scalar_one()
    logger.info("%s: %s rows", PERSON_CANONICAL_TABLE, f"{count:,}")
    return count


def _build_org_person_role(session) -> int:
    """DROP + CREATE + populate public.org_person_role.

    One row per filing (preserves every year's record). Columns converge
    the shapes of ``officers`` and ``officers_pf`` into a single role
    table; compensation fields are cast to NUMERIC where the source is
    all-TEXT but we know the domain (compensation is always a number).
    Role flags that only exist on the 990 side are NULL for 990-PF rows.
    """
    logger.info("Building %s…", ORG_PERSON_ROLE_TABLE)
    session.execute(text(f"DROP TABLE IF EXISTS {ORG_PERSON_ROLE_TABLE}"))
    # Ingested text like "170059" or "0.30" round-trips through NUMERIC
    # without loss; NULLIF avoids "" -> NUMERIC errors.
    session.execute(
        text(
            f"""
            CREATE TABLE {ORG_PERSON_ROLE_TABLE} AS
            WITH unified AS (
                SELECT
                    filerein                                                    AS ein,
                    namepeperson                                                AS name,
                    taxyear                                                     AS taxyear,
                    taxperend                                                   AS taxperend,
                    titleitle                                                   AS title,
                    NULLIF(avehouperwee, '')::NUMERIC                           AS avg_hours_per_week,
                    NULLIF(avhopewereel, '')::NUMERIC                           AS avg_hours_per_week_related,
                    NULLIF(repcomfroorg, '')::NUMERIC                           AS comp_reportable_org,
                    NULLIF(recofrrlorrg, '')::NUMERIC                           AS comp_reportable_related,
                    NULLIF(otherccompen, '')::NUMERIC                           AS comp_other,
                    (COALESCE(officerffice, '') <> '')                          AS is_officer,
                    (COALESCE(inditrusdire, '') <> '')                          AS is_individual_trustee,
                    (COALESCE(instittruste, '') <> '')                          AS is_institutional_trustee,
                    (COALESCE(keyempemploy, '') <> '')                          AS is_key_employee,
                    (COALESCE(highcompempl, '') <> '')                          AS is_highly_compensated,
                    (COALESCE(formerormer, '') <> '')                           AS is_former,
                    '990'::text                                                 AS source_form,
                    _source_version, _ingest_run_id
                FROM public.officers
                WHERE COALESCE(namepeperson, '') <> ''
                UNION ALL
                SELECT
                    filerein                                                    AS ein,
                    odtkeiodtokepn                                              AS name,
                    taxyear                                                     AS taxyear,
                    taxperend                                                   AS taxperend,
                    odtkeiodtoketi                                              AS title,
                    NULLIF(odtkeiodtoke, '')::NUMERIC                           AS avg_hours_per_week,
                    NULL::NUMERIC                                               AS avg_hours_per_week_related,
                    NULLIF(odtkeiodtokecom, '')::NUMERIC                        AS comp_reportable_org,
                    NULL::NUMERIC                                               AS comp_reportable_related,
                    (
                        COALESCE(NULLIF(odtkeiodtokecoeb, '')::NUMERIC, 0)
                      + COALESCE(NULLIF(odtkeiodtokeeao, '')::NUMERIC, 0)
                    )                                                           AS comp_other,
                    NULL::boolean                                               AS is_officer,
                    NULL::boolean                                               AS is_individual_trustee,
                    NULL::boolean                                               AS is_institutional_trustee,
                    NULL::boolean                                               AS is_key_employee,
                    NULL::boolean                                               AS is_highly_compensated,
                    NULL::boolean                                               AS is_former,
                    '990-PF'::text                                              AS source_form,
                    _source_version, _ingest_run_id
                FROM public.officers_pf
                WHERE COALESCE(odtkeiodtokepn, '') <> ''
            )
            SELECT
                {_PERSON_ID_SQL}   AS person_id,
                ein,
                taxyear,
                taxperend,
                title,
                avg_hours_per_week,
                avg_hours_per_week_related,
                comp_reportable_org,
                comp_reportable_related,
                comp_other,
                is_officer,
                is_individual_trustee,
                is_institutional_trustee,
                is_key_employee,
                is_highly_compensated,
                is_former,
                source_form,
                _source_version                          AS source_version,
                _ingest_run_id                           AS source_run_id,
                NOW() AT TIME ZONE 'UTC'                 AS _built_at
            FROM unified
            """
        )
    )
    # (person_id, ein) is the natural lookup key from both directions.
    session.execute(
        text(
            f"""
            CREATE INDEX ix_org_person_role_person ON {ORG_PERSON_ROLE_TABLE} (person_id);
            CREATE INDEX ix_org_person_role_ein    ON {ORG_PERSON_ROLE_TABLE} (ein);
            CREATE INDEX ix_org_person_role_year   ON {ORG_PERSON_ROLE_TABLE} (taxyear);
            """
        )
    )
    count = session.execute(
        text(f"SELECT COUNT(*) FROM {ORG_PERSON_ROLE_TABLE}")
    ).scalar_one()
    logger.info("%s: %s rows", ORG_PERSON_ROLE_TABLE, f"{count:,}")
    return count


def build_canonical(*, include_people: bool = False) -> BuildResult:
    """Rebuild all Phase 2 canonical tables from current staging.

    All tables build inside a single transaction so a failure leaves the
    old canonical state intact — downstream consumers never see a
    half-built canonical layer. The overall build is recorded in
    ``datamart_meta.canonical_builds`` for lineage.

    ``include_people`` controls the officers-derived tables
    (``person_canonical`` + ``org_person_role``). They are off by default
    because together they materialize 90M+ rows, which we found we don't
    have RDS disk headroom for yet. Flip on once storage is sized for it
    (and once a downstream consumer — frontend profile pages, Phase 3
    person matching — actually needs them). When skipped, those tables'
    rowcount fields on ``BuildResult`` come back as 0.
    """
    ensure_canonical_meta()
    build_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    source_runs = latest_successful_run_ids()

    # Insert "started" row so a crash mid-build leaves a breadcrumb.
    with get_session(config=datamart_config()) as session:
        session.execute(
            text(
                f"""
                INSERT INTO {CANONICAL_BUILDS_TABLE} (
                    build_id, started_at, status, source_runs
                ) VALUES (
                    :build_id, :started_at, 'started', CAST(:source_runs AS JSONB)
                )
                """
            ),
            {
                "build_id": build_id,
                "started_at": started_at,
                "source_runs": json.dumps(source_runs),
            },
        )

    logger.info(
        "Starting canonical build %s (include_people=%s)",
        build_id, include_people,
    )
    try:
        with get_session(config=datamart_config()) as session:
            # Both identity builds read basic_fields[_pf][_ez]_current. The
            # ingestion path rebuilds those as part of any refresh that
            # reloads their source, so this is normally a no-op — but it
            # is checked rather than assumed, because building identity
            # from the previous drop is silent and would persist until
            # someone noticed a stale name or a missing EIN. The guard is
            # two scalar reads per relation.
            stale = stale_basic_fields_current(session.connection())
            if stale:
                logger.warning(
                    "%s stale or missing — rebuilding before identity builds. "
                    "Expected only if staging was loaded outside `sources refresh`.",
                    ", ".join(stale),
                )
                build_basic_fields_current(session.connection(), only=stale)
            # schedule_o_part_iii must land before nonprofit_text because the
            # text build reads from it. All builds share one transaction so
            # downstream consumers never see a half-built canonical layer.
            so3_rows = _build_schedule_o_part_iii(session)
            np_rows = _build_nonprofit_canonical(session)
            txt_rows = _build_nonprofit_text(session)
            fn_rows = _build_funder_canonical(session)
            if include_people:
                person_rows = _build_person_canonical(session)
                role_rows = _build_org_person_role(session)
            else:
                logger.info(
                    "Skipping person_canonical + org_person_role "
                    "(include_people=False; ~90M rows of disk would be needed)"
                )
                person_rows = 0
                role_rows = 0
    except Exception as err:
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
                    "error": str(err)[:4000],
                },
            )
        logger.exception("Canonical build failed")
        raise

    finished_at = datetime.now(timezone.utc)
    with get_session(config=datamart_config()) as session:
        session.execute(
            text(
                f"""
                UPDATE {CANONICAL_BUILDS_TABLE}
                SET finished_at = :finished_at,
                    status = 'success',
                    schedule_o_part_iii_rows = :so3_rows,
                    nonprofit_canonical_rows = :np_rows,
                    nonprofit_text_rows = :txt_rows,
                    funder_canonical_rows = :fn_rows,
                    person_canonical_rows = :person_rows,
                    org_person_role_rows = :role_rows
                WHERE build_id = :build_id
                """
            ),
            {
                "build_id": build_id,
                "finished_at": finished_at,
                "so3_rows": so3_rows,
                "np_rows": np_rows,
                "txt_rows": txt_rows,
                "fn_rows": fn_rows,
                "person_rows": person_rows,
                "role_rows": role_rows,
            },
        )
    duration = (finished_at - started_at).total_seconds()
    logger.info(
        "Canonical build %s success: schedule_o_part_iii=%s, "
        "nonprofit_canonical=%s, nonprofit_text=%s, funder_canonical=%s, "
        "person_canonical=%s, org_person_role=%s, duration=%.1fs",
        build_id,
        f"{so3_rows:,}", f"{np_rows:,}", f"{txt_rows:,}",
        f"{fn_rows:,}", f"{person_rows:,}", f"{role_rows:,}", duration,
    )
    return BuildResult(
        build_id=build_id,
        started_at=started_at,
        finished_at=finished_at,
        schedule_o_part_iii_rows=so3_rows,
        nonprofit_canonical_rows=np_rows,
        nonprofit_text_rows=txt_rows,
        funder_canonical_rows=fn_rows,
        person_canonical_rows=person_rows,
        org_person_role_rows=role_rows,
        source_runs=source_runs,
    )


__all__ = [
    "BuildResult",
    "CANONICAL_BUILDS_TABLE",
    "EO_BMF_TABLE",
    "FTS_CONFIG",
    "FUNDER_CANONICAL_TABLE",
    "NONPROFIT_CANONICAL_TABLE",
    "NONPROFIT_TEXT_TABLE",
    "ORG_PERSON_ROLE_TABLE",
    "PERSON_CANONICAL_TABLE",
    "SCHEDULE_O_PART_III_FILTER",
    "SCHEDULE_O_PART_III_TABLE",
    "build_canonical",
    "ensure_canonical_meta",
    "latest_successful_run_ids",
]
