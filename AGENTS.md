## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Context Summary (Session 1)

### Goal
Build multi-tenant hospital management system with FastAPI backend (Keycloak OIDC auth, PostgreSQL) and Streamlit frontend with role-based access control.

### Architecture
- **Backend**: FastAPI on port 8000, Keycloak auth, PostgreSQL, SQLAlchemy ORM
- **Frontend**: Streamlit on port 8501
- **Auth**: Keycloak OIDC with password grant, admin API for user CRUD
- **Multi-tenancy**: Each tenant gets a `tenant_id` (hosp-XXXXXXXX), stored in User model + Keycloak user attribute

### Roles (in order of privilege)
`super_admin` → `hospital_admin` → `nurse` / `clinician` / `doctor` / `patient` → `hospital_user`

### Security Rules
- `super_admin`: Creates/manages other superadmins, manages tenants (activate/suspend), creates roles. **Never** sees or operates on tenant internal data.
- `hospital_admin`: Full CRUD on users within their own tenant only. `tenant_id` comes from JWT, never client input.
- `super_admin` role cannot be changed via edit endpoints.
- Local `role` field is a read-optimization copy; Keycloak realm roles remain authoritative.
- Password changes go through Keycloak Admin REST API (`PUT /users/{id}/reset-password`).

### Key Endpoints
| Prefix | Auth | Description |
|--------|------|-------------|
| `/api/v1/admin/users` | `hospital_admin` | CRUD users within tenant |
| `/api/v1/superadmin/users` | `super_admin` | CRUD superadmin users |
| `/api/v1/superadmin/tenants` | `super_admin` | Manage tenants |
| `/api/v1/superadmin/roles` | `super_admin` | Create roles |

### Database Models
- **User**: `id`, `keycloak_sub`, `username`, `full_name`, `email`, `role`, `hospital_id`
- **Tenant**: `id`, `tenant_id`, `name`, `db_dsn_encrypted`, `status`, `subscription_plan`, `is_active`, `created_at`, `updated_at`

### Completed Features
- Swagger UI `/docs` CSP fix (restrictive header in prod only)
- `.env` synced with `.env.example` (SECRET_KEY, PASSWORD_RESET_TOKEN_TTL)
- `pyarrow` installed (fixes Streamlit `st.table()` crash)
- User CRUD with full_name, username, password, email, role editing
- Super admin management (create/edit/delete) with full_name, username, password, email
- Tenant management (activate/suspend)
- Role creation endpoint
- Signup flow with admin_full_name field
- Password reset via Keycloak admin API
- Rate limiting on all endpoints
- README with role hierarchy, security rules, API endpoint tables, test users

### Key Files
- `app/models/user.py` — User SQLAlchemy model
- `app/core/database.py` — DB engine + auto-migration
- `app/services/keycloak_admin.py` — Keycloak admin API wrapper (create/update/delete users, set password, assign roles)
- `app/api/v1/admin/router.py` — Hospital admin user CRUD
- `app/api/v1/admin/schemas.py` — Hospital admin Pydantic schemas
- `app/api/v1/superadmin/router.py` — Superadmin user/tenant/role management
- `app/api/v1/superadmin/schemas.py` — Superadmin Pydantic schemas
- `app/api/v1/auth/router.py` — Login/signup/password-reset/logout
- `app/api/v1/auth/schemas.py` — Auth Pydantic schemas
- `streamlit_app/app.py` — Streamlit frontend (login, signup, user mgmt, tenant mgmt)

### Test Users (from README)
- Super Admin: `superadmin` / `superadmin123`
- Hospital Admin: `hadmin1` / `admin12345`
- Hospital User: `staff1` / `staff1234`
