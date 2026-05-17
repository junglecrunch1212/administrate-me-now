"""Pack-internal additive retrofit tests for the Plaid Cohort C retrofit.

Closes prompt 11r-b commit 4 (pack-internal layer). Sits alongside
``test_plaid_adapter_pack_internal.py`` (the unchanged pre-retrofit
suite) and asserts the retrofit's three pack-internal contract surfaces:

1. The :class:`PlaidAdapter` ``capabilities`` ClassVar agrees with the
   manifest's ``capabilities`` block — the class-level mirror at memo 05
   §3.2 is the source the supervisor uses for runtime introspection
   without re-parsing ``pack.yaml``.
2. The pack's ``pack.yaml`` round-trips through
   :func:`parse_adapter_manifest` with ``capabilities[0].kind == "Cat-C"``
   and the four extension fields populated per memo 05 §3.1.
3. Each of the 11-β validator's six checks returns zero error findings
   against the retrofitted pack — the install gate per memo 05 §3.4.

F-3 unique-basename per the prompt: this file's basename
(``test_plaid_inbound_data_adapter_retrofit_pack_internal.py``) is
distinct from every other ``test_*_pack_internal.py`` in the
supervised build's pytest rootdir, avoiding the module-name collision
the 10c-iii heritage F-3 finding pinned.
"""

from __future__ import annotations

from pathlib import Path

from adminme.lib.pack_registry.parser import parse_adapter_manifest
from adminme.lib.pack_registry.validator import validate_adapter_pack

from packs.adapters.financial.plaid.adapter import PlaidAdapter

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_capabilities_class_attribute_matches_manifest() -> None:
    """The :class:`PlaidAdapter` ``capabilities`` ClassVar mirrors the
    manifest's ``capabilities`` block at the class layer per memo 05
    §3.2. The supervisor's runtime introspection reads the ClassVar; the
    manifest is the source of truth. Drift between the two would mean a
    pack ships with two different stated capabilities — the validator
    can't catch this (it reads the manifest, not the class), so this
    pack-internal test is the guard."""
    manifest = parse_adapter_manifest(PACK_ROOT)
    cls_caps = PlaidAdapter.capabilities

    assert isinstance(cls_caps, list)
    assert len(cls_caps) == len(manifest.capabilities) == 1
    cls_entry = cls_caps[0]
    manifest_entry = manifest.capabilities[0]
    assert cls_entry["kind"] == manifest_entry.kind == "Cat-C"
    assert cls_entry["runtime"] == manifest_entry.runtime == "central"
    assert cls_entry["event_families"] == manifest_entry.event_families == [
        "money_flow.plaid_synced"
    ]


def test_pack_yaml_round_trips_through_parse_adapter_manifest() -> None:
    """The retrofit's manifest extension fields populate the
    :class:`AdapterManifest` model per memo 05 §3.1. ``dedup_strategy``
    is declared as a mapping ``{"kind": "external_id"}`` per the on-main
    model (``dict[str, Any] | None``); ``observation_mode_required``
    stays at the default ``False`` (Cat-C has no outbound surface to
    wrap); ``write_capabilities`` stays empty per [UT-27] (Cat-C has no
    write surface). All four extension fields together flip the
    validator's ``base_class_conformance`` advisory finding off."""
    manifest = parse_adapter_manifest(PACK_ROOT)

    assert manifest.capabilities[0].kind == "Cat-C"
    assert manifest.sensitivity_default == "privileged"
    assert manifest.sharing_model_discriminator == "owner_member_id"
    assert manifest.dedup_strategy == {"kind": "external_id"}
    assert manifest.observation_mode_required is False
    assert manifest.write_capabilities == []


def test_six_validator_checks_return_zero_errors() -> None:
    """The 11-β validator runs six checks per memo 05 §3.4
    (capability_runtime_coherence, event_schema_registration,
    projection_subscription_compatibility, base_class_conformance,
    write_capabilities_check, observation_mode_wrapping). Post-retrofit,
    NONE of the six returns an ``error`` severity finding against the
    Plaid pack. ``advisory_pending_retrofit`` on ``base_class_conformance``
    — the finding this prompt closes — is also absent. Warnings are
    permitted but should also be empty for a clean Cat-C pack."""
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
