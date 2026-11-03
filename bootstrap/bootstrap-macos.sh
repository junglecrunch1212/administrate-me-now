#!/bin/bash -p
set -Eeuo pipefail
umask 077

requested_args=("$@")
unset BASH_ENV ENV CDPATH PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
script_path="${BASH_SOURCE[0]}"
script_parent="${script_path%/*}"
[[ "$script_parent" != "$script_path" ]] || script_parent="."
BOOTSTRAP_DIR="$(cd "$script_parent" && pwd -P)"
SOURCE_ROOT="$(cd "$BOOTSTRAP_DIR/.." && pwd -P)"
REPOSITORY_ROOT="${ADMINME_SUPERVISED_CANDIDATE_ROOT:-$SOURCE_ROOT}"
CONTROLLER_SOURCE_ROOT="${ADMINME_FIXED_CONTROLLER_ROOT:-$REPOSITORY_ROOT}"
INSTANCE_ROOT="${ADMINME_INSTANCE_ROOT:-$HOME/Library/Application Support/AdministrateMe}"
DRY_RUN=0
EXPECTED_COMMIT=""
RELEASE_STAGING=""
BOOTSTRAP_CONTROLLER=""
PASSTHROUGH=()

while IFS= read -r git_environment_name; do
  if [[ "$git_environment_name" == GIT_* ]]; then
    unset "$git_environment_name"
  fi
done < <(compgen -e)
export GIT_NO_REPLACE_OBJECTS=1
export GIT_OPTIONAL_LOCKS=0
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export LC_ALL=C

safe_git() {
  [[ -x /usr/bin/git ]] || fail "the reviewed system Git executable is unavailable"
  /usr/bin/git --no-replace-objects -c core.fsmonitor=false "$@"
}

git_is_clean() {
  safe_git -C "$1" status --porcelain=v1 -z --untracked-files=all \
    | /usr/bin/python3 -I -c 'import sys; raise SystemExit(bool(sys.stdin.buffer.read(1)))'
}

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
  if [[ -n "$BOOTSTRAP_CONTROLLER" && -d "$BOOTSTRAP_CONTROLLER" ]]; then
    case "$BOOTSTRAP_CONTROLLER" in
      "$INSTANCE_ROOT"/app/tooling/.bootstrap-controller.*) rm -rf "$BOOTSTRAP_CONTROLLER" ;;
    esac
  fi
}
trap cleanup EXIT

[[ "$INSTANCE_ROOT" == /* ]] || fail "the instance root must be absolute"
[[ "$INSTANCE_ROOT" != "/" && "$INSTANCE_ROOT" != "$HOME" ]] || fail "refusing a broad instance root"
export OPENCLAW_STATE_DIR="$INSTANCE_ROOT/state/openclaw/runtime"
export OPENCLAW_CONFIG_PATH="$OPENCLAW_STATE_DIR/openclaw.json"

if [[ "${ADMINME_BOOTSTRAP_SUPERVISED:-}" != "1" ]]; then
  # The public entry never trusts ambient test controls. The bounded launcher
  # validates this checkout and executes only its fixed supervisor Git blob.
  # Internal controller coordinates are meaningful only inside the inherited,
  # descriptor-bound supervised branch below.
  REPOSITORY_ROOT="$SOURCE_ROOT"
  CONTROLLER_SOURCE_ROOT="$SOURCE_ROOT"
  unset ADMINME_TEST_MODE ADMINME_UPDATE_SUPERVISED \
    ADMINME_FIXED_CONTROLLER_ROOT ADMINME_SUPERVISED_CANDIDATE_ROOT \
    ADMINME_SUPERVISED_CANDIDATE_COMMIT ADMINME_CONTROLLER_RUNTIME_ROOT \
    ADMINME_CONTROLLER_LIFECYCLE_PATH ADMINME_CONTROLLER_LIFECYCLE_DEVICE \
    ADMINME_CONTROLLER_LIFECYCLE_INODE ADMINME_UPGRADE_OPERATION_LOCK_FD
  ((DRY_RUN == 0)) \
    || fail "--dry-run is an internal test-harness option, not a safe operator rehearsal; use --fixture with a separate Lab instance root"
  exec /usr/bin/python3 -I "$BOOTSTRAP_DIR/reviewed_launcher.py" \
    bootstrap "$INSTANCE_ROOT" "$REPOSITORY_ROOT" "${requested_args[@]}"
fi

safe_git -C "$REPOSITORY_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || fail "the installer must run from a Git checkout of the reviewed release"
git_is_clean "$REPOSITORY_ROOT" \
  || fail "the release checkout has local changes; restore a clean reviewed checkout instead of deploying it"
commit="$(safe_git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
if [[ "${ADMINME_BOOTSTRAP_SUPERVISED:-}" == "1" ]]; then
  supervised_commit="${ADMINME_SUPERVISED_CANDIDATE_COMMIT:-}"
  [[ "$supervised_commit" =~ ^[0-9a-f]{40}$ \
    && "$commit" == "$supervised_commit" ]] \
    || fail "reviewed candidate changed after fixed supervision"
fi
if [[ -n "$EXPECTED_COMMIT" ]]; then
  [[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] \
    || fail "--expected-commit must be the full 40-character reviewed Git SHA"
  [[ "$commit" == "$EXPECTED_COMMIT" ]] \
    || fail "checked-out commit $commit does not match reviewed commit $EXPECTED_COMMIT"
fi
echo "Reviewed source boundary: $commit"
export ADMINME_REVIEWED_COMMIT="$commit"

[[ -n "${ADMINME_FIXED_CONTROLLER_ROOT:-}" ]] \
  || fail "fixed bootstrap-controller root is missing"
[[ "$BOOTSTRAP_DIR" == "$ADMINME_FIXED_CONTROLLER_ROOT/bootstrap" ]] \
  || fail "fixed bootstrap-controller path is inconsistent"
[[ "${ADMINME_UPGRADE_OPERATION_LOCK_FD:-}" =~ ^[0-9]+$ ]] \
  || fail "fixed bootstrap operation-lock descriptor is missing"
/usr/bin/python3 -I - "$INSTANCE_ROOT" "$ADMINME_UPGRADE_OPERATION_LOCK_FD" <<'PY'
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
descriptor = int(sys.argv[2])
app = root / "app"
root_metadata = root.lstat()
app_metadata = app.lstat()
lock_metadata = os.fstat(descriptor)
lock_public = (app / ".upgrade-operation.lock").lstat()
if (
    root.is_symlink()
    or app.is_symlink()
    or not stat.S_ISDIR(root_metadata.st_mode)
    or not stat.S_ISDIR(app_metadata.st_mode)
    or root_metadata.st_uid != os.getuid()
    or app_metadata.st_uid != os.getuid()
    or stat.S_IMODE(root_metadata.st_mode) != 0o700
    or stat.S_IMODE(app_metadata.st_mode) != 0o700
    or not stat.S_ISREG(lock_metadata.st_mode)
    or lock_metadata.st_uid != os.getuid()
    or lock_metadata.st_nlink != 1
    or stat.S_IMODE(lock_metadata.st_mode) != 0o600
    or (lock_metadata.st_dev, lock_metadata.st_ino)
    != (lock_public.st_dev, lock_public.st_ino)
):
    raise SystemExit("unsafe supervised bootstrap coordinate")
PY

if [[ "${ADMINME_TEST_MODE:-0}" == "1" ]]; then
  ((DRY_RUN == 1)) \
    || fail "fixed bootstrap test mode requires the explicit dry-run harness"
  export ADMINME_INSTANCE_ROOT="$INSTANCE_ROOT"
  export ADMINME_REPOSITORY_ROOT="$REPOSITORY_ROOT"
  acceptance_python="${ADMINME_ACCEPTANCE_PYTHON:-}"
  [[ "$acceptance_python" == /* && -x "$acceptance_python" ]] \
    || fail "fixed bootstrap test interpreter is missing"
  bootstrap_args=(--instance-root "$INSTANCE_ROOT" bootstrap)
  if ((${#PASSTHROUGH[@]})); then
    bootstrap_args+=("${PASSTHROUGH[@]}")
  fi
  "$acceptance_python" -I -c '
import runpy
import sys
sys.path.insert(0, sys.argv[1])
sys.argv = ["bootstrap.cli", *sys.argv[2:]]
runpy.run_module("bootstrap.cli", run_name="__main__")
' "$CONTROLLER_SOURCE_ROOT" "${bootstrap_args[@]}"
  exit $?
fi

((DRY_RUN == 0)) || fail "--dry-run is an internal test-harness option, not a safe operator rehearsal; use --fixture with a separate Lab instance root"

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
BREW=/opt/homebrew/bin/brew
[[ -x "$BREW" ]] || fail "install Homebrew from its signed package, then rerun"
brew_version="$("$BREW" --version | awk 'NR==1 {print $2}')"
IFS=. read -r brew_major _ <<<"$brew_version"
[[ "$brew_major" =~ ^[0-9]+$ ]] || fail "could not read the Homebrew version"
if ((brew_major < 6 || brew_major >= 8)); then
  fail "Homebrew $brew_version is outside >=6,<8"
fi

free_kib="$(df -Pk "$(dirname "$INSTANCE_ROOT")" | awk 'NR==2 {print $4}')"
[[ "$free_kib" =~ ^[0-9]+$ ]] || fail "could not determine free disk space"
((free_kib >= 10485760)) || fail "at least 10 GiB free space is required"

echo "Preflight passed. The installer will use only user-level services."
echo "macOS will request Calendar, Reminders, Contacts, Notes, or microphone permission only for integrations you select."
echo "OAuth sign-in happens on the provider's page; credentials go to Keychain/OpenClaw, never a .env file."

manifest_coordinates="$(/usr/bin/python3 -I - \
  "$CONTROLLER_SOURCE_ROOT/bootstrap/versions.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
document = json.loads(path.read_text(encoding="utf-8"))
coordinates = (
    document["python"]["formula"],
    document["python"]["preferred"],
    document["node"]["formula"],
    document["node"]["preferred"],
    document["package_managers"]["poetry"],
)
if any(not isinstance(value, str) or not value or "\t" in value for value in coordinates):
    raise SystemExit("invalid runtime manifest")
print("\t".join(coordinates))
PY
)" || fail "the fixed runtime manifest is invalid"
IFS=$'\t' read -r python_formula python_preferred node_formula node_preferred poetry_preferred \
  <<<"$manifest_coordinates"
[[ "$python_formula" == "python@3.14" \
  && "$node_formula" == "node@24" \
  && "$poetry_preferred" == "2.4.1" ]] \
  || fail "the fixed runtime manifest names an unsupported bootstrap coordinate"

if ((DRY_RUN == 0)); then
  for formula in "$python_formula" "$node_formula"; do
    if ! "$BREW" list --versions "$formula" >/dev/null 2>&1; then
      echo "Installing required Homebrew formula: $formula"
      "$BREW" install "$formula"
    fi
  done
fi

python_bin="$("$BREW" --prefix "$python_formula")/bin/python3.14"
node_bin_dir="$("$BREW" --prefix "$node_formula")/bin"
export PATH="$node_bin_dir:$PATH"
[[ -x "$python_bin" ]] || fail "Python 3.14 is missing"
python_version="$("$python_bin" -I -c 'import platform; print(platform.python_version())')"
[[ "$python_version" == "$python_preferred" ]] \
  || fail "Python $python_version does not match reviewed version $python_preferred"
node_version="$("$node_bin_dir/node" -p 'process.versions.node')"
[[ "$node_version" == "$node_preferred" ]] \
  || fail "Node $node_version does not match reviewed version $node_preferred"
npm_version="$("$node_bin_dir/npm" --version)"
IFS=. read -r npm_major npm_minor _ <<<"$npm_version"
if ((npm_major != 11 || npm_minor < 6)); then
  fail "npm $npm_version is outside >=11.6,<12"
fi

poetry_venv="${ADMINME_CONTROLLER_RUNTIME_ROOT:-$INSTANCE_ROOT/app/tooling/poetry-$poetry_preferred}/poetry"
if [[ ! -x "$poetry_venv/bin/poetry" ]]; then
  "$python_bin" -m venv "$poetry_venv"
  chmod 700 "$poetry_venv"
  "$poetry_venv/bin/python" -m pip install --disable-pip-version-check \
    "poetry==$poetry_preferred"
fi
[[ "$("$poetry_venv/bin/poetry" --version)" == "Poetry (version $poetry_preferred)" ]] \
  || fail "the reviewed Poetry tooling coordinate has the wrong version"

"$BOOTSTRAP_DIR/install-openclaw-source.sh" \
  --instance-root "$INSTANCE_ROOT" \
  --python "$python_bin"
export PATH="$INSTANCE_ROOT/bin:$PATH"

release_dir="$INSTANCE_ROOT/app/releases/$commit"
[[ "$(safe_git -C "$REPOSITORY_ROOT" rev-parse HEAD)" == "$commit" \
  && "$(git_is_clean "$REPOSITORY_ROOT" && printf clean)" == "clean" ]] \
  || fail "the release checkout changed during preparation"
BOOTSTRAP_CONTROLLER="${ADMINME_CONTROLLER_RUNTIME_ROOT:-$INSTANCE_ROOT/app/tooling}/bootstrap-controller"
"$python_bin" -m venv "$BOOTSTRAP_CONTROLLER/venv"
chmod 700 "$BOOTSTRAP_CONTROLLER" "$BOOTSTRAP_CONTROLLER/venv"
(
  cd "$CONTROLLER_SOURCE_ROOT"
  VIRTUAL_ENV="$BOOTSTRAP_CONTROLLER/venv" \
    "$poetry_venv/bin/poetry" sync --only main --no-interaction
)
(
cd "$CONTROLLER_SOURCE_ROOT"
ADMINME_REPOSITORY_ROOT="$CONTROLLER_SOURCE_ROOT" \
ADMINME_UPGRADE_CONTROLLER_COMMIT="$commit" \
ADMINME_UPGRADE_CONTROLLER_ROOT="$CONTROLLER_SOURCE_ROOT" \
ADMINME_PREPARATION_POETRY="$poetry_venv/bin/poetry" \
  "$BOOTSTRAP_CONTROLLER/venv/bin/python" - "$INSTANCE_ROOT" "$REPOSITORY_ROOT" "$commit" <<'PY'
import sys
from pathlib import Path

from bootstrap.contracts import DeploymentPaths
from bootstrap.upgrade import UpgradeManager

paths = DeploymentPaths.from_root(Path(sys.argv[1]))
manager = UpgradeManager(
    paths,
    root=Path(sys.argv[2]),
    test_mode=False,
)
manager._attest_controller_provenance(Path(sys.argv[2]), sys.argv[3])
release = manager._publish_release(Path(sys.argv[2]), sys.argv[3])
if release != paths.app_dir / "releases" / sys.argv[3]:
    raise SystemExit("fixed release publication returned the wrong coordinate")
PY
)
rm -rf "$BOOTSTRAP_CONTROLLER"
BOOTSTRAP_CONTROLLER=""
[[ -d "$release_dir" && ! -L "$release_dir" ]] \
  || fail "the immutable release directory is incomplete; preserve it for diagnosis and deploy into a new instance root"

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
