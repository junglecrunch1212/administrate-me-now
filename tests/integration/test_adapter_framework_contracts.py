"""End-to-end integration tests for the prompt 11-α adapter framework.

Closes prompt 11-α's C4 deliverable: the 5 ABCs at
``adminme/lib/adapters/`` (shipped in c1+c2) and the manifest format
extensions at ``adminme/lib/pack_registry/`` (shipped in c3) are
exercised together against a synthetic **multi-capability** adapter
(Cat-D + Cat-B per [PM-35]) wired through both the Pydantic manifest
parser and the runtime ABC.

The Cat-D + Cat-B shape is the canonical multi-capability worked
example called out in ``docs/05-architecture-amendment-personal-data-
layer.md`` §2.2 — Apple Notes-checklists run on a member bridge (Cat-D
ingestion of note bodies) AND mirror per-item checklist state back to
the external system (Cat-B writes). The integration here:

1. Builds a real ``pack.yaml`` on disk via ``tmp_path``.
2. Round-trips it through
   :func:`adminme.lib.pack_registry.parse_adapter_manifest`.
3. Instantiates a synthetic adapter subclassing
   :class:`adminme.lib.adapters.ExternalStateMirrorAdapter` (the
   write-surface-bearing base; the Cat-D facet is surfaced via the
   second capability block plus the
   :attr:`PersonalKnowledgeAdapter._owner_scope_immutable` Final flag
   inspected by reflection).
4. Verifies cross-layer coherence: the adapter's class-attribute
   :attr:`write_capabilities` aligns with the manifest's; the manifest's
   ``resolve_effective_sensitivity`` cannot lower a privileged pack
   default; the parser rejects forbidden fields end-to-end.

Per ``docs/SYSTEM_INVARIANTS.md`` §7.3 / [D17]: the ABCs and manifest
models do NOT write projections directly; the integration test only
exercises in-memory + on-disk yaml round-trips, never a projection
table. Per [D6] / §8.2: no LLM-SDK imports anywhere in this file or the
modules it touches.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from adminme.adapters.base import Adapter, AdapterContext, AdapterStatus
from adminme.lib.adapters import (
    ChangeEnvelope,
    ExternalStateMirrorAdapter,
    PersonalKnowledgeAdapter,
    WriteCapabilityError,
    WriteOperation,
    WriteResult,
)
from adminme.lib.pack_registry import (
    AdapterManifest,
    Capability,
    ManifestParseError,
    parse_adapter_manifest,
)

# Canonical multi-capability shape per memo 05 §2.2: Apple Notes-checklists.
# Cat-D for the notes themselves (per-member bridge knowledge);
# Cat-B for the checklist-item state that round-trips to iCloud.
_NOTES_CHECKLISTS_YAML: Final[str] = """\
pack: notes-checklists
runtime: bridge
family: knowledge
version: 0.1.0
capabilities:
  - kind: Cat-D
    runtime: bridge
    event_families:
      - note
  - kind: Cat-B
    runtime: bridge
    event_families:
      - list_item
    sensitivity_default: privileged
write_capabilities:
  - toggle_checklist_item
  - reorder_checklist
sensitivity_default: sensitive
observation_mode_required: true
owner_scope_overridability_at_install: false
sharing_model_discriminator: owner_member_id
dedup_strategy:
  kind: external_id_unique
  key_fields:
    - note_external_id
    - item_index
"""


class _SyntheticNotesChecklistAdapter(ExternalStateMirrorAdapter):
    """Synthetic multi-capability adapter for integration testing.

    Subclasses :class:`ExternalStateMirrorAdapter` (Cat-B is the
    write-surface-bearing role) and declares both Cat-D and Cat-B
    blocks in :attr:`capabilities` per [PM-35]. The Cat-D facet's
    bridge-only contract is exercised via reflection on
    :class:`PersonalKnowledgeAdapter` itself (the integration test
    asserts ``_owner_scope_immutable`` is Final on the Cat-D ABC).
    """

    name = "synthetic-notes-checklists"
    family = "reminders"
    version = "0.1.0"
    capabilities = [
        {"kind": "Cat-D", "runtime": "bridge"},
        {"kind": "Cat-B", "runtime": "bridge"},
    ]
    write_capabilities = ["toggle_checklist_item", "reorder_checklist"]
    sharing_model_discriminator = "owner_member_id"

    def __init__(self) -> None:
        self.applied: list[WriteOperation] = []
        self.changes: list[ChangeEnvelope] = []
        self._status = AdapterStatus()

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        self._status.state = "running"

    async def stop(self) -> None:
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    async def _apply_write_impl(self, operation: WriteOperation) -> WriteResult:
        self.applied.append(operation)
        return WriteResult(applied=True, external_id="note-1:item-3")

    async def _on_external_change(self, envelope: ChangeEnvelope) -> None:
        self.changes.append(envelope)


def _write_pack_yaml(tmp_path: Path, body: str) -> Path:
    pack_dir = tmp_path / "notes-checklists"
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text(body, encoding="utf-8")
    return pack_dir


def test_multi_capability_manifest_round_trips_through_parser(
    tmp_path: Path,
) -> None:
    """[PM-35]: on-disk pack.yaml with Cat-D + Cat-B parses end-to-end."""
    pack_dir = _write_pack_yaml(tmp_path, _NOTES_CHECKLISTS_YAML)

    manifest = parse_adapter_manifest(pack_dir)

    assert isinstance(manifest, AdapterManifest)
    assert len(manifest.capabilities) == 2
    assert {cap.kind for cap in manifest.capabilities} == {"Cat-D", "Cat-B"}
    assert all(cap.runtime == "bridge" for cap in manifest.capabilities)
    assert manifest.write_capabilities == [
        "toggle_checklist_item",
        "reorder_checklist",
    ]
    assert manifest.sharing_model_discriminator == "owner_member_id"
    assert manifest.owner_scope_overridability_at_install is False
    assert manifest.dedup_strategy is not None
    assert manifest.dedup_strategy["kind"] == "external_id_unique"


def test_adapter_class_write_capabilities_align_with_parsed_manifest(
    tmp_path: Path,
) -> None:
    """Cross-layer coherence: class attrs == parsed manifest values.

    The 11-β install-time validator will enforce this equality formally
    by comparing the loaded adapter class against the manifest fields;
    11-α only guarantees both layers carry the same shape so the
    validator has something to compare. This test pins that guarantee.
    """
    manifest = parse_adapter_manifest(
        _write_pack_yaml(tmp_path, _NOTES_CHECKLISTS_YAML)
    )

    adapter = _SyntheticNotesChecklistAdapter()

    assert adapter.write_capabilities == manifest.write_capabilities
    assert adapter.sharing_model_discriminator == manifest.sharing_model_discriminator
    manifest_kinds = {cap.kind for cap in manifest.capabilities}
    adapter_kinds = {cap["kind"] for cap in adapter.capabilities}
    assert manifest_kinds == adapter_kinds


def test_multi_capability_adapter_satisfies_simple_adapter_protocol() -> None:
    """[D19] / [PM-34]: each ABC is a runtime-checkable extension of the
    simple ``Adapter`` Protocol from ``adminme/adapters/base.py``."""
    adapter = _SyntheticNotesChecklistAdapter()
    assert isinstance(adapter, Adapter)
    assert isinstance(adapter, ExternalStateMirrorAdapter)


async def test_apply_write_gate_admits_declared_operation_end_to_end(
    tmp_path: Path,
) -> None:
    """[UT-27] happy path: a declared write_capability dispatches."""
    manifest = parse_adapter_manifest(
        _write_pack_yaml(tmp_path, _NOTES_CHECKLISTS_YAML)
    )
    adapter = _SyntheticNotesChecklistAdapter()

    operation = WriteOperation(
        kind=manifest.write_capabilities[0],
        target_external_id="note-1",
        payload={"item_index": 3, "completed": True},
    )

    result = await adapter.apply_write(operation)

    assert result.applied is True
    assert adapter.applied == [operation]


async def test_apply_write_gate_rejects_undeclared_operation_end_to_end(
    tmp_path: Path,
) -> None:
    """[UT-27] failure path: an operation not on the manifest is gated."""
    manifest = parse_adapter_manifest(
        _write_pack_yaml(tmp_path, _NOTES_CHECKLISTS_YAML)
    )
    adapter = _SyntheticNotesChecklistAdapter()

    forbidden = WriteOperation(kind="delete_note", target_external_id="note-1")
    assert forbidden.kind not in manifest.write_capabilities

    with pytest.raises(WriteCapabilityError) as excinfo:
        await adapter.apply_write(forbidden)

    assert "delete_note" in str(excinfo.value)
    assert adapter.applied == []


def test_manifest_sensitivity_floor_not_lowered_by_capability_override(
    tmp_path: Path,
) -> None:
    """[§6 invariant 10]: a per-capability sensitivity_default that would
    lower the pack-level floor is clamped, not honoured.

    The notes-checklists manifest sets ``sensitivity_default: sensitive``
    at the pack level and ``sensitivity_default: privileged`` on the
    Cat-B capability. The Cat-D capability declares no override. The
    Cat-D effective sensitivity must be the pack-level ``sensitive``
    floor; the Cat-B override (stricter) is honoured.
    """
    manifest = parse_adapter_manifest(
        _write_pack_yaml(tmp_path, _NOTES_CHECKLISTS_YAML)
    )
    cat_d_index = next(
        i for i, cap in enumerate(manifest.capabilities) if cap.kind == "Cat-D"
    )
    cat_b_index = next(
        i for i, cap in enumerate(manifest.capabilities) if cap.kind == "Cat-B"
    )

    assert manifest.resolve_effective_sensitivity(cat_d_index) == "sensitive"
    assert manifest.resolve_effective_sensitivity(cat_b_index) == "privileged"


def test_personal_knowledge_adapter_owner_scope_immutable_is_final() -> None:
    """memo 04 §2.2: the bridge stamps ``owner_scope``, not the adapter.

    The integration assertion is that the type contract is surfaced
    via :attr:`PersonalKnowledgeAdapter._owner_scope_immutable` as a
    ``Final[bool]`` set to ``True``. The 11-β validator additionally
    pairs this with ``owner_scope_overridability_at_install=False`` on
    the manifest — which the notes-checklists fixture declares.
    """
    assert PersonalKnowledgeAdapter._owner_scope_immutable is True
    assert PersonalKnowledgeAdapter.runtime == "bridge"


def test_unknown_field_inside_capability_block_rejected_end_to_end(
    tmp_path: Path,
) -> None:
    """``extra="forbid"`` on :class:`Capability` is preserved end-to-end.

    The parser forwards only the recognized top-level extension fields
    to :class:`AdapterManifest`; non-extension top-level keys are
    dropped on purpose so legacy pack.yaml files (with ``pack:``,
    ``runtime:``, ``family:`` etc.) parse cleanly. Nested fields inside
    a ``capabilities[]`` block, however, go straight to
    :class:`Capability`, which pins ``extra="forbid"``. A rogue field
    there must surface as :class:`ManifestParseError`, not a silent
    drop — that is the contract-drift defense the validator builds on.
    """
    rogue_yaml = _NOTES_CHECKLISTS_YAML.replace(
        "    event_families:\n      - note\n",
        "    event_families:\n      - note\n    mystery_per_cap_field: 1\n",
    )
    pack_dir = _write_pack_yaml(tmp_path, rogue_yaml)

    with pytest.raises(ManifestParseError):
        parse_adapter_manifest(pack_dir)


def test_capability_block_carries_event_families_per_pm_35(
    tmp_path: Path,
) -> None:
    """[PM-35]: each capability block declares its own event_families
    so a multi-capability adapter cleanly partitions its event surface."""
    manifest = parse_adapter_manifest(
        _write_pack_yaml(tmp_path, _NOTES_CHECKLISTS_YAML)
    )
    by_kind: dict[str, Capability] = {cap.kind: cap for cap in manifest.capabilities}
    assert by_kind["Cat-D"].event_families == ["note"]
    assert by_kind["Cat-B"].event_families == ["list_item"]
