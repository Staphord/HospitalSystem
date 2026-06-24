import uuid

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class SuperAdmin(Base):
    __tablename__ = "super_admins"

    super_admin_id = Column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(150), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(200), nullable=False)
    role = Column(String(50), nullable=False, default="super_admin")
    mfa_secret = Column(String(100), nullable=False)
    mfa_enabled = Column(Boolean, nullable=False, default=False)
    backup_codes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default="now()")
