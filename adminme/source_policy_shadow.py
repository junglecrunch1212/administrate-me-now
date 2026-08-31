"""Shadow-only source-policy consumer for production composition."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adminme.candidate_policy import default_policy, source_id_for_event
from adminme.events.envelope import EventEnvelope
from adminme.events.writer import EventSink
from adminme.settings_control import SettingsControlStore
from adminme.source_policy import (
    AcquisitionEvidence,
    SourcePolicyV2,
    assess_source_signals,
    current_v1_route,
    route_for_policy,
    source_coordinate_for_event,
)


class SourcePolicyShadowEvaluator:
    """Compute V1 versus V2 routes without changing admission or providers."""

    CONSUMER_VERSION = "1.0.0"

    def __init__(
        self,
        event_sink: EventSink,
        settings: SettingsControlStore,
        current_policy_fn: Callable[[str, str], dict[str, Any]],
        provider_bindings: tuple[Any, ...] = (),
    ) -> None:
        self._events = event_sink
        self._settings = settings
        self._current_policy_fn = current_policy_fn
        self._provider_bindings = provider_bindings

    async def handle(self, event: dict[str, Any]) -> None:
        source_event_id = str(event.get("event_id") or "")
        if not source_event_id:
            raise ValueError("shadow evaluation requires one persisted source event")
        coordinate = source_coordinate_for_event(
            event, provider_bindings=self._provider_bindings
        )
        effective = self._settings.effective_source_policy_v2(
            coordinate.owner_principal_id, coordinate
        )
        policy = SourcePolicyV2.model_validate(effective["policy"])
        signals = assess_source_signals(event)
        source_id = source_id_for_event(event) or coordinate.source_kind
        if coordinate.owner_kind == "member" and coordinate.context_member_id:
            current_policy = self._current_policy_fn(
                coordinate.context_member_id, source_id
            )
        else:
            # V1 had no service-principal policy.  Preserve that absence
            # explicitly instead of manufacturing a member owner.
            current_policy = {
                **default_policy(source_id),
                "mode": "manual",
                "candidate_kinds": [],
            }
        current_route = current_v1_route(event, current_policy)
        shadow_route = route_for_policy(coordinate, policy, signals)
        evaluated_at = str(
            event.get("recorded_at") or event.get("occurred_at") or "1970-01-01T00:00:00Z"
        )
        reasons = [
            "source_policy_v2_shadow_only",
            "exact_source_coordinate"
            if coordinate.exact
            else "source_coordinate_ambiguous_fail_closed",
            f"policy_origin:{effective['origin']}",
            f"migration_state:{effective['migration_state']}",
            "provider_labels_and_urgency_non_authoritative",
            "llm_claims_non_authoritative",
            "zero_provider_effects",
        ]
        envelope = EventEnvelope(
            event_at_ms=int(event["event_at_ms"]),
            tenant_id=str(event["tenant_id"]),
            type="source.policy_v2.shadow_evaluated",
            schema_version=1,
            occurred_at=evaluated_at,
            source_adapter="source-policy-v2-shadow",
            source_account_id="policy",
            owner_scope=str(event["owner_scope"]),
            visibility_scope=str(event["visibility_scope"]),
            sensitivity=event.get("sensitivity", "normal"),
            correlation_id=event.get("correlation_id"),
            causation_id=source_event_id,
            actor_identity="source-policy-v2-shadow",
            idempotency_key=f"source-policy-v2-shadow:v1:{source_event_id}",
            payload={
                "source_event_id": source_event_id,
                "policy_version": "source-policy-v2",
                "policy_key": coordinate.key,
                "coordinate": coordinate.model_dump(mode="json"),
                "coordinate_state": "exact" if coordinate.exact else "ambiguous",
                "policy_revision": effective["revision"],
                "policy": policy.model_dump(mode="json"),
                "policy_origin": effective["origin"],
                "migration_state": effective["migration_state"],
                "acquisition": AcquisitionEvidence(
                    state=policy.acquisition_state,
                    coverage="unknown",
                    freshness=signals.freshness,
                    provider_capability="available"
                    if coordinate.provider != "unknown"
                    else "unknown",
                ).model_dump(mode="json"),
                "signals": signals.model_dump(mode="json"),
                "current_route": current_route.model_dump(mode="json"),
                "shadow_route": shadow_route.model_dump(mode="json"),
                "reason_codes": reasons,
                "evaluated_at": evaluated_at,
                "live_admission_changed": False,
                "provider_effects": False,
                "llm_authority": False,
            },
        )
        await self._events.write(
            envelope,
            correlation_id=event.get("correlation_id"),
            causation_id=source_event_id,
        )


__all__ = ["SourcePolicyShadowEvaluator"]
