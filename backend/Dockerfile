# syntax=docker/dockerfile:1
# Base image shared by lock resolution and the production dependency build.
FROM python:3.14-slim-bookworm AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PIP_CACHE_DIR=/root/.cache/pip \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Avoid setuptools_scm git dependency for editable local install
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_BACKTESTING=0.6.5

# Install build dependencies
# - build-essential: C compiler for packages without wheels
# - libhdf5-dev: Required for PyTables/tables package
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    libhdf5-dev \
 && rm -rf /var/lib/apt/lists/*

# Use TA-Lib's architecture-specific Debian package rather than compiling the
# C library. Keep the package for the runtime stage so both stages use exactly
# the same 0.7.1 library and headers.
ARG TARGETARCH
RUN case "$TARGETARCH" in \
      amd64|arm64) wget -q \
        "https://github.com/TA-Lib/ta-lib/releases/download/v0.7.1/ta-lib_0.7.1_${TARGETARCH}.deb" \
        -O /tmp/ta-lib.deb ;; \
      *) echo "Unsupported TA-Lib architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && dpkg-deb -x /tmp/ta-lib.deb /

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create virtual environment with uv
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv"

# The lock must be resolved before the dependency layer consumes it. Otherwise
# an incompatible old lock prevents `make lock-backend` from producing its 3.14
# replacement.
FROM base AS lock

# Stage 1: Build production dependencies from the generated lock.
FROM base AS builder

# Install the fully pinned dependency set (346 packages, all ==).
# requirements.lock.txt is generated from requirements.txt by `make lock-backend`,
# resolved inside this very image so the environment markers match linux/py3.14.
# Editing requirements.txt alone does NOT change this layer — regenerate the lock.
#
# The `-e ./libs/backtesting.py` line is stripped here on purpose: copying that
# 5 MB source tree in before this layer would make every edit to it reinstall the
# whole dependency set (torch, catboost, xgboost, ...).
COPY requirements.lock.txt ./
RUN --mount=type=cache,id=all-in-one-uv-cache,target=/root/.cache/uv \
    --mount=type=cache,id=all-in-one-pip-cache,target=/root/.cache/pip \
    grep -v '^-e \./libs/' requirements.lock.txt > /tmp/third-party.txt \
 && uv pip install -r /tmp/third-party.txt --index-strategy unsafe-best-match

# Editable local package last: its source changes far more often than the
# dependency set above, and only this cheap layer is invalidated when it does.
# --no-deps is safe *because* of the lock: backtesting.py wants numpy/pandas/bokeh
# and the lock pins all three, so nothing here can silently upgrade them.
COPY libs/backtesting.py/ ./libs/backtesting.py/
RUN --mount=type=cache,id=all-in-one-uv-cache,target=/root/.cache/uv \
    uv pip install --no-deps -e ./libs/backtesting.py


# Stage 2: Production image
FROM python:3.14-slim-bookworm AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Install the official TA-Lib binary package selected in the base stage.
COPY --from=base /tmp/ta-lib.deb /tmp/ta-lib.deb
RUN dpkg-deb -x /tmp/ta-lib.deb / && rm /tmp/ta-lib.deb

# Install only runtime dependencies
# - libhdf5-103-1: HDF5 runtime for PyTables
# - libgomp1: OpenMP runtime (used by sklearn, xgboost, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libhdf5-103-1 \
    libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && apt-get clean \
 && ldconfig

# Create non-root user for security
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --shell /bin/bash --create-home appuser

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
# models/ is deliberately not copied: both compose files bind-mount it over
# /app/models at runtime, so baking it in only added ~77 MB and made every
# retrained model invalidate this layer and everything after it.
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser tasks ./tasks
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser libs/backtesting.py/ ./libs/backtesting.py/

# Create directory for database with correct permissions
RUN mkdir -p /app/data && chown appuser:appuser /app/data

# Switch to non-root user
USER appuser

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
