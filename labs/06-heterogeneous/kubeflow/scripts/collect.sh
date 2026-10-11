#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${KFP_STATE_DIR:?Set KFP_STATE_DIR to the original installation directory}"
namespace="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["namespace"])' "$KFP_STATE_DIR/state.json")"
output="${OUT_DIR:-$(cd "$KFP_STATE_DIR/.." && pwd)/kubeflow}"
mkdir -p "$output/collected"
for resource in pods resourceclaims resourceclaimtemplates events workflows.argoproj.io; do
    kubectl -n "$namespace" get "$resource" -o json > "$output/collected/$resource.json"
done
kubectl get resourceslices -o json > "$output/collected/resourceslices.json"
python3 "$script_dir/../../analysis/analyze.py" --out "$(cd "$KFP_STATE_DIR/.." && pwd)" --output "$output/summary.json"
