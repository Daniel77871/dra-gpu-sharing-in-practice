#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
provider_guard
[[ $# == 1 ]] || die 'usage: exclude-node.sh <node>'
provider_output
provider_backup
[[ "$1" == "$TARGET_NODE" ]] || die 'node differs from the explicitly selected backup'
log "change: use OnDelete and exclude $TARGET_NODE in every original node-affinity term; remove only its recorded native plugin Pod and stale native ResourceSlices; preserve $PROTECTED_NODE"
provider_snapshot
python3 "$PROVIDER_HELPER" preflight "$BACKUP_DIR" exclude
# Validate the recorded Pod before changing any DaemonSet field.
python3 "$PROVIDER_HELPER" pod-delete "$BACKUP_DIR" > "$BACKUP_DIR/target-pod-uri.txt"
provider_patch exclude
provider_snapshot
python3 "$PROVIDER_HELPER" preflight "$BACKUP_DIR" exclude
python3 "$PROVIDER_HELPER" pod-delete "$BACKUP_DIR" > "$BACKUP_DIR/target-pod-uri.txt"
if [[ -s "$BACKUP_DIR/target-pod-uri.txt" ]]; then
    IFS= read -r pod_uri < "$BACKUP_DIR/target-pod-uri.txt"
    provider_idle target-plugin-delete
    log "change: delete recorded target plugin $pod_uri with UID/resourceVersion preconditions"
    if kc delete --raw "$pod_uri" -f "$BACKUP_DIR/target-pod-delete.json" > "$BACKUP_DIR/target-pod-delete-result.json" 2> "$BACKUP_DIR/target-pod-delete-error.txt"; then
        :
    else
        status=$?
        cat "$BACKUP_DIR/target-pod-delete-error.txt" >&2
        case "$(cat "$BACKUP_DIR/target-pod-delete-error.txt")" in
            *'Error from server (Conflict)'*|*'Error from server (NotFound)'*)
                provider_snapshot
                python3 "$PROVIDER_HELPER" preflight "$BACKUP_DIR" exclude
                python3 "$PROVIDER_HELPER" pod-delete-recheck "$BACKUP_DIR"
                log 'recorded target plugin is absent or already terminating; wait without retrying DELETE'
                ;;
            *) exit "$status" ;;
        esac
    fi
else
    log 'target plugin already removed by the DaemonSet controller'
fi
provider_wait native-absent exclude
# Node-owned slices can outlive the plugin Pod. Validate all pending deletions
# before issuing one, then use the same preconditions as the original run.
python3 "$PROVIDER_HELPER" slice-deletes "$BACKUP_DIR" > "$BACKUP_DIR/stale-slice-deletes.tsv"
provider_idle native-slices-delete
while IFS=$'\t' read -r slice_uri options; do
    [[ -n "$slice_uri" ]] || continue
    log "change: delete stale native slice $slice_uri with UID/resourceVersion preconditions"
    kc delete --raw "$slice_uri" -f "$options" > "$options.result.json"
done < "$BACKUP_DIR/stale-slice-deletes.tsv"
provider_wait slices-absent exclude
python3 "$PROVIDER_HELPER" record "$BACKUP_DIR" excluded
log "native provider excluded from $TARGET_NODE; $PROTECTED_NODE plugin UID unchanged"
