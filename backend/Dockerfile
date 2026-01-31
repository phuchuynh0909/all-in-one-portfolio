# Stage 1: Build dependencies with uv
FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

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

# Build TA-Lib 0.6.4 from source (newer version from GitHub)
RUN wget -q https://github.com/TA-Lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz \
 && tar -xzf ta-lib-0.6.4-src.tar.gz \
 && cd ta-lib-0.6.4 \
 && ./configure --prefix=/usr \
 && make -j$(nproc) \
 && make install \
 && cd .. \
 && rm -rf ta-lib-0.6.4 ta-lib-0.6.4-src.tar.gz

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create virtual environment with uv
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv"

# Install dependencies with uv (much faster than pip)
COPY requirements.txt ./
COPY libs/backtesting.py/ ./libs/backtesting.py/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r requirements.txt


# Stage 2: Production image
FROM python:3.11-slim-bookworm AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Copy TA-Lib shared libraries from builder (compiled from source)
COPY --from=builder /usr/lib/libta_lib* /usr/lib/

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
COPY --chown=appuser:appuser app ./app
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
