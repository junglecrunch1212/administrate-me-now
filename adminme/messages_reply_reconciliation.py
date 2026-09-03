"""Reconcile a bridge terminal result with its later G10 outgoing observation."""

from __future__ import annotations

import time
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.messages_reply import REPLY_CONTRACT, body_sha256, canonical_sha256


class MessagesReplyReconciler:
    CONSUMER_VERSION = "apple-messages-reply-reconciliation-v1"

    def __init__(self, event_log: Any, events: Any) -> None:
        self._event_log = event_log
        self._events = events

    async def handle(self, observation: dict[str, Any]) -> None:
        payload = observation.get("payload")
        if (
            observation.get("type") != "messaging.sent"
            or not isinstance(payload, dict)
            or payload.get("provider") != "bluebubbles"
            or payload.get("direction") != "outgoing"
            or not isinstance(payload.get("body_text"), str)
        ):
            return
        member_id = str(payload.get("member_id") or "")
        account_id = str(payload.get("provider_account_id") or "")
        native_chat_id = str(payload.get("native_chat_id") or "")
        native_message_id = str(payload.get("native_message_id") or "")
        if not all((member_id, account_id, native_chat_id, native_message_id)):
            return
        observed_participants = sorted(
            str(value.get("participant_id") or "")
            for value in payload.get("participants") or []
            if isinstance(value, dict) and value.get("participant_id")
        )
        participant_fingerprint = canonical_sha256(observed_participants)
        observed_body_fingerprint = body_sha256(payload["body_text"])

        terminal_by_command: dict[str, dict[str, Any]] = {}
        async for event in self._event_log.read_since(
            owner_scope=f"private:{member_id}",
            types=["external_sync.write_completed", "external_sync.write_failed"],
        ):
            terminal_by_command[str(event["payload"].get("command_id") or "")] = event

        candidates: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        async for command in self._event_log.read_since(
            owner_scope=f"private:{member_id}", types=["external_sync.write_requested"]
        ):
            command_payload = command.get("payload") or {}
            nested = command_payload.get("payload")
            if (
                command_payload.get("provider") != "bluebubbles"
                or command_payload.get("operation") != "reply"
                or command_payload.get("provider_account_id") != account_id
                or not isinstance(nested, dict)
                or nested.get("contract") != REPLY_CONTRACT
                or nested.get("native_chat_id") != native_chat_id
                or nested.get("body_fingerprint") != observed_body_fingerprint
                or nested.get("participant_fingerprint") != participant_fingerprint
            ):
                continue
            command_id = str(command_payload.get("command_id") or "")
            terminal = terminal_by_command.get(command_id)
            if terminal is None:
                continue
            terminal_payload = terminal.get("payload")
            result = (
                terminal_payload.get("result")
                if isinstance(terminal_payload, dict)
                else None
            )
            if (
                not isinstance(terminal_payload, dict)
                or terminal.get("type") != "external_sync.write_completed"
                or terminal_payload.get("suppressed") is True
                or not isinstance(result, dict)
                or result.get("outcome") not in {"accepted", "sent", "ambiguous"}
            ):
                continue
            external_id = terminal_payload.get("external_id")
            if external_id:
                if external_id != native_message_id:
                    continue
                basis = "native_message_id"
            else:
                basis = "single_exact_chat_body_participants"
            reconciled = await self._event_log.get_by_idempotency(
                str(observation["tenant_id"]), f"messages-reply-reconciled:{command_id}"
            )
            if reconciled is None:
                candidates.append((command, terminal, basis))
        if len(candidates) != 1:
            return
        command, terminal, basis = candidates[0]
        command_payload = command["payload"]
        nested = command_payload["payload"]
        command_id = str(command_payload["command_id"])
        now = EventEnvelope.now_utc_iso()
        common = {
            "command_id": command_id,
            "member_id": member_id,
            "interaction_id": str(nested["interaction_id"]),
            "command_event_id": str(command["event_id"]),
            "result_event_id": str(terminal["event_id"]),
            "observation_event_id": str(observation["event_id"]),
            "native_message_id": native_message_id,
            "match_basis": basis,
            "observed_at": str(payload.get("observed_at") or now),
        }
        observed_event_id = await self._events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=str(observation["tenant_id"]),
                type="messaging.reply.observed",
                schema_version=1,
                occurred_at=now,
                source_adapter="apple-messages-reply-reconciliation",
                source_account_id=account_id,
                owner_scope=f"private:{member_id}",
                visibility_scope=f"private:{member_id}",
                sensitivity="sensitive",
                causation_id=str(observation["event_id"]),
                idempotency_key=f"messages-reply-observed:{command_id}:{observation['event_id']}",
                payload=common,
            )
        )
        await self._events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000) + 1,
                tenant_id=str(observation["tenant_id"]),
                type="messaging.reply.reconciled",
                schema_version=1,
                occurred_at=now,
                source_adapter="apple-messages-reply-reconciliation",
                source_account_id=account_id,
                owner_scope=f"private:{member_id}",
                visibility_scope=f"private:{member_id}",
                sensitivity="sensitive",
                causation_id=observed_event_id,
                idempotency_key=f"messages-reply-reconciled:{command_id}",
                payload={
                    **common,
                    "observed_event_id": observed_event_id,
                    "outcome": "reconciled",
                    "reconciled_at": now,
                },
            )
        )


__all__ = ["MessagesReplyReconciler"]
