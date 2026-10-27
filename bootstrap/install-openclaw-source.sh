#!/bin/bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
INSTANCE_ROOT=""
PYTHON_BIN=""
STAGING_DIR=""
PREPARE_ONLY=0
SELECT_PREPARED=0

fail() {
  echo "AdministrateMe OpenClaw install: $1" >&2
  exit 1
}

cleanup() {
  if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
    case "$STAGING_DIR" in
      "$INSTANCE_ROOT"/app/openclaw/.staging.*) rm -rf "$STAGING_DIR" ;;
    esac
  fi
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --instance-root)
      (($# >= 2)) || fail "--instance-root requires a value"
      INSTANCE_ROOT="$2"
      shift 2
      ;;
    --python)
      (($# >= 2)) || fail "--python requires a value"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --prepare-only)
      PREPARE_ONLY=1
      shift
      ;;
    --select-prepared)
      SELECT_PREPARED=1
      shift
      ;;
    *) fail "unknown option: $1" ;;
  esac
done

[[ "$INSTANCE_ROOT" == /* && "$INSTANCE_ROOT" != "/" && "$INSTANCE_ROOT" != "$HOME" ]] \
  || fail "the instance root must be a narrow absolute path"
[[ -x "$PYTHON_BIN" ]] || fail "the pinned Python executable is unavailable"
mkdir -p "$INSTANCE_ROOT"
INSTANCE_ROOT="$(cd "$INSTANCE_ROOT" && pwd -P)"
[[ "$INSTANCE_ROOT" != "/" && "$INSTANCE_ROOT" != "$(cd "$HOME" && pwd -P)" ]] \
  || fail "the resolved instance root must be a narrow absolute path"
command -v node >/dev/null 2>&1 || fail "Node is unavailable"
command -v corepack >/dev/null 2>&1 || fail "Node Corepack is unavailable"
command -v curl >/dev/null 2>&1 || fail "curl is unavailable"
command -v shasum >/dev/null 2>&1 || fail "shasum is unavailable"

IFS=$'\t' read -r OPENCLAW_VERSION OPENCLAW_COMMIT OPENCLAW_URL \
  OPENCLAW_SHA256 OPENCLAW_PACKAGE_MANAGER < <(
  "$PYTHON_BIN" - "$REPOSITORY_ROOT/bootstrap/versions.json" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))["openclaw"]
print("\t".join(str(value[key]) for key in (
    "version", "commit", "source_archive_url", "source_archive_sha256", "package_manager"
)))
PY
)
[[ "$OPENCLAW_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail "invalid reviewed commit pin"
[[ "$OPENCLAW_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "invalid archive checksum pin"
[[ "$OPENCLAW_URL" == "https://codeload.github.com/openclaw/openclaw/tar.gz/$OPENCLAW_COMMIT" ]] \
  || fail "source URL and reviewed commit disagree"

openclaw_parent="$INSTANCE_ROOT/app/openclaw"
source_root="$openclaw_parent/$OPENCLAW_COMMIT"
download_dir="$INSTANCE_ROOT/app/downloads"
archive="$download_dir/openclaw-$OPENCLAW_COMMIT.tar.gz"
bin_dir="$INSTANCE_ROOT/bin"
receipt="$INSTANCE_ROOT/state/openclaw/source-install.json"
mkdir -p "$openclaw_parent" "$download_dir"
if ((SELECT_PREPARED)) && [[ ! -d "$source_root" ]]; then
  fail "the native source must be prepared before stopped activation"
fi

validate_source() {
  local candidate="$1"
  [[ -d "$candidate" && ! -L "$candidate" && -x "$candidate/openclaw.mjs" ]] || return 1
  node - "$candidate/package.json" "$OPENCLAW_VERSION" "$OPENCLAW_PACKAGE_MANAGER" <<'JS'
const fs = require("fs");
const value = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
if (value.name !== "openclaw" || value.version !== process.argv[3] || value.packageManager !== process.argv[4]) {
  process.exit(1);
}
JS
}

if [[ -e "$source_root" ]] && ! validate_source "$source_root"; then
  fail "the existing reviewed OpenClaw source directory is incomplete; preserve it for diagnosis and rerun with a new instance root"
fi

if [[ ! -d "$source_root" ]]; then
  archive_ok=0
  if [[ -f "$archive" && ! -L "$archive" ]]; then
    observed_sha="$(shasum -a 256 "$archive" | awk '{print $1}')"
    [[ "$observed_sha" == "$OPENCLAW_SHA256" ]] && archive_ok=1
  fi
  if ((archive_ok == 0)); then
    partial="$archive.partial.$$"
    curl -fsSL --proto '=https' --tlsv1.2 \
      --speed-limit 1 --speed-time 30 \
      --retry 3 --retry-delay 1 --retry-connrefused \
      -o "$partial" "$OPENCLAW_URL"
    observed_sha="$(shasum -a 256 "$partial" | awk '{print $1}')"
    [[ "$observed_sha" == "$OPENCLAW_SHA256" ]] \
      || fail "the downloaded OpenClaw archive failed its pinned SHA-256 check"
    mv -f "$partial" "$archive"
  fi

  STAGING_DIR="$(mktemp -d "$openclaw_parent/.staging.XXXXXX")"
  tar -xzf "$archive" -C "$STAGING_DIR" --strip-components=1
  chmod +x "$STAGING_DIR/openclaw.mjs"
  validate_source "$STAGING_DIR" \
    || fail "the verified OpenClaw archive does not match the reviewed package contract"

  # A prepared source artifact cannot replace active shims or discover the
  # service account's real home, credentials or serving Gateway state.
  build_home="$STAGING_DIR/.adminme-build-home"
  build_bin="$STAGING_DIR/.adminme-build-bin"
  mkdir -m 700 "$build_home" "$build_bin"
  corepack enable --install-directory "$build_bin"
  (
    cd "$STAGING_DIR"
    export HOME="$build_home"
    export PATH="$build_bin:$PATH"
    export OPENCLAW_STATE_DIR="$build_home/openclaw"
    export OPENCLAW_CONFIG_PATH="$OPENCLAW_STATE_DIR/openclaw.json"
    export OPENCLAW_NO_AUTO_UPDATE=1
    CI=true pnpm install --frozen-lockfile
    pnpm ui:build
    pnpm build
  )
  observed_version="$(HOME="$build_home" OPENCLAW_STATE_DIR="$build_home/openclaw" \
    OPENCLAW_CONFIG_PATH="$build_home/openclaw/openclaw.json" OPENCLAW_NO_AUTO_UPDATE=1 \
    node "$STAGING_DIR/dist/entry.js" --version)"
  [[ "$observed_version" == *"$OPENCLAW_VERSION"* ]] \
    || fail "the built OpenClaw CLI reports an unexpected version"
  rm -rf "$build_home" "$build_bin"
  mv "$STAGING_DIR" "$source_root"
  STAGING_DIR=""
fi

if [[ "$OPENCLAW_COMMIT" == dc2523e5a5c7a309517fd7fa834add938231d526 ]]; then
  PYTHONPATH="$REPOSITORY_ROOT" "$PYTHON_BIN" - "$INSTANCE_ROOT" "$REPOSITORY_ROOT" "$SELECT_PREPARED" <<'PYSEAL'
import json
import sys
from pathlib import Path
from bootstrap.contracts import DeploymentPaths, atomic_write, load_versions
from bootstrap.openclaw_state_transition import native_source_inventory, _json
paths = DeploymentPaths.from_root(Path(sys.argv[1]))
manifest = load_versions(Path(sys.argv[2]))["openclaw"]
seal = paths.app_dir / "openclaw" / (manifest["commit"] + ".prepared.json")
record = {
    "contract": "administrateme-native-source-v2",
    "commit": manifest["commit"],
    "archive_sha256": manifest["source_archive_sha256"],
    **native_source_inventory(paths, manifest),
}
if seal.exists() or seal.is_symlink():
    if _json(seal) != record:
        raise RuntimeError("prepared native source bytes changed")
elif sys.argv[3] == "1":
    raise RuntimeError("prepared native source binding is missing")
else:
    atomic_write(seal, json.dumps(record, sort_keys=True).encode(), mode=0o600)
print("Prepared native inventory: " + json.dumps({
    key: record[key] for key in (
        "profile", "record_count", "content_bytes", "inventory_metadata_bytes"
    )
}, sort_keys=True))
PYSEAL
fi

if ((PREPARE_ONLY)); then
  echo "Prepared immutable OpenClaw $OPENCLAW_VERSION source commit $OPENCLAW_COMMIT; active selection unchanged"
  exit 0
fi

mkdir -p "$bin_dir" "$(dirname "$receipt")"
corepack enable --install-directory "$bin_dir"
temporary_link="$bin_dir/openclaw.$$.tmp"
ln -s "$source_root/openclaw.mjs" "$temporary_link"
mv -f "$temporary_link" "$bin_dir/openclaw"
export PATH="$bin_dir:$PATH"
observed_version="$(openclaw --version)"
[[ "$observed_version" == *"$OPENCLAW_VERSION"* ]] \
  || fail "the installed OpenClaw source wrapper failed version verification"

"$PYTHON_BIN" - "$receipt" "$source_root" "$OPENCLAW_VERSION" "$OPENCLAW_COMMIT" \
  "$OPENCLAW_URL" "$OPENCLAW_SHA256" "$OPENCLAW_PACKAGE_MANAGER" <<'PY'
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

target = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "install_kind": "verified-github-source-archive",
    "source_root": sys.argv[2],
    "version": sys.argv[3],
    "commit": sys.argv[4],
    "source_archive_url": sys.argv[5],
    "source_archive_sha256": sys.argv[6],
    "package_manager": sys.argv[7],
    "installed_at": datetime.now(timezone.utc).isoformat(),
}
target.parent.mkdir(parents=True, exist_ok=True)
descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
finally:
    Path(temporary).unlink(missing_ok=True)
PY

echo "Verified OpenClaw $OPENCLAW_VERSION source commit $OPENCLAW_COMMIT"
