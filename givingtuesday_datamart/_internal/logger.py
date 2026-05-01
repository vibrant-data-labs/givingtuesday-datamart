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
