#!/usr/bin/env bash
set -euo pipefail
[[ "${I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER:-}" == 1 ]] || {
    printf 'Set I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1; this restores the A40 provider.\n' >&2; exit 1;
}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISOLATION_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$ISOLATION_DIR/../common/lib.sh"
[[ $# -eq 1 ]] || die 'usage: uninstall-provider.sh <run-id>'
init_run "$ISOLATION_DIR" "$1"
[[ -s "$OUT_DIR/provider-backup/original-daemonset.json" ]] || die 'original provider backup missing'
printf 'Will remove only the labelled ConsumableShares provider, remove its stale A40 Slice, and restore the backed-up native DaemonSet.\n'
kc get resourceclaims -A -o json > "$OUT_DIR/claims-before-uninstall.json"
python3 - "$OUT_DIR/claims-before-uninstall.json" <<'PY'
import json,sys
for c in json.load(open(sys.argv[1]))['items']:
    allocations=(c.get('status',{}).get('allocation') or {}).get('devices',{}).get('results',[])
    if c['metadata'].get('namespace')=='gpu-isolation-lab' or any(r.get('pool')=='dra-a40' for r in allocations):
        raise SystemExit('Claims remain; collect and cleanup workloads before uninstalling provider')
PY
kc -n nvidia-dra-driver-gpu get daemonset dra-driver-nvidia-gpu-kubelet-plugin -o json > "$OUT_DIR/native-before-uninstall.json"
if python3 - "$ISOLATION_DIR/provider" "$OUT_DIR" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import control as c
p=Path(sys.argv[2]);original,state=c.read_backup(p/'provider-backup');current=c.load(p/'native-before-uninstall.json')
try: phase=c.check_ds(current,original,state)
except (ValueError,KeyError) as error:
    print(str(error),file=sys.stderr);raise SystemExit(2)
raise SystemExit(0 if phase in ('original','resumed') else 1)
PY
then
    bash "$ISOLATION_DIR/provider/restore.sh"
    printf 'Native provider already restored and verified.\n'
    exit 0
else
    status=$?
    (( status == 1 )) || die 'native DaemonSet identity/spec changed; refusing provider removal'
fi
kc get pods -A -o json > "$OUT_DIR/pods-before-uninstall.json"
python3 - "$ISOLATION_DIR/provider" "$OUT_DIR" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import control as c
p=Path(sys.argv[2]);original,state=c.read_backup(p/'provider-backup')
c.check_protected(c.load(p/'pods-before-uninstall.json'),original,state)
PY
driver="$(driver_pod dra-a40)"
kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- nvidia-smi --query-gpu=uuid,name,memory.total,memory.used,memory.free,utilization.gpu,compute_mode --format=csv,noheader,nounits > "$OUT_DIR/uninstall-gpu-idle.csv"
kc -n "$DRIVER_NAMESPACE" exec "$driver" -c "$DRIVER_CONTAINER" -- nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits > "$OUT_DIR/uninstall-compute-apps.csv"
python3 "$ISOLATION_DIR/provider/control.py" idle "$OUT_DIR/provider-backup" "$OUT_DIR/uninstall-gpu-idle.csv" "$OUT_DIR/uninstall-compute-apps.csv"
kc -n nvidia-dra-driver-gpu delete daemonsets -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=consumable-provider" --ignore-not-found --wait=true --timeout=120s
deadline=$((SECONDS + 120))
while :; do
    pods="$(kc -n nvidia-dra-driver-gpu get pods -l "$BOOK_SELECTOR,$COMPONENT_LABEL_KEY=consumable-provider" -o name)"
    [[ -n "$pods" ]] || break
    (( SECONDS < deadline )) || die 'alternative provider Pod still exists'
    sleep 1
done
DRIVER=gpu.nvidia.com bash "$ISOLATION_DIR/provider/remove-provider-slices.sh" dra-a40
bash "$ISOLATION_DIR/provider/restore.sh"
printf 'Native NVIDIA provider restored and verified; backup retained.\n'
