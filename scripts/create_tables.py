"""Thin wrapper around ``python -m givingtuesday_datamart.sources refresh``.

Previously this script hardcoded dated S3 URLs per table. The source registry
in ``givingtuesday_datamart.sources`` now owns URL resolution, lineage
stamping, and idempotency. This file exists only to preserve the familiar
entrypoint (``python scripts/create_tables.py``) — it delegates to the CLI.

Prefer ``python -m givingtuesday_datamart.sources refresh`` directly.
"""

from __future__ import annotations

import sys

from givingtuesday_datamart.sources.__main__ import main as sources_main


if __name__ == "__main__":
    # Translate any CLI args through to the `refresh` subcommand.
    sys.exit(sources_main(["refresh", *sys.argv[1:]]))
