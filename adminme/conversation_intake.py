"""Thin authenticated Gmail input adapter to the existing OpenClaw chat relay.

No model router, scheduler or provider sender lives here. Native provider
receipts provide identity; OpenClaw owns execution, transcript and final reply.
The durable start marker prevents automatic redispatch after an unknown result.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any

from fastapi import HTTPException

from adminme.request_actions_control import request_actions_allowed
from adminme.conversation_instructions import (
    capture_instruction,
    gmail_binding,
    gmail_source_member,
    input_ticket,
)
from adminme.events.envelope import EventEnvelope
from adminme.member_chat import MemberChatError
from adminme.openclaw_topology import member_agent_id
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID, service_private_scope


class GmailConversationIntake:
    CONSUMER_VERSION = "gmail-conversation-v1"
    ACTIVATION_KEY = "cos-gmail-conversation:activation-v1"

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.activated_at: datetime | None = None
        # Envelope occurred_at has second precision; compare at that same precision.
        self.started_at = datetime.now(timezone.utc).replace(microsecond=0)

    async def initialize(self, activation_at: str | None = None) -> None:
        runtime = self.runtime
        # No new channel account is provisioned. This only binds already enrolled
        # CoS Gmail sources; historical inbox/queued work is not released.
        if not any(
            channel.provider == "gmail"
            and channel.enrolled
            and channel.service_principal_id == COS_SERVICE_PRINCIPAL_ID
            for channel in runtime.settings.service_channel_accounts
        ):
            return
        existing = await runtime.event_log.get_by_idempotency(
            runtime.settings.instance.tenant_id, self.ACTIVATION_KEY
        )
        if existing is None:
            # Updater candidate startup precedes ObservationManager construction.
            # Never write a new schema until supported normal-operation restore.
            if (
                activation_at is None
                or datetime.fromisoformat(activation_at) < self.started_at
                or runtime.observation is None
                or await runtime.observation.is_active()
                or not request_actions_allowed(runtime)
            ):
                return
            now = EventEnvelope.now_utc_iso()
            await runtime.events.write(
                EventEnvelope(
                    event_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="cos.conversation.activation",
                    schema_version=1,
                    occurred_at=now,
                    source_adapter="cos-conversation",
                    source_account_id=COS_SERVICE_PRINCIPAL_ID,
                    owner_scope=service_private_scope(COS_SERVICE_PRINCIPAL_ID),
                    visibility_scope=service_private_scope(COS_SERVICE_PRINCIPAL_ID),
                    actor_identity=COS_SERVICE_PRINCIPAL_ID,
                    idempotency_key=self.ACTIVATION_KEY,
                    payload={
                        "activated_at": activation_at,
                        "channel": "gmail",
                        "rule": "new_authenticated_instructions_only",
                    },
                )
            )
            existing = await runtime.event_log.get_by_idempotency(
                runtime.settings.instance.tenant_id, self.ACTIVATION_KEY
            )
        self.activated_at = datetime.fromisoformat(existing["payload"]["activated_at"])

    async def _receipt(
        self, instruction: dict[str, Any], state: str, reason: str
    ) -> None:
        key = f"cos-conversation-delivery:{instruction['event_id']}:{state}"
        if (
            await self.runtime.event_log.get_by_idempotency(
                instruction["tenant_id"], key
            )
            is not None
        ):
            return
        now = EventEnvelope.now_utc_iso()
        await self.runtime.events.write(
            EventEnvelope(
                event_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
                tenant_id=instruction["tenant_id"],
                type="cos.conversation.delivery",
                schema_version=1,
                occurred_at=now,
                source_adapter="cos-conversation",
                source_account_id=instruction["source_account_id"],
                owner_scope=instruction["owner_scope"],
                visibility_scope=instruction["visibility_scope"],
                sensitivity=instruction["sensitivity"],
                actor_identity=COS_SERVICE_PRINCIPAL_ID,
                causation_id=instruction["event_id"],
                correlation_id=instruction.get("correlation_id"),
                idempotency_key=key,
                payload={
                    "instruction_event_id": instruction["event_id"],
                    "state": state,
                    "reason": reason,
                    "recorded_at": now,
                },
            )
        )

    async def handle(self, event: dict[str, Any]) -> None:
        runtime = self.runtime
        if not request_actions_allowed(runtime):
            return
        if event["type"] in {"cos.channels.activated", "observation.disabled"}:
            if self.activated_at is None:
                await self.initialize(event["occurred_at"])
            if self.activated_at is None:
                return
            # Resume only new feature-owned inputs held by maintenance. Never
            # release historical provider commands or an ambiguous started run.
            async for row in runtime.event_log.read_since(
                types=["cos.conversation.delivery"]
            ):
                if (
                    row["type"] == "cos.conversation.delivery"
                    and row["payload"]["state"] == "awaiting_live"
                ):
                    instruction = await runtime.event_log.get(
                        row["payload"]["instruction_event_id"]
                    )
                    if instruction is not None:
                        await self._dispatch(instruction)
            return
        if self.activated_at is None:
            return
        member = gmail_source_member(runtime, event)
        if member is None:
            return
        payload = event["payload"]
        try:
            received = datetime.fromisoformat(
                str(payload.get("received_at") or event["occurred_at"]).replace(
                    "Z", "+00:00"
                )
            )
        except ValueError:
            return
        if received.tzinfo is None or received < self.activated_at:
            return
        if not 0 <= (datetime.now(timezone.utc) - received).total_seconds() <= 3600:
            return
        account, thread = payload["provider_account_id"], payload.get("thread_id")
        native_id = payload.get("native_message_id")
        if (
            not isinstance(thread, str)
            or not thread
            or not isinstance(native_id, str)
            or not native_id
        ):
            return
        actor, session = gmail_binding(runtime, member, account, thread)
        request_id = hashlib.sha256(f"gmail|{account}|{native_id}".encode()).hexdigest()
        try:
            instruction = await capture_instruction(
                runtime,
                member_id=member,
                text="",
                request_id=request_id,
                source=event,
                provenance={
                    "channel_id": "gmail",
                    "account_id": account,
                    "actor_id": actor,
                    "agent_id": member_agent_id(member),
                    "session_key": session,
                    "message_id": native_id,
                    "thread_id": thread,
                    "correlation_id": event["event_id"],
                },
            )
        except HTTPException:
            return
        await self._dispatch(instruction)

    async def _dispatch(self, instruction: dict[str, Any]) -> None:
        runtime = self.runtime
        identity = instruction["event_id"]
        if not request_actions_allowed(
            runtime, issued_at=instruction["payload"]["submitted_at"]
        ):
            await self._receipt(instruction, "expired", "request_authority_revoked")
            return
        # A run that may have executed is never silently rerun. The same member
        # can inspect/modify/cancel its retained task through any genuine channel.
        started = await runtime.event_log.get_by_idempotency(
            instruction["tenant_id"], f"cos-conversation-delivery:{identity}:running"
        )
        if started is not None:
            return
        age = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(instruction["payload"]["submitted_at"])
        ).total_seconds()
        if not 0 <= age <= 3600:
            await self._receipt(instruction, "expired", "instruction_expired")
            return
        if runtime.observation is None or await runtime.observation.is_active():
            await self._receipt(instruction, "awaiting_live", "maintenance_observation")
            return
        await self._receipt(instruction, "running", "submitted_to_openclaw")
        try:
            final = False
            async for chunk in runtime.member_chat.stream(
                session_key=instruction["payload"]["session_key"],
                message=instruction["payload"]["text"],
                idempotency_key=f"conversation:{identity}",
                instruction_ticket=input_ticket(runtime, instruction),
            ):
                if chunk.kind == "final":
                    final = True
            await self._receipt(
                instruction,
                "answered" if final else "unknown",
                "agent_finished_reply_separately_audited"
                if final
                else "agent_outcome_unknown",
            )
        except (MemberChatError, TimeoutError, OSError):
            await self._receipt(instruction, "unknown", "agent_outcome_unknown")
