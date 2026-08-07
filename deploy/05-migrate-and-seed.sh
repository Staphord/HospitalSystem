#!/usr/bin/env bash
# Runs the master-DB Alembic migrations and creates the first super_admin user.
# Run this AFTER 04-generate-env-and-systemd.sh (needs common.env + Keycloak up).
#
# You do NOT need to manually provision a tenant/hospital database: once you
# log into the Master portal (frontend, /master/login) as the super_admin
# created here, creating a tenant from that UI drives master-service's own
# provisioning flow, which creates the tenant's Postgres database, encrypts
# its DSN, and runs the tenant Alembic migrations automatically.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo) — it needs to read /opt/hospitalflow/env/common.env." >&2
  exit 1
fi

BACKEND_DIR="/opt/hospitalflow/backend"
ENV_FILE="/opt/hospitalflow/env/common.env"

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

echo "== Running master DB migrations =="
cd "${BACKEND_DIR}/services/master-service"
venv/bin/python -m alembic -c migrations/master/alembic.ini upgrade head

echo
read -rp "Super admin username [superadmin]: " SU_USER
SU_USER="${SU_USER:-superadmin}"
read -rp "Super admin email: " SU_EMAIL
read -rsp "Super admin password: " SU_PASS
echo

echo "== Creating super admin (Keycloak + master DB) =="
cd "${BACKEND_DIR}"
scripts/venv/bin/python scripts/create_superuser.py \
  --username "${SU_USER}" --password "${SU_PASS}" --email "${SU_EMAIL}" --role super_admin

echo
echo "Done. Log into the frontend's /master/login as '${SU_USER}' and create"
echo "your first hospital tenant from there — the tenant's database and"
echo "Alembic schema are provisioned automatically by master-service."
