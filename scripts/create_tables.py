from givingtuesday_datamart.write_data_to_sql import write_csv_url_to_table


TABLES_TO_CREATE = [
    (
        'irs_filings.mission_statements',
        "https://gt990datalake-analytics-and-datamarts.s3.us-east-1.amazonaws.com/EfileDataMarts/2025_08_29_All_Years_990Part1Missions.csv"
    ),
    (
        'irs_filings.programs',
        'https://gt990datalake-analytics-and-datamarts.s3.us-east-1.amazonaws.com/EfileDataMarts/2025_08_29_All_Years_990Part3Programs.csv',
    ),
    (
        'irs_filings.privategrants',
        'https://gt990datalake-analytics-and-datamarts.s3.us-east-1.amazonaws.com/EfileDataMarts/2025_08_29_All_Years_990PFPart14Grants3A.csv', 
    ),
    (
        "irs_filings.basic_fields_pf",
        "https://gt990datalake-analytics-and-datamarts.s3.us-east-1.amazonaws.com/EfileDataMarts/2025_08_29_All_Years_990PFStandardFields.csv",
    ),
    (
        "irs_filings.grants_to_domestic_organizations",
        "https://gt990datalake-analytics-and-datamarts.s3.us-east-1.amazonaws.com/EfileDataMarts/2025_08_29_All_Years_ScheduleIPart2Grants.csv"
    ),
    (
        "irs_filings.basic_fields",
        "https://gt990datalake-analytics-and-datamarts.s3.us-east-1.amazonaws.com/EfileDataMarts/2025_10_18_All_Years_990StandardFields.csv"
    )
]


if __name__ == "__main__":
    for table_name, url in TABLES_TO_CREATE:
        write_csv_url_to_table(
            url,
            table_name,
            overwrite=True,
            use_cache=True,
        )