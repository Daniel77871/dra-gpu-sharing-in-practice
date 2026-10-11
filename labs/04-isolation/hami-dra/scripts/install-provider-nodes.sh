#!/usr/bin/env bash
set -euo pipefail
[[ "${I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER:-}" == 1 ]] || { printf 'Set I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1; this switches the listed GPU providers and changes host files.\n' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $# == 1 ]] || { printf 'usage: install-provider-nodes.sh <provider-run-id>\n' >&2; exit 1; }
exec python3 "$SCRIPT_DIR/provider_nodes.py" install "$1"
