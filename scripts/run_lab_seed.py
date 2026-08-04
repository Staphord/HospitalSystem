#!/usr/bin/env python3
"""
Seed realistic lab investigation requests into the correct tenant database.
Run from any directory — discovers tenant ID automatically.
"""
import asyncio
import uuid
import datetime
import sys

MASTER_DB_URL = "postgresql://postgres:12345678@localhost:5432/hospital_master"
TENANT_DB_TEMPLATE = "postgresql://postgres:12345678@localhost:5432/tenant_{tenant_id}"


async def get_tenant_id():
    """Pick the first active tenant from master DB."""
    import asyncpg
    conn = await asyncpg.connect(MASTER_DB_URL)
    try:
        row = await conn.fetchrow(
            "SELECT tenant_id, name FROM tenants WHERE is_active = true ORDER BY created_at LIMIT 1"
        )
        if not row:
            row = await conn.fetchrow("SELECT tenant_id, name FROM tenants ORDER BY created_at LIMIT 1")
        return row
    finally:
        await conn.close()


async def get_first_valid_ids(tenant_id: str):
    """Get real patient, visit, consultation UUIDs from the tenant DB."""
    import asyncpg
    dsn = TENANT_DB_TEMPLATE.format(tenant_id=tenant_id)
    conn = await asyncpg.connect(dsn)
    try:
        patients = await conn.fetch("SELECT id, full_name FROM patients LIMIT 6")
        if not patients:
            print("⚠️  No patients found in tenant DB.")
            return None

        patient_ids = [p['id'] for p in patients]
        visits = await conn.fetch(
            "SELECT visit_id, patient_id FROM visits WHERE patient_id = ANY($1::uuid[]) LIMIT 6",
            patient_ids
        )
        if not visits:
            print("⚠️  No visits found for those patients.")
            return None

        visit_ids = [v['visit_id'] for v in visits]
        consultations = await conn.fetch(
            "SELECT id, visit_id, patient_id FROM consultations WHERE visit_id = ANY($1::uuid[]) LIMIT 6",
            visit_ids
        )
        if not consultations:
            print("⚠️  No consultations found.")
            return None

        return {
            "patients": [dict(p) for p in patients],
            "visits": [dict(v) for v in visits],
            "consultations": [dict(c) for c in consultations],
        }
    finally:
        await conn.close()


async def seed_lab_requests(tenant_id: str, ids: dict):
    """Insert realistic investigation requests + specimens + lab_results."""
    import asyncpg
    dsn = TENANT_DB_TEMPLATE.format(tenant_id=tenant_id)
    conn = await asyncpg.connect(dsn)
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        consultations = ids["consultations"]

        existing = await conn.fetchval("SELECT COUNT(*) FROM investigation_requests WHERE request_type IN ('lab', 'laboratory')")
        if existing and existing > 0:
            print(f"ℹ️  Found {existing} existing lab requests in database.")

        print(f"Seeding lab requests for tenant {tenant_id}...")
        seeded = []

        # ── Request 1: Pending STAT (CBC)
        if len(consultations) >= 1:
            c = consultations[0]
            req_id = uuid.uuid4()
            await conn.execute("""
                INSERT INTO investigation_requests
                  (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code,
                   clinical_history, status, urgency, requested_by, requested_at, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            """, req_id, c['id'], c['visit_id'], c['patient_id'],
                'laboratory', 'Full Blood Count', 'L-FBC',
                'Acute anaemia workup — fatigue, pallor',
                'pending', 'stat', 'Dr. Sarah Chen',
                now - datetime.timedelta(minutes=20), now - datetime.timedelta(minutes=20))
            seeded.append(("FBC (STAT, pending)", str(req_id)))

        # ── Request 2: Pending URGENT (LFTs)
        if len(consultations) >= 2:
            c = consultations[1]
            req_id = uuid.uuid4()
            await conn.execute("""
                INSERT INTO investigation_requests
                  (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code,
                   clinical_history, status, urgency, requested_by, requested_at, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            """, req_id, c['id'], c['visit_id'], c['patient_id'],
                'laboratory', 'Liver Function Tests', 'L-LFT',
                'Jaundice, abdominal pain — rule out hepatitis',
                'pending', 'urgent', 'Dr. Amina Hassan',
                now - datetime.timedelta(minutes=45), now - datetime.timedelta(minutes=45))
            seeded.append(("LFTs (URGENT, pending)", str(req_id)))

        # ── Request 3: In-Progress with specimen collected (HbA1c)
        if len(consultations) >= 3:
            c = consultations[2]
            req_id = uuid.uuid4()
            await conn.execute("""
                INSERT INTO investigation_requests
                  (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code,
                   clinical_history, status, urgency, requested_by, requested_at, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            """, req_id, c['id'], c['visit_id'], c['patient_id'],
                'laboratory', 'HbA1c', 'L-HbA1c',
                'Uncontrolled diabetes — 3 month glucose control review',
                'specimen_collected', 'urgent', 'Dr. Sarah Chen',
                now - datetime.timedelta(hours=2), now - datetime.timedelta(hours=2))

            # Specimen
            await conn.execute("""
                INSERT INTO specimens
                  (specimen_id, request_id, patient_id, specimen_type, collected_by,
                   collected_at, status, created_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """, uuid.uuid4(), req_id, c['patient_id'],
                'Whole Blood', 'Tech Ali M.',
                now - datetime.timedelta(hours=1, minutes=30),
                'collected',
                now - datetime.timedelta(hours=1, minutes=30),
                now - datetime.timedelta(hours=1, minutes=30))
            seeded.append(("HbA1c (URGENT, specimen_collected)", str(req_id)))

        # ── Request 4: Completed with critical result (Urea & Electrolytes)
        if len(consultations) >= 4:
            c = consultations[3]
            req_id = uuid.uuid4()
            await conn.execute("""
                INSERT INTO investigation_requests
                  (id, consultation_id, visit_id, patient_id, request_type, test_name, test_code,
                   clinical_history, status, urgency, requested_by, requested_at, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            """, req_id, c['id'], c['visit_id'], c['patient_id'],
                'laboratory', 'Urea & Electrolytes', 'L-UE',
                'Acute kidney injury workup — oliguria, creatinine rising',
                'completed', 'stat', 'Dr. Amina Hassan',
                now - datetime.timedelta(hours=3), now - datetime.timedelta(hours=3))

            # Specimen
            spec_id = uuid.uuid4()
            await conn.execute("""
                INSERT INTO specimens
                  (specimen_id, request_id, patient_id, specimen_type, collected_by,
                   collected_at, status, created_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """, spec_id, req_id, c['patient_id'],
                'Serum', 'Tech Ali M.',
                now - datetime.timedelta(hours=2, minutes=45),
                'completed',
                now - datetime.timedelta(hours=2, minutes=45),
                now - datetime.timedelta(hours=2, minutes=45))

            # Critical result
            await conn.execute("""
                INSERT INTO lab_results
                  (result_id, request_id, visit_id, patient_id, specimen_type, result_value,
                   unit, reference_range, is_critical, result_notes, performed_by,
                   status, resulted_at, created_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            """, uuid.uuid4(), req_id, c['visit_id'], c['patient_id'],
                'Serum',
                'Na: 128, K: 6.8, Creat: 540, Urea: 28.4',
                'mmol/L',
                'Na 135-145 | K 3.5-5.0 | Creat 60-110 | Urea 2.5-7.5',
                True,
                'Severe hyperkalaemia and azotaemia. Immediate nephrology review required.',
                'Tech Ali M.',
                'verified',
                now - datetime.timedelta(hours=1),
                now - datetime.timedelta(hours=1),
                now - datetime.timedelta(hours=1))
            seeded.append(("U&E (STAT, completed, CRITICAL result)", str(req_id)))

        print("\n✅ Seeded successfully:")
        for label, rid in seeded:
            print(f"   • {label}  [{rid[:8]}...]")

    finally:
        await conn.close()


async def main():
    try:
        import asyncpg
    except ImportError:
        print("Installing asyncpg...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "asyncpg", "--break-system-packages", "-q"])
        import asyncpg

    print("🔍 Looking up tenant ID from master DB...")
    tenant_row = await get_tenant_id()
    if not tenant_row:
        print("❌ No tenants found in master DB.")
        sys.exit(1)

    tenant_id = tenant_row['tenant_id']
    tenant_name = tenant_row['name']
    print(f"✅ Using tenant: {tenant_id} ({tenant_name})")

    print("🔍 Looking up patients/visits/consultations in tenant DB...")
    ids = await get_first_valid_ids(tenant_id)
    if not ids:
        sys.exit(1)

    print(f"✅ Found {len(ids['consultations'])} consultations")
    await seed_lab_requests(tenant_id, ids)


if __name__ == "__main__":
    asyncio.run(main())
