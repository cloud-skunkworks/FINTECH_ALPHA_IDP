# Runbook: IRSA / Pod Identity Failures

**Audience:** Platform engineers, SRE  
**Severity:** Typically SEV-2 (service degraded; AWS API calls failing)

---

## Symptom

Pod fails with one of:
- `NoCredentialProviders` — no AWS credentials found
- `AccessDeniedException` — credentials found but permission denied
- `WebIdentityErr` — OIDC token exchange failed
- Pod logs show calls using the **EC2 node instance role** instead of the IRSA role

---

## Diagnosis Steps

### 1. Verify ServiceAccount annotation

```bash
kubectl describe serviceaccount <sa-name> -n <namespace>
# Look for:
# Annotations: eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/irsa-<service>-<env>
```

If the annotation is missing, the IRSA role was not provisioned correctly. Check the CDK stack outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name Idp<ServiceName><Environment>Stack \
  --query "Stacks[0].Outputs[?OutputKey=='WorkloadIrsaRoleArn'].OutputValue" \
  --output text
```

### 2. Verify the pod is using the correct ServiceAccount

```bash
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.serviceAccountName}'
# Must match the annotated ServiceAccount name exactly
```

### 3. Test IRSA from inside the pod

```bash
kubectl exec -it <pod-name> -n <namespace> -- \
  aws sts get-caller-identity
```

**Expected:** Returns the IRSA role ARN (e.g. `arn:aws:iam::123456789012:role/irsa-payments-api-prod`)  
**Problem:** Returns the EC2 node instance role (`arn:aws:iam::123456789012:role/idp-eks-node-prod`)

If the **node role** is returned, the IRSA annotation is present but the trust policy doesn't match.

### 4. Verify the IRSA trust policy

```bash
aws iam get-role \
  --role-name irsa-<service>-<env> \
  --query 'Role.AssumeRolePolicyDocument' \
  --output json
```

The trust policy must have:
```json
{
  "Condition": {
    "StringEquals": {
      "<oidc_issuer>:sub": "system:serviceaccount:<namespace>:<sa-name>",
      "<oidc_issuer>:aud": "sts.amazonaws.com"
    }
  }
}
```

Common mismatches:
- **Namespace mismatch:** Trust policy says `payments` but pod is in `payments-api`
- **ServiceAccount name mismatch:** Trust policy says `payments` but SA is `payments-api-sa`
- **OIDC issuer mismatch:** Multiple clusters; trust policy references wrong OIDC provider ARN

### 5. Verify the OIDC provider

```bash
# List OIDC providers in the account
aws iam list-open-id-connect-providers

# Verify the cluster's OIDC issuer matches a provider
aws eks describe-cluster --name idp-eks-<env> \
  --query 'cluster.identity.oidc.issuer' --output text
```

The issuer URL (without `https://`) must appear in the OIDC provider ARN in the trust policy.

---

## Fix

### Missing annotation

```bash
kubectl annotate serviceaccount <sa-name> -n <namespace> \
  eks.amazonaws.com/role-arn=arn:aws:iam::<account>:role/irsa-<service>-<env>
```

Then restart the pod to pick up the new token:
```bash
kubectl rollout restart deployment/<deployment-name> -n <namespace>
```

### Trust policy mismatch

Update the CDK code in `cdk/lib/constructs/irsa-role.ts` with the correct namespace and ServiceAccount name. Re-run `cdk deploy`.

**Do not manually edit IAM trust policies** — CDK will overwrite them on next deploy.

---

## Escalation

If OIDC token exchange itself is failing (check CloudTrail for `AssumeRoleWithWebIdentity` errors with non-client errors), escalate to the platform team — this may indicate an OIDC provider configuration issue.

---

## See Also

- [ADR-001: IRSA Over Node Roles](../adr/ADR-001-irsa-over-node-roles.md)
- [IrsaRole CDK Construct](../../cdk/lib/constructs/irsa-role.ts)
- [AWS IRSA Troubleshooting](https://repost.aws/knowledge-center/eks-troubleshoot-oidc-and-irsa)
