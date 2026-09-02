"""Cross-envelope invariants that payload schemas cannot enforce alone."""

from __future__ import annotations

from adminme.events.envelope import EventEnvelope
from adminme.service_identities import service_private_scope


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


def _agreement_scope(payload: dict[str, object]) -> str:
    authority_scope = payload.get("authority_scope")
    owner_member_id = payload.get("owner_member_id")
    if authority_scope == "member_private" and isinstance(owner_member_id, str):
        return f"private:{owner_member_id}"
    if authority_scope == "household_shared" and owner_member_id is None:
        return "shared:household"
    raise EnvelopeInvariantError("operating agreement authority scope is incomplete")


def _validate_exact_scope(envelope: EventEnvelope, expected_scope: str) -> None:
    violations: list[str] = []
    if envelope.owner_scope != expected_scope:
        violations.append("owner_scope")
    if envelope.visibility_scope != expected_scope:
        violations.append("visibility_scope")
    if violations:
        raise EnvelopeInvariantError(
            f"{envelope.type} envelope contradicts payload scope: "
            + ", ".join(violations)
        )


def validate_envelope_invariants(envelope: EventEnvelope) -> None:
    """Reject contradictions between typed payloads and routing metadata."""

    if envelope.type == "operating_agreement.constitution_registered":
        _validate_exact_scope(envelope, "shared:household")
        return
    if envelope.type.startswith("operating_agreement."):
        _validate_exact_scope(envelope, _agreement_scope(envelope.payload))
        return
    if envelope.type == "coordination.originator_receipt_recorded":
        member_id = envelope.payload.get("originator_member_id")
        if not isinstance(member_id, str):
            raise EnvelopeInvariantError("coordination receipt requires an originator")
        _validate_exact_scope(envelope, f"private:{member_id}")
        return
    if envelope.type == "coordination.conversation_linked":
        member_id = envelope.payload.get("member_id")
        if not isinstance(member_id, str):
            raise EnvelopeInvariantError("coordination conversation requires a member")
        _validate_exact_scope(envelope, f"private:{member_id}")
        return
    if envelope.type.startswith("coordination."):
        _validate_exact_scope(envelope, "shared:household")
        return

    if envelope.type not in {"messaging.received", "messaging.sent"}:
        return
    if envelope.schema_version < 5:
        return
    service_id = envelope.payload.get("service_principal_id")
    pending_service_route = (
        envelope.schema_version >= 6
        and isinstance(service_id, str)
        and envelope.payload.get("routing_scope") == "unresolved"
        and envelope.payload.get("routing_target_id") is None
    )
    if pending_service_route:
        assert isinstance(service_id, str)
        expected_scope = service_private_scope(service_id)
    else:
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
