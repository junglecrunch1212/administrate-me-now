"""Tests for the :mod:`adminme.lib.pack_registry.models` Pydantic models.

Covers the contract surface of :class:`Capability` and
:class:`AdapterManifest`: enum validation, ``extra="forbid"`` strict mode,
backward-compat defaults, multi-capability shape per [PM-35], asymmetric
write-capabilities per UT-27, and the
``resolve_effective_sensitivity`` helper that floors at the pack-level
default per [§6 invariant 10].
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adminme.lib.pack_registry import AdapterManifest, Capability


# ---------- AdapterManifest: minimal + maximal shape ----------


def test_minimal_adapter_manifest_defaults() -> None:
    manifest = AdapterManifest()
    assert manifest.capabilities == []
    assert manifest.write_capabilities == []
    assert manifest.sensitivity_default == "normal"
    assert manifest.observation_mode_required is False
    assert manifest.owner_scope_overridability_at_install is True
    assert manifest.sharing_model_discriminator is None
    assert manifest.dedup_strategy is None


def test_maximal_adapter_manifest_round_trip() -> None:
    payload = {
        "capabilities": [
            {
                "kind": "Cat-B",
                "runtime": "central",
                "event_families": ["reminder", "list_item"],
                "sensitivity_default": "sensitive",
                "observation_mode_required": True,
            },
        ],
        "write_capabilities": ["create_list", "toggle_completion", "delete_item"],
        "sensitivity_default": "sensitive",
        "observation_mode_required": True,
        "owner_scope_overridability_at_install": False,
        "sharing_model_discriminator": "list_owner_scope",
        "dedup_strategy": {"kind": "external_id_unique", "key_fields": ["id"]},
    }
    manifest = AdapterManifest.model_validate(payload)
    assert len(manifest.capabilities) == 1
    assert manifest.capabilities[0].kind == "Cat-B"
    assert manifest.capabilities[0].runtime == "central"
    assert manifest.write_capabilities == [
        "create_list",
        "toggle_completion",
        "delete_item",
    ]
    assert manifest.sharing_model_discriminator == "list_owner_scope"
    assert manifest.dedup_strategy == {
        "kind": "external_id_unique",
        "key_fields": ["id"],
    }


# ---------- Capability: kind / runtime enums + extra=forbid ----------


@pytest.mark.parametrize("kind", ["Cat-A", "Cat-B", "Cat-C", "Cat-D", "Cat-E"])
def test_capability_accepts_each_canonical_kind(kind: str) -> None:
    cap = Capability(kind=kind, runtime="central")  # type: ignore[arg-type]
    assert cap.kind == kind


def test_capability_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        Capability(kind="Cat-F", runtime="central")  # type: ignore[arg-type]


@pytest.mark.parametrize("runtime", ["central", "bridge", "dual", "plugin"])
def test_capability_accepts_each_canonical_runtime(runtime: str) -> None:
    cap = Capability(kind="Cat-A", runtime=runtime)  # type: ignore[arg-type]
    assert cap.runtime == runtime


def test_capability_rejects_unknown_runtime() -> None:
    with pytest.raises(ValidationError):
        Capability(kind="Cat-A", runtime="hybrid")  # type: ignore[arg-type]


def test_capability_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        Capability.model_validate(
            {"kind": "Cat-A", "runtime": "central", "mystery_field": 1}
        )


def test_adapter_manifest_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        AdapterManifest.model_validate({"capabilities": [], "unknown_extension": True})


# ---------- Multi-capability shape per [PM-35] ----------


def test_multi_capability_cat_d_plus_cat_b_per_pm_35() -> None:
    """Apple Notes-checklists shape: Cat-D notes + Cat-B checklist state."""
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "bridge",
                    "event_families": ["note"],
                },
                {
                    "kind": "Cat-B",
                    "runtime": "bridge",
                    "event_families": ["list_item"],
                },
            ],
            "write_capabilities": ["toggle_checklist_item"],
        }
    )
    assert len(manifest.capabilities) == 2
    assert {c.kind for c in manifest.capabilities} == {"Cat-D", "Cat-B"}


# ---------- Asymmetric write_capabilities per UT-27 ----------


def test_write_capabilities_empty_allowed_for_read_only_shape() -> None:
    """Cat-A / Cat-C / Cat-D adapters have no write_capabilities."""
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {"kind": "Cat-C", "runtime": "central", "event_families": ["money"]}
            ]
        }
    )
    assert manifest.write_capabilities == []


def test_write_capabilities_is_per_operation_list_not_boolean() -> None:
    """UT-27: per-operation strings, not a global on/off flag."""
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {"kind": "Cat-B", "runtime": "central", "event_families": ["list"]}
            ],
            "write_capabilities": ["create_list", "delete_item"],
        }
    )
    assert manifest.write_capabilities == ["create_list", "delete_item"]


# ---------- resolve_effective_sensitivity per [§6 invariant 10] ----------


def test_resolve_returns_pack_default_when_override_absent() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {"kind": "Cat-C", "runtime": "central", "event_families": ["money"]}
            ],
            "sensitivity_default": "privileged",
        }
    )
    assert manifest.resolve_effective_sensitivity(0) == "privileged"


def test_resolve_returns_override_when_override_is_stricter() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-C",
                    "runtime": "central",
                    "event_families": ["money"],
                    "sensitivity_default": "privileged",
                }
            ],
            "sensitivity_default": "normal",
        }
    )
    assert manifest.resolve_effective_sensitivity(0) == "privileged"


def test_resolve_clamps_to_pack_default_when_override_would_lower() -> None:
    """[§6 invariant 10]: per-capability cannot lower below pack default."""
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-C",
                    "runtime": "central",
                    "event_families": ["money"],
                    "sensitivity_default": "normal",
                }
            ],
            "sensitivity_default": "privileged",
        }
    )
    assert manifest.resolve_effective_sensitivity(0) == "privileged"


def test_resolve_index_out_of_range_raises() -> None:
    manifest = AdapterManifest()
    with pytest.raises(IndexError):
        manifest.resolve_effective_sensitivity(0)


# ---------- Backward-compat: legacy fields ignored at model level ----------


def test_capabilities_default_empty_for_legacy_v1_shape() -> None:
    """Parser feeds only extension fields; an empty payload yields []."""
    manifest = AdapterManifest.model_validate({})
    assert manifest.capabilities == []
    assert manifest.sensitivity_default == "normal"


def test_owner_scope_overridability_default_true() -> None:
    """Memo 05 §2.6: default true; Cat-D bridge adapters set false."""
    manifest = AdapterManifest()
    assert manifest.owner_scope_overridability_at_install is True


def test_sharing_model_discriminator_optional() -> None:
    """Per memo 05 §2.5: Cat-B-only field; Nones for other categories."""
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {"kind": "Cat-A", "runtime": "central", "event_families": ["msg"]}
            ]
        }
    )
    assert manifest.sharing_model_discriminator is None
