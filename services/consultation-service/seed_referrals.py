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
        # Clear existing referrals first
        await db.execute(text("TRUNCATE TABLE referrals CASCADE"))
        await db.commit()
        print("Cleared existing referrals.")

        now = datetime.datetime.utcnow()

        # ── 1. Pending Internal Referral (abdul)
        await db.execute(text("""
            INSERT INTO referrals (id, patient_id, visit_id, referred_to, type, reason, status, urgency, category, department, preferred_doctor, referred_at, created_at, updated_at)
            VALUES (:id, :patient_id, :visit_id, :referred_to, :type, :reason, :status, :urgency, :category, :department, :preferred_doctor, :referred_at, :created_at, :updated_at)
        """), {
            "id": uuid.uuid4(),
            "patient_id": uuid.UUID('619ac477-0a17-410d-86d3-85b811908cab'),
            "visit_id": uuid.UUID('fcfc3016-b3aa-4b0a-aaf3-f25c88673bc3'),
            "referred_to": "Cardiology Dept.",
            "type": "internal",
            "reason": "Persistent chest pain on exertion with atypical ECG changes. Request cardiology review for risk stratification and further workup.",
            "status": "pending",
            "urgency": "urgent",
            "category": "general",
            "department": "Cardiology",
            "preferred_doctor": "Dr. Miller",
            "referred_at": now - datetime.timedelta(hours=24),
            "created_at": now - datetime.timedelta(hours=24),
            "updated_at": now - datetime.timedelta(hours=24)
        })

        # ── 2. Accepted External Referral (juma)
        await db.execute(text("""
            INSERT INTO referrals (id, patient_id, visit_id, referred_to, type, reason, status, urgency, category, hospital_name, external_doctor, contact_number, referred_at, responded_at, created_at, updated_at)
            VALUES (:id, :patient_id, :visit_id, :referred_to, :type, :reason, :status, :urgency, :category, :hospital_name, :external_doctor, :contact_number, :referred_at, :responded_at, :created_at, :updated_at)
        """), {
            "id": uuid.uuid4(),
            "patient_id": uuid.UUID('7a48ecdb-4b67-48bc-bfc0-13786f2a7460'),
            "visit_id": uuid.UUID('bfa70102-0472-4710-8d75-a79754a98466'),
            "referred_to": "General Hospital East",
            "type": "external",
            "reason": "Advanced MRI imaging needed for suspected ligament tear not available on-site. Patient consented to external transfer.",
            "status": "accepted",
            "urgency": "routine",
            "category": "lab-imaging",
            "hospital_name": "General Hospital East",
            "external_doctor": "Dr. Patel",
            "contact_number": "+255 712 345 678",
            "referred_at": now - datetime.timedelta(days=2),
            "responded_at": now - datetime.timedelta(days=1),
            "created_at": now - datetime.timedelta(days=2),
            "updated_at": now - datetime.timedelta(days=1)
        })

        # ── 3. Declined Internal Referral (valli6)
        await db.execute(text("""
            INSERT INTO referrals (id, patient_id, visit_id, referred_to, type, reason, status, urgency, category, department, decline_reason, referred_at, responded_at, created_at, updated_at)
            VALUES (:id, :patient_id, :visit_id, :referred_to, :type, :reason, :status, :urgency, :category, :department, :decline_reason, :referred_at, :responded_at, :created_at, :updated_at)
        """), {
            "id": uuid.uuid4(),
            "patient_id": uuid.UUID('44d4ba85-fcb4-4ebd-9ce3-f7db0fc8781c'),
            "visit_id": uuid.UUID('9f344f3b-83b8-46c3-97d0-11e20386e0b6'),
            "referred_to": "Physiotherapy",
            "type": "internal",
            "reason": "Post-operative recovery plan following ACL repair. Requires structured physiotherapy programme.",
            "status": "declined",
            "urgency": "routine",
            "category": "follow-up",
            "department": "Physiotherapy",
            "decline_reason": "Physiotherapy capacity full this month. Resubmit with revised timeline.",
            "referred_at": now - datetime.timedelta(days=3),
            "responded_at": now - datetime.timedelta(days=2),
            "created_at": now - datetime.timedelta(days=3),
            "updated_at": now - datetime.timedelta(days=2)
        })

        # ── 4. Completed External Referral (another)
        await db.execute(text("""
            INSERT INTO referrals (id, patient_id, visit_id, referred_to, type, reason, status, urgency, category, hospital_name, external_doctor, contact_number, referred_at, responded_at, created_at, updated_at)
            VALUES (:id, :patient_id, :visit_id, :referred_to, :type, :reason, :status, :urgency, :category, :hospital_name, :external_doctor, :contact_number, :referred_at, :responded_at, :created_at, :updated_at)
        """), {
            "id": uuid.uuid4(),
            "patient_id": uuid.UUID('f5c1d927-1d9d-4fbb-a0d3-3985050689e0'),
            "visit_id": uuid.UUID('455f72f4-6721-4b23-8f63-95c291939cd8'),
            "referred_to": "Radiology Centre Mwanza",
            "type": "external",
            "reason": "Specialist CT angiography not available locally. External imaging referral.",
            "status": "completed",
            "urgency": "urgent",
            "category": "lab-imaging",
            "hospital_name": "Radiology Centre Mwanza",
            "external_doctor": "Dr. Kimaro",
            "contact_number": "+255 768 999 000",
            "referred_at": now - datetime.timedelta(days=5),
            "responded_at": now - datetime.timedelta(days=4),
            "created_at": now - datetime.timedelta(days=5),
            "updated_at": now - datetime.timedelta(days=4)
        })

        await db.commit()
        print("Successfully seeded referrals!")

asyncio.run(main())
