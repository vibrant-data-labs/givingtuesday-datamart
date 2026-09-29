"""The classifier's pointer pattern, as Postgres regular expressions.

A pointer row is a grant row whose recipient name points at an attachment
("SEE ATTACHED", "STATEMENT 25", "ATCH 4") instead of naming a grantee. The
pattern is version ``v2`` of ``data/exploratory/placeholder_population_by_year.sql``,
where it was measured; a test holds the two texts equal. It is the SQL form
of ``attachment_grants.is_pointer``: the two agreed on every one of 13,589
filer-years when checked on 2026-09-28. A row that withholds the list
("available upon request") is not a pointer, since nothing is attached.

The work list's query and the view both build their SQL from here, so the
rows the view drops are the rows the work list counted.
"""

from __future__ import annotations

CLASSIFIER_VERSION = "v2"

WITHHELD = r"upon request|on request|on file|kept on file|hipaa|hippa|not required|privacy|confidential"
POINTER = (
    r"(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|^\s*see\s*$",
    r"\y(see|refer)\w*\y.{0,40}\y(attach|schedul|statement|stmt|list|exhibit|detail|footnote|supplement)",
    r"^\W*(please\s+)?(see\s+)?(attached|attachment|atch|sched|schedule|statement|stmt|exhibit|listing|list"
    r"|details?|supplement(al)?)\y[\w\s#&().,/-]{0,30}$",
    r"\y(attached|attachment|atch)\y",
    r"^\W*(total|grants?|contributions?|donations?|grants?\s+paid|grants?\s+approved\s+for\s+future\s+payment)\W*$",
)
# Every alternative of POINTER needs one of these words, so a name without
# any of them is not a pointer. One cheap expression on the raw columns,
# which Postgres runs in its parallel scan, leaves 1% of the rows for the
# five above: the pass over 16.6 million rows takes about a minute in place
# of twelve.
PREFILTER = ("see|refer|attach|atch|sched|statement|stmt|exhibit|list|detail|supplement"
             "|total|grant|contribution|donation")

NUMERIC = r"^-?[0-9]+(\.[0-9]+)?$"
TAX_YEAR = r"^[0-9]{4}$"


def raw_name_sql(alias: str) -> str:
    """The name columns of a ``privategrants_current`` row, joined as read."""
    return f"concat_ws(' ', {alias}.sigocpyrpnam, {alias}.sigocpyrbnbn1, {alias}.sigocpyrbnbn2)"


def name_sql(alias: str) -> str:
    """The recipient name of a row: person and business names, one space between words."""
    return rf"trim(regexp_replace({raw_name_sql(alias)}, '\s+', ' ', 'g'))"


def prefilter_sql(alias: str) -> str:
    """True for every row that can be a pointer, and cheap."""
    return f"{raw_name_sql(alias)} ~* '{PREFILTER}'"


def pointer_sql(name: str) -> str:
    """True when ``name``, an SQL expression, is a pointer."""
    matches = " OR ".join(f"{name} ~* '{pattern}'" for pattern in POINTER)
    return f"({name} !~* '{WITHHELD}' AND ({matches}))"


def amount_sql(column: str) -> str:
    """A text amount as a number, NULL when it is not one."""
    return f"CASE WHEN {column} ~ '{NUMERIC}' THEN {column}::numeric END"
