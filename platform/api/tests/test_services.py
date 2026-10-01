"""Tests for AWS client factory (moto) and Slack notifier."""

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from ..services import notify
from .conftest import ORIGINAL_HEALTH_CHECK
from ..services.aws_client import AWSClientFactory

REGION = "ca-central-1"
JOB = dict(
    service_name="payments-api",
    environment="dev",
    workspace_name="idp-workload-payments-api-dev",
    template_id="eks-microservice",
    owner_team="payments-team",
    cost_centre="CC-1234",
    requester="user-1",
)


@pytest.fixture
def aws(mock_ddb_table):
    # mock_ddb_table opens mock_aws() and creates the jobs table
    return AWSClientFactory()


async def test_health_check_ok(aws):
    assert await aws.health_check() is True
    assert await aws.get_account_id() == "123456789012"


async def test_health_check_failure(monkeypatch):
    f = AWSClientFactory()
    monkeypatch.setattr(AWSClientFactory, "health_check", ORIGINAL_HEALTH_CHECK)

    class _Broken:
        def get_caller_identity(self):
            raise RuntimeError("x")

    f._sts = _Broken()
    assert await f.health_check() is False


async def test_job_lifecycle(aws):
    await aws.put_job(job_id="j1", **JOB)
    job = await aws.get_job("j1")
    assert job["status"] == "PENDING"
    assert job["service_name"] == "payments-api"
    assert job["resources_created"] == [] and job["outputs"] == {}
    assert job["completed_at"] is None and job["error_message"] is None

    await aws.update_job_status("j1", "PLANNING")
    assert (await aws.get_job("j1"))["completed_at"] is None

    await aws.update_job_status(
        "j1", "SUCCESS", outputs={"ns": "payments"}, resources_created=["arn:1"]
    )
    job = await aws.get_job("j1")
    assert job["status"] == "SUCCESS"
    assert job["outputs"] == {"ns": "payments"}
    assert job["resources_created"] == ["arn:1"]
    assert job["completed_at"] is not None

    await aws.update_job_status("j1", "FAILED", error_message="boom")
    assert (await aws.get_job("j1"))["error_message"] == "boom"


async def test_get_missing_job_returns_none(aws):
    assert await aws.get_job("nope") is None


async def test_put_job_rejects_duplicates(aws):
    await aws.put_job(job_id="dup", **JOB)
    with pytest.raises(ClientError):
        await aws.put_job(job_id="dup", **JOB)


async def test_get_job_propagates_backend_errors(aws):
    boto3.client("dynamodb", region_name=REGION).delete_table(TableName="idp-provision-jobs-test")
    with pytest.raises(ClientError):
        await aws.get_job("anything")


async def test_ensure_ecr_repository_is_idempotent(aws):
    uri1 = await aws.ensure_ecr_repository("payments-api-dev", "CC-1234", "payments-team", "dev")
    uri2 = await aws.ensure_ecr_repository("payments-api-dev", "CC-1234", "payments-team", "dev")
    assert uri1 == uri2 == f"123456789012.dkr.ecr.{REGION}.amazonaws.com/payments-api-dev"
    repos = boto3.client("ecr", region_name=REGION).describe_repositories()["repositories"]
    assert [r["repositoryName"] for r in repos] == ["payments-api-dev"]


async def test_start_codebuild_run(aws):
    cb = boto3.client("codebuild", region_name=REGION)
    cb.create_project(
        name="idp-cdk-deploy",
        source={"type": "CODECOMMIT", "location": "https://git-codecommit.ca-central-1.amazonaws.com/v1/repos/x"},
        artifacts={"type": "NO_ARTIFACTS"},
        environment={"type": "LINUX_CONTAINER", "image": "aws/codebuild/standard:7.0", "computeType": "BUILD_GENERAL1_SMALL"},
        serviceRole="arn:aws:iam::123456789012:role/cb",
    )
    build_id = await aws.start_codebuild_run("idp-cdk-deploy", {"JOB_ID": "j1"})
    assert build_id.startswith("idp-cdk-deploy:")


# ── Slack notifier ─────────────────────────────────────────────────────────
class _FakeClient:
    calls: list = []
    fail = False

    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json):
        if self.fail:
            raise RuntimeError("slack down")
        self.calls.append((url, json))

        class _R:
            def raise_for_status(self):
                pass

        return _R()


async def test_notify_skipped_without_webhook(monkeypatch):
    monkeypatch.setattr(notify, "_SLACK_WEBHOOK_URL", "")
    monkeypatch.setattr(notify.httpx, "AsyncClient", _FakeClient)
    _FakeClient.calls = []
    await notify.notify_slack("hi")
    assert _FakeClient.calls == []


async def test_notify_posts_message(monkeypatch):
    monkeypatch.setattr(notify, "_SLACK_WEBHOOK_URL", "https://hooks.example/x")
    monkeypatch.setattr(notify.httpx, "AsyncClient", _FakeClient)
    _FakeClient.calls, _FakeClient.fail = [], False
    await notify.notify_slack("done", level="success")
    assert _FakeClient.calls == [("https://hooks.example/x", {"text": "✅ done"})]


async def test_notify_swallows_errors(monkeypatch):
    monkeypatch.setattr(notify, "_SLACK_WEBHOOK_URL", "https://hooks.example/x")
    monkeypatch.setattr(notify.httpx, "AsyncClient", _FakeClient)
    _FakeClient.fail = True
    try:
        await notify.notify_slack("x", level="error")  # must not raise
    finally:
        _FakeClient.fail = False
