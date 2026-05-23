"""
AWS client factory for the IDP Platform API.

All AWS interactions go through this class. Uses boto3 with the ECS task role
(IRSA on EKS) — no static credentials anywhere.
"""

import os
from datetime import datetime, timezone
from typing import Any

import boto3
import structlog
from botocore.exceptions import ClientError

log = structlog.get_logger(__name__)

_REGION = os.environ.get("AWS_REGION", "ca-central-1")
_JOB_TABLE = os.environ.get("JOB_TABLE_NAME", "idp-provision-jobs")


class AWSClientFactory:
    """
    Thin AWS client wrapper.

    Lazy-initialises boto3 clients on first use.
    In production, the ECS task role (or IRSA on EKS) provides credentials.
    """

    def __init__(self) -> None:
        self._ddb = None
        self._sts = None
        self._ecr = None
        self._codebuild = None

    @property
    def ddb(self):
        if self._ddb is None:
            self._ddb = boto3.client("dynamodb", region_name=_REGION)
        return self._ddb

    @property
    def sts(self):
        if self._sts is None:
            self._sts = boto3.client("sts", region_name=_REGION)
        return self._sts

    @property
    def ecr(self):
        if self._ecr is None:
            self._ecr = boto3.client("ecr", region_name=_REGION)
        return self._ecr

    @property
    def codebuild(self):
        if self._codebuild is None:
            self._codebuild = boto3.client("codebuild", region_name=_REGION)
        return self._codebuild

    async def health_check(self) -> bool:
        """Verify AWS connectivity on startup."""
        try:
            self.sts.get_caller_identity()
            return True
        except Exception as e:
            log.error("aws.health_check.failed", error=str(e))
            return False

    async def get_account_id(self) -> str:
        identity = self.sts.get_caller_identity()
        return identity["Account"]

    async def put_job(
        self,
        job_id: str,
        service_name: str,
        environment: str,
        workspace_name: str,
        template_id: str,
        owner_team: str,
        cost_centre: str,
        requester: str,
        status: str = "PENDING",
    ) -> None:
        """Create a new job record in DynamoDB."""
        now = datetime.now(timezone.utc).isoformat()
        self.ddb.put_item(
            TableName=_JOB_TABLE,
            Item={
                "job_id": {"S": job_id},
                "status": {"S": status},
                "service_name": {"S": service_name},
                "environment": {"S": environment},
                "workspace_name": {"S": workspace_name},
                "template_id": {"S": template_id},
                "owner_team": {"S": owner_team},
                "cost_centre": {"S": cost_centre},
                "requester": {"S": requester},
                "created_at": {"S": now},
                "updated_at": {"S": now},
                "resources_created": {"L": []},
                "outputs": {"M": {}},
            },
            ConditionExpression="attribute_not_exists(job_id)",  # Prevent duplicates
        )
        log.info("aws.job.created", job_id=job_id, status=status)

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve a job record from DynamoDB."""
        try:
            response = self.ddb.get_item(
                TableName=_JOB_TABLE,
                Key={"job_id": {"S": job_id}},
                ConsistentRead=True,
            )
        except ClientError as e:
            log.error("aws.job.get_failed", job_id=job_id, error=str(e))
            return None

        item = response.get("Item")
        if not item:
            return None

        # Flatten DynamoDB types
        return {
            "job_id": item["job_id"]["S"],
            "status": item["status"]["S"],
            "service_name": item["service_name"]["S"],
            "environment": item["environment"]["S"],
            "workspace_name": item["workspace_name"]["S"],
            "template_id": item["template_id"]["S"],
            "owner_team": item["owner_team"]["S"],
            "cost_centre": item["cost_centre"]["S"],
            "resources_created": [r["S"] for r in item.get("resources_created", {}).get("L", [])],
            "outputs": {k: v["S"] for k, v in item.get("outputs", {}).get("M", {}).items()},
            "error_message": item.get("error_message", {}).get("S"),
            "created_at": item["created_at"]["S"],
            "updated_at": item["updated_at"]["S"],
            "completed_at": item.get("completed_at", {}).get("S"),
        }

    async def update_job_status(
        self,
        job_id: str,
        status: str,
        error_message: str | None = None,
        outputs: dict[str, str] | None = None,
        resources_created: list[str] | None = None,
    ) -> None:
        """Update job status in DynamoDB."""
        now = datetime.now(timezone.utc).isoformat()
        update_expr = "SET #s = :s, updated_at = :u"
        expr_names = {"#s": "status"}
        expr_values: dict[str, Any] = {":s": {"S": status}, ":u": {"S": now}}

        if error_message is not None:
            update_expr += ", error_message = :e"
            expr_values[":e"] = {"S": error_message}

        if status in ("SUCCESS", "FAILED", "ROLLED_BACK", "DESTROYED"):
            update_expr += ", completed_at = :c"
            expr_values[":c"] = {"S": now}

        if outputs:
            update_expr += ", outputs = :o"
            expr_values[":o"] = {"M": {k: {"S": v} for k, v in outputs.items()}}

        if resources_created is not None:
            update_expr += ", resources_created = :r"
            expr_values[":r"] = {"L": [{"S": r} for r in resources_created]}

        self.ddb.update_item(
            TableName=_JOB_TABLE,
            Key={"job_id": {"S": job_id}},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
        log.info("aws.job.status_updated", job_id=job_id, status=status)

    async def ensure_ecr_repository(
        self,
        name: str,
        cost_centre: str,
        owner_team: str,
        environment: str,
    ) -> str:
        """Create ECR repository if it doesn't exist; return the repository URI."""
        account_id = await self.get_account_id()
        uri = f"{account_id}.dkr.ecr.{_REGION}.amazonaws.com/{name}"

        try:
            self.ecr.describe_repositories(repositoryNames=[name])
            log.info("aws.ecr.already_exists", name=name)
            return uri
        except self.ecr.exceptions.RepositoryNotFoundException:
            pass

        self.ecr.create_repository(
            repositoryName=name,
            imageScanningConfiguration={"scanOnPush": True},
            encryptionConfiguration={"encryptionType": "AES256"},
            tags=[
                {"Key": "CostCentre", "Value": cost_centre},
                {"Key": "Owner", "Value": owner_team},
                {"Key": "Environment", "Value": environment},
                {"Key": "Project", "Value": name.split("-")[0]},
                {"Key": "ManagedBy", "Value": "idp-platform-api"},
            ],
        )
        log.info("aws.ecr.created", name=name, uri=uri)
        return uri

    async def start_codebuild_run(
        self,
        project_name: str,
        environment_variables: dict[str, str],
    ) -> str:
        """Start a CodeBuild project run and return the build ID."""
        env_overrides = [
            {"name": k, "value": v, "type": "PLAINTEXT"}
            for k, v in environment_variables.items()
        ]
        response = self.codebuild.start_build(
            projectName=project_name,
            environmentVariablesOverride=env_overrides,
        )
        build_id = response["build"]["id"]
        log.info("aws.codebuild.started", project=project_name, build_id=build_id)
        return build_id
