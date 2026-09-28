#!/usr/bin/env bash
# Quick launcher for OpenMausBot Web Dashboard
set -e
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT/packages/openmausbot-adapter"
exec bun run dev
