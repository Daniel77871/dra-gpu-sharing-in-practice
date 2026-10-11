#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ $# -eq 1 && -d "$ISOLATION_DIR/out/$1" ]] || die 'usage: cleanup.sh <existing-run-id>'
init_run "$ISOLATION_DIR" "$1"
RUN_OUT="$OUT_DIR"
kc get namespace "$ISOLATION_NS" --ignore-not-found -o json > "$RUN_OUT/namespace-cleanup.json"
if [[ ! -s "$RUN_OUT/namespace-cleanup.json" ]]; then exit 0; fi
python3 - "$RUN_OUT/namespace-cleanup.json" <<'PY'
import json,sys
if json.load(open(sys.argv[1]))['metadata'].get('labels',{}).get('app.kubernetes.io/part-of')!='dra-book':
    raise SystemExit('namespace ownership differs; refusing cleanup')
PY
kc -n "$ISOLATION_NS" get jobs,pods,resourceclaims,configmaps -l "$BOOK_SELECTOR" -o json > "$RUN_OUT/objects-cleanup.json"
helper cleanup-owner "$RUN_OUT/objects-cleanup.json" "$RUN_ID"
# Ask CUDA clients to synchronize and exit before deleting their Job. Keep
# resources if normal exit cannot be verified; collect.sh can be run again.
for ROUND_DIR in "$RUN_OUT"/*; do
    [[ -f "$ROUND_DIR/trial.json" && ! -f "$ROUND_DIR/cleaned.json" ]] || continue
    export OUT_DIR="$ROUND_DIR"
    backend="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("case",""))' "$ROUND_DIR/trial.json")"
    if [[ "$backend" != accounting ]]; then
        for prefix in a b; do
            [[ -s "$ROUND_DIR/$prefix-pod-name.txt" ]] || continue
            pod="$(cat "$ROUND_DIR/$prefix-pod-name.txt")"
            kc -n "$ISOLATION_NS" get pod "$pod" --ignore-not-found -o json > "$ROUND_DIR/$prefix-cleanup.json"
            [[ -s "$ROUND_DIR/$prefix-cleanup.json" ]] || continue
            helper same-pod "$ROUND_DIR/$prefix-created.json" "$ROUND_DIR/$prefix-cleanup.json"
            if ! helper finished "$ROUND_DIR/$prefix-cleanup.json" >/dev/null; then
                control "$pod" "$prefix" stop false false true
                wait_exit "$pod" "$prefix" 1
            fi
        done
    fi
    if [[ -f "$ROUND_DIR/gpu.csv.pid" ]]; then bash "$SAMPLE" stop "$GPU_NODE" "$ROUND_DIR/gpu.csv"; fi
done
bash "$ISOLATION_DIR/common/scripts/collect.sh" "$RUN_ID"
export OUT_DIR="$RUN_OUT"
kc -n "$ISOLATION_NS" delete jobs -l "$WORKLOAD_SELECTOR,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true --timeout=120s
wait_pods_absent "$WORKLOAD_SELECTOR,app.kubernetes.io/instance=$RUN_ID"
kc -n "$ISOLATION_NS" delete resourceclaims -l "$CLAIM_SELECTOR,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true --timeout=120s
kc -n "$ISOLATION_NS" delete configmaps -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=workload-code,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true
ROUND_DIR="$RUN_OUT"
wait_gpu_clean after
OUT_DIR="$RUN_OUT/idle-final" bash "$ISOLATION_DIR/../common/gpu-idle-check.sh" "$GPU_NODE"
# Namespace deletion also removes its contents. Inspect every listable kind,
# allowing only Kubernetes defaults or objects owned by this run.
kc api-resources --namespaced=true --verbs=list -o name > "$RUN_OUT/namespaced-kinds.txt"
while IFS= read -r kind; do
    [[ "$kind" != events && "$kind" != events.events.k8s.io ]] || continue
    kc -n "$ISOLATION_NS" get "$kind" -o json > "$RUN_OUT/namespace-contents.json"
    python3 - "$RUN_OUT/namespace-contents.json" "$kind" "$RUN_ID" <<'PY'
import json, sys
for obj in json.load(open(sys.argv[1])).get('items', []):
    meta = obj['metadata']
    default = (sys.argv[2] == 'serviceaccounts' and meta['name'] == 'default') or (
        sys.argv[2] == 'configmaps' and meta['name'] == 'kube-root-ca.crt')
    if default:
        continue
    labels = meta.get('labels', {})
    if labels.get('app.kubernetes.io/part-of') != 'dra-book':
        raise SystemExit('refusing namespace deletion: foreign ' + sys.argv[2] + '/' + meta['name'])
    if labels.get('app.kubernetes.io/instance') != sys.argv[3]:
        raise SystemExit('refusing namespace deletion: object belongs to a different run: ' + meta['name'])
PY
done < "$RUN_OUT/namespaced-kinds.txt"
kc delete namespace "$ISOLATION_NS" --ignore-not-found --wait=true --timeout=300s
log 'Owned workloads and gpu-isolation-lab Namespace removed. Provider and provider-backup retained.'
