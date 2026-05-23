# ADR-001: IRSA Over Node Instance Roles for Pod Identity

**Status:** Accepted  
**Date:** 2025-01-15  
**Deciders:** @platform-engineering @security  
**Tags:** security, identity, eks

---

## Context

EKS workload pods need AWS credentials to call services like S3, DynamoDB, Secrets Manager, and SQS. The two main approaches are:

1. **Node Instance Role** — The EC2 node IAM role has the permissions. All pods on the node inherit access via the EC2 metadata service (IMDS).
2. **IAM Roles for Service Accounts (IRSA)** — Each Kubernetes ServiceAccount is annotated with an IAM role ARN. Pods using that ServiceAccount get scoped credentials via the OIDC provider.

The platform serves multiple teams with different compliance requirements. A PCI-DSS scope extends to any system that handles or can access cardholder data.

---

## Decision

We will use **IRSA exclusively** for all workload pod identity. Node instance roles will have no business permissions — only the minimum required for nodes to join the cluster (CNI, kubelet).

OPA Gatekeeper enforces this at admission: any ServiceAccount in a non-system namespace without the `eks.amazonaws.com/role-arn` annotation is blocked.

---

## Consequences

### Positive

- **Least-privilege:** Each pod has exactly the IAM permissions it needs — not the union of everything any pod on the node needs.
- **Blast radius reduction:** Compromised pod cannot assume node role and access other teams' resources.
- **Auditability:** CloudTrail shows exactly which ServiceAccount (→ pod) made each API call via the IRSA role session name.
- **PCI-DSS alignment:** Satisfies requirement 7.2 (least-privilege access) and 10.2.7 (audit of privileged access).
- **No IMDS v1 risk:** With IMDSv2 enforced (SCP) and no business permissions on the node role, SSRF-based credential theft is mitigated.

### Negative

- **Operational overhead:** Every new service needs an IRSA role provisioned via CDK. The WorkloadTemplate construct automates this, reducing friction to near-zero.
- **Namespace/ServiceAccount name binding:** The trust policy is tied to the exact `namespace/service-account-name`. A rename requires updating both the trust policy and the Kubernetes manifest. The IrsaRole construct centralises this.

### Risks

- **OIDC provider compromise:** If the EKS OIDC issuer is compromised, trust policies could be bypassed. Mitigated by: EKS control plane access being private-only in prod, CloudTrail auditing all STS calls, 1-hour maximum session duration on IRSA roles.

---

## Alternatives Considered

| Option | Reason Rejected |
|---|---|
| Node instance role with scoped policies | Cannot achieve per-pod isolation; blast radius too large for PCI-DSS scope |
| Kubernetes secrets with AWS access keys | Static credentials; rotation burden; secret exposure risk; violates IAM Identity Center mandate |
| EKS Pod Identity (new feature) | Evaluated — similar mechanism to IRSA but requires EKS 1.30+ and doesn't yet have full parity with IRSA across all SDKs. Will re-evaluate in 2026. |

---

## References

- [AWS IRSA Documentation](https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html)
- [PCI-DSS v4.0 Requirement 7](https://www.pcisecuritystandards.org/documents/PCI-DSS-v4_0.pdf)
- [EKS Best Practices — Security](https://aws.github.io/aws-eks-best-practices/security/docs/iam/)
