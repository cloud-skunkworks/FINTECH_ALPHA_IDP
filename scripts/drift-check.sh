#!/usr/bin/env bash
# Infrastructure Drift Detection Script
#
# Usage:
#   ./scripts/drift-check.sh --env dev
#   ./scripts/drift-check.sh --env prod --smoke-test
#   ./scripts/drift-check.sh --env all

set -euo pipefail

ENV=""
SMOKE_TEST=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --env) ENV="$2"; shift 2 ;;
    --smoke-test) SMOKE_TEST=true; shift ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

check_env() {
  local env="$1"
  log "Checking drift for environment: $env"

  cd cdk
  DIFF_OUTPUT=$(cdk diff "*-${env}" -c env="${env}" 2>&1 || true)
  cd ..

  if echo "$DIFF_OUTPUT" | grep -qE "^\["; then
    log "DRIFT DETECTED in $env:"
    echo "$DIFF_OUTPUT"
    return 1
  else
    log "No drift detected in $env"
    return 0
  fi
}

smoke_test() {
  local env="$1"
  local api_url="https://api.idp.${env}.internal.example.com"

  if [[ "$env" == "prod" ]]; then
    api_url="https://api.idp.internal.example.com"
  fi

  log "Running smoke tests against $env ($api_url)..."

  # Liveness
  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    "${api_url}/healthz" || echo "000")

  if [[ "$HTTP_STATUS" == "200" ]]; then
    log "Liveness check PASSED ($HTTP_STATUS)"
  else
    log "Liveness check FAILED ($HTTP_STATUS)"
    return 1
  fi

  log "Smoke tests PASSED for $env"
}

# Main
if [[ "$ENV" == "all" ]]; then
  FAILED=false
  for env in dev uat prod; do
    check_env "$env" || FAILED=true
  done
  $FAILED && exit 1
else
  check_env "$ENV"
fi

if [[ "$SMOKE_TEST" == "true" ]]; then
  smoke_test "$ENV"
fi

log "Drift check complete"
