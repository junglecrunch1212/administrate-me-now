#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$REPOSITORY_ROOT"

PYTHONPATH=bridge poetry run pytest \
  tests/acceptance/test_release_candidate_hardening.py::test_two_instances_accept_identical_ids_without_cross_tenant_leakage \
  tests/acceptance/test_member_bridge_knowledge_vertical_slice.py::test_member_bridge_knowledge_is_private_searchable_and_replayable \
  tests/acceptance/test_gate7_household_experience.py::test_member_chat_is_openclaw_owned_isolated_and_server_authorized \
  -q

echo "ADMINISTRATEME TENANT ISOLATION: PASS"
