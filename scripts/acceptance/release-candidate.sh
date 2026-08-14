#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$REPOSITORY_ROOT"

export PYTHONPATH=.:bridge

# The release rehearsal validates the candidate manifest, not whichever Node
# happens to be globally linked on the host. Prefer the reviewed side-by-side
# Homebrew formula when it is installed; GitHub's Apple runner instead supplies
# the same compatible runtime through actions/setup-node.
IFS=$'\t' read -r candidate_node_formula candidate_node_compatible < <(
  poetry run python -c \
    'import json; v=json.load(open("bootstrap/versions.json", encoding="utf-8"))["node"]; print(v["formula"], v["compatible"], sep="\t")'
)
[[ "$candidate_node_formula" =~ ^node@[0-9]+$ && -n "$candidate_node_compatible" ]] \
  || { echo "invalid candidate Node coordinates" >&2; exit 1; }
if command -v brew >/dev/null 2>&1 \
  && candidate_node_prefix="$(brew --prefix "$candidate_node_formula" 2>/dev/null)" \
  && [[ -x "$candidate_node_prefix/bin/node" ]]; then
  PATH="$candidate_node_prefix/bin:$PATH"
  export PATH
fi
candidate_node_executable="$(command -v node || true)"
[[ -x "$candidate_node_executable" ]] \
  || { echo "candidate Node runtime is unavailable" >&2; exit 1; }
candidate_node_version="$("$candidate_node_executable" -p 'process.versions.node')"
poetry run python - "$candidate_node_version" "$candidate_node_compatible" <<'PY'
from packaging.specifiers import SpecifierSet
from packaging.version import Version
import sys

if Version(sys.argv[1]) not in SpecifierSet(sys.argv[2]):
    raise SystemExit(
        f"candidate Node {sys.argv[1]} does not satisfy {sys.argv[2]}"
    )
PY
unset candidate_node_compatible candidate_node_executable candidate_node_formula
unset candidate_node_prefix candidate_node_version

poetry run pytest tests/acceptance -q --runxfail
poetry run pytest tests packs -q --import-mode=importlib
poetry -C bridge sync --with dev --no-interaction
poetry -C bridge run pytest -q
poetry -C bridge run ruff check adminme_bridge tests
poetry -C bridge run mypy adminme_bridge
poetry -C bridge run pip-audit --local --progress-spinner off
poetry run ruff check .
poetry run ruff format --check adminme bootstrap bridge packs scripts tests tools
poetry run mypy adminme bridge/adminme_bridge bootstrap
poetry run pyright adminme bridge/adminme_bridge bootstrap packs
poetry run pip-audit --local --progress-spinner off
poetry run python scripts/acceptance/performance-smoke.py
poetry check --lock
poetry build
poetry -C bridge check --lock
poetry -C bridge build

poetry run bash scripts/acceptance/architecture-negative-checks.sh
poetry run bash scripts/acceptance/clean-install.sh
poetry run bash scripts/acceptance/tenant-isolation.sh
poetry run bash scripts/acceptance/backup-restore.sh

(
  cd openclaw/administrateme
  npm ci --ignore-scripts
  npm run check
  npm test
  npm run build
  npm pack --dry-run >/dev/null
  npm audit --omit=dev
)

shellcheck -x bootstrap/*.sh scripts/acceptance/*.sh scripts/verify_invariants.sh
bash -n bootstrap/*.sh scripts/acceptance/*.sh scripts/verify_invariants.sh
PYTHONPATH=bridge poetry run python -m scripts.harness.cli diff-hygiene

echo "ADMINISTRATEME RELEASE CANDIDATE SUITE: PASS"
