from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime

class TriageAssessmentCreate(BaseModel):
    visit_id: UUID
    patient_id: str = Field(..., max_length=50)
    
    # Vitals
    blood_pressure: Optional[str] = Field(None, max_length=20, pattern=r"^\d+/\d+$", description="e.g. 120/80")
    temperature: Optional[float] = Field(None, ge=30.0, le=45.0, description="Celsius")
    pulse: Optional[int] = Field(None, ge=20, le=250, description="bpm")
    oxygen_saturation: Optional[float] = Field(None, ge=10.0, le=100.0, description="% SpO2")
    respiratory_rate: Optional[int] = Field(None, ge=4, le=60, description="breaths/min")
    weight: Optional[float] = Field(None, ge=0.1, le=500.0, description="kg")
    
    # Complaints
    presenting_complaint: Optional[str] = None
    structured_complaint: Optional[str] = Field(None, max_length=255)
    
    # Category
    triage_category: str = Field(..., description="emergency, urgent, semi_urgent, non_urgent")
    notes: Optional[str] = None


class TriageAssessmentResponse(BaseModel):
    id: UUID
    visit_id: UUID
    patient_id: str
    
    blood_pressure: Optional[str] = None
    temperature: Optional[float] = None
    pulse: Optional[int] = None
    oxygen_saturation: Optional[float] = None
    respiratory_rate: Optional[int] = None
    weight: Optional[float] = None
    
    presenting_complaint: Optional[str] = None
    structured_complaint: Optional[str] = None
    triage_category: str
    notes: Optional[str] = None
    
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None

    class Config:
        from_attributes = True


class VitalsInput(BaseModel):
    blood_pressure: Optional[str] = None
    temperature: Optional[float] = None
    pulse: Optional[int] = None
    oxygen_saturation: Optional[float] = None
    respiratory_rate: Optional[int] = None
    weight: Optional[float] = None


class TriageCategorySuggestionResponse(BaseModel):
    suggested_category: str
    reason: str
