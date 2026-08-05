#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$REPOSITORY_ROOT"

export PYTHONPATH=.:bridge

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
git diff --check

echo "ADMINISTRATEME RELEASE CANDIDATE SUITE: PASS"
