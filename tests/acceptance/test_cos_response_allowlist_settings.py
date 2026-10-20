"""CoS recipient Settings through the production application and encrypted log."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.cos_response_policy import POLICY_EVENT_TYPE, recipient_decision
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
A = "member-allowlist-a"  # fixture:tenant_data:ok
B = "member-allowlist-b"  # fixture:tenant_data:ok
CHILD = "member-allowlist-child"  # fixture:tenant_data:ok
A_HEADERS = {"Tailscale-User-Login": "allowlist-a.example.test"}
B_HEADERS = {"Tailscale-User-Login": "allowlist-b.example.test"}
CHILD_HEADERS = {"Tailscale-User-Login": "allowlist-child.example.test"}
PATH = "/api/member/v1/settings/cos-replies"


def _settings(instance: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance,
        encryption_key=b"r" * 32,
        repository_root=REPO_ROOT,
        member_identities={
            "allowlist-a.example.test": A,
            "allowlist-b.example.test": B,
            "allowlist-child.example.test": CHILD,
        },
        member_roles={A: "principal", B: "principal", CHILD: "child"},
    )


async def _events(runtime: Any, types: list[str]) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since(types=types)]


def test_principal_settings_isolation_replay_revocation_and_zero_effects(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    entries = [
        {"member_id": A, "channel": "email", "address": "member-a@EXAMPLE.TEST"},
        {"member_id": B, "channel": "email", "address": "member-b@example.test"},
        {
            "member_id": A,
            "channel": "imessage",
            "address": "+12025550101",  # fixture:tenant_data:ok
        },
    ]
    command = {
        "expected_revision": 0,
        "request_id": "allowlist-initial-request",
        "entries": entries,
    }
    app = create_application(settings)
    with TestClient(app) as client:
        initial = client.get(PATH, headers=A_HEADERS)
        assert initial.status_code == 200, initial.text
        assert initial.json()["entries"] == []
        assert initial.json()["revision"] == 0
        response = client.put(PATH, headers=A_HEADERS, json=command)
        assert response.status_code == 202, response.text
        assert response.json()["provider_effects"] == 0
        assert response.json()["grants_provider_authority"] is False
        first_event_id = response.json()["event_id"]
        repeated = client.put(PATH, headers=A_HEADERS, json=command)
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["idempotent"] is True
        assert repeated.json()["event_id"] == first_event_id
        conflicted = client.put(
            PATH, headers=A_HEADERS, json={**command, "entries": []}
        )
        assert conflicted.status_code == 409
        stale = client.put(
            PATH,
            headers=B_HEADERS,
            json={**command, "request_id": "allowlist-stale-request"},
        )
        assert stale.status_code == 409

        invalid = client.put(
            PATH,
            headers=A_HEADERS,
            json={
                "expected_revision": 1,
                "request_id": "allowlist-unknown-member",
                "entries": [
                    {
                        "member_id": "not-a-household-member",
                        "channel": "email",
                        "address": "outsider@example.test",
                    }
                ],
            },
        )
        assert invalid.status_code == 422
        for headers in (
            CHILD_HEADERS,
            {**A_HEADERS, "X-AdminMe-View-Member": B},
        ):
            assert client.get(PATH, headers=headers).status_code == 403
            assert client.put(PATH, headers=headers, json=command).status_code == 403
            viewed = client.get("/api/member/v1/surfaces/settings", headers=headers)
            if headers == CHILD_HEADERS:
                assert viewed.status_code == 404
                continue
            assert viewed.status_code == 200, viewed.text
            assert viewed.json()["control"]["cos_replies"] == {
                "visible": False,
                "can_mutate": False,
            }
            assert "member-a@example.test" not in viewed.text

        surface = client.get("/api/member/v1/surfaces/settings", headers=B_HEADERS)
        assert surface.status_code == 200, surface.text
        policy = surface.json()["control"]["cos_replies"]
        assert policy["visible"] is True
        assert policy["revision"] == 1
        assert recipient_decision(
            policy, channel="email", recipients=["member-a@example.test"], member_id=A
        )["allowed"]
        assert not recipient_decision(
            policy, channel="email", recipients=["member-b@example.test"], member_id=A
        )["allowed"]
        runtime = app.state.runtime
        saved = client.portal.call(_events, runtime, [POLICY_EVENT_TYPE])
        assert len(saved) == 1
        assert saved[0]["sensitivity"] == "sensitive"
        assert saved[0]["owner_scope"] == f"private:{A}"
        assert saved[0]["actor_identity"] == A
        assert saved[0]["payload"]["grants_provider_authority"] is False
        journal = runtime.settings_control.path
        if journal.exists():
            assert "member-a@example.test" not in journal.read_text()
            assert "+12025550101" not in journal.read_text()  # fixture:tenant_data:ok
        # The ordinary asset delivers the Settings editor without an account in code.
        asset = client.get("/member-assets/member.js")
        assert asset.status_code == 200
        assert '["cos-replies", "CoS replies"]' in asset.text
        assert "Revoke all replies" in asset.text

    restarted = create_application(_settings(instance))
    with TestClient(restarted) as client:
        policy = client.get(PATH, headers=A_HEADERS).json()
        assert policy["revision"] == 1
        assert policy["event_id"] == first_event_id
        assert len(policy["entries"]) == 3
        revoke = client.put(
            PATH,
            headers=B_HEADERS,
            json={
                "expected_revision": 1,
                "request_id": "allowlist-revoke-all",
                "entries": [],
            },
        )
        assert revoke.status_code == 202, revoke.text
        assert revoke.json()["revision"] == 2
        # Retrying the old command is safe and cannot resurrect revoked recipients.
        assert client.put(PATH, headers=A_HEADERS, json=command).status_code == 202
        current = client.get(PATH, headers=A_HEADERS).json()
        assert current["revision"] == 2
        assert current["entries"] == []
        assert not recipient_decision(
            current,
            channel="email",
            recipients=["member-a@example.test"],
            member_id=A,
        )["allowed"]
        effects = client.portal.call(
            _events,
            restarted.state.runtime,
            [
                "external_sync.write_requested",
                "external_sync.write_sent",
                "external_sync.write_completed",
                "provider.command.requested",
            ],
        )
        assert effects == []
