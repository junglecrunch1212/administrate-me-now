"""Production reminder intent, restart, exact timer, guarded send and revocation."""

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.cos_response_assurance import AUDIT_TYPES, cos_provider_effect_counts
from adminme.cos_reminder_work import CONSUMER, reminder_work_id
from adminme.cos_self_replies import self_reply_session
from adminme.events.envelope import EventEnvelope
from adminme.historical_attribution import _cos_response_owner_matches
from adminme.openclaw_topology import member_agent_id
from adminme.runtime.application import create_application
from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID
from tests.acceptance.test_cos_self_replies import _activate, _inbound, _send, ENDPOINT
from tests.acceptance.test_cos_response_execution import (
    A,
    B,
    AUTH,
    HEADERS,
    IMESSAGE_ACCOUNT,
    _settings,
    _events,
    _provider_assurance,
)

BASE = "/api/openclaw/v1/cos-reminders"
JOB = "isolated-reminder-timer"


def receipt(
    client: TestClient, request: dict[str, Any], reminder_id: str
) -> dict[str, Any]:
    result = client.post(
        BASE + "/status",
        headers=AUTH,
        json={
            **{key: value for key, value in request.items() if key != "due_at"},
            "reminder_event_id": reminder_id,
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def work(client: TestClient, readback: dict[str, Any]) -> dict[str, Any]:
    runtime = client.app.state.runtime
    event_id = (
        readback["terminal_event_id"]
        or readback["cancellation_event_id"]
        or readback["binding_event_id"]
        or readback["reminder_event_id"]
    )
    client.portal.call(runtime.wait_for_subscriber, CONSUMER, event_id)
    row = (
        runtime.projections.connection("tasks")
        .execute(
            "SELECT * FROM tasks WHERE tenant_id=? AND task_id=?",
            (runtime.settings.instance.tenant_id, readback["task_id"]),
        )
        .fetchone()
    )
    assert row is not None
    return dict(row)


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
    monkeypatch.setattr("adminme.events.envelope.datetime", Clock)


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
        initial_receipt = receipt(client, request, callback["reminder_event_id"])
        initial_work = work(client, initial_receipt)
        assert initial_receipt["status"] == "waiting"
        assert initial_work["status"] == "deferred"
        assert (
            initial_work["owner_scope"] == f"service-private:{COS_SERVICE_PRINCIPAL_ID}"
        )
        assert initial_work["owner_member_id"] is None
        assert initial_work["owner_party_id"] == COS_PARTY_ID
        assert initial_work["created_by"] == COS_SERVICE_PRINCIPAL_ID
        assert request["body"] not in json.dumps(initial_work)
        assert (
            json.loads(initial_work["source_authorities_json"])[0]["source_event_id"]
            == callback["reminder_event_id"]
        )
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
            grant = {
                key: value
                for key, value in grant.items()
                if key in EventEnvelope.model_fields
                and key not in {"event_id", "recorded_at"}
            }
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
        terminal_receipt = receipt(client, request, callback["reminder_event_id"])
        terminal_work = work(client, terminal_receipt)
        assert terminal_receipt["status"] == terminal_status
        assert terminal_receipt["automatic_retry_allowed"] is False
        assert terminal_work["status"] == (
            "done" if terminal_status == "sent" else "waiting_on"
        )
        assert terminal_work["completed_by"] is None
        assert terminal_work["completed_by_party_id"] == (
            COS_PARTY_ID if terminal_status == "sent" else None
        )
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
        recovered = work(
            client, receipt(client, request, callback["reminder_event_id"])
        )
        assert recovered["status"] == terminal_work["status"]
        created = client.portal.call(
            _events, client.app.state.runtime, ["task.created"]
        )
        assert (
            len(
                [
                    event
                    for event in created
                    if event["payload"]["task_id"]
                    == reminder_work_id(callback["reminder_event_id"])
                ]
            )
            == 1
        )
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
            assert (
                work(client, receipt(client, request, callback["reminder_event_id"]))[
                    "status"
                ]
                == "cancelled"
            )
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


def test_member_reminder_receipt_is_exact_scoped_and_survives_send_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    other_actor = "+12025550102"  # fixture:tenant_data:ok
    settings.openclaw_actor_identities[f"imessage|{IMESSAGE_ACCOUNT}|{other_actor}"] = B
    with TestClient(create_application(settings)) as client:
        _activate(client, monkeypatch)
        enable(client)
        request, callback, _ = prepare(client)
        result = receipt(client, request, callback["reminder_event_id"])
        recorded = work(client, result)
        other_grant = client.post(
            "/api/member/v1/cos/delegations",
            headers={"Tailscale-User-Login": "response-b.example.test"},
            json={
                "request_id": "other-receipt-read",
                "context_scope": "member_private",
                "permissions": ["read_context"],
            },
        )
        assert other_grant.status_code in {200, 201}, other_grant.text
        other = {
            **{key: value for key, value in request.items() if key != "due_at"},
            "reminder_event_id": callback["reminder_event_id"],
            "provenance": {
                **request["provenance"],
                "actor_id": other_actor,
                "agent_id": member_agent_id(B),
                "session_key": self_reply_session(B, IMESSAGE_ACCOUNT, other_actor),
            },
        }
        assert (
            client.post(BASE + "/status", headers=AUTH, json=other).status_code == 404
        )
        assert (
            client.post(
                "/api/openclaw/v1/tools/service-private-state",
                headers=AUTH,
                json={
                    "provenance": request["provenance"],
                    "task_id": result["task_id"],
                },
            ).status_code
            == 403
        )
        assert set(result) == {
            "reminder_event_id",
            "task_id",
            "status",
            "due_at",
            "job_id",
            "source_message_event_id",
            "binding_event_id",
            "cancellation_event_id",
            "approval_event_id",
            "terminal_event_id",
            "automatic_retry_allowed",
        }
        path = settings.instance.config_dir.parent / "openclaw/runtime/openclaw.json"
        config = json.loads(path.read_text())
        config["plugins"]["entries"]["administrateme"]["config"][
            "scheduledSelfReminderBindings"
        ] = []
        path.write_text(json.dumps(config))
        assert (
            receipt(client, request, callback["reminder_event_id"])["status"]
            == "waiting"
        )
        grant_id = json.loads(recorded["source_authorities_json"])[0][
            "authority_grant_id"
        ]
        revoked = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant_id}",
            headers=HEADERS,
            json={"request_id": "reminder-grant-revoke", "reason": "fixture"},
        )
        assert revoked.status_code == 200, revoked.text
        denied = client.post(
            BASE + "/status",
            headers=AUTH,
            json={
                **{key: value for key, value in request.items() if key != "due_at"},
                "reminder_event_id": callback["reminder_event_id"],
            },
        )
        assert denied.status_code == 403
        assert (
            client.portal.call(
                client.app.state.runtime.event_log.get, callback["reminder_event_id"]
            )
            is not None
        )
