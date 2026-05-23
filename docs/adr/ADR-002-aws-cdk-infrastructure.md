# ADR-002: AWS CDK (TypeScript) for All Infrastructure-as-Code

**Status:** Accepted  
**Date:** 2025-01-15  
**Deciders:** @platform-engineering @sre  
**Tags:** iac, cdk, tooling

---

## Context

The existing platform used a mix of hand-crafted CloudFormation, scattered Terraform modules, and undocumented manual console changes. We needed a single IaC approach that:

- Supports programmatic constructs and reuse (DRY)
- Integrates natively with AWS services
- Enables type-safe infrastructure definitions
- Can be tested with unit tests
- Works with the team's existing TypeScript/JavaScript skills

Options evaluated: AWS CDK (TypeScript), Terraform (HCL), Pulumi (TypeScript), CloudFormation directly.

---

## Decision

We will use **AWS CDK with TypeScript** for all new infrastructure. Key constructs are encapsulated in `cdk/lib/constructs/` and consumed by stacks in `cdk/lib/stacks/`. Workload teams never write CDK directly — they use Backstage templates that trigger the IaC Agent to generate CDK configs for review.

---

## Consequences

### Positive

- **Type safety:** TypeScript catches misconfiguration errors at compile time, before a `cdk synth` or `cdk deploy`.
- **L2/L3 constructs:** CDK's higher-level constructs (e.g. `ApplicationLoadBalancedFargateService`) encode AWS best practices — VPC integration, security groups, IAM — reducing the surface area for errors.
- **Native AWS:** CDK is maintained by AWS; new services get CDK support same-day or same-week.
- **Testing:** Jest unit tests for constructs verify IAM, security group, and tag configuration without deploying.
- **Drift detection:** `cdk diff` gives an exact preview of changes; daily scheduled runs detect manual console changes.
- **IaC Agent compatibility:** The AI IaC agent generates TypeScript CDK code — type checking and `cdk synth` provide immediate feedback on agent output quality.

### Negative

- **AWS-only:** CDK targets AWS exclusively. For multi-cloud scenarios (out of scope for this refactor cycle), a different tool would be needed.
- **State management:** CDK synthesizes to CloudFormation; CloudFormation state is managed by AWS (no separate state file). This is generally simpler but means CloudFormation quotas apply.
- **Learning curve:** Platform engineers unfamiliar with TypeScript need onboarding. Mitigated by: shared construct library, CDK patterns documentation, and code reviews.

### Risks

- **CDK breaking changes:** Major CDK version upgrades occasionally introduce breaking changes in L2 constructs. Mitigated by: pinned versions in `package.json`, monthly patch-only updates, major version upgrades in a dedicated branch.

---

## Alternatives Considered

| Option | Reason Rejected |
|---|---|
| Terraform (HCL) | No native type checking; HCL lacks programmatic constructs; state management requires additional tooling (TFC). Considered for Phase 2 multi-cloud. |
| Pulumi (TypeScript) | Similar capability to CDK but less AWS-native; smaller community for AWS patterns; additional backend state management needed. |
| CloudFormation directly | Verbose; no abstractions; no type safety; high error rate for complex networking and IAM configurations. |

---

## References

- [AWS CDK Developer Guide](https://docs.aws.amazon.com/cdk/v2/guide/home.html)
- [CDK Patterns](https://cdkpatterns.com/)
- [CDK Best Practices](https://docs.aws.amazon.com/cdk/v2/guide/best-practices.html)
