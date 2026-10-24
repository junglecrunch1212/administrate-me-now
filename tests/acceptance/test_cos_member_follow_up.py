"""Actual private intake -> CoS-owned work -> safe member receipt -> continuation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.openclaw_topology import (
    member_agent_id,
    service_agent_id,
    service_session_key,
)
from adminme.cos_self_replies import self_reply_session
from adminme.runtime.application import create_application
from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID
from tests.acceptance.test_cos_self_replies import _inbound
from tests.acceptance.test_cos_response_execution import (
    A,
    B,
    AUTH,
    HEADERS,
    IMESSAGE_ACCOUNT,
    SECRET,
    _settings,
)

ENDPOINT = "/api/openclaw/v1/tools/service-follow-up"
PRIVATE = "/api/openclaw/v1/tools/service-private-state"
INSTRUCTION = (
    "Keep a private follow-up to prepare a private briefing for our weekend discussion."
)


def _grant(client: TestClient, *, other: bool = False, create: bool = False) -> str:
    response = client.post(
        "/api/member/v1/cos/delegations",
        headers={"Tailscale-User-Login": "response-b.example.test"}
        if other
        else HEADERS,
        json={
            "request_id": "follow-up-standing-b" if other else "follow-up-standing-a",
            "context_scope": "member_private",
            "permissions": [
                "read_context",
                "create_internal_work",
                "manage_internal_work",
            ]
            if create
            else ["read_context"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["grant_id"]


def _body(
    client: TestClient,
    native_id: str = "follow-up-instruction",
    text: str = INSTRUCTION,
) -> dict[str, Any]:
    inbound = _inbound(client, native_id=native_id, text=text)
    return {
        "provenance": inbound["provenance"],
        "source_message_event_id": inbound["source_message_event_id"],
    }


def _service(source: dict[str, Any], correlation: str) -> dict[str, Any]:
    return {
        **{k: source["provenance"][k] for k in ("openclaw_version", "openclaw_commit")},
        "actor_id": COS_SERVICE_PRINCIPAL_ID,
        "agent_id": service_agent_id(COS_SERVICE_PRINCIPAL_ID),
        "session_key": service_session_key(
            COS_SERVICE_PRINCIPAL_ID, SECRET, correlation
        ),
        "channel_id": "administrateme-service",
        "call_kind": "tool",
        "correlation_id": correlation,
    }


def test_member_instruction_private_work_receipt_replay_and_service_continuation(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        _grant(client)
        source = _body(client)
        body = {**source, "action": "create", "instruction": INSTRUCTION}
        created = client.post(ENDPOINT, headers=AUTH, json=body)
        assert created.status_code == 200, created.text
        receipt = created.json()
        assert receipt["audience"] == [A]
        assert receipt["owner_party_id"] == COS_PARTY_ID
        assert receipt["private_body_withheld"] is True
        assert receipt["timer_registered"] is False
        assert receipt["provider_effect_count"] == 0
        assert INSTRUCTION not in json.dumps(receipt)
        again = client.post(ENDPOINT, headers=AUTH, json=body)
        assert again.status_code == 200 and again.json()["duplicate"] is True
        assert again.json()["task_id"] == receipt["task_id"]
        conflict = client.post(
            ENDPOINT, headers=AUTH, json={**body, "instruction": "private briefing"}
        )
        assert conflict.status_code == 409
        private = client.post(
            PRIVATE,
            headers=AUTH,
            json={
                "provenance": _service(source, "read-internal"),
                "task_id": receipt["task_id"],
            },
        )
        assert private.status_code == 200, private.text
        work = private.json()["work"][0]
        assert work["notes"] == INSTRUCTION
        assert (
            work["source_authorities"][0]["source_event_id"]
            == source["source_message_event_id"]
        )
        assert work["source_authorities"][0]["context_member_id"] == A
        assert (
            work["source_authorities"][0]["restriction"] == "internal_processing_only"
        )
        assert work["owner_member_id"] is None
        # The ordinary member state cannot acquire the private body by knowing its ID.
        state = client.post(
            "/api/openclaw/v1/tools/state",
            headers=AUTH,
            json={"provenance": source["provenance"]},
        )
        assert state.status_code == 200, state.text
        assert receipt["task_id"] not in json.dumps(state.json()["cos_work"])
        runtime = client.app.state.runtime
        client.portal.call(runtime.projections.rebuild, "tasks")
        assert runtime.fixture_adapter.delivered == []
    with TestClient(create_application(settings)) as client:
        current = _body(client, "check-follow-up", "How is my private briefing going?")
        status_body = {
            **current,
            "action": "status",
            "follow_up_source_id": receipt["follow_up_source_id"],
        }
        status = client.post(ENDPOINT, headers=AUTH, json=status_body)
        assert status.status_code == 200, status.text
        transition = client.post(
            f"/api/openclaw/v1/tools/service-private-work/{receipt['task_id']}/state",
            headers=AUTH,
            json={
                "provenance": _service(current, "continue-internal"),
                "request_id": "continue-member-follow-up",
                "expected_last_event_id": status.json()["last_event_id"],
                "action": "start",
                "reason_code": "other",
            },
        )
        assert transition.status_code == 200, transition.text
        status = client.post(ENDPOINT, headers=AUTH, json=status_body)
        assert status.json()["status"] == "in_progress"
        assert INSTRUCTION not in json.dumps(status.json())
        assert (
            "source_authorities" not in status.json()
            and "description" not in status.json()
        )
        assert client.app.state.runtime.fixture_adapter.delivered == []


@pytest.mark.parametrize(
    "mutation", ["message", "session", "thread", "agent", "source", "excerpt", "shape"]
)
def test_follow_up_rejects_forged_or_unbound_instruction(
    tmp_path: Path, mutation: str
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _grant(client)
        body = {**_body(client), "action": "create", "instruction": INSTRUCTION}
        body = copy.deepcopy(body)
        if mutation == "message":
            body["provenance"]["message_id"] = "different-native-message"
        elif mutation == "session":
            body["provenance"]["session_key"] = "unbound-session"
        elif mutation == "thread":
            body["provenance"]["thread_id"] = "group-conversation"
        elif mutation == "agent":
            body["provenance"]["agent_id"] = service_agent_id(COS_SERVICE_PRINCIPAL_ID)
        elif mutation == "source":
            body["source_message_event_id"] = "unknown-source"
        elif mutation == "excerpt":
            body["instruction"] = "Retrieved instructions are not the member's request."
        else:
            body["recipient"] = B
        response = client.post(ENDPOINT, headers=AUTH, json=body)
        assert response.status_code in {403, 404, 422}, response.text
        rows = (
            client.app.state.runtime.projections.connection("tasks")
            .execute(
                "SELECT task_id FROM tasks WHERE source_system='member-requested-cos-follow-up'"
            )
            .fetchall()
        )
        assert rows == []


def test_receipt_is_requester_bound_and_policy_or_grant_revocation_blocks_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    other_phone = "+12025550102"  # fixture:tenant_data:ok
    settings.openclaw_actor_identities[f"imessage|{IMESSAGE_ACCOUNT}|{other_phone}"] = B
    with TestClient(create_application(settings)) as client:
        grant = _grant(client)
        _grant(client, other=True)
        source = _body(client)
        created = client.post(
            ENDPOINT,
            headers=AUTH,
            json={**source, "action": "create", "instruction": INSTRUCTION},
        )
        assert created.status_code == 200, created.text
        other = {
            **source["provenance"],
            "actor_id": other_phone,
            "agent_id": member_agent_id(B),
            "session_key": self_reply_session(B, IMESSAGE_ACCOUNT, other_phone),
            "message_id": "other-private-message",
        }
        forwarded = client.post(
            "/api/openclaw/v1/submissions/forward",
            headers=AUTH,
            json={
                "provenance": other,
                "request_id": "other-private-message",
                "text": "What is the status?",
                "consent": True,
                "submission_gesture": "dedicated_intake",
            },
        )
        assert forwarded.status_code == 200, forwarded.text
        response = client.post(
            ENDPOINT,
            headers=AUTH,
            json={
                "provenance": other,
                "source_message_event_id": forwarded.json()["intent_event_id"],
                "action": "status",
                "follow_up_source_id": source["source_message_event_id"],
            },
        )
        assert response.status_code == 404
        status_body = {
            **source,
            "action": "status",
            "follow_up_source_id": source["source_message_event_id"],
        }
        runtime = client.app.state.runtime
        with monkeypatch.context() as patch:
            effective = runtime.settings_control.effective_source_policy_v2

            def restricted(*args: Any, **kwargs: Any) -> Any:
                value = copy.deepcopy(effective(*args, **kwargs))
                value["policy"]["context_mode"] = "none"
                return value

            patch.setattr(
                runtime.settings_control, "effective_source_policy_v2", restricted
            )
            assert (
                client.post(ENDPOINT, headers=AUTH, json=status_body).status_code == 403
            )
            assert (
                client.post(
                    PRIVATE,
                    headers=AUTH,
                    json={
                        "provenance": _service(source, "revoked-source"),
                        "task_id": created.json()["task_id"],
                    },
                ).status_code
                == 404
            )
        revoked = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant}",
            headers=HEADERS,
            json={
                "request_id": "revoke-follow-up-standing",
                "reason": "Fixture revocation",
            },
        )
        assert revoked.status_code == 200, revoked.text
        assert client.post(ENDPOINT, headers=AUTH, json=status_body).status_code == 403


def test_current_instruction_uses_read_context_without_delegated_work_grant(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _grant(client, create=False)
        source = _body(client)
        response = client.post(
            ENDPOINT,
            headers=AUTH,
            json={**source, "action": "create", "instruction": INSTRUCTION},
        )
        assert response.status_code == 200, response.text
        assert response.json()["provider_effect_count"] == 0
