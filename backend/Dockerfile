# Stage 1: Build dependencies with uv
FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install build dependencies
# - build-essential: C compiler for packages without wheels
# - libta-lib: Required for ta-lib Python package (no prebuilt wheels)
# - libhdf5-dev: Required for PyTables/tables package
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    libta-lib0-dev \
    libhdf5-dev \
 && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create virtual environment with uv
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv"

# Install dependencies with uv (much faster than pip)
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r requirements.txt


# Stage 2: Production image
FROM python:3.11-slim-bookworm AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Install only runtime dependencies
# - libta-lib0: TA-Lib shared library (runtime)
# - libhdf5-103-1: HDF5 runtime for PyTables
# - libgomp1: OpenMP runtime (used by sklearn, xgboost, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libta-lib0 \
    libhdf5-103-1 \
    libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && apt-get clean

# Create non-root user for security
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --shell /bin/bash --create-home appuser

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser alembic.ini ./

# Create directory for database with correct permissions
RUN mkdir -p /app/data && chown appuser:appuser /app/data

# Switch to non-root user
USER appuser

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
