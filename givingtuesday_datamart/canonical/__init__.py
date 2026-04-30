"""Canonical entity tables built on top of the staging layer.

One row per real-world entity (nonprofit, funder, person), materialized
from the staging tables after each refresh. Lives alongside staging in
``public.*`` and is rebuilt by ``python -m givingtuesday_datamart.sources
build-canonical``.
"""
