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
import re
from collections.abc import Sequence
from contextlib import contextmanager
from typing import Iterator, Literal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL
from sqlalchemy.orm import Session, sessionmaker

from givingtuesday_datamart.client.models import (
    BasicFieldsRow,
    CanonicalIdentity,
    Grant,
    GrantSummary,
    IdentityHit,
    IdentityQuery,
    Nonprofit,
    NonprofitHit,
)


logger = logging.getLogger(__name__)

GranteeOrGranter = Literal["grantee", "granter"]
SearchMode = Literal["stemmed", "exact"]
IdentityOrgType = Literal["all", "nonprofit", "funder"]

# Ranking tiers for ``search_identity``, ported from the frontend's
# ``searchOrgs`` query (frontend/src/lib/queries/search.ts). Tiers are
# separated by wide ranges so a tier always beats every lower tier. The
# frontend's fifth tier — narrative FTS at raw ``ts_rank_cd`` — is
# intentionally not ported; see ``search_identity`` for why.
IDENTITY_RANK_EIN = 2_000_000.0
IDENTITY_RANK_URL_EXACT = 1_500_000.0
IDENTITY_RANK_URL_PREFIX = 1_200_000.0
IDENTITY_RANK_NAME = 1_000_000.0

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


def _normalize_identity_signals(
    name: str | None,
    url: str | None,
    ein: str | None,
    *,
    strict: bool = True,
) -> tuple[str | None, str, str]:
    """Normalize the three identity signals to ``(name, domain, ein_digits)``.

    Shared by ``search_identity`` and ``search_identity_bulk`` so the two
    paths can't drift on what counts as a usable signal. Blank/whitespace
    names, unparseable EINs, and non-domain URLs all collapse to "absent".

    With ``strict=True`` a malformed ``ein`` or ``url`` raises ``ValueError``
    rather than silently degrading the search (a name-only fallback on a
    typo'd EIN is a wrong-match risk). With ``strict=False`` the bad signal
    is dropped and the caller decides what to do with a query that ends up
    with no signals at all.
    """
    name = name.strip() if name is not None and name.strip() else None

    ein_digits = ""
    if ein is not None:
        digits = re.sub(r"\D", "", ein)
        if len(digits) == 9:
            ein_digits = digits
        elif strict:
            raise ValueError(f"ein must contain exactly 9 digits, got {ein!r}")

    domain = ""
    if url is not None:
        domain = _normalize_domain(url)
        if not domain and strict:
            raise ValueError(f"url does not normalize to a searchable domain: {url!r}")

    return name, domain, ein_digits


def _identity_sort_key(hit: IdentityHit) -> tuple:
    """Frontend's cross-arm ordering: rank DESC, latest_taxyear DESC nulls
    last, name ASC nulls last, ein ASC."""
    return (
        -hit.rank,
        -(hit.latest_taxyear if hit.latest_taxyear is not None else -1),
        hit.name is None,
        hit.name or "",
        hit.ein,
    )


def _normalize_domain(raw: str) -> str:
    """Port of the frontend's ``normalizeDomainForQuery`` (validation.ts).

    Must stay consistent with the SQL expression behind the generated
    ``nonprofit_canonical.domain`` column (canonical/build.py): lowercase,
    scheme + ``www.`` stripped, path/query/fragment dropped. Returns ``""``
    when the input isn't domain-shaped (contains whitespace after stripping,
    or is shorter than 3 chars).
    """
    stripped = raw.strip().lower()
    stripped = re.sub(r"^(https?:)?//", "", stripped)
    stripped = re.sub(r"^www\.", "", stripped)
    stripped = re.sub(r"[/?#].*$", "", stripped)
    if re.search(r"\s", stripped) or len(stripped) < 3:
        return ""
    return stripped


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
        min_avg_contributions: float | None = None,
        min_avg_grants: float | None = None,
        min_num_grants: int | None = None,
        min_taxyear: int | None = None,
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

        Eligibility filters (all optional, all server-side via CTEs):

        * ``min_avg_contributions`` — keep only EINs whose mean yearly
          ``totacashcont`` since ``min_taxyear`` is at least the threshold.
          Sums per (ein, taxyear) first, then averages across years.
        * ``min_avg_grants`` — same per-year-sum-then-mean shape over
          grants received from ``unioned_grants``.
        * ``min_num_grants`` — total grant-row count across the
          ``min_taxyear`` window must be at least this many.

        Any of those filters requires ``min_taxyear`` to be set; raises
        ``ValueError`` otherwise. ``None`` and ``0`` are both treated as
        "filter off"; pass a positive value to apply the threshold.
        ``min_taxyear`` is only required when at least one threshold is
        positive.
        """
        cleaned = [kw.strip() for kw in keywords if kw and kw.strip()]
        if not cleaned:
            raise ValueError("search_nonprofits requires at least one non-empty keyword")

        # Treat both ``None`` and ``0`` as "filter off". ``0`` reads as
        # "no minimum" to callers, but gating on ``is not None`` alone
        # would still attach the eligibility CTE + INNER JOIN, which
        # silently restricts results to EINs present in basic_fields /
        # unioned_grants for the window — the opposite of the natural
        # reading. Only a positive threshold actually filters.
        needs_basic = min_avg_contributions is not None and min_avg_contributions > 0
        needs_grants = (
            (min_avg_grants is not None and min_avg_grants > 0)
            or (min_num_grants is not None and min_num_grants > 0)
        )
        if (needs_basic or needs_grants) and min_taxyear is None:
            raise ValueError(
                "min_taxyear is required when any eligibility filter "
                "(min_avg_contributions / min_avg_grants / min_num_grants) is set"
            )

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

        fts_where = f"nt.{tsv_col} @@ q.tsq"
        if min_rank is not None:
            fts_where += f" AND ts_rank(nt.{tsv_col}, q.tsq) >= :min_rank"
            params["min_rank"] = min_rank

        # Hoist the FTS match into its own CTE so the eligibility CTEs
        # below have a stable name to join against.
        ctes: list[str] = [
            f"q AS (SELECT {tsquery_terms} AS tsq)",
            f"""fts AS (
                SELECT
                    nt.ein,
                    ts_rank(nt.{tsv_col}, q.tsq) AS rank,
                    {"nt.unique_text_compact" if return_text else "NULL"} AS unique_text
                FROM public.nonprofit_text nt
                CROSS JOIN q
                WHERE {fts_where}
            )""",
        ]
        joins: list[str] = []

        if needs_basic:
            # ``basic_fields`` can have multiple rows per (filerein, taxyear),
            # so sum within each year first and then take the mean across
            # years — averaging the raw rows would weight by row count.
            # The staging columns are all-TEXT; cast at query time.
            ctes.append(
                """contrib_eligible AS (
                    SELECT filerein AS ein
                    FROM (
                        SELECT filerein, taxyear,
                               SUM(NULLIF(totacashcont, '')::bigint) AS yr_sum
                        FROM public.basic_fields
                        WHERE NULLIF(taxyear, '')::int >= :min_taxyear
                        GROUP BY filerein, taxyear
                    ) yearly
                    GROUP BY filerein
                    HAVING AVG(yr_sum) >= :min_avg_contributions
                )"""
            )
            joins.append("JOIN contrib_eligible USING (ein)")
            params["min_avg_contributions"] = min_avg_contributions

        if needs_grants:
            # Aggregate per (grantee_ein, taxyear) so min_avg_grants is the
            # mean of per-year grant totals (not the mean of raw grant rows),
            # and min_num_grants is the total grant-row count across the
            # window.
            having_clauses: list[str] = []
            if min_num_grants is not None and min_num_grants > 0:
                having_clauses.append("SUM(yr_count) >= :min_num_grants")
                params["min_num_grants"] = min_num_grants
            if min_avg_grants is not None and min_avg_grants > 0:
                having_clauses.append("AVG(yr_sum) >= :min_avg_grants")
                params["min_avg_grants"] = min_avg_grants
            having_sql = " AND ".join(having_clauses)
            # Some unioned_grants rows have NULL grant_amount (e.g. amount
            # not parsed off the 990). COALESCE the inner sum to 0 so those
            # years count as $0 grant flow instead of letting NULL propagate
            # through the outer AVG and drop the EIN from grant_eligible.
            ctes.append(
                f"""grant_eligible AS (
                    SELECT grantee_ein AS ein
                    FROM (
                        SELECT grantee_ein, taxyear,
                               COALESCE(SUM(grant_amount), 0) AS yr_sum,
                               COUNT(*) AS yr_count
                        FROM public.unioned_grants
                        WHERE taxyear >= :min_taxyear
                        GROUP BY grantee_ein, taxyear
                    ) yearly
                    GROUP BY grantee_ein
                    HAVING {having_sql}
                )"""
            )
            joins.append("JOIN grant_eligible USING (ein)")

        if needs_basic or needs_grants:
            params["min_taxyear"] = min_taxyear

        joins_sql = ("\n            " + "\n            ".join(joins)) if joins else ""
        sql = f"""
            WITH {",".join(ctes)}
            SELECT
                fts.ein,
                nc.name,
                nc.name_secondary,
                nc.city,
                nc.state,
                fts.rank,
                fts.unique_text
            FROM fts
            LEFT JOIN public.nonprofit_canonical nc USING (ein){joins_sql}
            ORDER BY fts.rank DESC
        """
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit

        logger.info(
            "search_nonprofits: %d keyword(s), mode=%s, min_rank=%s, limit=%s, "
            "min_avg_contributions=%s, min_avg_grants=%s, min_num_grants=%s, min_taxyear=%s",
            len(cleaned),
            search_mode,
            min_rank,
            limit,
            min_avg_contributions,
            min_avg_grants,
            min_num_grants,
            min_taxyear,
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

    def get_nonprofits_by_ein(
        self,
        eins: list[str],
        *,
        return_text: bool = True,
    ) -> list[NonprofitHit]:
        """Bulk lookup of ``NonprofitHit`` rows by EIN — no FTS, no eligibility.

        Sibling to ``search_nonprofits``: same SELECT shape (and same hit
        dataclass), but the row set comes from the supplied EIN list rather
        than a tsquery match, and none of the eligibility filters apply.
        Use this to inject a hand-curated EIN set into a result alongside
        keyword-search hits.

        ``rank`` is hard-coded to ``0.0`` (forced rows have no FTS score;
        ``NonprofitHit.rank`` is a required ``float``). EINs absent from
        ``nonprofit_canonical`` still come back with NULL identity columns,
        matching ``search_nonprofits`` semantics. When ``return_text`` is
        ``False``, ``unique_text`` is NULL on every returned row.
        """
        if not eins:
            return []

        text_select = "nt.unique_text_compact" if return_text else "NULL"
        text_join = (
            "LEFT JOIN public.nonprofit_text nt USING (ein)" if return_text else ""
        )
        sql = f"""
            SELECT
                n.ein,
                nc.name,
                nc.name_secondary,
                nc.city,
                nc.state,
                0.0::float AS rank,
                {text_select} AS unique_text
            FROM unnest(:eins ::text[]) AS n(ein)
            LEFT JOIN public.nonprofit_canonical nc USING (ein)
            {text_join}
        """
        params: dict[str, object] = {"eins": list(eins)}

        logger.info(
            "get_nonprofits_by_ein: %d EIN(s), return_text=%s",
            len(eins),
            return_text,
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
        logger.info("get_nonprofits_by_ein: %d hits", len(hits))
        return hits

    def _identity_arm_sql(
        self,
        *,
        arm: Literal["nonprofit", "funder"],
        name: str | None,
        domain: str,
        ein_digits: str,
        limit: int | None,
    ) -> tuple[str, dict[str, object]]:
        """Assemble one arm of the ``search_identity`` query.

        CTE union of the active signal sources, collapsed to one row per EIN
        with the best rank and the signal that produced it. Signals that
        weren't supplied are simply not emitted (the frontend gates its URL
        CTE the same way — ``domain`` is a generated column that may not
        exist on older canonical builds, so it must only be referenced when
        actually searching by URL).
        """
        table = (
            "public.nonprofit_canonical" if arm == "nonprofit" else "public.funder_canonical"
        )
        ctes: list[str] = []
        unions: list[str] = []
        params: dict[str, object] = {}

        if name is not None:
            # Funders carry no DBA columns; nonprofits match on all four
            # name surfaces. ``%`` / ``_`` in the input are live ILIKE
            # wildcards — parity with the frontend, which doesn't escape.
            name_cols = (
                ["name", "name_secondary", "dba_1", "dba_2"]
                if arm == "nonprofit"
                else ["name", "name_secondary"]
            )
            name_where = " OR ".join(f"{col} ILIKE :like" for col in name_cols)
            ctes.append(
                f"""name_hits AS (
                    SELECT ein, {IDENTITY_RANK_NAME}::float8 AS rank, 'name' AS signal
                    FROM {table}
                    WHERE {name_where}
                )"""
            )
            unions.append("SELECT ein, rank, signal FROM name_hits")
            params["like"] = f"%{name}%"

        if ein_digits:
            ctes.append(
                f"""ein_hits AS (
                    SELECT ein, {IDENTITY_RANK_EIN}::float8 AS rank, 'ein' AS signal
                    FROM {table}
                    WHERE ein = :ein
                )"""
            )
            unions.append("SELECT ein, rank, signal FROM ein_hits")
            params["ein"] = ein_digits

        if arm == "nonprofit" and domain:
            # Both URL tiers sit between EIN-exact and name. Uses the
            # ix_nonprofit_canonical_domain partial btree (LIKE 'foo%' is
            # index-eligible on a btree).
            ctes.append(
                f"""url_hits AS (
                    SELECT ein,
                           CASE WHEN domain = :domain
                                THEN {IDENTITY_RANK_URL_EXACT}::float8
                                ELSE {IDENTITY_RANK_URL_PREFIX}::float8 END AS rank,
                           CASE WHEN domain = :domain
                                THEN 'url_exact' ELSE 'url_prefix' END AS signal
                    FROM public.nonprofit_canonical
                    WHERE domain = :domain OR domain LIKE :domain_prefix
                )"""
            )
            unions.append("SELECT ein, rank, signal FROM url_hits")
            params["domain"] = domain
            params["domain_prefix"] = f"{domain}%"

        # Inner join: every signal source above selects from this same table,
        # so the matched EIN set is a subset of it by construction. (A LEFT
        # JOIN would be needed only for a signal sourced from nonprofit_text,
        # which can carry 990-EZ filers absent from the canonical — there is
        # no such signal here.)
        join = f"JOIN {table} c USING (ein)"
        cte_sql = ",\n            ".join(ctes)
        union_sql = "\n                    UNION ALL\n                    ".join(unions)
        # MAX(rank) picks the winning tier per EIN; ARRAY_AGG ordered by
        # rank DESC reports which signal that winning rank came from.
        sql = f"""
            WITH {cte_sql},
            matched AS (
                SELECT ein,
                       MAX(rank) AS rank,
                       (ARRAY_AGG(signal ORDER BY rank DESC))[1] AS signal
                FROM (
                    {union_sql}
                ) u
                GROUP BY ein
            )
            SELECT
                m.ein,
                c.name,
                c.name_secondary,
                c.city,
                c.state,
                NULLIF(c.latest_taxyear, '')::int AS latest_taxyear,
                m.rank,
                m.signal
            FROM matched m
            {join}
            ORDER BY m.rank DESC,
                     NULLIF(c.latest_taxyear, '')::int DESC NULLS LAST,
                     c.name ASC,
                     m.ein ASC
        """
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        return sql, params

    def search_identity(
        self,
        name: str | None = None,
        url: str | None = None,
        ein: str | None = None,
        *,
        org_type: IdentityOrgType = "all",
        limit: int | None = 25,
    ) -> list[IdentityHit]:
        """Multi-signal ranked identity search, ported from the frontend's
        ``searchOrgs`` query (frontend/src/lib/queries/search.ts).

        Built for EIN-less nonprofit resolution: pass whatever identity
        signals a row has and get back ranked candidates. Signals are
        inferred from the arguments — at least one of ``name`` / ``url`` /
        ``ein`` is required:

        * ``ein`` — exact match (rank 2,000,000). Separators are stripped
          (``"13-1234567"`` works); raises ``ValueError`` unless exactly 9
          digits remain.
        * ``url`` — normalized to a bare domain (scheme, ``www.``, and
          path/query/fragment stripped) and matched against the generated
          ``nonprofit_canonical.domain`` column: exact domain (1,500,000)
          beats prefix domain (1,200,000). Raises ``ValueError`` when the
          input doesn't normalize to something domain-shaped — normalize
          upstream, don't pass raw customer junk like ``"N/A"``.
        * ``name`` — ILIKE substring over ``name`` / ``name_secondary``
          (plus ``dba_1`` / ``dba_2`` on the nonprofit arm) at rank
          1,000,000.

        Identity signals only. The frontend's narrative-FTS arm is
        deliberately **not** ported: a topical match on 990 program text is
        not evidence of identity, and mixing its raw ``ts_rank_cd`` scores
        (~0–50) into a list topped by fixed 1,000,000+ tiers means a weak
        topical hit lands at rank 1 whenever no real identity signal matches.
        Measured on a live portfolio, it bought ~4 points of recall while
        doubling the wrong-at-rank-1 rate, and on rows with no EIN it was
        wrong nearly every time ("Freedom River Well Project" → CRAG LAW
        CENTER). Use ``search_nonprofits`` for narrative/topical discovery —
        that's the method built for it, and it returns FTS ranks unmixed
        with identity tiers.

        Two arms: ``nonprofit_canonical`` (all three signals) and
        ``funder_canonical`` (name + EIN only — funders have no website, so
        no domain column). ``org_type`` restricts to one arm; a funder-only
        search with just a ``url`` has nothing to match and returns ``[]``.
        Each EIN collapses to its best rank per arm, with
        ``IdentityHit.signal`` naming the winning signal.

        ``limit`` is pushed into each arm's SQL, then the arms are merged
        and re-sliced here (rank DESC, latest_taxyear DESC nulls-last, name,
        ein — the frontend's exact ordering), so worst case ``2 * limit``
        rows cross the wire. ``limit=None`` returns everything; fine for
        EIN/URL lookups, unbounded for a broad name substring.
        """
        if org_type not in ("all", "nonprofit", "funder"):
            raise ValueError(
                f"org_type must be 'all', 'nonprofit', or 'funder', got {org_type!r}"
            )

        name, domain, ein_digits = _normalize_identity_signals(name, url, ein)

        if name is None and not ein_digits and not domain:
            raise ValueError(
                "search_identity requires at least one of name, url, or ein"
            )

        run_nonprofit = org_type in ("all", "nonprofit")
        # The funder arm can only contribute via name or EIN; skip the
        # query entirely when neither is present (mirrors the frontend's
        # foundationCanContribute gate).
        run_funder = org_type in ("all", "funder") and (name is not None or bool(ein_digits))
        if not run_nonprofit and not run_funder:
            logger.warning(
                "search_identity: funder arm has no URL signal; nothing to search"
            )
            return []

        logger.info(
            "search_identity: signals(name=%s, url=%s, ein=%s), org_type=%s, limit=%s",
            name is not None,
            bool(domain),
            bool(ein_digits),
            org_type,
            limit,
        )

        hits: list[IdentityHit] = []
        with self._session() as session:
            for arm, run in (("nonprofit", run_nonprofit), ("funder", run_funder)):
                if not run:
                    continue
                sql, params = self._identity_arm_sql(
                    arm=arm,
                    name=name,
                    domain=domain,
                    ein_digits=ein_digits,
                    limit=limit,
                )
                rows = session.execute(text(sql), params).mappings().all()
                hits.extend(
                    IdentityHit(
                        ein=r["ein"],
                        name=r["name"],
                        name_secondary=r["name_secondary"],
                        city=r["city"],
                        state=r["state"],
                        latest_taxyear=(
                            int(r["latest_taxyear"]) if r["latest_taxyear"] is not None else None
                        ),
                        org_type=arm,
                        rank=float(r["rank"]),
                        signal=r["signal"],
                    )
                    for r in rows
                )

        # Cross-arm merge with the frontend's exact ordering; NULL taxyears
        # and names sort last within their rank tier.
        hits.sort(key=_identity_sort_key)
        if limit is not None:
            hits = hits[:limit]
        logger.info("search_identity: %d hits", len(hits))
        return hits

    @staticmethod
    def _identity_bulk_arm_sql(
        *,
        arm: Literal["nonprofit", "funder"],
        has_name: bool,
        has_domain: bool,
        has_ein: bool,
        include_url_prefix: bool,
        limit_per_query: int | None,
    ) -> str:
        """Assemble one arm of the batched query.

        Same tiers and same output shape as ``_identity_arm_sql``, but every
        signal CTE joins against an ``unnest``-ed array of (key, value) pairs
        instead of a scalar bind, so N input rows cost one round trip per arm
        rather than N. Results are keyed by the caller's ``key`` throughout.
        """
        table = (
            "public.nonprofit_canonical" if arm == "nonprofit" else "public.funder_canonical"
        )
        ctes: list[str] = []
        unions: list[str] = []

        if has_name:
            name_cols = (
                ["name", "name_secondary", "dba_1", "dba_2"]
                if arm == "nonprofit"
                else ["name", "name_secondary"]
            )
            # Nested loop: one scan per input name. ILIKE '%x%' is
            # unindexable, so this is the expensive arm — see the
            # iter_identity_universe docstring for the local-match
            # alternative when names dominate the batch.
            on_clause = " OR ".join(
                f"c.{col} ILIKE '%' || q.val || '%'" for col in name_cols
            )
            ctes.append(
                f"""name_hits AS (
                    SELECT q.key, c.ein, {IDENTITY_RANK_NAME}::float8 AS rank,
                           'name' AS signal
                    FROM unnest(:name_keys ::text[], :name_vals ::text[]) AS q(key, val)
                    JOIN {table} c ON {on_clause}
                )"""
            )
            unions.append("SELECT key, ein, rank, signal FROM name_hits")

        if has_ein:
            ctes.append(
                f"""ein_hits AS (
                    SELECT q.key, c.ein, {IDENTITY_RANK_EIN}::float8 AS rank,
                           'ein' AS signal
                    FROM unnest(:ein_keys ::text[], :ein_vals ::text[]) AS q(key, val)
                    JOIN {table} c ON c.ein = q.val
                )"""
            )
            unions.append("SELECT key, ein, rank, signal FROM ein_hits")

        if arm == "nonprofit" and has_domain:
            # Exact-domain is a hash join — ONE scan for the whole batch.
            ctes.append(
                f"""url_hits AS (
                    SELECT q.key, c.ein, {IDENTITY_RANK_URL_EXACT}::float8 AS rank,
                           'url_exact' AS signal
                    FROM unnest(:url_keys ::text[], :url_vals ::text[]) AS q(key, val)
                    JOIN public.nonprofit_canonical c ON c.domain = q.val
                    WHERE c.domain <> ''
                )"""
            )
            unions.append("SELECT key, ein, rank, signal FROM url_hits")
            if include_url_prefix:
                # Opt-in: LIKE 'x%' can't use the btree under a non-C
                # collation, so this degenerates to one scan per input row.
                ctes.append(
                    f"""url_prefix_hits AS (
                        SELECT q.key, c.ein,
                               {IDENTITY_RANK_URL_PREFIX}::float8 AS rank,
                               'url_prefix' AS signal
                        FROM unnest(:url_keys ::text[], :url_vals ::text[]) AS q(key, val)
                        JOIN public.nonprofit_canonical c
                          ON c.domain LIKE q.val || '%'
                        WHERE c.domain <> ''
                    )"""
                )
                unions.append("SELECT key, ein, rank, signal FROM url_prefix_hits")

        # Inner, for the same reason as the single-row path.
        join = f"JOIN {table} c USING (ein)"
        cte_sql = ",\n            ".join(ctes)
        union_sql = "\n                    UNION ALL\n                    ".join(unions)
        # ROW_NUMBER partitioned by key applies limit_per_query server-side,
        # so a 500-row batch can't drag back the entire match set for a
        # generic name like "foundation".
        rank_filter = "WHERE rn <= :limit_per_query" if limit_per_query is not None else ""
        return f"""
            WITH {cte_sql},
            matched AS (
                SELECT key, ein,
                       MAX(rank) AS rank,
                       (ARRAY_AGG(signal ORDER BY rank DESC))[1] AS signal
                FROM (
                    {union_sql}
                ) u
                GROUP BY key, ein
            ),
            ranked AS (
                SELECT
                    m.key,
                    m.ein,
                    c.name,
                    c.name_secondary,
                    c.city,
                    c.state,
                    NULLIF(c.latest_taxyear, '')::int AS latest_taxyear,
                    m.rank,
                    m.signal,
                    ROW_NUMBER() OVER (
                        PARTITION BY m.key
                        ORDER BY m.rank DESC,
                                 NULLIF(c.latest_taxyear, '')::int DESC NULLS LAST,
                                 c.name ASC,
                                 m.ein ASC
                    ) AS rn
                FROM matched m
                {join}
            )
            SELECT key, ein, name, name_secondary, city, state,
                   latest_taxyear, rank, signal
            FROM ranked
            {rank_filter}
        """

    def search_identity_bulk(
        self,
        queries: Sequence[IdentityQuery],
        *,
        org_type: IdentityOrgType = "all",
        include_url_prefix: bool = False,
        limit_per_query: int | None = 25,
        chunk_size: int = 500,
        on_invalid: Literal["raise", "skip"] = "raise",
    ) -> dict[str, list[IdentityHit]]:
        """Batched ``search_identity`` — N input rows, not N round trips.

        Returns ``{query.key: [IdentityHit, ...]}`` with an entry for **every**
        input key, empty list included, so callers can zip results back onto
        their rows without membership checks.

        Per-signal cost is wildly uneven, and that drives how to call this:

        * ``ein`` — PK index scan. ~500 lookups in one ~170ms round trip
          against a remote instance, vs ~78s looping ``search_identity``.
        * ``url`` (exact domain) — hash join, ONE table scan for the whole
          batch regardless of size. ~500 in ~300ms.
        * ``url`` prefix tier — off by default here. ``LIKE 'x%'`` can't use
          the ``domain`` btree under a non-C collation, so it degrades to a
          scan per input row. It's also much less useful in a pipeline than
          in a search box: URLs arrive normalized, and prefix matching
          invites false positives. Set ``include_url_prefix=True`` when you
          specifically want it.
        * ``name`` — the expensive one. ``ILIKE '%x%'`` is unindexable, so
          this is a scan per input row no matter how it's batched (batching
          still saves the round trips — roughly 30% — but not the scans).
          Budget ~0.5s per named row. Above a few hundred name-only rows,
          prefer ``iter_identity_universe`` and match in process.

        ``chunk_size`` bounds both the array-parameter size and the length of
        any single transaction; progress is logged per chunk. Lower it for
        name-heavy batches (a 500-name chunk is a multi-minute query), raise
        it for pure EIN/URL work.

        ``on_invalid`` controls malformed ``ein``/``url`` values. ``"raise"``
        (default) matches ``search_identity`` and fails the whole batch, with
        every offending key named — validation runs before any query, so a bad
        row costs no query time. ``"skip"`` drops just the bad signal and
        keeps the row (a row left with no usable signal gets an empty list);
        use it when feeding unwashed customer data where ``"N/A"`` in a URL
        column shouldn't sink 5,000 good rows.

        ``org_type`` and the ranking tiers behave exactly as in
        ``search_identity`` — including the deliberate absence of a narrative
        signal, which is a topical match rather than identity evidence. See
        that method's docstring for why, and use ``search_nonprofits`` when
        narrative discovery is what you actually want.
        """
        if org_type not in ("all", "nonprofit", "funder"):
            raise ValueError(
                f"org_type must be 'all', 'nonprofit', or 'funder', got {org_type!r}"
            )
        if on_invalid not in ("raise", "skip"):
            raise ValueError(
                f"on_invalid must be 'raise' or 'skip', got {on_invalid!r}"
            )
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
        if not queries:
            return {}

        seen: set[str] = set()
        for q in queries:
            if not q.key:
                raise ValueError("IdentityQuery.key must be a non-empty string")
            if q.key in seen:
                raise ValueError(f"duplicate IdentityQuery.key: {q.key!r}")
            seen.add(q.key)

        # Validate + normalize everything up front so a malformed row on
        # input 4,000 doesn't surface after twenty minutes of scanning.
        normalized: list[tuple[str, str | None, str, str]] = []
        invalid: list[str] = []
        for q in queries:
            try:
                name, domain, ein_digits = _normalize_identity_signals(
                    q.name, q.url, q.ein, strict=(on_invalid == "raise")
                )
            except ValueError as exc:
                invalid.append(f"{q.key}: {exc}")
                continue
            normalized.append((q.key, name, domain, ein_digits))
        if invalid:
            shown = "; ".join(invalid[:10])
            more = f" (+{len(invalid) - 10} more)" if len(invalid) > 10 else ""
            raise ValueError(f"invalid identity queries -> {shown}{more}")

        results: dict[str, list[IdentityHit]] = {q.key: [] for q in queries}
        run_nonprofit = org_type in ("all", "nonprofit")
        run_funder = org_type in ("all", "funder")

        total_chunks = (len(normalized) + chunk_size - 1) // chunk_size
        logger.info(
            "search_identity_bulk: %d queries in %d chunk(s) of %d, org_type=%s, "
            "include_url_prefix=%s, limit_per_query=%s",
            len(normalized),
            total_chunks,
            chunk_size,
            org_type,
            include_url_prefix,
            limit_per_query,
        )

        with self._session() as session:
            for chunk_i in range(total_chunks):
                chunk = normalized[chunk_i * chunk_size : (chunk_i + 1) * chunk_size]

                name_keys, name_vals = [], []
                url_keys, url_vals = [], []
                ein_keys, ein_vals = [], []
                for key, name, domain, ein_digits in chunk:
                    if name is not None:
                        name_keys.append(key)
                        name_vals.append(name)
                    if domain:
                        url_keys.append(key)
                        url_vals.append(domain)
                    if ein_digits:
                        ein_keys.append(key)
                        ein_vals.append(ein_digits)

                has_name, has_domain, has_ein = (
                    bool(name_keys),
                    bool(url_keys),
                    bool(ein_keys),
                )
                if not (has_name or has_domain or has_ein):
                    continue

                base_params: dict[str, object] = {
                    "name_keys": name_keys,
                    "name_vals": name_vals,
                    "url_keys": url_keys,
                    "url_vals": url_vals,
                    "ein_keys": ein_keys,
                    "ein_vals": ein_vals,
                }
                if limit_per_query is not None:
                    base_params["limit_per_query"] = limit_per_query

                for arm, run in (("nonprofit", run_nonprofit), ("funder", run_funder)):
                    # The funder arm has no URL surface, so a
                    # chunk carrying only URLs gives it nothing to match.
                    if not run:
                        continue
                    if arm == "funder" and not (has_name or has_ein):
                        continue
                    sql = self._identity_bulk_arm_sql(
                        arm=arm,
                        has_name=has_name,
                        has_domain=has_domain,
                        has_ein=has_ein,
                        include_url_prefix=include_url_prefix,
                        limit_per_query=limit_per_query,
                    )
                    rows = session.execute(text(sql), base_params).mappings().all()
                    for r in rows:
                        results[r["key"]].append(
                            IdentityHit(
                                ein=r["ein"],
                                name=r["name"],
                                name_secondary=r["name_secondary"],
                                city=r["city"],
                                state=r["state"],
                                latest_taxyear=(
                                    int(r["latest_taxyear"])
                                    if r["latest_taxyear"] is not None
                                    else None
                                ),
                                org_type=arm,
                                rank=float(r["rank"]),
                                signal=r["signal"],
                            )
                        )
                logger.info(
                    "search_identity_bulk: chunk %d/%d done (%d rows)",
                    chunk_i + 1,
                    total_chunks,
                    len(chunk),
                )

        # Cross-arm merge per key, then the per-key slice (SQL already
        # limited each arm; this trims the union of the two).
        for key, hits in results.items():
            hits.sort(key=_identity_sort_key)
            if limit_per_query is not None and len(hits) > limit_per_query:
                results[key] = hits[:limit_per_query]

        matched = sum(1 for h in results.values() if h)
        logger.info(
            "search_identity_bulk: %d/%d queries matched", matched, len(results)
        )
        return results

    def iter_identity_universe(
        self,
        *,
        org_type: IdentityOrgType = "all",
        batch_size: int = 10_000,
    ) -> Iterator[CanonicalIdentity]:
        """Stream the full canonical identity universe for local matching.

        The escape hatch for name-heavy resolution. Name search can't be made
        cheap server-side — ``ILIKE '%x%'`` is unindexable, so every name
        costs a full scan whether looped or batched (measured: ~0.55s per
        name either way) — but the whole universe is small enough to pull
        once: ~479K nonprofit rows stream in ~25s at ~19K rows/s, ~57MB of
        strings, plus ~161K funder rows. That's break-even at roughly 50
        name-only rows and a rout past a few hundred.

        Yields ``CanonicalIdentity`` and streams server-side (no full
        materialization in the driver), so memory stays bounded by
        ``batch_size`` rather than by the table.

        Matching itself deliberately stays out of this client: it needs
        pandas/recordlinkage, and the client is dependency-light by design
        (sqlalchemy + psycopg2 only) so consumers can install it standalone.
        ``grant_matching.py`` already has the blocking + Jaro-Winkler pattern
        to feed this into — it's the same shape as the 990-PF matching work.
        """
        if org_type not in ("all", "nonprofit", "funder"):
            raise ValueError(
                f"org_type must be 'all', 'nonprofit', or 'funder', got {org_type!r}"
            )
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")

        # funder_canonical has no website (hence no generated domain column)
        # and no DBA columns; NULL them so both arms yield the same shape.
        arms: list[tuple[str, str]] = []
        if org_type in ("all", "nonprofit"):
            arms.append(("nonprofit", """
                SELECT ein, name, name_secondary, dba_1, dba_2, domain,
                       city, state, NULLIF(latest_taxyear, '')::int AS latest_taxyear
                FROM public.nonprofit_canonical
            """))
        if org_type in ("all", "funder"):
            arms.append(("funder", """
                SELECT ein, name, name_secondary,
                       NULL::text AS dba_1, NULL::text AS dba_2, NULL::text AS domain,
                       city, state, NULLIF(latest_taxyear, '')::int AS latest_taxyear
                FROM public.funder_canonical
            """))

        logger.info(
            "iter_identity_universe: streaming org_type=%s, batch_size=%d",
            org_type,
            batch_size,
        )
        for arm, sql in arms:
            emitted = 0
            with self._session() as session:
                result = (
                    session.connection()
                    .execution_options(stream_results=True, yield_per=batch_size)
                    .execute(text(sql))
                    .mappings()
                )
                for r in result:
                    emitted += 1
                    yield CanonicalIdentity(
                        ein=r["ein"],
                        name=r["name"],
                        name_secondary=r["name_secondary"],
                        dba_1=r["dba_1"],
                        dba_2=r["dba_2"],
                        domain=r["domain"],
                        city=r["city"],
                        state=r["state"],
                        latest_taxyear=(
                            int(r["latest_taxyear"])
                            if r["latest_taxyear"] is not None
                            else None
                        ),
                        org_type=arm,
                    )
            logger.info("iter_identity_universe: %s arm streamed %d rows", arm, emitted)

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
        string is much more common than NULL in the staging data, and we
        want a missing governgrants value to subtract zero (not produce
        NULL) so the no-gov contribution stays a real number.
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

        TODO: collapse to a single ``LEFT JOIN canonical_organizations`` once
        the unified canonical table lands — see
        https://github.com/vibrant-data-labs/givingtuesday-datamart/issues/22.
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
        # TODO: collapse the inner SELECT to a single LEFT JOIN +
        # ``COALESCE(co.name, ug.granter_name)`` once the unified canonical
        # table lands — see
        # https://github.com/vibrant-data-labs/givingtuesday-datamart/issues/22.
        #
        # ``COALESCE(SUM(...), 0)`` keeps the total numeric even when an
        # EIN has grants but every grant_amount is NULL — otherwise NaN
        # propagates through downstream mean/threshold filters and silently
        # drops the EIN.
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
        # taxyear), so sum the duplicates within each year first and then
        # average across years. ``AVG(col)`` directly over all rows would
        # weight by row count and quietly drop EINs whose duplicates pulled
        # the all-rows mean below the threshold.
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
