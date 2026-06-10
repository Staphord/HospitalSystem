from fastapi import APIRouter, Depends

from app.core.tenant_auth import get_current_tenant
from app.api.v1.schemas import *  # noqa
from app.api.v1.patients.router import router as patients_router

router = APIRouter(dependencies=[Depends(get_current_tenant)])
router.include_router(patients_router, tags=["patients"])
# CRUD endpoints for patients, visits, insurance, queue
# Placeholder — preserve existing monolith structure
