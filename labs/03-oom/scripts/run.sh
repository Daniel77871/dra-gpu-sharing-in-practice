#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$LAB_DIR/../common/lib.sh"
require_cmd python3
MODE="${MODE:-both}"; ROUNDS="${ROUNDS:-3}"; SMOKE="${SMOKE:-0}"
[[ "$MODE" == both || "$MODE" == hold || "$MODE" == exit ]] || die 'MODE must be both, hold or exit'
[[ "$ROUNDS" =~ ^[1-9][0-9]*$ ]] || die 'ROUNDS must be positive'
[[ "$SMOKE" == 0 || "$SMOKE" == 1 ]] || die 'SMOKE must be 0 or 1'
if [[ "$SMOKE" == 1 ]]; then MODE=hold; ROUNDS=1; fi
GPU_NODE="${GPU_NODE:-dra-a40}"
init_run "$LAB_DIR" "${1:-$(utc_run_id)}"
RUN_OUT="$OUT_DIR"
HELPER="$LAB_DIR/analysis/control.py"
SELECTOR="$BOOK_SELECTOR,$COMPONENT_LABEL_KEY in (steady-service,greedy-batch)"
if [[ -f "$RUN_OUT/completed.json" ]]; then
    python3 "$LAB_DIR/analysis/analyze.py" "$RUN_OUT"
    exit 0
fi
[[ ! -f "$RUN_OUT/run.json" ]] || die 'run-id already started; collect, clean up and choose a new run-id'
python3 - "$RUN_OUT/run.json" "$MODE" "$ROUNDS" "$SMOKE" "$GPU_NODE" <<'PY'
import json, sys
with open(sys.argv[1], 'w') as stream:
    json.dump({'mode': sys.argv[2], 'rounds': int(sys.argv[3]), 'smoke': sys.argv[4] == '1', 'node': sys.argv[5]}, stream, indent=2)
PY
ROUND_DIR=''
on_exit() {
    local status=$?
    trap - EXIT
    if [[ -n "$ROUND_DIR" ]]; then
        if [[ -f "$ROUND_DIR/gpu.jsonl.pid" ]]; then
            bash "$SCRIPT_DIR/sample.sh" stop "$GPU_NODE" "$ROUND_DIR/gpu.jsonl" || true
        fi
        if (( status != 0 )); then
            printf 'runner exit %s; collect before cleanup\n' "$status" > "$ROUND_DIR/run-error.txt"
            python3 "$HELPER" failed "$ROUND_DIR" || true
        fi
    fi
    if (( status != 0 )); then bash "$SCRIPT_DIR/collect.sh" "$RUN_ID" || true; fi
    exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

kc get namespace gpu-oom-lab --ignore-not-found -o json > "$RUN_OUT/namespace-before.json"
if [[ -s "$RUN_OUT/namespace-before.json" ]]; then
    python3 - "$RUN_OUT/namespace-before.json" <<'PY'
import json, sys
namespace = json.load(open(sys.argv[1]))
if namespace['metadata'].get('labels', {}).get('app.kubernetes.io/part-of') != 'dra-book':
    raise SystemExit('refusing to adopt gpu-oom-lab without dra-book ownership')
PY
fi
kc -n gpu-oom-lab get configmaps,resourceclaims.resource.k8s.io,jobs,pods -o json > "$RUN_OUT/objects-before.json"
python3 "$HELPER" ownership "$RUN_OUT/objects-before.json"
kc get resourceslices.resource.k8s.io -o json > "$RUN_OUT/slices.json"
kc get deviceclass gpu.nvidia.com -o json > "$RUN_OUT/deviceclass.json"
python3 "$HELPER" target "$RUN_OUT/slices.json" "$RUN_OUT/deviceclass.json" "$GPU_NODE" > "$RUN_OUT/target.json"
kc version -o json > "$RUN_OUT/version.json"
kc kustomize "$LAB_DIR/manifests" --load-restrictor=LoadRestrictionsNone > "$RUN_OUT/manifests.yaml"
kc create --dry-run=client --validate=false -f "$RUN_OUT/manifests.yaml" -o json > "$RUN_OUT/manifests.json"
python3 "$HELPER" split "$RUN_OUT/manifests.json" "$RUN_OUT"

wait_pod() {
    local job="$1" prefix="$2" deadline=$((SECONDS + 600)) pod
    while :; do
        kc -n gpu-oom-lab get pods -l "$BOOK_SELECTOR,job-name=$job" -o json > "$ROUND_DIR/$prefix-pods.json"
        if pod="$(python3 "$HELPER" pod "$ROUND_DIR/$prefix-pods.json")"; then
            kc -n gpu-oom-lab get pod "$pod" -o json > "$ROUND_DIR/$prefix-created.json"
            printf '%s\n' "$pod" > "$ROUND_DIR/$prefix-pod-name.txt"
            printf '%s\n' "$pod"
            return 0
        fi
        (( SECONDS < deadline )) || die "timeout waiting for Job Pod: $job"
        sleep 1
    done
}
wait_event() {
    local pod="$1" prefix="$2" event="$3" timeout="$4" command_id="${5:-}" deadline
    deadline=$((SECONDS + timeout))
    while :; do
        kc -n gpu-oom-lab get pod "$pod" -o json > "$ROUND_DIR/$prefix-current.json"
        # A waiting container has no logs yet. Terminated containers do: B may
        # exit between polls, so inspect their final logs before rejecting it.
        if python3 "$HELPER" logs-ready "$ROUND_DIR/$prefix-current.json"; then
            if kc -n gpu-oom-lab logs "$pod" -c workload > "$ROUND_DIR/$prefix-current.jsonl"; then
                if [[ -n "$command_id" ]]; then
                    if python3 "$HELPER" event "$ROUND_DIR/$prefix-current.jsonl" "$event" "$command_id" > "$ROUND_DIR/$prefix-$event.json"; then return 0; fi
                elif python3 "$HELPER" event "$ROUND_DIR/$prefix-current.jsonl" "$event" > "$ROUND_DIR/$prefix-$event.json"; then return 0; fi
            fi
        fi
        python3 - "$ROUND_DIR/$prefix-current.json" "$event" <<'PY'
import json, sys
pod = json.load(open(sys.argv[1]))
if pod.get('status', {}).get('phase') in ('Failed', 'Succeeded'):
    raise SystemExit('Pod terminated before ' + sys.argv[2])
PY
        (( SECONDS < deadline )) || die "timeout waiting for $event"
        sleep 1
    done
}
control() {
    local pod="$1" prefix="$2" phase="$3" probe="$4" start="$5" stop="$6" body command_id
    body="$(python3 "$HELPER" command "$phase" "$probe" "$start" "$stop")"
    command_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["command_id"])' "$body")"
    kc -n gpu-oom-lab exec "$pod" -c workload -- python -c \
        'import os,sys; from pathlib import Path; p=Path("/tmp/gpu-oom-control.tmp"); p.write_text(sys.argv[1]); os.replace(str(p),"/tmp/gpu-oom-control.json")' "$body"
    if [[ "$prefix" == a ]]; then wait_event "$pod" a CONTROL_ACK 60 "$command_id"; fi
}
wait_terminated() {
    local pod="$1" prefix="$2" deadline=$((SECONDS + 60))
    while :; do
        kc -n gpu-oom-lab get pod "$pod" -o json > "$ROUND_DIR/$prefix-current.json"
        if python3 - "$ROUND_DIR/$prefix-current.json" <<'PY'
import json, sys
pod = json.load(open(sys.argv[1]))
state = next((c.get('state', {}).get('terminated') for c in pod.get('status', {}).get('containerStatuses', []) if c['name'] == 'workload'), None)
raise SystemExit(0 if state else 1)
PY
        then return 0; fi
        (( SECONDS < deadline )) || die "timeout waiting for termination: $pod"
        sleep 1
    done
}

modes=(exit hold)
[[ "$MODE" == both ]] || modes=("$MODE")
for mode in "${modes[@]}"; do
    for ((repeat=1; repeat<=ROUNDS; repeat++)); do
        ROUND_DIR="$RUN_OUT/$mode-r$repeat"
        mkdir -p "$ROUND_DIR"
        export OUT_DIR="$ROUND_DIR"
        python3 "$HELPER" init "$ROUND_DIR" "$mode" "$repeat" "$RUN_OUT/target.json"
        OUT_DIR="$ROUND_DIR/idle" bash "$LAB_DIR/../common/gpu-idle-check.sh" "$GPU_NODE"
        driver="$(driver_pod "$GPU_NODE")"
        kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- \
            nvidia-smi --query-gpu=uuid,compute_mode --format=csv,noheader,nounits > "$ROUND_DIR/idle-mode.csv"
        kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- \
            nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits > "$ROUND_DIR/idle-processes.csv"
        python3 - "$ROUND_DIR/idle-mode.csv" "$ROUND_DIR/idle-processes.csv" "$RUN_OUT/target.json" <<'PY'
import csv, json, sys
from pathlib import Path
rows = list(csv.reader(Path(sys.argv[1]).read_text().splitlines()))
expected = json.load(open(sys.argv[3]))['gpu_uuid']
if len(rows) != 1 or [v.strip() for v in rows[0]] != [expected, 'Default'] or Path(sys.argv[2]).read_text().strip():
    raise SystemExit('requires the expected A40 in Default compute mode and no CUDA processes')
PY
        kc apply -f "$RUN_OUT/base.json"
        kc apply -f "$RUN_OUT/claim-manifest.json"
        kc apply -f "$RUN_OUT/steady-service-manifest.json"
        pod_a="$(wait_pod steady-service a)"
        wait_event "$pod_a" a A_READY 600
        python3 "$HELPER" phase "$ROUND_DIR" baseline
        python3 "$HELPER" window "$ROUND_DIR" baseline start
        sleep 60
        python3 "$HELPER" window "$ROUND_DIR" baseline end
        control "$pod_a" a growth false false false
        python3 "$HELPER" phase "$ROUND_DIR" probes_drained
        bash "$SCRIPT_DIR/sample.sh" start "$GPU_NODE" "$ROUND_DIR/gpu.jsonl"
        cp "$RUN_OUT/greedy-batch-manifest.json" "$ROUND_DIR/greedy-batch-manifest.json"
        python3 "$HELPER" mode "$ROUND_DIR/greedy-batch-manifest.json" "$mode"
        kc apply -f "$ROUND_DIR/greedy-batch-manifest.json"
        pod_b="$(wait_pod greedy-batch b)"
        wait_event "$pod_b" b B_READY 300
        kc -n gpu-oom-lab get resourceclaim a40-shared -o json > "$ROUND_DIR/claim.json"
        python3 "$HELPER" shared "$ROUND_DIR/claim.json" "$ROUND_DIR/a-A_READY.json" "$ROUND_DIR/b-B_READY.json" \
            "$RUN_OUT/target.json" "$ROUND_DIR/a-created.json" "$ROUND_DIR/b-created.json"
        python3 "$HELPER" phase "$ROUND_DIR" growth
        control "$pod_b" b growth false true false
        wait_event "$pod_b" b B_OOM 150
        python3 "$HELPER" phase "$ROUND_DIR" b_oom_observed
        if [[ "$mode" == hold ]]; then
            wait_event "$pod_b" b B_HOLD 10
            control "$pod_a" a hold true false false
            python3 "$HELPER" phase "$ROUND_DIR" hold
        fi
        wait_terminated "$pod_b" b
        control "$pod_a" a recovery true false false
        python3 "$HELPER" phase "$ROUND_DIR" recovery
        python3 "$HELPER" window "$ROUND_DIR" recovery start
        sleep 60
        python3 "$HELPER" window "$ROUND_DIR" recovery end
        control "$pod_a" a stop false false true
        wait_terminated "$pod_a" a
        python3 - "$ROUND_DIR/gpu.jsonl.pid" <<'PY'
import json, sys, time
record = json.load(open(sys.argv[1]))
if record['state'] != 'ready' or time.time() - record['heartbeat'] > 3:
    raise SystemExit('GPU sampler failed or lost its heartbeat')
PY
        bash "$SCRIPT_DIR/sample.sh" stop "$GPU_NODE" "$ROUND_DIR/gpu.jsonl"
        python3 "$HELPER" complete "$ROUND_DIR"
        bash "$SCRIPT_DIR/collect.sh" "$RUN_ID"
        python3 "$LAB_DIR/analysis/analyze.py" "$RUN_OUT"
        kc -n gpu-oom-lab delete jobs -l "$SELECTOR" --cascade=background --ignore-not-found --wait=true --timeout=60s
        deadline=$((SECONDS + 60))
        while :; do
            remaining="$(kc -n gpu-oom-lab get pods -l "$SELECTOR" -o name)"
            [[ -n "$remaining" ]] || break
            (( SECONDS < deadline )) || die 'timeout waiting for Job Pods to disappear'
            sleep 1
        done
        kc -n gpu-oom-lab delete resourceclaims.resource.k8s.io -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=shared-gpu" --ignore-not-found --wait=true --timeout=60s
        printf '{"cleaned": true}\n' > "$ROUND_DIR/cleaned.json"
        ROUND_DIR=''
    done
done
export OUT_DIR="$RUN_OUT"
python3 "$LAB_DIR/analysis/analyze.py" "$RUN_OUT"
printf '{"complete": true}\n' > "$RUN_OUT/completed.json"
