#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

[[ $# -eq 1 ]] || die 'usage: gpu-idle-check.sh <node>'
node="$1"
trap 'status=$?; if (( status != 0 )); then printf "FAIL: GPU idle check on %s\n" "$node" >&2; exit 1; fi' EXIT
require_cmd kubectl
require_cmd python3
ensure_out_dir
threshold="${GPU_IDLE_MAX_MIB:-100}"
# 100 MiB is a conservative scaffold policy, not a measured cluster baseline.
kc get resourceclaims.resource.k8s.io -A -o json > "$OUT_DIR/idle-claims.json"
kc get node "$node" -o json > "$OUT_DIR/idle-node.json"
python3 "$COMMON_DIR/gpu_check.py" claims "$OUT_DIR/idle-claims.json" "$OUT_DIR/idle-node.json"
pod="$(driver_pod "$node")"
kc -n "$DRIVER_NAMESPACE" exec "$pod" -c "$DRIVER_CONTAINER" -- \
    nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits > "$OUT_DIR/idle-memory.csv"
python3 "$COMMON_DIR/gpu_check.py" memory "$OUT_DIR/idle-memory.csv" "$threshold"
printf 'PASS: GPU idle check on %s (memory.used < %s MiB)\n' "$node" "$threshold"
