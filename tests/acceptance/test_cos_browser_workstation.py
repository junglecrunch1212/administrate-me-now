"""Real authenticated intake, encrypted browser observation and audience return."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.runtime.application import create_application
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.source_policy import policy_for_preset
from adminme.workstation_browser import BROWSER_POLICY_FILE
from adminme.workstation_inventory import INVENTORY_FILE
from adminme.workstation_sources import coordinate, current_resource
from tests.acceptance.test_cos_member_follow_up import _body, _grant, _service
from tests.acceptance.test_cos_response_execution import A, _settings
from tests.acceptance.test_cos_workstation_handoff import _post
from tests.acceptance.test_native_apple_reminder import _normal_operation
from tests.unit.bootstrap.test_workstation_browser import private_file
from tests.unit.test_workstation_inventory import document

BASE = "/api/openclaw/v1/tools/workstation-browser"
INSTRUCTION = (
    "Read the currently authorized CoS planning page and tell me what is visible."
)
NONCE = "7" * 32


def setup_browser(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict, dict]:
    runtime = client.app.state.runtime
    directory = runtime.settings.instance.config_dir
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    private_file(directory / INVENTORY_FILE, document(datetime.now(timezone.utc)))
    policy = {
        "revision": 1,
        "enabled": True,
        "resources": [
            {
                "resource_id": "own-planning-page",
                "inventory_binding_id": "cos-browser",
                "inventory_binding_revision": 1,
                "native_target_id": "observed-native-target",
                "target_evidence_reference": "operator:profile-and-native-tab",
                "url": "https://work.example.invalid/planning",
                "authorized_url_prefix": "https://work.example.invalid/",
                "identity_probes": {
                    key: {"selector": f"#{key}"}
                    for key in ("account", "workspace", "role")
                },
                "audience_member_ids": [A],
                "restrictions": ["internal_household_coordination_only"],
                "sensitivity": "privileged",
            }
        ],
    }
    private_file(directory / BROWSER_POLICY_FILE, policy)
    resource = current_resource(runtime, "own-planning-page", for_read=True)
    source = coordinate(runtime, resource)
    state = runtime.settings_control.read()
    source_policy = {
        **policy_for_preset("balanced").model_dump(mode="json"),
        "context_mode": "content_on_explicit_authorized_demand",
    }
    state["source_policies_v2"].setdefault(COS_SERVICE_PRINCIPAL_ID, {})[source.key] = {
        "policy": source_policy
    }
    monkeypatch.setattr(runtime.settings_control, "read", lambda: state)
    _normal_operation(client)
    _grant(client)
    return policy, source_policy


def start(client: TestClient, *, nonce: str = NONCE) -> tuple[dict, dict, dict]:
    source = _body(client, text=INSTRUCTION)
    admitted = _post(
        client,
        BASE + "/admit",
        {**source, "action": "create", "instruction": INSTRUCTION, "run_nonce": nonce},
    )
    assert admitted["resource_id"] == "own-planning-page"
    service = _service(source, nonce)
    resource = _post(client, BASE + "/policy", {"provenance": service})["resource"]
    body = {
        "provenance": service,
        "route_sha256": resource["route_sha256"],
        "native_target_id": resource["native_target_id"],
        "url": resource["url"],
        "actual_identity": resource["expected_identity"],
        "excerpt": "Planning: prepare the household briefing.",
        "truncated": False,
    }
    result = {
        **source,
        "follow_up_source_id": admitted["follow_up_source_id"],
        "run_nonce": nonce,
    }
    return body, result, resource


def test_native_observation_requires_terminal_capture_and_preserves_source_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_application(_settings(tmp_path / "instance"))) as client:
        policy, source_policy = setup_browser(client, monkeypatch)
        body, result, resource = start(client)
        assert _post(client, BASE + "/result", result)["status"] == "pending_or_unknown"
        wrong = {
            **body,
            "actual_identity": {
                **body["actual_identity"],
                "account": "other@fixture.invalid",
            },
        }
        _post(client, BASE + "/capture", wrong, 403)
        _post(client, BASE + "/capture", {**body, "native_target_id": "other-tab"}, 403)
        _post(client, BASE + "/capture", {**body, "excerpt": "🙂" * 1000}, 403)
        captured = _post(client, BASE + "/capture", body)
        assert captured["status"] == "verified"
        assert _post(client, BASE + "/capture", body) == captured
        _post(client, BASE + "/capture", {**body, "excerpt": "Changed text"}, 409)
        runtime = client.app.state.runtime
        event = client.portal.call(
            runtime.event_log.get, captured["observation_event_id"]
        )
        assert event["type"] == "cos.workstation.source_observed"
        assert event["source_account_id"] == resource["expected_identity"]["account"]
        assert event["sensitivity"] == "privileged"
        assert (
            event["payload"]["restrictions"] == policy["resources"][0]["restrictions"]
        )
        completed = _post(client, BASE + "/result", result)
        assert completed["status"] == "verified"
        assert completed["excerpt"] == body["excerpt"]
        assert completed["audience"] == [A]
        assert completed["return_to"] == "current_authenticated_conversation"
        assert completed["provider_effect_count"] == 0
        current = _body(
            client,
            text="What did that browser read find?",
            native_id="browser-status-request",
        )
        retained = _post(
            client,
            BASE + "/retained",
            {
                **current,
                "browser_request_event_id": completed["browser_request_event_id"],
            },
        )
        assert retained == completed
        _post(
            client,
            BASE + "/retained",
            {
                **current,
                "browser_request_event_id": "missing-browser-request",
            },
            404,
        )
        client.portal.call(runtime.projections.rebuild, "tasks")
        assert _post(client, BASE + "/result", result) == completed
        # Changing only the verified tab route preserves historical source facts;
        # the old worker cannot acquire another snapshot through its old route.
        policy["revision"] += 1
        policy["resources"][0]["native_target_id"] = "replacement-native-target"
        private_file(runtime.settings.instance.config_dir / BROWSER_POLICY_FILE, policy)
        assert _post(client, BASE + "/result", result) == completed
        _post(client, BASE + "/capture", body, 403)
        source_policy["context_mode"] = "metadata_only"
        _post(client, BASE + "/result", result, 403)
        assert runtime.fixture_adapter.delivered == []


def test_browser_read_revocation_audience_and_recorded_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_application(_settings(tmp_path / "instance"))) as client:
        policy, _ = setup_browser(client, monkeypatch)
        body, result, _ = start(client)
        _post(
            client,
            BASE + "/failure",
            {
                "provenance": body["provenance"],
                "reason": "native_browser_read_not_verified",
            },
        )
        assert _post(client, BASE + "/result", result)["status"] == "blocked"
        _post(
            client,
            BASE + "/failure",
            {
                "provenance": body["provenance"],
                "reason": "private account detail must not enter errors",
            },
            422,
        )
        runtime = client.app.state.runtime
        policy["resources"][0]["audience_member_ids"] = ["unrelated-member"]
        private_file(runtime.settings.instance.config_dir / BROWSER_POLICY_FILE, policy)
        _post(client, BASE + "/result", result, 403)
        _post(client, BASE + "/capture", body, 403)


def test_browser_resource_choice_is_filtered_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_application(_settings(tmp_path / "instance"))) as client:
        policy, source_policy = setup_browser(client, monkeypatch)
        runtime = client.app.state.runtime
        original = policy["resources"][0]
        # Each configured source still needs an existing authorized source policy.
        state = runtime.settings_control.read()
        for n in range(1, 14):
            row = {
                **deepcopy(original),
                "resource_id": f"planning-{n}",
                "url": f"https://work.example.invalid/planning/{n}",
            }
            policy["resources"].append(row)
        private_file(runtime.settings.instance.config_dir / BROWSER_POLICY_FILE, policy)
        for row in policy["resources"]:
            resource = current_resource(runtime, row["resource_id"], for_read=True)
            state["source_policies_v2"][COS_SERVICE_PRINCIPAL_ID][
                coordinate(runtime, resource).key
            ] = {"policy": source_policy}
        source = _body(client, text=INSTRUCTION)
        body: dict[str, Any] = {
            **source,
            "action": "create",
            "instruction": INSTRUCTION,
            "run_nonce": NONCE,
        }
        first = _post(client, BASE + "/admit", body)
        second = _post(
            client, BASE + "/admit", {**body, "choice_offset": first["next_offset"]}
        )
        assert first["status"] == second["status"] == "resource_choice_required"
        assert len(first["resource_ids"]) == 10
        assert len(second["resource_ids"]) == 4
        assert second["next_offset"] is None
        assert len(json.dumps(first).encode()) < 3800
        assert (
            _post(
                client, BASE + "/admit", {**body, "resource_id": "own-planning-page"}
            )["status"]
            == "ready"
        )
        client.portal.call(
            runtime.observation.enable,
            "operator:fixture:browser",
            "Pause requested browser work",
        )
        _post(client, BASE + "/policy", {"provenance": _service(source, NONCE)}, 409)


def test_operator_source_provision_is_exact_append_only_and_does_not_override_restrictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta
    from bootstrap.workstation_browser import stage_source_request
    from adminme.workstation_source_policy import PROVISION_FILE

    with TestClient(create_application(_settings(tmp_path / "instance"))) as client:
        setup_browser(client, monkeypatch)
        monkeypatch.undo()  # Exercise actual durable settings policy below.
        runtime = client.app.state.runtime
        original = _body(client, text=INSTRUCTION)
        service = _service(original, NONCE)
        state = _post(
            client,
            BASE + "/source-policy",
            {"provenance": service, "resource_id": "own-planning-page"},
        )
        assert state["origin"] == "recommended_default"
        staged = {
            "request_id": "4" * 32,
            "resource_id": "own-planning-page",
            "policy_key": state["policy_key"],
            "expected_source_revision": state["revision"],
            "expected_binding_revision": state["binding_revision"],
            "operator_principal_id": "operator:fixture:owner-delegated",
            "owner_authorization_sha256": "a" * 64,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=15)
            ).isoformat(),
            "context_mode": "content_on_explicit_authorized_demand",
        }
        call = {"provenance": service, "request_id": staged["request_id"]}
        _post(client, BASE + "/provision-source-policy", call, 403)
        directory = runtime.settings.instance.config_dir
        stage_source_request(directory, staged)
        _post(
            client,
            BASE + "/provision-source-policy",
            {**call, "request_id": "5" * 32},
            403,
        )
        granted = _post(client, BASE + "/provision-source-policy", call)
        assert (
            granted["policy"]["context_mode"] == "content_on_explicit_authorized_demand"
        )
        assert granted["idempotent"] is False
        assert (
            _post(client, BASE + "/provision-source-policy", call)["idempotent"] is True
        )
        event = client.portal.call(runtime.event_log.get, granted["event_id"])
        assert event["type"] == "settings.source_policy_v2_changed"
        assert event["payload"]["owner_principal_id"] == COS_SERVICE_PRINCIPAL_ID
        assert event["actor_identity"] == staged["operator_principal_id"]
        assert staged["owner_authorization_sha256"] in event["raw_ref"]
        client.portal.call(
            runtime.settings_control.replay_source_policy_v2_events, runtime.event_log
        )
        assert (
            _post(
                client,
                BASE + "/source-policy",
                {"provenance": service, "resource_id": "own-planning-page"},
            )["revision"]
            == granted["revision"]
        )
        assert (
            _post(
                client,
                BASE + "/admit",
                {
                    **original,
                    "action": "create",
                    "instruction": INSTRUCTION,
                    "run_nonce": NONCE,
                },
            )["status"]
            == "ready"
        )
        # Staging is a one-request private operator control, not a model argument.
        assert (directory / PROVISION_FILE).stat().st_mode & 0o777 == 0o600
        record = runtime.settings_control.read()
        record["source_policies_v2"][COS_SERVICE_PRINCIPAL_ID][state["policy_key"]][
            "policy"
        ]["context_mode"] = "none"
        monkeypatch.setattr(runtime.settings_control, "read", lambda: record)

        async def preserve_fixture_restriction(_log):
            return 0

        monkeypatch.setattr(
            runtime.settings_control,
            "replay_source_policy_v2_events",
            preserve_fixture_restriction,
        )
        staged.update(request_id="6" * 32, expected_source_revision=granted["revision"])
        stage_source_request(directory, staged)
        _post(
            client,
            BASE + "/provision-source-policy",
            {"provenance": service, "request_id": staged["request_id"]},
            409,
        )
