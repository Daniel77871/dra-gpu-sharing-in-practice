#!/usr/bin/env bash
# Remote actions use only kubectl. Python helpers read/write local evidence.
set -euo pipefail
ISOLATION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ISOLATION_DIR/../common/lib.sh"
readonly ISOLATION_NS='gpu-isolation-lab'
readonly HELPER="$ISOLATION_DIR/common/analysis/control.py"
readonly ANALYZER="$ISOLATION_DIR/common/analysis/analyze.py"
readonly SAMPLE="$ISOLATION_DIR/common/scripts/sample.sh"
readonly WORKLOAD_SELECTOR="$BOOK_SELECTOR,$COMPONENT_LABEL_KEY in (steady-service,quota-pressure,capacity-resident,capacity-release,capacity-waiter)"
readonly CLAIM_SELECTOR="$BOOK_SELECTOR,$COMPONENT_LABEL_KEY in (shared-mps,steady-gpu,pressure-gpu,capacity-resident-gpu,capacity-release-gpu,capacity-waiter-gpu)"
GPU_NODE='dra-a40'
require_cmd python3

helper() { python3 "$HELPER" "$@"; }
update_trial() { helper update "$ROUND_DIR" "$1"; }
phase() { helper phase "$ROUND_DIR" "$1"; log "$ROUND_DIR: $1"; }

preflight() {
    kc get namespace "$ISOLATION_NS" --ignore-not-found -o json > "$RUN_OUT/namespace-before.json"
    if [[ -s "$RUN_OUT/namespace-before.json" ]]; then
        kc -n "$ISOLATION_NS" get configmaps,resourceclaims.resource.k8s.io,jobs,pods -o json > "$RUN_OUT/objects-before.json"
    else printf '{"items":[]}\n' > "$RUN_OUT/objects-before.json"; fi
    helper ownership "$RUN_OUT/namespace-before.json" "$RUN_OUT/objects-before.json"
    kc get resourceslices.resource.k8s.io -o json > "$RUN_OUT/slices.json"
    kc get deviceclass gpu.nvidia.com -o json > "$RUN_OUT/deviceclass.json"
    helper target "$RUN_OUT/slices.json" "$BACKEND" "$RUN_OUT/deviceclass.json" > "$RUN_OUT/target.json"
    kc version -o json > "$RUN_OUT/version.json"
}

render() {
    kc kustomize "$1" --load-restrictor=LoadRestrictionsNone > "$ROUND_DIR/manifests.yaml"
    kc create --dry-run=client --validate=false -f "$ROUND_DIR/manifests.yaml" -o json > "$ROUND_DIR/manifests.json"
    helper split "$ROUND_DIR/manifests.json" "$ROUND_DIR" "$RUN_ID"
}

wait_job_pod() {
    local job="$1" prefix="$2" pod deadline=$((SECONDS + 300)) status
    while :; do
        kc -n "$ISOLATION_NS" get pods -l "$BOOK_SELECTOR,job-name=$job" -o json > "$ROUND_DIR/$prefix-pods.json" || return $?
        if pod="$(helper pod "$ROUND_DIR/$prefix-pods.json")"; then
            kc -n "$ISOLATION_NS" get pod "$pod" -o json > "$ROUND_DIR/$prefix-created.json" || return $?
            printf '%s\n' "$pod" > "$ROUND_DIR/$prefix-pod-name.txt" || return $?
            printf '%s\n' "$pod"; return 0
        else status=$?; (( status == 1 )) || return "$status"; fi
        (( SECONDS < deadline )) || die "Job did not create exactly one Pod: $job"
        sleep 1
    done
}

fetch_logs() {
    local pod="$1" prefix="$2"
    kc -n "$ISOLATION_NS" get pod "$pod" -o json > "$ROUND_DIR/$prefix-current.json" || return $?
    helper same-pod "$ROUND_DIR/$prefix-created.json" "$ROUND_DIR/$prefix-current.json" || return $?
    if helper logs-ready "$ROUND_DIR/$prefix-current.json"; then
        kc -n "$ISOLATION_NS" logs "$pod" -c workload > "$ROUND_DIR/$prefix-log.tmp" || return $?
        mv "$ROUND_DIR/$prefix-log.tmp" "$ROUND_DIR/task-$prefix.log" || return $?
        return 0
    fi
    return 1
}

wait_event() {
    local pod="$1" prefix="$2" event="$3" nonce="${4:-}" deadline=$((SECONDS + 300)) status
    while :; do
        if fetch_logs "$pod" "$prefix"; then
            if [[ -n "$nonce" ]]; then
                if helper event "$ROUND_DIR/task-$prefix.log" "$event" "$nonce" > "$ROUND_DIR/$prefix-$event.json"; then return 0; fi
            elif helper event "$ROUND_DIR/task-$prefix.log" "$event" > "$ROUND_DIR/$prefix-$event.json"; then return 0; fi
        else status=$?; (( status == 1 )) || return "$status"; fi
        helper alive "$ROUND_DIR/$prefix-current.json" || die "Pod exited before $event"
        (( SECONDS < deadline )) || die "timeout waiting for $event"
        sleep 1
    done
}

control() {
    local pod="$1" prefix="$2" name="$3" probe="$4" start="$5" stop="$6" command nonce
    command="$(helper command "$name" "$probe" "$start" "$stop")"
    nonce="$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["command_id"])' "$command")"
    kc -n "$ISOLATION_NS" get pod "$pod" -o json > "$ROUND_DIR/$prefix-control.json"
    helper same-pod "$ROUND_DIR/$prefix-created.json" "$ROUND_DIR/$prefix-control.json"
    kc -n "$ISOLATION_NS" exec "$pod" -c workload -- python -c \
        'import os,sys;from pathlib import Path;p=Path("/tmp/isolation-control.tmp");p.write_text(sys.argv[1]);os.replace(str(p),"/tmp/isolation-control.json")' "$command"
    if [[ "$prefix" == a ]]; then wait_event "$pod" a CONTROL_ACK "$nonce"; fi
}

wait_exit() {
    local pod="$1" prefix="$2" deadline=$((SECONDS + 120)) status action=terminated
    [[ "${3:-0}" == 1 ]] && action=finished
    while :; do
        kc -n "$ISOLATION_NS" get pod "$pod" -o json > "$ROUND_DIR/$prefix-current.json"
        helper same-pod "$ROUND_DIR/$prefix-created.json" "$ROUND_DIR/$prefix-current.json"
        if helper "$action" "$ROUND_DIR/$prefix-current.json" > "$ROUND_DIR/$prefix-termination.json"; then return 0
        else status=$?; (( status == 1 )) || die 'unexpected container exit'; fi
        (( SECONDS < deadline )) || die "timeout waiting for normal termination: $pod"
        sleep 1
    done
}

wait_pressure() {
    local pod="$1" count="$2" deadline=$((SECONDS + 150)) status verdict
    while :; do
        if fetch_logs "$pod" b; then
            if verdict="$(helper pressure "$ROUND_DIR/task-b.log" "$count")"; then printf '%s\n' "$verdict"; return 0
            else status=$?; (( status == 1 )) || die 'B failed outside quota boundary'; fi
        else status=$?; (( status == 1 )) || return "$status"; fi
        helper alive "$ROUND_DIR/b-current.json" || die 'B exited without boundary evidence'
        (( SECONDS < deadline )) || die 'pressure observation timed out'
        sleep 0.5
    done
}

gpu_snapshot() {
    local label="$1" driver
    driver="$(driver_pod "$GPU_NODE")"
    kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- \
        nvidia-smi --query-gpu=uuid,name,memory.total,memory.used,memory.free,utilization.gpu,compute_mode \
        --format=csv,noheader,nounits > "$ROUND_DIR/gpu-$label.txt"
    kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- \
        nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits > "$ROUND_DIR/processes-$label.txt"
}

wait_gpu_clean() {
    local label="$1" deadline=$((SECONDS + 120)) status
    while :; do
        gpu_snapshot "$label"
        kc -n nvidia-dra-driver-gpu get pods,deployments -o json > "$ROUND_DIR/mps-$label.json"
        if helper gpu-idle "$ROUND_DIR/gpu-$label.txt" "$ROUND_DIR/processes-$label.txt" "$ROUND_DIR/mps-$label.json"; then return 0
        else status=$?; (( status == 1 )) || return "$status"; fi
        (( SECONDS < deadline )) || die 'GPU must be idle at at most 64 MiB in Default mode with no A40 MPS daemon'
        sleep 1
    done
}

mps_snapshot() {
    local quota="$1" count="$2" selection daemon container rs deploy server driver clients pid field
    kc -n nvidia-dra-driver-gpu get pods,deployments -o json > "$ROUND_DIR/mps-daemons.json"
    selection="$(python3 "$ISOLATION_DIR/common/analysis/mps.py" select "$ROUND_DIR")"
    IFS=$'\t' read -r daemon container rs <<< "$selection"
    kc -n nvidia-dra-driver-gpu get replicaset "$rs" -o json > "$ROUND_DIR/mps-replicaset.json"
    deploy="$(python3 -c 'import json,sys;print(next(r["name"] for r in json.load(open(sys.argv[1]))["metadata"]["ownerReferences"] if r["kind"]=="Deployment" and r.get("controller")))' "$ROUND_DIR/mps-replicaset.json")"
    kc -n nvidia-dra-driver-gpu get deployment "$deploy" -o json > "$ROUND_DIR/mps-deployment.json"
    mps_query() {
        printf '%s\n' "$1" | kc -n nvidia-dra-driver-gpu exec -i "$daemon" -c "$container" -- chroot /driver-root sh -c 'exec nvidia-cuda-mps-control'
    }
    mps_query get_server_list > "$ROUND_DIR/mps-server-list.txt"
    server="$(python3 "$ISOLATION_DIR/common/analysis/mps.py" server "$ROUND_DIR/mps-server-list.txt")"
    mps_query "get_default_device_pinned_mem_limit GPU-29ffee3e-bee4-e341-917a-fe7e43e977af" > "$ROUND_DIR/mps-default-memory.txt"
    mps_query "get_device_pinned_mem_limit $server GPU-29ffee3e-bee4-e341-917a-fe7e43e977af" > "$ROUND_DIR/mps-server-memory.txt"
    mps_query "get_active_thread_percentage $server" > "$ROUND_DIR/mps-active-thread.txt"
    mps_query get_default_active_thread_percentage > "$ROUND_DIR/mps-default-active-thread.txt"
    mps_query ps > "$ROUND_DIR/mps-ps.txt"
    mps_query "get_client_list $server" > "$ROUND_DIR/mps-client-list.txt"
    clients="$(python3 "$ISOLATION_DIR/common/analysis/mps.py" clients "$ROUND_DIR/mps-client-list.txt")"
    driver="$(driver_pod "$GPU_NODE")"
    for pid in $clients; do
        for field in status cgroup cmdline; do
            kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- cat "/proc/$pid/$field" > "$ROUND_DIR/client-$pid.$field.txt"
        done
    done
    kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- cat "/proc/$server/status" > "$ROUND_DIR/mps-server-status.txt"
    fetch_logs "$pod_a" a
    fetch_logs "$pod_b" b
    python3 "$ISOLATION_DIR/common/analysis/mps.py" verify "$ROUND_DIR" "$quota" "$count"
    update_trial '{"mps_verified":true}'
}

round_cleanup() {
    # Only called after evidence collection and normal CUDA client termination.
    kc -n "$ISOLATION_NS" delete jobs -l "$WORKLOAD_SELECTOR,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true --timeout=120s
    wait_pods_absent "$WORKLOAD_SELECTOR,app.kubernetes.io/instance=$RUN_ID"
    kc -n "$ISOLATION_NS" delete resourceclaims.resource.k8s.io -l "$CLAIM_SELECTOR,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true --timeout=120s
    wait_gpu_clean after
    OUT_DIR="$ROUND_DIR/idle-after" bash "$ISOLATION_DIR/../common/gpu-idle-check.sh" "$GPU_NODE"
    printf '{"verified":true}\n' > "$ROUND_DIR/cleanup.json"
    update_trial '{"cleanup":{"verified":true},"valid":true,"complete":true}'
    printf '{"cleaned":true}\n' > "$ROUND_DIR/cleaned.json"
}

wait_pods_absent() {
    local selector="$1" deadline=$((SECONDS + 120)) remaining
    while :; do
        remaining="$(kc -n "$ISOLATION_NS" get pods -l "$selector" -o name)"
        [[ -n "$remaining" ]] || return 0
        (( SECONDS < deadline )) || die 'Job Pods did not disappear'
        sleep 1
    done
}

runtime_round() {
    local manifests="$1" case_name="$2" quota="$3" repeat="$4" count=1 pod_a pod_b verdict
    [[ "$case_name" == scope ]] && count=2
    update_trial "{\"backend\":\"$BACKEND\",\"case\":\"$case_name\",\"quota_gib\":$quota,\"repeat\":$repeat,\"valid\":false}"
    OUT_DIR="$ROUND_DIR/idle-before" bash "$ISOLATION_DIR/../common/gpu-idle-check.sh" "$GPU_NODE"
    wait_gpu_clean before
    render "$manifests"
    kc apply -f "$ROUND_DIR/base.json"
    kc apply -f "$ROUND_DIR/claims.json"
    kc apply -f "$ROUND_DIR/steady-service.json"
    pod_a="$(wait_job_pod steady-service a)"
    wait_event "$pod_a" a A_READY
    bash "$SAMPLE" start "$GPU_NODE" "$ROUND_DIR/gpu.csv"
    kc apply -f "$ROUND_DIR/quota-pressure.json"
    pod_b="$(wait_job_pod quota-pressure b)"
    wait_event "$pod_b" b B_READY
    kc -n "$ISOLATION_NS" get resourceclaims -l "$CLAIM_SELECTOR" -o json > "$ROUND_DIR/claims-allocated.json"
    helper allocations "$ROUND_DIR/claims-allocated.json" "$BACKEND" "$ROUND_DIR/a-current.json" "$ROUND_DIR/b-current.json" "$ROUND_DIR/a-A_READY.json" "$ROUND_DIR/b-B_READY.json"
    gpu_snapshot before
    control "$pod_a" a baseline true false false
    phase baseline; helper window "$ROUND_DIR" baseline start
    sleep 60
    helper window "$ROUND_DIR" baseline end
    control "$pod_a" a pressure true false false
    control "$pod_b" b pressure false true false
    phase pressure
    verdict="$(wait_pressure "$pod_b" "$count")"
    update_trial "{\"pressure_observed\":\"$verdict\"}"
    gpu_snapshot during
    if [[ "$BACKEND" == mps ]]; then
        mps_snapshot "$quota" "$count"
    fi
    wait_exit "$pod_b" b
    python3 - "$ROUND_DIR" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1]); meta=json.loads((p/'trial.json').read_text())
meta['b_actual_exit_utc']=json.loads((p/'b-termination.json').read_text())['finishedAt']
(p/'trial.json').write_text(json.dumps(meta,indent=2)+'\n')
PY
    control "$pod_a" a recovery true false false
    python3 - "$ROUND_DIR" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1]); meta=json.loads((p/'trial.json').read_text())
meta['recovery_start_utc']=json.loads((p/'a-CONTROL_ACK.json').read_text())['utc']
(p/'trial.json').write_text(json.dumps(meta,indent=2)+'\n')
PY
    phase recovery; helper window "$ROUND_DIR" recovery start
    sleep 60
    helper window "$ROUND_DIR" recovery end
    control "$pod_a" a stop false false true
    wait_exit "$pod_a" a
    bash "$ISOLATION_DIR/common/scripts/collect.sh" "$RUN_ID"
    python3 - "$ROUND_DIR/gpu.csv.pid" <<'PY'
import json,sys,time
r=json.load(open(sys.argv[1]))
if r['state']!='ready' or time.time()-r['heartbeat']>3: raise SystemExit('GPU sampler failed')
PY
    bash "$SAMPLE" stop "$GPU_NODE" "$ROUND_DIR/gpu.csv"
    round_cleanup
    helper check-summary "$ROUND_DIR"
}
