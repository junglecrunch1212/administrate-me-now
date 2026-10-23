#!/usr/bin/env bash
# Focused hosted identity/isolation/replay proof; never run against a live instance.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true ]] || { echo "Use isolated hosted CI" >&2; exit 1; }
case "${1:-}" in linux|macos) ;; *) echo "Expected linux or macos" >&2; exit 1 ;; esac

tests=(
  tests/acceptance/test_cos_service_private_work.py
  tests/acceptance/test_cos_service_working_context.py
  tests/unit/test_service_work_principal.py
  tests/unit/test_service_identities.py
  tests/unit/test_operating_agreements.py
  tests/unit/test_source_policy_v2.py
  tests/unit/test_scope.py
  tests/unit/test_session.py
  tests/unit/test_event_validation.py
  tests/unit/test_event_schemas.py
  tests/unit/test_schema_registry.py
  tests/unit/test_lifecycle_catalog.py
  tests/unit/test_projection_tasks.py
  tests/unit/test_capability_catalog.py
  tests/integration/test_projection_scope_canary.py
  tests/integration/test_projection_rebuild.py::test_projection_native_lifecycle_stays_on_one_owner_worker
  tests/acceptance/test_cos_identity_delegation_kernel.py
  tests/acceptance/test_cos_work_coordination_operating_agreements.py
  tests/acceptance/test_openclaw_native_seam.py
  tests/acceptance/test_member_chat_subscription.py
  tests/acceptance/test_member_cos_approval.py
  tests/acceptance/test_cos_self_reminders.py
  tests/acceptance/test_cos_self_replies.py
  tests/unit/bootstrap/test_cos_service_topology.py
  tests/unit/bootstrap/test_cos_member_topology.py
  tests/unit/bootstrap/test_topology_readiness.py
  tests/unit/bootstrap/test_cos_channel_activation.py
  tests/unit/bootstrap/test_openclaw_plugin_reconciliation.py
  tests/unit/bootstrap/test_cos_guard_release.py
  tests/unit/test_ci_release_gates.py
)
poetry run pytest -p no:cacheprovider --runxfail -q -ra \
  --basetemp="$RUNNER_TEMP/cos-service-work-tests" \
  --junitxml="$RUNNER_TEMP/cos-service-work.xml" "${tests[@]}"
python - "$RUNNER_TEMP/cos-service-work.xml" <<'PY_CHECK'
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
npm run check --prefix openclaw/administrateme
npm run build --prefix openclaw/administrateme
npm test --prefix openclaw/administrateme
poetry run ruff check adminme bootstrap scripts/release_scope.py "${tests[@]%%::*}"
poetry run ruff format --check adminme bootstrap scripts/release_scope.py "${tests[@]%%::*}"
poetry run mypy adminme bridge/adminme_bridge bootstrap scripts/release_scope.py
poetry run pyright adminme bridge/adminme_bridge bootstrap scripts/release_scope.py
poetry run python scripts/generate_event_lifecycle_catalog.py --check
bash -n scripts/acceptance/cos-service-work-release.sh
bash scripts/verify_invariants.sh
poetry check --lock
if [[ "$1" == macos ]]; then
  bash scripts/acceptance/openclaw-source-install.sh --service-work
fi
git diff --check
test -z "$(git status --porcelain)"
