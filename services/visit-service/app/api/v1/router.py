from fastapi import APIRouter

from app.api.v1.visits.router import router as visits_router

router = APIRouter(prefix="/api/v1")
router.include_router(visits_router)
