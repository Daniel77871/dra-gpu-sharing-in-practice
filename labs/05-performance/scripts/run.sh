#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export MODES="${MODES:-all}"
export WORKLOADS="${WORKLOADS:-continuous bursty-aligned bursty-staggered}"
export NS="${NS:-1 2 4}"
export BLOCKS="${BLOCKS:-5}"
export SMOKE="${SMOKE:-0}"
export HAMI="${HAMI:-0}"
RUN_ID="${1:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
exec python3 "$LAB_DIR/analysis/run.py" run "$RUN_ID"
