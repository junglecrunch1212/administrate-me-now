from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from adminme.products.bridge.auth import EnrollmentMemberIdResolver
from bootstrap.bridge_enrollment import (
    BridgeEnrollmentError,
    BridgeEnrollmentManager,
    MemberBridgeInstaller,
    MemberBridgePaths,
)
from bootstrap.contracts import DeploymentPaths
from bootstrap.wizard import BootstrapWizard


REPO_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 8, 4, 16, 0, tzinfo=timezone.utc)


def _central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DeploymentPaths:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "central")

    async def no_runtime(_wizard: BootstrapWizard, _state: dict) -> None:
        return None

    monkeypatch.setattr(BootstrapWizard, "_initialize_runtime_state", no_runtime)
    BootstrapWizard(
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
    ).run()
    return paths


def test_transfer_accept_is_member_bound_and_consumes_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    central = _central(tmp_path, monkeypatch)
    manager = BridgeEnrollmentManager(
        central,
        root=REPO_ROOT,
        test_mode=True,
        now=lambda: NOW,
    )
    transfer_path = tmp_path / "member.adminmebridge"
    exported = manager.export(
        "member_fixture_principal_a",
        transfer_path,
        central_url="https://central.example-tailnet.ts.net",
        transfer_key=b"t" * 32,
    )
    assert exported.transfer_code not in transfer_path.read_text("utf-8")
    captured: dict = {}

    def submitter(_transfer: dict, envelope: dict) -> str:
        captured.update(envelope)
        return "ev_enrollment_fixture"

    member_paths = MemberBridgePaths.from_root(tmp_path / "member")
    receipt = MemberBridgeInstaller(
        member_paths,
        root=REPO_ROOT,
        test_mode=True,
        now=lambda: NOW + timedelta(minutes=1),
        submitter=submitter,
    ).accept(
        transfer_path,
        exported.transfer_code,
        identity_override="principal-a@example.invalid",
        node_override="node-principal-a",
    )

    assert receipt["status"] == "active"
    assert receipt["transfer_material_retained"] is False
    assert not transfer_path.exists()
    assert captured["owner_scope"] == "private:member_fixture_principal_a"
    assert captured["source_account_id"] == exported.public["enrollment_id"]
    config = member_paths.config_path.read_text("utf-8")
    assert "member_fixture_principal_a" in config
    assert "principal-b" not in config
    assert member_paths.launch_agent_path.is_file()
    assert (
        MemberBridgeInstaller(member_paths, root=REPO_ROOT, test_mode=True).status()[
            "status"
        ]
        == "healthy"
    )


def test_wrong_member_identity_and_expired_packages_fail_before_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    central = _central(tmp_path, monkeypatch)
    manager = BridgeEnrollmentManager(
        central, root=REPO_ROOT, test_mode=True, now=lambda: NOW
    )
    transfer_path = tmp_path / "member.adminmebridge"
    exported = manager.export(
        "member_fixture_principal_a",
        transfer_path,
        central_url="https://central.example-tailnet.ts.net",
        transfer_key=b"u" * 32,
    )
    member_paths = MemberBridgePaths.from_root(tmp_path / "member")
    installer = MemberBridgeInstaller(
        member_paths,
        root=REPO_ROOT,
        test_mode=True,
        now=lambda: NOW + timedelta(minutes=1),
        submitter=lambda _transfer, _envelope: "never",
    )
    with pytest.raises(BridgeEnrollmentError, match="different Tailscale"):
        installer.accept(
            transfer_path,
            exported.transfer_code,
            identity_override="other@example.invalid",
        )
    assert not member_paths.config_path.exists()

    expired_installer = MemberBridgeInstaller(
        member_paths,
        root=REPO_ROOT,
        test_mode=True,
        now=lambda: NOW + timedelta(minutes=31),
        submitter=lambda _transfer, _envelope: "never",
    )
    with pytest.raises(BridgeEnrollmentError, match="expired"):
        expired_installer.accept(
            transfer_path,
            exported.transfer_code,
            identity_override="principal-a@example.invalid",
        )
    assert not member_paths.config_path.exists()


def test_active_member_bridge_must_be_disabled_before_reenrollment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    central = _central(tmp_path, monkeypatch)
    manager = BridgeEnrollmentManager(
        central, root=REPO_ROOT, test_mode=True, now=lambda: NOW
    )
    first_path = tmp_path / "first.adminmebridge"
    first = manager.export(
        "member_fixture_principal_a",
        first_path,
        central_url="https://central.example-tailnet.ts.net",
        transfer_key=b"w" * 32,
    )
    member_paths = MemberBridgePaths.from_root(tmp_path / "member")
    installer = MemberBridgeInstaller(
        member_paths,
        root=REPO_ROOT,
        test_mode=True,
        now=lambda: NOW + timedelta(minutes=1),
        submitter=lambda _transfer, _envelope: "event-first",
    )
    installer.accept(
        first_path,
        first.transfer_code,
        identity_override="principal-a@example.invalid",
    )

    second_path = tmp_path / "second.adminmebridge"
    second = manager.export(
        "member_fixture_principal_a",
        second_path,
        central_url="https://central.example-tailnet.ts.net",
        transfer_key=b"x" * 32,
    )
    with pytest.raises(BridgeEnrollmentError, match="already has an active"):
        installer.accept(
            second_path,
            second.transfer_code,
            identity_override="principal-a@example.invalid",
        )
    assert second_path.is_file()


def test_bridge_resolver_expires_exports_and_keeps_active_records() -> None:
    records = {
        "pending@example.invalid": {
            "member_id": "member-pending",
            "status": "package-exported",
            "expires_at": (NOW + timedelta(minutes=1)).isoformat(),
        },
        "active@example.invalid": {
            "member_id": "member-active",
            "status": "active",
        },
        "revoked@example.invalid": {
            "member_id": "member-revoked",
            "status": "revoked",
        },
    }
    resolver = EnrollmentMemberIdResolver(records, now=lambda: NOW)
    assert resolver.resolve("pending@example.invalid") == "member-pending"
    assert resolver.resolve("PENDING@example.invalid") == "member-pending"
    assert resolver.resolve("active@example.invalid") == "member-active"
    assert resolver.resolve("revoked@example.invalid") is None

    expired = EnrollmentMemberIdResolver(
        records, now=lambda: NOW + timedelta(minutes=2)
    )
    assert expired.resolve("pending@example.invalid") is None
    assert expired.resolve("active@example.invalid") == "member-active"


def test_central_revoke_does_not_change_portal_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    central = _central(tmp_path, monkeypatch)
    manager = BridgeEnrollmentManager(
        central, root=REPO_ROOT, test_mode=True, now=lambda: NOW
    )
    transfer_path = tmp_path / "member.adminmebridge"
    manager.export(
        "member_fixture_principal_a",
        transfer_path,
        central_url="https://central.example-tailnet.ts.net",
        transfer_key=b"v" * 32,
    )
    result = manager.revoke("member_fixture_principal_a")
    assert result["portal_identity_unchanged"] is True
    bridge_index = json.loads(manager.index_path.read_text("utf-8"))
    assert bridge_index["principal-a@example.invalid"]["status"] == "revoked"
    portal_mapping = json.loads(
        (central.instance_dir / "config" / "member-mapping.json").read_text("utf-8")
    )
    principal = next(
        member
        for member in portal_mapping["members"]
        if member["member_id"] == "member_fixture_principal_a"
    )
    assert principal["tailscale_identity"] == "principal-a@example.invalid"
    assert not transfer_path.exists()


def test_export_failure_restores_prior_authorization_and_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    central = _central(tmp_path, monkeypatch)
    manager = BridgeEnrollmentManager(
        central, root=REPO_ROOT, test_mode=True, now=lambda: NOW
    )
    member_id = "member_fixture_principal_a"
    prior_package = manager._load_internal(member_id)
    prior_reference = prior_package["bootstrap_token_reference"]
    prior_index = manager.index_path.read_bytes()
    output = tmp_path / "failed.adminmebridge"

    def fail_restart() -> bool:
        raise BridgeEnrollmentError("injected restart failure")

    monkeypatch.setattr(manager, "_restart_if_running", fail_restart)
    with pytest.raises(BridgeEnrollmentError, match="injected"):
        manager.export(
            member_id,
            output,
            central_url="https://central.example-tailnet.ts.net",
            transfer_key=b"z" * 32,
        )

    assert not output.exists()
    assert manager.index_path.read_bytes() == prior_index
    restored = manager._load_internal(member_id)
    assert restored == prior_package
    assert manager.store.has(prior_reference)
