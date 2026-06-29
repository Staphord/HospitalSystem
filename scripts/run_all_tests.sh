#!/usr/bin/env bash
set -e

SERVICES=(
  api-gateway
  auth-service
  master-service
  patient-service
  visit-service
  reception-service
  triage-service
  consultation-service
  laboratory-service
  radiology-service
  pharmacy-service
  billing-service
  ward-service
  admin-service
  notification-service
  report-service
)

for svc in "${SERVICES[@]}"; do
  echo "========================================"
  echo "Running tests for: $svc"
  echo "========================================"
  if [ -d "services/$svc" ]; then
    cd "services/$svc"
    if [ -f "pytest.ini" ] || [ -d "tests" ]; then
      if [ -f "../../.env" ]; then
        export $(grep -v '^#' ../../.env | sed 's/#.*//g' | xargs)
      fi
      PYTHONPATH=.:../.. pytest -q || echo "Tests failed for $svc"
    else
      echo "No tests found for $svc"
    fi
    cd - > /dev/null
  else
    echo "Directory services/$svc not found"
  fi
  echo ""
done

echo "All service tests completed."
