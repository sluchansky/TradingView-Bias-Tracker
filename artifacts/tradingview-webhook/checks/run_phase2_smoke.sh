#!/usr/bin/env bash
# run_phase2_smoke.sh — V1-P2 smoke runner
#
# Runs both Phase 2 smoke scripts in sequence.
# Usage (from any directory):
#
#   bash artifacts/tradingview-webhook/checks/run_phase2_smoke.sh
#
# Each script resolves its own paths from its location — no CWD dependency.
# Exits 0 only when both scripts pass.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

echo "================================================================"
echo "  V1-P2 Phase 2 Smoke Suite"
echo "================================================================"
echo ""

echo "--- V1-P2-001: Databento health smoke ---"
bash "${SCRIPT_DIR}/check_databento_health.sh"
echo ""

echo "--- V1-P2-003: Stale-VWAP gate smoke ---"
bash "${SCRIPT_DIR}/check_stale_vwap.sh"
echo ""

echo "================================================================"
echo "  V1-P2 SMOKE SUITE PASSED"
echo "================================================================"
