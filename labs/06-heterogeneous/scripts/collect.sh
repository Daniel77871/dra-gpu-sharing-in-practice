#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $# == 1 ]] || { printf 'usage: collect.sh <run-id>\n' >&2; exit 1; }
exec python3 "$SCRIPT_DIR/control.py" collect "$1"
