from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.patients.schemas import (
    PatientRegisterRequest,
    PatientResponse,
    PatientSearchResponse,
)
from app.core.security import require_role
from app.dependencies import get_tenant_db, get_tenant_id_from_token
from app.services.patient_service import (
    delete_patient,
    get_patient_by_id,
    get_patient_count,
    register_patient,
    search_patients,
)

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("", response_model=PatientSearchResponse)
def list_patients(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    patients = search_patients(db=db, hospital_id=tenant_id, limit=limit, offset=offset)
    total = get_patient_count(db, tenant_id)
    return PatientSearchResponse(patients=patients, total=total)


@router.post("/register", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def register(
    body: PatientRegisterRequest,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    existing = search_patients(
        db, tenant_id, national_id=body.national_id
    ) if body.national_id else []
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Patient with national_id '{body.national_id}' already exists",
        )

    patient = register_patient(
        db=db,
        hospital_id=tenant_id,
        full_name=body.full_name,
        date_of_birth=body.date_of_birth,
        gender=body.gender,
        phone=body.phone,
        email=body.email,
        address=body.address,
        emergency_contact_name=body.emergency_contact_name,
        emergency_contact_phone=body.emergency_contact_phone,
        national_id=body.national_id,
        medical_history=body.medical_history,
        allergies=body.allergies,
        blood_group=body.blood_group,
        created_by=payload.get("preferred_username", ""),
    )
    return patient


@router.get("/search", response_model=PatientSearchResponse)
def search(
    query: str = Query(None, min_length=1),
    national_id: str = Query(None, min_length=1),
    patient_number: str = Query(None, min_length=1),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    patients = search_patients(
        db=db,
        hospital_id=tenant_id,
        query=query,
        national_id=national_id,
        patient_number=patient_number,
        limit=limit,
        offset=offset,
    )
    total = get_patient_count(db, tenant_id)
    return PatientSearchResponse(patients=patients, total=total)


@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient(
    patient_id: str,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    patient = get_patient_by_id(db, tenant_id, patient_id)
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return patient


@router.delete("/{patient_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_patient(
    patient_id: str,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    deleted = delete_patient(db, tenant_id, patient_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
