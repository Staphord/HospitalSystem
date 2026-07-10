# Visit Service

Creates visit records, handles payment type selection and insurance verification, and assigns triage queue entries.

## Endpoints

| Method | Path | Description | Tags |
|--------|------|-------------|------|
| `POST` | `/api/v1/visits` | Create a visit + insurance validation + queue assignment | `visits` |
| `GET` | `/api/v1/visits/{visit_id}` | Get detailed visit record by ID | `visits` |
| `PATCH` | `/api/v1/visits/{visit_id}/status` | Transition a visit status (e.g. registered -> triaged) | `visits` |
| `POST` | `/api/v1/visits/patients/{patient_id}/insurance` | Register a new patient insurance policy | `insurance` |
| `GET` | `/api/v1/visits/patients/{patient_id}/insurance` | List all insurance policies of a patient | `insurance` |
| `PATCH` | `/api/v1/visits/insurance/{insurance_id}/verify` | Record outcome of manual insurance verification | `insurance` |
| `GET` | `/api/v1/visits/queues/{queue_type}` | List all entries for a specific queue | `queues` |
| `GET` | `/api/v1/visits/queues/{queue_type}/next` | Call next waiting patient in a queue | `queues` |
| `PATCH` | `/api/v1/visits/queues/{queue_id}/status` | Update status of a queue entry | `queues` |

## Build & Run

```bash
docker compose build visit-service
docker compose up -d visit-service
```

## Run Tests

Ensure you run tests separately from other microservices to prevent virtual environment path collisions:
```bash
venv/bin/pytest services/visit-service/tests -v
```

## Visit Creation Flow

1. Validate request (patient_id, visit_type, payment_type)
2. Generate visit_number: `VIS-YYYYMMDD-XXXX`
3. If insurance: look up `patient_insurance`, verify policy
4. Insert `visits` record
5. Generate queue_number: `T-XXX` (triage)
6. Insert `queues` record with `queue_type='triage'`
7. Return visit + queue_number
