#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ $# -eq 1 ]] || die 'usage: collect.sh <existing-run-id>'
[[ -d "${OUT_DIR:-$ISOLATION_DIR/out/$1}" ]] || die 'run output directory does not exist'
open_run "$1"
kc -n "$ISOLATION_NS" get jobs,pods,resourceclaims,configmaps -l "$BOOK_SELECTOR" -o json > "$RUN_OUT/objects-collect.json"
helper cleanup-owner "$RUN_OUT/objects-collect.json" "$RUN_ID"
for ROUND_DIR in "$RUN_OUT"/*; do
    [[ -f "$ROUND_DIR/trial.json" && ! -f "$ROUND_DIR/cleaned.json" ]] || continue
    export OUT_DIR="$ROUND_DIR"
    for prefix in a b c scope-primary scope-secondary container-scope-pressure; do
        [[ -s "$ROUND_DIR/$prefix-pod-name.txt" ]] || continue
        pod="$(cat "$ROUND_DIR/$prefix-pod-name.txt")"
        kc -n "$ISOLATION_NS" get pod "$pod" --ignore-not-found -o json > "$ROUND_DIR/$prefix-collect.json"
        [[ -s "$ROUND_DIR/$prefix-collect.json" ]] || continue
        helper same-pod "$ROUND_DIR/$prefix-created.json" "$ROUND_DIR/$prefix-collect.json"
        while IFS= read -r container; do
            kc -n "$ISOLATION_NS" logs "$pod" -c "$container" > "$ROUND_DIR/$pod-$container.log.tmp"
            mv "$ROUND_DIR/$pod-$container.log.tmp" "$ROUND_DIR/$pod-$container.log"
            case "$container" in
                worker-0) cp "$ROUND_DIR/$pod-$container.log" "$ROUND_DIR/client-0.log" ;;
                worker-1) cp "$ROUND_DIR/$pod-$container.log" "$ROUND_DIR/client-1.log" ;;
                workload) cp "$ROUND_DIR/$pod-$container.log" "$ROUND_DIR/task-$prefix.log" ;;
            esac
        done < <(python3 - "$ROUND_DIR/$prefix-collect.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]));states={c['name']:c.get('state',{}) for c in p.get('status',{}).get('containerStatuses',[])}
for c in p['spec']['containers']:
    if any(k in states.get(c['name'],{}) for k in ('running','terminated')): print(c['name'])
PY
)
        case "$prefix" in
            a) cp "$ROUND_DIR/$prefix-collect.json" "$ROUND_DIR/survivor-final.json" ;;
            b) cp "$ROUND_DIR/$prefix-collect.json" "$ROUND_DIR/pressure-final.json" ;;
            c) cp "$ROUND_DIR/$prefix-collect.json" "$ROUND_DIR/waiter-final.json" ;;
        esac
    done
    kc -n "$ISOLATION_NS" get resourceclaims -l "$BOOK_SELECTOR,app.kubernetes.io/instance=$RUN_ID" -o json > "$ROUND_DIR/claims-final.json"
    kc -n "$ISOLATION_NS" get events -o json > "$ROUND_DIR/events-final.json"
    kc get resourceslices -o json > "$ROUND_DIR/slices-final.json"
done
if [[ "${HAMI_COLLECT_QUIET:-0}" == 1 ]]; then
    exec > "$RUN_OUT/analysis-collect.log"
fi
if python3 "$HAMI_ANALYZER" "$RUN_OUT" --output "$RUN_OUT"; then :
else
    status=$?
    if (( status == 2 )); then
        [[ "${HAMI_COLLECT_QUIET:-0}" == 1 ]] || log 'Evidence saved; summary includes incomplete rounds.'
    else exit "$status"; fi
fi
