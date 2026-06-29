from fastapi import APIRouter, Depends, Request

from app.core.limiter import limiter
from app.core.tenant_auth import get_current_tenant, TenantContext
from app.api.v1.patients.router import router as patients_router
from app.api.v1.visits.router import router as visits_router
from app.services.orchestrator import register_and_create_visit

router = APIRouter(dependencies=[Depends(get_current_tenant)])
router.include_router(patients_router, tags=["patients"])
router.include_router(visits_router, tags=["visits"])


@router.post("/register-and-visit")
@limiter.limit("10/minute")
async def register_and_visit(
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    """Register a patient and create a visit in a single call.

    **Request body:**
    ```json
    {
      "patient": { "full_name": "...", "date_of_birth": "...", "gender": "..." },
      "visit": { "visit_type": "outpatient", "payment_type": "cash" }
    }
    ```
    """
    return await register_and_create_visit(request)
