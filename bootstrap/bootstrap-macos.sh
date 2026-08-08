#!/bin/bash
set -Eeuo pipefail
umask 077

BOOTSTRAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$BOOTSTRAP_DIR/.." && pwd -P)"
INSTANCE_ROOT="${ADMINME_INSTANCE_ROOT:-$HOME/Library/Application Support/AdministrateMe}"
DRY_RUN=0
EXPECTED_COMMIT=""
RELEASE_STAGING=""
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
    --expected-commit)
      (($# >= 2)) || { echo "--expected-commit requires a value" >&2; exit 2; }
      EXPECTED_COMMIT="$2"
      shift 2
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

cleanup() {
  if [[ -n "$RELEASE_STAGING" && -d "$RELEASE_STAGING" ]]; then
    case "$RELEASE_STAGING" in
      "$INSTANCE_ROOT"/app/releases/.staging.*) rm -rf "$RELEASE_STAGING" ;;
    esac
  fi
}
trap cleanup EXIT

[[ "$INSTANCE_ROOT" == /* ]] || fail "the instance root must be absolute"
[[ "$INSTANCE_ROOT" != "/" && "$INSTANCE_ROOT" != "$HOME" ]] || fail "refusing a broad instance root"
export OPENCLAW_STATE_DIR="$INSTANCE_ROOT/state/openclaw/runtime"
export OPENCLAW_CONFIG_PATH="$OPENCLAW_STATE_DIR/openclaw.json"

if [[ "${ADMINME_TEST_MODE:-0}" == "1" ]]; then
  export ADMINME_INSTANCE_ROOT="$INSTANCE_ROOT"
  export ADMINME_REPOSITORY_ROOT="$REPOSITORY_ROOT"
  python_bin="$(command -v python3 || command -v python)"
  if ((${#PASSTHROUGH[@]})); then
    "$python_bin" -m bootstrap.cli --instance-root "$INSTANCE_ROOT" bootstrap "${PASSTHROUGH[@]}"
  else
    "$python_bin" -m bootstrap.cli --instance-root "$INSTANCE_ROOT" bootstrap
  fi
  exit $?
fi

((DRY_RUN == 0)) || fail "--dry-run is an internal test-harness option, not a safe operator rehearsal; use --fixture with a separate Lab instance root"

[[ "$(uname -s)" == "Darwin" ]] || fail "requires macOS"
[[ "$(uname -m)" == "arm64" ]] || fail "requires native Apple-silicon arm64; Rosetta is unsupported"
[[ "$(id -u)" -ne 0 ]] || fail "do not run as root; use the dedicated AdministrateMe account"

git -C "$REPOSITORY_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || fail "the installer must run from a Git checkout of the reviewed release"
repository_status="$(git -C "$REPOSITORY_ROOT" status --porcelain --untracked-files=all)"
[[ -z "$repository_status" ]] \
  || fail "the release checkout has local changes; restore a clean reviewed checkout instead of deploying it"
commit="$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
if [[ -n "$EXPECTED_COMMIT" ]]; then
  [[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] \
    || fail "--expected-commit must be the full 40-character reviewed Git SHA"
  [[ "$commit" == "$EXPECTED_COMMIT" ]] \
    || fail "checked-out commit $commit does not match reviewed commit $EXPECTED_COMMIT"
fi
echo "Reviewed source boundary: $commit"
export ADMINME_REVIEWED_COMMIT="$commit"

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
IFS=. read -r brew_major _ <<<"$brew_version"
[[ "$brew_major" =~ ^[0-9]+$ ]] || fail "could not read the Homebrew version"
if ((brew_major < 6 || brew_major >= 7)); then
  fail "Homebrew $brew_version is outside >=6,<7"
fi

free_kib="$(df -Pk "$(dirname "$INSTANCE_ROOT")" | awk 'NR==2 {print $4}')"
[[ "$free_kib" =~ ^[0-9]+$ ]] || fail "could not determine free disk space"
((free_kib >= 10485760)) || fail "at least 10 GiB free space is required"

echo "Preflight passed. The installer will use only user-level services."
echo "macOS will request Calendar, Reminders, Contacts, Notes, or microphone permission only for integrations you select."
echo "OAuth sign-in happens on the provider's page; credentials go to Keychain/OpenClaw, never a .env file."

if ((DRY_RUN == 0)); then
  for formula in python@3.14 node@24; do
    if ! brew list --versions "$formula" >/dev/null 2>&1; then
      echo "Installing required Homebrew formula: $formula"
      brew install "$formula"
    fi
  done
fi

python_bin="$(brew --prefix python@3.14)/bin/python3.14"
node_bin_dir="$(brew --prefix node@24)/bin"
export PATH="$node_bin_dir:$PATH"
[[ -x "$python_bin" ]] || fail "Python 3.14 is missing"
node_version="$(node -p 'process.versions.node')"
IFS=. read -r node_major node_minor _ <<<"$node_version"
if ((node_major != 24 || node_minor < 15)); then
  fail "Node $node_version is outside >=24.15,<25"
fi
npm_version="$(npm --version)"
IFS=. read -r npm_major npm_minor _ <<<"$npm_version"
if ((npm_major != 11 || npm_minor < 6)); then
  fail "npm $npm_version is outside >=11.6,<12"
fi

"$BOOTSTRAP_DIR/install-openclaw-source.sh" \
  --instance-root "$INSTANCE_ROOT" \
  --python "$python_bin"
export PATH="$INSTANCE_ROOT/bin:$PATH"

release_dir="$INSTANCE_ROOT/app/releases/$commit"
release_parent="$INSTANCE_ROOT/app/releases"
mkdir -p "$release_parent"
if [[ ! -d "$release_dir" ]]; then
  RELEASE_STAGING="$(mktemp -d "$release_parent/.staging.XXXXXX")"
  git -C "$REPOSITORY_ROOT" archive --format=tar HEAD | tar -xf - -C "$RELEASE_STAGING"
  printf '%s\n' "$commit" >"$RELEASE_STAGING/.administrateme-source-commit"
  mv "$RELEASE_STAGING" "$release_dir"
  RELEASE_STAGING=""
fi
[[ ! -L "$release_dir" \
  && -f "$release_dir/pyproject.toml" \
  && -f "$release_dir/poetry.lock" \
  && -f "$release_dir/bootstrap/cli.py" \
  && -f "$release_dir/.administrateme-source-commit" \
  && "$(<"$release_dir/.administrateme-source-commit")" == "$commit" ]] \
  || fail "the immutable release directory is incomplete; preserve it for diagnosis and deploy into a new instance root"

poetry_venv="$INSTANCE_ROOT/app/tooling/poetry-2.4.1"
if [[ ! -x "$poetry_venv/bin/poetry" ]]; then
  "$python_bin" -m venv "$poetry_venv"
  "$poetry_venv/bin/python" -m pip install --disable-pip-version-check "poetry==2.4.1"
fi
if [[ ! -x "$release_dir/.venv/bin/python" ]]; then
  "$python_bin" -m venv "$release_dir/.venv"
fi
venv_receipt="$release_dir/.venv/.administrateme-installed-commit"
if [[ ! -f "$venv_receipt" || "$(<"$venv_receipt")" != "$commit" ]]; then
  (
    cd "$release_dir"
    VIRTUAL_ENV="$release_dir/.venv" \
      "$poetry_venv/bin/poetry" sync --only main --no-interaction
  )
  printf '%s\n' "$commit" >"$venv_receipt"
fi

(
  cd "$release_dir/openclaw/administrateme"
  npm ci --ignore-scripts
  npm run check
  npm run build
)

if ((DRY_RUN == 0)); then
  for _ in {1..20}; do
    openclaw plugins inspect administrateme --json >/dev/null 2>&1 || break
    # Remove only prior registration metadata; immutable release files stay intact.
    openclaw plugins uninstall administrateme --force --keep-files
  done
  if openclaw plugins inspect administrateme --json >/dev/null 2>&1; then
    fail "could not remove every stale AdministrateMe plugin registration"
  fi
  # The local path is the plugin inside the exact reviewed release archived above.
  openclaw plugins install --link --force "$release_dir/openclaw/administrateme"
  openclaw config set gateway.mode local
  (
    cd "$release_dir"
    ADMINME_INSTANCE_ROOT="$INSTANCE_ROOT" \
      ADMINME_REPOSITORY_ROOT="$release_dir" \
      "$release_dir/.venv/bin/python" -m bootstrap.openclaw_tools \
        --instance-root "$INSTANCE_ROOT"
  )
  openclaw config validate
  openclaw plugins inspect administrateme --runtime --json >/dev/null
fi

export ADMINME_INSTANCE_ROOT="$INSTANCE_ROOT"
export ADMINME_REPOSITORY_ROOT="$release_dir"
if ((${#PASSTHROUGH[@]})); then
  "$release_dir/.venv/bin/python" -m bootstrap.cli \
    --instance-root "$INSTANCE_ROOT" bootstrap "${PASSTHROUGH[@]}"
else
  "$release_dir/.venv/bin/python" -m bootstrap.cli \
    --instance-root "$INSTANCE_ROOT" bootstrap
fi
