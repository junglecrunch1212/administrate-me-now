"""Content reuse must not hide changed contracts or retain unbounded history."""

from __future__ import annotations

import gc
import os
import weakref
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Iterator

import pytest
from pydantic import ValidationError

import adminme.source_assurance as module
from adminme.source_assurance import SourceObligation, load_source_obligations


@pytest.fixture(autouse=True)
def clear_manifest_cache() -> Iterator[None]:
    module._cached_source_manifest.cache_clear()
    yield
    module._cached_source_manifest.cache_clear()


def _document(event: str) -> str:
    return (
        "source_assurance:\n"
        f"  - source_events: [{event}]\n"
        "    mode: canonical_observation\n"
    )


def _write(root: Path, name: str, text: str, *, bridge: bool = False) -> Path:
    parent = (
        root / "bridge" / "adminme_bridge" / "connector_packs"
        if bridge
        else root / "packs" / "adapters"
    )
    path = parent / name / "pack.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _events(root: Path) -> set[str]:
    return {item.event_type for item in load_source_obligations(root)}


def test_cached_contracts_preserve_parity_deduplication_and_immutability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform = load_source_obligations(tmp_path)
    document = """source_assurance:
  - source_events: [fixture.created, fixture.updated]
    mode: canonical_projection
    projections: [fixture_view]
  - source_events: [fixture.created]
    mode: canonical_projection
    projections: [fixture_view]
  - source_events: [fixture.requested]
    mode: candidate_assessment
    terminal_events: [fixture.assessed, fixture.refused]
    assessor: pipeline:fixture
"""
    _write(tmp_path, "source", document)
    _write(tmp_path, "same-contract", document, bridge=True)
    expected = tuple(
        sorted(
            (
                *platform,
                SourceObligation(
                    "fixture.created",
                    "canonical_projection",
                    projections=("fixture_view",),
                ),
                SourceObligation(
                    "fixture.updated",
                    "canonical_projection",
                    projections=("fixture_view",),
                ),
                SourceObligation(
                    "fixture.requested",
                    "candidate_assessment",
                    terminal_events=("fixture.assessed", "fixture.refused"),
                    assessor="pipeline:fixture",
                ),
            ),
            key=lambda item: item.key,
        )
    )
    with monkeypatch.context() as patch:
        parse = module.yaml.safe_load
        calls = []

        def counted(text: str) -> object:
            calls.append(text)
            return parse(text)

        patch.setattr(module.yaml, "safe_load", counted)
        first = load_source_obligations(tmp_path)
        assert first == expected
        assert load_source_obligations(tmp_path) == expected
        assert calls == [document]
    declaration = next(item for item in first if item.event_type == "fixture.created")
    with pytest.raises(FrozenInstanceError):
        setattr(declaration, "mode", "canonical_observation")
    assert declaration.projections == ("fixture_view",)
    assert load_source_obligations(tmp_path) == expected


def test_same_size_same_timestamp_edit_is_observed(tmp_path: Path) -> None:
    path = _write(tmp_path, "source", _document("fixture.first"))
    before = path.stat()
    assert "fixture.first" in _events(tmp_path)
    path.write_text(_document("fixture.other"), encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    events = _events(tmp_path)
    assert "fixture.other" in events
    assert "fixture.first" not in events


def test_addition_removal_bridge_and_repository_changes_are_observed(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    path = _write(first, "source", _document("fixture.first"))
    assert "fixture.first" in _events(first)
    bridge = _write(first, "bridge", _document("fixture.bridge"), bridge=True)
    _write(second, "source", _document("fixture.other"))
    assert {"fixture.first", "fixture.bridge"} <= _events(first)
    assert "fixture.other" in _events(second)
    assert "fixture.first" not in _events(second)
    path.unlink()
    assert "fixture.first" not in _events(first)
    assert "fixture.bridge" in _events(first)
    bridge.unlink()
    assert "fixture.bridge" not in _events(first)


@pytest.mark.parametrize(
    ("invalid", "error", "match"),
    [
        ("source_assurance: [", RuntimeError, "cannot load source-assurance manifest"),
        ("[]", RuntimeError, "source-assurance manifest is not a mapping"),
        (
            "source_assurance: [{source_events: [], mode: invalid}]",
            ValidationError,
            "validation errors",
        ),
    ],
)
def test_invalid_replacement_fails_and_repair_recovers(
    tmp_path: Path, invalid: str, error: type[Exception], match: str
) -> None:
    path = _write(tmp_path, "source", _document("fixture.first"))
    assert "fixture.first" in _events(tmp_path)
    path.write_text(invalid, encoding="utf-8")
    for _ in range(2):
        with pytest.raises(error, match=match):
            load_source_obligations(tmp_path)
    path.write_text(_document("fixture.fixed"), encoding="utf-8")
    assert "fixture.fixed" in _events(tmp_path)
    assert "fixture.first" not in _events(tmp_path)


def test_cached_parse_does_not_hide_current_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, "source", _document("fixture.first"))
    assert "fixture.first" in _events(tmp_path)
    original = Path.read_text

    def unreadable(current: Path, *args: object, **kwargs: object) -> str:
        if current == path:
            raise PermissionError("synthetic current read denied")
        return original(current, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(
        RuntimeError, match="cannot load source-assurance manifest"
    ) as error:
        load_source_obligations(tmp_path)
    assert isinstance(error.value.__cause__, PermissionError)


def test_evicted_declarations_are_released(tmp_path: Path) -> None:
    path = _write(tmp_path, "source", _document("fixture.old"))
    declarations = load_source_obligations(tmp_path)
    reference = weakref.ref(
        next(item for item in declarations if item.event_type == "fixture.old")
    )
    del declarations
    assert reference() is not None
    for index in range(module._SOURCE_MANIFEST_CACHE_SIZE + 1):
        path.write_text(_document(f"fixture.new_{index}"), encoding="utf-8")
        load_source_obligations(tmp_path)
    gc.collect()
    assert reference() is None
    info = module._cached_source_manifest.cache_info()
    assert info.currsize == info.maxsize == 32


def test_oversized_valid_document_is_parsed_without_retention(tmp_path: Path) -> None:
    document = (
        _document("fixture.large")
        + "#"
        + "x" * module._SOURCE_MANIFEST_CACHE_MAX_CHARACTERS
    )
    _write(tmp_path, "large", document)
    first = load_source_obligations(tmp_path)
    reference = weakref.ref(
        next(item for item in first if item.event_type == "fixture.large")
    )
    assert load_source_obligations(tmp_path) == first
    assert module._cached_source_manifest.cache_info().currsize == 0
    del first
    gc.collect()
    assert reference() is None
