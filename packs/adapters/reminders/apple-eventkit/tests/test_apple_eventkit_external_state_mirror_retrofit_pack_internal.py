"""Pack-internal additive retrofit tests for the Reminders Cohort C retrofit.

Closes prompt 11r-c commit 4 (pack-internal layer). Sits alongside
``test_apple_eventkit_adapter_pack_internal.py`` (the unchanged
pre-retrofit suite) and asserts the retrofit's three pack-internal
contract surfaces:

1. The :class:`AppleEventKitAdapter` ``capabilities`` ClassVar agrees
   with the manifest's ``capabilities`` block — the class-level mirror
   at memo 05 §3.2 is the source the supervisor uses for runtime
   introspection without re-parsing ``pack.yaml``. Drift between the
   two would mean the pack ships with two different stated
   capabilities (the validator only reads the manifest, not the class,
   so this pack-internal test is the only guard).
2. The pack's ``pack.yaml`` round-trips through
   :func:`parse_adapter_manifest` with ``capabilities[0].kind ==
   "Cat-B"``, the asymmetric ``write_capabilities == ["create_reminder"]``
   per [UT-27], and the other three extension fields populated per memo
   05 §3.1.
3. Each of the 11-β validator's six checks returns zero error findings
   against the retrofitted pack — the install gate per memo 05 §3.4 —
   AND the ``base_class_conformance`` advisory the retrofit closes is
   absent. This is the third and FINAL ``advisory_pending_retrofit``
   on a shipped v1 pack; once this assertion holds, Cohort C is closed.

F-3 unique-basename per the prompt: this file's basename
(``test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py``)
is distinct from every other ``test_*_pack_internal.py`` in the
supervised build's pytest rootdir, avoiding the module-name collision
the 10c-iii heritage F-3 finding pinned (reinforced by 11r-a / 11r-b).

``packs/adapters/reminders/apple-eventkit`` includes a hyphen in the
final directory segment and is therefore not a valid Python package
name; the adapter is loaded via the file-path importlib pattern shared
with ``test_apple_eventkit_adapter_pack_internal._load_adapter_module``
and the framework retrofit tests at
``tests/unit/adapters/reminders/test_apple_eventkit_retrofit.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from adminme.lib.pack_registry.parser import parse_adapter_manifest
from adminme.lib.pack_registry.validator import validate_adapter_pack

PACK_ROOT = Path(__file__).resolve().parents[1]


def _load_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root via importlib.

    Same file-path import pattern the supervisor's loader uses. Packs
    are not a top-level Python package under poetry (only ``adminme``
    is), and the ``apple-eventkit`` directory segment's hyphen prevents
    a ``from packs.adapters.reminders.apple-eventkit.adapter import``
    statement entirely."""
    spec = importlib.util.spec_from_file_location(
        "apple_eventkit_adapter_retrofit_pack_internal_under_test",
        PACK_ROOT / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_adapter_module()
AppleEventKitAdapter: Any = _adapter_mod.AppleEventKitAdapter


def test_capabilities_class_attribute_matches_manifest() -> None:
    """The :class:`AppleEventKitAdapter` ``capabilities`` ClassVar
    mirrors the manifest's ``capabilities`` block at the class layer
    per memo 05 §3.2. The supervisor's runtime introspection reads the
    ClassVar; the manifest is the source of truth. Drift between the
    two would mean a pack ships with two different stated capabilities
    — the validator can't catch this (it reads the manifest, not the
    class), so this pack-internal test is the guard."""
    manifest = parse_adapter_manifest(PACK_ROOT)
    cls_caps = AppleEventKitAdapter.capabilities

    assert isinstance(cls_caps, list)
    assert len(cls_caps) == len(manifest.capabilities) == 1
    cls_entry = cls_caps[0]
    manifest_entry = manifest.capabilities[0]
    assert cls_entry["kind"] == manifest_entry.kind == "Cat-B"
    assert cls_entry["runtime"] == manifest_entry.runtime == "bridge"
    assert (
        cls_entry["event_families"]
        == manifest_entry.event_families
        == [
            "reminder.list_item.synced",
            "list.created",
            "list.updated",
            "list.deleted",
            "list_item.added",
            "list_item.updated",
            "list_item.completed",
            "list_item.uncompleted",
            "list_item.deleted",
        ]
    )


def test_pack_yaml_round_trips_through_parse_adapter_manifest_with_write_capabilities() -> (
    None
):
    """The retrofit's manifest extension fields populate the
    :class:`AdapterManifest` model per memo 05 §3.1.
    ``write_capabilities`` is the asymmetric Cat-B / Cat-E surface per
    [UT-27]; declaring ONLY ``create_reminder`` matches what the
    adapter actually implements today. ``dedup_strategy`` is a mapping
    ``{"kind": "external_id"}`` per the on-main ``AdapterManifest``
    model (``dict[str, Any] | None``). ``observation_mode_required``
    stays at the default ``False`` because the per-write observation
    wrap is implemented in :meth:`_apply_write_impl` directly per
    [§6.20], not gated through the manifest flag (the
    ``observation_mode_wrapping`` validator check passes implicitly
    because every Cat-B with a write capability is wrapped). All five
    extension assertions together flip the validator's
    ``base_class_conformance`` advisory finding off."""
    manifest = parse_adapter_manifest(PACK_ROOT)

    assert manifest.capabilities[0].kind == "Cat-B"
    assert manifest.sensitivity_default == "normal"
    assert manifest.sharing_model_discriminator == "owner_member_id"
    assert manifest.dedup_strategy == {"kind": "calendar_item_identifier_last_modified"}
    assert manifest.observation_mode_required is False
    assert manifest.write_capabilities == [
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "complete_reminder",
        "uncomplete_reminder",
        "move_reminder",
        "create_list",
        "update_list",
        "delete_list",
    ]


def test_six_validator_checks_return_zero_errors_and_no_advisory_pending_retrofit() -> (
    None
):
    """The 11-β validator runs six checks per memo 05 §3.4
    (capability_runtime_coherence, event_schema_registration,
    projection_subscription_compatibility, base_class_conformance,
    write_capabilities_check, observation_mode_wrapping). Post-retrofit,
    NONE of the six returns an ``error`` severity finding against the
    Reminders pack. ``advisory_pending_retrofit`` on
    ``base_class_conformance`` — the THIRD and FINAL of three such
    advisories on shipped v1 packs, the finding this prompt closes — is
    also absent. Once this assertion holds, Cohort C is closed."""
    result = validate_adapter_pack(PACK_ROOT)

    assert not result.has_errors(), (
        f"validator reports errors post-retrofit: {result.errors()}"
    )
    pending = [
        f
        for f in result.advisories()
        if f.check == "base_class_conformance"
        and "advisory_pending_retrofit" in f.message
    ]
    assert pending == [], (
        f"advisory_pending_retrofit still present post-retrofit: {pending}"
    )
