from fastapi import APIRouter
from app.api.v1.admin import router as admin_router

router = APIRouter()
router.include_router(admin_router, prefix="/admin")
