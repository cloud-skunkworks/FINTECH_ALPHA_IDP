# RC2 DevOps Installer Playbook

Short, linear install guide. The full operations reference is `PLAYBOOK.md`.

## 0. Prerequisites
| Tool | Version |
|------|---------|
| AWS CLI | v2, admin credentials for the target account |
| Node.js | 20+ (CI uses 24) and npm |
| CDK CLI | `npm i -g aws-cdk@2.1143.0` (matches `cdk/package.json`) |
| Python | 3.12 |
| kubectl, Helm | current stable (Helm 3.14+) |
| OPA | 1.x (policy tests) |

GitHub repo variables needed by CI: `AWS_ACCOUNT_DEV`, `AWS_ACCOUNT_UAT`, `AWS_ACCOUNT_PROD`, `AWS_ACCOUNT_ID`; secret `SLACK_PLATFORM_WEBHOOK`. IAM roles `github-actions-idp-deploy`, `-plan`, `-readonly` must trust GitHub OIDC (ADR-003).

## 1. Pre-flight validation (no AWS needed)
```bash
cd cdk && npm ci && npx tsc --noEmit && npm test && cd ..
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r platform/api/requirements.txt
(cd platform && python -m pytest)
opa test policy/rego policy/tests
./scripts/bootstrap.sh --env dev --dry-run
```
Expected: 23 CDK tests, 100 API tests, 24 OPA tests, dry-run completes.

## 2. Bootstrap an environment
```bash
aws sts get-caller-identity            # confirm the right account
./scripts/bootstrap.sh --env dev       # prod asks you to type 'bootstrap-prod'
```
Order: CDK toolkit bootstrap, `IdpNetworkStack`, `IdpEksStack`, kubeconfig, namespaces/RBAC, Gatekeeper, constraints, then Observability, PlatformApi, Backstage stacks.
Known ordering gap: the OTel collector is skipped on a first run because the ObservabilityStack does not exist yet. After the run, re-run `./scripts/bootstrap.sh --env <env> --skip-k8s` to install it.

## 3. Deploy via CI (normal path)
Merge to `main`: `cdk-deploy.yml` deploys dev, then uat, then prod (prod needs GitHub Environment approval). `service-deploy.yml` builds, scans (Trivy) and rolls the API.
Stack IDs: `Idp{Network,Eks,PlatformApi,Backstage,Observability}Stack-<env>`.

## 4. Validate
```bash
./scripts/smoke-test.sh --env dev            # /healthz, /readyz, nodes Ready, pods healthy
./scripts/drift-check.sh --env dev --smoke-test
```
Add `--require-k8s` to fail instead of warn when the cluster is unreachable.

## 5. Rollback
- API: `service-deploy.yml` with `action=rollback` and a prior `image_tag`.
- CDK: revert the commit on `main` and redeploy; prod stateful resources are `RETAIN`.
- Helm: `helm rollback <release> -n <namespace>`.

## 6. Upgrade notes for RC1 environments
- EKS moves 1.32 to 1.36 in code; existing clusters must step one minor version per deploy (1.32, 1.33, 1.34, 1.35, 1.36), control plane before node groups.
- Pin EKS add-on versions via CDK context `idp:addonVersions` (none are pinned yet; prod warns).
- The Dockerfile now runs `api.main:app`; any ECS task overrides of the command must change.
