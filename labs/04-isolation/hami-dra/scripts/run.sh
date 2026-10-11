#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
export BACKEND=hami LAB_DIR="$HAMI_DIR"
SMOKE="${SMOKE:-0}"; MODE="${MODE:-runtime}"
[[ "$SMOKE" == 0 || "$SMOKE" == 1 ]] || die 'SMOKE must be 0 or 1'
case "$MODE" in
    accounting) cases=(accounting) ;;
    runtime) cases=(single dual-process); [[ "$SMOKE" != 1 ]] || cases+=(case4a) ;;
    scope) cases=(case3 case4a case4b); [[ "$SMOKE" != 1 ]] || cases=(case4a) ;;
    *) die 'MODE must be accounting, runtime or scope' ;;
esac
ROUNDS=3; [[ "$SMOKE" != 1 ]] || ROUNDS=1
open_run "${1:-$(utc_run_id)}"
if [[ -f "$RUN_OUT/completed.json" ]]; then
    exec python3 "$HAMI_ANALYZER" "$RUN_OUT" --output "$RUN_OUT"
fi
[[ ! -f "$RUN_OUT/run.json" ]] || die 'run-id already started; collect and cleanup it before choosing a new run-id'
# Internal collection still writes summaries, but the console gets one verdict
# per round only after all cleanup checks and the final analysis have completed.
export HAMI_COLLECT_QUIET=1
python3 - "$RUN_OUT/run.json" "$MODE" "$ROUNDS" "$SMOKE" "${cases[@]}" <<'PY'
import json,sys
json.dump({'backend':'hami','mode':sys.argv[2],'rounds':int(sys.argv[3]),'smoke':sys.argv[4]=='1','cases':sys.argv[5:]},open(sys.argv[1],'w'),indent=2)
PY
ROUND_DIR=''
on_exit() {
    local status=$?
    trap - EXIT
    if [[ -n "$ROUND_DIR" && -f "$ROUND_DIR/gpu.csv.pid" ]]; then bash "$SAMPLE" stop "$GPU_NODE" "$ROUND_DIR/gpu.csv" || true; fi
    if (( status != 0 )); then
        if [[ -n "$ROUND_DIR" ]]; then
            printf 'runner exit %s; resources retained, collect before cleanup\n' "$status" > "$ROUND_DIR/run-error.txt"
            update_trial '{"valid":false,"complete":false,"error":"runner failed or control timeout; see run-error.txt"}' || true
        fi
        OUT_DIR="$RUN_OUT" bash "$HAMI_DIR/scripts/collect.sh" "$RUN_ID" || true
    fi
    export OUT_DIR="$RUN_OUT"
    host_stop || true
    exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
preflight
host_start

accounting_round() {
    local pod_a pod_b pod_c deadline status
    update_trial "{\"backend\":\"hami\",\"case\":\"accounting\",\"repeat\":$REPEAT,\"valid\":false}"
    wait_gpu_clean before
    OUT_DIR="$ROUND_DIR/idle-before" bash "$ISOLATION_DIR/../common/gpu-idle-check.sh" "$GPU_NODE"
    render "$HAMI_DIR/manifests/accounting"
    kc apply -f "$ROUND_DIR/base.json"
    kc apply -f "$ROUND_DIR/capacity-resident-gpu.json"
    kc apply -f "$ROUND_DIR/capacity-resident.json"
    pod_a="$(wait_job_pod capacity-resident a)"
    kc -n "$ISOLATION_NS" wait pod "$pod_a" --for=condition=Ready --timeout=300s
    kc apply -f "$ROUND_DIR/capacity-release-gpu.json"
    kc apply -f "$ROUND_DIR/capacity-release.json"
    pod_b="$(wait_job_pod capacity-release b)"
    kc -n "$ISOLATION_NS" wait pod "$pod_b" --for=condition=Ready --timeout=300s
    kc apply -f "$ROUND_DIR/capacity-waiter-gpu.json"
    kc apply -f "$ROUND_DIR/capacity-waiter.json"
    pod_c="$(wait_job_pod capacity-waiter c)"
    deadline=$((SECONDS + 120))
    while :; do
        kc -n "$ISOLATION_NS" get events -o json > "$ROUND_DIR/events-before-release.json"
        if python3 - "$ROUND_DIR/events-before-release.json" "$pod_c" <<'PY'
import json,sys
events=json.load(open(sys.argv[1]))['items']
messages=[e.get('message','') for e in events if e.get('reason')=='FailedScheduling' and e.get('involvedObject',{}).get('name')==sys.argv[2]]
if any('Insufficient cpu' in m or 'Insufficient memory' in m for m in messages): raise SystemExit(2)
raise SystemExit(0 if any('cannot allocate all claims' in m for m in messages) else 1)
PY
        then break
        else status=$?; (( status == 1 )) || die 'Pending has CPU/RAM cause rather than DRA capacity'; fi
        (( SECONDS < deadline )) || die 'no DRA FailedScheduling event for capacity-waiter'
        sleep 1
    done
    sleep 5
    kc -n "$ISOLATION_NS" get resourceclaims -l "$CLAIM_SELECTOR,app.kubernetes.io/instance=$RUN_ID" -o json > "$ROUND_DIR/claims-before-release.json"
    kc -n "$ISOLATION_NS" get pod "$pod_c" -o json > "$ROUND_DIR/c-pending.json"
    python3 - "$ROUND_DIR/c-pending.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
if p['status']['phase']!='Pending' or p['spec'].get('nodeName'): raise SystemExit('third consumer is not scheduler Pending')
PY
    gpu_snapshot during
    OUT_DIR="$RUN_OUT" bash "$HAMI_DIR/scripts/collect.sh" "$RUN_ID"
    kc -n "$ISOLATION_NS" delete jobs -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=capacity-release,app.kubernetes.io/instance=$RUN_ID" --wait=true --timeout=120s
    wait_pods_absent "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=capacity-release,app.kubernetes.io/instance=$RUN_ID"
    kc -n "$ISOLATION_NS" delete resourceclaims -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=capacity-release-gpu,app.kubernetes.io/instance=$RUN_ID" --wait=true --timeout=120s
    kc -n "$ISOLATION_NS" wait pod "$pod_c" --for=condition=Ready --timeout=120s
    kc -n "$ISOLATION_NS" get resourceclaims -l "$CLAIM_SELECTOR,app.kubernetes.io/instance=$RUN_ID" -o json > "$ROUND_DIR/claims-after-release.json"
    python3 - "$HAMI_DIR/analysis" "$ROUND_DIR" <<'PY'
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import control as h
p=Path(sys.argv[2]);before=json.load(open(p/'claims-before-release.json'))['items'];after=json.load(open(p/'claims-after-release.json'))['items']
for claims,roles in [(before,{'capacity-resident-gpu':16,'capacity-release-gpu':24}),(after,{'capacity-resident-gpu':16,'capacity-waiter-gpu':8})]:
    for name,quota in roles.items(): h.check_allocation(next(c for c in claims if c['metadata']['name']==name),quota,10)
PY
    OUT_DIR="$RUN_OUT" bash "$HAMI_DIR/scripts/collect.sh" "$RUN_ID"
    round_cleanup
    helper check-summary "$ROUND_DIR"
}

for case_name in "${cases[@]}"; do
    for ((REPEAT=1; REPEAT<=ROUNDS; REPEAT++)); do
        ROUND_DIR="$RUN_OUT/$case_name-r$REPEAT"
        mkdir "$ROUND_DIR"
        export OUT_DIR="$ROUND_DIR"
        case "$case_name" in
            accounting) accounting_round ;;
            single) runtime_round "$HAMI_DIR/manifests/runtime/single" boundary ;;
            dual-process) runtime_round "$HAMI_DIR/manifests/runtime/dual-process" scope ;;
            case3|case4a|case4b) scope_round "$case_name" ;;
        esac
    done
done
OUT_DIR="$RUN_OUT" bash "$HAMI_DIR/scripts/cleanup.sh" "$RUN_ID"
python3 "$HAMI_ANALYZER" "$RUN_OUT" --output "$RUN_OUT"
printf '{"complete":true}\n' > "$RUN_OUT/completed.json"
