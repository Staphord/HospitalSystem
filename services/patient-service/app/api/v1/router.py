from fastapi import APIRouter

from app.api.v1.patients.router import router as patients_router

router = APIRouter(prefix="/api/v1")
router.include_router(patients_router)
