"""Pydantic models for adapter pack manifest extensions.

Per :doc:`docs/05-architecture-amendment-personal-data-layer.md` §3.1 and
prompt 11-α §7, adapter pack ``pack.yaml`` files are extended with a small
set of new fields that declare per-capability epistemic-role metadata
([D19], [PM-34], [PM-35], UT-26, UT-27).

This module ships the **schema** for those extensions; the install-time
validator that *enforces* them ships in 11-β. Existing v1 ``pack.yaml``
files (Gmail, Plaid, Reminders shipped before the framework) continue to
parse cleanly via the parser layer: the new fields are all optional and
default to a "single-capability inferred from family" shape.

The two public models:

- :class:`Capability` — one capability block in the manifest's
  ``capabilities:`` list. Multi-capability adapters declare multiple
  entries per [PM-35] (e.g. Apple Notes-checklists is Cat-D + Cat-B;
  Home Assistant is Cat-C + Cat-E).
- :class:`AdapterManifest` — the pack-level extension fields. The
  parser yields one of these per ``pack.yaml``.

Both models pin ``extra="forbid"``: unknown fields are a hard error.
Backward-compatibility for legacy ``pack.yaml`` files is achieved by the
**parser** (``parser.py``) extracting only the recognized extension
fields and forwarding them to the model, not by relaxing the model.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Three-level sensitivity ordering per [§6 invariant 10]. Note this is a
# strictly richer ladder than ``adminme.adapters.base.SensitivityFloor``
# (normal | privileged): the framework introduces ``sensitive`` as the
# middle rung used by Cat-A messaging adapters per memo 05 §2.4.
SensitivityLevel = Literal["normal", "sensitive", "privileged"]

# Mapping for `resolve_effective_sensitivity` ordering checks. ASCII
# order would not preserve normal < sensitive < privileged.
_SENSITIVITY_ORDER: dict[str, int] = {
    "normal": 0,
    "sensitive": 1,
    "privileged": 2,
}

# Cat-A through Cat-E per memo 05 §2.1 / [D19]. The five-category
# taxonomy is canonical; new categories require a constitutional
# amendment, not a manifest extension.
CapabilityKind = Literal["Cat-A", "Cat-B", "Cat-C", "Cat-D", "Cat-E"]

# Runtime placement per memo 05 §2.1. ``dual`` adapters declare separate
# central/bridge deployments; ``plugin`` adapters extend a host adapter
# (e.g. Home Assistant integrations) and inherit the host's runtime.
CapabilityRuntime = Literal["central", "bridge", "dual", "plugin"]

SourceAssuranceMode = Literal[
    "candidate_assessment",
    "candidate_not_applicable",
    "canonical_projection",
    "canonical_observation",
    "terminal_action",
]


class SourceAssuranceRule(BaseModel):
    """Executable disposition obligation for one or more source facts.

    A capability category describes what an adapter *is*.  This rule declares
    what must happen after each fact it emits, preventing a healthy adapter
    task or advanced cursor from being mistaken for end-to-end product health.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_events: list[str] = Field(min_length=1)
    mode: SourceAssuranceMode
    terminal_events: list[str] = Field(default_factory=list)
    projections: list[str] = Field(default_factory=list)
    assessor: str | None = Field(default=None, min_length=1)


class Capability(BaseModel):
    """One capability block in an adapter pack's ``capabilities:`` list.

    Each capability declares a single epistemic-role category (Cat-A
    through Cat-E) and runtime placement, plus optional per-capability
    sensitivity and observation-mode overrides. Multi-capability adapters
    declare multiple :class:`Capability` entries per [PM-35].
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: CapabilityKind
    runtime: CapabilityRuntime
    event_families: list[str] = Field(default_factory=list)
    sensitivity_default: SensitivityLevel | None = None
    observation_mode_required: bool | None = None


class AdapterManifest(BaseModel):
    """Adapter pack manifest extensions parsed from ``pack.yaml``.

    All fields are optional and default to backward-compatible values so
    that v1 ``pack.yaml`` files (which predate the framework) parse
    cleanly with ``capabilities=[]``. The validator in 11-β will refuse
    empty-capabilities manifests for adapter packs; this layer only
    parses the schema.
    """

    model_config = ConfigDict(extra="forbid")

    capabilities: list[Capability] = Field(default_factory=list)
    write_capabilities: list[str] = Field(default_factory=list)
    sensitivity_default: SensitivityLevel = "normal"
    observation_mode_required: bool = False
    owner_scope_overridability_at_install: bool = True
    sharing_model_discriminator: str | None = None
    dedup_strategy: dict[str, Any] | None = None
    source_assurance: list[SourceAssuranceRule] = Field(default_factory=list)

    def resolve_effective_sensitivity(self, capability_index: int) -> SensitivityLevel:
        """Resolve the effective sensitivity for one capability.

        Returns the per-capability override if it is at least as strict
        as the pack-level default; otherwise returns the pack-level
        default. The "cannot lower below pack-default" rule per
        [§6 invariant 10] is enforced by clamping: a lower override is
        ignored, not rejected, since the manifest may legitimately
        declare a higher pack default with a less-strict per-capability
        block (e.g. a privileged-floor financial adapter exposing a
        normal-shaped read-only capability would still surface as
        privileged because that is the strictest floor across the pack).
        """
        if capability_index < 0 or capability_index >= len(self.capabilities):
            raise IndexError(
                f"capability_index {capability_index} out of range "
                f"(have {len(self.capabilities)} capabilities)"
            )
        override = self.capabilities[capability_index].sensitivity_default
        if override is None:
            return self.sensitivity_default
        if _SENSITIVITY_ORDER[override] < _SENSITIVITY_ORDER[self.sensitivity_default]:
            return self.sensitivity_default
        return override


__all__ = [
    "AdapterManifest",
    "Capability",
    "CapabilityKind",
    "CapabilityRuntime",
    "SourceAssuranceMode",
    "SourceAssuranceRule",
    "SensitivityLevel",
]
