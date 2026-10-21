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
[[ -n "$PACKAGE" && -f "$PACKAGE" && ! -L "$PACKAGE" ]] \
  || fail "provide the exported .adminmebridge file"
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
prepare_environment() {
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
  venv_receipt="$release_dir/.venv/.administrateme-bridge-installed-commit"
  if [[ ! -f "$venv_receipt" || "$(<"$venv_receipt")" != "$commit" ]]; then
    (
      cd "$release_dir" || exit "$?"
      VIRTUAL_ENV="$release_dir/.venv" \
        "$poetry_venv/bin/poetry" -C bridge sync --only main --no-interaction
    ) || return "$?"
    printf '%s\n' "$commit" >"$venv_receipt"
  fi
}

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
