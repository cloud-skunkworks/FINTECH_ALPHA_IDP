# RC2 Consolidated Smoke Test Report

Date: 2026-10-01 · Branch: `claude/practical-clarke-ETxP2`

| Area | Check | Result |
|------|-------|--------|
| Platform API | `python -m pytest` from `platform/` (Python 3.12) | 100 passed, coverage 99.8% (gate 80%) |
| Platform API | `pip check` on pinned requirements | clean |
| Platform API | Live uvicorn: `GET /healthz` | 200 |
| Platform API | Live uvicorn: `POST /v1/provision`, `GET /v1/catalog` without token | 401 (auth enforced) |
| CDK | `tsc --noEmit` | clean |
| CDK | `npm test` (jest, dev + prod stacks) | 23 passed |
| CDK | `cdk synth` dev and prod (AZ lookup pre-seeded) | success |
| Policy | `opa test policy/rego policy/tests` | 24/24, 100% coverage |
| Scripts | `bash -n` + shellcheck on bootstrap, drift-check, smoke-test | clean |
| Scripts | `smoke-test.sh --env dev --dry-run` | passes |
| YAML | All workflows, k8s manifests, otel configs parse | ok |

## Not exercised (needs real AWS / cluster)
- `cdk deploy`, `bootstrap.sh` without `--dry-run`, `smoke-test.sh` against a live API/EKS
- Docker image build and ARM64 run
- Backstage template end-to-end
- GitHub Actions workflows (only YAML-parsed locally)
