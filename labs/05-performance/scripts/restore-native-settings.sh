#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $# -eq 1 ]] || { printf 'usage: restore-native-settings.sh <provider-run-id>\n' >&2; exit 1; }
exec python3 "$SCRIPT_DIR/../analysis/native_settings.py" restore "$1"
