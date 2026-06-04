from fastapi import APIRouter, Depends

from app.core.security import get_current_active_user
from .auth.router import public_router as auth_public_router
from .auth.router import router as auth_router
from .reception.router import router as reception_router
from .triage.router import router as triage_router
from .consultation.router import router as consultation_router
from .laboratory.router import router as laboratory_router
from .radiology.router import router as radiology_router
from .pharmacy.router import router as pharmacy_router
from .billing.router import router as billing_router
from .ward.router import router as ward_router
from .admin.router import router as admin_router
from .notifications.router import router as notifications_router
from .superadmin.router import router as superadmin_router
from .endpoints.auth import router as health_router
from .endpoints.users import router as users_router
from .endpoints.patients import router as patients_router

router = APIRouter()
protected_router = APIRouter(dependencies=[Depends(get_current_active_user)])

router.include_router(auth_public_router, prefix="/auth", tags=["auth"])
router.include_router(health_router, tags=["health"])

protected_router.include_router(auth_router, prefix="/auth", tags=["auth"])
protected_router.include_router(
    reception_router, prefix="/reception", tags=["reception"])
protected_router.include_router(
    triage_router, prefix="/triage", tags=["triage"])
protected_router.include_router(
    consultation_router, prefix="/consultation", tags=["consultation"])
protected_router.include_router(
    laboratory_router, prefix="/laboratory", tags=["laboratory"])
protected_router.include_router(
    radiology_router, prefix="/radiology", tags=["radiology"])
protected_router.include_router(
    pharmacy_router, prefix="/pharmacy", tags=["pharmacy"])
protected_router.include_router(
    billing_router, prefix="/billing", tags=["billing"])
protected_router.include_router(ward_router, prefix="/ward", tags=["ward"])
protected_router.include_router(admin_router, prefix="/admin", tags=["admin"])
protected_router.include_router(
    notifications_router, prefix="/notifications", tags=["notifications"])
protected_router.include_router(
    superadmin_router, prefix="/superadmin", tags=["superadmin"])
protected_router.include_router(users_router, tags=["users"])
protected_router.include_router(patients_router, tags=["patients"])

router.include_router(protected_router)
