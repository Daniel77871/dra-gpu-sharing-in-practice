#!/usr/bin/env bash
set -euo pipefail
PROVIDER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$PROVIDER_DIR/.." && pwd)"
source "$LAB_DIR/../common/lib.sh"
readonly PROVIDER_NAMESPACE='nvidia-dra-driver-gpu'
readonly PROVIDER_DAEMONSET='dra-driver-nvidia-gpu-kubelet-plugin'
readonly PROVIDER_HELPER="$PROVIDER_DIR/control.py"

provider_guard() {
    [[ "${I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER:-}" == 1 ]] || die 'set I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1 for these lab-only provider operations'
    require_cmd python3
    require_cmd kubectl
}

provider_output() {
    local selected="${1:-}"
    if [[ -n "${OUT_DIR:-}" ]]; then
        [[ -d "$OUT_DIR" || ! -e "$OUT_DIR" ]] || die 'OUT_DIR is not a directory'
        mkdir -p "$OUT_DIR"
        OUT_DIR="$(cd "$OUT_DIR" && pwd)"
        export OUT_DIR
    else
        [[ -n "${RUN_ID:-$selected}" ]] || die 'select the backup explicitly with OUT_DIR or RUN_ID'
        init_run "$LAB_DIR" "${RUN_ID:-$selected}"
    fi
    BACKUP_DIR="$OUT_DIR/provider-backup"
    export BACKUP_DIR
    mkdir "$OUT_DIR/.provider-lock" 2>/dev/null || die 'another provider operation is active for this run directory'
    trap 'rmdir "$OUT_DIR/.provider-lock"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    PROVIDER_TIMEOUT="${PROVIDER_TIMEOUT:-300}"
    wait_timeout "$PROVIDER_TIMEOUT"
}

provider_backup() {
    [[ -f "$BACKUP_DIR/backup.json" ]] || die "complete provider backup missing: $BACKUP_DIR"
    python3 "$PROVIDER_HELPER" validate "$BACKUP_DIR"
    TARGET_NODE="$(python3 "$PROVIDER_HELPER" field "$BACKUP_DIR" target_node)"
    PROTECTED_NODE="$(python3 "$PROVIDER_HELPER" field "$BACKUP_DIR" protected_node)"
}

provider_snapshot() {
    kc -n "$PROVIDER_NAMESPACE" get daemonset "$PROVIDER_DAEMONSET" -o json > "$BACKUP_DIR/current-daemonset.json"
    kc get pods -A -o json > "$BACKUP_DIR/current-pods.json"
    kc get resourceslices.resource.k8s.io -o json > "$BACKUP_DIR/current-slices.json"
    kc get resourceclaims.resource.k8s.io -A -o json > "$BACKUP_DIR/current-claims.json"
    kc get daemonsets.apps -A -o json > "$BACKUP_DIR/current-daemonsets.json"
    kc get node "$TARGET_NODE" -o json > "$BACKUP_DIR/current-node.json"
}

provider_patch() {
    local stage="$1" status
    if python3 "$PROVIDER_HELPER" patch "$BACKUP_DIR" "$stage"; then
        provider_idle "$stage"
        log "change: patch $PROVIDER_NAMESPACE/$PROVIDER_DAEMONSET using $BACKUP_DIR/$stage-patch.json (UID/spec preconditions)"
        kc -n "$PROVIDER_NAMESPACE" patch daemonset "$PROVIDER_DAEMONSET" --type=json --patch-file "$BACKUP_DIR/$stage-patch.json" -o json > "$BACKUP_DIR/$stage-result.json"
    else
        status=$?
        [[ "$status" == 1 ]] || return "$status"
        log "already at provider stage: $stage"
    fi
}

provider_idle() {
    local stage="$1" pod
    pod="$(driver_pod "$TARGET_NODE")"
    kc -n "$DRIVER_NAMESPACE" exec "$pod" -c "$DRIVER_CONTAINER" -- nvidia-smi --query-gpu=uuid,name,memory.total,memory.used,memory.free,utilization.gpu,compute_mode --format=csv,noheader,nounits > "$BACKUP_DIR/$stage-gpu-idle.csv"
    kc -n "$DRIVER_NAMESPACE" exec "$pod" -c "$DRIVER_CONTAINER" -- nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits > "$BACKUP_DIR/$stage-compute-apps.csv"
    python3 "$PROVIDER_HELPER" idle "$BACKUP_DIR" "$BACKUP_DIR/$stage-gpu-idle.csv" "$BACKUP_DIR/$stage-compute-apps.csv"
}

provider_wait() {
    local condition="$1" mode="$2" deadline=$((SECONDS + PROVIDER_TIMEOUT)) status
    local timeout_message="${3:-timeout waiting for provider: $condition}"
    while :; do
        provider_snapshot
        python3 "$PROVIDER_HELPER" preflight "$BACKUP_DIR" "$mode"
        if python3 "$PROVIDER_HELPER" "$condition" "$BACKUP_DIR"; then return 0; else status=$?; fi
        [[ "$status" == 1 ]] || return "$status"
        (( SECONDS < deadline )) || die "$timeout_message"
        sleep "${WAIT_INTERVAL:-2}"
    done
}
