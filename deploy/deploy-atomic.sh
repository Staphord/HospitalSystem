#!/usr/bin/env bash
# Legacy single-stack Docker release helper. The production GitHub CD workflow
# uses bluegreen-deploy.sh so the active application slot is never recreated
# in place. Keep this helper for controlled maintenance or recovery only.
#
# Usage:
#   sudo ./deploy-atomic.sh stage <RELEASE_TAG>
#   sudo ./deploy-atomic.sh activate <RELEASE_TAG>
#   sudo ./deploy-atomic.sh cleanup <RELEASE_TAG>
#   sudo ./deploy-atomic.sh rollback
#   sudo ./deploy-atomic.sh health
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

ACTION="${1:-stage}"
RELEASE_TAG="${2:-$(date -u +%Y%m%d%H%M%S)}"
BASE_DIR="${BASE_DIR:-/var/www/hospital-backend}"
COMPOSE_FILE="${COMPOSE_FILE:-${BASE_DIR}/infrastructure/docker-compose.yml}"
STATE_DIR="${STATE_DIR:-${BASE_DIR}/.deploy-state}"
PREVIOUS_STATE="${STATE_DIR}/previous-images.tsv"
STAGED_STATE="${STATE_DIR}/staged-release"
CURRENT_TAG_FILE="${STATE_DIR}/current-release"

if [[ ! "${RELEASE_TAG}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Invalid release tag: ${RELEASE_TAG}" >&2
  exit 1
fi

compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

services_to_check() {
  compose config --services | grep -Ev '^(bootstrap|postgres-master|redis|rabbitmq|keycloak)$'
}

save_previous_images() {
  mkdir -p "${STATE_DIR}"
  : > "${PREVIOUS_STATE}"
  while IFS= read -r service; do
    container_id="$(compose ps -q "${service}" || true)"
    [[ -n "${container_id}" ]] || continue
    image_name="$(docker inspect --format '{{.Config.Image}}' "${container_id}")"
    image_id="$(docker inspect --format '{{.Image}}' "${container_id}")"
    printf '%s\t%s\t%s\n' "${service}" "${image_name}" "${image_id}" >> "${PREVIOUS_STATE}"
  done < <(services_to_check)
}

restore_previous_images() {
  [[ -s "${PREVIOUS_STATE}" ]] || {
    echo "No previous image state found at ${PREVIOUS_STATE}." >&2
    return 1
  }

  while IFS=$'\t' read -r service image_name image_id; do
    [[ -n "${service}" && -n "${image_name}" && -n "${image_id}" ]] || continue
    docker image inspect "${image_id}" >/dev/null
    docker tag "${image_id}" "${image_name}"
  done < "${PREVIOUS_STATE}"
}

wait_for_dependencies() {
  echo "Waiting for core dependencies..."
  for attempt in {1..30}; do
    local ready=1
    for service in postgres-master redis rabbitmq keycloak; do
      container_id="$(compose ps -q "${service}" || true)"
      if [[ -z "${container_id}" ]]; then
        ready=0
        break
      fi

      state="$(docker inspect --format '{{.State.Status}}' "${container_id}")"
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "${container_id}")"
      if [[ "${state}" != "running" || "${health}" != "healthy" ]]; then
        ready=0
        break
      fi
    done

    if [[ "${ready}" -eq 1 ]]; then
      return 0
    fi
    sleep 2
  done
  echo "Core dependencies did not become ready." >&2
  return 1
}

health_check() {
  local failed=0
  for service in $(services_to_check); do
    container_id="$(compose ps -q "${service}" || true)"
    if [[ -z "${container_id}" ]]; then
      echo "${service}: container missing" >&2
      failed=1
      continue
    fi

    state="$(docker inspect --format '{{.State.Status}}' "${container_id}")"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "${container_id}")"
    if [[ "${state}" != "running" || ( "${health}" != "healthy" && "${health}" != "no-healthcheck" ) ]]; then
      echo "${service}: state=${state}, health=${health}" >&2
      failed=1
    else
      echo "${service}: ${state}/${health}"
    fi
  done

  gateway_status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/health || true)"
  if [[ "${gateway_status}" != "200" ]]; then
    echo "api-gateway: /health returned ${gateway_status}" >&2
    failed=1
  fi

  return "${failed}"
}

stage_release() {
  echo "== Staging release ${RELEASE_TAG} =="
  mkdir -p "${STATE_DIR}"
  save_previous_images

  compose config --quiet
  compose build --parallel

  if [[ -f "${BASE_DIR}/migrations/master/alembic.ini" ]]; then
    echo "Applying master migrations..."
    compose run --rm master-service alembic -c /app/migrations/master/alembic.ini upgrade head
  fi

  compose up -d --remove-orphans --no-build
  wait_for_dependencies

  for attempt in {1..20}; do
    if health_check; then
      printf '%s\n' "${RELEASE_TAG}" > "${STAGED_STATE}"
      echo "Release ${RELEASE_TAG} passed health checks."
      return 0
    fi
    echo "Health check attempt ${attempt}/20 failed; retrying in 3 seconds..."
    sleep 3
  done

  echo "Release ${RELEASE_TAG} failed health checks; restoring previous images." >&2
  if [[ -s "${PREVIOUS_STATE}" ]]; then
    restore_previous_images
    compose up -d --force-recreate --no-build
  else
    compose down --remove-orphans
  fi
  exit 1
}

activate_release() {
  [[ -f "${STAGED_STATE}" ]] || {
    echo "No staged release is available." >&2
    exit 1
  }
  staged_tag="$(cat "${STAGED_STATE}")"
  [[ "${staged_tag}" == "${RELEASE_TAG}" ]] || {
    echo "Requested release ${RELEASE_TAG} does not match staged release ${staged_tag}." >&2
    exit 1
  }
  printf '%s\n' "${RELEASE_TAG}" > "${CURRENT_TAG_FILE}"
  rm -f "${STAGED_STATE}"
  echo "Release ${RELEASE_TAG} activated."
}

rollback_release() {
  echo "== Rolling back release =="
  restore_previous_images
  compose up -d --force-recreate --no-build
  health_check
  rm -f "${STAGED_STATE}"
  echo "Rollback completed successfully."
}

cleanup_staged_release() {
  echo "== Cleaning up failed release ${RELEASE_TAG} =="
  rollback_release
}

case "${ACTION}" in
  stage) stage_release ;;
  activate) activate_release ;;
  cleanup) cleanup_staged_release ;;
  rollback) rollback_release ;;
  health) health_check ;;
  *)
    echo "Usage: $0 {stage|activate|cleanup|rollback|health} [RELEASE_TAG]" >&2
    exit 1
    ;;
esac
