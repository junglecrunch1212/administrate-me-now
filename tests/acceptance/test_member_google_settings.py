"""Self-service Google consent through the real authenticated member application.

Only Google, Keychain publication and the deployment committer are synthetic.
The routes, encrypted handoffs/receipts, scope guards, runtime rebind, adapters,
projections and context policy execute through the production composition root.
Behavioral execution belongs to disposable hosted CI, never a household host.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from adminme.adapters.credentials import credential_member_digest
from adminme.adapters.loader import load_builtin_adapters
from adminme.google_oauth_transport import GoogleOAuthGrant
from adminme.member_google import (
    COMPLETE_EVENT,
    EVENT,
    GoogleHandoffs,
    canonical,
    owned_accounts,
)
from adminme.openclaw_topology import service_agent_id, service_session_key
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import create_application
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap.cos_channel_activation import prepare_configuration
from tests.acceptance.test_forwarded_channel_intake import (
    AUTH,
    MEMBER_A,
    MEMBER_B,
    MEMBER_HEADERS,
    OTHER_HEADERS,
    ROOT,
    SECRET,
    TEXT,
    _events,
    _settings,
)
from tests.acceptance.test_member_google_connections import (
    ReadOnlyGoogle,
    google_configuration,
)

BASE = "/api/member/v1/settings/google-connections"
EMAIL = "new-member@example.invalid"


def services(
    *, gmail: str = "off", calendar: str = "read", tasks: str = "off"
) -> dict[str, Any]:
    return {
        name: {"read": mode != "off", "write": mode == "write"}
        for name, mode in (("gmail", gmail), ("calendar", calendar), ("tasks", tasks))
    }


class GoogleFixture(ReadOnlyGoogle):
    def calendarList(self) -> GoogleFixture:
        return self

    def messages(self) -> GoogleFixture:
        return self

    def send(self, **_kwargs: Any) -> None:
        raise AssertionError("Enrollment and permission changes must never send")

    def insert(self, **_kwargs: Any) -> None:
        raise AssertionError("Enrollment must never create provider records")

    def update(self, **_kwargs: Any) -> None:
        raise AssertionError("Enrollment must never update provider records")

    def delete(self, **_kwargs: Any) -> None:
        raise AssertionError("Enrollment must never delete provider records")

    def modify(self, **_kwargs: Any) -> None:
        raise AssertionError("Enrollment must never modify provider records")


class OAuthFixture:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.exchanged: list[dict[str, Any]] = []
        self.documents: dict[str, dict[str, Any]] = {}
        self.fail: str | None = None
        self.provider_unavailable = False

    def start(self, **values: Any) -> str:
        self.started.append(values)
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {"state": values["state"]}
        )

    def exchange(self, **values: Any) -> GoogleOAuthGrant:
        self.exchanged.append(values)
        if self.fail:
            raise RuntimeError(self.fail)
        reference = "keychain://fixture/" + values["secret_name"]
        self.documents[reference] = {
            "client_id": "fixture-client",
            "client_secret": "fixture-secret",
            "refresh_token": "fixture-refresh",
            "token_uri": "https://oauth2.invalid/token",
            "owner_member_digest": credential_member_digest(values["member_id"]),
            "provider_account_id": values["expected_account_id"],
            "scopes": values["scopes"],
        }
        return GoogleOAuthGrant(
            reference,
            values["expected_account_id"],
            "fixture-subject",
            tuple(values["scopes"]),
        )


class CommitterFixture:
    def __init__(self, path: Path) -> None:
        self.index_path = path
        self.readiness_path = path.with_name("deployment-readiness.json")
        self.pending = False
        self.calls: list[dict[str, Any]] = []
        self.failure: str | None = None

    def commit(
        self, *, previous_payload: bytes, next_payload: bytes, authority: dict[str, Any]
    ) -> dict[str, Any]:
        assert self.index_path.read_bytes() == previous_payload
        assert authority["actor_member_id"] == authority["member_id"]
        self.calls.append(copy.deepcopy(authority))
        if self.failure == "before":
            raise RuntimeError("fixture publication failed before durable commit")
        self.index_path.write_bytes(next_payload)
        evidence = {
            "deployment_binding": {
                "release_commit": "a" * 40,
                "configuration_fingerprint": "b" * 64,
            }
        }
        self.readiness_path.write_bytes(canonical(evidence))
        if self.failure == "after":
            raise RuntimeError("fixture lost return after durable commit")
        return evidence


class Environment:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.path = tmp_path / "instance"
        base = _settings(self.path)
        requested = google_configuration(tmp_path / "provider-configuration")
        config = prepare_configuration(requested, requested, enable_live=False)
        self.oauth = OAuthFixture()
        self.committer = CommitterFixture(base.instance.config_dir / "adapters.json")
        base.instance.config_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.committer.index_path.write_bytes(canonical(config) + b"\n")
        self.committer.index_path.chmod(0o600)
        self.settings = replace(
            base,
            adapter_config=config,
            google_oauth=self.oauth,
            google_config_committer=self.committer,
        )
        self.provider_calls: list[tuple[str, tuple[str, ...]]] = []
        for adapter in load_builtin_adapters(config, repository_root=ROOT):
            module = sys.modules[type(adapter).__module__]
            monkeypatch.setattr(
                module,
                "_default_credentials_resolver",
                lambda ref: self.oauth.documents.get(ref, {}),
            )

            def service(
                _credentials: Any, scopes: list[str], subject: str
            ) -> GoogleFixture:
                self.provider_calls.append((subject, tuple(scopes)))
                if self.oauth.provider_unavailable and subject == EMAIL:
                    raise RuntimeError("fixture Google temporarily unavailable")
                return GoogleFixture(subject)

            monkeypatch.setattr(module, "_default_service_factory", service)
            monkeypatch.setattr(type(adapter), "POLL_INTERVAL_S", 3600)

    @contextmanager
    def client(self) -> Iterator[TestClient]:
        settings = replace(
            self.settings,
            adapter_config=json.loads(self.committer.index_path.read_bytes()),
        )
        with TestClient(create_application(settings)) as client:
            yield client

    def handoffs(self, runtime: Any) -> GoogleHandoffs:
        return GoogleHandoffs(
            runtime.settings.instance.config_dir / "member-google-handoffs.enc",
            runtime.settings.encryption_key,
        )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Environment:
    return Environment(tmp_path, monkeypatch)


def request_body(**overrides: Any) -> dict[str, Any]:
    return {
        "request_id": str(uuid4()),
        "expected_account_id": EMAIL,
        "workspace": "personal",
        "services": services(),
        **overrides,
    }


def begin(
    client: TestClient, body: dict[str, Any], *, headers: dict[str, str] = OTHER_HEADERS
) -> str:
    response = client.post(BASE + "/authorize", headers=headers, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "pending" and result["authorization_required"]
    return parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]


def callback(
    client: TestClient,
    state: str,
    *,
    headers: dict[str, str] = OTHER_HEADERS,
    **params: str,
) -> Any:
    response = client.get(
        BASE + "/callback",
        headers=headers,
        params={"state": state, "code": "fixture-code", **params},
        follow_redirects=False,
    )
    return response


def status(
    client: TestClient, request_id: str, *, headers: dict[str, str] = OTHER_HEADERS
) -> dict[str, Any]:
    response = client.get(BASE + "/requests/" + request_id, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def connected(client: TestClient, body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or request_body()
    state = begin(client, body)
    response = callback(client, state)
    assert response.status_code == 303, response.text
    assert status(client, body["request_id"])["state"] == "configured"
    result = client.get(BASE, headers=OTHER_HEADERS)
    assert result.status_code == 200, result.text
    return next(
        row
        for row in result.json()["accounts"]
        if row["email"] == body["expected_account_id"]
    )


async def wait_running(runtime: Any, expected: set[str]) -> None:
    for _attempt in range(200):
        inventory = runtime.adapters.inventory()
        if all(inventory.get(key, {}).get("state") == "running" for key in expected):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Google fixture adapters did not become running")


@pytest.mark.parametrize("mode", ["missing", "unknown", "view_as", "child"])
def test_connections_reject_untrusted_or_nonowner_settings_session(
    env: Environment, mode: str
) -> None:
    if mode == "child":
        env.settings = replace(
            env.settings, member_roles={MEMBER_A: "principal", MEMBER_B: "child"}
        )
    with env.client() as client:
        headers = (
            {}
            if mode == "missing"
            else {"Tailscale-User-Login": "unrecognized.ts.net"}
            if mode == "unknown"
            else {**MEMBER_HEADERS, "X-AdminMe-View-Member": MEMBER_B}
            if mode == "view_as"
            else OTHER_HEADERS
        )
        assert client.get(BASE, headers=headers).status_code in {401, 403}
        assert client.post(
            BASE + "/authorize", headers=headers, json=request_body()
        ).status_code in {401, 403}
        assert env.oauth.started == env.oauth.exchanged == env.committer.calls == []


def test_callback_records_exact_permissions_and_preserves_other_owner_runtime(
    env: Environment,
) -> None:
    with env.client() as client:
        runtime = client.app.state.runtime
        prior_config = copy.deepcopy(runtime.settings.adapter_config)
        prior_adapters = {
            key: row.adapter for key, row in runtime.adapters._managed.items()
        }
        body = request_body(
            services=services(gmail="write", calendar="read", tasks="write")
        )
        account = connected(client, body)
        assert account["services"]["gmail"]["write"] is True
        assert account["services"]["calendar"]["write"] is False
        assert account["services"]["tasks"]["write"] is True
        assert account["cos_context_access"] is False
        assert all(
            runtime.adapters._managed[key].adapter is value
            for key, value in prior_adapters.items()
        )
        before_other = owned_accounts(prior_config, MEMBER_A)
        assert owned_accounts(runtime.settings.adapter_config, MEMBER_A) == before_other
        own = owned_accounts(runtime.settings.adapter_config, MEMBER_B)[account["id"]]
        expected = {row["id"] for row in own["rows"].values()}
        client.portal.call(wait_running, runtime, expected)
        assert env.oauth.exchanged[0]["member_id"] == MEMBER_B
        assert env.oauth.exchanged[0]["expected_account_id"] == EMAIL
        assert env.oauth.exchanged[0]["verifier"] == env.oauth.started[0]["verifier"]
        for name, row in own["rows"].items():
            adapter = runtime.adapters._managed[row["id"]].adapter
            if name == "gmail":
                assert adapter._config["enable_writes"] is True
            else:
                assert bool(adapter.write_capabilities) is (name == "tasks")
        authorization = client.portal.call(_events, runtime, EVENT)
        completion = client.portal.call(_events, runtime, COMPLETE_EVENT)
        assert len(authorization) == len(completion) == 1
        assert authorization[0]["owner_scope"] == f"private:{MEMBER_B}"
        assert authorization[0]["payload"]["services"] == body["services"]
        assert authorization[0]["payload"]["provider_effects"] is False
        handoff_path = env.handoffs(runtime).path
        assert EMAIL.encode() not in handoff_path.read_bytes()
        assert b"fixture-secret" not in canonical(authorization)
        assert b"fixture-code" not in handoff_path.read_bytes()
        assert "credential_ref" not in json.dumps(
            client.get(BASE, headers=OTHER_HEADERS).json()
        )
        for event_type in (
            "external_sync.write_requested",
            "external.sent",
            "triage.item.proposed",
            "triage.intent.submitted",
        ):
            assert client.portal.call(_events, runtime, event_type) == []


def test_request_collision_callback_owner_and_replay_are_exact(
    env: Environment,
) -> None:
    with env.client() as client:
        body = request_body()
        state = begin(client, body)
        repeat = client.post(BASE + "/authorize", headers=OTHER_HEADERS, json=body)
        assert repeat.status_code == 200
        assert len(env.oauth.started) == 1
        collision = {**body, "expected_account_id": "different-member@example.invalid"}
        assert (
            client.post(
                BASE + "/authorize", headers=OTHER_HEADERS, json=collision
            ).status_code
            == 409
        )
        assert (
            client.post(
                BASE + "/authorize", headers=MEMBER_HEADERS, json=body
            ).status_code
            == 409
        )
        assert (
            client.get(
                BASE + "/requests/" + body["request_id"], headers=MEMBER_HEADERS
            ).status_code
            == 404
        )
        assert callback(client, state, headers=MEMBER_HEADERS).status_code == 400
        assert callback(client, "unknown-fixture-state").status_code == 400
        assert env.oauth.exchanged == []
        assert callback(client, state).status_code == 303
        assert callback(client, state).status_code == 303
        assert status(client, body["request_id"])["state"] == "configured"
        assert len(env.oauth.exchanged) == len(env.committer.calls) == 1
        assert len(client.portal.call(_events, client.app.state.runtime, EVENT)) == 1


@pytest.mark.parametrize(
    "failure", ["expired", "denied", "wrong_account", "partial_grant"]
)
def test_unsuccessful_consent_changes_no_account_or_permissions(
    env: Environment, failure: str
) -> None:
    with env.client() as client:
        runtime = client.app.state.runtime
        prior = env.committer.index_path.read_bytes()
        body = request_body()
        state = begin(client, body)
        params = {}
        if failure == "expired":
            record = env.handoffs(runtime).get(body["request_id"], MEMBER_B)
            record["expires_epoch"] = time.time() - 1
            env.handoffs(runtime).put(record)
        elif failure == "denied":
            params["error"] = "access_denied"
        else:
            env.oauth.fail = (
                "fixture wrong account"
                if failure == "wrong_account"
                else "fixture scope mismatch"
            )
        assert callback(client, state, error=params.get("error", "")).status_code == 303
        assert status(client, body["request_id"])["state"] == (
            "expired"
            if failure == "expired"
            else "cancelled"
            if failure == "denied"
            else "failed"
        )
        assert env.committer.index_path.read_bytes() == prior
        assert env.committer.calls == []
        assert client.portal.call(_events, runtime, EVENT) == []
        count = len(env.oauth.exchanged)
        assert callback(client, state).status_code == 303
        assert len(env.oauth.exchanged) == count


def test_permission_reduction_needs_no_new_oauth_and_stale_or_foreign_change_is_refused(
    env: Environment,
) -> None:
    with env.client() as client:
        account = connected(
            client,
            request_body(
                services=services(gmail="read", calendar="write", tasks="read")
            ),
        )
        body = {
            "request_id": str(uuid4()),
            "expected_revision": account["revision"],
            "services": services(gmail="off", calendar="read", tasks="off"),
        }
        assert (
            client.post(
                BASE + "/" + account["id"] + "/permissions",
                headers=MEMBER_HEADERS,
                json=body,
            ).status_code
            == 404
        )
        response = client.post(
            BASE + "/" + account["id"] + "/permissions",
            headers=OTHER_HEADERS,
            json=body,
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "configured"
        assert len(env.oauth.started) == len(env.oauth.exchanged) == 1
        repeat = client.post(
            BASE + "/" + account["id"] + "/permissions",
            headers=OTHER_HEADERS,
            json=body,
        )
        assert repeat.status_code == 200, repeat.text
        assert len(env.committer.calls) == 2
        stale = {**body, "request_id": str(uuid4())}
        assert (
            client.post(
                BASE + "/" + account["id"] + "/permissions",
                headers=OTHER_HEADERS,
                json=stale,
            ).status_code
            == 409
        )
        current = client.get(BASE, headers=OTHER_HEADERS).json()["accounts"][0]
        assert current["services"]["calendar"]["read"] is True
        assert current["services"]["calendar"]["write"] is False
        assert current["services"]["gmail"]["setup"] == "disabled"
        runtime = client.app.state.runtime
        own = owned_accounts(runtime.settings.adapter_config, MEMBER_B)[account["id"]]
        assert len(own["rows"]) == 3
        assert own["rows"]["gmail"]["id"] not in runtime.adapters.inventory()
        assert own["rows"]["tasks"]["id"] not in runtime.adapters.inventory()
        assert (
            runtime.adapters._managed[
                own["rows"]["calendar"]["id"]
            ].adapter.write_capabilities
            == []
        )


def test_increased_permissions_require_fresh_consent_and_recheck_stale_callback(
    env: Environment,
) -> None:
    with env.client() as client:
        account = connected(client)
        body = {
            "request_id": str(uuid4()),
            "expected_revision": account["revision"],
            "services": services(calendar="write"),
        }
        response = client.post(
            BASE + "/" + account["id"] + "/permissions",
            headers=OTHER_HEADERS,
            json=body,
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "pending"
        state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][
            0
        ]
        assert len(env.committer.calls) == 1
        reduction = {
            "request_id": str(uuid4()),
            "expected_revision": account["revision"],
            "services": services(calendar="off"),
        }
        response = client.post(
            BASE + "/" + account["id"] + "/permissions",
            headers=OTHER_HEADERS,
            json=reduction,
        )
        assert response.status_code == 200, response.text
        assert callback(client, state).status_code == 303
        assert status(client, body["request_id"])["state"] == "failed"
        current = client.get(BASE, headers=OTHER_HEADERS).json()["accounts"][0]
        assert current["services"]["calendar"]["read"] is False
        assert len(env.committer.calls) == 2


@pytest.mark.parametrize("stage", ["pending", "authorizing", "publishing"])
def test_restart_reconciles_known_state_without_redeeming_code_twice(
    env: Environment, stage: str
) -> None:
    with env.client() as client:
        body = request_body()
        state = begin(client, body)
        runtime = client.app.state.runtime
        if stage == "authorizing":
            record = env.handoffs(runtime).get(body["request_id"], MEMBER_B)
            record["state"] = "authorizing"
            env.handoffs(runtime).put(record)
        elif stage == "publishing":
            env.committer.failure = "after"
            assert callback(client, state).status_code == 303
            assert len(env.oauth.exchanged) == 1
            assert EMAIL.encode() in env.committer.index_path.read_bytes()
    env.committer.failure = None
    with env.client() as client:
        result = status(client, body["request_id"])
        if stage == "pending":
            assert result["state"] == "pending"
            assert callback(client, state).status_code == 303
            assert status(client, body["request_id"])["state"] == "configured"
            assert len(env.oauth.exchanged) == 1
        elif stage == "authorizing":
            assert result["state"] == "failed"
            assert callback(client, state).status_code == 303
            assert env.oauth.exchanged == []
        else:
            assert result["state"] == "configured"
            assert callback(client, state).status_code == 303
            assert len(env.oauth.exchanged) == len(env.committer.calls) == 1
            runtime = client.app.state.runtime
            assert len(client.portal.call(_events, runtime, EVENT)) == 1
            assert len(client.portal.call(_events, runtime, COMPLETE_EVENT)) == 1


def test_failed_provider_sync_is_not_reported_as_connected(env: Environment) -> None:
    env.oauth.provider_unavailable = True
    with env.client() as client:
        account = connected(client)
        runtime = client.app.state.runtime
        own = owned_accounts(runtime.settings.adapter_config, MEMBER_B)[account["id"]]

        async def wait_failure() -> None:
            for _attempt in range(200):
                row = runtime.adapters.inventory().get(
                    own["rows"]["calendar"]["id"], {}
                )
                if row.get("state") in {"error", "backoff", "failed", "degraded"}:
                    return
                await asyncio.sleep(0.01)

        client.portal.call(wait_failure)
        result = client.get(BASE, headers=OTHER_HEADERS).json()["accounts"][0]
        assert result["services"]["calendar"]["read"] is True
        assert result["services"]["calendar"]["setup"] != "connected"
        assert (
            status(client, env.handoffs(runtime).read().popitem()[0])["state"]
            == "configured"
        )


def service_context(client: TestClient, event_id: str, correlation: str) -> Any:
    return client.post(
        "/api/openclaw/v1/tools/service-context",
        headers=AUTH,
        json={
            "provenance": {
                "openclaw_version": OPENCLAW_VERSION,
                "openclaw_commit": OPENCLAW_COMMIT,
                "actor_id": COS_SERVICE_PRINCIPAL_ID,
                "agent_id": service_agent_id(COS_SERVICE_PRINCIPAL_ID),
                "session_key": service_session_key(
                    COS_SERVICE_PRINCIPAL_ID, SECRET, correlation
                ),
                "channel_id": "administrateme-service",
                "call_kind": "tool",
                "correlation_id": correlation,
            },
            "source_event_ids": [event_id],
        },
    )


def test_synced_schedule_is_private_context_only_with_delegation_and_read_off_withholds_it(
    env: Environment,
) -> None:
    with env.client() as client:
        runtime = client.app.state.runtime
        account = connected(
            client, request_body(services=services(gmail="read", calendar="read"))
        )
        owned = owned_accounts(runtime.settings.adapter_config, MEMBER_B)[account["id"]]
        active = {row["id"] for row in owned["rows"].values() if row["enabled"]}
        client.portal.call(wait_running, runtime, active)

        async def emit() -> tuple[str, str]:
            calendar = runtime.adapters._managed[
                owned["rows"]["calendar"]["id"]
            ].adapter
            await calendar._emit_calendar_snapshot(
                ctx=calendar._ctx,
                event_type="calendar.event_added",
                calendar_id="primary",
                calendar_name="Member schedule",
                event={
                    "id": "member-google-settings-event",
                    "summary": "Private scheduled appointment",
                    "status": "confirmed",
                    "start": {"dateTime": "2031-01-02T12:00:00Z"},
                    "end": {"dateTime": "2031-01-02T13:00:00Z"},
                    "updated": "2030-12-01T12:00:00Z",
                    "etag": "fixture-one",
                },
            )
            gmail = runtime.adapters._managed[owned["rows"]["gmail"]["id"]].adapter
            await gmail._emit_messaging_received(
                ctx=gmail._ctx,
                message={
                    "id": "member-google-settings-mail",
                    "threadId": "member-google-settings-thread",
                    "internalDate": "1925035200000",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "From", "value": "sender@example.invalid"},
                            {"name": "To", "value": EMAIL},
                            {"name": "Subject", "value": "Personal inbound obligation"},
                        ],
                        "body": {
                            "data": base64.urlsafe_b64encode(TEXT.encode()).decode()
                        },
                    },
                },
            )
            calendar_id = await runtime.event_log.latest_event_id(
                types=["calendar.event_added"]
            )
            mail_id = await runtime.event_log.latest_event_id(
                types=["messaging.received"]
            )
            assert calendar_id and mail_id
            await runtime.wait_for_projection("calendars", calendar_id)
            await runtime.wait_for_subscriber("source-policy-v2:shadow", mail_id)
            await runtime.wait_for_subscriber(
                "pipeline:pipeline:noise_filtering", mail_id
            )
            return calendar_id, mail_id

        event_id, _mail_id = client.portal.call(emit)
        # Acquisition is neither private CoS delegation nor household disclosure.
        assert (
            service_context(client, event_id, "before-google-delegation").status_code
            == 404
        )
        grant = client.post(
            "/api/member/v1/cos/delegations",
            headers=OTHER_HEADERS,
            json={
                "request_id": "settings-google-context-grant",
                "context_scope": "member_private",
                "permissions": ["read_context"],
            },
        )
        assert grant.status_code == 201, grant.text
        context = service_context(client, event_id, "after-google-delegation")
        assert context.status_code == 200, context.text
        assert (
            context.json()["facts"][0]["fact"]["summary"]
            == "Private scheduled appointment"
        )
        assert context.json()["facts"][0]["outside_disclosure_authorized"] is False
        connections = client.get(BASE, headers=OTHER_HEADERS)
        assert connections.status_code == 200, connections.text
        assert connections.json()["accounts"][0]["cos_context_access"] is True
        own_surface = client.get(
            "/api/member/v1/surfaces/calendar?date=2031-01-02", headers=OTHER_HEADERS
        )
        other_surface = client.get(
            "/api/member/v1/surfaces/calendar?date=2031-01-02", headers=MEMBER_HEADERS
        )
        assert "member-google-settings-event" in own_surface.text
        assert "member-google-settings-event" not in other_surface.text
        for kind in (
            "triage.item.proposed",
            "triage.intent.submitted",
            "messaging.semantic_features_extracted",
            "external_sync.write_requested",
            "external.sent",
        ):
            assert client.portal.call(_events, runtime, kind) == []
        body = {
            "request_id": str(uuid4()),
            "expected_revision": account["revision"],
            "services": services(calendar="off"),
        }
        reduction = client.post(
            BASE + "/" + account["id"] + "/permissions",
            headers=OTHER_HEADERS,
            json=body,
        )
        assert reduction.status_code == 200, reduction.text
        assert (
            service_context(client, event_id, "after-google-read-off").status_code
            == 404
        )
        assert event_id in {
            event["event_id"]
            for event in client.portal.call(_events, runtime, "calendar.event_added")
        }
        assert not active & set(runtime.adapters.inventory())
    with env.client() as client:
        assert (
            service_context(client, event_id, "after-read-off-restart").status_code
            == 404
        )
        assert (
            client.get(BASE, headers=OTHER_HEADERS).json()["accounts"][0]["services"][
                "calendar"
            ]["read"]
            is False
        )


@pytest.mark.parametrize(
    "invalid", ["write_without_read", "malformed_uuid", "extra_owner"]
)
def test_request_validation_does_not_begin_oauth(
    env: Environment, invalid: str
) -> None:
    with env.client() as client:
        body = request_body()
        if invalid == "write_without_read":
            body["services"]["gmail"] = {"read": False, "write": True}
        elif invalid == "malformed_uuid":
            body["request_id"] = "not-a-request-uuid"
        else:
            body["member_id"] = MEMBER_A
        assert (
            client.post(
                BASE + "/authorize", headers=OTHER_HEADERS, json=body
            ).status_code
            == 422
        )
        assert env.oauth.started == []


def test_other_member_cannot_claim_an_existing_google_account(env: Environment) -> None:
    with env.client() as client:
        account = client.get(BASE, headers=MEMBER_HEADERS).json()["accounts"][0]
        body = request_body(expected_account_id=account["email"])
        state = begin(client, body)
        before = env.committer.index_path.read_bytes()
        assert callback(client, state).status_code == 303
        assert status(client, body["request_id"])["state"] == "failed"
        assert env.committer.index_path.read_bytes() == before
        assert env.committer.calls == []


def test_second_account_keeps_first_account_revision_and_running_instances(
    env: Environment,
) -> None:
    with env.client() as client:
        first = connected(client)
        runtime = client.app.state.runtime
        initial = owned_accounts(runtime.settings.adapter_config, MEMBER_B)[first["id"]]
        first_calendar_id = initial["rows"]["calendar"]["id"]
        first_adapter = runtime.adapters._managed[first_calendar_id].adapter
        second = connected(
            client,
            request_body(
                expected_account_id="second-member-account@example.invalid",
                services=services(tasks="read", calendar="off"),
            ),
        )
        assert second["id"] != first["id"]
        current = owned_accounts(runtime.settings.adapter_config, MEMBER_B)[first["id"]]
        assert current["revision"] == initial["revision"]
        assert current == initial
        assert runtime.adapters._managed[first_calendar_id].adapter is first_adapter
        assert len(client.get(BASE, headers=OTHER_HEADERS).json()["accounts"]) == 2
        assert len(env.committer.calls) == len(env.oauth.exchanged) == 2


@pytest.mark.parametrize("access", ["read", "write"])
def test_gmail_permission_display_requires_selected_writes_and_loaded_adapter(
    env: Environment, access: str
) -> None:
    with env.client() as client:
        account = connected(
            client,
            request_body(services=services(gmail=access, calendar="off")),
        )
        runtime = client.app.state.runtime
        gmail_id = owned_accounts(runtime.settings.adapter_config, MEMBER_B)[
            account["id"]
        ]["rows"]["gmail"]["id"]
        client.portal.call(wait_running, runtime, {gmail_id})

        def displayed_permissions() -> list[str]:
            response = client.get(
                "/api/member/v1/surfaces/settings", headers=OTHER_HEADERS
            )
            assert response.status_code == 200, response.text
            return next(
                row["permissions"]["write"]
                for row in response.json()["control"]["connections"]
                if row["id"] == gmail_id
            )

        assert displayed_permissions() == (
            ["reply", "send", "mark read", "archive"] if access == "write" else []
        )
        client.portal.call(runtime.adapters.stop_instances, {gmail_id})
        # A saved consent alone is not an executable adapter capability.
        assert displayed_permissions() == []
        assert client.portal.call(_events, runtime, "external.sent") == []
