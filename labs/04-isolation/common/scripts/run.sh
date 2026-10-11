#!/usr/bin/env bash
# Invoked by a sub-lab wrapper with BACKEND and LAB_DIR exported.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
SMOKE="${SMOKE:-0}"; ROUNDS="${ROUNDS:-3}"
[[ "$SMOKE" == 0 || "$SMOKE" == 1 ]] || die 'SMOKE must be 0 or 1'
[[ "$ROUNDS" =~ ^[1-9][0-9]*$ ]] || die 'ROUNDS must be positive'
if [[ "$SMOKE" == 1 ]]; then ROUNDS=1; fi
if [[ "$BACKEND" == mps ]]; then
    CASE="${CASE:-all}"
    case "$CASE" in all) cases=(single-4g single-8g dual-8g) ;; single-4g|single-8g|dual-8g) cases=("$CASE") ;; *) die 'CASE must be all, single-4g, single-8g or dual-8g' ;; esac
    if [[ "$SMOKE" == 1 && "$CASE" == all ]]; then cases=(single-8g dual-8g); fi
else
    MODE="${MODE:-both}"
    case "$MODE" in both) cases=(accounting runtime) ;; accounting|runtime) cases=("$MODE") ;; *) die 'MODE must be both, accounting or runtime' ;; esac
fi
init_run "$ISOLATION_DIR" "${1:-$(utc_run_id)}"
RUN_OUT="$OUT_DIR"
if [[ -f "$RUN_OUT/completed.json" ]]; then python3 "$ANALYZER" "$RUN_OUT" --output "$RUN_OUT"; exit 0; fi
[[ ! -f "$RUN_OUT/run.json" ]] || die 'run-id already started; collect and cleanup before choosing a new run-id'
python3 - "$RUN_OUT/run.json" "$BACKEND" "$ROUNDS" "$SMOKE" "${cases[@]}" <<'PY'
import json,sys
json.dump({'backend':sys.argv[2],'rounds':int(sys.argv[3]),'smoke':sys.argv[4]=='1','cases':sys.argv[5:]},open(sys.argv[1],'w'),indent=2)
PY
ROUND_DIR=''
on_exit() {
    local status=$?
    trap - EXIT
    if [[ -n "$ROUND_DIR" && -f "$ROUND_DIR/gpu.csv.pid" ]]; then
        bash "$SAMPLE" stop "$GPU_NODE" "$ROUND_DIR/gpu.csv" || true
    fi
    if (( status != 0 )); then
        if [[ -n "$ROUND_DIR" ]]; then
            printf 'runner exit %s; resources retained, collect before cleanup\n' "$status" > "$ROUND_DIR/run-error.txt"
            update_trial '{"valid":false,"complete":false,"error":"runner failed or interrupted; see run-error.txt"}' || true
        fi
        bash "$ISOLATION_DIR/common/scripts/collect.sh" "$RUN_ID" || true
    fi
    exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
preflight

accounting_round() {
    local repeat="$1" pod_a pod_b pod_c deadline
    update_trial "{\"backend\":\"$BACKEND\",\"case\":\"accounting\",\"repeat\":$repeat,\"valid\":false}"
    wait_gpu_clean before
    OUT_DIR="$ROUND_DIR/idle-before" bash "$ISOLATION_DIR/../common/gpu-idle-check.sh" "$GPU_NODE"
    render "$LAB_DIR/manifests/accounting"
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
    kc -n "$ISOLATION_NS" get resourceclaims -l "$CLAIM_SELECTOR" -o json > "$ROUND_DIR/claims-before-release.json"
    kc -n "$ISOLATION_NS" get pod "$pod_c" -o json > "$ROUND_DIR/c-pending.json"
    python3 - "$ROUND_DIR/c-pending.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
if p['status']['phase']!='Pending' or p['spec'].get('nodeName'): raise SystemExit('third consumer is not scheduler Pending')
PY
    kc get resourceslices -o json > "$ROUND_DIR/resourceslices.json"
    gpu_snapshot during
    # Capture release consumer logs before deletion (the original run lost them).
    bash "$ISOLATION_DIR/common/scripts/collect.sh" "$RUN_ID"
    kc -n "$ISOLATION_NS" delete jobs -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=capacity-release,app.kubernetes.io/instance=$RUN_ID" --wait=true --timeout=120s
    wait_pods_absent "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=capacity-release,app.kubernetes.io/instance=$RUN_ID"
    kc -n "$ISOLATION_NS" delete resourceclaims -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=capacity-release-gpu,app.kubernetes.io/instance=$RUN_ID" --wait=true --timeout=120s
    kc -n "$ISOLATION_NS" wait pod "$pod_c" --for=condition=Ready --timeout=120s
    kc -n "$ISOLATION_NS" get resourceclaims -l "$CLAIM_SELECTOR" -o json > "$ROUND_DIR/claims-after-release.json"
    bash "$ISOLATION_DIR/common/scripts/collect.sh" "$RUN_ID"
    round_cleanup
    helper check-summary "$ROUND_DIR"
}

for selected in "${cases[@]}"; do
    for ((repeat=1; repeat<=ROUNDS; repeat++)); do
        if [[ "$BACKEND" == mps ]]; then
            quota=8; case_name=boundary
            [[ "$selected" == single-4g ]] && quota=4
            [[ "$selected" == dual-8g ]] && case_name=scope
            name="$case_name-${quota}g-r$repeat"
        elif [[ "$selected" == accounting ]]; then name="consumable-accounting-r$repeat"
        else name="consumable-boundary-8g-r$repeat"; fi
        ROUND_DIR="$RUN_OUT/$name"
        mkdir "$ROUND_DIR"
        export OUT_DIR="$ROUND_DIR"
        if [[ "$BACKEND" == mps ]]; then runtime_round "$LAB_DIR/manifests/$selected" "$case_name" "$quota" "$repeat"
        elif [[ "$selected" == runtime ]]; then runtime_round "$LAB_DIR/manifests/runtime" boundary 8 "$repeat"
        else accounting_round "$repeat"; fi
        # Print this round once, after round_cleanup and completeness checks.
        python3 "$ANALYZER" "$ROUND_DIR" --output "$ROUND_DIR"
        python3 "$ANALYZER" "$RUN_OUT" --output "$RUN_OUT" --quiet
        ROUND_DIR=''
    done
done
export OUT_DIR="$RUN_OUT"
python3 "$ANALYZER" "$RUN_OUT" --output "$RUN_OUT" --quiet
printf '{"complete":true}\n' > "$RUN_OUT/completed.json"
