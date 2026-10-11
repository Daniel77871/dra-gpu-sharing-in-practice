#!/usr/bin/env bash
# Source from lab scripts. All kubectl calls go through kc for the run ledger.
set -euo pipefail

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly BOOK_LABEL_KEY='app.kubernetes.io/part-of'
readonly BOOK_LABEL_VALUE='dra-book'
readonly BOOK_SELECTOR="${BOOK_LABEL_KEY}=${BOOK_LABEL_VALUE}"
readonly COMPONENT_LABEL_KEY='app.kubernetes.io/component'
# Grounded in the original inventory; see .work/ledger/U0.md.
readonly DRIVER_NAMESPACE='gpu-operator'
readonly DRIVER_SELECTOR='app=nvidia-driver-daemonset'
readonly DRIVER_CONTAINER='nvidia-driver-ctr'

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "required command: $1"; }
utc_run_id() { printf '%s-%s\n' "$(date -u +%Y%m%dT%H%M%SZ)" "$$"; }

init_run() {
    local lab_dir="$1"
    RUN_ID="${2:-$(utc_run_id)}"
    [[ "$RUN_ID" =~ ^[A-Za-z0-9._-]+$ && "$RUN_ID" != '.' && "$RUN_ID" != '..' ]] || die 'invalid run id'
    mkdir -p "$lab_dir/out/$RUN_ID"
    OUT_DIR="$(cd "$lab_dir/out/$RUN_ID" && pwd)"
    export RUN_ID OUT_DIR
    touch "$OUT_DIR/commands.log"
    log "output: $OUT_DIR"
}

ensure_out_dir() {
    if [[ -z "${OUT_DIR:-}" ]]; then
        init_run "$COMMON_DIR"
    else
        mkdir -p "$OUT_DIR"
        export OUT_DIR
    fi
}

kc() (
    require_cmd kubectl
    ensure_out_dir
    local status stamp
    stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    {
        printf '%s pid=%s command=' "$stamp" "${BASHPID:-$$}"
        printf '%q ' kubectl "--request-timeout=${KC_REQUEST_TIMEOUT:-25s}" "$@"
        printf '\n'
    } >> "$OUT_DIR/commands.log"
    trap 'printf "%s pid=%s exit=143\n" "$stamp" "${BASHPID:-$$}" >> "$OUT_DIR/commands.log"; exit 143' TERM
    trap 'printf "%s pid=%s exit=130\n" "$stamp" "${BASHPID:-$$}" >> "$OUT_DIR/commands.log"; exit 130' INT
    if command kubectl "--request-timeout=${KC_REQUEST_TIMEOUT:-25s}" "$@"; then
        status=0
    else
        status=$?
    fi
    printf '%s pid=%s exit=%s\n' "$stamp" "${BASHPID:-$$}" "$status" >> "$OUT_DIR/commands.log"
    return "$status"
)

wait_timeout() {
    [[ "$1" =~ ^[0-9]+$ ]] || die 'timeout must be a nonnegative number of seconds'
}

wait_pod_phase() {
    local namespace="$1" name="$2" desired="$3" timeout="${4:-300}" phase deadline
    wait_timeout "$timeout"
    deadline=$((SECONDS + timeout))
    while :; do
        if phase="$(kc -n "$namespace" get pod "$name" -o 'jsonpath={.status.phase}')"; then
            [[ "$phase" == "$desired" ]] && return 0
            if [[ "$phase" == 'Failed' || "$phase" == 'Succeeded' ]]; then
                log "Pod $namespace/$name ended in $phase, expected $desired"; return 1
            fi
        fi
        if (( SECONDS >= deadline )); then log "timeout: Pod $namespace/$name phase $desired"; return 1; fi
        sleep "${WAIT_INTERVAL:-2}"
    done
}

wait_job_complete() {
    local namespace="$1" name="$2" timeout="${3:-600}" conditions deadline
    wait_timeout "$timeout"
    deadline=$((SECONDS + timeout))
    while :; do
        if conditions="$(kc -n "$namespace" get job "$name" -o 'jsonpath={range .status.conditions[*]}{.type}={.status}{"\n"}{end}')"; then
            if [[ "$conditions" == *'Failed=True'* ]]; then log "Job failed: $namespace/$name"; return 1; fi
            [[ "$conditions" == *'Complete=True'* ]] && return 0
        fi
        if (( SECONDS >= deadline )); then log "timeout: Job $namespace/$name completion"; return 1; fi
        sleep "${WAIT_INTERVAL:-2}"
    done
}

wait_claim_allocated() {
    local namespace="$1" name="$2" timeout="${3:-300}" devices deadline
    wait_timeout "$timeout"
    deadline=$((SECONDS + timeout))
    while :; do
        if devices="$(kc -n "$namespace" get resourceclaims.resource.k8s.io "$name" -o 'jsonpath={.status.allocation.devices.results[*].device}')"; then
            [[ -n "$devices" ]] && return 0
        fi
        if (( SECONDS >= deadline )); then log "timeout: ResourceClaim $namespace/$name allocation"; return 1; fi
        sleep "${WAIT_INTERVAL:-2}"
    done
}

driver_pod() {
    require_cmd python3
    kc -n "$DRIVER_NAMESPACE" get pods -l "$DRIVER_SELECTOR" --field-selector "spec.nodeName=$1" -o json |
        python3 "$COMMON_DIR/gpu_check.py" driver "$1" "$DRIVER_CONTAINER"
}
