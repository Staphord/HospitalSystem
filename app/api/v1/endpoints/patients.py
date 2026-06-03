from fastapi import APIRouter, Depends

from app.core.database import close_hospital_context, get_hospital_context
from app.core.limiter import limiter
from app.core.security import get_current_hospital_id, require_role

router = APIRouter()


@router.get("/patients")
@limiter.limit("30/minute")
async def list_patients(hospital_id: str = Depends(get_current_hospital_id)) -> dict:
    context = get_hospital_context(hospital_id)
    try:
        return {
            "hospital_id": context.hospital_id,
            "patients": [
                {"id": "p-001", "name": "Jane Doe"},
                {"id": "p-002", "name": "John Doe"},
            ],
        }
    finally:
        close_hospital_context(context)


@router.post("/patients/create")
@limiter.limit("10/minute")
async def create_patient(
    hospital_id: str = Depends(get_current_hospital_id),
    _user=Depends(require_role("hospital_admin")),
) -> dict:
    context = get_hospital_context(hospital_id)
    try:
        return {
            "hospital_id": context.hospital_id,
            "status": "created",
        }
    finally:
        close_hospital_context(context)
