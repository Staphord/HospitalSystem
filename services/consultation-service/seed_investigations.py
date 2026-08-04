import asyncio
import sys
import uuid
import datetime
from sqlalchemy import text

sys.path.insert(0, '/app')

async def seed_for_tenant(tenant_id: str):
    from app.db.tenant import _get_async_session_factory
    print(f"--- Seeding investigations for tenant: {tenant_id} ---")
    session_factory = await _get_async_session_factory(tenant_id)
    async with session_factory() as db:
        # Check existing patients, visits, consultations
        res_pat = await db.execute(text("SELECT id, full_name FROM patients LIMIT 10"))
        patients = res_pat.mappings().all()

        res_vis = await db.execute(text("SELECT visit_id, patient_id FROM visits LIMIT 10"))
        visits = res_vis.mappings().all()

        res_con = await db.execute(text("SELECT id, visit_id, patient_id FROM consultations LIMIT 10"))
        consultations = res_con.mappings().all()

        now = datetime.datetime.utcnow()
        random_tech_id = uuid.uuid4()

        if not consultations:
            print(f"No consultations found in tenant {tenant_id}. Creating fallback patient/visit/consultation for test requests.")
            pat_id = uuid.uuid4()
            vis_id = uuid.uuid4()
            con_id = uuid.uuid4()
            await db.execute(text("""
                INSERT INTO patients (id, hospital_id, patient_number, full_name, date_of_birth, gender)
                VALUES (:id, 'HOSP-001', 'P-10001', 'John Doe', '1985-05-15', 'male')
                ON CONFLICT DO NOTHING
            """), {"id": pat_id})
            await db.execute(text("""
                INSERT INTO visits (visit_id, patient_id, visit_number, visit_date, visit_type, status)
                VALUES (:visit_id, :patient_id, 'V-10001', CURRENT_DATE, 'outpatient', 'in_consultation')
                ON CONFLICT DO NOTHING
            """), {"visit_id": vis_id, "patient_id": pat_id})
            await db.execute(text("""
                INSERT INTO consultations (id, visit_id, patient_id, status, created_at)
                VALUES (:id, :visit_id, :patient_id, 'in_progress', NOW())
                ON CONFLICT DO NOTHING
            """), {"id": con_id, "visit_id": vis_id, "patient_id": pat_id})
            consultations = [{"id": con_id, "visit_id": vis_id, "patient_id": pat_id}]

        c0 = consultations[0]
        c1 = consultations[1 if len(consultations) > 1 else 0]
        c2 = consultations[2 if len(consultations) > 2 else 0]

        # ── 1. Pending STAT CBC
        req1_id = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO investigation_requests (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code, clinical_history, status, urgency, requested_by, requested_at, created_at)
            VALUES (:id, :consultation_id, :visit_id, :patient_id, :request_type, :test_name, :test_code, :clinical_history, :status, :urgency, :requested_by, :requested_at, :created_at)
        """), {
            "id": req1_id,
            "consultation_id": c0["id"],
            "visit_id": c0["visit_id"],
            "patient_id": c0["patient_id"],
            "request_type": "laboratory",
            "test_name": "Full Blood Count",
            "test_code": "L-FBC",
            "clinical_history": "Acute fever and fatigue — rule out infection",
            "status": "pending",
            "urgency": "stat",
            "requested_by": "Dr. Sarah Chen",
            "requested_at": now - datetime.timedelta(minutes=25),
            "created_at": now - datetime.timedelta(minutes=25)
        })

        # ── 2. Pending Urgent LFT
        req2_id = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO investigation_requests (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code, clinical_history, status, urgency, requested_by, requested_at, created_at)
            VALUES (:id, :consultation_id, :visit_id, :patient_id, :request_type, :test_name, :test_code, :clinical_history, :status, :urgency, :requested_by, :requested_at, :created_at)
        """), {
            "id": req2_id,
            "consultation_id": c1["id"],
            "visit_id": c1["visit_id"],
            "patient_id": c1["patient_id"],
            "request_type": "laboratory",
            "test_name": "Liver Function Tests",
            "test_code": "L-LFT",
            "clinical_history": "Abdominal discomfort & elevated enzymes",
            "status": "pending",
            "urgency": "urgent",
            "requested_by": "Dr. Amina Hassan",
            "requested_at": now - datetime.timedelta(hours=1),
            "created_at": now - datetime.timedelta(hours=1)
        })

        # ── 3. Completed Critical Result (HbA1c / U&E)
        req3_id = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO investigation_requests (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code, clinical_history, status, urgency, requested_by, requested_at, created_at)
            VALUES (:id, :consultation_id, :visit_id, :patient_id, :request_type, :test_name, :test_code, :clinical_history, :status, :urgency, :requested_by, :requested_at, :created_at)
        """), {
            "id": req3_id,
            "consultation_id": c2["id"],
            "visit_id": c2["visit_id"],
            "patient_id": c2["patient_id"],
            "request_type": "laboratory",
            "test_name": "Urea & Electrolytes",
            "test_code": "L-UE",
            "clinical_history": "Dehydration, high serum potassium suspected",
            "status": "completed",
            "urgency": "stat",
            "requested_by": "Dr. Sarah Chen",
            "requested_at": now - datetime.timedelta(hours=3),
            "created_at": now - datetime.timedelta(hours=3)
        })

        await db.execute(text("""
            INSERT INTO lab_results (result_id, request_id, visit_id, patient_id, specimen_type, result_value, unit, reference_range, is_critical, result_notes, performed_by, status, resulted_at)
            VALUES (:result_id, :request_id, :visit_id, :patient_id, :specimen_type, :result_value, :unit, :reference_range, :is_critical, :result_notes, :performed_by, :status, :resulted_at)
        """), {
            "result_id": uuid.uuid4(),
            "request_id": req3_id,
            "visit_id": c2["visit_id"],
            "patient_id": c2["patient_id"],
            "specimen_type": "Serum",
            "result_value": "K+: 6.8, Na+: 128, Creatinine: 240",
            "unit": "mmol/L",
            "reference_range": "K+: 3.5-5.0 | Na+: 135-145",
            "is_critical": True,
            "result_notes": "Critical Hyperkalemia detected! Doctor notified immediately.",
            "performed_by": "Tech Ali M.",
            "status": "verified",
            "resulted_at": now - datetime.timedelta(hours=1)
        })

        await db.commit()
        print(f"Successfully seeded investigation requests for tenant {tenant_id}!")

async def main():
    from app.db.master import get_master_db
    from sqlalchemy import text
    db = get_master_db()
    try:
        res = db.execute(text("SELECT tenant_id FROM tenants WHERE is_active = true"))
        tenants = [row[0] for row in res.fetchall()]
    finally:
        db.close()

    if not tenants:
        tenants = ['hosp-10f20e92']

    for t in tenants:
        await seed_for_tenant(t)

if __name__ == "__main__":
    asyncio.run(main())
