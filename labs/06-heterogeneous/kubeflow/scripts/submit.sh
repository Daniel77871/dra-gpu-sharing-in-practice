#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $# -ge 1 && $# -le 2 ]] || { echo 'usage: submit.sh h1|h2|l1 [round]' >&2; exit 1; }
: "${KFP_STATE_DIR:?Set KFP_STATE_DIR to the installed backend state directory}"
case_name="$1" round_number="${2:-1}"
output="${OUT_DIR:-$(cd "$KFP_STATE_DIR/.." && pwd)/kubeflow}/${case_name}-r${round_number}"
# submit.py calls curl for upload, Run creation and API polling; no KFP SDK is imported.
python3 "$script_dir/submit.py" "$case_name" --round "$round_number" --state-dir "$KFP_STATE_DIR" --api "${KFP_API:-http://127.0.0.1:8888}" --out "$output"
