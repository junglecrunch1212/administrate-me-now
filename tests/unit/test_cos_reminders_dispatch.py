"""A native create is one durable attempt, exact read-back, and honest terminal."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from adminme.adapters.cos_reminders_helper import NativeWriteResult
from adminme.events.registry import registry
from adminme.runtime import cos_reminders as dispatch_module
from tests.unit.test_cos_reminders_commands import _chain


class MemoryLog:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)

    async def get(self, event_id):
        return self.rows.get(event_id)

    async def get_by_idempotency(self, tenant_id, key):
        return next(
            (
                row
                for row in self.rows.values()
                if row["tenant_id"] == tenant_id and row.get("idempotency_key") == key
            ),
            None,
        )

    async def write(self, envelope, **_):
        row = envelope.model_dump()
        registry.validate(row["type"], row["schema_version"], row["payload"])
        event_id = f"ev_{len(self.rows) + 1:03}"
        row["event_id"] = event_id
        self.rows[event_id] = row
        return event_id


class NativeWriter:
    def __init__(self, intent, *, fault=None):
        self.intent, self.fault, self.calls = intent, fault, 0

    def create(self, selection, command):
        self.calls += 1
        assert selection == self.intent.selection
        assert command.resource_owner == "service-principal:chief-of-staff"
        if self.fault == "timeout":
            raise TimeoutError("private provider details must not enter the terminal")
        due = command.due_at.astimezone(timezone.utc)
        native = {
            "applied": True,
            "suppressed": False,
            "external_id": "native-reminder-fixture",
            "provider_account_id": selection.provider_account_id,
            "source_id": selection.source_id,
            "list_id": selection.list_id,
            "requested_due_at": due.isoformat(),
            "observed_due": {
                **{
                    key: getattr(due, key)
                    for key in ("year", "month", "day", "hour", "minute", "second")
                },
                "time_zone": "UTC",
                "all_day": False,
                "floating": False,
            },
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.fault == "readback":
            native["observed_due"]["hour"] = (due.hour + 1) % 24
        return NativeWriteResult.model_validate_json(json.dumps(native))


def setup_runtime(tmp_path, monkeypatch):
    config, intent, command, rows, _ = _chain(tmp_path)
    command["sensitivity"] = "normal"
    rows[command["event_id"]] = command
    log = MemoryLog(rows)
    context = SimpleNamespace(
        permissions=frozenset({"prepare_external_actions"}),
        actor_principal_id="service-principal:chief-of-staff",
    )
    monkeypatch.setattr(
        dispatch_module, "resolve_service_delegation", lambda *_, **__: context
    )
    monkeypatch.setattr(
        "adminme.projections.parties.queries.resolve_service_delegation",
        lambda *_, **__: context,
    )
    control_dir = tmp_path / "config"
    control_dir.mkdir()
    state = SimpleNamespace(active=False, guard_calls=0)

    async def is_active():
        return state.active

    async def check(*_):
        state.guard_calls += 1
        return SimpleNamespace(pass_=True)

    runtime = SimpleNamespace(
        event_log=log,
        events=log,
        settings_command_locks={},
        settings=SimpleNamespace(
            adapter_config=config,
            instance=SimpleNamespace(
                tenant_id=command["tenant_id"], config_dir=control_dir
            ),
            member_roles={command["payload"]["member_id"]: "principal"},
            service_principals=[
                SimpleNamespace(
                    service_principal_id="service-principal:chief-of-staff",
                    kind="chief_of_staff",
                )
            ],
        ),
        projections=SimpleNamespace(connection=lambda _: None),
        observation=SimpleNamespace(is_active=is_active),
        guarded_write=SimpleNamespace(check=check),
    )
    return runtime, command, NativeWriter(intent), state


def events(runtime, event_type):
    return [row for row in runtime.event_log.rows.values() if row["type"] == event_type]


@pytest.mark.asyncio
async def test_one_native_effect_terminal_and_no_duplicate_on_parallel_replay(
    tmp_path, monkeypatch
):
    registry.autoload()
    runtime, command, writer, state = setup_runtime(tmp_path, monkeypatch)
    await asyncio.gather(
        *(dispatch_module.dispatch(runtime, command, writer=writer) for _ in range(3))
    )
    assert writer.calls == 1
    assert state.guard_calls == 1
    (attempt,) = events(runtime, "external_sync.write_attempted")
    (terminal,) = events(runtime, "external_sync.write_completed")
    (sent,) = events(runtime, "external.sent")
    assert attempt["event_id"] < sent["event_id"] < terminal["event_id"]
    assert terminal["payload"]["provider_called"] is True
    assert (
        terminal["payload"]["result"]["native"]["external_id"]
        == "native-reminder-fixture"
    )
    assert terminal["actor_identity"] == "service-principal:chief-of-staff"
    assert terminal["owner_scope"] == command["owner_scope"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["timeout", "readback"])
async def test_possible_native_contact_is_unknown_never_a_safe_retry(
    tmp_path, monkeypatch, fault
):
    registry.autoload()
    runtime, command, writer, _ = setup_runtime(tmp_path, monkeypatch)
    writer.fault = fault
    await dispatch_module.dispatch(runtime, command, writer=writer)
    await dispatch_module.dispatch(runtime, command, writer=writer)
    (terminal,) = events(runtime, "external_sync.write_failed")
    assert writer.calls == 1
    assert terminal["payload"]["provider_called"] is True
    assert terminal["payload"]["retryable"] is False
    assert "private provider details" not in terminal["payload"]["message"]
    assert not events(runtime, "external.sent")


@pytest.mark.asyncio
async def test_restart_after_attempt_does_not_repeat_native_save(tmp_path, monkeypatch):
    registry.autoload()
    runtime, command, writer, _ = setup_runtime(tmp_path, monkeypatch)
    await dispatch_module.dispatch(runtime, command, writer=writer)
    # Simulate the persisted prefix at a crash immediately after provider contact.
    runtime.event_log.rows = {
        key: row
        for key, row in runtime.event_log.rows.items()
        if row["type"] not in {"external.sent", "external_sync.write_completed"}
    }
    runtime.settings_command_locks = {}
    await dispatch_module.dispatch(runtime, command, writer=writer)
    (terminal,) = events(runtime, "external_sync.write_failed")
    assert terminal["payload"]["provider_called"] is True
    assert terminal["payload"]["retryable"] is False
    assert writer.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["revoked", "source", "role", "control", "observation"]
)
async def test_current_admission_denies_before_native_contact(
    tmp_path, monkeypatch, fault
):
    registry.autoload()
    runtime, command, writer, state = setup_runtime(tmp_path, monkeypatch)
    if fault == "revoked":
        runtime.settings.adapter_config["cos_apple_reminders_writes"]["enabled"] = False
    elif fault == "source":
        runtime.settings.adapter_config["cos_apple_reminders_writes"][
            "selection_sha256"
        ] = "0" * 64
    elif fault == "role":
        runtime.settings.member_roles.clear()
    elif fault == "control":
        (
            runtime.settings.instance.config_dir / "request-actions-control.json"
        ).write_text(
            json.dumps(
                {
                    "contract": "request-actions-control-v1",
                    "disabled": True,
                    "revoked_before": None,
                }
            )
        )
    else:
        state.active = True
    await dispatch_module.dispatch(runtime, command, writer=writer)
    assert writer.calls == 0
    assert not events(runtime, "external_sync.write_attempted")
    assert not events(runtime, "external.sent")
    terminal = events(runtime, "external_sync.write_completed") + events(
        runtime, "external_sync.write_failed"
    )
    assert len(terminal) == 1
    assert terminal[0]["payload"]["provider_called"] is False
    if fault == "observation":
        (suppressed,) = events(runtime, "observation.suppressed")
        assert suppressed["owner_scope"] == command["owner_scope"]
        assert terminal[0]["payload"]["suppressed"] is True
