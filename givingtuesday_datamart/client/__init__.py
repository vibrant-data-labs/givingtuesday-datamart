"""Read-only Python client over the gt_datamart canonical surface."""

from givingtuesday_datamart.client.client import GtDatamartClient
from givingtuesday_datamart.client.models import (
    BasicFieldsRow,
    Grant,
    Nonprofit,
    NonprofitHit,
)

__all__ = [
    "GtDatamartClient",
    "NonprofitHit",
    "Nonprofit",
    "BasicFieldsRow",
    "Grant",
]
