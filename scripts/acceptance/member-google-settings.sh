#!/usr/bin/env bash
# Focused member consent, permission, persistence and rendered-settings checks.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true ]] || { echo "Use isolated hosted CI" >&2; exit 1; }
case "${1:-}" in linux|macos) ;; *) echo "Expected linux or macos" >&2; exit 1 ;; esac

tests=(
  tests/unit/test_member_google.py
  tests/unit/test_google_oauth_transport.py
  tests/unit/bootstrap/test_member_google_oauth.py
  tests/acceptance/test_member_google_settings.py
  tests/acceptance/test_member_google_readiness_transition.py
  tests/acceptance/test_bridge_readiness_transition.py
  tests/unit/bootstrap/test_provider_binding_transition.py
  tests/acceptance/test_member_google_connections.py
  tests/unit/daemons/test_adapter_supervisor.py
  tests/adapters/test_credentials.py
  tests/unit/test_provider_bindings.py
  tests/acceptance/test_cos_service_working_context.py
  tests/acceptance/test_cos_service_planning_context.py
  tests/acceptance/test_cos_identity_delegation_kernel.py
  tests/acceptance/test_forwarded_channel_intake.py
  tests/acceptance/test_cos_response_execution.py
  tests/integration/test_adapter_google_calendar_supervisor.py
  tests/integration/test_adapter_google_tasks_supervisor.py
  tests/integration/test_gmail_observation_receipts.py
  tests/unit/adapters/calendaring/test_google_calendar.py
  tests/unit/adapters/tasks/test_google_tasks.py
  tests/unit/test_source_policy_v2.py
  tests/unit/test_governance.py
  tests/unit/test_lifecycle_catalog.py
  tests/unit/test_ci_release_gates.py
)
python_checks() {
  poetry run pytest -p no:cacheprovider --runxfail -q -ra \
    --basetemp="$RUNNER_TEMP/member-google-settings-tests" \
    --junitxml="$RUNNER_TEMP/google-connections.xml" "${tests[@]}"
  python - "$RUNNER_TEMP/google-connections.xml" <<'PY_CHECK'
import sys
import xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
suites = list(root.iter("testsuite"))
assert suites and sum(int(s.attrib["tests"]) for s in suites) > 0
assert all(int(s.attrib[key]) == 0 for s in suites for key in ("skipped", "failures", "errors"))
assert not any(list(root.iter(k)) for k in ("skipped", "failure", "error"))
PY_CHECK
}
static_checks() {
  poetry run ruff check adminme bootstrap scripts tests
  poetry run ruff format --check adminme bootstrap scripts tests
  poetry run mypy adminme bridge/adminme_bridge bootstrap scripts/release_scope.py
  poetry run pyright adminme bridge/adminme_bridge bootstrap
  poetry run python scripts/generate_event_lifecycle_catalog.py --check
  bash scripts/verify_invariants.sh
  poetry check --lock
  node --check adminme/products/member/assets/member.js
  node --check tools/console-reference-harness/google-connections.mjs
  git diff --check
}
browser_checks() {
  npm ci --prefix tools/console-reference-harness --ignore-scripts
  ADMINME_GATE7_GOOGLE_CONNECTIONS_ONLY=1 \
  ADMINME_GATE7_PYTHON="$(poetry env info --executable)" \
  ADMINME_GATE7_EVIDENCE_DIR="${ADMINME_MEMBER_GOOGLE_EVIDENCE_DIR:-$RUNNER_TEMP/member-google-settings}" \
  npm run test:gate7 --prefix tools/console-reference-harness
}
names=(python static browser)
pids=()
python_checks > "$RUNNER_TEMP/member-google-settings-python.log" 2>&1 & pids+=("$!")
static_checks > "$RUNNER_TEMP/member-google-settings-static.log" 2>&1 & pids+=("$!")
browser_checks > "$RUNNER_TEMP/member-google-settings-browser.log" 2>&1 & pids+=("$!")
failed=0
for index in "${!pids[@]}"; do
  status=0
  wait "${pids[$index]}" || status=$?
  printf '::group::%s checks (exit %s)\n' "${names[$index]}" "$status"
  cat "$RUNNER_TEMP/member-google-settings-${names[$index]}.log" || failed=1
  echo "::endgroup::"
  [[ "$status" == 0 ]] || failed=1
done
test -z "$(git status --porcelain)" || failed=1
exit "$failed"
