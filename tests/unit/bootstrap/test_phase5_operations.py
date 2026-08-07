"""Focused safety tests for the Gate 5 macOS operations package."""

from __future__ import annotations

import base64
import builtins
import copy
import json
import plistlib
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bootstrap.contracts import OPENCLAW_LABEL, RUNTIME_LABEL, DeploymentPaths
from bootstrap.cli import _bootstrap_diagnostic_status
from bootstrap.cli import _interactive_answers
from bootstrap.doctor import Doctor
from bootstrap.keychain_probe import configured_keychain_references
from bootstrap.keychain_runtime_migration import (
    ALIAS_FILENAME,
    KeychainRuntimeMigrationError,
    _active_runtime_tag,
    configured_logical_references,
    migrate as migrate_keychain_runtime,
)
from bootstrap.preflight import PreflightFailure, run_preflight
from bootstrap.secrets import MacOSKeychainStore, secret_store
from bootstrap.services import (
    RUNTIME_LIVENESS_TIMEOUT_SECONDS,
    ServiceManager,
    ServiceStatus,
)
from bootstrap.state import EncryptedStateStore
from bootstrap.upgrade import UpgradeError, UpgradeManager, validate_candidate
from bootstrap.wizard import BootstrapInterrupted, BootstrapWizard, WizardFailure
import bootstrap.openclaw_service as openclaw_service
import bootstrap.runtime_service as runtime_service
from bootstrap.openclaw_activation import activate, model_provider_configured
from bootstrap.openclaw_install import reviewed_source_status, source_receipt_path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINTS = (
    "bootstrap-macos.sh",
    "install-openclaw-source.sh",
    "install.sh",
    "doctor.sh",
    "start.sh",
    "stop.sh",
    "status.sh",
    "backup.sh",
    "restore.sh",
    "bridge-export.sh",
    "bridge-finalize.sh",
    "bridge-revoke.sh",
    "bridge-status.sh",
    "install-member-bridge.sh",
    "member-bridge.sh",
)


@pytest.fixture
def test_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DeploymentPaths:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    return DeploymentPaths.from_root(tmp_path / "deployment")


def _wizard(paths: DeploymentPaths) -> BootstrapWizard:
    return BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    )


def _state(paths: DeploymentPaths) -> dict:
    store = secret_store(paths, test_mode=True)
    reference = f"test-secret://{paths.keychain_account()}/bootstrap-state"
    return EncryptedStateStore(paths.state_path, store.get(reference)).load()


def _stub_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    async def initialize(_wizard, _state) -> None:
        return None

    monkeypatch.setattr(BootstrapWizard, "_initialize_runtime_state", initialize)


def test_fixed_entrypoints_exist_and_delegate_without_secret_arguments() -> None:
    for name in ENTRYPOINTS:
        path = REPO_ROOT / "bootstrap" / name
        assert path.is_file(), name
        text = path.read_text("utf-8")
        assert text.startswith("#!/bin/bash")
        assert "source .env" not in text.lower()
        assert "ADMINME_EVENT_LOG_KEY_HEX=" not in text
    assert "bootstrap-macos.sh" in (REPO_ROOT / "bootstrap" / "install.sh").read_text(
        "utf-8"
    )
    common = (REPO_ROOT / "bootstrap" / "common.sh").read_text("utf-8")
    assert "current-release.json" in common
    assert "ACTIVE_RELEASE_ROOT/.venv/bin/python" in common
    assert 'formula="$("$parser" -c' in common
    assert 'brew --prefix "$formula"' in common
    assert "configure_active_runtime_path" in common
    assert '"${ADMINME_TEST_MODE:-0}" == "1"' in common
    assert "the test Node runtime does not match" in common
    installer = (REPO_ROOT / "bootstrap" / "bootstrap-macos.sh").read_text("utf-8")
    assert "--expected-commit" in installer
    assert 'ADMINME_REVIEWED_COMMIT="$commit"' in installer
    assert ".administrateme-source-commit" in installer
    assert ".administrateme-installed-commit" in installer
    assert 'mktemp -d "$release_parent/.staging.XXXXXX"' in installer
    assert "internal test-harness option" in installer
    assert "status --porcelain --untracked-files=all" in installer
    assert "npm install --global" not in installer
    assert 'poetry_venv="$INSTANCE_ROOT/app/tooling/poetry-2.4.1"' in installer
    assert '"$poetry_venv/bin/poetry" sync --only main' in installer
    assert 'openclaw plugins install --link --force "$release_dir/' in installer
    assert "openclaw plugins uninstall administrateme --force --keep-files" in installer
    assert "openclaw config set gateway.mode local" in installer
    assert "openclaw config validate" in installer
    assert "outside >=6,<7" in installer
    assert 'OPENCLAW_STATE_DIR="$INSTANCE_ROOT/state/openclaw/runtime"' in installer
    assert 'OPENCLAW_CONFIG_PATH="$OPENCLAW_STATE_DIR/openclaw.json"' in installer
    updater = (REPO_ROOT / "bootstrap" / "update.sh").read_text("utf-8")
    assert "runtime migration requires a clean reviewed Git checkout" in updater
    assert (
        'controller="$ADMINME_INSTANCE_ROOT/app/tooling/upgrade-controller-' in updater
    )
    assert 'VIRTUAL_ENV="$controller"' in updater
    assert 'ADMINME_REPOSITORY_ROOT="$candidate"' in updater
    assert (
        '"$active_python" "$candidate/bootstrap/keychain_runtime_migration.py"'
        in updater
    )
    assert 'upgrade "$candidate" "$approve_flag"' in updater
    source_installer = (
        REPO_ROOT / "bootstrap" / "install-openclaw-source.sh"
    ).read_text("utf-8")
    assert "source_archive_sha256" in source_installer
    assert "--frozen-lockfile" in source_installer
    assert 'INSTANCE_ROOT="$(cd "$INSTANCE_ROOT" && pwd -P)"' in source_installer
    assert (
        "the resolved instance root must be a narrow absolute path" in source_installer
    )
    versions = json.loads(
        (REPO_ROOT / "bootstrap" / "versions.json").read_text("utf-8")
    )
    assert (
        "codeload.github.com/openclaw/openclaw"
        in versions["openclaw"]["source_archive_url"]
    )
    assert versions["openclaw"]["package_manager"] == (
        "pnpm@11.15.1+sha512.81350b07e53c9538a02f1f2303b4290fa2d7be04e56e2a970c4cc4b417dc761de196edabd49d55c7dc9580db81007c44143e4e3d7e462b3000d23c255122d065"
    )
    assert (
        'OPENCLAW_STATE_DIR="$ADMINME_INSTANCE_ROOT/state/openclaw/runtime"' in common
    )
    assert 'cd "$ACTIVE_RELEASE_ROOT"' in common


def test_macos_installer_dispatches_without_optional_passthrough_args(
    tmp_path: Path,
) -> None:
    """The real first run has no fixture args and must work under macOS Bash 3."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    captured = tmp_path / "python-args.txt"
    python = fake_bin / "python3"
    python.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$@" > "$ADMINME_CAPTURE"\n', "utf-8"
    )
    python.chmod(0o755)
    instance_root = tmp_path / "instance"
    env = os.environ.copy()
    env.update(
        {
            "ADMINME_CAPTURE": str(captured),
            "ADMINME_TEST_MODE": "1",
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        }
    )

    subprocess.run(
        [
            "/bin/bash",
            str(REPO_ROOT / "bootstrap" / "bootstrap-macos.sh"),
            "--instance-root",
            str(instance_root),
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    assert captured.read_text("utf-8").splitlines() == [
        "-m",
        "bootstrap.cli",
        "--instance-root",
        str(instance_root),
        "bootstrap",
    ]


def test_interactive_answers_allow_deferred_child_tailscale_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(
        [
            "Guide",
            "y",
            "America/New_York",
            "2",
            "Principal",
            "principal",
            "principal@example.invalid",
            "Child",
            "child",
            "",
            "n",  # principal OpenClaw actor
            "n",  # child OpenClaw actor
            "y",  # principal portal chat; child is deferred automatically
            "n",  # Plaid sandbox
            *(["n"] * 9),  # seed follow-ups
            *(["n"] * 4),  # integration follow-ups
            "n",  # principal bridge; child is deferred automatically
            "y",  # observation mode
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    result = _interactive_answers()

    principal, child = result["household"]["members"]
    assert principal["tailscale_identity"] == "principal@example.invalid"
    assert "tailscale_identity" not in child
    assert result["integrations"]["member_chat_enabled_members"] == [
        principal["member_id"]
    ]
    assert result["bridges"]["enabled_members"] == []


def test_bootstrap_reports_expected_pending_bridge_work_without_masking_failures() -> (
    None
):
    pending = {
        "status": "failed",
        "checks": [
            {"name": "bootstrap_sections", "ok": False},
            {"name": "member_mapping_and_bridge_status", "ok": False},
        ],
    }
    assert _bootstrap_diagnostic_status(pending) == (
        "bridge_enrollment_required",
        0,
    )
    pending["checks"].append({"name": "encrypted_event_log", "ok": False})
    assert _bootstrap_diagnostic_status(pending) == ("failed", 1)


def test_lock_contains_pinned_apple_silicon_sqlcipher_wheel() -> None:
    versions = json.loads(
        (REPO_ROOT / "bootstrap" / "versions.json").read_text("utf-8")
    )
    assert versions["sqlcipher"]["python_distribution"] == "sqlcipher3"
    assert versions["sqlcipher"]["python_version"] == "0.6.2"
    lock = (REPO_ROOT / "poetry.lock").read_text("utf-8")
    assert 'name = "sqlcipher3"' in lock
    assert 'version = "0.6.2"' in lock
    assert "sqlcipher3-0.6.2-cp311-cp311-macosx_11_0_arm64.whl" in lock


def test_reviewed_openclaw_source_receipt_binds_exact_executable(
    test_paths: DeploymentPaths,
) -> None:
    versions = json.loads(
        (REPO_ROOT / "bootstrap" / "versions.json").read_text("utf-8")
    )["openclaw"]
    source_root = test_paths.app_dir / "openclaw" / versions["commit"]
    source_root.mkdir(parents=True)
    entry = source_root / "openclaw.mjs"
    entry.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    (source_root / "package.json").write_text(
        json.dumps(
            {
                "name": "openclaw",
                "version": versions["version"],
                "packageManager": versions["package_manager"],
            }
        ),
        encoding="utf-8",
    )
    receipt = {
        "schema_version": 1,
        "install_kind": "verified-github-source-archive",
        "source_root": str(source_root),
        "version": versions["version"],
        "commit": versions["commit"],
        "source_archive_url": versions["source_archive_url"],
        "source_archive_sha256": versions["source_archive_sha256"],
        "package_manager": versions["package_manager"],
    }
    source_receipt_path(test_paths).parent.mkdir(parents=True)
    source_receipt_path(test_paths).write_text(json.dumps(receipt), encoding="utf-8")

    status = reviewed_source_status(test_paths, executable=str(entry))
    assert status["ok"] is True
    assert status["receipt_mismatch_fields"] == []
    assert (
        reviewed_source_status(
            test_paths,
            executable=str(test_paths.root / "wrong-openclaw"),
        )["ok"]
        is False
    )


def test_preflight_wrong_platform_and_arch_leave_no_partial_instance(
    tmp_path: Path,
) -> None:
    target = tmp_path / "never-created"
    with pytest.raises(PreflightFailure):
        run_preflight(
            target,
            system="Windows",
            machine="AMD64",
            macos_version="",
            require_dependencies=False,
            check_filevault=False,
        )
    assert not target.exists()


def test_wizard_accepts_only_the_reviewed_installer_scaffold(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtime(monkeypatch)
    (test_paths.root / "app" / "tooling").mkdir(parents=True)
    (test_paths.root / "bin").mkdir()
    (test_paths.root / "state" / "openclaw").mkdir(parents=True)
    (test_paths.root / "state" / "openclaw" / "source-install.json").write_text(
        "{}", encoding="utf-8"
    )
    assert (
        _wizard(test_paths).run(stop_after_section=1)["sections"]["1"]["status"]
        == "complete"
    )


def test_wizard_rejects_unknown_prebootstrap_top_level_content(
    test_paths: DeploymentPaths,
) -> None:
    (test_paths.root / "unexpected").mkdir(parents=True)
    with pytest.raises(WizardFailure, match="non-empty"):
        _wizard(test_paths).run(stop_after_section=1)


def test_wizard_rejects_duplicate_tailscale_user_bindings(
    test_paths: DeploymentPaths,
) -> None:
    answers = copy.deepcopy(_wizard(test_paths).answers)
    answers["household"]["members"][1]["tailscale_identity"] = answers["household"][
        "members"
    ][0]["tailscale_identity"].upper()
    with pytest.raises(WizardFailure, match="Tailscale user logins must be unique"):
        BootstrapWizard(
            test_paths,
            root=REPO_ROOT,
            fixture=None,
            test_mode=True,
            dry_run=True,
            preflight_overrides={
                "system": "Darwin",
                "machine": "arm64",
                "macos_version": "26.0",
            },
            answers=answers,
        )


def test_deployment_paths_reject_relative_and_broad_targets() -> None:
    with pytest.raises(ValueError, match="absolute"):
        DeploymentPaths.from_root(Path("relative-deployment"))
    with pytest.raises(ValueError, match="broad"):
        DeploymentPaths.from_root(Path.home())


def test_all_sections_resume_independently_and_rerun_is_idempotent(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtime(monkeypatch)
    wizard = _wizard(test_paths)
    for number in range(1, 11):
        state = wizard.run(stop_after_section=number)
        assert all(
            state["sections"][str(done)]["status"] == "complete"
            for done in range(1, number + 1)
        )
    complete = wizard.run()
    before = test_paths.state_path.read_bytes()
    rerun = wizard.run()
    assert test_paths.state_path.read_bytes() == before
    assert [event["section"] for event in rerun["events"]] == list(range(1, 11))
    assert complete["sections"] == rerun["sections"]


def test_interrupted_section_records_failure_then_resumes(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtime(monkeypatch)
    wizard = _wizard(test_paths)
    with pytest.raises(BootstrapInterrupted):
        wizard.run(fail_section=6)
    failed = _state(test_paths)
    assert failed["sections"]["6"]["status"] == "failed"
    resumed = wizard.run()
    assert resumed["sections"]["6"]["status"] == "complete"
    assert resumed["sections"]["6"]["attempts"] == 2


def test_resume_refuses_a_different_answer_set(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtime(monkeypatch)
    original = _wizard(test_paths)
    original.run(stop_after_section=3)
    changed_answers = copy.deepcopy(original.answers)
    changed_answers["assistant"]["identity"] = "A different assistant"
    changed = BootstrapWizard(
        test_paths,
        root=REPO_ROOT,
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
        answers=changed_answers,
    )
    with pytest.raises(RuntimeError, match="different encrypted answer set"):
        changed.run()


def test_portal_chat_can_bootstrap_before_channel_actor_pairing(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtime(monkeypatch)
    fixture = _wizard(test_paths)
    answers = copy.deepcopy(fixture.answers)
    answers["providers"]["assistant"] = "openclaw"
    answers["integrations"]["openclaw_actor_identities"] = []
    wizard = BootstrapWizard(
        test_paths,
        root=REPO_ROOT,
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
        answers=answers,
    )
    state = wizard.run()
    adapters = json.loads(
        (test_paths.instance_dir / "config" / "adapters.json").read_text("utf-8")
    )
    assert adapters["openclaw_actor_identities"] == {}
    assert adapters["member_chat_enabled_members"]
    assert any(
        "Pair exact OpenClaw" in follow_up["action"]
        for follow_up in state["follow_ups"]
    )


def test_seed_questions_never_claim_household_rows_were_created(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtime(monkeypatch)
    _wizard(test_paths).run()
    manifest = json.loads(
        (test_paths.instance_dir / "data" / "seed-manifest.json").read_text("utf-8")
    )
    assert manifest["seeded"] == []
    assert manifest["requested_after_bootstrap"]
    assert "no household rows" in manifest["truthful_status"]


def test_launchd_and_bridge_packages_are_member_bound_and_child_safe(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtime(monkeypatch)
    state = _wizard(test_paths).run()
    adapters = json.loads(
        (test_paths.instance_dir / "config" / "adapters.json").read_text("utf-8")
    )
    assert adapters["member_chat_enabled_members"] == [
        "member_fixture_principal_a",
        "member_fixture_principal_b",
    ]
    assert adapters["child_chat_enabled_members"] == []
    mapping = json.loads(
        (test_paths.instance_dir / "config" / "member-mapping.json").read_text("utf-8")
    )
    assert {relationship["kind"] for relationship in mapping["relationships"]} == {
        "coparent",
        "caregiver",
    }
    for policy_name in ("governance.yaml", "authority.yaml"):
        installed_policy = test_paths.instance_dir / "config" / policy_name
        assert (
            installed_policy.read_bytes()
            == (REPO_ROOT / "bootstrap" / "policy" / policy_name).read_bytes()
        )
        assert (
            str(installed_policy.relative_to(test_paths.root))
            in state["sections"]["3"]["artifacts"]
        )
    for label in (RUNTIME_LABEL, OPENCLAW_LABEL):
        path = test_paths.launch_agent_dir / f"{label}.plist"
        definition = plistlib.loads(path.read_bytes())
        assert definition["Label"] == label
        assert "/opt/homebrew/bin" in definition["EnvironmentVariables"]["PATH"]
        assert definition["EnvironmentVariables"]["ADMINME_INSTANCE_DIR"] == str(
            test_paths.instance_dir
        )
        rendered = json.dumps(definition["ProgramArguments"]).lower()
        assert all(word not in rendered for word in ("secret", "token", "password"))
    store = secret_store(test_paths, test_mode=True)
    key = store.get(state["secret_references"]["bootstrap_state"])
    packages = sorted(
        (test_paths.instance_dir / "bridges" / "enrollment").glob("*.enc")
    )
    assert len(packages) == 3
    for path in packages:
        envelope = json.loads(path.read_text("utf-8"))
        member_id = envelope["member_id"]
        package = json.loads(
            AESGCM(key).decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                member_id.encode(),
            )
        )
        assert package["member_id"] == member_id
        assert package["owner_scope"] == f"private:{member_id}"
        assert package["restrictions"]["cross_owner_claims"] == "deny"
        assert set(package["allowed_packs"]) <= {
            "knowledge-apple-notes",
            "knowledge-apple-voice-memos",
            "knowledge-obsidian",
            "calendaring-apple-calendar-bridge",
            "reminders-apple-eventkit-bridge",
        }
        if package["restrictions"]["kid"]:
            assert "knowledge-obsidian" not in package["allowed_packs"]
            assert package["restrictions"]["obsidian_allowed"] is False


def test_launchers_share_keychain_gateway_token_and_load_member_chat_policy(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtime(monkeypatch)
    state = _wizard(test_paths).run()
    store = secret_store(test_paths, test_mode=True)
    shared = store.get(state["secret_references"]["openclaw_shared"]).decode("ascii")
    monkeypatch.setenv("ADMINME_INSTANCE_ROOT", str(test_paths.root))
    monkeypatch.setattr(runtime_service, "secret_store", lambda _paths: store)

    runtime_capture: dict[str, object] = {}

    def capture_settings(*_args, **kwargs):
        runtime_capture.update(kwargs)
        return object()

    monkeypatch.setattr(
        runtime_service.RuntimeSettings,
        "credential_free",
        staticmethod(capture_settings),
    )
    monkeypatch.setattr(runtime_service, "create_application", lambda value: value)
    uvicorn_capture: dict[str, object] = {}
    monkeypatch.setattr(
        runtime_service.uvicorn,
        "run",
        lambda *_args, **kwargs: uvicorn_capture.update(kwargs),
    )
    runtime_service.main()

    assert runtime_capture["openclaw_gateway_token"] == shared
    assert runtime_capture["openclaw_shared_secret"] == shared
    assert runtime_capture["openclaw_model_configured"] is False
    assert runtime_capture["member_chat_enabled_members"] == frozenset(
        {"member_fixture_principal_a", "member_fixture_principal_b"}
    )
    assert runtime_capture["child_chat_enabled_members"] == frozenset()
    assert uvicorn_capture["host"] == "127.0.0.1"
    assert uvicorn_capture["proxy_headers"] is False
    assert runtime_capture["adapter_config"] == json.loads(
        (test_paths.instance_dir / "config" / "adapters.json").read_text("utf-8")
    )

    monkeypatch.setattr(openclaw_service, "secret_store", lambda _paths: store)
    monkeypatch.setattr(
        openclaw_service,
        "resolve_reviewed_openclaw",
        lambda _paths: "/openclaw",
    )
    monkeypatch.setattr(
        openclaw_service.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="OpenClaw 2026.7.2"),
    )
    exec_capture: dict[str, object] = {}

    def capture_execve(executable, arguments, environment):
        exec_capture.update(
            executable=executable,
            arguments=arguments,
            gateway_token=environment.get("OPENCLAW_GATEWAY_TOKEN"),
        )
        raise SystemExit(0)

    monkeypatch.setattr(openclaw_service.os, "execve", capture_execve)
    with pytest.raises(SystemExit):
        openclaw_service.main()
    assert exec_capture["gateway_token"] == shared
    assert exec_capture["arguments"] == ["/openclaw", "gateway", "run"]


def test_openclaw_activation_probes_server_side_and_writes_metadata_only(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtime(monkeypatch)
    _wizard(test_paths).run()
    commands: list[list[str]] = []
    workspace = test_paths.instance_dir / "openclaw" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ("AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md"):
        (workspace / name).write_text("fixture", encoding="utf-8")

    environments: list[dict[str, str]] = []

    def run(command, **kwargs):
        commands.append(command)
        environments.append(dict(kwargs.get("env", {})))
        return SimpleNamespace(stdout="OpenClaw 2026.7.2")

    monkeypatch.setattr(
        "bootstrap.openclaw_activation.resolve_reviewed_openclaw",
        lambda _paths, test_mode=False: "/openclaw",
    )
    monkeypatch.setattr("bootstrap.openclaw_activation.subprocess.run", run)
    result = activate(test_paths, root=REPO_ROOT, test_mode=True)

    assert commands == [
        ["/openclaw", "--version"],
        ["/openclaw", "configure", "--section", "model", "--section", "channels"],
        [
            "/openclaw",
            "config",
            "set",
            "agents.defaults.workspace",
            str(workspace),
        ],
        ["/openclaw", "config", "validate"],
        ["/openclaw", "models", "status", "--probe"],
        ["/openclaw", "gateway", "status", "--require-rpc"],
    ]
    assert result["contains_credentials"] is False
    assert "OPENCLAW_GATEWAY_TOKEN" not in environments[4]
    assert environments[5]["OPENCLAW_GATEWAY_TOKEN"]
    assert result["cron_reconciled"] is True
    assert result["cron_jobs"] == 5
    assert model_provider_configured(test_paths) is True
    marker = (
        test_paths.instance_dir / "config" / "openclaw-activation.json"
    ).read_text("utf-8")
    assert all(word not in marker.lower() for word in ("api_key", "token", "password"))


def test_start_stop_restart_crash_recovery_states_are_truthful(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtime(monkeypatch)
    _wizard(test_paths).run()
    manager = ServiceManager(test_paths, test_mode=True)
    assert manager.status().status == "stopped"
    assert manager.start().status == "healthy"
    assert manager.start().status == "healthy"
    manager.mark_failed_for_test()
    assert manager.status().status == "failed"
    assert manager.start().status == "healthy"
    assert manager.stop().status == "stopped"


def test_live_services_start_runtime_before_openclaw(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ServiceManager(test_paths)
    commands: list[list[str]] = []
    healthy = ServiceStatus("healthy", "observation_only", "running", True, "fixture")
    monkeypatch.setattr(
        manager, "_validate_plist", lambda label: Path(f"/{label}.plist")
    )
    monkeypatch.setattr(manager, "_launchctl_loaded", lambda _label: True)
    monkeypatch.setattr(manager, "_wait_until_unloaded", lambda _label: True)
    monkeypatch.setattr(
        manager, "_launchctl_definition_matches", lambda _label, _path: True
    )
    monkeypatch.setattr(manager, "_runtime_liveness", lambda: True)
    monkeypatch.setattr(manager, "status", lambda: healthy)

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("bootstrap.services.subprocess.run", run)
    assert manager.start() == healthy
    kickstarts = [command[-1] for command in commands if "kickstart" in command]
    assert kickstarts == [
        f"gui/{os.getuid()}/com.administrateme.runtime",
        f"gui/{os.getuid()}/com.administrateme.openclaw",
    ]


def test_runtime_launch_probe_uses_lightweight_live_endpoint(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            return b'{"status":"alive","runtime_started":true}'

    def urlopen(url: str, *, timeout: int):
        observed.update(url=url, timeout=timeout)
        return Response()

    monkeypatch.setattr("bootstrap.services.urllib.request.urlopen", urlopen)
    manager = ServiceManager(test_paths)

    assert manager._runtime_liveness() is True
    assert str(observed["url"]).endswith("/api/system/live")
    assert observed["timeout"] == 5


def test_service_cold_start_window_is_bounded_and_retries_liveness(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ServiceManager(test_paths)
    outcomes = iter((False, False, True))
    times = iter((0.0, 1.0, 2.0))
    sleeps: list[float] = []
    monkeypatch.setattr(manager, "_runtime_liveness", lambda: next(outcomes))
    monkeypatch.setattr("bootstrap.services.time.monotonic", lambda: next(times))
    monkeypatch.setattr("bootstrap.services.time.sleep", sleeps.append)

    assert RUNTIME_LIVENESS_TIMEOUT_SECONDS == 300
    assert manager._wait_for_runtime_liveness() is True
    assert sleeps == [1, 1]


def test_service_cold_start_window_fails_closed_at_deadline(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ServiceManager(test_paths)
    times = iter((10.0, 12.0))
    monkeypatch.setattr(manager, "_runtime_liveness", lambda: False)
    monkeypatch.setattr("bootstrap.services.time.monotonic", lambda: next(times))

    assert manager._wait_for_runtime_liveness(timeout_seconds=2) is False


def test_keychain_probe_discovers_nested_instance_references(
    test_paths: DeploymentPaths,
) -> None:
    config = test_paths.instance_dir / "config"
    config.mkdir(parents=True)
    account = test_paths.keychain_account()
    first = f"keychain://{account}/event-log"
    second = f"keychain://{account}/google-oauth-primary"
    (config / "secret-references.json").write_text(
        json.dumps({"references": {"event_log": first}}), encoding="utf-8"
    )
    (config / "adapters.json").write_text(
        json.dumps({"adapters": [{"config": {"credential_ref": second}}]}),
        encoding="utf-8",
    )
    (config / "foreign.json").write_text(
        json.dumps({"reference": "keychain://another-instance/ignored"}),
        encoding="utf-8",
    )

    assert configured_keychain_references(test_paths) == (first, second)


def test_keychain_store_resolves_runtime_aliases_without_changing_logical_refs(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = test_paths.instance_dir / "config"
    config.mkdir(parents=True)
    account = test_paths.keychain_account()
    logical = f"keychain://{account}/event-log"
    physical = f"keychain://{account}/event-log-runtime-py314-deadbeefcafe"
    (config / ALIAS_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_commit": "d" * 40,
                "runtime_tag": "py314",
                "aliases": {logical: physical},
            }
        ),
        encoding="utf-8",
    )
    encoded = base64.urlsafe_b64encode(b"event-key").decode("ascii")
    observed: dict[str, str] = {}

    def get_runtime_password(service: str, account_name: str) -> str:
        observed.update(service=service, account=account_name)
        return encoded

    def put_runtime_password(service: str, account_name: str, value: str) -> None:
        observed.update(service=service, account=account_name, value=value)

    monkeypatch.setattr("bootstrap.secrets.get_runtime_password", get_runtime_password)
    monkeypatch.setattr("bootstrap.secrets.put_runtime_password", put_runtime_password)
    store = MacOSKeychainStore(test_paths)

    assert store.get(logical) == b"event-key"
    assert observed["service"].endswith(".event-log-runtime-py314-deadbeefcafe")
    assert store.put("event-log", b"replacement") == logical
    assert observed["service"].endswith(".event-log-runtime-py314-deadbeefcafe")
    assert observed["value"] == base64.urlsafe_b64encode(b"replacement").decode("ascii")


def test_keychain_store_preserves_keyring_for_unaliased_rollback_items(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = test_paths.keychain_account()
    logical = f"keychain://{account}/event-log"
    encoded = base64.urlsafe_b64encode(b"rollback-key").decode("ascii")
    calls: list[tuple[str, str]] = []

    def get_password(service: str, account_name: str) -> str:
        calls.append((service, account_name))
        return encoded

    monkeypatch.setattr("bootstrap.secrets.keyring.get_password", get_password)
    monkeypatch.setattr(
        "bootstrap.secrets.get_runtime_password",
        lambda *_args: pytest.fail("runtime CLI must not read rollback originals"),
    )

    assert MacOSKeychainStore(test_paths).get(logical) == b"rollback-key"
    assert calls == [(f"com.administrateme.{account}.event-log", account)]


def test_keychain_store_rejects_cyclic_or_cross_instance_aliases(
    test_paths: DeploymentPaths,
) -> None:
    config = test_paths.instance_dir / "config"
    config.mkdir(parents=True)
    account = test_paths.keychain_account()
    first = f"keychain://{account}/first"
    second = f"keychain://{account}/second"
    alias_path = config / ALIAS_FILENAME
    alias_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "aliases": {first: second, second: first},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="cycle"):
        MacOSKeychainStore(test_paths)

    alias_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "aliases": {first: "keychain://another-instance/first"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="instance boundary"):
        MacOSKeychainStore(test_paths)


def test_runtime_keychain_migration_pipes_values_and_atomically_publishes_aliases(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = test_paths.instance_dir / "config"
    config.mkdir(parents=True)
    account = test_paths.keychain_account()
    logical = f"keychain://{account}/event-log"
    previous_physical = f"keychain://{account}/event-log-runtime-py314-oldcommit000"
    (config / "secret-references.json").write_text(
        json.dumps({"event_log": logical}), encoding="utf-8"
    )
    (config / ALIAS_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_commit": "a" * 40,
                "runtime_tag": "py314",
                "aliases": {logical: previous_physical},
            }
        ),
        encoding="utf-8",
    )
    candidate_real = test_paths.root / "candidate-real-python"
    candidate_real.parent.mkdir(parents=True, exist_ok=True)
    candidate_real.write_text("#!/bin/sh\n", encoding="utf-8")
    candidate_real.chmod(0o700)
    candidate_python = test_paths.root / "candidate-venv" / "bin" / "python"
    candidate_python.parent.mkdir(parents=True)
    candidate_python.symlink_to(candidate_real)
    encoded_secret = base64.urlsafe_b64encode(b"never-log-this").decode("ascii")
    requested_services: list[str] = []

    def get_runtime_password(service: str, account_name: str) -> str:
        assert account_name == account
        requested_services.append(service)
        return encoded_secret

    transferred: dict[str, object] = {}

    def run(command, **kwargs):
        transferred.update(command=command, **kwargs)
        payload = json.loads(kwargs["input"])
        assert payload["entries"][0]["value"] == encoded_secret
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"status": "verified", "count": 1}),
            stderr="",
        )

    monkeypatch.setattr(
        "adminme.lib.macos_keychain.get_runtime_password",
        get_runtime_password,
    )
    monkeypatch.setattr(
        "bootstrap.keychain_runtime_migration._active_runtime_tag",
        lambda _root: "py314",
    )
    monkeypatch.setattr("bootstrap.keychain_runtime_migration.subprocess.run", run)
    commit = "b" * 40

    result = migrate_keychain_runtime(
        instance_root=test_paths.root,
        candidate_python=candidate_python,
        candidate_commit=commit,
        runtime_tag="py315",
    )

    assert result["count"] == 1
    invoked = transferred["command"]
    assert isinstance(invoked, list)
    assert invoked[0] == str(candidate_python)
    assert requested_services[0].endswith(".event-log-runtime-py314-oldcommit000")
    assert encoded_secret not in " ".join(transferred["command"])
    document = json.loads((config / ALIAS_FILENAME).read_text("utf-8"))
    assert document["aliases"][logical].endswith(
        "/event-log-runtime-py315-bbbbbbbbbbbb"
    )
    assert (config / ALIAS_FILENAME).stat().st_mode & 0o777 == 0o600


def test_runtime_keychain_coordinator_imports_without_candidate_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args, **kwargs):
        if name == "adminme.lib.macos_keychain":
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(REPO_ROOT / "bootstrap" / "keychain_runtime_migration.py"), "--help"],
    )

    with pytest.raises(SystemExit) as captured:
        runpy.run_path(
            str(REPO_ROOT / "bootstrap" / "keychain_runtime_migration.py"),
            run_name="__main__",
        )
    assert captured.value.code == 0


def test_runtime_keychain_migration_reads_active_release_runtime_receipt(
    test_paths: DeploymentPaths,
) -> None:
    release = test_paths.app_dir / "releases" / "reviewed"
    (release / "bootstrap").mkdir(parents=True)
    (release / "bootstrap" / "versions.json").write_text(
        json.dumps({"python": {"preferred": "3.11.15"}}), encoding="utf-8"
    )
    pointer = test_paths.app_dir / "current-release.json"
    pointer.write_text(json.dumps({"path": str(release)}), encoding="utf-8")

    assert _active_runtime_tag(test_paths.root) == "py311"

    pointer.write_text(
        json.dumps({"path": str(test_paths.root.parent / "outside")}),
        encoding="utf-8",
    )
    with pytest.raises(KeychainRuntimeMigrationError, match="escapes"):
        _active_runtime_tag(test_paths.root)


def test_runtime_keychain_migration_reads_legacy_formula_runtime_receipt(
    test_paths: DeploymentPaths,
) -> None:
    release = test_paths.app_dir / "releases" / "legacy-reviewed"
    (release / "bootstrap").mkdir(parents=True)
    (release / "bootstrap" / "versions.json").write_text(
        json.dumps(
            {
                "python": {
                    "compatible": ">=3.11,<3.12",
                    "formula": "python@3.11",
                    "lockfile": "poetry.lock",
                }
            }
        ),
        encoding="utf-8",
    )
    pointer = test_paths.app_dir / "current-release.json"
    pointer.write_text(json.dumps({"path": str(release)}), encoding="utf-8")

    assert _active_runtime_tag(test_paths.root) == "py311"


def test_runtime_keychain_migration_rejects_ambiguous_legacy_runtime_receipt(
    test_paths: DeploymentPaths,
) -> None:
    release = test_paths.app_dir / "releases" / "legacy-invalid"
    (release / "bootstrap").mkdir(parents=True)
    (release / "bootstrap" / "versions.json").write_text(
        json.dumps({"python": {"formula": "python3"}}), encoding="utf-8"
    )
    pointer = test_paths.app_dir / "current-release.json"
    pointer.write_text(json.dumps({"path": str(release)}), encoding="utf-8")

    with pytest.raises(KeychainRuntimeMigrationError, match="runtime receipt"):
        _active_runtime_tag(test_paths.root)


def test_runtime_keychain_migration_does_not_publish_alias_on_child_failure(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = test_paths.instance_dir / "config"
    config.mkdir(parents=True)
    account = test_paths.keychain_account()
    logical = f"keychain://{account}/event-log"
    (config / "secret-references.json").write_text(
        json.dumps({"event_log": logical}), encoding="utf-8"
    )
    stale_physical = f"keychain://{account}/event-log-runtime-py314-stalecommit"
    (config / ALIAS_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_commit": "a" * 40,
                "runtime_tag": "py314",
                "aliases": {logical: stale_physical},
            }
        ),
        encoding="utf-8",
    )
    candidate_python = test_paths.root / "candidate-python"
    candidate_python.write_text("#!/bin/sh\n", encoding="utf-8")
    candidate_python.chmod(0o700)
    requested_services: list[str] = []

    def get_password(service: str, _account_name: str) -> str:
        requested_services.append(service)
        return "encoded-secret"

    monkeypatch.setattr(
        "bootstrap.keychain_runtime_migration.keyring.get_password", get_password
    )
    monkeypatch.setattr(
        "bootstrap.keychain_runtime_migration._active_runtime_tag",
        lambda _root: "py311",
    )
    monkeypatch.setattr(
        "bootstrap.keychain_runtime_migration.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="redacted"
        ),
    )

    with pytest.raises(KeychainRuntimeMigrationError, match="could not create"):
        migrate_keychain_runtime(
            instance_root=test_paths.root,
            candidate_python=candidate_python,
            candidate_commit="c" * 40,
            runtime_tag="py314",
        )
    assert requested_services[0].endswith(".event-log")
    assert not requested_services[0].endswith(".event-log-runtime-py314-stalecommit")
    assert json.loads((config / ALIAS_FILENAME).read_text("utf-8"))["aliases"] == {
        logical: stale_physical
    }
    assert configured_logical_references(config, account) == (logical,)


def test_runtime_migration_keychain_prompt_fails_before_release_preparation(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = UpgradeManager(
        test_paths,
        root=REPO_ROOT,
        test_mode=False,
        approve_runtime_migration=True,
    )
    reference = f"keychain://{test_paths.keychain_account()}/event-log"
    monkeypatch.setattr(
        "bootstrap.upgrade.configured_keychain_references", lambda _paths: (reference,)
    )

    def blocked(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["python"], 8)

    monkeypatch.setattr("bootstrap.upgrade.subprocess.run", blocked)
    with pytest.raises(UpgradeError, match="Do not reset the login Keychain"):
        manager._preflight_keychain_access(REPO_ROOT)
    assert not (test_paths.app_dir / "current-release.json").exists()


def test_live_service_definition_must_match_the_candidate_release(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = test_paths.app_dir / "releases" / "candidate-sha"
    program = release / ".venv" / "bin" / "python"
    path = test_paths.launch_agent_dir / f"{RUNTIME_LABEL}.plist"
    path.parent.mkdir(parents=True)
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": RUNTIME_LABEL,
                "ProgramArguments": [str(program), "-m", "bootstrap.runtime_service"],
                "WorkingDirectory": str(release),
                "EnvironmentVariables": {"ADMINME_REPOSITORY_ROOT": str(release)},
            }
        )
    )
    launchctl_output = (
        f"program = {program}\n"
        f"working directory = {release}\n"
        f"ADMINME_REPOSITORY_ROOT => {release}\n"
    )

    def run(_command, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=launchctl_output, stderr="")

    monkeypatch.setattr("bootstrap.services.subprocess.run", run)
    manager = ServiceManager(test_paths)
    assert manager._launchctl_definition_matches(RUNTIME_LABEL, path) is True

    launchctl_output = launchctl_output.replace("candidate-sha", "previous-sha")
    assert manager._launchctl_definition_matches(RUNTIME_LABEL, path) is False


def test_doctor_export_is_machine_readable_and_contains_no_secret_values(
    test_paths: DeploymentPaths,
) -> None:
    state = _wizard(test_paths).run()
    ServiceManager(test_paths, test_mode=True).start()
    destination = test_paths.root / "diagnostic.json"
    result = Doctor(test_paths, root=REPO_ROOT, test_mode=True).export(destination)
    assert result["status"] == "healthy"
    assert result["safe_to_attach"] is True
    rendered = destination.read_bytes()
    store = secret_store(test_paths, test_mode=True)
    for reference in state["secret_references"].values():
        value = store.get(reference)
        assert value not in rendered
        assert value.hex().encode() not in rendered


def test_observation_cannot_be_trusted_before_briefing_and_period(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtime(monkeypatch)
    state = _wizard(test_paths).run(stop_after_section=8)
    assert state["observation"]["briefing_complete"] is False
    assert state["observation"]["trusted"] is False
    complete = _wizard(test_paths).run()
    assert complete["observation"]["briefing_complete"] is True
    assert complete["observation"]["trust_not_before"]
    assert complete["observation"]["trusted"] is False


def test_upgrade_incompatibility_is_detected_before_service_replacement(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    (candidate / "bootstrap").mkdir(parents=True)
    (candidate / "openclaw" / "administrateme").mkdir(parents=True)
    for source in (
        REPO_ROOT / "poetry.lock",
        REPO_ROOT / "bootstrap" / "versions.json",
        REPO_ROOT / "openclaw" / "administrateme" / "package.json",
        REPO_ROOT / "openclaw" / "administrateme" / "openclaw.plugin.json",
    ):
        relative = source.relative_to(REPO_ROOT)
        destination = candidate / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    versions_path = candidate / "bootstrap" / "versions.json"
    versions = json.loads(versions_path.read_text("utf-8"))
    versions["openclaw"]["version"] = "2099.1.0"
    versions_path.write_text(json.dumps(versions), encoding="utf-8")
    sentinel = tmp_path / "active-service-sentinel"
    sentinel.write_text("unchanged", encoding="utf-8")
    with pytest.raises(UpgradeError):
        validate_candidate(candidate)
    assert sentinel.read_text("utf-8") == "unchanged"


def test_runtime_baseline_change_requires_explicit_pre_switch_approval(
    tmp_path: Path,
) -> None:
    active = tmp_path / "active"
    candidate = tmp_path / "candidate"
    (active / "bootstrap").mkdir(parents=True)
    (candidate / "bootstrap").mkdir(parents=True)
    (candidate / "openclaw" / "administrateme").mkdir(parents=True)
    for source in (
        REPO_ROOT / "poetry.lock",
        REPO_ROOT / "bootstrap" / "versions.json",
        REPO_ROOT / "openclaw" / "administrateme" / "package.json",
        REPO_ROOT / "openclaw" / "administrateme" / "openclaw.plugin.json",
    ):
        destination = candidate / source.relative_to(REPO_ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    active_versions = json.loads(
        (REPO_ROOT / "bootstrap" / "versions.json").read_text("utf-8")
    )
    active_versions["python"]["compatible"] = ">=3.11,<3.12"
    active_versions["node"]["compatible"] = ">=22.22.3,<23"
    (active / "bootstrap" / "versions.json").write_text(
        json.dumps(active_versions), encoding="utf-8"
    )

    with pytest.raises(UpgradeError, match="approve-runtime-migration"):
        validate_candidate(candidate, active_root=active)

    result = validate_candidate(
        candidate,
        active_root=active,
        approve_runtime_migration=True,
    )
    assert result["runtime_migration"] == {
        "python": {"from": ">=3.11,<3.12", "to": ">=3.14,<3.15"},
        "node": {"from": ">=22.22.3,<23", "to": ">=24.15,<25"},
    }


def test_upgrade_waits_only_for_known_post_switch_transients(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transient = {
        "status": "failed",
        "checks": [
            {
                "name": "openclaw_compatibility_and_plugin_discovery",
                "ok": False,
            }
        ],
    }
    healthy = {"status": "healthy", "checks": []}
    reports = [transient, transient, healthy, healthy]
    include_synthetic: list[bool] = []

    class FakeDoctor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run(self, *, include_synthetic_proof: bool = True):
            include_synthetic.append(include_synthetic_proof)
            return reports.pop(0)

    monkeypatch.setattr("bootstrap.upgrade.Doctor", FakeDoctor)
    monkeypatch.setattr("bootstrap.upgrade.time.sleep", lambda _seconds: None)
    result = UpgradeManager(
        test_paths, root=REPO_ROOT, test_mode=False
    )._post_switch_doctor(REPO_ROOT)
    assert result["status"] == "healthy"
    assert include_synthetic == [False, False, False, True]


def test_upgrade_does_not_retry_a_structural_doctor_failure(
    test_paths: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structural = {
        "status": "failed",
        "checks": [{"name": "encrypted_event_log_and_migrations", "ok": False}],
    }
    calls = 0

    class FakeDoctor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run(self, *, include_synthetic_proof: bool = True):
            nonlocal calls
            calls += 1
            return structural

    monkeypatch.setattr("bootstrap.upgrade.Doctor", FakeDoctor)
    result = UpgradeManager(
        test_paths, root=REPO_ROOT, test_mode=False
    )._post_switch_doctor(REPO_ROOT)
    assert result == structural
    assert calls == 1


def test_upgrade_runs_post_switch_doctor_with_candidate_interpreter(
    test_paths: DeploymentPaths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    candidate_python = release / ".venv" / "bin" / "python"
    candidate_python.parent.mkdir(parents=True)
    candidate_python.write_text("fixture", encoding="utf-8")
    monkeypatch.delenv("ADMINME_TEST_MODE", raising=False)
    calls: list[dict[str, object]] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"status": "healthy", "checks": []}),
            stderr="",
        )

    monkeypatch.setattr("bootstrap.upgrade.subprocess.run", fake_run)
    manager = UpgradeManager(test_paths, root=REPO_ROOT, test_mode=False)
    result = manager._run_release_doctor(release, include_synthetic_proof=False)

    assert result["status"] == "healthy"
    assert calls[0]["command"] == [
        str(candidate_python),
        "-m",
        "bootstrap.cli",
        "--instance-root",
        str(test_paths.root),
        "doctor",
        "--no-synthetic-proof",
    ]
    assert calls[0]["cwd"] == release
    assert calls[0]["timeout"] == 120
    environment = calls[0]["env"]
    assert isinstance(environment, dict)
    assert environment["ADMINME_INSTANCE_ROOT"] == str(test_paths.root)
    assert environment["ADMINME_INSTANCE_DIR"] == str(test_paths.instance_dir)
    assert environment["ADMINME_REPOSITORY_ROOT"] == str(release)
    assert environment["OPENCLAW_STATE_DIR"] == str(
        test_paths.instance_dir / "openclaw" / "runtime"
    )
    assert environment["OPENCLAW_CONFIG_PATH"] == str(
        test_paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
    )
    assert str(test_paths.root / "bin") in environment["PATH"]
    assert "/opt/homebrew/opt/node@24/bin" in environment["PATH"]
    assert "PYTHONPATH" not in environment
