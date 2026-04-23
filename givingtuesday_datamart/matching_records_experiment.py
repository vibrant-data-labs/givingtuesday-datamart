import re

import pandas as pd
import recordlinkage
from sqlalchemy import text
from tqdm import tqdm

from vdl_tools.shared_tools.tools.logger import logger
from vdl_tools.shared_tools.database_cache.database_utils import get_session
from vdl_tools.shared_tools.tools.address_cleaning import create_clean_address
from vdl_tools.shared_tools.s3_tools import key_exists
from vdl_tools.shared_tools import parquet_cache as pqc

# Derived from USPS Publication 28 - Postal Addressing Standards


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
    basic_fields_unique_names_table: str = "basic_fields_unique_names_view",
    chunk_size: int = 50000,
    s3_bucket: str = "givingtuesday-datamart",
    s3_prefix: str = "grant_matching_checkpoints",
    resume_from_checkpoints: bool = True,
):
    # --- 3. APPLY CLEANING ---
    logger.info("Preparing data...")
    # 1. LOAD DATA
    # (Replace with your actual paths)
    with get_session() as session:
        connection = session.connection()
        logger.info(f"Reading basic fields unique names table: {basic_fields_unique_names_table}")
        basic_fields_df = pd.read_sql_table(basic_fields_unique_names_table, connection, schema="irs_filings")
        logger.info(f"Reading private foundations unique names table: privategrants_unique_names")
        private_foundations_df = pd.read_sql_table("privategrants_unique_names", connection, schema="irs_filings")

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

    logger.info("Computing matches...")
    results = []
    loaded_from_checkpoint = 0
    computed_chunks = 0

    for chunk_idx in tqdm(range(total_chunks), desc="Matching"):
        filename = f"chunk_{chunk_idx:05d}.parquet"
        full_key = f"{s3_prefix}/{filename}"
        checkpoint_uri = f"{checkpoint_uri_base}/{filename}"

        if resume_from_checkpoints and key_exists(bucket=s3_bucket, key=full_key):
            features = pqc.read_dataframe(checkpoint_uri)
            loaded_from_checkpoint += 1
            results.append(features)
            continue

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

        pqc.write_dataframe(features, checkpoint_uri)

        computed_chunks += 1
        results.append(features)

    logger.info(
        f"Chunk processing complete: loaded {loaded_from_checkpoint} from checkpoints, "
        f"computed {computed_chunks} new chunks."
    )

    if not results:
        final_features = pd.DataFrame(columns=['zip_score', 'name_score', 'addr_score'])
    else:
        final_features = pd.concat(results)
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

    matches.reset_index(inplace=True)
    matches.rename(columns={
        'level_0': 'basic_fields_df_index',
        'level_1': 'private_foundations_df_index'
    }, inplace=True)

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
    with get_session() as session:
        connection = session.connection()
        full_data_df.to_sql(
            temp_join_table_name,
            connection,
            schema="irs_filings",
            if_exists="replace"
        )

        private_grants_w_recipient_table_name = f"privategrants_w_recipients"
        logger.info(f"Dropping table: {private_grants_w_recipient_table_name}")
        connection.execute(text(f"DROP TABLE IF EXISTS irs_filings.{private_grants_w_recipient_table_name}"))
        logger.info(f"Creating table: {private_grants_w_recipient_table_name}")
        connection.execute(text(f"""
            SELECT
                pg.*,
                pfgm.recipeint_ein_key
            INTO irs_filings.{private_grants_w_recipient_table_name}
            FROM irs_filings.privategrants_w_column_keys pg
            JOIN irs_filings.{temp_join_table_name} pfgm
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
        # connection.execute(text(f"DROP TABLE IF EXISTS irs_filings.{temp_join_table_name}"))
    return full_data_df

if __name__ == "__main__":
    match_records(
        basic_fields_unique_names_table="basic_fields_unique_names",
    )