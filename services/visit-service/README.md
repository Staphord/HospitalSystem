# Visit Service

Creates visit records, handles payment type selection and insurance verification, and assigns triage queue entries.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/visits` | Create a visit + insurance verification + queue assignment |
| GET | `/api/v1/visits/queues/triage/today` | List today's triage queue |

## Build & Run

```bash
docker compose build visit-service
docker compose up -d visit-service
```

## Run Tests

```bash
cd services/visit-service
python -m pytest tests/ -v
```

## Visit Creation Flow

1. Validate request (patient_id, visit_type, payment_type)
2. Generate visit_number: `VIS-YYYYMMDD-XXXX`
3. If insurance: look up `patient_insurance`, verify policy
4. Insert `visits` record
5. Generate queue_number: `T-XXX` (triage)
6. Insert `queues` record with `queue_type='triage'`
7. Return visit + queue_number
