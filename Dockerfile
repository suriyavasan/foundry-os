# syntax=docker/dockerfile:1
#
# Company OS: API + worker image (one image, two commands). Multi-stage, non-root, no secrets baked in.
# STATUS: written but NOT built or run in the environment where M9 was done (no Docker daemon there).
#
#   docker build -t companyos .
#   docker build --build-arg WITH_RUNTIME=0 -t companyos-lite .     # without the agent runtime venv (~180 MB less)
#   docker build --build-arg WITH_POSTGRES=1 -t companyos-pg .      # adds the psycopg driver
#
ARG PYTHON_IMAGE=python:3.14-slim-bookworm
ARG NODE_IMAGE=node:22-alpine

# ---- 1. web UI -----------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS web
WORKDIR /src/web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# ---- 2. python environments (backend + agent runtime) -----------------------------------------------
FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
ARG WITH_RUNTIME=1
ARG WITH_POSTGRES=0

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project $( [ "$WITH_POSTGRES" = "1" ] && echo "--extra postgres" )
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev $( [ "$WITH_POSTGRES" = "1" ] && echo "--extra postgres" )

WORKDIR /app/runtime
COPY runtime/ ./
# Hermes' exact pins from its lockfile (never re-resolved). Same extras as runtime/README.md.
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$WITH_RUNTIME" = "1" ]; then \
      uv sync --frozen --no-install-project --extra anthropic --extra ddgs ; \
    fi

# ---- 3. final image -------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS final
ARG INSTALL_DOCKER_CLI=0
RUN useradd --system --uid 10001 --create-home --home-dir /home/companyos companyos \
 && mkdir -p /data && chown companyos:companyos /data \
 && if [ "$INSTALL_DOCKER_CLI" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends docker.io && rm -rf /var/lib/apt/lists/* ; \
    fi
COPY --from=build --chown=companyos:companyos /app /app
COPY --from=web --chown=companyos:companyos /src/web/dist /app/web/dist

ENV PATH="/app/backend/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    COMPANY_OS_DATABASE_URL="sqlite:////data/company_os.db" \
    COMPANY_OS_DATA_DIR=/data \
    COMPANY_OS_WEB_DIST=/app/web/dist \
    COMPANY_OS_RUNTIME_DIR=/app/runtime \
    COMPANY_OS_BIND_HOST=0.0.0.0 \
    COMPANY_OS_EMBEDDED_WORKER=false
VOLUME ["/data"]
WORKDIR /app/backend
USER companyos
EXPOSE 8700
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8700/api/health', timeout=4).status == 200 else 1)"
# Default: the API (+ the web UI). The worker is `companyos-worker`; migrations are `alembic upgrade head`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8700", "--proxy-headers", "--forwarded-allow-ips", "*"]
