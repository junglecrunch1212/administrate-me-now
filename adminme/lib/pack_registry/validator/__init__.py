"""Install-time adapter-pack validator.

Public entry point: :func:`validate_adapter_pack`. Per memo 05 §3.4
and [PM-34], the validator runs at install time — BEFORE the
supervisor instantiates the adapter — so wrong-shape pack manifests
fail fast with field-named diagnostics, not opaquely at runtime when
an adapter tries to call a non-existent method.

This module is the **aggregator**. The six per-check modules
implement the rules:

- :mod:`~.capability_runtime_coherence` (commit 1) — Cat-D⇒bridge,
  Cat-D⇒owner-scope-locked, Cat-E⇒observation-required, Cat-A
  messaging⇒central/plugin.
- :mod:`~.event_schema_registration` (commit 1) — every declared
  ``event_families`` entry resolves to a registered Pydantic schema.
- :mod:`~.projection_subscription_compatibility` (commit 2) — Cat-B
  / Cat-D capabilities feed at least one projection subscription,
  with ``advisory_pending_projection`` for ``member_knowledge`` and
  ``lists`` (projections shipping in later prompts).
- :mod:`~.base_class_conformance` (commit 3) — adapter class
  inherits from exactly one of the 5 ABCs, matching the manifest's
  primary capability category.
- :mod:`~.write_capabilities_check` (commit 3) — every
  ``write_capabilities`` entry has a corresponding
  ``_handle_<op>`` or ``_apply_write_impl`` method on the subclass.
- :mod:`~.observation_mode_wrapping` (commit 3) — Cat-A ``send`` and
  Cat-E ``execute_action`` route through
  :mod:`adminme.lib.observation`.

Commit-2 state: the aggregator currently wires the first three
checks; subsequent commits wire the remaining three without changing
the public signature. Test cases that exercise the not-yet-wired
checks live in their respective commits.

AST-walk-only stance per §6 (out of scope): the validator does NOT
``importlib.import_module`` adapter packs during validation.
Side-effecting adapter modules would otherwise fire on every install
attempt; the base-class conformance and observation-mode wrapping
checks AST-walk ``adapter.py`` content instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from adminme.lib.pack_registry.parser import (
    ManifestParseError,
    parse_adapter_manifest,
)
from adminme.lib.pack_registry.validator.capability_runtime_coherence import (
    check_capability_runtime_coherence,
)
from adminme.lib.pack_registry.validator.event_schema_registration import (
    check_event_schema_registration,
)
from adminme.lib.pack_registry.validator.projection_subscription_compatibility import (
    check_projection_subscription_compatibility,
)
from adminme.lib.pack_registry.validator.result import (
    Severity,
    ValidationFinding,
    ValidationResult,
)


def _read_pack_family(pack_dir: Path) -> str | None:
    """Best-effort read of the ``family:`` top-level key from ``pack.yaml``.

    Used by the capability-runtime-coherence check's messaging
    sub-rule. ``family:`` is NOT a manifest-extension field
    (:class:`adminme.lib.pack_registry.models.AdapterManifest` does
    not carry it); we re-read the yaml here rather than threading
    family through the parser API. Returns ``None`` if the yaml is
    malformed, missing the key, or the key is non-string.

    Errors in the yaml itself surface via
    :func:`parse_adapter_manifest` ahead of this function, so this
    helper does NOT need to raise: by the time we are reading family,
    the parser has already validated the file.
    """
    pack_yaml = pack_dir / "pack.yaml"
    if not pack_yaml.exists():
        return None
    try:
        with pack_yaml.open(encoding="utf-8") as f:
            data: Any = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    family = data.get("family")
    return family if isinstance(family, str) else None


def validate_adapter_pack(
    pack_dir: Path,
    *,
    registry: Any | None = None,
    projections_dir: Path | None = None,
) -> ValidationResult:
    """Validate an adapter pack at ``pack_dir`` and aggregate findings.

    The aggregator does NOT short-circuit: every check runs and every
    finding is preserved. Callers inspect
    :meth:`ValidationResult.has_errors` to refuse install, and
    :meth:`ValidationResult.warnings` / :meth:`.advisories` to surface
    non-blocking signals.

    The ``registry`` parameter is forwarded to the event-schema
    registration check; tests inject a fake registry to avoid loading
    the on-main schemas. The ``projections_dir`` parameter is forwarded
    to the projection-subscription-compatibility check; tests inject a
    ``tmp_path`` containing stub projection ``__init__.py`` files to
    avoid coupling to the full on-main projection tree.

    Raises :exc:`adminme.lib.pack_registry.parser.ManifestParseError`
    when ``pack.yaml`` is missing or malformed — those are not
    validator findings; they are parser errors and a malformed
    manifest cannot be validated.
    """
    pack_dir = Path(pack_dir)
    manifest = parse_adapter_manifest(pack_dir)
    pack_family = _read_pack_family(pack_dir)

    findings: list[ValidationFinding] = []
    findings.extend(
        check_capability_runtime_coherence(
            manifest, pack_family=pack_family
        )
    )
    findings.extend(
        check_event_schema_registration(manifest, registry=registry)
    )
    findings.extend(
        check_projection_subscription_compatibility(
            manifest, projections_dir=projections_dir
        )
    )

    return ValidationResult(pack_dir=pack_dir, findings=tuple(findings))


__all__ = [
    "ManifestParseError",
    "Severity",
    "ValidationFinding",
    "ValidationResult",
    "check_capability_runtime_coherence",
    "check_event_schema_registration",
    "check_projection_subscription_compatibility",
    "validate_adapter_pack",
]
