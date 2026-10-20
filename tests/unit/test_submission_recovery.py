"""Exact target selection rejects stale, ambiguous, or differently scoped facts."""

import copy
import hashlib
import json
from typing import Any

import pytest

from adminme.submission_recovery import (
    SubmissionRecoveryRefused,
    build_submission_recovery_requests,
    validate_submission_recovery_target,
)


class Log:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def get(self, event_id: str):
        return next((e for e in self.events if e["event_id"] == event_id), None)

    async def get_by_causation(self, event_id: str):
        return [e for e in self.events if e.get("causation_id") == event_id]

    async def get_by_idempotency(self, tenant_id: str, key: str):
        return next(
            (
                e
                for e in self.events
                if e.get("tenant_id") == tenant_id and e.get("idempotency_key") == key
            ),
            None,
        )

    async def latest_event_id(self):
        return self.events[-1]["event_id"] if self.events else None

    async def read_since(self, *, owner_scope=None, types=None):
        for event in self.events:
            if (owner_scope is None or event.get("owner_scope") == owner_scope) and (
                types is None or event["type"] in types
            ):
                yield event


def records():
    scope = {
        "tenant_id": "tenant-fixture",
        "owner_scope": "private:member-fixture",
        "visibility_scope": "private:member-fixture",
        "sensitivity": "normal",
    }
    source = {
        **scope,
        "event_id": "0001-source",
        "type": "triage.intent.submitted",
        "schema_version": 2,
        "source_adapter": "member-openclaw",
        "actor_identity": "member-fixture",
        "payload": {
            "intent_id": "intent-fixture",
            "member_id": "member-fixture",
            "source_surface": "openclaw",
            "text": "Call the school office.",
            "submission_kind": "member_forwarded",
            "evidence_complete": True,
            "evidence_issues": [],
        },
    }
    payload = {
        "intent_id": "intent-fixture",
        "source_event_id": source["event_id"],
        "extractor_id": "member-intent-hybrid-v2",
        "candidates": [],
        "extraction_state": "defensive_default",
        "assessment_outcome": "failed",
        "reason_codes": [
            "member_text_requires_human_interpretation",
            "language_seam_OpenClawTimeout",
        ],
    }
    feature = {
        **scope,
        "event_id": "0002-feature",
        "type": "triage.intent.features_extracted",
        "schema_version": 3,
        "source_adapter": "member-intent-hybrid-v2",
        "source_account_id": "member-fixture",
        "actor_identity": "pipeline:member-intent-hybrid-v2",
        "causation_id": source["event_id"],
        "idempotency_key": f"triage-intent-features:{source['event_id']}:member-intent-hybrid-v2",
        "payload": payload,
    }
    feature["raw_ref"] = (
        "features-sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    target = {
        "source_event_id": source["event_id"],
        "failed_assessment_event_id": feature["event_id"],
        "member_id": "member-fixture",
        "expected_failure_reason": "language_seam_OpenClawTimeout",
    }
    return source, feature, target


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "tenant",
        "member",
        "scope",
        "actor",
        "legacy",
        "manual",
        "incomplete",
        "issues",
        "feature_scope",
        "fingerprint",
        "feature_actor",
        "ambiguous_reason",
        "nontransient",
        "newer_feature",
        "hold",
        "decision",
        "proposal",
    ],
)
async def test_exact_failed_forward_target_selection(defect: str) -> None:
    source, feature, target = records()
    events = [source, feature]
    if defect == "tenant":
        source["tenant_id"] = "other-tenant"
    elif defect == "member":
        target["member_id"] = "other-member"
    elif defect == "scope":
        source["visibility_scope"] = "shared:household"
    elif defect == "actor":
        source["actor_identity"] = "other-member"
    elif defect == "legacy":
        source["schema_version"] = 1
    elif defect == "manual":
        source["payload"]["submission_kind"] = "manual"
    elif defect == "incomplete":
        source["payload"]["evidence_complete"] = False
    elif defect == "issues":
        source["payload"]["evidence_issues"] = ["attachment_unavailable"]
    elif defect == "feature_scope":
        feature["owner_scope"] = "private:other-member"
    elif defect == "fingerprint":
        feature["raw_ref"] = "features-sha256:" + "f" * 64
    elif defect == "feature_actor":
        feature["actor_identity"] = "other-member"
    elif defect in {"ambiguous_reason", "nontransient"}:
        feature["payload"]["reason_codes"] = (
            ["language_seam_OpenClawTimeout", "language_seam_OpenClawUnreachable"]
            if defect == "ambiguous_reason"
            else ["language_seam_SkillOutputInvalid"]
        )
        feature["raw_ref"] = (
            "features-sha256:"
            + hashlib.sha256(
                json.dumps(
                    feature["payload"], sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
    elif defect == "newer_feature":
        events.append({**copy.deepcopy(feature), "event_id": "0003-newer"})
    elif defect in {"hold", "decision", "proposal"}:
        events.append(
            {
                "event_id": "0003-action",
                "tenant_id": source["tenant_id"],
                "owner_scope": source["owner_scope"],
                "type": {
                    "hold": "triage.intent.publication_held",
                    "decision": "triage.item.dismissed",
                    "proposal": "triage.item.proposed",
                }[defect],
                "idempotency_key": f"triage-intent-held:{source['event_id']}"
                if defect == "hold"
                else None,
                "payload": {"proposal_id": source["payload"]["intent_id"]},
            }
        )
    if defect == "none":
        validated = await validate_submission_recovery_target(
            Log(events), target, tenant_id="tenant-fixture"
        )
        assert (
            validated["source"] == source and validated["failed_assessment"] == feature
        )
        assert validated["recovery_attempt"] == 1
    else:
        with pytest.raises(SubmissionRecoveryRefused):
            await validate_submission_recovery_target(
                Log(events), target, tenant_id="tenant-fixture"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 9])
async def test_recovery_batch_limit_precedes_any_source_read(count: int) -> None:
    _, _, target = records()
    with pytest.raises(SubmissionRecoveryRefused, match="recovery_batch_limit"):
        await build_submission_recovery_requests(
            Log([]),
            request_id="fixture-request",
            targets=[target] * count,
            tenant_id="tenant-fixture",
            operator_principal_id="operator:fixture",
            owner_member_id="member-fixture",
            owner_authorization_sha256="a" * 64,
            candidate_commit="b" * 40,
        )
