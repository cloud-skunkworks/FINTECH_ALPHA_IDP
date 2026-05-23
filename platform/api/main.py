"""
IDP Platform API — FastAPI provisioning service.

Provides self-service infrastructure provisioning for the FinTech IDP.
All endpoints require Cognito JWT authentication with appropriate scopes.
"""

import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import boto3
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .routers import catalog, health, provision, status
from .services.aws_client import AWSClientFactory

log = structlog.get_logger(__name__)


def configure_otel(service_name: str, service_version: str) -> None:
    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
        "deployment.environment": os.environ.get("ENVIRONMENT", "unknown"),
    })
    provider = TracerProvider(resource=resource)
    otlp_exporter = OTLPSpanExporter()  # Endpoint from OTEL_EXPORTER_OTLP_ENDPOINT env var
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(provider)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — startup and shutdown."""
    log.info("idp_api.startup", version=app.version)

    # Warm up AWS client connections
    aws_factory = AWSClientFactory()
    await aws_factory.health_check()

    log.info("idp_api.ready")
    yield

    log.info("idp_api.shutdown")


def create_app() -> FastAPI:
    configure_otel(
        service_name="idp-platform-api",
        service_version=os.environ.get("APP_VERSION", "dev"),
    )

    app = FastAPI(
        title="IDP Platform API",
        description=(
            "Self-service infrastructure provisioning API for the FinTech Internal "
            "Developer Platform. Allows developers to provision regulated AWS infrastructure "
            "in ≤ 15 minutes without writing CDK or raising a ticket."
        ),
        version=os.environ.get("APP_VERSION", "1.0.0"),
        docs_url="/docs" if os.environ.get("ENVIRONMENT", "dev") != "prod" else None,
        redoc_url="/redoc" if os.environ.get("ENVIRONMENT", "dev") != "prod" else None,
        openapi_url="/openapi.json" if os.environ.get("ENVIRONMENT", "dev") != "prod" else None,
        lifespan=lifespan,
        # Security: disable OpenAPI schema in production
    )

    # ── Security Middleware ────────────────────────────────────────────────
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "api.idp.internal.example.com",
            "api.idp.dev.internal.example.com",
            "api.idp.uat.internal.example.com",
            "localhost",
            "127.0.0.1",
        ],
    )

    # CORS — internal only; ALB handles external HTTPS termination
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://backstage.internal.example.com"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    # ── Request Logging Middleware ─────────────────────────────────────────
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.monotonic()
        with structlog.contextvars.bound_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        ):
            try:
                response = await call_next(request)
                duration_ms = (time.monotonic() - start) * 1000
                log.info(
                    "http.request",
                    status_code=response.status_code,
                    duration_ms=round(duration_ms, 2),
                )
                response.headers["X-Request-ID"] = request_id
                return response
            except Exception as exc:
                duration_ms = (time.monotonic() - start) * 1000
                log.error("http.request.error", error=str(exc), duration_ms=round(duration_ms, 2))
                raise

    # ── Global Exception Handler ───────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        log.exception("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request.headers.get("X-Request-ID")},
        )

    # ── Routers ───────────────────────────────────────────────────────────
    app.include_router(health.router)
    app.include_router(provision.router)
    app.include_router(catalog.router)
    app.include_router(status.router)

    # ── OTel Auto-Instrumentation ──────────────────────────────────────────
    FastAPIInstrumentor.instrument_app(app)

    return app


app = create_app()
