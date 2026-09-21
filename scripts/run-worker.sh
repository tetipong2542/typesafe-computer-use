#!/usr/bin/env bash
# ==============================================================================
# run-worker.sh: Run the typesafe-computer-use Background Worker Daemon
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

# Load guest-local .env securely if present
ENV_FILE="${PROJECT_ROOT}/.env"
if [[ -e "${ENV_FILE}" ]]; then
    if [[ -L "${ENV_FILE}" ]]; then
        echo "SECURITY ERROR: ${ENV_FILE} must be a regular file, not a symbolic link." >&2
        exit 1
    fi
    if [[ ! -f "${ENV_FILE}" ]]; then
        echo "SECURITY ERROR: ${ENV_FILE} is not a regular file." >&2
        exit 1
    fi

    # Verify file owner matches current guest user
    ENV_OWNER=$(stat -f "%u" "${ENV_FILE}" 2>/dev/null || stat -c "%u" "${ENV_FILE}" 2>/dev/null || id -u)
    if [[ "${ENV_OWNER}" != "$(id -u)" ]]; then
        echo "SECURITY ERROR: ${ENV_FILE} owner (${ENV_OWNER}) does not match current user ($(id -u))." >&2
        exit 1
    fi

    # Verify file permissions are not wider than 0600
    PERMS=$(stat -f "%Lp" "${ENV_FILE}" 2>/dev/null || stat -c "%a" "${ENV_FILE}" 2>/dev/null || echo "600")
    if [[ "${PERMS}" -gt 600 ]]; then
        echo "WARNING: ${ENV_FILE} permissions (${PERMS}) are too open. Enforcing 0600."
        chmod 600 "${ENV_FILE}"
    fi

    # Parse only allowlisted variables without shell execution (no `source`)
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#"${line%%[![:space:]]*}"}" # strip leading spaces
        [[ "$line" =~ ^# ]] && continue       # skip comments
        [[ -z "$line" ]] && continue          # skip empty lines

        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            val="${BASH_REMATCH[2]}"
            # Strip outer quotes and trailing spaces
            val="${val#"${val%%[![:space:]]*}"}"
            val="${val%"${val##*[![:space:]]}"}"
            val="${val#\"}"
            val="${val%\"}"
            val="${val#\'}"
            val="${val%\'}"

            case "$key" in
                WORKER_AUTH_TOKEN)
                    export WORKER_AUTH_TOKEN="$val"
                    ;;
                WORKER_HOST)
                    export WORKER_HOST="$val"
                    ;;
                WORKER_PORT)
                    export WORKER_PORT="$val"
                    ;;
                TYPESAFE_INPUT_LOCK_PATH)
                    export TYPESAFE_INPUT_LOCK_PATH="$val"
                    ;;
                TYPESAFE_API_KEY)
                    export TYPESAFE_API_KEY="$val"
                    ;;
                CLICKER_BROWSER)
                    export CLICKER_BROWSER="$val"
                    ;;
                OPENAI_API_KEY)
                    export OPENAI_API_KEY="$val"
                    ;;
                OPENAI_BASE_URL)
                    export OPENAI_BASE_URL="$val"
                    ;;
                CLICKER_WRITER_MODEL)
                    export CLICKER_WRITER_MODEL="$val"
                    ;;
                CLICKER_ANSWER_MODEL)
                    export CLICKER_ANSWER_MODEL="$val"
                    ;;
                INTERACTION_ROUTER_MODE)
                    export INTERACTION_ROUTER_MODE="$val"
                    ;;
                INTERACTION_ROUTER_ENABLED)
                    export INTERACTION_ROUTER_ENABLED="$val"
                    ;;
                *)
                    # Ignore non-allowlisted configuration keys
                    ;;
            esac
        fi
    done < "${ENV_FILE}"
    echo "Loaded guest configuration from .env (allowlisted keys only)"
fi


mkdir -p "${PROJECT_ROOT}/runs"

export WORKER_HOST="${WORKER_HOST:-0.0.0.0}"
export WORKER_PORT="${WORKER_PORT:-8000}"
if [[ -z "${WORKER_AUTH_TOKEN:-}" ]]; then
    export WORKER_AUTH_TOKEN="$(openssl rand -hex 32)"
    echo "[Security] Generated secure WORKER_AUTH_TOKEN: [REDACTED]"
fi

echo "=========================================================="
echo " Starting typesafe-computer-use Worker API"
echo " Host: ${WORKER_HOST}"
echo " Port: ${WORKER_PORT}"
echo "=========================================================="

PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
if [[ -x "${PYTHON_BIN}" ]]; then
    exec "${PYTHON_BIN}" -m typesafe_computer_use.worker.server --host "${WORKER_HOST}" --port "${WORKER_PORT}"
elif command -v uv &>/dev/null; then
    exec uv run python -m typesafe_computer_use.worker.server --host "${WORKER_HOST}" --port "${WORKER_PORT}"
else
    exec python3 -m typesafe_computer_use.worker.server --host "${WORKER_HOST}" --port "${WORKER_PORT}"
fi
