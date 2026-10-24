"""Three genuine conversation origins -> one CoS-owned native save and history.

Only the model HTTP boundary and native helper/EventKit store are synthetic.
The application, authenticated intake, independent request receipts, durable
command consumer, current admission and encrypted history use production code.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from functools import partial
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.adapters import cos_reminders_write as native
from adminme.adapters.cos_reminders_helper import NativeWriteResult
from adminme.adapters.cos_reminders_write_policy import RULE_ID, write_selection
from adminme.cos_reminders_commands import CoSReminderIntent, command_owner_matches
from adminme.historical_attribution import audit_historical_attribution
from adminme.runtime import cos_reminders
from adminme.runtime.application import create_application
from adminme.source_assurance import (
    _provider_effect_counts,
    _provider_effect_integrity_healthy,
)
from tests.acceptance.test_authenticated_conversation_channels import (
    gmail_observation,
    portal_instruction,
    provenance_for,
)
from tests.acceptance.test_authenticated_request_actions import (
    configured,
    model_boundary,
    policy_receipt,
)
from tests.acceptance.test_native_apple_reminder import (
    AUTH,
    MEMBER_A,
    _events,
    _grant,
    _normal_operation,
)
from tests.acceptance.test_forwarded_channel_intake import ACCOUNT
from adminme.openclaw_contract import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.openclaw_topology import member_agent_id
from adminme.cos_self_replies import self_reply_session
from tests.unit.adapters.reminders.test_cos_reminders_write_policy import (
    _configuration_pair,
)

CONFIRM = "/api/openclaw/v1/tools/cos-apple-reminders/confirm"
STATUS = "/api/openclaw/v1/tools/cos-apple-reminders/status"
EXECUTE = "/api/openclaw/v1/tools/execute-pending-provider-action"
ROOT = Path(__file__).resolve().parents[2]
COS = "service-principal:chief-of-staff"


def source_for(client: TestClient, channel: str, text: str) -> dict[str, Any]:
    if channel == "portal":
        return portal_instruction(client, "own-apple-portal", text=text)
    if channel == "gmail":
        parent = gmail_observation(client, "own-apple-gmail", text=text)
        rows = client.portal.call(
            _events, client.app.state.runtime, "cos.conversation.instruction"
        )
        source = next(
            row for row in rows if row["payload"]["source_message_event_id"] == parent
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
        "message_id": "own-apple-native",
        "correlation_id": "own-apple-native",
        "call_kind": "bridge",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }
    response = client.post(
        "/api/openclaw/v1/submissions/forward",
        headers=AUTH,
        json={
            "provenance": provenance,
            "request_id": "own-apple-native",
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


def fake_native(monkeypatch: pytest.MonkeyPatch, selected: Any) -> Any:
    state = SimpleNamespace(saves=0, receipt=None)
    calendar = SimpleNamespace(allowsContentModifications=lambda: True)
    store = object()

    def selected_calendar(selection):
        assert selection == selected
        return calendar

    reader = SimpleNamespace(
        _require_permission=lambda: None, _calendar=selected_calendar, _store=store
    )

    class Store:
        def __init__(self):
            pytest.fail("Generic EventKit construction would request permission")

        def build_reminder(self, *, list_identifier, title, notes, due_at, priority):
            self._calendar(list_identifier)
            assert self._store is store and priority == 0
            self.record = SimpleNamespace(
                calendarItemIdentifier="native-cos-reminder",
                list_identifier=list_identifier,
                title=title,
                notes=notes,
                completed=False,
                due_components={
                    **{
                        k: getattr(due_at, k)
                        for k in ("year", "month", "day", "hour", "minute", "second")
                    },
                    "time_zone": "UTC",
                    "floating": False,
                    "all_day": False,
                },
            )
            return self.record

        def save(self, reminder, *, commit):
            assert commit and reminder is self.record
            state.saves += 1

        def reminder_with_identifier(self, identity):
            assert identity == self.record.calendarItemIdentifier
            return self.record

    monkeypatch.setattr(
        native, "_shim", lambda: SimpleNamespace(PyObjCEventStore=Store)
    )

    class Helper:
        def create(self, selection, command):
            result = native.NativeRemindersWriter(reader=reader).create(
                selection, command
            )
            state.receipt = result
            return NativeWriteResult.model_validate_json(json.dumps(result))

    monkeypatch.setattr(cos_reminders, "HelperRemindersWriter", Helper)
    return state


async def terminal(runtime, command_id):
    async with asyncio.timeout(5):
        while True:
            result = await runtime.event_log.get_by_idempotency(
                runtime.settings.instance.tenant_id,
                f"external-sync-result:{command_id}",
            )
            if result is not None:
                return result
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("channel", ["imessage", "portal", "gmail"])
@pytest.mark.parametrize("suppressed", [False, True])
def test_current_conversation_owns_receipt_and_cos_owns_native_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str, suppressed: bool
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    _, selected_config = _configuration_pair(tmp_path / "source")
    selected = write_selection(selected_config)
    native_state = fake_native(monkeypatch, selected)
    allow_dispatch = Event()
    real_dispatch = cos_reminders.dispatch

    async def bounded_dispatch(runtime, command):
        assert await asyncio.to_thread(allow_dispatch.wait, 5)
        await real_dispatch(runtime, command)

    monkeypatch.setattr(cos_reminders, "dispatch", bounded_dispatch)
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        # Simulate the exact supported configuration activation, keeping all
        # existing channel/member configuration; no native reader is started.
        config = copy.deepcopy(runtime.settings.adapter_config)
        config.update(
            {
                key: selected_config[key]
                for key in ("cos_apple_reminders", "cos_apple_reminders_writes")
            }
        )
        config["adapters"] += [
            row
            for row in selected_config["adapters"]
            if row["id"] == selected.adapter_id
        ]
        runtime.settings = replace(runtime.settings, adapter_config=config)
        read_before = copy.deepcopy(config["cos_apple_reminders"])
        _grant(client)
        _normal_operation(client)
        model, calls = model_boundary(client)
        try:
            due = (
                datetime.now(timezone(timedelta(hours=-4))) + timedelta(days=1)
            ).replace(hour=9, minute=30, second=0, microsecond=0)
            text = f"Add a reminder to your own Apple work list to prepare the household brief at {due.isoformat()}."
            source = source_for(client, channel, text)
            body = {
                **source,
                "request_id": "own-apple-first",
                "title": "Prepare the household brief",
                "due_at": due.isoformat(),
                "request_authorization": True,
            }
            response = client.post(CONFIRM, headers=AUTH, json=body)
            assert response.status_code == 200, response.text
            receipt = response.json()
            assert (
                receipt["resource_owner"] == COS
                and receipt["state"] == "awaiting_request_policy"
            )
            assert receipt["request_authorization"]["rule_id"] == RULE_ID
            assert calls[0]["action"]["resource_owner"] == COS
            again = client.post(CONFIRM, headers=AUTH, json=body)
            assert again.status_code == 200, again.text
            assert (
                again.json()["request_authorization"]
                == receipt["request_authorization"]
            )
            assert len(calls) == 1
            execution = policy_receipt(client, source, receipt)
            accepted = client.post(EXECUTE, headers=AUTH, json=execution)
            assert accepted.status_code == 200, accepted.text
            if suppressed:
                client.portal.call(
                    runtime.observation.enable,
                    "operator:fixture",
                    "Synthetic activation window",
                )
            allow_dispatch.set()
            result = client.portal.call(terminal, runtime, receipt["command_id"])
            command = client.portal.call(
                runtime.event_log.get, accepted.json()["event_id"]
            )
            assert client.portal.call(
                partial(
                    command_owner_matches,
                    runtime.event_log,
                    command,
                    through=result["event_id"],
                )
            )
            assert native_state.saves == (0 if suppressed else 1)
            effects = client.portal.call(
                _events,
                runtime,
                "observation.suppressed" if suppressed else "external.sent",
            )
            effect = next(
                row
                for row in effects
                if row["payload"].get("initiating_event_id") == command["event_id"]
            )
            assert effect["actor_identity"] == COS
            assert effect["owner_scope"] == f"private:{MEMBER_A}"
            assert effect["payload"]["provider_called"] is (not suppressed)
            assert (
                effect["payload"]["domain_approval_event_id"]
                == receipt["domain_approval_event_id"]
            )
            assert (
                effect["payload"]["provider_account_id"] == selected.provider_account_id
            )
            counts = _provider_effect_counts(
                [command], {receipt["command_id"]: [result]}
            )
            assert _provider_effect_integrity_healthy(counts)
            assert counts["provider_called_true"] == (0 if suppressed else 1)
            status = client.post(
                STATUS,
                headers=AUTH,
                json={
                    **source,
                    "domain_approval_event_id": receipt["domain_approval_event_id"],
                },
            )
            assert status.status_code == 200, status.text
            assert status.json()["state"] == (
                "suppressed" if suppressed else "succeeded"
            )
            if not suppressed:
                assert native_state.receipt["observed_due"]["hour"] == 13
                replay = client.post(EXECUTE, headers=AUTH, json=execution)
                assert replay.status_code == 200 and replay.json()["duplicate"]
                client.portal.call(runtime._dispatch_external_write, command)
                assert native_state.saves == 1
            config["cos_apple_reminders_writes"]["enabled"] = False
            assert config["cos_apple_reminders"] == read_before
            history = client.portal.call(
                partial(
                    audit_historical_attribution,
                    runtime.event_log,
                    repository_root=ROOT,
                    provider_bindings=(),
                    hash_key=b"h" * 32,
                )
            )
            assert any(
                row["binding_state"] == "internal_cos_owned_reminder_source_reconciled"
                and row["event_count"] == 1
                for row in history["groups"]
            )
            intent = CoSReminderIntent.model_validate(command["payload"]["payload"])
            with pytest.raises(PermissionError):
                intent.require_current(config)
            assert client.portal.call(
                partial(
                    command_owner_matches,
                    runtime.event_log,
                    command,
                    through=result["event_id"],
                )
            )
        finally:
            allow_dispatch.set()
            client.portal.call(model.aclose)
