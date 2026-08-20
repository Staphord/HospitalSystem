#!/usr/bin/env bash
# Blue/green deployment for the Docker Compose VPS installation.
# Stateful infrastructure remains shared; only stateless application services
# are duplicated between slots.
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

ACTION="${1:-stage}"
RELEASE_TAG="${2:-$(date -u +%Y%m%d%H%M%S)}"
BASE_DIR="${BASE_DIR:-/var/www/hospital-backend}"
CANONICAL_COMPOSE="${COMPOSE_FILE:-${BASE_DIR}/infrastructure/docker-compose.yml}"
STATE_DIR="${STATE_DIR:-${BASE_DIR}/.deploy-state}"
ACTIVE_SLOT_FILE="${STATE_DIR}/active-slot"
STAGED_STATE="${STATE_DIR}/staged-bluegreen"
SHARED_NETWORK="${SHARED_NETWORK:-hospitalsystem_hospital-network}"
INFRA_PROJECT="${INFRA_PROJECT:-hospitalsystem}"
BLUE_PROJECT="${BLUE_PROJECT:-hospitalsystem-blue}"
GREEN_PROJECT="${GREEN_PROJECT:-hospitalsystem-green}"
BLUE_PORT="${BLUE_PORT:-18001}"
GREEN_PORT="${GREEN_PORT:-18002}"
TMP_DIR="${TMPDIR:-/tmp}/hospital-bluegreen"

mkdir -p "${STATE_DIR}" "${TMP_DIR}"

slot_project() {
  [[ "${1}" == blue ]] && echo "${BLUE_PROJECT}" || echo "${GREEN_PROJECT}"
}

slot_port() {
  [[ "${1}" == blue ]] && echo "${BLUE_PORT}" || echo "${GREEN_PORT}"
}

slot_file() {
  echo "${TMP_DIR}/$1.json"
}

compose() {
  docker compose "$@"
}

app_services() {
  compose -f "${CANONICAL_COMPOSE}" config --services \
    | grep -Ev '^(postgres-master|redis|rabbitmq|keycloak|bootstrap)$'
}

ensure_shared_infrastructure() {
  if ! docker network inspect "${SHARED_NETWORK}" >/dev/null 2>&1; then
    echo "Shared network ${SHARED_NETWORK} is missing; starting infrastructure."
    compose -p "${INFRA_PROJECT}" -f "${CANONICAL_COMPOSE}" config --format json \
      > "${TMP_DIR}/canonical.json"
    python3 "${BASE_DIR}/deploy/compose-slot.py" \
      "${TMP_DIR}/canonical.json" "$(slot_file infra)" infra "${SHARED_NETWORK}" 0 infra
    compose -p "${INFRA_PROJECT}" -f "$(slot_file infra)" up -d
  fi

  for attempt in {1..30}; do
    local ready=1
    for service in postgres-master redis rabbitmq keycloak; do
      local container_id state health
      container_id="$(compose -p "${INFRA_PROJECT}" -f "${CANONICAL_COMPOSE}" ps -q "${service}" || true)"
      if [[ -z "${container_id}" ]]; then
        ready=0
        break
      fi
      state="$(docker inspect --format '{{.State.Status}}' "${container_id}")"
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "${container_id}")"
      if [[ "${state}" != running || ( "${health}" != healthy && "${health}" != no-healthcheck ) ]]; then
        ready=0
        break
      fi
    done
    [[ "${ready}" -eq 1 ]] && return 0
    sleep 3
  done
  echo "Shared infrastructure did not become healthy." >&2
  return 1
}

prepare_slot_file() {
  local slot="$1"
  compose -f "${CANONICAL_COMPOSE}" config --format json > "${TMP_DIR}/canonical.json"
  python3 "${BASE_DIR}/deploy/compose-slot.py" \
    "${TMP_DIR}/canonical.json" "$(slot_file "${slot}")" slot "${SHARED_NETWORK}" "$(slot_port "${slot}")" "${slot}"
}

slot_compose() {
  local slot="$1"
  compose -p "$(slot_project "${slot}")" -f "$(slot_file "${slot}")"
}

wait_for_slot() {
  local slot="$1"
  local port
  port="$(slot_port "${slot}")"
  for attempt in {1..30}; do
    local healthy=1
    while IFS= read -r service; do
      local container_id state health
      container_id="$(slot_compose "${slot}" ps -q "${service}" || true)"
      if [[ -z "${container_id}" ]]; then
        healthy=0
        break
      fi
      state="$(docker inspect --format '{{.State.Status}}' "${container_id}")"
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "${container_id}")"
      if [[ "${state}" != running || ( "${health}" != healthy && "${health}" != no-healthcheck ) ]]; then
        healthy=0
        break
      fi
    done < <(app_services)

    if [[ "${healthy}" -eq 1 ]] && curl -fsS --max-time 5 "http://127.0.0.1:${port}/health" >/dev/null; then
      return 0
    fi
    sleep 3
  done
  return 1
}

stage_slot() {
  local slot="$1"
  ensure_shared_infrastructure
  prepare_slot_file "${slot}"
  slot_compose "${slot}" build --parallel
  slot_compose "${slot}" run --rm --no-deps master-service \
    alembic -c /app/migrations/master/alembic.ini upgrade head
  slot_compose "${slot}" up -d --remove-orphans
  wait_for_slot "${slot}"
  printf '%s\n' "${slot}" > "${STAGED_STATE}.slot"
  printf '%s\n' "${RELEASE_TAG}" > "${STAGED_STATE}.tag"
}

switch_nginx() {
  local slot="$1"
  local port
  port="$(slot_port "${slot}")"
  cat > /etc/nginx/conf.d/hospitalflow-backend-upstream.conf <<EOF
upstream hospitalflow_backend {
    server 127.0.0.1:${port};
    keepalive 32;
}
EOF
  nginx -t
  systemctl reload nginx
  printf '%s\n' "${slot}" > "${ACTIVE_SLOT_FILE}"
}

stop_slot() {
  local slot="$1"
  [[ -f "$(slot_file "${slot}")" ]] || return 0
  slot_compose "${slot}" down --remove-orphans --volumes=false || true
}

activate_slot() {
  local slot
  slot="$(cat "${STAGED_STATE}.slot")"
  [[ "$(cat "${STAGED_STATE}.tag")" == "${RELEASE_TAG}" ]] || {
    echo "Staged release tag mismatch." >&2
    exit 1
  }
  local previous=""
  [[ -f "${ACTIVE_SLOT_FILE}" ]] && previous="$(cat "${ACTIVE_SLOT_FILE}")"
  switch_nginx "${slot}"
  if [[ -n "${previous}" && "${previous}" != "${slot}" ]]; then
    stop_slot "${previous}"
  fi
  rm -f "${STAGED_STATE}.slot" "${STAGED_STATE}.tag"
}

rollback() {
  local active=""
  [[ -f "${ACTIVE_SLOT_FILE}" ]] && active="$(cat "${ACTIVE_SLOT_FILE}")"
  local staged=""
  [[ -f "${STAGED_STATE}.slot" ]] && staged="$(cat "${STAGED_STATE}.slot")"
  if [[ -n "${staged}" && "${staged}" != "${active}" ]]; then
    stop_slot "${staged}"
  fi
  [[ -n "${active}" ]] && switch_nginx "${active}"
  rm -f "${STAGED_STATE}.slot" "${STAGED_STATE}.tag"
}

case "${ACTION}" in
  stage)
    [[ "${RELEASE_TAG}" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "Invalid release tag." >&2; exit 1; }
    current="blue"
    [[ -f "${ACTIVE_SLOT_FILE}" ]] && current="$(cat "${ACTIVE_SLOT_FILE}")"
    [[ "${current}" == blue ]] && target=green || target=blue
    stage_slot "${target}"
    echo "Staged ${RELEASE_TAG} on ${target}."
    ;;
  activate) activate_slot ;;
  rollback) rollback ;;
  health)
    [[ -f "${ACTIVE_SLOT_FILE}" ]] || { echo "No active blue/green slot." >&2; exit 1; }
    wait_for_slot "$(cat "${ACTIVE_SLOT_FILE}")"
    ;;
  *) echo "Usage: $0 {stage|activate|rollback|health} [RELEASE_TAG]" >&2; exit 1 ;;
esac
