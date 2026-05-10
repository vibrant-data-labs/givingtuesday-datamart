"""
Read-only Python client over the ``gt_datamart`` canonical surface.

Hides the SQL layer from consumers so future schema changes are bounded
to this module. Owns its own SQLAlchemy engine and session factory; this
sub-package is shippable as a standalone, dependency-light library.

Returns frozen dataclasses (see ``models.py``); consumers that want a
DataFrame call ``pd.DataFrame.from_records([asdict(x) ...])`` themselves
(the client deliberately does not depend on pandas).
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator, Literal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL
from sqlalchemy.orm import Session, sessionmaker

from givingtuesday_datamart.client.models import (
    BasicFieldsRow,
    Grant,
    GrantSummary,
    Nonprofit,
    NonprofitHit,
)


logger = logging.getLogger(__name__)

GranteeOrGranter = Literal["grantee", "granter"]
SearchMode = Literal["stemmed", "exact"]

DEFAULT_DATABASE = "gt_datamart"
DEFAULT_PORT = 5432

# Env var names checked when no explicit connection components are
# passed to the constructor. Namespaced under ``GT_DATAMART_`` so the
# client doesn't collide with any other postgres config in the env.
ENV_HOST = "GT_DATAMART_PG_HOST"
ENV_PORT = "GT_DATAMART_PG_PORT"
ENV_USER = "GT_DATAMART_PG_USER"
ENV_PASSWORD = "GT_DATAMART_PG_PASSWORD"
ENV_DATABASE = "GT_DATAMART_PG_DATABASE"


def _engine_from_components(
    host: str | None,
    port: int | None,
    user: str | None,
    password: str | None,
    database: str | None,
) -> Engine:
    """Build a psycopg2 engine, falling back to GT_DATAMART_PG_* env vars."""
    host = host or os.environ.get(ENV_HOST)
    port = port or (int(os.environ[ENV_PORT]) if os.environ.get(ENV_PORT) else DEFAULT_PORT)
    user = user or os.environ.get(ENV_USER)
    password = password or os.environ.get(ENV_PASSWORD)
    database = database or os.environ.get(ENV_DATABASE) or DEFAULT_DATABASE

    missing = [
        name
        for name, value in (("host", host), ("user", user), ("password", password))
        if not value
    ]
    if missing:
        raise RuntimeError(
            "GtDatamartClient missing connection components: "
            f"{missing}. Pass them to the constructor or set "
            f"{ENV_HOST}/{ENV_USER}/{ENV_PASSWORD} (and optionally "
            f"{ENV_PORT}, {ENV_DATABASE})."
        )

    url = URL.create(
        "postgresql",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )
    return create_engine(url, future=True)


class GtDatamartClient:
    """Read-only client over gt_datamart.

    Connection inputs (in order of precedence):

    1. ``engine`` — a pre-built SQLAlchemy ``Engine``. Useful for tests,
       custom pooling, or sharing one engine across multiple clients.
    2. ``url`` — a SQLAlchemy URL string.
    3. ``host``/``port``/``user``/``password``/``database`` — components.
    4. ``GT_DATAMART_PG_*`` environment variables (with sensible defaults
       for ``port`` and ``database``).

    The client does not commit; every method opens a short-lived session
    against the persistent engine, runs a single SELECT, and closes it.
    """

    def __init__(
        self,
        *,
        engine: Engine | None = None,
        url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ) -> None:
        if engine is not None:
            self._engine = engine
        elif url is not None:
            self._engine = create_engine(url, future=True)
        else:
            self._engine = _engine_from_components(host, port, user, password, database)
        self._sessionmaker = sessionmaker(bind=self._engine, expire_on_commit=False)

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._sessionmaker()
        try:
            yield session
        finally:
            session.close()

    def search_nonprofits(
        self,
        keywords: list[str],
        *,
        search_mode: SearchMode = "stemmed",
        return_text: bool = False,
        min_rank: float | None = None,
        limit: int | None = None,
    ) -> list[NonprofitHit]:
        """Postgres FTS over ``public.nonprofit_text``.

        ``search_mode``:

        * ``"stemmed"`` (default) — queries ``text_tsv_compact`` with the
          ``english`` config via ``plainto_tsquery``. Snowball stemming +
          stopword removal, tokens AND-ed with no positional constraint.
          ``"tutoring"`` matches ``tutor``, ``tutored``, ``tutors``;
          ``"climate change"`` matches docs containing both stems anywhere.
          Best for relevance-ranked discovery.
        * ``"exact"`` — queries ``text_tsv_compact_simple`` with the
          ``simple`` config via ``phraseto_tsquery``. Lowercase + tokenize
          only, no stemming, no stopwords; multi-word inputs match as a
          phrase (tokens adjacent, in order). ``"tutoring"`` matches only
          ``tutoring``; ``"needs based"`` matches the literal phrase
          ``"needs based"`` and not ``"meet your needs ... based here"``.
          Best for precise term and phrase lookups.

        Each keyword is bound as a separate parameter; per-keyword tsqueries
        are OR-ed with ``||``. Both ``plainto_tsquery`` and ``phraseto_tsquery``
        handle multi-word inputs and special characters cleanly without
        manual escaping.

        ``LEFT JOIN nonprofit_canonical`` because ~46K 990-EZ filers live in
        ``nonprofit_text`` but are missing from ``nonprofit_canonical``
        (they file 990-EZ, which doesn't feed basic_fields). Hits for those
        EINs come back with NULL identity columns.
        """
        cleaned = [kw.strip() for kw in keywords if kw and kw.strip()]
        if not cleaned:
            raise ValueError("search_nonprofits requires at least one non-empty keyword")

        # Mode picks the indexed tsvector, the tsquery config, and the
        # tsquery constructor. text_tsv_compact pairs with 'english' +
        # plainto_tsquery (token-AND, with stemming); text_tsv_compact_simple
        # pairs with 'simple' + phraseto_tsquery (adjacent-token phrase
        # match, no stemming).
        if search_mode == "stemmed":
            tsv_col = "text_tsv_compact"
            ts_config = "english"
            ts_fn = "plainto_tsquery"
        elif search_mode == "exact":
            tsv_col = "text_tsv_compact_simple"
            ts_config = "simple"
            ts_fn = "phraseto_tsquery"
        else:
            raise ValueError(
                f"search_mode must be 'stemmed' or 'exact', got {search_mode!r}"
            )

        # Parameterize every keyword — never interpolate user input. The
        # config name and tsquery function are whitelisted above, so it's
        # safe to embed them.
        tsquery_terms = " || ".join(
            f"{ts_fn}('{ts_config}', :kw{i})" for i in range(len(cleaned))
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
                ts_rank(nt.{tsv_col}, q.tsq) AS rank,
                {"nt.unique_text_compact" if return_text else "NULL"} AS unique_text
            FROM public.nonprofit_text nt
            LEFT JOIN public.nonprofit_canonical nc USING (ein)
            CROSS JOIN q
            WHERE nt.{tsv_col} @@ q.tsq
        """
        if min_rank is not None:
            sql += f" AND ts_rank(nt.{tsv_col}, q.tsq) >= :min_rank"
            params["min_rank"] = min_rank
        sql += " ORDER BY rank DESC"
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit

        logger.info(
            "search_nonprofits: %d keyword(s), mode=%s, min_rank=%s, limit=%s",
            len(cleaned),
            search_mode,
            min_rank,
            limit,
        )
        with self._session() as session:
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
                nt.unique_text_compact AS unique_text,
                nc.source_run_id,
                nc.source_version
            FROM public.nonprofit_canonical nc
            LEFT JOIN public.nonprofit_text nt USING (ein)
            WHERE nc.ein = :ein
        """
        with self._session() as session:
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

        with self._session() as session:
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
        on. Composite indexes ``idx_unioned_grants_grantee_ein_taxyear`` and
        ``idx_unioned_grants_granter_ein_taxyear`` keep both lookups cheap
        and make ``min_taxyear`` an Index Cond rather than a post-index
        Filter — important when the EIN list is large (the filter would
        otherwise force a heap fetch for every grant in every year).

        ``granter_name`` and ``granter_name2`` are resolved at query time
        from the canonical identity tables (``nonprofit_canonical`` for 990
        filers, ``funder_canonical`` for 990-PF filers) so consumers see one
        stable spelling per granter EIN regardless of which year's filing
        produced the row. ``filesha256 IS NOT NULL`` discriminates PF rows
        (always set in the union) from 990 rows (always NULL). The COALESCE
        falls back to the other canonical, then to the per-year value on
        ``unioned_grants``, so we never blank out a name that was present.
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
                ug.granter_ein,
                COALESCE(
                    CASE WHEN ug.filesha256 IS NOT NULL THEN fc.name ELSE nc.name END,
                    CASE WHEN ug.filesha256 IS NOT NULL THEN nc.name ELSE fc.name END,
                    ug.granter_name
                ) AS granter_name,
                COALESCE(
                    CASE WHEN ug.filesha256 IS NOT NULL THEN fc.name_secondary ELSE nc.name_secondary END,
                    CASE WHEN ug.filesha256 IS NOT NULL THEN nc.name_secondary ELSE fc.name_secondary END,
                    ug.granter_name2
                ) AS granter_name2,
                ug.filesha256,
                ug.url,
                ug.taxyear,
                ug.taxperbegin,
                ug.taxperend,
                ug.grantee_ein,
                ug.grantee_person_name,
                ug.grantee_organization_name1,
                ug.grantee_organization_name2,
                ug.grantee_address1,
                ug.grantee_address2,
                ug.grantee_city,
                ug.grantee_state,
                ug.grantee_zip,
                ug.grant_amount,
                ug.grant_purpose,
                ug.grant_status,
                ug.grant_relationship
            FROM public.unioned_grants ug
            LEFT JOIN public.nonprofit_canonical nc ON nc.ein = ug.granter_ein
            LEFT JOIN public.funder_canonical fc ON fc.ein = ug.granter_ein
            WHERE ug.{ein_col} = ANY(:eins)
        """
        if min_taxyear is not None:
            sql += " AND ug.taxyear >= :min_taxyear"
            params["min_taxyear"] = min_taxyear

        with self._session() as session:
            rows = session.execute(text(sql), params).mappings().all()
        return [Grant(**dict(r)) for r in rows]

    def get_grant_summaries(
        self,
        eins: list[str],
        *,
        role: GranteeOrGranter = "grantee",
        min_taxyear: int | None = None,
    ) -> list[GrantSummary]:
        """Per-(EIN, taxyear) aggregate over ``public.unioned_grants``.

        Same row-set as ``get_grants`` for the same args, but rolled up to one
        row per ``(ein, taxyear)`` with ``SUM(grant_amount)`` and the deduped
        granter EIN / name arrays. The granter-name COALESCE matches
        ``get_grants`` exactly so consumers see the same canonical names —
        this is the "I just need yearly totals + funder rollups" path that
        avoids transferring 4M+ raw grant rows for queries that immediately
        group them.

        ``role`` whitelisted to the same composite-index columns as
        ``get_grants`` (``grantee_ein`` / ``granter_ein``); ``min_taxyear``
        stays an Index Cond on the composite index.
        """
        if not eins:
            return []
        if role not in ("grantee", "granter"):
            raise ValueError(f"role must be 'grantee' or 'granter', got {role!r}")

        ein_col = "grantee_ein" if role == "grantee" else "granter_ein"

        params: dict[str, object] = {"eins": list(eins)}
        # Resolve the canonical granter name once in the inner SELECT, then
        # aggregate over it in the outer query. ``ARRAY_AGG ... FILTER (WHERE
        # granter_name IS NOT NULL)`` reads cleanly; doing it inline would
        # mean duplicating the three-way COALESCE in both the aggregate and
        # the filter clause.
        #
        # ``COALESCE(SUM(...), 0)`` so an EIN with grants but all-NULL amounts
        # comes back as 0 rather than NULL — pandas-side groupby/mean callers
        # expect a numeric here (NULL → NaN → would silently drop the EIN
        # from any ``mean() >= threshold`` filter).
        where_taxyear = "AND ug.taxyear >= :min_taxyear" if min_taxyear is not None else ""
        if min_taxyear is not None:
            params["min_taxyear"] = min_taxyear
        sql = f"""
            WITH resolved AS (
                SELECT
                    ug.{ein_col}        AS ein,
                    ug.taxyear          AS taxyear,
                    ug.grant_amount     AS grant_amount,
                    ug.granter_ein      AS granter_ein,
                    COALESCE(
                        CASE WHEN ug.filesha256 IS NOT NULL THEN fc.name ELSE nc.name END,
                        CASE WHEN ug.filesha256 IS NOT NULL THEN nc.name ELSE fc.name END,
                        ug.granter_name
                    )                   AS granter_name
                FROM public.unioned_grants ug
                LEFT JOIN public.nonprofit_canonical nc ON nc.ein = ug.granter_ein
                LEFT JOIN public.funder_canonical    fc ON fc.ein = ug.granter_ein
                WHERE ug.{ein_col} = ANY(:eins)
                  {where_taxyear}
            )
            SELECT
                ein,
                taxyear,
                COALESCE(SUM(grant_amount), 0)::double precision    AS total_grant_amount,
                COUNT(*)                                            AS grant_count,
                ARRAY_AGG(DISTINCT granter_ein)
                    FILTER (WHERE granter_ein IS NOT NULL)          AS granter_eins,
                ARRAY_AGG(DISTINCT granter_name)
                    FILTER (WHERE granter_name IS NOT NULL)         AS granter_names
            FROM resolved
            GROUP BY ein, taxyear
        """

        logger.info(
            "get_grant_summaries: role=%s, %d EIN(s), min_taxyear=%s",
            role,
            len(eins),
            min_taxyear,
        )
        with self._session() as session:
            rows = session.execute(text(sql), params).mappings().all()
        logger.info("get_grant_summaries: %d (ein, taxyear) rows", len(rows))
        return [
            GrantSummary(
                ein=r["ein"],
                taxyear=r["taxyear"],
                total_grant_amount=(
                    float(r["total_grant_amount"]) if r["total_grant_amount"] is not None else None
                ),
                grant_count=int(r["grant_count"]),
                granter_eins=list(r["granter_eins"] or []),
                granter_names=list(r["granter_names"] or []),
            )
            for r in rows
        ]

    def find_eins_with_min_avg_contributions(
        self,
        eins: list[str],
        *,
        min_taxyear: int,
        min_avg: float,
        column: Literal["totacashcont", "totrevcuryea"] = "totacashcont",
    ) -> list[str]:
        """Return EINs whose avg yearly value of ``column`` is ``>= min_avg``.

        Pushes the avg-contributions filter into Postgres so the caller
        doesn't pull all per-year staging rows just to compute a Python
        groupby/mean. Mirrors the all-TEXT casting used by ``get_basic_fields``.

        Restricted to the two cash-flow columns the existing pipeline filters
        on; whitelisted (not interpolated user input) so it's safe to embed.
        """
        if not eins:
            return []
        if column not in ("totacashcont", "totrevcuryea"):
            raise ValueError(
                f"column must be 'totacashcont' or 'totrevcuryea', got {column!r}"
            )

        params: dict[str, object] = {
            "eins": list(eins),
            "min_taxyear": min_taxyear,
            "min_avg": min_avg,
        }
        # ``basic_fields`` can have multiple rows for the same (filerein,
        # taxyear). The legacy pipeline sums those duplicates within-year
        # before averaging across years (``groupby(['filerein','taxyear'])
        # [col].sum().groupby('filerein').mean()``); ``AVG(col)`` directly
        # over all rows would weight by row count and silently drop EINs
        # whose duplicates pulled the all-rows mean below the threshold.
        sql = f"""
            SELECT filerein AS ein
            FROM (
                SELECT filerein, taxyear,
                       SUM(NULLIF({column}, '')::bigint) AS yr_sum
                FROM public.basic_fields
                WHERE filerein = ANY(:eins)
                  AND NULLIF(taxyear, '')::int >= :min_taxyear
                GROUP BY filerein, taxyear
            ) yearly
            GROUP BY filerein
            HAVING AVG(yr_sum) >= :min_avg
        """
        logger.info(
            "find_eins_with_min_avg_contributions: %d EIN(s), column=%s, min_taxyear=%s, min_avg=%s",
            len(eins),
            column,
            min_taxyear,
            min_avg,
        )
        with self._session() as session:
            rows = session.execute(text(sql), params).mappings().all()
        result = [r["ein"] for r in rows]
        logger.info("find_eins_with_min_avg_contributions: %d EIN(s) pass", len(result))
        return result
