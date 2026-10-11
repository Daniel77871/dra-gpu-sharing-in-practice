#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ $# -eq 1 ]] || die 'usage: cleanup.sh <existing-run-id>'
[[ -d "${OUT_DIR:-$ISOLATION_DIR/out/$1}" ]] || die 'run output directory does not exist'
open_run "$1"
kc get namespace "$ISOLATION_NS" --ignore-not-found -o json > "$RUN_OUT/namespace-cleanup.json"
[[ -s "$RUN_OUT/namespace-cleanup.json" ]] || exit 0
python3 - "$RUN_OUT/namespace-cleanup.json" <<'PY'
import json,sys
if json.load(open(sys.argv[1]))['metadata'].get('labels',{}).get('app.kubernetes.io/part-of')!='dra-book':
    raise SystemExit('foreign Namespace; cleanup refused')
PY
kc -n "$ISOLATION_NS" get jobs,pods,resourceclaims,configmaps -l "$BOOK_SELECTOR" -o json > "$RUN_OUT/objects-cleanup.json"
helper cleanup-owner "$RUN_OUT/objects-cleanup.json" "$RUN_ID"
trap 'export OUT_DIR="$RUN_OUT"; host_stop' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for ROUND_DIR in "$RUN_OUT"/*; do
    [[ -f "$ROUND_DIR/trial.json" && ! -f "$ROUND_DIR/cleaned.json" ]] || continue
    export OUT_DIR="$ROUND_DIR"
    case_name="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("case",""))' "$ROUND_DIR/trial.json")"
    case "$case_name" in
        case3|case4a|case4b)
            host_start
            export OUT_DIR="$ROUND_DIR"
            scope_stop
            OUT_DIR="$RUN_OUT" bash "$HAMI_DIR/scripts/collect.sh" "$RUN_ID"
            if [[ -f "$ROUND_DIR/gpu.csv.pid" ]]; then bash "$SAMPLE" stop "$GPU_NODE" "$ROUND_DIR/gpu.csv"; fi
            scope_cleanup_round
            ;;
        accounting) ;;
        *)
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
            if [[ -f "$ROUND_DIR/gpu.csv.pid" ]]; then bash "$SAMPLE" stop "$GPU_NODE" "$ROUND_DIR/gpu.csv"; fi
            ;;
    esac
done
OUT_DIR="$RUN_OUT" bash "$HAMI_DIR/scripts/collect.sh" "$RUN_ID"
export OUT_DIR="$RUN_OUT"
kc -n "$ISOLATION_NS" delete jobs -l "$WORKLOAD_SELECTOR,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true --timeout=120s
wait_pods_absent "$WORKLOAD_SELECTOR,app.kubernetes.io/instance=$RUN_ID"
kc -n "$ISOLATION_NS" delete resourceclaims -l "$CLAIM_SELECTOR,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true --timeout=120s
kc -n "$ISOLATION_NS" delete configmaps -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY in (workload-code,hami-scope-code),app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true
ROUND_DIR="$RUN_OUT"
host_start
wait_claim_artifacts_absent "$RUN_OUT/objects-cleanup.json"
wait_gpu_clean after
OUT_DIR="$RUN_OUT/idle-final" bash "$ISOLATION_DIR/../common/gpu-idle-check.sh" "$GPU_NODE"
# Namespace deletion also removes its contents. Permit only Kubernetes defaults
# and events after the run's own objects have been removed.
kc api-resources --namespaced=true --verbs=list -o name > "$RUN_OUT/namespaced-kinds.txt"
while IFS= read -r kind; do
    [[ "$kind" != events && "$kind" != events.events.k8s.io ]] || continue
    kc -n "$ISOLATION_NS" get "$kind" -o json > "$RUN_OUT/namespace-contents.json"
    python3 - "$RUN_OUT/namespace-contents.json" "$kind" <<'PY'
import json,sys
for obj in json.load(open(sys.argv[1])).get('items',[]):
    meta=obj['metadata']
    default=(sys.argv[2]=='serviceaccounts' and meta['name']=='default') or (
        sys.argv[2]=='configmaps' and meta['name']=='kube-root-ca.crt')
    if not default:
        raise SystemExit('Namespace is not empty; preserving '+sys.argv[2]+'/'+meta['name'])
PY
done < "$RUN_OUT/namespaced-kinds.txt"
kc get namespace "$ISOLATION_NS" --ignore-not-found -o json > "$RUN_OUT/namespace-final.json"
if [[ -s "$RUN_OUT/namespace-final.json" ]]; then
    python3 - "$RUN_OUT/namespace-cleanup.json" "$RUN_OUT/namespace-final.json" "$RUN_OUT/namespace-delete-options.json" <<'PY'
import json,sys
before,current=(json.load(open(path)) for path in sys.argv[1:3])
meta=current['metadata']
if meta['uid']!=before['metadata']['uid'] or meta.get('labels',{}).get('app.kubernetes.io/part-of')!='dra-book':
    raise SystemExit('Namespace UID/ownership changed; cleanup refused')
if not meta.get('uid') or not meta.get('resourceVersion'):
    raise SystemExit('Namespace UID/resourceVersion missing; cleanup refused')
with open(sys.argv[3],'w') as stream:
    json.dump({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{
        'uid':meta['uid'],'resourceVersion':meta['resourceVersion']}},stream)
PY
    if kc delete --raw "/api/v1/namespaces/$ISOLATION_NS" -f "$RUN_OUT/namespace-delete-options.json" > "$RUN_OUT/namespace-delete-result.json" 2> "$RUN_OUT/namespace-delete-error.txt"; then
        :
    else
        namespace_delete_status=$?
        cat "$RUN_OUT/namespace-delete-error.txt" >&2
        case "$(cat "$RUN_OUT/namespace-delete-error.txt")" in
            *'(NotFound)'*|*'(Conflict)'*) ;; # Recheck a concurrent deletion below.
            *) exit "$namespace_delete_status" ;;
        esac
    fi
    # A raw DELETE starts Namespace finalization; it does not wait for absence.
    namespace_deadline=$((SECONDS + 300))
    while :; do
        kc get namespace "$ISOLATION_NS" --ignore-not-found -o json > "$RUN_OUT/namespace-delete-current.json"
        [[ -s "$RUN_OUT/namespace-delete-current.json" ]] || break
        python3 - "$RUN_OUT/namespace-final.json" "$RUN_OUT/namespace-delete-current.json" <<'PY'
import json,sys
before,current=(json.load(open(path)) for path in sys.argv[1:])
meta=current['metadata']
if meta['uid']!=before['metadata']['uid'] or meta.get('labels',{}).get('app.kubernetes.io/part-of')!='dra-book':
    raise SystemExit('Namespace UID/ownership changed during deletion; cleanup refused')
if not meta.get('deletionTimestamp'):
    raise SystemExit('Namespace DELETE did not leave the recorded UID terminating; cleanup refused')
PY
        (( SECONDS < namespace_deadline )) || die 'Namespace deletion timed out'
        sleep 1
    done
fi
log 'Owned jobs, claims, generated code, control directories and empty Namespace removed. Provider backup retained.'
