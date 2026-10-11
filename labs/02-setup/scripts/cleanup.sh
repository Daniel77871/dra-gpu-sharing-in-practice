#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$LAB_DIR/../common/lib.sh"
init_run "$LAB_DIR" "${1:-cleanup-$(utc_run_id)}"
name="$(kc get namespace gpu-setup-lab --ignore-not-found -o name)"
[[ -n "$name" ]] || exit 0
label="$(kc get namespace gpu-setup-lab -o 'jsonpath={.metadata.labels.app\.kubernetes\.io/part-of}')"
[[ "$label" == "$BOOK_LABEL_VALUE" ]] || die 'refusing to delete a namespace without dra-book ownership'
kc -n gpu-setup-lab delete jobs -l "$BOOK_SELECTOR" --cascade=background --ignore-not-found --wait=true --timeout=300s
kc -n gpu-setup-lab delete pods,resourceclaims.resource.k8s.io,resourceclaimtemplates.resource.k8s.io,configmaps \
    -l "$BOOK_SELECTOR" --ignore-not-found --wait=true --timeout=300s
# Namespace deletion would also delete foreign objects. Check every listable kind.
kc api-resources --namespaced=true --verbs=list -o name > "$OUT_DIR/namespaced-kinds.txt"
while IFS= read -r kind; do
    [[ "$kind" != events && "$kind" != events.events.k8s.io ]] || continue
    kc -n gpu-setup-lab get "$kind" -o json > "$OUT_DIR/cleanup-current.json"
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
kc delete namespace gpu-setup-lab --ignore-not-found --wait=true --timeout=300s
