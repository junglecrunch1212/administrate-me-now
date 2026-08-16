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
from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.adapters.loader import load_builtin_adapters
from adminme.daemons.adapter_supervisor import AdapterSupervisor
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
    """Captures typed asynchronous event-sink writes for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **_kwargs: Any) -> str:
        self.calls.append(envelope.model_dump())
        return f"event-{len(self.calls)}"


class _IntegrationBarrierEventLog(_IntegrationEventLog):
    def __init__(self, blocked_type: str) -> None:
        super().__init__()
        self.blocked_type = blocked_type
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def write(self, envelope: Any, **kwargs: Any) -> str:
        if envelope.type == self.blocked_type and not self.entered.is_set():
            self.entered.set()
            await self.release.wait()
        return await super().write(envelope, **kwargs)


def _make_integration_ctx(
    event_log: _IntegrationEventLog,
    *,
    cursor_store: AdapterCursorStore | None = None,
) -> AdapterContext:
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-integration-1",
        now_factory=lambda: _INTEGRATION_FROZEN_NOW,
        cursor_store=cursor_store,
    )


class _IntegrationListRequest:
    """``events().list(...).execute()`` half of the fake Calendar service."""

    def __init__(
        self,
        pages: list[dict[str, Any] | BaseException],
        call_log: list[dict[str, Any]],
    ) -> None:
        self._pages = pages
        self._call_log = call_log

    def execute(self) -> dict[str, Any]:
        if self._pages:
            value = self._pages.pop(0)
            if isinstance(value, BaseException):
                raise value
            return dict(value)
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

    def __init__(
        self,
        list_pages: list[dict[str, Any] | BaseException] | None = None,
    ) -> None:
        self._list_pages = list(list_pages or [])
        self.list_calls: list[dict[str, Any]] = []
        self.insert_calls: list[dict[str, Any]] = []

    def events(self) -> _IntegrationEventsResource:
        return _IntegrationEventsResource(self)


def _make_adapter(
    *,
    service: _IntegrationCalendarService | None = None,
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
    assert GoogleCalendarAdapter.write_capabilities == [
        "create_event",
        "update_event",
        "delete_event",
        "respond_event",
        "move_event",
    ]
    assert hasattr(adapter, "apply_write")
    assert callable(adapter.apply_write)

    op = WriteOperation(
        kind="share_calendar",
        target_external_id="gcal-evt-1",
        payload={},
    )
    with pytest.raises(WriteCapabilityError):
        asyncio.run(adapter.apply_write(op))

    # A second unsupported operation remains blocked before the
    # provider implementation is reached.
    unsupported_op = WriteOperation(
        kind="share_calendar",
        target_external_id="gcal-evt-2",
        payload={},
    )
    with pytest.raises(WriteCapabilityError):
        asyncio.run(adapter.apply_write(unsupported_op))


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
        "attendees": [{"email": "alice@example.test", "responseStatus": "accepted"}],
        "updated": "2026-05-18T14:00:00Z",
    }
    service = _IntegrationCalendarService(list_pages=[{"items": [google_event]}])
    adapter = _make_adapter(service=service)
    event_log = _IntegrationEventLog()
    ctx = _make_integration_ctx(event_log)
    adapter._ctx = ctx

    emitted = asyncio.run(adapter._poll_once(ctx=ctx))

    assert emitted == 1
    added_calls = [c for c in event_log.calls if c["type"] == "calendar.event_added"]
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


async def test_production_loader_supervisor_reports_running_after_poll_recovery(
    tmp_path: Path,
) -> None:
    config = {
        "adapter_supervisor_port": 0,
        "adapters": [
            {
                "id": "calendar-recovery-fixture",
                "kind": "calendaring-google-calendar",
                "config": {
                    "oauth_credentials_ref": "keychain://fixture/google-calendar",
                    "calendar_id": "primary",
                    "member_id": "member-fixture",
                    "provider_account_id": "account-fixture",
                    "poll_interval_s": 60,
                },
            }
        ],
    }
    loaded = load_builtin_adapters(config, repository_root=REPO_ROOT)
    assert len(loaded) == 1
    adapter = loaded[0]
    adapter._credentials_resolver = lambda _ref: "{}"
    service = _IntegrationCalendarService(
        list_pages=[
            ConnectionResetError("fixture transient"),
            {
                "items": [
                    {
                        "id": "fixture-event",
                        "etag": "fixture-revision-1",
                        "summary": "Credential-free provider event",
                        "start": {"dateTime": "2026-05-18T15:00:00Z"},
                        "end": {"dateTime": "2026-05-18T16:00:00Z"},
                        "updated": "2026-05-18T14:00:00Z",
                    },
                    {
                        "id": "fixture-deleted-event",
                        "etag": "fixture-revision-2",
                        "status": "cancelled",
                        "updated": "2026-05-18T14:01:00Z",
                    },
                ],
                "nextSyncToken": "fixture-sync-token",
            },
        ]
    )
    adapter._service_factory = lambda *_args: service
    adapter._poll_interval_s = 0
    event_log = _IntegrationEventLog()
    cursor_store = AdapterCursorStore(tmp_path / "calendar-cursors.sqlite3")
    supervisor = AdapterSupervisor(
        config_path=tmp_path / "unused.yaml",
        config_data=config,
        adapter_loader=lambda _config: loaded,
        ctx_factory=lambda: _make_integration_ctx(event_log, cursor_store=cursor_store),
    )

    await supervisor.start()
    try:

        async def recovered() -> None:
            while True:
                inventory = supervisor.inventory()
                if (
                    len(service.list_calls) >= 2
                    and next(iter(inventory.values()))["state"] == "running"
                ):
                    return
                await asyncio.sleep(0)

        await asyncio.wait_for(recovered(), timeout=1)
        instance = next(iter(supervisor.inventory().values()))
        assert instance["adapter_name"] == "calendaring-google-calendar"
        assert instance["state"] == "running"
        assert instance["restart_count"] == 0
        durable = cursor_store.get("calendar-recovery-fixture")
        assert durable.provider_watermark is not None
        assert durable.circuit_state == "closed"
        assert durable.error_count == 0
        errors = [call for call in event_log.calls if call["type"] == "adapter.error"]
        assert len(errors) == 1
        assert errors[0]["payload"]["error_kind"] == "network"
        assert (
            len(
                [
                    call
                    for call in event_log.calls
                    if call["type"] == "calendar.event_added"
                ]
            )
            == 1
        )
        assert (
            len(
                [
                    call
                    for call in event_log.calls
                    if call["type"] == "calendar.event_deleted"
                ]
            )
            == 1
        )
    finally:
        await supervisor.stop()
        cursor_store.close()


async def test_production_supervisor_shutdown_cancels_inflight_calendar_promptly(
    tmp_path: Path,
) -> None:
    config = {
        "adapter_supervisor_port": 0,
        "adapters": [
            {
                "id": "calendar-shutdown-fixture",
                "kind": "calendaring-google-calendar",
                "config": {
                    "oauth_credentials_ref": "keychain://fixture/google-calendar",
                    "calendar_id": "primary",
                    "member_id": "member-fixture",
                    "provider_account_id": "account-fixture",
                    "poll_interval_s": 60,
                },
            }
        ],
    }
    [adapter] = load_builtin_adapters(config, repository_root=REPO_ROOT)
    adapter._credentials_resolver = lambda _ref: "{}"
    service = _IntegrationCalendarService(
        list_pages=[
            {
                "items": [
                    {
                        "id": "shutdown-inflight-event",
                        "etag": "shutdown-revision",
                        "summary": "Synthetic in-flight event",
                        "start": {"dateTime": "2026-05-18T15:00:00Z"},
                        "end": {"dateTime": "2026-05-18T16:00:00Z"},
                    }
                ],
                "nextSyncToken": "must-not-advance",
            }
        ]
    )
    adapter._service_factory = lambda *_args: service
    event_log = _IntegrationBarrierEventLog("calendar.event_added")
    cursor_store = AdapterCursorStore(tmp_path / "calendar-cursors.sqlite3")
    supervisor = AdapterSupervisor(
        config_path=tmp_path / "unused.yaml",
        config_data=config,
        adapter_loader=lambda _config: [adapter],
        ctx_factory=lambda: _make_integration_ctx(event_log, cursor_store=cursor_store),
    )

    await supervisor.start()
    try:
        await asyncio.wait_for(event_log.entered.wait(), timeout=1)
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(supervisor.stop(), timeout=0.5)
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 0.5
        assert elapsed < adapter._poll_interval_s / 10
        durable = cursor_store.get("calendar-shutdown-fixture")
        assert durable.lease_owner is None
        assert durable.provider_watermark is None
        assert durable.error_count == 0
        assert durable.circuit_state == "closed"
        assert [call for call in event_log.calls if call["type"] == "adapter.error"] == []
        assert adapter.status().state == "stopped"
        assert all(
            managed.task is None or managed.task.done()
            for managed in supervisor._managed.values()
        )
    finally:
        if not supervisor._stopping:
            await supervisor.stop()
        cursor_store.close()


async def test_two_production_supervisors_fence_calendar_lease_loser(
    tmp_path: Path,
) -> None:
    config = {
        "adapter_supervisor_port": 0,
        "adapters": [
            {
                "id": "calendar-contended-fixture",
                "kind": "calendaring-google-calendar",
                "config": {
                    "oauth_credentials_ref": "keychain://fixture/google-calendar",
                    "calendar_id": "primary",
                    "member_id": "member-fixture",
                    "provider_account_id": "account-fixture",
                    "poll_interval_s": 60,
                },
            }
        ],
    }
    [winner_adapter] = load_builtin_adapters(config, repository_root=REPO_ROOT)
    [loser_adapter] = load_builtin_adapters(config, repository_root=REPO_ROOT)
    winner_adapter._credentials_resolver = lambda _ref: "{}"
    loser_adapter._credentials_resolver = lambda _ref: "{}"
    winner_service = _IntegrationCalendarService(
        list_pages=[{"items": [], "nextSyncToken": "winner-token"}]
    )
    loser_service = _IntegrationCalendarService(
        list_pages=[{"items": [], "nextSyncToken": "loser-must-not-run"}]
    )
    winner_adapter._service_factory = lambda *_args: winner_service
    loser_adapter._service_factory = lambda *_args: loser_service
    winner_log = _IntegrationBarrierEventLog("calendar.source_observed")
    loser_log = _IntegrationEventLog()
    database = tmp_path / "calendar-cursors.sqlite3"
    winner_store = AdapterCursorStore(database)
    loser_store = AdapterCursorStore(database)
    loser_claimed = asyncio.Event()
    original_claim = loser_store.claim

    def observed_claim(*args: Any, **kwargs: Any) -> bool:
        result = original_claim(*args, **kwargs)
        loser_claimed.set()
        return result

    loser_store.claim = observed_claim  # type: ignore[method-assign]
    winner_supervisor = AdapterSupervisor(
        config_path=tmp_path / "winner.yaml",
        config_data=config,
        adapter_loader=lambda _config: [winner_adapter],
        ctx_factory=lambda: _make_integration_ctx(
            winner_log, cursor_store=winner_store
        ),
    )
    loser_supervisor = AdapterSupervisor(
        config_path=tmp_path / "loser.yaml",
        config_data=config,
        adapter_loader=lambda _config: [loser_adapter],
        ctx_factory=lambda: _make_integration_ctx(loser_log, cursor_store=loser_store),
    )

    await winner_supervisor.start()
    try:
        await asyncio.wait_for(winner_log.entered.wait(), timeout=1)
        before = loser_store.get("calendar-contended-fixture")
        await loser_supervisor.start()
        await asyncio.wait_for(loser_claimed.wait(), timeout=1)
        after = loser_store.get("calendar-contended-fixture")

        assert after == before
        assert loser_service.list_calls == []
        assert [call for call in loser_log.calls if call["type"] == "adapter.error"] == []
        assert loser_adapter.status().state != "running"
        assert next(iter(loser_supervisor.inventory().values()))["state"] != "running"

        winner_log.release.set()

        async def winner_finished() -> None:
            while winner_store.get("calendar-contended-fixture").provider_watermark is None:
                await asyncio.sleep(0)

        await asyncio.wait_for(winner_finished(), timeout=1)
        completed = winner_store.get("calendar-contended-fixture")
        assert len(winner_service.list_calls) == 1
        assert completed.error_count == 0
        assert completed.circuit_state == "closed"
    finally:
        winner_log.release.set()
        await loser_supervisor.stop()
        await winner_supervisor.stop()
        loser_store.close()
        winner_store.close()


async def test_open_calendar_circuit_blocks_provider_write_admission(
    tmp_path: Path,
) -> None:
    config = {
        "adapter_supervisor_port": 0,
        "adapters": [
            {
                "id": "calendar-admission-fixture",
                "kind": "calendaring-google-calendar",
                "config": {
                    "oauth_credentials_ref": "keychain://fixture/google-calendar",
                    "calendar_id": "primary",
                    "member_id": "member-fixture",
                    "provider_account_id": "account-fixture",
                },
            }
        ],
    }
    [adapter] = load_builtin_adapters(config, repository_root=REPO_ROOT)
    cursor_store = AdapterCursorStore(tmp_path / "calendar-cursors.sqlite3")
    for _ in range(3):
        cursor_store.record_error("calendar-admission-fixture")
    adapter._cursor_store = cursor_store
    adapter._cursor_id_value = "calendar-admission-fixture"
    adapter._status.state = "running"
    supervisor = AdapterSupervisor(
        config_path=tmp_path / "unused.yaml",
        config_data=config,
        adapter_loader=lambda _config: [adapter],
        ctx_factory=lambda: _make_integration_ctx(
            _IntegrationEventLog(), cursor_store=cursor_store
        ),
    )
    supervisor.prepare()
    try:
        with pytest.raises(RuntimeError, match="closed durable circuit"):
            await supervisor.dispatch_write(
                provider="google_calendar",
                member_id="member-fixture",
                provider_account_id="account-fixture",
                operation=WriteOperation(
                    kind="create_event",
                    target_external_id=None,
                    payload={
                        "calendar_id": "primary",
                        "summary": "Must not reach provider",
                        "start": {"dateTime": "2026-05-18T15:00:00Z"},
                        "end": {"dateTime": "2026-05-18T16:00:00Z"},
                    },
                ),
            )
        assert adapter.status().state == "backoff"
        assert adapter._service is None
    finally:
        cursor_store.close()
