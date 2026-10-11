#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISOLATION_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$ISOLATION_DIR/../common/lib.sh"
[[ $# -eq 3 ]] || die 'usage: sample.sh start|stop|__worker <node> <gpu.csv>'
case "$1" in
    start|stop) python3 "$ISOLATION_DIR/common/analysis/sampler.py" "$1" "$2" "$3" ;;
    __worker)
        driver="$(driver_pod "$2")"
        export KC_REQUEST_TIMEOUT=0s
        kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- env TZ=UTC \
            nvidia-smi --query-gpu=timestamp,uuid,memory.total,memory.used,memory.free,utilization.gpu,compute_mode \
            --format=csv -lms 500
        ;;
    *) die 'unknown sampler action' ;;
esac
