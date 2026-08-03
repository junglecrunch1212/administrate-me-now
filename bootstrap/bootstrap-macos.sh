#!/bin/bash
set -Eeuo pipefail
umask 077

BOOTSTRAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$BOOTSTRAP_DIR/.." && pwd -P)"
INSTANCE_ROOT="${ADMINME_INSTANCE_ROOT:-$HOME/Library/Application Support/AdministrateMe}"
DRY_RUN=0
PASSTHROUGH=()

while (($#)); do
  case "$1" in
    --instance-root)
      (($# >= 2)) || { echo "--instance-root requires a value" >&2; exit 2; }
      INSTANCE_ROOT="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      PASSTHROUGH+=("$1")
      shift
      ;;
    *)
      PASSTHROUGH+=("$1")
      shift
      ;;
  esac
done

fail() {
  echo "AdministrateMe bootstrap: $1" >&2
  exit 1
}

[[ "$INSTANCE_ROOT" == /* ]] || fail "the instance root must be absolute"
[[ "$INSTANCE_ROOT" != "/" && "$INSTANCE_ROOT" != "$HOME" ]] || fail "refusing a broad instance root"

if [[ "${ADMINME_TEST_MODE:-0}" == "1" ]]; then
  export ADMINME_INSTANCE_ROOT="$INSTANCE_ROOT"
  export ADMINME_REPOSITORY_ROOT="$REPOSITORY_ROOT"
  python_bin="$(command -v python3 || command -v python)"
  "$python_bin" -m bootstrap.cli --instance-root "$INSTANCE_ROOT" bootstrap "${PASSTHROUGH[@]}"
  exit $?
fi

[[ "$(uname -s)" == "Darwin" ]] || fail "requires macOS"
[[ "$(uname -m)" == "arm64" ]] || fail "requires native Apple-silicon arm64; Rosetta is unsupported"
[[ "$(id -u)" -ne 0 ]] || fail "do not run as root; use the dedicated AdministrateMe account"

macos_major="$(sw_vers -productVersion | cut -d. -f1)"
case "$macos_major" in
  14|15|26) ;;
  *) fail "macOS $macos_major is outside the tested majors 14, 15, and 26" ;;
esac

filevault_status="$(fdesetup status 2>/dev/null || true)"
[[ "$filevault_status" == *"On"* ]] || fail "turn on FileVault before bootstrap"
xcode-select -p >/dev/null 2>&1 || fail "install Apple's Command Line Tools with: xcode-select --install"
command -v brew >/dev/null 2>&1 || fail "install Homebrew from its signed package, then rerun"
brew_version="$(brew --version | awk 'NR==1 {print $2}')"
IFS=. read -r brew_major brew_minor _ <<<"$brew_version"
[[ "$brew_major" =~ ^[0-9]+$ ]] || fail "could not read the Homebrew version"
if ((brew_major < 4 || brew_major >= 6 || (brew_major == 4 && brew_minor < 6))); then
  fail "Homebrew $brew_version is outside >=4.6,<6"
fi

free_kib="$(df -Pk "$(dirname "$INSTANCE_ROOT")" | awk 'NR==2 {print $4}')"
[[ "$free_kib" =~ ^[0-9]+$ ]] || fail "could not determine free disk space"
((free_kib >= 10485760)) || fail "at least 10 GiB free space is required"

echo "Preflight passed. The installer will use only user-level services."
echo "macOS will request Calendar, Reminders, Contacts, Notes, or microphone permission only for integrations you select."
echo "OAuth sign-in happens on the provider's page; credentials go to Keychain/OpenClaw, never a .env file."

if ((DRY_RUN == 0)); then
  for formula in python@3.11 node@22; do
    if ! brew list --versions "$formula" >/dev/null 2>&1; then
      echo "Installing required Homebrew formula: $formula"
      brew install "$formula"
    fi
  done
fi

python_bin="$(brew --prefix python@3.11)/bin/python3.11"
node_bin_dir="$(brew --prefix node@22)/bin"
export PATH="$node_bin_dir:$PATH"
[[ -x "$python_bin" ]] || fail "Python 3.11 is missing"
node_version="$(node -p 'process.versions.node')"
IFS=. read -r node_major node_minor node_patch <<<"$node_version"
if ((node_major != 22 || node_minor < 22 || (node_minor == 22 && node_patch < 3))); then
  fail "Node $node_version is outside >=22.22.3,<23"
fi
npm_version="$(npm --version)"
IFS=. read -r npm_major npm_minor _ <<<"$npm_version"
if ((npm_major != 10 || npm_minor < 9)); then
  fail "npm $npm_version is outside >=10.9,<11"
fi

commit="$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
release_dir="$INSTANCE_ROOT/app/releases/$commit"
mkdir -p "$release_dir"
if [[ ! -f "$release_dir/pyproject.toml" ]]; then
  git -C "$REPOSITORY_ROOT" archive --format=tar HEAD | tar -xf - -C "$release_dir"
fi

if [[ ! -x "$release_dir/.venv/bin/python" ]]; then
  "$python_bin" -m venv "$release_dir/.venv"
  "$release_dir/.venv/bin/python" -m pip install --disable-pip-version-check "poetry==2.2.1"
  (
    cd "$release_dir"
    VIRTUAL_ENV="$release_dir/.venv" \
      "$release_dir/.venv/bin/poetry" install --only main --sync --no-interaction
  )
fi

(
  cd "$release_dir/openclaw/administrateme"
  npm ci --ignore-scripts
  npm run check
  npm run build
)

if ((DRY_RUN == 0)); then
  current_openclaw="$(openclaw --version 2>/dev/null || true)"
  if [[ "$current_openclaw" != *"2026.7.2"* ]]; then
    npm install --global "openclaw@2026.7.2"
  fi
  openclaw plugins install --link "$release_dir/openclaw/administrateme"
  openclaw plugins inspect administrateme --runtime --json >/dev/null
fi

export ADMINME_INSTANCE_ROOT="$INSTANCE_ROOT"
export ADMINME_REPOSITORY_ROOT="$release_dir"
"$release_dir/.venv/bin/python" -m bootstrap.cli \
  --instance-root "$INSTANCE_ROOT" bootstrap "${PASSTHROUGH[@]}"
