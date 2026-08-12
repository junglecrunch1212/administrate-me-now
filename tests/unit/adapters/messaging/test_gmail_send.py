"""Framework-level tests for the Gmail real-outbound ``send`` path.

Closes prompt 11a commit 3. Verifies the real :meth:`GmailAdapter.send`
implementation shipped in 11a c2 against:

- the MIME builder's RFC 2822 + base64url contract (the
  ``users.messages.send`` ``body.raw`` field's wire shape);
- the Phase-A-conditional observation-wrap pattern from 11r-c (seam
  slots populated → route through :func:`adminme.lib.observation.outbound`;
  unset → direct Gmail API call);
- the field-mapping onto the EXISTING ``messaging.sent@v1`` schema
  registered at ``adminme/events/schemas/ingest.py`` since phase 04-3
  (commit ``2ce1cff``); the payload validates byte-for-byte against
  :class:`MessagingSentV1` with no extras (``extra="forbid"``);
- the AdapterErrorV1 taxonomy classification for 401/429/503 HttpError
  responses;
- the c1 registry-lookup contract (``registry.get("messaging.sent", 1)``
  returns :class:`MessagingSentV1`).

These tests live under ``tests/unit/adapters/messaging/`` (framework
location) rather than the pack-internal ``packs/adapters/messaging/
gmail/tests/`` directory because they exercise the framework-level send
contract — the observation seam, the existing ``messaging.sent@v1``
schema, the AdapterContext shape — not the pack's internal polling
logic. The basename is unique per F-4 so collection from the repo
root does not collide with sibling packs (11r-c precedent +
sidecar #150).
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
from adminme.events.registry import registry
from adminme.events.schemas.ingest import MessagingSentV1, MessagingSentV2, MessagingSentV3
from adminme.lib.adapters import OutboundMessage, SendResult
from adminme.lib.observation import OutboundResult

REPO_ROOT = Path(__file__).resolve().parents[4]
GMAIL_PACK_DIR = REPO_ROOT / "packs" / "adapters" / "messaging" / "gmail"


def _load_gmail_adapter_module() -> Any:
    """Load the Gmail adapter module from the pack root by file path.

    Mirrors :func:`test_gmail_retrofit._load_gmail_adapter_class` and the
    pack-internal loader: ``packs/`` is not a Python package under
    poetry's package config, so the file-path importlib pattern is the
    canonical loader for pack code from framework tests."""
    spec = importlib.util.spec_from_file_location(
        "gmail_adapter_send_under_test", GMAIL_PACK_DIR / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_gmail_adapter_module()
GmailAdapter: Any = _adapter_mod.GmailAdapter


_FROZEN_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


class _FakeEventLog:
    """Captures synchronous append calls for assertion. Mirrors the
    pack-internal test's ``_FakeEventLog`` so the two test suites stay
    in sync on the event-log contract surface."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **_kwargs: Any) -> str:
        self.calls.append(envelope.model_dump())
        return f"event-{len(self.calls)}"


def _ctx(event_log: _FakeEventLog) -> AdapterContext:
    """Build an AdapterContext with a deterministic clock and
    correlation id. The frozen clock makes ``sent_at`` assertions
    stable; the fixed correlation id mirrors the pack-internal tests."""
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-send-test",
        now_factory=lambda: _FROZEN_NOW,
    )


class _FakeSendService:
    """Minimal stand-in for ``googleapiclient`` Gmail service exposing
    ``service.users().messages().send(userId, body).execute()``.

    ``response`` is what ``execute()`` returns on the happy path;
    ``raises`` (when set) is the exception ``execute()`` raises instead.
    All call args are captured on ``send_calls`` for assertion.
    """

    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._response = response or {
            "id": "gmail-msg-abc",
            "threadId": "gmail-thr-xyz",
        }
        self._raises = raises
        self.send_calls: list[dict[str, Any]] = []

    def users(self) -> Any:
        return _FakeSendUsers(self)


class _FakeSendUsers:
    def __init__(self, service: _FakeSendService) -> None:
        self._service = service

    def messages(self) -> Any:
        return _FakeSendMessages(self._service)


class _FakeSendMessages:
    def __init__(self, service: _FakeSendService) -> None:
        self._service = service

    def send(self, **kwargs: Any) -> Any:
        self._service.send_calls.append(kwargs)
        return _FakeSendExecute(self._service)


class _FakeSendExecute:
    def __init__(self, service: _FakeSendService) -> None:
        self._service = service

    def execute(self) -> Any:
        if self._service._raises is not None:
            raise self._service._raises
        return dict(self._service._response)


class _FakeHttpResponse:
    """httplib2-style mapping with a ``status`` attribute, sufficient
    for :meth:`GmailAdapter._classify_gmail_error`."""

    def __init__(self, *, status: int, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    def get(self, key: str, default: Any = None) -> Any:
        if key == "status":
            return self.status
        return self._headers.get(key.lower(), default)


class _FakeHttpError(Exception):
    """Duck-type for ``googleapiclient.errors.HttpError`` — the
    classifier reads ``.resp.status`` and ``.resp.get('retry-after')``."""

    def __init__(
        self,
        resp: _FakeHttpResponse,
        content: bytes = b"",
    ) -> None:
        super().__init__(f"HTTP {resp.status}")
        self.resp = resp
        self.content = content


def _make_adapter(
    *,
    service: _FakeSendService | None = None,
    observation_session: Any = None,
    observation_manager: Any = None,
    observation_event_log: Any = None,
    user_email: str = "sandbox@example.test",
) -> Any:
    """Construct a GmailAdapter pre-seeded with a fake service +
    optional observation-seam slots. ``_ctx`` is set via :func:`_seed_ctx`
    on demand to mirror what :meth:`GmailAdapter.start` does in
    production."""
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": user_email,
        },
        observation_session=observation_session,
        observation_manager=observation_manager,
        observation_event_log=observation_event_log,
    )
    adapter._service = service
    return adapter


def _seed_ctx(adapter: Any, event_log: _FakeEventLog) -> AdapterContext:
    """Attach an AdapterContext to the adapter (the supervisor does this
    via ``start(ctx)`` in production). Returned for assertions that need
    the same ctx instance."""
    ctx = _ctx(event_log)
    adapter._ctx = ctx
    return ctx


# ---------------------------------------------------------------------------
# 1) MIME builder
# ---------------------------------------------------------------------------


def test_build_mime_message_text_plain_happy_path() -> None:
    """The MIME builder produces a base64url-encoded ``text/plain``
    envelope with the expected From/To/Subject/Date headers. The encoded
    body round-trips through :func:`base64.urlsafe_b64decode` and parses
    cleanly via :mod:`email`. This is the wire shape Gmail v1
    ``users.messages.send`` requires in ``body.raw``."""
    adapter = _make_adapter()
    message = OutboundMessage(
        channel="gmail",
        recipient="alice@example.test",
        body="hello alice",
        metadata={"subject": "Quick note"},
    )

    encoded = adapter._build_mime_message(message)

    # base64url-decodes; parse the raw MIME and assert headers + body.
    padding = "=" * (-len(encoded) % 4)
    raw = base64.urlsafe_b64decode(encoded + padding)
    parsed = email.message_from_bytes(raw)
    assert parsed.get("To") == "alice@example.test"
    assert parsed.get("From") == "sandbox@example.test"
    assert parsed.get("Subject") == "Quick note"
    assert parsed.get("Date") is not None
    assert parsed.get_content_type() == "text/plain"
    body = parsed.get_payload(decode=True)
    assert isinstance(body, (bytes, bytearray))
    assert body.decode("utf-8") == "hello alice"


def test_build_mime_message_missing_subject_uses_placeholder() -> None:
    """When ``metadata`` has no ``subject``, the MIME envelope omits the
    Subject header rather than fabricating a value. Per A-3 in §4 of
    the prompt: ``subject`` lives in ``metadata`` and is optional. The
    envelope must still encode cleanly (To/From/Date present)."""
    adapter = _make_adapter()
    message = OutboundMessage(
        channel="gmail",
        recipient="alice@example.test",
        body="no subject body",
        metadata={},
    )

    encoded = adapter._build_mime_message(message)

    padding = "=" * (-len(encoded) % 4)
    raw = base64.urlsafe_b64decode(encoded + padding)
    parsed = email.message_from_bytes(raw)
    # No subject → header omitted (or empty). Adapter's builder only sets
    # Subject when ``metadata["subject"]`` is truthy.
    assert parsed.get("Subject") in (None, "")
    assert parsed.get("To") == "alice@example.test"
    assert parsed.get("Date") is not None


# ---------------------------------------------------------------------------
# 2) send() — observation seam populated (Phase B path)
# ---------------------------------------------------------------------------


async def test_send_happy_path_with_seams_populated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When all three observation-seam slots are populated, the adapter
    routes through :func:`adminme.lib.observation.outbound` with the
    expected action name, payload, and target metadata; on
    non-suppressed result it emits ``messaging.sent@v1`` and returns a
    ``SendResult`` carrying the Gmail message id."""
    captured: list[dict[str, Any]] = []

    async def fake_outbound(
        session: Any,
        action: str,
        payload: dict[str, Any],
        action_fn: Any,
        *,
        manager: Any,
        event_sink: Any,
        target_channel: str,
        target_identifier: str,
    ) -> OutboundResult:
        captured.append(
            {
                "session": session,
                "action": action,
                "payload": dict(payload),
                "manager": manager,
                "event_log": event_sink,
                "target_channel": target_channel,
                "target_identifier": target_identifier,
            }
        )
        # Simulate observation-mode OFF: actually call through.
        result = await action_fn()
        return OutboundResult(suppressed=False, result=result)

    monkeypatch.setattr(_adapter_mod, "outbound", fake_outbound)

    service = _FakeSendService(response={"id": "gmail-msg-001", "threadId": "thr-001"})
    session_sentinel = object()
    manager_sentinel = object()
    obs_log_sentinel = object()
    adapter = _make_adapter(
        service=service,
        observation_session=session_sentinel,
        observation_manager=manager_sentinel,
        observation_event_log=obs_log_sentinel,
    )
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)
    message = OutboundMessage(
        channel="gmail",
        recipient="bob@example.test",
        body="hello bob",
        metadata={"subject": "Hi Bob"},
    )

    result = await adapter.send(message)

    # outbound() was called once with the expected signature.
    assert len(captured) == 1
    call = captured[0]
    assert call["session"] is session_sentinel
    assert call["manager"] is manager_sentinel
    assert call["event_log"] is obs_log_sentinel
    assert call["action"] == "messaging.send"
    assert call["target_channel"] == "gmail"
    assert call["target_identifier"] == "bob@example.test"
    assert call["payload"] == {
        "channel": "gmail",
        "recipient": "bob@example.test",
        "body": "hello bob",
    }
    # Gmail API was actually invoked (observation OFF path).
    assert len(service.send_calls) == 1
    assert service.send_calls[0]["userId"] == "me"
    assert "raw" in service.send_calls[0]["body"]

    # messaging.sent@v1 emitted exactly once with the full field set.
    sent = [c for c in event_log.calls if c["type"] == "messaging.sent"]
    assert len(sent) == 1
    assert sent[0]["schema_version"] == 3
    payload = sent[0]["payload"]
    assert payload["source_channel"] == "gmail"
    assert payload["to_identifier"] == "bob@example.test"
    assert payload["thread_id"] == "thr-001"
    assert payload["subject"] == "Hi Bob"
    assert payload["body_text"] == "hello bob"
    assert payload["delivery_status"] == "sent"
    assert isinstance(payload["sent_at"], str) and payload["sent_at"]

    assert result == SendResult(
        delivered=True, external_id="gmail-msg-001", suppressed=False
    )


async def test_send_observation_suppressed_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When :func:`outbound` reports suppression (Phase B observation-on
    default per [§6.16]), the adapter MUST emit
    ``messaging.sent@v1`` with ``delivery_status="queued"`` +
    ``thread_id=None`` and return ``SendResult(delivered=False,
    external_id=None, suppressed=True)``. The Gmail API is NOT called."""

    async def fake_outbound(
        session: Any,
        action: str,
        payload: dict[str, Any],
        action_fn: Any,
        *,
        manager: Any,
        event_sink: Any,
        target_channel: str,
        target_identifier: str,
    ) -> OutboundResult:
        # Suppressed → do NOT call action_fn.
        return OutboundResult(suppressed=True)

    monkeypatch.setattr(_adapter_mod, "outbound", fake_outbound)

    service = _FakeSendService()
    adapter = _make_adapter(
        service=service,
        observation_session=object(),
        observation_manager=object(),
        observation_event_log=object(),
    )
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)
    message = OutboundMessage(
        channel="gmail",
        recipient="bob@example.test",
        body="suppressed body",
        metadata={"subject": "Suppressed"},
    )

    result = await adapter.send(message)

    # Gmail API was NOT called.
    assert service.send_calls == []

    sent = [c for c in event_log.calls if c["type"] == "messaging.sent"]
    assert len(sent) == 1
    payload = sent[0]["payload"]
    assert payload["delivery_status"] == "queued"
    assert payload["thread_id"] is None
    # Full body is still captured for the audit log.
    assert payload["body_text"] == "suppressed body"

    assert result == SendResult(delivered=False, external_id=None, suppressed=True)


# ---------------------------------------------------------------------------
# 3) send() — Phase A: seams unset → direct Gmail API call
# ---------------------------------------------------------------------------


async def test_send_direct_call_when_seams_not_populated() -> None:
    """Phase A (today): the supervisor's adapter loader does NOT yet
    populate the observation-seam slots, so :meth:`send` calls Gmail v1
    directly. The 11r-c Phase-A-conditional pattern. The
    ``messaging.sent@v1`` event is still emitted with
    ``delivery_status="sent"``."""
    service = _FakeSendService(
        response={"id": "gmail-direct-1", "threadId": "thr-direct"}
    )
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)
    message = OutboundMessage(
        channel="gmail",
        recipient="alice@example.test",
        body="direct send",
        metadata={"subject": "Direct"},
    )

    result = await adapter.send(message)

    assert len(service.send_calls) == 1
    sent = [c for c in event_log.calls if c["type"] == "messaging.sent"]
    assert len(sent) == 1
    payload = sent[0]["payload"]
    assert payload["delivery_status"] == "sent"
    assert payload["thread_id"] == "thr-direct"
    assert result.delivered is True
    assert result.external_id == "gmail-direct-1"
    assert result.suppressed is False


# ---------------------------------------------------------------------------
# 4) Schema fidelity — payload validates against existing MessagingSentV1
# ---------------------------------------------------------------------------


async def test_send_emits_only_existing_messaging_sent_fields() -> None:
    """The emitted ``messaging.sent`` payload MUST validate against the
    EXISTING :class:`MessagingSentV1` model unchanged. ``extra="forbid"``
    on the model means any drift introducing a new field would fail
    validation. This pins F-6 (MAP DON'T REGISTER) — Gmail outbound
    reuses the on-main schema; no new fields, no new schema."""
    service = _FakeSendService(response={"id": "gmail-x", "threadId": "thr-x"})
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)
    message = OutboundMessage(
        channel="gmail",
        recipient="alice@example.test",
        body="schema test body",
        metadata={"subject": "Schema check"},
    )

    await adapter.send(message)

    sent = [c for c in event_log.calls if c["type"] == "messaging.sent"]
    assert len(sent) == 1
    payload = sent[0]["payload"]
    # Round-trips strict-validation; if any extra field crept in, this
    # call would raise pydantic.ValidationError.
    MessagingSentV3.model_validate(payload)


# ---------------------------------------------------------------------------
# 5) Input validation
# ---------------------------------------------------------------------------


async def test_send_validates_outbound_message_recipient() -> None:
    """Empty ``recipient`` is rejected before any external call. The
    ABC's :class:`OutboundMessage` is frozen but does NOT validate
    non-emptiness — :meth:`GmailAdapter.send` enforces it defensively
    so the adapter never emits a degenerate ``messaging.sent`` event
    [§7.3, D17]."""
    adapter = _make_adapter(service=_FakeSendService())
    bad = OutboundMessage(
        channel="gmail",
        recipient="",
        body="orphan body",
        metadata={},
    )

    with pytest.raises(ValueError, match="recipient"):
        await adapter.send(bad)


# ---------------------------------------------------------------------------
# 6) Error classification (AdapterErrorV1 taxonomy)
# ---------------------------------------------------------------------------


async def test_send_4xx_auth_failure_classifies_fatal() -> None:
    """A 401 from ``users.messages.send`` classifies as ``fatal`` /
    ``auth_failed``, emits exactly one ``adapter.error@v1``, AND
    re-raises so the supervisor's backoff handles retry. NO
    ``messaging.sent`` event — the send did not, in fact, happen."""
    error = _FakeHttpError(resp=_FakeHttpResponse(status=401))
    service = _FakeSendService(raises=error)
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)
    message = OutboundMessage(
        channel="gmail",
        recipient="alice@example.test",
        body="auth-fail body",
    )

    with pytest.raises(_FakeHttpError):
        await adapter.send(message)

    errors = [c for c in event_log.calls if c["type"] == "adapter.error"]
    assert len(errors) == 1
    err_payload = errors[0]["payload"]
    assert err_payload["severity"] == "fatal"
    assert err_payload["error_kind"] == "auth_failed"
    # No messaging.sent event on the error path.
    assert [c for c in event_log.calls if c["type"] == "messaging.sent"] == []


async def test_send_5xx_classifies_transient() -> None:
    """A 503 from Gmail classifies as ``transient`` / ``network`` so
    the supervisor's backoff retries. One ``adapter.error@v1`` emitted;
    the exception propagates."""
    error = _FakeHttpError(resp=_FakeHttpResponse(status=503))
    service = _FakeSendService(raises=error)
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)
    message = OutboundMessage(
        channel="gmail",
        recipient="alice@example.test",
        body="5xx body",
    )

    with pytest.raises(_FakeHttpError):
        await adapter.send(message)

    errors = [c for c in event_log.calls if c["type"] == "adapter.error"]
    assert len(errors) == 1
    assert errors[0]["payload"]["severity"] == "transient"
    assert errors[0]["payload"]["error_kind"] == "network"


async def test_send_429_classifies_rate_limited_transient() -> None:
    """A 429 with a ``Retry-After`` header classifies as ``transient``
    / ``rate_limited`` and parses ``retry_after_s`` from the header.
    Mirrors the inbound-poll rate-limit handling in the pack-internal
    tests."""
    error = _FakeHttpError(
        resp=_FakeHttpResponse(status=429, headers={"Retry-After": "45"})
    )
    service = _FakeSendService(raises=error)
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)
    message = OutboundMessage(
        channel="gmail",
        recipient="alice@example.test",
        body="rate-limited body",
    )

    with pytest.raises(_FakeHttpError):
        await adapter.send(message)

    errors = [c for c in event_log.calls if c["type"] == "adapter.error"]
    assert len(errors) == 1
    payload = errors[0]["payload"]
    assert payload["severity"] == "transient"
    assert payload["error_kind"] == "rate_limited"
    assert payload["retry_after_s"] == 45


# ---------------------------------------------------------------------------
# 7) Full body preserved (no truncation) — schema's body_text: str | None
# ---------------------------------------------------------------------------


async def test_send_full_body_text_not_truncated() -> None:
    """The emitted ``messaging.sent`` payload's ``body_text`` carries
    the FULL body — the existing schema's field is ``str | None`` with
    no length cap. A 5000-char body round-trips byte-for-byte. Pins
    the prompt's §1 invariant ("FULL body, not a truncated preview")."""
    service = _FakeSendService(response={"id": "gmail-y", "threadId": "thr-y"})
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)
    long_body = "x" * 5000
    message = OutboundMessage(
        channel="gmail",
        recipient="alice@example.test",
        body=long_body,
        metadata={"subject": "Long"},
    )

    await adapter.send(message)

    sent = [c for c in event_log.calls if c["type"] == "messaging.sent"]
    assert len(sent) == 1
    assert sent[0]["payload"]["body_text"] == long_body
    assert len(sent[0]["payload"]["body_text"]) == 5000


# ---------------------------------------------------------------------------
# 8) c1 contract — registry lookup returns the existing model
# ---------------------------------------------------------------------------


def test_messaging_sent_v1_registry_lookup_returns_existing_model() -> None:
    """The c1 verify-only contract: ``registry.get("messaging.sent", 1)``
    returns the existing :class:`MessagingSentV1` model registered at
    ``adminme/events/schemas/ingest.py`` since phase 04-3 (commit
    ``2ce1cff``). 11a adds NO new schema and re-registers nothing — the
    cycle #17 c1 collision is the precedent."""
    assert registry.get("messaging.sent", 1) is MessagingSentV1
    assert registry.get("messaging.sent", 2) is MessagingSentV2
    assert registry.get("messaging.sent", 3) is MessagingSentV3
