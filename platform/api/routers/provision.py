"""
Provisioning router — POST /v1/provision, DELETE /v1/provision/{provision_id}
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from opentelemetry import trace

from ..auth.cognito import TokenPayload, require_scope
from ..models.provision import DestroyRequest, ProvisionRequest, ProvisionResponse
from ..services.aws_client import AWSClientFactory
from ..services.notify import notify_slack

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

router = APIRouter(prefix="/v1", tags=["Provisioning"])

# In-memory job store for local dev — replaced by DynamoDB in production
# (see services/aws_client.py for the DynamoDB implementation)
_JOB_STORE: dict[str, dict] = {}


@router.post(
    "/provision",
    response_model=ProvisionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit infrastructure provisioning request",
    description=(
        "Accepts a provisioning request and triggers an asynchronous CDK deployment. "
        "Returns a job_id to poll with GET /v1/status/{job_id}. "
        "Requires scope: idp:provision."
    ),
)
async def provision(
    request: ProvisionRequest,
    background_tasks: BackgroundTasks,
    auth: Annotated[TokenPayload, Depends(require_scope("idp:provision"))],
    aws: Annotated[AWSClientFactory, Depends()],
) -> ProvisionResponse:
    with tracer.start_as_current_span("provision.create") as span:
        job_id = uuid.uuid4()
        workspace_name = f"idp-workload-{request.service_name}-{request.environment}"

        span.set_attribute("job.id", str(job_id))
        span.set_attribute("job.workspace", workspace_name)
        span.set_attribute("job.environment", request.environment)
        span.set_attribute("job.template", request.template_id)

        log.info(
            "provision.accepted",
            job_id=str(job_id),
            service_name=request.service_name,
            environment=request.environment,
            template_id=request.template_id,
            owner_team=request.owner_team,
            cost_centre=request.cost_centre,
            requester=auth.sub,
        )

        # Persist job to DynamoDB (non-blocking — returns immediately)
        try:
            await aws.put_job(
                job_id=str(job_id),
                service_name=request.service_name,
                environment=request.environment,
                workspace_name=workspace_name,
                template_id=request.template_id,
                owner_team=request.owner_team,
                cost_centre=request.cost_centre,
                requester=auth.sub,
                status="PENDING",
            )
        except Exception as exc:
            log.error("provision.job_persist_failed", job_id=str(job_id), error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to persist job state. Please retry.",
            )

        # Trigger CDK deployment asynchronously
        background_tasks.add_task(
            _trigger_cdk_deployment,
            job_id=str(job_id),
            request=request,
            workspace_name=workspace_name,
            aws=aws,
        )

        # Notify Slack
        background_tasks.add_task(
            notify_slack,
            message=(
                f"Provisioning started: `{workspace_name}` | "
                f"Job: `{job_id}` | "
                f"Template: `{request.template_id}` | "
                f"Requested by: `{auth.sub}`"
            ),
        )

        # Pre-create ECR repository synchronously so developers can start building images
        try:
            ecr_uri = await aws.ensure_ecr_repository(
                name=f"{request.service_name}-{request.environment}",
                cost_centre=request.cost_centre,
                owner_team=request.owner_team,
                environment=request.environment,
            )
        except Exception:
            ecr_uri = f"<pending — will be created during provisioning>"

        return ProvisionResponse(
            job_id=job_id,
            status="ACCEPTED",
            workspace_name=workspace_name,
            ecr_repository=ecr_uri,
            irsa_role_arn=(
                f"arn:aws:iam::{await aws.get_account_id()}:"
                f"role/irsa-{request.service_name}-{request.environment}"
            ),
            poll_url=f"/v1/status/{job_id}",
            eta_seconds=900,
            message=(
                f"Provisioning accepted. Poll /v1/status/{job_id} for progress. "
                f"Target: ≤ 15 minutes."
            ),
        )


async def _trigger_cdk_deployment(
    job_id: str,
    request: ProvisionRequest,
    workspace_name: str,
    aws: AWSClientFactory,
) -> None:
    """Background task: trigger CDK deployment via CodeBuild or Step Functions."""
    log.info("provision.cdk_trigger.start", job_id=job_id, workspace=workspace_name)

    try:
        await aws.update_job_status(job_id=job_id, status="PLANNING")

        run_id = await aws.start_codebuild_run(
            project_name="idp-cdk-deploy",
            environment_variables={
                "JOB_ID": job_id,
                "SERVICE_NAME": request.service_name,
                "ENVIRONMENT": request.environment,
                "TEMPLATE_ID": request.template_id,
                "SIZE": request.size,
                "OWNER_TEAM": request.owner_team,
                "COST_CENTRE": request.cost_centre,
                "REGION": request.region,
                "ADDITIONAL_PARAMS": str(request.additional_params),
            },
        )

        log.info("provision.cdk_trigger.success", job_id=job_id, run_id=run_id)
    except Exception as exc:
        log.error("provision.cdk_trigger.failed", job_id=job_id, error=str(exc))
        await aws.update_job_status(job_id=job_id, status="FAILED", error_message=str(exc))
        await notify_slack(
            message=f"Provisioning FAILED: job `{job_id}` | Error: `{exc}`",
            level="error",
        )


@router.delete(
    "/provision/{provision_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Destroy a provisioned environment",
    description="Triggers CDK destroy for the specified provisioning job. Requires scope: idp:destroy.",
)
async def destroy_provision(
    provision_id: str,
    body: DestroyRequest,
    background_tasks: BackgroundTasks,
    auth: Annotated[TokenPayload, Depends(require_scope("idp:destroy"))],
    aws: Annotated[AWSClientFactory, Depends()],
) -> dict:
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must set confirm=true to destroy a provisioned environment.",
        )

    job = await aws.get_job(provision_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provisioning job not found.")

    if job["status"] not in ("SUCCESS",):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot destroy job in status '{job['status']}'. Only 'SUCCESS' jobs can be destroyed.",
        )

    log.info(
        "provision.destroy.accepted",
        provision_id=provision_id,
        reason=body.reason,
        requester=auth.sub,
    )

    background_tasks.add_task(
        _trigger_cdk_destroy,
        provision_id=provision_id,
        job=job,
        reason=body.reason,
        requester=auth.sub,
        aws=aws,
    )

    return {
        "provision_id": provision_id,
        "status": "DESTROY_ACCEPTED",
        "message": "Destroy operation accepted. Resources will be removed within 15 minutes.",
    }


async def _trigger_cdk_destroy(
    provision_id: str,
    job: dict,
    reason: str,
    requester: str,
    aws: AWSClientFactory,
) -> None:
    log.info("provision.destroy.start", provision_id=provision_id, reason=reason, requester=requester)
    try:
        await aws.update_job_status(job_id=provision_id, status="APPLYING")
        await aws.start_codebuild_run(
            project_name="idp-cdk-destroy",
            environment_variables={
                "JOB_ID": provision_id,
                "WORKSPACE_NAME": job["workspace_name"],
                "ENVIRONMENT": job["environment"],
                "DESTROY_REASON": reason,
                "REQUESTER": requester,
            },
        )
        await aws.update_job_status(job_id=provision_id, status="DESTROYED")
    except Exception as exc:
        log.error("provision.destroy.failed", provision_id=provision_id, error=str(exc))
        await aws.update_job_status(job_id=provision_id, status="FAILED", error_message=str(exc))
