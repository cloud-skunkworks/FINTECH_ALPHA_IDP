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
_REGION = os.environ.get("AWS_REGION", "ca-central-1")


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
def readiness() -> JSONResponse:
    # Plain `def`: FastAPI runs it in a threadpool, so blocking boto3 calls
    # don't stall the event loop.
    checks: dict[str, str] = {}
    all_ok = True

    # Check AWS identity (confirms IRSA/task role is functional)
    try:
        sts = boto3.client("sts", region_name=_REGION)
        sts.get_caller_identity()
        checks["aws_identity"] = "ok"
    except Exception as e:
        log.warning("readyz.check_failed", check="aws_identity", error=str(e))
        checks["aws_identity"] = "error"
        all_ok = False

    # Check DynamoDB (job state store)
    try:
        table_name = os.environ.get("JOB_TABLE_NAME", "idp-provision-jobs")
        ddb = boto3.client("dynamodb", region_name=_REGION)
        ddb.describe_table(TableName=table_name)
        checks["dynamodb"] = "ok"
    except Exception as e:
        log.warning("readyz.check_failed", check="dynamodb", error=str(e))
        checks["dynamodb"] = "error"
        all_ok = False

    # Check Secrets Manager (API secrets)
    try:
        sm = boto3.client("secretsmanager", region_name=_REGION)
        env = os.environ.get("ENVIRONMENT", "dev")
        sm.describe_secret(SecretId=f"/idp/{env}/platform-api")
        checks["secrets_manager"] = "ok"
    except Exception as e:
        log.warning("readyz.check_failed", check="secrets_manager", error=str(e))
        checks["secrets_manager"] = "error"
        all_ok = False

    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
    )
