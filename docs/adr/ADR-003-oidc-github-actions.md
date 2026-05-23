# ADR-003: OIDC Federated Identity for GitHub Actions

**Status:** Accepted  
**Date:** 2025-01-15  
**Deciders:** @platform-engineering @security  
**Tags:** security, oidc, ci-cd, github-actions

---

## Context

GitHub Actions workflows need AWS credentials to run `cdk deploy`, push Docker images to ECR, and create CodeDeploy deployments. The legacy approach used long-lived IAM access key pairs stored as GitHub secrets. These keys:

- Never expire unless manually rotated
- Are visible to anyone with admin access to the repo
- Have appeared in SIEM alerts for credential exposure
- Violate the "no long-lived credentials" requirement

---

## Decision

We will use **GitHub Actions OIDC** to authenticate to AWS with short-lived tokens. Each GitHub Actions workflow job assumes an IAM role via `sts:AssumeRoleWithWebIdentity`. The IAM trust policy scopes the assumption to specific GitHub organisations, repositories, and branches.

No static AWS credentials will exist in GitHub secrets. Existing key pairs are revoked as part of this migration.

---

## Consequences

### Positive

- **No static credentials:** Tokens are issued per-job, expire after 1 hour maximum, and cannot be reused after the job ends.
- **Auditable:** Each role assumption is logged in CloudTrail with the GitHub Actions `ref`, `sha`, and `workflow` in the session context.
- **Scoped trust:** Trust policies can be restricted to specific branches (`refs/heads/main`) or environments (`ref:refs/environments/prod`).
- **PCI-DSS alignment:** Satisfies requirement 8.2 (unique IDs for access) and 8.6 (management of system/application accounts).

### Negative

- **Network dependency:** OIDC token exchange requires HTTPS connectivity from the runner to AWS STS. Outage of either service blocks CI. Mitigated by: retry logic in the `configure-aws-credentials` action and monitoring.
- **Trust policy complexity:** Subject conditions must be precise. A misconfigured wildcard subject (`"*"`) would allow any GitHub repository to assume the role. All trust policies are reviewed by `@org/security`.

---

## Trust Policy Structure

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:your-org/idp-platform:*"
      }
    }
  }]
}
```

Production roles further restrict `sub` to `"repo:your-org/idp-platform:environment:prod"`.

---

## Alternatives Considered

| Option | Reason Rejected |
|---|---|
| Long-lived IAM access keys | SIEM violations; key rotation burden; static credential risk |
| IAM User with access keys + rotation Lambda | Reduces but does not eliminate static credential risk; operational overhead |
| Self-hosted runners with instance role | Requires managing runner infrastructure; complexity outweighs benefit |

---

## References

- [GitHub OIDC with AWS](https://docs.github.com/en/actions/security-guides/security-hardening-with-openid-connect)
- [configure-aws-credentials Action](https://github.com/aws-actions/configure-aws-credentials)
- [PCI-DSS v4.0 Requirement 8](https://www.pcisecuritystandards.org/documents/PCI-DSS-v4_0.pdf)
