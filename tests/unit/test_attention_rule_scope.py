"""Exact protected-source conjunctions and versioned event compatibility."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from adminme.events.registry import registry
from adminme.events.schemas.triage_management import (
    AttentionRuleV1,
    AttentionRuleV2,
    TriageAttentionFeedbackRecordedV1,
    TriageAttentionFeedbackRecordedV2,
    TriageAttentionRuleSavedV1,
    TriageAttentionRuleSavedV2,
)
from adminme.products.member.main import (
    _attention_event_version,
    _attention_request_fingerprint,
    _triage_request_fingerprint,
)
from adminme.triage_management import (
    attention_match,
    attention_rule_preview,
    resolve_attention_rules,
)


def _rule():
    return {
        "conditions": {
            "adapter_instance_id": "fixture-enrollment",
            "provider_account_id": "fixture-account",
            "native_container_id": "fixture-conversation",
        },
        "action": "reference",
    }


def test_protected_source_is_an_exact_term_and_strictly_narrows_specificity():
    broad = AttentionRuleV2.model_validate(_rule()).model_dump(exclude_none=True)
    narrow = AttentionRuleV2.model_validate(
        {
            **_rule(),
            "conditions": {
                **_rule()["conditions"],
                "protected_source_id": "fixture-source-a",
            },
            "action": "review",
        }
    ).model_dump(exclude_none=True)
    matching = {"reconciliation": {"scope": narrow["conditions"]}}
    different = {
        "reconciliation": {
            "scope": {**narrow["conditions"], "protected_source_id": "fixture-source-b"}
        }
    }
    missing = {"reconciliation": {"scope": broad["conditions"]}}
    assert attention_match(narrow, matching) == "match"
    assert attention_match(narrow, different) == "nonmatch"
    assert attention_match(narrow, missing) == "nonmatch"
    assert attention_match(broad, different) == "match"
    assert attention_match(broad, missing) == "match"
    assert resolve_attention_rules(
        [{**broad, "rule_id": "broad"}, {**narrow, "rule_id": "narrow"}]
    ) == {"action": "review", "rule_ids": ["narrow"], "conflict": False}
    excepted = {
        **broad,
        "exceptions": [{"protected_source_id": "fixture-source-b"}],
    }
    assert attention_match(excepted, matching) == "match"
    assert attention_match(excepted, different) == "exception"
    assert _attention_event_version(excepted) == 2
    assert (
        attention_rule_preview(narrow, [matching])["preview_digest"]
        != (attention_rule_preview(narrow, [different])["preview_digest"])
    )


def test_optional_scope_preserves_v1_requests_and_exact_account_requirements():
    old = AttentionRuleV1.model_validate(_rule())
    current = AttentionRuleV2.model_validate(_rule())
    assert _attention_request_fingerprint("fixture-rule", current) == (
        _triage_request_fingerprint("fixture-rule", old)
    )
    assert _attention_event_version(current.model_dump(exclude_none=True)) == 1
    for invalid in (None, ""):
        with pytest.raises(ValidationError):
            AttentionRuleV2.model_validate(
                {
                    **_rule(),
                    "conditions": {
                        **_rule()["conditions"],
                        "provider_account_id": invalid,
                        "protected_source_id": "fixture-source-a",
                    },
                }
            )
    with pytest.raises(ValidationError):
        AttentionRuleV2.model_validate(
            {
                **_rule(),
                "conditions": {**_rule()["conditions"], "protected_source_id": ""},
            }
        )


@pytest.mark.parametrize(
    "event_type", ["triage.attention_rule.saved", "triage.attention_feedback.recorded"]
)
def test_v1_attention_event_shapes_remain_registered_and_upcast_without_inferred_scope(
    event_type,
):
    now = datetime.now(timezone.utc).isoformat()
    if event_type == "triage.attention_rule.saved":
        payload = {
            **_rule(),
            "rule_id": "fixture-rule",
            "member_id": "fixture-member",
            "preview_digest": "a" * 64,
            "mode": "active",
            "starts_at": now,
            "saved_by_member_id": "fixture-member",
        }
        old_model, current_model = (
            TriageAttentionRuleSavedV1,
            TriageAttentionRuleSavedV2,
        )
    else:
        payload = {
            "feedback_id": "fixture-feedback",
            "member_id": "fixture-member",
            "proposal_id": "fixture-proposal",
            "source_event_id": "fixture-source-event",
            "independent_observation_key": "fixture-independent-observation",
            "kind": "future_attention_preference",
            "rule": _rule(),
            "recorded_by_member_id": "fixture-member",
            "recorded_at": now,
        }
        old_model, current_model = (
            TriageAttentionFeedbackRecordedV1,
            TriageAttentionFeedbackRecordedV2,
        )
    assert registry.get(event_type, 1) is old_model
    assert registry.get(event_type, 2) is current_model
    before = json.dumps(payload, sort_keys=True)
    old = old_model.model_validate(payload).model_dump(mode="json", exclude_none=True)
    upcast = registry.upcast(event_type, payload, from_version=1, to_version=2)
    assert (
        current_model.model_validate(upcast).model_dump(mode="json", exclude_none=True)
        == old
    )
    assert json.dumps(payload, sort_keys=True) == before
    scoped = json.loads(before)
    rule = scoped if "conditions" in scoped else scoped["rule"]
    rule["conditions"]["protected_source_id"] = "fixture-source-a"
    assert _attention_event_version(scoped) == 2
    assert current_model.model_validate(scoped)
    with pytest.raises(ValidationError):
        old_model.model_validate(scoped)
