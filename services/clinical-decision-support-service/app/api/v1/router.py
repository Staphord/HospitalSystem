from fastapi import APIRouter

from app.cds.router import router as cds_router

router = APIRouter()
router.include_router(cds_router)
