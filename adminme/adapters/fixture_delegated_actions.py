"""Credential-free provider fixture for G07 execution/reconciliation tests.

The adapter is part of the production composition only when the explicit
deterministic fixture setting is active.  It never contacts a provider.  Its
in-memory provider state lets process acceptance prove exact effects, ambiguous
missing results, later observations, drift, and conflict handling.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast

from adminme.delegated_execution import ACTION_SPECS, ActionClass, action_fingerprint
from adminme.lib.adapters import ProviderConflictError
from adminme.lib.observation import ObservationManager, outbound
from adminme.lib.session import Session


@dataclass(frozen=True)
class FixtureActionResult:
    applied: bool
    external_id: str | None
    provider_revision: str | None
    state: dict[str, Any]
    suppressed: bool


class FixtureDelegatedActionAdapter:
    """Exact-state fixture; executable only through the runtime command bus."""

    name = "fixture-delegated-actions"
    version = "1.0.0"

    def __init__(
        self, *, event_sink: Any, observation_manager: ObservationManager
    ) -> None:
        self._events = event_sink
        self._observation = observation_manager
        self.effects: list[dict[str, Any]] = []
        self._state_by_action: dict[str, dict[str, Any]] = {}
        self._revision_by_action: dict[str, int] = {}
        self.suppress_terminal_for: set[str] = set()
        self.fail_for: set[str] = set()

    async def apply(
        self,
        *,
        session: Session,
        command: dict[str, Any],
    ) -> FixtureActionResult:
        details = command.get("payload")
        if not isinstance(details, dict) or details.get("delegated_action") is not True:
            raise ValueError("fixture delegated action marker is required")
        action_id = str(details.get("action_id") or "")
        action_class = str(details.get("action_class") or "")
        spec = ACTION_SPECS.get(cast(ActionClass, action_class))
        if not action_id or spec is None or not spec.fixture_executable:
            raise ValueError("fixture action class is not executable")
        if action_id in self.fail_for:
            self.fail_for.remove(action_id)
            raise RuntimeError("fixture provider failure")
        if command.get("provider") != "fixture":
            raise ValueError("fixture adapter accepts only fixture commands")
        if details.get("provider_family") != spec.provider_family:
            raise ValueError("fixture provider family does not match the action class")
        if command.get("operation") != spec.operation:
            raise ValueError("fixture operation does not match the action class")
        destination_id = str(details.get("destination_id") or "")
        owner = str(details.get("provider_account_owner_principal_id") or "")
        if not destination_id or not owner:
            raise ValueError("fixture action requires exact owner and destination")
        member_prefix = f"fixture:member:{session.auth_member_id}:"
        service_prefix = "fixture:service:chief-of-staff:"
        if owner == session.auth_member_id:
            if not destination_id.startswith(member_prefix):
                raise ValueError("member fixture destination belongs to another owner")
        elif owner == "service-principal:chief-of-staff":
            if not destination_id.startswith(service_prefix):
                raise ValueError("service fixture destination is not service-owned")
        else:
            raise ValueError("fixture provider owner is outside the active context")
        expected = command.get("expected_provider_revision")
        prior_revision_number = self._revision_by_action.get(action_id, 0)
        prior_revision = (
            f"fixture-rev:{prior_revision_number}" if prior_revision_number else None
        )
        if expected is not None and expected != prior_revision:
            raise ProviderConflictError("fixture provider revision changed")
        desired_state = details.get("desired_state")
        if not isinstance(desired_state, dict):
            raise ValueError("fixture desired state is required")
        exact_effect = {
            "action_id": action_id,
            "action_class": action_class,
            "operation": spec.operation,
            "provider_family": spec.provider_family,
            "provider_account_id": command.get("provider_account_id"),
            "provider_account_owner_principal_id": owner,
            "destination_id": destination_id,
            "desired_state": deepcopy(desired_state),
            "action_fingerprint": details.get("action_fingerprint"),
            "domain_approval_event_id": command.get("domain_approval_event_id"),
            "openclaw_approval_event_id": command.get("openclaw_approval_event_id"),
        }

        async def _apply() -> dict[str, Any]:
            revision_number = prior_revision_number + 1
            provider_revision = f"fixture-rev:{revision_number}"
            external_id = str(
                command.get("target_external_id")
                or f"fixture-object:{hashlib.sha256(action_id.encode()).hexdigest()[:24]}"
            )
            observation = {
                "action_id": action_id,
                "external_id": external_id,
                "provider_revision": provider_revision,
                "destination_id": destination_id,
                "state": deepcopy(desired_state),
                "direct_provider_edit_detected": False,
            }
            self._revision_by_action[action_id] = revision_number
            self._state_by_action[action_id] = observation
            self.effects.append(
                {
                    **exact_effect,
                    "external_id": external_id,
                    "provider_revision": provider_revision,
                }
            )
            return observation

        result = await outbound(
            session,
            f"fixture.{spec.provider_family}.{spec.operation}",
            exact_effect,
            _apply,
            manager=self._observation,
            event_sink=self._events,
            target_channel=self.name,
            target_identifier=destination_id,
        )
        if result.suppressed:
            return FixtureActionResult(False, None, None, {}, True)
        applied = dict(result.result or {})
        return FixtureActionResult(
            True,
            str(applied["external_id"]),
            str(applied["provider_revision"]),
            dict(applied["state"]),
            False,
        )

    def observation(self, action_id: str) -> dict[str, Any] | None:
        value = self._state_by_action.get(action_id)
        return deepcopy(value) if value is not None else None

    def direct_edit(self, action_id: str, state: dict[str, Any]) -> None:
        """Fixture-only simulation of a provider edit outside AdministrateMe."""

        current = self._state_by_action.get(action_id)
        if current is None:
            raise LookupError("fixture provider object does not exist")
        revision_number = self._revision_by_action[action_id] + 1
        self._revision_by_action[action_id] = revision_number
        current["provider_revision"] = f"fixture-rev:{revision_number}"
        current["state"] = deepcopy(state)
        current["direct_provider_edit_detected"] = True

    def drift(self, action_id: str, state: dict[str, Any]) -> None:
        """Fixture-only simulation of an observed non-edit state mismatch."""

        current = self._state_by_action.get(action_id)
        if current is None:
            raise LookupError("fixture provider object does not exist")
        current["state"] = deepcopy(state)
        current["direct_provider_edit_detected"] = False

    def effect_fingerprints(self) -> list[str]:
        return [action_fingerprint(effect) for effect in self.effects]


__all__ = ["FixtureActionResult", "FixtureDelegatedActionAdapter"]
