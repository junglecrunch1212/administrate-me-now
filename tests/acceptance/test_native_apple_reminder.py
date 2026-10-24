"""Private native instruction -> two approvals -> exact bridge write -> receipt.

Only the EventKit store is synthetic. The encrypted application, enrollment,
member grants, native intake, approval receipts, bridge queue/authorization,
command ledger and result ingestion use their production implementations.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import time
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.apple_service_permissions import digest
from adminme.cos_self_replies import self_reply_session
from adminme.openclaw_contract import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.openclaw_topology import member_agent_id
from adminme.runtime.application import create_application
from adminme_bridge.config import load_config
from adminme_bridge.connector_packs.reminders_apple_eventkit_bridge import (
    AppleEventKitBridgeAdapter,
)
from adminme_bridge.connector_packs.reminders_apple_eventkit_bridge.wrapper import (
    _load_central_adapter_class,
)
from adminme_bridge.ingest_client import SubmitResult
from adminme_bridge.main import BridgeSupervisor
from tests.acceptance.test_apple_messages_governed_reply import (
    OPENCLAW_HEADERS as AUTH,
    SECRET,
    _allow_once,
    _execute,
    _provenance,
)
from tests.acceptance.test_apple_service_permissions import (
    PATH,
    grant_services,
    prepare_apple_sources,
)
from tests.acceptance.test_cos_member_bridge_acquisition import (
    MEMBER_A,
    MEMBER_B,
    MEMBER_HEADERS,
    OTHER_HEADERS,
    _bridge_headers,
    _configuration_settings,
    _observation,
)
from tests.acceptance.test_forwarded_channel_intake import ACCOUNT, _events

CONFIRM = "/api/openclaw/v1/tools/confirm-apple-reminder"
STATUS = "/api/openclaw/v1/tools/apple-reminder-status"
PACK = "reminders-apple-eventkit-bridge"
LIST = "fixture-apple_reminders-container"
DUE_AT = "2026-10-24T09:30:00-04:00"
UTC_DUE = "2026-10-24T13:30:00+00:00"
COMPONENTS = {
    "year": 2026,
    "month": 10,
    "day": 24,
    "hour": 13,
    "minute": 30,
    "second": 0,
    "time_zone": "UTC",
    "all_day": False,
    "floating": False,
}


def _settings(tmp_path: Path) -> Any:
    base = _configuration_settings(tmp_path)
    return replace(
        base,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={
            **base.openclaw_actor_identities,
            "fixture|g11-account-a|g11-actor-a": MEMBER_A,
        },
    )


def _grant(client: TestClient, *, other: bool = False) -> None:
    result = client.post(
        "/api/member/v1/cos/delegations",
        headers=OTHER_HEADERS if other else MEMBER_HEADERS,
        json={
            "request_id": "native-reminder-delegation-b"
            if other
            else "native-reminder-delegation-a",
            "context_scope": "member_private",
            "permissions": ["read_context", "prepare_external_actions"],
        },
    )
    assert result.status_code == 201, result.text


def _instruction(
    client: TestClient, native_id: str, *, other: bool = False
) -> dict[str, Any]:
    member = MEMBER_B if other else MEMBER_A
    actor = "actor-forward-b" if other else "actor-forward-a"
    provenance = {
        "actor_id": actor,
        "agent_id": member_agent_id(member),
        "account_id": ACCOUNT,
        "session_key": self_reply_session(member, ACCOUNT, actor),
        "channel_id": "imessage",
        "message_id": native_id,
        "correlation_id": native_id,
        "call_kind": "bridge",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }
    forwarded = client.post(
        "/api/openclaw/v1/submissions/forward",
        headers=AUTH,
        json={
            "provenance": provenance,
            "request_id": native_id,
            "text": "Add a reminder to my Apple Reminders to call my mom at 9:30 AM today",
            "consent": True,
            "submission_gesture": "dedicated_intake",
        },
    )
    assert forwarded.status_code == 200, forwarded.text
    return {
        "provenance": provenance,
        "source_message_event_id": forwarded.json()["intent_event_id"],
    }


def _body(
    source: dict[str, Any], request_id: str = "native-reminder-request"
) -> dict[str, Any]:
    return {
        **source,
        "request_id": request_id,
        "title": "Call my mom",
        "due_at": DUE_AT,
        "notes": "Requested in private member chat",
    }


def _status(
    client: TestClient, source: dict[str, Any], approval_id: str
) -> dict[str, Any]:
    response = client.post(
        STATUS,
        headers=AUTH,
        json={**source, "domain_approval_event_id": approval_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _normal_operation(client: TestClient) -> None:
    client.portal.call(
        client.app.state.runtime.observation.disable,
        "operator:fixture:native-reminder",
        "Credential-free native reminder acceptance",
    )


class _EventKitStore:
    """Native storage boundary only; no macOS API or account is opened."""

    def __init__(self) -> None:
        self.effects: list[dict[str, Any]] = []

    def build_reminder(self, **fields: Any) -> Any:
        return SimpleNamespace(
            calendarItemIdentifier="fixture-created-reminder", **fields
        )

    def update_reminder(self, reminder: Any, *, payload: dict[str, Any]) -> Any:
        reminder.due_components = copy.deepcopy(payload["due"])
        return reminder

    def save(self, reminder: Any, *, commit: bool) -> None:
        assert commit is True
        self.effects.append(copy.deepcopy(vars(reminder)))


class _BridgeHTTP:
    """One actual command-loop pass against authenticated central HTTP routes."""

    def __init__(
        self,
        client: TestClient,
        headers: dict[str, str],
        replay: dict[str, Any] | None = None,
    ) -> None:
        self.client, self.headers, self.replay = client, headers, replay
        self.supervisor: BridgeSupervisor | None = None
        self.submitted: list[dict[str, Any]] = []

    async def fetch_commands(self, providers: list[str]) -> dict[str, Any]:
        if self.replay is not None:
            return {"commands": [copy.deepcopy(self.replay)]}
        response = self.client.get(
            "/api/bridge/commands",
            headers=self.headers,
            params={"providers": ",".join(providers)},
        )
        assert response.status_code == 200, response.text
        assert response.json()["count"] == 1
        return response.json()

    async def authorize_apple_command(
        self, command: dict[str, Any], configuration: str
    ) -> dict[str, Any]:
        response = self.client.post(
            f"/api/bridge/commands/{command['command_id']}/apple-authorization",
            headers=self.headers,
            json={
                "event_id": command["event_id"],
                "configuration_sha256": configuration,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["authorization"]

    async def submit(self, envelope: dict[str, Any]) -> SubmitResult:
        response = self.client.post(
            "/api/bridge/ingest", headers=self.headers, json=envelope
        )
        assert response.status_code == 202, response.text
        self.submitted.append(envelope)
        assert self.supervisor is not None
        self.supervisor._stopping = True
        return SubmitResult(True, response.json()["event_id"], response.status_code)


def _supervisor(
    client: TestClient,
    helper: Any,
    transfer: dict[str, Any],
    tmp_path: Path,
    store: _EventKitStore,
    *,
    replay: dict[str, Any] | None = None,
) -> tuple[BridgeSupervisor, _BridgeHTTP]:
    config = load_config(helper.paths.config_path).model_copy(
        update={
            "cursor_db_path": str(tmp_path / "native-reminder-ledger.sqlite3"),
            "command_poll_interval_s": 1.0,
        }
    )
    adapter = AppleEventKitBridgeAdapter(internal_config=config.pack_config[PACK])
    # Keep the production adapter and its last native permission check. Only its
    # OS-owned store initialization is replaced with the isolated store boundary.
    internal = _load_central_adapter_class()(config=config.pack_config[PACK])
    internal._ek_store = store
    adapter._internal = internal
    record = next(
        row
        for row in client.app.state.runtime.settings.bridge_enrollments.values()
        if row.get("member_id") == MEMBER_A and row.get("status") == "active"
    )
    transport = _BridgeHTTP(client, _bridge_headers(transfer), replay)
    supervisor = BridgeSupervisor(
        config=config,
        ingest_client=transport,
        adapters=[adapter],
        device_identity_sha256=record["device_identity_sha256"],
    )  # type: ignore[arg-type]
    transport.supervisor = supervisor
    return supervisor, transport


def _run_dispatch(supervisor: BridgeSupervisor) -> None:
    asyncio.run(asyncio.wait_for(supervisor._command_loop(), timeout=5))


def test_native_reminder_exact_due_two_approvals_bridge_receipt_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    settings = _settings(tmp_path)
    store = _EventKitStore()
    with TestClient(
        create_application(settings), base_url="https://central-fixture.ts.net"
    ) as client:
        runtime = client.app.state.runtime
        helper, transfer = prepare_apple_sources(client, tmp_path)
        _grant(client)
        _grant(client, other=True)
        grant_services(client)
        source = _instruction(client, "native-reminder-create-input")
        body = _body(source)
        observed = client.post(CONFIRM, headers=AUTH, json=body)
        assert observed.status_code == 409 and "Observation" in observed.text
        _normal_operation(client)
        response = client.post(CONFIRM, headers=AUTH, json=body)
        assert response.status_code == 200, response.text
        receipt = response.json()
        approval_id = receipt["domain_approval_event_id"]
        assert receipt["state"] == "awaiting_openclaw_approval"
        assert receipt["due_at"] == UTC_DUE and receipt["list_id"] == LIST
        assert receipt["provider_effect_count"] == 0
        domain = client.portal.call(runtime.event_log.get, approval_id)
        assert (
            domain["owner_scope"] == domain["visibility_scope"] == f"private:{MEMBER_A}"
        )
        assert domain["causation_id"] == source["source_message_event_id"]
        assert domain["payload"]["command_payload"]["due"] == COMPONENTS
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        assert (
            _execute(client, approval_id, "native-missing", receipt=False).status_code
            == 403
        )
        assert (
            _status(client, source, approval_id)["state"]
            == "awaiting_openclaw_approval"
        )
        accepted = _execute(client, approval_id, "native-reminder-once", receipt=True)
        assert accepted.status_code == 200, accepted.text
        pending = _status(client, source, approval_id)
        assert (
            pending["state"] == "pending" and pending["provider_effect_count"] is None
        )
        headers = _bridge_headers(transfer)
        command = client.get(
            "/api/bridge/commands",
            headers=headers,
            params={"providers": "apple_reminders"},
        ).json()["commands"][0]
        assert command["payload"]["due"] == COMPONENTS
        supervisor, transport = _supervisor(client, helper, transfer, tmp_path, store)
        try:
            proof = asyncio.run(
                transport.authorize_apple_command(
                    command, digest(supervisor._config.pack_config)
                )
            )
            bound = {**command, "apple_service_authorization": proof}
            for path, value in (
                (["payload", "list_external_id"], "unselected-list"),
                (["payload", "title"], "Changed title"),
                (["provider_account_id"], "other-account"),
            ):
                forged = copy.deepcopy(bound)
                target = forged
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = value
                with pytest.raises(ValueError):
                    supervisor._authorized_command_adapter(
                        forged, {"apple_reminders": supervisor._adapters[0]}
                    )
            assert store.effects == []
            _run_dispatch(supervisor)
            assert len(transport.submitted) == len(store.effects) == 1
            effect = store.effects[0]
            assert (
                effect["list_identifier"] == LIST
                and effect["due_components"] == COMPONENTS
            )
            assert effect["title"] == body["title"] and effect["notes"] == body["notes"]
        finally:
            supervisor._command_ledger.close()
        terminal = _status(client, source, approval_id)
        assert (
            terminal["state"] == "succeeded" and terminal["provider_effect_count"] == 1
        )
        assert terminal["provider_observation"] == "pending"
        assert terminal["external_id"] == "fixture-created-reminder"
        retained = client.portal.call(
            runtime.event_log.get, terminal["result_event_id"]
        )
        assert retained["source_adapter"] == "adminme-bridge-command-dispatcher"
        assert retained["causation_id"] == command["event_id"]
        assert (
            retained["payload"]["openclaw_approval_event_id"]
            == command["openclaw_approval_event_id"]
        )
        original_lookup = runtime.event_log.get_by_idempotency
        for path, value in (
            (["source_adapter"], "untrusted-result-source"),
            (["causation_id"], "different-command"),
            (["source_account_id"], "other-source-account"),
            (["payload", "openclaw_approval_event_id"], "other-approval"),
            (["owner_scope"], f"private:{MEMBER_B}"),
            (["payload", "provider_account_id"], "other-account"),
            (["payload", "result", "applied"], False),
        ):
            # Corrupt only the status read boundary after a real bridge result;
            # no invented success is appended and no provider call is repeated.
            async def corrupted_lookup(tenant: str, key: str) -> Any:
                event = await original_lookup(tenant, key)
                if (
                    event is not None
                    and event["event_id"] == terminal["result_event_id"]
                ):
                    event = copy.deepcopy(event)
                    target = event
                    for part in path[:-1]:
                        target = target[part]
                    target[path[-1]] = value
                return event

            with monkeypatch.context() as patch:
                patch.setattr(runtime.event_log, "get_by_idempotency", corrupted_lookup)
                unverified = _status(client, source, approval_id)
                assert unverified["state"] == "unknown"
                assert unverified["provider_effect_count"] is None
        mirror = _observation(
            runtime, "apple_reminders", "native-reminder-independent-readback"
        )
        mirror["payload"].update(
            item_external_id=terminal["external_id"],
            title=body["title"],
            notes=body["notes"],
            due=COMPONENTS,
            due_at=UTC_DUE,
        )
        ingested = client.post("/api/bridge/ingest", headers=headers, json=mirror)
        assert ingested.status_code == 202, ingested.text
        client.portal.call(
            runtime.wait_for_projection, "lists", ingested.json()["event_id"]
        )
        assert (
            _status(client, source, approval_id)["provider_observation"] == "observed"
        )
        other = _instruction(client, "native-reminder-other-status", other=True)
        hidden = client.post(
            STATUS,
            headers=AUTH,
            json={**other, "domain_approval_event_id": approval_id},
        )
        assert hidden.status_code == 404
        # The same caller request ID belongs to a different member namespace;
        # B cannot recover A's confirmation or borrow A's native write grant.
        other_create = client.post(CONFIRM, headers=AUTH, json=_body(other))
        assert other_create.status_code == 409
        assert approval_id not in other_create.text
        conflict = client.post(
            CONFIRM, headers=AUTH, json={**body, "due_at": "2026-10-24T10:30:00-04:00"}
        )
        assert conflict.status_code == 409
        revoked = client.post(
            f"{PATH}/apple_reminders",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "native-reminder-revoke",
                "expected_revision": 1,
                "enabled": False,
                "scope_ids": [],
            },
        )
        assert revoked.status_code == 202, revoked.text
        assert _status(client, source, approval_id)["state"] == "succeeded"
        replay = client.post(CONFIRM, headers=AUTH, json=body)
        assert replay.status_code == 200 and replay.json()["duplicate"] is True
        assert replay.json()["domain_approval_event_id"] == approval_id
        assert (
            client.post(
                CONFIRM,
                headers=AUTH,
                json={**body, "request_id": "native-reminder-after-revoke"},
            ).status_code
            == 409
        )
        # Even a duplicate transport delivery after bridge restart uses the same
        # terminal ledger; revoked permission never causes a second native call.
        restarted, replay_transport = _supervisor(
            client, helper, transfer, tmp_path, store, replay=command
        )
        try:
            _run_dispatch(restarted)
            assert len(replay_transport.submitted) == 1 and len(store.effects) == 1
        finally:
            restarted._command_ledger.close()
    with TestClient(
        create_application(settings), base_url="https://central-fixture.ts.net"
    ) as client:
        source = _instruction(client, "native-reminder-after-central-restart")
        status = _status(client, source, approval_id)
        assert status["state"] == "succeeded" and status["provider_effect_count"] == 1
        replay = client.post(CONFIRM, headers=AUTH, json=_body(source))
        assert replay.status_code == 200 and replay.json()["duplicate"] is True
        assert replay.json()["domain_approval_event_id"] == approval_id
        runtime = client.app.state.runtime
        assert (
            len(
                client.portal.call(_events, runtime, "member.provider_action.confirmed")
            )
            == 1
        )
        assert (
            len(client.portal.call(_events, runtime, "external_sync.write_requested"))
            == 1
        )
        assert (
            len(client.portal.call(_events, runtime, "external_sync.write_completed"))
            == 1
        )
        assert len(store.effects) == 1


def test_native_reminder_rejects_unbound_input_and_changed_current_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        prepare_apple_sources(client, tmp_path)
        _grant(client)
        grants = grant_services(client)
        _normal_operation(client)
        body = _body(_instruction(client, "native-reminder-negative-input"))
        mutations = (
            (["provenance", "session_key"], "unbound-session"),
            (["provenance", "message_id"], "other-native-message"),
            (["provenance", "thread_id"], "group-conversation"),
            (["provenance", "agent_id"], "different-agent"),
            (["provenance", "call_kind"], "tool"),
            (["source_message_event_id"], "missing-source"),
            (["list_id"], "unselected-list"),
            (["due_at"], "2026-10-24T09:30:00"),
            (["due_at"], 1792848600),
            (["due_at"], "2026-10-24T09:30:00.123-04:00"),
            (["provider_account_id"], "untrusted-account"),
            (["openclaw_approval_receipt_id"], "invented-approval"),
        )
        for path, value in mutations:
            candidate = copy.deepcopy(body)
            target = candidate
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            response = client.post(CONFIRM, headers=AUTH, json=candidate)
            assert response.status_code in {403, 404, 422}, (path, response.text)
        import adminme.products.openclaw.service_work as private_work

        class Later(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                return datetime.now(tz) + timedelta(seconds=3601)

        with monkeypatch.context() as patch:
            patch.setattr(private_work, "datetime", Later)
            expired = client.post(CONFIRM, headers=AUTH, json=body)
            assert expired.status_code == 403 and "expired" in expired.text
        records = runtime.settings.bridge_enrollments
        identity, record = next(
            (key, row)
            for key, row in records.items()
            if row.get("member_id") == MEMBER_A and row.get("status") == "active"
        )
        records["ambiguous-fixture-device"] = copy.deepcopy(record)
        try:
            assert client.post(CONFIRM, headers=AUTH, json=body).status_code == 409
        finally:
            records.pop("ambiguous-fixture-device")
        original_config = copy.deepcopy(record["pack_config"])
        record["pack_config"][PACK]["allowed_container_ids"] = ["changed-container"]
        try:
            assert client.post(CONFIRM, headers=AUTH, json=body).status_code == 409
        finally:
            records[identity]["pack_config"] = original_config
        confirmed = client.post(CONFIRM, headers=AUTH, json=body)
        assert confirmed.status_code == 200, confirmed.text
        approval_id = confirmed.json()["domain_approval_event_id"]
        import adminme.openclaw_approvals as approval_store

        expired_receipt = _allow_once(client, approval_id, "native-expired-approval")
        expiry_now = time.time() + 121
        with monkeypatch.context() as patch:
            patch.setattr(
                approval_store, "time", SimpleNamespace(time=lambda: expiry_now)
            )
            expired = client.post(
                "/api/openclaw/v1/tools/execute-pending-provider-action",
                headers=AUTH,
                json={
                    "provenance": _provenance(),
                    "domain_approval_event_id": approval_id,
                    "openclaw_approval_receipt_id": expired_receipt,
                },
            )
            assert expired.status_code == 403 and "expired" in expired.text
        revoked = client.post(
            f"{PATH}/apple_reminders",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "native-reminder-stale-revoke",
                "expected_revision": 1,
                "enabled": False,
                "scope_ids": [],
            },
        )
        assert revoked.status_code == 202, revoked.text
        assert (
            _execute(
                client, approval_id, "native-revoked-execute", receipt=True
            ).status_code
            == 409
        )
        reenabled = client.post(
            f"{PATH}/apple_reminders",
            headers=MEMBER_HEADERS,
            json={
                **grants["apple_reminders"],
                "request_id": "native-reminder-new-grant",
                "expected_revision": 2,
            },
        )
        assert reenabled.status_code == 202, reenabled.text
        assert (
            _execute(
                client, approval_id, "native-stale-execute", receipt=True
            ).status_code
            == 409
        )
        replay = client.post(CONFIRM, headers=AUTH, json=body)
        assert replay.status_code == 200 and replay.json()["duplicate"] is True
        assert replay.json()["domain_approval_event_id"] == approval_id
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_completed") == []
        )


def test_native_reminder_asks_for_exact_list_when_multiple_are_authorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tests.acceptance.test_apple_service_permissions as permission_fixture

    original_enable = permission_fixture._enable

    def enable(helper: Any, capability: str) -> None:
        if capability != "apple_reminders":
            original_enable(helper, capability)
            return
        config = helper.pack_config_for(
            capability,
            source_coordinate=LIST,
            account_coordinate="fixture-apple_reminders-account",
        )
        config["allowed_container_ids"].append("fixture-second-reminder-list")
        helper.record_permission(capability, granted=True, pack_config=config)
        helper.run_capability_test(
            capability,
            lambda _capability, _request: {
                "status": "passed",
                "content_exposed": False,
            },
        )
        helper.prove_and_start(capability)
        helper.record_observing(capability)

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    monkeypatch.setattr(permission_fixture, "_enable", enable)
    with TestClient(
        create_application(_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        body = _body(_instruction(client, "native-reminder-choose-list"))
        choice = client.post(CONFIRM, headers=AUTH, json=body)
        assert choice.status_code == 200, choice.text
        assert choice.json()["state"] == "list_choice_required"
        assert {row["list_id"] for row in choice.json()["lists"]} == {
            LIST,
            "fixture-second-reminder-list",
        }
        assert all(set(row) == {"list_id", "label"} for row in choice.json()["lists"])
        assert (
            client.portal.call(_events, runtime, "member.provider_action.confirmed")
            == []
        )
        selected = client.post(CONFIRM, headers=AUTH, json={**body, "list_id": LIST})
        assert selected.status_code == 200, selected.text
        assert selected.json()["list_id"] == LIST
        assert selected.json()["state"] == "awaiting_openclaw_approval"
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )


def test_native_request_id_namespace_keeps_member_and_tenant_coordinates_distinct() -> (
    None
):
    from adminme.products.openclaw.apple_reminders import reminder_command_id

    original = reminder_command_id("tenant-fixture", MEMBER_A, "same-request-id")
    assert original == reminder_command_id(
        "tenant-fixture", MEMBER_A, "same-request-id"
    )
    assert original != reminder_command_id(
        "tenant-fixture", MEMBER_B, "same-request-id"
    )
    assert original != reminder_command_id("other-tenant", MEMBER_A, "same-request-id")
    assert reminder_command_id(
        "tenant|member", "actor", "request-id"
    ) != reminder_command_id("tenant", "member|actor", "request-id")


def test_native_reminder_provider_called_timeout_stays_unknown_without_reexecution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UncertainStore(_EventKitStore):
        def save(self, reminder: Any, *, commit: bool) -> None:
            super().save(reminder, commit=commit)
            raise TimeoutError("Synthetic provider timed out after the save boundary")

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    store = UncertainStore()
    with TestClient(
        create_application(_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        helper, transfer = prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        source = _instruction(client, "native-reminder-timeout-input")
        body = _body(source, "native-reminder-timeout-request")
        confirmed = client.post(CONFIRM, headers=AUTH, json=body)
        assert confirmed.status_code == 200, confirmed.text
        approval_id = confirmed.json()["domain_approval_event_id"]
        executed = _execute(
            client, approval_id, "native-timeout-approval", receipt=True
        )
        assert executed.status_code == 200, executed.text
        command = client.get(
            "/api/bridge/commands",
            headers=_bridge_headers(transfer),
            params={"providers": "apple_reminders"},
        ).json()["commands"][0]
        supervisor, transport = _supervisor(client, helper, transfer, tmp_path, store)
        try:
            _run_dispatch(supervisor)
            assert len(store.effects) == len(transport.submitted) == 1
            assert transport.submitted[0]["type"] == "external_sync.write_failed"
            assert transport.submitted[0]["payload"]["provider_called"] is True
        finally:
            supervisor._command_ledger.close()
        status = _status(client, source, approval_id)
        assert status["state"] == "unknown" and status["provider_effect_count"] is None
        assert status["automatic_retry"] is False
        replay = client.post(CONFIRM, headers=AUTH, json=body)
        assert replay.status_code == 200 and replay.json()["duplicate"] is True
        assert replay.json()["state"] == "unknown"
        restarted, replay_transport = _supervisor(
            client, helper, transfer, tmp_path, store, replay=command
        )
        try:
            _run_dispatch(restarted)
            assert len(replay_transport.submitted) == 1 and len(store.effects) == 1
        finally:
            restarted._command_ledger.close()
        assert (
            len(client.portal.call(_events, runtime, "external_sync.write_failed")) == 1
        )
        assert (
            len(client.portal.call(_events, runtime, "external_sync.write_requested"))
            == 1
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_completed") == []
        )


def test_native_reminder_observation_or_revocation_after_queue_blocks_edge_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    store = _EventKitStore()
    with TestClient(
        create_application(_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        helper, transfer = prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        headers = _bridge_headers(transfer)
        config = load_config(helper.paths.config_path)
        for stop in ("observation", "revocation"):
            source = _instruction(client, f"native-reminder-edge-{stop}")
            body = _body(source, f"native-reminder-edge-{stop}-request")
            confirmed = client.post(CONFIRM, headers=AUTH, json=body)
            assert confirmed.status_code == 200, confirmed.text
            approval_id = confirmed.json()["domain_approval_event_id"]
            executed = _execute(
                client, approval_id, f"native-edge-{stop}-approval", receipt=True
            )
            assert executed.status_code == 200, executed.text
            command = client.get(
                "/api/bridge/commands",
                headers=headers,
                params={"providers": "apple_reminders"},
            ).json()["commands"][0]
            assert _status(client, source, approval_id)["state"] == "pending"
            if stop == "observation":
                client.portal.call(
                    runtime.observation.enable,
                    "operator:fixture:native-reminder",
                    "Stop queued native writes",
                )
            else:
                revoked = client.post(
                    f"{PATH}/apple_reminders",
                    headers=MEMBER_HEADERS,
                    json={
                        "request_id": "native-edge-revoked-permission",
                        "expected_revision": 1,
                        "enabled": False,
                        "scope_ids": [],
                    },
                )
                assert revoked.status_code == 202, revoked.text
            proof = client.post(
                f"/api/bridge/commands/{command['command_id']}/apple-authorization",
                headers=headers,
                json={
                    "event_id": command["event_id"],
                    "configuration_sha256": digest(config.pack_config),
                },
            )
            assert proof.status_code == 409, proof.text
            supervisor, transport = _supervisor(
                client, helper, transfer, tmp_path, store
            )
            try:
                _run_dispatch(supervisor)
                assert len(transport.submitted) == 1
                assert transport.submitted[0]["payload"]["provider_called"] is False
                assert store.effects == []
            finally:
                supervisor._command_ledger.close()
            status = _status(client, source, approval_id)
            assert status["state"] == "failed" and status["provider_effect_count"] == 0
            if stop == "observation":
                _normal_operation(client)
        assert (
            len(client.portal.call(_events, runtime, "external_sync.write_failed")) == 2
        )
        assert store.effects == []
