from enum import Enum
from pydantic import BaseModel


class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    HOSPITAL_ADMIN = "hospital_admin"
    RECEPTIONIST = "receptionist"
    TRIAGE_NURSE = "triage_nurse"
    DOCTOR = "doctor"
    LAB_TECHNICIAN = "lab_technician"
    RADIOGRAPHER = "radiographer"
    PHARMACIST = "pharmacist"
    CASHIER = "cashier"
    PATIENT = "patient"
    HOSPITAL_USER = "hospital_user"


class BaseResponse(BaseModel):
    success: bool = True
    message: str | None = None
