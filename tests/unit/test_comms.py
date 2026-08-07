"""Deterministic Comms cockpit and account-capability tests."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.comms import compose_comms_cockpit, validate_outbound_communication
from adminme.products.member.surfaces import communication_accounts


def test_account_union_requires_exact_member_binding_and_keeps_workspaces() -> None:
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            adapter_config={
                "adapters": [
                    {
                        "id": "personal-mail",
                        "kind": "messaging-gmail-api",
                        "config": {
                            "member_id": "member-a",
                            "provider_account_id": "personal-fixture",
                            "workspace": "personal",
                            "enable_writes": False,
                            "oauth_credentials_ref": "keychain://fixture-personal",
                        },
                    },
                    {
                        "id": "work-mail",
                        "kind": "messaging-microsoft-graph",
                        "config": {
                            "member_id": "member-a",
                            "provider_account_id": "work-fixture",
                            "workspace": "work",
                            "enable_writes": True,
                            "oauth_credentials_ref": "keychain://fixture-work",
                        },
                    },
                    {
                        "id": "other-member-mail",
                        "kind": "messaging-gmail-api",
                        "config": {
                            "member_id": "member-b",
                            "provider_account_id": "other-fixture",
                            "enable_writes": True,
                        },
                    },
                    {
                        "id": "disabled-mail",
                        "kind": "messaging-gmail-api",
                        "enabled": False,
                        "config": {
                            "member_id": "member-a",
                            "provider_account_id": "disabled-fixture",
                        },
                    },
                ]
            },
            fixture_member_id=None,
        ),
        fixture_adapter=None,
        adapters=SimpleNamespace(
            inventory=lambda: {
                "personal-mail": {
                    "state": "running",
                    "write_capabilities": [],
                },
                "work-mail": {
                    "state": "running",
                    "write_capabilities": ["send_message"],
                },
            }
        ),
        settings_control=SimpleNamespace(read=lambda: {"connections": {}}),
        _observation_active=True,
    )

    accounts = communication_accounts(runtime, "member-a")

    assert [account["provider_account_id"] for account in accounts] == [
        "personal-fixture",
        "work-fixture",
    ]
    assert [account["workspace"] for account in accounts] == ["personal", "work"]
    assert accounts[0]["capabilities"]["reply"] is False
    assert accounts[0]["write_access_remedy"]
    assert accounts[1]["capabilities"]["reply"] is True
    assert all(account["state"] == "observation_only" for account in accounts)


def test_focus_rules_are_explained_stable_and_model_free() -> None:
    base = {
        "member_id": "member-a",
        "provider": "gmail",
        "provider_account_id": "personal-fixture",
        "workspace": "personal",
        "direction": "inbound",
        "channel_specific": "gmail",
        "owner_scope": "private:member-a",
        "body_text": "A fixture message",
        "body_preview": "A fixture message",
        "raw_event_ids": '["event-source"]',
        "last_event_id": "event-source",
    }
    rows: list[dict[str, Any]] = [
        {
            **base,
            "interaction_id": "important-message",
            "native_message_id": "native-important",
            "thread_id": "thread-important",
            "occurred_at": "2026-08-06T12:00:00Z",
            "unread": 1,
            "important": 1,
            "semantic_features_json": (
                '{"reply_signal":"required","person_to_person":true}'
            ),
        },
        {
            **base,
            "interaction_id": "noise-message",
            "native_message_id": "native-noise",
            "thread_id": "thread-noise",
            "occurred_at": "2026-08-06T13:00:00Z",
            "classification": "promotional",
        },
    ]
    accounts = [
        {
            "provider": "gmail",
            "provider_account_id": "personal-fixture",
            "label": "Personal fixture",
            "workspace": "personal",
            "capabilities": {"reply": False},
        }
    ]

    first = compose_comms_cockpit(
        member_id="member-a",
        rows=rows,
        participants={},
        party_labels={},
        accounts=accounts,
        triage_items=[],
        now=datetime(2026, 8, 6, 14, tzinfo=timezone.utc),
    )
    second = compose_comms_cockpit(
        member_id="member-a",
        rows=list(reversed(rows)),
        participants={},
        party_labels={},
        accounts=accounts,
        triage_items=[],
        now=datetime(2026, 8, 6, 14, tzinfo=timezone.utc),
    )

    assert first == second
    assert first["decision_maker"] == "rules_not_model"
    assert [thread["bucket"] for thread in first["threads"]] == [
        "focus",
        "quieted",
    ]
    assert first["threads"][0]["why"] == [
        "A typed semantic signal says a reply is required",
        "Provider-important person-to-person message",
    ]
    assert first["attention_policy"]["unread_is_visual_state_only"] is True
    assert first["threads"][0]["attention"]["decision_maker"] == "rules_not_model"


def test_unread_alone_stays_later_and_transport_entities_are_normalized() -> None:
    result = compose_comms_cockpit(
        member_id="member-a",
        rows=[
            {
                "interaction_id": "message-entity",
                "member_id": "member-a",
                "provider": "gmail",
                "provider_account_id": "account-a",
                "workspace": "personal",
                "direction": "inbound",
                "channel_specific": "gmail",
                "body_text": "You&#x27;re all set\n--\nFixture signature",
                "body_preview": "You&#x27;re all set\nOn Yesterday, Sender wrote:\nOld text",
                "unread": 1,
                "occurred_at": "2026-08-06T12:00:00Z",
                "raw_event_ids": '["event-entity"]',
                "last_event_id": "event-entity",
            }
        ],
        participants={},
        party_labels={},
        accounts=[],
        triage_items=[],
        now=datetime(2026, 8, 6, 14, tzinfo=timezone.utc),
    )
    thread = result["threads"][0]
    assert thread["bucket"] == "later"
    assert thread["preview"] == "You're all set"
    assert thread["messages"][0]["body"] == "You're all set"


def test_temporary_security_code_expires_out_of_focus_after_fifteen_minutes() -> None:
    result = compose_comms_cockpit(
        member_id="member-a",
        rows=[
            {
                "interaction_id": "expired-code",
                "member_id": "member-a",
                "provider": "gmail",
                "provider_account_id": "account-a",
                "direction": "inbound",
                "channel_specific": "gmail",
                "subject": "Temporary code",
                "semantic_features_json": '{"security_code":true}',
                "occurred_at": "2026-08-06T12:00:00Z",
                "raw_event_ids": '["event-code"]',
                "last_event_id": "event-code",
            }
        ],
        participants={},
        party_labels={},
        accounts=[],
        triage_items=[],
        now=datetime(2026, 8, 6, 12, 16, tzinfo=timezone.utc),
    )
    thread = result["threads"][0]
    assert thread["bucket"] == "later"
    assert thread["attention"]["signals"]["security_code"] is True
    assert thread["attention"]["signals"]["security_code_active"] is False


def test_final_outbound_guard_rejects_header_injection_and_account_drift() -> None:
    interaction = {
        "member_id": "member-a",
        "provider": "gmail",
        "provider_account_id": "personal-fixture",
        "native_message_id": "native-fixture",
        "reply_to_identifier": "sender@fixture.invalid",
    }
    account = {
        "provider": "gmail",
        "provider_account_id": "personal-fixture",
    }
    passed = validate_outbound_communication(
        operation="reply",
        member_id="member-a",
        interaction=interaction,
        account=account,
        recipient="sender@fixture.invalid",
        body="A member-authored reply.",
    )
    assert passed["policy"] == "deterministic-comms-content-privacy-v1"
    assert passed["passed"] is True

    with pytest.raises(ValueError, match="unsafe header"):
        validate_outbound_communication(
            operation="reply",
            member_id="member-a",
            interaction=interaction,
            account=account,
            recipient="sender@fixture.invalid\r\nBcc: hidden@fixture.invalid",
            body="A member-authored reply.",
        )
    with pytest.raises(ValueError, match="recipient binding"):
        validate_outbound_communication(
            operation="reply",
            member_id="member-a",
            interaction=interaction,
            account=account,
            recipient="different@fixture.invalid",
            body="A member-authored reply.",
        )
    with pytest.raises(ValueError, match="account binding"):
        validate_outbound_communication(
            operation="reply",
            member_id="member-a",
            interaction=interaction,
            account={**account, "provider_account_id": "other-fixture"},
            recipient="sender@fixture.invalid",
            body="A member-authored reply.",
        )
    with pytest.raises(ValueError, match="source-derived reply recipient"):
        validate_outbound_communication(
            operation="reply",
            member_id="member-a",
            interaction={**interaction, "reply_to_identifier": None},
            account=account,
            recipient="sender@fixture.invalid",
            body="A member-authored reply.",
        )
