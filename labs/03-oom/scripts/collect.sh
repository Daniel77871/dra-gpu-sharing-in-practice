#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$LAB_DIR/../common/lib.sh"
[[ $# -eq 1 ]] || die 'usage: collect.sh <run-id>'
[[ -d "$LAB_DIR/out/$1" ]] || die 'run directory does not exist'
init_run "$LAB_DIR" "$1"
RUN_OUT="$OUT_DIR"
for directory in "$RUN_OUT"/exit-r* "$RUN_OUT"/hold-r*; do
    [[ -f "$directory/trial.json" ]] || continue
    [[ ! -f "$directory/cleaned.json" ]] || continue
    export OUT_DIR="$directory"
    for prefix in a b; do
        [[ -s "$directory/$prefix-pod-name.txt" ]] || continue
        pod="$(cat "$directory/$prefix-pod-name.txt")"
        kc -n gpu-oom-lab get pod "$pod" --ignore-not-found -o json > "$directory/$prefix-collect.json"
        [[ -s "$directory/$prefix-collect.json" ]] || continue
        python3 - "$directory/$prefix-created.json" "$directory/$prefix-collect.json" <<'PY'
import json, sys
created, current = [json.load(open(path)) for path in sys.argv[1:]]
if current['metadata']['uid'] != created['metadata']['uid']:
    raise SystemExit('Pod UID changed; refusing to overwrite earlier evidence')
if current['metadata'].get('labels', {}).get('app.kubernetes.io/part-of') != 'dra-book':
    raise SystemExit('Pod ownership changed')
PY
        kc -n gpu-oom-lab logs "$pod" -c workload > "$directory/$prefix-log.tmp"
        mv "$directory/$prefix-log.tmp" "$directory/$prefix.jsonl"
        role=survivor
        [[ "$prefix" == a ]] || role=pressure
        cp "$directory/$prefix-collect.json" "$directory/$role-final.json"
    done
    kc -n gpu-oom-lab get resourceclaim a40-shared --ignore-not-found -o json > "$directory/claim-current.json"
    # Preserve the allocation snapshot taken while both consumers were reserved.
    if [[ ! -s "$directory/claim.json" && -s "$directory/claim-current.json" ]]; then
        cp "$directory/claim-current.json" "$directory/claim.json"
    fi
    kc -n gpu-oom-lab get events -o json > "$directory/events.json"
    python3 "$LAB_DIR/analysis/control.py" collected "$directory"
done
python3 "$LAB_DIR/analysis/analyze.py" "$RUN_OUT"
