#!/usr/bin/env bash
set -euo pipefail
HAMI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HAMI_DIR/../common/scripts/lib.sh"
HAMI_HELPER="$HAMI_DIR/analysis/control.py"
HAMI_ANALYZER="$HAMI_DIR/analysis/analyze.py"
HAMI_DEVICE_CLASS='hami-core-gpu.project-hami.io'
HAMI_HOST_IMAGE='docker.io/pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755'
HAMI_SCOPE_SELECTOR="$BOOK_SELECTOR,$COMPONENT_LABEL_KEY in (container-scope-pressure,scope-primary,scope-secondary)"
HAMI_SCOPE_CLAIMS="$BOOK_SELECTOR,$COMPONENT_LABEL_KEY in (container-scope-gpu,pod-shared-gpu,worker-primary-gpu,worker-secondary-gpu)"
helper() { python3 "$HAMI_HELPER" "$@"; }

open_run() {
    local chosen="$1" selected="${OUT_DIR:-}"
    init_run "$ISOLATION_DIR" "$chosen"
    if [[ -n "$selected" ]]; then
        mkdir -p "$selected"
        OUT_DIR="$(cd "$selected" && pwd)"
        export OUT_DIR
    fi
    RUN_OUT="$OUT_DIR"
    python3 - "$RUN_ID" <<'PY'
import re,sys
if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?',sys.argv[1]):
    raise SystemExit('run-id must fit a Kubernetes instance label')
PY
}

preflight() {
    kc get namespace "$ISOLATION_NS" --ignore-not-found -o json > "$RUN_OUT/namespace-before.json"
    if [[ -s "$RUN_OUT/namespace-before.json" ]]; then
        kc -n "$ISOLATION_NS" get configmaps,resourceclaims,jobs,pods -o json > "$RUN_OUT/objects-before.json"
    else printf '{"items":[]}\n' > "$RUN_OUT/objects-before.json"; fi
    helper ownership "$RUN_OUT/namespace-before.json" "$RUN_OUT/objects-before.json"
    kc get resourceslices -o json > "$RUN_OUT/slices.json"
    kc get deviceclass "$HAMI_DEVICE_CLASS" -o json > "$RUN_OUT/deviceclass.json"
    helper target "$RUN_OUT/slices.json" hami "$RUN_OUT/deviceclass.json" > "$RUN_OUT/target.json"
    kc version -o json > "$RUN_OUT/version.json"
}

render() {
    kc kustomize "$1" --load-restrictor=LoadRestrictionsNone > "$ROUND_DIR/manifests.yaml"
    kc create --dry-run=client --validate=false -f "$ROUND_DIR/manifests.yaml" -o json > "$ROUND_DIR/manifests.json"
    helper render "$ROUND_DIR/manifests.json" "$ROUND_DIR" "$RUN_ID" "${REPEAT:-1}"
    kc get resourceslices -o json > "$ROUND_DIR/resourceslices.json"
}

host_start() {
    local name status
    export OUT_DIR="$RUN_OUT"
    if [[ -f "$RUN_OUT/host-helper-created.json" ]]; then
        name="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["metadata"]["name"])' "$RUN_OUT/host-helper-created.json")"
        kc -n nvidia-dra-driver-gpu get pod "$name" --ignore-not-found -o json > "$RUN_OUT/host-helper-current.json"
        if [[ -s "$RUN_OUT/host-helper-current.json" ]]; then
            helper same-pod "$RUN_OUT/host-helper-created.json" "$RUN_OUT/host-helper-current.json"
            HOST_POD="$name"; return
        fi
    fi
    kc get node "$GPU_NODE" -o json > "$RUN_OUT/host-node.json"
    kc -n nvidia-dra-driver-gpu get pods --field-selector "spec.nodeName=$GPU_NODE" -o json > "$RUN_OUT/host-pods-before.json"
    kc -n nvidia-dra-driver-gpu debug "node/$GPU_NODE" --profile=sysadmin --image="$HAMI_HOST_IMAGE" \
        --image-pull-policy=IfNotPresent --attach=false --container=hami-host-helper \
        --env=NVIDIA_VISIBLE_DEVICES=void -- bash -c 'sleep 3600' > "$RUN_OUT/host-debug.txt"
    kc -n nvidia-dra-driver-gpu get pods --field-selector "spec.nodeName=$GPU_NODE" -o json > "$RUN_OUT/host-pods-after.json"
    name="$(python3 - "$RUN_OUT" "$HAMI_HOST_IMAGE" "$RUN_ID" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);before={x['metadata']['uid'] for x in json.load(open(p/'host-pods-before.json'))['items']}
found=[x for x in json.load(open(p/'host-pods-after.json'))['items'] if x['metadata']['uid'] not in before
       and len(x['spec']['containers'])==1 and x['spec']['containers'][0]['name']=='hami-host-helper'
       and x['spec']['containers'][0]['image']==sys.argv[2]]
if len(found)!=1: raise SystemExit('cannot identify exactly one new pinned node debug Pod')
pod=found[0]
if pod['spec'].get('nodeName')!='dra-a40' or not pod['spec']['containers'][0].get('securityContext',{}).get('privileged'):
    raise SystemExit('debug node/security identity differs')
if not any(v.get('hostPath',{}).get('path')=='/' for v in pod['spec']['volumes']): raise SystemExit('debug host root is not mounted')
labels=dict(pod['metadata'].get('labels',{}));labels.update({'app.kubernetes.io/part-of':'dra-book','app.kubernetes.io/component':'hami-host-helper','app.kubernetes.io/instance':sys.argv[3]})
patch=[{'op':'test','path':'/metadata/uid','value':pod['metadata']['uid']},
       {'op':'test','path':'/spec','value':pod['spec']},{'op':'add','path':'/metadata/labels','value':labels}]
(p/'host-helper-patch.json').write_text(json.dumps(patch))
print(pod['metadata']['name'])
PY
)"
    kc -n nvidia-dra-driver-gpu patch pod "$name" --type=json --patch-file "$RUN_OUT/host-helper-patch.json" -o json > "$RUN_OUT/host-helper-created.json"
    kc -n nvidia-dra-driver-gpu wait pod "$name" --for=condition=Ready --timeout=300s
    HOST_POD="$name"
}

host_call() {
    local payload="$1" response status
    kc -n nvidia-dra-driver-gpu get pod "$HOST_POD" -o json > "$RUN_OUT/host-helper-current.json" || return $?
    helper same-pod "$RUN_OUT/host-helper-created.json" "$RUN_OUT/host-helper-current.json" || return $?
    if response="$(kc -n nvidia-dra-driver-gpu exec "$HOST_POD" -c hami-host-helper -- \
        python -c "$(cat "$HAMI_DIR/scripts/host.py")" "$payload")"; then :
    else
        status=$?
        printf 'HAMi host operation failed with exit %s; see the helper error above.\n' "$status" >&2
        return "$status"
    fi
    [[ -n "$response" ]] || { printf 'HAMi host operation returned empty stdout.\n' >&2; return 1; }
    printf '%s\n' "$response" | python3 -c 'import json,sys
try: value=json.load(sys.stdin)["result"]
except (ValueError,KeyError,TypeError) as error:
    print("Invalid HAMi host response: " + str(error),file=sys.stderr);raise SystemExit(1)
print(json.dumps(value))'
}

host_stop() {
    [[ -n "${HOST_POD:-}" ]] || return 0
    kc -n nvidia-dra-driver-gpu get pod "$HOST_POD" --ignore-not-found -o json > "$RUN_OUT/host-helper-current.json" || return $?
    if [[ -s "$RUN_OUT/host-helper-current.json" ]]; then
        helper same-pod "$RUN_OUT/host-helper-created.json" "$RUN_OUT/host-helper-current.json" || return $?
        kc -n nvidia-dra-driver-gpu delete pods -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=hami-host-helper,app.kubernetes.io/instance=$RUN_ID" --wait=true --timeout=120s || return $?
    fi
    HOST_POD=''
}

host_footprint() { host_call '{"operation":"footprint"}'; }

wait_claim_artifacts_absent() {
    local claims="$1" deadline=$((SECONDS + 120))
    while :; do
        host_footprint > "$ROUND_DIR/host-after-unprepare.json"
        if python3 - "$claims" "$ROUND_DIR/host-after-unprepare.json" <<'PY'
import json,sys
items=json.load(open(sys.argv[1]))['items'];uids={c['metadata']['uid'] for c in items if c.get('kind','ResourceClaim')=='ResourceClaim'}
footprint=json.load(open(sys.argv[2]))
raise SystemExit(1 if any(uid in row['path'] or uid in row.get('content','') for rows in footprint.values() for row in rows for uid in uids) else 0)
PY
        then return; fi
        (( SECONDS < deadline )) || die 'Claim cache/CDI/checkpoint residue remains'
        sleep 1
    done
}

process_snapshot() {
    local label="$1" driver payload
    driver="$(driver_pod "$GPU_NODE")"
    kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- \
        nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits > "$ROUND_DIR/processes-$label.txt"
    payload="$(python3 - "$ROUND_DIR/processes-$label.txt" <<'PY'
import csv,json,sys
print(json.dumps({'operation':'proc','pids':[int(row[0]) for row in csv.reader(open(sys.argv[1])) if row]}))
PY
)"
    host_call "$payload" > "$ROUND_DIR/proc-fields.json"
    cp "$ROUND_DIR/proc-fields.json" "$ROUND_DIR/client-processes.json"
}

runtime_hooks() {
    kc -n "$ISOLATION_NS" exec "$pod_a" -c workload -- python -c "$(cat "$HAMI_DIR/workload/runtime_hooks.py")" > "$ROUND_DIR/a-hooks.json"
    kc -n "$ISOLATION_NS" exec "$pod_b" -c workload -- python -c "$(cat "$HAMI_DIR/workload/runtime_hooks.py")" > "$ROUND_DIR/b-hooks.json"
    python3 - "$ROUND_DIR" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);result={name:json.load(open(p/(prefix+'-hooks.json'))) for prefix,name in [('a','steady-service'),('b','quota-pressure')]}
(p/'runtime-hooks.json').write_text(json.dumps(result,indent=2)+'\n')
PY
}

control() {
    local pod="$1" prefix="$2" name="$3" probe="$4" start="$5" stop="$6" grow="${7:-false}" command nonce
    command="$(helper command "$name" "$probe" "$start" "$stop")"
    command="$(python3 -c 'import json,sys;c=json.loads(sys.argv[1]);c["scope_grow"]=sys.argv[2]=="true";print(json.dumps(c))' "$command" "$grow")"
    nonce="$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["command_id"])' "$command")"
    kc -n "$ISOLATION_NS" get pod "$pod" -o json > "$ROUND_DIR/$prefix-control.json"
    helper same-pod "$ROUND_DIR/$prefix-created.json" "$ROUND_DIR/$prefix-control.json"
    kc -n "$ISOLATION_NS" exec "$pod" -c workload -- python -c \
        'import os,sys;from pathlib import Path;p=Path("/tmp/isolation-control.tmp");p.write_text(sys.argv[1]);os.replace(str(p),"/tmp/isolation-control.json")' "$command"
    if [[ "$prefix" == a ]]; then wait_event "$pod" a CONTROL_ACK "$nonce"; fi
}

round_cleanup() {
    kc -n "$ISOLATION_NS" get jobs,pods,resourceclaims,configmaps -l "$BOOK_SELECTOR" -o json > "$ROUND_DIR/objects-before-cleanup.json"
    helper cleanup-owner "$ROUND_DIR/objects-before-cleanup.json" "$RUN_ID"
    kc -n "$ISOLATION_NS" delete jobs -l "$WORKLOAD_SELECTOR,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true --timeout=120s
    wait_pods_absent "$WORKLOAD_SELECTOR,app.kubernetes.io/instance=$RUN_ID"
    kc -n "$ISOLATION_NS" delete resourceclaims -l "$CLAIM_SELECTOR,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true --timeout=120s
    wait_gpu_clean after
    OUT_DIR="$ROUND_DIR/idle-after" bash "$ISOLATION_DIR/../common/gpu-idle-check.sh" "$GPU_NODE"
    wait_claim_artifacts_absent "$ROUND_DIR/objects-before-cleanup.json"
    printf '{"verified":true}\n' > "$ROUND_DIR/cleanup.json"
    update_trial '{"cleanup":{"verified":true},"valid":true,"complete":true}'
    printf '{"cleaned":true}\n' > "$ROUND_DIR/cleaned.json"
}

runtime_round() {
    local manifests="$1" case_name="$2" quota=8 count=1 pod_a pod_b verdict deadline
    [[ "$case_name" == scope ]] && count=2
    update_trial "{\"backend\":\"hami\",\"case\":\"$case_name\",\"quota_gib\":8,\"repeat\":$REPEAT,\"valid\":false}"
    OUT_DIR="$ROUND_DIR/idle-before" bash "$ISOLATION_DIR/../common/gpu-idle-check.sh" "$GPU_NODE"
    wait_gpu_clean before
    render "$manifests"
    kc apply -f "$ROUND_DIR/base.json"
    kc apply -f "$ROUND_DIR/claims.json"
    kc apply -f "$ROUND_DIR/steady-service.json"
    pod_a="$(wait_job_pod steady-service a)"; wait_event "$pod_a" a A_READY
    bash "$SAMPLE" start "$GPU_NODE" "$ROUND_DIR/gpu.csv"
    kc apply -f "$ROUND_DIR/quota-pressure.json"
    pod_b="$(wait_job_pod quota-pressure b)"; wait_event "$pod_b" b B_READY
    kc -n "$ISOLATION_NS" get resourceclaims -l "$CLAIM_SELECTOR,app.kubernetes.io/instance=$RUN_ID" -o json > "$ROUND_DIR/claims-allocated.json"
    helper allocations "$ROUND_DIR/claims-allocated.json" hami "$ROUND_DIR/a-current.json" "$ROUND_DIR/b-current.json" "$ROUND_DIR/a-A_READY.json" "$ROUND_DIR/b-B_READY.json"
    if [[ "$case_name" == boundary ]]; then
        runtime_hooks
        process_snapshot mapping; helper runtime-identity "$ROUND_DIR"
    fi
    control "$pod_a" a baseline true false false
    phase baseline; helper window "$ROUND_DIR" baseline start
    sleep 60
    helper window "$ROUND_DIR" baseline end
    control "$pod_a" a pressure true false false
    control "$pod_b" b pressure false true false
    phase pressure
    if [[ "$case_name" == scope ]]; then
        deadline=$((SECONDS + 120))
        while :; do
            fetch_logs "$pod_b" b
            if python3 - "$ROUND_DIR/task-b.log" <<'PY'
import json,sys
rows=[]
for line in open(sys.argv[1]):
    try: rows.append(json.loads(line))
    except ValueError: pass
raise SystemExit(0 if {r.get('client_id') for r in rows if r.get('event')=='B_READY' and r.get('gpu_uuid')}=={'0','1'} else 1)
PY
            then break; fi
            helper alive "$ROUND_DIR/b-current.json"
            (( SECONDS < deadline )) || die 'dual CUDA identity barrier timeout'
            sleep 1
        done
        runtime_hooks
        process_snapshot mapping; helper runtime-identity "$ROUND_DIR"
        control "$pod_b" b pressure false true false true
    fi
    verdict="$(wait_pressure "$pod_b" "$count")"
    update_trial "{\"pressure_observed\":\"$verdict\"}"
    gpu_snapshot during
    wait_exit "$pod_b" b
    python3 - "$ROUND_DIR" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);meta=json.load(open(p/'trial.json'));meta['b_actual_exit_utc']=json.load(open(p/'b-termination.json'))['finishedAt'];(p/'trial.json').write_text(json.dumps(meta,indent=2)+'\n')
PY
    control "$pod_a" a recovery true false false
    python3 - "$ROUND_DIR" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);meta=json.load(open(p/'trial.json'));meta['recovery_start_utc']=json.load(open(p/'a-CONTROL_ACK.json'))['utc'];(p/'trial.json').write_text(json.dumps(meta,indent=2)+'\n')
PY
    phase recovery; helper window "$ROUND_DIR" recovery start
    sleep 60
    helper window "$ROUND_DIR" recovery end
    control "$pod_a" a stop false false true
    wait_exit "$pod_a" a
    OUT_DIR="$RUN_OUT" bash "$HAMI_DIR/scripts/collect.sh" "$RUN_ID"
    bash "$SAMPLE" stop "$GPU_NODE" "$ROUND_DIR/gpu.csv"
    round_cleanup
    helper check-summary "$ROUND_DIR"
}

scope_refresh() {
    local prefix pod
    for prefix in scope-primary scope-secondary container-scope-pressure; do
        [[ -s "$ROUND_DIR/$prefix-pod-name.txt" ]] || continue
        pod="$(cat "$ROUND_DIR/$prefix-pod-name.txt")"
        kc -n "$ISOLATION_NS" get pod "$pod" -o json > "$ROUND_DIR/$prefix-current.json" || return $?
        helper same-pod "$ROUND_DIR/$prefix-created.json" "$ROUND_DIR/$prefix-current.json" || return $?
    done
    python3 - "$ROUND_DIR" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);pods=[json.load(open(p/(name+'-current.json'))) for name in ('scope-primary','scope-secondary','container-scope-pressure') if (p/(name+'-pod-name.txt')).exists()]
(p/'pods-current.json').write_text(json.dumps({'items':pods},indent=2)+'\n')
PY
}

scope_inventory() {
    kc -n "$ISOLATION_NS" get jobs,pods -o json > "$ROUND_DIR/scope-objects-current.json"
}

scope_launch_job() {
    local job="$1"
    helper scope-launch "$ROUND_DIR" "$job" || return $?
    kc apply -f "$ROUND_DIR/$job.json" || return $?
    kc -n "$ISOLATION_NS" get job "$job" -o json > "$ROUND_DIR/$job-job-created.json.tmp" || return $?
    mv "$ROUND_DIR/$job-job-created.json.tmp" "$ROUND_DIR/$job-job-created.json" || return $?
    helper scope-launch "$ROUND_DIR" "$job" "$ROUND_DIR/$job-job-created.json"
}

scope_read() {
    local payload
    payload="$(python3 - "$ROUND_DIR" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);meta=json.load(open(p/'trial.json'));print(json.dumps({'operation':'read','root':meta['control_root'],'owner':json.load(open(p/'control-owner.json'))}))
PY
)"
    host_call "$payload" > "$ROUND_DIR/control-latest.json"
}

scope_receive() {
    local wanted="$1" ids="$2" output="$3" deadline=$((SECONDS + 120)) status
    while :; do
        scope_read
        if python3 - "$ROUND_DIR/control-latest.json" "$wanted" "$ids" > "$output.tmp" <<'PY'
import json,sys
value=json.load(open(sys.argv[1]));ids=sys.argv[3].split(',')
errors=[r['error.json'] for r in value.values() if 'error.json' in r]
if errors: print(json.dumps(errors),file=sys.stderr);raise SystemExit(2)
if not all(sys.argv[2] in value[cid] for cid in ids): raise SystemExit(1)
print(json.dumps([value[cid][sys.argv[2]] for cid in ids]))
PY
        then mv "$output.tmp" "$output"; return
        else status=$?; (( status == 1 )) || die 'scope CUDA client reported an error'; fi
        scope_refresh
        (( SECONDS < deadline )) || die "scope host control timeout waiting for $wanted"
        sleep 0.5
    done
}

scope_replay_command() {
    local payload
    payload="$(python3 - "$ROUND_DIR" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);meta=json.load(open(p/'trial.json'))
previous=json.load(open(p/'scope-previous-command.json')) if (p/'scope-previous-command.json').exists() else None
print(json.dumps({'operation':'replay','root':meta['control_root'],'owner':json.load(open(p/'control-owner.json')),
    'command':json.load(open(p/'scope-command.json')),'previous':previous}))
PY
)"
    host_call "$payload" > "$ROUND_DIR/scope-command-replayed.json"
}

scope_send() {
    local operation="$1" allocation_round="${2:--1}" abort="${3:-false}" payload pending saved_operation acknowledged
    if [[ -f "$ROUND_DIR/scope-command.json" ]]; then
        scope_replay_command
        pending="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["command"]["seq"])' "$ROUND_DIR/scope-command-replayed.json")"
        saved_operation="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["command"]["operation"])' "$ROUND_DIR/scope-command-replayed.json")"
        if [[ "$saved_operation" == stop ]]; then
            [[ "$operation" == stop ]] || die 'scope STOP was already issued; refusing more allocation commands'
            sequence="$pending"
            return
        fi
        acknowledged="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["acknowledged"])' "$ROUND_DIR/scope-command-replayed.json")"
        if [[ "$acknowledged" != True ]]; then
            scope_receive "ack-$pending.json" 0,1 "$ROUND_DIR/scope-recovery-acks.json"
            scope_replay_command
            python3 -c 'import json,sys;raise SystemExit(0 if json.load(open(sys.argv[1]))["acknowledged"] else 1)' "$ROUND_DIR/scope-command-replayed.json" || die 'scope pending acknowledgment barrier incomplete'
        fi
    fi
    scope_read
    payload="$(python3 - "$ROUND_DIR" "$operation" "$allocation_round" "$abort" <<'PY'
import json,sys,time
from pathlib import Path
p=Path(sys.argv[1]);meta=json.load(open(p/'trial.json'));latest=json.load(open(p/'control-latest.json'))
previous=json.load(open(p/'scope-command.json')) if (p/'scope-command.json').exists() else None
if set(latest)!={'0','1'} or any(row.get('command.json')!=previous for row in latest.values()):
    raise SystemExit('scope control differs from the replayed journal; preserving resources')
command={'run_id':meta['run_id'],'seq':previous['seq']+1 if previous else 1,'operation':sys.argv[2]}
if sys.argv[2]!='stop': command['round_id']=int(sys.argv[3])
if sys.argv[2]=='allocate': command['not_before_ns']=time.time_ns()+1_500_000_000
def save(name, value):
    path=p/name;temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value)+'\n');temporary.replace(path)
if previous: save('scope-previous-command.json',previous)
save('scope-command.json',command)
print(json.dumps({'operation':'send','root':meta['control_root'],'owner':json.load(open(p/'control-owner.json')),
    'command':command,'previous':previous,'abort':sys.argv[4]=='true'}))
PY
)"
    host_call "$payload" > "$ROUND_DIR/scope-command-sent.json"
    sequence="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["seq"])' "$ROUND_DIR/scope-command.json")"
}

scope_record_terminations() {
    local status
    if ! scope_refresh; then return 2; fi
    if ! scope_inventory; then return 2; fi
    if helper scope-terminations "$ROUND_DIR/pods-current.json" "$ROUND_DIR" > "$ROUND_DIR/terminations.tmp"; then
        mv "$ROUND_DIR/terminations.tmp" "$ROUND_DIR/terminations.json" || return $?
        cp "$ROUND_DIR/pods-current.json" "$ROUND_DIR/terminated-pods.json"
    else status=$?; return "$status"; fi
}

scope_wait_exit() {
    local deadline=$((SECONDS + 120)) status
    while :; do
        if scope_record_terminations; then return
        else status=$?; (( status == 1 )) || exit "$status"; fi
        (( SECONDS < deadline )) || die 'scope containers did not all terminate; no forced CUDA kill performed'
        sleep 1
    done
}

scope_cleanup_snapshot() {
    kc -n "$ISOLATION_NS" get jobs,pods,resourceclaims,configmaps -o json > "$ROUND_DIR/objects-before-cleanup.json"
    helper scope-cleanup-phase "$ROUND_DIR" "$ROUND_DIR/objects-before-cleanup.json"
}

scope_stop() {
    local status
    if [[ -f "$ROUND_DIR/scope-cleanup-started.json" ]]; then
        scope_cleanup_snapshot
        gpu_snapshot exited
        [[ ! -s "$ROUND_DIR/processes-exited.txt" ]] || die 'CUDA processes remain during scope cleanup retry'
        return
    elif scope_record_terminations; then :
    else
        status=$?; (( status == 1 )) || exit "$status"
        scope_send stop -1 true
        scope_wait_exit
    fi
    scope_read
    cp "$ROUND_DIR/control-latest.json" "$ROUND_DIR/control-final.json"
    gpu_snapshot exited
    [[ ! -s "$ROUND_DIR/processes-exited.txt" ]] || die 'CUDA processes remain after scope containers exited'
}

scope_cleanup_round() {
    local payload
    [[ -s "$ROUND_DIR/terminated-pods.json" ]] || die 'scope termination evidence missing; preserving Jobs and Claims'
    scope_cleanup_snapshot
    kc -n "$ISOLATION_NS" delete jobs -l "$HAMI_SCOPE_SELECTOR,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true --timeout=120s
    wait_pods_absent "$HAMI_SCOPE_SELECTOR,app.kubernetes.io/instance=$RUN_ID"
    kc -n "$ISOLATION_NS" delete resourceclaims -l "$HAMI_SCOPE_CLAIMS,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true --timeout=120s
    wait_claim_artifacts_absent "$ROUND_DIR/scope-cleanup-objects.json"
    payload="$(python3 - "$ROUND_DIR" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);print(json.dumps({'operation':'remove','root':json.load(open(p/'trial.json'))['control_root'],'owner':json.load(open(p/'control-owner.json')),'cuda_exited':True,'resume':True}))
PY
)"
    host_call "$payload" > "$ROUND_DIR/control-removed.json"
    wait_gpu_clean after
    OUT_DIR="$ROUND_DIR/idle-after" bash "$ISOLATION_DIR/../common/gpu-idle-check.sh" "$GPU_NODE"
    printf '{"verified":true,"all_cuda_containers_exited":true,"claims_cache_cdi_checkpoint_absent":true}\n' > "$ROUND_DIR/cleanup.json"
    update_trial '{"cleanup":{"verified":true},"valid":true,"complete":true}'
    printf '{"cleaned":true}\n' > "$ROUND_DIR/cleaned.json"
}

scope_round() {
    local case_name="$1" control_root pod payload sequence=0 verdict allocation_round job cid
    control_root="/var/tmp/dra-hami-$RUN_ID-$case_name-r$REPEAT"
    update_trial "{\"backend\":\"hami\",\"case\":\"$case_name\",\"repeat\":$REPEAT,\"quota_gib\":8,\"run_id\":\"$RUN_ID\",\"control_root\":\"$control_root\",\"target_bytes_per_client\":6442450944,\"step_bytes\":268435456,\"hold_seconds\":30,\"valid\":false}"
    helper scope-launch "$ROUND_DIR"
    wait_gpu_clean before
    OUT_DIR="$ROUND_DIR/idle-before" bash "$ISOLATION_DIR/../common/gpu-idle-check.sh" "$GPU_NODE"
    render "$HAMI_DIR/manifests/scope/$case_name"
    kc apply -f "$ROUND_DIR/base.json"
    kc apply -f "$ROUND_DIR/claims.json"
    kc -n "$ISOLATION_NS" get resourceclaims -l "$HAMI_SCOPE_CLAIMS,app.kubernetes.io/instance=$RUN_ID" -o json > "$ROUND_DIR/scope-claims-created.json.tmp"
    mv "$ROUND_DIR/scope-claims-created.json.tmp" "$ROUND_DIR/scope-claims-created.json"
    payload="$(python3 -c 'import json,sys;print(json.dumps({"operation":"create","root":sys.argv[1],"run_id":sys.argv[2]}))' "$control_root" "$RUN_ID")"
    host_call "$payload" > "$ROUND_DIR/control-owner.json"
    if [[ "$case_name" == case3 ]]; then
        scope_launch_job container-scope-pressure
        pod="$(wait_job_pod container-scope-pressure container-scope-pressure)"
        scope_receive ready.json 0,1 "$ROUND_DIR/ready.json"
    else
        scope_launch_job scope-primary
        pod="$(wait_job_pod scope-primary scope-primary)"
        scope_receive ready.json 0 "$ROUND_DIR/ready-primary.json"
        scope_launch_job scope-secondary
        pod="$(wait_job_pod scope-secondary scope-secondary)"
        scope_receive ready.json 0,1 "$ROUND_DIR/ready.json"
    fi
    scope_refresh
    kc -n "$ISOLATION_NS" get resourceclaims -l "$HAMI_SCOPE_CLAIMS,app.kubernetes.io/instance=$RUN_ID" -o json > "$ROUND_DIR/claims-allocated.json"
    process_snapshot mapping
    helper scope-identity "$ROUND_DIR"
    host_footprint > "$ROUND_DIR/host-prepared.json"
    bash "$SAMPLE" start "$GPU_NODE" "$ROUND_DIR/gpu.csv"
    for allocation_round in {0..23}; do
        scope_send allocate "$allocation_round"
        scope_receive "ack-$sequence.json" 0,1 "$ROUND_DIR/allocation-acks.json"
        scope_send snapshot "$allocation_round"
        scope_receive "ack-$sequence.json" 0,1 "$ROUND_DIR/snapshot-acks.json"
        python3 - "$ROUND_DIR/sample-time.json" <<'PY'
import datetime,json,sys
json.dump({'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()},open(sys.argv[1],'w'))
PY
        gpu_snapshot barrier
        verdict="$(helper scope-record "$ROUND_DIR" "$allocation_round")"
        [[ "$verdict" != finished ]] || break
    done
    [[ "$verdict" == finished ]] || die 'scope clients reached neither target nor allocation OOM in 24 rounds'
    update_trial "$(python3 -c 'import datetime,json;print(json.dumps({"hold_started_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}))')"
    deadline="$(python3 -c 'import time;print(time.monotonic()+30)')"
    while python3 -c 'import sys,time;raise SystemExit(0 if time.monotonic()<float(sys.argv[1]) else 1)' "$deadline"; do
        gpu_snapshot held
        python3 - "$ROUND_DIR" <<'PY'
import datetime,json,sys
from pathlib import Path
p=Path(sys.argv[1]);identity=json.load(open(p/'identity.json'));expected={r['host_pid'] for r in identity['clients']}
raw=(p/'processes-held.txt').read_text();pids={int(line.split(',')[0]) for line in raw.splitlines() if line.strip()}
if pids!=expected: raise SystemExit('scope hold CUDA PID set changed')
with (p/'hold-samples.jsonl').open('a') as f: f.write(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'gpu_raw':(p/'gpu-held.txt').read_text(),'processes_raw':raw})+'\n')
PY
        sleep 1
    done
    update_trial "$(python3 -c 'import datetime,json;print(json.dumps({"hold_finished_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}))')"
    scope_stop
    python3 - "$ROUND_DIR/terminations.json" <<'PY'
import json,sys
rows=json.load(open(sys.argv[1]))
if len(rows)!=2 or any(r['exitCode']!=0 or r.get('reason')=='OOMKilled' for r in rows): raise SystemExit('scope containers did not exit normally')
PY
    OUT_DIR="$RUN_OUT" bash "$HAMI_DIR/scripts/collect.sh" "$RUN_ID"
    bash "$SAMPLE" stop "$GPU_NODE" "$ROUND_DIR/gpu.csv"
    scope_cleanup_round
    helper check-summary "$ROUND_DIR"
}

# `kubectl get -f FILE --ignore-not-found -o json` prints nothing, not an empty List,
# when none of the objects exist. Normalize that to an empty List for the JSON readers.
kc_get_list() {
    local out="$1"; shift
    kc get "$@" --ignore-not-found -o json > "$out" || return
    [[ -s "$out" ]] || printf '{"apiVersion":"v1","kind":"List","items":[]}\n' > "$out"
}
