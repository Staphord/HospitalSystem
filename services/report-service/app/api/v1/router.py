from fastapi import APIRouter, Depends

from app.assistant.router import router as assistant_router
from app.core.tenant_auth import get_current_tenant
from app.api.v1.schemas import *  # noqa

router = APIRouter(dependencies=[Depends(get_current_tenant)])
# Read-only analytics endpoints
# Placeholder — preserve existing monolith structure

# Hospital assistant. Read-only operational chat and feedback, gated by the
# ASSISTANT_OPERATIONAL_CHAT_ENABLED flag, which is off by default.
router.include_router(assistant_router)
