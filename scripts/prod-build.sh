#!/usr/bin/env bash
# Production build for the TradingView webhook deployment.
#
# Installs BOTH dependency sets the running VM needs:
#   1. Python deps for the Flask webhook server (flask, requests) into
#      .pythonlibs via uv  -- the deploy image has no venv otherwise.
#   2. The compiled Express /api proxy bundle (dist/index.mjs).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[prod-build] installing Python deps via uv sync"
UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-.pythonlibs}" uv sync --frozen

echo "[prod-build] building Express api-server"
pnpm --filter @workspace/api-server run build

echo "[prod-build] done"
