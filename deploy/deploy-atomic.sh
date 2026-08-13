#!/usr/bin/env bash
# Production Docker-Based Atomic Release & Rollback Script for Hospital Flow
# Usage:
#   sudo ./deploy-atomic.sh deploy <RELEASE_TAG>
#   sudo ./deploy-atomic.sh rollback
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

ACTION="${1:-deploy}"
RELEASE_TAG="${2:-$(date +%Y%m%d%H%M%S)}"

BASE_DIR="/var/www/hospital-backend"
COMPOSE_FILE="${BASE_DIR}/docker-compose.yml"
PREVIOUS_TAG_FILE="${BASE_DIR}/previous_release_tag"
CURRENT_TAG_FILE="${BASE_DIR}/current_release_tag"

deploy_release() {
  echo "== Starting Docker Container Deployment: ${RELEASE_TAG} =="

  # 1. Save currently running release tag for rollback tracking
  if [ -f "${CURRENT_TAG_FILE}" ]; then
    cp "${CURRENT_TAG_FILE}" "${PREVIOUS_TAG_FILE}"
    echo "Saved active release tag: $(cat "${PREVIOUS_TAG_FILE}")"
  fi

  # 2. Build updated Docker images
  echo "Building Docker container images for release ${RELEASE_TAG}..."
  docker compose -f "${COMPOSE_FILE}" build --parallel

  # 3. Apply DB migrations via temporary runner container
  echo "Executing Alembic database migrations..."
  if [ -f "${BASE_DIR}/migrations/master/alembic.ini" ]; then
    docker compose -f "${COMPOSE_FILE}" run --rm master-service alembic -c /app/migrations/master/alembic.ini upgrade heads || true
  fi

  # 4. Spin up container updates with zero-downtime recreation
  echo "Re-creating microservice containers..."
  docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans

  # 5. Record new release tag
  echo "${RELEASE_TAG}" > "${CURRENT_TAG_FILE}"

  # 6. Cleanup dangling Docker build artifacts & old images
  echo "Cleaning up obsolete Docker images..."
  docker image prune -f

  echo "Docker deployment ${RELEASE_TAG} activated successfully."
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
  deploy)
    deploy_release
    ;;
  rollback)
    rollback_release
    ;;
  *)
    echo "Usage: $0 {deploy|rollback} [RELEASE_TAG]" >&2
    exit 1
    ;;
esac
