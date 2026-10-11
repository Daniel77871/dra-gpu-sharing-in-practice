#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
provider_guard
[[ $# == 0 ]] || die 'usage: restore.sh (select OUT_DIR or RUN_ID)'
provider_output
provider_backup
log "change: restore $PROVIDER_NAMESPACE/$PROVIDER_DAEMONSET from the selected backup in two stages; refuse remaining alternative providers, slices and ResourceClaims; preserve $PROTECTED_NODE"
provider_snapshot
python3 "$PROVIDER_HELPER" preflight "$BACKUP_DIR" restore
provider_patch resume
provider_wait ready restore
# Returning the original template under OnDelete first avoids replacing the
# protected plugin while the target plugin is recreated and becomes Ready.
provider_patch strategy
provider_wait ready restore
python3 "$PROVIDER_HELPER" record "$BACKUP_DIR" restored
kc -n "$PROVIDER_NAMESPACE" get daemonset "$PROVIDER_DAEMONSET" -o yaml > "$BACKUP_DIR/restored-daemonset.yaml"
log "original provider restored: UID/spec/updateStrategy match backup; native ResourceSlices returned; $PROTECTED_NODE plugin UID unchanged"
