"""Cross-module Cat-B integration tests for the Google Calendar adapter
(prompt 11b-iii c4 deliverable 2 — first fresh canonical-build Cat-B
adapter; default-CI sibling to the framework + pack-internal suites).

Sits alongside ``test_adapter_apple_eventkit_supervisor_retrofit.py``
(11r-c retrofit + 11b-i dual-emit) as the second Cat-B integration test
file in the suite. Unlike the Apple Reminders sibling — which is a
retrofit + dual-emit on a v1 shipped pack — Google Calendar is the FIRST
FRESH Cat-B adapter under the canonical-build pattern (prompts 11-α/β/γ
framework + 11a/11b-i precursors); there is no pre-retrofit baseline to
preserve and no dual-emit migration to verify. The three tests in this
file pin the contract surface the supervisor's loader path observes for
a fresh Cat-B pack:

- the supervisor's loader (file-path importlib — the same path the
  supervisor uses at runtime because ``google-calendar``'s hyphen
  forbids a ``from packs...google-calendar.adapter`` import) produces a
  class that inherits from :class:`ExternalStateMirrorAdapter` and from
  none of the other four ABCs ([PM-34] mutual-exclusion);

- the loaded class declares the asymmetric ``write_capabilities ==
  ["create_event"]`` per [UT-27]; the base-class :meth:`apply_write`
  gate raises :class:`WriteCapabilityError` for any undeclared kind
  (e.g. ``update_event`` / ``delete_event`` — both out of scope per
  parent draft + §6) BEFORE the Google Calendar API is touched;

- the inbound poll's ``_poll_once`` emits ``calendar.event_added@v1``
  on first observation of a Google event id with a payload that
  validates byte-for-byte against the on-main
  :class:`CalendarEventAddedV1` schema. Pins F-2 (MAP DON'T REGISTER)
  at the integration seam — the schema-source layer is pinned by the
  framework test, the pack layer by the pack-internal test, and this
  layer pins the same contract through the supervisor's loader path
  (so any future loader-side regression that mangles the import or the
  module-load registration is caught here).

Cat-B specifics vs the Cat-A (Gmail send) integration sibling: the
gate-rejection assertion in test (2) is the load-bearing addition —
Cat-B has TWO abstract methods + the [UT-27] gate + the [§6.20] wrap
(observation-mode contract); Cat-A has only ``send``. The fresh-pack
discriminator from the retrofit pattern is that the validator's
``base_class_conformance`` finding for this pack is NOT
``advisory_pending_retrofit`` (which only attaches to v1 packs that
predate the framework's ABCs — fresh packs like this one declare the
inheritance directly and the check passes cleanly with no advisory).

The supervisor's full lifecycle test (start / stop / poll-loop /
backoff) lives in the canonical Gmail integration file
(``test_adapter_supervisor_gmail.py``) and is exercised end-to-end
there; for Google Calendar's first fresh Cat-B canonical-build, this
default-CI sibling pins the loader + ABC + gate + emit contract at the
integration seam. A future ``test_adapter_google_calendar_supervisor_live.py``
under ``requires_live_services`` will exercise the full poll-loop +
restart-with-backoff path against the real Google Calendar v3 API per
the Plaid-supervisor precedent.

``packs/adapters/calendaring/google-calendar`` includes a hyphen in the
final directory segment and is therefore not a valid Python package
name; the adapter class is loaded via the file-path importlib pattern
shared with the framework test at
``tests/unit/adapters/calendaring/test_google_calendar.py`` and the
pack-internal test at
``packs/adapters/calendaring/google-calendar/tests/test_google_calendar_pack_internal.py``.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.schemas.ingest import CalendarEventAddedV1
from adminme.lib.adapters import (
    CommunicationAdapter,
    ExternalStateMirrorAdapter,
    InboundDataAdapter,
    OutboundActionAdapter,
    PersonalKnowledgeAdapter,
    WriteCapabilityError,
    WriteOperation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOOGLE_CALENDAR_PACK_ROOT = (
    REPO_ROOT / "packs" / "adapters" / "calendaring" / "google-calendar"
)


def _load_google_calendar_module() -> Any:
    """Load ``adapter.py`` from the pack root by file path.

    Same file-path importlib pattern the supervisor's loader uses at
    runtime — packs are not a top-level Python package under poetry's
    package config (only ``adminme`` is), and ``google-calendar``'s
    hyphen forbids a ``from packs.adapters.calendaring.google-calendar
    .adapter`` statement entirely. Mirrors the framework + pack-internal
    suite loaders so all three test layers exercise the same import
    seam."""
    spec = importlib.util.spec_from_file_location(
        "google_calendar_adapter_integration_under_test",
        GOOGLE_CALENDAR_PACK_ROOT / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_google_calendar_module()
GoogleCalendarAdapter: Any = _adapter_mod.GoogleCalendarAdapter


_INTEGRATION_FROZEN_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


class _IntegrationEventLog:
    """Captures synchronous ``append_sync`` calls for assertion. Mirrors
    the duck-typed contract :meth:`GoogleCalendarAdapter._append` reads
    via ``getattr(ctx.event_log, "append_sync", ...)``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append_sync(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _make_integration_ctx(event_log: _IntegrationEventLog) -> AdapterContext:
    return AdapterContext(
        event_log=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-integration-1",
        now_factory=lambda: _INTEGRATION_FROZEN_NOW,
    )


class _IntegrationListRequest:
    """``events().list(...).execute()`` half of the fake Calendar service."""

    def __init__(
        self, pages: list[dict[str, Any]], call_log: list[dict[str, Any]],
    ) -> None:
        self._pages = pages
        self._call_log = call_log

    def execute(self) -> dict[str, Any]:
        if self._pages:
            return dict(self._pages.pop(0))
        return {"items": []}


class _IntegrationEventsResource:
    def __init__(self, service: "_IntegrationCalendarService") -> None:
        self._service = service

    def list(self, **kwargs: Any) -> _IntegrationListRequest:
        self._service.list_calls.append(kwargs)
        return _IntegrationListRequest(
            self._service._list_pages, self._service.list_calls
        )

    def insert(self, **kwargs: Any) -> Any:  # pragma: no cover — no insert tests
        self._service.insert_calls.append(kwargs)
        raise AssertionError(
            "events().insert(...) is not exercised by these integration tests"
        )


class _IntegrationCalendarService:
    """Minimal Google Calendar v3 ``service`` surface — supports
    ``service.events().list(...).execute()``. Insert is intentionally
    absent because the gate-rejection test (2) never reaches the impl
    and the inbound-emit test (3) does not write."""

    def __init__(self, list_pages: list[dict[str, Any]] | None = None) -> None:
        self._list_pages = list(list_pages or [])
        self.list_calls: list[dict[str, Any]] = []
        self.insert_calls: list[dict[str, Any]] = []

    def events(self) -> _IntegrationEventsResource:
        return _IntegrationEventsResource(self)


def _make_adapter(
    *, service: _IntegrationCalendarService | None = None,
) -> Any:
    """Construct a :class:`GoogleCalendarAdapter` skipping ``start()``.

    The full lifecycle (start / stop / poll loop) lives in the future
    ``test_adapter_google_calendar_supervisor_live.py`` under
    ``requires_live_services``. The three tests here only pin the
    loader + ABC + gate + emit contract at the integration seam, so the
    real ``EKEventStore``-equivalent (the Google Calendar v3 service) is
    seeded directly via ``self._service`` without going through the
    credentials resolver."""
    adapter = GoogleCalendarAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "calendar_id": "primary@example.test",
            "user_email": "operator@example.test",
        },
    )
    if service is not None:
        adapter._service = service
    return adapter


# ===========================================================================
# 1) [PM-34] mutual-exclusion + Cat-B inheritance through the loader.
# ===========================================================================


def test_supervisor_loaded_adapter_inherits_external_state_mirror_adapter() -> None:
    """The supervisor's adapter-loading code path (file-path importlib —
    same path the supervisor uses at runtime) yields a class that
    inherits from :class:`ExternalStateMirrorAdapter` and is NOT a
    subclass of any of the other four ABCs per [PM-34]. The validator's
    ``base_class_conformance`` check observes this via AST walk against
    the same file; this integration test pins the same contract at the
    runtime-loader seam. First fresh Cat-B canonical-build adapter:
    unlike the Apple Reminders sibling (which is a retrofit), this
    inheritance was declared at authorship time per the canonical-build
    pattern, so no ``advisory_pending_retrofit`` finding attaches."""
    assert issubclass(GoogleCalendarAdapter, ExternalStateMirrorAdapter)
    # [PM-34] mutual-exclusion across the 5 ABCs.
    assert not issubclass(GoogleCalendarAdapter, CommunicationAdapter)
    assert not issubclass(GoogleCalendarAdapter, InboundDataAdapter)
    assert not issubclass(GoogleCalendarAdapter, OutboundActionAdapter)
    assert not issubclass(GoogleCalendarAdapter, PersonalKnowledgeAdapter)


# ===========================================================================
# 2) [UT-27] asymmetric write_capabilities gate through the loader.
# ===========================================================================


def test_supervisor_write_capabilities_gate_through_loader() -> None:
    """The [UT-27] gate on the base-class :meth:`apply_write` is what
    makes the asymmetric ``write_capabilities`` declaration load-bearing
    rather than decorative. The shipped outbound surface is
    ``create_event`` ONLY; ``update_event`` / ``delete_event`` are out
    of scope per parent draft + §6 (deferred to 11b-iii-β). The
    framework test pins the gate at the class-shape layer; this
    integration test pins it on the instance the supervisor would
    actually run, closing the loop between the contract
    (``write_capabilities`` ClassVar + base-class gate) and the
    supervisor's runtime invariant. The Google Calendar v3 API is NOT
    touched — the gate rejects the operation BEFORE the impl is
    dispatched."""
    adapter = _make_adapter()
    assert GoogleCalendarAdapter.write_capabilities == ["create_event"]
    assert hasattr(adapter, "apply_write")
    assert callable(adapter.apply_write)

    op = WriteOperation(
        kind="update_event",
        target_external_id="gcal-evt-1",
        payload={},
    )
    with pytest.raises(WriteCapabilityError):
        asyncio.run(adapter.apply_write(op))

    # The defense-in-depth check inside ``_apply_write_impl`` also
    # rejects ``delete_event`` directly (the gate would catch it first
    # in production; this asserts the inner guard is wired correctly).
    delete_op = WriteOperation(
        kind="delete_event",
        target_external_id="gcal-evt-2",
        payload={},
    )
    with pytest.raises(WriteCapabilityError):
        asyncio.run(adapter.apply_write(delete_op))


# ===========================================================================
# 3) Inbound poll emits ``calendar.event_added@v1`` via the loader path.
# ===========================================================================


def test_supervisor_inbound_poll_emits_calendar_event_added() -> None:
    """The supervisor-loaded ``GoogleCalendarAdapter`` (loaded via the
    file-path importlib pattern the supervisor uses at runtime) emits
    ``calendar.event_added@v1`` on first observation of a Google event
    id, with a payload that validates byte-for-byte against the on-main
    :class:`CalendarEventAddedV1` schema. F-2 (MAP DON'T REGISTER)
    pinned at the integration seam: any future loader-side regression
    that mangles the import surface or the module-load registration
    would fail here first. ``extra="forbid"`` on the schema means any
    drift introducing a new field would fail validation; the
    ``min_length=1`` on ``source`` / ``external_event_id`` /
    ``calendar_id`` / ``start`` / ``end`` would reject empties."""
    google_event: dict[str, Any] = {
        "id": "gcal-evt-integration-001",
        "summary": "Integration emit",
        "start": {"dateTime": "2026-05-18T15:00:00Z"},
        "end": {"dateTime": "2026-05-18T16:00:00Z"},
        "location": "HQ Conference Room",
        "description": "Integration round-trip",
        "attendees": [
            {"email": "alice@example.test", "responseStatus": "accepted"}
        ],
        "updated": "2026-05-18T14:00:00Z",
    }
    service = _IntegrationCalendarService(
        list_pages=[{"items": [google_event]}]
    )
    adapter = _make_adapter(service=service)
    event_log = _IntegrationEventLog()
    ctx = _make_integration_ctx(event_log)
    adapter._ctx = ctx

    emitted = asyncio.run(adapter._poll_once(ctx=ctx))

    assert emitted == 1
    added_calls = [
        c for c in event_log.calls if c["type"] == "calendar.event_added"
    ]
    assert len(added_calls) == 1
    payload = added_calls[0]["payload"]
    instance = CalendarEventAddedV1.model_validate(payload)
    assert instance.source == "google_calendar"
    assert instance.external_event_id == "gcal-evt-integration-001"
    assert instance.calendar_id == "primary@example.test"
    assert instance.summary == "Integration emit"
    assert instance.start == "2026-05-18T15:00:00Z"
    assert instance.end == "2026-05-18T16:00:00Z"
    assert instance.location == "HQ Conference Room"
    assert instance.body == "Integration round-trip"
    assert instance.attendees == [
        {"email": "alice@example.test", "responseStatus": "accepted"}
    ]
    # Re-poll on the same id is a no-op per the inbound dedupe gate
    # (F-3 of c2): _seen_inbound populated → no second emit.
    service._list_pages = [{"items": [google_event]}]
    emitted_second = asyncio.run(adapter._poll_once(ctx=ctx))
    assert emitted_second == 0
    added_calls_after = [
        c for c in event_log.calls if c["type"] == "calendar.event_added"
    ]
    assert len(added_calls_after) == 1
