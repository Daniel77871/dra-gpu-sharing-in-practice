#!/usr/bin/env bash
set -euo pipefail
[[ "${I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER:-}" == 1 ]] || {
    printf 'Set I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1; this removes HAMi host files and restores the A40 provider.\n' >&2; exit 1;
}
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ $# -eq 1 ]] || die 'usage: uninstall-provider.sh <provider-run-id>'
open_run "$1"
[[ -s "$RUN_OUT/provider-backup/backup.json" && -s "$RUN_OUT/host-before.json" && -s "$RUN_OUT/host-installed.json" ]] || die 'original provider and HAMi host snapshots are required'
mkdir "$RUN_OUT/.hami-provider-lock" 2>/dev/null || die 'another HAMi provider operation uses this directory'
native_restored=0
cleanup_stage=''
uninstall_exit() {
    local status=$?
    trap - EXIT
    if (( status != 0 && native_restored == 1 )); then
        printf 'Original NVIDIA provider UID/spec and protected 4090 plugin were restored and verified. %s failed; snapshots retained. Retry uninstall-provider.sh with the same provider-run-id.\n' "$cleanup_stage" >&2
    fi
    host_stop || status=1
    rmdir "$RUN_OUT/.hami-provider-lock" || status=1
    exit "$status"
}
trap uninstall_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Will remove the owned HAMi provider and slices, restore and verify the original NVIDIA DaemonSet UID/spec, then clean HAMi DeviceClass/RBAC and host artifacts.\n'
kc get resourceclaims -A -o json > "$RUN_OUT/claims-before-uninstall.json"
python3 - "$RUN_OUT/claims-before-uninstall.json" <<'PY'
import json,sys
for claim in json.load(open(sys.argv[1]))['items']:
    rows=(claim.get('status',{}).get('allocation') or {}).get('devices',{}).get('results',[])
    if claim['metadata'].get('namespace')=='gpu-isolation-lab' or any(row.get('pool')=='dra-a40' for row in rows):
        raise SystemExit('Claims remain; collect and cleanup all GPU jobs before uninstalling')
PY
kc -n nvidia-dra-driver-gpu get daemonset dra-driver-nvidia-gpu-kubelet-plugin -o json > "$RUN_OUT/native-current.json"
kc get pods -A -o json > "$RUN_OUT/pods-current.json"
native_phase="$(python3 - "$ISOLATION_DIR/provider" "$RUN_OUT" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import control as c
p=Path(sys.argv[2]);original,state=c.read_backup(p/'provider-backup')
print(c.check_ds(c.load(p/'native-current.json'),original,state));c.check_protected(c.load(p/'pods-current.json'),original,state)
PY
)"
kc_get_list "$RUN_OUT/provider-before-uninstall.json" -f "$RUN_OUT/provider-expected.json"
helper provider-set "$RUN_OUT/provider-before-uninstall.json" "$RUN_OUT/provider-expected.json" "$RUN_ID"
python3 - "$RUN_OUT" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);recorded={(i['kind'],i['metadata']['name']):i['metadata']['uid'] for i in json.load(open(p/'provider-owned.json'))['items']}
for item in json.load(open(p/'provider-before-uninstall.json'))['items']:
    if recorded.get((item['kind'],item['metadata']['name']))!=item['metadata']['uid']:
        raise SystemExit('provider object UID changed; refusing deletion')
PY
driver="$(driver_pod "$GPU_NODE")"
kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- nvidia-smi --query-gpu=uuid,name,memory.total,memory.used,memory.free,utilization.gpu,compute_mode --format=csv,noheader,nounits > "$RUN_OUT/uninstall-gpu-idle.csv"
kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits > "$RUN_OUT/uninstall-compute-apps.csv"
python3 "$ISOLATION_DIR/provider/control.py" idle "$RUN_OUT/provider-backup" "$RUN_OUT/uninstall-gpu-idle.csv" "$RUN_OUT/uninstall-compute-apps.csv"
kc -n nvidia-dra-driver-gpu delete daemonsets -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=hami-provider,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true --timeout=120s
deadline=$((SECONDS + 120))
while :; do
    remaining="$(kc -n nvidia-dra-driver-gpu get pods -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=hami-provider,app.kubernetes.io/instance=$RUN_ID" -o name)"
    [[ -n "$remaining" ]] || break
    (( SECONDS < deadline )) || die 'HAMi provider Pod remains'
    sleep 1
done
if [[ "$native_phase" == excluded ]]; then
    DRIVER=hami-core-gpu.project-hami.io bash "$ISOLATION_DIR/provider/remove-provider-slices.sh" "$GPU_NODE"
fi
bash "$ISOLATION_DIR/provider/restore.sh"
native_restored=1
cleanup_stage='HAMi DeviceClass/RBAC cleanup'
kc delete deviceclasses -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=hami-device-class,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true
kc delete clusterrolebindings,clusterroles -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=hami-provider-rbac,app.kubernetes.io/instance=$RUN_ID" --ignore-not-found --wait=true
cleanup_stage='HAMi host cleanup'
host_start
if [[ ! -f "$RUN_OUT/host-cleanup.verified" ]]; then
    host_footprint > "$RUN_OUT/host-after-provider-stop.json"
    payload="$(python3 - "$RUN_OUT" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);print(json.dumps({'operation':'remove-hooks','before':json.load(open(p/'host-before.json')),
    'installed':json.load(open(p/'host-installed.json')),'checked':json.load(open(p/'host-after-provider-stop.json'))}))
PY
)"
    host_call "$payload" > "$RUN_OUT/host-restored-clean.json"
    python3 - "$RUN_OUT/host-restored-clean.json" <<'PY'
import json,sys
value=json.load(open(sys.argv[1]))
if any(value[k] for k in ('/usr/local/vgpu','/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io','/tmp/vgpulock')):
    raise SystemExit('HAMi host artifacts remain after cleanup')
PY
    printf 'verified\n' > "$RUN_OUT/host-cleanup.verified"
fi
host_footprint > "$RUN_OUT/host-restored-clean.json"
python3 - "$RUN_OUT/host-restored-clean.json" <<'PY'
import json,sys
value=json.load(open(sys.argv[1]))
if any(value[k] for k in ('/usr/local/vgpu','/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io','/tmp/vgpulock')):
    raise SystemExit('HAMi host artifacts are present; refusing a successful restore claim')
PY
printf 'Original NVIDIA provider UID/spec and protected 4090 plugin verified; HAMi host artifacts removed.\n'
