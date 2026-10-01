#!/usr/bin/env bash
# IDP Smoke Test Script
#
# Usage:
#   ./scripts/smoke-test.sh --env dev
#   ./scripts/smoke-test.sh --env prod --dry-run
#   ./scripts/smoke-test.sh --env uat --api-url https://api.example.test --require-k8s
#
# Checks:
#   1. API liveness   (GET /healthz  -> 200)
#   2. API readiness  (GET /readyz   -> 200)
#   3. EKS nodes      (all nodes Ready)         [kubectl]
#   4. EKS pods       (no Failed/CrashLoop/Pending pods in platform namespaces) [kubectl]
#
# Kubernetes checks are skipped with a warning when kubectl/the cluster is not
# reachable, unless --require-k8s is given. Use --skip-k8s to skip them outright.
#
# Exit code: 0 = all executed checks passed, 1 = at least one failed.

set -euo pipefail

ENV=""
DRY_RUN=false
SKIP_K8S=false
REQUIRE_K8S=false
API_URL=""
MAX_TIME="${SMOKE_MAX_TIME:-10}"
RETRIES="${SMOKE_RETRIES:-3}"
AWS_REGION="${AWS_REGION:-ca-central-1}"
# Namespaces whose pods must be healthy (space separated); override via env.
K8S_NAMESPACES="${SMOKE_K8S_NAMESPACES:-platform monitoring gatekeeper-system}"

usage() {
  echo "Usage: $0 --env <dev|uat|prod> [--dry-run] [--api-url URL] [--skip-k8s] [--require-k8s]"
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --env) [[ $# -ge 2 ]] || { usage; exit 1; }; ENV="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --api-url) [[ $# -ge 2 ]] || { usage; exit 1; }; API_URL="$2"; shift 2 ;;
    --skip-k8s) SKIP_K8S=true; shift ;;
    --require-k8s) REQUIRE_K8S=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ "$ENV" != "dev" && "$ENV" != "uat" && "$ENV" != "prod" ]]; then
  echo "Invalid or missing environment: '${ENV}'. Must be dev, uat, or prod."
  usage
  exit 1
fi

if [[ -z "$API_URL" ]]; then
  if [[ "$ENV" == "prod" ]]; then
    API_URL="https://api.idp.internal.example.com"
  else
    API_URL="https://api.idp.${ENV}.internal.example.com"
  fi
fi
API_URL="${API_URL%/}"
CLUSTER="idp-eks-${ENV}"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
FAILURES=0
fail() { log "FAIL: $*"; FAILURES=$((FAILURES + 1)); }
pass() { log "PASS: $*"; }

# Run a command, or just print it under --dry-run.
dry() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] $*"
    return 0
  fi
  "$@"
}

http_check() {
  local name="$1" path="$2" status="000" attempt
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] curl --max-time ${MAX_TIME} ${API_URL}${path} (expect 200)"
    return 0
  fi
  for ((attempt = 1; attempt <= RETRIES; attempt++)); do
    status=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$MAX_TIME" "${API_URL}${path}" || true)
    [[ "$status" == "200" ]] && break
    [[ $attempt -lt $RETRIES ]] && sleep 3
  done
  if [[ "$status" == "200" ]]; then
    pass "${name} (${path} -> ${status})"
  else
    fail "${name} (${path} -> ${status})"
  fi
}

k8s_checks() {
  if [[ "$SKIP_K8S" == "true" ]]; then
    log "Kubernetes checks skipped (--skip-k8s)"
    return 0
  fi

  if [[ "$DRY_RUN" == "true" ]]; then
    dry aws eks update-kubeconfig --name "$CLUSTER" --region "$AWS_REGION"
    dry kubectl get nodes --no-headers
    dry kubectl get pods -n "<namespace>" --no-headers
    return 0
  fi

  if ! command -v kubectl >/dev/null 2>&1; then
    k8s_unavailable "kubectl not found"
    return 0
  fi
  if command -v aws >/dev/null 2>&1; then
    aws eks update-kubeconfig --name "$CLUSTER" --region "$AWS_REGION" >/dev/null 2>&1 || true
  fi

  local nodes
  if ! nodes=$(kubectl get nodes --no-headers --request-timeout=20s 2>/dev/null); then
    k8s_unavailable "cannot reach cluster ${CLUSTER}"
    return 0
  fi

  local total not_ready
  total=$(grep -c . <<<"$nodes" || true)
  not_ready=$(awk '$2 !~ /^Ready/ {print $1}' <<<"$nodes")
  if [[ "$total" -gt 0 && -z "$not_ready" ]]; then
    pass "all ${total} node(s) Ready"
  else
    fail "nodes not Ready (total=${total}): ${not_ready:-none found}"
  fi

  local ns pods bad
  for ns in $K8S_NAMESPACES; do
    if ! pods=$(kubectl get pods -n "$ns" --no-headers --request-timeout=20s 2>/dev/null); then
      log "WARN: namespace '${ns}' not readable, skipping"
      continue
    fi
    [[ -z "$pods" ]] && { log "WARN: no pods in namespace '${ns}'"; continue; }
    bad=$(awk '$3 != "Running" && $3 != "Completed" && $3 != "Succeeded" {print $1 "(" $3 ")"}' <<<"$pods")
    if [[ -z "$bad" ]]; then
      pass "pods healthy in ${ns}"
    else
      fail "unhealthy pods in ${ns}: ${bad//$'\n'/ }"
    fi
  done
}

k8s_unavailable() {
  if [[ "$REQUIRE_K8S" == "true" ]]; then
    fail "$1 (--require-k8s)"
  else
    log "WARN: $1 — skipping Kubernetes checks"
  fi
}

log "Smoke tests: env=${ENV} api=${API_URL} cluster=${CLUSTER} dry_run=${DRY_RUN}"

http_check "API liveness" "/healthz"
http_check "API readiness" "/readyz"
k8s_checks

if [[ "$FAILURES" -gt 0 ]]; then
  log "Smoke tests FAILED for ${ENV} (${FAILURES} failure(s))"
  exit 1
fi
log "Smoke tests PASSED for ${ENV}"
