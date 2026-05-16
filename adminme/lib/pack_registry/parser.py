"""Adapter pack manifest parser.

Mirrors the loader pattern from
``adminme/lib/skill_runner/pack_loader.py`` (yaml.safe_load + Pydantic
validation) but is structurally separate: adapter packs are not skill
packs, and adapter packs carry capability declarations that skill packs
do not.

The parser is intentionally narrow: it reads ``pack.yaml``, extracts the
recognized **extension** fields (see :mod:`adminme.lib.pack_registry.models`),
and yields an :class:`AdapterManifest`. Existing top-level fields
(``pack:``, ``runtime:``, ``family:``, ``triggers:``, ``depends_on:``,
``events_emitted:``, ``config:``, ``sensitivity_floor:``, etc.) are
left to the existing loader / supervisor — this module is the
forward-compat seam for the framework's new fields only.

The enforcement of these extensions (e.g. "Cat-D adapter must declare
``runtime: bridge``", "write_capabilities required when any capability
is Cat-B / Cat-E", "owner_scope_overridability_at_install must be false
for Cat-D") lands in 11-β's install-time validator. This parser only
PARSES; it does not VALIDATE the cross-field semantics.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from adminme.lib.pack_registry.models import AdapterManifest

# Top-level keys the parser forwards to the AdapterManifest model.
# Everything else in pack.yaml (pack:, runtime:, triggers:, etc.) is
# ignored here — those belong to the existing pack loader / supervisor.
_EXTENSION_FIELDS: tuple[str, ...] = (
    "capabilities",
    "write_capabilities",
    "sensitivity_default",
    "observation_mode_required",
    "owner_scope_overridability_at_install",
    "sharing_model_discriminator",
    "dedup_strategy",
)


class ManifestParseError(RuntimeError):
    """Raised when an adapter pack manifest cannot be parsed.

    Distinct from ``adminme.lib.skill_runner.pack_loader.PackLoadError``
    so that adapter-pack and skill-pack failure modes don't conflate.
    """


def parse_adapter_manifest(pack_dir: Path) -> AdapterManifest:
    """Parse adapter manifest extensions from ``<pack_dir>/pack.yaml``.

    Returns an :class:`AdapterManifest` populated from the extension
    fields in the yaml. Legacy v1 manifests (no ``capabilities:`` block)
    yield an ``AdapterManifest`` with ``capabilities=[]`` and all other
    fields at their defaults.

    Raises :class:`ManifestParseError` when:

    - ``<pack_dir>/pack.yaml`` is missing.
    - The yaml is malformed.
    - The yaml does not parse to a mapping at the top level.
    - The extension fields fail Pydantic validation (e.g. unknown
      capability ``kind``, unknown ``runtime``, extra unrecognized
      field inside a ``capabilities[]`` block).
    """
    pack_yaml = Path(pack_dir) / "pack.yaml"
    if not pack_yaml.exists():
        raise ManifestParseError(f"pack.yaml not found at {pack_yaml}")

    try:
        with pack_yaml.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestParseError(
            f"failed to read pack.yaml at {pack_yaml}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ManifestParseError(
            f"pack.yaml at {pack_yaml} did not parse to a mapping "
            f"(got {type(data).__name__})"
        )

    extension_payload: dict[str, object] = {
        key: data[key] for key in _EXTENSION_FIELDS if key in data
    }

    try:
        return AdapterManifest.model_validate(extension_payload)
    except ValidationError as exc:
        raise ManifestParseError(
            f"adapter manifest at {pack_yaml} failed validation: {exc}"
        ) from exc


__all__ = ["ManifestParseError", "parse_adapter_manifest"]
