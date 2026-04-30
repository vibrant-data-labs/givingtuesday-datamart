"""
Return-type dataclasses for the gt_datamart client.

All frozen so consumers can rely on hashability + immutability. Field-level
nullability mirrors the underlying Postgres column nullability, except where
the source-side TEXT staging is cast at query time (in which case the cast
itself can produce ``None`` for empty strings).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NonprofitHit:
    """Result of an FTS search over ``public.nonprofit_text``.

    ``unique_text`` is the full deduped text for the EIN (already collapsed
    at canonical-build time). Included on the hit so consumers can build
    EIN→text maps without a second round-trip.
    """

    ein: str
    name: str | None
    name_secondary: str | None
    city: str | None
    state: str | None
    rank: float
    unique_text: str | None


@dataclass(frozen=True)
class Nonprofit:
    """One row from ``public.nonprofit_canonical`` (DISTINCT ON winner per
    EIN, latest taxyear → taxperend → ingested_at), enriched with
    ``unique_text`` from ``public.nonprofit_text`` when present.
    """

    ein: str
    name: str | None
    name_secondary: str | None
    dba_1: str | None
    dba_2: str | None
    care_of: str | None
    addr_line_1: str | None
    addr_line_2: str | None
    city: str | None
    state: str | None
    zip: str | None
    addr_country: str | None
    website: str | None
    formation_year: str | None
    latest_taxyear: str | None
    latest_taxperend: str | None
    unique_text: str | None
    source_run_id: str | None
    source_version: str | None


@dataclass(frozen=True)
class BasicFieldsRow:
    """One row of multi-year IRS 990 basic fields for an EIN.

    Sourced from the all-TEXT ``public.basic_fields`` staging table; numeric
    columns are cast at query time via ``NULLIF(col, '')::int|::bigint``.
    ``total_cash_contributions_no_gov`` coalesces ``governgrants`` to 0
    (TEXT staging often carries empty string instead of NULL).
    """

    ein: str
    name: str | None
    name_secondary: str | None
    taxyear: int | None
    addr_line_1: str | None
    addr_line_2: str | None
    city: str | None
    state: str | None
    zip: str | None
    website: str | None
    total_revenue_current_year: int | None
    total_cash_contributions: int | None
    total_cash_contributions_no_gov: int | None


@dataclass(frozen=True)
class Grant:
    """One row from ``public.unioned_grants`` (PF Schedule I + 990 Schedule I,
    matched and post-processed; columns are typed at table-build time).
    """

    granter_ein: str | None
    granter_name: str | None
    granter_name2: str | None
    filesha256: str | None
    url: str | None
    taxyear: int | None
    taxperbegin: datetime | None
    taxperend: datetime | None
    grantee_ein: str | None
    grantee_person_name: str | None
    grantee_organization_name1: str | None
    grantee_organization_name2: str | None
    grantee_address1: str | None
    grantee_address2: str | None
    grantee_city: str | None
    grantee_state: str | None
    grantee_zip: str | None
    grant_amount: int | None
    grant_purpose: str | None
    grant_status: str | None
    grant_relationship: str | None
