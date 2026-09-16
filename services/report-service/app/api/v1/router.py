from fastapi import APIRouter, Depends

from app.api.v1.admin.assistant_config import router as assistant_config_router
from app.api.v1.metrics.router import router as metrics_router
from app.assistant.router import router as assistant_router
from app.core.tenant_auth import get_current_tenant
from app.api.v1.schemas import *  # noqa

router = APIRouter(dependencies=[Depends(get_current_tenant)])
# Read-only analytics endpoints
# Placeholder — preserve existing monolith structure

# Hospital assistant. Read-only operational chat, history, medicines, voice and
# live figures, all behind the single ASSISTANT_OPERATIONAL_CHAT_ENABLED switch,
# which is off by default. What each caller reaches is decided by role.
router.include_router(assistant_router)

# Operational figures read from the tenant database. Shares its registry, role
# gate and column allowlist with the assistant, and the same switch.
router.include_router(metrics_router)

# Platform assistant configuration. Super admin only, and not tenant-scoped:
# these routes set the model provider and the request bounds for every hospital
# at once. The role check lives on the routes themselves.
router.include_router(assistant_config_router)
