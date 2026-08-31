"""Fail-closed live-fidelity sandbox contracts and lifecycle."""

from adminme.sandbox.contracts import (
    FIDELITY_CONTRACT,
    GENERATOR_VERSION,
    ManifestError,
    build_target_envelope,
    dominance_report,
    validate_manifest,
)

__all__ = [
    "FIDELITY_CONTRACT",
    "GENERATOR_VERSION",
    "ManifestError",
    "build_target_envelope",
    "dominance_report",
    "validate_manifest",
]
