"""Private provisioning changes only reviewed policy and a new preparation file."""

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from adminme.workstation_browser import BROWSER_POLICY_FILE
from adminme.workstation_inventory import INVENTORY_FILE
from bootstrap.workstation_browser import prepare_config, provision
from tests.unit.test_workstation_inventory import document


def private_file(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def test_private_browser_preparation_preserves_existing_config_and_credentials(
    tmp_path,
):
    previous = tmp_path / "gateway.json"
    original = {
        "gateway": {"auth": {"token": "synthetic-test-only"}},
        "agents": {"list": [{"id": "service", "tools": {"allow": ["fixture"]}}]},
    }
    private_file(previous, original)
    output = tmp_path / "prepared.json"
    assert prepare_config(previous, output)["state"] == "prepared_not_activated"
    prepared = json.loads(output.read_text())
    assert json.loads(previous.read_text()) == original
    assert prepared["gateway"]["auth"] == original["gateway"]["auth"]
    assert prepared["agents"] == original["agents"]
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        prepare_config(previous, output)
    link = tmp_path / "output-link"
    link.symlink_to(previous)
    with pytest.raises(FileExistsError):
        prepare_config(previous, link)


def test_verified_tab_refresh_uses_current_revision_and_can_revoke_without_evidence(
    tmp_path,
):
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    private_file(config / INVENTORY_FILE, document(datetime.now(timezone.utc)))
    policy = {
        "revision": 1,
        "enabled": True,
        "resources": [
            {
                "resource_id": "own-page",
                "inventory_binding_id": "cos-browser",
                "inventory_binding_revision": 1,
                "native_target_id": "actual-native-target",
                "target_evidence_reference": "operator-observation:profile-and-tab",
                "url": "https://work.example.invalid/planning",
                "identity_probes": {
                    key: {"selector": f"#{key}"}
                    for key in ("account", "workspace", "role")
                },
                "audience_member_ids": ["member-a"],
            }
        ],
    }
    assert provision(config, policy, expected_revision=0)["enabled"] is True
    assert (config / BROWSER_POLICY_FILE).stat().st_mode & 0o777 == 0o600
    policy["revision"] = 2
    policy["resources"][0]["native_target_id"] = "replacement-native-target"
    with pytest.raises(ValueError, match="revision"):
        provision(config, policy, expected_revision=0)
    assert provision(config, policy, expected_revision=1)["revision"] == 2
    (config / INVENTORY_FILE).unlink()
    with pytest.raises(FileNotFoundError):
        provision(config, {**policy, "revision": 3}, expected_revision=2)
    assert (
        provision(
            config, {**policy, "revision": 3, "enabled": False}, expected_revision=2
        )["enabled"]
        is False
    )
    config.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        provision(config, {**policy, "revision": 4}, expected_revision=3)
