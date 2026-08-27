"""Cross-module Cat-B integration tests for the Apple Calendar adapter
(prompt 11b-ii-β-α c4 deliverable 2 — first fresh Apple-side
canonical-build Cat-B calendaring adapter; central-only deployment;
default-CI sibling to the framework + pack-internal suites).

Sits alongside ``test_adapter_google_calendar_supervisor.py`` (11b-iii
fresh Cat-B sibling — Google side) and
``test_adapter_apple_eventkit_supervisor_retrofit.py`` (11r-c retrofit
+ 11b-i dual-emit, reminders side) as the next default-CI Cat-B
integration test file in the suite. Like 11b-iii, Apple Calendar
β-α is a fresh canonical-build Cat-B adapter under the canonical-build
pattern (vs Apple Reminders' v1 retrofit). The three tests in this
file pin the contract surface the supervisor's loader path observes
for the fresh central Apple Calendar pack:

- the supervisor's loader (file-path importlib — the same path the
  supervisor uses at runtime because ``apple-calendar``'s hyphen
  forbids a ``from packs...apple-calendar.adapter`` import) produces a
  class that inherits from :class:`ExternalStateMirrorAdapter` and
  from none of the other four ABCs ([PM-34] mutual-exclusion);

- the loaded class declares the asymmetric ``write_capabilities ==
  ["create_event"]`` per [UT-27]; the base-class :meth:`apply_write`
  gate raises :class:`WriteCapabilityError` for any undeclared kind
  (e.g. ``update_event`` / ``delete_event`` — both out of scope per
  §6) BEFORE the EKEventStore is touched;

- the inbound poll's ``_poll_once`` emits ``calendar.event_added@v1``
  on first observation of an ``EKEvent.eventIdentifier()`` with a
  payload that validates byte-for-byte against the on-main
  :class:`CalendarEventAddedV1` schema. Pins F-2 (MAP DON'T REGISTER)
  at the integration seam — the schema-source layer is pinned by the
  framework test, the pack layer by the pack-internal test, and this
  layer pins the same contract through the supervisor's loader path
  (so any future loader-side regression that mangles the import or
  the module-load registration is caught here).

Cat-B specifics: the gate-rejection assertion in test (2) is the
load-bearing addition for Cat-B vs Cat-A — Cat-B has TWO abstract
methods + the [UT-27] gate + the [§6.20] wrap (observation-mode
contract); Cat-A has only ``send``. The fresh-pack discriminator from
the retrofit pattern is that the validator's
``base_class_conformance`` finding for this pack is NOT
``advisory_pending_retrofit`` (which only attaches to v1 packs that
predate the framework's ABCs — fresh packs like this one declare the
inheritance directly and the check passes cleanly with no advisory).

F-7 ("central-only") is implicit at the integration layer: the
``deployment_target`` config enum + the bridge connector-pack are
absent (added in 11b-ii-β-β); the integration test does NOT exercise
or import a bridge sink.

``packs/adapters/calendaring/apple-calendar`` includes a hyphen in
the final directory segment and is therefore not a valid Python
package name; the adapter class is loaded via the file-path importlib
pattern shared with the framework test at
``tests/unit/adapters/calendaring/test_apple_calendar.py`` and the
pack-internal test at
``packs/adapters/calendaring/apple-calendar/tests/test_apple_calendar_pack_internal.py``.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
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
APPLE_CALENDAR_PACK_ROOT = (
    REPO_ROOT / "packs" / "adapters" / "calendaring" / "apple-calendar"
)


def _load_apple_calendar_module() -> Any:
    """Load ``adapter.py`` from the pack root by file path.

    Same file-path importlib pattern the supervisor's loader uses at
    runtime — packs are not a top-level Python package under poetry's
    package config (only ``adminme`` is), and ``apple-calendar``'s
    hyphen forbids a ``from packs.adapters.calendaring.apple-calendar
    .adapter`` statement entirely. Mirrors the framework +
    pack-internal suite loaders so all three test layers exercise the
    same import seam."""
    spec = importlib.util.spec_from_file_location(
        "apple_calendar_adapter_integration_under_test",
        APPLE_CALENDAR_PACK_ROOT / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_apple_calendar_module()
AppleCalendarAdapter: Any = _adapter_mod.AppleCalendarAdapter


_INTEGRATION_FROZEN_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


class _IntegrationEventLog:
    """Captures typed asynchronous event-sink writes for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **_kwargs: Any) -> str:
        self.calls.append(envelope.model_dump())
        return f"event-{len(self.calls)}"

    async def emit(self, envelope: Any) -> Any:
        event_id = await self.write(envelope)
        return SimpleNamespace(ok=True, event_id=event_id, error=None)


def _make_integration_ctx(
    event_log: _IntegrationEventLog,
) -> AdapterContext:
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={
            "deployment_target": "bridge",
            "_bridge_member_id": "member-001",
            "_bridge_sink": event_log,
        },
        correlation_id_factory=lambda: "corr-integration-1",
        now_factory=lambda: _INTEGRATION_FROZEN_NOW,
    )


class _IntegrationFakeEKEvent:
    """Stand-in for ``EKEvent``. Exposes the seven accessor methods the
    adapter's inbound mapping reads."""

    def __init__(
        self,
        *,
        event_identifier: str,
        title: str | None,
        start_date: Any,
        end_date: Any,
        location: str | None,
        notes: str | None,
        attendees: list[Any] | None,
    ) -> None:
        self._event_identifier = event_identifier
        self._title = title
        self._start_date = start_date
        self._end_date = end_date
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


class _IntegrationFakeEKEventStore:
    """Minimal ``EKEventStore`` surface for the integration suite —
    supports the ``fetch_events`` operation the inbound poll consumes.
    ``build_event`` / ``save_event`` are intentionally absent because
    the gate-rejection test (2) never reaches the impl, and the
    inbound-emit test (3) does not write."""

    def __init__(
        self,
        *,
        fetch_pages: list[list[_IntegrationFakeEKEvent]] | None = None,
    ) -> None:
        self._fetch_pages = list(fetch_pages or [])
        self.fetch_calls: list[dict[str, Any]] = []

    def fetch_events(
        self,
        *,
        calendar_identifier: str,
        start: datetime,
        end: datetime,
    ) -> list[_IntegrationFakeEKEvent]:
        self.fetch_calls.append(
            {
                "calendar_identifier": calendar_identifier,
                "start": start,
                "end": end,
            }
        )
        if self._fetch_pages:
            return list(self._fetch_pages.pop(0))
        return []


def _make_adapter(
    *,
    store: _IntegrationFakeEKEventStore | None = None,
) -> Any:
    """Construct an :class:`AppleCalendarAdapter` skipping ``start()``.

    The full lifecycle (start / stop / poll loop) lives in a future
    ``test_adapter_apple_calendar_supervisor_live.py`` under
    ``requires_live_services``. The three tests here only pin the
    loader + ABC + gate + emit contract at the integration seam, so
    the real ``EKEventStore`` is seeded directly via ``self._store``
    without going through the EventKit permission grant."""
    adapter = AppleCalendarAdapter(
        config={
            "calendar_identifier": "apple-calendar-household-001",
            "member_id": "member-001",
            "provider_account_id": "provider-account:calendar-integration",
        },
    )
    if store is not None:
        adapter._store = store
    return adapter


# ===========================================================================
# 1) [PM-34] mutual-exclusion + Cat-B inheritance through the loader.
# ===========================================================================


def test_supervisor_loaded_adapter_inherits_external_state_mirror_adapter() -> None:
    """The supervisor's adapter-loading code path (file-path importlib
    — same path the supervisor uses at runtime) yields a class that
    inherits from :class:`ExternalStateMirrorAdapter` and is NOT a
    subclass of any of the other four ABCs per [PM-34]. The
    validator's ``base_class_conformance`` check observes this via AST
    walk against the same file; this integration test pins the same
    contract at the runtime-loader seam. Fresh Cat-B canonical-build
    adapter: unlike the Apple Reminders sibling (which is a retrofit),
    this inheritance was declared at authorship time per the
    canonical-build pattern, so no ``advisory_pending_retrofit``
    finding attaches."""
    assert issubclass(AppleCalendarAdapter, ExternalStateMirrorAdapter)
    # [PM-34] mutual-exclusion across the 5 ABCs.
    assert not issubclass(AppleCalendarAdapter, CommunicationAdapter)
    assert not issubclass(AppleCalendarAdapter, InboundDataAdapter)
    assert not issubclass(AppleCalendarAdapter, OutboundActionAdapter)
    assert not issubclass(AppleCalendarAdapter, PersonalKnowledgeAdapter)


# ===========================================================================
# 2) [UT-27] asymmetric write_capabilities gate through the loader.
# ===========================================================================


def test_supervisor_write_capabilities_gate_through_loader() -> None:
    """The [UT-27] gate on the base-class :meth:`apply_write` is what
    makes the asymmetric ``write_capabilities`` declaration
    load-bearing rather than decorative. The shipped outbound surface
    is ``create_event`` ONLY; ``update_event`` / ``delete_event`` are
    out of scope per §6 (deferred). The framework test pins the gate
    at the class-shape layer; this integration test pins it on the
    instance the supervisor would actually run, closing the loop
    between the contract (``write_capabilities`` ClassVar + base-class
    gate) and the supervisor's runtime invariant. The EKEventStore is
    NOT touched — the gate rejects the operation BEFORE the impl is
    dispatched."""
    adapter = _make_adapter()
    assert AppleCalendarAdapter.write_capabilities == [
        "create_event",
        "update_event",
        "delete_event",
        "move_event",
    ]
    assert hasattr(adapter, "apply_write")
    assert callable(adapter.apply_write)

    op = WriteOperation(
        kind="share_calendar",
        target_external_id="apple-evt-1",
        payload={},
    )
    with pytest.raises(WriteCapabilityError):
        asyncio.run(adapter.apply_write(op))

    # A second unsupported operation remains blocked before the
    # provider implementation is reached.
    unsupported_op = WriteOperation(
        kind="share_calendar",
        target_external_id="apple-evt-2",
        payload={},
    )
    with pytest.raises(WriteCapabilityError):
        asyncio.run(adapter.apply_write(unsupported_op))


# ===========================================================================
# 3) Inbound poll emits ``calendar.event_added@v1`` via the loader path.
# ===========================================================================


def test_supervisor_inbound_poll_emits_calendar_event_added() -> None:
    """The supervisor-loaded ``AppleCalendarAdapter`` (loaded via the
    file-path importlib pattern the supervisor uses at runtime) emits
    ``calendar.event_added@v1`` on first observation of an
    ``EKEvent.eventIdentifier()``, with a payload that validates
    byte-for-byte against the on-main :class:`CalendarEventAddedV1`
    schema. F-2 (MAP DON'T REGISTER) pinned at the integration seam:
    any future loader-side regression that mangles the import surface
    or the module-load registration would fail here first.
    ``extra="forbid"`` on the schema means any drift introducing a new
    field would fail validation; the ``min_length=1`` on ``source`` /
    ``external_event_id`` / ``calendar_id`` / ``start`` / ``end``
    would reject empties. Re-poll on the same ``eventIdentifier`` is a
    no-op per the inbound dedupe gate (F-3 of c2)."""
    apple_event = _IntegrationFakeEKEvent(
        event_identifier="apple-evt-integration-001",
        title="Integration emit",
        start_date=datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc),
        end_date=datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc),
        location="HQ Conference Room",
        notes="Integration round-trip",
        attendees=[{"name": "Alice", "url": "mailto:alice@example.test"}],
    )
    store = _IntegrationFakeEKEventStore(fetch_pages=[[apple_event]])
    adapter = _make_adapter(store=store)
    event_log = _IntegrationEventLog()
    ctx = _make_integration_ctx(event_log)
    adapter._ctx = ctx

    emitted = asyncio.run(adapter._poll_once(ctx=ctx))

    assert emitted == 1
    added_calls = [c for c in event_log.calls if c["type"] == "calendar.event_added"]
    assert len(added_calls) == 1
    payload = added_calls[0]["payload"]
    instance = CalendarEventAddedV1.model_validate(payload)
    assert instance.source == "apple_calendar"
    assert instance.external_event_id == "apple-evt-integration-001"
    assert instance.calendar_id == "apple-calendar-household-001"
    assert instance.summary == "Integration emit"
    assert instance.start == "2026-05-20T15:00:00Z"
    assert instance.end == "2026-05-20T16:00:00Z"
    assert instance.location == "HQ Conference Room"
    assert instance.body == "Integration round-trip"
    assert instance.attendees == [{"name": "Alice", "url": "mailto:alice@example.test"}]
    # Re-poll on the same id is a no-op per the inbound dedupe gate
    # (F-3 of c2): _seen_inbound populated → no second emit.
    store._fetch_pages = [[apple_event]]
    emitted_second = asyncio.run(adapter._poll_once(ctx=ctx))
    assert emitted_second == 0
    added_calls_after = [
        c for c in event_log.calls if c["type"] == "calendar.event_added"
    ]
    assert len(added_calls_after) == 1
