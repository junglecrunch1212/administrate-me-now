#!/bin/bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=bootstrap/common.sh
source "$SCRIPT_DIR/common.sh"

resolve_instance_root

approve_flag=""
source_repair_flag=""
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
    *)
      fail "unknown update option: $1"
      ;;
  esac
  shift
done

candidate="${1:-}"
[[ -n "$candidate" ]] \
  || fail "usage: update.sh [--approve-runtime-migration] [--approve-declared-source-assessment-repair] /absolute/path/to/reviewed-candidate"
[[ "$candidate" == /* ]] || fail "candidate path must be absolute"
[[ -d "$candidate" ]] || fail "candidate checkout does not exist"
candidate="$(cd "$candidate" && pwd -P)"

# Every upgrade policy is candidate code. Prove that code is a stable reviewed
# Git commit before it is allowed to prepare a release or control a switch.
[[ "$(git -C "$candidate" rev-parse --is-inside-work-tree 2>/dev/null || true)" == "true" ]] \
  || fail "upgrade requires a clean reviewed Git checkout"
candidate_status="$(git -C "$candidate" status --porcelain --untracked-files=all)"
[[ -z "$candidate_status" ]] \
  || fail "upgrade refuses a candidate checkout with local changes"
candidate_commit="$(git -C "$candidate" rev-parse HEAD)"
[[ "$candidate_commit" =~ ^[0-9a-f]{40}$ ]] \
  || fail "upgrade could not resolve the reviewed candidate commit"

manifest="$candidate/bootstrap/versions.json"
[[ -f "$manifest" ]] || fail "candidate version manifest is missing"
manifest_parser="$(command -v python3.14 || command -v python3.11 || command -v python3 || command -v python || true)"
[[ -n "$manifest_parser" ]] || fail "Python is unavailable for the candidate manifest"
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

resolve_active_release
configure_active_runtime_path
active_python="$ACTIVE_RELEASE_ROOT/.venv/bin/python"
[[ -x "$active_python" ]] || fail "upgrade requires the active release interpreter"

if [[ -n "$approve_flag" ]]; then
  command -v brew >/dev/null 2>&1 || fail "Homebrew is unavailable"
  controller_python="$(brew --prefix "$python_formula")/bin/python$python_minor"
  node_bin_dir="$(brew --prefix "$node_formula")/bin"
  [[ -x "$controller_python" && -x "$node_bin_dir/node" ]] \
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
  active_node="$(command -v node || true)"
  [[ -x "$active_node" ]] || fail "active release Node is unavailable"
  node_bin_dir="$(cd "$(dirname "$active_node")" && pwd -P)"
fi

poetry_venv="$ADMINME_INSTANCE_ROOT/app/tooling/poetry-$poetry_version"
if [[ ! -x "$poetry_venv/bin/poetry" ]]; then
  "$controller_python" -m venv "$poetry_venv"
  "$poetry_venv/bin/python" -m pip install \
    --disable-pip-version-check "poetry==$poetry_version"
fi

controller="$ADMINME_INSTANCE_ROOT/app/tooling/upgrade-controller-$candidate_commit"
if [[ ! -x "$controller/bin/python" ]]; then
  "$controller_python" -m venv "$controller"
fi
(
  cd "$candidate"
  PATH="$node_bin_dir:$PATH" VIRTUAL_ENV="$controller" \
    "$poetry_venv/bin/poetry" sync --only main --no-interaction
)

# Close the ordinary edit-during-preparation gap, then prove the controller
# imports the reviewed candidate's policy rather than the active release's.
[[ "$(git -C "$candidate" rev-parse HEAD)" == "$candidate_commit" \
  && -z "$(git -C "$candidate" status --porcelain --untracked-files=all)" ]] \
  || fail "candidate checkout changed while the upgrade controller was prepared"
controller_source="$(
  cd "$candidate"
  PATH="$node_bin_dir:$PATH" \
    ADMINME_REPOSITORY_ROOT="$candidate" \
    "$controller/bin/python" - <<'PY'
from pathlib import Path

import bootstrap.upgrade

print(Path(bootstrap.upgrade.__file__).resolve())
PY
)"
[[ "$controller_source" == "$candidate/bootstrap/upgrade.py" ]] \
  || fail "candidate upgrade-controller provenance attestation failed"

if [[ -n "$approve_flag" ]]; then
  runtime_tag="py${python_minor//./}"
  "$active_python" "$candidate/bootstrap/keychain_runtime_migration.py" \
    --instance-root "$ADMINME_INSTANCE_ROOT" \
    --candidate-python "$controller/bin/python" \
    --candidate-commit "$candidate_commit" \
    --runtime-tag "$runtime_tag"
fi

upgrade_args=(upgrade "$candidate")
if [[ -n "$approve_flag" ]]; then
  upgrade_args+=("$approve_flag")
fi
if [[ -n "$source_repair_flag" ]]; then
  upgrade_args+=("$source_repair_flag")
fi
(
  cd "$candidate"
  PATH="$node_bin_dir:$PATH" \
    ADMINME_REPOSITORY_ROOT="$candidate" \
    ADMINME_UPGRADE_CONTROLLER_COMMIT="$candidate_commit" \
    "$controller/bin/python" -m bootstrap.cli \
      --instance-root "$ADMINME_INSTANCE_ROOT" \
      "${upgrade_args[@]}"
)
