#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$LAB_DIR/../common/lib.sh"
require_cmd python3
[[ "${SMOKE:-0}" == 0 || "${SMOKE:-0}" == 1 ]] || die 'SMOKE must be 0 or 1'
timeout="${SETUP_TIMEOUT:-300}"
wait_timeout "$timeout"
init_run "$LAB_DIR" "${1:-$(utc_run_id)}"
if [[ -f "$OUT_DIR/summary.json" ]]; then
    python3 "$LAB_DIR/analysis/summarize.py" "$OUT_DIR/summary.json"
    python3 - "$OUT_DIR/summary.json" <<'PY'
import json, sys
summary = json.load(open(sys.argv[1]))
raise SystemExit(0 if 'error' not in summary and all(r['claim_allocated'] and r['pytorch_cuda_ok'] and r['pod_deleted_at'] and r['claim_deleted_at'] for r in summary['gpus']) else 1)
PY
    exit 0
fi
python3 - "$OUT_DIR/run.json" "$RUN_ID" "${SMOKE:-0}" <<'PY'
import datetime, json, sys
with open(sys.argv[1], 'w') as stream:
    json.dump({'run_id': sys.argv[2], 'mode': 'smoke' if sys.argv[3] == '1' else 'full',
               'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}, stream, indent=2)
PY
on_exit() {
    status=$?
    if (( status != 0 )); then
        printf 'run.sh failed with exit %s; see commands.log and events.yaml\n' "$status" > "$OUT_DIR/run-error.txt"
        bash "$SCRIPT_DIR/collect.sh" "$RUN_ID" || true
        python3 "$LAB_DIR/analysis/summarize.py" "$OUT_DIR" --write || true
    fi
}
trap on_exit EXIT

existing="$(kc get namespace gpu-setup-lab --ignore-not-found -o 'jsonpath={.metadata.labels.app\.kubernetes\.io/part-of}')"
if [[ -z "$existing" ]]; then
    # A namespace with no matching label must not be adopted by apply.
    name="$(kc get namespace gpu-setup-lab --ignore-not-found -o name)"
    [[ -z "$name" ]] || die 'gpu-setup-lab exists without the dra-book label'
else
    [[ "$existing" == "$BOOK_LABEL_VALUE" ]] || die 'gpu-setup-lab belongs to another workload'
fi
for role in a40 rtx4090; do
    prior="$(kc -n gpu-setup-lab get job "gpu-smoke-$role" --ignore-not-found -o name)"
    [[ -z "$prior" ]] || die "previous Job $prior remains; inspect it and run cleanup.sh first"
done
kc -n gpu-setup-lab get configmaps,resourceclaimtemplates.resource.k8s.io -o json > "$OUT_DIR/existing-manifest-objects.json"
python3 - "$OUT_DIR/existing-manifest-objects.json" <<'PY'
import json, sys
names = {('ConfigMap', 'gpu-setup-workload'), ('ResourceClaimTemplate', 'a40-whole-gpu'),
         ('ResourceClaimTemplate', 'rtx4090-whole-gpu')}
for obj in json.load(open(sys.argv[1]))['items']:
    meta = obj['metadata']
    if (obj['kind'], meta['name']) in names and meta.get('labels', {}).get('app.kubernetes.io/part-of') != 'dra-book':
        raise SystemExit('refusing to adopt foreign object: ' + obj['kind'] + '/' + meta['name'])
PY
for node in dra-a40 dra-4090; do
    OUT_DIR="$OUT_DIR/idle-$node" bash "$LAB_DIR/../common/gpu-idle-check.sh" "$node"
done
kc apply -f "$LAB_DIR/manifests"

# Both workloads hold a local marker barrier until their allocation is saved.
for role in a40 rtx4090; do
    deadline=$((SECONDS + timeout))
    while :; do
        kc -n gpu-setup-lab get pods -l "job-name=gpu-smoke-$role" -o json > "$OUT_DIR/$role-pods.json"
        details="$(python3 - "$OUT_DIR/$role-pods.json" <<'PY'
import json, sys
pods = json.load(open(sys.argv[1]))['items']
if len(pods) == 1:
    pod = pods[0]
    for claim in pod.get('status', {}).get('resourceClaimStatuses', []):
        if claim['name'] == 'gpu' and claim.get('resourceClaimName'):
            print(pod['metadata']['name'], claim['resourceClaimName'])
PY
)"
        [[ -n "$details" ]] && break
        (( SECONDS < deadline )) || die "timeout waiting for generated claim: $role"
        sleep "${WAIT_INTERVAL:-1}"
    done
    read -r pod claim <<< "$details"
    wait_claim_allocated gpu-setup-lab "$claim" "$timeout"
    kc -n gpu-setup-lab wait "pod/$pod" --for=condition=Ready --timeout="${timeout}s"
    kc -n gpu-setup-lab get pod "$pod" -o json > "$OUT_DIR/$role-pod-allocated.json"
    kc -n gpu-setup-lab get resourceclaim "$claim" -o yaml > "$OUT_DIR/$role-claim-allocated.yaml"
    kc -n gpu-setup-lab get resourceclaim "$claim" -o json > "$OUT_DIR/$role-claim-allocated.json"
done
for role in a40 rtx4090; do
    pod="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["name"])' "$OUT_DIR/$role-pod-allocated.json")"
    kc -n gpu-setup-lab exec "$pod" -c workload -- python -c 'from pathlib import Path; Path("/tmp/dra-book-start").touch()'
done
for role in a40 rtx4090; do
    wait_job_complete gpu-setup-lab "gpu-smoke-$role" "$timeout"
    pod="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["name"])' "$OUT_DIR/$role-pod-allocated.json")"
    claim="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["name"])' "$OUT_DIR/$role-claim-allocated.json")"
    kc -n gpu-setup-lab get pod "$pod" -o yaml > "$OUT_DIR/$role-pod-completed.yaml"
    kc -n gpu-setup-lab get pod "$pod" -o json > "$OUT_DIR/$role-pod-completed.json"
    kc -n gpu-setup-lab get resourceclaim "$claim" --ignore-not-found -o yaml > "$OUT_DIR/$role-claim-completed.yaml"
    kc -n gpu-setup-lab get resourceclaim "$claim" --ignore-not-found -o json > "$OUT_DIR/$role-claim-completed.json"
    [[ -s "$OUT_DIR/$role-claim-completed.json" ]] || printf '{}\n' > "$OUT_DIR/$role-claim-completed.json"
    kc -n gpu-setup-lab logs "$pod" -c workload > "$OUT_DIR/$role-pod.log"
done
bash "$SCRIPT_DIR/collect.sh" "$RUN_ID"
for role in a40 rtx4090; do
    pod="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["name"])' "$OUT_DIR/$role-pod-allocated.json")"
    claim="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["name"])' "$OUT_DIR/$role-claim-allocated.json")"
    start=$SECONDS
    requested_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    pod_deleted_at=''; claim_deleted_at=''; early=false; before_pod=false
    present="$(kc -n gpu-setup-lab get resourceclaim "$claim" --ignore-not-found -o name)"
    if [[ -z "$present" ]]; then
        early=true; before_pod=true; claim_deleted_at="$requested_at"
    fi
    kc -n gpu-setup-lab delete jobs -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=$role-smoke" --cascade=background --wait=false --ignore-not-found
    while :; do
        observed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        present="$(kc -n gpu-setup-lab get pod "$pod" --ignore-not-found -o name)"
        if [[ -z "$present" && -z "$pod_deleted_at" ]]; then pod_deleted_at="$observed_at"; fi
        present="$(kc -n gpu-setup-lab get resourceclaim "$claim" --ignore-not-found -o name)"
        if [[ -z "$present" && -z "$claim_deleted_at" ]]; then
            claim_deleted_at="$observed_at"
            [[ -n "$pod_deleted_at" ]] || before_pod=true
        fi
        [[ -z "$pod_deleted_at" || -z "$claim_deleted_at" ]] || break
        (( SECONDS - start < timeout )) || break
        sleep "${WAIT_INTERVAL:-1}"
    done
    python3 - "$OUT_DIR/$role-deletion.json" "$requested_at" "$pod_deleted_at" "$claim_deleted_at" "$early" "$before_pod" "$((SECONDS-start))" <<'PY'
import json, sys
with open(sys.argv[1], 'w') as stream:
    json.dump({'job_delete_requested_at': sys.argv[2], 'pod_deleted_at': sys.argv[3] or None,
               'claim_deleted_at': sys.argv[4] or None, 'claim_absent_before_job_deletion': sys.argv[5] == 'true',
               'claim_absent_before_pod_deletion': sys.argv[6] == 'true', 'observation_seconds': int(sys.argv[7])}, stream, indent=2)
PY
done
bash "$SCRIPT_DIR/collect.sh" "$RUN_ID"
python3 - "$OUT_DIR/summary.json" <<'PY'
import json, sys
summary = json.load(open(sys.argv[1]))
raise SystemExit(0 if all(r['claim_allocated'] and r['pytorch_cuda_ok'] and r['pod_deleted_at'] and r['claim_deleted_at'] for r in summary['gpus']) else 1)
PY
