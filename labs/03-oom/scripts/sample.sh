#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$LAB_DIR/../common/lib.sh"
[[ $# -eq 3 ]] || die 'usage: sample.sh start|stop|__worker <node> <gpu.jsonl>'
case "$1" in
    start|stop) python3 "$LAB_DIR/analysis/sampler.py" "$1" "$2" "$3" ;;
    __worker)
        pod="$(driver_pod "$2")"
        export KC_REQUEST_TIMEOUT=0s
        kc -n "$DRIVER_NAMESPACE" exec "$pod" -c "$DRIVER_CONTAINER" -- \
            nvidia-smi --query-gpu=uuid,name,memory.total,memory.used,memory.free,utilization.gpu,compute_mode \
            --format=csv,noheader,nounits -lms 500 |
            python3 -u "$LAB_DIR/analysis/gpu_csv.py"
        ;;
    *) die 'usage: sample.sh start|stop <node> <gpu.jsonl>' ;;
esac
