# syntax=docker/dockerfile:1
#
# Foundry AI OS: one image, API + worker. Multi-stage, multi-arch (linux/amd64, linux/arm64), non-root,
# no secrets baked in, data in the /data volume.
#
#   docker run -d --name foundry-os -p 127.0.0.1:8700:8700 -v foundry-os:/data ghcr.io/suriyavasan/foundry-os:latest
#   docker exec foundry-os foundryos auth setup-token     # first run from another device only
#   open http://localhost:8700
#
# The default command (`serve`) applies the database migrations, then runs the API with the worker
# embedded in the same process: one container is a complete install. docker-compose.yml runs the same image as
# three services instead (migrate -> api -> worker); see docs/deploy.md.
#
# Build (CI builds and smoke-tests this on native amd64 and arm64 runners, .github/workflows/release.yml):
#   docker build -t foundry-os .
#   docker build --build-arg WITH_RUNTIME=0 -t foundry-os-lite .     # without the agent runtime venv (~180 MB less)
#   docker build --build-arg WITH_POSTGRES=1 -t foundry-os-pg .      # adds the psycopg driver
#   docker build --build-arg FOUNDRYOS_VERSION=0.1.1 -t foundry-os .  # the version it reports (default: VERSION)
#
# STATUS: the build is exercised in CI (ci.yml "docker" job); it was NOT built on the machine where this
# file was written (no Docker daemon there).
ARG PYTHON_IMAGE=python:3.14-slim-bookworm
ARG NODE_IMAGE=node:22-alpine
ARG UV_VERSION=0.13.5

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---- 1. web UI (built once, on the builder's own architecture: the output is plain JS) ---------------
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS web
WORKDIR /src/web
RUN corepack enable && corepack prepare pnpm@10.6.2 --activate
COPY web/package.json web/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# ---- 2. python environments (backend + agent runtime) -----------------------------------------------
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
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

# ---- 3. final image ---------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS final
ARG INSTALL_DOCKER_CLI=0
RUN useradd --system --uid 10001 --create-home --home-dir /home/foundry foundry \
 && mkdir -p /data && chown foundry:foundry /data \
 && if [ "$INSTALL_DOCKER_CLI" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends docker.io && rm -rf /var/lib/apt/lists/* ; \
    fi
COPY --from=build --chown=foundry:foundry /app /app
COPY --from=web --chown=foundry:foundry /src/web/dist /app/web/dist
COPY packaging/docker/entrypoint.sh /usr/local/bin/foundry-entrypoint
# The version the image reports (`foundryos --version`, the update check): /app/VERSION is what
# app.core.install.read_version() reads (REPO_DIR = /app). Not /VERSION: that would make bundle_root() match.
# The repo's VERSION is only a floor, so the release workflow passes the real one as a build argument.
ARG FOUNDRYOS_VERSION=
COPY VERSION /app/VERSION
RUN if [ -n "$FOUNDRYOS_VERSION" ]; then printf '%s\n' "$FOUNDRYOS_VERSION" > /app/VERSION; fi
# `foundryos` (and its alias `companyos`) come from the backend venv, which is first on PATH
RUN chmod 0755 /usr/local/bin/foundry-entrypoint

ENV PATH="/app/backend/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    COMPANY_OS_DATABASE_URL="sqlite:////data/company_os.db" \
    COMPANY_OS_DATA_DIR=/data \
    COMPANY_OS_WEB_DIST_DIR=/app/web/dist \
    COMPANY_OS_RUNTIME_DIR=/app/runtime \
    COMPANY_OS_RUNTIME_PYTHON=/app/runtime/.venv/bin/python \
    COMPANY_OS_BIND_HOST=0.0.0.0 \
    COMPANY_OS_EMBEDDED_WORKER=true
VOLUME ["/data"]
WORKDIR /app/backend
USER foundry
EXPOSE 8700
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8700/api/health', timeout=4).status == 200 else 1)"
LABEL org.opencontainers.image.title="Foundry AI OS" \
      org.opencontainers.image.description="Self-hosted operating system for an autonomous company of AI agents" \
      org.opencontainers.image.source="https://github.com/suriyavasan/foundry-os" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${FOUNDRYOS_VERSION}"
# serve = migrate + API (+ embedded worker); api = API only; anything else runs as given
# (compose: `alembic upgrade head`, `companyos-worker`). The worker is `companyos-worker`.
ENTRYPOINT ["foundry-entrypoint"]
CMD ["serve"]
# OPT-IN: let agents run terminal/code tools in sibling containers via the HOST Docker daemon
# (docker-compose.sandbox.yml; read its warning first).
