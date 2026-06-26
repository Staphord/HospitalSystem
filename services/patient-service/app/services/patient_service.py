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
    phone_primary: str,
    phone_secondary: Optional[str] = None,
    email: Optional[str] = None,
    address: Optional[str] = None,
    next_of_kin_name: Optional[str] = None,
    next_of_kin_phone: Optional[str] = None,
    next_of_kin_relationship: Optional[str] = None,
    national_id: Optional[str] = None,
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
        phone_primary=phone_primary,
        phone_secondary=phone_secondary,
        email=email,
        address=address,
        next_of_kin_name=next_of_kin_name,
        next_of_kin_phone=next_of_kin_phone,
        next_of_kin_relationship=next_of_kin_relationship,
        national_id=national_id,
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
                TenantPatient.phone_primary.ilike(search),
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


def update_patient(
    db: Session,
    hospital_id: str,
    patient_id: str,
    full_name: Optional[str] = None,
    date_of_birth: Optional[date_type] = None,
    gender: Optional[str] = None,
    phone_primary: Optional[str] = None,
    phone_secondary: Optional[str] = None,
    email: Optional[str] = None,
    address: Optional[str] = None,
    next_of_kin_name: Optional[str] = None,
    next_of_kin_phone: Optional[str] = None,
    next_of_kin_relationship: Optional[str] = None,
    national_id: Optional[str] = None,
    allergies: Optional[str] = None,
    blood_group: Optional[str] = None,
) -> Optional[TenantPatient]:
    patient = get_patient_by_id(db, hospital_id, patient_id)
    if not patient:
        return None

    if full_name is not None:
        patient.full_name = full_name
    if date_of_birth is not None:
        patient.date_of_birth = date_of_birth
    if gender is not None:
        patient.gender = gender
    if phone_primary is not None:
        patient.phone_primary = phone_primary
    if phone_secondary is not None:
        patient.phone_secondary = phone_secondary
    if email is not None:
        patient.email = email
    if address is not None:
        patient.address = address
    if next_of_kin_name is not None:
        patient.next_of_kin_name = next_of_kin_name
    if next_of_kin_phone is not None:
        patient.next_of_kin_phone = next_of_kin_phone
    if next_of_kin_relationship is not None:
        patient.next_of_kin_relationship = next_of_kin_relationship
    if national_id is not None:
        patient.national_id = national_id
    if allergies is not None:
        patient.allergies = allergies
    if blood_group is not None:
        patient.blood_group = blood_group

    db.commit()
    db.refresh(patient)
    return patient


def get_patient_count(db: Session, hospital_id: str) -> int:
    return (
        db.query(func.count(TenantPatient.id))
        .filter(TenantPatient.hospital_id == hospital_id)
        .scalar()
    )
