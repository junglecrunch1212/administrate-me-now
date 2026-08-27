#!/bin/bash -p
set -Eeuo pipefail
unset BASH_ENV ENV CDPATH PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
script_path="${BASH_SOURCE[0]}"
script_parent="${script_path%/*}"
[[ "$script_parent" != "$script_path" ]] || script_parent="."
SCRIPT_DIR="$(cd "$script_parent" && pwd -P)"
requested_args=("$@")

die() {
  echo "ERROR: $*" >&2
  exit 1
}

# The reviewed candidate path, not ambient Git repository-selection state,
# controls every launcher-side identity and cleanliness decision.
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
  [[ -x /usr/bin/git ]] || die "the reviewed system Git executable is unavailable"
  /usr/bin/git --no-replace-objects -c core.fsmonitor=false "$@"
}

git_is_clean() {
  safe_git -C "$1" status --porcelain=v1 -z --untracked-files=all \
    | /usr/bin/python3 -I -c 'import sys; raise SystemExit(bool(sys.stdin.buffer.read(1)))'
}

if [[ "${ADMINME_UPDATE_SUPERVISED:-}" != "1" ]]; then
  # Ambient test controls are never authority for the public updater.  The
  # repository-owned process harness invokes the fixed supervisor's explicit
  # internal test mode instead.
  unset ADMINME_TEST_MODE
  instance_root="${ADMINME_INSTANCE_ROOT:-${HOME}/Library/Application Support/AdministrateMe}"
  [[ "$instance_root" == /* ]] || die "instance root must be absolute"
  candidate=""
  pending_attestation=""
  for argument in "${requested_args[@]}"; do
    case "$argument" in
      --approve-runtime-migration | --approve-declared-source-assessment-repair) ;;
      --pending-attestation-id=*)
        [[ -z "$pending_attestation" ]] \
          || die "pending-attestation identifier was repeated"
        pending_attestation="${argument#*=}"
        [[ "$pending_attestation" =~ ^[0-9a-f]{32}$ ]] \
          || die "pending-attestation identifier must be 32 lowercase hex"
        ;;
      --*) die "unknown update option: $argument" ;;
      *)
        [[ -z "$candidate" ]] || die "upgrade accepts exactly one candidate path"
        candidate="$argument"
        ;;
    esac
  done
  [[ -n "$pending_attestation" ]] \
    || die "production upgrade requires --pending-attestation-id=<32 lowercase hex>"
  [[ -n "$candidate" && "$candidate" == /* && -d "$candidate" ]] \
    || die "usage: update.sh --pending-attestation-id=<id> [options] /absolute/path/to/reviewed-candidate"
  candidate="$(cd "$candidate" && pwd -P)"
  exec /usr/bin/python3 -I "$SCRIPT_DIR/reviewed_launcher.py" \
    update "$instance_root" "$candidate" "${requested_args[@]}"
fi

# From this point onward the script itself came from the private fixed-commit
# controller tree and the supervisor holds the canonical no-follow operation
# lock through controller cleanup.
fixed_controller_root="${ADMINME_FIXED_CONTROLLER_ROOT:-}"
[[ -n "$fixed_controller_root" \
  && "$SCRIPT_DIR" == "$fixed_controller_root/bootstrap" ]] \
  || die "fixed update-controller provenance is missing"
# shellcheck source=bootstrap/common.sh
source "$SCRIPT_DIR/common.sh"
resolve_instance_root
[[ "${ADMINME_UPGRADE_OPERATION_LOCK_FD:-}" =~ ^[0-9]+$ ]] \
  || fail "upgrade operation lock descriptor is missing"
/usr/bin/python3 -I - "$ADMINME_INSTANCE_ROOT" "$ADMINME_UPGRADE_OPERATION_LOCK_FD" <<'PY'
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
    raise SystemExit("unsafe supervised upgrade coordinate")
PY

approve_flag=""
source_repair_flag=""
pending_flag=""
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --approve-runtime-migration)
      [[ -z "$approve_flag" ]] || fail "runtime-migration approval was repeated"
      approve_flag="$1"
      ;;
    --approve-declared-source-assessment-repair)
      [[ -z "$source_repair_flag" ]] || fail "source-repair approval was repeated"
      source_repair_flag="$1"
      ;;
    --pending-attestation-id=*)
      [[ -z "$pending_flag" ]] || fail "pending-attestation identifier was repeated"
      pending_value="${1#*=}"
      [[ "$pending_value" =~ ^[0-9a-f]{32}$ ]] \
        || fail "pending-attestation identifier must be 32 lowercase hex"
      pending_flag="$1"
      ;;
    *)
      fail "unknown update option: $1"
      ;;
  esac
  shift
done

if [[ "${ADMINME_TEST_MODE:-}" != "1" && -z "$pending_flag" ]]; then
  fail "production upgrade requires --pending-attestation-id=<32 lowercase hex>"
fi

candidate="${1:-}"
[[ -n "$candidate" ]] \
  || fail "usage: update.sh [--approve-runtime-migration] [--approve-declared-source-assessment-repair] /absolute/path/to/reviewed-candidate"
[[ "$candidate" == /* ]] || fail "candidate path must be absolute"
[[ -d "$candidate" ]] || fail "candidate checkout does not exist"
candidate="$(cd "$candidate" && pwd -P)"

# Every upgrade policy is candidate code. Prove that code is a stable reviewed
# Git commit before it is allowed to prepare a release or control a switch.
[[ "$(safe_git -C "$candidate" rev-parse --is-inside-work-tree 2>/dev/null || true)" == "true" ]] \
  || fail "upgrade requires a clean reviewed Git checkout"
git_is_clean "$candidate" \
  || fail "upgrade refuses a candidate checkout with local changes"
candidate_commit="$(safe_git -C "$candidate" rev-parse HEAD)"
[[ "$candidate_commit" =~ ^[0-9a-f]{40}$ ]] \
  || fail "upgrade could not resolve the reviewed candidate commit"
supervised_commit="${ADMINME_SUPERVISED_CANDIDATE_COMMIT:-}"
[[ "$supervised_commit" =~ ^[0-9a-f]{40}$ \
  && "$candidate_commit" == "$supervised_commit" ]] \
  || fail "reviewed candidate changed after fixed supervision"

manifest="$fixed_controller_root/bootstrap/versions.json"
[[ -f "$manifest" ]] || fail "candidate version manifest is missing"
manifest_parser="/usr/bin/python3"
[[ -x "$manifest_parser" ]] || fail "the system manifest parser is unavailable"
IFS=$'\t' read -r \
  python_formula python_preferred python_minor python_compatible \
  node_formula node_preferred node_compatible poetry_version < <(
  "$manifest_parser" - "$manifest" <<'PY'
import json
import sys

versions = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    versions["python"]["formula"],
    versions["python"]["preferred"],
    str(versions["python"]["preferred"]).rsplit(".", 1)[0],
    versions["python"]["compatible"],
    versions["node"]["formula"],
    versions["node"]["preferred"],
    versions["node"]["compatible"],
    versions["package_managers"]["poetry"],
    sep="\t",
)
PY
)
[[ "$python_formula" =~ ^python@[0-9]+\.[0-9]+$ \
  && "$python_preferred" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ \
  && "$python_minor" =~ ^[0-9]+\.[0-9]+$ \
  && -n "$python_compatible" \
  && "$node_formula" =~ ^node@[0-9]+$ \
  && "$node_preferred" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ \
  && -n "$node_compatible" \
  && "$poetry_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  || fail "candidate runtime manifest has invalid tooling coordinates"

ACTIVE_RELEASE_ROOT="${ADMINME_SUPERVISED_ACTIVE_RELEASE_ROOT:-}"
[[ -n "$ACTIVE_RELEASE_ROOT" && "$ACTIVE_RELEASE_ROOT" == "$ADMINME_INSTANCE_ROOT/app/releases/"* \
  && -d "$ACTIVE_RELEASE_ROOT" && ! -L "$ACTIVE_RELEASE_ROOT" && -O "$ACTIVE_RELEASE_ROOT" ]] \
  || fail "supervised active-release provenance is missing"
export ACTIVE_RELEASE_ROOT
PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
active_python="$ACTIVE_RELEASE_ROOT/.venv/bin/python"
[[ -x "$active_python" ]] || fail "upgrade requires the active release interpreter"

if [[ -n "$approve_flag" ]]; then
  [[ -x /opt/homebrew/bin/brew ]] || fail "Homebrew is unavailable"
  controller_python="$(/opt/homebrew/bin/brew --prefix "$python_formula")/bin/python$python_minor"
  reviewed_node="$(resolve_formula_node "$node_formula" "$node_preferred")"
  node_bin_dir="$(cd "$(dirname "$reviewed_node")" && pwd -P)"
  [[ -x "$controller_python" ]] \
    || fail "install the candidate Python and Node formulae side by side before migration"
else
  # A same-runtime update must not use candidate tooling merely because it is
  # installed. Reject a baseline change independently of candidate code, then
  # self-host that candidate code on the currently active interpreter/runtime.
  active_manifest="$ACTIVE_RELEASE_ROOT/bootstrap/versions.json"
  [[ -f "$active_manifest" ]] || fail "active release version manifest is missing"
  IFS=$'\t' read -r \
    active_python_formula active_python_preferred active_python_compatible \
    active_node_formula active_node_preferred active_node_compatible < <(
    "$manifest_parser" - "$active_manifest" <<'PY'
import json
import sys

versions = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    versions["python"]["formula"],
    versions["python"]["preferred"],
    versions["python"]["compatible"],
    versions["node"]["formula"],
    versions["node"]["preferred"],
    versions["node"]["compatible"],
    sep="\t",
)
PY
  )
  [[ "$python_formula" == "$active_python_formula" \
    && "$python_preferred" == "$active_python_preferred" \
    && "$python_compatible" == "$active_python_compatible" \
    && "$node_formula" == "$active_node_formula" \
    && "$node_preferred" == "$active_node_preferred" \
    && "$node_compatible" == "$active_node_compatible" ]] \
    || fail "candidate changes the Python/Node runtime baseline; rerun only after the clean migration rehearsal and explicit --approve-runtime-migration"
  controller_python="$active_python"
  reviewed_node="$(
    resolve_formula_node "$active_node_formula" "$active_node_preferred"
  )"
  node_bin_dir="$(cd "$(dirname "$reviewed_node")" && pwd -P)"
fi

controller_tooling="${ADMINME_CONTROLLER_RUNTIME_ROOT:-}"
[[ -n "$controller_tooling" \
  && "$controller_tooling" == "$(dirname "$fixed_controller_root")/runtime" \
  && -d "$controller_tooling" && ! -L "$controller_tooling" \
  && -O "$controller_tooling" \
  && "$(stat -f '%Lp' "$controller_tooling")" == "700" ]] \
  || fail "fixed update-controller runtime coordinate is missing"
poetry_venv="$controller_tooling/poetry"
controller="$controller_tooling/controller"
"$controller_python" -m venv "$poetry_venv"
chmod 700 "$poetry_venv"
"$poetry_venv/bin/python" -m pip install \
  --disable-pip-version-check "poetry==$poetry_version"
[[ -d "$poetry_venv" && ! -L "$poetry_venv" && -O "$poetry_venv" \
  && -f "$poetry_venv/bin/poetry" && ! -L "$poetry_venv/bin/poetry" \
  && -O "$poetry_venv/bin/poetry" \
  && "$(stat -f '%Lp' "$poetry_venv")" == "700" \
  && "$(stat -f '%Lp' "$poetry_venv/bin/poetry")" == "755" ]] \
  || fail "the reviewed Poetry tooling coordinate is unsafe"
[[ "$("$poetry_venv/bin/poetry" --version)" == "Poetry (version $poetry_version)" ]] \
  || fail "the reviewed Poetry tooling coordinate has the wrong version"

"$controller_python" -m venv "$controller"
chmod 700 "$controller"
[[ -d "$controller" && ! -L "$controller" && -O "$controller" \
  && -x "$controller/bin/python" ]] \
  || fail "the candidate upgrade-controller coordinate is unsafe"
[[ "$("$controller/bin/python" --version)" == "Python $python_preferred" ]] \
  || fail "the candidate upgrade-controller has the wrong Python version"
(
  cd "$fixed_controller_root"
  PATH="$node_bin_dir:$PATH" VIRTUAL_ENV="$controller" \
    "$poetry_venv/bin/poetry" sync --only main --no-interaction
)

# Close the ordinary edit-during-preparation gap, then prove the controller
# imports the reviewed candidate's policy rather than the active release's.
[[ "$(safe_git -C "$candidate" rev-parse HEAD)" == "$candidate_commit" \
  && "$(git_is_clean "$candidate" && printf clean)" == "clean" ]] \
  || fail "candidate checkout changed while the upgrade controller was prepared"
controller_source="$(
  cd "$fixed_controller_root"
  PATH="$node_bin_dir:$PATH" \
    ADMINME_REPOSITORY_ROOT="$fixed_controller_root" \
    "$controller/bin/python" - <<'PY'
from pathlib import Path

import bootstrap.upgrade

print(Path(bootstrap.upgrade.__file__).resolve())
PY
)"
[[ "$controller_source" == "$fixed_controller_root/bootstrap/upgrade.py" ]] \
  || fail "candidate upgrade-controller provenance attestation failed"

if [[ -n "$approve_flag" ]]; then
  runtime_tag="py${python_minor//./}"
  "$active_python" "$fixed_controller_root/bootstrap/keychain_runtime_migration.py" \
    --instance-root "$ADMINME_INSTANCE_ROOT" \
    --candidate-python "$controller/bin/python" \
    --candidate-commit "$candidate_commit" \
    --runtime-tag "$runtime_tag"
fi

upgrade_args=(upgrade "$candidate")
if [[ -n "$pending_flag" ]]; then
  upgrade_args+=("$pending_flag")
fi
if [[ -n "$approve_flag" ]]; then
  upgrade_args+=("$approve_flag")
fi
if [[ -n "$source_repair_flag" ]]; then
  upgrade_args+=("$source_repair_flag")
fi
(
  cd "$fixed_controller_root"
  PATH="$node_bin_dir:$PATH" \
    ADMINME_REPOSITORY_ROOT="$fixed_controller_root" \
    ADMINME_UPGRADE_CONTROLLER_COMMIT="$supervised_commit" \
    ADMINME_UPGRADE_CONTROLLER_ROOT="$fixed_controller_root" \
    ADMINME_PREPARATION_POETRY="$poetry_venv/bin/poetry" \
    "$controller/bin/python" -m bootstrap.cli \
      --instance-root "$ADMINME_INSTANCE_ROOT" \
      "${upgrade_args[@]}"
)
