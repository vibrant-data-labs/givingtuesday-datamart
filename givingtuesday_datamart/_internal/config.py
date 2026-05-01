"""Read the VDL ``config.ini`` from disk.

Mirrors the historical ``vdl_tools.shared_tools.tools.config_utils`` shape
so existing operator config files keep working unchanged. Lookup order:

1. Explicit ``configpath=`` argument.
2. ``GT_DATAMART_CONFIG_PATH`` env var (preferred for new deployments).
3. ``VDL_GLOBAL_CONFIG_PATH`` env var (legacy — still honored).
4. ``./config.ini`` in the current working directory.

Returns the parsed ``configparser.ConfigParser`` (which behaves like a
nested dict for reads — ``cfg["postgres"]["host"]`` works the same way as
the old vdl-tools dict). Returns an empty dict when no file is found, so
callers that only need env-var-driven behavior aren't forced to ship an
ini file.
"""

from __future__ import annotations

import configparser
import os
import pathlib as pl

from givingtuesday_datamart._internal.logger import logger


_GT_ENV_VAR = "GT_DATAMART_CONFIG_PATH"
_LEGACY_ENV_VAR = "VDL_GLOBAL_CONFIG_PATH"


def _resolve_config_path(configpath: pl.Path | None) -> pl.Path:
    if configpath is not None:
        return pl.Path(configpath)
    for env_var in (_GT_ENV_VAR, _LEGACY_ENV_VAR):
        env_val = os.getenv(env_var)
        if env_val:
            return pl.Path(env_val)
    return pl.Path.cwd() / "config.ini"


def get_configuration(configpath: pl.Path | None = None) -> configparser.ConfigParser | dict:
    path = _resolve_config_path(configpath)
    if not path.exists():
        logger.warning(
            "Config file not found at %s. Expecting configuration to be "
            "passed explicitly or via environment.",
            path,
        )
        return {}
    config = configparser.ConfigParser()
    config.read(path)
    return config
