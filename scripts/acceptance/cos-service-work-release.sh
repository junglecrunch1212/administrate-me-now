#!/usr/bin/env bash
# Focused hosted identity/isolation/replay proof; never run against a live instance.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true ]] || { echo "Use isolated hosted CI" >&2; exit 1; }
case "${1:-}" in linux|macos) ;; *) echo "Expected linux or macos" >&2; exit 1 ;; esac

tests=(
  tests/acceptance/test_cos_workstation_handoff.py
  tests/acceptance/test_authenticated_request_actions.py
  tests/acceptance/test_authenticated_conversation_channels.py
  tests/acceptance/test_authenticated_conversation_reply.py
  tests/acceptance/test_cos_response_execution.py
  tests/acceptance/test_gate7_household_experience.py
  tests/unit/test_member_chat_protocol.py
  tests/unit/test_openclaw_approvals.py
  tests/unit/test_request_actions_control.py
  tests/unit/test_workstation_inventory.py
  tests/acceptance/test_workstation_inventory.py
  tests/acceptance/test_native_apple_reminder.py
  tests/acceptance/test_apple_service_permissions.py
  tests/acceptance/test_member_apple_approval.py
  tests/acceptance/test_apple_service_notes.py
  tests/unit/test_apple_service_permission_contract.py
  tests/acceptance/test_cos_member_follow_up.py
  tests/acceptance/test_cos_service_private_work.py
  tests/acceptance/test_cos_service_working_context.py
  tests/acceptance/test_cos_service_planning_context.py
  tests/unit/test_projection_calendars.py
  tests/unit/projections/lists/test_handlers.py
  tests/unit/projections/lists/test_queries.py
  tests/integration/test_lists_projection_runner.py
  tests/integration/test_service_owned_google_sources.py
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
if [[ "$1" == macos ]]; then
  tests+=(tests/acceptance/test_native_reminder_due_components.py)
fi
python_checks() {
  local status=0
  poetry run pytest -p no:cacheprovider --runxfail -q -ra \
    --basetemp="$RUNNER_TEMP/cos-service-work-tests" \
    --junitxml="$RUNNER_TEMP/cos-service-work.xml" "${tests[@]}" || status=$?
  python - "$RUNNER_TEMP/cos-service-work.xml" <<'PY_CHECK' || status=$?
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
  return "$status"
}

plugin_checks() {
  local status=0
  npm run check --prefix openclaw/administrateme || status=$?
  npm run build --prefix openclaw/administrateme || status=$?
  npm test --prefix openclaw/administrateme || status=$?
  if [[ "$1" == macos ]]; then
    bash scripts/acceptance/openclaw-source-install.sh --service-work || status=$?
  fi
  return "$status"
}

static_checks() {
  local status=0
  poetry run ruff check adminme bootstrap scripts/release_scope.py "${tests[@]%%::*}" || status=$?
  poetry run ruff format --check adminme bootstrap scripts/release_scope.py "${tests[@]%%::*}" || status=$?
  poetry run mypy adminme bridge/adminme_bridge bootstrap scripts/release_scope.py || status=$?
  poetry run pyright adminme bridge/adminme_bridge bootstrap scripts/release_scope.py || status=$?
  poetry run python scripts/generate_event_lifecycle_catalog.py --check || status=$?
  bash -n scripts/acceptance/cos-service-work-release.sh scripts/acceptance/openclaw-source-install.sh || status=$?
  bash scripts/verify_invariants.sh || status=$?
  poetry check --lock || status=$?
  return "$status"
}

# Independent groups finish even when another fails; every result gates release.
names=(python plugin static)
logs=("$RUNNER_TEMP/cos-service-work-python.log" "$RUNNER_TEMP/cos-service-work-plugin.log" "$RUNNER_TEMP/cos-service-work-static.log")
pids=()
echo "Starting Python, plugin/native, and static check groups concurrently"
python_checks > "${logs[0]}" 2>&1 &
pids+=("$!")
plugin_checks "$1" > "${logs[1]}" 2>&1 &
pids+=("$!")
static_checks > "${logs[2]}" 2>&1 &
pids+=("$!")

failed=0
for index in "${!pids[@]}"; do
  status=0
  wait "${pids[$index]}" || status=$?
  printf '::group::%s checks (exit %s)\n' "${names[$index]}" "$status"
  cat "${logs[$index]}" || failed=1
  echo "::endgroup::"
  if [[ "$status" -ne 0 ]]; then
    failed=1
    printf '::error::%s checks failed with exit %s\n' "${names[$index]}" "$status"
  fi
done
git diff --check || failed=1
test -z "$(git status --porcelain)" || failed=1
exit "$failed"
