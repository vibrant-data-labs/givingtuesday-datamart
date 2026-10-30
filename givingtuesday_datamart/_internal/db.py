"""SQLAlchemy session helper for the Datamart pipeline modules.

``get_session(config=...)`` is a transactional context manager — yields a
session, commits on clean exit, rolls back + re-raises on exception, and
always closes the session. Callers pass a config dict shaped like
``{"postgres": {"host", "port", "user", "password", "database"}}``;
``ingestion.datamart_config()`` is the canonical builder.

This is a pipeline-side helper. The read-only ``client`` package owns its
own engine and does not import from here.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL
from sqlalchemy.orm import Session, sessionmaker

from givingtuesday_datamart._internal.config import get_configuration
from givingtuesday_datamart._internal.logger import logger


def _engine_from_config(config: dict | None) -> Engine:
    config = config if config is not None else get_configuration()
    pg = config["postgres"]
    url = URL.create(
        "postgresql",
        username=pg["user"],
        password=pg["password"],
        host=pg["host"],
        port=int(pg["port"]),
        database=pg["database"],
    )
    # TCP keepalives: the shared RDS host sits behind a NAT from the machines
    # that run multi-minute statements (canonical builds, matcher reads), and
    # a statement that returns nothing for ~10 minutes had its idle
    # connection dropped ("could not receive data from server: Operation
    # timed out", 2026-10-14). A probe every 30 s keeps the mapping alive.
    return create_engine(
        url,
        future=True,
        connect_args={
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 6,
        },
    )


@contextmanager
def get_session(
    config: dict | None = None,
    session: Session | None = None,
) -> Iterator[Session]:
    """Yield a transactional session against the postgres in ``config``.

    Builds a fresh engine + session per call. Commits on clean exit,
    rolls back + re-raises on exception, always closes, and disposes the
    engine it built.
    """
    engine = None
    if session is None:
        engine = _engine_from_config(config)
        session = sessionmaker(bind=engine)()
    try:
        yield session
        session.commit()
    except Exception:
        logger.exception("Session rollback")
        session.rollback()
        raise
    finally:
        session.close()
        # A closed session returns its connection to the engine's pool,
        # and the engine sits in a reference cycle that only a full
        # garbage collection frees: an idle backend on the shared instance
        # for every session a run opens until then (the matcher opens a
        # dozen). Disposing the engine this call built closes it at once.
        if engine is not None:
            engine.dispose()
