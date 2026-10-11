#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

[[ $# -eq 3 ]] || die 'usage: gpu-sample.sh start|stop <node> <out-csv>'
action="$1" node="$2" csv="$3"
require_cmd python3
case "$action" in
    start|stop)
        python3 "$COMMON_DIR/gpu_sample.py" "$action" "$node" "$csv"
        ;;
    __worker)
        # Only the local sampler supervisor starts this process group.
        require_cmd kubectl
        pod="$(driver_pod "$node")"
        export KC_REQUEST_TIMEOUT=0s
        kc -n "$DRIVER_NAMESPACE" exec "$pod" -c "$DRIVER_CONTAINER" -- \
            nvidia-smi --query-gpu=timestamp,uuid,name,memory.total,memory.used,memory.free,utilization.gpu \
            --format=csv -lms 500 &
        sample_child=$!
        trap 'wait "$sample_child" || true' TERM INT
        wait "$sample_child"
        ;;
    *) die 'usage: gpu-sample.sh start|stop <node> <out-csv>' ;;
esac
