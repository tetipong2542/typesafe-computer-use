#!/usr/bin/env bash
# Quick launcher for OpenMausBot Full Application (Frontend + Harness Server)
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$PROJECT_ROOT/apps/openmausbot"

echo "========================================================"
echo "🐭 Starting Official OpenMausBot Full Application..."
echo "🇹🇭 UI Language: Thai (ภาษาไทย) & English"
echo "🧠 Engine: TypeSafe Autonomous Tri-Tier Engine"
echo "========================================================"

cd "$APP_DIR"

# Launch harness server in background
echo "📡 Launching Harness Server on port 8799..."
node --experimental-strip-types server/index.ts &
SERVER_PID=$!

# Trap termination signals to kill child processes
cleanup() {
  echo ""
  echo "🛑 Stopping OpenMausBot..."
  kill $SERVER_PID 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# Give server a brief moment to bind
sleep 1.5

# Launch Vite Frontend UI
echo "💻 Launching OpenMausBot UI on http://127.0.0.1:5199..."
exec pnpm dev
