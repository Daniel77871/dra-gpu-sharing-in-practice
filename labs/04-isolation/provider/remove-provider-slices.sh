#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
provider_guard
[[ $# -le 1 ]] || die 'usage: remove-provider-slices.sh [node] (select OUT_DIR or RUN_ID; optional DRIVER)'
provider_output
provider_backup
[[ "${1:-$TARGET_NODE}" == "$TARGET_NODE" ]] || die 'node differs from the explicitly selected backup'
SLICE_DRIVER="${DRIVER:-gpu.nvidia.com}"
log "change: remove only stale $SLICE_DRIVER ResourceSlices for $TARGET_NODE after all target providers are absent; keep original NVIDIA provider excluded and preserve $PROTECTED_NODE"
provider_wait native-cleanup-complete remove-wait "original native target plugin or ResourceSlices did not disappear within ${PROVIDER_TIMEOUT}s; inspect the terminating Pod and original slices, complete exclude-node.sh $TARGET_NODE using the same OUT_DIR=$OUT_DIR, then retry uninstall"
python3 "$PROVIDER_HELPER" preflight "$BACKUP_DIR" remove
provider_idle alternative-slices-delete
python3 "$PROVIDER_HELPER" capture-alternative-slices "$BACKUP_DIR" "$SLICE_DRIVER"
# Capture before deleting, then re-read UID/spec to reject concurrent publication.
provider_snapshot
python3 "$PROVIDER_HELPER" preflight "$BACKUP_DIR" remove
python3 "$PROVIDER_HELPER" alternative-slice-deletes "$BACKUP_DIR" > "$BACKUP_DIR/alternative-slice-deletes.tsv"
while IFS=$'\t' read -r slice_uri options; do
    [[ -n "$slice_uri" ]] || continue
    log "change: delete stale alternative slice $slice_uri with UID/resourceVersion preconditions"
    kc delete --raw "$slice_uri" -f "$options" > "$options.result.json"
done < "$BACKUP_DIR/alternative-slice-deletes.tsv"
provider_wait slices-absent remove
log "alternative provider ResourceSlices removed from $TARGET_NODE; original provider remains excluded"
