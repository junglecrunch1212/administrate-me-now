"""Unit tests for adminme.events.registry.SchemaRegistry (prompt 04)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field

from adminme.events.registry import (
    EventValidationError,
    RegistryError,
    SchemaNotFound,
    SchemaRegistry,
    registry as module_registry,
)


class _PayloadV1(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1)
    count: int = Field(ge=0)


class _PayloadV2(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1)
    count: int = Field(ge=0)
    label: str


def _fresh() -> SchemaRegistry:
    return SchemaRegistry()


def test_register_and_get_roundtrip() -> None:
    r = _fresh()
    r.register("x.created", 1, _PayloadV1)
    assert r.get("x.created", 1) is _PayloadV1
    assert r.get("x.created", 2) is None
    assert r.known_types() == ["x.created"]


def test_duplicate_registration_raises() -> None:
    r = _fresh()
    r.register("x.created", 1, _PayloadV1)
    with pytest.raises(RegistryError, match="duplicate registration"):
        r.register("x.created", 1, _PayloadV1)


def test_latest_version_returns_max() -> None:
    r = _fresh()
    r.register("x.created", 1, _PayloadV1)
    r.register("x.created", 2, _PayloadV2)
    assert r.latest_version("x.created") == 2
    assert r.latest_version("never.registered") is None


def test_validate_returns_model_on_valid_payload() -> None:
    r = _fresh()
    r.register("x.created", 1, _PayloadV1)
    model = r.validate("x.created", 1, {"name": "alice", "count": 7})
    assert isinstance(model, _PayloadV1)
    assert model.name == "alice"
    assert model.count == 7


def test_validate_raises_event_validation_error_on_invalid_payload() -> None:
    r = _fresh()
    r.register("x.created", 1, _PayloadV1)
    with pytest.raises(EventValidationError) as exc_info:
        r.validate("x.created", 1, {"name": "", "count": -1})
    assert exc_info.value.event_type == "x.created"
    assert exc_info.value.version == 1
    assert exc_info.value.original is not None


def test_validate_raises_schema_not_found_for_unregistered() -> None:
    r = _fresh()
    with pytest.raises(SchemaNotFound) as exc_info:
        r.validate("never.registered", 1, {})
    assert exc_info.value.event_type == "never.registered"
    assert exc_info.value.version == 1


def test_upcaster_composes_across_versions() -> None:
    r = _fresh()
    r.register("x.created", 1, _PayloadV1)
    r.register("x.created", 2, _PayloadV2)
    r.register("x.created", 3, _PayloadV2)  # v3 uses same shape as v2 for this test

    def up_1_to_2(payload: dict[str, Any]) -> dict[str, Any]:
        return {**payload, "label": "backfilled"}

    def up_2_to_3(payload: dict[str, Any]) -> dict[str, Any]:
        return {**payload, "label": payload["label"].upper()}

    r.register_upcaster("x.created", 1, up_1_to_2)
    r.register_upcaster("x.created", 2, up_2_to_3)

    out = r.upcast(
        "x.created",
        {"name": "alice", "count": 3},
        from_version=1,
        to_version=3,
    )
    assert out == {"name": "alice", "count": 3, "label": "BACKFILLED"}


def test_upcast_without_registered_upcaster_raises() -> None:
    r = _fresh()
    r.register("x.created", 1, _PayloadV1)
    r.register("x.created", 2, _PayloadV2)
    with pytest.raises(RegistryError, match="no upcaster registered"):
        r.upcast("x.created", {"name": "a", "count": 0}, from_version=1, to_version=2)


def test_duplicate_upcaster_raises() -> None:
    r = _fresh()

    def up(payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    r.register_upcaster("x.created", 1, up)
    with pytest.raises(RegistryError, match="duplicate upcaster"):
        r.register_upcaster("x.created", 1, up)


def test_upcast_downcast_rejected() -> None:
    r = _fresh()
    with pytest.raises(RegistryError, match="downcasting not supported"):
        r.upcast("x.created", {}, from_version=3, to_version=1)


def test_autoload_is_idempotent() -> None:
    # autoload() walks the schemas package; this test confirms it runs cleanly
    # and is safe to call twice. The stronger "15 bundled schemas" check lives
    # in test_event_validation.py where the schemas are exercised end-to-end.
    from adminme.events.registry import ensure_autoloaded

    ensure_autoloaded()
    before = dict(module_registry._by_key)
    ensure_autoloaded()
    after = dict(module_registry._by_key)
    assert before == after, "autoload duplicated registrations on second call"
