"""Catalog router — GET /v1/catalog, GET /v1/catalog/{template_id}"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from ..auth.cognito import TokenPayload, require_scope
from ..models.catalog import CatalogTemplate, TemplateParameter

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["Catalog"])

# Static catalog — in production, loaded from Backstage API or DynamoDB
CATALOG: dict[str, CatalogTemplate] = {
    "eks-microservice": CatalogTemplate(
        template_id="eks-microservice",
        title="EKS Microservice",
        description=(
            "Provisions a Kubernetes namespace, IRSA role, ECR repository, "
            "ALB ingress, HPA, PDB, and CloudWatch log group for a new EKS-hosted microservice."
        ),
        tags=["eks", "kubernetes", "aws", "recommended"],
        type="service",
        requires_irsa=True,
        provisions=[
            "EKS Namespace",
            "Kubernetes ServiceAccount (with IRSA annotation)",
            "IAM Role (IRSA, scoped to namespace/SA)",
            "ECR Repository",
            "CloudWatch Log Group",
            "CloudWatch Alarm (memory utilisation)",
        ],
        estimated_cost_monthly_usd=50.0,
        parameters=[
            TemplateParameter(
                name="min_replicas",
                type="integer",
                description="Minimum pod replicas.",
                required=False,
                default=1,
                min_value=1,
                max_value=50,
            ),
        ],
    ),
    "ecs-fargate-service": CatalogTemplate(
        template_id="ecs-fargate-service",
        title="ECS Fargate Service",
        description=(
            "Provisions an ECS Fargate service with an internal ALB, ECR repository, "
            "IAM task role, CloudWatch log group, and CodeDeploy blue/green deployment."
        ),
        tags=["ecs", "fargate", "aws"],
        type="service",
        requires_irsa=False,
        provisions=[
            "ECS Task Definition",
            "ECS Service",
            "ALB Target Group",
            "ECR Repository",
            "IAM Task Role",
            "CloudWatch Log Group",
            "CodeDeploy Application + Deployment Group",
        ],
        estimated_cost_monthly_usd=30.0,
    ),
    "aurora-postgres": CatalogTemplate(
        template_id="aurora-postgres",
        title="Aurora Postgres (Serverless v2)",
        description=(
            "Provisions an Aurora Postgres Serverless v2 cluster with encrypted storage, "
            "automated backups, credentials in Secrets Manager, and optional read replica."
        ),
        tags=["rds", "aurora", "postgres", "database"],
        type="database",
        requires_irsa=True,
        provisions=[
            "Aurora Postgres Cluster (Serverless v2)",
            "DB Parameter Group",
            "Security Group",
            "Secrets Manager Secret (credentials)",
            "CloudWatch Log Group (postgres slow query log)",
        ],
        estimated_cost_monthly_usd=120.0,
        parameters=[
            TemplateParameter(
                name="min_acu",
                type="integer",
                description="Minimum Aurora Capacity Units.",
                required=False,
                default=1,
            ),
            TemplateParameter(
                name="max_acu",
                type="integer",
                description="Maximum Aurora Capacity Units.",
                required=False,
                default=8,
            ),
            TemplateParameter(
                name="enable_read_replica",
                type="boolean",
                description="Add a read replica instance (recommended for production).",
                required=False,
                default=False,
            ),
        ],
    ),
    "s3-data-bucket": CatalogTemplate(
        template_id="s3-data-bucket",
        title="S3 Data Bucket",
        description=(
            "Provisions a private S3 bucket with KMS encryption, versioning, "
            "lifecycle rules, public access block, and access logging."
        ),
        tags=["s3", "storage", "aws"],
        type="storage",
        requires_irsa=True,
        provisions=[
            "S3 Bucket (private, encrypted)",
            "S3 Bucket Policy",
            "KMS Key",
            "Lifecycle Rules",
            "IAM Role (read/write, scoped to calling service)",
        ],
        estimated_cost_monthly_usd=5.0,
    ),
    "sqs-queue": CatalogTemplate(
        template_id="sqs-queue",
        title="SQS Queue + DLQ",
        description=(
            "Provisions an SQS Standard or FIFO queue with a dead-letter queue, "
            "KMS encryption, and scoped IAM roles for producers and consumers."
        ),
        tags=["sqs", "queue", "messaging", "aws"],
        type="queue",
        requires_irsa=True,
        provisions=[
            "SQS Queue (Standard or FIFO)",
            "SQS Dead-Letter Queue",
            "KMS Key",
            "IAM Policy (producer)",
            "IAM Policy (consumer)",
        ],
        estimated_cost_monthly_usd=2.0,
        parameters=[
            TemplateParameter(
                name="fifo",
                type="boolean",
                description="Create a FIFO queue instead of Standard.",
                required=False,
                default=False,
            ),
            TemplateParameter(
                name="visibility_timeout_seconds",
                type="integer",
                description="SQS message visibility timeout in seconds.",
                required=False,
                default=30,
            ),
        ],
    ),
    "lambda-function": CatalogTemplate(
        template_id="lambda-function",
        title="Lambda Function",
        description=(
            "Provisions a Lambda function with an execution role, CloudWatch log group, "
            "and optional SQS trigger."
        ),
        tags=["lambda", "serverless", "aws"],
        type="function",
        requires_irsa=False,
        provisions=[
            "Lambda Function",
            "IAM Execution Role",
            "CloudWatch Log Group",
            "Optional: SQS Event Source Mapping",
        ],
        estimated_cost_monthly_usd=1.0,
    ),
}


@router.get(
    "/catalog",
    response_model=list[CatalogTemplate],
    summary="List available catalog templates",
    description="Returns all available infrastructure catalog templates. Requires scope: idp:read.",
)
async def list_catalog(
    auth: Annotated[TokenPayload, Depends(require_scope("idp:read"))],
) -> list[CatalogTemplate]:
    return list(CATALOG.values())


@router.get(
    "/catalog/{template_id}",
    response_model=CatalogTemplate,
    summary="Get catalog template details",
    description="Returns a specific template with full parameter schema. Requires scope: idp:read.",
)
async def get_template(
    template_id: str,
    auth: Annotated[TokenPayload, Depends(require_scope("idp:read"))],
) -> CatalogTemplate:
    template = CATALOG.get(template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found. Use GET /v1/catalog to list available templates.",
        )
    return template
