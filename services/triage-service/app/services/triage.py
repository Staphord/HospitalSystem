import logging
import httpx
import uuid
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.triage import TriageAssessment, Visit, Patient, Queue
from app.models.user import User
from app.core.config import settings
from app.exceptions import NotFoundError, ConflictError

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
    pulse = vitals.get("pulse_rate")
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
    sys_val = vitals.get("blood_pressure_systolic")
    dia_val = vitals.get("blood_pressure_diastolic")
    if sys_val is not None:
        bp_str = f"{sys_val}/{dia_val}" if dia_val is not None else f"{sys_val}/?"
        if sys_val > 220 or sys_val < 80:
            return "emergency", f"Blood pressure is critical ({bp_str})"
        elif sys_val > 180 or sys_val < 90:
            return "urgent", f"Blood pressure is high/low ({bp_str})"
        elif sys_val > 140:
            return "semi_urgent", f"Blood pressure is elevated ({bp_str})"

    return "non_urgent", "Vitals are within normal ranges"


async def record_triage_assessment(
    db: AsyncSession,
    assessment_data: Dict[str, Any],
    created_by: Optional[str] = None,
    auth_header: Optional[str] = None,
    tenant_id: Optional[str] = None
) -> Dict[str, Any]:
    visit_id = assessment_data["visit_id"]
    triage_category = assessment_data["triage_category"]
    
    # 1. Fetch visit to validate existence and status
    visit_stmt = select(Visit).where(Visit.visit_id == visit_id)
    visit_res = await db.execute(visit_stmt)
    db_visit = visit_res.scalars().first()
    
    if not db_visit:
        raise NotFoundError("Visit not found")
        
    if db_visit.status == "skipped":
        raise ConflictError("This patient was skipped and cannot be assessed.")
        
    if db_visit.status != "registered":
        raise ConflictError("Visit has already been triaged or is beyond triage stage.")
        
    # 2. Check no existing triage assessments row
    exist_stmt = select(TriageAssessment).where(TriageAssessment.visit_id == visit_id)
    exist_res = await db.execute(exist_stmt)
    if exist_res.scalars().first():
        raise ConflictError("A triage assessment already exists for this visit.")
        
    # Normalize triage category for downstream
    norm_category = triage_category.lower().replace("-", "_")
    
    # 3. Create new assessment and add it to session
    triage_nurse_id = uuid.UUID(created_by) if created_by else uuid.uuid4()
    
    new_assessment = TriageAssessment(
        visit_id=visit_id,
        patient_id=uuid.UUID(str(db_visit.patient_id)),
        triage_nurse_id=triage_nurse_id,
        blood_pressure_systolic=assessment_data.get("blood_pressure_systolic"),
        blood_pressure_diastolic=assessment_data.get("blood_pressure_diastolic"),
        temperature=assessment_data.get("temperature"),
        pulse_rate=assessment_data.get("pulse_rate"),
        oxygen_saturation=assessment_data.get("oxygen_saturation"),
        respiratory_rate=assessment_data.get("respiratory_rate"),
        weight_kg=assessment_data.get("weight_kg"),
        chief_complaint=assessment_data["chief_complaint"],
        complaint_code=assessment_data.get("complaint_code"),
        triage_category=triage_category,
        triage_notes=assessment_data.get("triage_notes"),
        created_by=str(triage_nurse_id),
        assessed_at=datetime.utcnow()
    )
    db.add(new_assessment)
    await db.flush()  # Generate PK local to session but keep transaction open
    
    # 4. Call visit-service to complete triage and update queues
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
            
    # 5. Fetch doctor queue entry created by visit-service from same tenant DB
    doctor_queue_stmt = select(Queue).where(
        Queue.visit_id == visit_id,
        Queue.queue_type == "doctor",
        Queue.status == "waiting"
    )
    q_res = await db.execute(doctor_queue_stmt)
    db_queue = q_res.scalars().first()
    
    # 6. Commit local transaction
    await db.commit()
    await db.refresh(new_assessment)
    
    # 7. Publish triage.completed event to RabbitMQ
    if tenant_id:
        try:
            from app.events.publisher import publish_triage_completed
            await publish_triage_completed(
                triage_id=str(new_assessment.triage_id),
                visit_id=str(visit_id),
                tenant_id=tenant_id
            )
        except Exception as rabbit_err:
            logger.error(f"Failed to publish triage.completed event: {rabbit_err}")
            
    return {
        "triage_id": new_assessment.triage_id,
        "visit_id": new_assessment.visit_id,
        "patient_id": new_assessment.patient_id,
        "triage_nurse_id": new_assessment.triage_nurse_id,
        "vitals": {
            "blood_pressure_systolic": new_assessment.blood_pressure_systolic,
            "blood_pressure_diastolic": new_assessment.blood_pressure_diastolic,
            "temperature": new_assessment.temperature,
            "pulse_rate": new_assessment.pulse_rate,
            "oxygen_saturation": new_assessment.oxygen_saturation,
            "respiratory_rate": new_assessment.respiratory_rate,
            "weight_kg": new_assessment.weight_kg
        },
        "chief_complaint": new_assessment.chief_complaint,
        "complaint_code": new_assessment.complaint_code,
        "triage_category": new_assessment.triage_category,
        "triage_notes": new_assessment.triage_notes,
        "assessed_at": new_assessment.assessed_at,
        "doctor_queue_entry": {
            "queue_id": db_queue.queue_id,
            "queue_type": db_queue.queue_type,
            "priority": db_queue.priority,
            "status": db_queue.status
        } if db_queue else None
    }


async def get_triage_summary(db: AsyncSession, visit_id: str) -> Optional[Dict[str, Any]]:
    # Get triage assessment
    stmt = select(TriageAssessment).where(TriageAssessment.visit_id == visit_id)
    res = await db.execute(stmt)
    assessment = res.scalars().first()
    if not assessment:
        return None
        
    # Get patient
    patient_stmt = select(Patient).where(Patient.id == assessment.patient_id)
    pat_res = await db.execute(patient_stmt)
    patient = pat_res.scalars().first()
    
    # Get nurse
    nurse_stmt = select(User).where(User.keycloak_sub == str(assessment.triage_nurse_id))
    nurse_res = await db.execute(nurse_stmt)
    nurse = nurse_res.scalars().first()
    
    return {
        "triage_id": assessment.triage_id,
        "visit_id": assessment.visit_id,
        "patient": {
            "patient_id": assessment.patient_id,
            "full_name": patient.full_name if patient else "Unknown",
            "date_of_birth": patient.date_of_birth if patient else date.today(),
            "gender": patient.gender if patient else "unknown"
        },
        "triage_nurse": {
            "user_id": assessment.triage_nurse_id,
            "full_name": nurse.full_name if nurse else "Nurse"
        },
        "vitals": {
            "blood_pressure_systolic": assessment.blood_pressure_systolic,
            "blood_pressure_diastolic": assessment.blood_pressure_diastolic,
            "temperature": assessment.temperature,
            "pulse_rate": assessment.pulse_rate,
            "oxygen_saturation": assessment.oxygen_saturation,
            "respiratory_rate": assessment.respiratory_rate,
            "weight_kg": assessment.weight_kg
        },
        "chief_complaint": assessment.chief_complaint,
        "complaint_code": assessment.complaint_code,
        "triage_category": assessment.triage_category,
        "triage_notes": assessment.triage_notes,
        "assessed_at": assessment.assessed_at
    }
