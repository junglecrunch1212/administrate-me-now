"""Tests for :mod:`~.event_schema_registration`.

Uses a stand-in registry exposing ``known_types()`` to avoid relying
on the on-main schema modules' side-effects (which would couple the
unit test to the entire ``adminme/events/schemas/`` tree). One test
also exercises the alternate ``registered_event_families()`` shape
the prompt's abstract description called out — both names must work.
"""

from __future__ import annotations

from dataclasses import dataclass

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.event_schema_registration import (
    check_event_schema_registration,
)


@dataclass
class _FakeRegistry:
    _names: list[str]

    def known_types(self) -> list[str]:
        return list(self._names)


@dataclass
class _FakeRegistryAlt:
    _names: list[str]

    def registered_event_families(self) -> list[str]:
        return list(self._names)


def _registry(names: list[str]) -> _FakeRegistry:
    return _FakeRegistry(_names=names)


def test_happy_path_all_declared_families_are_registered() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": [
                        "messaging.received",
                        "messaging.sent",
                    ],
                }
            ]
        }
    )
    registry = _registry(
        ["messaging.received", "messaging.sent", "calendar.event_added"]
    )
    assert (
        check_event_schema_registration(manifest, registry=registry)
        == []
    )


def test_unknown_family_yields_error_with_correct_field_path() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received", "msg.received"],
                }
            ]
        }
    )
    registry = _registry(
        ["messaging.received", "messaging.sent", "messaging.classified"]
    )
    findings = check_event_schema_registration(
        manifest, registry=registry
    )
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].check == "event_schema_registration"
    assert (
        findings[0].field_path
        == "capabilities[0].event_families[1]"
    )
    assert "msg.received" in findings[0].message


def test_unknown_family_message_includes_three_suggestions_by_prefix() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.classifie"],
                }
            ]
        }
    )
    registry = _registry(
        [
            "messaging.received",
            "messaging.sent",
            "messaging.classified",
            "calendar.event_added",
            "artifact.received",
        ]
    )
    findings = check_event_schema_registration(
        manifest, registry=registry
    )
    assert len(findings) == 1
    message = findings[0].message
    # All three messaging-prefixed candidates should appear before
    # non-messaging ones; suggestion list is exactly three names.
    assert "messaging.classified" in message
    assert "messaging.received" in message
    assert "messaging.sent" in message


def test_empty_event_families_in_capability_skips_check() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": [],
                }
            ]
        }
    )
    registry = _registry(["messaging.received"])
    assert (
        check_event_schema_registration(manifest, registry=registry)
        == []
    )


def test_accepts_alternate_registered_event_families_method_name() -> None:
    """The prompt's abstract surface used ``registered_event_families``;
    the on-main surface uses ``known_types``. The check normalizes both.
    """
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
    registry = _FakeRegistryAlt(_names=["messaging.received"])
    assert (
        check_event_schema_registration(manifest, registry=registry)
        == []
    )


def test_default_registry_loads_on_main_schemas_and_passes_for_known_family() -> None:
    """Smoke test: with no injected registry, the check uses the
    module-level :data:`adminme.events.registry.registry` and the
    autoload path. A pack declaring a known on-main family (e.g.
    ``messaging.received`` from ``adminme.events.schemas.ingest``)
    should produce zero findings.
    """
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
    assert check_event_schema_registration(manifest) == []
