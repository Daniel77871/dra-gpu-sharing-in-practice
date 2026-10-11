#!/usr/bin/env bash
set -euo pipefail
[[ "${ALLOW_KFP_INSTALL:-}" == 1 ]] || { echo 'Set ALLOW_KFP_INSTALL=1; this installs cluster-scoped KFP resources.' >&2; exit 1; }
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
lab_dir="$(cd "$script_dir/.." && pwd)"
export KFP_RUN_ID="${KFP_RUN_ID:-$(date -u +%Y%m%d%H%M%S)}"
export KFP_NAMESPACE="${KFP_NAMESPACE:-gpu-kubeflow-${KFP_RUN_ID}-lab}"
export KFP_STATE_DIR="${KFP_STATE_DIR:-$lab_dir/out/$KFP_RUN_ID/kfp-install}"
python3 "$script_dir/kfp_state.py" install --state-dir "$KFP_STATE_DIR" --run-id "$KFP_RUN_ID" --namespace "$KFP_NAMESPACE"
