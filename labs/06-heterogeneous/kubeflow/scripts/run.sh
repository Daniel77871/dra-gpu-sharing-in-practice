#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
lab_dir="$(cd "$script_dir/.." && pwd)"
: "${KFP_STATE_DIR:?Install KFP separately and export KFP_STATE_DIR}"
namespace="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["namespace"])' "$KFP_STATE_DIR/state.json")"
export OUT_DIR="${OUT_DIR:-$(cd "$KFP_STATE_DIR/.." && pwd)/kubeflow}"
mkdir -p "$OUT_DIR"
# The saved installation owns this Namespace; submit.py rechecks its UID and labels.
port="${KFP_PORT:-8888}"
export KFP_API="${KFP_API:-http://127.0.0.1:$port}"
forward_pid=''
sampled=()
stop_background() {
    for node in "${sampled[@]}"; do
        bash "$lab_dir/../../common/gpu-sample.sh" stop "$node" "$OUT_DIR/gpu-$node.csv" || true
    done
    if [[ -n "$forward_pid" ]]; then kill "$forward_pid" 2>/dev/null || true; wait "$forward_pid" 2>/dev/null || true; fi
}
trap stop_background EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ "$KFP_API" == "http://127.0.0.1:$port" ]]; then
    kubectl -n "$namespace" port-forward --address=127.0.0.1 service/ml-pipeline "$port:8888" > "$OUT_DIR/port-forward.log" 2>&1 &
    forward_pid=$!
fi
ready=0
for _ in $(seq 1 60); do
    if curl --fail --silent --max-time 5 "$KFP_API/apis/v2beta1/healthz" > "$OUT_DIR/health.json"; then ready=1; break; fi
    if [[ -n "$forward_pid" ]] && ! kill -0 "$forward_pid" 2>/dev/null; then break; fi
    sleep 2
done
[[ "$ready" == 1 ]] || { echo 'KFP API did not become ready; see port-forward.log' >&2; exit 1; }
for node in dra-a40 dra-4090; do
    bash "$lab_dir/../../common/gpu-sample.sh" start "$node" "$OUT_DIR/gpu-$node.csv"
    sampled+=("$node")
done
repeats="${REPEATS:-3}"
[[ "${SMOKE:-0}" != 1 ]] || repeats=1
[[ "$repeats" =~ ^[1-9][0-9]*$ ]] || { echo 'REPEATS must be positive' >&2; exit 1; }
cases="${CASES:-h1 h2 l1}"
cases="${cases//,/ }"
for round_number in $(seq 1 "$repeats"); do
    for case_name in $cases; do bash "$script_dir/submit.sh" "$case_name" "$round_number"; done
done
python3 "$lab_dir/../analysis/analyze.py" --out "$(cd "$KFP_STATE_DIR/.." && pwd)" --output "$OUT_DIR/summary.json"
