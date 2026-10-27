"""Real authenticated intake, encrypted browser observation and audience return."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
from tests.acceptance.test_cos_workstation_handoff import _native_failure, _post
from tests.acceptance.test_native_apple_reminder import _normal_operation
from tests.unit.bootstrap.test_workstation_browser import private_file
from tests.unit.test_workstation_inventory import document

BASE = "/api/openclaw/v1/tools/workstation-browser"
INSTRUCTION = (
    "Read the currently authorized CoS planning page and tell me what is visible."
)
NONCE = "7" * 32


def setup_browser(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, *, gmail: bool = False
) -> tuple[dict, dict]:
    runtime = client.app.state.runtime
    directory = runtime.settings.instance.config_dir
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    inventory = document(datetime.now(timezone.utc))
    if gmail:
        for field in ("expected_identity", "observed_identity"):
            inventory["bindings"][0][field]["role"] = "account user"
    private_file(directory / INVENTORY_FILE, inventory)
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
    if gmail:
        policy["resources"][0].update(
            url="https://mail.google.com/mail/u/0/#inbox",
            authorized_url_prefix="https://mail.google.com/mail/u/0/",
            identity_verifier="gmail_self_mailbox_v1",
            identity_probes={},
        )
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


@pytest.mark.parametrize("gmail", [False, True])
def test_native_observation_requires_terminal_capture_and_preserves_source_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gmail: bool
) -> None:
    with TestClient(create_application(_settings(tmp_path / "instance"))) as client:
        policy, source_policy = setup_browser(client, monkeypatch, gmail=gmail)
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
        # Reusing the identifier cannot substitute a new operator authorization.
        stage_source_request(
            directory, {**staged, "owner_authorization_sha256": "b" * 64}
        )
        _post(client, BASE + "/provision-source-policy", call, 409)
        stage_source_request(directory, staged)
        assert (
            _post(client, BASE + "/provision-source-policy", call)["event_id"]
            == granted["event_id"]
        )
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


def test_source_policy_tools_require_exact_service_and_current_operator_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adminme.products.openclaw import workstation_source_policy as endpoint
    from bootstrap.workstation_browser import stage_source_request

    with TestClient(create_application(_settings(tmp_path / "instance"))) as client:
        setup_browser(client, monkeypatch)
        monkeypatch.undo()
        runtime = client.app.state.runtime
        source = _body(client, text=INSTRUCTION)
        service = _service(source, NONCE)
        read = {"provenance": service, "resource_id": "own-planning-page"}
        state = _post(client, BASE + "/source-policy", read)
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
        directory = runtime.settings.instance.config_dir
        stage_source_request(directory, staged)
        apply = {"provenance": service, "request_id": staged["request_id"]}
        for path, body in (
            ("/source-policy", read),
            ("/provision-source-policy", apply),
        ):
            _post(
                client,
                BASE + path,
                {k: v for k, v in body.items() if k != "provenance"},
                422,
            )
            _post(
                client, BASE + path, {**body, "provenance": source["provenance"]}, 403
            )
            for field, value in (
                ("session_key", service["session_key"] + "0"),
                ("actor_id", "another-member"),
                ("agent_id", "operator"),
                ("account_id", "another-account"),
                ("channel_id", "imessage"),
            ):
                _post(
                    client,
                    BASE + path,
                    {**body, "provenance": {**service, field: value}},
                    403,
                )
            _post(
                client,
                BASE + path,
                {**body, "context_mode": staged["context_mode"]},
                422,
            )
        _post(
            client,
            BASE + "/source-policy",
            {**read, "resource_id": "unconfigured"},
            403,
        )
        for invalid in ("", "4" * 31, "G" * 32, "4" * 33):
            _post(
                client,
                BASE + "/provision-source-policy",
                {**apply, "request_id": invalid},
                422,
            )
        for field, value in (
            ("expected_source_revision", state["revision"] + 1),
            ("expected_binding_revision", state["binding_revision"] + 1),
            ("policy_key", "spv2_" + "0" * 64),
        ):
            stage_source_request(directory, {**staged, field: value})
            _post(client, BASE + "/provision-source-policy", apply, 409)
        stage_source_request(directory, staged)

        class AfterExpiry(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat(staged["expires_at"]) + timedelta(
                    seconds=1
                )

        with monkeypatch.context() as patch:
            patch.setattr(endpoint, "datetime", AfterExpiry)
            _post(client, BASE + "/provision-source-policy", apply, 409)
        assert _post(client, BASE + "/source-policy", read) == state
        assert (
            client.portal.call(
                runtime.event_log.get_by_idempotency,
                runtime.settings.instance.tenant_id,
                "workstation-browser-source-policy:" + staged["request_id"],
            )
            is None
        )


def test_parent_native_error_is_durable_without_inventing_browser_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path / "instance")
    with TestClient(create_application(settings)) as client:
        setup_browser(client, monkeypatch)
        body, result, _ = start(client)
        runtime = client.app.state.runtime
        state = runtime.settings_control.read()
        signed = {**result, "native_failure": _native_failure(result, "browser")}
        failed = _post(client, BASE + "/result", signed)
        assert failed["status"] == "failed"
        assert failed["reason"] == "native_worker_failed"
        assert failed["native_run_id"] == "native-worker-error"
        assert failed["provider_effect_count"] == 0
        assert "observation_event_id" not in failed and "excerpt" not in failed
        assert _post(client, BASE + "/result", signed) == failed
        retained = client.portal.call(
            runtime.event_log.get, failed["failure_receipt_event_id"]
        )
        assert (
            retained["type"] == "openclaw.tool_call.recorded"
            and retained["schema_version"] == 2
        )
        assert retained["owner_scope"] == retained["visibility_scope"] == f"private:{A}"
        assert retained["sensitivity"] == "privileged"
        payload = retained["payload"]
        assert (
            payload["context_scope"] == "member_private"
            and payload["context_member_id"] == A
        )
        assert payload["openclaw_actor_id"] == result["provenance"]["actor_id"]
        assert payload["request_correlation_id"] == "native-worker-error"
        assert payload["result_event_ids"] == payload["observation_event_ids"] == []
        assert payload["causation_event_ids"] == [
            failed["browser_request_event_id"],
            result["follow_up_source_id"],
            result["source_message_event_id"],
        ]
        assert payload["session_key"] is None and payload["session_id"] is None
        assert body["provenance"]["session_key"] not in json.dumps(retained)
        # The worker capture endpoint cannot assert a parent wait result.
        _post(
            client,
            BASE + "/capture",
            {**body, "native_failure": signed["native_failure"]},
            422,
        )
        _post(
            client, BASE + "/result", {**signed, "provenance": body["provenance"]}, 403
        )
        _post(client, BASE + "/result", {**signed, "run_nonce": "a" * 32}, 403)
        # Even another valid admission for this member/source cannot borrow the
        # original worker's signed terminal or change its run identity.
        _post(
            client,
            BASE + "/admit",
            {
                "provenance": result["provenance"],
                "source_message_event_id": result["source_message_event_id"],
                "action": "create",
                "instruction": INSTRUCTION,
                "run_nonce": "a" * 32,
            },
        )
        _post(client, BASE + "/result", {**signed, "run_nonce": "a" * 32}, 403)
        _post(
            client,
            BASE + "/result",
            {**signed, "source_message_event_id": "missing-source"},
            403,
        )
        _post(
            client,
            BASE + "/result",
            {**signed, "native_failure": _native_failure(result, "scratchpad")},
            403,
        )
        _post(
            client,
            BASE + "/result",
            {
                **signed,
                "native_failure": _native_failure(result, "browser", "different-run"),
            },
            409,
        )
        forged = {
            **signed,
            "provenance": {**result["provenance"], "actor_id": "+15555550199"},
        }
        _post(client, BASE + "/result", forged, 403)
        assert runtime.fixture_adapter.delivered == []
    with TestClient(create_application(settings)) as client:
        monkeypatch.setattr(
            client.app.state.runtime.settings_control, "read", lambda: state
        )
        _normal_operation(client)
        current = _body(
            client, "native-browser-error-status", "What happened to that browser read?"
        )
        assert (
            _post(
                client,
                BASE + "/retained",
                {
                    **current,
                    "browser_request_event_id": failed["browser_request_event_id"],
                },
            )
            == failed
        )


def test_verified_browser_capture_wins_over_later_native_worker_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_application(_settings(tmp_path / "instance"))) as client:
        setup_browser(client, monkeypatch)
        body, result, _ = start(client)
        captured = _post(client, BASE + "/capture", body)
        completed = _post(
            client,
            BASE + "/result",
            {**result, "native_failure": _native_failure(result, "browser")},
        )
        assert completed["status"] == "verified"
        assert completed["observation_event_id"] == captured["observation_event_id"]
        assert completed["excerpt"] == body["excerpt"]
        assert _post(client, BASE + "/result", result) == completed


def _native_failure_gateway_backend(instance: Path) -> None:
    """Fixed hosted fixture bridge for the existing real Gateway probe.

    The native plugin supplies the failure signature; this process only routes
    its exact HTTP body into the encrypted application and verifies persistence.
    It never manufactures a worker result or calls a model/provider.
    """
    from dataclasses import replace
    import os
    import sys
    import tempfile

    from adminme.conversation_instructions import input_ticket
    from adminme.openclaw_topology import member_agent_id
    from tests.acceptance.test_authenticated_conversation_channels import (
        provenance_for,
    )
    from tests.acceptance.test_cos_response_execution import HEADERS
    from tests.acceptance.test_forwarded_channel_intake import _events

    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    assert instance.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert instance.parent.name.startswith("adminme-service-gateway-")
    os.umask(0o077)
    initialization = json.loads(sys.stdin.readline(4096))
    secret = initialization["secret"]
    assert isinstance(secret, str) and len(secret) == 64
    settings = replace(
        _settings(instance),
        openclaw_shared_secret=secret,
        member_chat_enabled_members=frozenset({A}),
        deterministic_member_chat_fixture=True,
        openclaw_model_configured=True,
    )

    def emit(value: dict[str, Any]) -> None:
        print("ADMINME_NATIVE_BACKEND " + json.dumps(value), flush=True)

    with pytest.MonkeyPatch.context() as monkeypatch:
        with TestClient(create_application(settings)) as client:
            setup_browser(client, monkeypatch)
            runtime = client.app.state.runtime
            auth = {"Authorization": f"Bearer {secret}"}
            source: dict[str, Any] | None = None
            result_body: dict[str, Any] | None = None
            terminal: dict[str, Any] | None = None

            def instruction(request_id: str, text: str) -> dict[str, Any]:
                response = client.post(
                    "/api/member/v1/chat/stream",
                    headers=HEADERS,
                    json={"request_id": request_id, "message": text},
                )
                assert response.status_code == 200, response.text
                sources = client.portal.call(
                    _events, runtime, "cos.conversation.instruction"
                )
                return next(
                    row for row in sources if row["payload"]["request_id"] == request_id
                )

            emit({"ready": True, "member_agent_id": member_agent_id(A)})
            for line in sys.stdin:
                assert len(line) <= 1_048_576
                command = json.loads(line)
                operation = command["operation"]
                if operation == "prepare":
                    assert source is None
                    text = "PROBE_WORKSTATION_NATIVE_FAILURE: Read the authorized CoS planning page."
                    source = instruction("native-worker-terminal-error", text)
                    result: dict[str, Any] = {
                        "ticket": input_ticket(runtime, source),
                        "text": text,
                        "source_event_id": source["event_id"],
                    }
                elif operation == "request":
                    assert source is not None
                    route, body = command["path"], command["body"]
                    assert route in {BASE + "/admit", BASE + "/result"}
                    assert body["source_message_event_id"] == source["event_id"]
                    response = client.post(route, headers=auth, json=body)
                    result = {"status": response.status_code, "body": response.json()}
                    assert response.status_code == 200, response.text
                    if route.endswith("/result"):
                        assert body["native_failure"]["status"] == "error"
                        result_body, terminal = body, response.json()
                        assert terminal["status"] == "failed"
                        assert terminal["reason"] == "native_worker_failed"
                        assert (
                            terminal["native_run_id"]
                            == body["native_failure"]["run_id"]
                        )
                        assert (
                            "observation_event_id" not in terminal
                            and "excerpt" not in terminal
                        )
                elif operation == "verify":
                    assert (
                        source is not None
                        and result_body is not None
                        and terminal is not None
                    )
                    # Exact native report replay cannot append a second failure.
                    replay = client.post(
                        BASE + "/result", headers=auth, json=result_body
                    )
                    assert replay.status_code == 200 and replay.json() == terminal
                    current = instruction(
                        "native-worker-error-status",
                        "What happened to that browser read?",
                    )
                    response = client.post(
                        BASE + "/retained",
                        headers=auth,
                        json={
                            "provenance": provenance_for(current),
                            "source_message_event_id": current["event_id"],
                            "browser_request_event_id": terminal[
                                "browser_request_event_id"
                            ],
                        },
                    )
                    assert response.status_code == 200 and response.json() == terminal
                    audits = client.portal.call(
                        _events, runtime, "openclaw.tool_call.recorded"
                    )
                    failures = [
                        row
                        for row in audits
                        if row["payload"]["operation"]
                        == "tools.workstation-browser.worker-failed"
                    ]
                    assert len(failures) == 1
                    retained = failures[0]
                    assert retained["event_id"] == terminal["failure_receipt_event_id"]
                    assert (
                        retained["owner_scope"]
                        == retained["visibility_scope"]
                        == f"private:{A}"
                    )
                    assert (
                        retained["schema_version"] == 2
                        and retained["sensitivity"] == "privileged"
                    )
                    payload = retained["payload"]
                    assert (
                        payload["context_scope"] == "member_private"
                        and payload["context_member_id"] == A
                    )
                    assert payload["openclaw_actor_id"] == source["payload"]["actor_id"]
                    assert (
                        payload["request_correlation_id"] == terminal["native_run_id"]
                    )
                    assert payload["causation_event_ids"] == [
                        terminal["browser_request_event_id"],
                        source["event_id"],
                        source["event_id"],
                    ]
                    assert (
                        payload["result_event_ids"]
                        == payload["observation_event_ids"]
                        == []
                    )
                    assert (
                        payload["session_key"] is None and payload["session_id"] is None
                    )
                    assert not client.portal.call(
                        _events, runtime, "cos.workstation.source_observed"
                    )
                    assert runtime.fixture_adapter.delivered == []
                    result = {
                        "verified": True,
                        "failure_receipt_event_id": retained["event_id"],
                        "native_run_id": terminal["native_run_id"],
                        "provider_effect_count": 0,
                    }
                else:
                    raise AssertionError("Unexpected native failure fixture operation")
                emit({"id": command["id"], "result": result})


if __name__ == "__main__":
    import sys

    assert len(sys.argv) == 3 and sys.argv[1] == "--native-worker-failure"
    _native_failure_gateway_backend(Path(sys.argv[2]))
