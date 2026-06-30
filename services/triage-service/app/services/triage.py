import logging
import httpx
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.triage import TriageAssessment
from app.core.config import settings

logger = logging.getLogger("triage_service")


def suggest_category_from_vitals(vitals: Dict[str, Any]) -> Tuple[str, str]:
    """Suggest triage category based on vitals."""
    # Check SpO2 (oxygen saturation)
    spo2 = vitals.get("oxygen_saturation")
    if spo2 is not None:
        if spo2 < 90:
            return "emergency", "SpO2 is critically low (< 90%)"
        elif spo2 <= 94:
            return "urgent", "SpO2 is low (90-94%)"
            
    # Check Respiratory Rate
    rr = vitals.get("respiratory_rate")
    if rr is not None:
        if rr > 30 or rr < 8:
            return "emergency", f"Respiratory rate is critical ({rr} breaths/min)"
        elif rr >= 25:
            return "urgent", f"Respiratory rate is high ({rr} breaths/min)"
        elif rr >= 21:
            return "semi_urgent", f"Respiratory rate is elevated ({rr} breaths/min)"

    # Check Pulse
    pulse = vitals.get("pulse")
    if pulse is not None:
        if pulse > 130 or pulse < 40:
            return "emergency", f"Pulse rate is critical ({pulse} bpm)"
        elif pulse > 110 or pulse < 50:
            return "urgent", f"Pulse rate is high/low ({pulse} bpm)"
        elif pulse > 100 or pulse < 60:
            return "semi_urgent", f"Pulse rate is elevated/low ({pulse} bpm)"

    # Check Temp
    temp = vitals.get("temperature")
    if temp is not None:
        if temp > 40.0 or temp < 35.0:
            return "emergency", f"Temperature is critical ({temp}°C)"
        elif temp > 38.5 or temp < 36.0:
            return "urgent", f"Temperature is high/low ({temp}°C)"
        elif temp >= 37.6:
            return "semi_urgent", f"Temperature is elevated ({temp}°C)"

    # Check Blood Pressure (systolic)
    bp = vitals.get("blood_pressure")
    if bp:
        try:
            sys_val = int(bp.split("/")[0])
            if sys_val > 220 or sys_val < 80:
                return "emergency", f"Blood pressure is critical ({bp})"
            elif sys_val > 180 or sys_val < 90:
                return "urgent", f"Blood pressure is high/low ({bp})"
            elif sys_val > 140:
                return "semi_urgent", f"Blood pressure is elevated ({bp})"
        except Exception:
            pass

    return "non_urgent", "Vitals are within normal ranges"


async def record_triage_assessment(
    db: AsyncSession,
    assessment_data: Dict[str, Any],
    created_by: Optional[str] = None,
    auth_header: Optional[str] = None
) -> TriageAssessment:
    visit_id = assessment_data["visit_id"]
    triage_category = assessment_data["triage_category"]
    
    # Normalize triage category for the queue (lowercase, and semi-urgent is semi_urgent)
    norm_category = triage_category.lower().replace("-", "_")
    
    # 1. Check if triage assessment already exists for this visit
    stmt = select(TriageAssessment).where(TriageAssessment.visit_id == visit_id)
    result = await db.execute(stmt)
    db_assessment = result.scalars().first()
    
    if db_assessment:
        # Update existing
        for key, value in assessment_data.items():
            setattr(db_assessment, key, value)
        db_assessment.created_by = created_by
    else:
        # Create new
        db_assessment = TriageAssessment(
            **assessment_data,
            created_by=created_by
        )
        db.add(db_assessment)
        
    await db.commit()
    await db.refresh(db_assessment)
    
    # 2. Call visit-service to complete triage and update queues
    headers = {}
    if auth_header:
        headers["Authorization"] = auth_header
    
    visit_svc_payload = {
        "priority": norm_category
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        url = f"{settings.visit_service_url}/api/v1/visits/{visit_id}/triage-complete"
        try:
            resp = await client.post(url, json=visit_svc_payload, headers=headers)
            if resp.status_code >= 400:
                logger.error(f"Failed to complete triage in visit-service: {resp.status_code} - {resp.text}")
                resp.raise_for_status()
        except Exception as e:
            logger.exception(f"Exception when calling visit-service: {e}")
            raise e
            
    return db_assessment


async def get_triage_summary(db: AsyncSession, visit_id: str) -> Optional[TriageAssessment]:
    stmt = select(TriageAssessment).where(TriageAssessment.visit_id == visit_id)
    result = await db.execute(stmt)
    return result.scalars().first()
