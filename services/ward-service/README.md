# Ward Service — How to Test Endpoints

Backend API for SRS §2.8 Ward / Inpatient (**FR-47–FR-52**).  
Base URL (via gateway): `http://localhost:8000/api/v1/ward`  
Direct service: `http://localhost:8017/api/v1/ward`  
Swagger (dev): `http://localhost:8017/docs`

Prefer the **gateway** (`:8000`) so JWT + tenant resolution match production.

---

## Auth (temporary)

**JWT / role checks are currently disabled** on ward endpoints for local testing.

- Call `http://localhost:8017/api/v1/ward/...` (or gateway `/api/v1/ward/...`) **without** a Bearer token.
- Tenant DB defaults to `hosp-ac224699` (override with header `X-Tenant-ID: hosp-ac224699`).
- Actor recorded on writes is `dev-unauthenticated`.

Re-enable auth before any shared/staging deployment.

1. Stack running (`hospital-ward-service` healthy).
2. Tenant migrations applied through **`0016_ward_module_tables`**:

```powershell
docker cp scripts/migrate_existing_tenants.py hospital-master-service:/tmp/migrate_existing_tenants.py
docker exec -w /app `
  -e DATABASE_URL=postgresql://postgres:postgres@postgres-master:5432/hospital_master `
  -e TENANT_DB_TEMPLATE=postgresql://postgres:postgres@postgres-master:5432/tenant_{tenant_id} `
  hospital-master-service python /tmp/migrate_existing_tenants.py
```

3. At least one bed in the tenant DB (admin catalog, not ward):

```http
POST /api/v1/admin/beds
Authorization: Bearer <hospital_admin_token>
```

```json
{
  "ward_name": "General Ward",
  "bed_number": "G-01",
  "bed_type": "general",
  "is_available": true,
  "is_active": true
}
```

4. A real `visit_id` / `patient_id` from reception (registered visit).
5. Users with roles you will use: `doctor` or `clinician` (admit/discharge/orders), `nurse` (notes/bed assign), `hospital_admin` (read).

---

## 1. Login and get a token

```powershell
$BASE = "http://localhost:8000"

# Hospital admin (bed setup)
$adminLogin = Invoke-RestMethod -Method POST "$BASE/api/v1/auth/login" `
  -ContentType "application/json" `
  -Body '{"username":"hadmin1","password":"admin12345"}'
$ADMIN = $adminLogin.access_token

# Doctor (replace with a doctor user in your tenant)
$docLogin = Invoke-RestMethod -Method POST "$BASE/api/v1/auth/login" `
  -ContentType "application/json" `
  -Body '{"username":"<doctor_username>","password":"<password>"}'
$DOC = $docLogin.access_token

# Nurse (optional)
$nurseLogin = Invoke-RestMethod -Method POST "$BASE/api/v1/auth/login" `
  -ContentType "application/json" `
  -Body '{"username":"<nurse_username>","password":"<password>"}'
$NURSE = $nurseLogin.access_token

$H = @{ Authorization = "Bearer $DOC" }
```

If you only have `hadmin1`, create doctor/nurse via `POST /api/v1/admin/users` first (roles: `doctor`, `nurse`).

---

## 2. Recommended happy-path flow

```text
Consultation: final diagnosis → disposition=admission
        ↓
Admin: ensure bed exists and is available
        ↓
Ward: POST /admissions (visit_id + bed_id + diagnosis)
        ↓
Ward: orders + nursing notes
        ↓
Ward: GET /los → POST /discharge
        ↓
Billing: WARD_DAY line created on patient.discharged (async)
```

### 2.1 Consultation disposition (FR-18 / FR-21)

Required **before** admit if a consultation exists for that visit.

```powershell
# Add final diagnosis
Invoke-RestMethod -Method POST "$BASE/api/v1/consultation/encounters/$CONSULTATION_ID/diagnoses" `
  -Headers $H -ContentType "application/json" `
  -Body '{"diagnosis_type":"final","description":"Community-acquired pneumonia"}'

# Set disposition
Invoke-RestMethod -Method POST "$BASE/api/v1/consultation/encounters/$CONSULTATION_ID/disposition" `
  -Headers $H -ContentType "application/json" `
  -Body '{"disposition":"admission","notes":"Needs inpatient care"}'
```

Allowed dispositions: `outpatient` | `admission` | `referral` | `deceased`.  
`admission` / `outpatient` require a **`final`** diagnosis.

### 2.2 List beds / board (FR-47)

```powershell
Invoke-RestMethod "$BASE/api/v1/ward/beds?is_available=true" -Headers $H
Invoke-RestMethod "$BASE/api/v1/ward/beds/board" -Headers $H
```

Pick a free `bed_id` from the response.

### 2.3 Create admission (FR-48)

```powershell
$admit = Invoke-RestMethod -Method POST "$BASE/api/v1/ward/admissions" `
  -Headers $H -ContentType "application/json" `
  -Body (@{
    visit_id = "<VISIT_UUID>"
    bed_id = "<BED_UUID>"
    admitting_diagnosis = "Community-acquired pneumonia"
  } | ConvertTo-Json)

$ADMISSION_ID = $admit.admission_id
$admit
```

Expect **201**. Bed becomes unavailable. Event `patient.admitted` is published.

Common errors:

| Status | Meaning |
|--------|---------|
| 404 | Visit or bed not found |
| 409 | Bed occupied, or visit already has active admission |
| 400 | Consultation exists but disposition ≠ `admission` |

### 2.4 Inpatient orders (FR-49)

```powershell
Invoke-RestMethod -Method POST "$BASE/api/v1/ward/admissions/$ADMISSION_ID/orders" `
  -Headers $H -ContentType "application/json" `
  -Body '{"order_type":"medication","order_detail":"Amoxicillin 500mg TDS","frequency":"TDS"}'

Invoke-RestMethod "$BASE/api/v1/ward/admissions/$ADMISSION_ID/orders" -Headers $H
```

`order_type`: `medication` | `nursing` | `diet` | `investigation` | `procedure` | `other`

Update status:

```powershell
Invoke-RestMethod -Method PATCH "$BASE/api/v1/ward/admissions/$ADMISSION_ID/orders/$ORDER_ID" `
  -Headers $H -ContentType "application/json" `
  -Body '{"status":"completed"}'
```

### 2.5 Nursing notes (FR-50)

Use a **nurse** (or doctor) token:

```powershell
$HN = @{ Authorization = "Bearer $NURSE" }

Invoke-RestMethod -Method POST "$BASE/api/v1/ward/admissions/$ADMISSION_ID/nursing-notes" `
  -Headers $HN -ContentType "application/json" `
  -Body (@{
    note_type = "observation"
    note_text = "Patient stable, SpO2 97%"
    vitals_bp = "120/80"
    vitals_temp = 36.8
    vitals_pulse = 78
    vitals_spo2 = 97
  } | ConvertTo-Json)

Invoke-RestMethod "$BASE/api/v1/ward/admissions/$ADMISSION_ID/nursing-notes" -Headers $HN
```

`note_type`: `observation` | `intervention` | `progress` | `medication_given` | `ward_round`

### 2.6 Length of stay (FR-52)

```powershell
Invoke-RestMethod "$BASE/api/v1/ward/admissions/$ADMISSION_ID/los" -Headers $H
```

While active: computed from `admission_date` → now. After discharge: stored `length_of_stay_days`.

### 2.7 Discharge (FR-51)

```powershell
Invoke-RestMethod -Method POST "$BASE/api/v1/ward/admissions/$ADMISSION_ID/discharge" `
  -Headers $H -ContentType "application/json" `
  -Body '{"discharge_diagnosis":"Resolved pneumonia","discharge_instructions":"Complete antibiotics, follow up in 1 week"}'
```

Expect bed released, status `discharged`, event `patient.discharged`.  
Billing-service should add one idempotent `WARD_DAY` bill line (async via RabbitMQ).

Re-discharge → **400**.

---

## 3. Endpoint cheat sheet

| Method | Path | Roles |
|--------|------|--------|
| GET | `/beds` | nurse, doctor, clinician, hospital_admin, hospital_user |
| GET | `/beds/board` | nurse, doctor, clinician, hospital_admin |
| POST | `/beds/{bed_id}/assign` | nurse, doctor, clinician |
| POST | `/beds/{bed_id}/release` | nurse, doctor, clinician |
| POST | `/admissions` | doctor, clinician |
| GET | `/admissions` | nurse, doctor, clinician, hospital_admin |
| GET | `/admissions/{id}` | nurse, doctor, clinician, hospital_admin |
| GET | `/admissions/{id}/los` | nurse, doctor, clinician, hospital_admin, cashier |
| POST | `/admissions/{id}/discharge` | doctor, clinician |
| POST/GET/PATCH | `/admissions/{id}/orders` | doctor/clinician (write); nurse can PATCH |
| POST/GET | `/admissions/{id}/nursing-notes` | nurse, doctor, clinician |

`hospital_admin` is allowed on all endpoints via the shared role check.

---

## 4. curl examples (same flow)

```bash
export BASE=http://localhost:8000
export TOKEN=<access_token>

curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/ward/beds?is_available=true"

curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"visit_id":"...","bed_id":"...","admitting_diagnosis":"Pneumonia"}' \
  "$BASE/api/v1/ward/admissions"

curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"discharge_diagnosis":"Resolved","discharge_instructions":"Rest"}' \
  "$BASE/api/v1/ward/admissions/<admission_id>/discharge"
```

---

## 5. Health & troubleshooting

```powershell
Invoke-RestMethod http://localhost:8017/health
# {"status":"ok","service":"ward-service"}

docker logs hospital-ward-service --tail 80
```

| Symptom | Likely cause |
|---------|----------------|
| Container exits on start | Master DB password mismatch (`DATABASE_URL`) |
| 401 | Missing/expired token |
| 403 | User role not allowed |
| 400 disposition | Consultation exists without `disposition=admission` |
| 400 final diagnosis | Disposition without `diagnosis_type=final` |
| 409 bed | Bed already occupied |
| Empty beds | Create beds via `/api/v1/admin/beds` |
| No bill line after discharge | Check `hospital-billing-service` logs + RabbitMQ |

Optional fee for realistic ward charge (else `DEFAULT_WARD_DAY_RATE`, default 100):

```json
{ "item_name": "Ward day", "item_code": "WARD_DAY", "item_type": "ward", "standard_price": 150.00, "effective_from": "2026-01-01" }
```

`POST /api/v1/admin/fee-schedules` as hospital admin.

---

## 6. What is out of scope in this module

- Streamlit UI  
- Pharmacy/lab auto-fulfillment from inpatient investigation orders  
- Full cashier/payments (only the ward-day bill line on discharge)  
- Bed catalog create/delete (admin-service FR-55)
