# Hospital Patient Flow System

**Multi-Tenant SaaS | Microservices Architecture | FastAPI | PostgreSQL | RabbitMQ**

---

## Overview

A cloud-hosted Hospital Patient Flow Management System that serves multiple hospitals simultaneously. Each hospital is a fully isolated tenant with its own PostgreSQL database — no patient data is ever shared between hospitals.

The system is decomposed into **14 microservices**, each owning its own domain, deployable independently, and communicating via HTTP (synchronous) and RabbitMQ events (asynchronous).

---

## Service Map

| Service                | Port | Responsibility                                                      |
| ---------------------- | ---- | ------------------------------------------------------------------- |
| `api-gateway`          | 8000 | JWT verification, tenant resolution, request routing, rate limiting |
| `auth-service`         | 8001 | Login, token refresh, password reset, MFA (TOTP)                    |
| `master-service`       | 8002 | Super admin portal — tenant management, subscriptions, invoicing    |
| `reception-service`    | 8010 | Patient registration, visit creation, queue assignment              |
| `triage-service`       | 8011 | Vital signs, triage category, queue priority                        |
| `consultation-service` | 8012 | Clinical notes, diagnoses, investigation requests, prescriptions    |
| `laboratory-service`   | 8013 | Specimen tracking, result entry, critical value alerts              |
| `radiology-service`    | 8014 | Imaging scheduling, reports, DICOM references                       |
| `pharmacy-service`     | 8015 | Dispensing, drug interaction checks, inventory management           |
| `billing-service`      | 8016 | Bills, line items, payments, insurance claims                       |
| `ward-service`         | 8017 | Bed management, admissions, inpatient orders, nursing notes         |
| `admin-service`        | 8018 | Staff accounts, departments, fee schedules, audit logs              |
| `notification-service` | 8019 | In-system notifications (critical results, low stock, queue calls)  |
| `report-service`       | 8020 | Analytics — census, revenue, wait times, bed occupancy              |

---

## Repository Structure

```
hospital-flow/
├── services/
│   ├── api-gateway/
│   │   ├── app/
│   │   │   ├── main.py                     # FastAPI app — mounts all proxy routes, CORS, middleware
│   │   │   ├── config.py                   # Reads SERVICE_URLs, REDIS_URL, SECRET_KEY from .env
│   │   │   ├── proxy.py                    # Dynamic reverse-proxy logic — route table per service
│   │   │   ├── tenant.py                   # Resolves tenant DB URL from JWT → Master DB → Redis cache
│   │   │   ├── rate_limit.py               # Redis sliding-window rate limiter (100 req/min per tenant)
│   │   │   └── middleware.py               # JWT verification, X-Tenant-DB header injection, access log
│   │   ├── tests/
│   │   │   └── test_gateway.py             # JWT rejection, tenant resolution, rate limit, routing tests
│   │   ├── nginx/
│   │   │   └── gateway.conf                # Optional Nginx config if using Nginx as the outer proxy
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── auth-service/                       # Port 8001
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py             # get_tenant_db(), get_current_user()
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # POST /login, /logout, /refresh, /password-reset, /mfa/*
│   │   │   │   └── schemas.py              # LoginRequest, TokenResponse, PasswordResetRequest, MFASetup
│   │   │   ├── services/
│   │   │   │   └── auth.py                 # login_user(), refresh_token(), reset_password(), verify_mfa()
│   │   │   ├── models/
│   │   │   │   └── auth.py                 # User, RefreshToken, PasswordResetToken (ORM)
│   │   │   ├── db/
│   │   │   │   ├── tenant.py               # Dynamic SQLAlchemy engine per tenant (cached)
│   │   │   │   └── session.py              # get_tenant_db() generator
│   │   │   └── core/
│   │   │       ├── security.py             # JWT sign/decode, bcrypt hash/verify, TOTP helpers
│   │   │       └── middleware.py           # Audit log writer
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_auth.py            # login flow, JWT decode, password reset, MFA unit tests
│   │   │   └── integration/
│   │   │       └── test_auth_api.py        # Full API tests via TestClient
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── master-service/                     # Port 8002 — Super Admin portal only
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py             # get_master_db(), get_current_super_admin()
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # /tenants, /subscriptions, /invoices, /announcements, /audit-log
│   │   │   │   └── schemas.py              # TenantCreate, SubscriptionCreate, InvoiceResponse, SaasPaymentCreate
│   │   │   ├── services/
│   │   │   │   ├── tenant.py               # create_tenant(), suspend(), reactivate(), terminate()
│   │   │   │   ├── subscription.py         # assign_plan(), generate_invoice(), record_payment()
│   │   │   │   └── announcement.py         # broadcast(), schedule_announcement()
│   │   │   ├── models/
│   │   │   │   └── master.py               # Tenant, SubscriptionPlan, Subscription, Invoice, SaasPayment,
│   │   │   │                               # SuperAdmin, SuperAdminAuditLog, Announcement, SubscriptionAuditLog
│   │   │   ├── db/
│   │   │   │   ├── master.py               # SQLAlchemy engine for Master DB only
│   │   │   │   └── session.py              # get_master_db() generator
│   │   │   ├── events/
│   │   │   │   └── publisher.py            # Publishes tenant.created, tenant.suspended
│   │   │   └── core/
│   │   │       ├── security.py             # Super admin JWT, bcrypt
│   │   │       └── middleware.py           # Super admin audit log writer
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_tenant.py
│   │   │   └── integration/
│   │   │       └── test_master_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── reception-service/                  # Port 8010
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py             # get_tenant_db(), get_current_user(), require_role()
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # /patients, /visits, /visits/{id}/insurance, /queue
│   │   │   │   └── schemas.py              # PatientCreate, PatientResponse, VisitCreate, VisitResponse,
│   │   │   │                               # InsuranceCreate, QueueResponse
│   │   │   ├── services/
│   │   │   │   └── reception.py            # register_patient(), create_visit(), detect_duplicate(),
│   │   │   │                               # assign_queue(), call_queue_entry()
│   │   │   ├── models/
│   │   │   │   └── reception.py            # Patient, Visit, PatientInsurance, Queue (ORM)
│   │   │   ├── db/
│   │   │   │   ├── tenant.py
│   │   │   │   └── session.py
│   │   │   ├── events/
│   │   │   │   └── publisher.py            # Publishes patient.registered, visit.created
│   │   │   └── core/
│   │   │       ├── security.py
│   │   │       └── middleware.py
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_reception.py       # Patient registration, duplicate detection, queue assignment
│   │   │   └── integration/
│   │   │       └── test_reception_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── triage-service/                     # Port 8011
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # /queue, /assessments, /assessments/{visit_id}
│   │   │   │   └── schemas.py              # TriageCreate, TriageResponse, VitalsUpdate
│   │   │   ├── services/
│   │   │   │   └── triage.py               # save_assessment(), assign_category(), update_queue_priority()
│   │   │   ├── models/
│   │   │   │   └── triage.py               # TriageAssessment (ORM)
│   │   │   ├── db/
│   │   │   │   ├── tenant.py
│   │   │   │   └── session.py
│   │   │   ├── events/
│   │   │   │   ├── publisher.py            # Publishes triage.completed
│   │   │   │   └── subscriber.py           # Consumes visit.created
│   │   │   └── core/
│   │   │       ├── security.py
│   │   │       └── middleware.py
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_triage.py          # Category assignment logic, priority ordering
│   │   │   └── integration/
│   │   │       └── test_triage_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── consultation-service/               # Port 8012
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # /queue, /consultations, /diagnoses,
│   │   │   │   │                           # /investigation-requests, /prescriptions
│   │   │   │   └── schemas.py              # ConsultationCreate, DiagnosisCreate,
│   │   │   │                               # InvestigationRequestCreate, PrescriptionCreate
│   │   │   ├── services/
│   │   │   │   └── consultation.py         # open_consultation(), save_diagnosis(),
│   │   │   │                               # request_investigation(), issue_prescription(),
│   │   │   │                               # complete_consultation()
│   │   │   ├── models/
│   │   │   │   └── consultation.py         # Consultation, Diagnosis, InvestigationRequest,
│   │   │   │                               # Prescription (ORM)
│   │   │   ├── db/
│   │   │   │   ├── tenant.py
│   │   │   │   └── session.py
│   │   │   ├── events/
│   │   │   │   ├── publisher.py            # Publishes investigation.requested, prescription.issued
│   │   │   │   └── subscriber.py           # Consumes triage.completed, lab.result_ready,
│   │   │   │                               # radiology.report_ready
│   │   │   └── core/
│   │   │       ├── security.py
│   │   │       └── middleware.py
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_consultation.py    # Consultation workflow, diagnosis saving, disposition logic
│   │   │   └── integration/
│   │   │       └── test_consultation_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── laboratory-service/                 # Port 8013
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # /requests, /specimens, /results, /results/{id}/verify
│   │   │   │   └── schemas.py              # LabResultCreate, SpecimenUpdate, ResultResponse,
│   │   │   │                               # CriticalValueAlert
│   │   │   ├── services/
│   │   │   │   └── laboratory.py           # collect_specimen(), enter_result(),
│   │   │   │                               # detect_critical_value(), verify_result()
│   │   │   ├── models/
│   │   │   │   └── laboratory.py           # LabResult, Specimen (ORM)
│   │   │   ├── db/
│   │   │   │   ├── tenant.py
│   │   │   │   └── session.py
│   │   │   ├── events/
│   │   │   │   ├── publisher.py            # Publishes lab.result_ready, lab.critical_value
│   │   │   │   └── subscriber.py           # Consumes investigation.requested
│   │   │   └── core/
│   │   │       ├── security.py
│   │   │       └── middleware.py
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_laboratory.py      # Critical value detection, result entry, specimen tracking
│   │   │   └── integration/
│   │   │       └── test_laboratory_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── radiology-service/                  # Port 8014
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # /requests, /reports, /reports/{id}/verify,
│   │   │   │   │                           # /reports/{id}/image
│   │   │   │   └── schemas.py              # RadiologyReportCreate, RadiologyReportResponse,
│   │   │   │                               # ImagingStatusUpdate
│   │   │   ├── services/
│   │   │   │   └── radiology.py            # schedule_imaging(), submit_report(),
│   │   │   │                               # verify_report(), attach_image()
│   │   │   ├── models/
│   │   │   │   └── radiology.py            # RadiologyReport (ORM)
│   │   │   ├── db/
│   │   │   │   ├── tenant.py
│   │   │   │   └── session.py
│   │   │   ├── events/
│   │   │   │   ├── publisher.py            # Publishes radiology.report_ready
│   │   │   │   └── subscriber.py           # Consumes investigation.requested
│   │   │   └── core/
│   │   │       ├── security.py
│   │   │       └── middleware.py
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_radiology.py       # Report submission, status progression, verification
│   │   │   └── integration/
│   │   │       └── test_radiology_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── pharmacy-service/                   # Port 8015
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # /prescriptions, /dispense, /inventory,
│   │   │   │   │                           # /inventory/{id}/restock, /transactions/{inventory_id}
│   │   │   │   └── schemas.py              # DispensingCreate, InventoryUpdate,
│   │   │   │                               # StockTransactionCreate, LowStockAlert
│   │   │   ├── services/
│   │   │   │   └── pharmacy.py             # dispense_drug(), check_billing_clearance(),
│   │   │   │                               # check_drug_interactions(), deduct_stock(),
│   │   │   │                               # restock(), trigger_low_stock_alert()
│   │   │   ├── models/
│   │   │   │   └── pharmacy.py             # DispensingRecord, DrugInventory,
│   │   │   │                               # DrugInventoryTransaction (ORM)
│   │   │   ├── db/
│   │   │   │   ├── tenant.py
│   │   │   │   └── session.py
│   │   │   ├── events/
│   │   │   │   ├── publisher.py            # Publishes drug.dispensed, stock.low
│   │   │   │   └── subscriber.py           # Consumes prescription.issued, payment.received
│   │   │   └── core/
│   │   │       ├── security.py
│   │   │       └── middleware.py
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_pharmacy.py        # Stock deduction, low-stock threshold,
│   │   │   │                               # drug interaction check, billing clearance
│   │   │   └── integration/
│   │   │       └── test_pharmacy_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── billing-service/                    # Port 8016
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # /bills, /bills/{id}/items, /bills/{id}/discount,
│   │   │   │   │                           # /payments, /claims, /bills/{id}/clearance
│   │   │   │   └── schemas.py              # BillCreate, BillItemCreate, PaymentCreate,
│   │   │   │                               # InsuranceClaimCreate, BillResponse
│   │   │   ├── services/
│   │   │   │   └── billing.py              # create_bill(), add_line_item(), apply_discount(),
│   │   │   │                               # record_payment(), submit_claim(),
│   │   │   │                               # check_billing_clearance()
│   │   │   ├── models/
│   │   │   │   └── billing.py              # Bill, BillItem, Payment, InsuranceClaim (ORM)
│   │   │   ├── db/
│   │   │   │   ├── tenant.py
│   │   │   │   └── session.py
│   │   │   ├── events/
│   │   │   │   ├── publisher.py            # Publishes bill.created, payment.received
│   │   │   │   └── subscriber.py           # Consumes visit.created, drug.dispensed,
│   │   │   │                               # patient.admitted, patient.discharged
│   │   │   └── core/
│   │   │       ├── security.py
│   │   │       └── middleware.py
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_billing.py         # Bill calculation, discount logic, payment recording
│   │   │   └── integration/
│   │   │       └── test_billing_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── ward-service/                       # Port 8017
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # /beds, /admissions, /admissions/{id}/orders,
│   │   │   │   │                           # /admissions/{id}/notes, /admissions/{id}/discharge
│   │   │   │   └── schemas.py              # AdmissionCreate, BedResponse,
│   │   │   │                               # InpatientOrderCreate, NursingNoteCreate
│   │   │   ├── services/
│   │   │   │   └── ward.py                 # admit_patient(), check_bed_availability(),
│   │   │   │                               # assign_bed(), create_order(),
│   │   │   │                               # add_nursing_note(), discharge_patient()
│   │   │   ├── models/
│   │   │   │   └── ward.py                 # Bed, Admission, InpatientOrder, NursingNote (ORM)
│   │   │   ├── db/
│   │   │   │   ├── tenant.py
│   │   │   │   └── session.py
│   │   │   ├── events/
│   │   │   │   ├── publisher.py            # Publishes patient.admitted, patient.discharged
│   │   │   │   └── subscriber.py           # (none — ward is triggered by direct API calls)
│   │   │   └── core/
│   │   │       ├── security.py
│   │   │       └── middleware.py
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_ward.py            # Bed availability, admission logic, discharge workflow
│   │   │   └── integration/
│   │   │       └── test_ward_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── admin-service/                      # Port 8018
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # /users, /users/{id}/deactivate, /departments,
│   │   │   │   │                           # /fee-schedules, /audit-logs
│   │   │   │   └── schemas.py              # UserCreate, UserResponse, DepartmentCreate,
│   │   │   │                               # FeeScheduleCreate, AuditLogResponse
│   │   │   ├── services/
│   │   │   │   └── admin.py                # create_user(), deactivate_user(),
│   │   │   │                               # manage_fee_schedule(), query_audit_log()
│   │   │   ├── models/
│   │   │   │   └── admin.py                # User, Department, FeeSchedule, AuditLog,
│   │   │   │                               # Notification (ORM)
│   │   │   ├── db/
│   │   │   │   ├── tenant.py
│   │   │   │   └── session.py
│   │   │   ├── events/
│   │   │   │   └── publisher.py            # Publishes user.created, user.deactivated
│   │   │   └── core/
│   │   │       ├── security.py
│   │   │       └── middleware.py
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_admin.py           # User creation, role assignment, fee schedule management
│   │   │   └── integration/
│   │   │       └── test_admin_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   ├── notification-service/               # Port 8019
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── dependencies.py
│   │   │   ├── api/v1/
│   │   │   │   ├── router.py               # GET /notifications, PATCH /{id}/read,
│   │   │   │   │                           # PATCH /read-all, DELETE /{id}, GET /unread-count
│   │   │   │   └── schemas.py              # NotificationResponse, NotificationMarkRead
│   │   │   ├── services/
│   │   │   │   └── notifications.py        # create_notification(), dispatch(),
│   │   │   │                               # mark_read(), resolve_recipient()
│   │   │   ├── models/
│   │   │   │   └── notifications.py        # Notification (ORM)
│   │   │   ├── db/
│   │   │   │   ├── tenant.py
│   │   │   │   └── session.py
│   │   │   ├── events/
│   │   │   │   └── subscriber.py           # Consumes lab.critical_value, radiology.report_ready,
│   │   │   │                               # stock.low, patient.admitted, prescription.issued,
│   │   │   │                               # tenant.created
│   │   │   └── core/
│   │   │       ├── security.py
│   │   │       └── middleware.py
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── test_notifications.py   # Notification creation, recipient resolution, mark-read
│   │   │   └── integration/
│   │   │       └── test_notifications_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── .env.example
│   │
│   └── report-service/                     # Port 8020
│       ├── app/
│       │   ├── main.py
│       │   ├── config.py
│       │   ├── dependencies.py
│       │   ├── api/v1/
│       │   │   ├── router.py               # /patient-census, /revenue-summary, /wait-times,
│       │   │   │                           # /bed-occupancy, /discharge-stats, /lab-turnaround,
│       │   │   │                           # /drug-consumption, /outstanding-bills
│       │   │   └── schemas.py              # ReportParams, PatientCensusResponse,
│       │   │                               # RevenueSummaryResponse, WaitTimeResponse
│       │   ├── services/
│       │   │   └── reports.py              # patient_census(), revenue_summary(),
│       │   │                               # wait_times(), bed_occupancy(),
│       │   │                               # discharge_stats(), lab_turnaround()
│       │   ├── models/                     # Read-only ORM models — no writes in this service
│       │   │   └── reports.py              # References all tenant tables needed for aggregation
│       │   ├── db/
│       │   │   ├── tenant.py
│       │   │   └── session.py
│       │   └── core/
│       │       ├── security.py
│       │       └── middleware.py           # Audit logs report access (no writes to clinical data)
│       ├── tests/
│       │   ├── unit/
│       │   │   └── test_reports.py         # Aggregation logic, date range filters, empty data edge cases
│       │   └── integration/
│       │       └── test_reports_api.py
│       ├── Dockerfile
│       ├── requirements.txt
│       └── .env.example
│
├── infrastructure/
│   ├── docker-compose.yml                  # Full local dev stack (all 14 services + infra)
│   ├── docker-compose.test.yml             # Isolated test stack with separate DBs
│   ├── k8s/                                # Kubernetes manifests (Deployment + Service + HPA per service)
│   │   ├── api-gateway/
│   │   │   ├── deployment.yaml
│   │   │   ├── service.yaml                # LoadBalancer — the only external-facing service
│   │   │   └── hpa.yaml
│   │   ├── auth-service/
│   │   │   ├── deployment.yaml
│   │   │   ├── service.yaml                # ClusterIP
│   │   │   └── hpa.yaml
│   │   └── ...                             # Same pattern for all other services
│   └── nginx/
│       └── gateway.conf                    # Nginx upstream config for local dev
│
├── migrations/
│   ├── master/                             # Alembic project for the Master DB
│   │   ├── alembic.ini
│   │   ├── env.py
│   │   └── versions/
│   │       └── 0001_initial_master_schema.py
│   └── tenant/                             # Alembic project for all tenant DBs
│       ├── alembic.ini
│       ├── env.py
│       └── versions/
│           └── 0001_initial_tenant_schema.py
│
├── shared/
│   └── schemas/                            # Shared Pydantic event payload models imported by services
│       ├── events.py                       # VisitCreatedPayload, TriageCompletedPayload, etc.
│       └── common.py                       # Shared enums, base response models
│
├── scripts/
│   ├── provision_tenant.py                 # Provisions a new hospital database from CLI
│   ├── migrate_tenant.py                   # Runs pending tenant migrations for one hospital
│   ├── migrate_all_tenants.py              # Runs pending tenant migrations across all hospitals
│   ├── run_all_tests.sh                    # Runs pytest across all 14 service directories
│   └── seed_dev.py                         # Seeds local dev: 2 hospitals, super admin, staff accounts
│
├── docs/
│   ├── architecture_guide.docx             # Full microservices architecture document
│   ├── database_schema.pdf                 # Complete DB schema — all 40 tables
│   └── srs.docx                            # Software Requirements Specification
│
├── .env.example                            # Root env template — copy to .env
├── .gitignore                              # .env, __pycache__, .venv, *.pyc, migrations/versions/*.pyc
└── README.md
```

---

## Prerequisites

- Python 3.12+
- Node.js 20+ (only for running docx generation scripts in `scripts/`)
- Docker and Docker Compose v2
- PostgreSQL 16 (via Docker in development)
- Redis 7 (via Docker in development)
- RabbitMQ 3.13 (via Docker in development)

---

## Local Development Setup

### 1. Clone and configure environment

```bash
git clone https://github.com/your-org/hospital-flow.git
cd hospital-flow
cp .env.example .env
# Edit .env — fill in SECRET_KEY and any overrides
```

### 2. Start the full infrastructure stack

```bash
docker-compose -f infrastructure/docker-compose.yml up -d
```

This starts: PostgreSQL (Master DB on port 5432), Redis (port 6379), RabbitMQ (AMQP on 5672, management UI on 15672).

### 3. Run Master DB migrations

```bash
cd migrations/master
alembic upgrade head
```

### 4. Seed development data

```bash
python scripts/seed_dev.py
# Creates 2 test hospitals, a super admin, and staff accounts for each
```

### 5. Start services

Run each service in its own terminal, or use the provided docker-compose service entries:

```bash
# Option A: Docker Compose (all services)
docker-compose -f infrastructure/docker-compose.yml up

# Option B: Individual service (for active development)
cd services/auth-service
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

### 6. Verify

```bash
curl http://localhost:8000/health
# Should return: {"status": "ok", "services": {...}}
```

---

## Running Tests

Each service has its own test suite. From the service directory:

```bash
# Unit tests
pytest tests/unit/ -v

# Integration tests (requires a running test DB)
pytest tests/integration/ -v

# All tests with coverage
pytest --cov=app --cov-report=term-missing

# Tenant isolation tests (critical — run before every deploy)
pytest tests/integration/test_tenant_isolation.py -v
```

Run all service tests at once from the repository root:

```bash
scripts/run_all_tests.sh
```

---

## Database Migrations

The system has two migration targets managed separately.

**Master DB** (tenant registry, subscriptions, super admin):

```bash
cd migrations/master
alembic upgrade head
alembic downgrade -1
alembic revision --autogenerate -m "your change description"
```

**Tenant DB** (all clinical tables — applied per hospital):

```bash
# Migrate a single tenant
python scripts/migrate_tenant.py --tenant-id <UUID>

# Migrate all tenants (use during maintenance window)
python scripts/migrate_all_tenants.py

# Check migration status across all tenants
python scripts/migrate_all_tenants.py --dry-run
```

---

## Provisioning a New Hospital

When a Super Admin creates a hospital tenant via the API, the system automatically:

1. Creates a record in `tenants` (Master DB).
2. Provisions a new isolated PostgreSQL database.
3. Runs all tenant migrations against the new database.
4. Stores the encrypted `db_connection_string` in the Master DB.
5. Sends a welcome email to the hospital admin.

To provision manually (development/testing only):

```bash
python scripts/provision_tenant.py \
  --hospital-name "General Hospital" \
  --country Tanzania \
  --city "Dar es Salaam" \
  --admin-email admin@generalhospital.tz
```

---

## How Multi-Tenancy Works

Every request is tenant-scoped from the moment it arrives:

```
Client request (with JWT Bearer token)
    ↓
API Gateway — extracts tenant_id from JWT claims
    ↓
Gateway queries Master DB: SELECT db_connection_string WHERE tenant_id = ?
    (result cached in Redis — key: tenant:{id}:db_url — TTL 300s)
    ↓
Gateway attaches X-Tenant-DB header to proxied request
    ↓
Target service receives X-Tenant-DB → creates SQLAlchemy engine
    (engine cached in process memory per tenant_id — connection pool min=2, max=10)
    ↓
All queries in this request run against this hospital's database only
    ↓
Audit middleware writes action to audit_logs in the same tenant DB
    ↓
Response returned to client
```

**There is no application-level WHERE tenant_id = ? filter.** Isolation is enforced at the database connection level — a connection to Hospital A's database cannot see Hospital B's tables.

---

## Event Bus

Services communicate asynchronously via RabbitMQ. All events use a single topic exchange (`hospital_events`). Routing keys follow the pattern `{domain}.{event}`.

Key events:

| Event                     | Publisher    | Subscribers                |
| ------------------------- | ------------ | -------------------------- |
| `visit.created`           | reception    | billing, triage            |
| `triage.completed`        | triage       | consultation               |
| `investigation.requested` | consultation | laboratory, radiology      |
| `prescription.issued`     | consultation | pharmacy, billing          |
| `lab.critical_value`      | laboratory   | notification               |
| `lab.result_ready`        | laboratory   | consultation               |
| `radiology.report_ready`  | radiology    | consultation, notification |
| `drug.dispensed`          | pharmacy     | billing                    |
| `stock.low`               | pharmacy     | notification               |
| `patient.admitted`        | ward         | billing, notification      |
| `patient.discharged`      | ward         | billing                    |
| `payment.received`        | billing      | pharmacy (clearance)       |
| `tenant.suspended`        | master       | auth (revoke tokens)       |

All event consumers are idempotent — processing the same event twice produces the same result as processing it once.

---

## API Authentication

All endpoints (except `/auth/login` and `/auth/password-reset`) require a Bearer JWT:

```
Authorization: Bearer <access_token>
```

**Token structure:**

```json
{
  "sub": "user_uuid",
  "tenant_id": "hospital_uuid",
  "role": "doctor",
  "exp": 1718000000,
  "iat": 1717998200
}
```

Access tokens expire in **30 minutes**. Use `POST /api/v1/auth/refresh` with your refresh token to obtain a new access token without re-logging in.

---

## Role Reference

| Role             | Access                                               |
| ---------------- | ---------------------------------------------------- |
| `super_admin`    | Master service only — no access to any hospital data |
| `hospital_admin` | Admin, reports, audit logs for their hospital only   |
| `receptionist`   | Reception module                                     |
| `triage_nurse`   | Triage module                                        |
| `doctor`         | Consultation, ward, investigation results (read)     |
| `lab_technician` | Laboratory module                                    |
| `radiographer`   | Radiology module                                     |
| `pharmacist`     | Pharmacy module                                      |
| `cashier`        | Billing module                                       |

Roles are enforced by the `require_role()` FastAPI dependency on every protected endpoint. Attempting to access an endpoint with the wrong role returns `403 Forbidden`.

---

## Environment Variables

Copy `.env.example` to `.env`. Required variables:

| Variable                                                  | Description                                          |
| --------------------------------------------------------- | ---------------------------------------------------- |
| `SECRET_KEY`                                              | JWT signing secret — minimum 64-character hex string |
| `MASTER_DB_URL`                                           | PostgreSQL connection string for the Master DB       |
| `REDIS_URL`                                               | Redis connection string                              |
| `RABBITMQ_URL`                                            | RabbitMQ AMQP connection string                      |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | Transactional email                                  |
| `{SERVICE}_URL`                                           | Internal URL for each service (used by the gateway)  |

See `.env.example` for the full list with example values.

**Never commit `.env` to version control.** It is in `.gitignore`. In production, inject secrets via Kubernetes Secrets or AWS Secrets Manager.

---

## License

Proprietary — Hospital Patient Flow System. All rights reserved.
