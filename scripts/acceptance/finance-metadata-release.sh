#!/usr/bin/env bash
# Hosted owner-metadata proof. No household imports, grants or Finance benchmark.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true ]] || { echo "Use isolated hosted CI" >&2; exit 1; }
case "${1:-}" in linux|macos) ;; *) echo "Expected linux or macos" >&2; exit 1 ;; esac
output="$RUNNER_TEMP/finance-metadata"
mkdir -p "$output"
git rev-parse HEAD HEAD:adminme HEAD:poetry.lock > "$output/source-pins.txt"
tests=(
  tests/acceptance/test_finance_metadata_owner.py
  tests/acceptance/test_finance_metadata_history.py
  tests/unit/test_event_log.py
  tests/unit/test_schema_registry.py
  tests/acceptance/test_event_log_metadata_migration.py
  tests/unit/test_ci_release_gates.py
)
sources=(
  adminme/finance adminme/events/log.py
  adminme/events/schemas/finance_dated_projection.py
  adminme/events/schemas/finance_metadata.py
  adminme/events/schemas/finance_publications.py
  adminme/products/member/finance_metadata.py
  scripts/release_scope.py
)
poetry run ruff check "${sources[@]}" "${tests[@]}"
poetry run ruff format --check "${sources[@]}" "${tests[@]}"
poetry run mypy "${sources[@]}"
poetry run pyright "${sources[@]}"
node --check adminme/products/member/assets/member.js
node --check tools/console-reference-harness/finance-metadata.mjs
node --check tools/console-reference-harness/run-gate7.mjs
poetry run pytest -p no:cacheprovider --runxfail -q -ra \
  --basetemp="$RUNNER_TEMP/finance-metadata-tests" \
  --junitxml="$output/results.xml" "${tests[@]}"
python - "$output/results.xml" <<'PY_CHECK'
import sys
import xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
suites = list(root.iter("testsuite"))
assert suites
for suite in suites:
    assert all(k in suite.attrib and suite.attrib[k].isdigit() for k in ("tests", "skipped", "failures", "errors"))
    assert all(int(suite.attrib[k]) == 0 for k in ("skipped", "failures", "errors"))
tests = sum(int(suite.attrib["tests"]) for suite in suites)
assert tests > 0 and len(list(root.iter("testcase"))) == tests
assert not any(list(root.iter(k)) for k in ("skipped", "failure", "error"))
PY_CHECK
# One hosted browser platform suffices for unchanged cross-platform assets.
# Both platforms above run the actual SQLCipher/native persistence contracts.
if [[ "$1" == linux ]]; then
  npm ci --prefix tools/console-reference-harness --ignore-scripts
  ADMINME_GATE7_FINANCE_METADATA_ONLY=1 \
  ADMINME_GATE7_PYTHON="$(poetry env info --executable)" \
  ADMINME_GATE7_EVIDENCE_DIR="$output/browser" \
    npm run test:gate7 --prefix tools/console-reference-harness
fi
bash -n scripts/acceptance/finance-metadata-release.sh
bash scripts/verify_invariants.sh
poetry check --lock
git diff --check
test -z "$(git status --porcelain)"
