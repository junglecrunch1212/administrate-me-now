"""Capability-runtime coherence check (1 of 6).

Enforces four declarative invariants the manifest schema cannot pin
on its own (the schema layer allows any ``CapabilityKind`` paired with
any ``CapabilityRuntime``; this check refuses the combinations that
the architecture amendments forbid):

1. **Cat-D ⇒ runtime="bridge"** [memo 04 §2.2, memo 05 §2.2]. Personal
   knowledge-vault adapters run on the bridge runtime; running them
   centrally would defeat the Tailscale-binds-owner-scope property.

2. **Cat-D ⇒ ``owner_scope_overridability_at_install=False``**
   [memo 04 §2.2, memo 05 §2.6]. The same Tailscale-binds-owner-scope
   property forbids overriding the owner scope at install time;
   bridge-bound knowledge adapters inherit their owner from the bridge
   host's Tailscale identity.

3. **Cat-E ⇒ ``observation_mode_required=True``** [memo 05 §2.4,
   §6.20]. Outbound-action adapters MUST honor observation mode; the
   manifest must declare it required so the supervisor wires the
   observation suppression.

4. **Cat-A with messaging family ⇒ runtime ∈ {"central", "plugin"}**
   [memo 05 §2.1]. Messaging adapters never run on the bridge.

Manifest-level rules (2) and (3) surface as findings on the
manifest's pack-level field paths
(``owner_scope_overridability_at_install`` /
``observation_mode_required``); per-capability rules (1) and (4)
surface on ``capabilities[N].runtime`` / ``capabilities[N].kind``.

The check does NOT short-circuit: every violated rule yields one
finding, so a manifest that violates both Cat-D rules surfaces both at
once. Per memo 05 §3.4 "diagnostics that name the field."

The ``family`` field that rule (4) reads is the top-level ``family:``
key in ``pack.yaml`` — NOT a manifest-extension field, so it is not
on :class:`AdapterManifest`. The check accepts an optional
``pack_family`` argument that the aggregator passes through; when
absent, the messaging-family sub-rule is skipped (defensive: a
manifest with no family declared is not this check's responsibility
to flag).
"""

from __future__ import annotations

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.result import ValidationFinding

_CHECK_NAME = "capability_runtime_coherence"


def check_capability_runtime_coherence(
    manifest: AdapterManifest,
    *,
    pack_family: str | None = None,
) -> list[ValidationFinding]:
    """Run all four coherence rules against ``manifest`` and return findings.

    See module docstring for the rule list. ``pack_family`` is the
    top-level ``family:`` from ``pack.yaml`` (e.g. ``"messaging"``,
    ``"reminders"``, ``"financial"``); the aggregator wires this from
    the raw yaml payload since :class:`AdapterManifest` does not carry
    family (it is not a manifest-extension field).
    """
    findings: list[ValidationFinding] = []
    kinds = {cap.kind for cap in manifest.capabilities}

    for index, cap in enumerate(manifest.capabilities):
        if cap.kind == "Cat-D" and cap.runtime != "bridge":
            findings.append(
                ValidationFinding(
                    severity="error",
                    check=_CHECK_NAME,
                    message=(
                        f"Cat-D capability at index {index} declares "
                        f"runtime={cap.runtime!r}; Cat-D requires "
                        f"runtime='bridge' per memo 04 §2.2 "
                        f"(Tailscale-binds-owner-scope)."
                    ),
                    field_path=f"capabilities[{index}].runtime",
                )
            )

        if (
            cap.kind == "Cat-A"
            and pack_family == "messaging"
            and cap.runtime not in {"central", "plugin"}
        ):
            findings.append(
                ValidationFinding(
                    severity="error",
                    check=_CHECK_NAME,
                    message=(
                        f"Cat-A messaging capability at index {index} "
                        f"declares runtime={cap.runtime!r}; messaging "
                        f"family requires runtime in {{'central', "
                        f"'plugin'}} per memo 05 §2.1."
                    ),
                    field_path=f"capabilities[{index}].runtime",
                )
            )

    if "Cat-D" in kinds and manifest.owner_scope_overridability_at_install:
        findings.append(
            ValidationFinding(
                severity="error",
                check=_CHECK_NAME,
                message=(
                    "manifest declares a Cat-D capability but "
                    "owner_scope_overridability_at_install=True; "
                    "Cat-D requires it to be False per memo 04 §2.2 "
                    "and memo 05 §2.6."
                ),
                field_path="owner_scope_overridability_at_install",
            )
        )

    if "Cat-E" in kinds and not manifest.observation_mode_required:
        findings.append(
            ValidationFinding(
                severity="error",
                check=_CHECK_NAME,
                message=(
                    "manifest declares a Cat-E capability but "
                    "observation_mode_required=False; Cat-E requires "
                    "it to be True per memo 05 §2.4 and [§6.20]."
                ),
                field_path="observation_mode_required",
            )
        )

    return findings


__all__ = ["check_capability_runtime_coherence"]
