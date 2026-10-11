#!/usr/bin/env bash
set -euo pipefail
[[ "${I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER:-}" == 1 ]] || { printf 'Set I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1 for provider changes.\n' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $# == 1 ]] || { printf 'usage: restore-nodes.sh <provider-run-id>\n' >&2; exit 1; }
exec python3 "$SCRIPT_DIR/control_nodes.py" restore "$1"
