"""
Script to download a CSV from a URL and write it to a database table.

This script:
1. Downloads a CSV file from a remote URL (streaming, no disk cache)
2. Renames all columns to lowercase
3. Writes the data to a database table in batches (overwrites if table exists)

Designed to handle very large files (10GB+) with minimal memory usage.
"""

import argparse
import sys
import io
import csv
import os
from pathlib import Path
from urllib.parse import urlparse

import polars as pl
import requests
from sqlalchemy import text

from vdl_tools.shared_tools.database_cache.database_utils import get_session
from vdl_tools.shared_tools.tools.logger import logger


# Batch size for streaming - balance between memory usage and performance
STREAMING_BATCH_SIZE = 50000  # rows per batch


def _sanitize_column_name(col: str) -> str:
    """
    Sanitize a single column name for SQL.
    
    Args:
        col: Original column name
        
    Returns:
        Sanitized lowercase column name
    """
    sanitized = col.lower()
    sanitized = sanitized.replace('.', '_')
    sanitized = sanitized.replace(' ', '_')
    sanitized = sanitized.replace('-', '_')
    sanitized = sanitized.replace('(', '')
    sanitized = sanitized.replace(')', '')
    while '__' in sanitized:
        sanitized = sanitized.replace('__', '_')
    sanitized = sanitized.strip('_')
    return sanitized


def _build_column_mapping(columns: list[str]) -> dict[str, str]:
    """
    Build a mapping from original column names to sanitized SQL-safe names.
    Handles duplicate column names by appending numeric suffixes.
    
    Args:
        columns: List of original column names
        
    Returns:
        Dictionary mapping original names to sanitized names
    """
    column_mapping = {}
    seen_names = {}
    
    for col in columns:
        sanitized = _sanitize_column_name(col)
        
        if sanitized in seen_names:
            seen_names[sanitized] += 1
            sanitized = f"{sanitized}_{seen_names[sanitized]}"
        else:
            seen_names[sanitized] = 0
        
        column_mapping[col] = sanitized
    
    return column_mapping


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
    column_mapping = _build_column_mapping(df.columns)
    return df.rename(column_mapping)


# =============================================================================
# STANDARD MODE: Download to disk, load into memory, write to DB
# Best for files that fit in memory (< 1-2 GB)
# =============================================================================

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
        cache_dir = Path(__file__).parent.parent / "data" / "source_data"
    
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    parsed = urlparse(url)
    filename = Path(parsed.path).name
    
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
    
    if use_cache and os.path.exists(local_path):
        logger.info(f"Found cached file: {local_path}")
        logger.info(f"Reading from local file instead of downloading from: {url}")
        try:
            df = pl.scan_csv(
                str(local_path),
                infer_schema_length=1000,
                ignore_errors=True,
                try_parse_dates=False,
            ).collect()
            return df
        except Exception as e:
            logger.warning(f"Failed to read cached file {local_path}: {e}. Will download from URL.")
    
    logger.info(f"Downloading CSV from: {url}")
    try:
        logger.info(f"Streaming download to: {local_path}")
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        if total_size > 0:
            logger.info(f"File size: {total_size / (1024*1024):.2f} MB")
        
        chunk_size = 10 * 1024 * 1024  # 10 MB chunks
        downloaded = 0
        
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = (downloaded / total_size) * 100
                        logger.info(f"Download progress: {progress:.1f}% ({downloaded / (1024*1024):.2f} MB)")
        
        logger.info(f"Download complete. File saved to: {local_path}")
        
        logger.info("Reading CSV file into memory...")
        df = pl.scan_csv(
            str(local_path),
            infer_schema_length=1000,
            ignore_errors=True,
            try_parse_dates=False,
        ).collect()
        
        logger.info(f"Successfully loaded {len(df)} rows with {len(df.columns)} columns")
        return df
        
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to download CSV from URL: {url}. Error: {e}")
    except Exception as e:
        raise Exception(f"Failed to read CSV from downloaded file: {local_path}. Error: {e}")


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
    if session is None:
        with get_session() as session:
            _write_df_with_session(session, df, table_name, overwrite)
        return
    
    _write_df_with_session(session, df, table_name, overwrite)


def _write_df_with_session(session, df: pl.DataFrame, table_name: str, overwrite: bool):
    """Helper function to write DataFrame using a session."""
    try:
        engine = session.bind
        
        if '.' in table_name:
            schema_name, actual_table_name = table_name.split('.', 1)
        else:
            schema_name = None
            actual_table_name = table_name
        
        logger.info(f"Creating table schema for '{table_name}'...")
        sample_df = df.head(100).to_pandas()
        
        if overwrite:
            sample_df.head(1).to_sql(
                name=actual_table_name,
                schema=schema_name,
                con=engine,
                if_exists='replace',
                index=False
            )
            with engine.connect() as conn:
                conn.execute(text(f"TRUNCATE TABLE {table_name}"))
                conn.commit()
        else:
            with engine.connect() as conn:
                result = conn.execute(text(f"SELECT to_regclass('{table_name}')"))
                table_exists = result.scalar() is not None
            
            if not table_exists:
                sample_df.head(1).to_sql(
                    name=actual_table_name,
                    schema=schema_name,
                    con=engine,
                    if_exists='fail',
                    index=False
                )
                with engine.connect() as conn:
                    conn.execute(text(f"TRUNCATE TABLE {table_name}"))
                    conn.commit()
        
        logger.info(f"Table schema created successfully")
        
        raw_conn = engine.raw_connection()
        try:
            cursor = raw_conn.cursor()
            
            columns = df.columns
            column_names = ', '.join(columns)
            
            batch_size = 100000
            total_rows = len(df)
            rows_written = 0
            
            logger.info(f"Writing {total_rows} rows in batches of {batch_size}...")
            
            for batch_start in range(0, total_rows, batch_size):
                batch_end = min(batch_start + batch_size, total_rows)
                batch_df = df[batch_start:batch_end]
                
                output = io.StringIO()
                writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
                
                for row in batch_df.iter_rows():
                    csv_row = [val if val is not None else '' for val in row]
                    writer.writerow(csv_row)
                
                output.seek(0)
                
                cursor.copy_expert(
                    f"COPY {table_name} ({column_names}) FROM STDIN WITH (FORMAT csv, NULL '')",
                    output
                )
                
                rows_written += len(batch_df)
                progress = (rows_written / total_rows) * 100
                logger.info(f"Progress: {progress:.1f}% ({rows_written:,} / {total_rows:,} rows)")
                
                output.close()
            
            raw_conn.commit()
            cursor.close()
            
            logger.info(f"Successfully wrote {total_rows:,} rows to table '{table_name}' using COPY")
        finally:
            raw_conn.close()
        
    except Exception as e:
        raise Exception(f"Failed to write data to table '{table_name}'. Error: {e}")


def write_csv_url_to_table_standard(
    url: str,
    table_name: str,
    overwrite: bool = True,
    cache_dir: Path = None,
    use_cache: bool = True,
):
    """
    Download a CSV from a URL and write it to a database table (standard mode).
    
    This function downloads the file to disk, loads it into memory, then writes to DB.
    Best for files that fit comfortably in memory (< 1-2 GB).
    
    Args:
        url: URL of the CSV file to download
        table_name: Name of the database table to write to
        overwrite: If True, drop the table if it exists before writing
        cache_dir: Directory to cache files (default: data/source_data)
        use_cache: If True, check for and use cached files
    """
    try:
        df = download_csv_from_url(url, cache_dir=cache_dir, use_cache=use_cache)
        logger.info(f"Loaded {len(df)} rows with {len(df.columns)} columns")

        logger.info("Renaming columns to lowercase...")
        df = rename_columns_to_lowercase(df)
        logger.info(f"Columns: {', '.join(df.columns)}")
        
        logger.info(f"Writing to table '{table_name}'...")
        write_dataframe_to_table(
            df=df,
            table_name=table_name,
            overwrite=overwrite,
        )
        
        logger.info("Script completed successfully!")
        
    except Exception as e:
        raise Exception(f"Failed to write CSV from URL to table: {e}")


# =============================================================================
# STREAMING MODE: Stream directly from URL to DB without disk/memory
# Best for very large files (> 1-2 GB) or memory-constrained systems
# =============================================================================

def _create_table_from_columns(engine, table_name: str, columns: list[str], overwrite: bool):
    """
    Create a table with TEXT columns (we'll let PostgreSQL infer types aren't needed for most analytics).
    
    Args:
        engine: SQLAlchemy engine
        table_name: Full table name (schema.table or just table)
        columns: List of sanitized column names
        overwrite: If True, drop existing table first
    """
    # Build column definitions - use TEXT for everything (safest for large varied data)
    col_defs = ', '.join([f'"{col}" TEXT' for col in columns])
    
    with engine.connect() as conn:
        if overwrite:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
        
        conn.execute(text(f"CREATE TABLE IF NOT EXISTS {table_name} ({col_defs})"))
        conn.commit()
    
    logger.info(f"Created table {table_name} with {len(columns)} columns")


def _write_batch_to_db(cursor, table_name: str, columns: list[str], rows: list[list]):
    """
    Write a batch of rows to the database using COPY.
    
    Args:
        cursor: psycopg2 cursor
        table_name: Target table name
        columns: List of column names
        rows: List of row data (list of lists)
    """
    if not rows:
        return
    
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    
    for row in rows:
        csv_row = [val if val is not None else '' for val in row]
        writer.writerow(csv_row)
    
    output.seek(0)
    
    column_names = ', '.join([f'"{col}"' for col in columns])
    cursor.copy_expert(
        f"COPY {table_name} ({column_names}) FROM STDIN WITH (FORMAT csv, NULL '')",
        output
    )
    output.close()


def stream_csv_url_to_table(
    url: str,
    table_name: str,
    overwrite: bool = True,
    batch_size: int = STREAMING_BATCH_SIZE,
):
    """
    Stream a CSV from a URL directly to a database table.
    
    This function streams the download and processes rows in batches,
    never loading the entire file into memory or disk.
    
    Designed for very large files (10GB+) on memory-constrained systems.
    
    Args:
        url: URL of the CSV file to download
        table_name: Name of the database table to write to
        overwrite: If True, drop the table if it exists before writing
        batch_size: Number of rows to process at a time (default: 50000)
    """
    logger.info(f"Streaming CSV from: {url}")
    logger.info(f"Target table: {table_name}")
    logger.info(f"Batch size: {batch_size:,} rows")
    
    try:
        # Start streaming download
        response = requests.get(url, stream=True, timeout=3600)  # 1 hour timeout for large files
        response.raise_for_status()
        
        # Get file size if available
        total_size = int(response.headers.get('content-length', 0))
        if total_size > 0:
            logger.info(f"File size: {total_size / (1024*1024*1024):.2f} GB")
        
        # Wrap response in a text stream for CSV reader
        # Use iter_lines for memory-efficient line-by-line reading
        lines_iter = response.iter_lines(decode_unicode=True)
        
        # Read header line
        header_line = next(lines_iter)
        csv_reader = csv.reader([header_line])
        original_columns = next(csv_reader)
        
        # Build column mapping and get sanitized names
        column_mapping = _build_column_mapping(original_columns)
        sanitized_columns = [column_mapping[col] for col in original_columns]
        
        logger.info(f"Found {len(sanitized_columns)} columns")
        logger.info(f"Columns: {', '.join(sanitized_columns[:10])}{'...' if len(sanitized_columns) > 10 else ''}")
        
        # Create table
        with get_session() as session:
            engine = session.bind
            _create_table_from_columns(engine, table_name, sanitized_columns, overwrite)
            
            # Get raw connection for COPY
            raw_conn = engine.raw_connection()
            try:
                cursor = raw_conn.cursor()
                
                # Process rows in batches
                batch = []
                total_rows = 0
                bytes_processed = 0
                
                for line in lines_iter:
                    if not line:  # Skip empty lines
                        continue
                    
                    bytes_processed += len(line.encode('utf-8'))
                    
                    # Parse the CSV line
                    try:
                        csv_reader = csv.reader([line])
                        row = next(csv_reader)
                        
                        # Ensure row has correct number of columns
                        if len(row) < len(sanitized_columns):
                            row.extend([''] * (len(sanitized_columns) - len(row)))
                        elif len(row) > len(sanitized_columns):
                            row = row[:len(sanitized_columns)]
                        
                        batch.append(row)
                    except Exception as e:
                        # Skip malformed rows
                        logger.warning(f"Skipping malformed row: {e}")
                        continue
                    
                    # Write batch when full
                    if len(batch) >= batch_size:
                        _write_batch_to_db(cursor, table_name, sanitized_columns, batch)
                        raw_conn.commit()
                        
                        total_rows += len(batch)
                        batch = []
                        
                        # Log progress
                        if total_size > 0:
                            progress = (bytes_processed / total_size) * 100
                            logger.info(f"Progress: {progress:.1f}% - {total_rows:,} rows written")
                        else:
                            logger.info(f"Rows written: {total_rows:,}")
                
                # Write remaining rows
                if batch:
                    _write_batch_to_db(cursor, table_name, sanitized_columns, batch)
                    raw_conn.commit()
                    total_rows += len(batch)
                
                cursor.close()
                logger.info(f"Successfully wrote {total_rows:,} rows to table '{table_name}'")
                
            finally:
                raw_conn.close()
                
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to download CSV from URL: {url}. Error: {e}")
    except Exception as e:
        raise Exception(f"Failed to stream CSV to table: {e}")


def write_csv_url_to_table(
    url: str,
    table_name: str,
    overwrite: bool = True,
    streaming: bool = False,
    batch_size: int = STREAMING_BATCH_SIZE,
    cache_dir: Path = None,
    use_cache: bool = True,
):
    """
    Download a CSV from a URL and write it to a database table.
    
    Args:
        url: URL of the CSV file to download
        table_name: Name of the database table to write to
        overwrite: If True, drop the table if it exists before writing (default: True)
        streaming: If True, use streaming mode (no disk/memory). If False, use standard mode.
        batch_size: (streaming mode only) Number of rows to process at a time
        cache_dir: (standard mode only) Directory to cache files
        use_cache: (standard mode only) If True, check for and use cached files
    
    Returns:
        None
    
    Raises:
        Exception: If any step fails (download, processing, or database write)
    """
    if streaming:
        logger.info("Using STREAMING mode (memory-efficient for large files)")
        stream_csv_url_to_table(
            url=url,
            table_name=table_name,
            overwrite=overwrite,
            batch_size=batch_size,
        )
    else:
        logger.info("Using STANDARD mode (downloads to disk, loads into memory)")
        write_csv_url_to_table_standard(
            url=url,
            table_name=table_name,
            overwrite=overwrite,
            cache_dir=cache_dir,
            use_cache=use_cache,
        )


def main():
    """Main function to run the script from command line."""
    parser = argparse.ArgumentParser(
        description="Download CSV from URL and write to database table",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  Standard (default): Downloads file to disk, loads into memory, writes to DB.
                      Best for files < 1-2 GB. Supports caching.
  
  Streaming (--streaming): Streams directly from URL to DB without disk/memory.
                           Best for very large files (10GB+) or low-memory systems.

Examples:
  # Standard mode (default) - good for smaller files
  python -m givingtuesday_datamart.write_data_to_sql "https://example.com/data.csv" "schema.table"

  # Streaming mode - for large files on memory-constrained systems
  python -m givingtuesday_datamart.write_data_to_sql "https://example.com/huge.csv" "schema.table" --streaming
        """
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
        "--streaming",
        action="store_true",
        help="Use streaming mode (memory-efficient for large files, no caching)"
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Append to existing table instead of overwriting"
    )
    
    # Standard mode options
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="(Standard mode) Disable local file caching"
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=None,
        help="(Standard mode) Directory to cache downloaded files"
    )
    
    # Streaming mode options
    parser.add_argument(
        "--batch-size",
        type=int,
        default=STREAMING_BATCH_SIZE,
        help=f"(Streaming mode) Rows per batch (default: {STREAMING_BATCH_SIZE})"
    )
    
    args = parser.parse_args()
    
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    
    try:
        write_csv_url_to_table(
            url=args.url,
            table_name=args.table_name,
            overwrite=not args.no_overwrite,
            streaming=args.streaming,
            batch_size=args.batch_size,
            cache_dir=cache_dir,
            use_cache=not args.no_cache,
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

