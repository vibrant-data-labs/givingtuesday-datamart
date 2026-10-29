"""The classifier's pointer pattern, as Postgres regular expressions.

A pointer row is a grant row whose recipient name points at an attachment
("SEE ATTACHED", "STATEMENT 25", "ATCH 4") instead of naming a grantee. The
pattern is that of ``data/placeholder_recovery/placeholder_population_by_year.sql``,
where version ``v2`` was measured; a test holds the two texts equal, and the
SQL carries the one change since (``SEE_ALONE``, v3). It is the SQL form
of ``attachment_grants.is_pointer``: the two agreed on every one of 13,589
filer-years when checked on 2026-09-28. A row that withholds the list
("available upon request") is not a pointer, since nothing is attached.

The work list's query and the view both build their SQL from here, so the
rows the view drops are the rows the work list counted.

The same pattern reads the future-payment table, whose columns say
``sigocaff`` where the paid table says ``sigocpy`` and which has no column
for a person's name: the functions that build a name take the columns to
read, ``PAID_NAMES`` unless told otherwise.
"""

from __future__ import annotations

from typing import Sequence

# v3 (2026-10-29): "see" alone between punctuation ("SEE.", "(SEE)") is a
# pointer, as it is a placeholder to the matcher (``SEE_ALONE``); under v2's
# bare "see" such a row was neither recovered nor matched.
CLASSIFIER_VERSION = "v3"

# A name that is the word "see" with nothing else but punctuation. The
# matcher's placeholder pattern (``grant_matching.PLACEHOLDER_REGEX``) takes
# this arm from here, so a row the view leaves out of matching is a row the
# work list counts and the recovery reads.
SEE_ALONE = r"^[^a-z0-9]*see[^a-z0-9]*$"

WITHHELD = r"upon request|on request|on file|kept on file|hipaa|hippa|not required|privacy|confidential"
POINTER = (
    r"(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|" + SEE_ALONE,
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

# The columns that hold a recipient's name, and the one that holds the amount.
PAID_NAMES = ("sigocpyrpnam", "sigocpyrbnbn1", "sigocpyrbnbn2")       # privategrants_current
FUTURE_NAMES = ("sigocaffrbnb1", "sigocaffrbnb2")                     # privategrants_future_current
PAID_AMOUNT = "sigocpyamoun"
FUTURE_AMOUNT = "sigocaffamou"


def raw_name_sql(alias: str, columns: Sequence[str] = PAID_NAMES) -> str:
    """The name columns of a grant row, joined as read."""
    return f"concat_ws(' ', {', '.join(f'{alias}.{column}' for column in columns)})"


def name_sql(alias: str, columns: Sequence[str] = PAID_NAMES) -> str:
    """The recipient name of a row: person and business names, one space between words."""
    return rf"trim(regexp_replace({raw_name_sql(alias, columns)}, '\s+', ' ', 'g'))"


def prefilter_sql(alias: str, columns: Sequence[str] = PAID_NAMES) -> str:
    """True for every row that can be a pointer, and cheap."""
    return f"{raw_name_sql(alias, columns)} ~* '{PREFILTER}'"


def pointer_sql(name: str) -> str:
    """True when ``name``, an SQL expression, is a pointer."""
    matches = " OR ".join(f"{name} ~* '{pattern}'" for pattern in POINTER)
    return f"({name} !~* '{WITHHELD}' AND ({matches}))"


def amount_sql(column: str) -> str:
    """A text amount as a number, NULL when it is not one."""
    return f"CASE WHEN {column} ~ '{NUMERIC}' THEN {column}::numeric END"
