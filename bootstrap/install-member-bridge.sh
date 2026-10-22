#!/bin/bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
BRIDGE_ROOT="${ADMINME_BRIDGE_ROOT:-$HOME/Library/Application Support/AdministrateMe Bridge}"
EXPECTED_COMMIT=""
PACKAGE=""
RELEASE_STAGING=""
TRANSFER_CODE_FILE=""
PREVIEW_ONLY="false"
RELEASE_ARCHIVE=""
RELEASE_ARCHIVE_SHA256=""
COMPLETE_WIZARD="false"
PREPARE_RUNTIME_ONLY="false"

while (($#)); do
  case "$1" in
    --expected-commit)
      (($# >= 2)) || { echo "--expected-commit requires a value" >&2; exit 2; }
      EXPECTED_COMMIT="$2"
      shift 2
      ;;
    --bridge-root)
      (($# >= 2)) || { echo "--bridge-root requires a value" >&2; exit 2; }
      BRIDGE_ROOT="$2"
      shift 2
      ;;
    --transfer-code-file)
      (($# >= 2)) || { echo "--transfer-code-file requires a value" >&2; exit 2; }
      TRANSFER_CODE_FILE="$2"
      shift 2
      ;;
    --preview-only)
      PREVIEW_ONLY="true"
      shift
      ;;
    --release-archive)
      (($# >= 2)) || { echo "--release-archive requires a value" >&2; exit 2; }
      RELEASE_ARCHIVE="$2"
      shift 2
      ;;
    --release-sha256)
      (($# >= 2)) || { echo "--release-sha256 requires a value" >&2; exit 2; }
      RELEASE_ARCHIVE_SHA256="$2"
      shift 2
      ;;
    --prepare-runtime-only)
      PREPARE_RUNTIME_ONLY="true"
      shift
      ;;
    --complete-wizard)
      COMPLETE_WIZARD="true"
      shift
      ;;
    *)
      [[ -z "$PACKAGE" ]] || { echo "only one bridge package is allowed" >&2; exit 2; }
      PACKAGE="$1"
      shift
      ;;
  esac
done

fail() {
  echo "AdministrateMe member bridge: $1" >&2
  exit 1
}

report_phase() {
  if [[ "${ADMINME_BRIDGE_DIAGNOSTICS_FD:-}" == "3" ]]; then
    printf '%s\n' "$1" >&3
  fi
}

cleanup() {
  if [[ -n "$RELEASE_STAGING" && -d "$RELEASE_STAGING" ]]; then
    case "$RELEASE_STAGING" in
      "$BRIDGE_ROOT"/app/releases/.staging.*) rm -rf "$RELEASE_STAGING" ;;
    esac
  fi
}
trap cleanup EXIT

report_phase preflight
if [[ "$PREPARE_RUNTIME_ONLY" == "true" ]]; then
  [[ -z "$PACKAGE" && -z "$TRANSFER_CODE_FILE" && "$PREVIEW_ONLY" == "false" && "$COMPLETE_WIZARD" == "false" ]] \
    || fail "runtime preparation accepts no enrollment material or wizard action"
else
  [[ -n "$PACKAGE" && -f "$PACKAGE" && ! -L "$PACKAGE" ]] \
    || fail "provide the exported .adminmebridge file"
fi
[[ "$BRIDGE_ROOT" == /* && "$BRIDGE_ROOT" != "/" && "$BRIDGE_ROOT" != "$HOME" ]] \
  || fail "the member-bridge root must be a narrow absolute path"
[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] \
  || fail "requires native Apple-silicon macOS"
[[ "$(id -u)" -ne 0 ]] || fail "do not run as root"
filevault_status="$(fdesetup status 2>/dev/null || true)"
[[ "$filevault_status" == *"On"* ]] || fail "turn on FileVault before installing the member bridge"
command -v tailscale >/dev/null 2>&1 || fail "enable Tailscale CLI integration first"
tailscale status --json >/dev/null || fail "sign in to Tailscale first"
command -v brew >/dev/null 2>&1 || fail "install Homebrew first"
free_kib="$(df -Pk "$(dirname "$BRIDGE_ROOT")" | awk 'NR==2 {print $4}')"
[[ "$free_kib" =~ ^[0-9]+$ ]] || fail "could not determine free disk space"
((free_kib >= 5242880)) || fail "at least 5 GiB free space is required"

[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] \
  || fail "--expected-commit must be the full reviewed Git SHA"
if [[ -n "$RELEASE_ARCHIVE" || -n "$RELEASE_ARCHIVE_SHA256" ]]; then
  [[ -n "$RELEASE_ARCHIVE" && -f "$RELEASE_ARCHIVE" && ! -L "$RELEASE_ARCHIVE" ]] \
    || fail "the reviewed release archive is missing or unsafe"
  [[ "$RELEASE_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]] \
    || fail "the reviewed release archive checksum is invalid"
  archive_sha256="$(shasum -a 256 "$RELEASE_ARCHIVE" | awk '{print $1}')"
  [[ "$archive_sha256" == "$RELEASE_ARCHIVE_SHA256" ]] \
    || fail "the reviewed release archive checksum does not match"
  commit="$EXPECTED_COMMIT"
else
  git -C "$REPOSITORY_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || fail "run from the reviewed Git checkout or packaged helper app"
  [[ -z "$(git -C "$REPOSITORY_ROOT" status --porcelain --untracked-files=all)" ]] \
    || fail "the reviewed bridge checkout has local changes"
  commit="$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
  [[ "$commit" == "$EXPECTED_COMMIT" ]] \
    || fail "checked-out commit does not match the reviewed bridge release"
fi

report_phase python_setup
if ! brew list --versions python@3.14 >/dev/null 2>&1; then
  brew install python@3.14 >&2
fi
python_bin="$(brew --prefix python@3.14)/bin/python3.14"
report_phase release_preparation
release_dir="$BRIDGE_ROOT/app/releases/$commit"
release_parent="$BRIDGE_ROOT/app/releases"
mkdir -p "$release_parent"
if [[ ! -d "$release_dir" ]]; then
  RELEASE_STAGING="$(mktemp -d "$release_parent/.staging.XXXXXX")"
  if [[ -n "$RELEASE_ARCHIVE" ]]; then
    while IFS= read -r archive_entry; do
      case "$archive_entry" in
        /*|..|../*|*/../*) fail "the reviewed release archive contains an unsafe path" ;;
      esac
    done < <(tar -tf "$RELEASE_ARCHIVE")
    tar -xf "$RELEASE_ARCHIVE" -C "$RELEASE_STAGING"
    [[ -z "$(find "$RELEASE_STAGING" -type l -print -quit)" ]] \
      || fail "the reviewed release archive contains a symbolic link"
  else
    git -C "$REPOSITORY_ROOT" archive --format=tar HEAD | tar -xf - -C "$RELEASE_STAGING"
  fi
  printf '%s\n' "$commit" >"$RELEASE_STAGING/.administrateme-source-commit"
  mv "$RELEASE_STAGING" "$release_dir"
  RELEASE_STAGING=""
fi
[[ ! -L "$release_dir" \
  && -f "$release_dir/bridge/pyproject.toml" \
  && -f "$release_dir/bridge/poetry.lock" \
  && -f "$release_dir/bootstrap/cli.py" \
  && -f "$release_dir/.administrateme-source-commit" \
  && "$(<"$release_dir/.administrateme-source-commit")" == "$commit" ]] \
  || fail "the immutable member release is incomplete; preserve it for diagnosis and use a new bridge root"
prepare_environment() (
  # Keep build-only environment selection out of preview, acceptance and the
  # bridge daemon, including direct invocations without diagnostic descriptors.
  venv_receipt="$release_dir/.venv/.administrateme-bridge-installed-commit"
  if [[ ! -f "$venv_receipt" || "$(<"$venv_receipt")" != "$commit" ]]; then
    report_phase native_build_setup
    build_coordinates="$("$python_bin" -c '
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import stat
import subprocess
import sys
import sysconfig

def stop(reason):
    print("AdministrateMe member bridge: native build SDK setup failed (" + reason + "). Repair the selected developer tools or explicit build override before retrying.", file=sys.stderr)
    raise SystemExit(78)

def absolute_directory(value):
    if not value or "\n" in value or "\r" in value:
        stop("invalid_directory")
    path = Path(value)
    if not path.is_absolute():
        stop("invalid_directory")
    result = path.resolve(strict=True)
    if not result.is_dir():
        stop("invalid_directory")
    return result

try:
    developer_value = os.environ.get("DEVELOPER_DIR")
    if developer_value is None:
        selected = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True, timeout=10, check=False)
        if selected.returncode:
            stop("developer_selection_unavailable")
        developer_value = selected.stdout.strip()
    developer = absolute_directory(developer_value)
    if developer.suffix == ".app":
        developer = absolute_directory(str(developer / "Contents/Developer"))
    explicit_sdk = "SDKROOT" in os.environ
    if explicit_sdk:
        sdk_value = os.environ["SDKROOT"]
        sdk = absolute_directory(sdk_value)
    else:
        sdk_alias = developer / "SDKs/MacOSX.sdk"
        if not sdk_alias.exists():
            sdk_alias = developer / "Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
        sdk = absolute_directory(str(sdk_alias))
        if not sdk.is_relative_to(developer):
            stop("sdk_alias_outside_selected_developer")
        sdk_value = str(sdk)
    sdk_info = sdk.stat()
    if sdk_info.st_uid not in {0, os.getuid()} or stat.S_IMODE(sdk_info.st_mode) & 0o022:
        stop("unsafe_sdk_directory")
    metadata = sdk / "SDKSettings.json"
    if not metadata.exists():
        metadata = sdk / "SDKSettings.plist"
    metadata_info = metadata.lstat()
    if not stat.S_ISREG(metadata_info.st_mode) or metadata_info.st_uid not in {0, os.getuid()} or stat.S_IMODE(metadata_info.st_mode) & 0o022 or metadata_info.st_size > 1048576:
        stop("unsafe_sdk_metadata")
    data = json.loads(metadata.read_bytes()) if metadata.suffix == ".json" else plistlib.loads(metadata.read_bytes())
    version = data.get("Version") if isinstance(data, dict) else None
    name = data.get("CanonicalName") if isinstance(data, dict) else None
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}", version) or not isinstance(name, str) or not re.fullmatch(r"macosx(?:[0-9]+(?:\.[0-9]+){0,2})?", name):
        stop("invalid_macos_sdk_metadata")
    # Flags can supersede SDKROOT. Preserve compatible explicit choices and
    # fail on contradictions; never rewrite compiler commands or build flags.
    for key in ("CC", "CXX", "CFLAGS", "CPPFLAGS", "LDFLAGS", "LDSHARED", "LDCXXSHARED", "ARCHFLAGS"):
        values = [os.environ.get(key, "")]
        if key not in {"CC", "CXX", "LDSHARED", "LDCXXSHARED"} or key not in os.environ:
            values.append(sysconfig.get_config_var(key) or "")
        for value in values:
            tokens = shlex.split(value)
            for index, token in enumerate(tokens):
                override = None
                if token in {"-isysroot", "--sysroot"}:
                    if index + 1 == len(tokens):
                        stop("invalid_sysroot_override")
                    override = tokens[index + 1]
                elif token.startswith("--sysroot="):
                    override = token.partition("=")[2]
                elif token.startswith("-isysroot"):
                    override = token[len("-isysroot"):]
                elif token.startswith("-Wl,-syslibroot,"):
                    override = token[len("-Wl,-syslibroot,"):]
                elif token == "-Wl,-syslibroot":
                    if index + 1 == len(tokens) or not tokens[index + 1].startswith("-Wl,"):
                        stop("invalid_sysroot_override")
                    override = tokens[index + 1][len("-Wl,"):]
                elif token == "-syslibroot":
                    offset = 2 if index > 0 and tokens[index - 1] == "-Xlinker" else 1
                    if index + offset >= len(tokens) or (offset == 2 and tokens[index + 1] != "-Xlinker"):
                        stop("invalid_sysroot_override")
                    override = tokens[index + offset]
                if override is not None and absolute_directory(override) != sdk:
                    stop("conflicting_sysroot_override")
    print("AdministrateMe member bridge: native build SDK " + version + " selected (" + ("explicit" if explicit_sdk else "selected developer alias") + ").", file=sys.stderr)
    print(sdk_value)
    print(developer_value)
except (OSError, ValueError, TypeError, subprocess.SubprocessError, plistlib.InvalidFileException):
    stop("invalid_native_build_configuration")
')" || return "$?"
    SDKROOT="${build_coordinates%%$'\n'*}"
    DEVELOPER_DIR="${build_coordinates#*$'\n'}"
    export SDKROOT DEVELOPER_DIR
  fi
  report_phase tooling_setup
  poetry_venv="$BRIDGE_ROOT/app/tooling/poetry-2.4.1"
  if [[ ! -x "$poetry_venv/bin/poetry" ]]; then
    "$python_bin" -m venv "$poetry_venv" || return "$?"
    "$poetry_venv/bin/python" -m pip install --disable-pip-version-check "poetry==2.4.1" \
      || return "$?"
  fi
  report_phase environment_setup
  if [[ ! -x "$release_dir/.venv/bin/python" ]]; then
    "$python_bin" -m venv "$release_dir/.venv" || return "$?"
  fi
  report_phase dependency_install
  if [[ ! -f "$venv_receipt" || "$(<"$venv_receipt")" != "$commit" ]]; then
    (
      cd "$release_dir" || exit "$?"
      VIRTUAL_ENV="$release_dir/.venv" \
        "$poetry_venv/bin/poetry" -C bridge sync --only main --no-interaction
    ) || return "$?"
    printf '%s\n' "$commit" >"$venv_receipt"
  fi
)

# Only dependency preparation can reach the persistent diagnostic descriptor.
# It never reads/decrypts the handoff or transfer code. Keep the bounded tail
# while draining the complete stream, so logging cannot cause a SIGPIPE retry.
if [[ "${ADMINME_BRIDGE_DIAGNOSTICS_FD:-}" == "3" ]]; then
  prepare_environment 2>&1 | "$python_bin" -c '
import os
import sys

tail = b""
while chunk := sys.stdin.buffer.read1(65536):
    tail = (tail + chunk)[-262144:]
    os.lseek(4, 0, os.SEEK_SET)
    os.ftruncate(4, 0)
    view = memoryview(tail)
    while view:
        view = view[os.write(4, view):]
os.fsync(4)
'
  exec 4>&-
else
  prepare_environment >&2
fi

if [[ "$PREPARE_RUNTIME_ONLY" == "true" ]]; then
  printf '{"status":"prepared","commit":"%s","service_started":false}\n' "$commit"
  exit 0
fi

export ADMINME_REPOSITORY_ROOT="$release_dir"
export ADMINME_REVIEWED_COMMIT="$commit"
cd "$release_dir"
bridge_command="bridge-accept"
if [[ "$PREVIEW_ONLY" == "true" ]]; then
  bridge_command="bridge-preview"
  report_phase package_preview
else
  report_phase local_acceptance
fi
accept_args=("$bridge_command" "$PACKAGE" --bridge-root "$BRIDGE_ROOT")
if [[ -n "$TRANSFER_CODE_FILE" ]]; then
  accept_args+=(--transfer-code-file "$TRANSFER_CODE_FILE")
fi
if [[ "$PREVIEW_ONLY" == "true" ]]; then
  accept_args+=(--text)
fi
if [[ "$COMPLETE_WIZARD" == "true" && "$PREVIEW_ONLY" == "false" ]]; then
  "$release_dir/.venv/bin/python" -m bootstrap.cli "${accept_args[@]}"
  report_phase permission_setup
  exec "$release_dir/.venv/bin/python" -m bootstrap.cli \
    bridge-complete --bridge-root "$BRIDGE_ROOT"
fi
exec "$release_dir/.venv/bin/python" -m bootstrap.cli "${accept_args[@]}"
