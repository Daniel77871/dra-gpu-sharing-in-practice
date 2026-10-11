#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$LAB_DIR/../common/lib.sh"
[[ $# -eq 1 ]] || die 'usage: collect.sh <run-id>'
init_run "$LAB_DIR" "$1"
kc get nodes -o yaml > "$OUT_DIR/nodes.yaml"
kc get deviceclasses.resource.k8s.io -o yaml > "$OUT_DIR/deviceclasses.yaml"
kc get resourceslices.resource.k8s.io -o yaml > "$OUT_DIR/resourceslices.yaml"
kc -n gpu-setup-lab get jobs,pods,resourceclaims.resource.k8s.io,resourceclaimtemplates.resource.k8s.io \
    -l "$BOOK_SELECTOR" -o yaml > "$OUT_DIR/workloads-current.yaml"
kc -n gpu-setup-lab get events -o yaml > "$OUT_DIR/events.yaml"
kc -n gpu-setup-lab get events -o json > "$OUT_DIR/events.json"
for role in a40 rtx4090; do
    pod_file="$OUT_DIR/$role-pod-allocated.json"
    [[ -s "$pod_file" ]] || continue
    pod="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["name"])' "$pod_file")"
    current="$(kc -n gpu-setup-lab get pod "$pod" --ignore-not-found -o name)"
    if [[ -n "$current" ]]; then
        kc -n gpu-setup-lab logs "$pod" -c workload > "$OUT_DIR/$role-pod.log"
        kc -n gpu-setup-lab get pod "$pod" -o yaml > "$OUT_DIR/$role-pod-current.yaml"
    fi
done
python3 "$LAB_DIR/analysis/summarize.py" "$OUT_DIR" --write
