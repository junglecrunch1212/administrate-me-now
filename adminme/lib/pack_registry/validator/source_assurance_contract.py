"""Fail-closed source-disposition validation for adapter manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.lib.pack_registry.models import AdapterManifest, SourceAssuranceRule
from adminme.lib.pack_registry.validator.result import ValidationFinding

_CHECK_NAME = "source_assurance_contract"


def _finding(message: str, field_path: str) -> ValidationFinding:
    return ValidationFinding(
        severity="error",
        check=_CHECK_NAME,
        message=message,
        field_path=field_path,
    )


def _rules_for_event(
    rules: list[SourceAssuranceRule], event_type: str
) -> list[SourceAssuranceRule]:
    return [rule for rule in rules if event_type in rule.source_events]


def check_source_assurance_contract(
    manifest: AdapterManifest,
    *,
    registry: Any | None = None,
    projections_dir: Path | None = None,
) -> list[ValidationFinding]:
    """Require every declared source family to have a downstream disposition.

    The category-to-mode rules are deliberately structural.  They do not let
    an adapter claim health merely because it emitted bytes:

    * Cat-A facts must receive a candidate assessment, including a recorded
      no-candidate/quarantine/failure outcome.
    * Cat-B facts must reach a named canonical projection.
    * Cat-C facts must reach a projection or be explicitly retained as a
      canonical observation.
    * Cat-D facts must reach a private knowledge projection; every non-delete
      source explicitly declares candidate assessment or not-applicable.
    * Cat-E events are terminal action results, never inferred successes.
    """

    findings: list[ValidationFinding] = []
    declared: dict[str, str] = {}
    for cap_index, capability in enumerate(manifest.capabilities):
        for event_type in capability.event_families:
            declared[event_type] = capability.kind
            rules = _rules_for_event(manifest.source_assurance, event_type)
            field = f"capabilities[{cap_index}].event_families"
            if not rules:
                findings.append(
                    _finding(
                        f"{capability.kind} source event {event_type!r} has no "
                        "source_assurance disposition rule.",
                        field,
                    )
                )
                continue
            modes = {rule.mode for rule in rules}
            required_modes = {
                "Cat-A": {"candidate_assessment"},
                "Cat-B": {"canonical_projection", "canonical_observation"},
                "Cat-C": {"canonical_projection", "canonical_observation"},
                "Cat-D": {"canonical_projection"},
                "Cat-E": {"terminal_action"},
            }[capability.kind]
            if not modes.intersection(required_modes):
                findings.append(
                    _finding(
                        f"{capability.kind} source event {event_type!r} requires one "
                        f"of {sorted(required_modes)}; declared {sorted(modes)}.",
                        field,
                    )
                )
            if (
                capability.kind == "Cat-D"
                and not event_type.endswith(("_deleted", ".deleted"))
                and not modes.intersection(
                    {"candidate_assessment", "candidate_not_applicable"}
                )
            ):
                findings.append(
                    _finding(
                        f"Cat-D source event {event_type!r} must explicitly declare "
                        "candidate_assessment or candidate_not_applicable.",
                        field,
                    )
                )
            if {
                "candidate_assessment",
                "candidate_not_applicable",
            } <= modes:
                findings.append(
                    _finding(
                        f"source event {event_type!r} cannot be both candidate-bearing "
                        "and candidate_not_applicable.",
                        field,
                    )
                )

    if registry is None:
        from adminme.events.registry import (
            ensure_autoloaded,
            registry as default_registry,
        )

        ensure_autoloaded()
        registry = default_registry
    if hasattr(registry, "known_types"):
        registered = set(registry.known_types())
    elif hasattr(registry, "registered_event_families"):
        registered = set(registry.registered_event_families())
    else:
        raise TypeError("registry must expose known_types()")

    if projections_dir is None:
        from adminme.projections import base as projection_base

        projections_dir = Path(projection_base.__file__).parent

    for rule_index, rule in enumerate(manifest.source_assurance):
        field = f"source_assurance[{rule_index}]"
        unknown = sorted(set(rule.source_events) - set(declared))
        if unknown:
            findings.append(
                _finding(
                    f"source_assurance references undeclared capability events {unknown}.",
                    f"{field}.source_events",
                )
            )
        if rule.mode == "candidate_assessment":
            if "triage.intent.features_extracted" not in rule.terminal_events:
                findings.append(
                    _finding(
                        "candidate_assessment must terminate in "
                        "triage.intent.features_extracted.",
                        f"{field}.terminal_events",
                    )
                )
            if not rule.assessor:
                findings.append(
                    _finding(
                        "candidate_assessment requires a named deterministic assessor.",
                        f"{field}.assessor",
                    )
                )
        elif rule.mode == "canonical_projection":
            if not rule.projections:
                findings.append(
                    _finding(
                        "canonical_projection requires at least one projection name.",
                        f"{field}.projections",
                    )
                )
            for projection in rule.projections:
                if not (projections_dir / projection).is_dir():
                    findings.append(
                        _finding(
                            f"canonical_projection names unknown projection {projection!r}.",
                            f"{field}.projections",
                        )
                    )
        elif rule.mode in {
            "candidate_not_applicable",
            "canonical_observation",
            "terminal_action",
        }:
            if rule.terminal_events or rule.projections or rule.assessor:
                findings.append(
                    _finding(
                        f"{rule.mode} is terminal at the source event and cannot "
                        "claim downstream terminal events, projections, or an assessor.",
                        field,
                    )
                )
        for terminal in rule.terminal_events:
            if terminal not in registered:
                findings.append(
                    _finding(
                        f"source_assurance terminal event {terminal!r} is not registered.",
                        f"{field}.terminal_events",
                    )
                )
    return findings


__all__ = ["check_source_assurance_contract"]
