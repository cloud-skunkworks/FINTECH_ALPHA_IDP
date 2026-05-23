# IDP Platform — FinTech Infrastructure Vending Machine

> **Classification:** Internal Engineering — Confidential  
> **Version:** 1.0  
> **Regulatory Scope:** PCI-DSS v4.0 · SOC 2 Type II · PIPEDA  
> **Stack:** AWS CDK · EKS 1.30 · GitHub Actions · FastAPI · Backstage · OpenTelemetry · Claude AI

---

## What Is This?

The IDP (Internal Developer Platform) is a **self-service infrastructure vending machine** for regulated FinTech workloads on AWS. It lets application developers provision production-grade, compliance-ready cloud infrastructure in **≤ 15 minutes** — without writing AWS CDK, raising a ticket, or waiting for the platform team.

A developer opens Backstage, fills in a form (service name, environment, size, cost centre), and gets back:

- A Kubernetes namespace with Pod Security Admission enforced
- An IRSA IAM role scoped to their namespace and ServiceAccount
- An ECR repository with vulnerability scanning on push
- A CloudWatch Log Group with the correct retention policy
- CloudWatch alarms pre-wired to PagerDuty
- A GitHub PR for the generated CDK code, reviewed by an AI agent before a human approves

All of it tagged, encrypted, network-isolated, and compliant out of the box.

---

## Why Does This Exist?

### The Problem

Before this platform, provisioning a new microservice required:

1. Opening a JIRA ticket to the platform team
2. Waiting 2–5 business days for manual infrastructure setup
3. Manually configuring IAM, networking, ECR, logging, and alerting
4. Hoping everything was compliant — a PCI assessor would find out if not

The platform team was a bottleneck. Every team was asking for the same patterns: "I need a Kubernetes namespace with a database and an SQS queue." The answers were inconsistent, the security posture was uneven, and audits were painful.

### The Solution: Paved Roads

The IDP provides **paved roads** — opinionated, pre-approved infrastructure patterns that encode the security, compliance, and operational requirements the platform team would otherwise configure manually. Developers self-serve on the roads. The platform team maintains the roads.

The machine enforces non-negotiable controls (PCI-DSS, SOC 2, PIPEDA) as invariants — not as checklists that someone might forget.

### Design Principles

| Principle | Implementation |
|---|---|
| **15-minute provision** | Backstage → GitHub PR → CDK deploy in under 15 min end-to-end |
| **No ticket, no Terraform** | Developers never write IaC. Backstage scaffolder + IaC Agent generate it. |
| **Compliance as code** | OPA Gatekeeper blocks non-compliant pods at admission. CDK constructs enforce tagging. Checkov runs in CI. |
| **Least privilege by default** | Every pod gets its own IRSA role. Wildcard IAM is blocked. Node roles have no business permissions. |
| **Fail fast in CI** | CDK synth → OPA check → Checkov → cost estimate all run on PR, before a human reviews. AI Review Agent posts PASS/WARN/BLOCK verdict. |
| **Drift detection** | Daily `cdk diff` across all environments. Any manual console change is detected within 24 hours. |
| **Zero static credentials** | GitHub Actions authenticates via OIDC. Applications authenticate via IRSA. No IAM access keys anywhere. |
| **Human-in-the-loop for production** | Prod deployments always require a named platform engineer approval in GitHub Environments. AI agents can review but cannot deploy. |

---

## How It Works

### The End-to-End Flow

```
Developer opens Backstage
        │
        ▼  Fills scaffolder form: service name, env, size, cost centre
        │
Backstage calls POST /v1/provision (FastAPI + Cognito JWT)
        │
        ├─── DynamoDB: job record created (status=PENDING)
        ├─── ECR repository pre-created (developer can push images immediately)
        └─── CodeBuild: trigger CDK workspace generation
                │
                ▼
        IaC Agent (Claude Sonnet)
        Generates TypeScript CDK code from provisioning request template
                │
                ▼
        GitHub PR created with generated CDK stack
        CI pipeline kicks off automatically:
                │
                ├─── cdk synth          (TypeScript → CloudFormation)
                ├─── cdk diff           (what will change vs deployed)
                ├─── OPA conftest       (policy violations → PR annotation)
                ├─── Checkov            (security scan → SARIF report)
                └─── Infracost          (cost delta posted as PR comment)
                │
                ▼
        Review Agent (Claude Sonnet) analyses the diff
        Posts structured comment: PASS ✅ / WARN ⚠️ / BLOCK 🚫
        with per-finding details and recommended action
                │
                ▼
        Platform engineer reviews + approves PR
        (auto-approved for dev; manual for uat/prod)
                │
                ▼
        Merge → GitHub Actions: cdk deploy
        CloudFormation creates:
                ├─── Kubernetes Namespace (PSA=restricted)
                ├─── Kubernetes ServiceAccount (IRSA annotated)
                ├─── IAM Role (IRSA, scoped to namespace/SA)
                ├─── ECR Repository (scan on push, lifecycle rules)
                ├─── CloudWatch Log Group (retention by env)
                └─── CloudWatch Alarms (memory, error rate)
                │
                ▼
        Slack notification: ✅ Provisioning complete
        DynamoDB: job status=SUCCESS, outputs populated
        Developer polls GET /v1/status/{job_id} or checks Backstage
```

### Component Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Developer Experience                   │
│                                                           │
│  Backstage Portal ──► GitHub PR ──► Slack Notification   │
│  (ECS Fargate)        (scaffolder)   (#idp-alerts)        │
└──────────────────────────┬──────────────────────────────┘
                           │ POST /v1/provision
┌──────────────────────────▼──────────────────────────────┐
│               Platform API (FastAPI on ECS Fargate)       │
│                                                           │
│  /v1/provision  ──► Cognito JWT validation               │
│  /v1/status     ──► DynamoDB job state                   │
│  /v1/catalog    ──► template registry                    │
│  /healthz       ──► liveness (no auth)                   │
└──────────────────────────┬──────────────────────────────┘
                           │ triggers
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
  CodeBuild           DynamoDB            Slack webhook
  (CDK deploy)      (job state)           (notifications)
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│              AWS CDK Infrastructure Layer                  │
│                                                           │
│  IdpNetworkStack     VPC, subnets, NAT, VPC endpoints    │
│  IdpEksStack         EKS 1.30, node groups, OIDC         │
│  IdpPlatformApiStack ECS Fargate, Cognito, CodeDeploy    │
│  IdpBackstageStack   ECS Fargate, Aurora Postgres        │
│  IdpObservabilityStack AMP, CloudWatch, Grafana          │
│  IdpWorkload*Stack   Per-service: NS, IRSA, ECR, alarms  │
└──────────────────────────┬──────────────────────────────┘
                           │ deploys to
┌──────────────────────────▼──────────────────────────────┐
│                  EKS Cluster (1.30)                       │
│                                                           │
│  System Node Group      Workload Node Group               │
│  (On-Demand, m6i.large) (On-Demand/Spot, m6i.xlarge)     │
│                                                           │
│  OPA Gatekeeper ◄── policy/rego/ ── OPA tests            │
│  OTel DaemonSet ──► AMP + X-Ray + CloudWatch             │
│  CoreDNS, VPC CNI, EBS CSI (managed add-ons)             │
└──────────────────────────────────────────────────────────┘
```

### The Three AI Agents

The platform includes three Claude Sonnet agents. All are **read-only to AWS** — they cannot deploy, destroy, or modify infrastructure. Human approval is always required before their output is acted upon.

**IaC Agent** (`platform/agents/iac-agent/`)
- Triggered by: `POST /v1/provision` accepted
- Input: validated `ProvisionRequest` JSON
- Action: calls Claude Sonnet with the system prompt from `prompts/generate_workspace.md`
- Output: TypeScript CDK files (`main.ts`, `variables.ts`, `outputs.ts`) committed to a GitHub PR
- Constraints: must use `WorkloadTemplate` construct, mandatory tags, IRSA via `IrsaRole` construct, no hardcoded ARNs

**Review Agent** (`platform/agents/review-agent/`)
- Triggered by: CDK plan completing in GitHub Actions
- Input: `cdk diff` stdout + synthesised CloudFormation template
- Action: analyses for BLOCK (IAM wildcards, public S3, missing IRSA), WARN (burstable nodes, no alarms), INFO (cost optimisations)
- Output: structured JSON → formatted Markdown PR comment with PASS/WARN/BLOCK verdict
- The downstream CI job posts the comment; the agent itself has no GitHub write access

**Ops Agent** (`platform/agents/ops-agent/`)
- Triggered by: CloudWatch Alarm → SNS → Lambda
- Input: alarm name + description + service name + environment
- AWS read calls it makes: `CloudWatch Logs Insights` (last 15 min errors), `CodeDeploy list-deployments` (last 2h), `CloudWatch describe-alarms`
- Output: structured triage report with hypothesis, evidence, recommended kubectl/AWS CLI commands, and `escalate: true/false`
- Posts directly to Slack `#idp-alerts`

### Network Architecture

```
                        Internet
                            │
              ┌─────────────┴─────────────┐
              │      ALB (HTTPS only)      │
              │  (WAF in prod, ACM cert)   │
              └─────────────┬─────────────┘
                            │
         ┌──────────────────┼──────────────────┐
         │           VPC (10.0.0.0/16)         │
         │                                      │
    Public /24 × 3 AZs          Private /22 × 3 AZs
    ALB only — no compute        EKS nodes, ECS tasks
                                      │
                                      │ (NAT Gateway)
                                      │
                               Isolated /24 × 3 AZs
                               RDS Aurora, ElastiCache
                               No internet route — ever

VPC Endpoints (traffic stays on AWS backbone, no NAT cost):
  S3 Gateway · ECR API · ECR Docker · Secrets Manager
  SSM · SSM Messages · CloudWatch Logs
```

### Security Controls Summary

Every layer has defence-in-depth:

| Layer | What | How |
|---|---|---|
| **Account** | Region restriction | SCP: only `ca-central-1` + `us-east-1` |
| **Account** | No root usage | SCP: deny all root API calls |
| **Account** | IMDSv2 enforced | SCP: deny EC2 launch without `HttpTokens=required` |
| **Network** | No direct internet for workloads | All compute in private subnets |
| **Network** | No unapproved outbound | Security Groups: explicit port rules only |
| **Identity (human)** | MFA enforced | Cognito MFA required, SCP `require-mfa` |
| **Identity (machine CI)** | No static keys | GitHub Actions OIDC, scoped by repo+branch |
| **Identity (workload pod)** | IRSA per pod | OPA Gatekeeper: deny SA without IRSA annotation |
| **Workload** | Non-root containers | OPA: deny `runAsUser: 0` or `privileged: true` |
| **Workload** | No host namespace | OPA: deny `hostPID`, `hostNetwork` |
| **Data at rest** | KMS encryption | All S3, RDS, ECR, EBS volumes |
| **Data in transit** | TLS 1.3 | ALB → HTTPS; no HTTP allowed |
| **Secrets** | No plaintext env vars | All secrets via Secrets Manager refs in task definitions |
| **Audit** | Immutable log | CloudTrail + VPC Flow Logs → S3 in Security account |
| **PCI-DSS (PAN)** | No card data in traces | OTel Transform processor masks 16-digit patterns |

---

## Repository Layout

```
idp-platform/
│
├── .github/
│   ├── workflows/
│   │   ├── cdk-plan.yml          # PR gate: synth → diff → OPA → Checkov → cost
│   │   ├── cdk-deploy.yml        # Merge: deploy DEV → UAT → PROD (manual gate)
│   │   ├── service-deploy.yml    # Docker build → Trivy scan → ECR → CodeDeploy canary
│   │   ├── drift-detect.yml      # Daily: cdk diff → Slack + GitHub Issue on drift
│   │   └── policy-check.yml      # Every PR: OPA tests, Checkov, Bandit, Gitleaks
│   └── CODEOWNERS                # Ownership rules per directory
│
├── cdk/                          # AWS CDK app (TypeScript)
│   ├── bin/
│   │   └── app.ts                # Entry point — instantiates all stacks
│   ├── lib/
│   │   ├── stacks/
│   │   │   ├── network-stack.ts  # VPC, subnets, NAT, VPC endpoints
│   │   │   ├── eks-stack.ts      # EKS 1.30, node groups, add-ons, OIDC
│   │   │   ├── platform-api-stack.ts  # ECS Fargate, Cognito, ALB, CodeDeploy
│   │   │   ├── backstage-stack.ts     # ECS Fargate, Aurora Postgres
│   │   │   └── observability-stack.ts # AMP, CloudWatch, IRSA for OTel
│   │   └── constructs/
│   │       ├── irsa-role.ts      # Reusable IRSA factory — one call, one scoped role
│   │       └── workload-template.ts  # Golden path: ECR + IRSA + NS + logs + alarms
│   ├── package.json
│   ├── cdk.json                  # CDK feature flags (all modern flags enabled)
│   └── tsconfig.json
│
├── platform/
│   ├── api/                      # FastAPI provisioning service
│   │   ├── main.py               # App factory: OTel setup, middleware, routers
│   │   ├── routers/
│   │   │   ├── provision.py      # POST /v1/provision, DELETE /v1/provision/{id}
│   │   │   ├── catalog.py        # GET /v1/catalog, GET /v1/catalog/{template_id}
│   │   │   ├── status.py         # GET /v1/status/{job_id}
│   │   │   └── health.py         # GET /healthz, GET /readyz
│   │   ├── auth/
│   │   │   └── cognito.py        # JWT validation + scope enforcement
│   │   ├── models/
│   │   │   ├── provision.py      # ProvisionRequest (with field validators)
│   │   │   ├── catalog.py        # CatalogTemplate, TemplateParameter
│   │   │   └── status.py         # JobStatus enum + model
│   │   ├── services/
│   │   │   ├── aws_client.py     # boto3 wrapper: DynamoDB, ECR, CodeBuild, STS
│   │   │   └── notify.py         # Slack webhook (async, non-blocking)
│   │   ├── tests/
│   │   │   ├── conftest.py       # moto fixtures, dependency overrides
│   │   │   └── test_provision.py # Router tests with mock AWS
│   │   ├── Dockerfile            # Multi-stage, non-root, ARM64 (Graviton)
│   │   └── requirements.txt      # Pinned versions
│   │
│   ├── backstage/
│   │   ├── app-config.yaml       # Backstage config (local/dev)
│   │   └── catalog/
│   │       └── templates/
│   │           └── eks-microservice/
│   │               └── template.yaml  # Full scaffolder template
│   │
│   └── agents/
│       ├── iac-agent/
│       │   ├── agent.py          # CDK code generation via Claude Sonnet
│       │   └── prompts/generate_workspace.md  # System prompt
│       ├── review-agent/
│       │   ├── agent.py          # CDK plan security review
│       │   └── prompts/review_plan.md
│       └── ops-agent/
│           ├── agent.py          # Alert triage via CloudWatch Logs Insights
│           └── prompts/triage_alert.md
│
├── k8s/
│   ├── namespaces/platform.yaml  # idp-platform, monitoring, gatekeeper-system
│   ├── rbac/platform-rbac.yaml   # ClusterRoles, NetworkPolicies
│   └── gatekeeper/constraints.yaml  # OPA constraint instantiations
│
├── otel/
│   └── collector/
│       ├── config.yaml           # Full OTel Collector pipeline config
│       └── values.yaml           # Helm values for DaemonSet deployment
│
├── policy/
│   ├── rego/
│   │   ├── deny_privileged_containers.rego
│   │   ├── irsa_required.rego
│   │   └── no_public_endpoints.rego
│   └── tests/
│       └── irsa_required_test.rego
│
├── scripts/
│   ├── bootstrap.sh              # Full environment bootstrap (CDK + Helm + K8s)
│   └── drift-check.sh            # Manual drift detection + smoke tests
│
├── docs/
│   ├── adr/                      # Architecture Decision Records
│   │   ├── ADR-001-irsa-over-node-roles.md
│   │   ├── ADR-002-aws-cdk-infrastructure.md
│   │   └── ADR-003-oidc-github-actions.md
│   ├── architecture/overview.md  # Full architecture with diagrams
│   ├── onboarding/QUICKSTART.md  # Developer first-service guide
│   └── runbooks/
│       ├── irsa-debugging.md
│       └── cdk-failures.md
│
├── PLAYBOOK.md                   # Operational procedures for platform engineers
└── README.md                     # This file
```

---

## Quick Start

### For Developers — Provision a New Service

See [`docs/onboarding/QUICKSTART.md`](docs/onboarding/QUICKSTART.md) for the full 6-step guide.

**tl;dr:**
1. Open Backstage: `https://backstage.internal.example.com`
2. Click **Create** → select **EKS Microservice**
3. Fill the form → click **Create**
4. CI validates and posts a cost estimate (~3 min)
5. PR auto-approves for `dev`; merge when CI is green
6. Resources provisioned within 10 minutes; Slack notification confirms

### For Platform Engineers — Local Development

```bash
# Prerequisites: Node 20, Python 3.12, AWS CLI v2, kubectl, Docker, OPA

git clone https://github.com/your-org/idp-platform.git
cd idp-platform

# CDK
cd cdk && npm install

# Verify CDK synthesises correctly (no AWS calls, fast)
npm run build && npm run synth -- -c env=dev

# Platform API
cd ../platform/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload   # http://localhost:8000/docs

# Run all tests
cd ../../
cdk/npm test                # CDK construct unit tests
(cd platform/api && pytest -v)   # API tests (moto for AWS)
(cd policy && opa test rego/ tests/ -v)  # OPA policy tests
```

### For Platform Engineers — Deploy a New Environment

```bash
# Full bootstrap (first time, ~30 min)
chmod +x scripts/bootstrap.sh
./scripts/bootstrap.sh --env dev

# Dry run first to see what will happen
./scripts/bootstrap.sh --env prod --dry-run
```

See [`PLAYBOOK.md`](PLAYBOOK.md) for detailed operational procedures.

---

## Catalog Templates

| Template ID | Provisions | Typical Use |
|---|---|---|
| `eks-microservice` | K8s namespace, IRSA, ECR, log group, HPA | REST APIs, gRPC services, workers |
| `ecs-fargate-service` | ECS service, ALB TG, ECR, task role, CodeDeploy | Simpler container workloads |
| `aurora-postgres` | Aurora Serverless v2, Secrets Manager creds | Relational databases |
| `s3-data-bucket` | S3 (private, KMS, lifecycle), IRSA read/write | Data storage, artefacts |
| `sqs-queue` | SQS + DLQ, KMS, producer/consumer IAM | Async messaging |
| `lambda-function` | Lambda, execution role, log group, optional SQS trigger | Event-driven functions |

All templates enforce: IRSA, mandatory tags, private networking, KMS encryption, and CloudWatch alarms.

---

## Environment Model

| Environment | Account | CDK Approval | PR Approval | Auto-Deploy |
|---|---|---|---|---|
| `dev` | dev account | `--require-approval never` | Auto (CI green) | Yes, on merge |
| `uat` | uat account | `--require-approval never` | Manual (1 reviewer) | Yes, after DEV |
| `prod` | prod account | `--require-approval never` | Manual (2 reviewers + CODEOWNERS) | No — GitHub Env gate |

Production deployment requires:
- 2 PR approvals including a `@org/platform-engineering` member
- All CI checks green (no exceptions)
- GitHub Environment `prod` approval from a named approver
- No active P0/P1 incidents at deploy time (checked manually)

---

## Observability

### What Is Collected

| Signal | Collected By | Destination |
|---|---|---|
| Traces | OTel SDK in services → OTel Collector | AWS X-Ray |
| Metrics | OTel Collector (Prometheus scrape + OTLP) | Amazon Managed Prometheus (AMP) |
| Logs | OTel Collector → CloudWatch Logs | CloudWatch Log Groups `/idp/...` |
| K8s Events | OTel Collector `k8s_events` receiver | CloudWatch Logs |
| Platform health | CloudWatch Alarms | PagerDuty via SNS |

### Dashboards

- `idp-platform-health-{env}` CloudWatch dashboard: request rate, error rate %, p99 latency
- Grafana (self-hosted on EKS): per-service breakdown, node utilisation, cost attribution

### Alerting

Every provisioned service gets:
- **Memory utilisation > 80%** (3 consecutive 5-min periods)
- CloudWatch Alarm → SNS → PagerDuty

The Platform API gets:
- **HTTP 5xx error rate > 5%** → SEV-2 → PagerDuty + Slack
- **p99 latency > 2000ms** → SEV-2 → PagerDuty + Slack

---

## Compliance Controls

### PCI-DSS v4.0

| Requirement | Control |
|---|---|
| 1.3 Network Access Controls | Private subnets; ALB-only ingress; SGs with explicit rules |
| 2.2 System Configuration | CDK golden-path constructs enforce secure defaults |
| 3.3 No PAN Storage (unless authorised) | OTel PAN masking; no PAN allowed in log fields |
| 6.3 Security Vulnerabilities | Checkov in CI; Trivy image scanning; Gitleaks secret detection |
| 7.2 Least-Privilege Access | IRSA per pod; no wildcard IAM (OPA blocks it) |
| 8.2 Unique Identities | Cognito per user; IRSA per ServiceAccount; OIDC per pipeline |
| 10.2 Audit Logging | CloudTrail (immutable) + VPC Flow Logs + K8s audit |
| 12.3 Risk Management | ADRs document decisions; drift detection finds deviations |

### SOC 2 Type II

| Trust Service Criteria | Control |
|---|---|
| CC6.1 Logical Access | IAM Identity Center SSO; MFA enforced |
| CC6.2 Authentication | Cognito JWT; OIDC for CI; IRSA for workloads |
| CC6.3 Separation of Duties | CODEOWNERS; 2-approval prod gate; security reviews policy/ |
| CC7.2 System Monitoring | CloudWatch Alarms; OTel; daily drift detection |
| CC8.1 Change Management | All changes via PR; no manual console changes (SCP enforced) |

---

## ADR Index

| ID | Title | Status |
|---|---|---|
| [ADR-001](docs/adr/ADR-001-irsa-over-node-roles.md) | IRSA over node instance roles for pod identity | Accepted |
| [ADR-002](docs/adr/ADR-002-aws-cdk-infrastructure.md) | AWS CDK (TypeScript) for all IaC | Accepted |
| [ADR-003](docs/adr/ADR-003-oidc-github-actions.md) | OIDC federated identity for GitHub Actions | Accepted |

New architectural decisions must be recorded as ADRs in `docs/adr/` before implementation. See the [ADR template](docs/adr/ADR-001-irsa-over-node-roles.md) for the format.

---

## Support and Contacts

| Channel | Use For |
|---|---|
| `#platform-engineering` (Slack) | Questions, feature requests, non-urgent issues |
| `#idp-alerts` (Slack) | Auto-generated: provisioning events, drift alerts, agent triage |
| GitHub Issues (this repo, label `platform`) | Bug reports, enhancements |
| PagerDuty — `idp-platform` service | Production incidents (SEV-1/SEV-2) |
| On-call rotation | See PagerDuty schedule `platform-engineering-oncall` |

**For provisioning failures:** Post in `#platform-engineering` with your job ID from the Slack notification. The Ops Agent will have already posted a triage summary in `#idp-alerts`.

**For security concerns:** Do not post publicly. Contact `@security` directly or open a confidential GitHub Security Advisory.
