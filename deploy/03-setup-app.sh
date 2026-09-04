#!/usr/bin/env bash
# Copies this repo to /opt/hospitalflow/backend, creates the service account,
# and builds one virtualenv per microservice (mirrors what each Dockerfile did).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

APP_USER="hospitalflow"
BACKEND_DIR="/opt/hospitalflow/backend"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

id -u "${APP_USER}" &>/dev/null || useradd -r -s /usr/sbin/nologin -d /opt/hospitalflow "${APP_USER}"

mkdir -p "${BACKEND_DIR}"
echo "Syncing ${REPO_DIR} -> ${BACKEND_DIR} ..."
rsync -a --delete \
  --exclude '.git' \
  --exclude '**/venv' \
  --exclude '**/__pycache__' \
  --exclude 'deploy' \
  "${REPO_DIR}/" "${BACKEND_DIR}/"

# Services whose code does `from shared...` / uses migrations or scripts at
# runtime — same set as the volume mounts in infrastructure/docker-compose.yml.
# pharmacy-service and report-service read the medicines pack from
# shared/medicines: the assistant answers medicine questions from it and the
# dispensing gate raises its interaction alerts from it, so the two cannot
# disagree.
SHARED_CONSUMERS=(api-gateway auth-service master-service admin-service pharmacy-service report-service)
MIGRATIONS_CONSUMERS=(auth-service master-service)

for svc in "${SHARED_CONSUMERS[@]}"; do
  ln -sfn "${BACKEND_DIR}/shared" "${BACKEND_DIR}/services/${svc}/shared"
done
for svc in "${MIGRATIONS_CONSUMERS[@]}"; do
  ln -sfn "${BACKEND_DIR}/migrations" "${BACKEND_DIR}/services/${svc}/migrations"
done
ln -sfn "${BACKEND_DIR}/scripts" "${BACKEND_DIR}/services/master-service/scripts"

SERVICES=(
  api-gateway auth-service master-service patient-service visit-service
  reception-service triage-service consultation-service laboratory-service
  radiology-service pharmacy-service billing-service ward-service
  admin-service notification-service report-service
)

for svc in "${SERVICES[@]}"; do
  dir="${BACKEND_DIR}/services/${svc}"
  echo "== ${svc}: building venv =="
  python3.11 -m venv "${dir}/venv"
  "${dir}/venv/bin/pip" install --upgrade pip -q
  "${dir}/venv/bin/pip" install -q -r "${dir}/requirements.txt"
done

# Standalone venv for the migration/provisioning/superuser CLI scripts.
echo "== scripts: building venv =="
python3.11 -m venv "${BACKEND_DIR}/scripts/venv"
"${BACKEND_DIR}/scripts/venv/bin/pip" install --upgrade pip -q
"${BACKEND_DIR}/scripts/venv/bin/pip" install -q -r "${BACKEND_DIR}/scripts/requirements-migrate.txt"
"${BACKEND_DIR}/scripts/venv/bin/pip" install -q python-dotenv bcrypt httpx python-keycloak

mkdir -p /var/backups/hospital
chown -R "${APP_USER}:${APP_USER}" /opt/hospitalflow /var/backups/hospital

echo "App code deployed and virtualenvs built under ${BACKEND_DIR}."
