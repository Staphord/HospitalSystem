from sqlalchemy import Boolean, Column, Integer, String, Text

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
    mfa_secret = Column(String(255), nullable=True)
    mfa_enabled = Column(Boolean, nullable=False, default=False)
    backup_codes = Column(Text, nullable=True)
