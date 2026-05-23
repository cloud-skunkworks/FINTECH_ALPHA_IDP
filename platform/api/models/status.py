"""Job status models."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class JobStatus(BaseModel):
    """Provisioning job status — returned by GET /v1/status/{job_id}."""

    job_id: UUID
    status: Literal["PENDING", "PLANNING", "APPLYING", "SUCCESS", "FAILED", "ROLLED_BACK", "DESTROYED"]
    service_name: str
    environment: str
    workspace_name: str
    owner_team: str
    cost_centre: str
    template_id: str
    resources_created: list[str] = Field(
        default_factory=list,
        description="ARNs / IDs of AWS resources created by this job.",
    )
    outputs: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Key outputs from the provisioning run, e.g. "
            "{'ecr_uri': '...', 'irsa_role_arn': '...', 'namespace': '...'}"
        ),
    )
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
