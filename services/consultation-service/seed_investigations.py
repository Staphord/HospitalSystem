import asyncio
import sys
import uuid
import datetime
from sqlalchemy import text

sys.path.insert(0, '/app')

async def main():
    from app.db.tenant import _get_async_session_factory
    
    session_factory = await _get_async_session_factory('hosp-10f20e92')
    async with session_factory() as db:
        # Delete existing lab results, radiology reports, and investigation requests first to have a clean slate
        await db.execute(text("TRUNCATE TABLE lab_results, radiology_reports, investigation_requests CASCADE"))
        await db.commit()
        print("Cleared existing investigation tables.")

        now = datetime.datetime.utcnow()
        random_tech_id = uuid.uuid4()

        # ── 1. Critical Lab Result (abdul)
        req1_id = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO investigation_requests (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code, clinical_history, status, urgency, requested_by, requested_at, created_at)
            VALUES (:id, :consultation_id, :visit_id, :patient_id, :request_type, :test_name, :test_code, :clinical_history, :status, :urgency, :requested_by, :requested_at, :created_at)
        """), {
            "id": req1_id,
            "consultation_id": uuid.UUID('291dbf4a-e86b-49ed-b855-ab7fa6349c76'),
            "visit_id": uuid.UUID('fcfc3016-b3aa-4b0a-aaf3-f25c88673bc3'),
            "patient_id": uuid.UUID('619ac477-0a17-410d-86d3-85b811908cab'),
            "request_type": "laboratory",
            "test_name": "HbA1c",
            "test_code": "L-HbA1c",
            "clinical_history": "Uncontrolled diabetes management",
            "status": "completed",
            "urgency": "urgent",
            "requested_by": "Dr. Amina",
            "requested_at": now - datetime.timedelta(hours=4),
            "created_at": now - datetime.timedelta(hours=4)
        })

        await db.execute(text("""
            INSERT INTO lab_results (result_id, request_id, visit_id, patient_id, specimen_type, result_value, unit, reference_range, is_critical, result_notes, performed_by, status, resulted_at)
            VALUES (:result_id, :request_id, :visit_id, :patient_id, :specimen_type, :result_value, :unit, :reference_range, :is_critical, :result_notes, :performed_by, :status, :resulted_at)
        """), {
            "result_id": uuid.uuid4(),
            "request_id": req1_id,
            "visit_id": uuid.UUID('fcfc3016-b3aa-4b0a-aaf3-f25c88673bc3'),
            "patient_id": uuid.UUID('619ac477-0a17-410d-86d3-85b811908cab'),
            "specimen_type": "Whole Blood",
            "result_value": "10.2",
            "unit": "%",
            "reference_range": "Target: < 7.0% for diabetic patients",
            "is_critical": True,
            "result_notes": "Result significantly above therapeutic target. Immediate clinical review recommended.",
            "performed_by": "Lab Tech Salim",
            "status": "resulted",
            "resulted_at": now - datetime.timedelta(hours=2)
        })

        # ── 2. Ready Radiology Result (juma)
        req2_id = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO investigation_requests (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code, clinical_history, status, urgency, requested_by, requested_at, created_at)
            VALUES (:id, :consultation_id, :visit_id, :patient_id, :request_type, :test_name, :test_code, :clinical_history, :status, :urgency, :requested_by, :requested_at, :created_at)
        """), {
            "id": req2_id,
            "consultation_id": uuid.UUID('c4754495-3a53-407c-9490-edf4591125cf'),
            "visit_id": uuid.UUID('bfa70102-0472-4710-8d75-a79754a98466'),
            "patient_id": uuid.UUID('7a48ecdb-4b67-48bc-bfc0-13786f2a7460'),
            "request_type": "radiology",
            "test_name": "Chest X-Ray",
            "test_code": "R-CXR",
            "clinical_history": "Productive cough, chest pain, fever",
            "status": "completed",
            "urgency": "routine",
            "requested_by": "Dr. Baraka",
            "requested_at": now - datetime.timedelta(hours=6),
            "created_at": now - datetime.timedelta(hours=6)
        })

        await db.execute(text("""
            INSERT INTO radiology_reports (report_id, request_id, visit_id, patient_id, modality, body_part, findings, impression, status, performed_by, reported_by, reported_at, created_at, updated_at)
            VALUES (:report_id, :request_id, :visit_id, :patient_id, CAST(:modality AS modality_enum), :body_part, :findings, :impression, CAST(:status AS report_status_enum), :performed_by, :reported_by, :reported_at, :created_at, :updated_at)
        """), {
            "report_id": uuid.uuid4(),
            "request_id": req2_id,
            "visit_id": uuid.UUID('bfa70102-0472-4710-8d75-a79754a98466'),
            "patient_id": uuid.UUID('7a48ecdb-4b67-48bc-bfc0-13786f2a7460'),
            "modality": "xray",
            "body_part": "Chest PA",
            "findings": "Mild peribronchial thickening bilaterally. No consolidation, effusion or pneumothorax.",
            "impression": "No acute cardiopulmonary pathology",
            "status": "reported",
            "performed_by": random_tech_id,
            "reported_by": random_tech_id,
            "reported_at": now - datetime.timedelta(hours=3),
            "created_at": now - datetime.timedelta(hours=3),
            "updated_at": now - datetime.timedelta(hours=3)
        })

        # ── 3. Pending Lab Result (valli6)
        await db.execute(text("""
            INSERT INTO investigation_requests (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code, clinical_history, status, urgency, requested_by, requested_at, created_at)
            VALUES (:id, :consultation_id, :visit_id, :patient_id, :request_type, :test_name, :test_code, :clinical_history, :status, :urgency, :requested_by, :requested_at, :created_at)
        """), {
            "id": uuid.uuid4(),
            "consultation_id": uuid.UUID('e76e0f03-e022-4393-8ee2-813e7c26a6b7'),
            "visit_id": uuid.UUID('9f344f3b-83b8-46c3-97d0-11e20386e0b6'),
            "patient_id": uuid.UUID('44d4ba85-fcb4-4ebd-9ce3-f7db0fc8781c'),
            "request_type": "laboratory",
            "test_name": "Full Blood Count",
            "test_code": "L-FBC",
            "clinical_history": "Routine screen, fatigue",
            "status": "pending",
            "urgency": "routine",
            "requested_by": "Dr. Amina",
            "requested_at": now - datetime.timedelta(minutes=30),
            "created_at": now - datetime.timedelta(minutes=30)
        })

        # ── 4. Ready Lab Result (another)
        req4_id = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO investigation_requests (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code, clinical_history, status, urgency, requested_by, requested_at, created_at)
            VALUES (:id, :consultation_id, :visit_id, :patient_id, :request_type, :test_name, :test_code, :clinical_history, :status, :urgency, :requested_by, :requested_at, :created_at)
        """), {
            "id": req4_id,
            "consultation_id": uuid.UUID('e3245012-b265-4f95-adb1-05ff70dcd1f6'),
            "visit_id": uuid.UUID('455f72f4-6721-4b23-8f63-95c291939cd8'),
            "patient_id": uuid.UUID('f5c1d927-1d9d-4fbb-a0d3-3985050689e0'),
            "request_type": "laboratory",
            "test_name": "Urea & Electrolytes",
            "test_code": "L-UE",
            "clinical_history": "Pre-op evaluation",
            "status": "completed",
            "urgency": "routine",
            "requested_by": "Dr. Baraka",
            "requested_at": now - datetime.timedelta(hours=2),
            "created_at": now - datetime.timedelta(hours=2)
        })

        await db.execute(text("""
            INSERT INTO lab_results (result_id, request_id, visit_id, patient_id, specimen_type, result_value, unit, reference_range, is_critical, result_notes, performed_by, status, resulted_at)
            VALUES (:result_id, :request_id, :visit_id, :patient_id, :specimen_type, :result_value, :unit, :reference_range, :is_critical, :result_notes, :performed_by, :status, :resulted_at)
        """), {
            "result_id": uuid.uuid4(),
            "request_id": req4_id,
            "visit_id": uuid.UUID('455f72f4-6721-4b23-8f63-95c291939cd8'),
            "patient_id": uuid.UUID('f5c1d927-1d9d-4fbb-a0d3-3985050689e0'),
            "specimen_type": "Serum",
            "result_value": "Na: 138, K: 3.8, Creat: 88",
            "unit": "mmol/L",
            "reference_range": "Na 135-145 | K 3.5-5.0 | Creat 60-110",
            "is_critical": False,
            "result_notes": "All parameters within normal limits.",
            "performed_by": "Lab Tech Salim",
            "status": "resulted",
            "resulted_at": now - datetime.timedelta(minutes=45)
        })

        # ── 5. Critical Radiology Result (abdul)
        req5_id = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO investigation_requests (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code, clinical_history, status, urgency, requested_by, requested_at, created_at)
            VALUES (:id, :consultation_id, :visit_id, :patient_id, :request_type, :test_name, :test_code, :clinical_history, :status, :urgency, :requested_by, :requested_at, :created_at)
        """), {
            "id": req5_id,
            "consultation_id": uuid.UUID('291dbf4a-e86b-49ed-b855-ab7fa6349c76'),
            "visit_id": uuid.UUID('fcfc3016-b3aa-4b0a-aaf3-f25c88673bc3'),
            "patient_id": uuid.UUID('619ac477-0a17-410d-86d3-85b811908cab'),
            "request_type": "radiology",
            "test_name": "CT Head",
            "test_code": "R-CTH",
            "clinical_history": "Fall, confusion, progressive headache",
            "status": "completed",
            "urgency": "stat",
            "requested_by": "Dr. Amina",
            "requested_at": now - datetime.timedelta(hours=3),
            "created_at": now - datetime.timedelta(hours=3)
        })

        await db.execute(text("""
            INSERT INTO radiology_reports (report_id, request_id, visit_id, patient_id, modality, body_part, findings, impression, status, performed_by, reported_by, reported_at, created_at, updated_at)
            VALUES (:report_id, :request_id, :visit_id, :patient_id, CAST(:modality AS modality_enum), :body_part, :findings, :impression, CAST(:status AS report_status_enum), :performed_by, :reported_by, :reported_at, :created_at, :updated_at)
        """), {
            "report_id": uuid.uuid4(),
            "request_id": req5_id,
            "visit_id": uuid.UUID('fcfc3016-b3aa-4b0a-aaf3-f25c88673bc3'),
            "patient_id": uuid.UUID('619ac477-0a17-410d-86d3-85b811908cab'),
            "modality": "ct",
            "body_part": "Head",
            "findings": "Right-sided acute subdural hematoma ~8 mm. Midline shift of 4 mm to the left.",
            "impression": "Acute subdural hematoma with mass effect. URGENT neurosurgery consultation recommended.",
            "status": "reported",
            "performed_by": random_tech_id,
            "reported_by": random_tech_id,
            "reported_at": now - datetime.timedelta(hours=1),
            "created_at": now - datetime.timedelta(hours=1),
            "updated_at": now - datetime.timedelta(hours=1)
        })

        await db.commit()
        print("Successfully seeded realistic investigation requests and results!")

asyncio.run(main())
