"""
Build the Phase 2 canonical tables from staging.

Two materialized tables (not views — the inputs are multi-million row staging
tables, and we want query-time cost to be zero):

* ``public.nonprofit_canonical`` — one row per EIN, identity + address + the
  latest-filing identifiers. The "winning" row for each EIN is selected via
  ``DISTINCT ON (filerein)`` ordered by tax year desc, then tax-period-end
  desc, then ingested_at desc as a deterministic tiebreak.

* ``public.nonprofit_text`` — one row per EIN with a deduped concatenation
  of every non-empty text field across every year (mission, programs
  activities 1/2/3, Schedule O narrative + supplemental detail), plus a
  ``tsvector`` column with a GIN index. This is the FTS surface that
  replaces vdl-tools' in-memory keyword search.

Builds are idempotent: DROP + CREATE inside a transaction. Each build
records a row in ``datamart_meta.canonical_builds`` with the ``ingest_run_id``
of every staging table that fed it, so a consumer can always answer
"which source versions is this canonical table derived from?".

Field-level dedup (not sentence-level) is the v1 choice — ``UNION`` across
every (ein, field, year) tuple naturally collapses exact-string duplicates
(e.g. same mission copy-pasted every year). Cross-field overlap (Activity 1
text happens to match Schedule O Part III) is not deduped at this layer;
Postgres FTS weighting handles the ranking penalty for repeated tokens.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text
from vdl_tools.shared_tools.database_cache.database_utils import get_session
from vdl_tools.shared_tools.tools.logger import logger

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


def _build_nonprofit_canonical(session) -> int:
    """DROP + CREATE + populate public.nonprofit_canonical. Returns rowcount.

    Winner per EIN: latest tax year (numeric), then latest taxperend, then
    most recently ingested (final deterministic tiebreak).
    """
    logger.info("Building %s…", NONPROFIT_CANONICAL_TABLE)
    session.execute(text(f"DROP TABLE IF EXISTS {NONPROFIT_CANONICAL_TABLE}"))
    # Build as a CTAS; the DISTINCT ON picks the winning row per filerein.
    # NULLIF + CAST keeps row selection robust against blank-string taxyears —
    # they sort last (NULLS LAST), so any row with a real taxyear wins.
    session.execute(
        text(
            f"""
            CREATE TABLE {NONPROFIT_CANONICAL_TABLE} AS
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
                websitsiteit                            AS website,
                formationorm                            AS formation_year,
                taxyear                                 AS latest_taxyear,
                taxperend                               AS latest_taxperend,
                _ingest_run_id                          AS source_run_id,
                _source_version                         AS source_version,
                NOW() AT TIME ZONE 'UTC'                AS _built_at
            FROM public.basic_fields
            ORDER BY
                filerein,
                CAST(NULLIF(taxyear, '') AS INT) DESC NULLS LAST,
                taxperend DESC NULLS LAST,
                _ingested_at DESC NULLS LAST
            """
        )
    )
    # ein is a natural primary key once DISTINCT ON enforces uniqueness.
    session.execute(
        text(
            f"""
            ALTER TABLE {NONPROFIT_CANONICAL_TABLE}
            ADD CONSTRAINT nonprofit_canonical_pkey PRIMARY KEY (ein)
            """
        )
    )
    count = session.execute(
        text(f"SELECT COUNT(*) FROM {NONPROFIT_CANONICAL_TABLE}")
    ).scalar_one()
    logger.info("%s: %s rows", NONPROFIT_CANONICAL_TABLE, f"{count:,}")
    return count


def _build_nonprofit_text(session) -> int:
    """DROP + CREATE + populate public.nonprofit_text with two FTS surfaces.

    Two parallel views of each EIN's narrative are produced:

    * ``unique_text`` / ``text_tsv`` — the v1 surface. Exact-string dedup
      across (ein, txt) tuples (cross-source: a mission paragraph copy-
      pasted into Schedule O Part III collapses to one snippet). Preserved
      for compatibility while the compact surface is validated.

    * ``unique_text_compact`` / ``text_tsv_compact`` / ``text_tsv_compact_simple``
      — near-duplicate dedup. Each snippet is normalized (lowercase + strip
      4-digit years, dollar amounts, percentages, comma-numbers, punctuation,
      whitespace; bare digits are intentionally preserved so "5 victims" ≠
      "50 victims"), then md5-hashed into a ``norm_key``. Snippets sharing
      a ``norm_key`` cluster, and the most-recent (longest, then
      deterministic) original becomes the cluster representative.
      Both compact tsvectors are built from the **token-union** of every
      cluster member's original text — not just representatives — so tokens
      that only appeared in older filings (e.g. "tutoring" before a 2024
      rewrite to "after-school programs") survive in the FTS index.

      ``text_tsv_compact`` uses the ``english`` config (Snowball stemming
      + stopword removal) — what you want for relevance ranking and
      stem-tolerant matching ("tutoring" matches "tutor"). ``text_tsv_compact_simple``
      uses the ``simple`` config (lowercase + tokenize, no stemming, no
      stopwords) — what you want for exact-term matching ("tutoring"
      matches only "tutoring"). Both are GIN-indexed; clients pick by
      query intent.

    The compact pass collapses near-duplicates that the v1 UNION misses
    (year/dollar/headcount changes year-over-year, slight rewordings).
    """
    logger.info("Building %s…", NONPROFIT_TEXT_TABLE)
    session.execute(text(f"DROP TABLE IF EXISTS {NONPROFIT_TEXT_TABLE}"))

    # Encourage parallel scan/regex execution. Per-statement only; doesn't
    # leak into the rest of the build's session.
    session.execute(text("SET LOCAL max_parallel_workers_per_gather = 4"))

    # Two-pass normalization. PASS 1 strips year/$/percentage/comma-number
    # tokens to empty. PASS 2 collapses any run of punctuation + whitespace
    # into a single space (combined class so a comma between two spaces
    # collapses with them, instead of breaking the run into 3 separate
    # matches and producing inconsistent whitespace counts).
    #
    # Conservative: 4-digit years (1900s/2000s, anchored at both word
    # boundaries so we don't strip `1998` out of `21998`), dollar amounts,
    # percentages, and comma-grouped numbers go to empty. Bare digits are
    # deliberately retained — "5 victims" vs "50 victims" stay distinct.
    # ``\m``/``\M`` are PG ARE word-boundary anchors (``\b`` would mean
    # backspace in PG — see SCHEDULE_O_PART_III_FILTER above for the same
    # convention).
    norm_strip = r"\m(19|20)\d{2}\M|\$\d[\d,.]*|\d+%|\d{1,3}(,\d{3})+"
    norm_collapse = r"[[:punct:][:space:]]+"

    session.execute(
        text(
            f"""
            CREATE TABLE {NONPROFIT_TEXT_TABLE} AS
            WITH all_text AS MATERIALIZED (
                -- Single materialization of every non-empty (ein, txt, taxyear)
                -- across the five source fields. UNION ALL — dedup happens
                -- downstream so we don't pay a hash/sort to collapse rows
                -- that the norm_key step is going to collapse anyway.
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
            ),
            per_ein_old AS (
                -- v1 surface: exact-string dedup on (ein, txt).
                SELECT ein,
                       COUNT(*)                                AS n_source_rows,
                       string_agg(txt, E'\\n\\n' ORDER BY txt) AS unique_text
                FROM (SELECT DISTINCT ein, txt FROM all_text) d
                GROUP BY ein
            ),
            norm AS MATERIALIZED (
                -- Materialized so the two regex passes run once per source
                -- row, not twice (once for `reps`, once for `per_ein_recall`).
                SELECT ein, txt, taxyear, md5(norm_clean) AS norm_key
                FROM (
                    SELECT ein, txt, taxyear,
                           trim(regexp_replace(
                               regexp_replace(
                                   lower(txt), '{norm_strip}', '', 'g'
                               ),
                               '{norm_collapse}', ' ', 'g'
                           )) AS norm_clean
                    FROM all_text
                ) x
                WHERE length(norm_clean) > 0
            ),
            reps AS (
                -- One representative per (ein, norm_key) cluster: most
                -- recent year wins; longest txt as tiebreak; txt itself
                -- as a deterministic final tiebreak so re-runs on the
                -- same data produce identical output.
                SELECT DISTINCT ON (ein, norm_key)
                    ein, norm_key, txt AS rep_txt
                FROM norm
                ORDER BY ein, norm_key,
                         taxyear DESC NULLS LAST,
                         length(txt) DESC,
                         txt
            ),
            per_ein_compact AS (
                SELECT ein,
                       string_agg(rep_txt, E'\\n\\n' ORDER BY rep_txt)
                           AS unique_text_compact,
                       COUNT(*) AS n_compact_snippets
                FROM reps
                GROUP BY ein
            ),
            per_ein_recall AS (
                -- Token union for FTS: every cluster member's original
                -- text contributes tokens, even if its representative was
                -- displaced. tsvector dedups tokens, so the GIN index
                -- stays small while preserving search recall.
                SELECT ein, string_agg(txt, ' ') AS recall_text
                FROM norm
                GROUP BY ein
            )
            SELECT
                o.ein,
                o.n_source_rows,
                o.unique_text,
                to_tsvector('{FTS_CONFIG}', o.unique_text)         AS text_tsv,
                c.unique_text_compact,
                c.n_compact_snippets,
                to_tsvector('{FTS_CONFIG}', COALESCE(r.recall_text, ''))
                                                                   AS text_tsv_compact,
                to_tsvector('simple', COALESCE(r.recall_text, ''))
                                                                   AS text_tsv_compact_simple,
                NOW() AT TIME ZONE 'UTC'                           AS _built_at
            FROM per_ein_old o
            LEFT JOIN per_ein_compact c ON c.ein = o.ein
            LEFT JOIN per_ein_recall  r ON r.ein = o.ein
            """
        )
    )
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
    session.execute(
        text(
            f"""
            CREATE INDEX ix_nonprofit_text_tsv
            ON {NONPROFIT_TEXT_TABLE}
            USING GIN (text_tsv)
            """
        )
    )
    session.execute(
        text(
            f"""
            CREATE INDEX ix_nonprofit_text_tsv_compact
            ON {NONPROFIT_TEXT_TABLE}
            USING GIN (text_tsv_compact)
            """
        )
    )
    session.execute(
        text(
            f"""
            CREATE INDEX ix_nonprofit_text_tsv_compact_simple
            ON {NONPROFIT_TEXT_TABLE}
            USING GIN (text_tsv_compact_simple)
            """
        )
    )
    count = session.execute(
        text(f"SELECT COUNT(*) FROM {NONPROFIT_TEXT_TABLE}")
    ).scalar_one()
    logger.info("%s: %s rows", NONPROFIT_TEXT_TABLE, f"{count:,}")
    return count


def _build_funder_canonical(session) -> int:
    """DROP + CREATE + populate public.funder_canonical from basic_fields_pf.

    Private-foundation identity table — mirrors ``nonprofit_canonical``'s
    selection logic (latest taxyear, then latest taxperend, then latest
    ingested) but built against ``basic_fields_pf``. Funder classification
    (DAF / community / corporate / family) is deliberately out of scope for
    v1 — it's a Phase 3 enrichment task that needs Candid data. v1 carries
    identity + address + contact only.

    Note: many grant-making 501(c)(3)s (community foundations, etc.) file
    990, not 990-PF. They live in ``nonprofit_canonical`` today. A future
    ``funder_canonical`` could be widened to a UNION of grant-makers across
    both form types; keeping the scope narrow here matches the "private
    foundation-centric" grant-making data we already have.
    """
    logger.info("Building %s…", FUNDER_CANONICAL_TABLE)
    session.execute(text(f"DROP TABLE IF EXISTS {FUNDER_CANONICAL_TABLE}"))
    session.execute(
        text(
            f"""
            CREATE TABLE {FUNDER_CANONICAL_TABLE} AS
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
                _source_version                         AS source_version,
                NOW() AT TIME ZONE 'UTC'                AS _built_at
            FROM public.basic_fields_pf
            ORDER BY
                filerein,
                CAST(NULLIF(taxyear, '') AS INT) DESC NULLS LAST,
                taxperend DESC NULLS LAST,
                _ingested_at DESC NULLS LAST
            """
        )
    )
    session.execute(
        text(
            f"""
            ALTER TABLE {FUNDER_CANONICAL_TABLE}
            ADD CONSTRAINT funder_canonical_pkey PRIMARY KEY (ein)
            """
        )
    )
    count = session.execute(
        text(f"SELECT COUNT(*) FROM {FUNDER_CANONICAL_TABLE}")
    ).scalar_one()
    logger.info("%s: %s rows", FUNDER_CANONICAL_TABLE, f"{count:,}")
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
