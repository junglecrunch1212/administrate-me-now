#!/bin/bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
BRIDGE_ROOT="${ADMINME_BRIDGE_ROOT:-$HOME/Library/Application Support/AdministrateMe Bridge}"
EXPECTED_COMMIT=""
PACKAGE=""
RELEASE_STAGING=""

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

cleanup() {
  if [[ -n "$RELEASE_STAGING" && -d "$RELEASE_STAGING" ]]; then
    case "$RELEASE_STAGING" in
      "$BRIDGE_ROOT"/app/releases/.staging.*) rm -rf "$RELEASE_STAGING" ;;
    esac
  fi
}
trap cleanup EXIT

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

git -C "$REPOSITORY_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || fail "run from the reviewed Git checkout"
[[ -z "$(git -C "$REPOSITORY_ROOT" status --porcelain --untracked-files=all)" ]] \
  || fail "the reviewed bridge checkout has local changes"
commit="$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] \
  || fail "--expected-commit must be the full reviewed Git SHA"
[[ "$commit" == "$EXPECTED_COMMIT" ]] \
  || fail "checked-out commit does not match the reviewed bridge release"

if ! brew list --versions python@3.14 >/dev/null 2>&1; then
  brew install python@3.14
fi
python_bin="$(brew --prefix python@3.14)/bin/python3.14"
release_dir="$BRIDGE_ROOT/app/releases/$commit"
release_parent="$BRIDGE_ROOT/app/releases"
mkdir -p "$release_parent"
if [[ ! -d "$release_dir" ]]; then
  RELEASE_STAGING="$(mktemp -d "$release_parent/.staging.XXXXXX")"
  git -C "$REPOSITORY_ROOT" archive --format=tar HEAD | tar -xf - -C "$RELEASE_STAGING"
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
poetry_venv="$BRIDGE_ROOT/app/tooling/poetry-2.4.1"
if [[ ! -x "$poetry_venv/bin/poetry" ]]; then
  "$python_bin" -m venv "$poetry_venv"
  "$poetry_venv/bin/python" -m pip install --disable-pip-version-check "poetry==2.4.1"
fi
if [[ ! -x "$release_dir/.venv/bin/python" ]]; then
  "$python_bin" -m venv "$release_dir/.venv"
fi
venv_receipt="$release_dir/.venv/.administrateme-bridge-installed-commit"
if [[ ! -f "$venv_receipt" || "$(<"$venv_receipt")" != "$commit" ]]; then
  (
    cd "$release_dir"
    VIRTUAL_ENV="$release_dir/.venv" \
      "$poetry_venv/bin/poetry" -C bridge sync --only main --no-interaction
  )
  printf '%s\n' "$commit" >"$venv_receipt"
fi

export ADMINME_REPOSITORY_ROOT="$release_dir"
export ADMINME_REVIEWED_COMMIT="$commit"
cd "$release_dir"
exec "$release_dir/.venv/bin/python" -m bootstrap.cli \
  bridge-accept "$PACKAGE" --bridge-root "$BRIDGE_ROOT"
