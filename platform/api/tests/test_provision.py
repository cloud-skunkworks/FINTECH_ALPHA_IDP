"""Tests for the provisioning router."""

import pytest


class TestProvisionEndpoint:
    def test_provision_returns_202(self, client, valid_provision_payload):
        response = client.post("/v1/provision", json=valid_provision_payload)
        assert response.status_code == 202

    def test_provision_response_body(self, client, valid_provision_payload):
        data = client.post("/v1/provision", json=valid_provision_payload).json()
        assert data["status"] == "ACCEPTED"
        assert "dkr.ecr" in data["ecr_repository"]
        assert data["irsa_role_arn"] == (
            "arn:aws:iam::123456789012:role/irsa-test-payments-api-dev"
        )
        assert data["poll_url"] == f"/v1/status/{data['job_id']}"
        assert data["workspace_name"] == "idp-workload-test-payments-api-dev"

    def test_provision_persists_job_and_triggers_build(
        self, client, mock_aws_factory, valid_provision_payload
    ):
        data = client.post("/v1/provision", json=valid_provision_payload).json()
        job = mock_aws_factory.jobs[data["job_id"]]
        assert job["status"] == "PLANNING"
        assert job["requester"] == "test-user@example.com"
        project, env = mock_aws_factory.codebuild_runs[0]
        assert project == "idp-cdk-deploy"
        assert env["SERVICE_NAME"] == "test-payments-api"
        assert env["ADDITIONAL_PARAMS"] == "{}"

    def test_additional_params_passed_as_json(
        self, client, mock_aws_factory, valid_provision_payload
    ):
        valid_provision_payload["additional_params"] = {"min_replicas": "3"}
        assert client.post("/v1/provision", json=valid_provision_payload).status_code == 202
        assert mock_aws_factory.codebuild_runs[0][1]["ADDITIONAL_PARAMS"] == '{"min_replicas": "3"}'

    def test_unknown_template_rejected(self, client, valid_provision_payload):
        valid_provision_payload["template_id"] = "does-not-exist"
        assert client.post("/v1/provision", json=valid_provision_payload).status_code == 422

    @pytest.mark.parametrize(
        "template,params",
        [
            ("eks-microservice", {"bogus": "1"}),
            ("eks-microservice", {"min_replicas": "abc"}),
            ("eks-microservice", {"min_replicas": "0"}),
            ("eks-microservice", {"min_replicas": "51"}),
            ("aurora-postgres", {"enable_read_replica": "maybe"}),
        ],
    )
    def test_invalid_additional_params_rejected(
        self, client, valid_provision_payload, template, params
    ):
        valid_provision_payload["template_id"] = template
        valid_provision_payload["additional_params"] = params
        assert client.post("/v1/provision", json=valid_provision_payload).status_code == 422

    def test_boolean_param_accepted(self, client, valid_provision_payload):
        valid_provision_payload["template_id"] = "aurora-postgres"
        valid_provision_payload["additional_params"] = {"enable_read_replica": "true"}
        assert client.post("/v1/provision", json=valid_provision_payload).status_code == 202

    def test_enum_param_validation(self, client, valid_provision_payload, monkeypatch):
        from ..models.catalog import CatalogTemplate, TemplateParameter
        from ..routers import catalog

        monkeypatch.setitem(
            catalog.CATALOG,
            "enum-tpl",
            CatalogTemplate(
                template_id="enum-tpl",
                title="t",
                description="d",
                parameters=[
                    TemplateParameter(
                        name="mode", type="enum", description="m", enum_values=["a", "b"]
                    )
                ],
            ),
        )
        valid_provision_payload["template_id"] = "enum-tpl"
        valid_provision_payload["additional_params"] = {"mode": "c"}
        assert client.post("/v1/provision", json=valid_provision_payload).status_code == 422
        valid_provision_payload["additional_params"] = {"mode": "a"}
        assert client.post("/v1/provision", json=valid_provision_payload).status_code == 202

    @pytest.mark.parametrize(
        "field,value",
        [
            ("service_name", "UPPERCASE-not-allowed"),
            ("service_name", "trailing-hyphen-"),
            ("service_name", "kube-system"),
            ("service_name", "ab"),
            ("cost_centre", "NOCCDASH"),
            ("cost_centre", "CC-12"),
            ("cost_centre", "CC-12a4"),
            ("region", "eu-west-1"),
            ("environment", "staging"),
            ("size", "xxl"),
        ],
    )
    def test_invalid_fields_rejected(self, client, valid_provision_payload, field, value):
        valid_provision_payload[field] = value
        assert client.post("/v1/provision", json=valid_provision_payload).status_code == 422

    def test_xs_size_rejected_in_prod(self, client, valid_provision_payload):
        valid_provision_payload["environment"] = "prod"
        valid_provision_payload["size"] = "xs"
        assert client.post("/v1/provision", json=valid_provision_payload).status_code == 422

    def test_job_persist_failure_returns_503(
        self, client, mock_aws_factory, valid_provision_payload
    ):
        mock_aws_factory.fail_put_job = True
        assert client.post("/v1/provision", json=valid_provision_payload).status_code == 503
        assert mock_aws_factory.codebuild_runs == []

    def test_ecr_failure_degrades_gracefully(
        self, client, mock_aws_factory, valid_provision_payload
    ):
        mock_aws_factory.fail_ecr = True
        response = client.post("/v1/provision", json=valid_provision_payload)
        assert response.status_code == 202
        assert response.json()["ecr_repository"].startswith("<pending")

    def test_account_lookup_failure_does_not_500(
        self, client, mock_aws_factory, valid_provision_payload
    ):
        mock_aws_factory.fail_account = True
        assert client.post("/v1/provision", json=valid_provision_payload).status_code == 202

    def test_codebuild_failure_marks_job_failed(
        self, client, mock_aws_factory, valid_provision_payload
    ):
        mock_aws_factory.fail_codebuild = True
        data = client.post("/v1/provision", json=valid_provision_payload).json()
        job = mock_aws_factory.jobs[data["job_id"]]
        assert job["status"] == "FAILED"
        assert "codebuild down" in job["error_message"]

    # ── auth ──
    def test_requires_authentication(self, make_client, valid_provision_payload):
        c = make_client(None)
        assert c.post("/v1/provision", json=valid_provision_payload).status_code == 401

    @pytest.mark.parametrize("scopes", [{"idp:read"}, {"idp:destroy"}, set()])
    def test_requires_provision_scope(self, make_client, valid_provision_payload, scopes):
        c = make_client(scopes)
        response = c.post("/v1/provision", json=valid_provision_payload)
        assert response.status_code == 403
        assert "idp:provision" in response.json()["detail"]


DESTROY_BODY = {"confirm": True, "reason": "Decommissioning unused service"}


def _seed_job(aws, status="SUCCESS", job_id="11111111-1111-1111-1111-111111111111"):
    aws.jobs[job_id] = {
        "job_id": job_id,
        "status": status,
        "workspace_name": "idp-workload-x-dev",
        "environment": "dev",
    }
    return job_id


class TestDestroyEndpoint:
    def test_destroy_accepted(self, client, mock_aws_factory):
        job_id = _seed_job(mock_aws_factory)
        response = client.request("DELETE", f"/v1/provision/{job_id}", json=DESTROY_BODY)
        assert response.status_code == 202
        assert response.json()["status"] == "DESTROY_ACCEPTED"
        project, env = mock_aws_factory.codebuild_runs[0]
        assert project == "idp-cdk-destroy"
        assert env["WORKSPACE_NAME"] == "idp-workload-x-dev"
        # Not DESTROYED until the build actually completes
        assert mock_aws_factory.jobs[job_id]["status"] == "APPLYING"

    def test_destroy_build_failure_marks_job_failed(self, client, mock_aws_factory):
        job_id = _seed_job(mock_aws_factory)
        mock_aws_factory.fail_codebuild = True
        client.request("DELETE", f"/v1/provision/{job_id}", json=DESTROY_BODY)
        assert mock_aws_factory.jobs[job_id]["status"] == "FAILED"

    def test_confirm_false_rejected(self, client, mock_aws_factory):
        job_id = _seed_job(mock_aws_factory)
        body = {**DESTROY_BODY, "confirm": False}
        assert client.request("DELETE", f"/v1/provision/{job_id}", json=body).status_code == 400

    def test_short_reason_rejected(self, client, mock_aws_factory):
        job_id = _seed_job(mock_aws_factory)
        body = {"confirm": True, "reason": "short"}
        assert client.request("DELETE", f"/v1/provision/{job_id}", json=body).status_code == 422

    def test_unknown_job_404(self, client):
        assert client.request("DELETE", "/v1/provision/nope", json=DESTROY_BODY).status_code == 404

    @pytest.mark.parametrize("state", ["PENDING", "PLANNING", "APPLYING", "FAILED", "DESTROYED"])
    def test_non_success_job_conflict(self, client, mock_aws_factory, state):
        job_id = _seed_job(mock_aws_factory, status=state)
        assert (
            client.request("DELETE", f"/v1/provision/{job_id}", json=DESTROY_BODY).status_code
            == 409
        )

    def test_requires_authentication(self, make_client):
        c = make_client(None)
        assert c.request("DELETE", "/v1/provision/x", json=DESTROY_BODY).status_code == 401

    @pytest.mark.parametrize("scopes", [{"idp:provision"}, {"idp:read"}, {"idp:provision", "idp:read"}])
    def test_requires_destroy_scope(self, make_client, mock_aws_factory, scopes):
        job_id = _seed_job(mock_aws_factory)
        c = make_client(scopes)
        response = c.request("DELETE", f"/v1/provision/{job_id}", json=DESTROY_BODY)
        assert response.status_code == 403
        assert mock_aws_factory.codebuild_runs == []
