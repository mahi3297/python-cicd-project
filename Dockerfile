# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Builder — installs deps in isolated layer
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build deps (gcc needed for some packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python deps into a prefix dir (keeps them isolated)
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --prefix=/install --no-cache-dir -r requirements.txt


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: Production image — minimal, non-root, no build tools
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS production

# OCI standard labels (populated by Jenkins build args)
ARG BUILD_DATE
ARG GIT_COMMIT
ARG APP_VERSION

LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.title="python-cicd-app" \
      org.opencontainers.image.description="Demo Flask app for Jenkins + ArgoCD + K8s"

# Runtime OS deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create non-root user for security
RUN groupadd --gid 1001 appuser \
 && useradd --uid 1001 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

# Copy application source
COPY --chown=appuser:appuser . .

# Set version env vars from build args
ENV APP_VERSION=${APP_VERSION} \
    GIT_COMMIT=${GIT_COMMIT} \
    BUILD_DATE=${BUILD_DATE} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=5000

# Switch to non-root
USER appuser

# Expose app port
EXPOSE 5000

# Health check (Docker-level, K8s probes are separate)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5000/health/live || exit 1

# Start with Gunicorn (production WSGI server)
# - 4 workers  (tune: 2 × CPU cores + 1)
# - threads for I/O concurrency
# - access log to stdout for container log aggregation
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "4", \
     "--threads", "2", \
     "--worker-class", "gthread", \
     "--worker-tmp-dir", "/dev/shm", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "app:create_app()"]
