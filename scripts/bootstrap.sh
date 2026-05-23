#!/usr/bin/env bash
# IDP Platform Bootstrap Script
#
# Usage:
#   ./scripts/bootstrap.sh --env dev
#   ./scripts/bootstrap.sh --env prod --dry-run
#
# Prerequisites:
#   - AWS CLI v2 configured with admin credentials
#   - Node.js 20 + npm
#   - CDK CLI 2.140+
#   - kubectl
#   - Helm 3.14+
#
# This script:
#   1. Bootstraps the CDK toolkit in the target account
#   2. Deploys foundation stacks (network, EKS)
#   3. Deploys platform services (API, Backstage, Observability)
#   4. Installs Helm charts (OTel Collector, Gatekeeper, CoreDNS)
#   5. Applies Kubernetes manifests (namespaces, RBAC, Gatekeeper constraints)
#   6. Runs smoke tests

set -euo pipefail

# ── Argument parsing ───────────────────────────────────────────────────────
ENV=""
DRY_RUN=false
SKIP_HELM=false
SKIP_K8S=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --env) ENV="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --skip-helm) SKIP_HELM=true; shift ;;
    --skip-k8s) SKIP_K8S=true; shift ;;
    --smoke-test)
      # Run only smoke tests against an already-deployed environment
      ./scripts/smoke-test.sh --env "$ENV"
      exit 0
      ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ -z "$ENV" ]]; then
  echo "Usage: $0 --env <dev|uat|prod>"
  exit 1
fi

if [[ "$ENV" != "dev" && "$ENV" != "uat" && "$ENV" != "prod" ]]; then
  echo "Invalid environment: $ENV. Must be dev, uat, or prod."
  exit 1
fi

# ── Safety check for prod ──────────────────────────────────────────────────
if [[ "$ENV" == "prod" && "$DRY_RUN" == "false" ]]; then
  echo "WARNING: You are bootstrapping PRODUCTION."
  echo "This will create real AWS resources and incur cost."
  read -rp "Type 'bootstrap-prod' to confirm: " confirm
  if [[ "$confirm" != "bootstrap-prod" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] $*"
  else
    "$@"
  fi
}

# ── Prerequisite checks ────────────────────────────────────────────────────
log "Checking prerequisites..."
command -v aws >/dev/null || { echo "aws CLI not found"; exit 1; }
command -v node >/dev/null || { echo "node not found"; exit 1; }
command -v cdk >/dev/null || { echo "cdk not found. Run: npm install -g aws-cdk@2.140.0"; exit 1; }
command -v kubectl >/dev/null || { echo "kubectl not found"; exit 1; }
command -v helm >/dev/null || { echo "helm not found"; exit 1; }

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION="${AWS_REGION:-ca-central-1}"

log "Account: $AWS_ACCOUNT | Region: $AWS_REGION | Env: $ENV"

# ── CDK Bootstrap ─────────────────────────────────────────────────────────
log "Bootstrapping CDK toolkit in $AWS_ACCOUNT/$AWS_REGION..."
run cdk bootstrap "aws://${AWS_ACCOUNT}/${AWS_REGION}" \
  --cloudformation-execution-policies "arn:aws:iam::aws:policy/AdministratorAccess" \
  --trust "$AWS_ACCOUNT" \
  --tags "Project=idp-platform" \
  --tags "Environment=$ENV" \
  --tags "ManagedBy=aws-cdk"

# ── Install CDK dependencies ───────────────────────────────────────────────
log "Installing CDK dependencies..."
run bash -c "cd cdk && npm ci"

# ── Deploy Foundation Stacks ───────────────────────────────────────────────
log "Deploying Network Stack..."
run bash -c "cd cdk && cdk deploy IdpNetworkStack-${ENV} -c env=${ENV} --require-approval never"

log "Deploying EKS Stack..."
run bash -c "cd cdk && cdk deploy IdpEksStack-${ENV} -c env=${ENV} --require-approval never"

# ── Configure kubectl ──────────────────────────────────────────────────────
log "Updating kubeconfig..."
run aws eks update-kubeconfig \
  --name "idp-eks-${ENV}" \
  --region "$AWS_REGION"

# ── Apply Kubernetes Manifests ─────────────────────────────────────────────
if [[ "$SKIP_K8S" == "false" ]]; then
  log "Applying Kubernetes namespaces..."
  run kubectl apply -f k8s/namespaces/

  log "Applying RBAC..."
  run kubectl apply -f k8s/rbac/
fi

# ── Install Helm Charts ────────────────────────────────────────────────────
if [[ "$SKIP_HELM" == "false" ]]; then
  log "Adding Helm repositories..."
  run helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
  run helm repo add gatekeeper https://open-policy-agent.github.io/gatekeeper/charts
  run helm repo update

  log "Installing OPA Gatekeeper..."
  run helm upgrade --install gatekeeper gatekeeper/gatekeeper \
    --namespace gatekeeper-system \
    --create-namespace \
    --version 3.16.3 \
    --set replicas=3 \
    --set auditInterval=60 \
    --wait

  log "Waiting for Gatekeeper to be ready..."
  run kubectl wait --for=condition=ready pod -l gatekeeper.sh/system=yes \
    -n gatekeeper-system --timeout=120s

  log "Applying Gatekeeper constraints..."
  run kubectl apply -f k8s/gatekeeper/

  # Get AMP endpoint from CDK output
  AMP_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name "IdpObservabilityStack-${ENV}" \
    --query "Stacks[0].Outputs[?OutputKey=='AmpEndpoint'].OutputValue" \
    --output text 2>/dev/null || echo "")

  OTEL_ROLE_ARN=$(aws cloudformation describe-stacks \
    --stack-name "IdpObservabilityStack-${ENV}" \
    --query "Stacks[0].Outputs[?OutputKey=='OtelCollectorRoleArn'].OutputValue" \
    --output text 2>/dev/null || echo "")

  if [[ -n "$AMP_ENDPOINT" && -n "$OTEL_ROLE_ARN" ]]; then
    log "Installing OTel Collector..."
    run kubectl create secret generic otel-collector-config \
      --namespace monitoring \
      --from-literal=amp_remote_write_endpoint="${AMP_ENDPOINT}api/v1/remote_write" \
      --dry-run=client -o yaml | kubectl apply -f -

    run helm upgrade --install otel-collector open-telemetry/opentelemetry-collector \
      --namespace monitoring \
      --create-namespace \
      --version 0.86.0 \
      --values otel/collector/values.yaml \
      --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"="$OTEL_ROLE_ARN" \
      --wait
  else
    log "WARN: AMP endpoint not found — skipping OTel Collector install. Run after deploying ObservabilityStack."
  fi
fi

# ── Deploy Platform Services ───────────────────────────────────────────────
log "Deploying Observability Stack..."
run bash -c "cd cdk && cdk deploy IdpObservabilityStack-${ENV} -c env=${ENV} --require-approval never"

log "Deploying Platform API Stack..."
run bash -c "cd cdk && cdk deploy IdpPlatformApiStack-${ENV} -c env=${ENV} --require-approval never"

log "Deploying Backstage Stack..."
run bash -c "cd cdk && cdk deploy IdpBackstageStack-${ENV} -c env=${ENV} --require-approval never"

log "Bootstrap complete for environment: $ENV"
log "Next steps:"
log "  1. Push platform-api Docker image: docker push \$(aws ecr describe-repositories --query 'repositories[?repositoryName==\`idp-platform-api-${ENV}\`].repositoryUri' --output text):latest"
log "  2. Push backstage Docker image: docker push \$(aws ecr describe-repositories --query 'repositories[?repositoryName==\`idp-backstage-${ENV}\`].repositoryUri' --output text):latest"
log "  3. Force ECS task refresh: aws ecs update-service --cluster idp-platform-${ENV} --service idp-platform-api-${ENV} --force-new-deployment"
log "  4. Access Backstage at: https://backstage.${ENV}.internal.example.com"
