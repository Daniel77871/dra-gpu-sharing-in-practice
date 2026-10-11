#!/usr/bin/env bash
set -euo pipefail
[[ "${ALLOW_KFP_UNINSTALL:-}" == 1 ]] || { echo 'Set ALLOW_KFP_UNINSTALL=1; this removes only the recorded KFP installation.' >&2; exit 1; }
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${KFP_STATE_DIR:?Set KFP_STATE_DIR to the original installation directory}"
python3 "$script_dir/kfp_state.py" uninstall --state-dir "$KFP_STATE_DIR"
