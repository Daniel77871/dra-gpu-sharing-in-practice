#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $# -le 1 ]] || { printf 'usage: run.sh [run-id]\n' >&2; exit 1; }
run_id="${1:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
exec python3 "$SCRIPT_DIR/control.py" run "$run_id"
