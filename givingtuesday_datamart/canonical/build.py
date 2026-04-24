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
    """
    ensure_meta_schema()
    with get_session(config=datamart_config()) as session:
        session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {CANONICAL_BUILDS_TABLE} (
                    build_id UUID PRIMARY KEY,
                    started_at TIMESTAMPTZ NOT NULL,
                    finished_at TIMESTAMPTZ,
                    status TEXT NOT NULL,
                    schedule_o_part_iii_rows BIGINT,
                    nonprofit_canonical_rows BIGINT,
                    nonprofit_text_rows BIGINT,
                    funder_canonical_rows BIGINT,
                    person_canonical_rows BIGINT,
                    org_person_role_rows BIGINT,
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
                ADD COLUMN IF NOT EXISTS schedule_o_part_iii_rows BIGINT,
                ADD COLUMN IF NOT EXISTS funder_canonical_rows BIGINT,
                ADD COLUMN IF NOT EXISTS person_canonical_rows BIGINT,
                ADD COLUMN IF NOT EXISTS org_person_role_rows BIGINT
                """
            )
        )


def _latest_successful_run_ids() -> dict[str, str | None]:
    """Map each logical_name → most recent successful run_id (or None).

    Recorded on every canonical build so we can later answer "which source
    versions is this canonical table derived from?" without re-deriving.
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
    """DROP + CREATE + populate public.nonprofit_text with a GIN-indexed tsvector.

    The UNION across every (ein, source, text) triple provides field-level
    dedup: identical text across years or across activity slots collapses
    to one row, so the string_agg result contains each distinct non-empty
    snippet exactly once per EIN.
    """
    logger.info("Building %s…", NONPROFIT_TEXT_TABLE)
    session.execute(text(f"DROP TABLE IF EXISTS {NONPROFIT_TEXT_TABLE}"))
    # Every text field gets an equivalent SELECT … WHERE COALESCE(col,'') <> ''
    # stanza. UNION (not UNION ALL) deduplicates the (src, text) tuples for
    # us, so a mission statement copy-pasted for 15 years contributes one row.
    session.execute(
        text(
            f"""
            CREATE TABLE {NONPROFIT_TEXT_TABLE} AS
            WITH all_text AS (
                SELECT filerein AS ein, 'mission'::text AS src, mission AS txt
                FROM public.mission_statements
                WHERE COALESCE(mission, '') <> ''
                UNION
                SELECT filerein, 'programs_1', actividescri1
                FROM public.programs
                WHERE COALESCE(actividescri1, '') <> ''
                UNION
                SELECT filerein, 'programs_2', actividescri2
                FROM public.programs
                WHERE COALESCE(actividescri2, '') <> ''
                UNION
                SELECT filerein, 'programs_3', actividescri3
                FROM public.programs
                WHERE COALESCE(actividescri3, '') <> ''
                UNION
                SELECT filerein, 'schedule_o_part_iii', supinfdetexp
                FROM {SCHEDULE_O_PART_III_TABLE}
                WHERE COALESCE(supinfdetexp, '') <> ''
            ),
            per_ein AS (
                SELECT
                    ein,
                    COUNT(*)                                   AS n_source_rows,
                    string_agg(txt, E'\\n\\n' ORDER BY src, txt) AS unique_text
                FROM all_text
                GROUP BY ein
            )
            SELECT
                ein,
                n_source_rows,
                unique_text,
                to_tsvector('{FTS_CONFIG}', unique_text) AS text_tsv,
                NOW() AT TIME ZONE 'UTC' AS _built_at
            FROM per_ein
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
    # GIN index on the tsvector is what makes FTS queries fast. Named
    # explicitly so re-runs (which drop the parent table) re-create a
    # consistent index name.
    session.execute(
        text(
            f"""
            CREATE INDEX ix_nonprofit_text_tsv
            ON {NONPROFIT_TEXT_TABLE}
            USING GIN (text_tsv)
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


def build_canonical() -> BuildResult:
    """Rebuild all Phase 2 canonical tables from current staging.

    Both tables are rebuilt inside a single transaction so a failure leaves
    the old canonical state intact — downstream consumers never see a
    half-built canonical layer. The overall build is recorded in
    ``datamart_meta.canonical_builds`` for lineage.
    """
    ensure_canonical_meta()
    build_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    source_runs = _latest_successful_run_ids()

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

    logger.info("Starting canonical build %s", build_id)
    try:
        with get_session(config=datamart_config()) as session:
            # schedule_o_part_iii must land before nonprofit_text because the
            # text build reads from it. All builds share one transaction so
            # downstream consumers never see a half-built canonical layer.
            so3_rows = _build_schedule_o_part_iii(session)
            np_rows = _build_nonprofit_canonical(session)
            txt_rows = _build_nonprofit_text(session)
            fn_rows = _build_funder_canonical(session)
            person_rows = _build_person_canonical(session)
            role_rows = _build_org_person_role(session)
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
]
