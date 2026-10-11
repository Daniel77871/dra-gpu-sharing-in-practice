#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ $# -eq 1 && -d "$ISOLATION_DIR/out/$1" ]] || die 'usage: collect.sh <existing-run-id>'
init_run "$ISOLATION_DIR" "$1"
RUN_OUT="$OUT_DIR"
kc -n "$ISOLATION_NS" get jobs,pods,resourceclaims,configmaps -l "$BOOK_SELECTOR" -o json > "$RUN_OUT/objects-collect.json"
helper cleanup-owner "$RUN_OUT/objects-collect.json" "$RUN_ID"
for ROUND_DIR in "$RUN_OUT"/*; do
    [[ -f "$ROUND_DIR/trial.json" && ! -f "$ROUND_DIR/cleaned.json" ]] || continue
    export OUT_DIR="$ROUND_DIR"
    for prefix in a b c; do
        [[ -s "$ROUND_DIR/$prefix-pod-name.txt" ]] || continue
        pod="$(cat "$ROUND_DIR/$prefix-pod-name.txt")"
        kc -n "$ISOLATION_NS" get pod "$pod" --ignore-not-found -o json > "$ROUND_DIR/$prefix-collect.json"
        [[ -s "$ROUND_DIR/$prefix-collect.json" ]] || continue
        helper same-pod "$ROUND_DIR/$prefix-created.json" "$ROUND_DIR/$prefix-collect.json"
        if helper logs-ready "$ROUND_DIR/$prefix-collect.json"; then
            kc -n "$ISOLATION_NS" logs "$pod" -c workload > "$ROUND_DIR/$prefix-log.tmp"
            mv "$ROUND_DIR/$prefix-log.tmp" "$ROUND_DIR/task-$prefix.log"
        fi
        case "$prefix" in a) role=survivor ;; b) role=pressure ;; c) role=waiter ;; esac
        cp "$ROUND_DIR/$prefix-collect.json" "$ROUND_DIR/$role-final.json"
    done
    kc -n "$ISOLATION_NS" get resourceclaims -l "$CLAIM_SELECTOR" -o json > "$ROUND_DIR/claims-final.json"
    kc -n "$ISOLATION_NS" get events -o json > "$ROUND_DIR/events-final.json"
    kc get resourceslices -o json > "$ROUND_DIR/slices-final.json"
done
if python3 "$ANALYZER" "$RUN_OUT" --output "$RUN_OUT" --quiet; then :
else
    status=$?
    # Collection is also needed between protocol stages and after interrupts.
    # The runner separately checks completeness after cleanup.
    if (( status == 2 )); then log 'Evidence saved; summary records the current protocol stage.'
    else exit "$status"; fi
fi
