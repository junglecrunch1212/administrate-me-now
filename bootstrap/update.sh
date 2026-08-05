#!/bin/bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=bootstrap/common.sh
source "$SCRIPT_DIR/common.sh"
resolve_instance_root
candidate="${1:-}"
[[ -n "$candidate" ]] || fail "usage: update.sh /absolute/path/to/reviewed-candidate"
[[ "$candidate" == /* ]] || fail "candidate path must be absolute"
run_cli upgrade "$candidate"
