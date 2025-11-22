"""
Script to download a CSV from a URL and write it to a database table.

This script:
1. Downloads a CSV file from a remote URL
2. Renames all columns to lowercase
3. Writes the data to a database table (overwrites if table exists)
"""

import argparse
import sys
import io
import csv
import os
from pathlib import Path
from urllib.parse import urlparse

import polars as pl
import psycopg2
from sqlalchemy import create_engine, text

from vdl_tools.shared_tools.database_cache.database_utils import get_session
from vdl_tools.shared_tools.tools.logger import logger


def _get_local_cache_path(url: str, cache_dir: Path = None) -> Path:
    """
    Generate a local cache file path from a URL.
    
    Args:
        url: URL of the CSV file
        cache_dir: Directory to cache files (default: data/source_data)
        
    Returns:
        Path object for the cached file
    """
    if cache_dir is None:
        # Default to data/source_data directory
        cache_dir = Path(__file__).parent.parent / "data" / "source_data"
    
    # Create cache directory if it doesn't exist
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract filename from URL
    parsed = urlparse(url)
    filename = Path(parsed.path).name
    
    # If no filename found, use a default
    if not filename or not filename.endswith('.csv'):
        filename = "downloaded_file.csv"
    
    return cache_dir / filename


def download_csv_from_url(url: str, cache_dir: Path = None, use_cache: bool = True) -> pl.DataFrame:
    """
    Download a CSV file from a URL and return it as a Polars DataFrame.
    Checks for local cached file first, downloads only if not found.
    
    Args:
        url: URL of the CSV file to download
        cache_dir: Directory to cache files (default: data/source_data)
        use_cache: If True, check for and use cached files (default: True)
        
    Returns:
        Polars DataFrame containing the CSV data
    """
    local_path = _get_local_cache_path(url, cache_dir)
    
    # Check if local file exists
    if use_cache and os.path.exists(local_path):
        logger.info(f"Found cached file: {local_path}")
        logger.info(f"Reading from local file instead of downloading from: {url}")
        try:
            df = pl.read_csv(
                str(local_path),
                infer_schema_length=10000,
                ignore_errors=True,
                try_parse_dates=True
            )
            return df
        except Exception as e:
            logger.warning(f"Failed to read cached file {local_path}: {e}. Will download from URL.")
            # Continue to download if reading cached file fails
    
    # Download from URL
    logger.info(f"Downloading CSV from: {url}")
    try:
        # Polars can read directly from URLs
        # Use robust parsing options to handle schema inference issues:
        # - infer_schema_length=10000: Sample more rows for better type inference
        # - ignore_errors=True: Handle problematic values gracefully (converts to null)
        # - try_parse_dates=True: Attempt to parse dates automatically
        df = pl.read_csv(
            url,
            infer_schema_length=10000,
            ignore_errors=True,
            try_parse_dates=True
        )
        
        # Save to cache for future use
        if use_cache:
            logger.info(f"Caching file to: {local_path}")
            df.write_csv(str(local_path))
            logger.info(f"File cached successfully")
        
        return df
    except Exception as e:
        raise Exception(f"Failed to download or read CSV from URL: {url}. Error: {e}")


def rename_columns_to_lowercase(df: pl.DataFrame) -> pl.DataFrame:
    """
    Rename all columns to lowercase and sanitize for SQL.
    Removes invalid characters like periods, spaces, etc.
    Handles duplicate column names by appending numeric suffixes.
    
    Args:
        df: Polars DataFrame
        
    Returns:
        Polars DataFrame with lowercase, SQL-safe column names
    """
    # Get current column names and create mapping to lowercase and sanitize
    column_mapping = {}
    seen_names = {}
    
    for col in df.columns:
        # Convert to lowercase and replace invalid SQL characters
        sanitized = col.lower()
        # Replace periods, spaces, and other special chars with underscores
        sanitized = sanitized.replace('.', '_')
        sanitized = sanitized.replace(' ', '_')
        sanitized = sanitized.replace('-', '_')
        sanitized = sanitized.replace('(', '')
        sanitized = sanitized.replace(')', '')
        # Remove any double underscores
        while '__' in sanitized:
            sanitized = sanitized.replace('__', '_')
        # Remove leading/trailing underscores
        sanitized = sanitized.strip('_')
        
        # Handle duplicate column names
        if sanitized in seen_names:
            seen_names[sanitized] += 1
            sanitized = f"{sanitized}_{seen_names[sanitized]}"
        else:
            seen_names[sanitized] = 0
        
        column_mapping[col] = sanitized
    
    return df.rename(column_mapping)


def write_dataframe_to_table(
    df: pl.DataFrame,
    table_name: str,
    overwrite: bool = True,
    session=None
):
    """
    Write a Polars DataFrame to a database table using psycopg2 COPY.
    This is the fastest method for bulk loading data into PostgreSQL.
    
    Args:
        df: Polars DataFrame to write
        table_name: Name of the target database table
        overwrite: If True, drop the table if it exists before writing
        session: SQLAlchemy session (if None, will get a new session)
    """
    # get_session() returns a context manager, so we need to handle it properly
    if session is None:
        # Use the context manager
        with get_session() as session:
            _write_with_session(session, df, table_name, overwrite)
        return
    
    # Session was provided, use it directly
    _write_with_session(session, df, table_name, overwrite)


def _write_with_session(session, df, table_name, overwrite):
    """Helper function to write data using a session."""
    try:
        # Get the engine from the session
        engine = session.bind
        
        # Parse schema and table name
        if '.' in table_name:
            schema_name, actual_table_name = table_name.split('.', 1)
        else:
            schema_name = None
            actual_table_name = table_name
        
        # Convert to pandas for table operations
        pandas_df = df.to_pandas()
        
        # Step 1: Create table structure using pandas (creates schema)
        # Use first row to create table, then we'll use COPY for the bulk insert
        if overwrite:
            # Create table with proper schema by writing just first row
            pandas_df.head(1).to_sql(
                name=actual_table_name,
                schema=schema_name,
                con=engine,
                if_exists='replace',
                index=False
            )
            # Delete the sample row - we'll insert all data via COPY
            with engine.connect() as conn:
                conn.execute(text(f"TRUNCATE TABLE {table_name}"))
                conn.commit()
        else:
            # Check if table exists, if not create it
            with engine.connect() as conn:
                result = conn.execute(text(f"SELECT to_regclass('{table_name}')"))
                table_exists = result.scalar() is not None
            
            if not table_exists:
                # Create table with proper schema
                pandas_df.head(1).to_sql(
                    name=actual_table_name,
                    schema=schema_name,
                    con=engine,
                    if_exists='fail',
                    index=False
                )
                # Delete the sample row
                with engine.connect() as conn:
                    conn.execute(text(f"TRUNCATE TABLE {table_name}"))
                    conn.commit()
        
        # Step 2: Use COPY for fast bulk insert
        raw_conn = engine.raw_connection()
        try:
            cursor = raw_conn.cursor()
            
            # Convert Polars DataFrame to list of tuples for COPY
            # Get column names (already lowercase and sanitized from rename_columns_to_lowercase)
            columns = df.columns
            column_names = ', '.join(columns)
            
            # Convert DataFrame to list of tuples
            data_tuples = [tuple(row) for row in df.iter_rows()]
            
            # Use COPY FROM with StringIO for fastest bulk insert
            # This is 10-100x faster than INSERT statements
            output = io.StringIO()
            writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
            for row in data_tuples:
                # Convert None to empty string (PostgreSQL COPY will handle NULL)
                csv_row = [val if val is not None else '' for val in row]
                writer.writerow(csv_row)
            
            output.seek(0)
            
            # Use COPY FROM STDIN with CSV format (fastest method)
            cursor.copy_expert(
                f"COPY {table_name} ({column_names}) FROM STDIN WITH (FORMAT csv, NULL '')",
                output
            )
            
            raw_conn.commit()
            cursor.close()
            
            logger.info(f"Successfully wrote {len(df)} rows to table '{table_name}' using COPY")
        finally:
            raw_conn.close()
        
    except Exception as e:
        raise Exception(f"Failed to write data to table '{table_name}'. Error: {e}")


def write_csv_url_to_table(
    url: str,
    table_name: str,
    overwrite: bool = True,
    cache_dir: Path = None,
    use_cache: bool = True,
):
    """
    Download a CSV from a URL and write it to a database table.
    
    This function:
    1. Checks for local cached file, downloads from URL if not found
    2. Renames all columns to lowercase
    3. Writes the data to a database table (overwrites if table exists)
    
    Args:
        url: URL of the CSV file to download
        table_name: Name of the database table to write to
        overwrite: If True, drop the table if it exists before writing (default: True)
        cache_dir: Directory to cache files (default: data/source_data)
        use_cache: If True, check for and use cached files (default: True)
    
    Returns:
        None
    
    Raises:
        Exception: If any step fails (download, processing, or database write)
    """
    try:
        # Step 1: Download CSV from URL (or use cached version)
        df = download_csv_from_url(url, cache_dir=cache_dir, use_cache=use_cache)
        logger.info(f"Loaded {len(df)} rows with {len(df.columns)} columns")
        
        # Step 2: Rename columns to lowercase
        logger.info("Renaming columns to lowercase...")
        df = rename_columns_to_lowercase(df)
        logger.info(f"Columns: {', '.join(df.columns)}")
        
        # Step 3: Write to database
        logger.info(f"Writing to table '{table_name}'...")
        write_dataframe_to_table(
            df=df,
            table_name=table_name,
            overwrite=overwrite,
        )
        
        logger.info("Script completed successfully!")
        
    except Exception as e:
        raise Exception(f"Failed to write CSV from URL to table: {e}")


def main():
    """Main function to run the script from command line."""
    parser = argparse.ArgumentParser(
        description="Download CSV from URL and write to database table"
    )
    parser.add_argument(
        "url",
        type=str,
        help="URL of the CSV file to download"
    )
    parser.add_argument(
        "table_name",
        type=str,
        help="Name of the database table to write to"
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Append to existing table instead of overwriting"
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable local file caching (always download from URL)"
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=None,
        help="Directory to cache downloaded files (default: data/source_data)"
    )
    
    args = parser.parse_args()
    
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    
    try:
        write_csv_url_to_table(
            url=args.url,
            table_name=args.table_name,
            overwrite=not args.no_overwrite,
            cache_dir=cache_dir,
            use_cache=not args.no_cache
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

