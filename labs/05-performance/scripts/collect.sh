#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $# -eq 1 ]] || { printf 'usage: collect.sh <run-id>\n' >&2; exit 2; }
exec python3 "$SCRIPT_DIR/../analysis/run.py" collect "$1"
