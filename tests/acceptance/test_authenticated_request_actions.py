"""Real three-channel intake -> independent request policy -> bridge terminal.

Only the Gateway model HTTP and native EventKit storage are synthetic. No human
allow-once callback is fabricated; the host decision uses its distinct contract.
"""

from __future__ import annotations

from dataclasses import replace
from functools import partial
import json
from pathlib import Path
import time
from typing import Any

from fastapi.testclient import TestClient
import httpx
import pytest

from adminme.apple_service_permissions import digest
from adminme.lib.skill_runner.fixture import _response
from adminme.lib.skill_runner.wrapper import run_skill
from adminme.openclaw_approvals import sign_approval_receipt
from adminme.products.openclaw.request_authorization import (
    canonical_evidence,
    evidence_matches,
    fingerprint,
)
from adminme.runtime.application import create_application
from tests.acceptance.test_authenticated_conversation_channels import (
    gmail_observation,
    portal_instruction,
    provenance_for,
)
from tests.acceptance.test_native_apple_reminder import (
    AUTH,
    CONFIRM,
    COMPONENTS,
    MEMBER_A,
    MEMBER_B,
    OPENCLAW_COMMIT,
    OPENCLAW_VERSION,
    SECRET,
    _EventKitStore,
    _body,
    _events,
    _grant,
    _instruction,
    _normal_operation,
    _run_dispatch,
    _settings,
    _status,
    _supervisor,
    grant_services,
    prepare_apple_sources,
)

EXECUTE = "/api/openclaw/v1/tools/execute-pending-provider-action"
TOOL = "administrateme_execute_pending_provider_action"


def configured(tmp_path: Path) -> Any:
    settings = _settings(tmp_path)
    return replace(
        settings,
        member_chat_enabled_members=frozenset({MEMBER_A, MEMBER_B}),
        deterministic_member_chat_fixture=True,
        openclaw_model_configured=True,
        instance=replace(settings.instance, timezone="America/New_York"),
    )


def model_boundary(
    client: TestClient,
    *,
    match: str = "matched",
    wrong_span: bool = False,
    malformed: bool = False,
    quotes: list[dict[str, Any]] | None = None,
) -> tuple[Any, list[Any]]:
    runtime = client.app.state.runtime
    calls: list[Any] = []

    def answer(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        inputs = data["args"]["input"]
        if "instruction" not in inputs:
            return _response(request)
        calls.append(inputs)
        assert 'root keys: "match" and "evidence"' in data["args"]["prompt"]
        assert "{{" not in data["args"]["prompt"]
        if malformed:
            # Exact public response from pinned llm-task when model output is
            # {"result": ...}; native proof exercises its actual schema rejection.
            return httpx.Response(
                500,
                json={
                    "ok": False,
                    "error": {"type": "tool_error", "message": "tool execution failed"},
                },
            )
        text = inputs["instruction"]
        evidence = (
            quotes
            if quotes is not None
            else [{"text": "Wrong evidence" if wrong_span else text}]
        )
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "details": {
                        "json": {
                            "match": match,
                            "evidence": evidence if match == "matched" else [],
                        },
                        "provider": "fixture",
                        "model": "request-grounding-fixture",
                    },
                    "invocation_id": "fixture-request-grounding",
                },
            },
        )

    model_client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    assert runtime.run_skill_fn.func is run_skill
    seam = replace(
        runtime.run_skill_fn.keywords["_runtime"],
        httpx_client_factory=lambda: model_client,
    )
    runtime.run_skill_fn = partial(run_skill, _runtime=seam)
    return model_client, calls


def source_for(client: TestClient, channel: str, suffix: str) -> dict[str, Any]:
    if channel == "imessage":
        return _instruction(client, suffix)
    if channel == "portal":
        return portal_instruction(client, suffix)
    original = gmail_observation(client, suffix)
    sources = client.portal.call(
        _events, client.app.state.runtime, "cos.conversation.instruction"
    )
    source = next(
        row for row in sources if row["payload"]["source_message_event_id"] == original
    )
    return {
        "provenance": provenance_for(source),
        "source_message_event_id": source["event_id"],
    }


def policy_receipt(
    client: TestClient,
    source: dict[str, Any],
    receipt: dict[str, Any],
    *,
    suffix: str = "once",
) -> dict[str, Any]:
    ticket = receipt["request_authorization"]
    params = {
        "domain_approval_event_id": receipt["domain_approval_event_id"],
        "request_authorization_event_id": ticket["grounding_event_id"],
    }
    issued = int(time.time() * 1000)
    authorization = {
        "source_event_id": ticket["source_event_id"],
        "request_authorization_event_id": ticket["grounding_event_id"],
        "domain_approval_event_id": ticket["domain_approval_event_id"],
        "action_fingerprint": ticket["action_fingerprint"],
        "rule_id": ticket["rule_id"],
    }
    signed = {
        "receipt_id": f"ocreq_fixture-{suffix}",
        "tool_name": TOOL,
        "request_fingerprint": fingerprint(params),
        "issued_at_ms": issued,
        "expires_at_ms": issued + 120000,
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
        "authorization": authorization,
    }
    response = client.post(
        "/api/openclaw/v1/approvals/authenticated-request",
        headers=AUTH,
        json={
            **signed,
            "decision": "authenticated_request",
            "signature": sign_approval_receipt(SECRET, **signed),
        },
    )
    assert response.status_code == 200, response.text
    return {
        **params,
        "provenance": source["provenance"],
        "openclaw_approval_receipt_id": signed["receipt_id"],
    }


@pytest.mark.parametrize("channel", ["imessage", "portal", "gmail"])
def test_three_conversations_complete_exact_request_without_manual_allow_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    store = _EventKitStore()
    settings = configured(tmp_path)
    with TestClient(
        create_application(settings), base_url="https://central-fixture.ts.net"
    ) as client:
        runtime = client.app.state.runtime
        helper, transfer = prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        model, model_calls = model_boundary(client)
        try:
            source = source_for(client, channel, f"request-policy-{channel}")
            body = {**_body(source), "notes": None, "request_authorization": True}
            confirmed = client.post(CONFIRM, headers=AUTH, json=body)
            assert confirmed.status_code == 200, confirmed.text
            receipt = confirmed.json()
            grounded = client.portal.call(
                runtime.event_log.get,
                receipt["request_authorization"]["grounding_event_id"],
            )
            assert grounded["actor_identity"] == "service-principal:chief-of-staff"
            assert grounded["payload"]["member_id"] == MEMBER_A
            assert receipt["state"] == "awaiting_request_policy"
            assert (
                len(model_calls) == 1
                and model_calls[0]["action"]["resource_owner"] == "requester"
            )
            assert (
                client.portal.call(_events, runtime, "external_sync.write_requested")
                == []
            )
            replay = client.post(CONFIRM, headers=AUTH, json=body)
            assert replay.status_code == 200, replay.text
            assert replay.json()["state"] == "awaiting_request_policy"
            assert (
                replay.json()["request_authorization"]
                == receipt["request_authorization"]
            )
            assert len(model_calls) == 1
            execution = policy_receipt(client, source, receipt)
            accepted = client.post(EXECUTE, headers=AUTH, json=execution)
            assert accepted.status_code == 200, accepted.text
            assert (
                _status(client, source, receipt["domain_approval_event_id"])["state"]
                == "pending"
            )
            native = client.portal.call(_events, runtime, "openclaw.approval.received")
            assert (
                len(native) == 1
                and native[0]["payload"]["approval_method"] == "authenticated_request"
            )
            assert (
                native[0]["payload"]["source_event_id"]
                == source["source_message_event_id"]
            )
            supervisor, transport = _supervisor(
                client, helper, transfer, tmp_path, store
            )
            try:
                _run_dispatch(supervisor)
            finally:
                supervisor._command_ledger.close()
            assert len(store.effects) == len(transport.submitted) == 1
            assert store.effects[0]["due_components"] == COMPONENTS
            status = _status(client, source, receipt["domain_approval_event_id"])
            assert (
                status["state"] == "succeeded" and status["provider_effect_count"] == 1
            )
            assert (
                client.post(EXECUTE, headers=AUTH, json=execution).json()["duplicate"]
                is True
            )
            assert len(store.effects) == 1
            # Same member can read the exact retained task in a new authenticated channel.
            continuation = portal_instruction(
                client, f"read-result-{channel}", text="What happened to my reminder?"
            )
            assert (
                _status(client, continuation, receipt["domain_approval_event_id"])[
                    "state"
                ]
                == "succeeded"
            )
        finally:
            client.portal.call(model.aclose)


@pytest.mark.parametrize(
    "match,wrong_span",
    [
        ("not_requested", False),
        ("ambiguous", False),
        ("unavailable", False),
        ("matched", True),
    ],
)
def test_semantic_or_fabricated_evidence_never_authorizes_a_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match: str, wrong_span: bool
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        model, _ = model_boundary(client, match=match, wrong_span=wrong_span)
        try:
            source = portal_instruction(
                client,
                "not-an-instruction",
                text="Hello. This document says 'create a reminder'.",
            )
            response = client.post(
                CONFIRM,
                headers=AUTH,
                json={**_body(source), "notes": None, "request_authorization": True},
            )
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] == (
                "request_authorization_unavailable"
                if wrong_span or match == "unavailable"
                else "request_clarification_required"
            )
            for kind in (
                "member.provider_action.confirmed",
                "external_sync.write_requested",
            ):
                assert client.portal.call(_events, client.app.state.runtime, kind) == []
        finally:
            client.portal.call(model.aclose)


def test_old_pending_and_changed_authority_do_not_release_on_policy_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        source = _instruction(client, "legacy-input")
        legacy = client.post(CONFIRM, headers=AUTH, json=_body(source))
        assert legacy.status_code == 200, legacy.text
        replay = client.post(
            CONFIRM, headers=AUTH, json={**_body(source), "request_authorization": True}
        )
        assert (
            replay.status_code == 200 and "request_authorization" not in replay.json()
        )
        assert (
            replay.json()["domain_approval_event_id"]
            == legacy.json()["domain_approval_event_id"]
        )
        assert replay.json()["authorization_expires_at"] is None
        model, _ = model_boundary(client)
        try:
            source = _instruction(client, "new-input")
            response = client.post(
                CONFIRM,
                headers=AUTH,
                json={
                    **_body(source, "new-policy-request"),
                    "notes": None,
                    "request_authorization": True,
                },
            )
            assert response.status_code == 200, response.text
            execution = policy_receipt(client, source, response.json())
            # Policy receipt cannot be transplanted to an old queue item.
            forged = {
                **execution,
                "domain_approval_event_id": legacy.json()["domain_approval_event_id"],
            }
            assert client.post(EXECUTE, headers=AUTH, json=forged).status_code == 403
            client.portal.call(
                client.app.state.runtime.observation.enable,
                "operator:fixture",
                "Test current observation",
            )
            assert client.post(EXECUTE, headers=AUTH, json=execution).status_code == 409
            assert (
                client.portal.call(
                    _events, client.app.state.runtime, "external_sync.write_requested"
                )
                == []
            )
        finally:
            client.portal.call(model.aclose)


@pytest.mark.parametrize("reenable", [False, True])
def test_schema_aware_revocation_stops_queued_policy_actions_but_preserves_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reenable: bool
) -> None:
    from bootstrap.request_actions import set_disabled
    from tests.acceptance.test_native_apple_reminder import _execute

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
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
        model, _ = model_boundary(client)
        try:
            source = _instruction(client, "queued-request-input")
            response = client.post(
                CONFIRM,
                headers=AUTH,
                json={
                    **_body(source, "queued-request-action"),
                    "notes": None,
                    "request_authorization": True,
                },
            )
            assert response.status_code == 200, response.text
            receipt = response.json()
            execution = policy_receipt(client, source, receipt, suffix="recovery")
            assert client.post(EXECUTE, headers=AUTH, json=execution).status_code == 200
            set_disabled(runtime.settings.instance.config_dir, True)
            if reenable:
                set_disabled(runtime.settings.instance.config_dir, False)
            supervisor, transport = _supervisor(
                client, helper, transfer, tmp_path, store
            )
            command = client.portal.call(
                _events, runtime, "external_sync.write_requested"
            )[0]
            denied = client.post(
                f"/api/bridge/commands/{command['payload']['command_id']}/apple-authorization",
                headers=transport.headers,
                json={
                    "event_id": command["event_id"],
                    "configuration_sha256": digest(supervisor._config.pack_config),
                },
            )
            assert denied.status_code == 409, denied.text
            try:
                _run_dispatch(supervisor)
            finally:
                supervisor._command_ledger.close()
            assert store.effects == []
            assert len(transport.submitted) == 1
            assert transport.submitted[0]["payload"]["provider_called"] is False
            assert (
                _status(client, source, receipt["domain_approval_event_id"])["state"]
                == "failed"
            )
            # Old history remains readable and unchanged manual-authority flows
            # remain operational even while the new feature is revoked.
            set_disabled(runtime.settings.instance.config_dir, True)
            legacy_source = _instruction(client, "legacy-after-revocation")
            legacy = client.post(
                CONFIRM,
                headers=AUTH,
                json=_body(legacy_source, "legacy-recovery-action"),
            )
            assert legacy.status_code == 200, legacy.text
            legacy_id = legacy.json()["domain_approval_event_id"]
            accepted = _execute(
                client, legacy_id, "legacy-recovery-approval", receipt=True
            )
            assert accepted.status_code == 200, accepted.text
            supervisor, _ = _supervisor(client, helper, transfer, tmp_path, store)
            try:
                _run_dispatch(supervisor)
            finally:
                supervisor._command_ledger.close()
            assert len(store.effects) == 1
            assert _status(client, legacy_source, legacy_id)["state"] == "succeeded"
        finally:
            client.portal.call(model.aclose)


def test_malformed_grounding_keeps_original_failure_without_provider_or_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        model, calls = model_boundary(client, malformed=True)
        try:
            source = _instruction(client, "malformed-grounding-input")
            body = {**_body(source), "notes": None, "request_authorization": True}
            for _ in range(2):
                response = client.post(CONFIRM, headers=AUTH, json=body)
                assert response.status_code == 409, response.text
                assert (
                    response.json()["detail"]["code"]
                    == "request_authorization_unavailable"
                )
            assert len(calls) == 1
            failed = [
                event
                for event in client.portal.call(_events, runtime, "skill.call.failed")
                if event["payload"]["skill_name"] == "skill:ground_requested_action"
            ]
            assert len(failed) == 1
            assert failed[0]["payload"]["skill_version"] == "1.0.2"
            assert failed[0]["payload"]["failure_class"] == "openclaw_unreachable"
            for kind in (
                "cos.action.request_grounded",
                "member.provider_action.confirmed",
                "external_sync.write_requested",
                "external_sync.write_completed",
                "external.sent",
            ):
                assert client.portal.call(_events, runtime, kind) == []
        finally:
            client.portal.call(model.aclose)


@pytest.mark.parametrize("malformed", [False, True])
def test_followup_cannot_rebind_failed_invocation_or_escape_as_server_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, malformed: bool
) -> None:
    """A genuine new 'Yes' cannot reuse a failed original action identity."""
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        model, calls = model_boundary(client, malformed=malformed, wrong_span=True)
        try:
            source = _instruction(client, "original-failed-request")
            original = {**_body(source), "notes": None, "request_authorization": True}
            result = client.post(CONFIRM, headers=AUTH, json=original)
            assert result.status_code == 409, result.text
            assert (
                result.json()["detail"]["code"] == "request_authorization_unavailable"
            )

            def grounding_receipts(kind: str) -> list[dict[str, Any]]:
                return [
                    event
                    for event in client.portal.call(_events, runtime, kind)
                    if event["payload"]["skill_name"] == "skill:ground_requested_action"
                ]

            before = {
                kind: grounding_receipts(kind)
                for kind in ("skill.call.recorded", "skill.call.failed")
            }
            assert len(before["skill.call.recorded"]) == 1
            assert len(before["skill.call.failed"]) == int(malformed)
            followup = _instruction(client, "new-authenticated-followup", text="Yes")
            assert (
                followup["source_message_event_id"] != source["source_message_event_id"]
            )
            for current in (followup, followup, source):
                response = client.post(
                    CONFIRM, headers=AUTH, json={**original, **current}
                )
                assert response.status_code == 409, response.text
                assert (
                    response.json()["detail"]["code"]
                    == "request_authorization_unavailable"
                )
            assert len(calls) == 1
            for kind, retained in before.items():
                assert grounding_receipts(kind) == retained
            for kind in (
                "cos.action.request_grounded",
                "member.provider_action.confirmed",
                "external_sync.write_requested",
                "external_sync.write_completed",
                "external.sent",
            ):
                assert client.portal.call(_events, runtime, kind) == []
        finally:
            client.portal.call(model.aclose)


_QUOTE_INSTRUCTION = (
    'Add a reminder to my Apple reminders titled "COS imessage retry" '
    "for tomorrow at 9:30 AM Eastern"
)
_QUOTE_EVIDENCE = [
    {"text": "Add a reminder to my Apple reminders"},
    {"text": "COS imessage retry"},
    {"text": "tomorrow at 9:30 AM Eastern"},
]
_CANONICAL_EVIDENCE = [
    {"start": 0, "end": 36, "text": "Add a reminder to my Apple reminders"},
    {"start": 45, "end": 63, "text": "COS imessage retry"},
    {"start": 69, "end": 96, "text": "tomorrow at 9:30 AM Eastern"},
]


def test_quote_only_grounding_reaches_original_apple_effect_with_canonical_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce the actual quote/count mismatch at the production HTTP seam."""
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
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
        model, calls = model_boundary(client, quotes=_QUOTE_EVIDENCE)
        try:
            source = _instruction(
                client, "quote-evidence-repro", text=_QUOTE_INSTRUCTION
            )
            body = {
                **_body(source),
                "notes": None,
                "request_authorization": True,
                "title": "COS imessage retry",
                "due_at": "2026-10-25T09:30:00-04:00",
            }
            confirmed = client.post(CONFIRM, headers=AUTH, json=body)
            assert confirmed.status_code == 200, confirmed.text
            receipt = confirmed.json()
            ticket = receipt["request_authorization"]
            assert ticket["evidence"] == _CANONICAL_EVIDENCE
            assert ticket["action"]["resource_owner"] == "requester"
            assert ticket["action"]["due_at"] == "2026-10-25T13:30:00+00:00"
            retained = client.portal.call(
                runtime.event_log.get, ticket["grounding_event_id"]
            )
            assert retained["schema_version"] == 1
            assert retained["payload"]["evidence"] == _CANONICAL_EVIDENCE
            assert (
                client.post(CONFIRM, headers=AUTH, json=body).json()[
                    "request_authorization"
                ]
                == ticket
            )
            assert len(calls) == 1
            execution = policy_receipt(client, source, receipt)
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
            assert store.effects[0]["due_components"] == {**COMPONENTS, "day": 25}
            assert (
                _status(client, source, receipt["domain_approval_event_id"])["state"]
                == "succeeded"
            )
            assert (
                client.post(EXECUTE, headers=AUTH, json=execution).json()["duplicate"]
                is True
            )
            assert len(store.effects) == 1
        finally:
            client.portal.call(model.aclose)


@pytest.mark.parametrize(
    "quotes",
    [
        [{"text": "invented"}],
        [{"text": "reminder"}],  # Appears in both reminder and reminders.
        [_QUOTE_EVIDENCE[0], _QUOTE_EVIDENCE[0]],
        [{"text": _QUOTE_INSTRUCTION}, _QUOTE_EVIDENCE[1]],
        [{"start": 0, "end": 40, "text": _QUOTE_EVIDENCE[0]["text"]}],
    ],
)
def test_unusable_model_quotes_are_system_failure_not_account_ambiguity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quotes: list[dict[str, Any]]
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        prepare_apple_sources(client, tmp_path)
        _grant(client)
        grant_services(client)
        _normal_operation(client)
        model, calls = model_boundary(client, quotes=quotes)
        try:
            source = _instruction(client, "unusable-quotes", text=_QUOTE_INSTRUCTION)
            body = {**_body(source), "notes": None, "request_authorization": True}
            for _ in range(2):
                result = client.post(CONFIRM, headers=AUTH, json=body)
                assert result.status_code == 409, result.text
                assert (
                    result.json()["detail"]["code"]
                    == "request_authorization_unavailable"
                )
            assert len(calls) == 1, (
                "Same-ID recovery must not repeat a cached bad extraction"
            )
            for kind in (
                "cos.action.request_grounded",
                "member.provider_action.confirmed",
                "external_sync.write_requested",
                "external.sent",
            ):
                assert client.portal.call(_events, runtime, kind) == []
        finally:
            client.portal.call(model.aclose)


@pytest.mark.parametrize(
    "text,quotes,expected",
    [
        (_QUOTE_INSTRUCTION, list(reversed(_QUOTE_EVIDENCE)), _CANONICAL_EVIDENCE),
        ("🙂 Add café", [{"text": "café"}], [{"start": 6, "end": 10, "text": "café"}]),
        (
            "👩🏽\u200d💻 call",
            [{"text": "call"}],
            [{"start": 5, "end": 9, "text": "call"}],
        ),
        ("Add cafe\u0301", [{"text": "café"}], None),
        ("banana", [{"text": "ana"}], None),
        ("quoted text", [{"text": "quoted", "start": 0}], None),
        ("only spaces", [{"text": " "}], None),
        ("only text", [], None),
    ],
)
def test_quote_spans_are_exact_unicode_and_reject_ambiguous_or_legacy_model_shapes(
    text: str, quotes: Any, expected: Any
) -> None:
    assert canonical_evidence(text, quotes) == expected
    # Existing signed/retained canonical span receipts retain their validator.
    if expected is not None:
        assert evidence_matches(text, expected)
    assert evidence_matches(_QUOTE_INSTRUCTION, _CANONICAL_EVIDENCE)
