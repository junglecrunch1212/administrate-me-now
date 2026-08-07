#!/bin/bash
set -Eeuo pipefail
umask 077

[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || {
  echo "OpenClaw source install proof requires Apple-silicon macOS" >&2
  exit 2
}
REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/adminme-openclaw-source.XXXXXX")"
cleanup() {
  case "$TEST_ROOT" in
    "${TMPDIR:-/tmp}"/adminme-openclaw-source.*) rm -rf "$TEST_ROOT" ;;
  esac
}
trap cleanup EXIT

python_bin="$(command -v python3.14 || command -v python3 || command -v python)"
"$REPOSITORY_ROOT/bootstrap/install-openclaw-source.sh" \
  --instance-root "$TEST_ROOT" \
  --python "$python_bin"
"$TEST_ROOT/bin/openclaw" --version

PYTHONPATH="$REPOSITORY_ROOT" "$python_bin" - "$TEST_ROOT" <<'PY'
import sys
from pathlib import Path

from bootstrap.contracts import DeploymentPaths
from bootstrap.openclaw_install import reviewed_source_status

paths = DeploymentPaths.from_root(Path(sys.argv[1]))
status = reviewed_source_status(
    paths,
    executable=str(paths.root / "bin" / "openclaw"),
)
if not status["ok"]:
    raise SystemExit(status)
print(status)
PY

echo "ADMINISTRATEME OPENCLAW SOURCE INSTALL: PASS"
