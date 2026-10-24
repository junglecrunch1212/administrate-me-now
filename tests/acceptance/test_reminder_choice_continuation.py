"""Offered member task -> fresh other-channel answer -> one governed native effect.

The real encrypted runtime, member enrollment and bridge command ledger run in
isolated CI. Only the model HTTP and native EventKit storage are synthetic.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import partial
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import httpx
import pytest

from adminme.lib.skill_runner.fixture import _response
from adminme.lib.skill_runner.wrapper import run_skill
from adminme.runtime.application import create_application
from tests.acceptance.test_authenticated_conversation_channels import (
    gmail_observation,
    portal_instruction,
    provenance_for,
)
from tests.acceptance.test_authenticated_request_actions import (
    configured,
    policy_receipt,
    EXECUTE,
)
from tests.acceptance.test_native_apple_reminder import (
    ACCOUNT,
    AUTH,
    CONFIRM,
    LIST,
    MEMBER_A,
    OPENCLAW_VERSION,
    OPENCLAW_COMMIT,
    _EventKitStore,
    _grant,
    _events,
    _normal_operation,
    _run_dispatch,
    _supervisor,
    grant_services,
    prepare_apple_sources,
    member_agent_id,
    self_reply_session,
)

CHOICE = "/api/openclaw/v1/tools/apple-reminder-choice"


def two_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    import tests.acceptance.test_apple_service_permissions as permissions

    original = permissions._enable

    def enable(helper: Any, capability: str) -> None:
        if capability != "apple_reminders":
            original(helper, capability)
            return
        config = helper.pack_config_for(
            capability,
            source_coordinate=LIST,
            account_coordinate="fixture-apple_reminders-account",
        )
        config["allowed_container_ids"].append("fixture-second-list")
        helper.record_permission(capability, granted=True, pack_config=config)
        helper.run_capability_test(
            capability, lambda *_: {"status": "passed", "content_exposed": False}
        )
        helper.prove_and_start(capability)
        helper.record_observing(capability)

    monkeypatch.setattr(permissions, "_enable", enable)


def instruction(
    client: TestClient, channel: str, identity: str, text: str
) -> dict[str, Any]:
    if channel == "portal":
        return portal_instruction(client, identity, text=text)
    if channel == "gmail":
        original = gmail_observation(client, identity, text=text)
        rows = client.portal.call(
            _events, client.app.state.runtime, "cos.conversation.instruction"
        )
        source = next(
            row for row in rows if row["payload"]["source_message_event_id"] == original
        )
        return {
            "provenance": provenance_for(source),
            "source_message_event_id": source["event_id"],
        }
    actor = "actor-forward-a"
    provenance = {
        "actor_id": actor,
        "agent_id": member_agent_id(MEMBER_A),
        "account_id": ACCOUNT,
        "session_key": self_reply_session(MEMBER_A, ACCOUNT, actor),
        "channel_id": "imessage",
        "message_id": identity,
        "correlation_id": identity,
        "call_kind": "bridge",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }
    response = client.post(
        "/api/openclaw/v1/submissions/forward",
        headers=AUTH,
        json={
            "provenance": provenance,
            "request_id": identity,
            "text": text,
            "consent": True,
            "submission_gesture": "dedicated_intake",
        },
    )
    assert response.status_code == 200, response.text
    return {
        "provenance": provenance,
        "source_message_event_id": response.json()["intent_event_id"],
    }


def model_boundary(
    client: TestClient, *, invalid: bool = False
) -> tuple[Any, list[Any]]:
    runtime = client.app.state.runtime
    calls = []

    def answer(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        inputs = data["args"]["input"]
        if "original_instruction" not in inputs:
            return _response(request)
        calls.append(inputs)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "details": {
                        "json": {
                            "match": "matched",
                            "original_quotes": [
                                {"text": inputs["original_instruction"]}
                            ],
                            "answer_quotes": [
                                {
                                    "text": "fabricated answer"
                                    if invalid
                                    else inputs["answer"]
                                }
                            ],
                        },
                        "provider": "fixture",
                        "model": "reminder-choice-fixture",
                    },
                    "invocation_id": "fixture-reminder-choice",
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    assert runtime.run_skill_fn.func is run_skill
    boundary = replace(
        runtime.run_skill_fn.keywords["_runtime"], httpx_client_factory=lambda: http
    )
    runtime.run_skill_fn = partial(run_skill, _runtime=boundary)
    return http, calls


def offer(
    client: TestClient,
    channel: str,
    suffix: str = "original",
    *,
    title: str = "Call a relative",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    due = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
        hour=13, minute=30, second=0, microsecond=0
    )
    source = instruction(
        client,
        channel,
        suffix,
        f"Add a reminder to my Apple Reminders to {title.lower()} at {due.isoformat()}",
    )
    body = {
        **source,
        "request_id": "fixture-choice-" + suffix,
        "request_authorization": True,
        "title": title,
        "due_at": due.isoformat(),
        "notes": None,
    }
    response = client.post(CONFIRM, headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "list_choice_required"
    assert result["pending_expires_at"] == result["due_at"]
    assert len(result["lists"]) == 2
    return source, body, result


@pytest.mark.parametrize(
    "original_channel,answer_channel,status_channel",
    [
        ("imessage", "gmail", "portal"),
        ("gmail", "portal", "imessage"),
        ("portal", "imessage", "gmail"),
    ],
)
def test_one_task_continues_across_real_channels_with_one_terminal_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original_channel: str,
    answer_channel: str,
    status_channel: str,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    two_lists(monkeypatch)
    store = _EventKitStore()
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        helper, transfer = prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        model, calls = model_boundary(client)
        try:
            original, body, offered = offer(client, original_channel)
            assert calls == []  # Staging is never an execution approval.
            replay = client.post(
                CONFIRM,
                headers=AUTH,
                json={**body, "request_id": "model-restarted-new-id"},
            )
            assert replay.status_code == 200, replay.text
            assert replay.json()["task_id"] == offered["task_id"]
            assert (
                len(client.portal.call(_events, runtime, "cos.reminder.choice_offered"))
                == 1
            )
            selected = next(row for row in offered["lists"] if row["list_id"] == LIST)
            fresh = instruction(
                client, answer_channel, "fresh-choice-answer", selected["label"]
            )
            continuation = {**fresh, "action": "answer", "task_id": offered["task_id"]}
            response = client.post(CHOICE, headers=AUTH, json=continuation)
            assert response.status_code == 200, response.text
            receipt = response.json()
            assert receipt["state"] == "awaiting_request_policy"
            assert receipt["task_id"] == offered["task_id"]
            assert receipt["due_at"] == body["due_at"]
            assert len(calls) == 1
            source = client.portal.call(
                runtime.event_log.get, original["source_message_event_id"]
            )
            assert calls[0]["original_instruction"] == source["payload"]["text"]
            assert (
                calls[0]["original_submitted_at"] == source["payload"]["submitted_at"]
            )
            assert calls[0]["answer"] == selected["label"]
            duplicate = client.post(CHOICE, headers=AUTH, json=continuation)
            assert duplicate.status_code == 200, duplicate.text
            assert (
                duplicate.json()["request_authorization"]
                == receipt["request_authorization"]
            )
            assert len(calls) == 1
            assert (
                client.portal.call(_events, runtime, "external_sync.write_requested")
                == []
            )
            execution = policy_receipt(client, fresh, receipt)
            accepted = client.post(EXECUTE, headers=AUTH, json=execution)
            assert accepted.status_code == 200, accepted.text
            supervisor, transport = _supervisor(
                client, helper, transfer, tmp_path, store
            )
            try:
                _run_dispatch(supervisor)
            finally:
                supervisor._command_ledger.close()
            assert len(store.effects) == len(transport.submitted) == 1
            due = datetime.fromisoformat(body["due_at"])
            assert store.effects[0]["due_components"] == {
                **{
                    field: getattr(due, field)
                    for field in ("year", "month", "day", "hour", "minute", "second")
                },
                "time_zone": "UTC",
                "all_day": False,
                "floating": False,
            }
            current = instruction(
                client,
                status_channel,
                "fresh-task-status",
                "What happened to my reminder?",
            )
            status = client.post(
                CHOICE,
                headers=AUTH,
                json={**current, "action": "status", "task_id": offered["task_id"]},
            )
            assert status.status_code == 200, status.text
            assert status.json()["state"] == "succeeded"
            assert status.json()["task_id"] == offered["task_id"]
            assert status.json()["provider_effect_count"] == 1
            assert (
                client.post(EXECUTE, headers=AUTH, json=execution).json()["duplicate"]
                is True
            )
            assert len(store.effects) == 1
            from adminme.apple_service_history import apple_command_owner_matches
            from adminme.source_assurance import (
                _provider_effect_counts,
                _provider_effect_integrity_healthy,
            )

            command = client.portal.call(
                _events, runtime, "external_sync.write_requested"
            )[-1]
            terminal = client.portal.call(
                _events, runtime, "external_sync.write_completed"
            )[-1]
            assert client.portal.call(
                partial(
                    apple_command_owner_matches,
                    runtime.event_log,
                    command,
                    through=terminal["event_id"],
                )
            )
            counts = _provider_effect_counts(
                [command], {receipt["command_id"]: [terminal]}
            )
            assert _provider_effect_integrity_healthy(counts)
            assert counts["provider_called_true"] == 1
        finally:
            client.portal.call(model.aclose)


def test_ambiguous_pending_answer_cross_member_and_cancel_never_confirm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    two_lists(monkeypatch)
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        prepare_apple_sources(client, tmp_path)
        _grant(client)
        _grant(client, other=True)
        grant_services(client)
        _normal_operation(client)
        _, _, first = offer(client, "portal")
        yes = instruction(client, "imessage", "ambiguous-yes", "Yes")
        refused = client.post(
            CHOICE,
            headers=AUTH,
            json={**yes, "action": "answer", "task_id": first["task_id"]},
        )
        assert refused.status_code == 409
        other = portal_instruction(
            client, "other-member-answer", other=True, text="Personal"
        )
        assert (
            client.post(
                CHOICE,
                headers=AUTH,
                json={**other, "action": "status", "task_id": first["task_id"]},
            ).status_code
            == 404
        )
        _, _, second = offer(client, "gmail", "second", title="Buy groceries")
        multiple = client.post(CHOICE, headers=AUTH, json={**yes, "action": "answer"})
        assert multiple.status_code == 200, multiple.text
        assert multiple.json()["state"] == "task_choice_required"
        assert {row["task_id"] for row in multiple.json()["tasks"]} == {
            first["task_id"],
            second["task_id"],
        }
        cancel = instruction(client, "gmail", "cancel-first", "Cancel this reminder")
        cancelled = client.post(
            CHOICE,
            headers=AUTH,
            json={**cancel, "action": "cancel", "task_id": first["task_id"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["cancelled_before_confirmation"] is True
        assert (
            client.post(
                CHOICE,
                headers=AUTH,
                json={**yes, "action": "answer", "task_id": first["task_id"]},
            ).json()["state"]
            == "cancelled"
        )
        assert (
            client.portal.call(_events, runtime, "member.provider_action.confirmed")
            == []
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )


@pytest.mark.parametrize("failure", ["revoked_list", "expired_pending", "bad_quote"])
def test_changed_authority_or_unproved_answer_never_creates_a_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from tests.acceptance.test_native_apple_reminder import MEMBER_HEADERS
    from tests.acceptance.test_apple_service_permissions import PATH
    import adminme.products.openclaw.reminder_choice_authorization as authorization

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    two_lists(monkeypatch)
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        model, _ = model_boundary(client, invalid=failure == "bad_quote")
        try:
            _, _, offered = offer(client, "portal")
            label = next(
                row["label"] for row in offered["lists"] if row["list_id"] == LIST
            )
            fresh = instruction(client, "imessage", "answer-after-change", label)
            if failure == "revoked_list":
                response = client.post(
                    f"{PATH}/apple_reminders",
                    headers=MEMBER_HEADERS,
                    json={
                        "request_id": "revoke-pending-reminder",
                        "expected_revision": 1,
                        "enabled": False,
                        "scope_ids": [],
                    },
                )
                assert response.status_code == 202, response.text
            elif failure == "expired_pending":

                class AfterDue(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return datetime.fromisoformat(offered["due_at"]).astimezone(tz)

                monkeypatch.setattr(authorization, "datetime", AfterDue)
            result = client.post(
                CHOICE,
                headers=AUTH,
                json={**fresh, "action": "answer", "task_id": offered["task_id"]},
            )
            assert result.status_code == 409, result.text
            assert (
                client.portal.call(_events, runtime, "member.provider_action.confirmed")
                == []
            )
            assert (
                client.portal.call(_events, runtime, "external_sync.write_requested")
                == []
            )
        finally:
            client.portal.call(model.aclose)


@pytest.mark.parametrize(
    "failure", ["omitted_ticket", "expired_ticket", "after_due", "mutated_offer"]
)
def test_choice_domain_cannot_fall_back_or_refresh_its_original_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    import copy
    import adminme.products.openclaw.reminder_choice_authorization as authorization
    from tests.acceptance.test_apple_messages_governed_reply import _allow_once

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    two_lists(monkeypatch)
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        model, _ = model_boundary(client)
        try:
            _, _, offered = offer(client, "portal")
            label = next(
                row["label"] for row in offered["lists"] if row["list_id"] == LIST
            )
            fresh = instruction(client, "imessage", "verified-choice-answer", label)
            response = client.post(
                CHOICE,
                headers=AUTH,
                json={**fresh, "action": "answer", "task_id": offered["task_id"]},
            )
            assert response.status_code == 200, response.text
            receipt = response.json()
            execution = policy_receipt(client, fresh, receipt)
            if failure == "omitted_ticket":
                # A valid legacy fixture receipt still cannot replace this rule.
                execution = {
                    "provenance": fresh["provenance"],
                    "domain_approval_event_id": receipt["domain_approval_event_id"],
                    "openclaw_approval_receipt_id": _allow_once(
                        client,
                        receipt["domain_approval_event_id"],
                        "choice-forbidden-fallback",
                    ),
                }
            elif failure in {"expired_ticket", "after_due"}:
                expired = (
                    receipt["request_authorization"]["expires_at"]
                    if failure == "expired_ticket"
                    else offered["due_at"]
                )

                class Expired(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return datetime.fromisoformat(expired).astimezone(tz)

                monkeypatch.setattr(authorization, "datetime", Expired)
            else:
                original_get = runtime.event_log.get

                async def altered(event_id):
                    event = await original_get(event_id)
                    if event_id == offered["choice_event_id"]:
                        event = copy.deepcopy(event)
                        event["payload"]["title"] = "A different requested action"
                    return event

                monkeypatch.setattr(runtime.event_log, "get", altered)
            refused = client.post(EXECUTE, headers=AUTH, json=execution)
            assert refused.status_code in {403, 409}, refused.text
            assert (
                client.portal.call(_events, runtime, "external_sync.write_requested")
                == []
            )
            status = client.post(
                CHOICE,
                headers=AUTH,
                json={**fresh, "action": "status", "task_id": offered["task_id"]},
            )
            assert status.status_code in {200, 409}, status.text
            assert (
                len(
                    client.portal.call(_events, runtime, "cos.reminder.choice_grounded")
                )
                == 1
            )
        finally:
            client.portal.call(model.aclose)
