#!/bin/bash
# Fail early at the changed persistence and public recovery boundaries. The
# existing selected system lanes retain their aggregate checks afterward.
set -Eeuo pipefail
export PYTHONDONTWRITEBYTECODE=1
poetry run pytest -p no:cacheprovider -q -ra \
  tests/unit/test_openclaw_roster.py \
  tests/unit/test_openclaw_roster_migration.py \
  tests/unit/bootstrap/test_openclaw_migration.py \
  tests/unit/bootstrap/test_native_migration_maintenance.py \
  tests/unit/bootstrap/test_native_recovery.py \
  tests/unit/bootstrap/test_native_recovery_entry.py \
  tests/unit/bootstrap/test_activation_transaction.py \
  tests/unit/bootstrap/test_htr10_export.py \
  tests/unit/bootstrap/test_candidate_readiness.py \
  tests/unit/bootstrap/test_workspace_instructions.py
