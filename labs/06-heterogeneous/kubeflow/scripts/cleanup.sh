#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# This is a cluster-wide installation cleanup; the uninstall guard is required.
exec bash "$script_dir/uninstall-kfp.sh"
