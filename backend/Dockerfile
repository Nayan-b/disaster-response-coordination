# Backend: FastAPI + LangGraph agents + the geo data layer.
#
# No apt GDAL packages here on purpose. rasterio's manylinux wheels bundle their own GDAL, so
# installing the system libraries as well produces a much larger image and risks two GDAL builds
# disagreeing at runtime. The wheel is the supported path.

FROM python:3.12-slim-bookworm AS base

# uv resolves and installs from the committed lockfile. Installed from PyPI rather than copied from
# ghcr.io/astral-sh/uv, so the build needs only one registry.
RUN pip install --no-cache-dir "uv==0.9.9"

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# ---- dependency layer -------------------------------------------------------
# Copied separately from the source so editing application code doesn't invalidate
# the (slow) dependency install.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---- application layer ------------------------------------------------------
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Two runtime libraries, and only two:
#   libexpat1 — rasterio's bundled GDAL links against it but the wheel does not vendor it, so
#               `import rasterio` fails on slim with "libexpat.so.1: cannot open shared object
#               file". Confirmed by ldd against the wheel's own .so files as the only gap.
#   curl      — used by the compose healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 curl \
    && rm -rf /var/lib/apt/lists/*

# Run unprivileged. The cache directory is a mounted volume, so it must be owned by this user or
# the first WorldPop download fails with EACCES.
RUN useradd --create-home --uid 10001 flood \
    && mkdir -p /data/cache \
    && chown -R flood:flood /app /data
USER flood

ENV FLOOD_CACHE_DIR=/data/cache \
    FLOOD_CHECKPOINT_DB=/data/checkpoints.sqlite

EXPOSE 8088

# One worker by design: an in-flight LangGraph run is advanced on a worker thread inside this
# process, and the per-thread lock that stops two clients racing the same run is process-local.
# Scaling out means moving that lock into Postgres first.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8088", "--workers", "1"]
