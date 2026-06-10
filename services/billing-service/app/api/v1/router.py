from fastapi import APIRouter, Depends

from app.core.tenant_auth import get_current_tenant
from app.api.v1.schemas import *  # noqa

router = APIRouter(dependencies=[Depends(get_current_tenant)])
# CRUD endpoints for bills, bill items, payments, insurance claims
# Placeholder — preserve existing monolith structure
