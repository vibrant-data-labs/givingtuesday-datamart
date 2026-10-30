"""Pipeline-wide logger.

A single ``logging.Logger`` named ``givingtuesday_datamart``, configured at
INFO level on first import. Handlers are not attached here — operators
attach their own (or rely on root configuration). Importing modules just
do ``from givingtuesday_datamart._internal.logger import logger``.
"""

from __future__ import annotations

import logging


logger = logging.getLogger("givingtuesday_datamart")
if logger.level == logging.NOTSET:
    logger.setLevel(logging.INFO)


def configure_cli_logging() -> None:
    """Attach a console handler so INFO progress lines are visible from a
    command line (each ``main`` calls it; once is enough).

    The package logger deliberately ships without handlers so library
    consumers control their own output. But with no handler anywhere,
    Python's last-resort handler only prints WARNING and above — which
    silently eats all the progress logging (Starting ingest / Rows written
    / a relation built and committed) when run as a command-line tool.
    """
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(handler)
