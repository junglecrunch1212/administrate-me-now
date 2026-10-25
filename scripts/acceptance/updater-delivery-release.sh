#!/usr/bin/env bash
# Focused hosted updater proof; candidate behavior never runs on the live host.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true ]] || { echo "Use isolated hosted CI" >&2; exit 1; }
case "${1:-}" in linux|macos) ;; *) echo "Expected linux or macos" >&2; exit 1 ;; esac

tests=(
  tests/unit/bootstrap/test_source_identity.py
  tests/unit/bootstrap/test_activation_transaction.py
  tests/acceptance/test_forwarded_submission_repair_operations.py
  tests/unit/bootstrap/test_cos_channel_activation.py
  tests/unit/bootstrap/test_backup_rehearsal.py
  tests/unit/bootstrap/test_candidate_readiness.py
  tests/unit/bootstrap/test_recovery_readiness.py
  tests/unit/bootstrap/test_phase5_operations.py
  tests/unit/bootstrap/test_openclaw_plugin_reconciliation.py
  tests/unit/bootstrap/test_cos_guard_release.py
  tests/acceptance/test_upgrade_deadline_rollback.py
  tests/acceptance/test_upgrade_readiness_blackout.py
  tests/acceptance/test_recovery_readiness_baseline.py
  tests/acceptance/test_cos_live_maintenance.py
  tests/acceptance/test_macos_bootstrap_operations.py::test_credential_free_bootstrap_doctor_backup_restore_round_trip
  tests/unit/test_ci_release_gates.py
  tests/acceptance/test_cos_email_audit_context.py
  tests/unit/test_source_assurance.py
  tests/unit/test_observation_readiness_window.py
  tests/unit/bootstrap/test_effect_context_repair.py
  tests/acceptance/test_effect_context_repair_operations.py
)
# A module introduced by a later small PR must bring its corresponding proof.
# An absent optional module is not a skipped test or evidence for that feature.
for module in prepared_runtime workspace_instructions storage_headroom; do
  if [[ -f "bootstrap/$module.py" ]]; then
    test -f "tests/unit/bootstrap/test_$module.py"
    tests+=("tests/unit/bootstrap/test_$module.py")
  elif [[ -f "tests/unit/bootstrap/test_$module.py" ]]; then
    echo "Updater tests require their corresponding module" >&2
    exit 1
  fi
done
poetry run ruff check bootstrap adminme/source_assurance.py scripts/release_scope.py "${tests[@]%%::*}"
poetry run ruff format --check bootstrap adminme/source_assurance.py scripts/release_scope.py "${tests[@]%%::*}"
poetry run mypy bootstrap bridge/adminme_bridge adminme/source_assurance.py scripts/release_scope.py
poetry run pyright bootstrap adminme/source_assurance.py scripts/release_scope.py
poetry run pytest -p no:cacheprovider --runxfail -q -ra \
  --basetemp="$RUNNER_TEMP/updater-delivery-tests" \
  --junitxml="$RUNNER_TEMP/updater-delivery.xml" "${tests[@]}"
python - "$RUNNER_TEMP/updater-delivery.xml" <<'PY_CHECK'
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
bash -n bootstrap/update.sh scripts/acceptance/updater-delivery-release.sh
bash scripts/verify_invariants.sh
poetry check --lock
git diff --check
test -z "$(git status --porcelain)"
