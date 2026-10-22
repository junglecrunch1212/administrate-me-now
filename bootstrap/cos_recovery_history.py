"""Exact proof for one historical, interrupted CoS recovery defect.

This is not a general history editor. Missing boundaries are derived only from
the two archived successor windows and the corresponding immutable control
events. The caller owns source selection, private-file hashes and the guarded
backup/settlement transaction. No event or old journal is changed.
"""

from __future__ import annotations

import base64
import copy
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.cos_channel_operation import (
    ACTIVATED_EVENT,
    DEACTIVATED_EVENT,
    aware_time,
    parse_activation,
)
from bootstrap.candidate_readiness import provider_effect_window_evidence


class RecoveryHistoryError(ValueError):
    """The exact retained evidence does not prove this bounded repair."""


class JournalReference(BaseModel):
    model_config = {"extra": "forbid", "strict": True}

    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RecoveryHistoryRepairRequest(BaseModel):
    model_config = {"extra": "forbid", "strict": True}

    contract: Literal["cos-recovery-history-repair-v1"] = (
        "cos-recovery-history-repair-v1"
    )
    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_principal_id: str = Field(pattern=r"^operator:.+", max_length=240)
    owner_member_id: str = Field(min_length=1, max_length=240)
    expected_release_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    expected_controller_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    initial_snapshot: dict[str, str]
    readiness_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    failed_operation: JournalReference
    activation_operation: JournalReference
    maintenance_operation: JournalReference
    recovery_deactivation_event_ids: list[str] = Field(min_length=2, max_length=2)
    observation_enabled_event_ids: list[str] = Field(min_length=2, max_length=2)
    activation_event_id: str = Field(min_length=1, max_length=240)
    observation_disabled_event_id: str = Field(min_length=1, max_length=240)
    maintenance_deactivation_event_id: str = Field(min_length=1, max_length=240)
    maintenance_observation_enabled_event_id: str = Field(min_length=1, max_length=240)


def initial_readiness(journal: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(
            base64.b64decode(
                journal["initial_files"]["deployment-readiness.json"], validate=True
            )
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoveryHistoryError("archived initial readiness is unavailable") from exc
    if not isinstance(value, dict):
        raise RecoveryHistoryError("archived initial readiness is not an object")
    return value


def window_vector(readiness: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider_effect_window": copy.deepcopy(
            readiness.get("provider_effect_window")
        ),
        "prior_provider_effect_windows": copy.deepcopy(
            readiness.get("prior_provider_effect_windows", [])
        ),
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RecoveryHistoryError(message)


def _time(value: Any) -> datetime:
    parsed = aware_time(value)
    if parsed is None or parsed > datetime.now(timezone.utc):
        raise RecoveryHistoryError("historical boundary is missing or invalid")
    return parsed


def derive_recovery_history(
    request: RecoveryHistoryRepairRequest,
    readiness: dict[str, Any],
    failed: dict[str, Any],
    activated: dict[str, Any],
    maintenance: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Reconstruct only the proved omitted interval and one unclosed interval."""
    references = (
        request.failed_operation,
        request.activation_operation,
        request.maintenance_operation,
    )
    _require(
        len({ref.operation_id for ref in references}) == 3, "journal roles overlap"
    )
    for journal, reference, phase in zip(
        (failed, activated, maintenance),
        references,
        ("recovered", "committed", "committed"),
        strict=True,
    ):
        _require(
            journal.get("contract") == "cos-channel-operation-journal-v1"
            and journal.get("activation_id") == reference.operation_id
            and journal.get("phase") == phase
            and journal.get("release_commit") == request.expected_release_commit
            and journal.get("operator_principal_id") == request.operator_principal_id
            and journal.get("owner_member_id") == request.owner_member_id,
            "archived operation provenance differs",
        )
    _require(
        failed.get("enabled_live") is True
        and activated.get("enabled_live") is True
        and failed.get("operation") is None
        and activated.get("operation") is None
        and "recovery_window_plan" not in failed
        and maintenance.get("operation") == "enter_observation"
        and maintenance.get("prior_activation_id") == activated["activation_id"],
        "journal operations do not match the legacy recovery defect",
    )
    original = initial_readiness(failed)
    original_vector = provider_effect_window_evidence(
        original, now=datetime.now(timezone.utc)
    )
    base = original_vector["prior_provider_effect_windows"]
    omitted = original_vector["provider_effect_window"]
    capture = initial_readiness(activated)
    capture_prior = capture.get("prior_provider_effect_windows")
    first = (
        capture_prior[-1] if isinstance(capture_prior, list) and capture_prior else None
    )
    second = capture.get("provider_effect_window")
    _require(
        isinstance(first, dict)
        and isinstance(second, dict)
        and capture_prior == [*base, first]
        and set(first) == {"started_at", "reason"}
        and set(second) == {"started_at", "reason"}
        and first["reason"] == second["reason"] == "cos_operation_recovery"
        and "ended_at" not in omitted
        and omitted.get("reason") == "cos_enter_observation",
        "archived history is not the exact two-recovery overwrite shape",
    )
    assert isinstance(first, dict) and isinstance(second, dict)
    _require(
        _time(omitted["started_at"])
        < _time(first["started_at"])
        < _time(second["started_at"]),
        "recovery boundaries are not ordered",
    )
    # Read only authentic tenant control events in durable log order. No other
    # activation/toggle can be hidden between the source windows and maintenance.
    control_types = {
        ACTIVATED_EVENT,
        DEACTIVATED_EVENT,
        "observation.enabled",
        "observation.disabled",
    }
    controls = [
        row
        for row in events
        if row.get("tenant_id") == tenant_id and row.get("type") in control_types
    ]
    expected_ids = [
        request.recovery_deactivation_event_ids[0],
        request.observation_enabled_event_ids[0],
        request.recovery_deactivation_event_ids[1],
        request.observation_enabled_event_ids[1],
        request.activation_event_id,
        request.observation_disabled_event_id,
        request.maintenance_deactivation_event_id,
        request.maintenance_observation_enabled_event_id,
    ]
    _require(len(set(expected_ids)) == 8, "control event references overlap")
    positions = [
        i for i, row in enumerate(controls) if row.get("event_id") == expected_ids[0]
    ]
    _require(len(positions) == 1, "first recovery event is missing or ambiguous")
    offset = positions[0]
    selected = controls[offset:]
    _require(
        [row.get("event_id") for row in selected] == expected_ids,
        "control history has missing or intervening transitions",
    )
    _require(
        not any(
            row["type"] in {ACTIVATED_EVENT, "observation.disabled"}
            and _time(row.get("occurred_at")) >= _time(omitted["started_at"])
            for row in controls[:offset]
        ),
        "omitted interval has an intervening live transition",
    )
    for row in selected:
        _require(
            row.get("actor_identity") == request.operator_principal_id
            and row.get("owner_scope") == "shared:household"
            and row.get("visibility_scope") == "shared:household"
            and row.get("source_account_id") == "system"
            and row.get("sensitivity") == "normal"
            and row.get("schema_version")
            == (
                2
                if row.get("event_id") == request.maintenance_deactivation_event_id
                or (
                    row["type"] == ACTIVATED_EVENT
                    and "self_reply_bindings" in row.get("payload", {})
                )
                else 1
            )
            and row.get("source_adapter")
            == (
                "observation"
                if row["type"].startswith("observation.")
                else "cos-channel-operation"
            ),
            "control event authority or scope differs",
        )
    _require(
        not any(
            row["type"] == ACTIVATED_EVENT
            and row.get("payload", {}).get("activation_id") == failed["activation_id"]
            for row in controls
        ),
        "failed operation has a live activation fact",
    )
    for index, reason, boundary in (
        (0, "operation_failed", first),
        (2, "interrupted_operation", second),
    ):
        deactivated, enabled = selected[index : index + 2]
        _require(
            deactivated["type"] == DEACTIVATED_EVENT
            and deactivated.get("source_adapter") == "cos-channel-operation"
            and deactivated.get("payload")
            == {
                "activation_id": failed["activation_id"],
                "operator_principal_id": request.operator_principal_id,
                "reason": reason,
                "observation_active": True,
            }
            and enabled["type"] == "observation.enabled"
            and enabled.get("payload", {}).get("reason") == "CoS operation recovery"
            and enabled.get("payload", {}).get("actor") == request.operator_principal_id
            and enabled.get("payload", {}).get("prior_state") is True
            and _time(enabled.get("payload", {}).get("enabled_at"))
            == _time(enabled.get("occurred_at"))
            and _time(deactivated.get("occurred_at")).replace(microsecond=0)
            == _time(enabled.get("occurred_at"))
            and _time(deactivated.get("occurred_at")) <= _time(boundary["started_at"])
            and _time(boundary["started_at"]).replace(microsecond=0)
            == _time(enabled.get("occurred_at")),
            "recovery boundary lacks its exact adjacent control facts",
        )
    activation_event, disabled, paused, observing = selected[4:]
    activation = parse_activation(activation_event.get("payload", {}))
    _require(
        activation_event["type"] == ACTIVATED_EVENT
        and activation_event.get("source_adapter") == "cos-channel-operation"
        and activation.activation_id == activated["activation_id"]
        and activation.release_commit == request.expected_release_commit
        and activation.operator_principal_id == request.operator_principal_id
        and activation.owner_authorization_sha256
        == activated.get("owner_authorization_sha256")
        and activation.observation_window_started_at == second["started_at"]
        and disabled["type"] == "observation.disabled"
        and disabled.get("payload", {}).get("actor") == request.operator_principal_id
        and disabled.get("payload", {}).get("prior_state") is True
        and disabled.get("payload", {}).get("reason")
        == "owner-authorized CoS channel replies"
        and _time(disabled.get("payload", {}).get("disabled_at"))
        == _time(disabled.get("occurred_at"))
        and -1
        < (
            _time(disabled.get("occurred_at")) - _time(activation.enabled_at)
        ).total_seconds()
        <= 1,
        "successor activation does not prove the retained closed boundary",
    )
    closed_second = {**second, "ended_at": activation.enabled_at}
    before_maintenance = initial_readiness(maintenance)
    _require(
        window_vector(before_maintenance)
        == {
            "prior_provider_effect_windows": [*base, first],
            "provider_effect_window": closed_second,
        }
        and window_vector(readiness)
        == {
            "prior_provider_effect_windows": [*base, first, closed_second],
            "provider_effect_window": {
                "started_at": maintenance.get("observation_started_at"),
                "reason": "cos_enter_observation",
            },
        },
        "current history differs from the immutable maintenance prefix",
    )
    _require(
        paused["type"] == DEACTIVATED_EVENT
        and paused.get("payload")
        == {
            "activation_id": activated["activation_id"],
            "operator_principal_id": request.operator_principal_id,
            "reason": "deployment_observation",
            "observation_active": True,
        }
        and paused.get("occurred_at") == maintenance.get("observation_started_at")
        and observing["type"] == "observation.enabled"
        and observing.get("payload", {}).get("actor") == request.operator_principal_id
        and observing.get("payload", {}).get("prior_state") is False
        and _time(observing.get("payload", {}).get("enabled_at"))
        == _time(observing.get("occurred_at"))
        and -1
        < (
            _time(observing.get("occurred_at"))
            - _time(maintenance.get("observation_started_at"))
        ).total_seconds()
        <= 1
        and observing.get("payload", {}).get("reason")
        == f"CoS deployment observation:{maintenance['activation_id']}",
        "maintenance observation lacks its exact durable transition",
    )
    repaired = copy.deepcopy(readiness)
    repaired["prior_provider_effect_windows"] = [
        *base,
        {**omitted, "ended_at": first["started_at"]},
        {**first, "ended_at": second["started_at"]},
        closed_second,
    ]
    provider_effect_window_evidence(repaired, now=datetime.now(timezone.utc))
    return repaired
