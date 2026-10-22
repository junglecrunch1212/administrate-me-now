"""Production private inbound -> standing permission -> guarded native receipt."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.cos_channel_operation import CosChannelActivationV2
from adminme.cos_self_replies import self_reply_binding, self_reply_session
from adminme.events.envelope import EventEnvelope
from adminme.historical_attribution import _cos_response_owner_matches
from adminme.openclaw_topology import member_agent_id
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import create_application
from tests.acceptance.test_cos_response_execution import (
    A,
    B,
    AUTH,
    HEADERS,
    PHONE,
    IMESSAGE_ACCOUNT,
    POLICY,
    _settings,
    _prepare,
    _events,
    _provider_assurance,
)

ENDPOINT = "/api/openclaw/v1/cos-responses"


def _host(runtime: Any, enabled: bool) -> None:
    path = (
        runtime.settings.instance.config_dir.parent / "openclaw/runtime/openclaw.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "plugins": {
                    "entries": {
                        "administrateme": {
                            "enabled": True,
                            "config": {
                                "standingSelfReplyBindings": [
                                    self_reply_binding(A, IMESSAGE_ACCOUNT, PHONE)
                                ]
                                if enabled
                                else []
                            },
                        }
                    }
                }
            }
        )
    )


def _activate(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    runtime = client.app.state.runtime
    _prepare(client, permissions=("read_context",))
    _host(runtime, True)
    binding = self_reply_binding(A, IMESSAGE_ACCOUNT, PHONE)
    now = datetime.now(timezone.utc)
    activation = CosChannelActivationV2(
        activation_id="a" * 32,
        release_commit="b" * 40,
        enabled_at=(now - timedelta(seconds=2)).isoformat(),
        acquisition_sha256="c" * 64,
        allowlist_event_id=client.get(POLICY, headers=HEADERS).json()["event_id"],
        allowlist_revision=1,
        channels=[
            {
                "channel": "imessage",
                "provider_account_sha256": hashlib.sha256(
                    IMESSAGE_ACCOUNT.encode()
                ).hexdigest(),
                "sender_evidence_sha256": "d" * 64,
                "transport_evidence_sha256": "e" * 64,
                "observed_at": (now - timedelta(seconds=3)).isoformat(),
            }
        ],
        backup_sha256="f" * 64,
        rehearsal_sha256="1" * 64,
        owner_authorization_sha256="2" * 64,
        operator_principal_id="operator:fixture-self-reply",
        observation_window_started_at=(now - timedelta(days=1)).isoformat(),
        self_reply_bindings=[binding],
    )
    event = EventEnvelope(
        event_at_ms=int(now.timestamp() * 1000),
        tenant_id=runtime.settings.instance.tenant_id,
        type="cos.channels.activated",
        schema_version=2,
        occurred_at=now.isoformat(),
        source_adapter="cos-channel-operation",
        source_account_id="system",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="privileged",
        actor_identity=activation.operator_principal_id,
        payload=activation.model_dump(mode="json"),
    )
    activation_id = client.portal.call(runtime.events.write, event)
    authority = {
        "authorized": True,
        "event_id": activation_id,
        "self_reply_bindings": [binding],
    }

    async def active(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return authority

    # Native operational proof is tested separately with real receipt replay.
    # No provider is contacted by this production API/guard composition fixture.
    monkeypatch.setattr(
        "adminme.products.openclaw.cos_self_replies.runtime_live_authorization", active
    )
    monkeypatch.setattr(
        "adminme.products.openclaw.cos_responses.require_cos_response_activation",
        active,
    )
    client.portal.call(
        runtime.observation.disable, A, "isolated standing reply fixture"
    )
    return authority


def _inbound(
    client: TestClient, native_id: str = "native-standing-message"
) -> dict[str, Any]:
    provenance = {
        "actor_id": PHONE,
        "agent_id": member_agent_id(A),
        "account_id": IMESSAGE_ACCOUNT,
        "session_key": self_reply_session(A, IMESSAGE_ACCOUNT, PHONE),
        "channel_id": "imessage",
        "message_id": native_id,
        "correlation_id": native_id,
        "call_kind": "bridge",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }
    source = client.post(
        "/api/openclaw/v1/submissions/forward",
        headers=AUTH,
        json={
            "provenance": provenance,
            "request_id": native_id,
            "text": "Please explain your capabilities.",
            "consent": True,
            "submission_gesture": "dedicated_intake",
        },
    )
    assert source.status_code == 200, source.text
    return {
        "provenance": provenance,
        "source_message_event_id": source.json()["intent_event_id"],
        "host_binding": self_reply_binding(A, IMESSAGE_ACCOUNT, PHONE),
        "body": "I can answer you directly. Here is a link: https://example.invalid/a?x=1&y=2",
        "reply_index": 0,
    }


def _send(
    client: TestClient, body: dict[str, Any], status: str = "sent"
) -> dict[str, Any]:
    approved = client.post(ENDPOINT + "/self-reply", headers=AUTH, json=body)
    assert approved.status_code == 200, approved.text
    dispatch = approved.json()["transport_dispatch"]
    claim = client.post(
        ENDPOINT + "/claim",
        headers=AUTH,
        json={
            "account_id": dispatch["account_id"],
            "recipient": dispatch["recipient"],
            "body": dispatch["body"],
        },
    )
    assert claim.status_code == 200, claim.text
    terminal = client.post(
        ENDPOINT + "/terminal",
        headers=AUTH,
        json={"claim_id": claim.json()["claim_id"], "status": status},
    )
    assert terminal.status_code == 200, terminal.text
    assert terminal.json()["status"] == status
    return terminal.json()


@pytest.mark.parametrize("status", ["sent", "unknown"])
def test_standing_reply_is_delivered_once_without_per_message_prompt_and_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        _activate(client, monkeypatch)
        body = _inbound(client)
        terminal = _send(client, body, status)
        repeated = client.post(ENDPOINT + "/self-reply", headers=AUTH, json=body)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == terminal
        conflict = client.post(
            ENDPOINT + "/self-reply",
            headers=AUTH,
            json={**body, "body": "Different answer."},
        )
        assert conflict.status_code == 409
        assert (
            len(
                client.portal.call(
                    _events, client.app.state.runtime, ["cos.response.attempted"]
                )
            )
            == 1
        )
        attempts = client.portal.call(
            _events, client.app.state.runtime, ["cos.response.attempted"]
        )
        assert client.portal.call(
            _cos_response_owner_matches,
            client.app.state.runtime.event_log,
            attempts[0]["event_id"],
        )
        if status == "sent":
            assurance = client.portal.call(
                _provider_assurance, client.app.state.runtime
            )
            assert assurance["state"] == "healthy", assurance
    with TestClient(create_application(settings)) as client:
        repeated = client.post(ENDPOINT + "/self-reply", headers=AUTH, json=body)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == terminal
        if status == "sent":
            fresh = _inbound(client, "native-after-restart")
            _send(client, fresh)


@pytest.mark.parametrize("revoke", ["host", "domain", "recipient", "delegation"])
def test_standing_reply_rechecks_revocation_at_native_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revoke: str
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        authority = _activate(client, monkeypatch)
        body = _inbound(client)
        approved = client.post(ENDPOINT + "/self-reply", headers=AUTH, json=body)
        assert approved.status_code == 200, approved.text
        dispatch = approved.json()["transport_dispatch"]
        if revoke == "host":
            _host(client.app.state.runtime, False)
        elif revoke == "domain":
            authority["authorized"] = False
        elif revoke == "delegation":
            grants = client.get(
                "/api/member/v1/cos/delegations", headers=HEADERS
            ).json()
            revoked = client.request(
                "DELETE",
                f"/api/member/v1/cos/delegations/{grants['items'][0]['grant_id']}",
                headers=HEADERS,
                json={"request_id": "revoke-standing-context"},
            )
            assert revoked.status_code == 200, revoked.text
        else:
            changed = client.put(
                POLICY,
                headers=HEADERS,
                json={
                    "request_id": "remove-self-recipient",
                    "expected_revision": 1,
                    "entries": [],
                },
            )
            assert changed.status_code == 202, changed.text
        claim = client.post(
            ENDPOINT + "/claim",
            headers=AUTH,
            json={
                "account_id": IMESSAGE_ACCOUNT,
                "recipient": PHONE,
                "body": dispatch["body"],
            },
        )
        assert claim.status_code == 403, claim.text
        assert not client.portal.call(
            _events, client.app.state.runtime, ["external.sent"]
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "agent",
        "session",
        "actor",
        "account",
        "thread",
        "source",
        "host",
        "no-grant",
        "tool",
    ],
)
def test_self_reply_rejects_wrong_or_ungranted_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        authority = _activate(client, monkeypatch)
        body = _inbound(client)
        changes = {
            "agent": ("agent_id", member_agent_id(B)),
            "session": ("session_key", "shared-session"),
            "actor": ("actor_id", "+12025550102"),
            "account": ("account_id", "other-account"),  # fixture:tenant_data:ok
            "thread": ("thread_id", "group-1"),
            "tool": ("call_kind", "tool"),
        }
        if mutation in changes:
            key, value = changes[mutation]
            body["provenance"][key] = value
        elif mutation == "source":
            body["source_message_event_id"] = "unknown-source"
        elif mutation == "host":
            body["host_binding"] = "f" * 64
        else:
            authority["self_reply_bindings"] = []
        result = client.post(ENDPOINT + "/self-reply", headers=AUTH, json=body)
        assert result.status_code == 403, result.text
        assert not client.portal.call(
            _events,
            client.app.state.runtime,
            ["cos.response.approved", "external.sent"],
        )


def test_standing_reply_observation_and_ordered_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _activate(client, monkeypatch)
        body = _inbound(client)
        skipped = client.post(
            ENDPOINT + "/self-reply", headers=AUTH, json={**body, "reply_index": 1}
        )
        assert skipped.status_code == 409
        _send(client, body)
        _send(client, {**body, "reply_index": 1, "body": "Second part."})
        runtime = client.app.state.runtime
        client.portal.call(
            runtime.observation.enable, A, "isolated observation regression"
        )
        suppressed = client.post(
            ENDPOINT + "/self-reply",
            headers=AUTH,
            json=_inbound(client, "observed-input"),
        )
        assert suppressed.status_code == 200, suppressed.text
        assert suppressed.json()["status"] == "suppressed"
        assert "transport_dispatch" not in suppressed.json()


def test_standing_reply_orphan_attempt_is_not_retried_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        _activate(client, monkeypatch)
        body = _inbound(client)
        attempted = client.post(ENDPOINT + "/self-reply", headers=AUTH, json=body)
        assert attempted.status_code == 200
        dispatch = attempted.json()["transport_dispatch"]
        claim = client.post(
            ENDPOINT + "/claim",
            headers=AUTH,
            json={
                "account_id": dispatch["account_id"],
                "recipient": dispatch["recipient"],
                "body": dispatch["body"],
            },
        )
        assert claim.status_code == 200
    with TestClient(create_application(settings)) as client:
        replay = client.post(ENDPOINT + "/self-reply", headers=AUTH, json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "unknown"
        assert "transport_dispatch" not in replay.json()
        attempts = client.portal.call(
            _events, client.app.state.runtime, ["cos.response.attempted"]
        )
        assert len(attempts) == 1
