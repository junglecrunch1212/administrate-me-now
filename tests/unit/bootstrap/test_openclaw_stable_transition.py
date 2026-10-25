"""Exact reviewed OpenClaw API transition admission, without starting a runtime."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from bootstrap.contracts import load_versions
from bootstrap.upgrade import UpgradeError, validate_candidate

ROOT = Path(__file__).resolve().parents[3]
OLD_COMMIT = "ce67ffb70e83e5ebb56ea02e814e577153f84f61"
NEW_COMMIT = "157d8c2bc3ea2fa183afdc11088e69690bf8e64e"


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _releases(tmp_path: Path) -> tuple[Path, Path]:
    old, new = tmp_path / "old", tmp_path / "new"
    for root in (old, new):
        for name in (
            "poetry.lock",
            "bootstrap/versions.json",
            "openclaw/administrateme/package.json",
            "openclaw/administrateme/openclaw.plugin.json",
        ):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
    versions = load_versions(old)
    versions["node"].update(preferred="24.19.0", compatible=">=24.15,<25")
    versions["openclaw"].update(
        version="2026.7.2", plugin_api="2026.7.2", commit=OLD_COMMIT
    )
    versions["openclaw"].pop("reviewed_predecessor", None)
    versions["openclaw"].pop("supersedes_commit", None)
    _write(old / "bootstrap/versions.json", versions)
    path = old / "openclaw/administrateme/package.json"
    package = json.loads(path.read_text())
    package["openclaw"]["build"].update(
        openclawCommit=OLD_COMMIT,
        openclawVersion="2026.7.2",
        pluginSdkVersion="2026.7.2",
    )
    package["openclaw"]["compat"].update(
        pluginApi="2026.7.2", minGatewayVersion="2026.7.2"
    )
    _write(path, package)
    return old, new


def test_reviewed_api_transition_keeps_explicit_node_baseline_admission(
    tmp_path: Path,
) -> None:
    old, new = _releases(tmp_path)
    before = (old / "bootstrap/versions.json").read_bytes()
    with pytest.raises(UpgradeError, match="approve-runtime-migration"):
        validate_candidate(new, active_root=old)
    result = validate_candidate(new, active_root=old, approve_runtime_migration=True)
    assert result["openclaw_migration"] == {"from": OLD_COMMIT, "to": NEW_COMMIT}
    assert result["openclaw_version"] == "2026.10.7"
    assert set(result["runtime_migration"]) == {"node"}
    assert result["runtime_migration"]["node"]["from"]["formula"] == "node@24"
    assert result["runtime_migration"]["node"]["to"]["formula"] == "node@24"
    assert (old / "bootstrap/versions.json").read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [("commit", "0" * 40), ("version", "2026.7.1"), ("plugin_api", "2026.7.1")],
)
def test_other_predecessor_identity_is_refused(
    tmp_path: Path, field: str, value: str
) -> None:
    old, new = _releases(tmp_path)
    versions = load_versions(old)
    versions["openclaw"][field] = value
    _write(old / "bootstrap/versions.json", versions)
    with pytest.raises(UpgradeError, match="exact reviewed transition"):
        validate_candidate(new, active_root=old, approve_runtime_migration=True)


def test_commit_successor_alone_cannot_authorize_api_change(tmp_path: Path) -> None:
    old, new = _releases(tmp_path)
    versions = load_versions(new)
    versions["openclaw"].pop("reviewed_predecessor")
    _write(new / "bootstrap/versions.json", versions)
    with pytest.raises(UpgradeError, match="exact reviewed transition"):
        validate_candidate(new, active_root=old, approve_runtime_migration=True)


@pytest.mark.parametrize(
    "section,field",
    [
        ("build", "openclawCommit"),
        ("build", "openclawVersion"),
        ("build", "pluginSdkVersion"),
        ("compat", "pluginApi"),
        ("compat", "minGatewayVersion"),
    ],
)
def test_mixed_plugin_and_gateway_contract_is_refused(
    tmp_path: Path, section: str, field: str
) -> None:
    old, new = _releases(tmp_path)
    path = new / "openclaw/administrateme/package.json"
    package = json.loads(path.read_text())
    package["openclaw"][section][field] = "unreviewed"
    _write(path, package)
    with pytest.raises(UpgradeError, match="contradicts"):
        validate_candidate(new, active_root=old, approve_runtime_migration=True)


def test_reverse_api_transition_is_refused_even_with_binary_rollback_flag(
    tmp_path: Path,
) -> None:
    # The target migrates shared/agent SQLite schemas from6/16 to20/25.
    # Source identity alone cannot prove the older reader can recover that state.
    old, new = _releases(tmp_path)
    for allow in (False, True):
        with pytest.raises(UpgradeError, match="exact reviewed transition"):
            validate_candidate(
                old,
                active_root=new,
                approve_runtime_migration=True,
                allow_openclaw_rollback=allow,
            )
