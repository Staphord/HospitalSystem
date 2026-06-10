from fastapi import APIRouter, Depends

from app.core.tenant_auth import get_current_tenant
from app.api.v1.schemas import *  # noqa

router = APIRouter(dependencies=[Depends(get_current_tenant)])
# radiology endpoints
# Placeholder — preserve existing monolith structure
