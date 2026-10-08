"""Explicit boundary double for selector/journal tests without an event log."""

from __future__ import annotations

from pathlib import Path

import pytest

from bootstrap.contracts import DeploymentPaths


def stub_uninitialized_intake(
    monkeypatch: pytest.MonkeyPatch, paths: DeploymentPaths
) -> None:
    """Never accept an initialized instance or broaden into process acceptance."""
    assert not (paths.instance_dir / "events" / "events.db").exists()

    def no_persisted_policy(
        actual: DeploymentPaths, *, test_mode: bool = False
    ) -> frozenset[str]:
        del test_mode
        assert actual.instance_dir == paths.instance_dir
        assert not (actual.instance_dir / "events" / "events.db").exists()
        return frozenset()

    monkeypatch.setattr(
        "bootstrap.upgrade.required_instance_policies", no_persisted_policy
    )


def write_release_manifest_fixture(release: Path) -> None:
    """Populate a skeletal temporary release's real immutable capability data."""
    target = release / "bootstrap" / "versions.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        (
            Path(__file__).resolve().parents[3] / "bootstrap" / "versions.json"
        ).read_bytes()
    )
