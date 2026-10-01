"""Pytest configuration and shared fixtures."""

import os

import boto3
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from moto import mock_aws

# Ensure test environment uses mock AWS
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "ca-central-1"
os.environ["ENVIRONMENT"] = "test"
os.environ["COGNITO_USER_POOL_ID"] = "ca-central-1_test123"
os.environ["COGNITO_CLIENT_ID"] = "test-client-id"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-minimum-32-chars-long"
os.environ["JOB_TABLE_NAME"] = "idp-provision-jobs-test"


@pytest.fixture(scope="session")
def aws_credentials():
    """Mock AWS credentials for moto."""
    return {
        "aws_access_key_id": "testing",
        "aws_secret_access_key": "testing",
        "aws_session_token": "testing",
        "region_name": "ca-central-1",
    }


@pytest.fixture
def mock_ddb_table(aws_credentials):
    """Create a mock DynamoDB table for testing."""
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ca-central-1")
        ddb.create_table(
            TableName="idp-provision-jobs-test",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield ddb


@pytest.fixture
def valid_provision_payload():
    return {
        "service_name": "test-payments-api",
        "environment": "dev",
        "template_id": "eks-microservice",
        "size": "sm",
        "owner_team": "payments-team",
        "cost_centre": "CC-1234",
        "region": "ca-central-1",
    }


@pytest.fixture
def auth_headers():
    """
    Return headers with a mock Bearer token.
    In tests, the auth middleware is bypassed via dependency override.
    """
    return {"Authorization": "Bearer mock-test-token"}


# ── Shared API test fixtures ───────────────────────────────────────────────
# The OTel SDK would otherwise try to export spans to a local collector.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from datetime import datetime, timezone  # noqa: E402

from ..auth.cognito import TokenPayload, get_token_payload  # noqa: E402
from ..main import app  # noqa: E402
from ..services.aws_client import AWSClientFactory  # noqa: E402

ALL_SCOPES = {"idp:provision", "idp:read", "idp:destroy"}


ORIGINAL_HEALTH_CHECK = AWSClientFactory.health_check


class MockAWSClientFactory(AWSClientFactory):
    """In-memory AWSClientFactory — no network, records calls."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.account_id = "123456789012"
        self.codebuild_runs: list[tuple[str, dict]] = []
        self.fail_put_job = False
        self.fail_ecr = False
        self.fail_account = False
        self.fail_codebuild = False

    async def health_check(self):
        return True

    async def get_account_id(self):
        if self.fail_account:
            raise RuntimeError("sts down")
        return self.account_id

    async def put_job(self, job_id, **kwargs):
        if self.fail_put_job:
            raise RuntimeError("ddb down")
        now = datetime.now(timezone.utc).isoformat()
        self.jobs[job_id] = {
            "job_id": job_id,
            "status": "PENDING",
            "resources_created": [],
            "outputs": {},
            "error_message": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            **kwargs,
        }

    async def get_job(self, job_id):
        return self.jobs.get(job_id)

    async def update_job_status(self, job_id, status, error_message=None, **kwargs):
        if job_id in self.jobs:
            self.jobs[job_id]["status"] = status
            self.jobs[job_id]["error_message"] = error_message

    async def ensure_ecr_repository(self, name, **kwargs):
        if self.fail_ecr:
            raise RuntimeError("ecr down")
        return f"{self.account_id}.dkr.ecr.ca-central-1.amazonaws.com/{name}"

    async def start_codebuild_run(self, project_name, environment_variables):
        if self.fail_codebuild:
            raise RuntimeError("codebuild down")
        self.codebuild_runs.append((project_name, environment_variables))
        return f"{project_name}:build-123"


def make_token(scopes=ALL_SCOPES) -> TokenPayload:
    return TokenPayload(
        sub="test-user@example.com",
        scope=" ".join(sorted(scopes)),
        client_id="test-client-id",
        token_use="access",
    )


@pytest.fixture(autouse=True)
def _no_real_aws_on_startup(monkeypatch):
    """The app lifespan builds its own AWSClientFactory; keep it offline."""

    async def _ok(self):
        return True

    monkeypatch.setattr(AWSClientFactory, "health_check", _ok)


@pytest.fixture
def mock_aws_factory():
    return MockAWSClientFactory()


@pytest.fixture
def make_client(mock_aws_factory):
    """
    Factory: make_client(scopes) -> TestClient authenticated with those scopes.

    Overrides get_token_payload (so require_scope still runs and enforces 403)
    and the AWSClientFactory dependency. make_client(None) leaves auth
    un-overridden so the real 401 path is exercised.
    """
    clients: list[TestClient] = []

    def _make(scopes=ALL_SCOPES):
        app.dependency_overrides.clear()
        app.dependency_overrides[AWSClientFactory] = lambda: mock_aws_factory
        if scopes is not None:
            token = make_token(scopes)
            app.dependency_overrides[get_token_payload] = lambda: token
        c = TestClient(app, base_url="http://localhost")
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)
    app.dependency_overrides.clear()


@pytest.fixture
def client(make_client):
    """Client with all scopes."""
    return make_client(ALL_SCOPES)
