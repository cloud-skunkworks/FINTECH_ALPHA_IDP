"""Tests for the provisioning router."""

import pytest
from fastapi.testclient import TestClient

from ..auth.cognito import require_scope, TokenPayload
from ..main import app
from ..services.aws_client import AWSClientFactory


class MockTokenPayload(TokenPayload):
    def __init__(self):
        self.sub = "test-user@example.com"
        self.scopes = {"idp:provision", "idp:read", "idp:destroy"}
        self.client_id = "test-client"
        self.token_use = "access"


class MockAWSClientFactory(AWSClientFactory):
    def __init__(self):
        self._jobs = {}
        self._account_id = "123456789012"

    async def health_check(self):
        return True

    async def get_account_id(self):
        return self._account_id

    async def put_job(self, job_id, **kwargs):
        self._jobs[job_id] = {"job_id": job_id, "status": "PENDING", **kwargs}

    async def get_job(self, job_id):
        return self._jobs.get(job_id)

    async def update_job_status(self, job_id, status, **kwargs):
        if job_id in self._jobs:
            self._jobs[job_id]["status"] = status

    async def ensure_ecr_repository(self, name, **kwargs):
        return f"123456789012.dkr.ecr.ca-central-1.amazonaws.com/{name}"

    async def start_codebuild_run(self, project_name, environment_variables):
        return f"idp-cdk-deploy:build-{project_name}-123"


@pytest.fixture
def client():
    """Test client with auth and AWS dependencies overridden."""
    mock_aws = MockAWSClientFactory()
    mock_token = MockTokenPayload()

    app.dependency_overrides[require_scope("idp:provision")] = lambda: mock_token
    app.dependency_overrides[require_scope("idp:read")] = lambda: mock_token
    app.dependency_overrides[require_scope("idp:destroy")] = lambda: mock_token
    app.dependency_overrides[AWSClientFactory] = lambda: mock_aws

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


class TestProvisionEndpoint:
    def test_provision_returns_202(self, client, valid_provision_payload):
        response = client.post("/v1/provision", json=valid_provision_payload)
        assert response.status_code == 202

    def test_provision_returns_job_id(self, client, valid_provision_payload):
        response = client.post("/v1/provision", json=valid_provision_payload)
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "ACCEPTED"

    def test_provision_returns_ecr_uri(self, client, valid_provision_payload):
        response = client.post("/v1/provision", json=valid_provision_payload)
        data = response.json()
        assert "ecr_repository" in data
        assert "dkr.ecr" in data["ecr_repository"]

    def test_provision_returns_irsa_role_arn(self, client, valid_provision_payload):
        response = client.post("/v1/provision", json=valid_provision_payload)
        data = response.json()
        assert "irsa_role_arn" in data
        assert "arn:aws:iam" in data["irsa_role_arn"]

    def test_provision_returns_poll_url(self, client, valid_provision_payload):
        response = client.post("/v1/provision", json=valid_provision_payload)
        data = response.json()
        assert "poll_url" in data
        assert "/v1/status/" in data["poll_url"]

    def test_invalid_service_name_rejected(self, client, valid_provision_payload):
        valid_provision_payload["service_name"] = "UPPERCASE-not-allowed"
        response = client.post("/v1/provision", json=valid_provision_payload)
        assert response.status_code == 422

    def test_invalid_cost_centre_rejected(self, client, valid_provision_payload):
        valid_provision_payload["cost_centre"] = "NOCCDASH"
        response = client.post("/v1/provision", json=valid_provision_payload)
        assert response.status_code == 422

    def test_invalid_region_rejected(self, client, valid_provision_payload):
        valid_provision_payload["region"] = "eu-west-1"
        response = client.post("/v1/provision", json=valid_provision_payload)
        assert response.status_code == 422

    def test_reserved_service_name_rejected(self, client, valid_provision_payload):
        valid_provision_payload["service_name"] = "kube-system"
        response = client.post("/v1/provision", json=valid_provision_payload)
        assert response.status_code == 422

    def test_xs_size_rejected_in_prod(self, client, valid_provision_payload):
        valid_provision_payload["environment"] = "prod"
        valid_provision_payload["size"] = "xs"
        response = client.post("/v1/provision", json=valid_provision_payload)
        assert response.status_code == 422


class TestHealthEndpoints:
    def test_liveness(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
