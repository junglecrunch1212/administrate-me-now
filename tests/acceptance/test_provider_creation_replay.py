"""A durable provider attempt is never repeated after an uncertain crash.

The production composition root, encrypted log, and durable consumer execute;
only the provider transport is a fixture, and no connected accounts are used.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.daemons.adapter_supervisor import AdapterSupervisor
from adminme.events.envelope import EventEnvelope
from adminme.lib.adapters.external_state_mirror_base import WriteResult
from adminme.runtime.application import RuntimeSettings, create_application


def _settings(path: Path) -> RuntimeSettings:
    path.mkdir()
    return RuntimeSettings.credential_free(
        path,
        encryption_key=b"r" * 32,
        repository_root=Path(__file__).resolve().parents[2],
        member_identities={"member-a.ts.net": "member-a"},
    )


async def _request(runtime: Any, provider: str) -> dict[str, Any]:
    now = EventEnvelope.now_utc_iso()
    request_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=1_792_000_000_000,
            tenant_id=runtime.settings.instance.tenant_id,
            type="external_sync.write_requested",
            schema_version=2,
            occurred_at=now,
            source_adapter="fixture-provider-command",
            source_account_id="fixture-account",
            owner_scope="private:member-a",
            visibility_scope="private:member-a",
            sensitivity="normal",
            idempotency_key=f"fixture-create:{provider}",
            payload={
                "command_id": f"fixture-create-{provider}",
                "member_id": "member-a",
                "provider": provider,
                "provider_account_id": "fixture-account",
                "operation": "create_task"
                if provider == "google_tasks"
                else "create_event",
                "payload": {"title": "Fixture creation"},
                "requested_at": now,
                "domain_approval_event_id": "fixture-domain-approval",
                "openclaw_approval_event_id": "fixture-openclaw-approval",
                "request_fingerprint": "a" * 64,
                "privacy_guard_version": "fixture-only",
                "origin_proposal_id": "fixture-proposal",
            },
        )
    )
    event = await runtime.event_log.get(request_id)
    assert event is not None
    return event


async def _receipt(runtime: Any, event: dict[str, Any], kind: str) -> Any:
    return await runtime.event_log.get_by_idempotency(
        event["tenant_id"], f"external-sync-{kind}:{event['payload']['command_id']}"
    )


@pytest.mark.parametrize("provider", ["google_tasks", "google_calendar"])
def test_completed_creation_replay_never_calls_provider_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    calls = 0

    async def fixture_dispatch(_self: Any, **_kwargs: Any) -> WriteResult:
        nonlocal calls
        calls += 1
        return WriteResult(applied=True, external_id="fixture-native-created-object")

    monkeypatch.setattr(AdapterSupervisor, "dispatch_write", fixture_dispatch)
    app = create_application(_settings(tmp_path / "completed"))
    with TestClient(app) as client:
        runtime = app.state.runtime
        event = client.portal.call(_request, runtime, provider)
        client.portal.call(
            runtime.wait_for_subscriber,
            "external-sync:central-providers",
            event["event_id"],
        )
        terminal = client.portal.call(_receipt, runtime, event, "result")
        assert terminal["type"] == "external_sync.write_completed"
        assert terminal["payload"]["result"]["applied"] is True
        assert client.portal.call(_receipt, runtime, event, "attempt") is not None
        for _ in range(2):
            client.portal.call(runtime._dispatch_external_write, event)
        assert calls == 1
        # A same-command receipt cannot settle a different member/account/request.
        for location, field, value in (
            ("root", "event_id", "fixture-other-request"),
            ("root", "owner_scope", "private:member-b"),
            ("payload", "provider_account_id", "fixture-other-account"),
            ("payload", "request_fingerprint", "b" * 64),
            ("payload", "domain_approval_event_id", "fixture-other-approval"),
        ):
            mismatched = deepcopy(event)
            (mismatched if location == "root" else mismatched["payload"])[field] = value
            with pytest.raises(
                RuntimeError, match="identity conflicts with its terminal"
            ):
                client.portal.call(runtime._dispatch_external_write, mismatched)
        assert calls == 1


class SimulatedCrash(BaseException):
    """An interrupted process must not turn an unknown creation into a retry."""


@pytest.mark.parametrize("provider", ["google_tasks", "google_calendar"])
@pytest.mark.parametrize("effect_happened", [False, True])
def test_restart_refuses_creation_after_attempt_with_no_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    effect_happened: bool,
) -> None:
    calls = 0
    effects = 0
    settings = _settings(tmp_path / "interrupted")
    app = create_application(settings)
    active_runtime: Any = None
    event: dict[str, Any] = {}

    async def interrupted_dispatch(_self: Any, **_kwargs: Any) -> WriteResult:
        nonlocal calls, effects
        calls += 1
        assert await _receipt(active_runtime, event, "attempt") is not None
        effects += int(effect_happened)
        raise SimulatedCrash()

    monkeypatch.setattr(AdapterSupervisor, "dispatch_write", interrupted_dispatch)
    with TestClient(app) as client:
        active_runtime = app.state.runtime
        # Capture the command without advancing the real durable checkpoint.
        active_runtime.bus._subscribers[
            "external-sync:central-providers"
        ].degraded = True
        event = client.portal.call(_request, active_runtime, provider)

        async def interrupt_after_attempt() -> None:
            try:
                await active_runtime._dispatch_external_write(event)
            except SimulatedCrash:
                return
            raise AssertionError("fixture transport did not interrupt dispatch")

        client.portal.call(interrupt_after_attempt)
        assert client.portal.call(_receipt, active_runtime, event, "result") is None
        assert calls == 1
        assert effects == int(effect_happened)

    restored = create_application(settings)
    with TestClient(restored) as client:
        active_runtime = restored.state.runtime
        client.portal.call(
            active_runtime.wait_for_subscriber,
            "external-sync:central-providers",
            event["event_id"],
        )
        terminal = client.portal.call(_receipt, active_runtime, event, "result")
        assert terminal["type"] == "external_sync.write_failed"
        assert terminal["payload"]["provider_called"] is True
        assert terminal["payload"]["retryable"] is False
        assert "ambiguous_after_possible_contact" in terminal["payload"]["message"]
        client.portal.call(active_runtime._dispatch_external_write, event)
        assert calls == 1
        assert effects == int(effect_happened)
