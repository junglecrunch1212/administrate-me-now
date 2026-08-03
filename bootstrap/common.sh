#!/bin/bash
set -Eeuo pipefail
umask 077

BOOTSTRAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$BOOTSTRAP_DIR/.." && pwd -P)"
DEFAULT_INSTANCE_ROOT="$HOME/Library/Application Support/AdministrateMe"
ACTIVE_RELEASE_ROOT=""

fail() {
  echo "AdministrateMe: $1" >&2
  exit 1
}

resolve_instance_root() {
  local requested="${ADMINME_INSTANCE_ROOT:-$DEFAULT_INSTANCE_ROOT}"
  if [[ "$requested" != /* ]]; then
    fail "the instance root must be an absolute path"
  fi
  if [[ "$requested" == "/" || "$requested" == "$HOME" ]]; then
    fail "refusing a broad instance root"
  fi
  ADMINME_INSTANCE_ROOT="$requested"
  export ADMINME_INSTANCE_ROOT
}

resolve_active_release() {
  ACTIVE_RELEASE_ROOT="$REPOSITORY_ROOT"
  local pointer="$ADMINME_INSTANCE_ROOT/app/current-release.json"
  if [[ ! -f "$pointer" ]]; then
    export ACTIVE_RELEASE_ROOT
    return
  fi
  [[ ! -L "$pointer" ]] || fail "refusing a symlinked active-release pointer"
  local parser
  parser="$(command -v python3.11 || command -v python3 || command -v python || true)"
  [[ -n "$parser" ]] || fail "Python is unavailable for the active-release pointer"
  local candidate
  candidate="$("$parser" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["path"])' "$pointer")" \
    || fail "the active-release pointer is invalid"
  case "$candidate" in
    "$ADMINME_INSTANCE_ROOT"/app/releases/*) ;;
    *) fail "the active-release pointer escapes the deployment release directory" ;;
  esac
  [[ -d "$candidate" ]] || fail "the active release directory is missing"
  ACTIVE_RELEASE_ROOT="$candidate"
  export ACTIVE_RELEASE_ROOT
}

select_python() {
  if [[ -n "${ADMINME_PYTHON:-}" && -x "$ADMINME_PYTHON" ]]; then
    printf '%s\n' "$ADMINME_PYTHON"
    return
  fi
  if [[ -x "$ACTIVE_RELEASE_ROOT/.venv/bin/python" ]]; then
    printf '%s\n' "$ACTIVE_RELEASE_ROOT/.venv/bin/python"
    return
  fi
  command -v python3 || command -v python || fail "Python is unavailable"
}

run_cli() {
  local forwarded=()
  while (($#)); do
    if [[ "$1" == "--instance-root" ]]; then
      (($# >= 2)) || fail "--instance-root requires a value"
      ADMINME_INSTANCE_ROOT="$2"
      shift 2
    else
      forwarded+=("$1")
      shift
    fi
  done
  resolve_instance_root
  resolve_active_release
  local python_bin
  python_bin="$(select_python)"
  ADMINME_REPOSITORY_ROOT="$ACTIVE_RELEASE_ROOT" \
    "$python_bin" -m bootstrap.cli --instance-root "$ADMINME_INSTANCE_ROOT" "${forwarded[@]}"
}
