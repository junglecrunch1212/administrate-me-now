"""Unit tests for ``discover_packs()`` state-mirror subkind extension.

11b-ii-α-1 generalizes the Cat-D-only ``subkind: knowledge-source``
filter shipped in 11c-i to also accept Cat-B ``subkind: state-mirror``
packs per [D19] + ``docs/10-split-memo-11b-ii-alpha.md`` §1. Unknown
subkinds continue silent-skip (existing 11c-i contract preserved);
malformed manifests still raise ``PackLoadError``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import adminme_bridge.packs as packs_mod
from adminme_bridge.packs import (
    KNOWLEDGE_SOURCE_KIND,
    KNOWLEDGE_SOURCE_SUBKIND,
    PackLoadError,
    STATE_MIRROR_SUBKIND,
    discover_packs,
)


def _write_pack(
    packs_dir: Path,
    *,
    name: str,
    kind: str = KNOWLEDGE_SOURCE_KIND,
    subkind: str | None = KNOWLEDGE_SOURCE_SUBKIND,
    entrypoint: str | None = "adminme_bridge.connector_packs.fake:Adapter",
    pack_name: str | None = None,
    extra: dict[str, Any] | None = None,
    raw_yaml: str | None = None,
) -> Path:
    """Write a synthetic pack.yaml under ``packs_dir/<name>/``.

    F-1 path-exists pre-check + F-4 unique test basename: this fixture
    builds manifests in-test so the production reminders pack is never
    touched (11b-ii-α-2 wires that)."""
    pack_dir = packs_dir / name
    pack_dir.mkdir(parents=True)
    manifest = pack_dir / "pack.yaml"
    if raw_yaml is not None:
        manifest.write_text(raw_yaml, encoding="utf-8")
        return pack_dir
    lines = [f"kind: {kind}"]
    if subkind is not None:
        lines.append(f"subkind: {subkind}")
    lines.append("pack:")
    lines.append(f"  name: {pack_name or name}")
    if entrypoint is not None:
        lines.append(f"entrypoint: {entrypoint}")
    if extra:
        for k, v in extra.items():
            lines.append(f"{k}: {v}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pack_dir


def test_discover_packs_unchanged_yields_knowledge_source_packs(tmp_path: Path) -> None:
    """Existing 11c-i behavior preserved: a Cat-D ``subkind:
    knowledge-source`` pack is yielded as a PackHandle. The on-ramp
    generalization is additive — no Cat-D regression."""
    _write_pack(
        tmp_path,
        name="apple-notes",
        subkind=KNOWLEDGE_SOURCE_SUBKIND,
        entrypoint="adminme_bridge.connector_packs.apple_notes:Adapter",
    )
    handles = list(discover_packs(tmp_path))
    assert len(handles) == 1
    handle = handles[0]
    assert handle.name == "apple-notes"
    assert handle.entrypoint == "adminme_bridge.connector_packs.apple_notes:Adapter"


def test_discover_packs_yields_state_mirror_packs(tmp_path: Path) -> None:
    """[D19] + memo 04 §3.1 + α-1 contract: a Cat-B ``subkind:
    state-mirror`` pack is now yielded alongside Cat-D ones. α-1 ships
    pure-infrastructure (no production state-mirror packs yet — that's
    α-2); the loader extension is testable end-to-end via synthetic
    manifests."""
    _write_pack(
        tmp_path,
        name="apple-eventkit-mirror",
        subkind=STATE_MIRROR_SUBKIND,
        entrypoint="adminme_bridge.connector_packs.apple_eventkit:Adapter",
    )
    _write_pack(
        tmp_path,
        name="apple-notes",
        subkind=KNOWLEDGE_SOURCE_SUBKIND,
        entrypoint="adminme_bridge.connector_packs.apple_notes:Adapter",
    )
    handles = list(discover_packs(tmp_path))
    names = sorted(h.name for h in handles)
    # Both Cat-D and Cat-B packs land in the same yielded list — supervisor
    # widening (BridgeSupervisor.adapters: list[BridgeAdapter]) consumes both.
    assert names == ["apple-eventkit-mirror", "apple-notes"]


def test_discover_packs_silently_skips_unknown_subkind(tmp_path: Path) -> None:
    """Memo 04 §3.1: unknown ``subkind`` values are silently skipped,
    NOT raised. Future Cat-A / Cat-C / Cat-E packs (or typos) don't crash
    the bridge daemon at load time. Pins the silent-skip path stays
    closed to ``knowledge-source`` + ``state-mirror`` only."""
    _write_pack(tmp_path, name="oddball", subkind="some-future-subkind")
    _write_pack(
        tmp_path,
        name="apple-eventkit-mirror",
        subkind=STATE_MIRROR_SUBKIND,
    )
    handles = list(discover_packs(tmp_path))
    assert [h.name for h in handles] == ["apple-eventkit-mirror"]


def test_discover_packs_raises_pack_load_error_on_malformed_state_mirror_manifest(
    tmp_path: Path,
) -> None:
    """A Cat-B manifest with the right ``kind``/``subkind`` but missing
    ``pack.name`` or ``entrypoint`` raises :class:`PackLoadError` — the
    error path is symmetric to the Cat-D contract shipped in 11c-i so
    the loader's failure mode is uniform across subkinds."""
    # subkind matches state-mirror, but pack.name is empty → PackLoadError.
    _write_pack(
        tmp_path,
        name="bad-mirror",
        raw_yaml=(
            f"kind: {KNOWLEDGE_SOURCE_KIND}\n"
            f"subkind: {STATE_MIRROR_SUBKIND}\n"
            "pack:\n"
            '  name: ""\n'
            "entrypoint: adminme_bridge.connector_packs.bad:Adapter\n"
        ),
    )
    with pytest.raises(PackLoadError):
        list(discover_packs(tmp_path))

    # And a Cat-B manifest missing `entrypoint` is equally fatal (no
    # silent bridge-side import drift on α-2 pack publication).
    _write_pack(
        tmp_path,
        name="no-entrypoint-mirror",
        raw_yaml=(
            f"kind: {KNOWLEDGE_SOURCE_KIND}\n"
            f"subkind: {STATE_MIRROR_SUBKIND}\n"
            "pack:\n"
            "  name: no-entrypoint-mirror\n"
        ),
    )
    with pytest.raises(PackLoadError):
        list(discover_packs(tmp_path))


def test_discover_packs_constants_match_module_exports() -> None:
    """Pin the public constants so 11b-ii-α-2 + future Cat-B pack
    consumers (Apple Calendar, Apple Contacts) import them by name
    without the loader's filter literal drifting silently."""
    assert KNOWLEDGE_SOURCE_KIND == "adapter"
    assert KNOWLEDGE_SOURCE_SUBKIND == "knowledge-source"
    assert STATE_MIRROR_SUBKIND == "state-mirror"
    # Both subkinds belong to the loader's allow-list — neither alone
    # is canonical going forward. ``__all__`` carries the constant names
    # (not their string values), so the export check uses the variable
    # name spelling 11b-ii-α-2 imports.
    assert "STATE_MIRROR_SUBKIND" in packs_mod.__all__
    assert "KNOWLEDGE_SOURCE_SUBKIND" in packs_mod.__all__
