"""Privacy, grounding, and multi-candidate tests for contextual Triage."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

import adminme.message_context as message_context
from adminme.events.schemas.triage import TriageContextSourceV2
from adminme.message_context import (
    MAX_CONTEXT_CHARS,
    MAX_MESSAGE_CHARS,
    authored_message_text,
    build_message_context_pack,
    strip_quoted_history,
)
from adminme.triage_intent import extract_grounded_context_candidates


class _Log:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def read_since(self, **_kwargs: Any):  # type: ignore[no-untyped-def]
        for event in self.events:
            yield event


class _CursorRecordingLog:
    """Append-only fixture that records how context history is traversed."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.cursors: list[str | None] = []
        self.yield_counts: list[int] = []

    async def read_since(
        self,
        cursor: str | None = None,
        **_kwargs: Any,
    ):  # type: ignore[no-untyped-def]
        self.cursors.append(cursor)
        start = 0
        if cursor is not None:
            start = next(
                index + 1
                for index, event in enumerate(self.events)
                if event["event_id"] == cursor
            )
        values = self.events[start:]
        self.yield_counts.append(len(values))
        for event in values:
            yield event


def test_interpretation_v2_schema_rejects_weak_related_source_without_edge() -> None:
    with pytest.raises(ValidationError):
        TriageContextSourceV2(
            source_event_id="source-related",
            relation="related",
            reason_codes=["subject_token_overlap"],
            relationship_source_event_ids=[],
        )


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
    rfc_message_id: str | None = None,
    references: list[str] | None = None,
    in_reply_to: str | None = None,
    provider: str = "fixture",
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
            "provider": provider,
            "provider_account_id": "fixture-account",
            "workspace": workspace,
            "source_channel": "gmail",
            "thread_id": thread_id,
            "rfc_message_id": rfc_message_id or f"<{event_id}@example.invalid>",
            "references": references or [],
            "in_reply_to": in_reply_to,
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
async def test_context_history_reads_only_the_append_only_suffix_after_warmup() -> None:
    messages = [
        _message(
            f"source-scan-{index:03d}",
            f"Fixture update {index}.",
            thread_id=f"thread-scan-{index}",
            occurred_at=f"2034-02-{(index % 27) + 1:02d}T10:00:00Z",
        )
        for index in range(200)
    ]
    log = _CursorRecordingLog(messages)

    first = await build_message_context_pack(messages[-1], log)
    appended = _message(
        "source-scan-200",
        "A new fixture update.",
        thread_id="thread-scan-200",
        occurred_at="2034-03-01T10:00:00Z",
    )
    messages.append(appended)
    second = await build_message_context_pack(appended, log)

    assert [message.source_event_id for message in first.messages] == [
        "source-scan-199"
    ]
    assert [message.source_event_id for message in second.messages] == [
        "source-scan-200"
    ]
    assert log.cursors == [None, "source-scan-199"]
    assert log.yield_counts == [200, 1]


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
        references=["<source-06@example.invalid>"],
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
async def test_indexed_history_keeps_edges_from_every_native_thread_source() -> None:
    first = _message(
        "source-index-first",
        "The first source carries the exact RFC identity.",
        rfc_message_id="<source-index-first@example.invalid>",
        occurred_at="2034-02-01T10:00:00Z",
    )
    anchor = _message(
        "source-index-anchor",
        "A later update in the same provider thread.",
        occurred_at="2034-02-01T11:00:00Z",
    )
    related = _message(
        "source-index-related",
        "A provider-linked update from another thread.",
        thread_id="thread-index-related",
        references=["<source-index-first@example.invalid>"],
        occurred_at="2034-02-01T10:30:00Z",
    )

    pack = await build_message_context_pack(anchor, _Log([first, related, anchor]))

    assert [message.source_event_id for message in pack.messages] == [
        "source-index-first",
        "source-index-anchor",
        "source-index-related",
    ]
    assert pack.messages[-1].relationship_source_event_ids == ("source-index-first",)


@pytest.mark.asyncio
async def test_related_cross_thread_source_does_not_expand_authority_transitively() -> (
    None
):
    anchor = _message(
        "source-index-root",
        "The native-thread root.",
        rfc_message_id="<source-index-root@example.invalid>",
    )
    related = _message(
        "source-index-one-hop",
        "The directly related source.",
        thread_id="thread-index-one-hop",
        rfc_message_id="<source-index-one-hop@example.invalid>",
        references=["<source-index-root@example.invalid>"],
    )
    transitive = _message(
        "source-index-two-hop",
        "A source linked only through the related cross-thread source.",
        thread_id="thread-index-two-hop",
        references=["<source-index-one-hop@example.invalid>"],
    )

    pack = await build_message_context_pack(anchor, _Log([anchor, related, transitive]))

    assert [message.source_event_id for message in pack.messages] == [
        "source-index-root",
        "source-index-one-hop",
    ]


@pytest.mark.asyncio
async def test_weak_participant_and_authored_name_overlap_do_not_relate_threads() -> (
    None
):
    """Shared mailboxes, addresses, subjects, and recurring names stay weak."""
    anchor = _message(
        "source-anchor",
        "Please send the authorization package when it is ready.",
        sender="counsel@example.invalid",
        recipient="member@example.invalid",
        attachments=[{"filename": "shared-fixture-form.pdf"}],
    )
    service_notice = _message(
        "source-service",
        "A secure request from counsel@example.invalid is ready for review.",
        thread_id="thread-service",
        sender="delivery@example.invalid",
        recipient="member@example.invalid",
        subject="Secure delivery notice",
    )
    near_text = _message(
        "source-near-text",
        "A secure request from counsel at example dot invalid is ready.",
        thread_id="thread-near-text",
        sender="delivery@example.invalid",
        recipient="member@example.invalid",
        subject="Secure delivery notice",
    )
    wrong_scope = _message(
        "source-wrong-scope",
        "A secure request from counsel@example.invalid is ready for review.",
        thread_id="thread-wrong-scope",
        sender="delivery@example.invalid",
        recipient="member@example.invalid",
        subject="Secure delivery notice",
        workspace="work",
    )
    recurring_family_name = _message(
        "source-family-name",
        "The Fixture Holdings records are ready for the unrelated account.",
        thread_id="thread-unrelated-account",
        sender="delivery@example.invalid",
        recipient="member@example.invalid",
        subject="Fixture Holdings account records",
    )
    same_subject_and_filename = _message(
        "source-subject-filename",
        "This is a separate fixture matter.",
        thread_id="thread-repeated-metadata",
        attachments=[{"filename": "shared-fixture-form.pdf"}],
    )
    wrong_provider = _message(
        "source-wrong-provider",
        "A provider-crossing message must stay out.",
        thread_id="thread-wrong-provider",
        provider="other-fixture-provider",
        references=["<source-anchor@example.invalid>"],
    )

    pack = await build_message_context_pack(
        anchor,
        _Log(
            [
                anchor,
                service_notice,
                near_text,
                wrong_scope,
                recurring_family_name,
                same_subject_and_filename,
                wrong_provider,
            ]
        ),
    )

    assert [message.source_event_id for message in pack.messages] == ["source-anchor"]


@pytest.mark.asyncio
async def test_resolved_party_aliases_do_not_establish_matter_identity() -> None:
    anchor = _message(
        "source-anchor",
        "A fixture note for one matter.",
        sender="alias-one@example.invalid",
    )
    unrelated = _message(
        "source-unrelated",
        "A fixture note for another matter.",
        thread_id="thread-unrelated",
        sender="alias-two@example.invalid",
    )
    resolutions = [
        {
            "event_id": f"resolution-{index}",
            "type": "interaction.participant_resolved",
            "tenant_id": "tenant-fixture",
            "owner_scope": "private:member-fixture",
            "visibility_scope": "private:member-fixture",
            "sensitivity": "normal",
            "payload": {
                "source_event_id": source_event_id,
                "party_id": "party-fixture-shared",
                "identifier_normalized": identifier,
            },
        }
        for index, (source_event_id, identifier) in enumerate(
            [
                ("source-anchor", "alias-one@example.invalid"),
                ("source-unrelated", "alias-two@example.invalid"),
            ]
        )
    ]

    pack = await build_message_context_pack(
        anchor, _Log([anchor, unrelated, *resolutions])
    )

    assert [message.source_event_id for message in pack.messages] == ["source-anchor"]


@pytest.mark.asyncio
async def test_exact_attachment_digest_can_admit_a_cross_thread_source() -> None:
    digest = "c" * 64
    anchor = _message(
        "source-anchor",
        "Please review the attached fixture form.",
        attachments=[{"filename": "form.pdf", "sha256": digest}],
    )
    related = _message(
        "source-related",
        "The attached fixture form is ready.",
        thread_id="thread-attachment-service",
        attachments=[{"filename": "renamed.pdf", "content_sha256": digest}],
    )

    pack = await build_message_context_pack(anchor, _Log([anchor, related]))

    assert [message.source_event_id for message in pack.messages] == [
        "source-anchor",
        "source-related",
    ]
    assert "attachment_identity" in pack.messages[-1].relation_reason_codes


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
        references=["<source-06@example.invalid>"],
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


def test_reply_or_forward_boundary_at_offset_zero_fails_closed() -> None:
    plain = authored_message_text(
        {"body_text": "On Tuesday, Counsel wrote:\r\n> Please wire funds."}
    )
    html_only = authored_message_text(
        {"body_html": ('<div class="gmail_quote"><div>Please wire funds.</div></div>')}
    )
    mobile_forward = authored_message_text(
        {"body_text": "Begin forwarded message:\nPlease wire fixture funds."}
    )
    spanish_reply = authored_message_text(
        {"body_text": "El martes, Remitente escribió:\n> Envíe los documentos."}
    )
    outlook_html = authored_message_text(
        {
            "body_html": (
                '<div id="divRplyFwdMsg"><b>From:</b> sender@example.invalid'
                "<br>Please wire fixture funds.</div>"
            )
        }
    )
    apple_quote = authored_message_text(
        {"body_html": '<blockquote type="cite">Old fixture ask.</blockquote>'}
    )

    for result in (
        plain,
        html_only,
        mobile_forward,
        spanish_reply,
        outlook_html,
        apple_quote,
    ):
        assert result.text == ""
        assert result.quoted_history_removed is True
        assert result.authored_span is None


def test_authored_text_normalizes_crlf_once_for_grounding_and_display() -> None:
    result = authored_message_text(
        {"body_text": "Please review line one.\r\nThen confirm line two."}
    )

    assert result.text == "Please review line one.\nThen confirm line two."
    assert result.reliable is True
    assert result.canonical_source_text == result.text
    assert result.authored_span == (0, len(result.text))


def test_explicit_html_quote_boundary_constrains_multipart_plain_text() -> None:
    quoted_only = authored_message_text(
        {
            "body_text": "Please authorize the old fixture transfer.",
            "body_html": (
                '<div class="gmail_quote">'
                "Please authorize the old fixture transfer.</div>"
            ),
        }
    )
    authored_then_quoted = authored_message_text(
        {
            "body_text": "New fixture update.\nOld fixture request.",
            "body_html": (
                "<div>New fixture update.</div>"
                '<div class="gmail_quote">Old fixture request.</div>'
            ),
        }
    )
    disagreeing_alternatives = authored_message_text(
        {
            "body_text": "A different apparent fixture request.",
            "body_html": (
                "<div>New fixture update.</div>"
                '<div class="gmail_quote">Old fixture request.</div>'
            ),
        }
    )

    assert quoted_only.text == ""
    assert quoted_only.reliable is True
    assert quoted_only.quoted_history_removed is True
    assert authored_then_quoted.text == "New fixture update."
    assert authored_then_quoted.reliable is True
    assert authored_then_quoted.quoted_history_removed is True
    assert disagreeing_alternatives.text == ""
    assert disagreeing_alternatives.reliable is False


def test_unrecognized_plain_quote_headers_fail_closed_as_uncertain() -> None:
    result = authored_message_text(
        {
            "body_text": (
                "Sent: Tuesday, February 1\n"
                "To: member@example.invalid\n"
                "Subject: Earlier request\n"
                "Please wire fixture funds."
            )
        }
    )

    assert result.text == ""
    assert result.reliable is False


@pytest.mark.asyncio
async def test_reused_provider_thread_is_segmented_into_bounded_episodes() -> None:
    old = _message(
        "source-old",
        "Please review the old matter.",
        occurred_at="2034-01-01T10:00:00Z",
    )
    current = _message(
        "source-current",
        "Please review the current matter.",
        occurred_at="2034-03-15T10:00:00Z",
    )

    pack = await build_message_context_pack(current, _Log([old, current]))

    assert [message.source_event_id for message in pack.messages] == ["source-current"]
    assert "source-old" not in pack.thread_key


@pytest.mark.asyncio
async def test_provider_thread_episode_closes_at_one_hundred_sources() -> None:
    start = datetime(2034, 2, 1, 10, tzinfo=timezone.utc)
    messages = [
        _message(
            f"source-{index:03d}",
            f"Fixture message {index}.",
            occurred_at=(start + timedelta(minutes=index)).isoformat(),
        )
        for index in range(101)
    ]

    pack = await build_message_context_pack(messages[-1], _Log(messages))

    assert [message.source_event_id for message in pack.messages] == ["source-100"]
    assert "source-000" not in pack.thread_key


@pytest.mark.asyncio
@pytest.mark.parametrize("old_year, expected_edge_checks", [(2030, 0), (2034, 200)])
async def test_long_native_thread_skips_nonadmitting_body_scans(
    monkeypatch: pytest.MonkeyPatch, old_year: int, expected_edge_checks: int
) -> None:
    old_start = datetime(old_year, 2, 1, 10, tzinfo=timezone.utc)
    old = [
        _message(
            f"source-history-{index:03d}",
            "member@example.invalid " * 500,
            occurred_at=(old_start + timedelta(minutes=index)).isoformat(),
        )
        for index in range(200)
    ]
    current = _message(
        "source-current",
        "Please review the current fixture request.",
        occurred_at="2034-02-01T13:20:00Z",
    )
    baseline = await build_message_context_pack(current, _Log([current]))
    edge_checks = []
    body_scans = []
    original_edges = message_context._related_edge_reasons
    original_references = message_context._referenced_identifiers

    def counted_edges(payload: Mapping[str, Any], **kwargs: Any):
        edge_checks.append(payload["rfc_message_id"])
        return original_edges(payload, **kwargs)

    def counted_references(payload: Mapping[str, Any]):
        body_scans.append(payload["rfc_message_id"])
        return original_references(payload)

    monkeypatch.setattr(message_context, "_related_edge_reasons", counted_edges)
    monkeypatch.setattr(message_context, "_referenced_identifiers", counted_references)
    expanded = await build_message_context_pack(current, _Log([*old, current]))

    # Entire public pack equality covers aliases, reasons, source text, bounds,
    # episode identity, current-anchor state, and the interpretation fingerprint.
    assert expanded == baseline
    assert len(edge_checks) == expected_edge_checks
    assert body_scans == []


@pytest.mark.asyncio
async def test_context_budget_reserves_current_anchor_and_tracks_full_episode() -> None:
    start = datetime(2034, 2, 1, 10, tzinfo=timezone.utc)
    messages = [
        _message(
            f"source-{index:02d}",
            "x" * MAX_MESSAGE_CHARS,
            occurred_at=(start + timedelta(minutes=index)).isoformat(),
        )
        for index in range(12)
    ]
    messages.append(
        _message(
            "source-current",
            "Please review the current fixture request.",
            occurred_at=(start + timedelta(minutes=12)).isoformat(),
        )
    )

    pack = await build_message_context_pack(messages[-1], _Log(messages))

    assert pack.episode_source_event_ids == tuple(
        [*(f"source-{index:02d}" for index in range(12)), "source-current"]
    )
    assert pack.messages[-1].source_event_id == "source-current"
    assert pack.messages[-1].body_text == "Please review the current fixture request."
    assert sum(len(message.body_text) for message in pack.messages) <= MAX_CONTEXT_CHARS
    assert pack.messages[-1].text_truncated is False


@pytest.mark.asyncio
async def test_replay_pack_keeps_old_current_source_beside_latest_anchor() -> None:
    start = datetime(2034, 2, 1, 10, tzinfo=timezone.utc)
    messages = [
        _message(
            f"source-{index:02d}",
            f"Fixture update {index}.",
            occurred_at=(start + timedelta(minutes=index)).isoformat(),
        )
        for index in range(13)
    ]

    pack = await build_message_context_pack(messages[0], _Log(messages))
    source_ids = [message.source_event_id for message in pack.messages]

    assert len(source_ids) == 12
    assert "source-00" in source_ids
    assert "source-12" in source_ids
    assert pack.anchor_event_id == "source-12"
    assert pack.current_is_thread_anchor is False


@pytest.mark.asyncio
async def test_context_budget_reserves_non_anchor_source_under_assessment() -> None:
    start = datetime(2034, 2, 1, 10, tzinfo=timezone.utc)
    messages = [
        _message(
            f"source-{index:02d}",
            str(index % 10) * MAX_MESSAGE_CHARS,
            occurred_at=(start + timedelta(minutes=index)).isoformat(),
        )
        for index in range(13)
    ]

    pack = await build_message_context_pack(messages[10], _Log(messages))
    by_source = {message.source_event_id: message for message in pack.messages}

    assert by_source["source-10"].body_text == "0" * MAX_MESSAGE_CHARS
    assert by_source["source-12"].body_text == "2" * MAX_MESSAGE_CHARS
    assert sum(len(message.body_text) for message in pack.messages) <= MAX_CONTEXT_CHARS


@pytest.mark.asyncio
async def test_legacy_gmail_sent_body_without_html_provenance_is_uncertain() -> None:
    legacy_sent = _message(
        "source-legacy-sent",
        "I will authorize the old fixture transfer.",
    )
    legacy_sent.update(
        {
            "type": "messaging.sent",
            "schema_version": 3,
            "source_adapter": "messaging-gmail-api",
        }
    )
    legacy_sent["payload"].update(
        {
            "provider": "gmail",
            "sent_at": legacy_sent["occurred_at"],
        }
    )

    pack = await build_message_context_pack(legacy_sent, _Log([legacy_sent]))

    assert len(pack.messages) == 1
    assert pack.messages[0].body_text == ""
    assert pack.messages[0].authored_text_reliable is False
    assert pack.messages[0].authored_text_reason_codes == (
        "legacy_gmail_sent_quote_boundary_unavailable",
    )
    assert (
        extract_grounded_context_candidates(
            pack,
            [
                {
                    "target_kind": "commitment",
                    "evidence_source_id": "m01",
                    "evidence_quote": "I will authorize the old fixture transfer.",
                    "action_owner": "member",
                    "lifecycle_state": "open",
                }
            ],
        )
        == []
    )


def _context_decision(
    event_id: str,
    event_type: str,
    source_event_id: str,
    related_source_event_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "member_id": "member-fixture",
        "provider": "fixture",
        "provider_account_id": "fixture-account",
        "workspace": "personal",
        "source_event_id": source_event_id,
    }
    if related_source_event_id is not None:
        payload["related_source_event_id"] = related_source_event_id
        payload["algorithm"] = "message-context-pack-v2"
    return {
        "event_id": event_id,
        "type": event_type,
        "tenant_id": "tenant-fixture",
        "owner_scope": "private:member-fixture",
        "visibility_scope": "private:member-fixture",
        "sensitivity": "normal",
        "source_account_id": "member-fixture",
        "occurred_at": "2034-02-01T16:00:00Z",
        "payload": payload,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed_time", ["not-a-time", "2030-02-01T10:00:00Z"])
async def test_corroboration_runs_only_for_admitted_edges_and_confirmations(
    monkeypatch: pytest.MonkeyPatch, confirmed_time: str
) -> None:
    start = datetime(2034, 2, 1, 10, tzinfo=timezone.utc)
    episode = [
        _message(
            f"source-episode-{index:02d}",
            f"Fixture episode update {index}.",
            occurred_at=(start + timedelta(minutes=index)).isoformat(),
            attachments=[{"filename": "authorization.pdf", "sha256": "a" * 64}]
            if index == 0
            else [{"filename": "authorization.pdf"}]
            if index == 12
            else [],
        )
        for index in range(13)
    ]
    related = _message(
        "source-related",
        "Contact member@example.invalid about the authorization.",
        thread_id="thread-related",
        references=["<source-episode-00@example.invalid>"],
        attachments=[{"filename": "authorization.pdf", "sha256": "a" * 64}],
    )
    confirmed = _message(
        "source-confirmed",
        "Contact member@example.invalid about the older source.",
        thread_id="thread-confirmed",
        occurred_at=confirmed_time,
    )
    confirmation = _context_decision(
        "correction-confirmed",
        "context.relationship.confirmed",
        "source-episode-00",
        "source-confirmed",
    )
    rejected = _message(
        "source-rejected",
        "member@example.invalid " * 500,
        thread_id="thread-rejected",
        references=["<source-episode-00@example.invalid>"],
    )
    prior_confirmation = _context_decision(
        "correction-rejected-first",
        "context.relationship.confirmed",
        "source-episode-00",
        "source-rejected",
    )
    rejection = _context_decision(
        "correction-rejected-last",
        "context.relationship.rejected",
        "source-episode-00",
        "source-rejected",
    )
    old_unconfirmed = _message(
        "source-old-unconfirmed",
        "member@example.invalid " * 500,
        thread_id="thread-old-unconfirmed",
        occurred_at="2030-02-01T10:00:00Z",
        references=["<source-episode-00@example.invalid>"],
    )
    baseline_events = [*episode, related, confirmed, confirmation]
    baseline = await build_message_context_pack(episode[-1], _Log(baseline_events))
    body_scans = []
    original_references = message_context._referenced_identifiers

    def counted_references(payload: Mapping[str, Any]):
        body_scans.append(payload["rfc_message_id"])
        return original_references(payload)

    monkeypatch.setattr(message_context, "_referenced_identifiers", counted_references)
    expanded = await build_message_context_pack(
        episode[-1],
        _Log(
            [*baseline_events, rejected, old_unconfirmed, prior_confirmation, rejection]
        ),
    )

    assert expanded == baseline
    assert set(body_scans) == {
        "<source-related@example.invalid>",
        "<source-confirmed@example.invalid>",
    }
    assert len(body_scans) == 2
    assert "source-episode-00" not in {
        message.source_event_id for message in expanded.messages
    }
    assert expanded.episode_source_event_ids[0] == "source-episode-00"
    related_context, confirmed_context = expanded.messages[-2:]
    assert related_context.relationship_source_event_ids == ("source-episode-00",)
    assert related_context.relation_reason_codes == (
        "attachment_identity",
        "rfc_reference",
        "participant_overlap",
        "participant_reference_overlap",
        "subject_token_overlap",
        "attachment_filename_overlap",
    )
    assert confirmed_context.relationship_source_event_ids == ("source-episode-00",)
    assert confirmed_context.relation_reason_codes == (
        "member_confirmed_context",
        "participant_overlap",
        "participant_reference_overlap",
        "subject_token_overlap",
    )


@pytest.mark.asyncio
async def test_exact_relationship_correction_detaches_and_confirmation_relinks() -> (
    None
):
    anchor = _message("source-anchor", "Please review the authorization.")
    related = _message(
        "source-related",
        "The authorization is ready.",
        thread_id="thread-service",
        references=["<source-anchor@example.invalid>"],
    )
    rejected = _context_decision(
        "correction-1",
        "context.relationship.rejected",
        "source-anchor",
        "source-related",
    )
    confirmed = _context_decision(
        "correction-2",
        "context.relationship.confirmed",
        "source-anchor",
        "source-related",
    )

    detached = await build_message_context_pack(
        anchor, _Log([anchor, related, rejected])
    )
    relinked = await build_message_context_pack(
        anchor, _Log([anchor, related, rejected, confirmed])
    )

    assert [message.source_event_id for message in detached.messages] == [
        "source-anchor"
    ]
    assert [message.source_event_id for message in relinked.messages] == [
        "source-anchor",
        "source-related",
    ]
    assert "member_confirmed_context" in relinked.messages[-1].relation_reason_codes
    assert relinked.messages[-1].relationship_source_event_ids == ("source-anchor",)


@pytest.mark.asyncio
async def test_relationship_rejection_survives_a_later_same_thread_anchor() -> None:
    first = _message(
        "source-first",
        "Please review the authorization.",
        occurred_at="2034-02-01T10:00:00Z",
    )
    later_anchor = _message(
        "source-later",
        "A later provider-thread update.",
        occurred_at="2034-02-01T11:00:00Z",
    )
    related = _message(
        "source-related",
        "The authorization is ready.",
        thread_id="thread-service",
        occurred_at="2034-02-01T10:30:00Z",
        references=["<source-first@example.invalid>"],
    )
    initial = await build_message_context_pack(
        later_anchor, _Log([first, related, later_anchor])
    )
    related_context = initial.messages[-1]
    assert related_context.source_event_id == "source-related"
    assert related_context.relationship_source_event_ids == ("source-first",)

    rejected = _context_decision(
        "correction-later-anchor",
        "context.relationship.rejected",
        "source-first",
        "source-related",
    )
    rebuilt = await build_message_context_pack(
        later_anchor, _Log([first, related, later_anchor, rejected])
    )

    assert [message.source_event_id for message in rebuilt.messages] == [
        "source-first",
        "source-later",
    ]


@pytest.mark.asyncio
async def test_gmail_message_local_attachment_ids_are_not_relationship_edges() -> None:
    local_attachment = {
        "filename": "form.pdf",
        "provider_attachment_id": "fixture-local-id",
    }
    anchor = _message(
        "source-anchor",
        "Please review the attached form.",
        attachments=[local_attachment],
        provider="gmail",
    )
    related = _message(
        "source-related",
        "The attached form is ready.",
        thread_id="thread-service",
        attachments=[local_attachment],
        provider="gmail",
    )

    pack = await build_message_context_pack(anchor, _Log([anchor, related]))

    assert [message.source_event_id for message in pack.messages] == ["source-anchor"]


@pytest.mark.asyncio
async def test_source_text_correction_suppresses_and_reinstatement_restores() -> None:
    body = "Please wire the fixture funds. Also please archive the fixture receipt."
    quote = "Please wire the fixture funds."
    anchor = _message("source-anchor", body)
    corrected = _context_decision(
        "correction-1", "context.source_text.corrected", "source-anchor"
    )
    corrected["payload"].update(
        {
            "correction_id": "text-fixture",
            "proposal_id": "proposal-fixture",
            "candidate_key": "candidate-fixture",
            "algorithm": "authored-message-text-v2",
            "authored_text_digest": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "evidence_start": 0,
            "evidence_end": len(quote),
            "evidence_quote_digest": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        }
    )
    reinstated = _context_decision(
        "correction-2", "context.source_text.reinstated", "source-anchor"
    )
    reinstated["payload"]["correction_id"] = "text-fixture"

    suppressed = await build_message_context_pack(anchor, _Log([anchor, corrected]))
    restored = await build_message_context_pack(
        anchor, _Log([anchor, corrected, reinstated])
    )

    assert (
        suppressed.messages[0].body_text == "Also please archive the fixture receipt."
    )
    assert restored.messages[0].body_text == body
