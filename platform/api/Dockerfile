# IDP Platform API — Multi-stage Dockerfile
# Target: ARM64 (Graviton) for cost efficiency
# Base: python:3.12-slim for minimal attack surface

# ── Build Stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip==24.0 \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime Stage ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="idp-platform-api"
LABEL org.opencontainers.image.description="IDP Platform API — FinTech infrastructure provisioning service"
LABEL org.opencontainers.image.source="https://github.com/your-org/idp-platform"

# Runtime-only system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Non-root user (matches ECS task user: '1000')
RUN groupadd -r -g 1000 idp && useradd -r -u 1000 -g idp -s /sbin/nologin idp

# Copy installed packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application code
COPY --chown=idp:idp . .

# Drop to non-root
USER idp

EXPOSE 8000

# Liveness probe command (used by ECS health check)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8000/healthz || exit 1

# 4 workers for ARM64/Graviton — tune based on vCPU allocation
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--log-level", "info", \
     "--access-log", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
