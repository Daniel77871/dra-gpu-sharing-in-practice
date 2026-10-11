#!/usr/bin/env bash
set -euo pipefail
[[ "${I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER:-}" == 1 ]] || {
    printf 'Set I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1; this switches the A40 provider and writes host files.\n' >&2; exit 1;
}
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ $# -eq 1 ]] || die 'usage: install-provider.sh <provider-run-id>'
open_run "$1"
[[ ! -f "$RUN_OUT/host-cleanup.verified" ]] || die 'this provider-run-id was already restored; choose a new provider-run-id'
mkdir "$RUN_OUT/.hami-provider-lock" 2>/dev/null || die 'another HAMi provider operation uses this directory'
install_exit() {
    local status=$?
    trap - EXIT
    if (( status != 0 )) && [[ -s "$RUN_OUT/provider-expected.json" ]]; then
        if kc_get_list "$RUN_OUT/provider-failed.json" -f "$RUN_OUT/provider-expected.json" &&
           helper provider-set "$RUN_OUT/provider-failed.json" "$RUN_OUT/provider-expected.json" "$RUN_ID"; then
            [[ -f "$RUN_OUT/provider-owned.json" ]] || cp "$RUN_OUT/provider-failed.json" "$RUN_OUT/provider-owned.json"
        fi
    fi
    if (( status != 0 )) && [[ -n "${HOST_POD:-}" ]]; then
        # Preserve evidence from a partially started init container. An unknown
        # later file is still refused by remove_hooks; retry this install to
        # finish initialization before attempting restoration in that case.
        if host_footprint > "$RUN_OUT/host-install-failed.json.tmp"; then
            mv "$RUN_OUT/host-install-failed.json.tmp" "$RUN_OUT/host-install-failed.json"
            [[ -f "$RUN_OUT/host-installed.json" ]] || cp "$RUN_OUT/host-install-failed.json" "$RUN_OUT/host-installed.json"
        fi
        printf 'Installation stopped; snapshots retained. Retry this provider-run-id, or use uninstall-provider.sh to restore the recorded state.\n' >&2
    fi
    host_stop || status=1
    rmdir "$RUN_OUT/.hami-provider-lock" || status=1
    exit "$status"
}
trap install_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Will back up the native provider, exclude dra-a40, and install the pinned HAMi provider. The 4090 plugin is protected.\n'
kc -n nvidia-dra-driver-gpu get serviceaccount dra-driver-nvidia-gpu-service-account-kubeletplugin -o json > "$RUN_OUT/provider-sa.json"
kc get clusterrole dra-driver-nvidia-gpu-clusterrole-kubeletplugin -o json > "$RUN_OUT/provider-native-role.json"
kc get clusterrolebinding dra-driver-nvidia-gpu-clusterrole-binding-kubeletplugin -o json > "$RUN_OUT/provider-native-binding.json"
python3 - "$RUN_OUT" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);role=json.load(open(p/'provider-native-role.json'));binding=json.load(open(p/'provider-native-binding.json'))
sa={'kind':'ServiceAccount','name':'dra-driver-nvidia-gpu-service-account-kubeletplugin','namespace':'nvidia-dra-driver-gpu'}
if sa not in binding.get('subjects',[]) or binding['roleRef']['name']!=role['metadata']['name']:
    raise SystemExit('native ServiceAccount/RBAC identity differs')
required=[('resource.nvidia.com','computedomains',{'get','list','watch'}),('resource.k8s.io','resourceclaims',{'get','list','watch'}),
          ('resource.k8s.io','resourceslices',{'get','list','watch','create','update','delete'}),
          ('','nodes',{'get','list','watch','update','patch'}),('','pods',{'get','list','watch'})]
for group,resource,verbs in required:
    allowed=set()
    for rule in role['rules']:
        if group in rule['apiGroups'] and resource in rule['resources']: allowed.update(rule['verbs'])
    if not verbs<=allowed: raise SystemExit('native RBAC is missing '+resource)
PY
kc kustomize "$HAMI_DIR/manifests/provider" > "$RUN_OUT/provider-input.yaml"
kc create --dry-run=client --validate=false -f "$RUN_OUT/provider-input.yaml" -o json > "$RUN_OUT/provider-rendered.json"
helper provider-render "$RUN_OUT/provider-rendered.json" "$RUN_OUT/provider-expected.json" "$RUN_ID"
kc_get_list "$RUN_OUT/provider-existing.json" -f "$RUN_OUT/provider-expected.json"
helper provider-set "$RUN_OUT/provider-existing.json" "$RUN_OUT/provider-expected.json" "$RUN_ID"
if [[ -f "$RUN_OUT/provider-owned.json" ]]; then
    python3 - "$RUN_OUT" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);old={(x['kind'],x['metadata']['name']):x['metadata']['uid'] for x in json.load(open(p/'provider-owned.json'))['items']}
for item in json.load(open(p/'provider-existing.json'))['items']:
    key=(item['kind'],item['metadata']['name'])
    if key in old and old[key]!=item['metadata']['uid']: raise SystemExit('provider object UID changed; refusing to adopt it')
PY
fi
host_start
if [[ ! -f "$RUN_OUT/host-before.json" ]]; then
    host_call '{"operation":"assert-empty"}' > "$RUN_OUT/host-before.json.tmp"
    mv "$RUN_OUT/host-before.json.tmp" "$RUN_OUT/host-before.json"
fi
kc -n nvidia-dra-driver-gpu get daemonset a40-hami-provider --ignore-not-found -o json > "$RUN_OUT/alternative-before.json"
if [[ -s "$RUN_OUT/alternative-before.json" ]]; then
    [[ -f "$RUN_OUT/provider-backup/backup.json" ]] || die 'running HAMi provider has no original backup in this directory'
    kc -n nvidia-dra-driver-gpu get daemonset dra-driver-nvidia-gpu-kubelet-plugin -o json > "$RUN_OUT/native-current.json"
    kc get pods -A -o json > "$RUN_OUT/pods-current.json"
    helper native-excluded "$RUN_OUT"
else
    bash "$ISOLATION_DIR/provider/backup.sh" "$RUN_ID"
    bash "$ISOLATION_DIR/provider/exclude-node.sh" "$GPU_NODE"
fi
# Reapply the complete owned set, including after a partially successful apply.
kc apply -f "$RUN_OUT/provider-expected.json"
kc get -f "$RUN_OUT/provider-expected.json" -o json > "$RUN_OUT/provider-owned.json"
helper provider-set "$RUN_OUT/provider-owned.json" "$RUN_OUT/provider-expected.json" "$RUN_ID"
deadline=$((SECONDS + 300))
while :; do
    kc -n nvidia-dra-driver-gpu get daemonset a40-hami-provider -o json > "$RUN_OUT/alternative-ready.json"
    kc -n nvidia-dra-driver-gpu get pods -o json > "$RUN_OUT/alternative-pods.json"
    if helper provider-ready "$RUN_OUT/alternative-ready.json" "$RUN_OUT/alternative-pods.json"; then break
    else status=$?; (( status == 1 )) || exit "$status"; fi
    (( SECONDS < deadline )) || die 'HAMi A40 provider did not become Ready'
    sleep 1
done
deadline=$((SECONDS + 120))
while :; do
    kc get resourceslices -o json > "$RUN_OUT/provider-slices.json"
    kc get deviceclass "$HAMI_DEVICE_CLASS" -o json > "$RUN_OUT/provider-deviceclass.json"
    if helper target "$RUN_OUT/provider-slices.json" hami "$RUN_OUT/provider-deviceclass.json" > "$RUN_OUT/provider-target.json"; then break; fi
    (( SECONDS < deadline )) || die 'HAMi A40 ResourceSlice was not published'
    sleep 1
done
host_footprint > "$RUN_OUT/host-install-ready.json"
python3 - "$RUN_OUT/host-install-ready.json" <<'PY'
import json,sys
rows=json.load(open(sys.argv[1]))['/usr/local/vgpu']
lib=next((r for r in rows if r['path']=='/usr/local/vgpu/libvgpu.so'),{})
if lib.get('sha256')!='56fc0944896337cc0ee439cbc7364105ccac11e8dc38add6a3dc467cea34b18f':
    raise SystemExit('installed libvgpu.so SHA256 differs')
PY
if [[ ! -f "$RUN_OUT/provider-installed.verified" ]]; then
    cp "$RUN_OUT/host-install-ready.json" "$RUN_OUT/host-installed.json"
    printf 'verified\n' > "$RUN_OUT/provider-installed.verified"
fi
printf 'HAMi provider verified. DeviceClass: %s. Use the same provider-run-id for uninstall.\n' "$HAMI_DEVICE_CLASS"
