import uuid
from datetime import date as date_type
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.patient import TenantPatient
from app.services.patient_number import generate_patient_number


def register_patient(
    db: Session,
    hospital_id: str,
    full_name: str,
    date_of_birth: date_type,
    gender: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    address: Optional[str] = None,
    emergency_contact_name: Optional[str] = None,
    emergency_contact_phone: Optional[str] = None,
    national_id: Optional[str] = None,
    medical_history: Optional[str] = None,
    allergies: Optional[str] = None,
    blood_group: Optional[str] = None,
    created_by: Optional[str] = None,
) -> TenantPatient:
    patient_number = generate_patient_number(db, hospital_id)

    patient = TenantPatient(
        hospital_id=hospital_id,
        patient_number=patient_number,
        full_name=full_name,
        date_of_birth=date_of_birth,
        gender=gender,
        phone=phone,
        email=email,
        address=address,
        emergency_contact_name=emergency_contact_name,
        emergency_contact_phone=emergency_contact_phone,
        national_id=national_id,
        medical_history=medical_history,
        allergies=allergies,
        blood_group=blood_group,
        created_by=created_by,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def search_patients(
    db: Session,
    hospital_id: str,
    query: Optional[str] = None,
    national_id: Optional[str] = None,
    patient_number: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> list[TenantPatient]:
    filters = [TenantPatient.hospital_id == hospital_id]

    if patient_number:
        filters.append(TenantPatient.patient_number == patient_number)
    elif national_id:
        filters.append(TenantPatient.national_id == national_id)
    elif query:
        search = f"%{query}%"
        filters.append(
            or_(
                TenantPatient.full_name.ilike(search),
                TenantPatient.patient_number.ilike(search),
                TenantPatient.phone.ilike(search),
                TenantPatient.email.ilike(search),
                TenantPatient.national_id.ilike(search),
            )
        )

    return (
        db.query(TenantPatient)
        .filter(*filters)
        .order_by(TenantPatient.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_patient_by_id(db: Session, hospital_id: str, patient_id: str) -> Optional[TenantPatient]:
    try:
        pid = uuid.UUID(patient_id)
    except ValueError:
        return None
    return (
        db.query(TenantPatient)
        .filter(
            TenantPatient.id == pid,
            TenantPatient.hospital_id == hospital_id,
        )
        .first()
    )


def delete_patient(db: Session, hospital_id: str, patient_id: str) -> bool:
    patient = get_patient_by_id(db, hospital_id, patient_id)
    if not patient:
        return False
    db.delete(patient)
    db.commit()
    return True


def get_patient_count(db: Session, hospital_id: str) -> int:
    return (
        db.query(func.count(TenantPatient.id))
        .filter(TenantPatient.hospital_id == hospital_id)
        .scalar()
    )
