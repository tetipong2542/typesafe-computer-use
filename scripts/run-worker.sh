#!/usr/bin/env bash
# ==============================================================================
# run-worker.sh: Run the typesafe-computer-use Background Worker Daemon
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

# Load .env if present
if [[ -f .env ]]; then
    echo "Loading environment from .env"
    set -a
    source .env
    set +a
fi

export WORKER_HOST="${WORKER_HOST:-0.0.0.0}"
export WORKER_PORT="${WORKER_PORT:-8000}"
if [[ -z "${WORKER_AUTH_TOKEN:-}" ]]; then
    export WORKER_AUTH_TOKEN="$(openssl rand -hex 32)"
    echo "[Security] Generated secure WORKER_AUTH_TOKEN: ${WORKER_AUTH_TOKEN}"
fi

echo "=========================================================="
echo " Starting typesafe-computer-use Worker API"
echo " Host: ${WORKER_HOST}"
echo " Port: ${WORKER_PORT}"
echo "=========================================================="

exec uv run clicker-worker
