#!/bin/bash
set -Eeuo pipefail
umask 077

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/adminme-backup-restore.XXXXXX")"
cleanup() {
  [[ "$TEST_ROOT" == "${TMPDIR:-/tmp}"/adminme-backup-restore.* ]] && rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

export ADMINME_TEST_MODE=1
export ADMINME_REPOSITORY_ROOT="$REPOSITORY_ROOT"
python3 -m bootstrap.cli --instance-root "$TEST_ROOT" \
  bootstrap --dry-run --fixture two-adult-one-child >/dev/null
python3 -m bootstrap.cli --instance-root "$TEST_ROOT" backup >/dev/null
backup_file="$(find "$TEST_ROOT/backups" -maxdepth 1 -type f -name '*.ambackup' -print -quit)"
[[ -n "$backup_file" ]] || { echo "encrypted backup was not created" >&2; exit 1; }
python3 -m bootstrap.cli --instance-root "$TEST_ROOT" restore "$backup_file" >/dev/null
python3 -m bootstrap.cli --instance-root "$TEST_ROOT" doctor --no-synthetic-proof >/dev/null
echo "ADMINISTRATEME BACKUP RESTORE: PASS"
