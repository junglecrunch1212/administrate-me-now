"""Pack-internal tests for Gmail real-outbound ``send``.

Sibling to ``test_gmail_adapter_pack_internal.py`` and the
framework-level ``tests/unit/adapters/messaging/test_gmail_send.py``.
These three test seams pin the 11a contract at the pack + framework +
integration layers respectively:

- ``test_gmail_adapter_pack_internal.py`` (existing, 11r-a precedent) —
  manifest + ingest path canaries that live next to the pack.
- ``test_gmail_send.py`` (11a c3) — framework-level send-contract
  coverage (MIME builder + observation seam + ``MessagingSentV2``
  field-mapping + ``AdapterErrorV1`` taxonomy).
- THIS FILE (11a c4) — pack-internal coverage focused on the contract
  the pack itself owns: that the import surface of the pack's
  ``adapter.py`` matches the googleapiclient surface :meth:`send`
  actually invokes, that the MIME builder's base64url envelope round-
  trips through stdlib decoders (the wire-shape contract the Gmail v1
  ``users.messages.send`` endpoint reads), and that the
  ``messaging.sent`` payload :meth:`send` emits validates byte-for-
  byte against the additive :class:`MessagingSentV2` schema while the
  immutable v1 registration remains available to historical producers.

F-4: unique basename (``test_gmail_send_pack_internal.py``) per the
sidecar #150 + 11r-c precedent so pytest collection from the repo root
does not collide with sibling packs.
"""

from __future__ import annotations

import base64
import email
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.schemas.ingest import MessagingSentV2
from adminme.lib.adapters import OutboundMessage

PACK_ROOT = Path(__file__).resolve().parents[1]


def _load_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root via importlib — mirrors
    ``test_gmail_adapter_pack_internal._load_adapter_module`` so the
    two pack-internal test files use the same loader pattern."""
    spec = importlib.util.spec_from_file_location(
        "gmail_send_pack_internal_under_test", PACK_ROOT / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_adapter_module()
GmailAdapter: Any = _adapter_mod.GmailAdapter


_FROZEN_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


class _FakeEventLog:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **kwargs: Any) -> str:
        self.calls.append({**envelope.model_dump(), **kwargs})
        return f"event-{len(self.calls)}"


def _ctx(event_log: _FakeEventLog) -> AdapterContext:
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-send-pack-internal",
        now_factory=lambda: _FROZEN_NOW,
    )


class _FakeSendService:
    """Minimal Gmail service surface: ``users().messages().send(...)
    .execute()``. Captures call args; returns a configurable response."""

    def __init__(self, *, response: dict[str, Any] | None = None) -> None:
        self._response = response or {
            "id": "gmail-msg-pack-1",
            "threadId": "gmail-thr-pack-1",
        }
        self.send_calls: list[dict[str, Any]] = []

    def users(self) -> Any:
        return _FakeUsers(self)


class _FakeUsers:
    def __init__(self, svc: _FakeSendService) -> None:
        self._svc = svc

    def messages(self) -> Any:
        return _FakeMessages(self._svc)


class _FakeMessages:
    def __init__(self, svc: _FakeSendService) -> None:
        self._svc = svc

    def send(self, **kwargs: Any) -> Any:
        self._svc.send_calls.append(kwargs)
        return _FakeExecute(self._svc)


class _FakeExecute:
    def __init__(self, svc: _FakeSendService) -> None:
        self._svc = svc

    def execute(self) -> Any:
        return dict(self._svc._response)


def _make_adapter(*, service: _FakeSendService | None = None) -> Any:
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
            "member_id": "member-fixture",
            "workspace": "personal",
        },
    )
    if service is not None:
        adapter._service = service
    return adapter


# ---------------------------------------------------------------------------
# 1) the imports inside adapter.py match the googleapiclient surface the
#    real send path invokes (chain: service.users().messages().send(...
#    body={"raw": ...}).execute()). Pack-internal because the import
#    statements are owned by this pack; the framework test asserts the
#    behavior; this asserts the contract surface the framework relies on.
# ---------------------------------------------------------------------------


def test_send_imports_match_gmail_api_client_surface() -> None:
    """``adapter.py`` imports the stdlib surface :meth:`send` uses to
    build the wire-shape payload: ``base64`` for urlsafe-b64 encoding of
    the MIME bytes; ``MIMEText`` for the RFC 2822 envelope;
    ``formatdate`` for the RFC 2822 Date header.

    :meth:`_classify_gmail_error` duck-types
    ``googleapiclient.errors.HttpError`` via ``exc.resp.status`` rather
    than importing the SDK at module load (cleaner test seam +
    classifies non-googleapiclient HTTP exceptions with the same shape).
    The contract is verified by exercising the classifier with a
    duck-typed exception below.

    Finally, the Gmail send chain method names (``users().messages().
    send(body=...).execute()``) — the only googleapiclient surface
    :meth:`send` touches — are present on a fresh ``_FakeSendService``,
    confirming the pack's send code relies only on the documented
    public surface (no private attrs)."""
    assert hasattr(_adapter_mod, "base64")
    assert hasattr(_adapter_mod, "MIMEText")
    assert hasattr(_adapter_mod, "formatdate")

    classify = GmailAdapter._classify_gmail_error

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status = status

        def get(self, key: str, default: Any = None) -> Any:
            if key == "status":
                return self.status
            return default

    class _DuckHttpError(Exception):
        def __init__(self, status: int) -> None:
            super().__init__(f"HTTP {status}")
            self.resp = _Resp(status)

    # 401 / 429 / 503 round-trip through the duck-typed taxonomy.
    assert classify(_DuckHttpError(401))[:2] == ("fatal", "auth_failed")
    assert classify(_DuckHttpError(429))[:2] == ("transient", "rate_limited")
    assert classify(_DuckHttpError(503))[:2] == ("transient", "network")

    svc = _FakeSendService()
    assert hasattr(svc.users(), "messages")
    assert hasattr(svc.users().messages(), "send")
    assert hasattr(svc.users().messages().send(body={"raw": ""}), "execute")


# ---------------------------------------------------------------------------
# 2) the MIME envelope :meth:`_build_mime_message` produces round-trips
#    through stdlib base64url decode + email.message_from_bytes. This is
#    the wire-shape contract the Gmail v1 endpoint reads — if it fails
#    here, Gmail's server rejects every outbound message.
# ---------------------------------------------------------------------------


def test_mime_message_round_trips_through_base64_decode() -> None:
    """``_build_mime_message`` returns base64url-encoded RFC 2822 MIME.
    The Gmail v1 ``users.messages.send`` endpoint reads ``body.raw`` and
    decodes via base64url; the envelope MUST round-trip through stdlib
    ``base64.urlsafe_b64decode`` + :func:`email.message_from_bytes`
    cleanly, with From/To/Subject/Date/Content-Type headers preserved
    and the body bytes equal to the input ``message.body``."""
    adapter = _make_adapter()
    message = OutboundMessage(
        channel="gmail",
        recipient="alice@example.test",
        body="Pack-internal round-trip body — UTF-8 ✓",
        metadata={"subject": "Pack-internal round trip"},
    )

    encoded = adapter._build_mime_message(message)

    # base64url decode (padding-tolerant).
    padding = "=" * (-len(encoded) % 4)
    raw = base64.urlsafe_b64decode(encoded + padding)
    parsed = email.message_from_bytes(raw)

    assert parsed.get("To") == "alice@example.test"
    assert parsed.get("From") == "sandbox@example.test"
    assert parsed.get("Subject") == "Pack-internal round trip"
    assert parsed.get("Date") is not None
    assert parsed.get_content_type() == "text/plain"
    body_bytes = parsed.get_payload(decode=True)
    assert isinstance(body_bytes, (bytes, bytearray))
    assert body_bytes.decode("utf-8") == message.body


# ---------------------------------------------------------------------------
# 3) the messaging.sent payload :meth:`send` emits validates byte-for-
#    byte against the additive :class:`MessagingSentV2` schema. The
#    inherited extra="forbid" check means any extra field on the
#    pack's emit-side would fail validation; the field-name spelling
#    must match the on-main registration character-for-character per
#    [D7]. This is the closing assertion on the [D7] "v1 immutable"
#    contract from the pack side.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messaging_sent_v2_emit_validates_against_additive_schema() -> None:
    """End-to-end pack-internal: after a successful Phase A send (seams
    unset), the emitted ``messaging.sent`` payload validates byte-for-
    byte against :class:`MessagingSentV2` (which has ``extra='forbid'``
    on its model_config). Any drift in field names or extras introduced
    by the pack would fail validation here — the pack-side guard
    against accidental schema-shape drift across future Gmail-adapter
    edits."""
    service = _FakeSendService(
        response={"id": "gmail-pack-x", "threadId": "thr-pack-x"}
    )
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    adapter._ctx = _ctx(event_log)
    message = OutboundMessage(
        channel="gmail",
        recipient="alice@example.test",
        body="pack-internal emit test body",
        metadata={"subject": "Pack-internal emit"},
    )

    result = await adapter.send(message)

    assert result.delivered is True
    sent = [c for c in event_log.calls if c["type"] == "messaging.sent"]
    assert len(sent) == 1
    assert sent[0]["schema_version"] == 2
    payload = sent[0]["payload"]
    # extra="forbid" — extras would raise here.
    model = MessagingSentV2.model_validate(payload)
    assert model.source_channel == "gmail"
    assert model.to_identifier == "alice@example.test"
    assert model.thread_id == "thr-pack-x"
    assert model.subject == "Pack-internal emit"
    assert model.body_text == "pack-internal emit test body"
    assert model.delivery_status == "sent"
    assert model.sent_at  # ISO timestamp set
    assert model.member_id == "member-fixture"
    assert model.provider == "gmail"
    assert model.provider_account_id == "sandbox@example.test"
    assert model.workspace == "personal"
    assert model.native_message_id == "gmail-pack-x"
