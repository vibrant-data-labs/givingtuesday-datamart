"""
Read-only Python client over the ``gt_datamart`` canonical surface.

Hides the SQL layer from vdl-tools consumers so future schema changes are
bounded to this module. Every method opens its own session via
``get_session(config=datamart_config())`` — no long-lived connection state
on the client. Returns frozen dataclasses (see ``models.py``); consumers
that want a DataFrame call ``pd.DataFrame.from_records([asdict(x) ...])``
themselves (the client deliberately does not depend on pandas).
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import text
from vdl_tools.shared_tools.database_cache.database_utils import get_session
from vdl_tools.shared_tools.tools.logger import logger

from givingtuesday_datamart.client.models import (
    BasicFieldsRow,
    Grant,
    Nonprofit,
    NonprofitHit,
)
from givingtuesday_datamart.ingestion import datamart_config


GranteeOrGranter = Literal["grantee", "granter"]


class GtDatamartClient:
    def __init__(self, config: dict | None = None) -> None:
        self._config = config

    def _config_or_default(self) -> dict:
        return self._config if self._config is not None else datamart_config()

    def search_nonprofits(
        self,
        keywords: list[str],
        *,
        min_rank: float | None = None,
        limit: int | None = None,
    ) -> list[NonprofitHit]:
        """Postgres FTS over ``public.nonprofit_text.text_tsv``.

        Each keyword is bound as a separate parameter and run through
        ``plainto_tsquery('english', :kw)``; the per-keyword tsqueries are
        OR-ed together with ``||``. ``plainto_tsquery`` handles multi-word
        phrases and special characters cleanly without manual escaping.

        ``LEFT JOIN nonprofit_canonical`` because ~46K 990-EZ filers live in
        ``nonprofit_text`` but are missing from ``nonprofit_canonical``
        (they file 990-EZ, which doesn't feed basic_fields). Hits for those
        EINs come back with NULL identity columns.
        """
        cleaned = [kw.strip() for kw in keywords if kw and kw.strip()]
        if not cleaned:
            raise ValueError("search_nonprofits requires at least one non-empty keyword")

        # Build the tsquery as a chain of plainto_tsquery(:kwN) || …
        # parameterized — never interpolate user input into SQL text.
        tsquery_terms = " || ".join(
            f"plainto_tsquery('english', :kw{i})" for i in range(len(cleaned))
        )
        params: dict[str, object] = {f"kw{i}": kw for i, kw in enumerate(cleaned)}

        sql = f"""
            WITH q AS (SELECT {tsquery_terms} AS tsq)
            SELECT
                nt.ein,
                nc.name,
                nc.name_secondary,
                nc.city,
                nc.state,
                ts_rank(nt.text_tsv, q.tsq) AS rank,
                nt.unique_text
            FROM public.nonprofit_text nt
            LEFT JOIN public.nonprofit_canonical nc USING (ein)
            CROSS JOIN q
            WHERE nt.text_tsv @@ q.tsq
        """
        if min_rank is not None:
            sql += " AND ts_rank(nt.text_tsv, q.tsq) >= :min_rank"
            params["min_rank"] = min_rank
        sql += " ORDER BY rank DESC"
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit

        logger.info(
            "search_nonprofits: %d keyword(s), min_rank=%s, limit=%s",
            len(cleaned),
            min_rank,
            limit,
        )
        with get_session(config=self._config_or_default()) as session:
            rows = session.execute(text(sql), params).mappings().all()

        hits = [
            NonprofitHit(
                ein=r["ein"],
                name=r["name"],
                name_secondary=r["name_secondary"],
                city=r["city"],
                state=r["state"],
                rank=float(r["rank"]),
                unique_text=r["unique_text"],
            )
            for r in rows
        ]
        logger.info("search_nonprofits: %d hits", len(hits))
        return hits

    def get_nonprofit(self, ein: str) -> Nonprofit | None:
        sql = """
            SELECT
                nc.ein,
                nc.name,
                nc.name_secondary,
                nc.dba_1,
                nc.dba_2,
                nc.care_of,
                nc.addr_line_1,
                nc.addr_line_2,
                nc.city,
                nc.state,
                nc.zip,
                nc.addr_country,
                nc.website,
                nc.formation_year,
                nc.latest_taxyear,
                nc.latest_taxperend,
                nt.unique_text,
                nc.source_run_id,
                nc.source_version
            FROM public.nonprofit_canonical nc
            LEFT JOIN public.nonprofit_text nt USING (ein)
            WHERE nc.ein = :ein
        """
        with get_session(config=self._config_or_default()) as session:
            row = session.execute(text(sql), {"ein": ein}).mappings().first()
        if row is None:
            return None
        return Nonprofit(**dict(row))

    def get_basic_fields(
        self,
        eins: list[str],
        *,
        min_taxyear: int | None = None,
    ) -> list[BasicFieldsRow]:
        """Multi-year staging reads from ``public.basic_fields``.

        Staging is all-TEXT, so every numeric column is cast at query time
        via ``NULLIF(col, '')::bigint``. ``governgrants`` is COALESCEd to 0
        when computing ``total_cash_contributions_no_gov`` because empty
        string is much more common than NULL in the staging data — this
        differs from the OLD VDL DB's numeric-typed subtraction (where empty
        was already NULL and NULL - NULL = NULL).
        """
        if not eins:
            return []

        params: dict[str, object] = {"eins": list(eins)}
        sql = """
            SELECT
                filerein                                AS ein,
                filername1                              AS name,
                filername2                              AS name_secondary,
                NULLIF(taxyear, '')::int                AS taxyear,
                filerus1                                AS addr_line_1,
                filerus2                                AS addr_line_2,
                fileruscity                             AS city,
                filerusstate                            AS state,
                fileruszip                              AS zip,
                websitsiteit                            AS website,
                NULLIF(totrevcuryea, '')::bigint        AS total_revenue_current_year,
                NULLIF(totacashcont, '')::bigint        AS total_cash_contributions,
                (NULLIF(totacashcont, '')::bigint
                    - COALESCE(NULLIF(governgrants, '')::bigint, 0))
                                                        AS total_cash_contributions_no_gov
            FROM public.basic_fields
            WHERE filerein = ANY(:eins)
        """
        if min_taxyear is not None:
            sql += " AND NULLIF(taxyear, '')::int >= :min_taxyear"
            params["min_taxyear"] = min_taxyear

        with get_session(config=self._config_or_default()) as session:
            rows = session.execute(text(sql), params).mappings().all()
        return [BasicFieldsRow(**dict(r)) for r in rows]

    def get_grants(
        self,
        eins: list[str],
        *,
        role: GranteeOrGranter = "grantee",
        min_taxyear: int | None = None,
    ) -> list[Grant]:
        """Reads from ``public.unioned_grants`` (typed at table-build time).

        ``role`` chooses which side of the relationship the EIN list filters
        on. Indexes ``idx_unioned_grants_grantee_ein`` and
        ``idx_unioned_grants_granter_ein`` keep both lookups cheap.
        """
        if not eins:
            return []
        if role not in ("grantee", "granter"):
            raise ValueError(f"role must be 'grantee' or 'granter', got {role!r}")

        # Whitelisted column name — safe to interpolate.
        ein_col = "grantee_ein" if role == "grantee" else "granter_ein"

        params: dict[str, object] = {"eins": list(eins)}
        sql = f"""
            SELECT
                granter_ein,
                granter_name,
                granter_name2,
                filesha256,
                url,
                taxyear,
                taxperbegin,
                taxperend,
                grantee_ein,
                grantee_person_name,
                grantee_organization_name1,
                grantee_organization_name2,
                grantee_address1,
                grantee_address2,
                grantee_city,
                grantee_state,
                grantee_zip,
                grant_amount,
                grant_purpose,
                grant_status,
                grant_relationship
            FROM public.unioned_grants
            WHERE {ein_col} = ANY(:eins)
        """
        if min_taxyear is not None:
            sql += " AND taxyear >= :min_taxyear"
            params["min_taxyear"] = min_taxyear

        with get_session(config=self._config_or_default()) as session:
            rows = session.execute(text(sql), params).mappings().all()
        return [Grant(**dict(r)) for r in rows]
