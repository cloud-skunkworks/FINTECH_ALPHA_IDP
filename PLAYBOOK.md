# IDP Platform — Operational Playbook

> **Audience:** Platform engineers, SRE, on-call responders  
> **Assumed knowledge:** AWS CLI, kubectl, CDK CLI, GitHub Actions  
> **Classification:** Internal Engineering — Confidential

This playbook is the authoritative reference for operating the IDP platform. It covers Day 0 (bootstrap), Day 1 (standard operations), Day 2 (incident response), and every recurring procedure in between. Read top-to-bottom for orientation; use the section index for on-call reference.

---

## Section Index

1. [Prerequisites and Access](#1-prerequisites-and-access)
2. [Day 0 — Bootstrap a New Environment](#2-day-0--bootstrap-a-new-environment)
3. [Day 1 — Standard Operating Procedures](#3-day-1--standard-operating-procedures)
4. [Deploying CDK Changes](#4-deploying-cdk-changes)
5. [Deploying Platform API Changes](#5-deploying-platform-api-changes)
6. [Deploying Backstage Changes](#6-deploying-backstage-changes)
7. [Provisioning a Developer Workload (Manual)](#7-provisioning-a-developer-workload-manual)
8. [Rotating Secrets](#8-rotating-secrets)
9. [Drift Detection and Remediation](#9-drift-detection-and-remediation)
10. [Incident Response](#10-incident-response)
11. [Rollback Procedures](#11-rollback-procedures)
12. [EKS Operations](#12-eks-operations)
13. [OTel Collector Operations](#13-otel-collector-operations)
14. [OPA Gatekeeper Operations](#14-opa-gatekeeper-operations)
15. [AI Agent Operations](#15-ai-agent-operations)
16. [Access Management](#16-access-management)
17. [Cost Governance](#17-cost-governance)
18. [Compliance Checks](#18-compliance-checks)
19. [Offboarding a Service](#19-offboarding-a-service)
20. [Tooling Reference](#20-tooling-reference)

---

## 1. Prerequisites and Access

### Required Tools

Install these before performing any procedure in this playbook.

```bash
# Node.js 20 LTS (use nvm)
nvm install 20 && nvm use 20

# AWS CDK CLI (pinned version — match cdk/package.json)
npm install -g aws-cdk@2.140.0

# AWS CLI v2
# macOS:
brew install awscli
# Linux:
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install

# Python 3.12 (use pyenv)
pyenv install 3.12 && pyenv local 3.12

# kubectl (matches EKS 1.30)
brew install kubectl
# or: curl -LO "https://dl.k8s.io/release/v1.30.0/bin/linux/amd64/kubectl"

# Helm 3.14+
brew install helm

# OPA CLI
brew install opa
# or: curl -L -o /usr/local/bin/opa https://github.com/open-policy-agent/opa/releases/download/v0.65.0/opa_darwin_amd64

# jq (used in several scripts)
brew install jq
```

### AWS Access

Platform engineers authenticate via AWS IAM Identity Center (SSO). No IAM access keys are used.

```bash
# Configure SSO profile (one-time)
aws configure sso
# SSO Start URL: https://your-org.awsapps.com/start
# SSO Region: ca-central-1
# Profile name: idp-platform-dev (or uat, prod)

# Authenticate (daily — tokens expire after 8 hours)
aws sso login --profile idp-platform-dev

# Verify identity
aws sts get-caller-identity --profile idp-platform-dev

# Set default profile for a session
export AWS_PROFILE=idp-platform-dev
```

### Kubernetes Access

```bash
# Update kubeconfig after SSO login
aws eks update-kubeconfig \
  --name idp-eks-dev \
  --region ca-central-1 \
  --profile idp-platform-dev

# Verify
kubectl get nodes
kubectl get pods -n idp-platform

# Switch context (if managing multiple clusters)
kubectl config get-contexts
kubectl config use-context arn:aws:eks:ca-central-1:<account>:cluster/idp-eks-prod
```

### GitHub Access

Platform engineers need:
- Member of `your-org/platform-engineering` GitHub team (for CODEOWNERS approvals)
- GitHub Environments approval permission for `uat` and `prod`
- Read access to repository secrets (for `TF_API_TOKEN`, `INFRACOST_API_KEY`)

---

## 2. Day 0 — Bootstrap a New Environment

Use this procedure when standing up the platform in a new AWS account (e.g. a new `dev2` environment or disaster recovery in `us-east-1`).

### Pre-flight Checklist

Before running bootstrap:

- [ ] AWS account created and enrolled in AWS Organisation
- [ ] SCPs applied to the new account's OU (contact AWS admin)
- [ ] IAM Identity Center permission set granted to platform engineering team
- [ ] GitHub repository has the new environment's account ID in `vars.AWS_ACCOUNT_<ENV>`
- [ ] GitHub Environment `<env>` created with required reviewers configured
- [ ] Slack webhook URL in GitHub secrets as `SLACK_PLATFORM_WEBHOOK`
- [ ] `INFRACOST_API_KEY` in GitHub secrets
- [ ] `ANTHROPIC_API_KEY` in GitHub secrets (for AI agents)

### Step 1 — Authenticate and Verify

```bash
export ENV=dev   # or uat, prod
export AWS_PROFILE=idp-platform-${ENV}
export AWS_REGION=ca-central-1

aws sso login --profile $AWS_PROFILE
aws sts get-caller-identity
# Confirm: Account matches the target, not prod accidentally
```

### Step 2 — CDK Bootstrap (one-time per account/region)

CDK bootstrap creates the `CDKToolkit` CloudFormation stack, which holds the S3 bucket and ECR repository that CDK uses to store assets.

```bash
cd cdk && npm ci

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

cdk bootstrap "aws://${AWS_ACCOUNT}/${AWS_REGION}" \
  --cloudformation-execution-policies "arn:aws:iam::aws:policy/AdministratorAccess" \
  --trust "${AWS_ACCOUNT}" \
  --tags Project=idp-platform \
  --tags Environment=${ENV} \
  --tags ManagedBy=aws-cdk

# Verify
aws cloudformation describe-stacks \
  --stack-name CDKToolkit \
  --query "Stacks[0].StackStatus"
# Expected: CREATE_COMPLETE
```

### Step 3 — Run Bootstrap Script

```bash
cd ..  # back to idp-platform root

# Dry run first — see what will happen
./scripts/bootstrap.sh --env ${ENV} --dry-run

# Full bootstrap (expect ~25 min for a fresh environment)
./scripts/bootstrap.sh --env ${ENV}
```

The script deploys stacks in dependency order:
1. `IdpNetworkStack-{env}` — VPC, subnets, NAT, VPC endpoints (~8 min)
2. `IdpEksStack-{env}` — EKS cluster, node groups, add-ons (~15 min)
3. `IdpObservabilityStack-{env}` — AMP, CloudWatch dashboards (~3 min)
4. `IdpPlatformApiStack-{env}` — ECS Fargate, Cognito, ALB (~5 min)
5. `IdpBackstageStack-{env}` — ECS Fargate, Aurora (~8 min)
6. Kubernetes manifests (namespaces, RBAC)
7. Helm charts (Gatekeeper, OTel Collector)
8. Gatekeeper constraints

### Step 4 — Push Initial Container Images

CDK creates the ECS services but they need container images to run. Push the initial images:

```bash
# Get ECR URIs from CDK outputs
API_ECR=$(aws cloudformation describe-stacks \
  --stack-name IdpPlatformApiStack-${ENV} \
  --query "Stacks[0].Outputs[?OutputKey=='EcrRepositoryUri'].OutputValue" \
  --output text)

BACKSTAGE_ECR=$(aws cloudformation describe-stacks \
  --stack-name IdpBackstageStack-${ENV} \
  --query "Stacks[0].Outputs[?OutputKey=='BackstageEcrUri'].OutputValue" \
  --output text)

# Authenticate to ECR
aws ecr get-login-password --region ${AWS_REGION} | \
  docker login --username AWS --password-stdin ${API_ECR}

# Build and push Platform API
cd platform/api
docker buildx build \
  --platform linux/arm64 \
  --tag ${API_ECR}:latest \
  --tag ${API_ECR}:initial \
  --push .

# Force ECS service to pick up the new image
aws ecs update-service \
  --cluster idp-platform-${ENV} \
  --service idp-platform-api-${ENV} \
  --force-new-deployment

# Wait for deployment
aws ecs wait services-stable \
  --cluster idp-platform-${ENV} \
  --services idp-platform-api-${ENV}
```

### Step 5 — Smoke Test

```bash
./scripts/drift-check.sh --env ${ENV} --smoke-test
```

Expected output:
```
[HH:MM:SS] Running smoke tests against dev (https://api.idp.dev.internal.example.com)...
[HH:MM:SS] Liveness check PASSED (200)
[HH:MM:SS] Smoke tests PASSED for dev
```

### Step 6 — Verify Gatekeeper Is Enforcing

```bash
# Check Gatekeeper pods are running
kubectl get pods -n gatekeeper-system

# Test policy enforcement — this should be DENIED
kubectl run test-privileged \
  --image=nginx \
  --overrides='{"spec":{"containers":[{"name":"test","image":"nginx","securityContext":{"privileged":true}}]}}' \
  --namespace=idp-platform \
  --dry-run=server \
  2>&1 | grep -i "denied\|blocked\|error" || echo "WARNING: Gatekeeper may not be enforcing"
```

### Step 7 — Configure GitHub Environments

In the GitHub repository settings:
1. Create Environment `dev` — no required reviewers (auto-deploy)
2. Create Environment `uat` — 1 required reviewer from `@org/platform-engineering`
3. Create Environment `prod` — 2 required reviewers; deployment branch: `main` only

---

## 3. Day 1 — Standard Operating Procedures

### Morning Health Check (daily)

Run this at the start of each on-call shift:

```bash
# 1. Verify EKS nodes are healthy
kubectl get nodes --all-namespaces
# All nodes should be Ready. NotReady nodes need immediate investigation.

# 2. Check system pods
kubectl get pods -n kube-system
kubectl get pods -n gatekeeper-system
kubectl get pods -n monitoring
# All should be Running or Completed.

# 3. Check Platform API health
curl -s https://api.idp.dev.internal.example.com/healthz | jq .
curl -s https://api.idp.dev.internal.example.com/readyz | jq .
# Both should return {"status": "ok"}

# 4. Check for active CloudWatch alarms
aws cloudwatch describe-alarms \
  --state-value ALARM \
  --query "MetricAlarms[].{Name:AlarmName,Reason:StateReason}" \
  --output table

# 5. Check DynamoDB for stuck jobs (PENDING > 30 min)
aws dynamodb scan \
  --table-name idp-provision-jobs-prod \
  --filter-expression "#s = :s" \
  --expression-attribute-names '{"#s":"status"}' \
  --expression-attribute-values '{":s":{"S":"PENDING"}}' \
  --query "Items[?created_at < '$(date -u -v-30M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '-30 minutes' +%Y-%m-%dT%H:%M:%S)Z'].{job_id:job_id.S,created_at:created_at.S}" \
  --output table
```

### Checking a Specific Provisioning Job

```bash
JOB_ID="<job-uuid>"
ENV="prod"

# Via API
curl -s \
  -H "Authorization: Bearer <token>" \
  "https://api.idp.internal.example.com/v1/status/${JOB_ID}" | jq .

# Via DynamoDB directly (platform engineers only)
aws dynamodb get-item \
  --table-name idp-provision-jobs-${ENV} \
  --key "{\"job_id\":{\"S\":\"${JOB_ID}\"}}" \
  --output json | jq '.Item | {status: .status.S, service: .service_name.S, error: .error_message.S}'
```

### Viewing Platform API Logs

```bash
# Live tail (ECS Fargate)
aws logs tail /ecs/idp-platform-api-prod \
  --follow \
  --filter-pattern "ERROR"

# Last 100 error logs
aws logs filter-log-events \
  --log-group-name /ecs/idp-platform-api-prod \
  --filter-pattern "ERROR" \
  --start-time $(date -u -d '-1 hour' +%s)000 \
  --limit 100 \
  --query "events[].message" \
  --output text

# CloudWatch Logs Insights query (for structured queries)
aws logs start-query \
  --log-group-name /ecs/idp-platform-api-prod \
  --start-time $(date -u -d '-1 hour' +%s) \
  --end-time $(date -u +%s) \
  --query-string "fields @timestamp, @message | filter @message like /ERROR/ | sort @timestamp desc | limit 50"
```

---

## 4. Deploying CDK Changes

### Standard Path (via CI/CD)

All CDK changes should go through the GitHub Actions pipeline:

```
1. Create feature branch
2. Make changes in cdk/lib/
3. Push and open a PR
4. CI runs automatically:
   - cdk synth (TypeScript → CloudFormation)
   - cdk diff (posts changes to PR comment)
   - OPA conftest (policy check)
   - Checkov SAST
   - Infracost cost estimate
5. Review Agent posts PASS/WARN/BLOCK verdict
6. Platform engineer reviews + approves
7. Merge to main → auto-deploy to dev → uat → prod (with gate)
```

### Emergency Change (Break-Glass)

Only for active SEV-1 incidents where the CI pipeline is too slow:

```bash
# Document in the incident Slack thread BEFORE running
export AWS_PROFILE=idp-platform-prod
export ENV=prod

cd cdk

# Synth first — review the diff
npm run build
cdk diff "*-${ENV}" -c env=${ENV}

# Deploy with explicit stack targeting
cdk deploy IdpPlatformApiStack-${ENV} \
  -c env=${ENV} \
  --require-approval never \
  --hotswap  # For ECS task definition changes (faster — skips CF wait)

# Post-deploy: create a follow-up PR to bring the code in sync
# The drift-detect.yml will catch any discrepancy within 24 hours
```

> **Rule:** All break-glass changes must have a follow-up PR within 24 hours. Add to the incident post-mortem action items.

### Deploying a Single Stack

```bash
# Deploy only the observability stack (e.g. after adding a dashboard)
cdk deploy IdpObservabilityStack-dev -c env=dev --require-approval never

# Deploy multiple stacks concurrently (independent stacks only)
cdk deploy IdpObservabilityStack-dev IdpPlatformApiStack-dev \
  -c env=dev \
  --require-approval never \
  --concurrency 2
```

### Verifying a CDK Deployment

```bash
# Check CloudFormation stack status
aws cloudformation describe-stacks \
  --stack-name IdpPlatformApiStack-prod \
  --query "Stacks[0].{Status:StackStatus,Updated:LastUpdatedTime}" \
  --output table

# List recently changed resources
aws cloudformation describe-stack-events \
  --stack-name IdpPlatformApiStack-prod \
  --query "StackEvents[:10].{Time:Timestamp,Status:ResourceStatus,Resource:LogicalResourceId}" \
  --output table
```

---

## 5. Deploying Platform API Changes

### Via GitHub Actions (standard)

Merging changes to `platform/api/**` on `main` automatically triggers `service-deploy.yml`:

1. Docker build (multi-stage, ARM64)
2. Trivy vulnerability scan — fails on CRITICAL/HIGH
3. Push to ECR with SHA tag
4. Deploy to DEV (ECS rolling update, waits for stability)
5. Smoke test (GET /healthz)
6. Deploy to PROD via CodeDeploy (canary: 10% → 5 min bake → 100%)

### Manual ECS Service Update

```bash
ENV=dev
SERVICE=idp-platform-api-${ENV}
CLUSTER=idp-platform-${ENV}

# Force task replacement (picks up latest image in task definition)
aws ecs update-service \
  --cluster ${CLUSTER} \
  --service ${SERVICE} \
  --force-new-deployment \
  --query "service.{Status:status,Running:runningCount,Desired:desiredCount}" \
  --output table

# Watch deployment progress
watch -n 5 "aws ecs describe-services \
  --cluster ${CLUSTER} \
  --services ${SERVICE} \
  --query 'services[0].deployments[].{Status:status,Running:runningCount,Desired:desiredCount,Failed:failedTasks}' \
  --output table"

# Wait for stabilisation (blocks until stable or timeout)
aws ecs wait services-stable \
  --cluster ${CLUSTER} \
  --services ${SERVICE}
```

### Updating the Task Definition (new environment variable or secret)

Task definition changes must go through CDK (never manual console edits):

1. Edit `cdk/lib/stacks/platform-api-stack.ts` — add to `container.environment` or `container.secrets`
2. Submit PR → CI validates → merge → CDK deploys new task definition revision
3. ECS automatically rolls tasks to the new definition

### Viewing ECS Task Logs

```bash
# Get the task ARN for a running task
TASK_ARN=$(aws ecs list-tasks \
  --cluster idp-platform-prod \
  --service-name idp-platform-api-prod \
  --desired-status RUNNING \
  --query "taskArns[0]" \
  --output text)

# Stream logs
aws logs tail /ecs/idp-platform-api-prod \
  --follow \
  --since 30m
```

---

## 6. Deploying Backstage Changes

Backstage is deployed as a Docker container on ECS Fargate. The Backstage app-config is baked into the image at build time (not injected at runtime, except for secrets via Secrets Manager).

```bash
ENV=dev

BACKSTAGE_ECR=$(aws cloudformation describe-stacks \
  --stack-name IdpBackstageStack-${ENV} \
  --query "Stacks[0].Outputs[?OutputKey=='BackstageEcrUri'].OutputValue" \
  --output text)

# Build and push
aws ecr get-login-password --region ca-central-1 | \
  docker login --username AWS --password-stdin ${BACKSTAGE_ECR}

cd platform/backstage
docker build -t backstage:latest .
docker tag backstage:latest ${BACKSTAGE_ECR}:$(git rev-parse --short HEAD)
docker tag backstage:latest ${BACKSTAGE_ECR}:latest
docker push ${BACKSTAGE_ECR}:$(git rev-parse --short HEAD)
docker push ${BACKSTAGE_ECR}:latest

# Update ECS service
aws ecs update-service \
  --cluster idp-backstage-${ENV} \
  --service idp-backstage-${ENV} \
  --force-new-deployment
```

### Adding a New Catalog Template

1. Create directory `platform/backstage/catalog/templates/<template-id>/`
2. Add `template.yaml` following the Backstage scaffolder spec
3. Register in `platform/backstage/app-config.yaml` under `catalog.locations`
4. Add the corresponding CDK construct (if the template provisions new resource types)
5. Register in the FastAPI catalog router (`platform/api/routers/catalog.py`)
6. Submit PR → standard CI pipeline

---

## 7. Provisioning a Developer Workload (Manual)

Use this when Backstage is unavailable or a developer cannot complete the self-service flow.

```bash
# Set variables
SERVICE_NAME="payments-api"
ENV="dev"
TEMPLATE_ID="eks-microservice"
SIZE="sm"
OWNER_TEAM="payments-team"
COST_CENTRE="CC-1234"

# Get an API token (Cognito client credentials)
TOKEN=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=<your-email>,PASSWORD=<password> \
  --client-id $(aws cloudformation describe-stacks \
    --stack-name IdpPlatformApiStack-${ENV} \
    --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolClientId'].OutputValue" \
    --output text) \
  --query "AuthenticationResult.AccessToken" \
  --output text)

# Submit provisioning request
JOB=$(curl -s -X POST \
  "https://api.idp.${ENV}.internal.example.com/v1/provision" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"service_name\": \"${SERVICE_NAME}\",
    \"environment\": \"${ENV}\",
    \"template_id\": \"${TEMPLATE_ID}\",
    \"size\": \"${SIZE}\",
    \"owner_team\": \"${OWNER_TEAM}\",
    \"cost_centre\": \"${COST_CENTRE}\",
    \"region\": \"ca-central-1\"
  }")

JOB_ID=$(echo $JOB | jq -r '.job_id')
echo "Job ID: $JOB_ID"
echo "ECR: $(echo $JOB | jq -r '.ecr_repository')"
echo "IRSA: $(echo $JOB | jq -r '.irsa_role_arn')"

# Poll for completion
while true; do
  STATUS=$(curl -s \
    "https://api.idp.${ENV}.internal.example.com/v1/status/${JOB_ID}" \
    -H "Authorization: Bearer ${TOKEN}" | jq -r '.status')
  echo "Status: $STATUS"
  [[ "$STATUS" == "SUCCESS" || "$STATUS" == "FAILED" ]] && break
  sleep 30
done
```

---

## 8. Rotating Secrets

### Platform API Secrets (Cognito client secret, JWT key)

```bash
ENV=prod
SECRET_ARN=$(aws cloudformation describe-stacks \
  --stack-name IdpPlatformApiStack-${ENV} \
  --query "Stacks[0].Outputs[?OutputKey=='ApiSecretArn'].OutputValue" \
  --output text)

# Rotate the secret value (generates new random string for jwt_secret_key)
aws secretsmanager rotate-secret \
  --secret-id ${SECRET_ARN}

# Force ECS tasks to restart and pick up new secret values
aws ecs update-service \
  --cluster idp-platform-${ENV} \
  --service idp-platform-api-${ENV} \
  --force-new-deployment

aws ecs wait services-stable \
  --cluster idp-platform-${ENV} \
  --services idp-platform-api-${ENV}

echo "Secret rotated and service restarted."
```

### Backstage Database Credentials

```bash
ENV=prod

# Rotate Aurora credentials via Secrets Manager
DB_SECRET=$(aws cloudformation describe-stacks \
  --stack-name IdpBackstageStack-${ENV} \
  --query "Stacks[0].Outputs[?OutputKey=='DbSecretArn'].OutputValue" \
  --output text 2>/dev/null || \
  aws secretsmanager list-secrets \
    --query "SecretList[?Name=='/idp/${ENV}/backstage-db'].ARN" \
    --output text)

aws secretsmanager rotate-secret --secret-id ${DB_SECRET}

# Restart Backstage ECS service to pick up new credentials
aws ecs update-service \
  --cluster idp-backstage-${ENV} \
  --service idp-backstage-${ENV} \
  --force-new-deployment
```

### GitHub App Private Key (Backstage → GitHub integration)

1. Regenerate in GitHub → Organisation → GitHub Apps → IDP Platform → Private Keys
2. Update the secret in Secrets Manager:
   ```bash
   aws secretsmanager put-secret-value \
     --secret-id /idp/prod/github-app \
     --secret-string "{\"token\":\"<new-private-key>\",\"app_id\":\"<app-id>\"}"
   ```
3. Restart Backstage ECS service

---

## 9. Drift Detection and Remediation

### Manual Drift Check

```bash
# Check specific environment
./scripts/drift-check.sh --env prod

# Check all environments
./scripts/drift-check.sh --env all

# Check specific stack
cd cdk
cdk diff IdpEksStack-prod -c env=prod
```

### Interpreting Drift Output

```
Stack IdpPlatformApiStack-prod
Resources
[~] AWS::ECS::Service idp-platform-api
 └─ [~] DesiredCount: 3 → 2   ← Someone manually scaled down in console
```

This means someone changed the ECS desired count via the console. CDK will restore it on next deploy.

### Remediating Drift

**Option A — Let CDK restore on next scheduled deploy (low risk)**

If the drift is non-critical (e.g. someone temporarily scaled down a dev service), wait for the next merge to main, which will restore CDK state.

**Option B — Re-deploy immediately**

```bash
cd cdk
cdk deploy IdpPlatformApiStack-prod \
  -c env=prod \
  --require-approval never
```

**Option C — Accept the manual change in CDK**

If the drift represents a legitimate change (e.g. the desired count should stay at 2), update the CDK code to match and submit a PR.

> **Rule:** Never suppress drift without investigating its cause. Drift in production is always an audit finding.

---

## 10. Incident Response

### Severity Matrix

| Severity | Definition | Response SLO | Who |
|---|---|---|---|
| **SEV-1** | Production down; payment processing impacted; all users affected | 15 min acknowledge; immediate action | On-call + Platform Lead + VP Eng |
| **SEV-2** | Partial degradation; elevated error rate; provisioning failures | 30 min acknowledge; 2h mitigation | On-call + Platform Lead |
| **SEV-3** | Non-critical service impaired; no revenue impact | 4h acknowledge; 24h fix | On-call |
| **SEV-4** | Monitoring alert; no user impact | Next business day | Engineer via Slack |

### Incident Response Procedure

```bash
# 1. Acknowledge in PagerDuty (resets SLO timer)
# 2. Join #incidents Slack channel and post:
#    "Taking ownership of <incident> | https://link-to-pagerduty"

# 3. Assess impact
kubectl top pods --all-namespaces | grep -v "0m\|0Mi"
aws cloudwatch describe-alarms --state-value ALARM --output table

# 4. Check the Ops Agent triage in #idp-alerts (auto-posted within 2 min of alarm)

# 5. Determine: deployment-correlated?
aws deploy list-deployments \
  --application-name idp-platform-api \
  --create-time-range start=$(date -d '-2 hours' -u +%Y-%m-%dT%H:%M:%SZ),end=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --include-only-statuses Succeeded Failed \
  --output table

# 6. Act based on root cause (see Rollback section below)

# 7. Post updates to #incidents every 15 min (SEV-1) or 30 min (SEV-2)

# 8. Resolve: update PagerDuty, post all-clear in Slack

# 9. File post-mortem within 48 hours (SEV-1) or 1 week (SEV-2)
```

### Ops Agent — Interpreting the Triage Report

The Ops Agent auto-posts to `#idp-alerts` within ~2 minutes of a CloudWatch alarm breach:

```
❌ Platform API deployed to PROD | SHA: abc1234 | Deployment: d-XXXXXXXXX

📊 Ops Agent Triage — idp-platform-api-error-rate-prod

Hypothesis: Deployment d-XXXXXXXXX (30 min ago) introduced a regression.
Error logs show NullPointerException in /v1/provision router, starting
exactly at 14:32 UTC — 2 min after deployment completion.

Evidence:
- 47 ERROR entries in last 15 min, all from provision.py:line 94
- CodeDeploy deployment d-XXXXXXXXX completed at 14:30 UTC
- Error rate: 0% → 23% at 14:32 UTC

Recommended actions:
1. aws deploy stop-deployment --deployment-id d-XXXXXXXXX --auto-rollback-enabled
2. kubectl logs -l app=idp-platform-api -n idp-platform --tail=50

Escalate: false (single service, clear cause)
```

Use the recommended actions as your starting point. They are specific and runnable.

---

## 11. Rollback Procedures

### CodeDeploy Rollback (Platform API — preferred)

```bash
DEPLOYMENT_ID="d-XXXXXXXXX"

# Option A: Stop deployment and auto-rollback
aws deploy stop-deployment \
  --deployment-id ${DEPLOYMENT_ID} \
  --auto-rollback-enabled

# Wait for rollback
aws deploy wait deployment-successful \
  --deployment-id $(aws deploy list-deployments \
    --application-name idp-platform-api \
    --deployment-group-name idp-platform-api-prod \
    --query "deployments[0]" --output text)

# Verify
curl -s https://api.idp.internal.example.com/healthz | jq .
```

### ECS Service Rollback (faster, skips CodeDeploy)

If CodeDeploy rollback is slow and the service is completely down:

```bash
ENV=prod

# Find the previous task definition revision
PREV_TASK_DEF=$(aws ecs list-task-definitions \
  --family-prefix idp-platform-api-${ENV} \
  --sort DESC \
  --query "taskDefinitionArns[1]" \
  --output text)

# Force service to use previous task definition
aws ecs update-service \
  --cluster idp-platform-${ENV} \
  --service idp-platform-api-${ENV} \
  --task-definition ${PREV_TASK_DEF} \
  --force-new-deployment

aws ecs wait services-stable \
  --cluster idp-platform-${ENV} \
  --services idp-platform-api-${ENV}

echo "Service rolled back to: ${PREV_TASK_DEF}"
```

### CDK Infrastructure Rollback

CDK does not have a native rollback command. Roll back by:

1. **Git revert** (preferred):
   ```bash
   git revert <commit-sha>
   git push origin main
   # CI will deploy the reverted CDK
   ```

2. **CloudFormation rollback** (for stuck stacks):
   ```bash
   aws cloudformation cancel-update-stack \
     --stack-name IdpEksStack-prod

   aws cloudformation continue-update-rollback \
     --stack-name IdpEksStack-prod
   ```

### Kubernetes Deployment Rollback

```bash
NAMESPACE="payments-api"
DEPLOYMENT="payments-api"

# View rollout history
kubectl rollout history deployment/${DEPLOYMENT} -n ${NAMESPACE}

# Roll back to previous revision
kubectl rollout undo deployment/${DEPLOYMENT} -n ${NAMESPACE}

# Roll back to a specific revision
kubectl rollout undo deployment/${DEPLOYMENT} -n ${NAMESPACE} --to-revision=3

# Watch rollout
kubectl rollout status deployment/${DEPLOYMENT} -n ${NAMESPACE}
```

---

## 12. EKS Operations

### Cluster Access and Context

```bash
# Get cluster info
aws eks describe-cluster \
  --name idp-eks-prod \
  --query "cluster.{Version:version,Status:status,Endpoint:endpoint}" \
  --output table

# Update local kubeconfig
aws eks update-kubeconfig --name idp-eks-prod --region ca-central-1

# Check node health
kubectl get nodes -o wide
kubectl describe node <node-name>  # for a specific node with issues
```

### Draining a Node (for maintenance)

```bash
NODE_NAME="ip-10-0-1-45.ca-central-1.compute.internal"

# Cordon — prevent new pods scheduling on this node
kubectl cordon ${NODE_NAME}

# Drain — evict existing pods gracefully
kubectl drain ${NODE_NAME} \
  --ignore-daemonsets \
  --delete-emptydir-data \
  --grace-period=60 \
  --timeout=300s

# After maintenance, uncordon
kubectl uncordon ${NODE_NAME}
```

### Checking Pod Issues

```bash
NAMESPACE="payments-api"
POD="payments-api-7d9f8b6c4-xk2wq"

# Describe pod events (most useful first step)
kubectl describe pod ${POD} -n ${NAMESPACE}

# Get logs (current container)
kubectl logs ${POD} -n ${NAMESPACE} --tail=100

# Get logs (previous crash — if container restarted)
kubectl logs ${POD} -n ${NAMESPACE} --previous --tail=100

# Exec into pod (only if kubectl exec-credentials is granted)
kubectl exec -it ${POD} -n ${NAMESPACE} -- /bin/sh

# Check IRSA credential
kubectl exec -it ${POD} -n ${NAMESPACE} -- \
  aws sts get-caller-identity
```

### Node Group Scaling

Manual scaling is only for emergencies. Normally the Cluster Autoscaler handles this.

```bash
# Find the node group name
aws eks list-nodegroups --cluster-name idp-eks-prod --output table

# Scale up (emergency)
aws eks update-nodegroup-config \
  --cluster-name idp-eks-prod \
  --nodegroup-name workload-prod \
  --scaling-config minSize=3,maxSize=50,desiredSize=10
```

---

## 13. OTel Collector Operations

The OTel Collector runs as a DaemonSet in the `monitoring` namespace. One collector pod per node.

### Checking Collector Health

```bash
# View collector pods
kubectl get pods -n monitoring -l app.kubernetes.io/name=otel-collector

# Check collector metrics (self-telemetry)
kubectl port-forward -n monitoring \
  $(kubectl get pod -n monitoring -l app.kubernetes.io/name=otel-collector -o name | head -1) \
  8888:8888 &
curl -s http://localhost:8888/metrics | grep otelcol_

# Check for export errors
kubectl logs -n monitoring \
  -l app.kubernetes.io/name=otel-collector \
  --tail=50 | grep -i "error\|failed"
```

### Restarting the Collector

```bash
# Rolling restart of all collector pods (DaemonSet)
kubectl rollout restart daemonset/otel-collector -n monitoring
kubectl rollout status daemonset/otel-collector -n monitoring
```

### Updating Collector Configuration

1. Edit `otel/collector/config.yaml`
2. Update the Helm values if needed (`otel/collector/values.yaml`)
3. Submit PR → merge → CI runs Helm upgrade:

```bash
# Manual Helm upgrade (if CI is unavailable)
helm upgrade otel-collector open-telemetry/opentelemetry-collector \
  --namespace monitoring \
  --values otel/collector/values.yaml \
  --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"=$(
    aws cloudformation describe-stacks \
      --stack-name IdpObservabilityStack-prod \
      --query "Stacks[0].Outputs[?OutputKey=='OtelCollectorRoleArn'].OutputValue" \
      --output text) \
  --wait
```

---

## 14. OPA Gatekeeper Operations

### Checking Policy Violations

```bash
# See all Gatekeeper constraint violations across the cluster
kubectl get constraints -A

# Detailed violations for a specific constraint
kubectl describe K8sDenyPrivilegedContainers deny-privileged-containers

# View Gatekeeper audit results
kubectl get constraintaudit -A -o json | jq '.items[].status.violations'

# Check Gatekeeper controller logs
kubectl logs -n gatekeeper-system \
  -l control-plane=controller-manager \
  --tail=100 | grep -i "deny\|error\|violation"
```

### Temporarily Disabling a Constraint (emergency only)

```bash
# Change enforcement to "warn" instead of "deny"
kubectl patch K8sDenyPrivilegedContainers deny-privileged-containers \
  --type merge \
  -p '{"spec":{"enforcementAction":"warn"}}'

# IMPORTANT: Re-enable after the emergency
kubectl patch K8sDenyPrivilegedContainers deny-privileged-containers \
  --type merge \
  -p '{"spec":{"enforcementAction":"deny"}}'
```

> **Security requirement:** All constraint modifications must be approved by `@org/security` and logged in the incident record.

### Adding a New Policy

1. Write the Rego policy in `policy/rego/<policy-name>.rego`
2. Write tests in `policy/tests/<policy-name>_test.rego`
3. Run tests: `opa test policy/rego/ policy/tests/ -v`
4. Add the ConstraintTemplate to `k8s/gatekeeper/constraint-templates.yaml`
5. Add the Constraint instantiation to `k8s/gatekeeper/constraints.yaml`
6. Submit PR → security team review required (CODEOWNERS)

---

## 15. AI Agent Operations

### IaC Agent — Re-generating a Failed Workspace

If the IaC Agent generates invalid CDK code (TypeScript compile error, CDK synth failure):

```bash
# Re-trigger via the Platform API
curl -s -X POST \
  "https://api.idp.internal.example.com/v1/provision" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"service_name\": \"<service>\",
    \"environment\": \"<env>\",
    \"template_id\": \"<template>\",
    \"size\": \"sm\",
    \"owner_team\": \"<team>\",
    \"cost_centre\": \"<cc>\"
  }"
```

### Checking Agent Audit Logs

All agent inputs and outputs are logged to S3:

```bash
AUDIT_BUCKET="idp-agent-audit-$(aws sts get-caller-identity --query Account --output text)-prod"

# List recent agent logs
aws s3 ls s3://${AUDIT_BUCKET}/agents/iac-agent/$(date +%Y/%m/%d)/ --human-readable

# Download a specific job's audit log
aws s3 cp s3://${AUDIT_BUCKET}/agents/iac-agent/$(date +%Y/%m/%d)/${JOB_ID}.json - | jq .
```

### Updating Agent System Prompts

Agent prompts are versioned in the repo. To update:

1. Edit the `.md` file in `platform/agents/<agent>/prompts/`
2. Test locally:
   ```bash
   cd platform/agents/iac-agent
   python -c "
   from agent import generate_workspace_config
   result = generate_workspace_config({'service_name': 'test', 'environment': 'dev', 'template_id': 'eks-microservice', 'size': 'sm', 'owner_team': 'test', 'cost_centre': 'CC-0001'}, 'test-job-id')
   print(result['main_ts'][:500])
   "
   ```
3. Review agent output quality
4. Submit PR → `@org/ai-engineering` review required (CODEOWNERS)

---

## 16. Access Management

### Granting a Developer Kubernetes Access

Developers get namespace-scoped access via RBAC. Access is provisioned by CDK — not manually.

1. The `WorkloadTemplate` CDK construct creates the namespace and a `RoleBinding` binding the team's GitHub group to the `workload-developer` ClusterRole.
2. The developer authenticates via `aws eks update-kubeconfig` using their IAM Identity Center credentials.
3. If the team doesn't have a GitHub team mapped to an IAM Identity Center group yet, open a request with `@org/security`.

```bash
# Verify a developer's access
kubectl auth can-i list pods \
  --namespace payments-api \
  --as arn:aws:iam::<account>:assumed-role/AWSReservedSSO_PlatformDev_xxxx/developer@example.com

# List all RoleBindings in a namespace
kubectl get rolebindings -n payments-api -o wide
```

### Revoking Access

Access is revoked by removing the IAM Identity Center assignment (removes the underlying IAM role). No manual Kubernetes changes are needed — the IAM role assumption fails, which blocks kubectl.

---

## 17. Cost Governance

### Checking Current Month Spend by Cost Centre

```bash
# Requires Cost Explorer API access (not available in all accounts)
aws ce get-cost-and-usage \
  --time-period Start=$(date +%Y-%m-01),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --filter '{"Tags":{"Key":"Environment","Values":["prod"]}}' \
  --group-by '[{"Type":"TAG","Key":"CostCentre"}]' \
  --metrics UnblendedCost \
  --output json | jq '.ResultsByTime[0].Groups[] | {cc: .Keys[0], cost: .Metrics.UnblendedCost.Amount}'
```

### Identifying Untagged Resources

```bash
# Resources missing mandatory tags (CostCentre, Environment, Owner, Project)
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=CostCentre,Values="" \
  --query "ResourceTagMappingList[].ResourceARN" \
  --output text

# Check which CDK stacks have untagged resources (OPA will catch at synth time)
```

### Budget Alerts

Budget alerts are configured in AWS Budgets per environment. If an alert fires:

1. Check Cost Explorer for the spike source
2. Identify the offending service (`CostCentre` + `Project` tags)
3. Contact the `Owner` team
4. If it's platform infrastructure, escalate to the platform team lead

---

## 18. Compliance Checks

### Running a Full Compliance Scan

```bash
# 1. CDK synth + Checkov scan (fastest)
cd cdk && npm run build
cdk synth "*-prod" -c env=prod --output cdk-out-prod/
checkov -d cdk-out-prod/ --framework cloudformation --output table

# 2. OPA policy tests
cd ../policy
opa test rego/ tests/ -v --coverage

# 3. Gitleaks — scan git history for secrets
gitleaks detect --source=. --verbose

# 4. Check Gatekeeper for active violations
kubectl get constraints -A \
  -o json | jq '[.items[].status.violations // [] | length] | add'
# Target: 0 violations in prod

# 5. Verify IMDSv2 on all nodes
for node in $(kubectl get nodes -o name | cut -d/ -f2); do
  INSTANCE_ID=$(kubectl get node $node -o jsonpath='{.spec.providerID}' | cut -d/ -f5)
  METADATA_OPTIONS=$(aws ec2 describe-instances \
    --instance-ids ${INSTANCE_ID} \
    --query "Reservations[0].Instances[0].MetadataOptions.HttpTokens" \
    --output text 2>/dev/null)
  echo "$node: IMDSv2=${METADATA_OPTIONS}"
done
# All should show: required
```

### Pre-Audit Evidence Collection

Before a PCI-DSS or SOC 2 audit, collect:

```bash
# CloudTrail — last 90 days of API calls
aws cloudtrail lookup-events \
  --start-time $(date -d '-90 days' +%s) \
  --output json > cloudtrail-90days.json

# VPC Flow Logs evidence location
aws logs describe-log-groups \
  --log-group-name-prefix /aws/vpc \
  --output table

# Gatekeeper violations (should be 0 in prod)
kubectl get constraints -A -o json > gatekeeper-violations.json

# IAM report
aws iam generate-credential-report
sleep 10
aws iam get-credential-report \
  --query "Content" --output text | base64 -d > iam-credential-report.csv
```

---

## 19. Offboarding a Service

When a team decommissions a service:

```bash
SERVICE="payments-api"
ENV="prod"

# 1. Ensure all data is backed up / migrated (team responsibility)

# 2. Call the destroy API (requires idp:destroy scope)
curl -s -X DELETE \
  "https://api.idp.internal.example.com/v1/provision/${JOB_ID}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"confirm\": true, \"reason\": \"Service decommissioned per JIRA-1234\"}"

# 3. The destroy flow triggers a CDK destroy for the workload stack
# Monitor: GET /v1/status/{job_id}

# 4. Verify resources are removed
kubectl get namespace ${SERVICE} || echo "Namespace removed"
aws ecr describe-repositories --repository-names ${SERVICE}-${ENV} || echo "ECR removed"

# 5. Remove from Backstage catalog (platform team does this)
# Edit/delete: platform/backstage/catalog/components/<service>.yaml

# 6. Archive the GitHub PR / CDK workspace code (tag, don't delete)
```

---

## 20. Tooling Reference

### Key Commands Cheatsheet

```bash
# ── CDK ───────────────────────────────────────────────────────
cd cdk
npm run build                          # TypeScript compile
cdk synth -c env=dev                   # Generate CloudFormation
cdk diff "*-prod" -c env=prod          # Show changes vs deployed
cdk deploy IdpEksStack-dev -c env=dev  # Deploy one stack

# ── AWS ECS ────────────────────────────────────────────────────
aws ecs list-services --cluster idp-platform-prod
aws ecs describe-services --cluster idp-platform-prod --services idp-platform-api-prod
aws ecs update-service --cluster idp-platform-prod --service idp-platform-api-prod --force-new-deployment
aws ecs wait services-stable --cluster idp-platform-prod --services idp-platform-api-prod

# ── Kubernetes ─────────────────────────────────────────────────
kubectl get pods -A                    # All pods, all namespaces
kubectl get nodes -o wide              # Nodes with IP + capacity
kubectl top pods -A                    # CPU/memory by pod
kubectl rollout restart deployment/x -n <ns>   # Rolling restart
kubectl rollout undo deployment/x -n <ns>      # Roll back

# ── CloudWatch Logs ────────────────────────────────────────────
aws logs tail /ecs/idp-platform-api-prod --follow
aws logs filter-log-events --log-group-name /ecs/idp-platform-api-prod --filter-pattern "ERROR"

# ── OPA ────────────────────────────────────────────────────────
opa test policy/rego/ policy/tests/ -v      # Run tests
opa eval -d policy/rego/ "data" --format pretty  # Eval rules

# ── Drift ──────────────────────────────────────────────────────
./scripts/drift-check.sh --env prod         # Manual drift check
./scripts/drift-check.sh --env prod --smoke-test  # With smoke test
```

### Useful AWS Console Links

Replace `<account>` and `<region>` with your values:

| What | URL Pattern |
|---|---|
| ECS Services | `console.aws.amazon.com/ecs/v2/clusters/idp-platform-prod` |
| EKS Cluster | `console.aws.amazon.com/eks/clusters/idp-eks-prod` |
| CloudWatch Alarms | `console.aws.amazon.com/cloudwatch/alarms` |
| CloudTrail | `console.aws.amazon.com/cloudtrailv2/events` |
| CloudFormation | `console.aws.amazon.com/cloudformation` |
| Secrets Manager | `console.aws.amazon.com/secretsmanager` |
| CodeDeploy | `console.aws.amazon.com/codesuite/codedeploy` |

### On-Call Escalation Path

```
Alert fires in PagerDuty
       │
       ▼ (15 min)
On-Call Engineer
       │ if unresolved / SEV-1
       ▼ (15 min)
Platform Team Lead
       │ if unresolved / business impact
       ▼
VP Engineering + Security (for PCI-DSS breaches)
```

All escalations are documented in PagerDuty. Post-mortems are filed in `docs/runbooks/incidents/`.
