#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
provider_guard
[[ $# -le 1 ]] || die 'usage: backup.sh [run-id]'
provider_output "${1:-}"
if [[ -f "$BACKUP_DIR/backup.json" ]]; then
    python3 "$PROVIDER_HELPER" validate "$BACKUP_DIR"
    log "keeping existing original provider backup: $BACKUP_DIR"
    exit 0
fi
[[ ! -e "$BACKUP_DIR" ]] || die 'incomplete provider backup exists; keep it for diagnosis and choose a new run-id'
TARGET_NODE="${GPU_NODE:-dra-a40}"
PROTECTED_NODE="${PROTECTED_NODE:-dra-4090}"
log "change: write original provider evidence to $BACKUP_DIR; no cluster object will change"
mkdir "$BACKUP_DIR"
kc -n "$PROVIDER_NAMESPACE" get daemonset "$PROVIDER_DAEMONSET" -o json > "$BACKUP_DIR/original-daemonset.json"
kc -n "$PROVIDER_NAMESPACE" get daemonset "$PROVIDER_DAEMONSET" -o yaml > "$BACKUP_DIR/original-daemonset.yaml"
kc get pods -A -o json > "$BACKUP_DIR/original-pods.json"
kc get resourceslices.resource.k8s.io -o json > "$BACKUP_DIR/original-slices.json"
kc get node "$TARGET_NODE" -o json > "$BACKUP_DIR/original-node.json"
python3 "$PROVIDER_HELPER" initialize "$BACKUP_DIR" "$TARGET_NODE" "$PROTECTED_NODE"
log "original provider backed up; target=$TARGET_NODE protected=$PROTECTED_NODE"
