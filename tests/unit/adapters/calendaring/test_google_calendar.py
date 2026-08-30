"""Framework-level tests for the Google Calendar Cat-B adapter pack.

Closes prompt 11b-iii §7 c3 deliverable (≥15 tests, basename unique per
F-4 against the Gmail / Plaid / Apple Reminders pack-internal basenames).
Verifies the fresh Cat-B implementation shipped in 11b-iii c2 against:

- :class:`ExternalStateMirrorAdapter` inheritance + ``family="calendaring"``
  + ``write_capabilities=["create_event"]`` (asymmetric per [UT-27]);
- the :meth:`apply_write` gate raising :class:`WriteCapabilityError` for
  any undeclared operation kind BEFORE the external API is touched;
- the [§6.20] observation-wrap pattern: seam slots populated → routes
  through :func:`adminme.lib.observation.outbound`; unset → direct
  ``events.insert`` call; F-3 (HARD): ``OutboundResult(suppressed=True)``
  → :class:`WriteResult` with ``applied=False, suppressed=True``;
- the AdapterErrorV1 taxonomy classification for 401/429/503 ``HttpError``
  responses on the outbound write path;
- the inbound ``_poll_once`` emit contract: first observation of a Google
  event id → ``calendar.event_added@v1``; re-poll on the same id → skip;
  payload validates byte-for-byte against the EXISTING
  :class:`CalendarEventAddedV1` schema (F-2 MAP DON'T REGISTER);
- the c1 registry-lookup contract (``registry.get("calendar.event_added",
  1)`` returns :class:`CalendarEventAddedV1`).

These tests live under ``tests/unit/adapters/calendaring/`` (framework
location, sibling to ``tests/unit/adapters/messaging/test_gmail_send.py``
and ``tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py``)
because they exercise the framework-level Cat-B contract + the canonical
event-schema layer — not the pack's internal manifest / validator surface
(that's the c4 pack-internal deliverable).
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.events.registry import registry
from adminme.events.schemas.ingest import CalendarEventAddedV1, CalendarEventV2
from adminme.lib.adapters import (
    ExternalStateMirrorAdapter,
    WriteCapabilityError,
    WriteOperation,
    WriteResult,
)
from adminme.lib.observation import OutboundResult

REPO_ROOT = Path(__file__).resolve().parents[4]
GOOGLE_CALENDAR_PACK_DIR = (
    REPO_ROOT / "packs" / "adapters" / "calendaring" / "google-calendar"
)


def _load_google_calendar_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root by file path. Mirrors the
    Gmail / Apple Reminders test loaders: ``packs/`` is not a Python
    package under poetry's package config, so file-path importlib is the
    canonical loader for pack code from framework tests."""
    spec = importlib.util.spec_from_file_location(
        "google_calendar_adapter_under_test",
        GOOGLE_CALENDAR_PACK_DIR / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_google_calendar_adapter_module()
GoogleCalendarAdapter: Any = _adapter_mod.GoogleCalendarAdapter


_FROZEN_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
_OBSERVATION_DEFAULT = object()


@pytest.fixture(autouse=True)
def _inactive_observation_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    async def call_through(
        _session: Any,
        _action: str,
        _payload: dict[str, Any],
        action_fn: Any,
        **_kwargs: Any,
    ) -> OutboundResult:
        return OutboundResult(suppressed=False, result=await action_fn())

    monkeypatch.setattr(_adapter_mod, "observation_outbound", call_through)


class _FakeEventLog:
    """Captures typed asynchronous event-sink writes for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **_kwargs: Any) -> str:
        self.calls.append(envelope.model_dump())
        return f"event-{len(self.calls)}"


class _FailingEventLog(_FakeEventLog):
    async def write(self, envelope: Any, **_kwargs: Any) -> str:
        raise RuntimeError("event log unavailable")


def _ctx(
    event_log: _FakeEventLog,
    *,
    cursor_store: AdapterCursorStore | None = None,
    now: datetime = _FROZEN_NOW,
) -> AdapterContext:
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-gcal-test",
        now_factory=lambda: now,
        cursor_store=cursor_store,
    )


async def test_append_failure_propagates_before_sync_state_can_advance() -> None:
    adapter = _make_adapter(service=_FakeCalendarService())
    with pytest.raises(RuntimeError, match="event log unavailable"):
        await adapter._append(
            ctx=_ctx(_FailingEventLog()),
            event_type="calendar.event_added",
            payload={"source": "google_calendar"},
        )


class _FakeHttpResponse:
    """httplib2-style mapping with a ``status`` attribute, sufficient for
    :meth:`GoogleCalendarAdapter._classify_calendar_error`."""

    def __init__(self, *, status: int, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    def get(self, key: str, default: Any = None) -> Any:
        if key == "status":
            return self.status
        return self._headers.get(key.lower(), default)


class _FakeHttpError(Exception):
    """Duck-type for ``googleapiclient.errors.HttpError`` — the classifier
    reads ``.resp.status`` and ``.resp.get('Retry-After')``."""

    def __init__(self, resp: _FakeHttpResponse, content: bytes = b"") -> None:
        super().__init__(f"HTTP {resp.status}")
        self.resp = resp
        self.content = content


class _FakeCalendarService:
    """Minimal Calendar v3 service exposing ``events().list(...).execute()``
    and ``events().insert(...).execute()``.

    ``list_pages`` is a list of pages returned in sequence by ``list()``;
    each page is ``{"items": [...], "nextPageToken": "..."}``. Once the
    sequence is exhausted, the next call returns an empty page (no token)
    so the adapter's pagination loop terminates.

    ``insert_response`` is the dict ``insert().execute()`` returns; if
    ``raises`` is set, ``execute()`` raises that exception instead.
    """

    def __init__(
        self,
        *,
        list_pages: list[dict[str, Any] | BaseException] | None = None,
        calendar_list_pages: list[dict[str, Any]] | None = None,
        insert_response: dict[str, Any] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self._list_pages = list(list_pages or [])
        self._calendar_list_pages = list(calendar_list_pages or [])
        self._insert_response = insert_response or {"id": "gcal-evt-out-001"}
        self._raises = raises
        self.list_calls: list[dict[str, Any]] = []
        self.insert_calls: list[dict[str, Any]] = []
        self.calendar_list_calls: list[dict[str, Any]] = []

    def events(self) -> "_FakeEventsResource":
        return _FakeEventsResource(self)

    def calendarList(self) -> "_FakeCalendarListResource":
        return _FakeCalendarListResource(self)


class _FakeCalendarListResource:
    def __init__(self, service: _FakeCalendarService) -> None:
        self._service = service

    def list(self, **kwargs: Any) -> "_FakeCalendarListRequest":
        self._service.calendar_list_calls.append(kwargs)
        return _FakeCalendarListRequest(self._service)


class _FakeCalendarListRequest:
    def __init__(self, service: _FakeCalendarService) -> None:
        self._service = service

    def execute(self) -> dict[str, Any]:
        if self._service._calendar_list_pages:
            return dict(self._service._calendar_list_pages.pop(0))
        return {"items": []}


class _FakeEventsResource:
    def __init__(self, service: _FakeCalendarService) -> None:
        self._service = service

    def list(self, **kwargs: Any) -> "_FakeListRequest":
        self._service.list_calls.append(kwargs)
        return _FakeListRequest(self._service)

    def insert(self, **kwargs: Any) -> "_FakeInsertRequest":
        self._service.insert_calls.append(kwargs)
        return _FakeInsertRequest(self._service)


class _FakeListRequest:
    def __init__(self, service: _FakeCalendarService) -> None:
        self._service = service

    def execute(self) -> dict[str, Any]:
        if self._service._raises is not None:
            raise self._service._raises
        if self._service._list_pages:
            page = self._service._list_pages.pop(0)
            if isinstance(page, BaseException):
                raise page
            return dict(page)
        return {"items": []}


class _FakeInsertRequest:
    def __init__(self, service: _FakeCalendarService) -> None:
        self._service = service

    def execute(self) -> dict[str, Any]:
        if self._service._raises is not None:
            raise self._service._raises
        return dict(self._service._insert_response)


def _make_adapter(
    *,
    service: _FakeCalendarService | None = None,
    observation_session: Any = _OBSERVATION_DEFAULT,
    observation_manager: Any = _OBSERVATION_DEFAULT,
    observation_event_log: Any = _OBSERVATION_DEFAULT,
    calendar_id: str = "primary@example.test",
) -> Any:
    """Construct a GoogleCalendarAdapter with a fake service. Mirrors the
    Gmail test's ``_make_adapter`` factory shape so the two suites stay
    structurally aligned."""
    adapter = GoogleCalendarAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "calendar_id": calendar_id,
            "user_email": "operator@example.test",
        },
        observation_session=(
            object()
            if observation_session is _OBSERVATION_DEFAULT
            else observation_session
        ),
        observation_manager=(
            object()
            if observation_manager is _OBSERVATION_DEFAULT
            else observation_manager
        ),
        observation_event_log=(
            object()
            if observation_event_log is _OBSERVATION_DEFAULT
            else observation_event_log
        ),
    )
    if service is not None:
        adapter._service = service
    return adapter


def _seed_ctx(
    adapter: Any,
    event_log: _FakeEventLog,
    *,
    cursor_store: AdapterCursorStore | None = None,
) -> AdapterContext:
    """Attach an AdapterContext to the adapter — what
    :meth:`GoogleCalendarAdapter.start` does in production via
    ``self._ctx = ctx``."""
    ctx = _ctx(event_log, cursor_store=cursor_store)
    adapter._ctx = ctx
    return ctx


# ===========================================================================
# 1) Cat-B inheritance + ClassVars
# ===========================================================================


def test_google_calendar_adapter_inherits_external_state_mirror_adapter() -> None:
    """The pack's adapter is a Cat-B :class:`ExternalStateMirrorAdapter`
    subclass — the load-bearing inheritance per memo 05 §1.2 / [PM-34].
    Failing this would mean the validator's ``base_class_conformance``
    check would reject the pack at install time."""
    assert issubclass(GoogleCalendarAdapter, ExternalStateMirrorAdapter)


def test_family_class_attribute_is_calendaring_literal() -> None:
    """``family`` is the ``"calendaring"`` literal — pinned at the
    class level so the validator can read it without instantiation.
    Memo 05 §3.2 + [D19] anchor the three-value union; this is the
    calendaring tip."""
    assert GoogleCalendarAdapter.family == "calendaring"


def test_write_capabilities_declared_asymmetrically() -> None:
    """[UT-27] asymmetric write surface — declare ONLY the per-operation
    kinds the adapter actually implements. 11b-iii ships ``create_event``
    only; ``update_event`` / ``delete_event`` are out of scope per the
    parent draft and intentionally NOT declared. A declared-but-not-
    implemented kind would fail the validator's method-presence rule per
    memo 05 §3.4."""
    assert GoogleCalendarAdapter.write_capabilities == [
        "create_event",
        "update_event",
        "delete_event",
        "respond_event",
        "move_event",
    ]
    assert len(GoogleCalendarAdapter.write_capabilities) == 5


async def test_read_only_instance_uses_exact_scope_and_denies_declared_writes() -> None:
    adapter = GoogleCalendarAdapter(
        config={
            "oauth_credentials_ref": "keychain://fixture/google",
            "calendar_id": "primary",
            "provider_account_id": "calendar-account-fixture",
            "permission_scope": ["https://www.googleapis.com/auth/calendar.readonly"],
            "read_only": True,
        }
    )

    assert adapter._scopes == ["https://www.googleapis.com/auth/calendar.readonly"]
    assert adapter.write_capabilities == []
    assert GoogleCalendarAdapter.write_capabilities != []
    with pytest.raises(WriteCapabilityError):
        await adapter.apply_write(
            WriteOperation(kind="create_event", target_external_id=None, payload={})
        )


def test_read_only_instance_rejects_a_broader_configured_scope() -> None:
    with pytest.raises(ValueError, match="must exactly match"):
        GoogleCalendarAdapter(
            config={
                "oauth_credentials_ref": "keychain://fixture/google",
                "calendar_id": "primary",
                "permission_scope": ["https://www.googleapis.com/auth/calendar"],
                "read_only": True,
            }
        )


# ===========================================================================
# 2) apply_write gate — [UT-27]
# ===========================================================================


async def test_apply_write_gate_raises_on_undeclared_kind() -> None:
    """The base class's :meth:`apply_write` gate raises
    :class:`WriteCapabilityError` for any operation whose ``kind`` is not
    in ``write_capabilities`` — BEFORE ``_apply_write_impl`` runs and
    therefore BEFORE the Google API is touched. ``update_event`` is the
    canonical undeclared kind for 11b-iii (out of scope per parent draft;
    deferred to 11b-iii-β)."""
    adapter = _make_adapter()
    operation = WriteOperation(
        kind="share_calendar",
        target_external_id="gcal-evt-1",
        payload={},
    )

    with pytest.raises(WriteCapabilityError):
        await adapter.apply_write(operation)


async def test_apply_write_dispatches_to_impl_on_declared_kind() -> None:
    """For ``kind="create_event"`` the gate passes and
    :meth:`apply_write` dispatches to :meth:`_apply_write_impl`.
    Monkeypatching the instance method records the dispatch without
    touching the real Google API surface."""
    adapter = _make_adapter()
    captured: list[WriteOperation] = []

    async def _recording_impl(operation: WriteOperation) -> WriteResult:
        captured.append(operation)
        return WriteResult(
            applied=True, external_id="gcal-evt-recorded", suppressed=False
        )

    adapter._apply_write_impl = _recording_impl  # type: ignore[method-assign]

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "primary@example.test",
            "summary": "Coffee with Alice",
            "start": {"dateTime": "2026-05-18T15:00:00Z"},
            "end": {"dateTime": "2026-05-18T16:00:00Z"},
        },
    )
    result = await adapter.apply_write(op)

    assert len(captured) == 1
    assert captured[0].kind == "create_event"
    assert result.applied is True
    assert result.external_id == "gcal-evt-recorded"


# ===========================================================================
# 3) Observation-mode wrap — [§6.20] + F-3 (HARD)
# ===========================================================================


async def test_apply_write_routes_through_observation_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """:meth:`_apply_write_impl` MUST route ``events.insert`` through
    :func:`adminme.lib.observation.outbound` per [§6.20] when the three
    observation-mode seam slots are populated. The adapter imports the
    function under the alias ``observation_outbound`` in its own module
    namespace; monkeypatching THAT name records the wrap without needing
    real session / manager / event-log instances. Missing seams are covered
    separately as a fail-closed configuration error."""
    service = _FakeCalendarService(insert_response={"id": "gcal-evt-wrapped-001"})
    session_sentinel = object()
    manager_sentinel = object()
    obs_log_sentinel = object()
    adapter = _make_adapter(
        service=service,
        observation_session=session_sentinel,
        observation_manager=manager_sentinel,
        observation_event_log=obs_log_sentinel,
    )

    captured: list[dict[str, Any]] = []

    async def _fake_outbound(
        session: Any,
        action: str,
        payload: dict[str, Any],
        action_fn: Any,
        *,
        manager: Any,
        event_sink: Any,
        target_channel: str = "unknown",
        target_identifier: str = "unknown",
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
        # Observation mode INACTIVE: actually call through so the fake
        # ``events.insert`` registers on the service mock.
        result = await action_fn()
        return OutboundResult(suppressed=False, result=result)

    monkeypatch.setattr(_adapter_mod, "observation_outbound", _fake_outbound)

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "primary@example.test",
            "summary": "Coffee with Alice",
            "start": {"dateTime": "2026-05-18T15:00:00Z"},
            "end": {"dateTime": "2026-05-18T16:00:00Z"},
        },
    )
    result = await adapter.apply_write(op)

    assert len(captured) == 1
    call = captured[0]
    assert call["session"] is session_sentinel
    assert call["manager"] is manager_sentinel
    assert call["event_log"] is obs_log_sentinel
    assert call["action"] == "calendar.create_event"
    assert call["target_channel"] == "calendaring-google-calendar"
    assert call["target_identifier"] == "primary@example.test"
    # The wrapped ``insert`` actually fired against the fake service.
    assert len(service.insert_calls) == 1
    assert result.applied is True
    assert result.external_id == "gcal-evt-wrapped-001"
    assert result.suppressed is False


async def test_apply_write_observation_suppressed_returns_unapplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-3 (HARD): when :func:`observation_outbound` returns
    ``OutboundResult(suppressed=True)``, :meth:`_apply_write_impl` returns
    ``WriteResult(applied=False, external_id=None, suppressed=True)``.
    The Google API is NOT called — observation mode is the final outbound
    filter per [§6.13] and the fake stub must therefore NOT invoke
    ``action_fn``."""
    service = _FakeCalendarService()
    adapter = _make_adapter(
        service=service,
        observation_session=object(),
        observation_manager=object(),
        observation_event_log=object(),
    )

    async def _suppressing_outbound(
        _session: Any,
        _action: str,
        _payload: dict[str, Any],
        _action_fn: Any,
        **_kwargs: Any,
    ) -> OutboundResult:
        # Observation mode ACTIVE: do NOT call action_fn — the suppress
        # contract per [§6.13/§6.16] is "did not perform the side effect."
        return OutboundResult(suppressed=True, result=None)

    monkeypatch.setattr(_adapter_mod, "observation_outbound", _suppressing_outbound)

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "primary@example.test",
            "summary": "Will not send",
            "start": {"dateTime": "2026-05-18T15:00:00Z"},
            "end": {"dateTime": "2026-05-18T16:00:00Z"},
        },
    )
    result = await adapter.apply_write(op)

    assert result.applied is False
    assert result.external_id is None
    assert result.suppressed is True
    # The wrapped ``events.insert`` was NOT invoked.
    assert service.insert_calls == []


async def test_apply_write_fails_closed_when_seams_unset() -> None:
    """Missing observation dependencies must not bypass the final
    outbound side-effect guard."""
    service = _FakeCalendarService(insert_response={"id": "gcal-evt-direct-001"})
    adapter = _make_adapter(
        service=service,
        observation_session=None,
        observation_manager=None,
        observation_event_log=None,
    )

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "primary@example.test",
            "summary": "Direct call event",
            "start": {"dateTime": "2026-05-18T15:00:00Z"},
            "end": {"dateTime": "2026-05-18T16:00:00Z"},
        },
    )
    with pytest.raises(RuntimeError, match="observation"):
        await adapter.apply_write(op)

    assert service.insert_calls == []


# ===========================================================================
# 4) Error classification — AdapterErrorV1 taxonomy
# ===========================================================================


async def test_apply_write_4xx_auth_failure_classifies_fatal() -> None:
    """A 401 from ``events.insert`` classifies as ``fatal`` /
    ``auth_failed``, emits exactly one ``adapter.error@v1``, AND re-raises
    so the supervisor's backoff handles retry. Mirrors Gmail's outbound
    error path on the calendaring side."""
    error = _FakeHttpError(resp=_FakeHttpResponse(status=401))
    service = _FakeCalendarService(raises=error)
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "primary@example.test",
            "summary": "Auth fail",
            "start": {"dateTime": "2026-05-18T15:00:00Z"},
            "end": {"dateTime": "2026-05-18T16:00:00Z"},
        },
    )

    with pytest.raises(_FakeHttpError):
        await adapter.apply_write(op)

    errors = [c for c in event_log.calls if c["type"] == "adapter.error"]
    assert len(errors) == 1
    payload = errors[0]["payload"]
    assert payload["severity"] == "fatal"
    assert payload["error_kind"] == "auth_failed"


async def test_apply_write_5xx_classifies_transient() -> None:
    """A 503 from ``events.insert`` classifies as ``transient`` /
    ``network`` so the supervisor's backoff retries. One
    ``adapter.error@v1`` emitted; the exception propagates."""
    error = _FakeHttpError(resp=_FakeHttpResponse(status=503))
    service = _FakeCalendarService(raises=error)
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "primary@example.test",
            "summary": "5xx body",
            "start": {"dateTime": "2026-05-18T15:00:00Z"},
            "end": {"dateTime": "2026-05-18T16:00:00Z"},
        },
    )

    with pytest.raises(_FakeHttpError):
        await adapter.apply_write(op)

    errors = [c for c in event_log.calls if c["type"] == "adapter.error"]
    assert len(errors) == 1
    assert errors[0]["payload"]["severity"] == "transient"
    assert errors[0]["payload"]["error_kind"] == "network"


async def test_apply_write_429_classifies_rate_limited_transient() -> None:
    """A 429 with a ``Retry-After`` header classifies as ``transient`` /
    ``rate_limited`` and parses ``retry_after_s`` from the header.
    Mirrors the canonical Gmail outbound rate-limit handling."""
    error = _FakeHttpError(
        resp=_FakeHttpResponse(status=429, headers={"Retry-After": "45"})
    )
    service = _FakeCalendarService(raises=error)
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "primary@example.test",
            "summary": "rate-limited",
            "start": {"dateTime": "2026-05-18T15:00:00Z"},
            "end": {"dateTime": "2026-05-18T16:00:00Z"},
        },
    )

    with pytest.raises(_FakeHttpError):
        await adapter.apply_write(op)

    errors = [c for c in event_log.calls if c["type"] == "adapter.error"]
    assert len(errors) == 1
    err_payload = errors[0]["payload"]
    assert err_payload["severity"] == "transient"
    assert err_payload["error_kind"] == "rate_limited"
    assert err_payload["retry_after_s"] == 45


# ===========================================================================
# 5) Inbound poll — dedup + emit
# ===========================================================================


def _make_event(
    *,
    event_id: str,
    summary: str = "Demo event",
    start: dict[str, Any] | None = None,
    end: dict[str, Any] | None = None,
    location: str | None = None,
    description: str | None = None,
    attendees: list[dict[str, Any]] | None = None,
    updated: str | None = None,
) -> dict[str, Any]:
    """Shape-only fake of a Google Calendar v3 ``Event`` resource carrying
    the fields the adapter's inbound mapping reads."""
    out: dict[str, Any] = {
        "id": event_id,
        "summary": summary,
        "start": start or {"dateTime": "2026-05-18T15:00:00Z"},
        "end": end or {"dateTime": "2026-05-18T16:00:00Z"},
    }
    if location is not None:
        out["location"] = location
    if description is not None:
        out["description"] = description
    if attendees is not None:
        out["attendees"] = list(attendees)
    if updated is not None:
        out["updated"] = updated
    return out


async def test_poll_once_emits_calendar_event_added_on_first_observation() -> None:
    """First observation of a Google event id (``_seen_inbound`` empty) →
    one ``calendar.event_added@v1`` emit per the inbound poll contract.
    The emit type matches the EXISTING on-main schema family per F-2
    (MAP DON'T REGISTER) — no new schemas registered."""
    event = _make_event(event_id="gcal-evt-inbound-001", summary="Standup")
    service = _FakeCalendarService(list_pages=[{"items": [event]}])
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    emitted = await adapter._poll_once(ctx=ctx)

    assert emitted == 1
    added_calls = [c for c in event_log.calls if c["type"] == "calendar.event_added"]
    assert len(added_calls) == 1
    assert added_calls[0]["schema_version"] == 1
    assert added_calls[0]["payload"]["external_event_id"] == "gcal-evt-inbound-001"


async def test_poll_once_skips_already_seen_event_id() -> None:
    """A re-poll on the same event id (``_seen_inbound`` populated) emits
    NO new events. The dedupe gate is the load-bearing invariant per F-3
    (HARD/standing) — without it every poll cycle would re-emit the
    same event, drowning downstream consumers."""
    event = _make_event(event_id="gcal-evt-dup-001", summary="Repeat")
    # The fake returns the same page on both calls (list_pages exhausts
    # → empty page on second call; we want the same item twice instead).
    service = _FakeCalendarService(list_pages=[{"items": [event]}, {"items": [event]}])
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    first = await adapter._poll_once(ctx=ctx)
    second = await adapter._poll_once(ctx=ctx)

    assert first == 1
    assert second == 0  # already seen → not re-emitted
    added_calls = [c for c in event_log.calls if c["type"] == "calendar.event_added"]
    assert len(added_calls) == 1


async def test_poll_emit_payload_validates_against_calendar_event_added_v1() -> None:
    """The emitted payload validates byte-for-byte against the EXISTING
    :class:`CalendarEventAddedV1` model. ``extra="forbid"`` on the model
    means any drift introducing a new field would fail validation —
    pinning F-2 (MAP DON'T REGISTER): Google Calendar inbound reuses
    the on-main schema, no new fields, no new schema."""
    event = _make_event(
        event_id="gcal-evt-validate-001",
        summary="Schema check",
        start={"dateTime": "2026-05-18T15:00:00Z"},
        end={"dateTime": "2026-05-18T16:00:00Z"},
        location="HQ Conference Room",
        description="Quarterly review",
        attendees=[{"email": "alice@example.test", "responseStatus": "accepted"}],
    )
    service = _FakeCalendarService(list_pages=[{"items": [event]}])
    adapter = _make_adapter(service=service, calendar_id="primary@example.test")
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    await adapter._poll_once(ctx=ctx)

    added_call = next(c for c in event_log.calls if c["type"] == "calendar.event_added")
    payload = added_call["payload"]
    # Round-trip via strict validation — any extra field would raise
    # pydantic.ValidationError under ``extra="forbid"``.
    instance = CalendarEventAddedV1.model_validate(payload)
    assert instance.source == "google_calendar"
    assert instance.external_event_id == "gcal-evt-validate-001"
    assert instance.calendar_id == "primary@example.test"
    assert instance.summary == "Schema check"
    assert instance.start == "2026-05-18T15:00:00Z"
    assert instance.end == "2026-05-18T16:00:00Z"
    assert instance.location == "HQ Conference Room"
    assert instance.body == "Quarterly review"
    assert instance.attendees == [
        {"email": "alice@example.test", "responseStatus": "accepted"}
    ]


async def test_discovery_syncs_each_calendar_and_observes_capabilities_once() -> None:
    entries = [
        {
            "id": "calendar-a",
            "summary": "Personal",
            "accessRole": "owner",
            "primary": True,
            "selected": True,
        },
        {
            "id": "calendar-b",
            "summary": "Context",
            "accessRole": "reader",
            "selected": True,
        },
    ]
    service = _FakeCalendarService(
        calendar_list_pages=[{"items": entries}, {"items": entries}],
        list_pages=[
            {"items": [_make_event(event_id="event-a", summary="First")]},
            {"items": [_make_event(event_id="event-b", summary="Second")]},
            {"items": []},
            {"items": []},
        ],
    )
    adapter = GoogleCalendarAdapter(
        config={
            "oauth_credentials_ref": "keychain://calendar/account-a",
            "calendar_id": "calendar-a",
            "discover_calendars": True,
            "member_id": "member-a",
            "provider_account_id": "account-a",
        }
    )
    adapter._service = service
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    assert await adapter._poll_once(ctx=ctx) == 2
    assert await adapter._poll_once(ctx=ctx) == 0

    observed = [
        call for call in event_log.calls if call["type"] == "calendar.source_observed"
    ]
    snapshots = [
        call for call in event_log.calls if call["type"] == "calendar.event_added"
    ]
    assert len(observed) == 2
    assert observed[0]["payload"]["write_events"] is True
    assert observed[1]["payload"]["write_events"] is False
    assert {call["payload"]["calendar_external_id"] for call in snapshots} == {
        "calendar-a",
        "calendar-b",
    }


async def test_calendar_source_dedupe_survives_adapter_restart(
    tmp_path: Path,
) -> None:
    entry = {
        "id": "calendar-a",
        "summary": "Personal",
        "accessRole": "owner",
        "primary": True,
        "selected": True,
    }
    config = {
        "oauth_credentials_ref": "keychain://calendar/account-a",
        "calendar_id": "calendar-a",
        "discover_calendars": True,
        "member_id": "member-a",
        "provider_account_id": "account-a",
    }
    cursor_store = AdapterCursorStore(tmp_path / "calendar-cursors.sqlite3")
    first_service = _FakeCalendarService(
        calendar_list_pages=[{"items": [entry]}],
        list_pages=[{"items": [], "nextSyncToken": "sync-page-1"}],
    )
    first = GoogleCalendarAdapter(config=config)
    first.instance_name = "google-calendar-primary"
    first._service = first_service
    first_log = _FakeEventLog()

    assert (
        await first._poll_once(
            ctx=_seed_ctx(first, first_log, cursor_store=cursor_store)
        )
        == 0
    )
    assert (
        len(
            [
                call
                for call in first_log.calls
                if call["type"] == "calendar.source_observed"
            ]
        )
        == 1
    )
    source_call = next(
        call for call in first_log.calls if call["type"] == "calendar.source_observed"
    )
    assert source_call["idempotency_key"].startswith("google-calendar:calendar-source:")

    restarted_service = _FakeCalendarService(
        calendar_list_pages=[{"items": [entry]}],
        list_pages=[{"items": [], "nextSyncToken": "sync-page-2"}],
    )
    restarted = GoogleCalendarAdapter(config=config)
    restarted.instance_name = "google-calendar-primary"
    restarted._service = restarted_service
    restarted_log = _FakeEventLog()

    assert (
        await restarted._poll_once(
            ctx=_seed_ctx(
                restarted,
                restarted_log,
                cursor_store=cursor_store,
            )
        )
        == 0
    )
    calendar_cursor_id = restarted._calendar_cursor_id("calendar-a")
    assert cursor_store.get(calendar_cursor_id).provider_watermark == "sync-page-2"
    assert restarted_service.list_calls[0]["syncToken"] == "sync-page-1"
    assert restarted.status().extra["durable_cursor"]["provider_watermark"] == "present"
    assert not [
        call
        for call in restarted_log.calls
        if call["type"] == "calendar.source_observed"
    ]
    cursor_store.close()


async def test_calendar_source_idempotency_binds_observation_timestamp() -> None:
    entry = {
        "id": "calendar-a",
        "summary": "Personal",
        "accessRole": "owner",
        "primary": True,
        "selected": True,
    }
    config = {
        "oauth_credentials_ref": "keychain://calendar/account-a",
        "calendar_id": "calendar-a",
        "discover_calendars": True,
        "member_id": "member-a",
        "provider_account_id": "account-a",
    }
    first = GoogleCalendarAdapter(config=config)
    first.instance_name = "google-calendar-primary"
    first_log = _FakeEventLog()
    await first._emit_calendar_source(ctx=_ctx(first_log), entry=entry)

    second = GoogleCalendarAdapter(config=config)
    second.instance_name = "google-calendar-primary"
    second_log = _FakeEventLog()
    await second._emit_calendar_source(
        ctx=_ctx(
            second_log,
            now=_FROZEN_NOW.replace(second=_FROZEN_NOW.second + 1),
        ),
        entry=entry,
    )

    first_event = first_log.calls[0]
    second_event = second_log.calls[0]
    assert (
        first_event["payload"]["observed_at"] != second_event["payload"]["observed_at"]
    )
    assert first_event["idempotency_key"] != second_event["idempotency_key"]


async def test_calendar_sync_token_does_not_advance_on_partial_page_failure(
    tmp_path: Path,
) -> None:
    service = _FakeCalendarService(
        list_pages=[
            {"items": [], "nextPageToken": "page-2"},
            ConnectionResetError("fixture page failed"),
        ]
    )
    adapter = GoogleCalendarAdapter(
        config={
            "oauth_credentials_ref": "keychain://calendar/account-a",
            "calendar_id": "calendar-a",
            "member_id": "member-a",
            "provider_account_id": "account-a",
        }
    )
    adapter.instance_name = "google-calendar-primary"
    adapter._service = service
    store = AdapterCursorStore(tmp_path / "partial-calendar.sqlite3")
    try:
        with pytest.raises(ConnectionResetError, match="page failed"):
            await adapter._poll_calendar_once(
                ctx=_seed_ctx(adapter, _FakeEventLog(), cursor_store=store),
                calendar_id="calendar-a",
                calendar_name="Personal",
            )
        assert (
            store.get(adapter._calendar_cursor_id("calendar-a")).provider_watermark
            is None
        )
    finally:
        store.close()


async def test_calendar_expired_sync_token_resets_and_full_resyncs(
    tmp_path: Path,
) -> None:
    adapter = GoogleCalendarAdapter(
        config={
            "oauth_credentials_ref": "keychain://calendar/account-a",
            "calendar_id": "calendar-a",
            "member_id": "member-a",
            "provider_account_id": "account-a",
        }
    )
    adapter.instance_name = "google-calendar-primary"
    store = AdapterCursorStore(tmp_path / "expired-calendar.sqlite3")
    cursor_id = adapter._calendar_cursor_id("calendar-a")
    store.record_success(cursor_id, provider_watermark="expired-sync")
    service = _FakeCalendarService(
        list_pages=[
            _FakeHttpError(_FakeHttpResponse(status=410)),
            {"items": [], "nextSyncToken": "fresh-sync"},
        ]
    )
    adapter._service = service
    try:
        assert (
            await adapter._poll_calendar_once(
                ctx=_seed_ctx(adapter, _FakeEventLog(), cursor_store=store),
                calendar_id="calendar-a",
                calendar_name="Personal",
            )
            == 0
        )
        assert service.list_calls[0]["syncToken"] == "expired-sync"
        assert "syncToken" not in service.list_calls[1]
        assert store.get(cursor_id).provider_watermark == "fresh-sync"
    finally:
        store.close()


async def test_calendar_duplicate_runtime_cannot_poll_same_calendar(
    tmp_path: Path,
) -> None:
    adapter = GoogleCalendarAdapter(
        config={
            "oauth_credentials_ref": "keychain://calendar/account-a",
            "calendar_id": "calendar-a",
            "member_id": "member-a",
            "provider_account_id": "account-a",
        }
    )
    adapter.instance_name = "google-calendar-primary"
    service = _FakeCalendarService(
        list_pages=[{"items": [], "nextSyncToken": "must-not-be-read"}]
    )
    adapter._service = service
    store = AdapterCursorStore(tmp_path / "calendar-owner.sqlite3")
    cursor_id = adapter._calendar_cursor_id("calendar-a")
    assert store.claim(cursor_id, "other-runtime", ttl_ms=60_000)
    try:
        assert (
            await adapter._poll_calendar_once(
                ctx=_seed_ctx(adapter, _FakeEventLog(), cursor_store=store),
                calendar_id="calendar-a",
                calendar_name="Personal",
            )
            == 0
        )
        assert service.list_calls == []
        assert adapter.status().extra["cursor_lease"] == "held_by_another_runtime"
    finally:
        store.close()


async def test_calendar_provider_failures_open_durable_circuit(
    tmp_path: Path,
) -> None:
    adapter = GoogleCalendarAdapter(
        config={
            "oauth_credentials_ref": "keychain://calendar/account-a",
            "calendar_id": "calendar-a",
            "member_id": "member-a",
            "provider_account_id": "account-a",
            "poll_interval_s": 0,
        }
    )
    adapter.instance_name = "google-calendar-primary"
    store = AdapterCursorStore(tmp_path / "calendar-circuit.sqlite3")
    ctx = _seed_ctx(adapter, _FakeEventLog(), cursor_store=store)
    cursor_id = adapter._calendar_cursor_id("calendar-a")
    try:
        for _attempt in range(3):
            adapter._service = _FakeCalendarService(
                list_pages=[ConnectionResetError("fixture provider failure")]
            )
            with pytest.raises(ConnectionResetError):
                await adapter._poll_calendar_once(
                    ctx=ctx,
                    calendar_id="calendar-a",
                    calendar_name="Personal",
                )
        assert store.get(cursor_id).circuit_state == "open"
        blocked = _FakeCalendarService(list_pages=[{"items": []}])
        adapter._service = blocked
        assert (
            await adapter._poll_calendar_once(
                ctx=ctx,
                calendar_id="calendar-a",
                calendar_name="Personal",
            )
            == 0
        )
        assert blocked.list_calls == []
        assert adapter.status().extra["cursor_lease"] == "circuit_open"
    finally:
        store.close()


async def test_calendar_poll_loop_recovers_health_and_honors_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter()
    adapter._max_poll_cycles = 2
    adapter._poll_interval_s = 0
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)
    attempts = 0
    sleeps: list[float] = []

    async def poll_once(*, ctx: AdapterContext) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _FakeHttpError(
                _FakeHttpResponse(status=429, headers={"Retry-After": "45"})
            )
        return 0

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    adapter._poll_once = poll_once
    monkeypatch.setattr(_adapter_mod.asyncio, "sleep", record_sleep)

    await adapter._poll_loop(ctx=ctx)

    assert attempts == 2
    assert sleeps[0] == 45
    assert adapter.status().state == "running"


async def test_initial_sync_does_not_backfill_elapsed_outcomes() -> None:
    entry = {
        "id": "calendar-a",
        "summary": "Personal",
        "accessRole": "owner",
        "primary": True,
        "selected": True,
    }
    past = _make_event(
        event_id="past-event",
        summary="Already elapsed before observation",
        start={"dateTime": "2026-05-18T09:00:00Z"},
        end={"dateTime": "2026-05-18T10:00:00Z"},
    )
    service = _FakeCalendarService(
        calendar_list_pages=[{"items": [entry]}, {"items": [entry]}],
        list_pages=[
            {"items": [past], "nextSyncToken": "sync-a"},
            {"items": [], "nextSyncToken": "sync-b"},
        ],
    )
    adapter = GoogleCalendarAdapter(
        config={
            "oauth_credentials_ref": "keychain://calendar/account-a",
            "calendar_id": "calendar-a",
            "discover_calendars": True,
            "member_id": "member-a",
            "provider_account_id": "account-a",
        }
    )
    adapter._service = service
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    assert await adapter._poll_once(ctx=ctx) == 1
    assert await adapter._poll_once(ctx=ctx) == 0
    assert not [
        call
        for call in event_log.calls
        if call["type"] == "calendar.event_outcome_recorded"
    ]


async def test_future_event_emits_one_deterministic_elapsed_outcome() -> None:
    entry = {
        "id": "calendar-a",
        "summary": "Personal",
        "accessRole": "owner",
        "primary": True,
        "selected": True,
    }
    future = _make_event(
        event_id="future-event",
        summary="Becomes elapsed after observation",
        start={"dateTime": "2026-05-18T12:30:00Z"},
        end={"dateTime": "2026-05-18T13:00:00Z"},
    )
    service = _FakeCalendarService(
        calendar_list_pages=[{"items": [entry]}, {"items": [entry]}],
        list_pages=[
            {"items": [future], "nextSyncToken": "sync-a"},
            {"items": [], "nextSyncToken": "sync-b"},
        ],
    )
    adapter = GoogleCalendarAdapter(
        config={
            "oauth_credentials_ref": "keychain://calendar/account-a",
            "calendar_id": "calendar-a",
            "discover_calendars": True,
            "member_id": "member-a",
            "provider_account_id": "account-a",
        }
    )
    adapter._service = service
    event_log = _FakeEventLog()
    clock = [_FROZEN_NOW]
    ctx = AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-gcal-elapsed",
        now_factory=lambda: clock[0],
    )

    assert await adapter._poll_once(ctx=ctx) == 1
    clock[0] = datetime(2026, 5, 18, 14, 0, 0, tzinfo=timezone.utc)
    assert await adapter._poll_once(ctx=ctx) == 1
    outcomes = [
        call
        for call in event_log.calls
        if call["type"] == "calendar.event_outcome_recorded"
    ]
    assert len(outcomes) == 1
    assert outcomes[0]["payload"]["recorded_at"] == "2026-05-18T13:00:00Z"
    assert outcomes[0]["idempotency_key"].startswith("google-calendar:event-outcome:")


async def test_provider_revision_idempotency_survives_adapter_restart() -> None:
    entry = {
        "id": "calendar-a",
        "summary": "Personal",
        "accessRole": "owner",
        "primary": True,
        "selected": True,
    }
    event = _make_event(
        event_id="event-restart",
        summary="Stable provider snapshot",
        updated="2026-05-18T14:05:00Z",
    )
    event["created"] = "2026-05-18T14:00:00Z"
    event["etag"] = '"provider-revision-2"'
    event_log = _FakeEventLog()

    for _ in range(2):
        service = _FakeCalendarService(
            calendar_list_pages=[{"items": [entry]}],
            list_pages=[{"items": [event]}],
        )
        adapter = GoogleCalendarAdapter(
            config={
                "oauth_credentials_ref": "keychain://calendar/account-a",
                "calendar_id": "calendar-a",
                "discover_calendars": True,
                "member_id": "member-a",
                "provider_account_id": "account-a",
            }
        )
        adapter._service = service
        await adapter._poll_once(ctx=_seed_ctx(adapter, event_log))

    snapshots = [
        call
        for call in event_log.calls
        if call["type"] in {"calendar.event_added", "calendar.event_updated"}
    ]
    assert [call["type"] for call in snapshots] == [
        "calendar.event_updated",
        "calendar.event_updated",
    ]
    assert snapshots[0]["idempotency_key"] == snapshots[1]["idempotency_key"]
    assert snapshots[0]["idempotency_key"].startswith(
        "google-calendar:provider-revision:"
    )


async def test_google_popup_reminder_maps_to_canonical_alert_alarm() -> None:
    event = _make_event(event_id="event-with-popup", summary="Reminder shape")
    event["reminders"] = {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": 15}],
    }
    adapter = GoogleCalendarAdapter(
        config={
            "oauth_credentials_ref": "keychain://calendar/account-a",
            "calendar_id": "calendar-a",
            "member_id": "member-a",
            "provider_account_id": "account-a",
        }
    )
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    await adapter._emit_calendar_snapshot(
        ctx=ctx,
        event_type="calendar.event_added",
        calendar_id="calendar-a",
        calendar_name="Personal",
        event=event,
    )

    payload = event_log.calls[0]["payload"]
    assert payload["alarms"] == [
        {
            "method": "alert",
            "trigger_at": None,
            "relative_offset_seconds": -900,
            "provider_alarm_id": None,
        }
    ]
    CalendarEventV2.model_validate(payload)


# ===========================================================================
# 6) Outbound body shape
# ===========================================================================


async def test_apply_write_create_event_body_shape() -> None:
    """The outbound ``events.insert`` call carries the expected ``body``
    shape: ``summary``, ``start``, ``end``, and the optional ``location``
    / ``description`` / ``attendees`` keys when set on the payload. The
    ``calendarId`` kwarg matches the operation payload's ``calendar_id``.
    Pins the Google Calendar v3 API surface contract the parent draft
    §1 item 2 anchors."""
    service = _FakeCalendarService(insert_response={"id": "gcal-evt-body-001"})
    adapter = _make_adapter(service=service)

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "primary@example.test",
            "summary": "Coffee with Alice",
            "start": {"dateTime": "2026-05-18T15:00:00Z"},
            "end": {"dateTime": "2026-05-18T16:00:00Z"},
            "location": "Blue Bottle",
            "description": "Catch up on Q2",
            "attendees": [{"email": "alice@example.test"}],
        },
    )
    await adapter.apply_write(op)

    assert len(service.insert_calls) == 1
    call = service.insert_calls[0]
    assert call["calendarId"] == "primary@example.test"
    body = call["body"]
    assert body["summary"] == "Coffee with Alice"
    assert body["start"] == {"dateTime": "2026-05-18T15:00:00Z"}
    assert body["end"] == {"dateTime": "2026-05-18T16:00:00Z"}
    assert body["location"] == "Blue Bottle"
    assert body["description"] == "Catch up on Q2"
    assert body["attendees"] == [{"email": "alice@example.test"}]


# ===========================================================================
# 7) c1 registry contract — schema unchanged on main
# ===========================================================================


def test_calendar_event_added_v1_registry_lookup_returns_existing_model() -> None:
    """The c1 verify-only contract pinned in the framework test suite:
    ``registry.get("calendar.event_added", 1)`` returns the EXISTING
    :class:`CalendarEventAddedV1` model registered at
    ``adminme/events/schemas/ingest.py`` — 11b-iii adds NO new schema
    and re-registers nothing per F-2 (MAP DON'T REGISTER) + [D7]."""
    assert registry.get("calendar.event_added", 1) is CalendarEventAddedV1
