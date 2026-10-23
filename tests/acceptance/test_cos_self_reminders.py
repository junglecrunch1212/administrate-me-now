"""Production reminder intent, restart, exact timer, guarded send and revocation."""

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.cos_response_assurance import AUDIT_TYPES, cos_provider_effect_counts
from adminme.events.envelope import EventEnvelope
from adminme.historical_attribution import _cos_response_owner_matches
from adminme.openclaw_topology import member_agent_id
from adminme.runtime.application import create_application
from tests.acceptance.test_cos_self_replies import _activate, _inbound, _send, ENDPOINT
from tests.acceptance.test_cos_response_execution import (
    A,
    AUTH,
    _settings,
    _events,
    _provider_assurance,
)

BASE = "/api/openclaw/v1/cos-reminders"
JOB = "isolated-reminder-timer"


def enable(client: TestClient) -> None:
    path = (
        client.app.state.runtime.settings.instance.config_dir.parent
        / "openclaw/runtime/openclaw.json"
    )
    value = json.loads(path.read_text())
    config = value["plugins"]["entries"]["administrateme"]["config"]
    config["scheduledSelfReminderBindings"] = config["standingSelfReplyBindings"]
    path.write_text(json.dumps(value))


def prepare(client: TestClient) -> tuple[dict[str, Any], dict[str, Any], datetime]:
    source = _inbound(
        client, text="Text me a reminder about the weekend plan tomorrow morning."
    )
    source["body"] = "Remember to discuss the weekend plan."
    due = datetime.now(timezone.utc) + timedelta(hours=13)
    body = {**source, "due_at": due.isoformat()}
    result = client.post(BASE + "/prepare", headers=AUTH, json=body)
    assert result.status_code == 200, result.text
    reminder_id = result.json()["reminder_event_id"]
    bound = client.post(
        BASE + "/bind",
        headers=AUTH,
        json={**source, "reminder_event_id": reminder_id, "job_id": JOB},
    )
    assert bound.status_code == 200, bound.text
    return (
        body,
        {
            "reminder_event_id": reminder_id,
            "job_id": JOB,
            "agent_id": member_agent_id(A),
            "session_key": f"agent:{member_agent_id(A)}:cron:{JOB}:trigger",
        },
        due,
    )


def advance(monkeypatch: pytest.MonkeyPatch, when: datetime) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return when.astimezone(tz) if tz else when.replace(tzinfo=None)

    for name in ("cos_reminders", "cos_self_replies", "cos_responses", "main"):
        monkeypatch.setattr(f"adminme.products.openclaw.{name}.datetime", Clock)


@pytest.mark.parametrize(
    "terminal_status,reactivate", [("sent", False), ("sent", True), ("unknown", False)]
)
def test_requested_reminder_survives_restart_and_never_repeats_unknown_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
    reactivate: bool,
) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_application(settings)) as client:
        authority = _activate(client, monkeypatch)
        enable(client)
        request, callback, due = prepare(client)
        repeated = client.post(BASE + "/prepare", headers=AUTH, json=request)
        assert repeated.json()["reminder_event_id"] == callback["reminder_event_id"]
        assert (
            client.post(BASE + "/fire", headers=AUTH, json=callback).status_code == 409
        )
    advance(monkeypatch, due + timedelta(seconds=1))
    with TestClient(create_application(settings)) as client:
        if reactivate:
            # A routine restart/deployment may renew the channel grant. Keep
            # the original request authority and validate the current send grant.
            runtime = client.app.state.runtime
            grant = copy.deepcopy(
                client.portal.call(runtime.event_log.get, authority["event_id"])
            )
            grant.pop("event_id")
            grant.pop("recorded_at")
            renewed_at = due - timedelta(seconds=1)
            grant["event_at_ms"] = int(renewed_at.timestamp() * 1000)
            grant["occurred_at"] = renewed_at.isoformat()
            grant["payload"]["enabled_at"] = renewed_at.isoformat()
            authority["event_id"] = client.portal.call(
                runtime.events.write, EventEnvelope(**grant)
            )
        # The original inbound transient reply window has expired.
        ordinary = client.post(
            ENDPOINT + "/self-reply",
            headers=AUTH,
            json={k: v for k, v in request.items() if k != "due_at"},
        )
        assert ordinary.status_code == 403
        result = client.post(BASE + "/fire", headers=AUTH, json=callback)
        assert result.status_code == 200, result.text
        dispatch = result.json()["transport_dispatch"]
        assert dispatch["body"] == request["body"]
        claim = client.post(
            ENDPOINT + "/claim",
            headers=AUTH,
            json={k: dispatch[k] for k in ("account_id", "recipient", "body")},
        )
        assert claim.status_code == 200, claim.text
        terminal = client.post(
            ENDPOINT + "/terminal",
            headers=AUTH,
            json={"claim_id": claim.json()["claim_id"], "status": terminal_status},
        )
        assert terminal.status_code == 200, terminal.text
        repeated = client.post(BASE + "/fire", headers=AUTH, json=callback)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["status"] == terminal_status
        assert "transport_dispatch" not in repeated.json()
        runtime = client.app.state.runtime
        attempts = client.portal.call(_events, runtime, ["cos.response.attempted"])
        assert len(attempts) == 1
        assert client.portal.call(
            _cos_response_owner_matches, runtime.event_log, attempts[0]["event_id"]
        )
        if terminal_status == "sent":
            assurance = client.portal.call(_provider_assurance, runtime)
            assert assurance["state"] == "healthy", assurance
            history = client.portal.call(_events, runtime, sorted(AUDIT_TYPES))
            for missing in ("cos.reply.reminder_requested", "cos.reply.reminder_bound"):
                incomplete = [event for event in history if event["type"] != missing]
                assert (
                    cos_provider_effect_counts(incomplete)[
                        "approval_correlation_missing"
                    ]
                    > 0
                )
            changed = copy.deepcopy(history)
            next(
                event
                for event in changed
                if event["type"] == "cos.reply.reminder_requested"
            )["payload"]["body"] = "Unrequested replacement."
            assert (
                cos_provider_effect_counts(changed)["approval_correlation_missing"] > 0
            )
    with TestClient(create_application(settings)) as client:
        repeated = client.post(BASE + "/fire", headers=AUTH, json=callback)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["status"] == terminal_status
        assert "transport_dispatch" not in repeated.json()
        if terminal_status == "sent":
            # Recovery checkpoint: keep the schema-aware artifact, disable the
            # new capability, and preserve ordinary replies with retained receipts.
            path = (
                client.app.state.runtime.settings.instance.config_dir.parent
                / "openclaw/runtime/openclaw.json"
            )
            value = json.loads(path.read_text())
            value["plugins"]["entries"]["administrateme"]["config"][
                "scheduledSelfReminderBindings"
            ] = []
            path.write_text(json.dumps(value))
            _send(client, _inbound(client, "ordinary-after-reminder-recovery"))
            assert (
                client.post(BASE + "/fire", headers=AUTH, json=callback).status_code
                == 403
            )
            assurance = client.portal.call(
                _provider_assurance, client.app.state.runtime
            )
            assert assurance["state"] == "healthy", assurance


@pytest.mark.parametrize("change", ["cancel", "host", "domain", "job", "agent", "late"])
def test_reminder_denies_revoked_wrong_or_stale_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    with TestClient(create_application(_settings(tmp_path))) as client:
        authority = _activate(client, monkeypatch)
        enable(client)
        request, callback, due = prepare(client)
        if change == "cancel":
            cancel = {k: v for k, v in request.items() if k != "due_at"}
            cancel["reminder_event_id"] = callback["reminder_event_id"]
            response = client.post(BASE + "/cancel", headers=AUTH, json=cancel)
            assert response.status_code == 200, response.text
        elif change == "host":
            path = (
                client.app.state.runtime.settings.instance.config_dir.parent
                / "openclaw/runtime/openclaw.json"
            )
            value = json.loads(path.read_text())
            value["plugins"]["entries"]["administrateme"]["config"][
                "scheduledSelfReminderBindings"
            ] = []
            path.write_text(json.dumps(value))
        elif change == "domain":
            authority["authorized"] = False
        elif change == "job":
            callback["job_id"] = "wrong-job"
            callback["session_key"] = (
                f"agent:{member_agent_id(A)}:cron:wrong-job:trigger"
            )
        elif change == "agent":
            callback["agent_id"] = "adminme-0000000000000000"
        advance(monkeypatch, due + timedelta(seconds=601 if change == "late" else 1))
        response = client.post(BASE + "/fire", headers=AUTH, json=callback)
        assert response.status_code in {403, 409}, response.text
        assert not client.portal.call(
            _events,
            client.app.state.runtime,
            ["cos.response.attempted", "external.sent"],
        )


def test_new_timed_permission_is_opt_in_and_source_content_cannot_be_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_application(_settings(tmp_path))) as client:
        _activate(client, monkeypatch)
        source = _inbound(
            client, text="Text me a reminder about the weekend plan tomorrow morning."
        )
        body = {
            **source,
            "due_at": (datetime.now(timezone.utc) + timedelta(hours=13)).isoformat(),
        }
        assert (
            client.post(BASE + "/prepare", headers=AUTH, json=body).status_code == 403
        )
        enable(client)
        original = client.post(BASE + "/prepare", headers=AUTH, json=body)
        assert original.status_code == 200, original.text
        conflict = client.post(
            BASE + "/prepare", headers=AUTH, json={**body, "body": "Different text"}
        )
        assert conflict.status_code == 409
        naive = client.post(
            BASE + "/prepare",
            headers=AUTH,
            json={**body, "due_at": "2028-01-01T09:45:00"},
        )
        assert naive.status_code == 422
        callback = {
            "reminder_event_id": original.json()["reminder_event_id"],
            "job_id": JOB,
            "agent_id": member_agent_id(A),
            "session_key": f"agent:{member_agent_id(A)}:cron:{JOB}:trigger",
        }
        assert client.post(BASE + "/fire", json=callback).status_code == 401
