"""Pydantic schemas for orchestrated reception endpoints."""

from typing import Any, Optional

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str


class CombinedRegisterAndVisitResponse(BaseModel):
    patient: dict[str, Any]
    visit: dict[str, Any]
