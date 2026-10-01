#!/usr/bin/env bash
# Infrastructure Drift Detection Script
#
# Usage:
#   ./scripts/drift-check.sh --env dev
#   ./scripts/drift-check.sh --env prod --smoke-test
#   ./scripts/drift-check.sh --env all

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV=""
SMOKE_TEST=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --env) [[ $# -ge 2 ]] || { echo "--env requires a value"; exit 1; }; ENV="$2"; shift 2 ;;
    --smoke-test) SMOKE_TEST=true; shift ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ "$ENV" != "dev" && "$ENV" != "uat" && "$ENV" != "prod" && "$ENV" != "all" ]]; then
  echo "Usage: $0 --env <dev|uat|prod|all> [--smoke-test]"
  exit 1
fi

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

check_env() {
  local env="$1"
  log "Checking drift for environment: $env"

  DIFF_OUTPUT=$(cd "${REPO_ROOT}/cdk" && cdk diff "*-${env}" -c env="${env}" 2>&1 || true)

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
  "${SCRIPT_DIR}/smoke-test.sh" --env "$env"
}

# Main
if [[ "$ENV" == "all" ]]; then
  ENVS=(dev uat prod)
else
  ENVS=("$ENV")
fi

FAILED=false
for env in "${ENVS[@]}"; do
  check_env "$env" || FAILED=true
  if [[ "$SMOKE_TEST" == "true" ]]; then
    smoke_test "$env" || FAILED=true
  fi
done

if [[ "$FAILED" == "true" ]]; then
  log "Drift check FAILED"
  exit 1
fi

log "Drift check complete"
