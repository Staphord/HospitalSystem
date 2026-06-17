# Hospital Flow Security Guide

## Security Policy

This document outlines the security measures, policies, and best practices for the Hospital Flow platform.

## Authentication & Authorization

### Keycloak Identity Provider
- **Protocol**: OpenID Connect (OIDC) with OAuth2
- **Token Type**: JWT (RS256 for access, HS256 for impersonation)
- **Token Expiry**: 5 minutes (access), 30 minutes (refresh)
- **Introspection**: Optional Keycloak token introspection endpoint
- **JWKS Caching**: 5-minute TTL cache for public keys (per realm)

### Multi-Realm Isolation
The system uses separate Keycloak realms for identity isolation:
- **`master` realm**: Superadmins authenticate here
- **`{tenant_id}` realms**: Each tenant gets its own Keycloak realm (e.g. `hosp-abc123`)
  - Realm is created automatically on signup with a dedicated client, roles, and protocol mappers
  - `tenant_id` is injected as a user attribute and mapped to the JWT via a client protocol mapper
  - Realm user profile declares `tenant_id` as a custom attribute (required for Keycloak 26+)
- **Realm Resolution**: The `iss` (issuer) claim in every JWT determines which realm is used for JWKS verification
- **Fallback**: A default `keycloak_realm` setting is used for backward compatibility with single-realm deployments

### Role-Based Access Control (RBAC)
Roles are enforced at multiple layers:
1. **API Gateway**: JWT verification and role extraction
2. **Service Layer**: `@require_role("super_admin")` decorators
3. **Database Layer**: Tenant ID filtering on every query

### Role Hierarchy
```
super_admin
    └── hospital_admin
            ├── nurse
            ├── clinician
            ├── doctor
            ├── patient
            └── hospital_user
```

### Privilege Rules
- `super_admin`: Global access, tenant management, subscription control
- `hospital_admin`: Full CRUD within their own tenant only
- `hospital_user`: Basic access within their tenant
- `super_admin` role cannot be assigned or modified via edit endpoints

## Input Validation & Sanitization

### Password Policy
All passwords must meet the following criteria:
- Minimum 8 characters, maximum 128 characters
- At least one uppercase letter (A-Z)
- At least one lowercase letter (a-z)
- At least one digit (0-9)
- At least one special character (`!@#$%^&*()_+-=[]{}|;:,.<>?`)
- No whitespace characters
- No common weak patterns (e.g., "password", "123456", "qwerty")

### Username Sanitization
- Must be 3-255 characters
- Must start with alphanumeric
- Only alphanumeric, underscore, hyphen, and dot allowed
- No consecutive special characters
- Leading/trailing whitespace stripped

### Email Validation
- Standard `EmailStr` validation via Pydantic
- Must be unique across the system

## Brute-Force Protection

### Login Endpoints
- Tracks failed attempts per `username + IP` combination
- Threshold: 5 failed attempts within a 5-minute window
- Block duration: 5 minutes
- Storage: Redis with auto-expiring keys
- Successful login clears the counter

### Rate Limiting
- All endpoints protected by `slowapi` rate limiting
- Login: 10 requests per minute
- Signup: 5 requests per minute
- Password reset: 3 requests per minute
- Admin operations: 30 requests per minute

## Data Protection

### Encryption at Rest
- **Tenant DB DSNs**: Encrypted with Fernet (AES-128-CBC + HMAC)
- **Encryption Key**: `TENANT_DB_ENCRYPTION_KEY` (must be 32 bytes base64)
- **Storage**: Encrypted DSNs stored in `tenants.db_dsn_encrypted`

### Encryption in Transit
- **TLS**: All services communicate over HTTPS in production
- **HSTS**: `max-age=31536000; includeSubDomains`
- **Certificate**: Managed via reverse proxy (nginx/traefik)

### Secret Management
- **Environment Variables**: All secrets loaded via `.env` files
- **Never Commit**: `.env` files are in `.gitignore`
- **Key Rotation**: Encryption keys should be rotated periodically
- **Keycloak Secrets**: Client secret and admin credentials stored in env vars

## Security Headers

All HTTP responses include the following headers:

| Header | Value | Purpose |
|--------|-------|---------|
| `X-Content-Type-Options` | `nosniff` | Prevents MIME sniffing |
| `X-Frame-Options` | `DENY` | Prevents clickjacking |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | Enforces HTTPS |
| `Content-Security-Policy` | `default-src 'none'` (prod only) | Prevents XSS |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limits referrer leakage |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` | Restricts browser APIs |
| `X-Request-ID` | UUID | Request tracing |
| `X-Process-Time-Ms` | float | Performance monitoring |

## Request Security

### Body Size Limits
- Maximum request body size: 1 MB
- Exceeding requests receive HTTP 413 with `PAYLOAD_TOO_LARGE` error

### CORS Policy
- Configured per environment via `ALLOWED_ORIGINS`
- Dev: `http://localhost:8000, http://localhost:8501`
- Prod: Strict origin matching only
- Credentials allowed for authenticated requests

### Audit Logging
All non-GET operations are logged to `global_audit_logs`:
- User subject (from JWT)
- Tenant ID
- Action (HTTP method or business action)
- Request path
- Status code
- Processing time
- IP address
- Timestamp

## Tenant Isolation

### Database Isolation
- Each tenant has a dedicated PostgreSQL database
- Tenant DB DSN is resolved via Redis cache or Master DB fallback
- DSN injected as `X-Tenant-DB` header for downstream services

### Suspension & Termination
- **Suspended tenants**: Blocked at login, refresh, and gateway proxy
- **Terminated tenants**: Irreversible, sessions revoked, Redis blocklist
- **Blocklist**: Redis keys with TTL for automatic expiration
- **Session Revocation**: Keycloak sessions revoked via Admin API

### Force Password Change
- New tenant admins are forced to change their temporary password on first login
- `force_password_change` flag in user record and Keycloak
- Streamlit intercepts the flag and shows a password change form

## API Security

### Public Endpoints (No Authentication)
- `GET /health`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/superadmin/login`
- `POST /api/v1/auth/signup`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/password-reset`
- `POST /api/v1/auth/password-reset/confirm`

### Protected Endpoints
- All other endpoints require a valid Bearer token
- Token must not be expired or revoked
- Tenant must be active (not suspended or terminated)

### Super Admin Endpoints
- All `/api/v1/superadmin/*` endpoints require `super_admin` role
- Additional local `super_admin` record sync on login

## Vulnerability Prevention

### SQL Injection
- All database queries use SQLAlchemy ORM or parameterized statements
- Never construct raw SQL with user input
- Alembic migrations are version-controlled and reviewed

### XSS (Cross-Site Scripting)
- `Content-Security-Policy` header prevents inline scripts
- No direct HTML rendering of user input
- Streamlit handles frontend sanitization

### CSRF (Cross-Site Request Forgery)
- Stateless API design (no cookies)
- Bearer token required for all state-changing operations
- CORS restricts cross-origin requests

### Sensitive Data Exposure
- Passwords are never logged or returned in responses
- Tokens are redacted in logs
- `redact_sensitive_data()` utility available for request/response logging
- DSNs are encrypted before storage

## Incident Response

### Security Event Logging
Critical events are logged with high visibility:
- Brute-force block activation
- Tenant suspension/termination
- Failed login attempts
- Password changes
- Role changes
- Unauthorized access attempts

### Monitoring
- Health check endpoints for all services
- Request ID tracking for request tracing
- Process time headers for performance monitoring
- Redis connection monitoring

## Security Checklist

### Deployment
- [ ] All secrets rotated from development defaults
- [ ] Keycloak realm configured with strong policies
- [ ] HTTPS enforced in production
- [ ] HSTS enabled
- [ ] CSP headers configured
- [ ] Rate limiting enabled
- [ ] Redis blocklist TTL configured
- [ ] Database connections use SSL
- [ ] `.env` files not committed to version control

### Development
- [ ] Password policy enforced in all forms
- [ ] Input validation on all endpoints
- [ ] No sensitive data in logs
- [ ] Brute-force protection active
- [ ] Security headers present on all responses
- [ ] Tenant isolation verified

## Contact

For security issues, contact the Hospital Flow security team.
