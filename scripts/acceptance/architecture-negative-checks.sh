#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$REPOSITORY_ROOT"

bash scripts/verify_invariants.sh

if grep -RInE '^\s*(from|import)\s+(anthropic|openai|sentence_transformers)(\.|\s|$)' \
  adminme bootstrap packs --include='*.py'; then
  echo "direct model/embedding SDK import found" >&2
  exit 1
fi

if grep -RInE '\.(notify|publish)\(' adminme/adapters adminme/pipelines packs \
  --include='*.py' | grep -v '/tests/'; then
  echo "manual bus notification found outside the event-writer boundary" >&2
  exit 1
fi

if grep -RInE '(innerHTML|eval\(|exec\()' \
  adminme/temporary_surfaces.py adminme/products/member \
  --include='*.py' --include='*.js' --include='*.html'; then
  echo "executable temporary-surface path found" >&2
  exit 1
fi

if grep -nE '(execute\(|executescript\(|\.event_log)' adminme/temporary_surfaces.py; then
  echo "temporary-surface store reaches SQL or the event log directly" >&2
  exit 1
fi

grep -q '"sql", "code", "template", "script", "event_log", "raw_log"' \
  adminme/temporary_surfaces.py || {
  echo "temporary-surface forbidden-field denylist is incomplete" >&2
  exit 1
}

if grep -RIn '3337' adminme bootstrap bridge packs openclaw \
  --include='*.py' --include='*.md' --include='*.toml' --include='*.json' --include='*.sh'; then
  echo "retired standalone bridge port 3337 found" >&2
  exit 1
fi

poetry run python - <<'PY'
from adminme.projections.runner import DEFAULT_PROJECTION_CLASSES

names = {projection.name for projection in DEFAULT_PROJECTION_CLASSES}
if len(names) != 13:
    raise SystemExit(f"expected exactly 13 projections, found {len(names)}: {sorted(names)}")
PY

echo "ADMINISTRATEME ARCHITECTURE NEGATIVE CHECKS: PASS"
