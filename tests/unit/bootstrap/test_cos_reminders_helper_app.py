"""Cross-platform static/mock checks for the native component installer."""

from __future__ import annotations

from pathlib import Path

import pytest

from bootstrap import cos_reminders_helper_app as helper


def test_component_identity_excludes_central_release_coordinates() -> None:
    sources = {name: f"fixture:{name}".encode() for name in helper.BUILD_SOURCES}
    manifest = helper._component_manifest(sources)
    assert set(manifest) == {
        "contract",
        "bundle_identifier",
        "python_abi",
        "native_in_process",
        "sources",
    }
    assert helper._component_manifest(dict(sources)) == manifest
    assert (
        helper._info()["NSRemindersFullAccessUsageDescription"]
        == helper.USAGE_DESCRIPTION
    )
    assert helper._info()["NSRemindersUsageDescription"] == helper.USAGE_DESCRIPTION


def test_builder_rejects_changed_loaded_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        helper.source_identity, "reviewed_candidate_commit", lambda root: "a" * 40
    )
    monkeypatch.setattr(helper, "_run", lambda *args, **kwargs: b"different-source")
    for relative in helper.BUILD_SOURCES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"different-source")
    with pytest.raises(RuntimeError, match="loaded helper builder"):
        helper._reviewed_sources(tmp_path, "a" * 40)


def test_builder_requires_expected_protected_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        helper.source_identity, "reviewed_candidate_commit", lambda root: "a" * 40
    )
    with pytest.raises(RuntimeError, match="expected protected commit"):
        helper._reviewed_sources(tmp_path, "b" * 40)


def test_artifact_digest_rejects_symlink_and_writable_resources(tmp_path: Path) -> None:
    app = tmp_path / "Fixture.app"
    app.mkdir()
    resource = app / "resource"
    resource.write_bytes(b"sealed")
    good = helper.helper_artifact_sha256(app)
    resource.write_bytes(b"changed")
    assert helper.helper_artifact_sha256(app) != good
    resource.chmod(0o666)
    with pytest.raises(RuntimeError, match="owner controlled"):
        helper.helper_artifact_sha256(app)
    resource.unlink()
    resource.symlink_to(tmp_path / "outside")
    with pytest.raises(RuntimeError, match="unsafe entry"):
        helper.helper_artifact_sha256(app)
