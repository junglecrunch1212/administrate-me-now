"""Exact, authorized later evidence may identify only its original action."""

from dataclasses import replace

import pytest

from adminme.message_context import ContextMessage, MessageContextPack
from adminme.triage_intent import extract_grounded_related_completions


ACTION = "Please schedule the blue workshop inspection."
OTHER_ACTION = "Please prepare the separate inventory."
DONE = "Scheduling the blue workshop inspection is complete."


def _fixture():
    primary = ContextMessage(
        alias="m01",
        source_event_id="later-source",
        relation="thread",
        relation_reason_codes=("provider_thread_id",),
        relationship_source_event_ids=(),
        direction="inbound",
        occurred_at="2034-02-03T10:00:00Z",
        source_channel="gmail",
        sender="fixture@example.invalid",
        recipients=("member@example.invalid",),
        subject="Synthetic inspection",
        body_text=DONE,
        authored_text_reliable=True,
        authored_text_reason_codes=(),
        attachment_names=(),
    )
    original = replace(
        primary,
        alias="m02",
        source_event_id="original-source",
        relation="related",
        relation_reason_codes=("rfc_reference",),
        relationship_source_event_ids=(primary.source_event_id,),
        occurred_at="2034-02-02T10:00:00Z",
        body_text=f"{ACTION} {OTHER_ACTION}",
    )
    pack = MessageContextPack(
        anchor_event_id=primary.source_event_id,
        thread_key="synthetic-thread",
        fingerprint="f" * 64,
        messages=(primary, original),
        episode_source_event_ids=(primary.source_event_id,),
        current_is_thread_anchor=True,
    )
    raw = {
        "target_kind": "task",
        "evidence_source_id": "m02",
        "evidence_quote": ACTION,
        "candidate": {},
        "action_owner": "member",
        "lifecycle_state": "completed",
        "obligation_assessment": {
            "purpose": "preparation",
            "actionability": "none",
            "condition_state": "not_applicable",
            "qualifying_evidence": [
                {"source_id": "m01", "quote": DONE, "role": "completion"}
            ],
        },
    }
    return pack, raw


def test_later_linked_completion_returns_only_exact_source_kind_and_action():
    pack, raw = _fixture()
    result = extract_grounded_related_completions(pack, [raw])
    assert result == {("original-source", "task", ACTION)}
    assert ("original-source", "task", OTHER_ACTION) not in result
    assert ("original-source", "commitment", ACTION) not in result
    assert ("another-source", "task", ACTION) not in result


@pytest.mark.parametrize(
    ("target", "changes"),
    [
        ("raw", {"evidence_source_id": "m99"}),
        ("raw", {"evidence_source_id": "original-source"}),
        ("original", {"relation": "weak"}),
        ("original", {"relationship_source_event_ids": ()}),
        ("original", {"relationship_source_event_ids": ("foreign-source",)}),
        ("original", {"authored_text_reliable": False}),
        ("primary", {"authored_text_reliable": False}),
        ("primary", {"occurred_at": "2034-02-01T10:00:00Z"}),
        ("primary", {"occurred_at": "2034-02-02T10:00:00Z"}),
        ("primary", {"occurred_at": "not-a-source-date"}),
        ("qualifier", {"source_id": "m99"}),
        ("qualifier", {"quote": "An invented completion."}),
        ("qualifier", {"role": "negation"}),
        ("raw", {"evidence_quote": "Please schedule an unrelated inspection."}),
        ("raw", {"action_owner": "other"}),
        ("raw", {"action_owner": "unknown"}),
        ("raw", {"lifecycle_state": "open"}),
        ("raw", {"lifecycle_state": "unknown"}),
        ("pack", {"current_is_thread_anchor": False}),
    ],
)
def test_unqualified_or_unrelated_completion_has_no_effect(target, changes):
    pack, raw = _fixture()
    primary, original = pack.messages
    if target == "raw":
        raw.update(changes)
    elif target == "qualifier":
        raw["obligation_assessment"]["qualifying_evidence"][0].update(changes)
    elif target == "pack":
        pack = replace(pack, **changes)
    else:
        if target == "primary":
            primary = replace(primary, **changes)
        else:
            original = replace(original, **changes)
        pack = replace(pack, messages=(primary, original))
    assert extract_grounded_related_completions(pack, [raw]) == set()
