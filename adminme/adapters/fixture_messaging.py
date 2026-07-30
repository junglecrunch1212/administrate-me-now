"""Credential-free Cat-A messaging adapter used by the canonical demo."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

from adminme.adapters.base import AdapterContext, AdapterStatus
from adminme.adapters.emission import write_adapter_event
from adminme.events.log import new_correlation_id
from adminme.events.envelope import Sensitivity
from adminme.lib.adapters import (
    CommunicationAdapter,
    InboundEnvelope,
    OutboundMessage,
    SendResult,
)
from adminme.lib.observation import ObservationManager, outbound
from adminme.lib.session import Session


class FixtureMessagingAdapter(CommunicationAdapter):
    name = "fixture-messaging"
    version = "1.0.0"
    capabilities: ClassVar[list[Any]] = []

    def __init__(
        self,
        *,
        event_sink: Any,
        observation_manager: ObservationManager,
        member_id: str,
        default_session: Session,
    ) -> None:
        self._ctx = AdapterContext(
            event_sink=event_sink,
            config={"member_id": member_id, "source_account_id": "fixture-account"},
            correlation_id_factory=new_correlation_id,
            now_factory=lambda: datetime.now(timezone.utc),
        )
        self._observation = observation_manager
        self._default_session = default_session
        self._status = AdapterStatus(state="running")
        self.delivered: list[OutboundMessage] = []

    async def start(self, *, ctx: AdapterContext) -> None:
        self._ctx = ctx
        self._status.state = "running"

    async def stop(self) -> None:
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    async def emit_canonical_message(
        self,
        *,
        native_message_id: str,
        sender: str,
        recipient: str,
        body: str,
        received_at: str,
        sensitivity: Sensitivity = "normal",
        correlation_id: str | None = None,
    ) -> str:
        return await write_adapter_event(
            self._ctx,
            adapter_name=self.name,
            sensitivity_floor=self.sensitivity_floor,
            config=self._ctx.config,
            event_type="messaging.received",
            schema_version=2,
            occurred_at_override=received_at,
            sensitivity_override=sensitivity,
            idempotency_key=f"fixture-message:{native_message_id}",
            raw_ref=f"fixture://messages/{native_message_id}",
            correlation_id=correlation_id,
            payload={
                "native_message_id": native_message_id,
                "source_channel": "fixture-cat-a",
                "from_identifier": sender,
                "to_identifier": recipient,
                "thread_id": f"fixture-thread:{native_message_id}",
                "subject": None,
                "body_text": body,
                "body_html": None,
                "received_at": received_at,
                "attachments": [],
            },
        )

    async def _on_inbound(self, envelope: InboundEnvelope) -> None:
        await self.emit_canonical_message(
            native_message_id=envelope.external_id,
            sender=envelope.sender,
            recipient=str(envelope.payload["to_identifier"]),
            body=str(envelope.payload["body_text"]),
            received_at=envelope.received_at,
        )

    async def send(self, message: OutboundMessage) -> SendResult:
        return await self.send_as(self._default_session, message)

    async def send_as(self, session: Session, message: OutboundMessage) -> SendResult:
        async def _deliver() -> str:
            self.delivered.append(message)
            return f"fixture-delivery:{len(self.delivered)}"

        result = await outbound(
            session,
            "communication.send",
            {"body": message.body, "metadata": message.metadata},
            _deliver,
            manager=self._observation,
            event_sink=self._ctx.event_sink,
            target_channel=message.channel,
            target_identifier=message.recipient,
        )
        return SendResult(
            delivered=not result.suppressed,
            external_id=result.result,
            suppressed=result.suppressed,
        )


__all__ = ["FixtureMessagingAdapter"]
