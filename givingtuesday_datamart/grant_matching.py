import concurrent.futures as cf
import io
import json
import re
import uuid
from datetime import datetime, timezone

import boto3
import botocore.config
import pandas as pd
import pyarrow.parquet as pq
import recordlinkage
from sqlalchemy import text
from tqdm import tqdm

from givingtuesday_datamart._internal import parquet_cache as pqc
from givingtuesday_datamart._internal.address_cleaning import create_clean_address
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart._internal.parquet_cache import _decode_json as _pqc_decode_json
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.canonical.build import (
    CANONICAL_BUILDS_TABLE,
    ensure_canonical_meta,
    latest_successful_run_ids,
)

# Derived from USPS Publication 28 - Postal Addressing Standards


# --- 1. SQL DDL HELPERS ---
# These were previously in sql_queries/unique_fields_for_grants.sql. Lifted into
# Python so the matching pipeline owns its own preconditions and postconditions
# in one place. The views must exist before matching reads them; unioned_grants
# must be rebuilt after matching writes privategrants_w_recipients.

# 4 views built on top of the raw `public.privategrants` and `public.basic_fields`
# staging tables. Both `_w_column_keys_view` views normalize names/addresses
# (lowercase, 5-digit zip) into `*_key` columns the matching pipeline blocks/joins
# on. Both `_unique_names_view` views collapse to DISTINCT (key tuple) for orgs
# that filed in 2015+.
_VIEW_DDL = [
    """
    CREATE OR REPLACE VIEW public.privategrants_w_column_keys_view AS (
        SELECT
            *,
            CASE WHEN sigocpyrbnbn1 IS NULL THEN '' ELSE LOWER(sigocpyrbnbn1) END name1_key,
            CASE WHEN sigocpyrbnbn2 IS NULL THEN '' ELSE LOWER(sigocpyrbnbn2) END name2_key,
            CASE WHEN sigocpyrfaal1 IS NULL THEN '' ELSE LOWER(sigocpyrfaal1) END address1_key,
            CASE WHEN sigocpyrfaal2 IS NULL THEN '' ELSE LOWER(sigocpyrfaal2) END address2_key,
            LOWER(sigocpyrfaci) addresscity_key,
            LOWER(sigocpyrfapo) addressstate_key,
            LOWER(LEFT(sigocpyrfapc, 5)) addresszip_key
        FROM public.privategrants
    )
    """,
    """
    CREATE OR REPLACE VIEW public.privategrants_unique_names_view AS (
        SELECT
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
        FROM public.privategrants_w_column_keys_view
        WHERE taxyear::int >= 2015
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
    CREATE OR REPLACE VIEW public.basic_fields_w_column_keys_view AS (
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
    """
    CREATE OR REPLACE VIEW public.basic_fields_unique_names_view AS (
        SELECT
            filerein_key,
            name1_key,
            name2_key,
            address1_key,
            address2_key,
            addresscity_key,
            addressstate_key,
            addresszip_key
        FROM public.basic_fields_w_column_keys_view bf
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
]


# Union of (a) matched private foundation grants written to
# public.privategrants_w_recipients and (b) Schedule I grants from 990 filers.
# Column aliases preserved verbatim from the original SQL so downstream
# consumers see the same shape.
_UNIONED_GRANTS_DDL = """
DROP TABLE IF EXISTS public.unioned_grants;
SELECT *
INTO public.unioned_grants
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
        sigocpyamoun::bigint AS grant_amount,
        sigocpypogoc AS grant_purpose,
        sigocpyrfsta AS grant_status,
        sigocpyrrela AS grant_relationship
    FROM public.privategrants_w_recipients

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
        NULL AS grant_relationship
    FROM public.grants_to_domestic_organizations
) sub;
"""

_UNIONED_GRANTS_INDEXES = [
    "DROP INDEX IF EXISTS idx_unioned_grants_granter_ein",
    "DROP INDEX IF EXISTS idx_unioned_grants_grantee_ein",
    "DROP INDEX IF EXISTS idx_unioned_grants_taxyear",
    "CREATE INDEX IF NOT EXISTS idx_unioned_grants_granter_ein ON public.unioned_grants (granter_ein)",
    "CREATE INDEX IF NOT EXISTS idx_unioned_grants_grantee_ein ON public.unioned_grants (grantee_ein)",
    "CREATE INDEX IF NOT EXISTS idx_unioned_grants_taxyear ON public.unioned_grants (taxyear)",
]


def create_or_replace_views(connection):
    """Idempotently (re)create the 4 views the matching pipeline reads from.

    Safe to call on every run: `CREATE OR REPLACE VIEW` updates definitions
    in place without touching dependent objects.
    """
    logger.info("Creating/replacing grant matching views in public.*")
    for ddl in _VIEW_DDL:
        connection.execute(text(ddl))


def rebuild_unioned_grants(connection):
    """Drop and rebuild public.unioned_grants from the matched PF grants
    and the 990 Schedule I grants. Recreates the 3 EIN/year indexes after.
    """
    logger.info("Rebuilding public.unioned_grants")
    connection.execute(text(_UNIONED_GRANTS_DDL))
    for stmt in _UNIONED_GRANTS_INDEXES:
        connection.execute(text(stmt))


# Logical names of the two staging tables the matching pipeline reads from.
# These are the only data dependencies whose freshness affects checkpoint
# validity. Schedule-I grants feed unioned_grants but not the matching itself.
_MATCHING_INPUT_LOGICAL_NAMES = ("irs_990pf_grants", "irs_990_basic_fields")


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
    base_prefix: str = "grant_matching_checkpoints",
) -> str:
    """Build an S3 prefix that's keyed on the source versions of both
    upstream staging tables. A new ingest of either source produces a fresh
    prefix, so old checkpoints can never silently be reused against new data.

    Resolves the latest successful (status='success') ingest_run per
    logical_name from datamart_meta.ingest_runs. Raises if either source has
    no successful run on record.
    """
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
    # Nested rather than flat (`pg_X__bf_Y`) so `aws s3 ls` is actually
    # browseable: each level shows progressively narrower scope, and a single
    # source version's chunks can be `aws s3 rm --recursive`'d in one shot.
    return (
        f"{base_prefix}/"
        f"pg_{versions['irs_990pf_grants']}/"
        f"bf_{versions['irs_990_basic_fields']}"
    )


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

def create_full_name(row):
    return " ".join([p.strip() for p in [row['name1_key'], row['name2_key']] if p.strip()])


def filter_match_rules(
    features_df: pd.DataFrame,
    near_perfect_name_name_min: float,
    near_perfect_name_addr_min: float,
    near_perfect_addr_name_min: float,
    near_perfect_addr_addr_min: float,
    good_enough_name_name_min: float,
    good_enough_name_addr_min: float,
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

    return features_df[
        near_perfect_name_matches | near_perfect_addr_matches | good_enough_name_matches
    ]



def match_records(
    chunk_size: int = 50000,
    s3_bucket: str = "givingtuesday-datamart",
    s3_prefix: str | None = None,
    resume_from_checkpoints: bool = True,
    resume_workers: int = 32,
    limit: int | None = None,
):
    """Run the recordlinkage grant-matching pipeline against gt_datamart.

    ``s3_prefix`` defaults to a lineage-keyed path derived from the latest
    successful ingest_runs of irs_990pf_grants + irs_990_basic_fields, e.g.
    ``grant_matching_checkpoints/pg_2026_04_15/bf_2026_04_18``. This means
    checkpoints can only be resumed against the exact source versions that
    produced them — re-ingesting either source forces a clean recompute.
    Pass an explicit string to override (e.g. for testing).

    ``limit`` (test-only): caps both view reads to the first N rows. Used
    to validate the chunk write/read round-trip end-to-end without paying
    for a full multi-hour run. When set, chunks are namespaced under a
    ``_test_limit_<N>`` subdirectory so they can't be confused with
    production chunks (the row positions differ between subsets and would
    silently produce wrong matches if mixed). The output tables
    ``public.privategrants_w_recipients`` and ``public.unioned_grants``
    are still rebuilt — small data while testing, restored to full data
    on the next non-limited run.

    Each run is recorded in ``datamart_meta.canonical_builds`` with
    ``build_kind='grant_matching'``, the ingest_run_ids of every staging
    source at run time, and (on success) the row counts of
    ``public.privategrants_w_recipients`` and ``public.unioned_grants``.
    Consumers can join against ``datamart_meta.ingest_runs`` to detect
    when the matched/unioned tables are stale relative to upstream.
    """
    ensure_canonical_meta()
    build_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    source_runs = latest_successful_run_ids()
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


def _do_match_records(
    chunk_size: int,
    s3_bucket: str,
    s3_prefix: str | None,
    resume_from_checkpoints: bool,
    resume_workers: int = 32,
    limit: int | None = None,
):
    """Inner pipeline body. Returns ``(full_data_df, pg_rows, ug_rows)``.

    Split out from ``match_records`` so the canonical_builds lifecycle
    (insert started → try → finalize success/failed) wraps the whole
    pipeline cleanly without indenting 200 lines.
    """
    # --- 3. APPLY CLEANING ---
    logger.info("Preparing data...")
    # 1. LOAD DATA
    with get_session(config=datamart_config()) as session:
        connection = session.connection()
        create_or_replace_views(connection)
        if s3_prefix is None:
            s3_prefix = _resolve_checkpoint_prefix(connection)
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

        # Explicit ORDER BY (redundant with the view's own ORDER BY, but
        # contractual): row order MUST be deterministic across runs because
        # chunk checkpoints reference DataFrame rows by integer position.
        # If row order shifts between runs, resumed chunks would point at
        # the wrong rows and produce silently-incorrect matches.
        logger.info("Reading public.basic_fields_unique_names_view")
        basic_fields_df = pd.read_sql_query(
            text(f"""
                SELECT * FROM public.basic_fields_unique_names_view
                ORDER BY filerein_key, name1_key, name2_key, address1_key,
                         address2_key, addresscity_key, addressstate_key,
                         addresszip_key
                {limit_clause}
            """),
            connection,
        )
        logger.info("Reading public.privategrants_unique_names_view")
        private_foundations_df = pd.read_sql_query(
            text(f"""
                SELECT * FROM public.privategrants_unique_names_view
                ORDER BY name1_key, name2_key, address1_key, address2_key,
                         addresscity_key, addressstate_key, addresszip_key
                {limit_clause}
            """),
            connection,
        )

    basic_fields_df.fillna("", inplace=True)
    private_foundations_df.fillna("", inplace=True)

    # A. The Hard Blocks (Must Match Exactly)
    logger.info("Cleaning zip codes...")
    basic_fields_df['clean_zip'] = basic_fields_df['addresszip_key'].apply(clean_zip)
    private_foundations_df['clean_zip'] = private_foundations_df['addresszip_key'].apply(clean_zip)

    # B. The Fuzzy Data
    logger.info("Cleaning names...")
    basic_fields_df['full_name'] = basic_fields_df.apply(create_full_name, axis=1)
    private_foundations_df['full_name'] = private_foundations_df.apply(create_full_name, axis=1)

    logger.info("Cleaning addresses...")
    basic_fields_df['compare_addr'] = basic_fields_df.apply(create_clean_address, axis=1)
    private_foundations_df['compare_addr'] = private_foundations_df.apply(create_clean_address, axis=1)

    # --- 4. SYNC CATEGORIES (Speed Step) ---
    # Make sure the zip codes (and other blocking columns) are Pandas Categorical types for faster matching
    logger.info("Turning zip codes into categorical types...")
    block_cols = ['clean_zip']
    for col in block_cols:
        union_cats = pd.concat([basic_fields_df[col], private_foundations_df[col]]).unique()
        cat_type = pd.CategoricalDtype(categories=union_cats, ordered=False)
        basic_fields_df[col] = basic_fields_df[col].astype(cat_type)
        private_foundations_df[col] = private_foundations_df[col].astype(cat_type)

    # --- 5. EXECUTE BLOCKING ---
    logger.info("Indexing...")
    indexer = recordlinkage.Index()
    indexer.block(
        left_on=['clean_zip'],
        right_on=[ 'clean_zip']
    )

    candidate_links = indexer.index(basic_fields_df, private_foundations_df)
    logger.info(f"Found {len(candidate_links)} pairs to compare.")

    # --- 6. COMPARE ---
    compare = recordlinkage.Compare()
    # compare.exact('clean_year', 'clean_year', label='year_score')
    compare.exact('clean_zip', 'clean_zip', label='zip_score')
    compare.string('full_name', 'full_name', method='jarowinkler', label='name_score')
    compare.string('compare_addr', 'compare_addr', method='levenshtein', label='addr_score')

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
    INDEX_COLS = ['basic_fields_df_index', 'private_foundations_df_index']

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

    # --- Compute path: serial (recordlinkage compute() is single-threaded
    # internally; parallelizing chunks here would compete for memory without
    # buying speed). Keeping it serial also keeps the post-crash resume story
    # simple — every written chunk is fully computed and filtered.
    if to_compute:
        logger.info(f"Computing {len(to_compute)} new chunks...")
    for chunk_idx in tqdm(to_compute, desc="Computing"):
        start_idx = chunk_idx * chunk_size
        end_idx = min(start_idx + chunk_size, total_pairs)
        chunk = candidate_links[start_idx:end_idx]
        features = compare.compute(chunk, basic_fields_df, private_foundations_df)
        # Keep chunk checkpoints generous so reruns can still tighten final rules later.
        features = filter_match_rules(
            features_df=features,
            near_perfect_name_name_min=0.95,
            near_perfect_name_addr_min=0.35,
            near_perfect_addr_name_min=0.75,
            near_perfect_addr_addr_min=0.75,
            good_enough_name_name_min=0.55,
            good_enough_name_addr_min=0.85,
        )

        # Materialize the (basic_fields_idx, privategrants_idx) MultiIndex
        # as regular columns so it survives the parquet round-trip.
        features.index = features.index.set_names(INDEX_COLS)
        features = features.reset_index()

        pqc.write_dataframe(features, f"{checkpoint_uri_base}/chunk_{chunk_idx:05d}.parquet")
        results[chunk_idx] = features

    logger.info(
        f"Chunk processing complete: loaded {len(resumed)} from checkpoints, "
        f"computed {len(to_compute)} new chunks."
    )

    if not results:
        final_features = pd.DataFrame(
            columns=INDEX_COLS + ['zip_score', 'name_score', 'addr_score']
        )
    else:
        # Concat in chunk_idx order. Order doesn't strictly matter for
        # correctness (the index columns carry row positions), but preserves
        # determinism vs. the previous serial-loop behavior.
        ordered = [results[idx] for idx in sorted(results.keys())]
        final_features = pd.concat(ordered, ignore_index=True)
    logger.info(f"Finished matching")

    # --- 7. FILTER ---
    logger.info(f"Filtering matches...")
    matches = filter_match_rules(
        features_df=final_features,
        near_perfect_name_name_min=0.99,
        near_perfect_name_addr_min=0.50,
        near_perfect_addr_name_min=0.85,
        near_perfect_addr_addr_min=0.85,
        good_enough_name_name_min=0.70,
        good_enough_name_addr_min=0.90,
    )

    logger.info(f"Found {len(matches)} matches.")

    # filter_match_rules returns a boolean-mask slice (a view). Take an
    # explicit copy so subsequent assignments don't trigger
    # SettingWithCopyWarning on a view. The index columns are already
    # regular columns on `matches` (materialized at chunk-write time), so
    # no reset_index / rename gymnastics needed.
    matches = matches.copy()

    matches = matches.drop_duplicates()

    # Each private_foundations record (grant recipient) should map to exactly one
    # basic_fields org. When fuzzy matching finds multiple candidates, keep the one
    # with the highest combined score; name_score is weighted 2x since the name is the
    # primary identifier and address collisions (shared buildings, PO boxes) are common.
    pre_resolve_count = len(matches)
    matches = matches.assign(_combined_score=matches['name_score'] * 2 + matches['addr_score'])
    matches = matches.sort_values('_combined_score', ascending=False)
    matches = matches.drop_duplicates(subset=['private_foundations_df_index'], keep='first')
    matches = matches.drop(columns='_combined_score')
    logger.info(
        f"Resolved multi-matches: {pre_resolve_count} -> {len(matches)} "
        f"pairs after keeping the best basic_fields match per private_foundations record."
    )

    basic_fields_df_matched_w_nulls = basic_fields_df.merge(matches, left_index=True, right_on='basic_fields_df_index', how='left')
    basic_fields_df_matched = basic_fields_df_matched_w_nulls[basic_fields_df_matched_w_nulls['private_foundations_df_index'].notna()]

    logger.info(basic_fields_df_matched.head())

    percentage_matched = basic_fields_df_matched[basic_fields_df_matched['private_foundations_df_index'].notna()]['filerein_key'].nunique() / basic_fields_df['filerein_key'].nunique()
    logger.info(f'% of Organizations matched to a private foundation name: {percentage_matched}')

    private_foundations_df['private_foundations_df_index'] = private_foundations_df.index
    full_data_df = basic_fields_df_matched.merge(private_foundations_df, on='private_foundations_df_index', suffixes=("_bf", ""))[[
        'filerein_key',
        'name1_key',
        'name2_key',
        'address1_key',
        'address2_key',
        'addresscity_key',
        'addressstate_key',
        'addresszip_key',
    ]].drop_duplicates()

    full_data_df.rename(columns={
        'filerein_key': 'recipeint_ein_key',
    }, inplace=True)

    temp_join_table_name = "pf_grant_matching_temp_table"
    logger.info(f"Writing to database: {temp_join_table_name}")
    with get_session(config=datamart_config()) as session:
        connection = session.connection()
        full_data_df.to_sql(
            temp_join_table_name,
            connection,
            schema="public",
            if_exists="replace"
        )

        private_grants_w_recipient_table_name = "privategrants_w_recipients"
        logger.info(f"Dropping table: {private_grants_w_recipient_table_name}")
        connection.execute(text(f"DROP TABLE IF EXISTS public.{private_grants_w_recipient_table_name}"))
        logger.info(f"Creating table: {private_grants_w_recipient_table_name}")
        # Joins against the *_view rather than a materialized table — only the
        # view exists in gt_datamart's public schema. SELECT INTO materializes
        # the join result into a real table.
        connection.execute(text(f"""
            SELECT
                pg.*,
                pfgm.recipeint_ein_key
            INTO public.{private_grants_w_recipient_table_name}
            FROM public.privategrants_w_column_keys_view pg
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

        # Final step: rebuild the public.unioned_grants table from the freshly-
        # written privategrants_w_recipients + the 990 Schedule I grants. Same
        # session as the SELECT INTO above so a mid-pipeline failure leaves an
        # obviously-incomplete state rather than a stale unioned_grants.
        rebuild_unioned_grants(connection)

        # Row counts of the two output tables, captured in the same session
        # that wrote them so they're guaranteed-consistent with what just
        # committed. Bubbled up to the canonical_builds 'success' row.
        pg_rows = connection.execute(
            text("SELECT COUNT(*) FROM public.privategrants_w_recipients")
        ).scalar_one()
        ug_rows = connection.execute(
            text("SELECT COUNT(*) FROM public.unioned_grants")
        ).scalar_one()
    return full_data_df, pg_rows, ug_rows


if __name__ == "__main__":
    import argparse

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
    args = parser.parse_args()

    match_records(
        limit=args.limit,
        resume_from_checkpoints=not args.no_resume,
        chunk_size=args.chunk_size,
        resume_workers=args.resume_workers,
    )