from fastapi import APIRouter
from app.api.v1.superadmin.router import router as superadmin_router

router = APIRouter()
router.include_router(superadmin_router, prefix="/superadmin")
