#!/bin/bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=bootstrap/common.sh
source "$SCRIPT_DIR/common.sh"
resolve_instance_root
approve_flag=""
if [[ "${1:-}" == "--approve-runtime-migration" ]]; then
  approve_flag="$1"
  shift
fi
candidate="${1:-}"
[[ -n "$candidate" ]] || fail "usage: update.sh [--approve-runtime-migration] /absolute/path/to/reviewed-candidate"
[[ "$candidate" == /* ]] || fail "candidate path must be absolute"
if [[ -n "$approve_flag" ]]; then
  [[ -d "$candidate/.git" ]] \
    || fail "runtime migration requires a clean reviewed Git checkout"
  candidate_status="$(git -C "$candidate" status --porcelain --untracked-files=all)"
  [[ -z "$candidate_status" ]] \
    || fail "runtime migration refuses a candidate checkout with local changes"
  candidate_commit="$(git -C "$candidate" rev-parse HEAD)"
  [[ "$candidate_commit" =~ ^[0-9a-f]{40}$ ]] \
    || fail "runtime migration could not resolve the reviewed candidate commit"
  manifest="$candidate/bootstrap/versions.json"
  [[ -f "$manifest" ]] || fail "candidate version manifest is missing"
  manifest_parser="$(command -v python3.14 || command -v python3.11 || command -v python3 || command -v python || true)"
  [[ -n "$manifest_parser" ]] || fail "Python is unavailable for the candidate manifest"
  IFS=$'\t' read -r python_formula python_minor node_formula poetry_version < <(
    "$manifest_parser" - "$manifest" <<'PY'
import json
import sys

versions = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    versions["python"]["formula"],
    str(versions["python"]["preferred"]).rsplit(".", 1)[0],
    versions["node"]["formula"],
    versions["package_managers"]["poetry"],
    sep="\t",
)
PY
  )
  [[ "$python_formula" =~ ^python@[0-9]+\.[0-9]+$ \
    && "$python_minor" =~ ^[0-9]+\.[0-9]+$ \
    && "$node_formula" =~ ^node@[0-9]+$ \
    && "$poetry_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || fail "candidate runtime manifest has invalid tooling coordinates"
  command -v brew >/dev/null 2>&1 || fail "Homebrew is unavailable"
  python_bin="$(brew --prefix "$python_formula")/bin/python$python_minor"
  node_bin_dir="$(brew --prefix "$node_formula")/bin"
  [[ -x "$python_bin" && -x "$node_bin_dir/node" ]] \
    || fail "install the candidate Python and Node formulae side by side before migration"

  poetry_venv="$ADMINME_INSTANCE_ROOT/app/tooling/poetry-$poetry_version"
  if [[ ! -x "$poetry_venv/bin/poetry" ]]; then
    "$python_bin" -m venv "$poetry_venv"
    "$poetry_venv/bin/python" -m pip install --disable-pip-version-check "poetry==$poetry_version"
  fi
  controller="$ADMINME_INSTANCE_ROOT/app/tooling/upgrade-controller-$candidate_commit"
  if [[ ! -x "$controller/bin/python" ]]; then
    "$python_bin" -m venv "$controller"
  fi
  (
    cd "$candidate"
    PATH="$node_bin_dir:$PATH" VIRTUAL_ENV="$controller" \
      "$poetry_venv/bin/poetry" sync --only main --no-interaction
  )
  resolve_active_release
  active_python="$ACTIVE_RELEASE_ROOT/.venv/bin/python"
  [[ -x "$active_python" ]] \
    || fail "runtime migration requires the active release interpreter"
  runtime_tag="py${python_minor//./}"
  "$active_python" "$candidate/bootstrap/keychain_runtime_migration.py" \
    --instance-root "$ADMINME_INSTANCE_ROOT" \
    --candidate-python "$controller/bin/python" \
    --candidate-commit "$candidate_commit" \
    --runtime-tag "$runtime_tag"
  (
    cd "$candidate"
    PATH="$node_bin_dir:$PATH" \
      ADMINME_REPOSITORY_ROOT="$candidate" \
      "$controller/bin/python" -m bootstrap.cli \
        --instance-root "$ADMINME_INSTANCE_ROOT" \
        upgrade "$candidate" "$approve_flag"
  )
else
  run_cli upgrade "$candidate"
fi
