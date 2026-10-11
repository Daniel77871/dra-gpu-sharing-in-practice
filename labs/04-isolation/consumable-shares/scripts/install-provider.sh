#!/usr/bin/env bash
set -euo pipefail
[[ "${I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER:-}" == 1 ]] || {
    printf 'Set I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1; this switches the A40 provider.\n' >&2; exit 1;
}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISOLATION_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$ISOLATION_DIR/../common/lib.sh"
[[ $# -eq 1 ]] || die 'usage: install-provider.sh <run-id>'
init_run "$ISOLATION_DIR" "$1"
PROVIDER_OUT="$OUT_DIR"
wait_provider_ready() {
    local deadline=$((SECONDS + 300)) status
    while :; do
        kc -n nvidia-dra-driver-gpu get daemonset a40-consumable-provider -o json > "$PROVIDER_OUT/alternative-ready.json"
        kc -n nvidia-dra-driver-gpu get pods -o json > "$PROVIDER_OUT/alternative-pods.json"
        if python3 "$ISOLATION_DIR/common/analysis/control.py" provider-ready "$PROVIDER_OUT/alternative-ready.json" "$PROVIDER_OUT/alternative-pods.json"; then return 0
        else status=$?; (( status == 1 )) || return "$status"; fi
        (( SECONDS < deadline )) || die 'alternative A40 provider Pod did not become Ready'
        sleep 1
    done
}
printf 'Will back up the native NVIDIA DaemonSet, exclude dra-a40, and install a40-consumable-provider. The 4090 plugin is protected.\n'
kc -n nvidia-dra-driver-gpu get serviceaccount dra-driver-nvidia-gpu-service-account-kubeletplugin -o json > "$PROVIDER_OUT/provider-sa.json"
kc get clusterrole dra-driver-nvidia-gpu-clusterrole-kubeletplugin -o json > "$PROVIDER_OUT/provider-role.json"
kc get clusterrolebinding dra-driver-nvidia-gpu-clusterrole-binding-kubeletplugin -o json > "$PROVIDER_OUT/provider-binding.json"
python3 - "$PROVIDER_OUT" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); role=json.loads((p/'provider-role.json').read_text()); binding=json.loads((p/'provider-binding.json').read_text())
sa='dra-driver-nvidia-gpu-service-account-kubeletplugin'
if not any(s.get('kind')=='ServiceAccount' and s.get('name')==sa and s.get('namespace')=='nvidia-dra-driver-gpu' for s in binding['subjects']):
    raise SystemExit('original kubelet-plugin ServiceAccount binding differs')
if binding['roleRef']['name']!='dra-driver-nvidia-gpu-clusterrole-kubeletplugin': raise SystemExit('original RBAC roleRef differs')
required=[('resource.nvidia.com','computedomains',{'get','list','watch'}),('resource.k8s.io','resourceclaims',{'get','list','watch'}),
          ('resource.k8s.io','resourceslices',{'get','list','watch','create','update','delete'}),
          ('','nodes',{'get','list','watch','update','patch'}),('','pods',{'get','list','watch'})]
for group,resource,verbs in required:
    allowed=set()
    for rule in role['rules']:
        if group in rule['apiGroups'] and resource in rule['resources']: allowed.update(rule['verbs'])
    if not verbs <= allowed: raise SystemExit('original kubelet-plugin RBAC is missing '+resource+' permissions')
PY
kc -n nvidia-dra-driver-gpu get daemonset a40-consumable-provider --ignore-not-found -o json > "$PROVIDER_OUT/alternative-before.json"
if [[ -s "$PROVIDER_OUT/alternative-before.json" ]]; then
    python3 - "$PROVIDER_OUT/alternative-before.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); labels=d['metadata'].get('labels',{})
if labels.get('app.kubernetes.io/part-of')!='dra-book' or labels.get('app.kubernetes.io/component')!='consumable-provider':
    raise SystemExit('refusing to replace foreign a40-consumable-provider')
PY
    [[ -s "$PROVIDER_OUT/provider-backup/backup.json" ]] || die 'running alternative provider has no backup for this run-id'
    kc -n nvidia-dra-driver-gpu get daemonset dra-driver-nvidia-gpu-kubelet-plugin -o json > "$PROVIDER_OUT/native-current.json"
    kc get pods -A -o json > "$PROVIDER_OUT/pods-current.json"
    kc create --dry-run=client --validate=false -f "$SCRIPT_DIR/../manifests/provider/daemonset.yaml" -o json > "$PROVIDER_OUT/provider-expected.json"
    python3 - "$ISOLATION_DIR/provider" "$PROVIDER_OUT" "$ISOLATION_DIR/common/analysis/control.py" <<'PY'
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]); import control as c
p=Path(sys.argv[2]);original,state=c.read_backup(p/'provider-backup')
current=c.load(p/'native-current.json'); c.check_ds(current,original,state)
if c.canonical_spec(current['spec'])!=c.canonical_spec(c.excluded_spec(original['spec'],'dra-a40')):
    raise SystemExit('native provider is not excluded as recorded')
pods=c.load(p/'pods-current.json');c.check_protected(pods,original,state)
if c.owned_pods(pods,original,'dra-a40'): raise SystemExit('native A40 plugin still exists')
import importlib.util
spec=importlib.util.spec_from_file_location('runtime_control',sys.argv[3]);runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
runtime.verify_provider_spec(c.load(p/'alternative-before.json'),c.load(p/'provider-expected.json'))
PY
    wait_provider_ready
    kc get resourceslices -o json > "$PROVIDER_OUT/provider-slices.json"
    python3 "$ISOLATION_DIR/common/analysis/control.py" target "$PROVIDER_OUT/provider-slices.json" consumable-shares > "$PROVIDER_OUT/provider-target.json"
    printf 'Same provider already installed and verified; no cluster change.\n'
    exit 0
fi
bash "$ISOLATION_DIR/provider/backup.sh" "$1"
bash "$ISOLATION_DIR/provider/exclude-node.sh" dra-a40
printf 'Will apply the A40-only DaemonSet using the unchanged existing ServiceAccount and RBAC.\n'
kc apply -f "$SCRIPT_DIR/../manifests/provider/daemonset.yaml"
wait_provider_ready
deadline=$((SECONDS + 120))
while :; do
    kc get resourceslices -o json > "$PROVIDER_OUT/provider-slices.json"
    if python3 "$ISOLATION_DIR/common/analysis/control.py" target "$PROVIDER_OUT/provider-slices.json" consumable-shares > "$PROVIDER_OUT/provider-target.json"; then break; fi
    (( SECONDS < deadline )) || die 'ConsumableShares A40 ResourceSlice was not published'
    sleep 1
done
printf 'Provider ready. Run consumable-shares/scripts/run.sh with the same run-id.\n'
