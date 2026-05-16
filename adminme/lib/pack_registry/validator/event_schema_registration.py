"""Event-schema registration check (2 of 6).

Walks every capability's declared ``event_families`` and asserts each
one resolves to a Pydantic schema registered with
:data:`adminme.events.registry.registry` at import time. Adapter packs
that declare emission of an unregistered event family fail validation
with an ``error`` finding that names the family + the field path +
the three registered families with the longest prefix overlap (as
suggestions) so authors can fix typos without grepping the registry
by hand.

The on-main registry surface (per
``adminme/events/registry.py``) exposes registered names via
:meth:`SchemaRegistry.known_types`. The check ensures the registry is
autoloaded (via :func:`ensure_autoloaded`) before querying it; tests
pass a stand-in registry directly to bypass autoload.

The injected ``registry`` parameter is typed structurally — anything
exposing a ``known_types() -> list[str]`` method (or
``registered_event_families() -> list[str]`` as the prompt phrased
the abstract surface) satisfies the type. The check normalizes both
shapes so test fakes can adopt either name without coupling to the
on-main symbol.

Rule: every entry in every capability's ``event_families`` MUST appear
in the registry. Empty ``event_families`` skip the check for that
capability (a capability with no declared families has no emissions
to verify — the parser still requires the manifest to declare the
``event_families`` key, but the list itself may be empty for adapters
whose capabilities are read-only with no emitted family).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.result import ValidationFinding

_CHECK_NAME = "event_schema_registration"
_MAX_SUGGESTIONS = 3


@runtime_checkable
class _RegistryLike(Protocol):
    """Structural type for an event-schema registry.

    The default registry (``adminme.events.registry.registry``)
    satisfies this via :meth:`SchemaRegistry.known_types`. Tests may
    pass a fake exposing either ``known_types`` or
    ``registered_event_families`` — the check accepts either name to
    decouple the validator from a future rename.
    """

    def known_types(self) -> list[str]: ...


def _collect_registered(registry: object) -> list[str]:
    if hasattr(registry, "known_types"):
        return list(registry.known_types())  # type: ignore[no-any-return]
    if hasattr(registry, "registered_event_families"):
        return list(registry.registered_event_families())  # type: ignore[attr-defined]
    raise TypeError(
        "registry object must expose either known_types() or "
        "registered_event_families() returning list[str]"
    )


def _prefix_overlap_len(needle: str, candidate: str) -> int:
    i = 0
    while i < len(needle) and i < len(candidate) and needle[i] == candidate[i]:
        i += 1
    return i


def _suggestions_for(needle: str, registered: list[str]) -> list[str]:
    """Top-N registered names by prefix overlap with ``needle``.

    Stable order: prefix-overlap-length DESCENDING, name ASCENDING for
    ties. Names with no overlap (length 0) still surface (just at the
    end of the sort), so the suggestion list always has up to
    ``_MAX_SUGGESTIONS`` entries even when the unknown family shares
    no prefix with any registered family.
    """
    scored = sorted(
        registered,
        key=lambda c: (-_prefix_overlap_len(needle, c), c),
    )
    return scored[:_MAX_SUGGESTIONS]


def check_event_schema_registration(
    manifest: AdapterManifest,
    *,
    registry: _RegistryLike | object | None = None,
) -> list[ValidationFinding]:
    """Run the event-schema registration rule against ``manifest``.

    Default: queries the module-level
    ``adminme.events.registry.registry`` (autoloaded on demand).
    Tests pass a fake registry via the ``registry`` keyword to avoid
    importing the schema modules' side effects.
    """
    if registry is None:
        from adminme.events.registry import (
            ensure_autoloaded,
            registry as default_registry,
        )

        ensure_autoloaded()
        registry = default_registry

    registered = _collect_registered(registry)

    findings: list[ValidationFinding] = []
    for cap_index, cap in enumerate(manifest.capabilities):
        for family_index, family in enumerate(cap.event_families):
            if family in registered:
                continue
            suggestions = _suggestions_for(family, registered)
            findings.append(
                ValidationFinding(
                    severity="error",
                    check=_CHECK_NAME,
                    message=(
                        f"event family {family!r} declared in "
                        f"capabilities[{cap_index}].event_families"
                        f"[{family_index}] is not registered with "
                        f"adminme.events.registry; "
                        f"closest registered: {suggestions}."
                    ),
                    field_path=(
                        f"capabilities[{cap_index}]."
                        f"event_families[{family_index}]"
                    ),
                )
            )

    return findings


__all__ = ["check_event_schema_registration"]
