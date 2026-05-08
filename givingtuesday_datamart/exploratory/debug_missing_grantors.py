"""
Goal is to find which grantors are missing from the unioned_grants table
compared to dataset that includes Recipient | Funder EINs - "grantor_recipient_labeled_set"

grantor_recipient_labeled_set is a subset of the true grantor_recipient relationship.

There are 3 types of misses:

* Grantor in "grantor_recipient_labeled_set" missing entirely from unioned_grants
  - This could be the case where a grantor's 990PF has "see attached" for the grants section.
    - This is making an assumption that the grantor had "see attached" in every year....
  - The grantor is a Non-Profit and their 990 Schedule I Part II says "see addtional information"
    - Happens a lot with the DAFs

* Grantor | Recipient in "grantor_recipient_labeled_set" missing from privategrants_w_recipients but Grantor is in privategrants_w_recipients
  - This could be the case where the grantor listed grants in the 990PF but the matching algorithm failed to match the recipient.
  - This could be the case where a grantor's 990PF has "see attached" for the grants section for a given year.

* Grantor | Recipient in "privategrants_w_recipients" missing from "grantor_recipient_labeled_set"
  - This could be the case where the matching algorithm created a false positive.
  - This could be the case where the data is correct, but we didn't get the match from the provider because the recipient
    was not in the query the provider ran.

"""

import pandas as pd
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import Text, text
from givingtuesday_datamart._internal.db import get_session, get_configuration
from givingtuesday_datamart._internal.logger import logger


DEFAULT_PROPUBLICA_REQUESTS_PER_SECOND = 5


def _get_config():
    config = get_configuration()
    if config["postgres"]["database"] != "gt_datamart":
        config["postgres"]["database"] = "gt_datamart"
    return config


def insert_labeled_set_into_table(paths_to_labeled_sets: list[str]):
    """Insert the labeled set into the table."""
    labeled_dfs = []
    for path_to_labeled_set in paths_to_labeled_sets:
        logger.info(f"Reading {path_to_labeled_set}")
        df = pd.read_csv(path_to_labeled_set, delimiter="|", encoding="utf-16")
        df = df[df['funder_ein'].notna() & df['recip_ein'].notna()].copy()
        df['recip_ein'] = df['recip_ein'].apply(lambda x: x.replace("-", ""))
        df['funder_ein'] = df['funder_ein'].apply(lambda x: x.replace("-", ""))
        labeled_dfs.append(df)
    df = pd.concat(labeled_dfs)
    df.drop_duplicates(subset=['funder_ein', 'recip_ein'], inplace=True)
    logger.info(f"Inserting {len(df)} rows into grantor_recipient_labeled_set")
    with get_session(config=_get_config()) as session:
        connection = session.connection()
        df.to_sql(
            "grantor_recipient_labeled_set",
            connection,
            if_exists="replace",
            dtype={
                "recip_ein": Text,
                "funder_ein": Text,
            },
            index=False
        )

    with get_session(config=_get_config()) as session:
        "ALTER TABLE grantor_recipient_labeled_set ADD COLUMN filing_type TEXT"
        connection = session.connection()
        connection.execute(text("ALTER TABLE grantor_recipient_labeled_set ADD COLUMN filing_type TEXT"))
        logger.info("Successfully added filing_type column to grantor_recipient_labeled_set")
        connection.execute(text("""
        UPDATE grantor_recipient_labeled_set
        SET filing_type = '990'
        WHERE funder_ein IN (SELECT DISTINCT filerein FROM basic_fields)"""
        ))

        connection.execute(text("""
        UPDATE grantor_recipient_labeled_set
        SET filing_type = '990PF'
        WHERE funder_ein IN (SELECT DISTINCT filerein FROM basic_fields_pf)"""
        ))

        connection.execute(text("""
        UPDATE grantor_recipient_labeled_set
        SET filing_type = 'unknown'
        WHERE filing_type IS NULL"""
        ))

    logger.info(f"Successfully inserted {len(df)} rows into grantor_recipient_labeled_set")
    with get_session(config=_get_config()) as session:
        df = pd.read_sql_query(text("SELECT * FROM grantor_recipient_labeled_set"), session.connection())
    return df


def retrieve_labeled_set() -> pd.DataFrame:
    with get_session(config=_get_config()) as session:
        df = pd.read_sql_query(text("SELECT * FROM grantor_recipient_labeled_set"), session.connection())
    return df


def _make_missing_grantors_query(grantor_filing_type: str = "990PF") -> str:
    if grantor_filing_type == "990":
        table_name = "grants_to_domestic_organizations"
    elif grantor_filing_type == "990PF":
        table_name = "privategrants_w_recipients"
    else:
        raise ValueError(f"Invalid grantor filing type: {grantor_filing_type}")
    return f"""
        SELECT funder_ein, COUNT(*) AS count
        FROM grantor_recipient_labeled_set grls
        WHERE funder_ein NOT IN (SELECT DISTINCT filerein FROM {table_name})
        AND grls.filing_type = '{grantor_filing_type}'
        GROUP BY funder_ein
        ORDER BY count DESC
    """, table_name


def find_missing_grantors(grantor_filing_type: str = "990PF") -> None:
    """Find which grantors are missing from the privategrants_w_recipients table
    compared to the grantor_recipient_labeled_set table."""
    with get_session(config=_get_config()) as session:
        connection = session.connection()
        query, table_name  = _make_missing_grantors_query(grantor_filing_type=grantor_filing_type)
        df = pd.read_sql_query(text(query), connection)
        logger.info(f"Found {len(df)} grantors missing from {table_name}")
    return df


def retrieve_missing_grantor_original_dataset(grantor_filing_type: str = "990PF") -> pd.DataFrame:
    if grantor_filing_type == "990":
        original_table_name = "grants_to_domestic_organizations"
    elif grantor_filing_type == "990PF":
        original_table_name = "privategrants"
    else:
        raise ValueError(f"Invalid grantor filing type: {grantor_filing_type}")
    missing_grantors_query, _ = _make_missing_grantors_query(grantor_filing_type=grantor_filing_type)
    full_query = f"""
        WITH missing_grantors AS (
            {missing_grantors_query}
        )
        SELECT mg.*, og.*
        FROM missing_grantors mg
        LEFT JOIN {original_table_name} og
            ON mg.funder_ein = og.filerein
    """
    with get_session(config=_get_config()) as session:
        connection = session.connection()
        df = pd.read_sql_query(text(full_query), connection)
        return df


def lookup_propublica_page(ein):
    url = f'https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json'
    response = requests.get(url)
    if not response.ok:
        return None, None
    data = response.json()
    is_pf = data['organization']['pf_filing_requirement_code'] == 1
    is_np = data['organization']['filing_requirement_code'] == 1
    return is_np, is_pf


def multithread_lookup_propublica_page(
    eins: list[str],
    requests_per_second: float = DEFAULT_PROPUBLICA_REQUESTS_PER_SECOND,
):
    if requests_per_second <= 0:
        raise ValueError("requests_per_second must be greater than 0")

    min_request_interval = 1 / requests_per_second
    request_lock = threading.Lock()
    last_request_at = 0.0

    def rate_limited_lookup(ein: str):
        nonlocal last_request_at
        with request_lock:
            elapsed = time.monotonic() - last_request_at
            if elapsed < min_request_interval:
                time.sleep(min_request_interval - elapsed)
            last_request_at = time.monotonic()

        return lookup_propublica_page(ein)

    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(rate_limited_lookup, ein): ein
            for ein in eins
        }
        for future in as_completed(futures):
            ein = futures[future]
            is_np, is_pf = future.result()
            results[ein] = {'is_np': is_np, 'is_pf': is_pf}
    return results


def find_missing_grantors_in_all_dataset(
    labeled_df: pd.DataFrame,
    label_propublica=False
):
    # Those with unknown filing type weren't found in the 990 or 990PF basic fields
    missing_completely_df = labeled_df[labeled_df['filing_type'] == 'unknown']
    # Sort by number of times they appear in the labeled set
    missing_completely_unique = (
        missing_completely_df
        .groupby(['funder_ein', 'gm_name'])
        .count()['filing_type']
        .sort_values(ascending=False)
    )
    missing_completely_unique.name = 'count'
    missing_completely_unique = missing_completely_unique.reset_index()
    if label_propublica:
        propublica_data = multithread_lookup_propublica_page(missing_completely_unique['funder_ein'].tolist())
        missing_completely_unique['is_np'] = missing_completely_unique['funder_ein'].map(propublica_data).apply(lambda x: x['is_np'])
        missing_completely_unique['is_pf'] = missing_completely_unique['funder_ein'].map(propublica_data).apply(lambda x: x['is_pf'])
        missing_completely_unique['in_propublica'] = missing_completely_unique['is_np'] | missing_completely_unique['is_pf']
    return missing_completely_unique


if __name__ == "__main__":
    labeled_df = insert_labeled_set_into_table(
        [
            "/Users/zeintawil/dev/vdl/shared-data-clean/data/candid/2025_09_08/candid_funders.txt",
            "/Users/zeintawil/dev/vdl/shared-data-clean/data/candid/education revised_2023_05_24/candid_funders.txt",
        ]
    )
    labeled_df = retrieve_labeled_set()

    #########################################################
    #########################################################
    # Non-Profits that give money that aren't in grants_to_domestic_organizations
    #########################################################
    #########################################################
    # Non-Profits that give money that aren't in grants_to_domestic_organizations
    missing_nps = find_missing_grantors(grantor_filing_type="990")
    missing_nps.to_csv("./data/exploratory/missing_nps.csv", index=False)


    #########################################################
    #########################################################
    # Foundations that give money that aren't in privategrants_w_recipients
    #########################################################
    #########################################################
    missing_pfs_full = retrieve_missing_grantor_original_dataset('990PF')

    #********************************************************
    # Totally missing from the privategrants table
    #********************************************************
    missing_pfs_not_in_privategrants = missing_pfs_full[
        missing_pfs_full['filerein'].isnull()
    ].sort_values('count', ascending=False)[['funder_ein', 'count']]
    missing_pfs_not_in_privategrants.to_csv("./data/exploratory/missing_pfs_not_in_privategrants.csv", index=False)

    #********************************************************
    # Missing from the privategrants_w_recipients table but in the privategrants table
    #********************************************************
    missing_pfs_in_privategrants = (
        missing_pfs_full[
            missing_pfs_full['filerein'].notnull()
        ]
        .copy()
        .sort_values('count', ascending=False)
        .reset_index(drop=True)
    )

    missing_pfs_in_privategrants_count = (
         missing_pfs_in_privategrants
         # Dedupe by funder_ein, gm_name, and taxyear to remove the duplicate from the join on multi-taxyears
         .groupby(['funder_ein', 'taxyear'])
         ['_source_url']
         .count()
         .sort_values(ascending=False)
    )
    missing_pfs_in_privategrants_count.name = 'count_unique_in_taxyear'
    missing_pfs_in_privategrants_count = missing_pfs_in_privategrants_count.reset_index()
    #********************************************************
    # If the count is 1 they either gave 1 real grant we didn't map OR they have 1 line that says something like "see attached"
    #********************************************************

    missing_pfs_in_privategrants_count_singletons = missing_pfs_in_privategrants_count[missing_pfs_in_privategrants_count['count_unique_in_taxyear'] == 1]
    singletons_w_labeled_counts = (
        missing_pfs_in_privategrants_count_singletons.merge(
            missing_pfs_in_privategrants[['funder_ein', 'count']],
            on='funder_ein',
        )
        # [['funder_ein', 'count']]
    )
    singletons_w_labeled_counts = singletons_w_labeled_counts.sort_values('count', ascending=False)
    singletons_w_labeled_counts.to_csv("./data/exploratory/missing_pfs_in_privategrants_count_singletons.csv", index=False)

    #********************************************************
    # If the count is greater than 1 they either gave many grants and we didn't map any of them
    #********************************************************
    missing_pfs_in_privategrants_count_multiples = missing_pfs_in_privategrants_count[missing_pfs_in_privategrants_count['count_unique_in_taxyear'] > 1]

    multiples_w_labeled_counts = (
        missing_pfs_in_privategrants_count_multiples.merge(
            missing_pfs_in_privategrants[['funder_ein', 'count']],
            on='funder_ein',
        )
    )
    multiples_w_labeled_counts = multiples_w_labeled_counts.sort_values(['count_unique_in_taxyear', 'count'], ascending=False)
    multiples_w_labeled_counts = multiples_w_labeled_counts.drop_duplicates(subset=['funder_ein', 'taxyear', 'count_unique_in_taxyear', 'count'])
    multiples_w_labeled_counts.to_csv("./data/exploratory/missing_pfs_in_privategrants_count_multiples.csv", index=False)

    #########################################################
    #########################################################
    # Missing completely from the basic_fields or basic_fields_pf
    #########################################################
    #########################################################

    missing_completely_unique = find_missing_grantors_in_all_dataset(
        labeled_df,
        label_propublica=False
    )
    missing_completely_unique.to_csv("./data/exploratory/missing_completely_unique.csv", index=False)
