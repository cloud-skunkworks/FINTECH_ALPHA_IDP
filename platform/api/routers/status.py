"""Status router — GET /v1/status/{job_id}"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from ..auth.cognito import TokenPayload, require_scope
from ..models.status import JobStatus
from ..services.aws_client import AWSClientFactory

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["Status"])


@router.get(
    "/status/{job_id}",
    response_model=JobStatus,
    summary="Poll provisioning job status",
    description=(
        "Returns the current status of a provisioning job. "
        "Poll this endpoint after POST /v1/provision. "
        "Requires scope: idp:read."
    ),
)
async def get_job_status(
    job_id: str,
    auth: Annotated[TokenPayload, Depends(require_scope("idp:read"))],
    aws: Annotated[AWSClientFactory, Depends()],
) -> JobStatus:
    job = await aws.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return JobStatus(**job)
