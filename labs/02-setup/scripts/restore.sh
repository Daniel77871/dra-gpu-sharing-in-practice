#!/usr/bin/env bash
# Undo ONLY a fresh installation made by install.sh, using its saved backup.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$LAB_DIR/../common/lib.sh"
[[ $# -eq 1 ]] || die 'usage: restore.sh <install-run-id>'
require_cmd helm
require_cmd python3
[[ "$1" =~ ^[A-Za-z0-9._-]+$ && "$1" != '.' && "$1" != '..' ]] || die 'invalid install run-id'
backup="$LAB_DIR/out/$1"
[[ -f "$backup/created-daemonsets.json" ]] || die 'install backup/created DaemonSet snapshot is missing'
[[ ! -f "$backup/restored.txt" ]] || { printf 'Already restored: %s\n' "$backup"; exit 0; }
init_run "$LAB_DIR" "restore-$(utc_run_id)"
kc get daemonsets -A -o json > "$OUT_DIR/current-daemonsets.json"
python3 "$LAB_DIR/analysis/install_state.py" compare "$backup/original-daemonsets.json" "$OUT_DIR/current-daemonsets.json"
python3 "$LAB_DIR/analysis/install_state.py" compare "$backup/created-daemonsets.json" "$OUT_DIR/current-daemonsets.json"
kc get resourceclaims.resource.k8s.io -A -o json > "$OUT_DIR/current-claims.json"
python3 - "$OUT_DIR/current-claims.json" <<'PY'
import json, sys
for claim in json.load(open(sys.argv[1]))['items']:
    results = claim.get('status', {}).get('allocation', {}).get('devices', {}).get('results', [])
    if any(r.get('driver') == 'gpu.nvidia.com' for r in results):
        raise SystemExit('GPU claims remain allocated; finish/clean up workloads before restore')
PY
for release in dra-driver-nvidia-gpu gpu-operator; do
    if [[ "$release" == gpu-operator ]]; then namespace=gpu-operator; else namespace=nvidia-dra-driver-gpu; fi
    if helm status "$release" -n "$namespace" > "$OUT_DIR/$release-status.txt" 2>&1; then
        helm uninstall "$release" -n "$namespace" --wait --timeout=10m
    fi
done
kc get nodes -o json > "$OUT_DIR/current-nodes.json"
python3 "$LAB_DIR/analysis/install_state.py" node-labels "$backup/original-nodes.json" "$OUT_DIR/current-nodes.json" > "$OUT_DIR/restore-labels.txt"
while read -r node label; do
    kc label node "$node" "$label" --overwrite
done < "$OUT_DIR/restore-labels.txt"
# Both namespaces were required to be absent before install. Do not delete one
# now containing foreign workloads, even though it was created by this install.
kc api-resources --namespaced=true --verbs=list -o name > "$OUT_DIR/namespaced-kinds.txt"
[[ -s "$OUT_DIR/namespaced-kinds.txt" ]] || die 'namespaced resource inventory is empty'
for namespace in gpu-operator nvidia-dra-driver-gpu; do
    while IFS= read -r kind; do
        [[ "$kind" != events && "$kind" != events.events.k8s.io ]] || continue
        kc -n "$namespace" get "$kind" -o json > "$OUT_DIR/$namespace-$kind-remaining.json"
        python3 - "$OUT_DIR/$namespace-$kind-remaining.json" <<'PY'
import json, sys
for obj in json.load(open(sys.argv[1]))['items']:
    meta = obj['metadata']
    if (obj['kind'], meta['name']) not in {('ServiceAccount', 'default'), ('ConfigMap', 'kube-root-ca.crt')}:
        raise SystemExit('namespace has remaining objects; inspect before deleting it: ' + obj['kind'] + '/' + meta['name'])
PY
    done < "$OUT_DIR/namespaced-kinds.txt"
done
for namespace in gpu-operator nvidia-dra-driver-gpu; do
    kc delete namespace "$namespace" --ignore-not-found --wait=true --timeout=300s
done
kc get daemonsets -A -o json > "$OUT_DIR/restored-daemonsets.json"
python3 "$LAB_DIR/analysis/install_state.py" compare "$backup/original-daemonsets.json" "$OUT_DIR/restored-daemonsets.json"
printf '%s\n' "$OUT_DIR" > "$backup/restored.txt"
printf 'Kubernetes installation removed; see README.md for host/runtime verification.\n'
