"""Authentic three-channel intake -> presented option -> one CoS reminder.

Hosted/disposable only. Model HTTP, native EventKit and native response transport
receipts are synthetic boundaries; intake, retained tasks, signed presentation,
independent decisions, dispatcher and history validation are production code.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import partial
import hashlib
import hmac
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from adminme.events.envelope import EventEnvelope
from adminme.cos_self_replies import self_reply_binding
from adminme.cos_responses import content_fingerprint
from adminme.products.openclaw.request_authorization import canonical
from adminme.reminder_modes import MODE_RULE, mode_origin
from adminme.runtime.application import create_application
from adminme.adapters.cos_reminders_write_policy import write_selection
from adminme.cos_reminders_commands import command_owner_matches
from tests.acceptance.test_reminder_choice_continuation import (
    instruction,
    model_boundary,
)
from tests.acceptance.test_authenticated_request_actions import (
    configured,
    policy_receipt,
)
from tests.acceptance.test_cos_owned_apple_reminder import fake_native, terminal
from tests.acceptance.test_native_apple_reminder import (
    AUTH,
    MEMBER_A,
    _events,
    _grant,
    _normal_operation,
)
from tests.unit.adapters.reminders.test_cos_reminders_write_policy import (
    _configuration_pair,
)

PATH = "/api/openclaw/v1/tools/cos-reminder-choice"
EXECUTE = "/api/openclaw/v1/tools/execute-pending-provider-action"


@contextmanager
def environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, bad_quote: bool = False
):
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    _, selected_config = _configuration_pair(tmp_path / "source")
    selected = write_selection(selected_config)
    native = fake_native(monkeypatch, selected)
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        config = copy.deepcopy(runtime.settings.adapter_config)
        for key in ("cos_apple_reminders", "cos_apple_reminders_writes"):
            config[key] = selected_config[key]
        config["adapters"] += [
            row
            for row in selected_config["adapters"]
            if row["id"] == selected.adapter_id
        ]
        runtime.settings = replace(runtime.settings, adapter_config=config)
        _grant(client)
        _normal_operation(client)
        model, calls = model_boundary(client, invalid=bad_quote)
        try:
            yield client, native, calls
        finally:
            client.portal.call(model.aclose)


def offer(client: TestClient, channel: str, *, suffix: str = "first"):
    due = (datetime.now(timezone(timedelta(hours=-4))) + timedelta(days=1)).replace(
        hour=9, minute=30, second=0, microsecond=0
    )
    text = f"Make yourself an Apple reminder to email me the household brief at {due.isoformat()}, so that it tells you to send it."
    source = instruction(client, channel, "mode-original-" + suffix, text)
    body = {
        **source,
        "action": "offer",
        "title": "Email the household brief",
        "due_at": due.isoformat(),
        "request_id": "mode-request-" + suffix,
    }
    response = client.post(PATH, headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "mode_choice_required"
    assert result["timer_registered"] is False
    return source, body, result


async def response_transport_receipt(runtime, source, offer, receipt_owner=None):
    """Synthetic native transport acknowledgement, not an external send."""
    p = source["provenance"]
    member = MEMBER_A
    now = datetime.now(timezone.utc)
    recipient = (
        "fixture-member@example.invalid"
        if p["channel_id"] == "gmail"
        else p["actor_id"]
    )
    content = content_fingerprint(
        channel="email" if p["channel_id"] == "gmail" else "imessage",
        account_id=p["account_id"],
        recipient=recipient,
        subject="",
        body=offer["presentation_prompt"],
    )
    common = dict(
        event_at_ms=int(now.timestamp() * 1000),
        tenant_id=runtime.settings.instance.tenant_id,
        occurred_at=now.isoformat(),
        source_adapter="openclaw-plugin",
        source_account_id="cos-response",
        owner_scope=f"private:{receipt_owner or member}",
        visibility_scope=f"private:{receipt_owner or member}",
        actor_identity="service-principal:chief-of-staff",
    )
    approval = await runtime.events.write(
        EventEnvelope(
            **common,
            type="cos.response.approved",
            schema_version=4 if p["channel_id"] == "gmail" else 2,
            causation_id=source["source_message_event_id"]
            if p["channel_id"] == "gmail"
            else "fixture-activation",
            payload={
                "contract": "cos-response-plain-text-v1",
                "member_id": member,
                "channel": "email" if p["channel_id"] == "gmail" else "imessage",
                "service_channel_id": "fixture-service-channel",
                "provider_account_id": p["account_id"],
                "recipient": "fixture-member@example.invalid"
                if p["channel_id"] == "gmail"
                else p["actor_id"],
                "subject": "",
                "body": offer["presentation_prompt"],
                "content_fingerprint": content,
                "policy_event_id": "fixture-transport-authority",
                "policy_revision": 1,
                "approved_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=1)).isoformat(),
                **(
                    {
                        "authorization_method": "authenticated_conversation",
                        "conversation_source_event_id": source[
                            "source_message_event_id"
                        ],
                        "thread_id": p["thread_id"],
                        "in_reply_to": "<fixture-mode-source@example.invalid>",
                    }
                    if p["channel_id"] == "gmail"
                    else {
                        "authorization_method": "standing_self_reply",
                        "standing_authorization_event_id": "fixture-activation",
                        "source_message_event_id": source["source_message_event_id"],
                        "self_reply_binding": self_reply_binding(
                            member, p["account_id"], p["actor_id"]
                        ),
                        "reply_index": 0,
                    }
                ),
            },
        )
    )
    return await runtime.events.write(
        EventEnvelope(
            **common,
            type="cos.response.result",
            schema_version=1,
            causation_id=approval,
            payload={
                "contract": "cos-response-plain-text-v1",
                "member_id": member,
                "approval_event_id": approval,
                "content_fingerprint": content,
                "status": "sent",
                "reason": "fixture_native_transport",
                "provider_effect_count": 1,
                "automatic_retry_allowed": False,
                "completed_at": now.isoformat(),
            },
        )
    )


def presentation(
    client: TestClient,
    source,
    offered,
    *,
    body_override: str | None = None,
    bad_signature: bool = False,
    receipt_owner: str | None = None,
    receipt_source=None,
    issued_at_ms: int | None = None,
):
    runtime = client.app.state.runtime
    result_id = (
        None
        if source["provenance"]["channel_id"] == "administrateme-portal"
        else client.portal.call(
            response_transport_receipt,
            runtime,
            receipt_source or source,
            offered,
            receipt_owner,
        )
    )
    issued = int(time.time() * 1000) if issued_at_ms is None else issued_at_ms
    proof = {
        "source_message_event_id": source["source_message_event_id"],
        "offered_event_id": offered["choice_event_id"],
        "body": offered["presentation_prompt"]
        if body_override is None
        else body_override,
        "run_id": "fixture-native-final",
        "response_result_event_id": result_id,
        "issued_at_ms": issued,
        "expires_at_ms": issued + 30000,
        "origin": mode_origin(source["provenance"]).model_dump(mode="json"),
    }
    signature = hmac.new(
        runtime.settings.openclaw_shared_secret.encode(),
        ("cos-reminder-presentation-v1|" + canonical(proof)).encode(),
        hashlib.sha256,
    ).hexdigest()
    del proof["origin"]
    return client.post(
        PATH + "/presented",
        headers=AUTH,
        json={
            **proof,
            "provenance": source["provenance"],
            "signature": "0" * 64 if bad_signature else signature,
        },
    )


@pytest.mark.parametrize("channel", ["imessage", "gmail", "portal"])
def test_presented_number_continues_original_request_once(
    tmp_path, monkeypatch, channel
):
    with environment(tmp_path, monkeypatch) as (client, native, calls):
        runtime = client.app.state.runtime
        source, body, offered = offer(client, channel)
        repeated = client.post(
            PATH,
            headers=AUTH,
            json={**body, "request_id": "model-restart-different-id"},
        )
        assert (
            repeated.status_code == 200
            and repeated.json()["task_id"] == offered["task_id"]
        )
        assert calls == [] and native.saves == 0
        shown = presentation(client, source, offered)
        assert shown.status_code == 200, shown.text
        before_replies = client.portal.call(_events, runtime, "cos.response.approved")
        shown_event = client.portal.call(
            runtime.event_log.get, shown.json()["presentation_event_id"]
        )
        # Reproduce a genuine same-second answer deterministically: intake's
        # second-precision timestamp must not be compared as microsecond-exact.
        submitted = datetime.fromisoformat(
            shown_event["payload"]["presented_at"]
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        with monkeypatch.context() as answer_clock:
            answer_clock.setattr(
                EventEnvelope, "now_utc_iso", staticmethod(lambda: submitted)
            )
            answer = instruction(client, channel, "mode-fresh-answer", "1")
        answer_event = client.portal.call(
            runtime.event_log.get, answer["source_message_event_id"]
        )
        assert answer_event["payload"]["submitted_at"] == submitted
        assert shown_event["event_id"] < answer_event["event_id"]
        request = {**answer, "action": "answer", "task_id": offered["task_id"]}
        confirmed = client.post(PATH, headers=AUTH, json=request)
        assert confirmed.status_code == 200, confirmed.text
        receipt = confirmed.json()
        assert receipt["request_authorization"]["rule_id"] == MODE_RULE
        assert receipt["resource_owner"] == "service-principal:chief-of-staff"
        assert calls[0]["answer"] == "1"
        assert calls[0]["original_instruction"].startswith(
            "Make yourself an Apple reminder"
        )
        assert calls[0]["presented_prompt"] == offered["presentation_prompt"]
        repeated = client.post(PATH, headers=AUTH, json=request)
        assert repeated.status_code == 200, repeated.text
        assert (
            repeated.json()["request_authorization"] == receipt["request_authorization"]
        )
        assert len(calls) == 1 and native.saves == 0
        execution = policy_receipt(client, answer, receipt)
        accepted = client.post(EXECUTE, headers=AUTH, json=execution)
        assert accepted.status_code == 200, accepted.text
        result = client.portal.call(terminal, runtime, receipt["command_id"])
        command = client.portal.call(runtime.event_log.get, accepted.json()["event_id"])
        assert native.saves == 1
        assert native.receipt["observed_due"]["hour"] == 13
        assert client.portal.call(
            partial(
                command_owner_matches,
                runtime.event_log,
                command,
                through=result["event_id"],
            )
        )
        status = client.post(
            PATH,
            headers=AUTH,
            json={**answer, "action": "status", "task_id": offered["task_id"]},
        )
        assert status.status_code == 200 and status.json()["state"] == "succeeded"
        replay = client.post(EXECUTE, headers=AUTH, json=execution)
        assert replay.status_code == 200 and replay.json()["duplicate"]
        assert native.saves == 1
        assert (
            client.portal.call(_events, runtime, "cos.response.approved")
            == before_replies
        )
        assert (
            len(client.portal.call(_events, runtime, "external_sync.write_requested"))
            == 1
        )
        assert client.portal.call(_events, runtime, "cos.reminder.requested") == []
        assert client.portal.call(_events, runtime, "cos.email.request_grounded") == []
        runtime.settings.adapter_config["cos_apple_reminders_writes"]["enabled"] = False
        assert client.portal.call(
            partial(
                command_owner_matches,
                runtime.event_log,
                command,
                through=result["event_id"],
            )
        )


def test_earlier_same_second_answer_cannot_enter_execution_history(
    tmp_path, monkeypatch
):
    from adminme.products.openclaw import reminder_mode_tools
    from adminme.products.openclaw.reminder_choice_authorization import (
        historical_choice_matches,
    )
    from adminme.products.openclaw.reminder_choice_work import selection_snapshot

    with environment(tmp_path, monkeypatch) as (client, native, _):
        runtime = client.app.state.runtime
        source, _, offered = offer(client, "portal")
        early = instruction(client, "portal", "before-mode-presentation", "1")
        early_event = client.portal.call(
            runtime.event_log.get, early["source_message_event_id"]
        )
        offer_event = client.portal.call(
            runtime.event_log.get, offered["choice_event_id"]
        )
        submitted = early_event["payload"]["submitted_at"]
        presented_at = max(
            datetime.fromisoformat(submitted),
            datetime.fromisoformat(offer_event["payload"]["offered_at"]),
        )
        if presented_at.microsecond == 0:
            presented_at += timedelta(microseconds=1)
        assert presented_at.replace(microsecond=0) == datetime.fromisoformat(submitted)

        class PresentationClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return presented_at.astimezone(tz)

        with monkeypatch.context() as clock:
            clock.setattr(reminder_mode_tools, "datetime", PresentationClock)
            shown = presentation(
                client,
                source,
                offered,
                issued_at_ms=int(presented_at.timestamp() * 1000),
            )
        assert shown.status_code == 200, shown.text
        assert early_event["event_id"] < shown.json()["presentation_event_id"]
        rejected = client.post(
            PATH,
            headers=AUTH,
            json={**early, "action": "answer", "task_id": offered["task_id"]},
        )
        assert rejected.status_code == 409, rejected.text

        with monkeypatch.context() as clock:
            clock.setattr(EventEnvelope, "now_utc_iso", staticmethod(lambda: submitted))
            answer = instruction(client, "portal", "after-mode-presentation", "1")
        confirmed = client.post(
            PATH,
            headers=AUTH,
            json={**answer, "action": "answer", "task_id": offered["task_id"]},
        )
        assert confirmed.status_code == 200, confirmed.text
        receipt = confirmed.json()
        grounding_id = receipt["request_authorization"]["grounding_event_id"]
        grounded = client.portal.call(runtime.event_log.get, grounding_id)
        assert client.portal.call(
            historical_choice_matches, runtime.event_log, grounded
        )
        # A fabricated continuation must not substitute the real earlier source,
        # even when it carries the same text and timestamp second.
        earlier = copy.deepcopy(grounded)
        earlier["causation_id"] = early_event["event_id"]
        earlier["payload"]["source_event_id"] = early_event["event_id"]
        snapshot = client.portal.call(
            selection_snapshot,
            runtime.event_log,
            grounded["tenant_id"],
            MEMBER_A,
            early_event,
        )
        earlier["payload"]["continuation"]["selection"] = snapshot.model_dump(
            mode="json"
        )
        assert not client.portal.call(
            historical_choice_matches, runtime.event_log, earlier
        )
        execution = policy_receipt(client, answer, receipt)
        original_get = runtime.event_log.get

        async def substituted_history(event_id):
            return earlier if event_id == grounding_id else await original_get(event_id)

        with monkeypatch.context() as history:
            history.setattr(runtime.event_log, "get", substituted_history)
            rejected = client.post(EXECUTE, headers=AUTH, json=execution)
        assert rejected.status_code in {403, 409}, rejected.text
        assert native.saves == 0
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )


@pytest.mark.parametrize("channel", ["imessage", "gmail", "portal"])
@pytest.mark.parametrize("action", ["status", "answer"])
def test_mode_task_redirects_from_list_choice_without_effects(
    tmp_path, monkeypatch, channel, action
):
    with environment(tmp_path, monkeypatch) as (client, native, calls):
        runtime = client.app.state.runtime
        source, _, offered = offer(client, channel)
        shown = presentation(client, source, offered)
        assert shown.status_code == 200, shown.text
        before_replies = client.portal.call(_events, runtime, "cos.response.approved")
        answer = instruction(client, channel, "wrong-list-tool-" + action, "1")
        result = client.post(
            "/api/openclaw/v1/tools/apple-reminder-choice",
            headers=AUTH,
            json={**answer, "action": action, "task_id": offered["task_id"]},
        )
        assert result.status_code == 200, result.text
        assert result.json() == {
            "state": "different_choice_kind",
            "continuation_tool": "administrateme_cos_reminder_choice",
            "task_id": offered["task_id"],
            "provider_effect_count": 0,
        }
        assert calls == [] and native.saves == 0
        for event_type in (
            "member.provider_action.confirmed",
            "external_sync.write_requested",
            "cos.reminder.requested",
            "cos.email.request_grounded",
        ):
            assert client.portal.call(_events, runtime, event_type) == []
        assert (
            client.portal.call(_events, runtime, "cos.response.approved")
            == before_replies
        )


@pytest.mark.parametrize(
    "failure",
    [
        "unpresented",
        "reordered",
        "bad_signature",
        "other_conversation",
        "before_presentation",
        "expired",
        "revoked_writer",
        "recovery_revocation",
        "observation",
        "bad_quote",
    ],
)
def test_number_cannot_bypass_presentation_or_current_authority(
    tmp_path, monkeypatch, failure
):
    with environment(tmp_path, monkeypatch, bad_quote=failure == "bad_quote") as (
        client,
        native,
        calls,
    ):
        runtime = client.app.state.runtime
        source, _, offered = offer(client, "portal")
        early = (
            instruction(client, "portal", "premature-answer", "1")
            if failure == "before_presentation"
            else None
        )
        if failure != "unpresented":
            body = (
                offered["presentation_prompt"].replace("1. Create", "2. Create")
                if failure == "reordered"
                else None
            )
            shown = presentation(
                client,
                source,
                offered,
                body_override=body,
                bad_signature=failure == "bad_signature",
            )
            if failure in {"reordered", "bad_signature"}:
                assert shown.status_code == 403
            else:
                assert shown.status_code == 200, shown.text
        answer = early or instruction(
            client,
            "gmail" if failure == "other_conversation" else "portal",
            "fresh-mode-negative",
            "1",
        )
        if failure == "revoked_writer":
            runtime.settings.adapter_config["cos_apple_reminders_writes"]["enabled"] = (
                False
            )
        if failure == "recovery_revocation":
            from bootstrap.request_actions import set_disabled

            set_disabled(runtime.settings.instance.config_dir, True)
            set_disabled(runtime.settings.instance.config_dir, False)
        if failure == "observation":
            client.portal.call(
                runtime.observation.enable,
                "operator:fixture",
                "Synthetic activation window",
            )
        if failure == "expired":
            import adminme.products.openclaw.reminder_mode_evidence as evidence

            class AfterDue(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.fromisoformat(offered["due_at"]).astimezone(tz)

            monkeypatch.setattr(evidence, "datetime", AfterDue)
        response = client.post(
            PATH,
            headers=AUTH,
            json={**answer, "action": "answer", "task_id": offered["task_id"]},
        )
        assert response.status_code in {403, 409}, response.text
        assert (
            client.portal.call(_events, runtime, "member.provider_action.confirmed")
            == []
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        assert native.saves == 0


@pytest.mark.parametrize("channel", ["imessage", "gmail", "portal"])
def test_email_choice_stays_unresolved_without_substitution(
    tmp_path, monkeypatch, channel
):
    with environment(tmp_path, monkeypatch) as (client, native, calls):
        runtime = client.app.state.runtime
        source, _, offered = offer(client, channel)
        shown = presentation(client, source, offered)
        assert shown.status_code == 200, shown.text
        answer = instruction(client, channel, "choose-email", "2")
        result = client.post(
            PATH,
            headers=AUTH,
            json={**answer, "action": "answer", "task_id": offered["task_id"]},
        )
        assert result.status_code == 200, result.text
        assert result.json()["state"] == "email_workflow_unresolved"
        assert result.json()["timer_registered"] is False
        assert "request_authorization" not in result.json()
        assert calls == [] and native.saves == 0
        assert (
            client.portal.call(_events, runtime, "member.provider_action.confirmed")
            == []
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )


def test_model_task_reference_cannot_select_between_two_presented_offers(
    tmp_path, monkeypatch
):
    with environment(tmp_path, monkeypatch) as (client, native, calls):
        first_source, _, first = offer(client, "portal", suffix="first")
        assert presentation(client, first_source, first).status_code == 200
        second_source, _, second = offer(client, "portal", suffix="second")
        assert presentation(client, second_source, second).status_code == 200
        answer = instruction(client, "portal", "ambiguous-number", "1")
        result = client.post(
            PATH,
            headers=AUTH,
            json={**answer, "action": "answer", "task_id": first["task_id"]},
        )
        assert (
            result.status_code == 200
            and result.json()["state"] == "task_choice_required"
        )
        assert calls == [] and native.saves == 0
        assert "request_authorization" not in result.json()


def test_same_member_can_explicitly_continue_elsewhere_but_other_member_cannot(
    tmp_path, monkeypatch
):
    from tests.acceptance.test_authenticated_conversation_channels import (
        portal_instruction,
    )

    with environment(tmp_path, monkeypatch) as (client, native, calls):
        source, _, offered = offer(client, "imessage")
        assert presentation(client, source, offered).status_code == 200
        other = portal_instruction(
            client, "other-member-number", other=True, text=offered["task_id"] + ": 1"
        )
        rejected = client.post(
            PATH,
            headers=AUTH,
            json={**other, "action": "answer", "task_id": offered["task_id"]},
        )
        assert rejected.status_code in {403, 404}, rejected.text
        answer = instruction(
            client,
            "gmail",
            "cross-channel-author-reference",
            offered["task_id"] + ": 1",
        )
        result = client.post(
            PATH,
            headers=AUTH,
            json={**answer, "action": "answer", "task_id": offered["task_id"]},
        )
        assert result.status_code == 200, result.text
        assert (
            result.json()["request_authorization"]["source_event_id"]
            == answer["source_message_event_id"]
        )
        assert result.json()["task_id"] == offered["task_id"]
        assert native.saves == 0


@pytest.mark.parametrize("revocation", ["writer", "observation", "recovery"])
def test_current_permission_is_rechecked_after_grounding_before_execution(
    tmp_path, monkeypatch, revocation
):
    from bootstrap.request_actions import set_disabled

    with environment(tmp_path, monkeypatch) as (client, native, calls):
        runtime = client.app.state.runtime
        source, _, offered = offer(client, "portal")
        shown = presentation(client, source, offered)
        assert shown.status_code == 200, shown.text
        answer = instruction(client, "portal", "number-before-change", "1")
        confirmed = client.post(
            PATH,
            headers=AUTH,
            json={**answer, "action": "answer", "task_id": offered["task_id"]},
        )
        assert confirmed.status_code == 200, confirmed.text
        receipt = confirmed.json()
        execution = policy_receipt(client, answer, receipt)
        if revocation == "writer":
            runtime.settings.adapter_config["cos_apple_reminders_writes"]["enabled"] = (
                False
            )
        elif revocation == "observation":
            client.portal.call(
                runtime.observation.enable,
                "operator:fixture",
                "Synthetic activation window",
            )
        else:
            set_disabled(runtime.settings.instance.config_dir, True)
        rejected = client.post(EXECUTE, headers=AUTH, json=execution)
        assert rejected.status_code in {403, 409}, rejected.text
        assert native.saves == 0
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )


def test_fresh_number_does_not_invent_an_offer_from_old_conversation(
    tmp_path, monkeypatch
):
    with environment(tmp_path, monkeypatch) as (client, native, calls):
        instruction(
            client,
            "portal",
            "historical-request-without-offer",
            "Keep a reminder for yourself to email me tomorrow.",
        )
        answer = instruction(client, "imessage", "new-number-only", "1")
        result = client.post(PATH, headers=AUTH, json={**answer, "action": "answer"})
        assert (
            result.status_code == 200
            and result.json()["state"] == "no_pending_reminder_choice"
        )
        assert (
            client.portal.call(
                _events, client.app.state.runtime, "cos.reminder.mode_offered"
            )
            == []
        )
        assert calls == [] and native.saves == 0


@pytest.mark.parametrize("channel", ["imessage", "gmail"])
@pytest.mark.parametrize("mismatch", ["owner", "original_source", "account"])
def test_invalid_sent_receipt_cannot_poison_retained_presentation(
    tmp_path, monkeypatch, channel, mismatch
):
    with environment(tmp_path, monkeypatch) as (client, native, calls):
        runtime = client.app.state.runtime
        source, _, offered = offer(client, channel)
        wrong_source = copy.deepcopy(source)
        if mismatch == "original_source":
            wrong_source["source_message_event_id"] = "unrelated-original-message"
        if mismatch == "account":
            wrong_source["provenance"]["account_id"] = "unrelated-service-account"
        rejected = presentation(
            client,
            source,
            offered,
            receipt_source=wrong_source,
            receipt_owner="member-other" if mismatch == "owner" else None,
        )
        assert rejected.status_code == 403, rejected.text
        assert client.portal.call(_events, runtime, "cos.reminder.mode_presented") == []
        recovered = presentation(client, source, offered)
        assert recovered.status_code == 200, recovered.text
        assert (
            len(client.portal.call(_events, runtime, "cos.reminder.mode_presented"))
            == 1
        )
        assert native.saves == 0 and calls == []
