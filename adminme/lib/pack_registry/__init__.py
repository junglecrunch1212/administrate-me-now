"""
Adapter pack registry — manifest format extensions for Phase A framework.

Sibling to :mod:`adminme.lib.skill_runner` (which loads skill packs).
Where the skill-runner package consumes the entire ``pack.yaml`` to
build a runnable skill, this package consumes only the **extension**
fields added by the adapter framework per memo 05 §3.1 and prompt 11-α
§7: ``capabilities``, ``write_capabilities``, ``sensitivity_default``,
``observation_mode_required``, ``owner_scope_overridability_at_install``,
``sharing_model_discriminator``, ``dedup_strategy``.

The schema lives in :mod:`adminme.lib.pack_registry.models`; the parser
lives in :mod:`adminme.lib.pack_registry.parser`.

The install-time validator that ENFORCES these extensions
(capability-runtime coherence, write-capabilities presence, Cat-D
runtime locked to ``bridge``, etc.) ships in prompt 11-β; the
``adminme adapters`` CLI that wraps the validator ships in 11-γ. This
module is the contract layer the validator builds on.
"""

from __future__ import annotations

from adminme.lib.pack_registry.models import (
    AdapterManifest,
    Capability,
    CapabilityKind,
    CapabilityRuntime,
    SensitivityLevel,
)
from adminme.lib.pack_registry.parser import (
    ManifestParseError,
    parse_adapter_manifest,
)

__all__ = [
    "AdapterManifest",
    "Capability",
    "CapabilityKind",
    "CapabilityRuntime",
    "ManifestParseError",
    "SensitivityLevel",
    "parse_adapter_manifest",
]
