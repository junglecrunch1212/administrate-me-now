#!/bin/bash
set -Eeuo pipefail
umask 077

[[ "$(uname -s)" == "Darwin" ]] || { echo "macOS rehearsal requires Darwin" >&2; exit 2; }
REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/adminme-macos-rehearsal.XXXXXX")"
cleanup() {
  [[ "$TEST_ROOT" == "${TMPDIR:-/tmp}"/adminme-macos-rehearsal.* ]] && rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

export ADMINME_TEST_MODE=1
export ADMINME_REPOSITORY_ROOT="$REPOSITORY_ROOT"
for section in 1 2 3 4 5 6 7 8 9 10; do
  python3 -m bootstrap.cli --instance-root "$TEST_ROOT" \
    bootstrap --dry-run --fixture two-adult-one-child \
    --stop-after-section "$section" >/dev/null
done
python3 -m pytest -q \
  tests/unit/bootstrap/test_phase5_operations.py \
  tests/acceptance/test_macos_bootstrap_operations.py
plutil -lint "$TEST_ROOT/launchd/com.administrateme.runtime.plist"
plutil -lint "$TEST_ROOT/launchd/com.administrateme.openclaw.plist"
echo "ADMINISTRATEME MACOS REHEARSAL: PASS ($(uname -m))"
