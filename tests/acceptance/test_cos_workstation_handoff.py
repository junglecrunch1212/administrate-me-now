"""Real encrypted application: current instruction -> CoS note -> read-back."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.products.openclaw.workstation_work import _content_hash
from adminme.runtime.application import create_application
from tests.acceptance.test_cos_member_follow_up import _body, _grant, _service
from tests.acceptance.test_cos_response_execution import AUTH, _settings

BASE = "/api/openclaw/v1/tools/workstation-work"
CREATE = "/api/openclaw/v1/tools/service-private-work"
READ = "/api/openclaw/v1/tools/service-private-state"
INSTRUCTION = "Draft a private scratchpad with three ways to organize the garage."
NONCE = "8" * 32


def _post(
    client: TestClient, path: str, body: dict[str, Any], status: int = 200
) -> dict[str, Any]:
    response = client.post(path, headers=AUTH, json=body)
    assert response.status_code == status, response.text
    return response.json()


def _admit(
    client: TestClient, source: dict[str, Any], nonce: str = NONCE
) -> dict[str, Any]:
    return _post(
        client,
        BASE + "/admit",
        {**source, "action": "create", "instruction": INSTRUCTION, "run_nonce": nonce},
    )


def test_authenticated_instruction_worker_note_verified_return_and_restart(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        _grant(client)
        source = _body(client, text=INSTRUCTION)
        admitted = _admit(client, source)
        assert admitted["mode"] == "create"
        assert admitted["instruction"] == INSTRUCTION
        service = _service(source, NONCE)
        policy = _post(client, BASE + "/policy", {"provenance": service})
        assert policy["bound"] is True
        assert policy["artifact_task_id"] == admitted["artifact_task_id"]
        result_body = {
            **source,
            "follow_up_source_id": admitted["follow_up_source_id"],
            "run_nonce": NONCE,
        }
        assert (
            _post(client, BASE + "/result", result_body)["status"]
            == "pending_or_unknown"
        )
        note_body = {
            "provenance": service,
            "request_id": admitted["artifact_request_id"],
            "record_kind": "scratchpad",
            "title": "Garage organization",
            "description": "Sort by use, label zones, and leave a return basket.",
        }
        made = _post(client, CREATE, note_body, 201)
        assert made["task_id"] == admitted["artifact_task_id"]
        assert (
            made["work"]["source_authorities"][0]["source_event_id"]
            == source["source_message_event_id"]
        )
        assert _post(client, CREATE, note_body, 201)["duplicate"] is True
        read = _post(client, READ, {"provenance": service, "task_id": made["task_id"]})[
            "work"
        ][0]
        proof = {
            "provenance": service,
            "task_id": read["task_id"],
            "last_event_id": read["last_event_id"],
            "content_sha256": _content_hash(read),
        }
        _post(client, BASE + "/read-back", {**proof, "content_sha256": "0" * 64}, 409)
        _post(client, BASE + "/read-back", proof)
        completed = _post(client, BASE + "/result", result_body)
        assert completed["status"] == "verified"
        assert completed["content"] == note_body["description"]
        assert completed["provider_effect_count"] == 0
        assert (
            completed["resource_owner_principal_id"]
            == "service-principal:chief-of-staff"
        )
        assert _admit(client, source)["mode"] == "read"
        assert client.app.state.runtime.fixture_adapter.delivered == []
    # Retained source/audit bindings survive reconstruction; an expired operator
    # transcript is unnecessary, and a new status does not create a second note.
    with TestClient(create_application(settings)) as client:
        current = _body(client, "check-workstation", "Read my garage scratchpad back.")
        status = _post(
            client,
            BASE + "/admit",
            {
                **current,
                "action": "status",
                "follow_up_source_id": admitted["follow_up_source_id"],
                "run_nonce": "9" * 32,
            },
        )
        assert status["mode"] == "read"
        assert status["artifact_task_id"] == admitted["artifact_task_id"]
        service = _service(current, "9" * 32)
        assert _post(client, BASE + "/policy", {"provenance": service})["bound"] is True
        read = _post(
            client,
            READ,
            {"provenance": service, "task_id": admitted["artifact_task_id"]},
        )["work"][0]
        _post(
            client,
            BASE + "/read-back",
            {
                "provenance": service,
                "task_id": read["task_id"],
                "last_event_id": read["last_event_id"],
                "content_sha256": _content_hash(read),
            },
        )
        completed = _post(
            client,
            BASE + "/result",
            {
                **current,
                "follow_up_source_id": admitted["follow_up_source_id"],
                "run_nonce": "9" * 32,
            },
        )
        assert completed["status"] == "verified"
        assert completed["content"] == note_body["description"]


def test_fake_identity_unbound_worker_and_unproved_result_fail_closed(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _grant(client)
        source = _body(client, text=INSTRUCTION)
        admitted = _admit(client, source)
        service = _service(source, NONCE)
        _post(client, BASE + "/policy", {"provenance": source["provenance"]}, 403)
        unbound = _service(source, "a" * 32)
        assert _post(client, BASE + "/policy", {"provenance": unbound}) == {
            "bound": False
        }
        _post(
            client,
            BASE + "/read-back",
            {
                "provenance": service,
                "task_id": "unrelated-note",
                "last_event_id": "unknown",
                "content_sha256": "0" * 64,
            },
            403,
        )
        _post(
            client,
            BASE + "/result",
            {
                **source,
                "follow_up_source_id": admitted["follow_up_source_id"],
                "run_nonce": "a" * 32,
            },
            403,
        )
        forged = {
            **source,
            "provenance": {**source["provenance"], "actor_id": "+15555550199"},
        }
        _post(
            client,
            BASE + "/admit",
            {
                **forged,
                "action": "create",
                "instruction": INSTRUCTION,
                "run_nonce": NONCE,
            },
            403,
        )


@pytest.mark.parametrize("channel", ["gmail", "administrateme-portal"])
def test_real_gmail_and_dashboard_chat_private_draft_and_cross_channel_readback(
    tmp_path: Path, channel: str
) -> None:
    from adminme.openclaw_topology import service_session_key
    from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
    from tests.acceptance.test_authenticated_conversation_channels import (
        gmail_instruction,
        portal_instruction,
        settings,
    )
    from tests.acceptance.test_forwarded_channel_intake import AUTH as CHANNEL_AUTH
    from tests.acceptance.test_native_apple_reminder import SECRET, _grant as grant

    with TestClient(create_application(settings(tmp_path / channel))) as client:
        grant(client)
        grant(client, other=True)
        intake = gmail_instruction if channel == "gmail" else portal_instruction
        source = intake(client, "workstation-channel-create", text=INSTRUCTION)

        def send(path: str, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
            response = client.post(path, headers=CHANNEL_AUTH, json=body)
            assert response.status_code == status, response.text
            return response.json()

        admitted = send(
            BASE + "/admit",
            {
                **source,
                "action": "create",
                "instruction": INSTRUCTION,
                "run_nonce": NONCE,
            },
        )
        service = {
            **_service(source, NONCE),
            "session_key": service_session_key(COS_SERVICE_PRINCIPAL_ID, SECRET, NONCE),
        }
        made = send(
            CREATE,
            {
                "provenance": service,
                "request_id": admitted["artifact_request_id"],
                "title": "Garage draft",
                "description": "Keep equipment in labeled zones.",
                "record_kind": "scratchpad",
            },
            201,
        )
        note = send(READ, {"provenance": service, "task_id": made["task_id"]})["work"][
            0
        ]
        send(
            BASE + "/read-back",
            {
                "provenance": service,
                "task_id": note["task_id"],
                "last_event_id": note["last_event_id"],
                "content_sha256": _content_hash(note),
            },
        )
        result = send(
            BASE + "/result",
            {
                **source,
                "follow_up_source_id": admitted["follow_up_source_id"],
                "run_nonce": NONCE,
            },
        )
        assert result["status"] == "verified"
        assert result["content"] == "Keep equipment in labeled zones."
        assert source["provenance"]["channel_id"] == channel
        other = portal_instruction(
            client, "other-member-draft-status", other=True, text="Read that draft."
        )
        send(
            BASE + "/admit",
            {
                **other,
                "action": "status",
                "follow_up_source_id": admitted["follow_up_source_id"],
                "run_nonce": "b" * 32,
            },
            404,
        )
        other_channel = portal_instruction if channel == "gmail" else gmail_instruction
        current = other_channel(
            client, "cross-channel-draft-status", text="Read my garage draft back."
        )
        continued = send(
            BASE + "/admit",
            {
                **current,
                "action": "status",
                "follow_up_source_id": admitted["follow_up_source_id"],
                "run_nonce": "c" * 32,
            },
        )
        assert continued["mode"] == "read"
        assert continued["artifact_task_id"] == admitted["artifact_task_id"]
        # The current domain permission remains necessary, including after the
        # native worker has created its artifact; retained history is not a grant.
        from tests.acceptance.test_forwarded_channel_intake import MEMBER_HEADERS

        source_grant = note["source_authorities"][0]["authority_grant_id"]
        revoke = client.request(
            "DELETE",
            "/api/member/v1/cos/delegations/" + source_grant,
            headers=MEMBER_HEADERS,
            json={
                "request_id": "revoke-workstation-source",
                "reason": "Fixture revocation",
            },
        )
        assert revoke.status_code == 200, revoke.text
        denied = client.post(
            BASE + "/read-back",
            headers=CHANNEL_AUTH,
            json={
                "provenance": service,
                "task_id": note["task_id"],
                "last_event_id": note["last_event_id"],
                "content_sha256": _content_hash(note),
            },
        )
        assert denied.status_code == 403
