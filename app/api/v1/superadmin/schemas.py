from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    username: str
    password: str
    email: EmailStr
    role: str = "hospital_user"
    hospital_id: str | None = None


class UserOut(BaseModel):
    keycloak_sub: str
    username: str
    email: str | None
    hospital_id: str | None

    class Config:
        from_attributes = True


class UserDelete(BaseModel):
    username: str
