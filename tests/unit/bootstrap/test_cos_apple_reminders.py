"""Production composition and native permission tests using isolated provider fakes."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.adapters.cos_apple_reminders import (
    ADAPTER_KIND,
    CONFIG_KEY,
    CoSRemindersLookup,
    CoSRemindersSelection,
    selected_entries,
    validate_authorized_change,
)
from adminme.adapters.cos_apple_reminders_native import SelectedSnapshot
from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.adapters.loader import load_builtin_adapters
from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.adapters.external_state_mirror_base import (
    WriteCapabilityError,
    WriteOperation,
)
from adminme.lib.instance_config import load_instance_config
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap.cos_apple_reminders import prepare_configuration, read_back
from bootstrap.cos_channel_activation import prepare_configuration as activate
from tests.acceptance.test_cos_channel_acquisition import _configuration
from tests.acceptance.test_forwarded_channel_intake import MEMBER_A, ROOT


def _selection() -> CoSRemindersSelection:
    return CoSRemindersSelection.model_validate(
        {
            "adapter_id": "fixture-cos-reminders",
            "source_id": "fixture-source",
            "list_id": "fixture-list",
            "source_kind": "local",
            "device_identity_sha256": "a" * 64,
            "os_uid": 501,
            "source_ownership_evidence_sha256": "b" * 64,
            "owner_authorization_sha256": "c" * 64,
        }
    )


def _item(**changes: Any) -> Any:
    return SimpleNamespace(
        **{
            "calendarItemIdentifier": "fixture-item",
            "list_identifier": "fixture-list",
            "title": "Fixture reminder",
            "notes": "Private source text",
            "priority": 0,
            "completed": False,
            "completion_date": None,
            "start_components": None,
            "due_components": None,
            "last_modified_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
            "url": None,
            "alarms": [],
            "recurrence_rules": [],
            **changes,
        }
    )


class Reader:
    def __init__(self) -> None:
        self.snapshot = SelectedSnapshot(
            "fixture-source", "fixture-list", "Fixture list", (_item(),)
        )
        self.error: Exception | None = None
        self.calls = 0

    def read(self, selection: CoSRemindersSelection) -> SelectedSnapshot:
        self.calls += 1
        assert selection == _selection()
        if self.error:
            raise self.error
        return self.snapshot


def _adapter(tmp_path: Path, reader: Reader) -> Any:
    previous = _configuration(tmp_path / "fixture")
    requested = prepare_configuration(previous, _selection())
    prepared = activate(previous, requested, enable_live=False)
    rows = load_builtin_adapters(
        prepared,
        repository_root=ROOT,
        member_roles={MEMBER_A: "principal"},
        service_principal_ids={COS_SERVICE_PRINCIPAL_ID},
        validate_bindings=True,
    )
    adapter: Any = next(row for row in rows if row.name == ADAPTER_KIND)
    adapter._reader_factory = lambda: reader
    adapter._device_identity = lambda: ("a" * 64, 501)
    adapter._interval = 3600
    return adapter


@pytest.fixture
async def runtime(tmp_path: Path):
    instance_dir = tmp_path / "state"
    instance_dir.mkdir()
    instance = load_instance_config(instance_dir)
    log = EventLog(instance, b"r" * 32)
    bus = EventBus(log, instance.bus_checkpoint_path)
    cursor = AdapterCursorStore(tmp_path / "cursor.sqlite")
    ctx = AdapterContext(
        event_sink=EventWriter(log, bus),
        config={},
        correlation_id_factory=lambda: "fixture-cos-reminders",
        now_factory=lambda: datetime(2026, 10, 2, tzinfo=timezone.utc),
        cursor_store=cursor,
        source_events=log,
    )
    yield ctx, log
    cursor.close()
    await bus.stop()
    await log.close()


async def _facts(log: EventLog) -> list[dict[str, Any]]:
    return [
        row
        async for row in log.read_since()
        if row["type"].startswith(("list.", "list_item."))
    ]


@pytest.mark.asyncio
async def test_real_adapter_persists_updates_completion_and_restart_tombstones(
    tmp_path: Path, runtime: Any
) -> None:
    ctx, log = runtime
    reader = Reader()
    adapter = _adapter(tmp_path, reader)
    try:
        await adapter.start(ctx=ctx)
        assert adapter.status().state == "running"
        facts = await _facts(log)
        assert [(row["type"], row["schema_version"]) for row in facts] == [
            ("list.created", 2),
            ("list_item.added", 4),
        ]
        for row in facts:
            assert (
                row["owner_scope"]
                == row["visibility_scope"]
                == "service-private:" + COS_SERVICE_PRINCIPAL_ID
            )
            assert row["source_adapter"] == ADAPTER_KIND
            assert row["payload"]["member_id"] is None
            assert row["payload"]["service_principal_id"] == COS_SERVICE_PRINCIPAL_ID
            assert row["payload"]["provider"] == "apple_reminders"
        assert await adapter._poll_once(ctx=ctx) == 0
        reader.snapshot = replace(
            reader.snapshot,
            items=(_item(completed=True, completion_date=ctx.now_factory()),),
        )
        await adapter._poll_once(ctx=ctx)
        reader.snapshot = replace(
            reader.snapshot, items=(_item(title="Changed fixture"),)
        )
        await adapter._poll_once(ctx=ctx)
        with pytest.raises(WriteCapabilityError):
            await adapter.apply_write(
                WriteOperation(kind="create_reminder", target_external_id=None)
            )
    finally:
        await adapter.stop()
    restarted = _adapter(tmp_path, reader)
    reader.snapshot = replace(reader.snapshot, items=())
    try:
        await restarted.start(ctx=ctx)
        assert [row["type"] for row in await _facts(log)] == [
            "list.created",
            "list_item.added",
            "list_item.completed",
            "list_item.uncompleted",
            "list_item.deleted",
        ]
        reader.snapshot = replace(reader.snapshot, list_name=None)
        await restarted._poll_once(ctx=ctx)
        assert (await _facts(log))[-1]["type"] == "list.deleted"
        assert await restarted._poll_once(ctx=ctx) == 0
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_unavailable_or_foreign_snapshot_never_creates_false_deletions(
    tmp_path: Path, runtime: Any
) -> None:
    ctx, log = runtime
    reader = Reader()
    adapter = _adapter(tmp_path, reader)
    try:
        await adapter.start(ctx=ctx)
        before = await _facts(log)
        reader.error = PermissionError("private native error")
        with pytest.raises(PermissionError):
            await adapter._poll_once(ctx=ctx)
        reader.error = None
        reader.snapshot = replace(reader.snapshot, source_id="other-source", items=())
        with pytest.raises(PermissionError):
            await adapter._poll_once(ctx=ctx)
        reader.snapshot = replace(
            reader.snapshot,
            source_id="fixture-source",
            items=(_item(list_identifier="other-list"),),
        )
        with pytest.raises(PermissionError):
            await adapter._poll_once(ctx=ctx)
        assert await _facts(log) == before
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_partial_publication_retries_once_and_advances_only_complete_snapshot(
    tmp_path: Path, runtime: Any
) -> None:
    ctx, log = runtime
    reader = Reader()
    adapter = _adapter(tmp_path, reader)
    sink = ctx.event_sink

    class FailingSink:
        tenant_id = sink.tenant_id
        failed = False

        async def write(self, envelope: Any, **kwargs: Any) -> str:
            if envelope.type == "list_item.added" and not self.failed:
                self.failed = True
                raise RuntimeError("fixture sink failure")
            return await sink.write(envelope, **kwargs)

    failed_ctx = replace(ctx, event_sink=FailingSink())
    try:
        with pytest.raises(RuntimeError, match="startup read failed"):
            await adapter.start(ctx=failed_ctx)
        assert [row["type"] for row in await _facts(log)] == ["list.created"]
        later_ctx = replace(
            ctx, now_factory=lambda: datetime(2026, 10, 3, tzinfo=timezone.utc)
        )
        await adapter._poll_once(ctx=later_ctx)
        assert [row["type"] for row in await _facts(log)] == [
            "list.created",
            "list_item.added",
        ]
        assert await adapter._poll_once(ctx=later_ctx) == 0
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_wrong_device_fails_before_native_reader_or_any_event(
    tmp_path: Path, runtime: Any
) -> None:
    ctx, log = runtime
    reader = Reader()
    adapter = _adapter(tmp_path, reader)
    adapter._device_identity = lambda: ("f" * 64, 501)
    with pytest.raises(PermissionError, match="work computer"):
        await adapter.start(ctx=ctx)
    assert reader.calls == 0
    assert await _facts(log) == []


def test_preparation_preserves_previous_sources_and_requires_exact_authorization(
    tmp_path: Path,
) -> None:
    previous = _configuration(tmp_path)
    requested = prepare_configuration(previous, _selection())
    assert requested["adapters"][:-1] == previous["adapters"]
    validate_authorized_change(previous, requested, owner_authorization_sha256="c" * 64)
    with pytest.raises(ValueError, match="exact authorization"):
        validate_authorized_change(
            previous, requested, owner_authorization_sha256="d" * 64
        )
    for field, value in (
        ("member_id", MEMBER_A),
        ("read_only", False),
        ("permission_scope", ["eventkit.reminders.write"]),
    ):
        malformed = copy.deepcopy(requested)
        malformed["adapters"][-1]["config"][field] = value
        with pytest.raises(ValueError, match="binding mismatch"):
            selected_entries(malformed)
    paused = copy.deepcopy(requested)
    paused["adapters"][-1]["enabled"] = False
    assert (
        activate(requested, paused, enable_live=False)["adapters"][-1]["enabled"]
        is False
    )
    revoked = copy.deepcopy(requested)
    revoked[CONFIG_KEY]["selection"] = None
    assert (
        activate(requested, revoked, enable_live=False)["adapters"][-1]["enabled"]
        is False
    )
    with pytest.raises(ValueError, match="migration"):
        prepare_configuration(
            requested,
            _selection().model_copy(
                update={"source_kind": "icloud", "source_id": "reviewed-cloud-source"}
            ),
        )


def test_readback_is_content_free_and_does_not_treat_missing_list_as_ready() -> None:
    reader = Reader()
    result = read_back(
        _selection(), reader_factory=lambda: reader, identity=lambda: ("a" * 64, 501)
    )
    assert result["provider_write_count"] == 0
    assert result["item_count"] == 1
    assert "Private source text" not in str(result)
    reader.snapshot = replace(reader.snapshot, list_name=None, items=())
    with pytest.raises(ValueError, match="existing Reminders list"):
        read_back(
            _selection(),
            reader_factory=lambda: reader,
            identity=lambda: ("a" * 64, 501),
        )


@pytest.mark.parametrize("field", ["source_id", "list_id"])
@pytest.mark.parametrize("alias", ["default", "primary", "@default", "*", " padded "])
def test_default_or_ambiguous_resources_are_rejected(field: str, alias: str) -> None:
    with pytest.raises(ValueError, match="exact native"):
        CoSRemindersSelection.model_validate(
            {**_selection().model_dump(), field: alias}
        )


def _native_reader(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    import sys
    import adminme.adapters.cos_apple_reminders_native as native

    state = SimpleNamespace(
        permission=3,
        kind=0,
        source_id="fixture-source",
        list_id="fixture-list",
        missing=False,
        nil=False,
        revoke=False,
        converted=0,
        allocated=0,
        calls=[],
    )
    source = SimpleNamespace(
        sourceIdentifier=lambda: state.source_id,
        sourceType=lambda: state.kind,
        title=lambda: "Fixture source",
    )
    calendar = SimpleNamespace(
        calendarIdentifier=lambda: state.list_id,
        source=lambda: source,
        isSubscribed=lambda: False,
        title=lambda: "Fixture list",
    )

    state.catalog = [calendar]

    class Store:
        def calendarsForEntityType_(self, entity: int) -> list[Any]:
            assert entity == 1
            state.calls.append(("metadata", entity))
            return state.catalog

        def sourceWithIdentifier_(self, identifier: str) -> Any:
            state.calls.append(("source", identifier))
            return source

        def calendarWithIdentifier_(self, identifier: str) -> Any:
            state.calls.append(("list", identifier))
            return None if state.missing else calendar

        def predicateForRemindersInCalendars_(self, calendars: list[Any]) -> str:
            assert calendars == [calendar]
            state.calls.append(("predicate", len(calendars)))
            return "fixture-selected-predicate"

        def fetchRemindersMatchingPredicate_completion_(
            self, predicate: str, callback: Any
        ) -> None:
            assert predicate == "fixture-selected-predicate"
            callback(
                None if state.nil else [SimpleNamespace(calendar=lambda: calendar)]
            )
            if state.revoke:
                state.permission = 0

    class EventStore:
        @staticmethod
        def authorizationStatusForEntityType_(entity: int) -> int:
            assert entity == 1
            return state.permission

        @staticmethod
        def alloc() -> Any:
            state.allocated += 1
            return SimpleNamespace(init=Store)

    def record(_item: Any) -> Any:
        state.converted += 1
        return _item_fixture()

    _item_fixture = _item
    monkeypatch.setitem(
        sys.modules,
        "EventKit",
        SimpleNamespace(
            EKEventStore=EventStore,
            EKEntityTypeReminder=1,
            EKAuthorizationStatusFullAccess=3,
            EKSourceTypeLocal=0,
            EKSourceTypeCalDAV=2,
        ),
    )
    monkeypatch.setattr(
        native,
        "_shim",
        lambda: SimpleNamespace(PyObjCEventStore=SimpleNamespace(_record=record)),
    )
    return native, state


def test_native_reader_never_scans_other_lists_or_requests_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native, state = _native_reader(monkeypatch)
    reader = native.NativeRemindersReader()
    snapshot = reader.read(_selection())
    assert len(snapshot.items) == 1
    assert state.converted == 1
    assert state.calls == [
        ("source", "fixture-source"),
        ("list", "fixture-list"),
        ("predicate", 1),
        ("source", "fixture-source"),
        ("list", "fixture-list"),
    ]
    state.missing = True
    assert reader.read(_selection()).list_name is None
    assert state.converted == 1


@pytest.mark.parametrize("fault", ["nil", "revoke", "wrong-source", "wrong-type"])
def test_native_read_errors_do_not_return_empty_or_foreign_snapshots(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    native, state = _native_reader(monkeypatch)
    reader = native.NativeRemindersReader()
    if fault in {"nil", "revoke"}:
        setattr(state, fault, True)
    elif fault == "wrong-source":
        state.source_id = "unreviewed-source"
    else:
        state.kind = 2
    with pytest.raises((PermissionError, RuntimeError)):
        reader.read(_selection())
    assert state.converted == 0


def test_icloud_requires_explicit_selected_source_and_permission_is_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native, state = _native_reader(monkeypatch)
    state.permission = 0
    with pytest.raises(PermissionError):
        native.NativeRemindersReader()
    assert state.allocated == 0
    state.permission = 3
    state.kind = 2
    reader = native.NativeRemindersReader()
    selected = _selection().model_copy(update={"source_kind": "icloud"})
    assert len(reader.read(selected).items) == 1
    state.permission = 0
    with pytest.raises(PermissionError):
        reader.read(selected)


def test_service_reminders_pack_obeys_existing_adapter_contract() -> None:
    from adminme.lib.pack_registry.validator import validate_adapter_pack
    from adminme.events.registry import registry

    result = validate_adapter_pack(
        ROOT / "packs/adapters/reminders/cos-apple-eventkit",
        registry=registry,
        projections_dir=ROOT / "adminme/projections",
    )
    assert not result.has_errors(), result.errors()


def test_os_authentication_receipt_does_not_invent_a_member_or_credential() -> None:
    from adminme.events.schemas.adapters import ServiceOSAuthenticatedV3

    receipt = {
        "adapter_name": ADAPTER_KIND,
        "adapter_family": "reminders",
        "authorization_kind": "os-permission",
        "authenticated_at": "2026-10-01T00:00:00Z",
        "configuration_sha256": "a" * 64,
        "device_identity_sha256": "b" * 64,
        "source_ownership_evidence_sha256": "c" * 64,
        "scopes": ["eventkit.reminders.read"],
    }
    ServiceOSAuthenticatedV3.model_validate(receipt)
    for field, value in (
        ("adapter_name", "reminders-apple-eventkit"),
        ("scopes", ["eventkit.reminders.write"]),
        ("token_storage_ref", "keychain://fixture/not-a-token"),
        ("member_id", MEMBER_A),
    ):
        with pytest.raises(ValueError):
            ServiceOSAuthenticatedV3.model_validate({**receipt, field: value})


@pytest.mark.asyncio
async def test_fresh_missing_list_is_not_authenticated_or_checkpointed(
    tmp_path: Path, runtime: Any
) -> None:
    ctx, log = runtime
    reader = Reader()
    reader.snapshot = replace(reader.snapshot, list_name=None, items=())
    adapter = _adapter(tmp_path, reader)
    with pytest.raises(RuntimeError, match="LookupError"):
        await adapter.start(ctx=ctx)
    assert [row async for row in log.read_since()] == []
    assert adapter.status().state == "crashed"


def test_setup_metadata_lookup_reads_no_reminders_and_rejects_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native, state = _native_reader(monkeypatch)
    reader = native.NativeRemindersReader()
    lookup = CoSRemindersLookup(list_name="Fixture list", source_kind="local")
    result = reader.inspect_selection(lookup)
    assert result == {
        "source_id": "fixture-source",
        "source_kind": "local",
        "source_title": "Fixture source",
        "list_id": "fixture-list",
        "list_name": "Fixture list",
    }
    assert state.converted == 0
    assert state.calls == [
        ("metadata", 1),
        ("list", "fixture-list"),
        ("source", "fixture-source"),
    ]
    for candidates in ([], state.catalog * 2):
        state.catalog = candidates
        with pytest.raises(ValueError, match="missing or ambiguous"):
            reader.inspect_selection(lookup)
    assert state.converted == 0


def test_setup_metadata_title_does_not_supply_account_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bootstrap.cos_apple_reminders import inspect_selection

    native, state = _native_reader(monkeypatch)
    state.kind = 2
    reader = native.NativeRemindersReader()
    lookup = CoSRemindersLookup(list_name="Fixture list", source_kind="icloud")
    inspected = inspect_selection(
        lookup, reader_factory=lambda: reader, identity=lambda: ("a" * 64, 501)
    )
    assert inspected["requires_source_ownership_review"] is True
    assert "source_ownership_evidence_sha256" not in inspected
    assert "owner_authorization_sha256" not in inspected
    assert "adapters" not in inspected
    with pytest.raises(ValueError, match="missing or ambiguous"):
        reader.inspect_selection(
            lookup.model_copy(update={"source_id": "other-source"})
        )
    with pytest.raises(ValueError, match="missing or ambiguous"):
        reader.inspect_selection(lookup.model_copy(update={"source_kind": "local"}))
    assert state.converted == 0


@pytest.mark.parametrize("request_permission", [False, True])
def test_preparation_cli_requests_consent_only_when_explicit_and_keeps_output_private(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: Any,
    request_permission: bool,
) -> None:
    import sys
    import bootstrap.cos_apple_reminders as setup

    previous = _configuration(tmp_path / "fixture")
    selection_path = tmp_path / "selection.json"
    previous_path = tmp_path / "previous.json"
    output = tmp_path / "prepared.json"
    for path, value in (
        (selection_path, _selection().model_dump()),
        (previous_path, previous),
    ):
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    calls = []
    monkeypatch.setattr(setup, "local_device_identity", lambda: ("a" * 64, 501))
    monkeypatch.setattr(
        setup, "request_reminders_permission", lambda: calls.append("consent")
    )
    monkeypatch.setattr(
        setup,
        "read_back",
        lambda selection: {"selected_list_verified": True, "provider_write_count": 0},
    )
    argv = [
        "setup",
        "--selection",
        str(selection_path),
        "--previous-config",
        str(previous_path),
        "--output",
        str(output),
    ]
    if request_permission:
        argv.append("--request-permission")
    monkeypatch.setattr(sys, "argv", argv)
    setup.main()
    assert calls == (["consent"] if request_permission else [])
    assert output.stat().st_mode & 0o777 == 0o600
    assert selected_entries(json.loads(output.read_text()))
    assert json.loads(capsys.readouterr().out)["status"] == "prepared_not_activated"
    with pytest.raises(ValueError, match="new private"):
        setup.main()
    assert calls == (["consent"] if request_permission else [])


@pytest.mark.asyncio
async def test_native_absolute_alarm_and_missing_modified_clock_are_stable(
    tmp_path: Path, runtime: Any
) -> None:
    ctx, log = runtime
    reader = Reader()
    reader.snapshot = replace(
        reader.snapshot,
        items=(
            _item(
                last_modified_at=None,
                alarms=[{"kind": "absolute", "absolute_at": ctx.now_factory()}],
            ),
        ),
    )
    adapter = _adapter(tmp_path, reader)
    try:
        await adapter.start(ctx=ctx)
        payload = (await _facts(log))[-1]["payload"]
        assert payload["alarms"][0]["absolute_at"] == "2026-10-02T00:00:00Z"
        later_ctx = replace(
            ctx, now_factory=lambda: datetime(2026, 10, 3, tzinfo=timezone.utc)
        )
        assert await adapter._poll_once(ctx=later_ctx) == 0
    finally:
        await adapter.stop()
