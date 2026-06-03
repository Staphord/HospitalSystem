# Hospital Flow — FastAPI Backend

Production-ready authentication scaffold with Keycloak and JWT validation.

## Quick Start

1. Start services

```powershell
docker-compose up -d --build
```

2. Create test users

```powershell
.venv\Scripts\python.exe setup_keycloak.py
```

3. Run FastAPI

```powershell
uvicorn app.main:app --reload
```

## Test Users

- `testuser` / `testpassword` (role: `hospital_user`)
- `adminuser` / `adminpassword` (role: `hospital_admin`)

## Get a Token

```bash
curl -X POST "http://localhost:8080/realms/hospital-realm/protocol/openid-connect/token" \
	-d "client_id=hospital-api" \
	-d "client_secret=hospital-api-secret" \
	-d "grant_type=password" \
	-d "username=testuser" \
	-d "password=testpassword"
```

## Call Protected Endpoints

```bash
curl -H "Authorization: Bearer <ACCESS_TOKEN>" http://localhost:8000/api/v1/me
curl -H "Authorization: Bearer <ACCESS_TOKEN>" http://localhost:8000/api/v1/patients
```

## Environment Variables

See [.env.example](.env.example) for all values. Key settings:

- `KEYCLOAK_URL`
- `KEYCLOAK_REALM`
- `KEYCLOAK_CLIENT_ID`
- `KEYCLOAK_CLIENT_SECRET`

## HTTPS (Local)

Use mkcert for local TLS and run Uvicorn with certs:

```bash
mkcert localhost 127.0.0.1
uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile localhost-key.pem --ssl-certfile localhost.pem
```

## Refresh Token Rotation

Enable rotation in Keycloak:

- Realm Settings -> Tokens -> Refresh Token Max Reuse = 0

## Tests

Keycloak must be running before tests.

```powershell
.venv\Scripts\pytest.exe -q
```
