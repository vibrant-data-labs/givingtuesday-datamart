# Giving Tuesday Datamart

# Specific Asks for Giving Tuesday Team
### High Priority

* Parsed Attachments for the 990PF current grants and future grants tables when stated as attachment
  * We'd "settle" for a pointer to the raw XMLs or the files themselves
* Understanding of cadence for publishing updates.
  * Ideally at least quarterly
  * Understanding of the formats for the names so we can automate scripts

### Medium Priority
* Adding the raw XML column to the Basic Fields tables
* LinkedIn URLs for non-profits, private foundations, people (if you have through open corporates)
* Categorization for the PFs (DAF / community foundation / government / corporate / family, etc--can provide full Candid category if helpful)
* Processed clean files where names have been normalized, zip-codes are cleaned, etc

### Lower Priority / Things We will Do But would be helpful
* A canoncial representation of the non-profits, private foundations, and people that can be connected back to the original data that is done by year.
* Matched Grants to Recipients (we've done it but would love some help with test sets for measuring regression)
* All of this hosted in a database with access available (or a way to create a copy of it)
  * I think we'd actually prefer _not_ to have _only_ HTTPS API only because so much will be bulk. (happy to hear thoughts too)


# Our Notes / Current Work so Far

# Ingestion

## Write Raw Data from Datamart to SQL
Creates Tables and loads data into them scripts/create_tables.py

### Does Well
* Streams the downloads from the URL to disk so that it doesn't need to load the entire file into memory
* Writes the data to the database in batches so that it doesn't need to load the entire file into memory
### Areas for Improvement
* Naively creates the table schema by reading the first row of the file
* Does not have any typing on the columns
* Does not create proper indexes, primary keys, or foreign keys
* Column Names are straight from the CSV but attempted to be santized to be valid SQL column names
    * Not necessarily understandable
* Ideally don't need the exact URL but rather the "table" type and have a way to get the exact URL for the latest version of the table
* Doesn't track the table name to source datamart CSV so unclear which version of the table is being used
* Need to add the board members

## Getting Text per Non-Profit
Given a non-profit, get the text from the following sources:
* Programs
* Mission Statements
* Schedule O Part III
* Basic Fields

## Joining Text to Non-Profits
[SQL File](givingtuesday_datamart/sql_queries/non_profit_text_joins.sql)
Given a non-profit, join the text from the following sources to the non-profit. This is used later to do a "full text" search on the non-profit.
* Programs
    * Attempts to get the programs for the most recent year for that non-profit * Mainly because they are same for most years
* Mission Statements
    * Attempts to get the mission statement for the most recent year for that non-profit - Mainly because they are same for most years
* Schedule O Part III
    * Had to create as a seperate table because the other schedule O table is so large
    * Doesn't do the recent year aggregation
* Basic Fields
### Does Well
* Creates a single table for each non-profit with columns for each of the potential text fields
* Tries to do some deduplication on the text fields
### Areas for Improvement
* Doesn't create the single text column
* Needs to be used in Python memory to do the text search
* Pretty lazy string concatenation of the text fields
* Ideally would be in an index in SQL or some other way to search without loading the exported file into python memory

## Matching Grants from Private Foundations to Non-Profits
[SQL File](givingtuesday_datamart/sql_queries/unique_fields_for_grants.sql)
[Python File](givingtuesday_datamart/matching_records_experiment.py)
Given a grant from a private foundation, match it to a non-profit. [Datamart Table](https://nonprofitecosystem.givingtuesday.org/datamarts/?limit=9&sort=title:asc&co-item=current-grants-679696bc141dbd17de689e82)
Two sided matching problem to match the grant recipient to a list of potential non-profits.
* First tries to limit the potential number of matches getting unique  non-profit names and addresses
* Same for unique grant recipient names and addresses
* [SQL Queries](givingtuesday_datamart/sql_queries/unique_fields_for_grants.sql)
* Uses [recordlinkage](https://recordlinkage.readthedocs.io/en/stable/) to match the grant recipient to a list of potential non-profits
* Writes the matching results to a table (has to do an intermediate join to the unique non-profit names and addresses)
* Creates a Unioned Table Across the matched grants and [grants to domestic organizations](https://nonprofitecosystem.givingtuesday.org/datamarts/?page=2&limit=9&sort=title:asc&co-item=grants-to-domestic-orgs-679696bc141dbd17de689eb0) to get a single table of grant maker, grant recipient regardless of grant maker type.

### Does Well
* Runs the matching algorithm in batches so that if process dies we can re-start from the last checkpoint
* Spot checking results seems to do really well at matching Candid results

### Areas for Improvement
* Brings all data into memory so it requires a very large instance (r7a.4xlarge)
* No tests run to optimize matching critereon
* Need to bring in the grants that are listed in attachments

## Other General Areas for Improvement
* Would be great to get metadata for each of Private Foundations
    * Some sort of classification if they are a government entity, DAF, community foundation, etc. Can share the current classes from Candid on request.
* Need to update as much as possible to ensure data freshness
* Everything is run by hand for now section by section, would be good to have a full "load from scratch" script for updates

# Using in Our Current Pipeline
We use the data in our current pipeline by [Module](https://github.com/vibrant-data-labs/vdl-tools/blob/main/vdl_tools/scrape_enrich/givingtuesday/query_prepare_givingtuesday.py#L231):

* [Searching](https://github.com/vibrant-data-labs/vdl-tools/blob/95d68e6aa570eb5181e8b5cefb0dfc78dac59dd7/vdl_tools/shared_tools/keyword_extraction/search_term_recall_calculations.py#L8) through the joined text file via a list of keywords
* Returns the EINs that match
* Queries the basic fields table for the matching records
* Reformats the result from basic_fields to transform from many rows (1 per year per non-profit) to 1 per non-profit with the funding rows as new columns
* Chooses the "main" representation for the EIN (name, address) naively by taking the first instance in the table rather than a canonical version (or even last).
    * Area for Improvement: Have a "canoncial" table for each non-profit and private foundation
* Matches the Funders so we can have a list of Funders for each non-profit
    * Need to remove the smaller funders to make more performant / more legible

### Areas for Improvement
* Search against an API instead of needing to load text into memory
* Sanitizing the names and addresses -- Currently just takes what comes from table so doesn't try to title case the names or ensure we are using the latest one
* Sanitizing / Deduping the Funders -- Currently just takes what comes from table so doesn't try to title case the names or ensure we are using the latest one


# Web Application
[Vercel Deployment Here](https://givingtuesday-datamart.vercel.app/)
We created a really generic web application to explore the non-profits and private foundations quickly and gut check funding / grants

### Does Well
* Allows for searching by name
* Lists the grants recieved from both other non-profits and private foundations
* Lists the grants given

### Areas for Improvement
* Super slow as running on non-optimized tables
* Better search Interface with more options
* Full text search for the other fields and not just name
* Better profiles to list out more sections of the 990 rather than just the contributions and grants.