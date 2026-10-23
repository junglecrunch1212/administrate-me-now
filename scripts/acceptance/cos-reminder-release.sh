#!/usr/bin/env bash
# Scoped hosted release evidence; never run candidate behavior on a live host.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true ]] || { echo "Use isolated hosted CI" >&2; exit 1; }
case "${1:-}" in linux|macos) ;; *) echo "Expected linux or macos" >&2; exit 1 ;; esac

poetry run pytest -p no:cacheprovider --runxfail -q -ra \
  --basetemp="$RUNNER_TEMP/cos-reminder-release" \
  tests/acceptance/test_cos_self_reminders.py \
  tests/acceptance/test_cos_service_private_work.py \
  tests/acceptance/test_cos_self_replies.py \
  tests/acceptance/test_cos_response_execution.py \
  tests/acceptance/test_cos_response_allowlist_settings.py \
  tests/acceptance/test_cos_identity_delegation_kernel.py \
  tests/acceptance/test_member_cos_approval.py \
  tests/acceptance/test_forwarded_channel_intake.py \
  tests/unit/test_cos_channel_operation.py \
  tests/unit/test_cos_response_assurance.py \
  tests/unit/test_source_assurance.py \
  tests/unit/test_historical_attribution.py \
  tests/unit/test_lifecycle_catalog.py \
  tests/unit/test_schema_registry.py \
  tests/unit/test_event_schemas.py \
  tests/unit/test_ci_release_gates.py \
  tests/unit/bootstrap/test_cos_channel_activation.py \
  tests/unit/bootstrap/test_cos_reply_hooks.py \
  tests/unit/bootstrap/test_openclaw_plugin_reconciliation.py
npm run check --prefix openclaw/administrateme
npm test --prefix openclaw/administrateme
poetry run ruff check .
poetry run ruff format --check adminme bootstrap bridge packs scripts tests tools
poetry run python scripts/generate_event_lifecycle_catalog.py --check
poetry run mypy adminme bridge/adminme_bridge bootstrap scripts/release_scope.py
poetry run pyright adminme bridge/adminme_bridge bootstrap packs
bash scripts/verify_invariants.sh
poetry check --lock
git diff --check
if [[ "$1" == macos ]]; then
  bash scripts/acceptance/openclaw-source-install.sh --self-reminder
fi
