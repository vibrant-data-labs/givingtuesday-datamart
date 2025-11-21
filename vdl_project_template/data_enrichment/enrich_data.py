import pandas as pd

from vdl_tools.shared_tools.project_config import get_paths
from vdl_tools.scrape_enrich.scraper.scrape_websites import scrape_websites_psql
from vdl_tools.shared_tools.web_summarization.website_summarization_cache_psql import GENERIC_ORG_WEBSITE_PROMPT_TEXT
from vdl_tools.scrape_enrich.scraper.scrape_websites import extract_website_name
from vdl_tools.shared_tools.web_summarization.website_summarization_psql import summarize_scraped_df
from vdl_tools.shared_tools.database_cache.database_utils import get_session
from vdl_tools.linkedin.org_loader import scrape_organizations_psql
from vdl_tools.shared_tools.tools.config_utils import get_configuration
from vdl_tools.scrape_enrich.combine_crunchbase_candid_linkedin import combine_cb_cd_li
from vdl_tools.shared_tools.climate_landscape.enrichment_pipeline import add_summary_of_summaries
from vdl_tools.shared_tools.climate_landscape.add_taxonomy_mapping import (
    add_tailwind_taxonomy,
)
from vdl_tools.scrape_enrich import geocode

GLOBAL_CONFIG = get_configuration()
paths = get_paths()


def enrich_data():
    # Load combined data
    df = pd.read_json(paths.get('processed_data_path'))
    df['uuid'] = df['id']

    # Scrape All Pages
    web_df = scrape_websites_psql(
        urls=[x for x in df['url_homepage'].tolist() if x],
        skip_existing=True,
        subpage_type='about',
        max_errors=1,
        max_workers=10,
        summary_prompt=GENERIC_ORG_WEBSITE_PROMPT_TEXT,
        return_combined_res=True,
        verify_ssl=True,
    )

    # Summarize Website
    summaries = summarize_scraped_df(
        web_df,
        prompt_str=GENERIC_ORG_WEBSITE_PROMPT_TEXT,
        is_combined=True,
        skip_existing=True,
        max_workers=10,
        max_errors=1,
    )

    scraped_data = {extract_website_name(k): v for k, v in web_df[['cleaned_home_key', 'combined_text']].values if v}
    summaries = {extract_website_name(k): v for k, v in summaries.items() if v}
    df['website_summary'] = df['url_homepage'].map(summaries)

    # Get LinkedIn Data
    with get_session() as session:
        df_linkedin = scrape_organizations_psql(
            urls=df[df['linkedin_url'].notnull()]["linkedin_url"],
            session=session,
            api_key=GLOBAL_CONFIG["linkedin"]["coresignal_api_key"],
            skip_existing=True,
            max_errors=1,
            n_per_commit=10,
        )

    # Putting data in format required for enrichment
    df_linkedin.drop(columns=['summary'], inplace=True)
    df_linkedin.rename(
        columns={"about": "About LinkedIn", "name": "profile_name"},
        inplace=True,
    )

    for col in ['sectors_cb_cd', 'industries_cb_cd', 'n_Employees', 'logo', "hq_address"]:
        df[col] = None

    df['Organization'] = df['name']

    # Combine the LinkedIn data
    df = combine_cb_cd_li(
        df,
        df_linkedin,
        linkedin_url_column='linkedin_url',
        original_website_column="url_homepage",
        website_column="url_homepage_linkedin",
        hq_address_column="hq_address",
    )

    # Combine Text into Single Summary
    ## Website Summary, LinkedIn Summary, Crunchbase Description, Grantham Description
    df = add_summary_of_summaries(
        df,
        text_fields=[
            'website_summary',
            'About LinkedIn',
        ],
        id_col='uuid',
        use_cached_results=True,
        max_workers=10,
    )

    df = df[df['Summary'].notnull()]

    df = add_tailwind_taxonomy(
        df,
        id_col='uuid',
        text_col='Summary',
        name_col='Organization',
        use_cached_results=True,
        max_workers=10
    )

    for col in df.columns:
        if col.startswith('all_level'):
            # For some reason, the taxonomy results have a bunch of empty strings
            df[col] = df[col].apply(lambda x: [val for val in x if val] if isinstance(x, list) else [])

    # Geocode the HQ
    df = geocode.add_geo_lat_long(
        df,  # use file trimmed of <min search terms
        idCol="uuid",  # unique id column
        address="Location",  # address column
    )
    df = geocode.clean_geo(df, summarize_new_geo=False)

    paths.get('enriched_data_path').parent.mkdir(parents=True, exist_ok=True)

    df['funding_column'] = df['funding_max']
    df['funding_column'] = df['funding_column'].fillna(df['funding_min'])
    df['funding_column'] = df['funding_column'].fillna(
    df.to_json(paths.get('enriched_data_path'), orient='records')
    return df


if __name__ == "__main__":
    df = enrich_data()

