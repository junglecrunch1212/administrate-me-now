"""Private bounded Gmail acquisition through the production composition root.

The only substituted boundary is read-only Gmail/secret access. No provider or
private household data belongs in these fixtures; run in hosted CI or a lab.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.adapters.disposable_test_acquisition import (
    CONFIG_KEY,
    CONTRACT,
    DisposableTestAcquisition,
    discover_test_message_selection,
    restrict_adapter_config,
)
from adminme.adapters.loader import load_builtin_adapters
from adminme.runtime.application import RuntimeSettings, create_application


ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-bounded-adult-a"  # fixture:tenant_data:ok
OTHER = "fixture-bounded-adult-b"  # fixture:tenant_data:ok
ACCOUNT = "bounded-fixture@example.invalid"
HEADERS = {"Tailscale-User-Login": "bounded-a.ts.net"}
OTHER_HEADERS = {"Tailscale-User-Login": "bounded-b.ts.net"}


def _policy(ids: list[str]) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "adapter_id": "fixture-bounded-gmail",
        "member_id": MEMBER,
        "provider_account_id": ACCOUNT,
        "message_ids": ids,
    }


def _config(ids: list[str]) -> dict[str, Any]:
    return {
        "adapter_supervisor_port": 0,
        CONFIG_KEY: _policy(ids),
        "adapters": [
            {
                "id": "fixture-bounded-gmail",
                "kind": "messaging-gmail-api",
                "config": {
                    "service_account_key_ref": "op://fixture/gmail/credentials",
                    "member_id": MEMBER,
                    "provider_account_id": ACCOUNT,
                    "user_email": ACCOUNT,
                    "workspace": "personal",
                    "permission_scope": [
                        "https://www.googleapis.com/auth/gmail.readonly"
                    ],
                    "binding_provenance": "service-account-delegation",
                    "ownership_attested": True,
                    "initial_backfill_days": 730,
                    "initial_backfill_max_messages": 1000,
                    "historical_seed_queries": ["newer_than:7d"],
                    "enable_writes": True,
                },
            },
            # This enabled second importer must never even load its pack.
            {
                "id": "fixture-excluded",
                "kind": "unavailable-fixture-provider",
                "enabled": True,
                "config": {},
            },
        ],
    }


def _message(message_id: str, *, sent: bool = False) -> dict[str, Any]:
    return {
        "id": message_id,
        "threadId": "fixture-shared-thread",
        "internalDate": "1788264000000",
        "labelIds": ["SENT"] if sent else ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {
                    "name": "From",
                    "value": ACCOUNT if sent else "sender@example.invalid",
                },
                {
                    "name": "To",
                    "value": "recipient@example.invalid" if sent else ACCOUNT,
                },
                {"name": "Subject", "value": "Synthetic informational observation"},
            ],
            "body": {
                "data": base64.urlsafe_b64encode(
                    b"Informational fixture only."
                ).decode()
            },
        },
    }


class _ExpiredHistory(RuntimeError):
    resp = type("FixtureResponse", (), {"status": 404})()


class _Gmail:
    def __init__(self) -> None:
        self.messages_by_id = {
            "selected-incoming": _message("selected-incoming"),
            "selected-sent": _message("selected-sent", sent=True),
            "outside-thread-context": _message("outside-thread-context"),
            "selected-new": _message("selected-new"),
        }
        self.get_ids: list[str] = []
        self.history_ids: list[str] = []
        self.next_history: dict[str, Any] | Exception = {"historyId": "100"}
        self.profile_id = "100"
        self.mode = ""
        self.arguments: dict[str, Any] = {}

    def users(self) -> _Gmail:
        return self

    def messages(self) -> _Gmail:
        self.mode = "messages"
        return self

    def history(self) -> _Gmail:
        self.mode = "history"
        return self

    def threads(self) -> _Gmail:
        raise AssertionError("thread expansion is prohibited")

    def getProfile(self, **kwargs: Any) -> _Gmail:
        self.mode = "profile"
        return self

    def list(self, **kwargs: Any) -> _Gmail:
        assert self.mode == "history", "broad or related Gmail searches are prohibited"
        self.arguments = kwargs
        self.history_ids.append(kwargs["startHistoryId"])
        return self

    def get(self, **kwargs: Any) -> _Gmail:
        assert self.mode == "messages"
        self.arguments = kwargs
        self.get_ids.append(kwargs["id"])
        return self

    def execute(self) -> dict[str, Any]:
        if self.mode == "profile":
            return {"historyId": self.profile_id}
        if self.mode == "history":
            if isinstance(self.next_history, Exception):
                raise self.next_history
            return copy.deepcopy(self.next_history)
        return copy.deepcopy(self.messages_by_id[self.arguments["id"]])


def _settings(tmp_path: Path, config: dict[str, Any]) -> RuntimeSettings:
    instance = tmp_path / "instance"
    instance.mkdir(exist_ok=True)
    return RuntimeSettings.credential_free(
        instance,
        encryption_key=b"s" * 32,
        repository_root=ROOT,
        member_identities={"bounded-a.ts.net": MEMBER, "bounded-b.ts.net": OTHER},
        member_roles={MEMBER: "principal", OTHER: "principal"},
        adapter_config=config,
        deterministic_openclaw_fixture=True,
        human_triage_management_enabled=True,
    )


def _fake_provider(monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]) -> _Gmail:
    adapter = load_builtin_adapters(config, repository_root=ROOT)[0]
    module = sys.modules[type(adapter).__module__]
    service = _Gmail()
    monkeypatch.setattr(module, "_default_credentials_resolver", lambda _ref: {})

    def service_factory(_credentials: Any, scopes: list[str], subject: str) -> _Gmail:
        assert scopes == ["https://www.googleapis.com/auth/gmail.readonly"]
        assert subject == ACCOUNT
        return service

    monkeypatch.setattr(module, "_default_service_factory", service_factory)
    monkeypatch.setattr(type(adapter), "POLL_INTERVAL_S", 3600)
    return service


async def _adapter(runtime: Any) -> Any:
    deadline = asyncio.get_running_loop().time() + 30
    adapter = runtime.adapters._managed["fixture-bounded-gmail"].adapter
    while asyncio.get_running_loop().time() < deadline:
        if adapter.status().extra.get("initial_backfill", {}).get("state") in {
            "complete",
            "disabled",
        }:
            return adapter
        assert adapter.status().state not in {"crashed", "backoff"}, adapter.status()
        await asyncio.sleep(0.01)
    raise AssertionError("bounded acquisition did not settle")


async def _source_events(runtime: Any) -> list[dict[str, Any]]:
    return [
        event
        async for event in runtime.event_log.read_since(
            types=["messaging.received", "messaging.sent"]
        )
    ]


async def _settled_skill_receipts(runtime: Any) -> set[str]:
    deadline = asyncio.get_running_loop().time() + 60
    stable = 0
    while asyncio.get_running_loop().time() < deadline:
        statuses = [
            await runtime.bus.subscriber_status(name)
            for name in runtime.bus._subscribers
        ]
        assert not any(value["degraded"] for value in statuses), statuses
        stable = stable + 1 if all(value["lag_count"] == 0 for value in statuses) else 0
        if stable >= 2:
            return {
                event["event_id"]
                async for event in runtime.event_log.read_since(
                    types=["skill.call.recorded", "skill.call.failed"]
                )
            }
        await asyncio.sleep(0.01)
    raise AssertionError("bounded source processing did not settle")


def test_initial_incremental_expired_cursor_and_warm_restart_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(["selected-incoming", "selected-sent"])
    original_config = copy.deepcopy(config)
    service = _fake_provider(monkeypatch, config)
    settings = _settings(tmp_path, config)
    app = create_application(settings)
    with TestClient(app) as client:
        runtime = app.state.runtime
        adapter = client.portal.call(_adapter, runtime)
        assert set(runtime.adapters.inventory()) == {"fixture-bounded-gmail"}
        assert runtime.xlsx_reverse._observer is None
        assert runtime.fixture_adapter is None
        assert (
            adapter.status().extra["test_acquisition"][
                "lifetime_selected_message_count"
            ]
            == 2
        )
        sources = client.portal.call(_source_events, runtime)
        assert {event["type"] for event in sources} == {
            "messaging.received",
            "messaging.sent",
        }
        assert len(sources) == 2
        original_source_ids = {event["event_id"] for event in sources}
        assert all(event["owner_scope"] == f"private:{MEMBER}" for event in sources)
        service.next_history = {
            "historyId": "101",
            "history": [
                {
                    "messagesAdded": [
                        {"message": {"id": "selected-incoming"}},
                        {"message": {"id": "outside-thread-context"}},
                        {"message": {"id": "selected-incoming"}},
                    ]
                }
            ],
        }

        async def poll() -> int:
            return await adapter._run_poll_cycle(ctx=adapter._ctx)

        assert client.portal.call(poll) == 0
        assert "outside-thread-context" not in service.get_ids
        assert adapter._cursor == "101"
        service.next_history = _ExpiredHistory("synthetic expired cursor")
        service.profile_id = "200"
        assert client.portal.call(poll) == 0
        assert adapter._cursor == "200"
        assert (
            adapter.status().extra["cursor_recovery"] == "bounded_test_selection_rescan"
        )
        assert {
            event["event_id"] for event in client.portal.call(_source_events, runtime)
        } == original_source_ids
        response = client.get("/api/member/v1/surfaces/settings", headers=HEADERS)
        assert response.status_code == 200, response.text
        assert "bounded_test_sample" in response.text
        assert '"selected_message_count":2' in response.text
        assert "selected-incoming" not in response.text
        other = client.get("/api/member/v1/surfaces/settings", headers=OTHER_HEADERS)
        assert other.status_code == 200
        assert ACCOUNT not in other.text and "bounded_test_sample" not in other.text
        for path, payload in [
            ("/api/bridge/ingest", {}),
            ("/api/bridge/ingest/batch", {"envelopes": [{}]}),
        ]:
            blocked = client.post(path, headers=HEADERS, json=payload)
            assert blocked.status_code == 403
            assert (
                blocked.json()["detail"]
                == "bridge_ingestion_paused_for_disposable_test"
            )
        skill_receipts = client.portal.call(_settled_skill_receipts, runtime)
    assert config == original_config
    service.next_history = {"historyId": "200"}
    for _ in range(3):
        before_gets = len(service.get_ids)
        restarted = create_application(settings)
        with TestClient(restarted) as client:
            runtime = restarted.state.runtime
            adapter = client.portal.call(_adapter, runtime)
            assert adapter.status().extra["initial_backfill"]["durable_receipt_reused"]
            assert len(service.get_ids) == before_gets
            assert {
                event["event_id"]
                for event in client.portal.call(_source_events, runtime)
            } == original_source_ids
            assert adapter._cursor == "200"
            assert (
                client.portal.call(_settled_skill_receipts, runtime) == skill_receipts
            )


@pytest.mark.parametrize("bounded", [False, True])
def test_member_settings_do_not_publish_private_operator_adapter_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bounded: bool,
) -> None:
    config = _config([])
    if not bounded:
        config.pop(CONFIG_KEY)
        config["adapters"][1]["enabled"] = False
        config["adapters"][0]["config"].update(
            {
                "enable_writes": False,
                "initial_backfill_days": 0,
                "initial_backfill_max_messages": 0,
                "historical_seed_queries": [],
            }
        )
    _fake_provider(monkeypatch, config)
    app = create_application(_settings(tmp_path, config))
    with TestClient(app) as client:
        runtime = app.state.runtime
        client.portal.call(_adapter, runtime)
        client.portal.call(_settled_skill_receipts, runtime)
        capability_id = "adapter:messaging-gmail-api"
        operator = client.portal.call(runtime.diagnostics)
        operator_adapter = operator["capabilities"][capability_id]
        assert ACCOUNT in json.dumps(operator_adapter)
        assert operator_adapter["details"]["instances"]
        for headers, owner_visible in (
            (HEADERS, True),
            (OTHER_HEADERS, False),
            ({**OTHER_HEADERS, "X-AdminMe-View-Member": MEMBER}, False),
            ({**HEADERS, "X-AdminMe-View-Member": OTHER}, False),
        ):
            response = client.get("/api/member/v1/surfaces/settings", headers=headers)
            assert response.status_code == 200
            payload = response.json()
            if owner_visible:
                assert ACCOUNT in json.dumps(payload["control"]["connections"])
            else:
                assert ACCOUNT not in response.text
                assert "bounded_test_sample" not in response.text
            # All three compatibility paths expose only aggregate adapter
            # health; owner-only account controls retain the useful detail.
            for inventory in (
                payload["capabilities"],
                payload["system"]["capabilities"],
                list(payload["system"]["runtime"]["capabilities"].values()),
            ):
                assert ACCOUNT not in json.dumps(inventory)
                adapter_summary = next(
                    record
                    for record in inventory
                    if record["name"] == "messaging-gmail-api"
                )
                assert adapter_summary["state"] == operator_adapter["state"]
                assert "instances" not in adapter_summary["details"]
                assert "durable_watermarks" not in adapter_summary["details"]
        # Rendering one member never redacts the shared operator evidence.
        assert ACCOUNT in json.dumps(runtime.capabilities[capability_id].public())
        client.portal.call(_settled_skill_receipts, runtime)
        for headers in ({}, HEADERS, OTHER_HEADERS):
            operator = client.portal.call(runtime.diagnostics)
            response = client.get("/api/system/ready", headers=headers)
            assert response.status_code == (200 if operator["ready"] else 503)
            assert ACCOUNT not in response.text
            assert "bounded_test_sample" not in response.text
            readiness = response.json()
            # Public health retains all admission/settlement states and counts.
            # The historical repair exception cannot infer its exact private
            # predecessor baseline from this aggregate response.
            for field in (
                "status",
                "ready",
                "service_ready",
                "release_ready",
                "activation_eligible",
                "safe_reads",
                "write_admission",
                "accepting_writes",
                "event_ingest_ready",
                "observation_active",
                "deployment_readiness",
                "member_bridge_count",
            ):
                assert readiness[field] == operator[field]
            assert set(readiness["capabilities"]) == set(operator["capabilities"])
            for key, record in operator["capabilities"].items():
                assert readiness["capabilities"][key]["state"] == record["state"]
            source_key = "policy:source-assurance"
            assert (
                readiness["capabilities"][source_key]["details"]["counts"]
                == (operator["capabilities"][source_key]["details"]["counts"])
            )
            public_adapter = readiness["capabilities"][capability_id]
            assert "instances" not in public_adapter["details"]
            assert "durable_watermarks" not in public_adapter["details"]
            assert ACCOUNT in json.dumps(runtime.capabilities[capability_id].public())


def test_selected_update_and_interrupted_append_retry_are_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(["selected-incoming"])
    service = _fake_provider(monkeypatch, config)
    app = create_application(_settings(tmp_path, config))
    with TestClient(app) as client:
        runtime = app.state.runtime
        adapter = client.portal.call(_adapter, runtime)
        client.portal.call(_settled_skill_receipts, runtime)
        original_write = runtime.events.write
        interrupted = False

        async def write_then_interrupt(envelope: Any, **kwargs: Any) -> str:
            nonlocal interrupted
            event_id = await original_write(envelope, **kwargs)
            if envelope.type == "messaging.received" and not interrupted:
                interrupted = True
                raise RuntimeError("synthetic interruption after source commit")
            return event_id

        monkeypatch.setattr(runtime.events, "write", write_then_interrupt)
        service.messages_by_id["selected-incoming"]["historyId"] = "201"
        service.messages_by_id["selected-incoming"]["payload"]["body"]["data"] = (
            base64.urlsafe_b64encode(b"Updated informational fixture only.").decode()
        )
        service.next_history = {
            "historyId": "201",
            "history": [
                {
                    "messagesAdded": [
                        {"message": {"id": "selected-incoming"}},
                        {"message": {"id": "outside-thread-context"}},
                    ]
                }
            ],
        }

        async def poll() -> int:
            return await adapter._run_poll_cycle(ctx=adapter._ctx)

        with pytest.raises(RuntimeError, match="synthetic interruption"):
            client.portal.call(poll)
        receipts_after_commit = client.portal.call(_settled_skill_receipts, runtime)
        assert len(client.portal.call(_source_events, runtime)) == 2
        client.portal.call(poll)
        assert len(client.portal.call(_source_events, runtime)) == 2
        assert (
            client.portal.call(_settled_skill_receipts, runtime)
            == receipts_after_commit
        )
        assert client.portal.call(poll) == 0
        assert "outside-thread-context" not in service.get_ids


def test_empty_selection_can_admit_new_ids_without_reimporting_old_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config([])
    service = _fake_provider(monkeypatch, config)
    for expected_ids in [
        [],
        ["selected-incoming"],
        ["selected-incoming", "selected-new"],
    ]:
        config[CONFIG_KEY]["message_ids"] = expected_ids
        app = create_application(_settings(tmp_path, config))
        with TestClient(app) as client:
            runtime = app.state.runtime
            client.portal.call(_adapter, runtime)
            sources = client.portal.call(_source_events, runtime)
            assert {event["payload"]["native_message_id"] for event in sources} == set(
                expected_ids
            )
            assert len(sources) == len(expected_ids)
    assert set(service.get_ids) == {"selected-incoming", "selected-new"}


def test_lifetime_selection_cannot_recycle_slots_or_change_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cursors.sqlite3"
    store = AdapterCursorStore(path)
    assert (
        store.reserve_test_selection(
            "owner-coordinate", tuple(f"id-{i}" for i in range(200)), limit=200
        )
        == 200
    )
    store.close()
    store = AdapterCursorStore(path)
    try:
        with pytest.raises(ValueError, match="lifetime distinct-message cap"):
            store.reserve_test_selection("owner-coordinate", ("new-id",), limit=200)
        with pytest.raises(ValueError, match="owner cannot change"):
            store.reserve_test_selection("other-coordinate", (), limit=200)
        assert store.reserve_test_selection("owner-coordinate", (), limit=200) == 200
    finally:
        store.close()


@pytest.mark.parametrize(
    "ids",
    [
        ["duplicate", "duplicate"],
        [f"id-{i}" for i in range(201)],
        ["private\nvalue"],
        [True],
    ],
)
def test_invalid_selection_is_rejected(ids: list[Any]) -> None:
    with pytest.raises(ValueError):
        DisposableTestAcquisition.parse(_policy(ids))


def test_binding_and_dataset_marker_fail_closed(tmp_path: Path) -> None:
    config = _config([])
    config[CONFIG_KEY]["provider_account_id"] = "another-fixture@example.invalid"
    with pytest.raises(ValueError, match="binding mismatch"):
        restrict_adapter_config(config)
    config = _config([])
    settings = _settings(tmp_path, config)
    settings.instance.config_dir.mkdir(parents=True, exist_ok=True)
    (settings.instance.config_dir / "disposable-test-dataset.json").write_text(
        json.dumps({"contract": "disposable-test-dataset-v1"})
    )
    config.pop(CONFIG_KEY)
    with pytest.raises(ValueError, match="requires its acquisition policy"):
        _settings(tmp_path, config)


class _DiscoveryGmail:
    def __init__(self, *, repeat_cursor: bool = False, account: str = ACCOUNT) -> None:
        self.account = account
        self.profile_calls = 0
        self.list_calls: list[dict[str, Any]] = []
        self.mode = ""
        self.pages = [
            {
                "messages": [{"id": f"id-{i}"} for i in range(120)],
                "nextPageToken": "page-2",
            },
            {
                "messages": [{"id": f"id-{i}"} for i in range(100, 180)],
                "nextPageToken": "page-2" if repeat_cursor else "page-3",
            },
            {
                "messages": [{"id": f"id-{i}"} for i in range(180, 200)],
                "nextPageToken": "page-4",
            },
        ]

    def users(self) -> _DiscoveryGmail:
        return self

    def getProfile(self, **kwargs: Any) -> _DiscoveryGmail:
        assert kwargs == {"userId": "me"}
        self.profile_calls += 1
        self.mode = "profile"
        return self

    def messages(self) -> _DiscoveryGmail:
        self.mode = "list"
        return self

    def list(self, **kwargs: Any) -> _DiscoveryGmail:
        assert self.mode == "list"
        self.list_calls.append(kwargs)
        return self

    def execute(self) -> dict[str, Any]:
        return (
            {"emailAddress": self.account}
            if self.mode == "profile"
            else self.pages.pop(0)
        )


@pytest.mark.parametrize(
    "credential_key", ["service_account_key_ref", "oauth_credentials_ref"]
)
def test_read_only_discovery_uses_existing_credentials_and_private_capped_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    credential_key: str,
) -> None:
    config = _config([])
    if credential_key == "oauth_credentials_ref":
        config["adapters"][0]["config"].pop("service_account_key_ref")
        config["adapters"][0]["config"][credential_key] = (
            "keychain://fixture/installed-user"
        )
    original = copy.deepcopy(config)
    _fake_provider(monkeypatch, config)
    loaded = load_builtin_adapters(config, repository_root=ROOT)[0]
    module = sys.modules[type(loaded).__module__]
    service = _DiscoveryGmail()

    def factory(_credentials: Any, scopes: list[str], subject: str) -> _DiscoveryGmail:
        assert scopes == ["https://www.googleapis.com/auth/gmail.readonly"]
        assert subject == ACCOUNT
        return service

    monkeypatch.setattr(module, "_default_service_factory", factory)
    output = tmp_path / "selection.json"
    result = discover_test_message_selection(
        config, repository_root=ROOT, output=output
    )
    assert config == original
    assert service.profile_calls == 1
    assert [call["maxResults"] for call in service.list_calls] == [200, 80, 20]
    assert all(
        call["q"] == "newer_than:7d -label:drafts" for call in service.list_calls
    )
    assert all(call["includeSpamTrash"] is False for call in service.list_calls)
    assert result["selected_message_count"] == 200
    assert result["includes_incoming_and_sent"] is True
    assert result["message_bodies_fetched"] == result["thread_context_fetched"] == 0
    assert result["provider_mutations"] is False
    assert result["selection_limit_reason"] == "message_cap"
    assert "id-0" not in json.dumps(result)
    assert ACCOUNT not in json.dumps(result)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert output.stat().st_nlink == 1
    assert json.loads(output.read_text()) == {
        "message_ids": [f"id-{i}" for i in range(200)]
    }
    with pytest.raises(ValueError, match="new file in a private directory"):
        discover_test_message_selection(config, repository_root=ROOT, output=output)
    assert service.profile_calls == 1


@pytest.mark.parametrize("wrong_account", [False, True])
def test_discovery_stops_on_repeated_page_or_wrong_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wrong_account: bool,
) -> None:
    config = _config([])
    _fake_provider(monkeypatch, config)
    loaded = load_builtin_adapters(config, repository_root=ROOT)[0]
    module = sys.modules[type(loaded).__module__]
    service = _DiscoveryGmail(
        repeat_cursor=True,
        account="other-fixture@example.invalid" if wrong_account else ACCOUNT,
    )
    monkeypatch.setattr(module, "_default_service_factory", lambda *_args: service)
    output = tmp_path / "selection.json"
    if wrong_account:
        with pytest.raises(RuntimeError, match="read-only Gmail test discovery failed"):
            discover_test_message_selection(config, repository_root=ROOT, output=output)
        assert not output.exists() and not service.list_calls
    else:
        result = discover_test_message_selection(
            config, repository_root=ROOT, output=output
        )
        assert result["selected_message_count"] == 180
        assert result["selection_limit_reason"] == "repeated_page_cursor"
        assert len(service.list_calls) == 2
