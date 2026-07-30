"""Framework-level tests for the Apple Calendar Cat-B adapter pack.

Closes prompt 11b-ii-β-α §7 c3 deliverable (≥15 tests, basename unique per
F-4 against the Google Calendar / Apple Reminders pack basenames in this
tree). Verifies the fresh Cat-B implementation shipped in 11b-ii-β-α c2
against:

- :class:`ExternalStateMirrorAdapter` inheritance + ``family="calendaring"``
  + ``write_capabilities=["create_event"]`` (asymmetric per [UT-27]);
- the :meth:`apply_write` gate raising :class:`WriteCapabilityError` for
  any undeclared operation kind BEFORE the EKEventStore is touched;
- the [§6.20] observation-wrap pattern: seam slots populated → routes
  through :func:`adminme.lib.observation.outbound`; unset → direct
  ``EKEventStore.save_event`` call; F-3 (HARD): ``OutboundResult(
  suppressed=True)`` → :class:`WriteResult` with ``applied=False,
  suppressed=True``;
- the AdapterErrorV1 taxonomy classification for the EventKit error
  surface (access-denied fatal / store-failure transient — mirrors the
  Apple Reminders adapter's EventKit taxonomy);
- the inbound ``_poll_once`` emit contract: first observation of an
  ``EKEvent.eventIdentifier()`` → ``calendar.event_added@v1``; re-poll on
  the same identifier → skip; payload validates byte-for-byte against the
  EXISTING :class:`CalendarEventAddedV1` schema (F-2 MAP DON'T REGISTER);
- the c1 registry-lookup contract (``registry.get("calendar.event_added",
  1)`` returns :class:`CalendarEventAddedV1`).

These tests live under ``tests/unit/adapters/calendaring/`` (framework
location, sibling to ``test_google_calendar.py``) because they exercise
the framework-level Cat-B contract + the canonical event-schema layer —
not the pack's internal manifest / validator surface (that's the c4
pack-internal deliverable).
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.registry import registry
from adminme.events.schemas.ingest import CalendarEventAddedV1
from adminme.lib.adapters import (
    ExternalStateMirrorAdapter,
    WriteCapabilityError,
    WriteOperation,
    WriteResult,
)
from adminme.lib.observation import OutboundResult

REPO_ROOT = Path(__file__).resolve().parents[4]
APPLE_CALENDAR_PACK_DIR = (
    REPO_ROOT / "packs" / "adapters" / "calendaring" / "apple-calendar"
)


def _load_apple_calendar_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root by file path. Mirrors the
    Gmail / Google Calendar / Apple Reminders test loaders: ``packs/`` is
    not a Python package under poetry's package config, so file-path
    importlib is the canonical loader for pack code from framework tests."""
    spec = importlib.util.spec_from_file_location(
        "apple_calendar_adapter_under_test",
        APPLE_CALENDAR_PACK_DIR / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_apple_calendar_adapter_module()
AppleCalendarAdapter: Any = _adapter_mod.AppleCalendarAdapter


_FROZEN_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


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


def _ctx(event_log: _FakeEventLog) -> AdapterContext:
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-apple-cal-test",
        now_factory=lambda: _FROZEN_NOW,
    )


class _FakeEKEvent:
    """Stand-in for ``EKEvent``. Exposes the seven accessor methods the
    adapter's inbound mapping + outbound build path read. Mirrors the
    PyObjC bridging surface documented in
    ``docs/reference/apple-eventkit/ekevent.md``."""

    def __init__(
        self,
        *,
        event_identifier: str,
        title: str | None = "Untitled",
        start_date: Any = None,
        end_date: Any = None,
        location: str | None = None,
        notes: str | None = None,
        attendees: list[Any] | None = None,
    ) -> None:
        self._event_identifier = event_identifier
        self._title = title
        self._start_date = (
            start_date
            if start_date is not None
            else datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc)
        )
        self._end_date = (
            end_date
            if end_date is not None
            else datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc)
        )
        self._location = location
        self._notes = notes
        self._attendees = attendees

    def eventIdentifier(self) -> str:
        return self._event_identifier

    def title(self) -> str | None:
        return self._title

    def startDate(self) -> Any:
        return self._start_date

    def endDate(self) -> Any:
        return self._end_date

    def location(self) -> str | None:
        return self._location

    def notes(self) -> str | None:
        return self._notes

    def attendees(self) -> list[Any] | None:
        return self._attendees


class _FakeEKEventStore:
    """Strict fake ``EKEventStore`` for the framework-level Cat-B tests.

    Carries the union of operations the adapter's inbound poll + outbound
    write paths consume (``fetch_events`` / ``build_event`` / ``save_event``)
    per the shim protocol in
    ``packs/adapters/calendaring/apple-calendar/_eventkit_shim.py``.

    ``fetch_pages`` is a list of event-lists returned in sequence by
    ``fetch_events``; once the sequence is exhausted, subsequent calls
    return an empty list so the adapter's polling loop terminates.

    ``mint_event_identifiers`` populates the ``eventIdentifier`` stamp on
    each ``build_event``-minted ``EKEvent`` so outbound assertions can
    pin the round-trip value.

    Setting ``save_raises`` or ``fetch_raises`` swaps the corresponding
    method out for an exception throw — covers the AdapterErrorV1 error
    classification path.
    """

    def __init__(
        self,
        *,
        fetch_pages: list[list[_FakeEKEvent]] | None = None,
        mint_event_identifiers: list[str] | None = None,
        save_raises: BaseException | None = None,
        fetch_raises: BaseException | None = None,
    ) -> None:
        self._fetch_pages = list(fetch_pages or [])
        self._mint_queue = list(mint_event_identifiers or [])
        self._save_raises = save_raises
        self._fetch_raises = fetch_raises
        self.fetch_calls: list[dict[str, Any]] = []
        self.builds: list[dict[str, Any]] = []
        self.saves: list[tuple[Any, bool]] = []

    def fetch_events(
        self,
        *,
        calendar_identifier: str,
        start: datetime,
        end: datetime,
    ) -> list[_FakeEKEvent]:
        self.fetch_calls.append(
            {
                "calendar_identifier": calendar_identifier,
                "start": start,
                "end": end,
            }
        )
        if self._fetch_raises is not None:
            raise self._fetch_raises
        if self._fetch_pages:
            return list(self._fetch_pages.pop(0))
        return []

    def build_event(
        self,
        *,
        calendar_identifier: str,
        summary: str,
        start: Any,
        end: Any,
        location: str | None,
        notes: str | None,
        attendees: list[Any] | None,
    ) -> _FakeEKEvent:
        self.builds.append(
            {
                "calendar_identifier": calendar_identifier,
                "summary": summary,
                "start": start,
                "end": end,
                "location": location,
                "notes": notes,
                "attendees": list(attendees) if attendees is not None else None,
            }
        )
        cid = (
            self._mint_queue.pop(0)
            if self._mint_queue
            else f"EKID-build-{len(self.builds)}"
        )
        return _FakeEKEvent(
            event_identifier=cid,
            title=summary,
            start_date=start,
            end_date=end,
            location=location,
            notes=notes,
            attendees=attendees,
        )

    def save_event(self, event: _FakeEKEvent, *, commit: bool) -> None:
        if self._save_raises is not None:
            raise self._save_raises
        self.saves.append((event, commit))


def _make_adapter(
    *,
    store: _FakeEKEventStore | None = None,
    observation_session: Any = None,
    observation_manager: Any = None,
    observation_event_log: Any = None,
    calendar_identifier: str = "apple-calendar-household-001",
) -> Any:
    """Construct an :class:`AppleCalendarAdapter` with a fake store
    pre-installed at ``_store`` (mirrors what :meth:`start` does in
    production via ``self._store = self._ek_event_store_factory()``)."""
    adapter = AppleCalendarAdapter(
        config={
            "calendar_identifier": calendar_identifier,
            "member_id": "member-001",
        },
        observation_session=observation_session,
        observation_manager=observation_manager,
        observation_event_log=observation_event_log,
    )
    if store is not None:
        adapter._store = store
    return adapter


def _seed_ctx(adapter: Any, event_log: _FakeEventLog) -> AdapterContext:
    """Attach an AdapterContext to the adapter — what
    :meth:`AppleCalendarAdapter.start` does in production via
    ``self._ctx = ctx``."""
    ctx = _ctx(event_log)
    adapter._ctx = ctx
    return ctx


# ===========================================================================
# 1) Cat-B inheritance + ClassVars
# ===========================================================================


def test_apple_calendar_adapter_inherits_external_state_mirror_adapter() -> None:
    """The pack's adapter is a Cat-B :class:`ExternalStateMirrorAdapter`
    subclass — load-bearing inheritance per memo 05 §1.2 / [PM-34]. The
    pack validator's ``base_class_conformance`` check rejects packs that
    fail this."""
    assert issubclass(AppleCalendarAdapter, ExternalStateMirrorAdapter)


def test_family_class_attribute_is_calendaring_literal() -> None:
    """``family`` is the ``"calendaring"`` literal — pinned at the
    class level so the validator can read it without instantiation.
    Memo 05 §3.2 + [D19] anchor the four-value family union; this is
    the calendaring tip on the Apple side."""
    assert AppleCalendarAdapter.family == "calendaring"


def test_write_capabilities_declared_asymmetrically() -> None:
    """[UT-27] asymmetric write surface — declare ONLY the per-operation
    kinds the adapter actually implements. 11b-ii-β-α ships ``create_event``
    only; ``update_event`` / ``delete_event`` are out of scope per §6 and
    intentionally NOT declared. A declared-but-not-implemented kind would
    fail the validator's method-presence rule per memo 05 §3.4."""
    assert AppleCalendarAdapter.write_capabilities == ["create_event"]
    assert len(AppleCalendarAdapter.write_capabilities) == 1


def test_sharing_model_discriminator_is_owner_member_id() -> None:
    """[memo 05 §2.5] Cat-B projection sharing model: per-member rows
    keyed by ``owner_member_id``. Pinned at the class level so the
    validator can verify the manifest agrees with the code."""
    assert AppleCalendarAdapter.sharing_model_discriminator == "owner_member_id"


# ===========================================================================
# 2) apply_write gate — [UT-27]
# ===========================================================================


async def test_apply_write_gate_raises_on_undeclared_kind() -> None:
    """The base class's :meth:`apply_write` gate raises
    :class:`WriteCapabilityError` for any operation whose ``kind`` is not
    in ``write_capabilities`` — BEFORE ``_apply_write_impl`` runs and
    therefore BEFORE EventKit is touched. ``update_event`` is the
    canonical undeclared kind for 11b-ii-β-α (out of scope per §6;
    deferred to a later split)."""
    adapter = _make_adapter()
    operation = WriteOperation(
        kind="update_event",
        target_external_id="apple-evt-1",
        payload={},
    )

    with pytest.raises(WriteCapabilityError):
        await adapter.apply_write(operation)


async def test_apply_write_dispatches_to_impl_on_declared_kind() -> None:
    """For ``kind="create_event"`` the gate passes and
    :meth:`apply_write` dispatches to :meth:`_apply_write_impl`.
    Monkeypatching the instance method records the dispatch without
    touching the real EventKit surface."""
    adapter = _make_adapter()
    captured: list[WriteOperation] = []

    async def _recording_impl(operation: WriteOperation) -> WriteResult:
        captured.append(operation)
        return WriteResult(
            applied=True, external_id="apple-evt-recorded", suppressed=False
        )

    adapter._apply_write_impl = _recording_impl  # type: ignore[method-assign]

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "apple-calendar-household-001",
            "summary": "Pickup at school",
            "start": datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc),
        },
    )
    result = await adapter.apply_write(op)

    assert len(captured) == 1
    assert captured[0].kind == "create_event"
    assert result.applied is True
    assert result.external_id == "apple-evt-recorded"


# ===========================================================================
# 3) Observation-mode wrap — [§6.20] + F-3 (HARD)
# ===========================================================================


async def test_apply_write_routes_through_observation_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """:meth:`_apply_write_impl` MUST route ``EKEventStore.save_event``
    through :func:`adminme.lib.observation.outbound` per [§6.20] when the
    three observation-mode seam slots are populated. The adapter imports
    the function under the alias ``observation_outbound`` in its own
    module namespace; monkeypatching THAT name records the wrap without
    needing real session / manager / event-log instances. The Phase A
    path leaves the seams unset and bypasses the wrap (see the next
    test)."""
    store = _FakeEKEventStore(mint_event_identifiers=["EKID-wrapped-001"])
    session_sentinel = object()
    manager_sentinel = object()
    obs_log_sentinel = object()
    adapter = _make_adapter(
        store=store,
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
        # ``save_event`` registers on the store mock.
        result = await action_fn()
        return OutboundResult(suppressed=False, result=result)

    monkeypatch.setattr(_adapter_mod, "observation_outbound", _fake_outbound)

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "apple-calendar-household-001",
            "summary": "Coffee with Alice",
            "start": datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc),
        },
    )
    result = await adapter.apply_write(op)

    assert len(captured) == 1
    call = captured[0]
    assert call["session"] is session_sentinel
    assert call["manager"] is manager_sentinel
    assert call["event_log"] is obs_log_sentinel
    assert call["action"] == "calendar.create_event"
    assert call["target_channel"] == "calendaring-apple-calendar"
    assert call["target_identifier"] == "apple-calendar-household-001"
    # The wrapped ``save_event`` actually fired against the fake store.
    assert len(store.saves) == 1
    assert result.applied is True
    assert result.external_id == "EKID-wrapped-001"
    assert result.suppressed is False


async def test_apply_write_observation_suppressed_returns_unapplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-3 (HARD): when :func:`observation_outbound` returns
    ``OutboundResult(suppressed=True)``, :meth:`_apply_write_impl` returns
    ``WriteResult(applied=False, external_id=None, suppressed=True)``.
    EventKit is NOT touched — observation mode is the final outbound
    filter per [§6.13] and the fake stub must therefore NOT invoke
    ``action_fn``."""
    store = _FakeEKEventStore()
    adapter = _make_adapter(
        store=store,
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
            "calendar_id": "apple-calendar-household-001",
            "summary": "Will not send",
            "start": datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc),
        },
    )
    result = await adapter.apply_write(op)

    assert result.applied is False
    assert result.external_id is None
    assert result.suppressed is True
    # The wrapped ``save_event`` was NOT invoked.
    assert store.saves == []


async def test_apply_write_direct_call_when_seams_unset_returns_applied() -> None:
    """Phase A (today): the supervisor's adapter loader does NOT yet
    populate the observation-seam slots, so :meth:`_apply_write_impl`
    falls through the wrap branch and direct-calls
    ``store.save_event(...)``. The 11r-c / 11a Phase-A-conditional
    pattern. ``WriteResult(applied=True, ...)`` returns with the
    EventKit-stamped ``eventIdentifier``."""
    store = _FakeEKEventStore(mint_event_identifiers=["EKID-direct-001"])
    adapter = _make_adapter(store=store)  # seams default to None

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "apple-calendar-household-001",
            "summary": "Direct call event",
            "start": datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc),
        },
    )
    result = await adapter.apply_write(op)

    assert len(store.saves) == 1
    saved_event, commit_flag = store.saves[0]
    assert commit_flag is True
    assert saved_event.eventIdentifier() == "EKID-direct-001"
    assert result.applied is True
    assert result.external_id == "EKID-direct-001"
    assert result.suppressed is False


# ===========================================================================
# 4) Error classification — AdapterErrorV1 taxonomy
# ===========================================================================


class _PermissionDeniedError(Exception):
    """Duck-types the EventKit access-denied surface. The classifier
    matches on the class name containing ``permission`` / ``auth`` /
    ``access`` per the Apple Reminders adapter's EventKit taxonomy."""


async def test_apply_write_permission_denied_classifies_fatal() -> None:
    """An access-denied error from ``store.save_event`` classifies as
    ``fatal`` / ``auth_failed``, emits exactly one ``adapter.error@v1``,
    AND re-raises so the supervisor's backoff handles re-grant prompts."""
    err = _PermissionDeniedError("EKEventStore: access not granted")
    store = _FakeEKEventStore(save_raises=err)
    adapter = _make_adapter(store=store)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "apple-calendar-household-001",
            "summary": "Auth fail",
            "start": datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc),
        },
    )

    with pytest.raises(_PermissionDeniedError):
        await adapter.apply_write(op)

    errors = [c for c in event_log.calls if c["type"] == "adapter.error"]
    assert len(errors) == 1
    payload = errors[0]["payload"]
    assert payload["severity"] == "fatal"
    assert payload["error_kind"] == "auth_failed"


async def test_apply_write_store_failure_classifies_transient() -> None:
    """A generic store-save failure (e.g. transient EventKit OSError)
    classifies as ``transient`` / ``network`` so the supervisor's
    backoff retries. One ``adapter.error@v1`` emitted; the exception
    propagates."""
    err = OSError("EKEventStore: transient store-save failure")
    store = _FakeEKEventStore(save_raises=err)
    adapter = _make_adapter(store=store)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "apple-calendar-household-001",
            "summary": "transient body",
            "start": datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc),
        },
    )

    with pytest.raises(OSError):
        await adapter.apply_write(op)

    errors = [c for c in event_log.calls if c["type"] == "adapter.error"]
    assert len(errors) == 1
    assert errors[0]["payload"]["severity"] == "transient"
    assert errors[0]["payload"]["error_kind"] == "network"


# ===========================================================================
# 5) Inbound poll — dedup + emit
# ===========================================================================


async def test_poll_once_emits_calendar_event_added_on_first_observation() -> None:
    """First observation of an ``EKEvent.eventIdentifier()`` (``_seen_inbound``
    empty) → one ``calendar.event_added@v1`` emit per the inbound poll
    contract. The emit type matches the EXISTING on-main schema family
    per F-2 (MAP DON'T REGISTER) — no new schemas registered."""
    event = _FakeEKEvent(
        event_identifier="apple-evt-inbound-001",
        title="Standup",
    )
    store = _FakeEKEventStore(fetch_pages=[[event]])
    adapter = _make_adapter(store=store)
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    emitted = await adapter._poll_once(ctx=ctx)

    assert emitted == 1
    added_calls = [c for c in event_log.calls if c["type"] == "calendar.event_added"]
    assert len(added_calls) == 1
    assert added_calls[0]["schema_version"] == 1
    assert added_calls[0]["payload"]["external_event_id"] == "apple-evt-inbound-001"


async def test_poll_once_skips_already_seen_event_identifier() -> None:
    """A re-poll on the same ``eventIdentifier`` (``_seen_inbound``
    populated) emits NO new events. The dedupe gate is the load-bearing
    invariant per F-3 (HARD/standing) — without it every poll cycle
    would re-emit the same event, drowning downstream consumers."""
    event = _FakeEKEvent(
        event_identifier="apple-evt-dup-001",
        title="Repeat",
    )
    store = _FakeEKEventStore(fetch_pages=[[event], [event]])
    adapter = _make_adapter(store=store)
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
    pinning F-2 (MAP DON'T REGISTER): Apple Calendar inbound reuses the
    on-main schema, no new fields, no new schema. ``source`` is
    ``"apple_calendar"`` per §1 item 3."""
    event = _FakeEKEvent(
        event_identifier="apple-evt-validate-001",
        title="Schema check",
        start_date=datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc),
        end_date=datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc),
        location="HQ Conference Room",
        notes="Quarterly review",
        attendees=[{"name": "Alice", "url": "mailto:alice@example.test"}],
    )
    store = _FakeEKEventStore(fetch_pages=[[event]])
    adapter = _make_adapter(
        store=store, calendar_identifier="apple-calendar-household-001"
    )
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    await adapter._poll_once(ctx=ctx)

    added_call = next(c for c in event_log.calls if c["type"] == "calendar.event_added")
    payload = added_call["payload"]
    # Round-trip via strict validation — any extra field would raise
    # pydantic.ValidationError under ``extra="forbid"``.
    instance = CalendarEventAddedV1.model_validate(payload)
    assert instance.source == "apple_calendar"
    assert instance.external_event_id == "apple-evt-validate-001"
    assert instance.calendar_id == "apple-calendar-household-001"
    assert instance.summary == "Schema check"
    assert instance.start == "2026-05-20T15:00:00Z"
    assert instance.end == "2026-05-20T16:00:00Z"
    assert instance.location == "HQ Conference Room"
    assert instance.body == "Quarterly review"
    assert instance.attendees == [{"name": "Alice", "url": "mailto:alice@example.test"}]


async def test_poll_fetch_uses_window_from_config_now() -> None:
    """The inbound predicate query uses ``[now, now + poll_window_days]``
    against ``ctx.now_factory()`` per the EventKit predicate-window
    pattern documented in
    ``docs/reference/apple-eventkit/ekeventstore.md``. Pins that the
    adapter never queries a backward window (would re-ingest history)."""
    store = _FakeEKEventStore(fetch_pages=[[]])
    adapter = _make_adapter(store=store)
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    await adapter._poll_once(ctx=ctx)

    assert len(store.fetch_calls) == 1
    call = store.fetch_calls[0]
    assert call["calendar_identifier"] == "apple-calendar-household-001"
    assert call["start"] == _FROZEN_NOW
    assert call["end"] > _FROZEN_NOW


# ===========================================================================
# 6) Outbound EKEvent body shape
# ===========================================================================


async def test_apply_write_create_event_ekevent_body_shape() -> None:
    """The outbound ``store.build_event`` call carries the expected
    ``EKEvent`` shape: ``summary``, ``start``, ``end``, and the optional
    ``location`` / ``notes`` / ``attendees`` keys mapped from the
    operation payload. The ``calendar_identifier`` matches the operation
    payload's ``calendar_id``. Pins the EventKit binding surface
    contract §1 item 2 anchors."""
    store = _FakeEKEventStore(mint_event_identifiers=["EKID-body-001"])
    adapter = _make_adapter(store=store)

    op = WriteOperation(
        kind="create_event",
        target_external_id=None,
        payload={
            "calendar_id": "apple-calendar-household-001",
            "summary": "Coffee with Alice",
            "start": datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc),
            "location": "Blue Bottle",
            "body": "Catch up on Q2",
            "attendees": [{"name": "Alice"}],
        },
    )
    result = await adapter.apply_write(op)

    assert len(store.builds) == 1
    build = store.builds[0]
    assert build["calendar_identifier"] == "apple-calendar-household-001"
    assert build["summary"] == "Coffee with Alice"
    assert build["start"] == datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc)
    assert build["end"] == datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc)
    assert build["location"] == "Blue Bottle"
    assert build["notes"] == "Catch up on Q2"
    assert build["attendees"] == [{"name": "Alice"}]
    assert len(store.saves) == 1
    assert result.external_id == "EKID-body-001"


# ===========================================================================
# 7) c1 registry contract — schema unchanged on main
# ===========================================================================


def test_calendar_event_added_v1_registry_lookup_returns_existing_model() -> None:
    """The c1 verify-only contract pinned in the framework test suite:
    ``registry.get("calendar.event_added", 1)`` returns the EXISTING
    :class:`CalendarEventAddedV1` model registered at
    ``adminme/events/schemas/ingest.py`` — 11b-ii-β-α adds NO new schema
    and re-registers nothing per F-2 (MAP DON'T REGISTER) + [D7]."""
    assert registry.get("calendar.event_added", 1) is CalendarEventAddedV1
