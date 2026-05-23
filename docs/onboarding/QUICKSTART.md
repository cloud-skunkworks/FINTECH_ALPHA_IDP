# Developer Quickstart — Provision Your First Service

**Goal:** Provision a working EKS microservice in ≤ 15 minutes. No ticket. No CDK required.

---

## Prerequisites

- Access to Backstage: `https://backstage.internal.example.com`
- Your team's cost centre code (format: `CC-NNNN` — ask your manager or FinOps)
- An existing GitHub account in the `your-org` organisation

---

## Step 1 — Open Backstage

Navigate to `https://backstage.internal.example.com` and sign in with your AWS IAM Identity Center credentials.

---

## Step 2 — Choose a Template

1. Click **Create** in the left sidebar
2. Browse the template catalogue
3. Select **EKS Microservice** (recommended for REST APIs and background workers)

Available templates:

| Template | Use Case |
|---|---|
| **EKS Microservice** | REST APIs, gRPC services, background workers on Kubernetes |
| **ECS Fargate Service** | Simpler container workloads without K8s complexity |
| **Aurora Postgres** | Managed Postgres database (serverless v2) |
| **S3 Data Bucket** | Private S3 bucket with KMS encryption |
| **SQS Queue + DLQ** | Async messaging with dead-letter queue |
| **Lambda Function** | Event-driven functions with SQS trigger |

---

## Step 3 — Fill in the Form

| Field | Description | Example |
|---|---|---|
| **Service Name** | Lowercase, hyphens OK, 3–40 chars | `payments-api` |
| **Environment** | Start with `dev` | `dev` |
| **Size** | Start with `sm` — you can scale later | `sm` |
| **Owner Team** | Your GitHub team slug | `payments-team` |
| **Cost Centre** | From FinOps | `CC-1234` |

Click **Next** and review. Click **Create**.

---

## Step 4 — Wait for CI (~3 minutes)

Backstage will create a GitHub PR. CI will automatically:
1. Run `cdk synth` and `cdk diff`
2. Check OPA policies
3. Run Checkov security scan
4. Post a cost estimate comment on the PR

For **dev** environments, the PR is **automatically approved** — no manual review needed.

---

## Step 5 — Provisioning (~10 minutes)

After the PR merges, CDK deploys your infrastructure. You'll receive a Slack notification in `#idp-alerts` when it's complete.

**What gets created:**
- Kubernetes namespace: `payments-api`
- IRSA IAM role: `arn:aws:iam::<account>:role/irsa-payments-api-dev`
- ECR repository: `<account>.dkr.ecr.ca-central-1.amazonaws.com/payments-api-dev`
- CloudWatch Log Group: `/idp/workloads/dev/payments-api`
- Kubernetes ServiceAccount (with IRSA annotation pre-configured)

---

## Step 6 — Deploy Your Application

Once provisioning is complete:

```bash
# 1. Authenticate to ECR
aws ecr get-login-password --region ca-central-1 | \
  docker login --username AWS --password-stdin \
  <account>.dkr.ecr.ca-central-1.amazonaws.com

# 2. Build and push your image
docker build -t payments-api:latest .
docker tag payments-api:latest \
  <account>.dkr.ecr.ca-central-1.amazonaws.com/payments-api-dev:latest
docker push <account>.dkr.ecr.ca-central-1.amazonaws.com/payments-api-dev:latest

# 3. Create a Kubernetes Deployment
# (Your ServiceAccount and namespace are already provisioned)
kubectl create deployment payments-api \
  --image=<account>.dkr.ecr.ca-central-1.amazonaws.com/payments-api-dev:latest \
  --namespace=payments-api \
  --replicas=1
```

### Kubernetes Deployment manifest (recommended)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payments-api
  namespace: payments-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: payments-api
  template:
    metadata:
      labels:
        app: payments-api
    spec:
      serviceAccountName: payments-api  # Pre-created with IRSA annotation
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: payments-api
          image: <account>.dkr.ecr.ca-central-1.amazonaws.com/payments-api-dev:latest
          ports:
            - containerPort: 8080
          resources:
            requests:
              cpu: "250m"
              memory: "256Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: [ALL]
            readOnlyRootFilesystem: true
          env:
            - name: AWS_REGION
              value: ca-central-1
```

---

## Troubleshooting

**Q: My PR failed the OPA policy check.**
A: Check the policy check job output. Common causes: missing mandatory tags, missing IRSA annotation. The IDP API pre-populates required tags — if you modified the generated CDK code, restore the tag block.

**Q: I need AWS permissions (e.g. DynamoDB access) for my service.**
A: Open an issue in this repo with label `irsa-permissions-request`. The platform team will add permissions to your IRSA role via CDK. Never request node-level IAM changes.

**Q: My container image is failing to pull.**
A: Verify your IRSA role has `AmazonEC2ContainerRegistryReadOnly` attached and the ECR repository is in the same account. Check `kubectl describe pod <pod-name> -n <namespace>` for the specific error.

**Q: How do I scale to prod?**
A: Re-run the Backstage template with `environment: prod`. Prod requires a platform engineer review and cannot auto-approve.

---

## Getting Help

- **Slack:** `#platform-engineering`
- **Runbooks:** `docs/runbooks/`
- **On-call:** PagerDuty service `idp-platform` (for provisioning failures)
