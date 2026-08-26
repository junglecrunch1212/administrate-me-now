"""Cross-envelope invariants that payload schemas cannot enforce alone."""

from __future__ import annotations

from adminme.events.envelope import EventEnvelope


class EnvelopeInvariantError(ValueError):
    """An event payload contradicts its authoritative envelope metadata."""


def _messaging_route_scope(payload: dict[str, object]) -> str:
    routing_scope = payload.get("routing_scope")
    routing_target = payload.get("routing_target_id")
    if routing_scope == "member_private" and isinstance(routing_target, str):
        return f"private:{routing_target}"
    if routing_scope == "household_shared" and routing_target is None:
        return "shared:household"
    if routing_scope == "external_contact" and isinstance(routing_target, str):
        return f"external-contact:{routing_target}"
    if routing_scope == "unresolved" and routing_target is None:
        return "unresolved:service-channel"
    raise EnvelopeInvariantError("messaging v5 route is incomplete")


def validate_envelope_invariants(envelope: EventEnvelope) -> None:
    """Reject contradictions between typed payloads and routing metadata."""

    if envelope.type not in {"messaging.received", "messaging.sent"}:
        return
    if envelope.schema_version < 5:
        return
    expected_scope = _messaging_route_scope(envelope.payload)
    violations: list[str] = []
    if envelope.owner_scope != expected_scope:
        violations.append("owner_scope")
    if envelope.visibility_scope != expected_scope:
        violations.append("visibility_scope")
    if envelope.source_account_id != envelope.payload.get("provider_account_id"):
        violations.append("source_account_id")
    if violations:
        raise EnvelopeInvariantError(
            "messaging v5 envelope contradicts payload route: " + ", ".join(violations)
        )


__all__ = ["EnvelopeInvariantError", "validate_envelope_invariants"]
