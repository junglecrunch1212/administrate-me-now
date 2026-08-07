#!/bin/bash
set -Eeuo pipefail
umask 077

BRIDGE_ROOT="${ADMINME_BRIDGE_ROOT:-$HOME/Library/Application Support/AdministrateMe Bridge}"
POINTER="$BRIDGE_ROOT/app/current-release.json"
[[ -f "$POINTER" && ! -L "$POINTER" ]] || {
  echo "AdministrateMe member bridge is not installed." >&2
  exit 1
}
python_bin="$(command -v python3.14 || command -v python3.11 || command -v python3 || command -v python)"
release_root="$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["path"])' "$POINTER")"
case "$release_root" in
  "$BRIDGE_ROOT"/app/releases/*) ;;
  *) echo "AdministrateMe member-bridge release pointer is unsafe." >&2; exit 1 ;;
esac
[[ -x "$release_root/.venv/bin/python" ]] || {
  echo "AdministrateMe member-bridge Python environment is missing." >&2
  exit 1
}
export ADMINME_REPOSITORY_ROOT="$release_root"
cd "$release_root"
exec "$release_root/.venv/bin/python" -m bootstrap.cli "$@" --bridge-root "$BRIDGE_ROOT"
