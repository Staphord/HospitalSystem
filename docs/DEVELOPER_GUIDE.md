# Hospital Flow Developer Guide

## Local Development Setup

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Git
- VS Code (recommended) with Python extension

### 1. Clone and Install

```bash
git clone <repository-url>
cd hospital-flow
```

### 2. Start Infrastructure

```bash
cd infrastructure
docker compose -p hospital_flow -f docker-compose.yml up -d postgres-master redis rabbitmq
```

### 3. Start Keycloak

```bash
docker run -p 8080:8080 -e KEYCLOAK_ADMIN=admin -e KEYCLOAK_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:26.6.2 start-dev
```

Configure Keycloak (initial setup — per-tenant realms are created automatically):
1. Open http://localhost:8080/admin
2. Login: `admin` / `admin`
3. Create realm: `hospital-realm` (used as the default fallback realm)
4. Ensure `master` realm exists (built-in, used for superadmin auth)
5. Create client in `hospital-realm`: `hospital-api` (confidential)
6. Add redirect URI: `http://localhost:8000/*`
7. Create roles in `hospital-realm`: `super_admin`, `hospital_admin`, `hospital_user`, `nurse`, `clinician`, `doctor`, `patient`
8. Create user in `hospital-realm`: `superadmin` with password `superadmin123`
9. Assign `super_admin` role to `superadmin`
10. Create user in `master` realm: `superadmin` with password `superadmin123` and assign `super_admin` role (optional — login falls back to `hospital-realm` if `master` fails)

### Multi-Realm Architecture

The system uses separate Keycloak realms for identity isolation:
- **`master` realm**: Built-in Keycloak realm — superadmins authenticate here
- **`hospital-realm`**: Default fallback realm (used during migration from single-realm setup)
- **`{tenant_id}` realms**: Auto-created on signup / tenant creation — each tenant gets its own realm

When a hospital signs up or a superadmin creates a tenant, the system:
1. Creates a new Keycloak realm named `{tenant_id}`
2. Creates a `hospital-api` client with `directAccessGrantsEnabled`
3. Adds protocol mappers for `tenant_id` and `email` (user attribute → JWT claim)
4. Registers `tenant_id` in the realm's user profile (required for Keycloak 26+)
5. Creates realm roles: `hospital_admin`, `hospital_user`, `nurse`, `clinician`, `doctor`, `patient`
6. Creates the admin user in the new realm with the `hospital_admin` role

### 4. Environment Variables

Create a `.env` file in the project root:

```env
ENVIRONMENT=dev
DATABASE_URL=postgresql://postgres:nasr@localhost:5432/hospital_master
REDIS_URL=redis://localhost:6380/0
SECRET_KEY=6477db2372e99bef59ff6d4fa4edef3f3891daee3807153d4ea09448bec2f6c6
KEYCLOAK_URL=http://localhost:8080
KEYCLOAK_REALM=hospital-realm
KEYCLOAK_CLIENT_ID=hospital-api
KEYCLOAK_CLIENT_SECRET=HuqlMwVdGchYya4l3qRJwOhgwWQ1z5mL
KEYCLOAK_ADMIN_USERNAME=admin
KEYCLOAK_ADMIN_PASSWORD=admin
TENANT_DB_ENCRYPTION_KEY=RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0=
ALLOWED_ORIGINS=http://localhost:8000,http://localhost:8501
DB_ADMIN_URL=postgresql://postgres:nasr@localhost:5432/postgres
TENANT_DB_TEMPLATE=postgresql://postgres:nasr@localhost:5432/tenant_{tenant_id}
```

### 5. Run Services Locally

```bash
# API Gateway
cd services/api-gateway
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Auth Service
cd services/auth-service
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# Master Service
cd services/master-service
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8002 --reload

# Admin Service
cd services/admin-service
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8018 --reload
```

### 6. Run Streamlit Frontend

```bash
cd streamlit_app
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501

## Project Structure

```
hospital-flow/
├── services/              # 14 microservices
│   ├── api-gateway/
│   ├── auth-service/
│   ├── master-service/
│   ├── admin-service/
│   ├── reception-service/
│   ├── triage-service/
│   ├── consultation-service/
│   ├── laboratory-service/
│   ├── radiology-service/
│   ├── pharmacy-service/
│   ├── billing-service/
│   ├── ward-service/
│   ├── notification-service/
│   └── report-service/
├── infrastructure/
│   ├── docker-compose.yml
│   ├── docker-compose.test.yml
│   └── k8s/              # Kubernetes manifests
├── migrations/
│   ├── master/            # Alembic for global DB
│   └── tenant/            # Alembic for tenant DBs
├── shared/
│   ├── schemas/           # Common Pydantic models
│   ├── security/          # Security utilities
│   └── middleware/        # Shared middleware
├── streamlit_app/         # Streamlit frontend
├── scripts/               # Utility scripts
├── docs/                  # Documentation
└── README.md
```

## Service Development

### Adding a New Endpoint

1. Define Pydantic schema in `app/api/v1/<module>/schemas.py`
2. Add route in `app/api/v1/<module>/router.py`
3. Register router in `app/api/v1/router.py`
4. Add tests in `tests/`

### Database Changes

#### Master DB
```bash
cd migrations/master
alembic revision --autogenerate -m "Add new_column"
alembic upgrade head
```

#### Tenant DB
```bash
cd migrations/tenant
alembic revision --autogenerate -m "Add new_table"
# Run on all tenant DBs using scripts/migrate_existing_tenants.py
```

### Adding a New Service

1. Create directory under `services/`
2. Add `app/main.py`, `app/api/v1/router.py`, `Dockerfile`, `requirements.txt`
3. Register in `infrastructure/docker-compose.yml`
4. Add k8s manifests in `infrastructure/k8s/<service>/`
5. Add route in `services/api-gateway/app/proxy.py`

## Testing

### Unit Tests
```bash
cd services/<service>
pytest tests/unit/
```

### Integration Tests
```bash
cd services/<service>
pytest tests/integration/
```

### Running All Tests
```bash
cd scripts
./run_all_tests.sh
```

### Manual API Testing

```bash
# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"hadmin1","password":"admin12345"}'

# Get users (with token)
curl http://localhost:8000/api/v1/admin/users \
  -H "Authorization: Bearer <token>"
```

## Debugging

### Service Logs
```bash
docker compose -p hospital_flow logs -f <service-name>
```

### Database Queries
```bash
docker compose -p hospital_flow exec postgres-master psql -U postgres -d hospital_master
```

### Redis
```bash
docker compose -p hospital_flow exec redis redis-cli
```

### RabbitMQ
```bash
# Management UI
http://localhost:15672
# Default credentials: guest/guest
```

## Code Style

- Follow PEP 8
- Use type hints
- Use `black` for formatting
- Use `isort` for import sorting
- Use `flake8` for linting

## Git Workflow

1. Create feature branch: `git checkout -b feature/description`
2. Make changes
3. Run tests
4. Commit: `git commit -m "feat: description"`
5. Push: `git push origin feature/description`
6. Create Pull Request

## Troubleshooting

### Import Errors
Ensure `shared/` is in Python path:
```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

### Database Connection Issues
Check if PostgreSQL is running:
```bash
docker compose -p hospital_flow ps postgres-master
```

### Keycloak Token Issues
Verify Keycloak is accessible and check realm certs:
```bash
# Default realm (fallback)
curl http://localhost:8080/realms/hospital-realm/protocol/openid-connect/certs

# Specific tenant realm
curl http://localhost:8080/realms/hosp-xxxxxxxx/protocol/openid-connect/certs

# Master realm (superadmin)
curl http://localhost:8080/realms/master/protocol/openid-connect/certs
```

If login fails with "Failed to query Keycloak for realm" in logs, verify:
1. The tenant's `keycloak_realm` column in the Master DB is set correctly
2. The realm exists in Keycloak (`GET /admin/realms`)
3. The client `hospital-api` exists in the realm with `directAccessGrantsEnabled`

### Rate Limit Blocks
Wait 1 minute or restart Redis:
```bash
docker compose -p hospital_flow restart redis
```

## Contributing

1. Read [ARCHITECTURE.md](ARCHITECTURE.md)
2. Read [SECURITY.md](SECURITY.md)
3. Follow the coding style
4. Write tests for new features
5. Update documentation
6. Submit PR with clear description

## Resources

- FastAPI Docs: https://fastapi.tiangolo.com
- SQLAlchemy Docs: https://docs.sqlalchemy.org
- Keycloak Docs: https://www.keycloak.org/documentation
- Streamlit Docs: https://docs.streamlit.io
- Docker Docs: https://docs.docker.com

## Contact

For developer support, join the Hospital Flow developer channel.
