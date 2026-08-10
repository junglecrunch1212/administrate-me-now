"""Privacy, grounding, and multi-candidate tests for contextual Triage."""

from __future__ import annotations

from typing import Any

import pytest

from adminme.message_context import build_message_context_pack, strip_quoted_history
from adminme.triage_intent import extract_grounded_context_candidates


class _Log:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def read_since(self, **_kwargs: Any):  # type: ignore[no-untyped-def]
        for event in self.events:
            yield event


def _message(
    event_id: str,
    body: str,
    *,
    thread_id: str = "thread-legal",
    member: str = "member-fixture",
    workspace: str = "personal",
    sender: str = "counsel@example.invalid",
    recipient: str = "member@example.invalid",
    subject: str = "Legal authorization package",
    occurred_at: str = "2034-02-01T10:00:00Z",
    owner_scope: str | None = None,
    sensitivity: str = "normal",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scope = owner_scope or f"private:{member}"
    return {
        "event_id": event_id,
        "type": "messaging.received",
        "tenant_id": "tenant-fixture",
        "owner_scope": scope,
        "visibility_scope": scope,
        "sensitivity": sensitivity,
        "source_account_id": "fixture-account",
        "occurred_at": occurred_at,
        "payload": {
            "member_id": member,
            "provider": "fixture",
            "provider_account_id": "fixture-account",
            "workspace": workspace,
            "source_channel": "gmail",
            "thread_id": thread_id,
            "from_identifier": sender,
            "to_identifier": recipient,
            "to_identifiers": [recipient],
            "subject": subject,
            "body_text": body,
            "received_at": occurred_at,
            "attachments": attachments or [],
        },
    }


@pytest.mark.asyncio
async def test_context_pack_is_thread_complete_related_and_scope_closed() -> None:
    messages = [
        _message(
            "source-01",
            "Please prepare two authorization forms for the two proceedings.",
            occurred_at="2034-02-01T10:00:00Z",
        ),
        _message(
            "source-02",
            "Please return the compliance form and identification copy.",
            occurred_at="2034-02-01T11:00:00Z",
        ),
        _message(
            "source-03",
            "I will arrange the legal-fee transfer this afternoon.",
            sender="member@example.invalid",
            recipient="counsel@example.invalid",
            occurred_at="2034-02-01T12:00:00Z",
        ),
        _message(
            "source-04",
            "Can the signed PDF be used, or is the notarized original required?",
            occurred_at="2034-02-01T13:00:00Z",
        ),
        _message(
            "source-05",
            "The first form is signed; the second form is still outstanding.",
            occurred_at="2034-02-01T14:00:00Z",
        ),
        _message(
            "source-06",
            "Please confirm who will collect the original document.",
            occurred_at="2034-02-01T15:00:00Z",
        ),
    ]
    messages[2]["type"] = "messaging.sent"
    related = _message(
        "source-related",
        "Your authorization package is waiting for signature.",
        thread_id="thread-signing-service",
        sender="signing-service@example.invalid",
        subject="Sign legal authorization package",
        occurred_at="2034-02-01T15:30:00Z",
    )
    unrelated = _message(
        "source-unrelated",
        "Your weekly store offers are ready.",
        thread_id="thread-store",
        sender="store@example.invalid",
        subject="Weekly store offers",
    )
    generic_subject_overlap = _message(
        "source-generic-overlap",
        "A generic package update is available.",
        thread_id="thread-generic-package",
        sender="notifications@example.invalid",
        subject="Package update",
    )
    wrong_member = _message(
        "source-private-other",
        "Please sign the legal authorization package.",
        thread_id="thread-other-member",
        member="member-other",
        owner_scope="private:member-other",
    )
    wrong_workspace = _message(
        "source-work",
        "Please sign the legal authorization package.",
        thread_id="thread-work",
        workspace="work",
    )
    privileged = _message(
        "source-privileged",
        "Please sign the legal authorization package.",
        thread_id="thread-privileged",
        sensitivity="privileged",
    )
    pack = await build_message_context_pack(
        messages[-1],
        _Log(
            [
                *messages,
                related,
                unrelated,
                generic_subject_overlap,
                wrong_member,
                wrong_workspace,
                privileged,
            ]
        ),
    )

    assert pack.current_is_thread_anchor is True
    assert pack.anchor_event_id == "source-06"
    assert [value.source_event_id for value in pack.messages[:6]] == [
        f"source-0{index}" for index in range(1, 7)
    ]
    assert [value.source_event_id for value in pack.messages[6:]] == ["source-related"]
    assert pack.messages[-1].relation == "related"
    assert "source-private-other" not in {
        value.source_event_id for value in pack.messages
    }
    assert "source-work" not in {value.source_event_id for value in pack.messages}
    assert "source-privileged" not in {value.source_event_id for value in pack.messages}
    assert "source-generic-overlap" not in {
        value.source_event_id for value in pack.messages
    }

    same_content = _message(
        "source-same-content",
        messages[-1]["payload"]["body_text"],
        occurred_at=messages[-1]["occurred_at"],
    )
    same_content_pack = await build_message_context_pack(same_content, _Log([]))
    other_member_pack = await build_message_context_pack(
        _message(
            "source-same-content-other-member",
            messages[-1]["payload"]["body_text"],
            member="member-other",
            owner_scope="private:member-other",
            occurred_at=messages[-1]["occurred_at"],
        ),
        _Log([]),
    )
    assert other_member_pack.fingerprint != same_content_pack.fingerprint


@pytest.mark.asyncio
async def test_context_extraction_preserves_distinct_open_asks_and_provenance() -> None:
    messages = [
        _message(
            "source-01",
            "Please prepare two authorization forms for the two proceedings.",
            occurred_at="2034-02-01T10:00:00Z",
        ),
        _message(
            "source-02",
            "Please return the compliance form and identification copy.",
            occurred_at="2034-02-01T11:00:00Z",
        ),
        _message(
            "source-03",
            "I will arrange the legal-fee transfer this afternoon.",
            sender="member@example.invalid",
            recipient="counsel@example.invalid",
            occurred_at="2034-02-01T12:00:00Z",
        ),
        _message(
            "source-04",
            "Can the signed PDF be used, or is the notarized original required?",
            occurred_at="2034-02-01T13:00:00Z",
        ),
        _message(
            "source-05",
            "The first form is signed; the second form is still outstanding.",
            occurred_at="2034-02-01T14:00:00Z",
        ),
        _message(
            "source-06",
            "Please confirm who will collect the original document.",
            occurred_at="2034-02-01T15:00:00Z",
        ),
    ]
    messages[2]["type"] = "messaging.sent"
    related = _message(
        "source-related",
        "Your authorization package is waiting for signature.",
        thread_id="thread-signing-service",
        sender="signing-service@example.invalid",
        subject="Sign legal authorization package",
        occurred_at="2034-02-01T15:30:00Z",
    )
    pack = await build_message_context_pack(messages[-1], _Log([*messages, related]))
    aliases = {value.source_event_id: value.alias for value in pack.messages}

    def candidate(
        event_id: str,
        quote: str,
        summary: str,
        *,
        kind: str = "task",
        owner: str = "member",
        lifecycle: str = "open",
    ) -> dict[str, Any]:
        return {
            "target_kind": kind,
            "title": summary,
            "evidence_source_id": aliases[event_id],
            "evidence_quote": quote,
            "candidate": {},
            "confidence": 0.82,
            "reasons": ["exact conversational ask"],
            "action_owner": owner,
            "lifecycle_state": lifecycle,
            "compact_summary": summary,
            "case_summary": "Two legal authorizations require documents, execution, and delivery coordination.",
            "why_member": "The group request includes the member and an outbound message contains a member promise.",
            "next_step": "Review the exact ask and assign the correct owner before confirming.",
            "thread_state": "in_progress",
            "unresolved_questions": ["Who will collect the original?"],
            "related_source_ids": [aliases["source-related"]],
        }

    raw = [
        candidate(
            "source-01",
            "Please prepare two authorization forms for the two proceedings.",
            "Prepare two authorization forms",
            owner="group_including_member",
        ),
        candidate(
            "source-02",
            "Please return the compliance form and identification copy.",
            "Return compliance form and ID copy",
        ),
        candidate(
            "source-03",
            "I will arrange the legal-fee transfer this afternoon.",
            "Arrange legal-fee transfer",
            kind="commitment",
        ),
        candidate(
            "source-04",
            "Can the signed PDF be used, or is the notarized original required?",
            "Decide whether notarization is required",
            owner="unknown",
        ),
        candidate(
            "source-05",
            "The first form is signed",
            "Sign first form",
            lifecycle="completed",
        ),
        candidate(
            "source-06",
            "Please confirm who will collect the original document.",
            "Counsel confirms document collection",
            owner="other_party",
        ),
        candidate(
            "source-related",
            "Your authorization package is waiting for signature.",
            "Sign via service",
        ),
    ]
    candidates = extract_grounded_context_candidates(pack, raw)

    assert [value.target_kind for value in candidates] == [
        "task",
        "task",
        "commitment",
        "task",
    ]
    assert len({value.candidate_key for value in candidates}) == 4
    assert all(value.interpretation is not None for value in candidates)
    assert candidates[0].interpretation["evidence_refs"] == [  # type: ignore[index]
        {
            "source_event_id": "source-01",
            "quote": "Please prepare two authorization forms for the two proceedings.",
        }
    ]
    assert candidates[0].interpretation["related_source_event_ids"] == [  # type: ignore[index]
        "source-related"
    ]
    assert "ownership_requires_member_review_v1" in candidates[-1].reasons


def test_quoted_history_is_not_repeated_as_new_authored_content() -> None:
    assert (
        strip_quoted_history(
            "Please confirm the pickup time.\n\nOn Tuesday, Counsel wrote:\n> old ask"
        )
        == "Please confirm the pickup time."
    )
