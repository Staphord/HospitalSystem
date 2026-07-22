from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.patients.schemas import (
    PatientRegisterRequest,
    PatientResponse,
    PatientSearchResponse,
    PatientUpdateRequest,
)
from app.core.security import require_any_role
from app.dependencies import get_tenant_db, get_tenant_id_from_token
from app.services.patient_service import (
    delete_patient,
    get_patient_by_id,
    get_patient_count,
    register_patient,
    search_patients,
    update_patient,
)

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("", response_model=PatientSearchResponse, summary="List registered patients with pagination")
def list_patients(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_any_role(["hospital_admin", "receptionist"])),
):
    patients = search_patients(db=db, hospital_id=tenant_id, limit=limit, offset=offset)
    total = get_patient_count(db, tenant_id)
    return PatientSearchResponse(patients=patients, total=total)


@router.post("/register", response_model=PatientResponse, status_code=status.HTTP_201_CREATED, summary="Register a new patient profile")
def register(
    body: PatientRegisterRequest,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_any_role(["hospital_admin", "receptionist"])),
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
        phone_primary=body.phone_primary,
        phone_secondary=body.phone_secondary,
        email=body.email,
        address=body.address,
        next_of_kin_name=body.next_of_kin_name,
        next_of_kin_phone=body.next_of_kin_phone,
        next_of_kin_relationship=body.next_of_kin_relationship,
        national_id=body.national_id,
        allergies=body.allergies,
        blood_group=body.blood_group,
        created_by=payload.get("preferred_username", ""),
    )
    return patient


@router.get("/search", response_model=PatientSearchResponse, summary="Search patients by name, national_id, or patient_number")
def search(
    query: str = Query(None, min_length=1),
    national_id: str = Query(None, min_length=1),
    patient_number: str = Query(None, min_length=1),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_any_role(["hospital_admin", "receptionist", "triage_nurse", "doctor"])),
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


@router.get("/{patient_id}", response_model=PatientResponse, summary="Get patient profile details by ID")
def get_patient(
    patient_id: str,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_any_role(["hospital_admin", "receptionist", "triage_nurse", "doctor"])),
):
    patient = get_patient_by_id(db, tenant_id, patient_id)
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return patient


@router.patch("/{patient_id}", response_model=PatientResponse, summary="Update patient profile details")
def update(
    patient_id: str,
    body: PatientUpdateRequest,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_any_role(["hospital_admin", "receptionist"])),
):
    patient = update_patient(
        db=db,
        hospital_id=tenant_id,
        patient_id=patient_id,
        full_name=body.full_name,
        date_of_birth=body.date_of_birth,
        gender=body.gender,
        phone_primary=body.phone_primary,
        phone_secondary=body.phone_secondary,
        email=body.email,
        address=body.address,
        next_of_kin_name=body.next_of_kin_name,
        next_of_kin_phone=body.next_of_kin_phone,
        next_of_kin_relationship=body.next_of_kin_relationship,
        national_id=body.national_id,
        allergies=body.allergies,
        blood_group=body.blood_group,
    )
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return patient


@router.delete("/{patient_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Soft-delete or remove a patient profile")
def remove_patient(
    patient_id: str,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_any_role(["hospital_admin", "receptionist"])),
):
    deleted = delete_patient(db, tenant_id, patient_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
