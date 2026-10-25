"""Deterministic component selection and exact-revision protected CI gates.

Reviewed components have explicit contracts. Everything outside those
contracts falls back to unchanged system release validation. GitHub
labels, supplied path lists and previous runs cannot authorize a smaller gate.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from bootstrap.source_identity import GitTreeEntry, load_git_tree
from bootstrap.member_bridge_component import (
    HELPER_COMPONENT_PATHS,
    NON_RUNTIME_PROSE_DIRECTORY,
    NON_RUNTIME_PROSE_PATHS,
    is_runtime_input,
    runtime_interface_fingerprint,
)


POLICY_PATHS = frozenset(
    {
        ".github/workflows/administrateme-consistency.yml",
        ".github/workflows/macos-bootstrap.yml",
        "scripts/release_scope.py",
        "tests/unit/test_ci_release_gates.py",
    }
)
HELPER_TEST_PATHS = frozenset(
    {
        "tests/unit/bootstrap/test_member_bridge_component.py",
        "tests/unit/bootstrap/test_member_bridge_helper_app.py",
        "tests/unit/bootstrap/test_member_bridge_helper_diagnostics.py",
        "tests/unit/bootstrap/test_member_bridge_permission_controller.py",
        "tests/unit/bootstrap/test_member_bridge_launch.py",
        "tests/acceptance/test_member_bridge_launch_runtime.py",
        "tests/acceptance/test_member_bridge_installer_runtime.py",
        "tests/acceptance/test_member_bridge_permission_recovery.py",
    }
)
BRIDGE_SETUP_SHARED = frozenset(
    {
        "adminme/runtime/application.py",
        "adminme/products/member/assets/member.js",
        "bootstrap/bridge_enrollment.py",
        "bridge/adminme_bridge/main.py",
    }
)
BRIDGE_SETUP_REQUIRED = (
    frozenset(
        {
            "adminme/bridge_helper_distribution.py",
            "adminme/products/member/bridge_setup.py",
            "adminme/tailscale_local.py",
            "bootstrap/member_bridge_distribution.py",
            "bootstrap/member_bridge_runtime_repair.py",
            "tests/unit/bootstrap/test_member_bridge_runtime_repair.py",
            "bootstrap/member_bridge_helper_app.py",
            "bootstrap/member_bridge_permission_controller.py",
            "bootstrap/install-member-bridge.sh",
            "tests/unit/test_bridge_helper_distribution.py",
            "tests/unit/bootstrap/test_member_bridge_mac_preparation.py",
            "tests/acceptance/test_self_service_member_bridge_enrollment.py",
            "tools/console-reference-harness/bridge-enrollment.mjs",
            "tools/console-reference-harness/run-gate7.mjs",
            "tools/gate7_visual_server.py",
        }
    )
    | BRIDGE_SETUP_SHARED
)
BRIDGE_SETUP_PATHS = BRIDGE_SETUP_REQUIRED | HELPER_COMPONENT_PATHS | HELPER_TEST_PATHS


ENROLLMENT_INGEST_PATH = "adminme/products/bridge/routers/ingest.py"
GOOGLE_MODULE = "adminme/adapters/member_google_connections.py"
GOOGLE_WIRING = "scripts/google_connection_wiring.json"
GOOGLE_SHARED_PATHS = frozenset(
    {
        "adminme/adapters/cos_channel_acquisition.py",
        "adminme/adapters/disposable_test_acquisition.py",
        "adminme/deployment_readiness.py",
        "adminme/runtime/application.py",
        "adminme/settings_control.py",
        "bootstrap/cos_channel_activation.py",
        "bootstrap/versions.json",
    }
)
GOOGLE_TEST_PATHS = frozenset(
    {
        "tests/acceptance/test_member_google_connections.py",
        "tests/unit/bootstrap/test_cos_channel_activation.py",
        "tests/unit/test_cos_channel_operation.py",
    }
)
COS_GOOGLE_WORKSPACE_COMPONENT_PATHS = frozenset(
    {
        "adminme/adapters/cos_google_workspace.py",
        "adminme/adapters/cos_channel_acquisition.py",
        "adminme/adapters/credentials.py",
        "adminme/adapters/disposable_test_acquisition.py",
        "adminme/provider_bindings.py",
        "bootstrap/cos_google_workspace.py",
        "bootstrap/cos_channel_activation.py",
        "bootstrap/google_oauth.py",
        "packs/adapters/calendaring/google-calendar/adapter.py",
        "packs/adapters/tasks/google-tasks/adapter.py",
        "packs/adapters/tasks/google-tasks/config.schema.json",
    }
)
COS_GOOGLE_WORKSPACE_TEST_PATHS = frozenset(
    {
        "tests/unit/bootstrap/test_cos_google_workspace.py",
        "tests/unit/bootstrap/test_google_oauth.py",
        "tests/adapters/test_credentials.py",
        "tests/unit/test_provider_bindings.py",
        "tests/integration/test_service_owned_google_sources.py",
        "tests/unit/adapters/calendaring/test_google_calendar.py",
        "tests/unit/adapters/tasks/test_google_tasks.py",
        "tests/acceptance/test_cos_channel_acquisition.py",
        "tests/acceptance/test_member_google_connections.py",
        "tests/unit/bootstrap/test_cos_channel_activation.py",
        "packs/adapters/calendaring/google-calendar/tests/test_google_calendar_pack_internal.py",
        "packs/adapters/tasks/google-tasks/tests/test_google_tasks_pack_internal.py",
    }
)
COS_GOOGLE_WORKSPACE_REQUIRED_PATHS = (
    COS_GOOGLE_WORKSPACE_COMPONENT_PATHS | COS_GOOGLE_WORKSPACE_TEST_PATHS
)
MEMBER_GOOGLE_SETTINGS_WIRING = "scripts/member_google_settings_wiring.json"
MEMBER_GOOGLE_SETTINGS_CHECK_SCRIPT = "scripts/acceptance/member-google-settings.sh"
MEMBER_GOOGLE_SETTINGS_SHARED_PATHS = frozenset(
    {
        "adminme/daemons/adapter_supervisor.py",
        "adminme/products/member/assets/member.js",
        "adminme/products/member/assets/member.css",
        "adminme/reconciliation.py",
        "adminme/runtime/application.py",
        "adminme/settings_control.py",
        "adminme/source_policy_shadow.py",
        "bootstrap/bridge_readiness_transition.py",
        "bootstrap/candidate_readiness.py",
        "bootstrap/provider_binding_transition.py",
        "bootstrap/runtime_service.py",
    }
)
MEMBER_GOOGLE_SETTINGS_COMPONENT_PATHS = frozenset(
    {
        "adminme/member_google.py",
        "adminme/google_oauth_transport.py",
        "adminme/products/member/google_connections.py",
        "adminme/events/schemas/member_google.py",
        "adminme/adapters/member_google_connections.py",
        "bootstrap/member_google_oauth.py",
        "bootstrap/member_google_transition.py",
        "docs/refactor/evidence/event-lifecycle-catalog.json",
    }
)
MEMBER_GOOGLE_SETTINGS_TEST_PATHS = frozenset(
    {
        "tests/acceptance/test_member_google_settings.py",
        "tests/acceptance/test_member_google_connections.py",
        "tests/unit/test_member_google.py",
        "tests/acceptance/test_member_google_readiness_transition.py",
        "tests/unit/bootstrap/test_member_google_oauth.py",
        "tests/unit/test_google_oauth_transport.py",
        "tests/unit/daemons/test_adapter_supervisor.py",
        "tests/unit/bootstrap/test_provider_binding_transition.py",
        "tools/console-reference-harness/google-connections.mjs",
        "tools/console-reference-harness/run-gate7.mjs",
    }
)
MEMBER_GOOGLE_SETTINGS_REQUIRED_PATHS = (
    MEMBER_GOOGLE_SETTINGS_SHARED_PATHS
    | MEMBER_GOOGLE_SETTINGS_COMPONENT_PATHS
    | MEMBER_GOOGLE_SETTINGS_TEST_PATHS
    | {MEMBER_GOOGLE_SETTINGS_WIRING, MEMBER_GOOGLE_SETTINGS_CHECK_SCRIPT}
)
COS_APPLE_REMINDERS_COMPONENT_PATHS = frozenset(
    {
        "adminme/products/openclaw/service_context.py",
        "adminme/projections/lists/handlers.py",
        "adminme/capability_catalog.py",
        "adminme/adapters/cos_apple_reminders.py",
        "adminme/adapters/cos_apple_reminders_native.py",
        "adminme/adapters/cos_channel_acquisition.py",
        "adminme/adapters/disposable_test_acquisition.py",
        "adminme/provider_bindings.py",
        "adminme/events/schemas/adapters.py",
        "bootstrap/cos_apple_reminders.py",
        "adminme/adapters/cos_reminders_helper.py",
        "bootstrap/cos_reminders_helper_app.py",
        "bootstrap/cos_reminders_native_worker.py",
        "bootstrap/native/cos_reminders_launcher.m",
        "bootstrap/cos_channel_activation.py",
        "packs/adapters/reminders/cos-apple-eventkit/adapter.py",
        "packs/adapters/reminders/cos-apple-eventkit/pack.yaml",
        "packs/adapters/reminders/cos-apple-eventkit/config.schema.json",
        "packs/adapters/reminders/cos-apple-eventkit/config.example.yaml",
        "packs/adapters/reminders/apple-eventkit/_eventkit_shim.py",
    }
)
COS_APPLE_REMINDERS_TEST_PATHS = frozenset(
    {
        "tests/acceptance/test_cos_apple_reminders_context.py",
        "tests/acceptance/test_cos_service_planning_context.py",
        "tests/acceptance/test_cos_service_working_context.py",
        "tests/acceptance/test_cos_service_private_work.py",
        "tests/integration/test_lists_projection_runner.py",
        "tests/unit/test_capability_catalog.py",
        "tests/unit/bootstrap/test_cos_apple_reminders.py",
        "tests/unit/bootstrap/test_cos_reminders_helper.py",
        "tests/unit/bootstrap/test_cos_reminders_helper_app.py",
        "tests/unit/bootstrap/test_cos_reminders_helper_app_macos.py",
        "tests/unit/bootstrap/test_cos_reminders_helper_macos.py",
        "tests/unit/test_provider_bindings.py",
        "tests/unit/bootstrap/test_cos_channel_activation.py",
        "tests/acceptance/test_cos_channel_acquisition.py",
        "tests/unit/events/test_adapter_schemas.py",
        "tests/unit/events/schemas/test_lists_schemas.py",
        "packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_adapter_pack_internal.py",
        "packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_dual_deployment_pack_internal.py",
        "packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_dual_emit_pack_internal.py",
        "packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py",
    }
)
COS_APPLE_REMINDERS_REQUIRED_PATHS = (
    COS_APPLE_REMINDERS_COMPONENT_PATHS | COS_APPLE_REMINDERS_TEST_PATHS
)
COS_RECOVERY_CONTROLLER = "bootstrap/cos_channel_activation.py"
COS_RECOVERY_HISTORY = "bootstrap/cos_recovery_history.py"
COS_RECOVERY_COMPONENT_PATHS = frozenset(
    {
        COS_RECOVERY_CONTROLLER,
        COS_RECOVERY_HISTORY,
        "bootstrap/member_bridge_component.py",
    }
)
COS_RETAINED_SOURCE_HISTORY_PATHS = frozenset(
    {
        "bootstrap/cos_apple_source_history.py",
        "adminme/cos_apple_source_history.py",
        "adminme/events/schemas/cos_apple_source_history.py",
        "adminme/historical_attribution.py",
        "tests/unit/test_cos_apple_source_history.py",
        "tests/acceptance/test_cos_apple_source_history.py",
    }
)
COS_RECOVERY_TEST_PATHS = frozenset(
    {
        "tests/unit/bootstrap/test_cos_channel_activation.py",
        "tests/unit/bootstrap/test_cos_recovery_history.py",
        "tests/unit/bootstrap/test_cos_recovery_history_operation.py",
        "tests/unit/bootstrap/test_member_bridge_component.py",
    }
)
BRIDGE_RUNTIME_PATHS = frozenset(
    {
        "bootstrap/member_bridge_runtime_repair.py",
        "bootstrap/install-member-bridge.sh",
        "packs/adapters/reminders/apple-eventkit/_eventkit_shim.py",
        "packs/adapters/calendaring/apple-calendar/adapter.py",
        "tests/unit/adapters/calendaring/test_apple_calendar_native_dates.py",
        "tests/acceptance/test_apple_calendar_native_date_readback.py",
        "tests/unit/bootstrap/test_member_bridge_runtime_repair.py",
        "tests/unit/adapters/reminders/test_eventkit_color_bounds.py",
        "tests/unit/adapters/reminders/test_eventkit_selected_sources.py",
        "tests/integration/test_adapter_apple_eventkit_dual_deployment_integration.py",
        "packs/adapters/reminders/apple-eventkit/adapter.py",
        "tests/acceptance/test_eventkit_color_native.py",
        "tests/acceptance/test_member_bridge_installer_runtime.py",
        "tests/acceptance/test_member_bridge_launch_runtime.py",
    }
)
COS_REMINDER_PATHS = frozenset(
    {
        "adminme/cos_reminder_work.py",
        ".github/workflows/cos-reply-development.yml",
        "adminme/cos_response_assurance.py",
        "adminme/events/schemas/cos_reminders.py",
        "adminme/historical_attribution.py",
        "adminme/products/openclaw/cos_reminders.py",
        "adminme/products/openclaw/cos_self_replies.py",
        "adminme/runtime/application.py",
        "adminme/source_assurance.py",
        "bootstrap/cos_channel_activation.py",
        "bootstrap/openclaw/programs/requested_workflows.md",
        "docs/refactor/evidence/event-lifecycle-catalog.json",
        "openclaw/administrateme/openclaw.plugin.json",
        "openclaw/administrateme/scripts/check-cos-imessage-text.mjs",
        "openclaw/administrateme/scripts/check-self-reminder-gateway.mjs",
        "openclaw/administrateme/scripts/validate-contract.mjs",
        "openclaw/administrateme/src/config.ts",
        "openclaw/administrateme/src/contract.ts",
        "openclaw/administrateme/src/hooks.ts",
        "openclaw/administrateme/src/index.ts",
        "openclaw/administrateme/src/self-reminders.ts",
        "openclaw/administrateme/src/self-replies.ts",
        "openclaw/administrateme/test/fixtures/openclaw-2026.7.2/agent-harness-runtime.d.ts",
        "openclaw/administrateme/test/fixtures/openclaw-2026.7.2/agent-harness-runtime.ts",
        "openclaw/administrateme/test/fixtures/openclaw-2026.7.2/plugin-entry.d.ts",
        "openclaw/administrateme/test/plugin-contract.test.ts",
        "openclaw/administrateme/test/self-reminders.test.ts",
        "openclaw/administrateme/tsconfig.json",
        "openclaw/administrateme/vitest.config.ts",
        "openclaw/administrateme/workspace/AGENTS.md",
        "scripts/acceptance/cos-reminder-release.sh",
        "scripts/acceptance/openclaw-source-install.sh",
        "tests/acceptance/test_cos_self_reminders.py",
        "tests/acceptance/test_cos_self_replies.py",
        "tests/fixtures/openclaw_self_reminder_probe.mjs",
        "tests/unit/test_historical_attribution.py",
        "tests/unit/bootstrap/test_cos_channel_activation.py",
    }
)
COS_REMINDER_REQUIRED_PATHS = frozenset(
    {
        "adminme/cos_reminder_work.py",
        "adminme/products/openclaw/cos_reminders.py",
        "adminme/events/schemas/cos_reminders.py",
        "openclaw/administrateme/src/self-reminders.ts",
        "openclaw/administrateme/scripts/check-self-reminder-gateway.mjs",
        "tests/acceptance/test_cos_self_reminders.py",
        "tests/fixtures/openclaw_self_reminder_probe.mjs",
        "scripts/acceptance/cos-reminder-release.sh",
    }
)
UPDATER_COMPONENT_PATHS = frozenset(
    {
        "bootstrap/upgrade.py",
        "bootstrap/source_identity.py",
        "bootstrap/prepared_runtime.py",
        "bootstrap/workspace_instructions.py",
        "bootstrap/storage_headroom.py",
        "bootstrap/activation_transaction.py",
        "bootstrap/cos_channel_activation.py",
        "bootstrap/candidate_readiness.py",
        "bootstrap/backup_restore.py",
        "bootstrap/openclaw_tools.py",
    }
)
UPDATER_TEST_PATHS = frozenset(
    {
        "tests/unit/bootstrap/test_source_identity.py",
        "tests/unit/bootstrap/test_prepared_runtime.py",
        "tests/unit/bootstrap/test_workspace_instructions.py",
        "tests/unit/bootstrap/test_storage_headroom.py",
        "tests/unit/bootstrap/test_activation_transaction.py",
        "tests/acceptance/test_forwarded_submission_repair_operations.py",
        "tests/unit/bootstrap/test_cos_channel_activation.py",
        "tests/unit/bootstrap/test_backup_rehearsal.py",
        "tests/unit/bootstrap/test_candidate_readiness.py",
        "tests/unit/bootstrap/test_recovery_readiness.py",
        "tests/unit/bootstrap/test_openclaw_plugin_reconciliation.py",
        "tests/unit/bootstrap/test_cos_guard_release.py",
        "tests/acceptance/test_upgrade_deadline_rollback.py",
        "tests/acceptance/test_upgrade_readiness_blackout.py",
        "tests/acceptance/test_recovery_readiness_baseline.py",
        "tests/acceptance/test_cos_live_maintenance.py",
        "tests/acceptance/test_macos_bootstrap_operations.py",
    }
)
UPDATER_CHECK_SCRIPT = "scripts/acceptance/updater-delivery-release.sh"
UPDATER_OPTIONAL_MODULE_TESTS = {
    "bootstrap/prepared_runtime.py": "tests/unit/bootstrap/test_prepared_runtime.py",
    "bootstrap/workspace_instructions.py": "tests/unit/bootstrap/test_workspace_instructions.py",
    "bootstrap/storage_headroom.py": "tests/unit/bootstrap/test_storage_headroom.py",
}
UPDATER_REQUIRED_PATHS = (
    UPDATER_COMPONENT_PATHS | UPDATER_TEST_PATHS | {UPDATER_CHECK_SCRIPT}
) - (UPDATER_OPTIONAL_MODULE_TESTS.keys() | UPDATER_OPTIONAL_MODULE_TESTS.values())
COS_SERVICE_WORK_APPLICATION = "adminme/runtime/application.py"
COS_SERVICE_WORK_MEMBER = "adminme/products/member/main.py"
COS_SERVICE_WORK_INGEST = "adminme/products/bridge/routers/ingest.py"
COS_SERVICE_WORK_OBSERVATION = "adminme/lib/observation.py"
COS_SERVICE_WORK_HISTORY = "adminme/historical_attribution.py"
COS_SERVICE_WORK_CHECK_SCRIPT = "scripts/acceptance/cos-service-work-release.sh"
COS_SERVICE_WORK_NATIVE_INSTALL = "scripts/acceptance/openclaw-source-install.sh"
COS_SERVICE_WORK_COMPONENT_PATHS = frozenset(
    {
        COS_SERVICE_WORK_APPLICATION,
        COS_SERVICE_WORK_MEMBER,
        COS_SERVICE_WORK_INGEST,
        COS_SERVICE_WORK_OBSERVATION,
        COS_SERVICE_WORK_HISTORY,
        "adminme/adapters/cos_apple_reminders.py",
        "adminme/adapters/cos_reminders_helper.py",
        "adminme/adapters/cos_reminders_write.py",
        "adminme/adapters/cos_reminders_write_policy.py",
        "adminme/cos_reminders_commands.py",
        "adminme/events/schemas/cos_native_reminders.py",
        "adminme/products/openclaw/cos_apple_reminders.py",
        "adminme/runtime/cos_reminders.py",
        "bootstrap/cos_reminders_helper_app.py",
        "bootstrap/cos_reminders_native_worker.py",
        "bootstrap/cos_reminders_write.py",
        "bootstrap/native/cos_reminders_launcher.m",
        "openclaw/administrateme/src/cos-reminders.ts",
        "adminme/products/openclaw/apple_reminders.py",
        "adminme/apple_service_history.py",
        "adminme/conversation_instructions.py",
        "adminme/conversation_intake.py",
        "adminme/cos_response_assurance.py",
        "adminme/cos_responses.py",
        "adminme/events/schemas/conversation.py",
        "adminme/events/schemas/conversation_responses.py",
        "adminme/member_chat.py",
        "adminme/openclaw_approvals.py",
        "adminme/products/openclaw/conversation_reply.py",
        "adminme/products/openclaw/cos_responses.py",
        "adminme/products/openclaw/main.py",
        "adminme/products/openclaw/request_authorization.py",
        "adminme/request_actions_control.py",
        "adminme/lifecycle_catalog.py",
        "bootstrap/request_actions.py",
        "openclaw/administrateme/src/approval.ts",
        "openclaw/administrateme/src/conversation-input.ts",
        "openclaw/administrateme/src/request-policy.ts",
        "openclaw/administrateme/src/transport.ts",
        "packs/skills/ground_requested_action/SKILL.md",
        "packs/skills/ground_requested_action/pack.yaml",
        "packs/skills/ground_requested_action/prompt.jinja2",
        "packs/skills/ground_requested_action/schemas/input.schema.json",
        "packs/skills/ground_requested_action/schemas/output.schema.json",
        "adminme/workstation_inventory.py",
        "adminme/products/openclaw/workstation.py",
        "bootstrap/workstation_inventory.py",
        "bootstrap/openclaw/programs/workstation_inventory.md",
        "openclaw/administrateme/src/workstation.ts",
        "openclaw/administrateme/src/apple-reminders.ts",
        "openclaw/administrateme/src/tools.ts",
        "bootstrap/openclaw/programs/requested_workflows.md",
        "openclaw/administrateme/workspace/AGENTS.md",
        "adminme/lib/principal.py",
        "adminme/lib/scope.py",
        "adminme/service_identities.py",
        "adminme/events/schemas/domain.py",
        "adminme/events/schemas/openclaw.py",
        "adminme/projections/tasks/__init__.py",
        "adminme/projections/tasks/handlers.py",
        "adminme/projections/tasks/schema.sql",
        "adminme/projections/tasks/queries.py",
        "adminme/projections/calendars/__init__.py",
        "adminme/projections/calendars/handlers.py",
        "adminme/projections/calendars/schema.sql",
        "adminme/projections/lists/__init__.py",
        "adminme/projections/lists/handlers.py",
        "adminme/projections/lists/schema.sql",
        "adminme/products/openclaw/service_work.py",
        "adminme/products/openclaw/service_context.py",
        "adminme/operating_agreements.py",
        "adminme/capability_catalog.py",
        "adminme/openclaw_topology.py",
        "bootstrap/cos_channel_activation.py",
        "bootstrap/openclaw_activation.py",
        "bootstrap/openclaw_tools.py",
        "docs/refactor/evidence/event-lifecycle-catalog.json",
        "openclaw/administrateme/openclaw.plugin.json",
        "openclaw/administrateme/README.md",
        "openclaw/administrateme/src/config.ts",
        "openclaw/administrateme/src/contract.ts",
        "openclaw/administrateme/src/hooks.ts",
        "openclaw/administrateme/src/index.ts",
        "openclaw/administrateme/src/provenance.ts",
        "openclaw/administrateme/src/service-private.ts",
        "openclaw/administrateme/src/member-work.ts",
        "openclaw/administrateme/src/self-replies.ts",
        "openclaw/administrateme/workspace/service-private/AGENTS.md",
        "openclaw/administrateme/workspace/service-private/IDENTITY.md",
        "openclaw/administrateme/scripts/validate-contract.mjs",
        COS_SERVICE_WORK_NATIVE_INSTALL,
    }
)
COS_SERVICE_WORK_TEST_PATHS = frozenset(
    {
        "tests/unit/test_service_work_principal.py",
        "tests/acceptance/test_cos_owned_apple_reminder.py",
        "tests/unit/adapters/reminders/test_cos_reminders_write.py",
        "tests/unit/adapters/reminders/test_cos_reminders_write_policy.py",
        "tests/unit/bootstrap/test_cos_reminders_helper.py",
        "tests/unit/bootstrap/test_cos_reminders_helper_app.py",
        "tests/unit/bootstrap/test_cos_reminders_helper_app_macos.py",
        "tests/unit/bootstrap/test_cos_reminders_helper_macos.py",
        "tests/unit/bootstrap/test_cos_reminders_write.py",
        "tests/unit/test_cos_reminders_commands.py",
        "tests/unit/test_cos_reminders_dispatch.py",
        "tests/unit/test_cos_reminders_status.py",
        "tests/unit/test_observation.py",
        "tests/unit/test_historical_attribution.py",
        "tests/unit/test_service_identities.py",
        "tests/unit/test_operating_agreements.py",
        "tests/unit/test_projection_tasks.py",
        "tests/unit/test_capability_catalog.py",
        "tests/acceptance/test_cos_identity_delegation_kernel.py",
        "tests/acceptance/test_cos_service_private_work.py",
        "tests/acceptance/test_cos_member_follow_up.py",
        "tests/acceptance/test_native_apple_reminder.py",
        "tests/acceptance/test_authenticated_conversation_channels.py",
        "tests/acceptance/test_authenticated_conversation_reply.py",
        "tests/acceptance/test_authenticated_request_actions.py",
        "tests/acceptance/test_gate7_household_experience.py",
        "tests/acceptance/test_member_chat_subscription.py",
        "tests/unit/test_member_chat_protocol.py",
        "tests/unit/test_openclaw_approvals.py",
        "tests/unit/test_request_actions_control.py",
        "openclaw/administrateme/test/request-policy.test.ts",
        "openclaw/administrateme/test/conversation-input.test.ts",
        "tests/acceptance/test_workstation_inventory.py",
        "tests/unit/test_workstation_inventory.py",
        "openclaw/administrateme/test/workstation.test.ts",
        "tests/acceptance/test_native_reminder_due_components.py",
        "openclaw/administrateme/test/apple-reminders.test.ts",
        "openclaw/administrateme/scripts/check-cos-imessage-text.mjs",
        "tests/acceptance/test_apple_service_permissions.py",
        "tests/acceptance/test_member_apple_approval.py",
        "tests/acceptance/test_apple_service_notes.py",
        "openclaw/administrateme/test/member-work.test.ts",
        "openclaw/administrateme/test/self-replies.test.ts",
        "openclaw/administrateme/test/self-reminders.test.ts",
        "tests/acceptance/test_cos_service_working_context.py",
        "tests/acceptance/test_cos_service_planning_context.py",
        "tests/unit/test_projection_calendars.py",
        "tests/unit/projections/lists/test_handlers.py",
        "tests/unit/projections/lists/test_queries.py",
        "tests/integration/test_lists_projection_runner.py",
        "tests/integration/test_service_owned_google_sources.py",
        "tests/unit/bootstrap/test_cos_channel_activation.py",
        "tests/unit/bootstrap/test_cos_member_topology.py",
        "tests/unit/bootstrap/test_topology_readiness.py",
        "tests/unit/bootstrap/test_openclaw_plugin_reconciliation.py",
        "openclaw/administrateme/test/plugin-contract.test.ts",
        "openclaw/administrateme/test/fixtures/openclaw-2026.7.2/plugin-entry.d.ts",
        "openclaw/administrateme/scripts/check-service-work-gateway.mjs",
        "tests/fixtures/openclaw_service_work_probe.mjs",
        "tests/unit/bootstrap/test_cos_service_topology.py",
    }
)
COS_SERVICE_WORK_REQUIRED_PATHS = (
    COS_SERVICE_WORK_COMPONENT_PATHS
    | COS_SERVICE_WORK_TEST_PATHS
    | {COS_SERVICE_WORK_CHECK_SCRIPT}
)
SCOPES = frozenset(
    {
        "documentation",
        "bridge-helper",
        "bridge-enrollment",
        "bridge-runtime",
        "bridge-setup",
        "google-connections",
        "cos-google-workspace",
        "member-google-settings",
        "cos-apple-reminders",
        "cos-recovery-history",
        "cos-reminders",
        "updater-delivery",
        "cos-service-work",
        "system-wide",
    }
)
CONTRACT = "administrateme-component-release-v1"


def _git(repository: Path, *arguments: str) -> str:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "--literal-pathspecs",
            "-C",
            str(repository),
            *arguments,
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout


def _commit(repository: Path, value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("release evidence requires a full commit identity")
    if _git(repository, "rev-parse", f"{value}^{{commit}}").strip() != value:
        raise ValueError("release commit does not resolve exactly")
    return value


def _prose(path: str) -> bool:
    return (
        path.endswith(".md")
        and (
            path in NON_RUNTIME_PROSE_PATHS
            or PurePosixPath(path).parent.as_posix() == NON_RUNTIME_PROSE_DIRECTORY
        )
        and not is_runtime_input(path, mode="100644")
    )


def choose_scope(
    changes: list[tuple[str, str]],
    *,
    interfaces_equal: bool,
    path_modes: dict[str, set[str]],
    enrollment_interfaces_equal: bool = False,
    google_interfaces_equal: bool = False,
    cos_google_workspace_interfaces_equal: bool = False,
    member_google_settings_interfaces_equal: bool = False,
    cos_apple_reminders_interfaces_equal: bool = False,
    cos_recovery_interfaces_equal: bool = False,
    bridge_runtime_interfaces_equal: bool = False,
    cos_reminder_interfaces_equal: bool = False,
    updater_interfaces_equal: bool = False,
    cos_service_work_interfaces_equal: bool = False,
    bridge_setup_interfaces_equal: bool = False,
) -> str:
    if not changes:
        return "system-wide"
    if any(_prose(path) and path_modes.get(path) != {"100644"} for _, path in changes):
        return "system-wide"
    if all(_prose(path) and status in {"A", "M", "D"} for status, path in changes):
        return "documentation" if interfaces_equal else "system-wide"
    if (
        bridge_setup_interfaces_equal
        and all(
            status in {"A", "M"}
            and (path in BRIDGE_SETUP_PATHS | POLICY_PATHS or _prose(path))
            and path_modes.get(path) == {_bridge_setup_mode(path)}
            for status, path in changes
        )
        and any(path in BRIDGE_SETUP_REQUIRED for _, path in changes)
    ):
        return "bridge-setup"
    if (
        cos_service_work_interfaces_equal
        and all(
            status in {"A", "M"}
            and (path in COS_SERVICE_WORK_REQUIRED_PATHS | POLICY_PATHS or _prose(path))
            and path_modes.get(path) == {_service_work_mode(path)}
            for status, path in changes
        )
        and any(path in COS_SERVICE_WORK_COMPONENT_PATHS for _, path in changes)
    ):
        return "cos-service-work"
    if (
        bridge_runtime_interfaces_equal
        and all(
            status in {"A", "M"}
            and (path in BRIDGE_RUNTIME_PATHS | POLICY_PATHS or _prose(path))
            and path_modes.get(path)
            == {"100755" if path == "bootstrap/install-member-bridge.sh" else "100644"}
            for status, path in changes
        )
        and any(
            path
            in {
                "bootstrap/member_bridge_runtime_repair.py",
                "packs/adapters/reminders/apple-eventkit/adapter.py",
                "packs/adapters/reminders/apple-eventkit/_eventkit_shim.py",
                "packs/adapters/calendaring/apple-calendar/adapter.py",
            }
            for _, path in changes
        )
    ):
        return "bridge-runtime"
    if (
        cos_reminder_interfaces_equal
        and all(
            status in {"A", "M"}
            and (path in COS_REMINDER_PATHS | POLICY_PATHS or _prose(path))
            and path_modes.get(path) == {_cos_reminder_mode(path)}
            for status, path in changes
        )
        # The existing worker also covers attribution and source assurance;
        # an isolated audit repair does not need an unrelated reminder edit.
        and any(
            path in COS_REMINDER_REQUIRED_PATHS
            or path == "adminme/historical_attribution.py"
            for _, path in changes
        )
    ):
        return "cos-reminders"
    if (
        updater_interfaces_equal
        and all(
            status in {"A", "M"}
            and (
                path in UPDATER_COMPONENT_PATHS | UPDATER_TEST_PATHS | POLICY_PATHS
                or path == UPDATER_CHECK_SCRIPT
                or _prose(path)
            )
            and path_modes.get(path) == {"100644"}
            for status, path in changes
        )
        and any(
            path in UPDATER_COMPONENT_PATHS or path == UPDATER_CHECK_SCRIPT
            for _, path in changes
        )
    ):
        return "updater-delivery"
    recovery_allowed = (
        COS_RECOVERY_COMPONENT_PATHS
        | COS_RECOVERY_TEST_PATHS
        | COS_RETAINED_SOURCE_HISTORY_PATHS
        | POLICY_PATHS
    )
    if (
        cos_recovery_interfaces_equal
        and all(
            status in {"A", "M"}
            and (path in recovery_allowed or _prose(path))
            and path_modes.get(path) == {"100644"}
            for status, path in changes
        )
        and any(
            path
            in {
                COS_RECOVERY_CONTROLLER,
                COS_RECOVERY_HISTORY,
                "bootstrap/cos_apple_source_history.py",
            }
            for _, path in changes
        )
    ):
        return "cos-recovery-history"
    if (
        member_google_settings_interfaces_equal
        and all(
            status in {"A", "M"}
            and (
                path in MEMBER_GOOGLE_SETTINGS_REQUIRED_PATHS | POLICY_PATHS
                or _prose(path)
            )
            and path_modes.get(path) == {"100644"}
            for status, path in changes
        )
        and any(path in MEMBER_GOOGLE_SETTINGS_COMPONENT_PATHS for _, path in changes)
    ):
        return "member-google-settings"
    if (
        cos_apple_reminders_interfaces_equal
        and all(
            status in {"A", "M"}
            and (
                path in COS_APPLE_REMINDERS_REQUIRED_PATHS | POLICY_PATHS
                or _prose(path)
            )
            and path_modes.get(path) == {"100644"}
            for status, path in changes
        )
        and any(path in COS_APPLE_REMINDERS_COMPONENT_PATHS for _, path in changes)
    ):
        return "cos-apple-reminders"
    if (
        cos_google_workspace_interfaces_equal
        and all(
            status in {"A", "M"}
            and (
                path in COS_GOOGLE_WORKSPACE_REQUIRED_PATHS | POLICY_PATHS
                or _prose(path)
            )
            and path_modes.get(path) == {"100644"}
            for status, path in changes
        )
        and any(path in COS_GOOGLE_WORKSPACE_COMPONENT_PATHS for _, path in changes)
    ):
        return "cos-google-workspace"
    google_allowed = (
        GOOGLE_SHARED_PATHS
        | GOOGLE_TEST_PATHS
        | POLICY_PATHS
        | {GOOGLE_MODULE, GOOGLE_WIRING}
    )
    if (
        google_interfaces_equal
        and all(
            status in {"A", "M"}
            and (path in google_allowed or _prose(path))
            and path_modes.get(path) == {"100644"}
            for status, path in changes
        )
        and any(
            path == GOOGLE_MODULE or path in GOOGLE_SHARED_PATHS for _, path in changes
        )
    ):
        return "google-connections"
    allowed = set(HELPER_COMPONENT_PATHS) | POLICY_PATHS | HELPER_TEST_PATHS
    if interfaces_equal and all(
        status in {"A", "M"} and (path in allowed or _prose(path))
        for status, path in changes
    ):
        return "bridge-helper"
    if (
        enrollment_interfaces_equal
        and ("M", ENROLLMENT_INGEST_PATH) in changes
        and all(
            path_modes.get(path)
            == {"100755" if path == "bootstrap/install-member-bridge.sh" else "100644"}
            for _status, path in changes
        )
        and all(
            (status == "M" and path == ENROLLMENT_INGEST_PATH)
            or (status in {"A", "M"} and (path in allowed or _prose(path)))
            for status, path in changes
        )
    ):
        return "bridge-enrollment"
    return "system-wide"


def _bridge_setup_mode(path: str) -> str:
    return "100755" if path == "bootstrap/install-member-bridge.sh" else "100644"


def _bridge_setup_shared_source(path: str, source: str) -> tuple[str, bool] | None:
    """Only the setup function, direct router wiring and exact CLI call may vary."""
    if path.endswith("member.js"):
        start, end = (
            "function bridgeSettings(data) {",
            "function privacySettings(data) {",
        )
        if source.count(start) != 1 or source.count(end) != 1:
            return None
        before, rest = source.split(start)
        if end not in rest:
            return None
        _, after = rest.split(end)
        return before + start + "\n}\n" + end + after, True
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    if path == "adminme/runtime/application.py":
        imported = ast.parse(
            "from adminme.products.member.bridge_setup import router as member_bridge_setup_router"
        ).body[0]
        included = ast.parse("app.include_router(member_bridge_setup_router)").body[0]
        functions = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "create_application"
        ]
        if len(functions) != 1:
            return None
        containers = [tree.body, functions[0].body]
        shapes = [ast.dump(imported), ast.dump(included)]
        matches = [
            [n for n in body if ast.dump(n) == shape]
            for body, shape in zip(containers, shapes, strict=True)
        ]
        if [len(items) for items in matches] not in ([0, 0], [1, 1]):
            return None
        wired = bool(matches[0])
        for body, matches_here in zip(containers, matches, strict=True):
            for node in matches_here:
                body.remove(node)
    else:
        imported = ast.parse(
            "from adminme.tailscale_local import tailscale_executable"
        ).body[0]
        imports = [n for n in tree.body if ast.dump(n) == ast.dump(imported)]
        if len(imports) > 1:
            return None
        function_name = (
            "_tailscale_identity"
            if path.startswith("bootstrap/")
            else "_local_device_identity"
        )
        functions = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == function_name
        ]
        if len(functions) != 1:
            return None
        new_args = ast.parse(
            '[tailscale_executable(), "status", "--json"]', mode="eval"
        ).body
        old_args = ast.parse('["tailscale", "status", "--json"]', mode="eval").body
        env = ast.parse('{**os.environ, "TAILSCALE_BE_CLI": "1"}', mode="eval").body
        calls = [
            n
            for n in ast.walk(functions[0])
            if isinstance(n, ast.Call)
            and ast.dump(n.func)
            == ast.dump(ast.parse("subprocess.run", mode="eval").body)
        ]
        changed = [
            n for n in calls if n.args and ast.dump(n.args[0]) == ast.dump(new_args)
        ]
        wired = bool(imports)
        if len(changed) != len(imports):
            return None
        if wired:
            call = changed[0]
            environment = [
                k
                for k in call.keywords
                if k.arg == "env" and ast.dump(k.value) == ast.dump(env)
            ]
            if len(environment) != 1:
                return None
            call.args[0] = old_args
            call.keywords.remove(environment[0])
            tree.body.remove(imports[0])
    return ast.dump(tree, include_attributes=False), wired


def _bridge_setup_dependency_fingerprint(
    repository: Path, revision: str, entries: tuple[GitTreeEntry, ...]
) -> tuple[str, bool] | None:
    normalized: dict[str, str] = {}
    wired: list[bool] = []
    for path in BRIDGE_SETUP_SHARED:
        entry = next((entry for entry in entries if entry.path == path), None)
        if entry is None or entry.mode != "100644":
            return None
        result = _bridge_setup_shared_source(
            path, _git(repository, "show", f"{revision}:{path}")
        )
        if result is None:
            return None
        normalized[path], present = result
        wired.append(present)
    allowed = BRIDGE_SETUP_PATHS | POLICY_PATHS
    coordinates = sorted(
        (entry.path, entry.mode, normalized.get(entry.path, entry.object_id))
        for entry in entries
        if entry.path in BRIDGE_SETUP_SHARED
        or not (
            entry.mode == _bridge_setup_mode(entry.path)
            and (entry.path in allowed or _prose(entry.path))
        )
    )
    return hashlib.sha256(
        json.dumps(coordinates, separators=(",", ":")).encode()
    ).hexdigest(), all(wired)


def _enrollment_interface_fingerprint(entries: tuple[GitTreeEntry, ...]) -> str:
    # Hash immutable Git blob IDs and modes, never a caller's supplied path
    # list or a mutable checkout. Unknown tracked paths remain runtime inputs.
    # Shared coordinator/history/schema/auth/storage/lock/runtime/dependency
    # bytes are deliberately outside this single-route component boundary.
    # Unrelated test/CI blobs are also fixed so an exact-parent test merge
    # cannot introduce unreviewed paths absent from the head diff.
    allowed = set(HELPER_COMPONENT_PATHS) | POLICY_PATHS | HELPER_TEST_PATHS
    coordinates = sorted(
        (entry.path, entry.mode, entry.object_id)
        for entry in entries
        if entry.path != ENROLLMENT_INGEST_PATH
        and (
            is_runtime_input(entry.path, mode=entry.mode)
            or (entry.path not in allowed and not _prose(entry.path))
        )
    )
    return hashlib.sha256(
        json.dumps(coordinates, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _bridge_runtime_dependency_fingerprint(entries: tuple[GitTreeEntry, ...]) -> str:
    allowed = BRIDGE_RUNTIME_PATHS | POLICY_PATHS
    coordinates = sorted(
        (entry.path, entry.mode, entry.object_id)
        for entry in entries
        if not (
            entry.mode
            == (
                "100755"
                if entry.path == "bootstrap/install-member-bridge.sh"
                else "100644"
            )
            and (entry.path in allowed or _prose(entry.path))
        )
    )
    return hashlib.sha256(
        json.dumps(coordinates, separators=(",", ":")).encode()
    ).hexdigest()


def _cos_recovery_dependency_fingerprint(entries: tuple[GitTreeEntry, ...]) -> str:
    # Only this closed controller/history/compatibility edit set may differ.
    # Every other tracked byte and mode, including tests and unknown inputs,
    # stays frozen across the protected base, head, and exact-parent test merge.
    allowed = (
        COS_RECOVERY_COMPONENT_PATHS
        | COS_RECOVERY_TEST_PATHS
        | COS_RETAINED_SOURCE_HISTORY_PATHS
        | POLICY_PATHS
    )
    coordinates = sorted(
        (entry.path, entry.mode, entry.object_id)
        for entry in entries
        if not (
            entry.mode == "100644" and (entry.path in allowed or _prose(entry.path))
        )
    )
    return hashlib.sha256(
        json.dumps(coordinates, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _updater_dependency_fingerprint(entries: tuple[GitTreeEntry, ...]) -> str:
    # This delivery controller boundary does not exempt toolchains, runtime,
    # storage/crypto, permissions, provider code, or unknown tests. Bind every
    # outside byte and mode in base, head and the exact-parent merge tree.
    allowed = (
        UPDATER_COMPONENT_PATHS
        | UPDATER_TEST_PATHS
        | POLICY_PATHS
        | {UPDATER_CHECK_SCRIPT}
    )
    coordinates = sorted(
        (entry.path, entry.mode, entry.object_id)
        for entry in entries
        if not (
            entry.mode == "100644" and (entry.path in allowed or _prose(entry.path))
        )
    )
    return hashlib.sha256(
        json.dumps(coordinates, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _remove_service_statements(body: list[ast.stmt], source: str) -> int | None:
    """Remove one exact adjacent integration sequence, or require its absence."""
    shapes = [
        ast.dump(node, include_attributes=False) for node in ast.parse(source).body
    ]
    actual = [ast.dump(node, include_attributes=False) for node in body]
    counts = [actual.count(shape) for shape in shapes]
    if not any(counts):
        return 0
    if counts != [1] * len(shapes):
        return None
    index = actual.index(shapes[0])
    if actual[index : index + len(shapes)] != shapes:
        return None
    del body[index : index + len(shapes)]
    return 1


def _service_work_gmail_wiring(module: ast.Module) -> bool:
    """Keep startup logic frozen around four exact Gmail integration points."""
    imports = _remove_service_statements(
        module.body, "from adminme.conversation_intake import GmailConversationIntake"
    )
    classes = [
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "AdministrateMeRuntime"
    ]
    if len(classes) > 1:
        return False
    members = classes[0].body if classes else []
    constructors = [
        node
        for node in members
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    ]
    starts = [
        node
        for node in members
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "start"
    ]
    if len(constructors) > 1 or len(starts) > 1:
        return False
    startup = (
        [node for node in starts[0].body if isinstance(node, ast.Try)] if starts else []
    )
    if len(startup) > 1:
        return False
    init_body = constructors[0].body if constructors else []
    startup_body = startup[0].body if startup else []
    slot = _remove_service_statements(
        init_body, "self.gmail_conversations: GmailConversationIntake | None = None"
    )
    initialization = (
        "self.gmail_conversations = GmailConversationIntake(self)\n"
        "await self.gmail_conversations.initialize()"
    )
    subscription = (
        'self.bus.subscribe("cos-gmail:conversation", '
        '["messaging.received", "cos.channels.activated", "observation.disabled"], '
        "self.gmail_conversations.handle, "
        "consumer_version=self.gmail_conversations.CONSUMER_VERSION)"
    )
    startup_shapes = [ast.dump(node, include_attributes=False) for node in startup_body]
    order = [
        ast.dump(ast.parse(source).body[0], include_attributes=False)
        for source in (initialization, subscription)
    ]
    if all(shape in startup_shapes for shape in order) and startup_shapes.index(
        order[0]
    ) > startup_shapes.index(order[1]):
        return False
    initialized = _remove_service_statements(startup_body, initialization)
    subscribed = _remove_service_statements(startup_body, subscription)
    return [imports, slot, initialized, subscribed] in ([0, 0, 0, 0], [1, 1, 1, 1])


def _service_work_cos_reminders_wiring(module: ast.Module) -> bool:
    """Admit only the exact own-account dispatch prefix and matching router."""
    classes = [
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "AdministrateMeRuntime"
    ]
    applications = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_application"
    ]
    if len(classes) > 1 or len(applications) != 1:
        return False
    methods = [
        node
        for node in (classes[0].body if classes else [])
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_dispatch_external_write"
    ]
    if len(methods) > 1:
        return False
    body = methods[0].body if methods else []
    prefix = (
        "from adminme.runtime.cos_reminders import dispatch, is_cos_reminder\n"
        "if is_cos_reminder(event):\n"
        "    await dispatch(self, event)\n"
        "    return"
    )
    # This branch must precede all existing dispatch effects, after only a docstring.
    expected = ast.dump(ast.parse(prefix).body[0], include_attributes=False)
    actual = [ast.dump(node, include_attributes=False) for node in body]
    first = (
        1
        if body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
        else 0
    )
    if expected in actual and actual.index(expected) != first:
        return False
    dispatched = _remove_service_statements(body, prefix)
    routed = _remove_service_statements(
        applications[0].body,
        "from adminme.products.openclaw.cos_apple_reminders import router as cos_apple_router\n"
        "app.include_router(cos_apple_router)",
    )
    return [dispatched, routed] in ([0, 0], [1, 1])


def _service_work_observation(source: str) -> str | None:
    """Normalize only both outbound audit actors; retain every other guard byte."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    functions = [
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "outbound"
    ]
    if len(functions) > 1:
        return None
    actors: list[ast.keyword] = []
    kinds: list[str] = []
    for node in ast.walk(functions[0]) if functions else []:
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Name)
            or node.func.id != "EventEnvelope"
        ):
            continue
        event_types = [kw.value for kw in node.keywords if kw.arg == "type"]
        if (
            len(event_types) != 1
            or not isinstance(event_types[0], ast.Constant)
            or not isinstance(event_types[0].value, str)
            or event_types[0].value not in {"observation.suppressed", "external.sent"}
        ):
            continue
        kinds.append(event_types[0].value)
        actors.extend(kw for kw in node.keywords if kw.arg == "actor_identity")
    if actors or kinds:
        if (
            sorted(kinds) != ["external.sent", "observation.suppressed"]
            or len(actors) != 2
        ):
            return None
        values = [ast.dump(actor.value, include_attributes=False) for actor in actors]
        original = ast.dump(
            ast.parse("session.auth_member_id", mode="eval").body,
            include_attributes=False,
        )
        current = ast.dump(
            ast.parse("session.event_actor_identity", mode="eval").body,
            include_attributes=False,
        )
        if values not in ([original, original], [current, current]):
            return None
        for actor in actors:
            actor.value = ast.parse("session.auth_member_id", mode="eval").body
    return hashlib.sha256(
        ast.dump(module, include_attributes=False).encode()
    ).hexdigest()


def _service_work_history(source: str) -> str | None:
    """Keep the history audit frozen except the exact retained own-Apple branch."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    imported = _remove_service_statements(
        module.body,
        "from adminme.cos_reminders_commands import ("
        "CONTRACT as COS_REMINDER_CONTRACT, command_owner_matches as cos_reminder_owner_matches)",
    )
    functions = [
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "audit_historical_attribution"
    ]
    if len(functions) > 1:
        return None
    expected = ast.parse(
        "if apple_command is not None and (\n"
        '    (apple_command.get("payload", {}).get("payload") or {}).get("contract") == COS_REMINDER_CONTRACT\n'
        '    or apple_command.get("payload", {}).get("privacy_guard_version") == COS_REMINDER_CONTRACT\n'
        "):\n"
        '    if await cos_reminder_owner_matches(event_log, apple_command, through=watermark or ""):\n'
        '        binding_state = "internal_cos_owned_reminder_source_reconciled"\n'
        '        workspace = "service"\n'
        "        attributed += 1\n"
    ).body[0]
    assert isinstance(expected, ast.If)
    previous = ast.dump(
        ast.parse("event_type == ATTEMPTED", mode="eval").body, include_attributes=False
    )
    following = ast.dump(
        ast.parse(
            'apple_command is not None and (apple_command.get("payload", {}).get("provider") == "apple_notes" '
            'or (apple_command.get("payload", {}).get("payload") or {}).get("apple_service_permission"))',
            mode="eval",
        ).body,
        include_attributes=False,
    )
    branches = 0
    for node in ast.walk(functions[0]) if functions else []:
        if (
            not isinstance(node, ast.If)
            or len(node.orelse) != 1
            or not isinstance(node.orelse[0], ast.If)
        ):
            continue
        branch = node.orelse[0]
        if ast.dump(branch.test, include_attributes=False) == ast.dump(
            expected.test, include_attributes=False
        ) and [ast.dump(part, include_attributes=False) for part in branch.body] == [
            ast.dump(part, include_attributes=False) for part in expected.body
        ]:
            if (
                ast.dump(node.test, include_attributes=False) != previous
                or len(branch.orelse) != 1
                or not isinstance(branch.orelse[0], ast.If)
                or ast.dump(branch.orelse[0].test, include_attributes=False)
                != following
            ):
                return None
            branches += 1
            node.orelse = branch.orelse
    if [imported, branches] not in ([0, 0], [1, 1]):
        return None
    return hashlib.sha256(
        ast.dump(module, include_attributes=False).encode()
    ).hexdigest()


def _service_work_application(source: str) -> tuple[str, bool] | None:
    """Normalize reviewed router/Gmail wiring while binding all neighboring logic."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    if not _service_work_gmail_wiring(module) or not _service_work_cos_reminders_wiring(
        module
    ):
        return None
    applications = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_application"
    ]
    if len(applications) != 1:
        return None
    application = applications[0]
    expected = ast.parse(
        "from adminme.products.openclaw.service_work import router as service_work_router\n"
        "app.include_router(service_work_router)\n"
    ).body
    shapes = [ast.dump(node, include_attributes=False) for node in expected]
    body_shapes = [
        ast.dump(node, include_attributes=False) for node in application.body
    ]
    counts = [body_shapes.count(shape) for shape in shapes]
    if counts not in ([0, 0], [1, 1]):
        return None
    if counts == [1, 1] and body_shapes.index(shapes[0]) > body_shapes.index(shapes[1]):
        return None
    reminder_shapes = [
        ast.dump(node, include_attributes=False)
        for node in ast.parse(
            "from adminme.products.openclaw.apple_reminders import router as apple_reminder_router\n"
            "app.include_router(apple_reminder_router)\n"
        ).body
    ]
    reminder_counts = [body_shapes.count(shape) for shape in reminder_shapes]
    if reminder_counts not in ([0, 0], [1, 1]):
        return None
    if reminder_counts == [1, 1] and body_shapes.index(
        reminder_shapes[0]
    ) > body_shapes.index(reminder_shapes[1]):
        return None
    workstation_shapes = [
        ast.dump(node, include_attributes=False)
        for node in ast.parse(
            "from adminme.products.openclaw.workstation import router as workstation_router\n"
            "app.include_router(workstation_router)\n"
        ).body
    ]
    workstation_counts = [body_shapes.count(shape) for shape in workstation_shapes]
    if workstation_counts not in ([0, 0], [1, 1]):
        return None
    if workstation_counts == [1, 1] and body_shapes.index(
        workstation_shapes[0]
    ) > body_shapes.index(workstation_shapes[1]):
        return None
    application.body = [
        node
        for node, shape in zip(application.body, body_shapes, strict=True)
        if shape not in shapes + reminder_shapes + workstation_shapes
    ]
    digest = hashlib.sha256(
        ast.dump(module, include_attributes=False).encode()
    ).hexdigest()
    return digest, counts == [1, 1]


def _service_work_mode(path: str) -> str:
    return "100755" if path == COS_SERVICE_WORK_NATIVE_INSTALL else "100644"


def _service_work_member(source: str) -> str | None:
    """Bind every member route except reminder confirmation and actual chat intake."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    names = {"reminder_command", "confirm_reminder_command", "chat_stream"}
    imported = _remove_service_statements(
        module.body, "from adminme.openclaw_topology import member_agent_id"
    )
    if imported is None or (
        imported
        and not any(
            isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_stream"
            for node in module.body
        )
    ):
        return None
    for name in names:
        if (
            sum(
                isinstance(node, ast.AsyncFunctionDef) and node.name == name
                for node in module.body
            )
            > 1
        ):
            return None
    module.body = [
        node
        for node in module.body
        if not (isinstance(node, ast.AsyncFunctionDef) and node.name in names)
    ]
    return hashlib.sha256(
        ast.dump(module, include_attributes=False).encode()
    ).hexdigest()


def _service_work_ingest(source: str) -> str | None:
    """Exact terminal/request integrations; all other bridge logic is bound."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    commands = [
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "authorize_apple_command"
    ]
    if len(commands) > 1:
        return None
    for command in commands:
        locked = [
            node
            for node in command.body
            if isinstance(node, ast.AsyncWith)
            and len(node.items) == 1
            and ast.dump(node.items[0].context_expr, include_attributes=False)
            == ast.dump(ast.Name(id="lock", ctx=ast.Load()), include_attributes=False)
            and node.items[0].optional_vars is None
        ]
        if len(locked) > 1:
            return None
        if (
            locked
            and _remove_service_statements(
                locked[0].body,
                "from adminme.products.openclaw.request_authorization import require_current_bridge_request\n"
                "await require_current_bridge_request(runtime, event)",
            )
            is None
        ):
            return None
    pending = [
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "pending_commands"
    ]
    if len(pending) > 1:
        return None
    # Admit only this paired request-policy check and explicit denial handling.
    # The existing retained-grant/edge-terminal logic stays in the fingerprint.
    old_pending = ast.parse(
        "try:\n    apple_grant = command_permission(runtime, member_id, payload)\n"
        "except (ValueError, KeyError):\n    pass\n"
    ).body[0]
    new_pending = ast.parse(
        "try:\n    apple_grant = command_permission(runtime, member_id, payload)\n"
        "    from adminme.products.openclaw.request_authorization import require_current_bridge_request\n"
        "    await require_current_bridge_request(runtime, event)\n"
        "except (ValueError, KeyError, HTTPException):\n    pass\n"
    ).body[0]
    assert isinstance(old_pending, ast.Try) and isinstance(new_pending, ast.Try)
    assert old_pending.handlers[0].type is not None
    assert new_pending.handlers[0].type is not None
    replaced_pending = 0
    for function in pending:
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Try)
                and not node.orelse
                and not node.finalbody
                and len(node.handlers) == 1
                and node.handlers[0].name is None
                and node.handlers[0].type is not None
                and ast.dump(node.handlers[0].type, include_attributes=False)
                == ast.dump(new_pending.handlers[0].type, include_attributes=False)
                and [ast.dump(item, include_attributes=False) for item in node.body]
                == [
                    ast.dump(item, include_attributes=False)
                    for item in new_pending.body
                ]
            ):
                node.body = old_pending.body
                node.handlers[0].type = old_pending.handlers[0].type
                replaced_pending += 1
    if replaced_pending > 1:
        return None
    terminal = [
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_enforce_terminal_result"
    ]
    active = [
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_enforce_active_bridge_contract"
    ]
    if len(terminal) > 1 or len(active) > 1:
        return None
    module.body = [node for node in module.body if node not in terminal]
    old = ast.parse(
        "async def f():\n    await _enforce_terminal_result(request, envelope, authorization)\n    return envelope\n"
    ).body[0]
    new = ast.parse(
        "async def f():\n    return await _enforce_terminal_result(request, envelope, authorization)\n"
    ).body[0]
    assert isinstance(old, ast.AsyncFunctionDef) and isinstance(
        new, ast.AsyncFunctionDef
    )
    variants = [
        [ast.dump(node, include_attributes=False) for node in fn.body]
        for fn in (old, new)
    ]
    replaced = 0
    for function in active:
        for node in ast.walk(function):
            if (
                isinstance(node, ast.If)
                and [ast.dump(item, include_attributes=False) for item in node.body]
                in variants
            ):
                node.body = [ast.Pass()]
                replaced += 1
    if active and replaced != 1:
        return None
    return hashlib.sha256(
        ast.dump(module, include_attributes=False).encode()
    ).hexdigest()


def _service_work_dependency_fingerprint(
    repository: Path, revision: str, entries: tuple[GitTreeEntry, ...]
) -> tuple[str, bool] | None:
    # Freeze every outside source/test/dependency byte and mode. The central
    # runtime is not a general exemption: only exact integration AST statements differ.
    application = next(
        (entry for entry in entries if entry.path == COS_SERVICE_WORK_APPLICATION), None
    )
    if application is None or application.mode != "100644":
        return None
    normalized = _service_work_application(
        _git(repository, "show", f"{revision}:{COS_SERVICE_WORK_APPLICATION}")
    )
    if normalized is None:
        return None
    identity, wired = normalized
    shared_identities: list[tuple[str, str, str]] = []
    for path, normalize in (
        (COS_SERVICE_WORK_MEMBER, _service_work_member),
        (COS_SERVICE_WORK_INGEST, _service_work_ingest),
        (COS_SERVICE_WORK_OBSERVATION, _service_work_observation),
        (COS_SERVICE_WORK_HISTORY, _service_work_history),
    ):
        entry = next((entry for entry in entries if entry.path == path), None)
        if entry is not None and entry.mode != "100644":
            return None
        shared_identity = normalize(
            _git(repository, "show", f"{revision}:{path}") if entry else ""
        )
        if shared_identity is None:
            return None
        shared_identities.append((path, "100644", shared_identity))
    allowed = COS_SERVICE_WORK_REQUIRED_PATHS | POLICY_PATHS
    coordinates = sorted(
        (
            entry.path,
            entry.mode,
            identity if entry.path == COS_SERVICE_WORK_APPLICATION else entry.object_id,
        )
        for entry in entries
        if entry.path == COS_SERVICE_WORK_APPLICATION
        or not (
            entry.mode == _service_work_mode(entry.path)
            and (entry.path in allowed or _prose(entry.path))
        )
    )
    coordinates.extend(shared_identities)
    coordinates.sort()
    digest = hashlib.sha256(
        json.dumps(coordinates, separators=(",", ":")).encode()
    ).hexdigest()
    return digest, wired


def _cos_reminder_mode(path: str) -> str:
    return (
        "100755"
        if path == "scripts/acceptance/openclaw-source-install.sh"
        else "100644"
    )


def _cos_reminder_dependency_fingerprint(entries: tuple[GitTreeEntry, ...]) -> str:
    # The reviewed reply/timer boundary is closed. Storage, crypto, dependencies,
    # unrelated runtime and tests remain identical in base, head and test merge.
    allowed = COS_REMINDER_PATHS | POLICY_PATHS
    coordinates = sorted(
        (entry.path, entry.mode, entry.object_id)
        for entry in entries
        if not (
            entry.mode == _cos_reminder_mode(entry.path)
            and (entry.path in allowed or _prose(entry.path))
        )
    )
    return hashlib.sha256(
        json.dumps(coordinates, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _cos_google_workspace_dependency_fingerprint(
    entries: tuple[GitTreeEntry, ...],
) -> str:
    """Freeze all bytes/modes outside the tested OAuth/acquisition component.

    The Google worker covers account/purpose binding, selected source polling,
    member/mail preservation, governed writes and guarded activation/recovery.
    Locks, runtime topology, bridge, persistence and unknown tests remain bound.
    """
    allowed = COS_GOOGLE_WORKSPACE_REQUIRED_PATHS | POLICY_PATHS
    coordinates = sorted(
        (entry.path, entry.mode, entry.object_id)
        for entry in entries
        if not (
            entry.mode == "100644" and (entry.path in allowed or _prose(entry.path))
        )
    )
    return hashlib.sha256(
        json.dumps(coordinates, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _member_google_settings_shared_source(
    path: str, source: str
) -> tuple[str, bool] | None:
    """Normalize only reviewed exact integration edits in otherwise frozen files."""
    contract = json.loads(
        (Path(__file__).parent / "member_google_settings_wiring.json").read_text()
    )
    if (
        contract.get("contract") != "member-google-settings-wiring-v1"
        or set(contract.get("paths", {})) != MEMBER_GOOGLE_SETTINGS_SHARED_PATHS
    ):
        raise ValueError("invalid member Google Settings integration boundary")
    pairs = contract["paths"][path]
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("member Google Settings integration edits are missing")
    wired = True
    for pair in pairs:
        before, after = pair["before"], pair["after"]
        if not before or not after or before == after:
            raise ValueError("invalid member Google Settings integration edit")
        if source.count(after) == 1:
            source = source.replace(after, before, 1)
        elif source.count(before) == 1:
            wired = False
        else:
            return None
    return source, wired


def _member_google_settings_dependency_fingerprint(
    repository: Path, revision: str, entries: tuple[GitTreeEntry, ...]
) -> tuple[str, bool] | None:
    normalized: dict[str, str] = {}
    wired = []
    for path in MEMBER_GOOGLE_SETTINGS_SHARED_PATHS:
        entry = next((entry for entry in entries if entry.path == path), None)
        if entry is None or entry.mode != "100644":
            return None
        result = _member_google_settings_shared_source(
            path, _git(repository, "show", f"{revision}:{path}")
        )
        if result is None:
            return None
        normalized[path], present = result
        wired.append(present)
    allowed = MEMBER_GOOGLE_SETTINGS_REQUIRED_PATHS | POLICY_PATHS
    coordinates = sorted(
        (entry.path, entry.mode, normalized.get(entry.path, entry.object_id))
        for entry in entries
        if entry.path in MEMBER_GOOGLE_SETTINGS_SHARED_PATHS
        or not (
            entry.mode == "100644" and (entry.path in allowed or _prose(entry.path))
        )
    )
    return hashlib.sha256(
        json.dumps(coordinates, separators=(",", ":")).encode()
    ).hexdigest(), all(wired)


def _cos_apple_reminders_dependency_fingerprint(
    entries: tuple[GitTreeEntry, ...],
) -> str:
    """Freeze unrelated runtime, bridge, source and credential bytes/modes."""
    allowed = COS_APPLE_REMINDERS_REQUIRED_PATHS | POLICY_PATHS
    coordinates = sorted(
        (entry.path, entry.mode, entry.object_id)
        for entry in entries
        if not (
            entry.mode == "100644" and (entry.path in allowed or _prose(entry.path))
        )
    )
    return hashlib.sha256(
        json.dumps(coordinates, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def google_dependency_fingerprint(repository: Path, revision: str) -> str:
    """Exclude the Google module and only its exact tested integration edits.

    This is a CI scope boundary, not a retained-helper compatibility assertion.
    Every other runtime byte and mode, including all bridge code, stays bound.
    The protected worker separately tests the complete immutable Google candidate.
    """
    contract = json.loads(
        (Path(__file__).parent / "google_connection_wiring.json").read_text()
    )
    if (
        contract.get("contract") != "google-connection-wiring-v1"
        or set(contract["paths"]) != GOOGLE_SHARED_PATHS
    ):
        raise ValueError("invalid Google integration boundary")
    entries, _ = load_git_tree(repository, revision)
    inventory = {}
    for entry in entries:
        path = entry.path
        if (
            path in {GOOGLE_MODULE, GOOGLE_WIRING} | POLICY_PATHS
            or _prose(path)
            or path.startswith(("tests/", "bridge/tests/", ".github/"))
        ):
            continue
        identity = entry.object_id
        if path in GOOGLE_SHARED_PATHS:
            source = _git(repository, "show", f"{revision}:{path}")
            for pair in contract["paths"][path]:
                before, after = pair["before"], pair["after"]
                if not before or not after or before == after:
                    raise ValueError("invalid Google integration edit")
                if source.count(after) == 1:
                    source = source.replace(after, before, 1)
            identity = hashlib.sha256(source.encode()).hexdigest()
        inventory[path] = {"mode": entry.mode, "identity": identity}
    return hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()


def select_scope(
    repository: Path, event: dict[str, Any], *, checkout_sha: str
) -> dict[str, Any]:
    checkout = _commit(repository, checkout_sha)
    if _git(repository, "rev-parse", "HEAD").strip() != checkout:
        raise ValueError("checkout differs from this workflow's immutable revision")
    pull = event.get("pull_request")
    if pull is None:
        return {
            "contract": CONTRACT,
            "scope": "system-wide",
            "draft": False,
            "base_sha": checkout,
            "head_sha": checkout,
            "checkout_sha": checkout,
            "interface_sha256": runtime_interface_fingerprint(repository, checkout),
            "changes": [],
            "reason": "non-PR runs require system validation",
        }
    if not isinstance(pull, dict) or pull.get("base", {}).get("ref") != "main":
        raise ValueError("release selection requires the protected main base")
    if not isinstance(pull.get("draft"), bool):
        raise ValueError("release selection needs an explicit draft state")
    base = _commit(repository, pull.get("base", {}).get("sha"))
    head = _commit(repository, pull.get("head", {}).get("sha"))
    parents = _git(repository, "rev-list", "--parents", "-n", "1", checkout).split()[1:]
    checkout_binding = "event-head" if checkout == head else "exact-event-merge"
    if checkout != head and parents != [base, head]:
        # GitHub can retain an older event base while regenerating its test
        # merge against advanced main. Accept only the already-supported event
        # head's EXACT source tree, with that newer base explicitly integrated
        # in the head. Never accept merge-only resolution/code or drop changes
        # between the original event base and head from scope classification.
        equivalent = False
        if len(parents) == 2 and parents[1] == head:
            try:
                equivalent = (
                    _git(repository, "merge-base", base, parents[0]).strip() == base
                    and _git(repository, "merge-base", parents[0], head).strip()
                    == parents[0]
                    and _git(repository, "rev-parse", f"{checkout}^{{tree}}").strip()
                    == _git(repository, "rev-parse", f"{head}^{{tree}}").strip()
                )
            except subprocess.CalledProcessError:
                equivalent = False
        if not equivalent:
            raise ValueError("test merge does not bind the exact event base and head")
        checkout_binding = "head-equivalent-merge"
    raw = _git(repository, "diff", "--no-renames", "--name-status", "-z", base, head)
    fields = raw.rstrip("\0").split("\0") if raw else []
    if len(fields) % 2:
        raise ValueError("unexpected Git diff record")
    changes = list(zip(fields[::2], fields[1::2], strict=True))
    for status, path in changes:
        if (
            status not in {"A", "M", "D", "T", "U"}
            or path.startswith("/")
            or ".." in Path(path).parts
            or any(ord(character) < 32 for character in path)
        ):
            raise ValueError("unsafe or unsupported diff entry")
    path_modes: dict[str, set[str]] = {}
    enrollment_fingerprints: dict[str, str] = {}
    enrollment_route_present: dict[str, bool] = {}
    recovery_fingerprints: dict[str, str] = {}
    bridge_runtime_fingerprints: dict[str, str] = {}
    recovery_sources_present: dict[str, bool] = {}
    reminder_fingerprints: dict[str, str] = {}
    reminder_sources_present: dict[str, bool] = {}
    updater_fingerprints: dict[str, str] = {}
    updater_sources_present: dict[str, bool] = {}
    setup_fingerprints: dict[str, str] = {}
    setup_sources_present: dict[str, bool] = {}
    workspace_fingerprints: dict[str, str] = {}
    workspace_sources_present: dict[str, bool] = {}
    apple_fingerprints: dict[str, str] = {}
    apple_sources_present: dict[str, bool] = {}
    member_google_fingerprints: dict[str, str] = {}
    member_google_sources_present: dict[str, bool] = {}
    service_work_fingerprints: dict[str, str] = {}
    service_work_sources_present: dict[str, bool] = {}
    for revision in {base, head, checkout}:
        entries, _ = load_git_tree(repository, revision)
        setup = _bridge_setup_dependency_fingerprint(repository, revision, entries)
        setup_fingerprints[revision] = setup[0] if setup else ""
        setup_paths = {
            entry.path
            for entry in entries
            if entry.mode == _bridge_setup_mode(entry.path)
        }
        setup_sources_present[revision] = setup is not None and (
            revision == base or (setup[1] and BRIDGE_SETUP_REQUIRED <= setup_paths)
        )
        updater_fingerprints[revision] = _updater_dependency_fingerprint(entries)
        regular_paths = {entry.path for entry in entries if entry.mode == "100644"}
        member_google = _member_google_settings_dependency_fingerprint(
            repository, revision, entries
        )
        member_google_fingerprints[revision] = member_google[0] if member_google else ""
        member_google_sources_present[revision] = member_google is not None and (
            revision == base
            or (
                member_google[1]
                and MEMBER_GOOGLE_SETTINGS_REQUIRED_PATHS <= regular_paths
            )
        )
        workspace_fingerprints[revision] = _cos_google_workspace_dependency_fingerprint(
            entries
        )
        workspace_sources_present[revision] = (
            revision == base or COS_GOOGLE_WORKSPACE_REQUIRED_PATHS <= regular_paths
        )
        apple_fingerprints[revision] = _cos_apple_reminders_dependency_fingerprint(
            entries
        )
        apple_sources_present[revision] = (
            revision == base or COS_APPLE_REMINDERS_REQUIRED_PATHS <= regular_paths
        )
        service_work = _service_work_dependency_fingerprint(
            repository, revision, entries
        )
        service_work_fingerprints[revision] = service_work[0] if service_work else ""
        service_work_paths = {
            entry.path
            for entry in entries
            if entry.mode == _service_work_mode(entry.path)
        }
        service_work_sources_present[revision] = service_work is not None and (
            revision == base
            or (
                service_work[1]
                and COS_SERVICE_WORK_REQUIRED_PATHS <= service_work_paths
            )
        )
        required_updater_paths = UPDATER_REQUIRED_PATHS - (
            {UPDATER_CHECK_SCRIPT} if revision == base else set()
        )
        updater_sources_present[revision] = (
            required_updater_paths <= regular_paths
            and all(
                module not in regular_paths or test in regular_paths
                for module, test in UPDATER_OPTIONAL_MODULE_TESTS.items()
            )
        )
        reminder_fingerprints[revision] = _cos_reminder_dependency_fingerprint(entries)
        reminder_sources_present[revision] = (
            revision == base
            or COS_REMINDER_REQUIRED_PATHS
            <= {entry.path for entry in entries if entry.mode == "100644"}
        )
        enrollment_fingerprints[revision] = _enrollment_interface_fingerprint(entries)
        recovery_fingerprints[revision] = _cos_recovery_dependency_fingerprint(entries)
        bridge_runtime_fingerprints[revision] = _bridge_runtime_dependency_fingerprint(
            entries
        )
        required_recovery_paths = COS_RECOVERY_COMPONENT_PATHS - (
            {COS_RECOVERY_HISTORY} if revision == base else set()
        )
        regular_recovery_paths = {
            entry.path for entry in entries if entry.mode == "100644"
        }
        recovery_sources_present[revision] = (
            required_recovery_paths <= regular_recovery_paths
            and (
                "bootstrap/cos_apple_source_history.py" not in regular_recovery_paths
                or COS_RETAINED_SOURCE_HISTORY_PATHS <= regular_recovery_paths
            )
        )
        enrollment_route_present[revision] = any(
            entry.path == ENROLLMENT_INGEST_PATH and entry.mode == "100644"
            for entry in entries
        )
        for entry in entries:
            path_modes.setdefault(entry.path, set()).add(entry.mode)
    base_fingerprint = runtime_interface_fingerprint(repository, base)
    head_fingerprint = runtime_interface_fingerprint(repository, head)
    checkout_fingerprint = runtime_interface_fingerprint(repository, checkout)
    scope = choose_scope(
        changes,
        interfaces_equal=base_fingerprint == head_fingerprint == checkout_fingerprint,
        path_modes=path_modes,
        enrollment_interfaces_equal=(
            all(enrollment_route_present.values())
            and len(set(enrollment_fingerprints.values())) == 1
        ),
        google_interfaces_equal=len(
            {
                google_dependency_fingerprint(repository, revision)
                for revision in {base, head, checkout}
            }
        )
        == 1,
        member_google_settings_interfaces_equal=(
            all(member_google_sources_present.values())
            and len(set(member_google_fingerprints.values())) == 1
        ),
        cos_google_workspace_interfaces_equal=(
            all(workspace_sources_present.values())
            and len(set(workspace_fingerprints.values())) == 1
        ),
        cos_apple_reminders_interfaces_equal=(
            all(apple_sources_present.values())
            and len(set(apple_fingerprints.values())) == 1
        ),
        bridge_runtime_interfaces_equal=len(set(bridge_runtime_fingerprints.values()))
        == 1,
        bridge_setup_interfaces_equal=(
            all(setup_sources_present.values())
            and len(set(setup_fingerprints.values())) == 1
        ),
        cos_service_work_interfaces_equal=(
            all(service_work_sources_present.values())
            and len(set(service_work_fingerprints.values())) == 1
        ),
        updater_interfaces_equal=(
            all(updater_sources_present.values())
            and len(set(updater_fingerprints.values())) == 1
        ),
        cos_reminder_interfaces_equal=(
            all(reminder_sources_present.values())
            and len(set(reminder_fingerprints.values())) == 1
        ),
        cos_recovery_interfaces_equal=(
            all(recovery_sources_present.values())
            and len(set(recovery_fingerprints.values())) == 1
        ),
    )
    return {
        "contract": CONTRACT,
        "scope": scope,
        "draft": pull["draft"],
        "base_sha": base,
        "head_sha": head,
        "checkout_sha": checkout,
        "checkout_binding": checkout_binding,
        "test_merge_base_sha": parents[0] if checkout != head else None,
        "interface_sha256": checkout_fingerprint,
        "enrollment_interface_sha256": enrollment_fingerprints[checkout],
        "cos_recovery_dependency_sha256": recovery_fingerprints[checkout],
        "cos_reminder_dependency_sha256": reminder_fingerprints[checkout],
        "updater_dependency_sha256": updater_fingerprints[checkout],
        "cos_service_work_dependency_sha256": service_work_fingerprints[checkout],
        "bridge_setup_dependency_sha256": setup_fingerprints[checkout],
        "cos_google_workspace_dependency_sha256": workspace_fingerprints[checkout],
        "cos_apple_reminders_dependency_sha256": apple_fingerprints[checkout],
        "member_google_settings_dependency_sha256": member_google_fingerprints[
            checkout
        ],
        "changes": [{"status": status, "path": path} for status, path in changes],
    }


def required_workers(scope: str, platform: str) -> tuple[str, ...]:
    if scope not in SCOPES or platform not in {"linux", "macos"}:
        raise ValueError("unsupported release scope or platform")
    selected = {
        "documentation": "documentation-validation",
        "bridge-helper": "bridge-helper-validation",
        "bridge-enrollment": "bridge-development",
        "bridge-runtime": "bridge-development",
        "bridge-setup": "bridge-development",
        "google-connections": "google-connections-validation",
        "cos-google-workspace": "google-connections-validation",
        "member-google-settings": "google-connections-validation",
        "cos-apple-reminders": "google-connections-validation",
        "cos-recovery-history": "cos-recovery-history-validation",
        "cos-reminders": "cos-reminder-validation",
        "updater-delivery": "updater-delivery-validation",
        "cos-service-work": "cos-service-work-validation",
        "system-wide": "system-consistency"
        if platform == "linux"
        else "system-rehearsal",
    }[scope]
    workers: tuple[str, ...] = ("release-scope", selected)
    if platform == "macos" and scope in {
        "bridge-helper",
        "bridge-enrollment",
        "bridge-runtime",
        "bridge-setup",
        "system-wide",
    }:
        workers += ("bridge-installed-runtime",)
    return workers


def verify_gate(proof: dict[str, Any], needs: dict[str, Any], *, platform: str) -> None:
    if proof.get("contract") != CONTRACT or proof.get("draft") is not False:
        raise ValueError("development-only drafts cannot approve a component release")
    scope = proof.get("scope")
    if not isinstance(scope, str):
        raise ValueError("release scope is missing")
    for name in required_workers(scope, platform):
        worker = needs.get(name)
        if not isinstance(worker, dict) or worker.get("result") != "success":
            raise ValueError(f"required {name} did not finish successfully")
        outputs = worker.get("outputs", {})
        if outputs.get("validated_sha") != proof["checkout_sha"]:
            raise ValueError(f"required {name} has missing or stale revision evidence")
    selection = needs["release-scope"]["outputs"]
    if (
        selection.get("scope") != scope
        or selection.get("interface_sha256") != proof["interface_sha256"]
    ):
        raise ValueError("worker scope does not match recomputed interface evidence")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("select", "gate"))
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--checkout-sha", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--platform", choices=("linux", "macos"))
    args = parser.parse_args()
    proof = select_scope(
        args.repository,
        json.loads(args.event.read_text("utf-8")),
        checkout_sha=args.checkout_sha,
    )
    if args.command == "gate":
        verify_gate(
            proof, json.loads(os.environ["RELEASE_NEEDS_JSON"]), platform=args.platform
        )
    serialized = json.dumps(proof, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            for key, value in (
                ("scope", proof["scope"]),
                ("validated_sha", proof["checkout_sha"]),
                ("interface_sha256", proof["interface_sha256"]),
            ):
                stream.write(f"{key}={value}\n")
    print(serialized, end="")


if __name__ == "__main__":
    main()
