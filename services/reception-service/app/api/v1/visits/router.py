from fastapi import APIRouter, Depends, Request

from app.core.limiter import limiter
from app.core.tenant_auth import get_current_tenant, TenantContext
from app.services.orchestrator import create_visit, triage_queue_today

router = APIRouter()


@router.post("/visits")
@limiter.limit("10/minute")
async def create(
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await create_visit(request)


@router.get("/visits/queues/triage/today")
@limiter.limit("30/minute")
async def triage_today(
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await triage_queue_today(request)
