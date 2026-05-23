# Runbook: CDK Deployment Failures

**Audience:** Platform engineers, SRE  
**Severity:** Typically SEV-3 (infrastructure changes blocked; no production impact)

---

## Common Failure: `TypeScript compilation error`

**Symptom:** `cdk synth` or CI fails with TypeScript errors.

```
error TS2345: Argument of type 'string | undefined' is not assignable to parameter of type 'string'.
```

**Fix:** The error message includes the file and line number. Fix the TypeScript type issue. Common causes:
- `process.env.VAR` returns `string | undefined` — use `?? 'default'` or validate
- Property name typo on a CDK construct

---

## Common Failure: `UPDATE_ROLLBACK_COMPLETE` / CloudFormation stuck

**Symptom:** Stack is in `UPDATE_ROLLBACK_COMPLETE` state. CDK deploy fails with `Stack is in UPDATE_ROLLBACK_COMPLETE state`.

**Diagnosis:**
```bash
# Find the failing resource
aws cloudformation describe-stack-events \
  --stack-name IdpEksStack-prod \
  --query "StackEvents[?ResourceStatus=='UPDATE_FAILED'].{Resource:LogicalResourceId,Reason:ResourceStatusReason}" \
  --output table
```

**Fix:**
```bash
# Continue rollback if stuck
aws cloudformation continue-update-rollback \
  --stack-name IdpEksStack-prod

# Wait for rollback to complete
aws cloudformation wait stack-rollback-complete \
  --stack-name IdpEksStack-prod
```

Then investigate the root cause (e.g. immutable property change, IAM permission boundary).

---

## Common Failure: `Resource already exists`

**Symptom:** CDK tries to create a resource that already exists in AWS (e.g. duplicate S3 bucket name, ECR repository).

**Fix options:**
1. **Import the existing resource** into CloudFormation:
   ```bash
   cdk import IdpPlatformApiStack-dev --record-resource-mapping mapping.json
   ```
2. **Rename** the CDK resource (will create a new one — may cause downtime)
3. **Delete the orphaned resource** if it's genuinely not needed (caution in production)

---

## Common Failure: `CDK Bootstrap required`

**Symptom:** `This stack uses assets, so the toolkit stack must be deployed to the environment (Run 'cdk bootstrap aws://ACCOUNT/REGION')`

**Fix:**
```bash
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
cdk bootstrap aws://${AWS_ACCOUNT}/ca-central-1
```

---

## Common Failure: `VPC not found`

**Symptom:** EKS or Platform API stack fails because VPC ID is not resolvable.

**Diagnosis:**
```bash
# Check if the VPC stack deployed successfully
aws cloudformation describe-stacks \
  --stack-name IdpNetworkStack-dev \
  --query "Stacks[0].StackStatus"
```

**Fix:** Deploy the Network Stack first. Stacks must be deployed in dependency order:
1. `IdpNetworkStack-{env}`
2. `IdpEksStack-{env}`
3. `IdpPlatformApiStack-{env}`, `IdpBackstageStack-{env}`, `IdpObservabilityStack-{env}`

---

## Checking CloudFormation Events

Always check CloudFormation events when a deploy fails — the root cause is almost always in the events:

```bash
# Get last 20 events for a stack
aws cloudformation describe-stack-events \
  --stack-name IdpEksStack-prod \
  --query "StackEvents[:20].{Time:Timestamp,Status:ResourceStatus,Resource:LogicalResourceId,Reason:ResourceStatusReason}" \
  --output table
```

---

## Emergency: Rollback CDK Changes

If a deploy causes an outage and you need to revert:

```bash
# Option A: Git revert + redeploy (preferred)
git revert <commit-sha>
git push origin main
# CI will deploy the reverted code

# Option B: Force rollback via CloudFormation
aws cloudformation cancel-update-stack --stack-name <stack-name>
# Then investigate before redeploying
```

---

## See Also

- [CDK Troubleshooting Guide](https://docs.aws.amazon.com/cdk/v2/guide/troubleshooting.html)
- [drift-check.sh](../../scripts/drift-check.sh)
- [ADR-002: AWS CDK](../adr/ADR-002-aws-cdk-infrastructure.md)
