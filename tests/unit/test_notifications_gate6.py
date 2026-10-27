"""Gate 6 notification signal/decision/result contract."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.notifications import decide_notification, raise_and_decide
from adminme.runtime.application import RuntimeSettings, create_application


NOW = datetime(2035, 1, 1, 14, tzinfo=timezone.utc)
SIGNAL = {
    "signal_id": "signal-1",
    "member_id": "member-a",
    "category": "reminder",
    "urgency": "normal",
    "title": "A reminder",
    "body": "A deterministic fixture reminder.",
    "preferred_channels": ["openclaw"],
    "requires_approval": True,
    "not_before": None,
    "expires_at": None,
    "related_kind": "commitment",
    "related_id": "commitment-1",
}
PROFILE = {"behavior": {"quiet_hours": {"start": 21, "end": 7}}}
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_notification_policy_suppression_approval_routing_retry_and_circuit() -> None:
    assert (
        decide_notification(
            SIGNAL,
            profile=PROFILE,
            now=NOW,
            observation_active=True,
            approval_granted=True,
        )["decision"]
        == "suppress"
    )
    assert (
        decide_notification(
            SIGNAL,
            profile=PROFILE,
            now=NOW,
            observation_active=False,
            approval_granted=False,
        )["decision"]
        == "hold"
    )
    routed = decide_notification(
        SIGNAL,
        profile=PROFILE,
        now=NOW,
        observation_active=False,
        approval_granted=True,
    )
    assert routed["decision"] == "route"
    assert routed["route"] == "openclaw"
    quiet = decide_notification(
        {**SIGNAL, "requires_approval": False},
        profile=PROFILE,
        now=NOW.replace(hour=23),
        observation_active=False,
        approval_granted=False,
    )
    assert quiet["decision"] == "retry"
    assert quiet["retry_at"] is not None
    delayed = decide_notification(
        {
            **SIGNAL,
            "requires_approval": False,
            "not_before": "2035-01-01T15:00:00+00:00",
        },
        profile=PROFILE,
        now=NOW,
        observation_active=False,
        approval_granted=False,
    )
    assert delayed["decision"] == "retry"
    assert delayed["reasons"] == ["not_before"]
    circuit = decide_notification(
        {**SIGNAL, "requires_approval": False},
        profile=PROFILE,
        now=NOW,
        observation_active=False,
        approval_granted=False,
        prior_failures=3,
    )
    assert circuit["decision"] == "hold"


def test_notification_chain_uses_real_event_sink_and_accepts_delivery_result(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        config = load_instance_config(tmp_path)
        log = EventLog(config, b"n" * 32)
        bus = EventBus(log, config.bus_checkpoint_path)
        sink = EventWriter(log, bus)
        try:
            signal_id, decision_id, decision = await raise_and_decide(
                sink,
                SIGNAL,
                tenant_id=config.tenant_id,
                owner_scope="private:member-a",
                visibility_scope="private:member-a",
                sensitivity="normal",
                profile=PROFILE,
                now=NOW,
                observation_active=False,
                approval_granted=True,
                correlation_id="corr-notification",
            )
            assert decision["route"] == "openclaw"
            result_id = await sink.write(
                EventEnvelope(
                    event_at_ms=int(NOW.timestamp() * 1000),
                    tenant_id=config.tenant_id,
                    type="notification.delivery.result",
                    schema_version=1,
                    occurred_at=NOW.isoformat(),
                    source_adapter="openclaw-plugin",
                    source_account_id="fixture-channel",
                    owner_scope="private:member-a",
                    visibility_scope="private:member-a",
                    sensitivity="normal",
                    payload={
                        "attempt_id": "attempt-1",
                        "decision_id": decision["decision_id"],
                        "signal_id": SIGNAL["signal_id"],
                        "outcome": "delivered",
                        "route": "openclaw",
                        "attempted_at": NOW.isoformat(),
                        "provider_receipt_id": "fixture-receipt",
                        "error_code": None,
                        "next_retry_at": None,
                    },
                ),
                correlation_id="corr-notification",
                causation_id=decision_id,
            )
            chain = await log.get_by_correlation("corr-notification")
            assert [event["type"] for event in chain] == [
                "notification.signal.raised",
                "notification.decision.made",
                "notification.delivery.result",
            ]
            assert chain[1]["causation_id"] == signal_id
            assert chain[2]["event_id"] == result_id
        finally:
            await bus.stop()
            await log.close()

    asyncio.run(exercise())


def test_openclaw_result_endpoint_is_scoped_idempotent_and_sends_nothing(
    tmp_path: Path,
) -> None:
    async def seed() -> tuple[str, str]:
        config = load_instance_config(tmp_path)
        log = EventLog(config, b"o" * 32)
        bus = EventBus(log, config.bus_checkpoint_path)
        sink = EventWriter(log, bus)
        try:
            _signal_event_id, _decision_event_id, decision = await raise_and_decide(
                sink,
                {**SIGNAL, "requires_approval": False},
                tenant_id=config.tenant_id,
                owner_scope="private:member-a",
                visibility_scope="private:member-a",
                sensitivity="normal",
                profile=PROFILE,
                now=NOW,
                observation_active=False,
                approval_granted=False,
            )
            return decision["decision_id"], SIGNAL["signal_id"]
        finally:
            await bus.stop()
            await log.close()

    decision_id, signal_id = asyncio.run(seed())
    secret = "notification-result-fixture-secret"
    settings = RuntimeSettings.credential_free(
        tmp_path,
        encryption_key=b"o" * 32,
        repository_root=REPO_ROOT,
        member_identities={
            "member-a.ts.net": "member-a",
            "member-b.ts.net": "member-b",
        },
        openclaw_enabled=True,
        openclaw_shared_secret=secret,
        openclaw_actor_identities={
            "fixture|account-a|actor-a": "member-a",
            "fixture|account-b|actor-b": "member-b",
        },
        member_roles={"member-a": "principal", "member-b": "principal"},
    )
    provenance = {
        "actor_id": "actor-a",
        "agent_id": "agent-a",
        "session_key": "adminme:test:member-a",
        "session_id": "session-a",
        "channel_id": "fixture",
        "account_id": "account-a",
        "message_id": "message-a",
        "thread_id": None,
        "correlation_id": "corr-result",
        "call_kind": "tool",
        "openclaw_version": "2026.10.8",
        "openclaw_commit": "dc2523e5a5c7a309517fd7fa834add938231d526",
    }
    body = {
        "attempt_id": "attempt-endpoint-1",
        "decision_id": decision_id,
        "signal_id": signal_id,
        "outcome": "delivered",
        "attempted_at": NOW.isoformat(),
        "provider_receipt_id": "fixture-receipt",
        "error_code": None,
        "next_retry_at": None,
        "provenance": provenance,
    }
    headers = {"Authorization": f"Bearer {secret}"}
    with TestClient(create_application(settings)) as client:
        delegation = client.post(
            "/api/member/v1/cos/delegations",
            headers={"Tailscale-User-Login": "member-a.ts.net"},
            json={
                "request_id": "notification-result-cos",
                "context_scope": "member_private",
                "permissions": ["read_context", "manage_internal_work"],
            },
        )
        assert delegation.status_code == 201, delegation.text
        delegation_b = client.post(
            "/api/member/v1/cos/delegations",
            headers={"Tailscale-User-Login": "member-b.ts.net"},
            json={
                "request_id": "notification-result-cos-b",
                "context_scope": "member_private",
                "permissions": ["read_context", "manage_internal_work"],
            },
        )
        assert delegation_b.status_code == 201, delegation_b.text
        first = client.post(
            "/api/openclaw/v1/notifications/result", json=body, headers=headers
        )
        assert first.status_code == 200, first.text
        assert first.json()["duplicate"] is False
        duplicate = client.post(
            "/api/openclaw/v1/notifications/result", json=body, headers=headers
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True
        denied = client.post(
            "/api/openclaw/v1/notifications/result",
            json={
                **body,
                "attempt_id": "attempt-cross-owner",
                "provenance": {
                    **provenance,
                    "actor_id": "actor-b",
                    "account_id": "account-b",
                },
            },
            headers=headers,
        )
        assert denied.status_code == 404
