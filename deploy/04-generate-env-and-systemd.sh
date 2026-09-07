#!/usr/bin/env bash
# Builds /opt/hospitalflow/env/common.env from env.template, generates one
# systemd unit per microservice, then enables and starts all 16.
#
# Required environment variables (export these before running):
#   PG_PASSWORD             - printed by 01-setup-databases.sh
#   KEYCLOAK_ADMIN_PASSWORD - printed by 02-install-keycloak.sh
#   VPS_IP                  - your VPS's public IP (used for CORS/ALLOWED_ORIGINS)
#
# Optional:
#   GROQ_API_KEY            - turns the Hospital Assistant on, chat and chat
#                             history together. Left out, both stay off and the
#                             assistant is simply absent: a launcher that fails
#                             on every question is worse than no launcher. The
#                             key is written only into common.env (mode 640,
#                             owned by the service user) and never into a log.
#
# Example:
#   sudo PG_PASSWORD=xxxx KEYCLOAK_ADMIN_PASSWORD=yyyy VPS_IP=203.0.113.10 \
#     ./deploy/04-generate-env-and-systemd.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

: "${PG_PASSWORD:?Set PG_PASSWORD (from step 01) before running this script}"
: "${KEYCLOAK_ADMIN_PASSWORD:?Set KEYCLOAK_ADMIN_PASSWORD (from step 02) before running this script}"
: "${VPS_IP:?Set VPS_IP (e.g. your VPS public IP) before running this script}"

APP_USER="hospitalflow"
BACKEND_DIR="/opt/hospitalflow/backend"
ENV_DIR="/opt/hospitalflow/env"
ENV_FILE="${ENV_DIR}/common.env"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The assistant follows its key: supplied means on, absent means off. Both
# flags move together, because chat history with no chat stores nothing, and
# chat with no history is the thing staff report as the assistant forgetting.
GROQ_API_KEY="${GROQ_API_KEY:-}"
if [ -n "${GROQ_API_KEY}" ]; then
  ASSISTANT_ENABLED="true"
else
  ASSISTANT_ENABLED="false"
fi

SECRET_KEY="$(openssl rand -hex 32)"
TENANT_KEY="$("${BACKEND_DIR}/scripts/venv/bin/python" -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")"

mkdir -p "${ENV_DIR}"
sed \
  -e "s#__PG_PASSWORD__#${PG_PASSWORD}#g" \
  -e "s#__KEYCLOAK_ADMIN_PASSWORD__#${KEYCLOAK_ADMIN_PASSWORD}#g" \
  -e "s#__VPS_IP__#${VPS_IP}#g" \
  -e "s#__SECRET_KEY__#${SECRET_KEY}#g" \
  -e "s#__TENANT_DB_ENCRYPTION_KEY__#${TENANT_KEY}#g" \
  -e "s#__ASSISTANT_ENABLED__#${ASSISTANT_ENABLED}#g" \
  -e "s#__GROQ_API_KEY__#${GROQ_API_KEY}#g" \
  "${SCRIPT_DIR}/env.template" > "${ENV_FILE}"

chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"
chmod 640 "${ENV_FILE}"
echo "Wrote ${ENV_FILE}"
# Whether a key was supplied, never the key itself.
if [ "${ASSISTANT_ENABLED}" = "true" ]; then
  echo "Hospital Assistant: chat and chat history enabled (GROQ_API_KEY supplied)"
else
  echo "Hospital Assistant: off (no GROQ_API_KEY given); re-run with one to enable"
fi

# name:port — matches infrastructure/docker-compose.yml exactly.
SERVICES=(
  "api-gateway:8000"
  "auth-service:8001"
  "master-service:8002"
  "patient-service:8005"
  "visit-service:8006"
  "reception-service:8010"
  "triage-service:8011"
  "consultation-service:8012"
  "laboratory-service:8013"
  "radiology-service:8014"
  "pharmacy-service:8015"
  "billing-service:8016"
  "ward-service:8017"
  "admin-service:8018"
  "notification-service:8019"
  "report-service:8020"
)

for entry in "${SERVICES[@]}"; do
  svc="${entry%%:*}"
  port="${entry##*:}"
  dir="${BACKEND_DIR}/services/${svc}"

  cat >"/etc/systemd/system/hospital-${svc}.service" <<EOF
[Unit]
Description=Hospital Flow - ${svc}
After=network.target postgresql.service redis-server.service rabbitmq-server.service keycloak.service
Wants=postgresql.service redis-server.service rabbitmq-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${dir}
EnvironmentFile=${ENV_FILE}
ExecStart=${dir}/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port ${port} --workers 2
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  echo "Generated hospital-${svc}.service (port ${port})"
done

systemctl daemon-reload
for entry in "${SERVICES[@]}"; do
  svc="${entry%%:*}"
  systemctl enable "hospital-${svc}" >/dev/null
  systemctl restart "hospital-${svc}"
done

echo
echo "All 16 services enabled and started."
echo "Check status:  systemctl status hospital-api-gateway"
echo "Tail logs:     journalctl -u hospital-auth-service -f"
