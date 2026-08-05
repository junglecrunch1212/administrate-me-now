#!/bin/bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=bootstrap/common.sh
source "$SCRIPT_DIR/common.sh"
run_cli status "$@"
