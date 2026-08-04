"""Focused safety tests for the Gate 5 macOS operations package."""

from __future__ import annotations

import base64
import json
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bootstrap.contracts import OPENCLAW_LABEL, RUNTIME_LABEL, DeploymentPaths
from bootstrap.doctor import Doctor
from bootstrap.preflight import PreflightFailure, run_preflight
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from bootstrap.state import EncryptedStateStore
from bootstrap.upgrade import UpgradeError, validate_candidate
from bootstrap.wizard import BootstrapInterrupted, BootstrapWizard
import bootstrap.openclaw_service as openclaw_service
import bootstrap.runtime_service as runtime_service
from bootstrap.openclaw_activation import activate, model_provider_configured

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINTS = (
    "bootstrap-macos.sh",
    "install.sh",
    "doctor.sh",
    "start.sh",
    "stop.sh",
    "status.sh",
    "backup.sh",
    "restore.sh",
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
    for label in (RUNTIME_LABEL, OPENCLAW_LABEL):
        path = test_paths.launch_agent_dir / f"{label}.plist"
        definition = plistlib.loads(path.read_bytes())
        assert definition["Label"] == label
        assert "/opt/homebrew/bin" in definition["EnvironmentVariables"]["PATH"]
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
        if package["restrictions"]["kid"]:
            assert "obsidian" not in package["allowed_adapters"]
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
    monkeypatch.setattr(runtime_service.uvicorn, "run", lambda *_args, **_kwargs: None)
    runtime_service.main()

    assert runtime_capture["openclaw_gateway_token"] == shared
    assert runtime_capture["openclaw_shared_secret"] == shared
    assert runtime_capture["openclaw_model_configured"] is False
    assert runtime_capture["member_chat_enabled_members"] == frozenset(
        {"member_fixture_principal_a", "member_fixture_principal_b"}
    )
    assert runtime_capture["child_chat_enabled_members"] == frozenset()

    monkeypatch.setattr(openclaw_service, "secret_store", lambda _paths: store)
    monkeypatch.setattr(openclaw_service.shutil, "which", lambda _name: "/openclaw")
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
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(stdout="OpenClaw 2026.7.2")

    monkeypatch.setattr(
        "bootstrap.openclaw_activation.shutil.which", lambda _name: "/openclaw"
    )
    monkeypatch.setattr("bootstrap.openclaw_activation.subprocess.run", run)
    result = activate(test_paths)

    assert commands == [
        ["/openclaw", "--version"],
        ["/openclaw", "configure", "--section", "model", "--section", "channels"],
        ["/openclaw", "config", "validate"],
        ["/openclaw", "models", "status", "--probe"],
    ]
    assert result["contains_credentials"] is False
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
    assert manager.stop().status == "stopped"


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
