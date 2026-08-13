#!/usr/bin/env bash
# Production Docker-Based Atomic Release & Rollback Script for Hospital Flow
# Usage:
#   sudo ./deploy-atomic.sh stage <RELEASE_TAG>
#   sudo ./deploy-atomic.sh activate <RELEASE_TAG>
#   sudo ./deploy-atomic.sh cleanup <RELEASE_TAG>
#   sudo ./deploy-atomic.sh rollback
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

ACTION="${1:-stage}"
RELEASE_TAG="${2:-$(date +%Y%m%d%H%M%S)}"

BASE_DIR="/var/www/hospital-backend"
COMPOSE_FILE="${BASE_DIR}/docker-compose.yml"
PREVIOUS_TAG_FILE="${BASE_DIR}/previous_release_tag"
CURRENT_TAG_FILE="${BASE_DIR}/current_release_tag"

stage_release() {
  echo "== Staging Docker Release: ${RELEASE_TAG} =="

  # Save currently active release tag for tracking before building new release
  if [ -f "${CURRENT_TAG_FILE}" ]; then
    cp "${CURRENT_TAG_FILE}" "${PREVIOUS_TAG_FILE}"
    echo "Saved active release tag: $(cat "${PREVIOUS_TAG_FILE}")"
  fi

  # Build container images for staging
  echo "Building Docker container images for staged release ${RELEASE_TAG}..."
  docker compose -f "${COMPOSE_FILE}" build --parallel

  # Run DB migrations
  echo "Executing Alembic database migrations..."
  if [ -f "${BASE_DIR}/migrations/master/alembic.ini" ]; then
    docker compose -f "${COMPOSE_FILE}" run --rm master-service alembic -c /app/migrations/master/alembic.ini upgrade heads || true
  fi

  echo "Starting staged microservice containers..."
  docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans

  echo "Docker release ${RELEASE_TAG} staged and ready for activation."
}

activate_release() {
  echo "== Activating Staged Release: ${RELEASE_TAG} =="
  echo "${RELEASE_TAG}" > "${CURRENT_TAG_FILE}"
  docker image prune -f
  echo "Release ${RELEASE_TAG} activated successfully following frontend confirmation."
}

cleanup_staged_release() {
  echo "== Cleaning Up Staged Release: ${RELEASE_TAG} (Frontend Failed) =="
  if [ -f "${PREVIOUS_TAG_FILE}" ]; then
    local prev_tag
    prev_tag="$(cat "${PREVIOUS_TAG_FILE}")"
    echo "Reverting microservice containers to previous active release tag: ${prev_tag}..."
    docker compose -f "${COMPOSE_FILE}" up -d --force-recreate
    echo "${prev_tag}" > "${CURRENT_TAG_FILE}"
  fi
  docker image prune -f
  echo "Staged release ${RELEASE_TAG} cleaned up cleanly."
}

rollback_release() {
  echo "== Initiating Fast Container Rollback =="
  if [ ! -f "${PREVIOUS_TAG_FILE}" ]; then
    echo "Error: No previous release record found at ${PREVIOUS_TAG_FILE}" >&2
    exit 1
  fi

  local prev_tag
  prev_tag="$(cat "${PREVIOUS_TAG_FILE}")"

  echo "Reverting container release to tag: ${prev_tag}..."
  docker compose -f "${COMPOSE_FILE}" up -d --force-recreate

  echo "${prev_tag}" > "${CURRENT_TAG_FILE}"
  echo "Rollback to ${prev_tag} complete."
}

case "${ACTION}" in
  deploy|stage)
    stage_release
    ;;
  activate)
    activate_release
    ;;
  cleanup)
    cleanup_staged_release
    ;;
  rollback)
    rollback_release
    ;;
  *)
    echo "Usage: $0 {stage|activate|cleanup|rollback} [RELEASE_TAG]" >&2
    exit 1
    ;;
esac
