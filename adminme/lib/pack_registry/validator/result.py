"""Result types for the install-time adapter-pack validator.

Each of the six per-check modules (capability-runtime coherence,
event-schema registration, projection-subscription compatibility,
base-class conformance, write_capabilities method-presence,
observation-mode wrapping) returns a list of
:class:`ValidationFinding` entries. The
:func:`adminme.lib.pack_registry.validator.validate_adapter_pack`
aggregator collects findings from every check (without short-circuiting)
and packages them into a :class:`ValidationResult`.

Severity ladder per memo 05 §3.4:

- ``error`` — refuses install. Manifest is wrong-shape and the pack
  cannot be loaded as-is.
- ``warning`` — surfaces a concern but install proceeds. Future
  versions of the validator may promote these to errors.
- ``advisory`` — informational. Used today for two cases:
  ``advisory_pending_projection`` (the projection a Cat-D capability
  feeds ships in a later prompt — e.g. ``member_knowledge``, ``lists``)
  and ``advisory_pending_retrofit`` (an existing v1 pack predates the
  framework's ABCs; Cohort C retrofits will close the gap).

Both dataclasses are ``frozen=True`` so findings are
hashable/copyable and the aggregated result is safe to share across
callers (the supervisor and the ``adminme adapters validate`` CLI both
consume the same result).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Severity = Literal["error", "warning", "advisory"]


@dataclass(frozen=True)
class ValidationFinding:
    """One observation from one of the six validator checks.

    ``field_path`` is the dotted/indexed path into the manifest that
    surfaced the failing rule, e.g.
    ``capabilities[0].kind`` for the Cat-D-must-be-bridge rule, or
    ``owner_scope_overridability_at_install`` for the Cat-D
    owner-scope-locked rule. ``None`` is reserved for findings that
    reference an adapter source file rather than a manifest field
    (e.g. base-class conformance via AST walk).
    """

    severity: Severity
    check: str
    message: str
    field_path: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    """Aggregated findings from a single ``validate_adapter_pack`` call."""

    pack_dir: Path
    findings: tuple[ValidationFinding, ...] = field(default_factory=tuple)

    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    def errors(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == "error"]

    def warnings(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    def advisories(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == "advisory"]


__all__ = ["Severity", "ValidationFinding", "ValidationResult"]
