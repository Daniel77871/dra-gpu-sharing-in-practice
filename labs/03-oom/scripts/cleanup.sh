#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$LAB_DIR/../common/lib.sh"
init_run "$LAB_DIR" "${1:-cleanup-$(utc_run_id)}"
# Stop only this output's nonce-bound samplers; never kill a PID read from a file.
for record in "$OUT_DIR"/exit-r*/gpu.jsonl.pid "$OUT_DIR"/hold-r*/gpu.jsonl.pid; do
    [[ -f "$record" ]] || continue
    node="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["node"])' "$record")"
    bash "$SCRIPT_DIR/sample.sh" stop "$node" "${record%.pid}"
done
namespace="$(kc get namespace gpu-oom-lab --ignore-not-found -o name)"
[[ -n "$namespace" ]] || exit 0
owner="$(kc get namespace gpu-oom-lab -o 'jsonpath={.metadata.labels.app\.kubernetes\.io/part-of}')"
[[ "$owner" == "$BOOK_LABEL_VALUE" ]] || die 'refusing cleanup without dra-book namespace ownership'
selector="$BOOK_SELECTOR,$COMPONENT_LABEL_KEY in (steady-service,greedy-batch)"
kc -n gpu-oom-lab delete jobs -l "$selector" --cascade=background --ignore-not-found --wait=true --timeout=60s
kc -n gpu-oom-lab delete pods -l "$selector" --ignore-not-found --wait=true --timeout=60s
kc -n gpu-oom-lab delete resourceclaims.resource.k8s.io -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=shared-gpu" --ignore-not-found --wait=true --timeout=60s
kc -n gpu-oom-lab delete configmaps -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=workload-code" --ignore-not-found --wait=true --timeout=60s
# Namespace deletion also removes its contents. Preserve foreign objects, using
# the same ownership check as the setup lab; Kubernetes defaults are permitted.
kc api-resources --namespaced=true --verbs=list -o name > "$OUT_DIR/namespaced-kinds.txt"
while IFS= read -r kind; do
    [[ "$kind" != events && "$kind" != events.events.k8s.io ]] || continue
    kc -n gpu-oom-lab get "$kind" -o json > "$OUT_DIR/cleanup-current.json"
    python3 - "$OUT_DIR/cleanup-current.json" "$kind" <<'PY'
import json, sys
for obj in json.load(open(sys.argv[1])).get('items', []):
    meta = obj['metadata']
    default = (sys.argv[2] == 'serviceaccounts' and meta['name'] == 'default') or (
        sys.argv[2] == 'configmaps' and meta['name'] == 'kube-root-ca.crt')
    if not default and meta.get('labels', {}).get('app.kubernetes.io/part-of') != 'dra-book':
        raise SystemExit('refusing namespace deletion: foreign ' + sys.argv[2] + '/' + meta['name'])
PY
done < "$OUT_DIR/namespaced-kinds.txt"
kc delete namespace gpu-oom-lab --ignore-not-found --wait=true --timeout=300s
printf 'Cleaned labeled experiment objects and gpu-oom-lab namespace\n'
