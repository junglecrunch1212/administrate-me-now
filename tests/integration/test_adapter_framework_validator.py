"""End-to-end integration tests for the prompt 11-β install-time validator.

Closes prompt 11-β's C4 deliverable: the six per-check modules at
``adminme/lib/pack_registry/validator/`` (shipped in c1+c2+c3) and the
``validate_adapter_pack`` aggregator (shipped in c1, expanded in c2+c3)
are exercised together against realistic pack shapes — both
synthetic-on-``tmp_path`` and the three existing on-main packs (Gmail,
Plaid, apple-eventkit) that the validator must remain backward-compat
with per prompt §1 and the 11-α BUILD_LOG carry-forward.

Per ``docs/SYSTEM_INVARIANTS.md`` §7.3 / [D17]: the validator does NOT
write projections — it is pure-functional over manifest + AST. The
integration tests confirm this contract holds end-to-end by exercising
the aggregator across many pack shapes without touching any projection
table. Per [D6] / §8.2: no LLM-SDK imports anywhere in the validator.

AST-walk-only stance per memo 05 §3.4: the validator does NOT
``importlib.import_module`` adapter packs during validation. The
integration scenarios construct ``adapter.py`` content that exercises
the AST-walk paths (multi-capability, wrong-ABC, missing-handler, etc.)
without ever calling ``importlib.import_module``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from adminme.lib.pack_registry.parser import ManifestParseError
from adminme.lib.pack_registry.validator import (
    ValidationResult,
    validate_adapter_pack,
)


@dataclass
class _FakeRegistry:
    """Stand-in registry for tests that decouple from on-main schemas."""

    _names: list[str]

    def known_types(self) -> list[str]:
        return list(self._names)


_NOTES_CHECKLISTS_YAML: Final[str] = """\
pack: notes-checklists
runtime: bridge
family: reminders
version: 0.1.0
capabilities:
  - kind: Cat-B
    runtime: bridge
    event_families:
      - list_item.toggled
    sensitivity_default: privileged
  - kind: Cat-D
    runtime: bridge
    event_families:
      - note.added
write_capabilities:
  - toggle_checklist_item
  - reorder_checklist
sensitivity_default: sensitive
observation_mode_required: true
owner_scope_overridability_at_install: false
sharing_model_discriminator: owner_member_id
"""

# A Cat-B (write-surface-bearing) adapter whose ``_apply_write_impl``
# dispatches on ``operation.kind`` — the precedent shape from the on-main
# :class:`ExternalStateMirrorAdapter` base class. The integration test
# verifies this shape satisfies the write_capabilities check.
_NOTES_CHECKLISTS_ADAPTER_PY: Final[str] = """\
from adminme.lib.adapters import ExternalStateMirrorAdapter

class NotesChecklistsAdapter(ExternalStateMirrorAdapter):
    async def _apply_write_impl(self, operation):
        if operation.kind == "toggle_checklist_item":
            return None
        if operation.kind == "reorder_checklist":
            return None
        raise NotImplementedError(operation.kind)
"""


def _write_pack(
    tmp_path: Path,
    *,
    name: str = "synthetic_pack",
    pack_yaml: str,
    adapter_py: str,
) -> Path:
    pack_dir = tmp_path / name
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text(pack_yaml, encoding="utf-8")
    (pack_dir / "adapter.py").write_text(adapter_py, encoding="utf-8")
    return pack_dir


def _write_projection(
    projections_dir: Path,
    name: str,
    *,
    subscribes_to: list[str],
) -> None:
    pkg = projections_dir / name
    pkg.mkdir(parents=True, exist_ok=True)
    rendered = "[" + ", ".join(repr(f) for f in subscribes_to) + "]"
    (pkg / "__init__.py").write_text(
        "from __future__ import annotations\n"
        "\n"
        f"class {name.title().replace('_', '')}Projection(Projection):\n"
        f"    subscribes_to = {rendered}\n",
        encoding="utf-8",
    )


def test_multi_capability_pack_validates_cleanly_end_to_end(
    tmp_path: Path,
) -> None:
    """[PM-35] / memo 05 §2.2: a Cat-D + Cat-B Apple-Notes-checklists shape
    validates with zero errors when the manifest, the adapter source, and
    the projection tree are all correctly aligned.

    This is the canonical worked example called out in
    ``docs/05-architecture-amendment-personal-data-layer.md`` §2.2 — and
    the integration counterpart to 11-α's
    ``test_adapter_framework_contracts.py`` happy-path. The pack passes
    every one of the six checks; the result is "ready to install."
    """
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    _write_projection(
        projections_dir, "notes", subscribes_to=["note.added"]
    )
    _write_projection(
        projections_dir, "list_items", subscribes_to=["list_item.toggled"]
    )
    pack_dir = _write_pack(
        tmp_path,
        name="notes-checklists",
        pack_yaml=_NOTES_CHECKLISTS_YAML,
        adapter_py=_NOTES_CHECKLISTS_ADAPTER_PY,
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(
            _names=["note.added", "list_item.toggled"]
        ),
        projections_dir=projections_dir,
    )

    assert isinstance(result, ValidationResult)
    assert result.findings == ()
    assert not result.has_errors()


def test_existing_v1_gmail_pack_validates_without_errors() -> None:
    """Post-retrofit (prompt 11r-a — Cohort C step 1): the Gmail pack
    now inherits :class:`CommunicationAdapter` and declares a Cat-A
    ``capabilities`` block. The validator returns zero errors AND no
    ``advisory_pending_retrofit`` finding on ``base_class_conformance``
    — the first of three Cohort C advisories closed.
    """
    pack_dir = Path("packs/adapters/messaging/gmail")
    result = validate_adapter_pack(pack_dir)

    assert not result.has_errors(), [
        (f.check, f.message) for f in result.errors()
    ]
    pending = [
        f
        for f in result.advisories()
        if f.check == "base_class_conformance"
        and "advisory_pending_retrofit" in f.message
    ]
    assert pending == [], (
        f"advisory_pending_retrofit still present post-retrofit: {pending}"
    )


def test_existing_v1_plaid_pack_validates_without_errors() -> None:
    """Post-retrofit (prompt 11r-b — Cohort C step 2): the Plaid pack
    now inherits :class:`InboundDataAdapter` and declares a Cat-C
    ``capabilities`` block. The validator returns zero errors AND no
    ``advisory_pending_retrofit`` finding on ``base_class_conformance``
    — the second of three Cohort C advisories closed.
    """
    pack_dir = Path("packs/adapters/financial/plaid")
    result = validate_adapter_pack(pack_dir)

    assert not result.has_errors(), [
        (f.check, f.message) for f in result.errors()
    ]
    pending = [
        f
        for f in result.advisories()
        if f.check == "base_class_conformance"
        and "advisory_pending_retrofit" in f.message
    ]
    assert pending == [], (
        f"advisory_pending_retrofit still present post-retrofit: {pending}"
    )


def test_existing_v1_apple_reminders_pack_validates_without_errors() -> None:
    """Post-retrofit (prompt 11r-c — Cohort C step 3, closing): the
    Apple Reminders pack now inherits :class:`ExternalStateMirrorAdapter`
    and declares a Cat-B ``capabilities`` block + the asymmetric
    ``write_capabilities`` ``["create_reminder"]`` per [UT-27]. The
    validator returns zero errors AND no ``advisory_pending_retrofit``
    finding on ``base_class_conformance`` — the THIRD and FINAL of
    three Cohort C advisories closed, closing Cohort C entirely.
    """
    pack_dir = Path("packs/adapters/reminders/apple-eventkit")
    result = validate_adapter_pack(pack_dir)

    assert not result.has_errors(), [
        (f.check, f.message) for f in result.errors()
    ]
    pending = [
        f
        for f in result.advisories()
        if f.check == "base_class_conformance"
        and "advisory_pending_retrofit" in f.message
    ]
    assert pending == [], (
        f"advisory_pending_retrofit still present post-retrofit: {pending}"
    )


def test_cat_d_pack_with_wrong_runtime_yields_capability_runtime_error(
    tmp_path: Path,
) -> None:
    """memo 04 §2.2: a Cat-D capability declaring runtime!=bridge fails install.

    Cat-D is bridge-bound by the Tailscale-identity-binds-owner-scope
    property; declaring a central runtime is wrong-shape. The validator
    surfaces this as an error on
    ``capabilities[0].runtime`` so the pack author sees exactly which
    manifest field to fix.
    """
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    _write_projection(
        projections_dir, "knowledge", subscribes_to=["knowledge.added"]
    )
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: wrong_runtime\n"
            "family: knowledge\n"
            "capabilities:\n"
            "  - kind: Cat-D\n"
            "    runtime: central\n"
            "    event_families:\n"
            "      - knowledge.added\n"
            "owner_scope_overridability_at_install: false\n"
        ),
        adapter_py=(
            "from adminme.lib.adapters import PersonalKnowledgeAdapter\n"
            "\n"
            "class WrongRuntimeAdapter(PersonalKnowledgeAdapter):\n"
            "    pass\n"
        ),
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(_names=["knowledge.added"]),
        projections_dir=projections_dir,
    )

    assert result.has_errors()
    runtime_errors = [
        f
        for f in result.errors()
        if f.check == "capability_runtime_coherence"
        and f.field_path == "capabilities[0].runtime"
    ]
    assert len(runtime_errors) == 1
    assert "bridge" in runtime_errors[0].message


def test_cat_e_pack_without_observation_required_yields_error(
    tmp_path: Path,
) -> None:
    """memo 05 §2.4 / [§6.20]: Cat-E ⇒ observation_mode_required=True.

    Outbound-action adapters MUST honor observation mode; a manifest
    declaring Cat-E without observation_mode_required is wrong-shape.
    """
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: missing_obs\n"
            "family: external_action\n"
            "capabilities:\n"
            "  - kind: Cat-E\n"
            "    runtime: central\n"
            "    event_families:\n"
            "      - external_action.dispatched\n"
        ),
        adapter_py=(
            "from adminme.lib.observation import outbound\n"
            "from adminme.lib.adapters import OutboundActionAdapter\n"
            "\n"
            "class MissingObsAdapter(OutboundActionAdapter):\n"
            "    async def execute_action(self, request):\n"
            "        return await outbound(\n"
            "            session=None, action='a', payload={}, action_fn=None,\n"
            "            manager=None, event_log=None,\n"
            "        )\n"
        ),
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(_names=["external_action.dispatched"]),
        projections_dir=projections_dir,
    )

    assert result.has_errors()
    obs_errors = [
        f
        for f in result.errors()
        if f.check == "capability_runtime_coherence"
        and f.field_path == "observation_mode_required"
    ]
    assert len(obs_errors) == 1


def test_cat_b_pack_missing_write_handler_yields_write_capabilities_error(
    tmp_path: Path,
) -> None:
    """[UT-27]: a declared write_capabilities entry without a handler fails.

    The Cat-B adapter declares ``toggle_completion`` in its manifest's
    ``write_capabilities`` but the adapter class implements neither
    ``_handle_toggle_completion`` nor ``_apply_write_impl``. The
    validator surfaces an error on
    ``write_capabilities[0]`` naming the unimplemented operation.
    """
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    _write_projection(
        projections_dir, "tasks", subscribes_to=["task.created"]
    )
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: missing_handler\n"
            "family: reminders\n"
            "capabilities:\n"
            "  - kind: Cat-B\n"
            "    runtime: central\n"
            "    event_families:\n"
            "      - task.created\n"
            "write_capabilities:\n"
            "  - toggle_completion\n"
        ),
        adapter_py=(
            "from adminme.lib.adapters import ExternalStateMirrorAdapter\n"
            "\n"
            "class MissingHandlerAdapter(ExternalStateMirrorAdapter):\n"
            "    pass\n"
        ),
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(_names=["task.created"]),
        projections_dir=projections_dir,
    )

    handler_errors = [
        f
        for f in result.errors()
        if f.check == "write_capabilities"
        and f.field_path == "write_capabilities[0]"
    ]
    assert len(handler_errors) == 1
    assert "toggle_completion" in handler_errors[0].message


def test_cat_d_pack_referencing_pending_projection_yields_advisory(
    tmp_path: Path,
) -> None:
    """memo 05 §3.4: Cat-D pack referencing member_knowledge.* yields
    ``advisory_pending_projection`` (not error) per the forward-compat
    carve-out.

    11d ships ``member_knowledge`` projection; adapter packs landing
    BEFORE 11d that reference ``member_knowledge.*`` families surface
    an advisory only — the install proceeds, the supervisor flags
    the gap, and 11d closes the loop later.
    """
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: pending_proj\n"
            "family: knowledge\n"
            "capabilities:\n"
            "  - kind: Cat-D\n"
            "    runtime: bridge\n"
            "    event_families:\n"
            "      - member_knowledge.note_added\n"
            "owner_scope_overridability_at_install: false\n"
        ),
        adapter_py=(
            "from adminme.lib.adapters import PersonalKnowledgeAdapter\n"
            "\n"
            "class PendingProjAdapter(PersonalKnowledgeAdapter):\n"
            "    pass\n"
        ),
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(_names=["member_knowledge.note_added"]),
        projections_dir=projections_dir,
    )

    assert not result.has_errors()
    pending_advisories = [
        f
        for f in result.advisories()
        if f.check == "projection_subscription_compatibility"
        and "advisory_pending_projection" in f.message
    ]
    assert len(pending_advisories) == 1


def test_aggregator_collects_findings_across_multiple_checks_simultaneously(
    tmp_path: Path,
) -> None:
    """Three checks fire on a single wrong-shape pack; the aggregator
    surfaces all three findings without short-circuiting.

    The pack is intentionally cross-broken:
    - Cat-A `messaging` family + bridge runtime (capability-runtime-
      coherence error).
    - Unknown event family (event-schema-registration error).
    - Adapter class missing the `send` method (observation-mode-wrapping
      error).
    """
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: triple_broken\n"
            "family: messaging\n"
            "capabilities:\n"
            "  - kind: Cat-A\n"
            "    runtime: bridge\n"
            "    event_families:\n"
            "      - mistyped.family\n"
        ),
        adapter_py=(
            "from adminme.lib.adapters import CommunicationAdapter\n"
            "\n"
            "class TripleBrokenAdapter(CommunicationAdapter):\n"
            "    pass\n"
        ),
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(_names=["messaging.received"]),
        projections_dir=projections_dir,
    )

    checks_with_errors = {f.check for f in result.errors()}
    assert "capability_runtime_coherence" in checks_with_errors
    assert "event_schema_registration" in checks_with_errors
    assert "observation_mode_wrapping" in checks_with_errors


def test_aggregator_propagates_manifest_parse_error_on_malformed_yaml(
    tmp_path: Path,
) -> None:
    """A malformed pack.yaml is a parser concern, not a validator finding.

    The aggregator raises :class:`ManifestParseError` rather than
    silently producing findings — a manifest that cannot be parsed
    cannot be validated, so the supervisor needs to distinguish
    parser errors from validator findings.
    """
    pack_dir = tmp_path / "broken_yaml"
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text(
        "pack: broken\ncapabilities: [{:\n", encoding="utf-8"
    )
    (pack_dir / "adapter.py").write_text("class X: pass\n", encoding="utf-8")

    with pytest.raises(ManifestParseError):
        validate_adapter_pack(pack_dir)
