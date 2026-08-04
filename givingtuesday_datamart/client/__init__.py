"""Read-only Python client over the gt_datamart canonical surface."""

from givingtuesday_datamart.client.client import GtDatamartClient
from givingtuesday_datamart.client.models import (
    BasicFieldsRow,
    Grant,
    GrantSummary,
    IdentityHit,
    Nonprofit,
    NonprofitHit,
)

__all__ = [
    "GtDatamartClient",
    "IdentityHit",
    "NonprofitHit",
    "Nonprofit",
    "BasicFieldsRow",
    "Grant",
    "GrantSummary",
]
