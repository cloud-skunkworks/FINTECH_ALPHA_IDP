"""Tests for the status router."""

import pytest

JOB_ID = "22222222-2222-2222-2222-222222222222"


def _seed(aws, **overrides):
    aws.jobs[JOB_ID] = {
        "job_id": JOB_ID,
        "status": "SUCCESS",
        "service_name": "payments-api",
        "environment": "dev",
        "workspace_name": "idp-workload-payments-api-dev",
        "owner_team": "payments-team",
        "cost_centre": "CC-1234",
        "template_id": "eks-microservice",
        "resources_created": ["arn:aws:iam::1:role/x"],
        "outputs": {"namespace": "payments-api"},
        "error_message": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:10:00+00:00",
        "completed_at": "2026-01-01T00:10:00+00:00",
        **overrides,
    }


def test_get_status(client, mock_aws_factory):
    _seed(mock_aws_factory)
    response = client.get(f"/v1/status/{JOB_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == JOB_ID
    assert body["status"] == "SUCCESS"
    assert body["outputs"] == {"namespace": "payments-api"}


def test_status_after_provision_roundtrip(client, valid_provision_payload):
    job_id = client.post("/v1/provision", json=valid_provision_payload).json()["job_id"]
    response = client.get(f"/v1/status/{job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "PLANNING"


def test_status_not_found(client):
    assert client.get("/v1/status/missing").status_code == 404


def test_status_unauthenticated(make_client):
    assert make_client(None).get(f"/v1/status/{JOB_ID}").status_code == 401


@pytest.mark.parametrize("scopes", [{"idp:provision"}, {"idp:destroy"}, set()])
def test_status_requires_read_scope(make_client, mock_aws_factory, scopes):
    _seed(mock_aws_factory)
    assert make_client(scopes).get(f"/v1/status/{JOB_ID}").status_code == 403
