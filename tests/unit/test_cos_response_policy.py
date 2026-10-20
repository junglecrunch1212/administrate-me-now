"""Exact recipient/member matching is a ceiling, never send authority."""

from __future__ import annotations

from typing import Any

import pytest

from adminme.cos_response_policy import (
    POLICY_CONTRACT,
    POLICY_EVENT_TYPE,
    normalize_recipient,
    policy_from_event_log,
    recipient_decision,
    validate_recipient_entries,
)
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID


A = "fixture-allowlist-a"  # fixture:tenant_data:ok
B = "fixture-allowlist-b"  # fixture:tenant_data:ok


@pytest.mark.parametrize(
    ("channel", "address", "expected"),
    [
        ("email", "Member.A+review@EXAMPLE.TEST", "Member.A+review@example.test"),
        ("imessage", "member@example.test", "member@example.test"),
        ("imessage", "+12025550101", "+12025550101"),  # fixture:tenant_data:ok
    ],
)
def test_normalization_is_exact(channel: str, address: str, expected: str) -> None:
    assert normalize_recipient(channel, address) == expected


@pytest.mark.parametrize(
    ("channel", "address"),
    [
        ("email", "a@example.test,b@example.test"),
        ("email", "Person <a@example.test>"),
        ("email", "a@example.test\r\nBcc:b@example.test"),
        ("email", "Group:a@example.test;"),
        ("email", "*@example.test"),
        ("email", "?@example.test"),
        ("email", "a@*.test"),
        ("email", "a..b@example.test"),
        ("email", " a@example.test"),
        ("email", "a@example.test "),
        ("email", '"a,b"@example.test'),
        ("email", "a＠example.test"),
        ("email", "a@example"),
        ("email", "+12025550101"),  # fixture:tenant_data:ok
        ("imessage", "202-555-0101"),  # fixture:tenant_data:ok
        ("imessage", "+1 202 555 0101"),  # fixture:tenant_data:ok
        ("imessage", "+02025550101"),  # fixture:tenant_data:ok
        ("sms", "+12025550101"),  # fixture:tenant_data:ok
    ],
)
def test_ambiguous_or_wildcard_destinations_are_refused(
    channel: str, address: str
) -> None:
    with pytest.raises(ValueError):
        normalize_recipient(channel, address)


def _entries() -> list[dict[str, str]]:
    return [
        {"member_id": A, "channel": "email", "address": "member.a@example.test"},
        {"member_id": A, "channel": "email", "address": "alternate@example.test"},
        {"member_id": B, "channel": "email", "address": "member.b@example.test"},
        {
            "member_id": A,
            "channel": "imessage",
            "address": "+12025550101",  # fixture:tenant_data:ok
        },
    ]


def _policy() -> dict[str, Any]:
    return {
        "contract": POLICY_CONTRACT,
        "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "revision": 1,
        "event_id": "event-policy-1",
        "entries": _entries(),
    }


def test_all_recipient_slots_must_match_active_member_exactly() -> None:
    policy = _policy()
    decision = recipient_decision(
        policy,
        channel="email",
        recipients=["member.a@example.test", "alternate@example.test"],
        member_id=A,
    )
    assert decision == {
        "allowed": True,
        "reason": "recipients_allowed",
        "policy_revision": 1,
        "policy_event_id": "event-policy-1",
    }
    for disallowed in (
        "member.b@example.test",
        "outsider@example.test",
        "membera@example.test",
        "Member.a@example.test",
        "member.a+tag@example.test",
        "member.a@example.test,member.b@example.test",
    ):
        # Callers flatten every To, Cc, Bcc and resolved group participant.
        assert not recipient_decision(
            policy,
            channel="email",
            recipients=["member.a@example.test", disallowed],
            member_id=A,
        )["allowed"]
    assert not recipient_decision(
        policy,
        channel="email",
        recipients=["member.a@example.test"],
        member_id=A,
        service_principal_id="unrelated-service",
    )["allowed"]
    assert not recipient_decision(
        policy,
        channel="imessage",
        recipients=["member.a@example.test"],
        member_id=A,
    )["allowed"]
    assert not recipient_decision(
        {**policy, "entries": []},
        channel="email",
        recipients=["member.a@example.test"],
        member_id=A,
    )["allowed"]


def test_one_endpoint_cannot_be_associated_with_two_members() -> None:
    with pytest.raises(ValueError, match="exactly one member"):
        validate_recipient_entries(
            [
                {"member_id": A, "channel": "email", "address": "a@example.test"},
                {"member_id": B, "channel": "email", "address": "a@EXAMPLE.TEST"},
            ],
            {A, B},
        )
    with pytest.raises(ValueError, match="known household member"):
        validate_recipient_entries(_entries(), {A})
    with pytest.raises(ValueError):
        validate_recipient_entries(
            [
                {
                    "member_id": A,
                    "channel": "email",
                    "address": "a@example.test",
                    "all": True,
                }
            ],
            {A},
        )


class _Log:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def read_since(self, *, types: list[str]):
        assert types == [POLICY_EVENT_TYPE]
        for event in self.events:
            yield event


def _event(tenant: str, revision: int, entries: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "event_id": f"event-{tenant}-{revision}",
        "tenant_id": tenant,
        "payload": {
            "contract": POLICY_CONTRACT,
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "revision": revision,
            "entries": entries,
            "changed_by_member_id": A,
            "grants_provider_authority": False,
            "provider_effects": False,
        },
    }


@pytest.mark.asyncio
async def test_replay_is_tenant_scoped_and_revocation_is_durable() -> None:
    log = _Log(
        [
            _event("tenant-a", 1, _entries()),
            _event("tenant-b", 99, _entries()),  # foreign tenant cannot break replay
            _event("tenant-a", 2, []),
        ]
    )
    policy = await policy_from_event_log(log, "tenant-a")
    assert policy["entries"] == []
    assert policy["revision"] == 2
    assert policy["event_id"] == "event-tenant-a-2"
    initial = await policy_from_event_log(log, "tenant-empty")
    assert initial["revision"] == 0
    assert initial["entries"] == []
    assert initial["event_id"] is None
    with pytest.raises(RuntimeError, match="failed closed"):
        await policy_from_event_log(_Log([_event("tenant-a", 2, [])]), "tenant-a")
