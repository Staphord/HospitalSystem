from pydantic import BaseModel, EmailStr

class UserUpdate(BaseModel):
    username: str
    email: EmailStr
    full_name: str | None = None

class PasswordChange(BaseModel):
    current_password: str
    new_password: str
