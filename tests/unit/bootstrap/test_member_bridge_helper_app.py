"""Phase-A packaging proof for the document-opening bridge helper app."""

from __future__ import annotations

import json
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from bootstrap.member_bridge_helper_app import (
    BUNDLE_IDENTIFIER,
    HELPER_APP_CONTRACT,
    build_member_bridge_helper_app,
)
from adminme_bridge.enrollment_ui import (
    LocalSourceChoice,
    MemberBridgeEnrollmentWizard,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="the document-opening helper is compiled and verified on macOS",
)
def test_builds_reviewed_document_helper_without_secret_or_remote_execution(
    tmp_path: Path,
) -> None:
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    destination = tmp_path / "AdministrateMe Bridge Helper.app"
    result = build_member_bridge_helper_app(
        destination, root=REPO_ROOT, expected_commit=commit
    )

    info = plistlib.loads((destination / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == BUNDLE_IDENTIFIER
    assert info["CFBundleDocumentTypes"][0]["CFBundleTypeExtensions"] == [
        "adminmebridge"
    ]
    executable = destination / "Contents" / "MacOS" / info["CFBundleExecutable"]
    assert executable.is_file()
    launcher_path = destination / "Contents" / "Resources" / "bridge-helper-launcher.sh"
    assert stat.S_IMODE(launcher_path.stat().st_mode) == 0o700
    launcher = launcher_path.read_text("utf-8")
    assert "--preview-only" in launcher
    assert "--transfer-code-file" in launcher
    assert "--expected-commit" in launcher
    assert "--release-archive" in launcher
    assert "--release-sha256" in launcher
    assert "--complete-wizard" in launcher
    assert "bridge-complete" in launcher
    assert "bridge-uninstall" in launcher
    assert "TRANSFER_CODE" in launcher
    assert "unset TRANSFER_CODE" in launcher
    assert "--transfer-code " not in launcher
    assert "curl " not in launcher
    assert "ssh " not in launcher
    assert str(REPO_ROOT) not in launcher

    handler = (
        destination / "Contents" / "Resources" / "handler-source.applescript"
    ).read_text("utf-8")
    assert "on open enrollmentFiles" in handler
    assert "--manage" in handler

    manifest = json.loads(
        (destination / "Contents" / "Resources" / "helper-manifest.json").read_text(
            "utf-8"
        )
    )
    assert manifest["contract"] == HELPER_APP_CONTRACT
    assert manifest["release_commit"] == commit == result.release_commit
    assert manifest["runtime_commit"] == commit
    assert (
        manifest["helper_component_commit"] == commit == result.helper_component_commit
    )
    assert manifest["runtime_compatibility"]["runtime_inputs_unchanged"] is True
    assert len(manifest["installer_sha256"]) == 64
    assert len(manifest["release_archive_sha256"]) == 64
    assert result.release_archive_sha256 == manifest["release_archive_sha256"]
    assert (
        destination / "Contents" / "Resources" / "administrateme-reviewed-release.tar"
    ).is_file()
    assert manifest["transfer_secret_in_argv"] is False
    assert manifest["provider_writes"] is False
    assert manifest["auto_updater"] is False
    assert manifest["remote_shell"] is False
    assert manifest["arbitrary_central_code"] is False
    assert manifest["tcc_dialog_automation"] is False
    assert manifest["setup_diagnostics"] == {
        "contract": "administrateme-bridge-setup-diagnostic-v1",
        "private_terminal_receipts": True,
        "dependency_log_limit_bytes": 262144,
        "retains_enrollment_output": False,
        "retains_transfer_code": False,
    }
    failure_dialog = (
        destination / "Contents" / "Resources" / "failed.applescript"
    ).read_text()
    assert (
        'phase is in {"tooling_setup", "environment_setup", "dependency_install"}'
        in failure_dialog
    )
    assert "No provider write was authorized." in failure_dialog
    # Compilation catches native dialog syntax without showing any dialog or
    # exercising member permissions. The droplet compiler only checks handler
    # source; bundled dialog scripts need their own hosted macOS syntax gate.
    compiled_dialogs = tmp_path / "compiled-dialogs"
    compiled_dialogs.mkdir()
    for script in (destination / "Contents" / "Resources").glob("*.applescript"):
        syntax = subprocess.run(
            [
                "/usr/bin/osacompile",
                "-o",
                str(compiled_dialogs / f"{script.stem}.scpt"),
                str(script),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert syntax.returncode == 0, f"{script.name}: {syntax.stderr}"

    verification = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(destination)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verification.returncode == 0, verification.stderr

    # Confirm that both final document metadata and embedded installer content
    # participate in the seal, rather than merely checking the droplet binary.
    for sealed_path in (destination / "Contents" / "Info.plist", launcher_path):
        original = sealed_path.read_bytes()
        sealed_path.write_bytes(original + b"\n")
        changed = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(destination)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert changed.returncode != 0
        sealed_path.write_bytes(original)

    with pytest.raises(RuntimeError, match="new, narrow"):
        build_member_bridge_helper_app(
            destination, root=REPO_ROOT, expected_commit=commit
        )


class _FakeHelper:
    def __init__(self) -> None:
        self.capability_states = {
            "apple_calendar": "requested",
            "apple_reminders": "requested",
        }
        self.calls: list[tuple[object, ...]] = []

    def open_permission_settings(self, capability_id: str) -> None:
        self.calls.append(("open", capability_id))

    def pack_config_for(self, capability_id: str, **coordinates: object) -> dict:
        self.calls.append(("config", capability_id, coordinates))
        return {"member_id": "fixture-member"}

    def record_permission(
        self, capability_id: str, *, granted: bool, pack_config: dict | None = None
    ) -> dict:
        self.calls.append(("permission", capability_id, granted, pack_config))
        self.capability_states[capability_id] = (
            "permission_verified" if granted else "permission_denied"
        )
        return {}

    def run_capability_test(self, capability_id: str, probe: object) -> dict:
        self.calls.append(("test", capability_id))
        assert callable(probe)
        assert probe(capability_id, {"content_allowed": False}) == {
            "status": "passed",
            "content_exposed": False,
        }
        return {}

    def prove_and_start(self, capability_id: str) -> None:
        self.calls.append(("attest", capability_id))
        self.capability_states[capability_id] = "observation_starting"

    def record_observing(self, capability_id: str) -> None:
        self.calls.append(("observing", capability_id))
        self.capability_states[capability_id] = "observing"

    def record_prerequisite_missing(self, capability_id: str, *, reason: str) -> None:
        self.calls.append(("prerequisite", capability_id, reason))

    def record_capability_degraded(self, capability_id: str, *, reason: str) -> None:
        self.calls.append(("degraded", capability_id, reason))

    def finish_permission_phase(self) -> dict[str, str]:
        self.calls.append(("finish",))
        return {"state": "settling"}


class _FakeInteraction:
    def __init__(self) -> None:
        self.result: dict | None = None

    def confirm_permission(
        self, capability_id: str, label: str, requirement: str
    ) -> bool:
        assert label and requirement
        return capability_id == "apple_calendar"

    def choose_source(self, capability_id: str, label: str) -> LocalSourceChoice:
        assert capability_id == "apple_calendar" and label
        return LocalSourceChoice("fixture-calendar", "fixture-account")

    def content_free_probe(
        self, capability_id: str, choice: LocalSourceChoice
    ) -> dict[str, object]:
        assert capability_id == "apple_calendar"
        assert choice.source_coordinate == "fixture-calendar"
        return {"status": "passed", "content_exposed": False}

    def show_result(self, result: dict) -> None:
        self.result = result


def test_native_workflow_continues_capabilities_without_terminal_steps() -> None:
    helper = _FakeHelper()
    interaction = _FakeInteraction()

    result = MemberBridgeEnrollmentWizard(helper, interaction).run()  # type: ignore[arg-type]

    assert result == interaction.result
    assert result["state"] == "settling"
    assert result["observing"] == ["Apple Calendar"]
    assert result["blocked"] == ["Apple Reminders"]
    assert ("attest", "apple_calendar") in helper.calls
    assert ("permission", "apple_reminders", False, None) in helper.calls
    assert result["provider_writes_authorized"] is False
    assert result["provider_effects"] is False
