#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $# -eq 1 ]] || { printf 'usage: install-native-settings.sh <provider-run-id>\n' >&2; exit 1; }
printf 'Will back up the NVIDIA DaemonSet and enable performance feature gates; only the target plugin is restarted.\n'
exec python3 "$SCRIPT_DIR/../analysis/native_settings.py" install "$1"
