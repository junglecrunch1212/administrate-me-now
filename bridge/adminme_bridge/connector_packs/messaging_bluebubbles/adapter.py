"""Read-only Apple Messages ingestion through pinned BlueBubbles Server REST."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Literal, Mapping

from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.events.envelope import EventEnvelope
from adminme_bridge.adapters.base import AdapterStatus, BridgeContext
from adminme_bridge.adapters.sinks import BridgeEventSink, IngestClientBridgeSink
from adminme_bridge.connector_packs.messaging_bluebubbles.transport import (
    SELECTED_ARM64_DMG_SHA256,
    SELECTED_ARM64_DMG_SIZE,
    SELECTED_RELEASE_URL,
    SELECTED_TRANSPORT_COMMIT,
    SELECTED_TRANSPORT_VERSION,
    BlueBubblesAuthenticationError,
    BlueBubblesReadTransport,
    BlueBubblesRestTransport,
    BlueBubblesTransportError,
    MessagePage,
)


_ACCOUNT_PATTERN = re.compile(r"^provider-account:[0-9a-f]{32,64}$")
_PROTECTED_SOURCE_PATTERN = re.compile(r"^protected-source:[0-9a-f]{64}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_NATIVE_ID = 240
_MAX_CHAT_ID = 500
_MAX_PARTICIPANT_ID = 500
_MAX_ATTACHMENT_ID = 500
_MAX_ATTACHMENT_SIZE = 1 << 50
_SELF_PARTICIPANT_PREFIX = "self:"


class _RecordRejected(ValueError):
    def __init__(
        self,
        reason: str,
        *,
        kind: Literal["api_changed", "validation", "malformed"] = "validation",
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind: Literal["api_changed", "validation", "malformed"] = kind


@dataclass(frozen=True, order=True)
class _Position:
    date_ms: int
    guid: str

    def dump(self) -> str:
        return json.dumps(
            {"date_ms": self.date_ms, "guid": self.guid},
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def load(cls, value: str | None) -> _Position | None:
        if value is None:
            return None
        try:
            decoded = json.loads(value)
            position = cls(int(decoded["date_ms"]), str(decoded["guid"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BlueBubblesTransportError("Messages cursor was malformed") from exc
        if position.date_ms < 0 or len(position.guid) > _MAX_NATIVE_ID:
            raise BlueBubblesTransportError("Messages cursor was outside safe bounds")
        return position


@dataclass
class _ScanCounts:
    requested: int = 0
    accepted: int = 0
    duplicates: int = 0
    quarantined: int = 0
    failed: int = 0
    missing: int = 0

    @property
    def accounted(self) -> int:
        return (
            self.accepted
            + self.duplicates
            + self.quarantined
            + self.failed
            + self.missing
        )


@dataclass(frozen=True)
class _DecodedMessage:
    event_type: Literal["messaging.received", "messaging.sent"]
    native_id: str
    position: _Position
    revision_ms: int
    fingerprint: str
    payload: dict[str, Any]


class BlueBubblesMessagesAdapter:
    """Observe Messages facts; never send, mutate, download, OCR, or transcribe."""

    subkind: ClassVar[Literal["communication-source"]] = "communication-source"
    name: ClassVar[str] = "messaging-bluebubbles"
    provider: ClassVar[str] = "bluebubbles"

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        transport: BlueBubblesReadTransport | None = None,
        cursor_store: AdapterCursorStore | None = None,
    ) -> None:
        self._config = dict(config or {})
        forbidden = {
            key
            for key in self._config
            if any(
                fragment in key.casefold()
                for fragment in ("password", "secret", "token")
            )
        }
        if forbidden:
            raise ValueError("Messages bridge config cannot contain credential values")
        self._provider_account_id = self._required_pattern(
            "provider_account_id", _ACCOUNT_PATTERN
        )
        self._protected_source_id = self._required_pattern(
            "protected_source_id", _PROTECTED_SOURCE_PATTERN
        )
        self._device_identity_sha256 = self._required_pattern(
            "device_identity_sha256", _SHA256_PATTERN
        )
        self._member_id = self._config.get("member_id")
        if not isinstance(self._member_id, str) or not self._member_id:
            raise ValueError("Messages bridge requires an exact member_id")
        self._bridge_enrollment_id = self._config.get("bridge_enrollment_id")
        if (
            not isinstance(self._bridge_enrollment_id, str)
            or not self._bridge_enrollment_id
        ):
            raise ValueError("Messages bridge requires an exact bridge_enrollment_id")
        if self._config.get("transport_version") != SELECTED_TRANSPORT_VERSION or (
            self._config.get("transport_commit") != SELECTED_TRANSPORT_COMMIT
        ):
            raise ValueError("Messages bridge transport pin is incompatible")
        credential_ref = self._config.get("credential_ref")
        if not isinstance(credential_ref, str) or not credential_ref.startswith(
            "keychain://"
        ):
            raise ValueError("Messages bridge requires a local Keychain credential_ref")
        self._history_limit = self._bounded_int(
            "history_limit", 500, minimum=1, maximum=1000
        )
        self._page_size = self._bounded_int("page_size", 250, minimum=1, maximum=1000)
        self._reconcile_limit = self._bounded_int(
            "reconcile_limit", 250, minimum=1, maximum=1000
        )
        self._reconcile_lookback_ms = self._bounded_int(
            "reconcile_lookback_ms",
            7 * 24 * 60 * 60 * 1000,
            minimum=60_000,
            maximum=31 * 24 * 60 * 60 * 1000,
        )
        self._max_body_chars = self._bounded_int(
            "max_body_chars", 50_000, minimum=1, maximum=50_000
        )
        self._poll_interval_s = float(
            self._config.get(
                "poll_interval_seconds", self._config.get("poll_interval_s", 15.0)
            )
        )
        if self._poll_interval_s < 2 or self._poll_interval_s > 300:
            raise ValueError(
                "Messages bridge poll_interval_seconds must be in [2, 300]"
            )
        self._network_containment_verified = (
            self._config.get("network_containment_verified", False) is True
        )
        self._remote_services_verified_disabled = (
            self._config.get("remote_services_verified_disabled", False) is True
        )
        self._configured_computer_sha256 = self._config.get(
            "transport_computer_id_sha256"
        )
        if self._configured_computer_sha256 is not None and (
            not isinstance(self._configured_computer_sha256, str)
            or _SHA256_PATTERN.fullmatch(self._configured_computer_sha256) is None
        ):
            raise ValueError("transport_computer_id_sha256 must be a SHA-256 digest")

        self._transport = transport or BlueBubblesRestTransport(
            base_url=str(self._config.get("base_url") or "http://127.0.0.1:1234"),
            credential_ref=credential_ref,
        )
        self._owns_transport = transport is None
        self._cursor_store = cursor_store
        self._owns_cursor_store = cursor_store is None
        self._status = AdapterStatus(
            extra={
                "read_only": True,
                "write_capabilities": [],
                "transport_version": SELECTED_TRANSPORT_VERSION,
                "transport_commit": SELECTED_TRANSPORT_COMMIT,
                "selected_arm64_dmg_sha256": SELECTED_ARM64_DMG_SHA256,
                "selected_arm64_dmg_size": SELECTED_ARM64_DMG_SIZE,
                "selected_release_url": SELECTED_RELEASE_URL,
                "network_containment": (
                    "verified" if self._network_containment_verified else "unverified"
                ),
                "remote_services_disabled": (
                    "verified"
                    if self._remote_services_verified_disabled
                    else "unverified"
                ),
                "coverage": "unknown",
                "coverage_mode": "bounded_best_effort",
                "history_truncated": False,
                "edits": "unavailable_over_rest_polling",
                "retractions": "unavailable_over_rest_polling",
                "reactions": "unavailable",
                "system_records": "quarantined",
                "attachment_content": "disabled",
                "requested": 0,
                "accepted": 0,
                "duplicates": 0,
                "quarantined": 0,
                "failed": 0,
                "missing": 0,
            }
        )
        self._stop = asyncio.Event()
        self._probe_verified = False
        coordinate_material = (
            f"{self.name}|{self._member_id}|{self._bridge_enrollment_id}|"
            f"{self._device_identity_sha256}|{self._provider_account_id}|"
            f"{self._protected_source_id}"
        )
        self._adapter_id = (
            f"{self.name}:"
            + hashlib.sha256(coordinate_material.encode("utf-8")).hexdigest()
        )
        self._lease_owner = f"bridge-messages:{uuid.uuid4()}"
        self._chat_cache: dict[str, Mapping[str, Any]] = {}

    def _required_pattern(self, key: str, pattern: re.Pattern[str]) -> str:
        value = self._config.get(key)
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise ValueError(f"Messages bridge requires valid {key}")
        return value

    def _bounded_int(
        self, key: str, default: int, *, minimum: int, maximum: int
    ) -> int:
        value = self._config.get(key, default)
        if isinstance(value, bool):
            raise ValueError(f"Messages bridge {key} must be an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Messages bridge {key} must be an integer") from exc
        if parsed < minimum or parsed > maximum:
            raise ValueError(f"Messages bridge {key} must be in [{minimum}, {maximum}]")
        return parsed

    def status(self) -> AdapterStatus:
        return self._status

    def _store(self) -> AdapterCursorStore:
        if self._cursor_store is None:
            cursor_path = Path(
                str(
                    self._config.get("cursor_db_path")
                    or "~/.adminme-bridge/adapter-cursors.db"
                )
            ).expanduser()
            self._cursor_store = AdapterCursorStore(cursor_path)
        return self._cursor_store

    async def start(self, *, ctx: BridgeContext) -> None:
        self._status.started_at = ctx.now_factory()
        try:
            while not self._stop.is_set():
                try:
                    await self.observe_once(ctx)
                    self._status.state = self._healthy_state()
                    self._status.last_error = None
                except BlueBubblesAuthenticationError:
                    self._status.state = "crashed"
                    self._status.last_error = {
                        "kind": "auth_failed",
                        "message": "BlueBubbles authentication failed",
                    }
                    await self._emit_adapter_error(ctx, "auth_failed", fatal=True)
                    raise
                except (BlueBubblesTransportError, RuntimeError):
                    cursor = self._store().record_error(
                        self._adapter_id,
                        open_after=3,
                        recovery_after_ms=30_000,
                    )
                    self._status.state = "backoff"
                    self._status.restart_count = cursor.error_count
                    self._status.last_error = {
                        "kind": "network",
                        "message": "Messages observation temporarily unavailable",
                    }
                    await self._emit_adapter_error(ctx, "network", fatal=False)
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._poll_interval_s
                    )
                except TimeoutError:
                    pass
        finally:
            self._store().release(self._adapter_id, self._lease_owner)
            if self._owns_transport:
                await self._transport.aclose()
            if self._owns_cursor_store and self._cursor_store is not None:
                self._cursor_store.close()

    async def stop(self) -> None:
        self._stop.set()

    async def observe_once(self, ctx: BridgeContext) -> list[str]:
        """Run one deterministic bounded history/live observation cycle."""

        if (
            ctx.member_id != self._member_id
            or ctx.enrollment_id != self._bridge_enrollment_id
            or ctx.device_identity_sha256 != self._device_identity_sha256
        ):
            raise BlueBubblesTransportError(
                "Messages bridge context did not match its sealed enrollment"
            )
        store = self._store()
        if not store.claim(self._adapter_id, self._lease_owner, ttl_ms=120_000):
            self._status.state = "backoff"
            self._status.extra["reason"] = "cursor lease held or circuit open"
            return []
        try:
            if not self._probe_verified:
                await self._verify_server()
                self._probe_verified = True
            self._chat_cache.clear()
            cursor = _Position.load(store.get(self._adapter_id).provider_watermark)
            if cursor is None:
                event_ids, cursor = await self._capture_history(ctx)
            else:
                event_ids, cursor = await self._capture_live(ctx, cursor)
                event_ids.extend(await self._reconcile_recent(ctx, cursor))
            store.record_success(
                self._adapter_id,
                lease_owner=self._lease_owner,
                provider_watermark=cursor.dump(),
            )
            self._status.state = self._healthy_state()
            self._status.last_cursor_advance_at = datetime.fromtimestamp(
                cursor.date_ms / 1000, tz=timezone.utc
            )
            self._status.extra["cursor_watermark_ms"] = cursor.date_ms
            return event_ids
        finally:
            store.release(self._adapter_id, self._lease_owner)

    async def _verify_server(self) -> None:
        info = await self._transport.server_info()
        if info.get("server_version") != SELECTED_TRANSPORT_VERSION:
            raise BlueBubblesTransportError(
                "BlueBubbles version is incompatible with the reviewed transport"
            )
        if info.get("private_api") is not False:
            raise BlueBubblesTransportError(
                "BlueBubbles Private API must be disabled for read-only ingestion"
            )
        computer_id = info.get("computer_id")
        if not isinstance(computer_id, str) or not computer_id:
            raise BlueBubblesTransportError("BlueBubbles omitted its computer identity")
        observed = hashlib.sha256(computer_id.encode("utf-8")).hexdigest()
        if observed != self._protected_source_id.removeprefix("protected-source:"):
            raise BlueBubblesTransportError(
                "BlueBubbles source identity did not match enrollment"
            )
        if self._configured_computer_sha256 is not None:
            if observed != self._configured_computer_sha256:
                raise BlueBubblesTransportError(
                    "BlueBubbles computer identity did not match enrollment"
                )

    def _healthy_state(self) -> Literal["running", "degraded"]:
        if (
            self._network_containment_verified
            and self._remote_services_verified_disabled
        ):
            return "running"
        return "degraded"

    async def _capture_history(self, ctx: BridgeContext) -> tuple[list[str], _Position]:
        # Capture the live boundary first. History is then read only through
        # that boundary, so messages arriving during the scan remain live work.
        latest = await self._transport.query_messages(sort="DESC", limit=1)
        if not latest.messages:
            boundary = _Position(0, "")
            page = MessagePage((), 0, self._history_limit, 0)
        else:
            boundary = self._position(latest.messages[0])
            page = await self._transport.query_messages(
                sort="DESC",
                limit=self._history_limit,
                before_ms=max(1, boundary.date_ms),
            )
        records = [
            record
            for record in page.messages
            if self._safe_position(record) <= boundary
        ]
        records.sort(key=self._safe_position)
        missing = max(0, page.total - len(page.messages))
        counts = _ScanCounts(requested=len(records) + missing, missing=missing)
        event_ids = await self._process_records(ctx, records, counts)
        truncated = missing > 0
        event_ids.append(
            await self._emit_coverage(
                ctx,
                phase="history",
                counts=counts,
                truncated=truncated,
                watermark=boundary,
            )
        )
        self._status.extra["history_boundary_ms"] = boundary.date_ms
        self._status.extra["history_truncated"] = truncated
        self._status.extra["coverage"] = "partial"
        return event_ids, boundary

    async def _capture_live(
        self, ctx: BridgeContext, cursor: _Position
    ) -> tuple[list[str], _Position]:
        page = await self._transport.query_messages(
            sort="ASC", limit=self._page_size, after_ms=cursor.date_ms
        )
        records = sorted(page.messages, key=self._safe_position)
        missing = max(0, page.total - len(page.messages))
        counts = _ScanCounts(requested=len(records) + missing, missing=missing)
        event_ids = await self._process_records(ctx, records, counts)
        next_cursor = max([cursor, *(self._safe_position(item) for item in records)])
        truncated = missing > 0
        event_ids.append(
            await self._emit_coverage(
                ctx,
                phase="live",
                counts=counts,
                truncated=truncated,
                watermark=next_cursor,
            )
        )
        self._status.extra["live_truncated"] = truncated
        self._status.extra["coverage"] = "partial"
        return event_ids, next_cursor

    async def _reconcile_recent(
        self, ctx: BridgeContext, cursor: _Position
    ) -> list[str]:
        if cursor.date_ms == 0:
            return []
        page = await self._transport.query_messages(
            sort="DESC",
            limit=self._reconcile_limit,
            after_ms=max(0, cursor.date_ms - self._reconcile_lookback_ms),
            before_ms=max(1, cursor.date_ms),
        )
        records = sorted(page.messages, key=self._safe_position)
        missing = max(0, page.total - len(page.messages))
        counts = _ScanCounts(requested=len(records) + missing, missing=missing)
        event_ids = await self._process_records(ctx, records, counts)
        event_ids.append(
            await self._emit_coverage(
                ctx,
                phase="reconcile",
                counts=counts,
                truncated=missing > 0,
                watermark=cursor,
            )
        )
        self._status.extra["reconcile_truncated"] = missing > 0
        return event_ids

    async def _process_records(
        self,
        ctx: BridgeContext,
        records: list[Mapping[str, Any]],
        counts: _ScanCounts,
    ) -> list[str]:
        event_ids: list[str] = []
        for record in records:
            if not self._store().claim(
                self._adapter_id, self._lease_owner, ttl_ms=120_000
            ):
                raise BlueBubblesTransportError("Messages cursor lease was lost")
            try:
                decoded = await self._decode(ctx, record)
            except _RecordRejected as exc:
                outcome, event_id = await self._quarantine(
                    ctx, record, kind=exc.kind, reason=exc.reason
                )
                setattr(counts, outcome, getattr(counts, outcome) + 1)
                if event_id is not None:
                    event_ids.append(event_id)
                continue
            duplicate = self._store().dedupe_event(
                self._adapter_id, decoded.native_id, decoded.fingerprint
            )
            if duplicate is not None:
                counts.duplicates += 1
                continue
            latest = self._store().latest_event(self._adapter_id, decoded.native_id)
            if latest is not None:
                prior_revision = self._revision_from_fingerprint(latest[0])
                if decoded.revision_ms < prior_revision:
                    outcome, event_id = await self._quarantine(
                        ctx,
                        record,
                        kind="validation",
                        reason="out_of_order_native_revision",
                        separate_ledger=True,
                    )
                    setattr(counts, outcome, getattr(counts, outcome) + 1)
                    if event_id is not None:
                        event_ids.append(event_id)
                    continue
                if decoded.revision_ms == prior_revision:
                    outcome, event_id = await self._quarantine(
                        ctx,
                        record,
                        kind="validation",
                        reason="conflicting_native_revision",
                        separate_ledger=True,
                    )
                    setattr(counts, outcome, getattr(counts, outcome) + 1)
                    if event_id is not None:
                        event_ids.append(event_id)
                    continue
            try:
                event_id = await self._emit_message(ctx, decoded)
            except RuntimeError:
                counts.failed += 1
                raise
            self._store().record_success(
                self._adapter_id,
                lease_owner=self._lease_owner,
                mark_healthy=False,
                external_id=decoded.native_id,
                fingerprint=decoded.fingerprint,
                event_id=event_id,
            )
            counts.accepted += 1
            event_ids.append(event_id)
        return event_ids

    async def _decode(
        self, ctx: BridgeContext, record: Mapping[str, Any]
    ) -> _DecodedMessage:
        native_id = self._bounded_text(
            record.get("guid"), "native_message_id", _MAX_NATIVE_ID
        )
        position = self._position(record)
        if record.get("associatedMessageGuid") or record.get("associatedMessageType"):
            raise _RecordRejected("unsupported_reaction_record")
        if bool(record.get("isSystemMessage")) or (
            not record.get("text") and record.get("itemType") in {1, 2, 3}
        ):
            raise _RecordRejected("unsupported_system_record")
        if record.get("dateEdited") not in {None, 0, 0.0} or record.get(
            "dateRetracted"
        ) not in {None, 0, 0.0}:
            # v1.9.9's REST query cursor is creation-date based and does not
            # carry the messageSummaryInfo needed for reliable unsend parsing.
            # A stale/current snapshot must not be presented as a mutation
            # stream. Socket.IO support is intentionally deferred.
            raise _RecordRejected("unsupported_mutation_record")
        chats = record.get("chats")
        if (
            not isinstance(chats, list)
            or len(chats) != 1
            or not isinstance(chats[0], dict)
        ):
            raise _RecordRejected("message_chat_coordinate_missing", kind="malformed")
        native_chat_id = self._bounded_text(
            chats[0].get("guid"), "native_chat_id", _MAX_CHAT_ID
        )
        if native_chat_id == native_id:
            raise _RecordRejected("message_chat_coordinate_collision")
        chat = await self._chat_record(native_chat_id)
        style = chat.get("style")
        if style not in {43, 45}:
            raise _RecordRejected("unsupported_chat_style", kind="api_changed")
        remote_participants = self._participants(chat)
        if not remote_participants:
            raise _RecordRejected("chat_participants_missing", kind="malformed")

        is_from_me = record.get("isFromMe")
        if not isinstance(is_from_me, bool):
            raise _RecordRejected("message_direction_missing", kind="malformed")
        handle = record.get("handle")
        handle_address: str | None = None
        service: Any = None
        if isinstance(handle, dict):
            if handle.get("address") is not None:
                handle_address = self._bounded_text(
                    handle.get("address"), "sender_id", _MAX_PARTICIPANT_ID
                )
            service = handle.get("service")
        if service is None:
            service = native_chat_id.split(";", 1)[0]
        if service == "iMessage":
            transport_class = "imessage"
        elif service == "SMS":
            transport_class = "sms"
        else:
            raise _RecordRejected("unsupported_transport_class")

        self_id = _SELF_PARTICIPANT_PREFIX + self._provider_account_id
        if is_from_me:
            sender_id = self_id
        else:
            if handle_address is None or handle_address not in remote_participants:
                raise _RecordRejected("incoming_sender_not_in_chat", kind="malformed")
            sender_id = handle_address
        participant_payload = [
            {
                "participant_id": value,
                "role": (
                    "sender" if not is_from_me and value == sender_id else "recipient"
                ),
                "is_self": False,
            }
            for value in remote_participants
        ]
        participant_payload.append(
            {
                "participant_id": self_id,
                "role": "self",
                "is_self": True,
            }
        )

        text = record.get("text")
        if text is not None and not isinstance(text, str):
            raise _RecordRejected("message_text_was_not_text", kind="malformed")
        if isinstance(text, str) and len(text) > self._max_body_chars:
            raise _RecordRejected("message_body_exceeded_safety_limit")
        attachments = self._attachments(native_id, record.get("attachments"))
        if not text and not attachments and not record.get("dateRetracted"):
            raise _RecordRejected("message_had_no_observable_content", kind="malformed")
        reply_id = record.get("replyToGuid")
        if reply_id is not None:
            reply_id = self._bounded_text(
                reply_id, "reply_to_native_message_id", _MAX_CHAT_ID
            )
        revision_candidates = [position.date_ms]
        for key in ("dateEdited", "dateRetracted", "dateDelivered", "dateRead"):
            value = record.get(key)
            if value is not None:
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or value < 0
                ):
                    raise _RecordRejected(
                        "message_revision_was_malformed", kind="malformed"
                    )
                revision_candidates.append(int(value))
        revision_ms = max(revision_candidates)
        message_state: Literal["active", "edited", "retracted"] = "active"

        canonical = json.dumps(
            record,
            default=lambda _value: "<unsupported>",
            separators=(",", ":"),
            sort_keys=True,
        )
        content_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        fingerprint = f"{revision_ms:020d}:{content_sha256}"
        observed_at = ctx.now_factory().astimezone(timezone.utc).isoformat()
        occurred_at = datetime.fromtimestamp(
            position.date_ms / 1000, tz=timezone.utc
        ).isoformat()
        correlation_id = ctx.correlation_id_factory()
        common: dict[str, Any] = {
            "member_id": ctx.member_id,
            "service_principal_id": None,
            "provider": "bluebubbles",
            "provider_account_id": self._provider_account_id,
            "workspace": "personal",
            "routing_scope": "member_private",
            "routing_target_id": ctx.member_id,
            "source_channel": transport_class,
            "thread_id": native_chat_id,
            "subject": self._optional_text(record.get("subject"), 2_000),
            "body_text": text,
            "native_message_id": native_id,
            "rfc_message_id": None,
            "references": [reply_id] if reply_id else [],
            "in_reply_to": reply_id,
            "bridge_enrollment_id": ctx.enrollment_id,
            "device_identity_sha256": self._device_identity_sha256,
            "connector_id": self.name,
            "protected_source_id": self._protected_source_id,
            "native_chat_id": native_chat_id,
            "native_revision": f"{revision_ms}:{content_sha256[:32]}",
            "native_revision_ms": revision_ms,
            "participants": participant_payload,
            "sender_id": sender_id,
            "observed_at": observed_at,
            "reply_to_native_message_id": reply_id,
            "transport_class": transport_class,
            "is_group": style == 43,
            "message_state": message_state,
            "attachments": attachments,
            "transport_version": SELECTED_TRANSPORT_VERSION,
            "transport_commit": SELECTED_TRANSPORT_COMMIT,
            "correlation_id": correlation_id,
        }
        if is_from_me:
            payload = {
                **common,
                "to_identifier": remote_participants[0],
                "sent_at": occurred_at,
                "delivery_status": self._delivery_status(record),
                "in_reply_to_interaction_id": None,
                "body_html": None,
                "send_as": "member",
                "direction": "outgoing",
            }
            event_type: Literal["messaging.received", "messaging.sent"] = (
                "messaging.sent"
            )
        else:
            payload = {
                **common,
                "from_identifier": sender_id,
                "to_identifier": self_id,
                "received_at": occurred_at,
                "body_html": None,
                "to_identifiers": [self_id],
                "cc_identifiers": [],
                "bcc_identifiers": [],
                "reply_to_identifier": None,
                "provider_labels": [],
                "unread": not bool(record.get("dateRead")),
                "archived": bool(record.get("isArchived")),
                "starred": False,
                "important": False,
                "direction": "incoming",
            }
            event_type = "messaging.received"
        return _DecodedMessage(
            event_type=event_type,
            native_id=native_id,
            position=position,
            revision_ms=revision_ms,
            fingerprint=fingerprint,
            payload=payload,
        )

    async def _chat_record(self, native_chat_id: str) -> Mapping[str, Any]:
        if native_chat_id not in self._chat_cache:
            chat = await self._transport.chat(native_chat_id)
            if chat.get("guid") != native_chat_id:
                raise _RecordRejected("chat_identity_mismatch", kind="malformed")
            self._chat_cache[native_chat_id] = chat
        return self._chat_cache[native_chat_id]

    def _participants(self, chat: Mapping[str, Any]) -> list[str]:
        raw = chat.get("participants")
        if not isinstance(raw, list) or len(raw) > 99:
            raise _RecordRejected("chat_participants_were_malformed", kind="malformed")
        participants: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                raise _RecordRejected(
                    "chat_participant_was_malformed", kind="malformed"
                )
            value = self._bounded_text(
                item.get("address"), "participant_id", _MAX_PARTICIPANT_ID
            )
            if value.startswith(_SELF_PARTICIPANT_PREFIX):
                raise _RecordRejected("participant_spoofed_local_endpoint")
            participants.add(value)
        return sorted(participants)

    def _attachments(self, native_id: str, raw: Any) -> list[dict[str, Any]]:
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > 100:
            raise _RecordRejected("attachment_list_was_malformed", kind="malformed")
        attachments: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                raise _RecordRejected("attachment_was_malformed", kind="malformed")
            if any(key in item for key in ("data", "metadata", "filePath", "path")):
                raise _RecordRejected("attachment_content_crossed_metadata_boundary")
            attachment_id = self._bounded_text(
                item.get("guid"), "attachment_id", _MAX_ATTACHMENT_ID
            )
            if attachment_id in seen:
                raise _RecordRejected("duplicate_attachment_identity")
            seen.add(attachment_id)
            byte_size = item.get("totalBytes")
            if byte_size is not None and (
                isinstance(byte_size, bool)
                or not isinstance(byte_size, (int, float))
                or int(byte_size) < 0
                or int(byte_size) > _MAX_ATTACHMENT_SIZE
            ):
                raise _RecordRejected("attachment_size_was_malformed", kind="malformed")
            protected_seed = f"{self._protected_source_id}|{native_id}|{attachment_id}"
            transfer_name = item.get("transferName")
            if isinstance(transfer_name, str) and (
                "/" in transfer_name or "\\" in transfer_name
            ):
                raise _RecordRejected("attachment_filename_contained_a_path")
            attachments.append(
                {
                    "attachment_id": attachment_id,
                    "mime_type": self._optional_text(item.get("mimeType"), 240),
                    "uti": self._optional_text(item.get("uti"), 240),
                    "byte_size": int(byte_size) if byte_size is not None else None,
                    "filename": self._optional_text(transfer_name, 500),
                    "transfer_state": self._optional_text(
                        item.get("transferState"), 120, coerce_number=True
                    ),
                    "is_outgoing": bool(item.get("isOutgoing")),
                    "is_sticker": bool(item.get("isSticker")),
                    "protected_ref": "protected-attachment:"
                    + hashlib.sha256(protected_seed.encode("utf-8")).hexdigest(),
                }
            )
        return attachments

    @staticmethod
    def _delivery_status(record: Mapping[str, Any]) -> str:
        if record.get("error") not in {None, 0, "", False}:
            return "failed"
        if bool(record.get("isDelivered")) or int(record.get("dateDelivered") or 0) > 0:
            return "sent"
        return "queued"

    @staticmethod
    def _bounded_text(value: Any, field: str, maximum: int) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > maximum
            or any(ord(character) < 32 for character in value)
        ):
            raise _RecordRejected(f"{field}_was_malformed", kind="malformed")
        return value

    @staticmethod
    def _optional_text(
        value: Any, maximum: int, *, coerce_number: bool = False
    ) -> str | None:
        if value is None:
            return None
        if (
            coerce_number
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            value = str(value)
        if (
            not isinstance(value, str)
            or len(value) > maximum
            or any(ord(character) < 32 for character in value)
        ):
            raise _RecordRejected("attachment_metadata_was_malformed", kind="malformed")
        return value or None

    def _position(self, record: Mapping[str, Any]) -> _Position:
        guid = self._bounded_text(
            record.get("guid"), "native_message_id", _MAX_NATIVE_ID
        )
        date = record.get("dateCreated")
        if isinstance(date, bool) or not isinstance(date, (int, float)) or date < 0:
            raise _RecordRejected("message_timestamp_was_malformed", kind="malformed")
        return _Position(int(date), guid)

    def _safe_position(self, record: Mapping[str, Any]) -> _Position:
        try:
            return self._position(record)
        except _RecordRejected:
            # Malformed records still need deterministic placement and durable
            # quarantine accounting; their content never enters the envelope.
            digest = self._record_digest(record)
            return _Position(0, f"malformed:{digest[:32]}")

    @staticmethod
    def _revision_from_fingerprint(fingerprint: str) -> int:
        try:
            return int(fingerprint.split(":", 1)[0])
        except (TypeError, ValueError) as exc:
            raise BlueBubblesTransportError(
                "Messages revision ledger was malformed"
            ) from exc

    @staticmethod
    def _record_digest(record: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            record,
            default=lambda _value: "<unsupported>",
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def _emit_message(self, ctx: BridgeContext, decoded: _DecodedMessage) -> str:
        occurred_at = datetime.fromtimestamp(
            decoded.position.date_ms / 1000, tz=timezone.utc
        ).isoformat()
        protected_record_ref = hashlib.sha256(
            f"{self._protected_source_id}|{decoded.native_id}".encode("utf-8")
        ).hexdigest()
        envelope = EventEnvelope(
            event_at_ms=decoded.position.date_ms,
            tenant_id=ctx.tenant_id,
            type=decoded.event_type,
            schema_version=7,
            occurred_at=occurred_at,
            source_adapter=self.name,
            source_account_id=self._provider_account_id,
            owner_scope=f"private:{ctx.member_id}",
            visibility_scope=f"private:{ctx.member_id}",
            # Message text may enter the pinned, scope-preserving OpenClaw
            # triage seam. Credentials, rejected records, and source-health
            # evidence remain content-free and privileged.
            sensitivity="sensitive",
            correlation_id=decoded.payload["correlation_id"],
            raw_ref=f"protected-message:{protected_record_ref}",
            idempotency_key=(
                f"{self.name}:{self._provider_account_id}:"
                f"{decoded.native_id}:{decoded.fingerprint}"
            ),
            payload=decoded.payload,
        )
        return await self._emit(ctx, envelope)

    async def _quarantine(
        self,
        ctx: BridgeContext,
        record: Mapping[str, Any],
        *,
        kind: Literal["api_changed", "validation", "malformed"],
        reason: str,
        separate_ledger: bool = False,
    ) -> tuple[Literal["duplicates", "quarantined"], str | None]:
        digest = self._record_digest(record)
        raw_id = record.get("guid")
        identity_seed = raw_id if isinstance(raw_id, str) and raw_id else digest
        opaque_id = (
            "native-message:"
            + hashlib.sha256(str(identity_seed).encode("utf-8")).hexdigest()
        )
        ledger_id = opaque_id + (f":rejected:{digest[:16]}" if separate_ledger else "")
        if self._store().dedupe_event(self._adapter_id, ledger_id, digest):
            return "duplicates", None
        now = ctx.now_factory().astimezone(timezone.utc)
        watermark = self._store().get(self._adapter_id).provider_watermark
        envelope = EventEnvelope(
            event_at_ms=int(now.timestamp() * 1000),
            tenant_id=ctx.tenant_id,
            type="adapter.record.quarantined",
            schema_version=1,
            occurred_at=now.isoformat(),
            source_adapter=self.name,
            source_account_id=self._provider_account_id,
            owner_scope=f"private:{ctx.member_id}",
            visibility_scope=f"private:{ctx.member_id}",
            sensitivity="privileged",
            correlation_id=ctx.correlation_id_factory(),
            idempotency_key=f"{self.name}:quarantine:{ledger_id}:{digest}",
            payload={
                "adapter_name": self.name,
                "adapter_family": "communication",
                "external_id": opaque_id,
                "content_fingerprint": digest,
                "error_kind": kind,
                "error_message": reason,
                "quarantined_at": now.isoformat(),
                "provider_watermark": watermark,
            },
        )
        event_id = await self._emit(ctx, envelope)
        self._store().record_success(
            self._adapter_id,
            lease_owner=self._lease_owner,
            mark_healthy=False,
            external_id=ledger_id,
            fingerprint=digest,
            event_id=event_id,
        )
        return "quarantined", event_id

    async def _emit_coverage(
        self,
        ctx: BridgeContext,
        *,
        phase: Literal["history", "live", "reconcile"],
        counts: _ScanCounts,
        truncated: bool,
        watermark: _Position,
    ) -> str:
        if counts.accounted != counts.requested:
            raise RuntimeError("Messages assurance counts did not reconcile")
        now = ctx.now_factory().astimezone(timezone.utc)
        coverage = "partial"
        cycle_id = ctx.correlation_id_factory()
        envelope = EventEnvelope(
            event_at_ms=int(now.timestamp() * 1000),
            tenant_id=ctx.tenant_id,
            type="messaging.source_observed",
            schema_version=1,
            occurred_at=now.isoformat(),
            source_adapter=self.name,
            source_account_id=self._provider_account_id,
            owner_scope=f"private:{ctx.member_id}",
            visibility_scope=f"private:{ctx.member_id}",
            sensitivity="privileged",
            correlation_id=cycle_id,
            idempotency_key=(
                f"{self.name}:coverage:{cycle_id}:{phase}:{watermark.date_ms}:"
                f"{counts.requested}:{counts.accepted}:{counts.duplicates}:"
                f"{counts.quarantined}:{counts.failed}:{counts.missing}"
            ),
            payload={
                "member_id": ctx.member_id,
                "bridge_enrollment_id": ctx.enrollment_id,
                "device_identity_sha256": self._device_identity_sha256,
                "connector_id": self.name,
                "provider": "bluebubbles",
                "provider_account_id": self._provider_account_id,
                "workspace": "personal",
                "protected_source_id": self._protected_source_id,
                "transport_version": SELECTED_TRANSPORT_VERSION,
                "transport_commit": SELECTED_TRANSPORT_COMMIT,
                "phase": phase,
                "coverage": coverage,
                "truncated": truncated,
                "requested": counts.requested,
                "accepted": counts.accepted,
                "duplicates": counts.duplicates,
                "quarantined": counts.quarantined,
                "failed": counts.failed,
                "missing": counts.missing,
                "watermark_ms": watermark.date_ms,
                "observed_at": now.isoformat(),
            },
        )
        event_id = await self._emit(ctx, envelope)
        for key in (
            "requested",
            "accepted",
            "duplicates",
            "quarantined",
            "failed",
            "missing",
        ):
            self._status.extra[key] = int(self._status.extra[key]) + int(
                getattr(counts, key)
            )
        return event_id

    async def _emit_adapter_error(
        self, ctx: BridgeContext, error_kind: str, *, fatal: bool
    ) -> None:
        now = ctx.now_factory().astimezone(timezone.utc)
        envelope = EventEnvelope(
            event_at_ms=int(now.timestamp() * 1000),
            tenant_id=ctx.tenant_id,
            type="adapter.error",
            schema_version=1,
            occurred_at=now.isoformat(),
            source_adapter=self.name,
            source_account_id=self._provider_account_id,
            owner_scope=f"private:{ctx.member_id}",
            visibility_scope=f"private:{ctx.member_id}",
            sensitivity="privileged",
            correlation_id=ctx.correlation_id_factory(),
            payload={
                "adapter_name": self.name,
                "adapter_family": "communication",
                "severity": "fatal" if fatal else "transient",
                "error_kind": error_kind,
                "error_message": "Messages observation failed",
                "retry_after_s": None if fatal else 30,
                "occurred_at": now.isoformat(),
            },
        )
        try:
            await self._emit(ctx, envelope)
        except RuntimeError:
            return

    @staticmethod
    async def _emit(ctx: BridgeContext, envelope: EventEnvelope) -> str:
        sink: BridgeEventSink = (
            ctx.sink
            if ctx.sink is not None
            else IngestClientBridgeSink(ctx.ingest_client)
        )
        result = await sink.emit(envelope)
        if not result.ok or result.event_id is None:
            raise RuntimeError(result.error or "bridge ingest failed")
        return result.event_id


__all__ = ["BlueBubblesMessagesAdapter"]
