"""Read-only Python client over the gt_datamart canonical surface."""

from givingtuesday_datamart.client.client import GtDatamartClient
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

__all__ = [
    "GtDatamartClient",
    "CanonicalIdentity",
    "IdentityHit",
    "IdentityQuery",
    "NonprofitHit",
    "Nonprofit",
    "BasicFieldsRow",
    "Grant",
    "GrantSummary",
]
