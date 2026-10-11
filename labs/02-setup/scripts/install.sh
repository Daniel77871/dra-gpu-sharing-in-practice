#!/usr/bin/env bash
# ONLY FOR A FRESH CLUSTER. Changes node labels and cluster-wide GPU components.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$LAB_DIR/../common/lib.sh"
[[ "${FRESH_CLUSTER:-0}" == 1 ]] || die 'only for a fresh cluster; read README.md and set FRESH_CLUSTER=1'
require_cmd helm
require_cmd python3
init_run "$LAB_DIR" "${1:-install-$(utc_run_id)}"
[[ ! -e "$OUT_DIR/original-daemonsets.json" ]] || die 'use a new install run-id; do not overwrite the backup'
kc get nodes -o json > "$OUT_DIR/original-nodes.json"
kc get namespaces -o json > "$OUT_DIR/original-namespaces.json"
kc get daemonsets -A -o json > "$OUT_DIR/original-daemonsets.json"
kc get deviceclasses.resource.k8s.io -o json > "$OUT_DIR/original-deviceclasses.json"
kc get resourceslices.resource.k8s.io -o json > "$OUT_DIR/original-resourceslices.json"
helm list -A -o json > "$OUT_DIR/original-releases.json"
python3 "$LAB_DIR/analysis/install_state.py" fresh "$OUT_DIR/original-namespaces.json" "$OUT_DIR/original-daemonsets.json" \
    "$OUT_DIR/original-releases.json" "$OUT_DIR/original-deviceclasses.json" "$OUT_DIR/original-resourceslices.json"
kc wait node/dra-m1 node/dra-non node/dra-a40 node/dra-4090 --for=condition=Ready --timeout=300s

snapshot_created() {
    kc get daemonsets -A -o json > "$OUT_DIR/installed-daemonsets.json"
    python3 "$LAB_DIR/analysis/install_state.py" created "$OUT_DIR/installed-daemonsets.json" > "$OUT_DIR/created-daemonsets.json"
}
trap snapshot_created EXIT
kc label node dra-4090 dra-a40 nvidia.com/dra-kubelet-plugin=true --overwrite
helm repo add nvidia https://nvidia.github.io/gpu-operator
helm repo update
helm upgrade --install gpu-operator nvidia/gpu-operator \
    --version v26.7.0 --create-namespace --namespace gpu-operator \
    -f "$LAB_DIR/values/gpu-operator.yaml"

# Wait for the Operator to create its operands, then for both driver Pods.
deadline=$((SECONDS + 900))
while :; do
    kc -n gpu-operator get daemonsets -o json > "$OUT_DIR/operator-daemonsets.json"
    count="$(python3 -c 'import json,sys; print(sum(d["metadata"]["name"] == "nvidia-driver-daemonset" for d in json.load(open(sys.argv[1]))["items"]))' "$OUT_DIR/operator-daemonsets.json")"
    [[ "$count" == 1 ]] && break
    (( SECONDS < deadline )) || die 'timeout waiting for driver DaemonSet creation'
    sleep 2
done
kc -n gpu-operator rollout status daemonset/nvidia-driver-daemonset --timeout=900s
for node in dra-a40 dra-4090; do
    pod="$(driver_pod "$node")"
    kc -n gpu-operator exec "$pod" -c "$DRIVER_CONTAINER" -- nvidia-smi
done
deadline=$((SECONDS + 900))
while :; do
    kc -n gpu-operator get pods -o json > "$OUT_DIR/operator-ready.json"
    if python3 "$LAB_DIR/analysis/verify.py" operator "$OUT_DIR/operator-ready.json" > "$OUT_DIR/operator-ready.txt"; then break; fi
    (( SECONDS < deadline )) || die 'timeout waiting for GPU Operator operands; see operator-ready.txt'
    sleep 2
done
helm repo add nvidia-ngc https://helm.ngc.nvidia.com/nvidia
helm repo update nvidia-ngc
helm install dra-driver-nvidia-gpu nvidia-ngc/dra-driver-nvidia-gpu \
    --version 0.5.0 --namespace nvidia-dra-driver-gpu --create-namespace \
    -f "$LAB_DIR/values/dra-driver.yaml"
kc -n nvidia-dra-driver-gpu rollout status daemonset/dra-driver-nvidia-gpu-kubelet-plugin --timeout=300s
bash "$SCRIPT_DIR/verify.sh" "$RUN_ID-verify"
snapshot_created
python3 "$LAB_DIR/analysis/install_state.py" compare "$OUT_DIR/original-daemonsets.json" "$OUT_DIR/installed-daemonsets.json"
printf 'Install backup: %s\nRestore only this fresh installation: bash %s/restore.sh %s\n' "$OUT_DIR" "$SCRIPT_DIR" "$RUN_ID"
