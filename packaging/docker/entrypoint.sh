#!/bin/sh
# Container entrypoint of the Foundry AI OS image.
#   serve (default)   apply database migrations, then run the API on :8700; the worker runs inside the same
#                     process unless COMPANY_OS_EMBEDDED_WORKER=false
#   api               run the API only (docker-compose.yml: migrations are a separate one-shot service)
#   <anything else>   executed as given, e.g. `companyos-worker`, `alembic upgrade head`, `foundryos doctor`
set -eu

serve_api() {
    exec uvicorn app.main:app --host 0.0.0.0 --port "${COMPANY_OS_PORT:-8700}" \
        --proxy-headers --forwarded-allow-ips '*'
}

case ${1:-serve} in
    serve)
        alembic upgrade head
        serve_api
        ;;
    api)
        serve_api
        ;;
    *)
        exec "$@"
        ;;
esac
