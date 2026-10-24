"""Only the exact authorized native terminal can be reported as success."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from adminme.events.registry import registry
from adminme.products.openclaw.cos_apple_reminders import reminder_status
from adminme.runtime.cos_reminders import dispatch
from tests.unit.test_cos_reminders_dispatch import events, setup_runtime


def _session(command):
    return SimpleNamespace(
        tenant_id=command["tenant_id"],
        auth_member_id=command["payload"]["member_id"],
    )


@pytest.mark.asyncio
async def test_success_requires_terminal_and_exact_native_read_back(
    tmp_path, monkeypatch
):
    registry.autoload()
    runtime, command, writer, _ = setup_runtime(tmp_path, monkeypatch)
    before = await reminder_status(runtime, _session(command), "ev_003")
    assert before["state"] == "pending"
    assert before["provider_effect_count"] is None
    await dispatch(runtime, command, writer=writer)
    result = await reminder_status(runtime, _session(command), "ev_003")
    assert result["state"] == "succeeded"
    assert result["provider_effect_count"] == 1
    assert result["resource_owner"] == "service-principal:chief-of-staff"
    assert result["external_id"] == "native-reminder-fixture"
    assert result["automatic_retry"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["source", "actor", "account", "list", "time", "malformed"]
)
async def test_mismatched_terminal_is_unknown_not_success(tmp_path, monkeypatch, fault):
    registry.autoload()
    runtime, command, writer, _ = setup_runtime(tmp_path, monkeypatch)
    await dispatch(runtime, command, writer=writer)
    (terminal,) = events(runtime, "external_sync.write_completed")
    if fault == "source":
        terminal["source_adapter"] = "unrelated-dispatcher"
    elif fault == "actor":
        terminal["actor_identity"] = command["payload"]["member_id"]
    elif fault == "account":
        terminal["source_account_id"] = "another-native-account"
    elif fault == "list":
        terminal["payload"]["result"]["native"]["list_id"] = "another-list"
    elif fault == "time":
        terminal["payload"]["result"]["native"]["observed_due"]["floating"] = True
    else:
        terminal["payload"]["result"] = {"native": None}
    result = await reminder_status(runtime, _session(command), "ev_003")
    assert result["state"] == "unknown"
    assert result["provider_effect_count"] is None


@pytest.mark.asyncio
async def test_unknown_native_result_is_not_reported_as_failure_with_zero_effects(
    tmp_path, monkeypatch
):
    registry.autoload()
    runtime, command, writer, _ = setup_runtime(tmp_path, monkeypatch)
    writer.fault = "timeout"
    await dispatch(runtime, command, writer=writer)
    result = await reminder_status(runtime, _session(command), "ev_003")
    assert result["state"] == "unknown"
    assert result["provider_effect_count"] is None


@pytest.mark.asyncio
async def test_another_requester_cannot_read_private_terminal(tmp_path, monkeypatch):
    from fastapi import HTTPException

    runtime, command, _, _ = setup_runtime(tmp_path, monkeypatch)
    session = _session(command)
    session.auth_member_id = "different-member-fixture"
    with pytest.raises(HTTPException) as error:
        await reminder_status(runtime, session, "ev_003")
    assert error.value.status_code == 404
