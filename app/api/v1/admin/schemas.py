from pydantic import BaseModel, EmailStr, Field

ALLOWED_HOSPITAL_ROLES = r"^(hospital_admin|hospital_user|nurse|clinician|doctor|patient)$"


class HospitalUserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    email: EmailStr
    full_name: str = Field(default="", max_length=255)
    role: str = Field(default="hospital_user", pattern=ALLOWED_HOSPITAL_ROLES)


class HospitalUserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, pattern=ALLOWED_HOSPITAL_ROLES)


class HospitalUserOut(BaseModel):
    keycloak_sub: str
    username: str | None
    full_name: str | None
    email: str | None
    role: str | None
    hospital_id: str | None

    class Config:
        from_attributes = True
