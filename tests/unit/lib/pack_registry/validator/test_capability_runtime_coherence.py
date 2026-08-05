"""Tests for :mod:`~.capability_runtime_coherence`.

Covers each of the four rules (Cat-D⇒bridge, Cat-D⇒owner-scope-locked,
Cat-E⇒observation-required, Cat-A messaging⇒central/plugin) with a
happy path and at least one violation, plus the multi-rule
non-short-circuit shape per memo 05 §3.4.
"""

from __future__ import annotations

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.capability_runtime_coherence import (
    check_capability_runtime_coherence,
)


def test_happy_path_cat_d_bridge_with_owner_scope_locked() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "bridge",
                    "event_families": ["note"],
                }
            ],
            "owner_scope_overridability_at_install": False,
        }
    )
    findings = check_capability_runtime_coherence(manifest)
    assert findings == []


def test_cat_d_with_non_bridge_runtime_yields_error_finding() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "central",
                    "event_families": ["note"],
                }
            ],
            "owner_scope_overridability_at_install": False,
        }
    )
    findings = check_capability_runtime_coherence(manifest)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].check == "capability_runtime_coherence"
    assert findings[0].field_path == "capabilities[0].runtime"
    assert "Cat-D" in findings[0].message
    assert "bridge" in findings[0].message


def test_cat_d_with_owner_scope_overridability_true_yields_error_finding() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "bridge",
                    "event_families": ["note"],
                }
            ],
            # default is True; explicit here for legibility
            "owner_scope_overridability_at_install": True,
        }
    )
    findings = check_capability_runtime_coherence(manifest)
    field_paths = {f.field_path for f in findings}
    assert "owner_scope_overridability_at_install" in field_paths
    matching = [
        f for f in findings if f.field_path == "owner_scope_overridability_at_install"
    ]
    assert matching[0].severity == "error"
    assert matching[0].check == "capability_runtime_coherence"


def test_cat_e_without_observation_mode_required_yields_error_finding() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-E",
                    "runtime": "central",
                    "event_families": ["external_action"],
                }
            ],
            "observation_mode_required": False,
        }
    )
    findings = check_capability_runtime_coherence(manifest)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].field_path == "observation_mode_required"
    assert "Cat-E" in findings[0].message
    assert "observation_mode_required" in findings[0].message


def test_cat_a_messaging_with_bridge_runtime_yields_error() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "bridge",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    findings = check_capability_runtime_coherence(manifest, pack_family="messaging")
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].field_path == "capabilities[0].runtime"
    assert "messaging" in findings[0].message


def test_cat_a_messaging_with_central_runtime_is_happy() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    findings = check_capability_runtime_coherence(manifest, pack_family="messaging")
    assert findings == []


def test_multiple_violations_surface_without_short_circuit() -> None:
    """memo 05 §3.4: every violation is a finding; no short-circuit."""
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "central",  # violates Cat-D⇒bridge
                    "event_families": ["note"],
                },
                {
                    "kind": "Cat-E",
                    "runtime": "central",
                    "event_families": ["external_action"],
                },
            ],
            # violates Cat-D⇒owner-scope-locked (default True)
            # violates Cat-E⇒observation_mode_required (default False)
        }
    )
    findings = check_capability_runtime_coherence(manifest)
    field_paths = {f.field_path for f in findings}
    assert "capabilities[0].runtime" in field_paths
    assert "owner_scope_overridability_at_install" in field_paths
    assert "observation_mode_required" in field_paths
    # All three rules surface
    assert len(findings) >= 3
    assert all(f.severity == "error" for f in findings)
