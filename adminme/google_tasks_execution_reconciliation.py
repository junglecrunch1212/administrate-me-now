"""Independent normal-source observation and reconciliation for B03."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.google_tasks_execution import canonical_sha256


class GoogleTasksExecutionReconciler:
    CONSUMER_VERSION = "google-tasks-create-production-v2-reconciliation-v1"

    def __init__(self, event_log: Any, event_sink: Any) -> None:
        self._event_log = event_log
        self._events = event_sink

    async def _events_of(self, event_type: str) -> list[dict[str, Any]]:
        return [event async for event in self._event_log.read_since(types=[event_type])]

    async def handle(self, observation: dict[str, Any]) -> None:
        if observation["type"] not in {
            "list_item.added",
            "list_item.updated",
            "list_item.completed",
            "list_item.uncompleted",
            "list_item.deleted",
        }:
            return
        p = observation["payload"]
        if p.get("provider") != "google_tasks":
            return
        terminals = [
            event
            for event in await self._events_of("cos.google_tasks_execution.terminal")
            if event["tenant_id"] == observation["tenant_id"]
            and event["payload"].get("outcome") == "success"
            and str(event["payload"].get("provider_account_coordinate", "")).casefold()
            == str(p.get("provider_account_id", "")).casefold()
            and event["payload"].get("task_list_id") == p.get("list_external_id")
        ]
        prepared_events = await self._events_of(
            "cos.google_tasks_execution.prepared"
        )
        eligible: list[
            tuple[
                dict[str, Any],
                dict[str, Any],
                dict[str, Any],
                dict[str, Any],
                str,
                str,
            ]
        ] = []
        for terminal_candidate in terminals:
            command_candidate = str(terminal_candidate["payload"]["command_id"])
            prepared = [
                event
                for event in prepared_events
                if event["tenant_id"] == observation["tenant_id"]
                and event["payload"].get("command_id") == command_candidate
            ]
            if len(prepared) != 1:
                continue
            prepared_candidate = prepared[0]
            prepared_payload = prepared_candidate["payload"]
            expected_visibility = (
                f"private:{prepared_payload['resource_owner']}"
                if prepared_payload["active_context"] == "member_private"
                else "shared:household"
            )
            if (
                observation["visibility_scope"] != expected_visibility
                or p.get("service_principal_id")
                != prepared_payload["provider_owner"]
            ):
                continue
            desired_candidate = dict(prepared_payload["desired_task_content"])
            observed_candidate: dict[str, Any] = {}
            for key in desired_candidate:
                if key == "status":
                    observed_candidate[key] = (
                        "needsAction" if not p.get("completed") else "completed"
                    )
                elif key in p:
                    observed_candidate[key] = p.get(key)
            observed_candidate_fingerprint = canonical_sha256(observed_candidate)
            desired_candidate_fingerprint = str(
                terminal_candidate["payload"]["desired_state_fingerprint"]
            )
            if (
                terminal_candidate["payload"].get("native_task_id")
                != p.get("item_external_id")
                and observed_candidate_fingerprint
                != desired_candidate_fingerprint
            ):
                continue
            eligible.append(
                (
                    terminal_candidate,
                    prepared_candidate,
                    desired_candidate,
                    observed_candidate,
                    observed_candidate_fingerprint,
                    desired_candidate_fingerprint,
                )
            )
        if len(eligible) != 1:
            return
        (
            terminal,
            prepared_event,
            desired,
            observed,
            observed_fingerprint,
            desired_fingerprint,
        ) = eligible[0]
        command_id = str(terminal["payload"]["command_id"])
        sibling_events: list[dict[str, Any]] = []
        for event_type in (
            "list_item.added",
            "list_item.updated",
            "list_item.completed",
            "list_item.uncompleted",
            "list_item.deleted",
        ):
            sibling_events.extend(await self._events_of(event_type))
        latest_by_id: dict[str, dict[str, Any]] = {}
        for sibling in sibling_events:
            source = sibling["payload"]
            if (
                sibling["tenant_id"] != observation["tenant_id"]
                or source.get("provider") != "google_tasks"
                or str(source.get("provider_account_id", "")).casefold()
                != str(p.get("provider_account_id", "")).casefold()
                or source.get("list_external_id") != p.get("list_external_id")
                or sibling["visibility_scope"] != observation["visibility_scope"]
                or source.get("service_principal_id")
                != prepared_event["payload"]["provider_owner"]
            ):
                continue
            object_id = str(source.get("item_external_id") or "")
            prior = latest_by_id.get(object_id)
            if prior is None or str(prior["event_id"]) < str(sibling["event_id"]):
                latest_by_id[object_id] = sibling
        matching_object_ids: set[str] = set()
        for object_id, sibling in latest_by_id.items():
            if sibling["type"] == "list_item.deleted":
                continue
            source = sibling["payload"]
            sibling_state = {
                key: (
                    ("needsAction" if not source.get("completed") else "completed")
                    if key == "status"
                    else source.get(key)
                )
                for key in desired
            }
            if canonical_sha256(sibling_state) == desired_fingerprint:
                matching_object_ids.add(object_id)
        duplicate_count = len(
            matching_object_ids - {str(terminal["payload"]["native_task_id"])}
        )
        if duplicate_count:
            outcome, reason = "conflicted", "multiple_provider_objects_match_one_approved_effect"
        elif observation["type"] == "list_item.deleted":
            outcome, reason = "missing", "normal_source_observed_provider_object_deleted"
        elif observed_fingerprint == desired_fingerprint:
            outcome, reason = "matched", "independent_normal_source_matches_approved_effect"
        else:
            outcome, reason = "drifted", "normal_source_state_differs_from_approved_effect"
        now = datetime.now(timezone.utc).isoformat()
        observation_event_id = await self._events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=str(observation["tenant_id"]),
                type="cos.google_tasks_execution.observed",
                schema_version=2,
                occurred_at=now,
                source_adapter="google-tasks-normal-source-reconciliation",
                source_account_id=str(p["provider_account_id"]),
                owner_scope=str(observation["owner_scope"]),
                visibility_scope=str(observation["visibility_scope"]),
                sensitivity=observation["sensitivity"],
                causation_id=str(observation["event_id"]),
                idempotency_key=f"b03-observation:{command_id}:{observation['event_id']}",
                raw_ref=f"content-sha256:{observed_fingerprint}",
                payload={
                    "command_id": command_id,
                    "execution_envelope_id": terminal["payload"]["execution_envelope_id"],
                    "terminal_result_event_id": terminal["event_id"],
                    "source_event_id": observation["event_id"],
                    "provider": "google_tasks",
                    "provider_account_coordinate": p["provider_account_id"],
                    "resource_owner": prepared_event["payload"]["resource_owner"],
                    "task_list_id": p["list_external_id"],
                    "native_task_id": p["item_external_id"],
                    "provider_revision": p.get("provider_revision"),
                    "desired_state_fingerprint": desired_fingerprint,
                    "observed_state_fingerprint": observed_fingerprint,
                    "observed_at": now,
                    "independent_source_observation": True,
                },
            )
        )
        await self._events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=str(observation["tenant_id"]),
                type="cos.google_tasks_execution.reconciled",
                schema_version=2,
                occurred_at=now,
                source_adapter="google-tasks-normal-source-reconciliation",
                source_account_id=str(p["provider_account_id"]),
                owner_scope=str(observation["owner_scope"]),
                visibility_scope=str(observation["visibility_scope"]),
                sensitivity=observation["sensitivity"],
                causation_id=observation_event_id,
                idempotency_key=f"b03-reconciliation:{command_id}:{observation['event_id']}",
                raw_ref=f"content-sha256:{observed_fingerprint}",
                payload={
                    "command_id": command_id,
                    "execution_envelope_id": terminal["payload"]["execution_envelope_id"],
                    "terminal_result_event_id": terminal["event_id"],
                    "observation_event_id": observation_event_id,
                    "outcome": outcome,
                    "desired_state_fingerprint": desired_fingerprint,
                    "observed_state_fingerprint": observed_fingerprint,
                    "duplicate_provider_objects": duplicate_count,
                    "reason_code": reason,
                    "reconciled_at": now,
                    "provider_effect_count": 0,
                },
            )
        )


__all__ = ["GoogleTasksExecutionReconciler"]
