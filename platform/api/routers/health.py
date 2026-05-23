"""Health and readiness probes."""

import os
import time

import boto3
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = structlog.get_logger(__name__)
router = APIRouter(tags=["Health"])

_START_TIME = time.time()


@router.get(
    "/healthz",
    summary="Liveness probe",
    description="Returns 200 if the process is alive. No auth required.",
    include_in_schema=False,
)
async def liveness() -> dict:
    return {"status": "ok", "uptime_seconds": round(time.time() - _START_TIME, 1)}


@router.get(
    "/readyz",
    summary="Readiness probe",
    description=(
        "Returns 200 if the service is ready to serve traffic. "
        "Checks: DynamoDB connectivity, Secrets Manager, AWS identity. "
        "No auth required (internal probe only)."
    ),
    include_in_schema=False,
)
async def readiness() -> JSONResponse:
    checks: dict[str, str] = {}
    all_ok = True

    # Check AWS identity (confirms IRSA/task role is functional)
    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        checks["aws_identity"] = "ok"
    except Exception as e:
        checks["aws_identity"] = f"error: {e}"
        all_ok = False

    # Check DynamoDB (job state store)
    try:
        table_name = os.environ.get("JOB_TABLE_NAME", "idp-provision-jobs")
        ddb = boto3.client("dynamodb")
        ddb.describe_table(TableName=table_name)
        checks["dynamodb"] = "ok"
    except Exception as e:
        checks["dynamodb"] = f"error: {e}"
        all_ok = False

    # Check Secrets Manager (API secrets)
    try:
        sm = boto3.client("secretsmanager")
        env = os.environ.get("ENVIRONMENT", "dev")
        sm.describe_secret(SecretId=f"/idp/{env}/platform-api")
        checks["secrets_manager"] = "ok"
    except Exception as e:
        checks["secrets_manager"] = f"error: {e}"
        all_ok = False

    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
    )
