#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$LAB_DIR/../common/lib.sh"
require_cmd helm
require_cmd python3
init_run "$LAB_DIR" "${1:-verify-$(utc_run_id)}"

# This script only reads the cluster. helm template uses the tiny local chart.
kc get nodes -o json > "$OUT_DIR/verify-nodes.json"
kc -n gpu-operator get pods -o json > "$OUT_DIR/verify-operator-pods.json"
kc -n nvidia-dra-driver-gpu get pods -o json > "$OUT_DIR/verify-dra-pods.json"
kc get deviceclasses.resource.k8s.io -o json > "$OUT_DIR/verify-deviceclasses.json"
kc get resourceslices.resource.k8s.io -o json > "$OUT_DIR/verify-resourceslices.json"
result=0
python3 "$LAB_DIR/analysis/verify.py" inventory "$OUT_DIR/verify-nodes.json" \
    "$OUT_DIR/verify-operator-pods.json" "$OUT_DIR/verify-dra-pods.json" \
    "$OUT_DIR/verify-deviceclasses.json" "$OUT_DIR/verify-resourceslices.json" || result=1
for release in gpu-operator dra-driver-nvidia-gpu; do
    if [[ "$release" == gpu-operator ]]; then
        namespace=gpu-operator; values=gpu-operator
    else
        namespace=nvidia-dra-driver-gpu; values=dra-driver
    fi
    helm get values "$release" -n "$namespace" -o json > "$OUT_DIR/$values-actual-values.json"
    helm template local-values "$LAB_DIR/analysis/values-chart" -f "$LAB_DIR/values/$values.yaml" \
        > "$OUT_DIR/$values-rendered-values.yaml"
    sed '/^#/d; /^---$/d; /^[[:space:]]*$/d' "$OUT_DIR/$values-rendered-values.yaml" > "$OUT_DIR/$values-expected-values.json"
    printf 'Helm user values: %s\n' "$release"
    python3 "$LAB_DIR/analysis/verify.py" values "$OUT_DIR/$values-expected-values.json" \
        "$OUT_DIR/$values-actual-values.json" || result=1
done
exit "$result"
