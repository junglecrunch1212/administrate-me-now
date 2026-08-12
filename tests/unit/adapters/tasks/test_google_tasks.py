"""Framework-level tests for the Google Tasks Cat-B adapter pack.

Closes prompt 11b-iv §7 c3 deliverable (≥15 tests, basename unique per
F-4 against the Gmail / Plaid / Apple Reminders / Google Calendar
framework + pack-internal basenames). Verifies the fresh Cat-B
implementation shipped in 11b-iv c2 against:

- :class:`ExternalStateMirrorAdapter` inheritance + ``family="tasks"``
  (the PR #170 sidecar extended the Cat-B family Literal to include
  ``"tasks"``; this suite is the first framework test exercising that
  branch) + ``write_capabilities=["create_task"]`` (asymmetric per
  [UT-27]);
- the :meth:`apply_write` gate raising :class:`WriteCapabilityError`
  for any undeclared operation kind BEFORE the Google API is touched;
- the [§6.20] observation-wrap pattern: seam slots populated → routes
  through :func:`adminme.lib.observation.outbound`; unset → direct
  ``tasks.insert`` call; F-3 (HARD): ``OutboundResult(suppressed=True)``
  → :class:`WriteResult` with ``applied=False, suppressed=True``;
- the AdapterErrorV1 taxonomy classification for 401/429/503 ``HttpError``
  responses on the outbound write path;
- the inbound ``_poll_once`` emit contract: first observation of a
  Google task id → ``list_item.added@v1``; re-poll on the same id
  → skip; payload validates byte-for-byte against the EXISTING
  :class:`ListItemAddedV1` schema registered by prompt 11b-i (F-2 MAP
  DON'T REGISTER — no new schema per [D7]);
- F-7: Google Tasks has no native priority field, so every emission
  maps to ``priority=5`` (medium) — the ``Literal[0, 1, 5, 9]``
  ``ListItemAddedV1`` gate rejects anything else;
- the ``completed`` boolean derivation from Google's
  ``task["status"]`` string (``"completed"`` → True;
  ``"needsAction"`` → False);
- the c1 registry-lookup contract (``registry.get("list_item.added",
  1)`` returns :class:`ListItemAddedV1`).

These tests live under ``tests/unit/adapters/tasks/`` (framework
location, sibling to ``tests/unit/adapters/calendaring/test_google_calendar.py``
and ``tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py``)
because they exercise the framework-level Cat-B contract + the canonical
event-schema layer — not the pack's internal manifest / validator
surface (that's the c4 pack-internal deliverable).
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
from adminme.events.schemas.lists import ListItemAddedV1, ListItemV3
from adminme.lib.adapters import (
    ExternalStateMirrorAdapter,
    WriteCapabilityError,
    WriteOperation,
    WriteResult,
)
from adminme.lib.observation import OutboundResult

REPO_ROOT = Path(__file__).resolve().parents[4]
GOOGLE_TASKS_PACK_DIR = REPO_ROOT / "packs" / "adapters" / "tasks" / "google-tasks"


def _load_google_tasks_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root by file path. Mirrors the
    Gmail / Apple Reminders / Google Calendar test loaders: ``packs/``
    is not a Python package under poetry's package config, so file-path
    importlib is the canonical loader for pack code from framework
    tests."""
    spec = importlib.util.spec_from_file_location(
        "google_tasks_adapter_under_test",
        GOOGLE_TASKS_PACK_DIR / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_google_tasks_adapter_module()
GoogleTasksAdapter: Any = _adapter_mod.GoogleTasksAdapter


_FROZEN_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


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
        correlation_id_factory=lambda: "corr-gtasks-test",
        now_factory=lambda: _FROZEN_NOW,
    )


class _FakeHttpResponse:
    """httplib2-style mapping with a ``status`` attribute, sufficient for
    :meth:`GoogleTasksAdapter._classify_tasks_error`."""

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


class _FakeTasksService:
    """Minimal Tasks v1 service exposing ``tasks().list(...).execute()``
    and ``tasks().insert(...).execute()``.

    ``list_pages`` is a list of pages returned in sequence by ``list()``;
    each page is ``{"items": [...], "nextPageToken": "..."}``. Once the
    sequence is exhausted, the next call returns an empty page (no
    token) so the adapter's pagination loop terminates.

    ``insert_response`` is the dict ``insert().execute()`` returns; if
    ``raises`` is set, ``execute()`` raises that exception instead.
    """

    def __init__(
        self,
        *,
        list_pages: list[dict[str, Any]] | None = None,
        insert_response: dict[str, Any] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self._list_pages = list(list_pages or [])
        self._insert_response = insert_response or {"id": "gtask-out-001"}
        self._raises = raises
        self.list_calls: list[dict[str, Any]] = []
        self.insert_calls: list[dict[str, Any]] = []

    def tasks(self) -> "_FakeTasksResource":
        return _FakeTasksResource(self)


class _FakeTasksResource:
    def __init__(self, service: _FakeTasksService) -> None:
        self._service = service

    def list(self, **kwargs: Any) -> "_FakeListRequest":
        self._service.list_calls.append(kwargs)
        return _FakeListRequest(self._service)

    def insert(self, **kwargs: Any) -> "_FakeInsertRequest":
        self._service.insert_calls.append(kwargs)
        return _FakeInsertRequest(self._service)


class _FakeListRequest:
    def __init__(self, service: _FakeTasksService) -> None:
        self._service = service

    def execute(self) -> dict[str, Any]:
        if self._service._raises is not None:
            raise self._service._raises
        if self._service._list_pages:
            return dict(self._service._list_pages.pop(0))
        return {"items": []}


class _FakeInsertRequest:
    def __init__(self, service: _FakeTasksService) -> None:
        self._service = service

    def execute(self) -> dict[str, Any]:
        if self._service._raises is not None:
            raise self._service._raises
        return dict(self._service._insert_response)


def _make_adapter(
    *,
    service: _FakeTasksService | None = None,
    observation_session: Any = None,
    observation_manager: Any = None,
    observation_event_log: Any = None,
    tasklist_id: str = "tasklist-primary",
    member_id: str = "member-001",
    list_name: str | None = None,
) -> Any:
    """Construct a GoogleTasksAdapter with a fake service. Mirrors the
    Google Calendar test's ``_make_adapter`` factory shape so the two
    fresh-Cat-B suites stay structurally aligned."""
    config: dict[str, Any] = {
        "service_account_key_ref": "op://test/v/i/f",
        "tasklist_id": tasklist_id,
        "user_email": "operator@example.test",
        "member_id": member_id,
    }
    if list_name is not None:
        config["list_name"] = list_name
    adapter = GoogleTasksAdapter(
        config=config,
        observation_session=observation_session,
        observation_manager=observation_manager,
        observation_event_log=observation_event_log,
    )
    if service is not None:
        adapter._service = service
    return adapter


def _seed_ctx(adapter: Any, event_log: _FakeEventLog) -> AdapterContext:
    """Attach an AdapterContext to the adapter — what
    :meth:`GoogleTasksAdapter.start` does in production via
    ``self._ctx = ctx``."""
    ctx = _ctx(event_log)
    adapter._ctx = ctx
    return ctx


def _make_task(
    *,
    task_id: str,
    title: str = "Demo task",
    notes: str | None = None,
    due: str | None = None,
    status: str = "needsAction",
    updated: str | None = None,
) -> dict[str, Any]:
    """Shape-only fake of a Google Tasks v1 ``Task`` resource carrying
    the fields the adapter's inbound mapping reads. ``status`` defaults
    to ``"needsAction"`` (Google's open-task state); the completed
    derivation flips True when set to ``"completed"``."""
    out: dict[str, Any] = {
        "id": task_id,
        "title": title,
        "status": status,
    }
    if notes is not None:
        out["notes"] = notes
    if due is not None:
        out["due"] = due
    if updated is not None:
        out["updated"] = updated
    return out


# ===========================================================================
# 1) Cat-B inheritance + ClassVars
# ===========================================================================


def test_google_tasks_adapter_inherits_external_state_mirror_adapter() -> None:
    """The pack's adapter is a Cat-B :class:`ExternalStateMirrorAdapter`
    subclass — the load-bearing inheritance per memo 05 §1.2 / [PM-34].
    Failing this would mean the validator's ``base_class_conformance``
    check would reject the pack at install time."""
    assert issubclass(GoogleTasksAdapter, ExternalStateMirrorAdapter)


def test_family_class_attribute_is_tasks_literal() -> None:
    """``family`` is the ``"tasks"`` literal — the PR #170 sidecar
    extended the Cat-B family Literal from {reminders, calendaring,
    contacts} to include ``"tasks"`` per memo 05 §5.3. This test is the
    first framework-level assertion exercising that branch and pins
    the canonical 11b-iv contract."""
    assert GoogleTasksAdapter.family == "tasks"


def test_write_capabilities_declared_asymmetrically() -> None:
    """[UT-27] asymmetric write surface — declare ONLY the per-operation
    kinds the adapter actually implements. 11b-iv ships ``create_task``
    only; ``update_task`` / ``delete_task`` / ``complete_task`` are out
    of scope per the parent draft and intentionally NOT declared. A
    declared-but-not-implemented kind would fail the validator's
    method-presence rule per memo 05 §3.4."""
    assert GoogleTasksAdapter.write_capabilities == [
        "create_list",
        "update_list",
        "delete_list",
        "create_task",
        "update_task",
        "delete_task",
        "complete_task",
        "uncomplete_task",
        "move_task",
        "clear_completed_tasks",
    ]


# ===========================================================================
# 2) apply_write gate — [UT-27]
# ===========================================================================


async def test_apply_write_gate_raises_on_undeclared_kind() -> None:
    """The base class's :meth:`apply_write` gate raises
    :class:`WriteCapabilityError` for any operation whose ``kind`` is not
    in ``write_capabilities`` — BEFORE ``_apply_write_impl`` runs and
    therefore BEFORE the Google Tasks API is touched. ``update_task`` is
    the canonical undeclared kind for 11b-iv (out of scope per parent
    draft; deferred to 11b-iv-β)."""
    adapter = _make_adapter()
    operation = WriteOperation(
        kind="set_native_priority",
        target_external_id="gtask-1",
        payload={},
    )

    with pytest.raises(WriteCapabilityError):
        await adapter.apply_write(operation)


async def test_apply_write_dispatches_to_impl_on_declared_kind() -> None:
    """For ``kind="create_task"`` the gate passes and
    :meth:`apply_write` dispatches to :meth:`_apply_write_impl`.
    Monkeypatching the instance method records the dispatch without
    touching the real Google Tasks API surface."""
    adapter = _make_adapter()
    captured: list[WriteOperation] = []

    async def _recording_impl(operation: WriteOperation) -> WriteResult:
        captured.append(operation)
        return WriteResult(applied=True, external_id="gtask-recorded", suppressed=False)

    adapter._apply_write_impl = _recording_impl  # type: ignore[method-assign]

    op = WriteOperation(
        kind="create_task",
        target_external_id=None,
        payload={
            "tasklist_id": "tasklist-primary",
            "title": "Pick up dry cleaning",
            "notes": "By 5pm",
            "due": "2026-05-20T00:00:00.000Z",
        },
    )
    result = await adapter.apply_write(op)

    assert len(captured) == 1
    assert captured[0].kind == "create_task"
    assert result.applied is True
    assert result.external_id == "gtask-recorded"


# ===========================================================================
# 3) Observation-mode wrap — [§6.20] + F-3 (HARD)
# ===========================================================================


async def test_apply_write_routes_through_observation_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """:meth:`_apply_write_impl` MUST route ``tasks.insert`` through
    :func:`adminme.lib.observation.outbound` per [§6.20] when the three
    observation-mode seam slots are populated. The adapter imports the
    function under the alias ``observation_outbound`` in its own module
    namespace; monkeypatching THAT name records the wrap without needing
    real session / manager / event-log instances. The Phase A path
    leaves the seams unset and bypasses the wrap (see the next test)."""
    service = _FakeTasksService(insert_response={"id": "gtask-wrapped-001"})
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
        # ``tasks.insert`` registers on the service mock.
        result = await action_fn()
        return OutboundResult(suppressed=False, result=result)

    monkeypatch.setattr(_adapter_mod, "observation_outbound", _fake_outbound)

    op = WriteOperation(
        kind="create_task",
        target_external_id=None,
        payload={
            "tasklist_id": "tasklist-primary",
            "title": "Pick up dry cleaning",
            "notes": "By 5pm",
            "due": "2026-05-20T00:00:00.000Z",
        },
    )
    result = await adapter.apply_write(op)

    assert len(captured) == 1
    call = captured[0]
    assert call["session"] is session_sentinel
    assert call["manager"] is manager_sentinel
    assert call["event_log"] is obs_log_sentinel
    assert call["action"] == "tasks.create_task"
    assert call["target_channel"] == "tasks-google-tasks"
    assert call["target_identifier"] == "tasklist-primary"
    # The wrapped ``insert`` actually fired against the fake service.
    assert len(service.insert_calls) == 1
    assert result.applied is True
    assert result.external_id == "gtask-wrapped-001"
    assert result.suppressed is False


async def test_apply_write_observation_suppressed_returns_unapplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-3 (HARD): when :func:`observation_outbound` returns
    ``OutboundResult(suppressed=True)``, :meth:`_apply_write_impl` returns
    ``WriteResult(applied=False, external_id=None, suppressed=True)``.
    The Google Tasks API is NOT called — observation mode is the final
    outbound filter per [§6.13] and the fake stub must therefore NOT
    invoke ``action_fn``."""
    service = _FakeTasksService()
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
        kind="create_task",
        target_external_id=None,
        payload={
            "tasklist_id": "tasklist-primary",
            "title": "Will not send",
        },
    )
    result = await adapter.apply_write(op)

    assert result.applied is False
    assert result.external_id is None
    assert result.suppressed is True
    # The wrapped ``tasks.insert`` was NOT invoked.
    assert service.insert_calls == []


async def test_apply_write_direct_call_when_seams_unset_returns_applied() -> None:
    """Phase A (today): the supervisor's adapter loader does NOT yet
    populate the observation-seam slots, so :meth:`_apply_write_impl`
    falls through the wrap branch and direct-calls
    ``service.tasks().insert(...)``. The 11r-c / 11a / 11b-iii Phase-A-
    conditional pattern. ``WriteResult(applied=True, ...)`` returns with
    the Google task id."""
    service = _FakeTasksService(insert_response={"id": "gtask-direct-001"})
    adapter = _make_adapter(service=service)  # seams default to None

    op = WriteOperation(
        kind="create_task",
        target_external_id=None,
        payload={
            "tasklist_id": "tasklist-primary",
            "title": "Direct call task",
            "notes": "no observation seams",
        },
    )
    result = await adapter.apply_write(op)

    assert len(service.insert_calls) == 1
    insert_call = service.insert_calls[0]
    assert insert_call["tasklist"] == "tasklist-primary"
    assert insert_call["body"]["title"] == "Direct call task"
    assert insert_call["body"]["notes"] == "no observation seams"
    assert result.applied is True
    assert result.external_id == "gtask-direct-001"
    assert result.suppressed is False


# ===========================================================================
# 4) Error classification — AdapterErrorV1 taxonomy
# ===========================================================================


async def test_apply_write_4xx_auth_failure_classifies_fatal() -> None:
    """A 401 from ``tasks.insert`` classifies as ``fatal`` /
    ``auth_failed``, emits exactly one ``adapter.error@v1``, AND re-
    raises so the supervisor's backoff handles retry. Mirrors Gmail /
    Google Calendar outbound error paths on the tasks side."""
    error = _FakeHttpError(resp=_FakeHttpResponse(status=401))
    service = _FakeTasksService(raises=error)
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    op = WriteOperation(
        kind="create_task",
        target_external_id=None,
        payload={
            "tasklist_id": "tasklist-primary",
            "title": "Auth fail",
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
    """A 503 from ``tasks.insert`` classifies as ``transient`` /
    ``network`` so the supervisor's backoff retries. One
    ``adapter.error@v1`` emitted; the exception propagates."""
    error = _FakeHttpError(resp=_FakeHttpResponse(status=503))
    service = _FakeTasksService(raises=error)
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    op = WriteOperation(
        kind="create_task",
        target_external_id=None,
        payload={
            "tasklist_id": "tasklist-primary",
            "title": "5xx body",
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
    Mirrors the canonical Gmail / Google Calendar outbound rate-limit
    handling."""
    error = _FakeHttpError(
        resp=_FakeHttpResponse(status=429, headers={"Retry-After": "45"})
    )
    service = _FakeTasksService(raises=error)
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    op = WriteOperation(
        kind="create_task",
        target_external_id=None,
        payload={
            "tasklist_id": "tasklist-primary",
            "title": "rate-limited",
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


async def test_poll_once_emits_list_item_added_on_first_observation() -> None:
    """First observation of a Google task id (``_seen_inbound`` empty) →
    one ``list_item.added@v1`` emit per the inbound poll contract. The
    emit type matches the EXISTING on-main schema family per F-2 (MAP
    DON'T REGISTER) — no new schemas registered. 11b-iv reuses the
    schema 11b-i registered."""
    task = _make_task(
        task_id="gtask-inbound-001",
        title="Standup notes",
        updated="2026-05-18T11:00:00.000Z",
    )
    service = _FakeTasksService(list_pages=[{"items": [task]}])
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    emitted = await adapter._poll_once(ctx=ctx)

    assert emitted == 2  # list discovery + item discovery
    added_calls = [c for c in event_log.calls if c["type"] == "list_item.added"]
    assert len(added_calls) == 1
    assert added_calls[0]["schema_version"] == 3
    assert added_calls[0]["payload"]["item_external_id"] == "gtask-inbound-001"


async def test_poll_once_skips_already_seen_task_id() -> None:
    """A re-poll on the same task id (``_seen_inbound`` populated) emits
    NO new events. The dedupe gate is the load-bearing invariant per
    F-3 (HARD/standing) — without it every poll cycle would re-emit the
    same task, drowning downstream consumers."""
    task = _make_task(
        task_id="gtask-dup-001",
        title="Repeat task",
        updated="2026-05-18T11:00:00.000Z",
    )
    # The fake returns the same page on both calls (list_pages exhausts
    # → empty page on second call; we want the same item twice instead).
    service = _FakeTasksService(list_pages=[{"items": [task]}, {"items": [task]}])
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    first = await adapter._poll_once(ctx=ctx)
    second = await adapter._poll_once(ctx=ctx)

    assert first == 2
    assert second == 0  # already seen → not re-emitted
    added_calls = [c for c in event_log.calls if c["type"] == "list_item.added"]
    assert len(added_calls) == 1


async def test_poll_emit_payload_validates_against_list_item_added_v1() -> None:
    """The emitted payload validates byte-for-byte against the EXISTING
    :class:`ListItemAddedV1` model. ``extra="forbid"`` on the model
    means any drift introducing a new field would fail validation —
    pinning F-2 (MAP DON'T REGISTER): Google Tasks inbound reuses the
    on-main schema, no new fields, no new schema. Validates the full
    field set: ``member_id, list_external_id, item_external_id,
    list_name, title, notes, due_at, priority, completed,
    last_modified_at, sync_cycle_id``."""
    task = _make_task(
        task_id="gtask-validate-001",
        title="Schema check",
        notes="quarterly review prep",
        due="2026-05-20T00:00:00.000Z",
        status="needsAction",
        updated="2026-05-18T11:00:00.000Z",
    )
    service = _FakeTasksService(list_pages=[{"items": [task]}])
    adapter = _make_adapter(
        service=service,
        tasklist_id="tasklist-primary",
        member_id="member-001",
        list_name="Primary list",
    )
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    await adapter._poll_once(ctx=ctx)

    added_call = next(c for c in event_log.calls if c["type"] == "list_item.added")
    payload = added_call["payload"]
    # Round-trip via strict validation — any extra field would raise
    # pydantic.ValidationError under ``extra="forbid"``.
    instance = ListItemV3.model_validate(payload)
    assert instance.member_id == "member-001"
    assert instance.list_external_id == "tasklist-primary"
    assert instance.item_external_id == "gtask-validate-001"
    assert instance.list_name == "Primary list"
    assert instance.title == "Schema check"
    assert instance.notes == "quarterly review prep"
    assert instance.provider == "google_tasks"
    assert instance.due is not None and instance.due.day == 20
    assert instance.priority == 0
    assert instance.completed is False
    assert instance.sync_cycle_id.startswith("google-tasks-revision:")


async def test_provider_revision_dedupe_survives_adapter_restart(
    tmp_path: Path,
) -> None:
    task = _make_task(
        task_id="gtask-restart-001",
        title="Stable provider task",
        status="needsAction",
        updated="2026-05-18T11:00:00.000Z",
    )
    cursor_store = AdapterCursorStore(tmp_path / "google-tasks-cursors.sqlite3")
    event_log = _FakeEventLog()

    first = _make_adapter(service=_FakeTasksService(list_pages=[{"items": [task]}]))
    first.instance_name = "google-tasks-primary"
    first_ctx = AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "first-cycle",
        now_factory=lambda: _FROZEN_NOW,
        cursor_store=cursor_store,
    )
    assert await first._poll_once(ctx=first_ctx) == 2

    restarted = _make_adapter(service=_FakeTasksService(list_pages=[{"items": [task]}]))
    restarted.instance_name = "google-tasks-primary"
    restarted_ctx = AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "restart-cycle",
        now_factory=lambda: _FROZEN_NOW,
        cursor_store=cursor_store,
    )
    assert await restarted._poll_once(ctx=restarted_ctx) == 0
    assert restarted.status().extra["restart_dedupe"] == "durable"
    assert (
        len([call for call in event_log.calls if call["type"] == "list_item.added"])
        == 1
    )
    cursor_store.close()


async def test_poll_loop_recovers_current_health_after_transient_failure() -> None:
    adapter = _make_adapter()
    adapter._max_poll_cycles = 2
    adapter._poll_interval_s = 0
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)
    attempts = 0

    async def poll_once(*, ctx: AdapterContext) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionResetError("fixture transient")
        return 0

    adapter._poll_once = poll_once

    await adapter._poll_loop(ctx=ctx)

    assert attempts == 2
    assert adapter.status().state == "running"
    errors = [call for call in event_log.calls if call["type"] == "adapter.error"]
    assert len(errors) == 1
    assert errors[0]["payload"]["severity"] == "transient"
    assert errors[0]["payload"]["error_kind"] == "network"


async def test_poll_emit_priority_is_unspecified_per_google_contract() -> None:
    """F-7 (SOFT/new for 11b-iv): Google Tasks API does NOT carry a
    priority field; the ``ListItemAddedV1`` schema requires ``priority:
    Literal[0, 1, 5, 9]``. The adapter maps EVERY emission to
    ``priority=5`` (medium) so the schema gate accepts it. Pinning the
    default here means a future code change that introduces native-
    priority mapping (none exists today) would require this test to
    move."""
    task = _make_task(
        task_id="gtask-priority-001",
        title="No priority on Google Tasks",
        updated="2026-05-18T11:00:00.000Z",
    )
    service = _FakeTasksService(list_pages=[{"items": [task]}])
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    await adapter._poll_once(ctx=ctx)

    added_call = next(c for c in event_log.calls if c["type"] == "list_item.added")
    assert added_call["payload"]["priority"] == 0


async def test_poll_emit_completed_derives_from_task_status() -> None:
    """The emitted ``completed`` boolean derives from Google's
    ``task["status"]`` string per A-4: ``"completed"`` → True;
    ``"needsAction"`` → False. The adapter today filters
    ``showCompleted=False`` server-side, but the mapping must still be
    correct because (a) servers may return completed items at the boundary
    of a status change and (b) the c2 implementation reads the field
    unconditionally."""
    task_open = _make_task(
        task_id="gtask-open-001",
        title="Open task",
        status="needsAction",
        updated="2026-05-18T11:00:00.000Z",
    )
    task_done = _make_task(
        task_id="gtask-done-001",
        title="Done task",
        status="completed",
        updated="2026-05-18T11:01:00.000Z",
    )
    service = _FakeTasksService(list_pages=[{"items": [task_open, task_done]}])
    adapter = _make_adapter(service=service)
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    await adapter._poll_once(ctx=ctx)

    added_calls = {
        c["payload"]["item_external_id"]: c["payload"]
        for c in event_log.calls
        if c["type"] == "list_item.added"
    }
    assert added_calls["gtask-open-001"]["completed"] is False
    assert added_calls["gtask-done-001"]["completed"] is True


# ===========================================================================
# 6) Outbound body shape
# ===========================================================================


async def test_apply_write_create_task_body_shape() -> None:
    """The outbound ``tasks.insert`` call carries the expected ``body``
    shape: ``title`` always, plus the optional ``notes`` / ``due`` keys
    when set on the payload. The ``tasklist`` kwarg matches the
    operation payload's ``tasklist_id`` (falling back to the adapter's
    config when the payload omits it). Pins the Google Tasks v1 API
    surface contract the parent draft §1 item 2 anchors."""
    service = _FakeTasksService(insert_response={"id": "gtask-body-001"})
    adapter = _make_adapter(service=service)

    op = WriteOperation(
        kind="create_task",
        target_external_id=None,
        payload={
            "tasklist_id": "tasklist-primary",
            "title": "Pick up dry cleaning",
            "notes": "By 5pm",
            "due": "2026-05-20T00:00:00.000Z",
        },
    )
    await adapter.apply_write(op)

    assert len(service.insert_calls) == 1
    call = service.insert_calls[0]
    assert call["tasklist"] == "tasklist-primary"
    body = call["body"]
    assert body["title"] == "Pick up dry cleaning"
    assert body["notes"] == "By 5pm"
    assert body["due"] == "2026-05-20T00:00:00.000Z"


class _DispatchRequest:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {}
        self.headers: dict[str, str] = {}

    def execute(self) -> dict[str, Any]:
        return self.response


class _DispatchResource:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self.calls = calls

    def __getattr__(self, name: str) -> Any:
        def invoke(**kwargs: Any) -> _DispatchRequest:
            self.calls.append((name, kwargs))
            return _DispatchRequest(
                {"id": kwargs.get("task") or kwargs.get("tasklist")}
            )

        return invoke


class _DispatchService:
    def __init__(self) -> None:
        self.task_calls: list[tuple[str, dict[str, Any]]] = []
        self.list_calls: list[tuple[str, dict[str, Any]]] = []

    def tasks(self) -> _DispatchResource:
        return _DispatchResource(self.task_calls)

    def tasklists(self) -> _DispatchResource:
        return _DispatchResource(self.list_calls)


@pytest.mark.parametrize(
    ("kind", "target", "payload", "resource", "method"),
    [
        ("update_task", "task-a", {"title": "Changed"}, "task", "patch"),
        ("delete_task", "task-a", {}, "task", "delete"),
        ("complete_task", "task-a", {}, "task", "patch"),
        ("uncomplete_task", "task-a", {}, "task", "patch"),
        (
            "move_task",
            "task-a",
            {"destination_list_external_id": "list-b", "previous": "task-z"},
            "task",
            "move",
        ),
        ("clear_completed_tasks", None, {}, "task", "clear"),
        ("create_list", None, {"title": "Errands"}, "list", "insert"),
        ("update_list", "list-a", {"title": "Home"}, "list", "patch"),
        ("delete_list", "list-a", {}, "list", "delete"),
    ],
)
async def test_supported_writes_dispatch_to_exact_google_resource(
    kind: str,
    target: str | None,
    payload: dict[str, Any],
    resource: str,
    method: str,
) -> None:
    service = _DispatchService()
    adapter = _make_adapter(service=service)  # type: ignore[arg-type]
    result = await adapter.apply_write(
        WriteOperation(
            kind=kind,
            target_external_id=target,
            payload={"tasklist_id": "list-a", **payload},
        )
    )
    calls = service.task_calls if resource == "task" else service.list_calls
    assert calls[0][0] == method
    assert result.applied is True
    if kind == "move_task":
        assert calls[0][1]["destinationTasklist"] == "list-b"


def test_list_item_added_v3_registry_preserves_google_provider_fields() -> None:
    assert registry.get("list_item.added", 3) is ListItemV3


# ===========================================================================
# 7) c1 registry contract — schema unchanged on main
# ===========================================================================


def test_list_item_added_v1_registry_lookup_returns_existing_model() -> None:
    """The c1 verify-only contract pinned in the framework test suite:
    ``registry.get("list_item.added", 1)`` returns the EXISTING
    :class:`ListItemAddedV1` model registered at
    ``adminme/events/schemas/lists.py`` (by prompt 11b-i) — 11b-iv adds
    NO new schema and re-registers nothing per F-2 (MAP DON'T REGISTER)
    + [D7]."""
    assert registry.get("list_item.added", 1) is ListItemAddedV1
