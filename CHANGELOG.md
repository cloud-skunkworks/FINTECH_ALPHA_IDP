# Changelog

## RC2 — 2026-10-01

Versions below were checked against registries where the proxy allowed; items marked "unverified" were left unchanged.

### Dependencies
- Python API: fastapi 0.142.2, uvicorn 0.54.0, pydantic 2.13.5, boto3/botocore 1.43.106, PyJWT 2.15.1, cryptography 50.0.2, OpenTelemetry 1.45.0 / 0.66b0, structlog 26.1.0, pytest 9.1.1, moto 5.2.3, pip 26.2.1 (full list in `platform/api/requirements.txt`).
- CDK: aws-cdk-lib 2.272.0, aws-cdk CLI 2.1143.0, constructs 10.8.1, TypeScript 6.0.3 (7.x not yet supported by ts-jest/typescript-eslint), jest 30, eslint 10; EKS 1.32 to 1.36.
- CI/tooling: OPA 1.21.1, conftest 0.71.0, Node 24, ubuntu-24.04, CDK_VERSION aligned to `cdk/package.json`.
- Unverified, unchanged: Gatekeeper chart (3.16.3), OTel collector chart/image (0.86.0 / 0.101.0), GitHub Actions majors, `trivy-action@master` (pin to a tag).

### Fixes
- API: auth rejected all real Cognito tokens (`aud` required); JWKS errors returned 500; Dockerfile entrypoint could not import the app; template/params were not validated; blocking boto3 calls in async paths; destroy marked DESTROYED early; DynamoDB errors became 404; `/readyz` leaked exceptions; service-name regex allowed trailing hyphen.
- Agents: Anthropic client created at import; unvalidated model JSON; fenced-JSON crash.
- Backstage: missing proxy entry, dead catalog locations, template placeholders.
- CDK: stale duplicate stack shims removed; OTel IRSA trust ARN split; IRSA `CfnJson` conditions; kubectl layer; cluster admin role trust; Backstage had no service; Platform API could not deploy (CodeDeploy without group); dashboard used invented ALB dimension; deprecated APIs.
- Scripts/CI: missing `smoke-test.sh` created; `--smoke-test` flag ordering; dry-run pipe bug; wrong chmod target; prod deploy stack names; policy coverage step; CI pytest run from the wrong directory.
- Policy: OPA 1.x syntax migration, `no_public_endpoints` parse error, `irsa_required` unreachable deny; tests added for every policy.
- Tests: fixture overrode nothing (`require_scope` closures); replaced with `get_token_payload` override. API 11 to 100 tests, new CDK (23) and OPA (24) suites.

### Post-merge remediation (RC2.1)
- `service-deploy.yml`: removed duplicate `id-token` key (workflow was invalid since RC1).
- Checkov: `platform-viewer` ClusterRole now lists explicit read-only resources (no wildcard); k8s scan 21/21.
- Bandit SARIF extra installed; Gitleaks switched from the license-gated action to the v8.30.1 CLI.
- `trivy-action` pinned to `v0.36.0` (was `@master`).
- `cdk-deploy.yml`: deploy jobs skip when `AWS_ACCOUNT_<ENV>` repo variables are unset, instead of failing at OIDC.
- Docs: stale EKS/CDK versions refreshed in `PLAYBOOK.md` and `docs/architecture/overview.md`.
- Correction to PR #1 text: Cognito auth uses PyJWT (not python-jose) and boto3 is still a dependency.

### Known open items
Backstage approval-gate ordering and missing `skeleton/`; no ownership check on status/destroy; ALB listener is HTTP only (TODO); SQS queue referenced by API role is not defined; agents have no `requirements.txt`; add-on versions unpinned.
