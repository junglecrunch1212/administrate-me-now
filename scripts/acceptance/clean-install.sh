#!/bin/bash
set -Eeuo pipefail
umask 077

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/adminme-clean-install.XXXXXX")"
cleanup() {
  [[ "$TEST_ROOT" == "${TMPDIR:-/tmp}"/adminme-clean-install.* ]] && rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

export ADMINME_TEST_MODE=1
export ADMINME_REPOSITORY_ROOT="$REPOSITORY_ROOT"
"$REPOSITORY_ROOT/bootstrap/install.sh" \
  --instance-root "$TEST_ROOT" \
  --dry-run \
  --fixture two-adult-one-child >/dev/null
"$REPOSITORY_ROOT/bootstrap/install.sh" \
  --instance-root "$TEST_ROOT" \
  --dry-run \
  --fixture two-adult-one-child >/dev/null
"$REPOSITORY_ROOT/bootstrap/doctor.sh" \
  --instance-root "$TEST_ROOT" >/dev/null
echo "ADMINISTRATEME CLEAN INSTALL: PASS"
