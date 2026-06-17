from sqlalchemy import Boolean, Column, Integer, String

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    keycloak_sub = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, index=True, nullable=True)
    full_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    role = Column(String, nullable=True)
    hospital_id = Column(String, index=True, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    force_password_change = Column(Boolean, default=False, nullable=False)
