# IDP Platform Architecture Overview

## Summary

The IDP (Internal Developer Platform) is a self-service infrastructure vending machine for FinTech workloads on AWS. Developers provision production-grade, PCI-DSS-compliant infrastructure in ≤ 15 minutes without writing CDK or raising a ticket.

---

## High-Level Architecture

```
Developer
    │
    ▼
Backstage Portal (ECS Fargate)
    │  1. Fill scaffolder form
    ▼
GitHub PR (CDK workspace config)
    │  2. CI: synth → OPA check → cost estimate
    ▼
Review Agent (Claude Sonnet)
    │  3. Post structured PASS/WARN/BLOCK verdict to PR
    ▼
Platform Engineer Approval
    │  4. Merge
    ▼
GitHub Actions: CDK Deploy
    │  5. cdk deploy → CloudFormation
    ▼
AWS Resources Created
    │  6. Namespace, IRSA, ECR, Log Group, Alarms
    ▼
IaC Agent (Claude Sonnet)
    │  7. Generate deployment manifests for developer
    ▼
Slack Notification ✅
    │  < 15 minutes from form submission

Ongoing:
    CloudWatch Alarms → SNS → Lambda → Ops Agent → Slack triage
    Daily drift-detect.yml → cdk diff → GitHub Issue if drift found
```

---

## Component Map

| Component | Technology | Location | Purpose |
|---|---|---|---|
| Developer Portal | Backstage 1.27 | ECS Fargate | Self-service scaffolder UI |
| Platform API | FastAPI (Python 3.12) | ECS Fargate | Provisioning REST API |
| IaC Agent | Claude Sonnet (Anthropic) | Lambda / CodeBuild | CDK code generation |
| Review Agent | Claude Sonnet (Anthropic) | GitHub Actions | Plan security review |
| Ops Agent | Claude Sonnet (Anthropic) | Lambda | Alert triage |
| EKS Cluster | EKS 1.30 + Karpenter | VPC private subnets | Container orchestration |
| OTel Collector | otel-collector-contrib 0.101 | EKS DaemonSet | Traces, metrics, logs |
| Metrics Backend | Amazon Managed Prometheus | AWS managed | Long-term metric storage |
| Dashboards | Grafana (self-hosted on EKS) | EKS | Visualization + alerting |
| Policy Enforcement | OPA Gatekeeper 3.16 | EKS admission | K8s policy enforcement |
| IaC | AWS CDK 2.140 (TypeScript) | GitHub Actions + CDK | CloudFormation synthesis |
| CI/CD | GitHub Actions | GitHub | Build, test, deploy |
| Container Registry | Amazon ECR | AWS managed | Docker image storage |
| Job State | DynamoDB | AWS managed | Provisioning job tracking |
| Secrets | AWS Secrets Manager | AWS managed | Runtime secrets |
| Auth | Amazon Cognito | AWS managed | API JWT authentication |
| Audit | CloudTrail + S3 | AWS managed | Immutable audit log |

---

## Network Topology

```
                    Internet
                       │
              ┌────────┴────────┐
              │    ALB (HTTPS)  │  ← WAF (prod)
              └────────┬────────┘
                       │
         ┌─────────────┼─────────────┐
         │         VPC (10.0.0.0/16) │
         │                           │
   Public Subnets           Private Subnets (workloads)
   /24 × 3 AZs              /22 × 3 AZs
   (ALB only)               │
                     ┌──────┴──────┐
                     │  EKS Nodes  │
                     │  ECS Tasks  │
                     └──────┬──────┘
                            │ NAT Gateway
                     Isolated Subnets
                     /24 × 3 AZs
                     ┌──────┴──────┐
                     │  RDS Aurora │
                     │ ElastiCache │
                     └─────────────┘

VPC Endpoints (no NAT charges):
  - S3 Gateway    - ECR (API + Docker)
  - Secrets Manager   - SSM / SSM Messages
  - CloudWatch Logs
```

---

## Security Layers

| Layer | Control | Tool |
|---|---|---|
| Network | Private subnets, NACLs, SGs | VPC |
| Identity (human) | MFA, IAM Identity Center SSO | AWS SSO |
| Identity (machine) | IRSA per pod (no node role sharing) | EKS OIDC |
| Identity (CI/CD) | OIDC federated tokens (no static keys) | GitHub Actions |
| Secrets | Secrets Manager (no env var plaintext) | AWS Secrets Manager |
| Container admission | Pod Security Admission (restricted) + OPA Gatekeeper | EKS |
| Data at rest | KMS CMK on S3, RDS, EBS, ECR | AWS KMS |
| Data in transit | TLS 1.3, HTTPS only | ALB + ACM |
| Audit | CloudTrail (immutable), VPC Flow Logs, K8s audit log | CloudTrail + CW |
| Compliance | Checkov + OPA in CI, daily drift detection | GitHub Actions |

---

## Data Flows

### Provisioning Request (happy path)

```
Developer → Backstage form
  → POST /v1/provision (FastAPI + Cognito JWT)
  → DynamoDB job record created (PENDING)
  → CodeBuild: cdk deploy IdpWorkload<Name>Stack
  → CloudFormation: namespace, IRSA role, ECR, log group
  → IaC Agent: generate K8s manifests for developer
  → DynamoDB job updated (SUCCESS + outputs)
  → Slack: "✅ Provisioning complete | ECR: ... | IRSA: ..."
```

### Alert Triage (ops)

```
CloudWatch Alarm breach
  → SNS → Lambda trigger
  → Ops Agent:
      - CloudWatch Logs Insights (last 15 min errors)
      - CodeDeploy recent deployments (last 2h)
      - CloudWatch metrics snapshot
  → Slack #idp-alerts: hypothesis + recommended actions
  → PagerDuty escalation if escalate=true
```

---

## Approved Regions

- `ca-central-1` — Primary (Canada, PIPEDA compliance)
- `us-east-1` — DR / secondary

SCP `restrict-regions` blocks all other regions.

---

## Cost Attribution

All AWS resources are tagged:
- `CostCentre: CC-NNNN` — team cost centre
- `Environment: dev|uat|prod`
- `Owner: <team-slug>`
- `Project: <service-name>`

Monthly cost reports flow from AWS Cost Explorer → Grafana cost attribution dashboard (per team, per environment, per service).
