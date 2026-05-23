"""Pydantic models for the provisioning API."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class ProvisionRequest(BaseModel):
    """
    Request body for POST /v1/provision.

    service_name must match ^[a-z][a-z0-9-]+$ and be globally unique within the environment.
    cost_centre must be a valid CC-NNNN code.
    """

    service_name: str = Field(
        ...,
        min_length=3,
        max_length=40,
        pattern=r"^[a-z][a-z0-9-]+$",
        description=(
            "Lowercase alphanumeric with hyphens. Used as the Kubernetes namespace, "
            "ECR repository name prefix, and AWS resource name prefix."
        ),
        examples=["payments-api", "fraud-detection-worker", "reporting-svc"],
    )
    environment: Literal["dev", "uat", "prod"] = Field(
        ...,
        description="Target deployment environment.",
    )
    template_id: str = Field(
        ...,
        description=(
            "Backstage catalog template ID. Must match a registered template. "
            "See GET /v1/catalog for available templates."
        ),
        examples=["eks-microservice", "ecs-fargate-service", "aurora-postgres"],
    )
    size: Literal["xs", "sm", "md", "lg"] = Field(
        default="sm",
        description=(
            "Workload size tier. Maps to pre-approved CPU/memory configurations:\n"
            "- xs: 0.25 vCPU / 256MB\n"
            "- sm: 0.5 vCPU / 512MB\n"
            "- md: 1 vCPU / 1GB\n"
            "- lg: 2 vCPU / 2GB"
        ),
    )
    owner_team: str = Field(
        ...,
        min_length=2,
        max_length=60,
        description=(
            "GitHub team slug (e.g. 'payments-team'). Used for Kubernetes RBAC, "
            "IRSA namespace binding, and FinOps cost attribution."
        ),
        examples=["payments-team", "fraud-team", "reporting-team"],
    )
    cost_centre: str = Field(
        ...,
        description=(
            "Mandatory cost centre code. Format: CC-NNNN. "
            "Applied as an AWS tag on all provisioned resources."
        ),
        examples=["CC-1001", "CC-2345"],
    )
    region: str = Field(
        default="ca-central-1",
        description=(
            "Target AWS region. Must be in the approved region list "
            "(ca-central-1 or us-east-1). SCP enforced."
        ),
    )
    additional_params: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Template-specific parameter overrides. Validated against the template schema. "
            "See GET /v1/catalog/{template_id} for supported parameters."
        ),
    )

    @field_validator("cost_centre")
    @classmethod
    def validate_cost_centre(cls, v: str) -> str:
        if not v.startswith("CC-"):
            raise ValueError("cost_centre must begin with 'CC-' (e.g. CC-1234)")
        code = v[3:]
        if not code.isdigit() or len(code) < 4:
            raise ValueError("cost_centre format must be CC-NNNN (4+ digits)")
        return v

    @field_validator("region")
    @classmethod
    def validate_region(cls, v: str) -> str:
        allowed = {"ca-central-1", "us-east-1"}
        if v not in allowed:
            raise ValueError(f"region must be one of {allowed}. Other regions are blocked by SCP.")
        return v

    @field_validator("service_name")
    @classmethod
    def validate_no_reserved_names(cls, v: str) -> str:
        reserved = {"default", "kube-system", "kube-public", "kube-node-lease", "idp-platform", "monitoring"}
        if v in reserved:
            raise ValueError(f"'{v}' is a reserved name and cannot be used as a service name.")
        return v

    @model_validator(mode="after")
    def validate_prod_size(self) -> "ProvisionRequest":
        if self.environment == "prod" and self.size == "xs":
            raise ValueError("Size 'xs' is not allowed in production. Use 'sm' or larger.")
        return self


class ProvisionResponse(BaseModel):
    """Response for a successfully accepted provisioning request."""

    job_id: UUID
    status: Literal["ACCEPTED"]
    workspace_name: str = Field(description="CDK stack / workspace name that will be created.")
    ecr_repository: str = Field(description="ECR repository URI (pre-created for immediate use).")
    irsa_role_arn: str = Field(description="IRSA role ARN — annotate your Kubernetes ServiceAccount with this.")
    poll_url: str = Field(description="URL to poll for job status.")
    hcp_run_url: str | None = Field(default=None, description="Direct link to the infrastructure run.")
    eta_seconds: int = Field(default=900, description="Estimated time to completion in seconds.")
    message: str


class DestroyRequest(BaseModel):
    """Request body for DELETE /v1/provision/{provision_id}."""

    confirm: bool = Field(
        ...,
        description="Must be explicitly set to true to confirm destructive operation.",
    )
    reason: str = Field(
        ...,
        min_length=10,
        description="Reason for destruction. Recorded in the audit log.",
    )
