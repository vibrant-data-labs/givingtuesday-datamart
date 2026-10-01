"""The view consumers read: ``privategrants_current`` with the recovered
grants in place of the placeholder rows they replace.

``privategrants_current_w_recovered`` has ``privategrants_current``'s
columns under their own names, then where each row came from
(``row_source``) and, for a recovered row, its labels and its key in
``privategrants_recovered``.

**The placeholder rows are dropped.** A loaded filing's placeholder rows
carry the whole amount in ``privategrants_current``, so beside the
recovered rows they would count the dollars twice. The view leaves them
out, for the filings whose paid list is loaded under the view's policy and
for those only. Grants the filer named in the form itself stay as they are.

**Paid grants only.** The view shows the rows loaded with target ``paid``.
A future-payment list is loaded too, with target ``future``, and stays out:
its grants are approved, not paid, and ``privategrants_current`` holds none
of that kind. ``check`` holds the view to it.

**A recovered row's columns.** The name, less a place printed at its end,
is the business name; the address, less the state and zip at its end, is
address line 1; the state and the zip are in their own columns; status,
purpose and amount are as read. The columns that describe the filing (the
filer's name, the period, the url, the source version) are those of the
placeholder row it replaces, so a list appears only while the filing it
was read from is the version ``privategrants_current`` holds.

**One policy.** The view shows the rows loaded under one policy version,
written into its definition: a filing loaded under two would be counted
twice. Which policy is a decision, so only the ``view`` command changes
it: a load under another policy leaves the view as it is, and says so.

**Cost.** The pointer pattern is five regular expressions, and over every
row of ``privategrants_current`` it is a pass of minutes. The view never
makes that pass: each loaded filing's placeholder row is found through the
(filerein, taxyear) index, one lookup a filing (the LATERAL with its LIMIT
holds the planner to it), and the pattern runs on the rows of loaded
filings only. A count over the whole view costs what a count over
``privategrants_current`` costs.

**Rebuilds.** ``current_grants`` rebuilds ``privategrants_current`` with
DROP ... CASCADE, which takes this view with it. ``load`` creates it again
when it is gone (``ensure_view``); so does ``python -m
givingtuesday_datamart.placeholder_recovery view``.
"""

from __future__ import annotations

import re

from sqlalchemy import text

from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.placeholder_recovery import classifier, loader

VIEW = "privategrants_current_w_recovered"
CURRENT = "privategrants_current"
FROM_CURRENT = "privategrants_current"
FROM_RECOVERY = "placeholder_recovery"

# privategrants_current's columns, in its order.
CURRENT_COLUMNS = (
    "filerein", "filername1", "filername2", "filesha256", "sigocaffrfaco", "sigocpyamoun", "sigocpypogoc",
    "sigocpyrbnbn1", "sigocpyrbnbn2", "sigocpyrfaal1", "sigocpyrfaal2", "sigocpyrfaci", "sigocpyrfapc",
    "sigocpyrfapo", "sigocpyrfsta", "sigocpyrpnam", "sigocpyrrela", "taxperbegin", "taxperend", "taxyear", "url",
    "_source_version", "_source_url", "_ingested_at", "_ingest_run_id", "n_urls_for_year", "n_filings_for_year",
    "dedup_rule")
# What a recovered row puts in the recipient's columns. A recipient column
# not named here is NULL: the readers return no second name line, no city
# of its own, no country and no relationship.
RECIPIENT = {
    "sigocpyrbnbn1": "r.match_name",
    "sigocpyrfaal1": "r.match_address",
    "sigocpyrfapo": "r.state",
    "sigocpyrfapc": "r.zip5",
    "sigocpyrfsta": "r.recipient_status",
    "sigocpypogoc": "r.purpose",
    "sigocpyamoun": "trim_scale(r.amount)::text",
}
RECIPIENT_NULL = ("sigocaffrfaco", "sigocpyrbnbn2", "sigocpyrfaal2", "sigocpyrfaci", "sigocpyrpnam", "sigocpyrrela")
# The rest describe the filing, and come from the placeholder row replaced.
FILING = tuple(c for c in CURRENT_COLUMNS if c not in RECIPIENT and c not in RECIPIENT_NULL)
# The columns the view adds, with their type and what a recovered row holds.
ADDED = (
    ("row_source", "text", f"'{FROM_RECOVERY}'"),
    ("page_kind", "text", "r.page_kind"),
    ("page_verdict", "text", "r.page_verdict"),
    ("filer_marked_individual", "boolean", "r.filer_marked_individual"),
    ("state_source", "text", "r.state_source"),
    ("recovered_object_id", "text", "r.object_id"),
    ("recovered_policy_version", "text", "r.policy_version"),
    ("recovered_page", "integer", "r.page"),
    ("recovered_row_ordinal", "integer", "r.row_ordinal"),
    # Added on 2026-09-29. Last, since a view is replaced in place and takes
    # new columns at its end only.
    ("placeholder_exceeds_declared", "boolean", "r.placeholder_exceeds_declared"),
)
_VERSION = re.compile(r"[A-Za-z0-9_.-]+")
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
_SHOWN = re.compile(r"policy_version = '([^']+)'")


def _recovered_column(column: str) -> str:
    if column in RECIPIENT:
        return f"{RECIPIENT[column]} AS {column}"
    if column in RECIPIENT_NULL:
        return f"NULL::text AS {column}"
    return f"f.{column}"


def check_identifier(identifier: str) -> str:
    """``identifier`` when it can be written into SQL as a relation's name:
    lower case letters, digits and '_'. Raises ``ValueError`` otherwise.
    The matcher holds the names of the relations a run writes to it too."""
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"{identifier!r} cannot be written into SQL: lower case letters, digits and '_' only")
    return identifier


def view_sql(policy_version: str, view: str = VIEW, recovered: str = loader.TABLE) -> str:
    """The view's definition for the rows loaded under ``policy_version``.
    ``view`` and ``recovered`` are the view's name and the table of
    recovered grants it reads; a subset of the matcher's work gives its own
    (``matching_subset``), so that it reads a copy of the rows and holds
    nothing on the table a load writes."""
    if not _VERSION.fullmatch(policy_version):
        raise ValueError(f"{policy_version!r} cannot be written into the view: letters, digits, '_', '.', '-' only")
    check_identifier(view)
    check_identifier(recovered)
    pointer = classifier.pointer_sql(classifier.name_sql("g"))
    current = ", ".join(f"g.{column}" for column in CURRENT_COLUMNS)
    current_added = ", ".join(f"'{FROM_CURRENT}'::text AS row_source" if name == "row_source"
                              else f"NULL::{kind} AS {name}" for name, kind, _ in ADDED)
    recovered_columns = ", ".join(_recovered_column(column) for column in CURRENT_COLUMNS)
    recovered_added = ", ".join(f"{value}::{kind} AS {name}" for name, kind, value in ADDED)
    return f"""
CREATE OR REPLACE VIEW public.{view} AS
WITH loaded AS (
    -- the filings whose paid list is loaded under the policy
    SELECT DISTINCT object_id, filerein, taxyear::text AS taxyear
    FROM public.{recovered}
    WHERE policy_version = '{policy_version}' AND target = '{loader.PAID}'
),
filing AS (
    -- one placeholder row of each, as {CURRENT} holds it today:
    -- the filing's own columns, and the proof that the version read is the
    -- version loaded
    SELECT l.object_id, {', '.join(f'p.{column}' for column in FILING)}
    FROM loaded l
    CROSS JOIN LATERAL (
        SELECT {', '.join(f'g.{column}' for column in FILING)}
        FROM public.{CURRENT} g
        WHERE g.filerein = l.filerein AND g.taxyear = l.taxyear AND position(l.object_id in g.url) > 0
          AND {pointer}
        LIMIT 1
    ) p
)
SELECT {current}, {current_added}
FROM public.{CURRENT} g
LEFT JOIN filing f ON f.filerein = g.filerein AND f.taxyear = g.taxyear AND f.url = g.url
WHERE CASE WHEN f.object_id IS NULL THEN true ELSE NOT {pointer} END
UNION ALL
SELECT {recovered_columns}, {recovered_added}
FROM public.{recovered} r
JOIN filing f ON f.object_id = r.object_id
WHERE r.policy_version = '{policy_version}' AND r.target = '{loader.PAID}'
"""


def missing_columns(session) -> list[str]:
    """The columns the view needs that ``privategrants_current`` lacks."""
    found = session.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = :table"),
        {"table": CURRENT})
    held = {name for name, in found}
    extra = sorted(held - set(CURRENT_COLUMNS))
    if extra:
        logger.warning("%s has columns the view does not show: %s", CURRENT, ", ".join(extra))
    return [column for column in CURRENT_COLUMNS if column not in held]


def shown_policy(session) -> str | None:
    """The policy version the view shows, read from its definition; None
    when there is no view."""
    definition = session.execute(text(f"SELECT pg_get_viewdef(to_regclass('public.{VIEW}'))")).scalar()
    found = _SHOWN.search(definition or "")
    return found.group(1) if found else None


def ensure_view(session, policy_version: str) -> bool:
    """The view a load leaves behind: created when there is none, brought up
    to date when it shows ``policy_version``, left alone when it shows
    another policy. Returns whether the view shows ``policy_version``."""
    shown = shown_policy(session)
    if shown is not None and shown != policy_version:
        logger.warning("view %s shows policy %s and is left as it is; `view --policy %s` points it at these rows",
                       VIEW, shown, policy_version)
        return False
    create_view(session, policy_version)
    return True


def create_view(session, policy_version: str) -> None:
    """Create the view, or replace it, for the rows loaded under
    ``policy_version``. Raises ``LookupError`` when ``privategrants_current``
    lacks a column the view shows."""
    missing = missing_columns(session)
    if missing:
        raise LookupError(f"{CURRENT} lacks {missing}; the view cannot be built on it")
    session.execute(text(view_sql(policy_version)))
    session.commit()
    logger.info("view %s: created for policy %s", VIEW, policy_version)
