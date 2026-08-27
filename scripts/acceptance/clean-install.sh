#!/bin/bash
set -Eeuo pipefail
umask 077

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
ADMINME_ACCEPTANCE_PYTHON="$(command -v python)"
export ADMINME_ACCEPTANCE_PYTHON
TEST_PARENT="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
TEST_ROOT="$(mktemp -d "$TEST_PARENT/adminme-clean-install.XXXXXX")"
cleanup() {
  [[ "$TEST_ROOT" == "$TEST_PARENT"/adminme-clean-install.* ]] && rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

export ADMINME_REPOSITORY_ROOT="$REPOSITORY_ROOT"
candidate_commit="$(/usr/bin/git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
run_fixed_fixture_bootstrap() {
  /usr/bin/git -C "$REPOSITORY_ROOT" show \
    "$candidate_commit:bootstrap/update_supervisor.py" \
    | /usr/bin/python3 -I - \
      "$TEST_ROOT" "$REPOSITORY_ROOT" "$candidate_commit" \
      --bootstrap --test-mode --dry-run --fixture two-adult-one-child \
      >/dev/null
}
run_fixed_fixture_bootstrap
run_fixed_fixture_bootstrap
ADMINME_TEST_MODE=1 "$ADMINME_ACCEPTANCE_PYTHON" -I -c '
import runpy
import sys
sys.path.insert(0, sys.argv[1])
sys.argv = ["bootstrap.cli", "--instance-root", sys.argv[2], "doctor"]
runpy.run_module("bootstrap.cli", run_name="__main__")
' "$REPOSITORY_ROOT" "$TEST_ROOT" >/dev/null
echo "ADMINISTRATEME CLEAN INSTALL: PASS"
