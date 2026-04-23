"""
Schema registry — maps (event_type, schema_version) to payload Pydantic models.

Per ADMINISTRATEME_BUILD.md §L2 "Typed event registry",
SYSTEM_INVARIANTS.md §1 invariants 3 + 9, and DECISIONS.md §D7.

Upcasters are pure functions ``upcast_v{N}_to_v{N+1}(payload: dict) -> dict``
that compose in order when reading an old event whose schema has since been
upgraded. This module defines the plumbing; prompt 04 ships no upcasters
because every initial schema is v1 (with ``skill.call.recorded`` v2 reserved
— see schemas/domain.py).

Plugin-introduced event types register via the ``hearth.event_types`` entry
point per DECISIONS.md §D9 — ``autoload()`` walks that entry-point group in
addition to ``adminme.events.schemas``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import pkgutil
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

Upcaster = Callable[[dict[str, Any]], dict[str, Any]]


class RegistryError(RuntimeError):
    """Registry-level errors: duplicate registrations, missing upcasters."""


class SchemaNotFound(RegistryError):
    """No model is registered for the requested (event_type, schema_version)."""

    def __init__(self, event_type: str, version: int) -> None:
        self.event_type = event_type
        self.version = version
        super().__init__(f"no schema for {event_type!r} v{version}")


class EventValidationError(RuntimeError):
    """Raised when an event's payload fails validation. Wraps pydantic.ValidationError."""

    def __init__(self, event_type: str, version: int, original: Exception) -> None:
        self.event_type = event_type
        self.version = version
        self.original = original
        super().__init__(
            f"payload validation failed for {event_type} v{version}: {original}"
        )


class SchemaRegistry:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, int], type[BaseModel]] = {}
        self._upcasters: dict[tuple[str, int], Upcaster] = {}
        self._autoloaded = False

    def register(
        self,
        event_type: str,
        version: int,
        model: type[BaseModel],
    ) -> None:
        key = (event_type, version)
        if key in self._by_key:
            raise RegistryError(f"duplicate registration: {event_type} v{version}")
        self._by_key[key] = model

    def register_upcaster(
        self,
        event_type: str,
        from_version: int,
        upcaster: Upcaster,
    ) -> None:
        """Register an upcaster that transforms v{from_version} payload to v{from_version+1}."""
        key = (event_type, from_version)
        if key in self._upcasters:
            raise RegistryError(
                f"duplicate upcaster: {event_type} v{from_version}->v{from_version + 1}"
            )
        self._upcasters[key] = upcaster

    def get(self, event_type: str, version: int) -> type[BaseModel] | None:
        return self._by_key.get((event_type, version))

    def known_types(self) -> list[str]:
        return sorted({t for (t, _) in self._by_key})

    def latest_version(self, event_type: str) -> int | None:
        versions = [v for (t, v) in self._by_key if t == event_type]
        return max(versions) if versions else None

    def validate(self, event_type: str, version: int, payload: dict[str, Any]) -> BaseModel:
        model = self.get(event_type, version)
        if model is None:
            raise SchemaNotFound(event_type, version)
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise EventValidationError(event_type, version, exc) from exc

    def upcast(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        from_version: int,
        to_version: int,
    ) -> dict[str, Any]:
        if to_version < from_version:
            raise RegistryError(
                f"downcasting not supported: {event_type} v{from_version}->v{to_version}"
            )
        current = payload
        for v in range(from_version, to_version):
            up = self._upcasters.get((event_type, v))
            if up is None:
                raise RegistryError(
                    f"no upcaster registered for {event_type} v{v}->v{v + 1}"
                )
            current = up(current)
        return current

    def autoload(self) -> None:
        """Import every schema module under ``adminme.events.schemas`` and
        every ``hearth.event_types`` entry point. Each module is expected to
        call ``registry.register(...)`` at import time."""
        import adminme.events.schemas as schemas_pkg

        for _, name, _ in pkgutil.walk_packages(
            schemas_pkg.__path__, prefix="adminme.events.schemas."
        ):
            importlib.import_module(name)

        eps = importlib.metadata.entry_points(group="hearth.event_types")
        for ep in eps:
            ep.load()
        self._autoloaded = True


registry = SchemaRegistry()


def ensure_autoloaded() -> None:
    """Idempotent: load bundled schemas into the module-level registry once."""
    if not registry._autoloaded:
        registry.autoload()
