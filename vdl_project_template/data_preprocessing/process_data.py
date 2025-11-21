"""
This script is used to process the data before enriching the data.

The goal should be to end with standardized data across all datasets needed
for the project.
"""

import json
import boto3
import pandas as pd
from vdl_tools.shared_tools.project_config import get_paths

PATHS = get_paths()


def save_source_data() -> pd.DataFrame:
    """
    Reads the data from the s3 bucket and saves it to a json file.
    """
    s3 = boto3.client('s3')
    bucket = 'onboarding-and-default-template'
    key = 'lenders.json'
    response = s3.get_object(Bucket=bucket, Key=key)
    data = response['Body'].read().decode('utf-8')
    data = json.loads(data)
    json.dump(data, open(PATHS['source_data_path'], 'w'))
    return data


def process_data(data: pd.DataFrame) -> pd.DataFrame:
    """
    Process the data before enriching the data.
    """
    lenders = data['lenders']
    json.dump(lenders, open(PATHS['processed_data_path'], 'w'))
    return lenders


def main():
    data = save_source_data()
    data = process_data(data)
    return data



if __name__ == '__main__':
    main()
