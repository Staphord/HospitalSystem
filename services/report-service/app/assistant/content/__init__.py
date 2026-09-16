"""Versioned, repo-shipped operational content for the hospital assistant.

This pack is deliberately non-PHI. It describes how the hospital system works,
which reports exist and who may run them, and the operational policies staff are
expected to follow. It contains no patient data, no clinical judgement, no
medication severity, and no diagnosis content.

Nothing here reaches a user or the model until the retrieval layer has filtered
it by tenant, department, role, version, effective date, and approval state.
"""

from app.assistant.content.entries import (
    ADMIN_ONLY,
    ALL_STAFF,
    CONTENT_PACK_VERSION,
    OPERATIONAL_CONTENT,
)
from app.assistant.content.models import (
    ROLE_DEPARTMENT,
    ApprovalState,
    ContentEntry,
    ContentKind,
)

__all__ = [
    "ADMIN_ONLY",
    "ALL_STAFF",
    "CONTENT_PACK_VERSION",
    "OPERATIONAL_CONTENT",
    "ROLE_DEPARTMENT",
    "ApprovalState",
    "ContentEntry",
    "ContentKind",
]
