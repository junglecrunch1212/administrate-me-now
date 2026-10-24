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
"$python_bin" - "$python_bin" <<'PY'
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

# setup-python's macOS package installs its interpreter with mode 775.
# Tighten only this disposable runner's selected interpreter; the production
# pin checks remain unchanged and still refuse a writable interpreter.
if (
    os.environ.get("GITHUB_ACTIONS") != "true"
    or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
):
    raise SystemExit("Interpreter normalization requires a disposable hosted runner")
selected = Path(sys.argv[1]).resolve(strict=True)
interpreter = Path(sys.executable).resolve(strict=True)
if selected != interpreter or not interpreter.is_absolute():
    raise SystemExit("Selected interpreter identity mismatch")
before = interpreter.stat()
if not stat.S_ISREG(before.st_mode) or before.st_uid not in {0, os.getuid()}:
    raise SystemExit("Selected interpreter file or owner refused")
original_hash = hashlib.sha256(interpreter.read_bytes()).digest()
original_mode = stat.S_IMODE(before.st_mode)
restricted_mode = original_mode & ~0o022
if original_mode != restricted_mode:
    if before.st_uid == os.getuid():
        interpreter.chmod(restricted_mode)
    else:
        subprocess.run(
            ["/usr/bin/sudo", "-n", "/bin/chmod", "go-w", str(interpreter)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10,
        )
after = interpreter.stat()
if (
    interpreter.resolve(strict=True) != interpreter
    or not stat.S_ISREG(after.st_mode)
    or (before.st_dev, before.st_ino, before.st_uid) != (after.st_dev, after.st_ino, after.st_uid)
    or stat.S_IMODE(after.st_mode) != restricted_mode
    or hashlib.sha256(interpreter.read_bytes()).digest() != original_hash
):
    raise SystemExit("Selected interpreter changed during permission normalization")
print(f"Hosted interpreter mode {original_mode:o} -> {restricted_mode:o}; bytes unchanged")
PY
PYTHONPATH="$REPOSITORY_ROOT" "$python_bin" -c \
  'import json; from bootstrap.cos_apple_events import service_environment; print(json.dumps(service_environment()))' \
  > "$TEST_ROOT/permission-helper.json"
node "$REPOSITORY_ROOT/openclaw/administrateme/scripts/check-cos-permission-pins.mjs" \
  "$TEST_ROOT/permission-helper.json"
"$REPOSITORY_ROOT/bootstrap/install-openclaw-source.sh" \
  --instance-root "$TEST_ROOT" \
  --python "$python_bin"
"$TEST_ROOT/bin/openclaw" --version

openclaw_commit="$("$python_bin" -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["openclaw"]["commit"])' \
  "$REPOSITORY_ROOT/bootstrap/versions.json")"
if [[ "${1:-}" == --service-work ]]; then
  node "$REPOSITORY_ROOT/openclaw/administrateme/scripts/check-workstation-codex-policy.mjs" \
    "$TEST_ROOT/app/openclaw/$openclaw_commit" "$python_bin"
  node "$REPOSITORY_ROOT/openclaw/administrateme/scripts/check-cos-imessage-text.mjs" \
    "$TEST_ROOT/app/openclaw/$openclaw_commit"
  node "$REPOSITORY_ROOT/openclaw/administrateme/scripts/check-service-work-gateway.mjs" \
    "$TEST_ROOT/app/openclaw/$openclaw_commit"
  node "$REPOSITORY_ROOT/openclaw/administrateme/scripts/check-workstation-work-gateway.mjs" \
    "$TEST_ROOT/app/openclaw/$openclaw_commit"
  node "$REPOSITORY_ROOT/openclaw/administrateme/scripts/check-workstation-work-gateway.mjs" \
    "$TEST_ROOT/app/openclaw/$openclaw_commit" --browser
else
  node "$REPOSITORY_ROOT/openclaw/administrateme/scripts/check-cos-imessage-text.mjs" \
    "$TEST_ROOT/app/openclaw/$openclaw_commit"
  node "$REPOSITORY_ROOT/openclaw/administrateme/scripts/check-cos-approval-gateway.mjs" \
    "$TEST_ROOT/app/openclaw/$openclaw_commit" "$TEST_ROOT/permission-helper.json"
  if [[ "${1:-}" == --self-reminder ]]; then
    node "$REPOSITORY_ROOT/openclaw/administrateme/scripts/check-self-reminder-gateway.mjs" \
      "$TEST_ROOT/app/openclaw/$openclaw_commit"
  fi
fi

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
