"""Adapter-supervisor canaries.

Covers the three c1 supervisor tests called out in prompt 11a §7
Deliverables: minimal channels.yaml parses, spawns + stops adapter
tasks cleanly, restart-on-crash with exponential backoff.

The supervisor uses a stub-loader callable (PM-27) so tests inject
adapters without going through the production pack discovery path."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext, AdapterStatus
from adminme.daemons.adapter_supervisor import (
    INITIAL_BACKOFF_S,
    MAX_BACKOFF_S,
    UPTIME_RESET_S,
    AdapterSupervisor,
)
from adminme.lib.adapters import (
    CommunicationAdapter,
    InboundEnvelope,
    OutboundMessage,
    SendResult,
    WriteOperation,
    WriteResult,
)


class _FakeEventLog:
    """Captures synchronous append calls for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **_kwargs: Any) -> str:
        self.calls.append(envelope.model_dump())
        return f"event-{len(self.calls)}"


class _RecordingAdapter:
    name = "stub-adapter"
    family = "messaging"
    version = "0.0.0"
    sensitivity_floor = "normal"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self._status = AdapterStatus()

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        self.started.set()
        # Sleep until cancelled — emulates a long-running poll loop.
        await asyncio.Event().wait()

    async def stop(self) -> None:
        self.stopped.set()

    def status(self) -> AdapterStatus:
        return self._status


class _CrashThenStableAdapter:
    name = "crashy-adapter"
    family = "financial"
    version = "0.0.0"
    sensitivity_floor = "privileged"

    def __init__(self) -> None:
        self.start_count = 0
        self._status = AdapterStatus()

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        self.start_count += 1
        if self.start_count <= 2:
            raise RuntimeError(f"crash #{self.start_count}")
        # Stable on the 3rd attempt — block until cancelled.
        await asyncio.Event().wait()

    async def stop(self) -> None:
        return None

    def status(self) -> AdapterStatus:
        return self._status


class _WritableGoogleTasksAdapter(_RecordingAdapter):
    name = "tasks-google-tasks"
    family = "tasks"

    def __init__(self) -> None:
        super().__init__()
        self._config = {
            "member_id": "member-a",
            "user_email": "member-a@example.invalid",
        }
        self.operations: list[WriteOperation] = []

    async def apply_write(self, operation: WriteOperation) -> WriteResult:
        self.operations.append(operation)
        return WriteResult(applied=True, external_id="google-task-1", suppressed=False)


class _WritableGmailAdapter(CommunicationAdapter):
    name = "messaging-gmail-api"
    version = "1.0.0"

    def __init__(self) -> None:
        self._config = {
            "member_id": "member-a",
            "user_email": "member-a@example.invalid",
        }
        self.sent: list[OutboundMessage] = []
        self.managed: list[tuple[str, str | None]] = []
        self._status = AdapterStatus(state="running")

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        return None

    async def stop(self) -> None:
        return None

    def status(self) -> AdapterStatus:
        return self._status

    async def send(self, message: OutboundMessage) -> SendResult:
        self.sent.append(message)
        return SendResult(delivered=True, external_id="gmail-message-1")

    async def _on_inbound(self, envelope: InboundEnvelope) -> None:  # noqa: ARG002
        return None

    async def manage_message(
        self, *, operation: str, native_message_id: str | None
    ) -> dict[str, Any]:
        self.managed.append((operation, native_message_id))
        return {
            "applied": True,
            "suppressed": False,
            "external_id": native_message_id,
            "state": {"archived": operation == "archive"},
        }


def _make_ctx_factory(event_log: _FakeEventLog) -> Any:
    def _factory() -> AdapterContext:
        return AdapterContext(
            event_sink=event_log,  # type: ignore[arg-type]
            config={},
            correlation_id_factory=lambda: "corr-test",
            now_factory=lambda: datetime(2026, 5, 16, tzinfo=timezone.utc),
        )

    return _factory


# ---------------------------------------------------------------------------
# config parsing
# ---------------------------------------------------------------------------


async def test_supervisor_parses_minimal_channels_yaml(tmp_path: Path) -> None:
    channels = tmp_path / "channels.yaml"
    channels.write_text(
        """
adapter_supervisor_host: 127.0.0.1
adapter_supervisor_port: 0
adapters:
  - kind: messaging-gmail-api
    family: messaging
    sensitivity_floor: normal
        """.strip()
        + "\n",
        encoding="utf-8",
    )
    sup = AdapterSupervisor(
        config_path=channels,
        ctx_factory=_make_ctx_factory(_FakeEventLog()),
    )
    config = sup.load_config()
    assert config["adapter_supervisor_port"] == 0
    assert config["adapters"][0]["kind"] == "messaging-gmail-api"


async def test_supervisor_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    channels = tmp_path / "channels.yaml"
    channels.write_text("- not-a-mapping\n", encoding="utf-8")
    sup = AdapterSupervisor(
        config_path=channels,
        ctx_factory=_make_ctx_factory(_FakeEventLog()),
    )
    with pytest.raises(ValueError):
        sup.load_config()


# ---------------------------------------------------------------------------
# lifecycle — start / stop cleanly
# ---------------------------------------------------------------------------


async def test_supervisor_spawns_and_stops_adapter_cleanly(tmp_path: Path) -> None:
    channels = tmp_path / "channels.yaml"
    channels.write_text("adapter_supervisor_port: 0\nadapters: []\n", encoding="utf-8")

    adapter = _RecordingAdapter()
    sup = AdapterSupervisor(
        config_path=channels,
        ctx_factory=_make_ctx_factory(_FakeEventLog()),
        adapter_loader=lambda _cfg: [adapter],  # type: ignore[list-item]
    )

    await sup.start()
    await asyncio.wait_for(adapter.started.wait(), timeout=1.0)
    snapshot = sup.snapshot()
    assert snapshot["adapters"]["stub-adapter"]["state"] == "running"

    await sup.stop()
    assert adapter.stopped.is_set()
    snapshot_after = sup.snapshot()
    assert snapshot_after["adapters"]["stub-adapter"]["state"] == "stopped"


async def test_supervisor_routes_google_tasks_by_member_and_user_email(
    tmp_path: Path,
) -> None:
    channels = tmp_path / "channels.yaml"
    channels.write_text("adapter_supervisor_port: 0\nadapters: []\n", encoding="utf-8")
    adapter = _WritableGoogleTasksAdapter()
    sup = AdapterSupervisor(
        config_path=channels,
        ctx_factory=_make_ctx_factory(_FakeEventLog()),
        adapter_loader=lambda _cfg: [adapter],  # type: ignore[list-item]
    )
    sup.prepare()
    operation = WriteOperation(
        kind="create_task",
        target_external_id=None,
        payload={"tasklist_id": "@default", "title": "Fixture task"},
    )
    result = await sup.dispatch_write(
        provider="google_tasks",
        member_id="member-a",
        provider_account_id="member-a@example.invalid",
        operation=operation,
    )
    assert result.external_id == "google-task-1"
    assert adapter.operations == [operation]

    with pytest.raises(LookupError):
        await sup.dispatch_write(
            provider="google_tasks",
            member_id="member-a",
            provider_account_id="other@example.invalid",
            operation=operation,
        )


async def test_supervisor_routes_comms_to_exact_member_account(
    tmp_path: Path,
) -> None:
    channels = tmp_path / "channels.yaml"
    channels.write_text("adapter_supervisor_port: 0\nadapters: []\n", encoding="utf-8")
    adapter = _WritableGmailAdapter()
    sup = AdapterSupervisor(
        config_path=channels,
        ctx_factory=_make_ctx_factory(_FakeEventLog()),
        adapter_loader=lambda _cfg: [adapter],  # type: ignore[list-item]
    )
    sup.prepare()

    reply = await sup.dispatch_communication_write(
        provider="gmail",
        member_id="member-a",
        provider_account_id="member-a@example.invalid",
        operation="reply",
        payload={
            "recipient": "sender@example.invalid",
            "body": "Fixture reply",
            "thread_id": "thread-1",
            "rfc_message_id": "rfc-1",
            "interaction_id": "interaction-1",
        },
    )
    assert reply["applied"] is True
    assert adapter.sent[0].metadata["thread_id"] == "thread-1"
    assert adapter.sent[0].metadata["in_reply_to"] == "rfc-1"

    archived = await sup.dispatch_communication_write(
        provider="gmail",
        member_id="member-a",
        provider_account_id="member-a@example.invalid",
        operation="archive",
        payload={"native_message_id": "native-1"},
    )
    assert archived["state"] == {"archived": True}
    assert adapter.managed == [("archive", "native-1")]

    with pytest.raises(LookupError):
        await sup.dispatch_communication_write(
            provider="gmail",
            member_id="member-b",
            provider_account_id="member-a@example.invalid",
            operation="reply",
            payload={"recipient": "sender@example.invalid", "body": "Fixture"},
        )


# ---------------------------------------------------------------------------
# restart-on-crash with exponential backoff
# ---------------------------------------------------------------------------


async def test_supervisor_restarts_on_crash_with_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter crashes twice, then stabilises. The supervisor emits
    ``adapter.error`` on each crash with monotonically doubling
    ``retry_after_s`` and bumps ``restart_count``."""

    channels = tmp_path / "channels.yaml"
    channels.write_text("adapter_supervisor_port: 0\nadapters: []\n", encoding="utf-8")

    # Speed up the backoff window so the test doesn't sleep for seconds.
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def _fast_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr("adminme.daemons.adapter_supervisor.asyncio.sleep", _fast_sleep)

    event_log = _FakeEventLog()
    adapter = _CrashThenStableAdapter()
    sup = AdapterSupervisor(
        config_path=channels,
        ctx_factory=_make_ctx_factory(event_log),
        adapter_loader=lambda _cfg: [adapter],  # type: ignore[list-item]
    )
    await sup.start()

    # Wait for the third start (the stable one) to be reached.
    for _ in range(200):
        if adapter.start_count >= 3:
            break
        await asyncio.sleep(0.01)
    assert adapter.start_count >= 3

    snapshot = sup.snapshot()
    managed = snapshot["adapters"]["crashy-adapter"]
    assert managed["restart_count"] >= 2

    # adapter.error events emitted with doubling retry_after_s.
    error_calls = [c for c in event_log.calls if c.get("type") == "adapter.error"]
    assert len(error_calls) >= 2
    # Provider exception bodies may contain account IDs or receipt data; shared
    # supervisor events and public status retain only a fixed safe message.
    assert "crash #" not in repr(error_calls)
    assert "crash #" not in repr(snapshot)
    assert error_calls[0]["payload"]["error_message"] == (
        "Adapter failed; inspect the authorized source status."
    )
    first_retry = error_calls[0]["payload"]["retry_after_s"]
    second_retry = error_calls[1]["payload"]["retry_after_s"]
    assert first_retry == int(INITIAL_BACKOFF_S)
    assert second_retry == int(INITIAL_BACKOFF_S * 2)
    # Backoff scheduled at least once with the initial window.
    assert any(abs(s - INITIAL_BACKOFF_S) < 1e-6 for s in sleeps)

    await sup.stop()


# ---------------------------------------------------------------------------
# constants live under the sane bounds the prompt declares
# ---------------------------------------------------------------------------


def test_supervisor_backoff_constants() -> None:
    assert INITIAL_BACKOFF_S == 1.0
    assert MAX_BACKOFF_S == 300.0
    assert UPTIME_RESET_S == 300.0


class _ConfiguredRecordingAdapter(_RecordingAdapter):
    def __init__(self, entry: dict[str, Any]) -> None:
        super().__init__()
        self.instance_name = entry["id"]
        self._config = dict(entry.get("config", {}))


def _reconciliation_config() -> dict[str, Any]:
    return {
        "adapter_supervisor_port": 0,
        "adapters": [
            {"id": "cos-email", "config": {"service_principal_id": "cos"}},
            {
                "id": "member-calendar",
                "config": {"member_id": "member-a", "enable_writes": False},
            },
            {"id": "other-calendar", "config": {"member_id": "member-b"}},
            {
                "id": "member-tasks",
                "enabled": False,
                "config": {"member_id": "member-a"},
            },
        ],
    }


def _reconciliation_supervisor(
    tmp_path: Path, config: dict[str, Any]
) -> AdapterSupervisor:
    def loader(value: dict[str, Any]) -> Any:
        return [
            _ConfiguredRecordingAdapter(entry)
            for entry in value["adapters"]
            if entry.get("enabled") is not False
        ]

    return AdapterSupervisor(
        config_path=tmp_path / "unused.yaml",
        config_data=config,
        ctx_factory=_make_ctx_factory(_FakeEventLog()),
        adapter_loader=loader,
    )


async def test_selected_reconciliation_preserves_unrelated_tasks_and_backoff(
    tmp_path: Path,
) -> None:
    from copy import deepcopy

    config = _reconciliation_config()
    sup = _reconciliation_supervisor(tmp_path, config)
    await sup.start()
    await asyncio.sleep(0)
    before = dict(sup._managed)
    cos_task = before["cos-email"].task
    other_task = before["other-calendar"].task
    before["cos-email"].backoff_s = 64
    before["cos-email"].status.restart_count = 3
    candidate = deepcopy(config)
    candidate["adapters"][1]["config"]["enable_writes"] = True
    try:
        result = await sup.reconcile(
            candidate, affected_instance_ids={"member-calendar"}
        )
        assert sup._managed["cos-email"] is before["cos-email"]
        assert sup._managed["cos-email"].task is cos_task
        assert sup._managed["cos-email"].backoff_s == 64
        assert result["adapters"]["cos-email"]["restart_count"] == 3
        assert sup._managed["other-calendar"] is before["other-calendar"]
        assert sup._managed["other-calendar"].task is other_task
        assert not before["cos-email"].adapter.stopped.is_set()
        assert not before["other-calendar"].adapter.stopped.is_set()
        assert before["member-calendar"].adapter.stopped.is_set()
        assert before["member-calendar"].task.done()
        replacement = sup._managed["member-calendar"]
        assert replacement is not before["member-calendar"]
        assert replacement.adapter.started.is_set()
        assert replacement.adapter._config["enable_writes"] is True
    finally:
        await sup.stop()


@pytest.mark.parametrize("remove", [False, True])
async def test_selected_reconciliation_disables_or_removes_and_enables_exact_rows(
    tmp_path: Path, remove: bool
) -> None:
    from copy import deepcopy

    config = _reconciliation_config()
    sup = _reconciliation_supervisor(tmp_path, config)
    await sup.start()
    await asyncio.sleep(0)
    before = dict(sup._managed)
    candidate = deepcopy(config)
    candidate["adapters"][3]["enabled"] = True
    if remove:
        candidate["adapters"].pop(1)
    else:
        candidate["adapters"][1]["enabled"] = False
    try:
        await sup.reconcile(
            candidate,
            affected_instance_ids={"member-calendar", "member-tasks"},
        )
        assert set(sup._managed) == {"cos-email", "other-calendar", "member-tasks"}
        assert sup._managed["cos-email"] is before["cos-email"]
        assert sup._managed["other-calendar"] is before["other-calendar"]
        assert before["member-calendar"].adapter.stopped.is_set()
        assert sup._managed["member-tasks"].adapter.started.is_set()
    finally:
        await sup.stop()


@pytest.mark.parametrize(
    "mutation",
    ["other_permission", "global_port", "remove_other", "add_other", "duplicate_id"],
)
async def test_selected_reconciliation_rejects_unrelated_drift_before_any_stop(
    tmp_path: Path, mutation: str
) -> None:
    from copy import deepcopy

    config = _reconciliation_config()
    sup = _reconciliation_supervisor(tmp_path, config)
    await sup.start()
    await asyncio.sleep(0)
    before = dict(sup._managed)
    candidate = deepcopy(config)
    if mutation == "other_permission":
        candidate["adapters"][0]["config"]["enable_writes"] = True
    elif mutation == "global_port":
        candidate["adapter_supervisor_port"] = 8082
    elif mutation == "remove_other":
        candidate["adapters"].pop(2)
    elif mutation == "add_other":
        candidate["adapters"].append({"id": "new-unselected", "config": {}})
    else:
        candidate["adapters"].append(deepcopy(candidate["adapters"][1]))
    try:
        with pytest.raises(ValueError, match="selected reconciliation"):
            await sup.reconcile(candidate, affected_instance_ids={"member-calendar"})
        assert sup._config == config
        assert sup._managed == before
        assert all(not value.adapter.stopped.is_set() for value in before.values())
        assert all(not value.task.done() for value in before.values())
    finally:
        await sup.stop()


async def test_selected_reconciliation_validates_loader_before_stopping(
    tmp_path: Path,
) -> None:
    from copy import deepcopy

    config = _reconciliation_config()
    sup = _reconciliation_supervisor(tmp_path, config)
    await sup.start()
    await asyncio.sleep(0)
    before = dict(sup._managed)
    candidate = deepcopy(config)
    candidate["adapters"][1]["config"]["invalid"] = True

    def broken_loader(_value: dict[str, Any]) -> Any:
        raise ValueError("invalid candidate adapter")

    sup._loader = broken_loader
    try:
        with pytest.raises(ValueError, match="invalid candidate"):
            await sup.reconcile(candidate, affected_instance_ids={"member-calendar"})
        assert sup._managed == before
        assert all(not value.adapter.stopped.is_set() for value in before.values())
    finally:
        await sup.stop()


async def test_selected_reconciliation_refuses_unrelated_instance_identity_drift(
    tmp_path: Path,
) -> None:
    config = _reconciliation_config()
    sup = _reconciliation_supervisor(tmp_path, config)
    await sup.start()
    await asyncio.sleep(0)
    before = dict(sup._managed)
    loader = sup._loader

    def renamed_loader(value: dict[str, Any]) -> Any:
        adapters = loader(value)
        adapters[0].instance_name = "unselected-renamed"
        return adapters

    sup._loader = renamed_loader
    try:
        with pytest.raises(ValueError, match="unrelated adapter instances"):
            await sup.reconcile(config, affected_instance_ids={"member-calendar"})
        assert sup._managed == before
        assert all(not value.adapter.stopped.is_set() for value in before.values())
    finally:
        await sup.stop()


async def test_selected_reconciliation_allows_new_explicit_row_and_empty_noop(
    tmp_path: Path,
) -> None:
    from copy import deepcopy

    config = _reconciliation_config()
    sup = _reconciliation_supervisor(tmp_path, config)
    await sup.start()
    await asyncio.sleep(0)
    before = dict(sup._managed)
    candidate = deepcopy(config)
    candidate["adapters"].append({"id": "member-gmail", "config": {}})
    try:
        await sup.reconcile(candidate, affected_instance_ids={"member-gmail"})
        assert sup._managed["member-gmail"].adapter.started.is_set()
        assert all(sup._managed[name] is value for name, value in before.items())
        after = dict(sup._managed)
        await sup.reconcile(candidate, affected_instance_ids=set())
        assert sup._managed == after
        assert all(not value.adapter.stopped.is_set() for value in after.values())
        with pytest.raises(ValueError, match="unconfigured instance"):
            await sup.reconcile(candidate, affected_instance_ids={"misspelled"})
        assert sup._managed == after
    finally:
        await sup.stop()


async def test_default_reconciliation_keeps_full_restart_behavior(
    tmp_path: Path,
) -> None:
    config = _reconciliation_config()
    sup = _reconciliation_supervisor(tmp_path, config)
    await sup.start()
    await asyncio.sleep(0)
    before = dict(sup._managed)
    try:
        await sup.reconcile(config)
        assert set(sup._managed) == set(before)
        assert all(sup._managed[name] is not value for name, value in before.items())
        assert all(value.adapter.stopped.is_set() for value in before.values())
        assert all(value.adapter.started.is_set() for value in sup._managed.values())
    finally:
        await sup.stop()


async def test_stop_instances_contains_postcommit_failure_without_loading_or_stopping_others(
    tmp_path: Path,
) -> None:
    config = _reconciliation_config()
    sup = _reconciliation_supervisor(tmp_path, config)
    await sup.start()
    await asyncio.sleep(0)
    before = dict(sup._managed)

    def broken_loader(_value: dict[str, Any]) -> Any:
        raise AssertionError("containment must not construct candidate adapters")

    sup._loader = broken_loader
    try:
        result = await sup.stop_instances({"member-calendar", "member-tasks"})
        assert set(result["adapters"]) == {"cos-email", "other-calendar"}
        assert "member-calendar" not in sup._managed
        assert before["member-calendar"].adapter.stopped.is_set()
        assert before["member-calendar"].task.done()
        for name in ("cos-email", "other-calendar"):
            assert sup._managed[name] is before[name]
            assert not before[name].adapter.stopped.is_set()
            assert not before[name].task.done()
        # Repeated containment is safe and never reactivates the removed row.
        assert await sup.stop_instances({"member-calendar"}) == result
    finally:
        await sup.stop()


def _google_selection(adapter_id: str, member_id: str) -> dict[str, Any]:
    return {
        "adapter_id": adapter_id,
        "member_id": member_id,
        "provider_account_id": f"{member_id}@example.invalid",
        "access": "read_only",
        "binding_sha256": "a" * 64,
        "owner_authorization_sha256": "b" * 64,
    }


async def test_selected_google_policy_first_addition_and_narrowing_preserve_other_rows(
    tmp_path: Path,
) -> None:
    from copy import deepcopy

    config = _reconciliation_config()
    sup = _reconciliation_supervisor(tmp_path, config)
    await sup.start()
    await asyncio.sleep(0)
    before = dict(sup._managed)
    candidate = deepcopy(config)
    selection = _google_selection("member-calendar", "member-a")
    selection["access"] = "read_write"
    candidate["member_google_connections"] = {"selections": [selection]}
    candidate["adapters"][1]["config"]["enable_writes"] = True
    try:
        await sup.reconcile(candidate, affected_instance_ids={"member-calendar"})
        assert sup._managed["cos-email"] is before["cos-email"]
        assert sup._managed["other-calendar"] is before["other-calendar"]
        assert sup._managed["member-calendar"].adapter._config["enable_writes"]
        narrowed = deepcopy(candidate)
        narrowed["adapters"][1]["config"]["enable_writes"] = False
        narrowed["member_google_connections"]["selections"][0].update(
            access="read_only",
            binding_sha256="c" * 64,
            owner_authorization_sha256="d" * 64,
        )
        await sup.reconcile(narrowed, affected_instance_ids={"member-calendar"})
        assert sup._managed["cos-email"] is before["cos-email"]
        assert not sup._managed["member-calendar"].adapter._config["enable_writes"]
    finally:
        await sup.stop()


@pytest.mark.parametrize("change", ["access", "proof", "invariant"])
async def test_selected_google_policy_rejects_other_selection_or_invariant_changes(
    tmp_path: Path, change: str
) -> None:
    from copy import deepcopy

    config = _reconciliation_config()
    config["member_google_connections"] = {
        "selections": [
            _google_selection("member-calendar", "member-a"),
            _google_selection("other-calendar", "member-b"),
        ]
    }
    sup = _reconciliation_supervisor(tmp_path, config)
    await sup.start()
    await asyncio.sleep(0)
    before = dict(sup._managed)
    candidate = deepcopy(config)
    if change == "access":
        candidate["member_google_connections"]["selections"][1]["access"] = "read_write"
    elif change == "proof":
        candidate["member_google_connections"]["selections"][1][
            "owner_authorization_sha256"
        ] = "c" * 64
    else:
        candidate["member_google_connections"]["automated_candidate_extraction"] = True
    try:
        with pytest.raises(ValueError):
            await sup.reconcile(candidate, affected_instance_ids={"member-calendar"})
        assert sup._managed == before
        assert all(not value.adapter.stopped.is_set() for value in before.values())
    finally:
        await sup.stop()


@pytest.mark.parametrize("operation", ["reconcile", "contain"])
async def test_revoked_dispatch_is_removed_before_slow_adapter_shutdown(
    tmp_path: Path, operation: str
) -> None:
    class SlowStopTasks(_WritableGoogleTasksAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.instance_name = "member-tasks"
            self.stop_entered = asyncio.Event()
            self.release_stop = asyncio.Event()

        async def stop(self) -> None:
            self.stop_entered.set()
            await self.release_stop.wait()
            await super().stop()

    adapter = SlowStopTasks()
    cos = _RecordingAdapter()
    cos.instance_name = "cos-email"
    config = {
        "adapter_supervisor_port": 0,
        "adapters": [{"id": "member-tasks"}, {"id": "cos-email"}],
    }
    sup = AdapterSupervisor(
        config_path=tmp_path / "unused.yaml",
        config_data=config,
        ctx_factory=_make_ctx_factory(_FakeEventLog()),
        adapter_loader=lambda value: (
            [cos]
            + ([adapter] if value["adapters"][0].get("enabled") is not False else [])
        ),  # type: ignore[list-item]
    )
    await sup.start()
    await asyncio.sleep(0)
    cos_managed = sup._managed["cos-email"]
    pending = asyncio.create_task(
        sup.reconcile(
            {
                **config,
                "adapters": [
                    {"id": "member-tasks", "enabled": False},
                    {"id": "cos-email"},
                ],
            },
            affected_instance_ids={"member-tasks"},
        )
        if operation == "reconcile"
        else sup.stop_instances({"member-tasks"})
    )
    try:
        await asyncio.wait_for(adapter.stop_entered.wait(), timeout=1)
        with pytest.raises(LookupError, match="found 0"):
            await sup.dispatch_write(
                provider="google_tasks",
                member_id="member-a",
                provider_account_id="member-a@example.invalid",
                operation=WriteOperation(
                    kind="create_task",
                    target_external_id=None,
                    payload={"title": "Must never be dispatched"},
                ),
            )
        assert adapter.operations == []
        assert sup._managed["cos-email"] is cos_managed
        assert not cos.stopped.is_set()
    finally:
        adapter.release_stop.set()
        await pending
        await sup.stop()
