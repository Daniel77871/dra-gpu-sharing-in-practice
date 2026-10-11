#!/usr/bin/env bash
set -euo pipefail
[[ "${I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER:-}" == 1 ]] || { printf 'Set I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1; this changes both GPU providers and host files.\n' >&2; exit 1; }
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# == 1 ]] || { printf 'usage: install-provider.sh <provider-run-id>\n' >&2; exit 1; }
export NODES="${NODES:-dra-a40 dra-4090}"
export OUT_DIR="$LAB_DIR/out/provider-$1"
exec bash "$LAB_DIR/../04-isolation/hami-dra/scripts/install-provider-nodes.sh" "$1"
