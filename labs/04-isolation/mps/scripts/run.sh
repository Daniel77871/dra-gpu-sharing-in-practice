#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export BACKEND=mps
exec bash "$LAB_DIR/../common/scripts/run.sh" "$@"
