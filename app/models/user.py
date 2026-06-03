from sqlalchemy import Column, Integer, String

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    keycloak_sub = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, nullable=True)
    hospital_id = Column(String, index=True, nullable=True)
