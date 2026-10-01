"""Tests for the health router and app wiring."""

import boto3
from fastapi.testclient import TestClient
from moto import mock_aws

from ..main import app


def test_liveness(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "uptime_seconds" in response.json()


def test_health_endpoints_need_no_auth(make_client):
    assert make_client(None).get("/healthz").status_code == 200


def test_readiness_ok(client):
    with mock_aws():
        boto3.client("dynamodb", region_name="ca-central-1").create_table(
            TableName="idp-provision-jobs-test",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        boto3.client("secretsmanager", region_name="ca-central-1").create_secret(
            Name="/idp/test/platform-api", SecretString="x"
        )
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"aws_identity": "ok", "dynamodb": "ok", "secrets_manager": "ok"},
    }


def test_readiness_degraded_returns_503_without_leaking_errors(client):
    with mock_aws():  # empty account: no table, no secret
        response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["dynamodb"] == "error"
    assert body["checks"]["secrets_manager"] == "error"


def test_readiness_identity_failure(client, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("no creds")

    monkeypatch.setattr("boto3.client", boom)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert set(response.json()["checks"].values()) == {"error"}


def test_unhandled_exception_returns_500_json(make_client):
    async def boom():
        raise RuntimeError("kaboom")

    app.add_api_route("/__boom", boom)
    try:
        make_client(None)  # resets overrides
        with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as c:
            response = c.get("/__boom", headers={"X-Request-ID": "rid-1"})
    finally:
        app.router.routes.pop()
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error", "request_id": "rid-1"}


def test_request_id_header_echoed(client):
    response = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


def test_startup_survives_aws_unreachable(monkeypatch, make_client):
    from ..services.aws_client import AWSClientFactory

    async def _down(self):
        return False

    monkeypatch.setattr(AWSClientFactory, "health_check", _down)
    assert make_client(None).get("/healthz").status_code == 200
