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
COS_RECOVERY_CONTROLLER = "bootstrap/cos_channel_activation.py"
COS_RECOVERY_HISTORY = "bootstrap/cos_recovery_history.py"
COS_RECOVERY_COMPONENT_PATHS = frozenset(
    {
        COS_RECOVERY_CONTROLLER,
        COS_RECOVERY_HISTORY,
        "bootstrap/member_bridge_component.py",
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
}
UPDATER_REQUIRED_PATHS = (
    UPDATER_COMPONENT_PATHS | UPDATER_TEST_PATHS | {UPDATER_CHECK_SCRIPT}
) - (UPDATER_OPTIONAL_MODULE_TESTS.keys() | UPDATER_OPTIONAL_MODULE_TESTS.values())
COS_SERVICE_WORK_APPLICATION = "adminme/runtime/application.py"
COS_SERVICE_WORK_CHECK_SCRIPT = "scripts/acceptance/cos-service-work-release.sh"
COS_SERVICE_WORK_NATIVE_INSTALL = "scripts/acceptance/openclaw-source-install.sh"
COS_SERVICE_WORK_COMPONENT_PATHS = frozenset(
    {
        COS_SERVICE_WORK_APPLICATION,
        "adminme/lib/principal.py",
        "adminme/lib/scope.py",
        "adminme/service_identities.py",
        "adminme/events/schemas/domain.py",
        "adminme/projections/tasks/__init__.py",
        "adminme/projections/tasks/handlers.py",
        "adminme/projections/tasks/schema.sql",
        "adminme/projections/tasks/queries.py",
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
        "openclaw/administrateme/workspace/service-private/AGENTS.md",
        "openclaw/administrateme/scripts/validate-contract.mjs",
        COS_SERVICE_WORK_NATIVE_INSTALL,
    }
)
COS_SERVICE_WORK_TEST_PATHS = frozenset(
    {
        "tests/unit/test_service_work_principal.py",
        "tests/unit/test_service_identities.py",
        "tests/unit/test_operating_agreements.py",
        "tests/unit/test_projection_tasks.py",
        "tests/unit/test_capability_catalog.py",
        "tests/acceptance/test_cos_identity_delegation_kernel.py",
        "tests/acceptance/test_cos_service_private_work.py",
        "tests/acceptance/test_cos_service_working_context.py",
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
        "google-connections",
        "cos-google-workspace",
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
    cos_recovery_interfaces_equal: bool = False,
    bridge_runtime_interfaces_equal: bool = False,
    cos_reminder_interfaces_equal: bool = False,
    updater_interfaces_equal: bool = False,
    cos_service_work_interfaces_equal: bool = False,
) -> str:
    if not changes:
        return "system-wide"
    if any(_prose(path) and path_modes.get(path) != {"100644"} for _, path in changes):
        return "system-wide"
    if all(_prose(path) and status in {"A", "M", "D"} for status, path in changes):
        return "documentation" if interfaces_equal else "system-wide"
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
        COS_RECOVERY_COMPONENT_PATHS | COS_RECOVERY_TEST_PATHS | POLICY_PATHS
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
            path in {COS_RECOVERY_CONTROLLER, COS_RECOVERY_HISTORY}
            for _, path in changes
        )
    ):
        return "cos-recovery-history"
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
    allowed = COS_RECOVERY_COMPONENT_PATHS | COS_RECOVERY_TEST_PATHS | POLICY_PATHS
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


def _service_work_application(source: str) -> tuple[str, bool] | None:
    """Normalize only the direct service-work import/router pair, never runtime logic."""
    try:
        module = ast.parse(source)
    except SyntaxError:
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
    application.body = [
        node
        for node, shape in zip(application.body, body_shapes, strict=True)
        if shape not in shapes
    ]
    digest = hashlib.sha256(
        ast.dump(module, include_attributes=False).encode()
    ).hexdigest()
    return digest, counts == [1, 1]


def _service_work_mode(path: str) -> str:
    return "100755" if path == COS_SERVICE_WORK_NATIVE_INSTALL else "100644"


def _service_work_dependency_fingerprint(
    repository: Path, revision: str, entries: tuple[GitTreeEntry, ...]
) -> tuple[str, bool] | None:
    # Freeze every outside source/test/dependency byte and mode. The central
    # runtime is not a general exemption: only the exact router AST pair differs.
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
    if checkout != head and parents != [base, head]:
        raise ValueError("test merge does not bind the exact event base and head")
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
    workspace_fingerprints: dict[str, str] = {}
    workspace_sources_present: dict[str, bool] = {}
    service_work_fingerprints: dict[str, str] = {}
    service_work_sources_present: dict[str, bool] = {}
    for revision in {base, head, checkout}:
        entries, _ = load_git_tree(repository, revision)
        updater_fingerprints[revision] = _updater_dependency_fingerprint(entries)
        regular_paths = {entry.path for entry in entries if entry.mode == "100644"}
        workspace_fingerprints[revision] = _cos_google_workspace_dependency_fingerprint(
            entries
        )
        workspace_sources_present[revision] = (
            revision == base or COS_GOOGLE_WORKSPACE_REQUIRED_PATHS <= regular_paths
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
        recovery_sources_present[revision] = required_recovery_paths <= {
            entry.path for entry in entries if entry.mode == "100644"
        }
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
        cos_google_workspace_interfaces_equal=(
            all(workspace_sources_present.values())
            and len(set(workspace_fingerprints.values())) == 1
        ),
        bridge_runtime_interfaces_equal=len(set(bridge_runtime_fingerprints.values()))
        == 1,
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
        "interface_sha256": checkout_fingerprint,
        "enrollment_interface_sha256": enrollment_fingerprints[checkout],
        "cos_recovery_dependency_sha256": recovery_fingerprints[checkout],
        "cos_reminder_dependency_sha256": reminder_fingerprints[checkout],
        "updater_dependency_sha256": updater_fingerprints[checkout],
        "cos_service_work_dependency_sha256": service_work_fingerprints[checkout],
        "cos_google_workspace_dependency_sha256": workspace_fingerprints[checkout],
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
        "google-connections": "google-connections-validation",
        "cos-google-workspace": "google-connections-validation",
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
