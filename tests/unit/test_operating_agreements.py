"""Staff authority remains bounded and constitutional revisions retain history."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import sqlcipher3

from adminme.events.schemas.operating_agreement import (
    ConstitutionalAgreementRegisteredV1,
)
from adminme.lib.session import build_internal_session
from adminme.operating_agreements import (
    CONSTITUTIONAL_AGREEMENT_ID,
    constitutional_agreement_payload,
)
from adminme.projections.parties import PartiesProjection
from adminme.projections.parties.queries import (
    effective_operating_agreements,
    get_operating_agreement,
    list_operating_agreements,
)


REGISTERED_AT = "2026-10-23T10:00:00Z"
AS_OF = "2026-10-23T11:00:00Z"


def _payload(root: Path, *, adults: list[str] | None = None) -> dict[str, Any]:
    return constitutional_agreement_payload(
        adult_principal_ids=adults or ["member-a", "member-b"],
        registered_at=REGISTERED_AT,
        repository_root=root,
    )


def test_staff_rule_uses_standing_authority_without_granting_external_effects(
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path)
    validated = ConstitutionalAgreementRegisteredV1.model_validate(payload)
    assert validated.agreement_id == CONSTITUTIONAL_AGREEMENT_ID
    assert validated.revision == 2
    rule = validated.rule
    assert rule.rule_id == "household-cos-safety-v2"
    assert {
        "routine_cross_source_coordination_with_current_source_grants",
        "service_private_notes_memory_and_scratchpad_with_source_provenance",
        "reuse_current_standing_authority_without_repeat_approval",
    } <= set(rule.permitted_internal_processing)
    assert "cross_member_private_evidence_disclosure" not in rule.prohibitions
    assert {
        "unauthorized_cross_member_private_evidence_disclosure",
        "disclosure_contrary_to_explicit_client_confidentiality",
        "technical_access_or_work_computer_treated_as_disclosure_authority",
        "derived_work_losing_source_restrictions",
        "provider_execution_without_independent_domain_and_openclaw_approval",
        "self_activating_policy_or_trust_expansion",
    } <= set(rule.prohibitions)
    assert "source_provenance" in rule.required_fact_types
    assert "explicit_client_confidentiality" in rule.required_fact_types
    assert "current_grant_for_each_source_and_destination" in (
        rule.verification_requirements
    )
    assert "recheck_source_authority_on_reuse_after_revocation_or_expiry" in (
        rule.reconciliation_requirements
    )
    assert validated.provider_effect_count == 0
    assert rule.provider_execution_permitted is False
    assert rule.independent_openclaw_approval_required is True
    assert rule.source_policy_embedded is False
    assert rule.provider_ids == rule.provider_account_references == []
    assert rule.destination_ids == rule.ceilings.permitted_destination_ids == []
    assert rule.trust_scope.providers == rule.trust_scope.destination_ids == []
    assert rule.trust_scope.provider_account_references == []
    assert rule.ceilings.financial_micros == rule.ceilings.maximum_quantity == 0


def test_constitution_definition_identity_ignores_registration_and_member_order(
    tmp_path: Path,
) -> None:
    first = _payload(tmp_path, adults=["member-b", "member-a", "member-a"])
    later = constitutional_agreement_payload(
        adult_principal_ids=["member-a", "member-b"],
        registered_at=AS_OF,
        repository_root=tmp_path,
    )
    assert first["rule"] == later["rule"]
    assert first["definition_digest"] == later["definition_digest"]
    expected = hashlib.sha256(
        json.dumps(first["rule"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert first["definition_digest"] == expected


def _event(payload: dict[str, Any], event_id: str) -> dict[str, Any]:
    ConstitutionalAgreementRegisteredV1.model_validate(payload)
    return {
        "type": "operating_agreement.constitution_registered",
        "tenant_id": "tenant-fixture",
        "event_id": event_id,
        "source_adapter": "constitutional-operating-agreement",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "sensitivity": "normal",
        "payload": payload,
    }


@pytest.mark.parametrize("prior_first", [True, False], ids=["upgrade", "late-prior"])
def test_constitution_upgrade_retains_prior_references_and_replays_additively(
    tmp_path: Path,
    prior_first: bool,
) -> None:
    current = _payload(tmp_path)
    prior = deepcopy(current)
    prior["revision"] = 1
    prior["rule"]["rule_id"] = "household-cos-safety-v1"
    prior["rule"]["prohibitions"] = ["cross_member_private_evidence_disclosure"]
    prior["definition_digest"] = hashlib.sha256(
        json.dumps(prior["rule"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    events = [_event(prior, "constitution-v1"), _event(current, "constitution-v2")]
    if not prior_first:
        events.reverse()
    projection = PartiesProjection()
    member = build_internal_session("member-a", "principal", "tenant-fixture")
    other_tenant = build_internal_session("member-a", "principal", "tenant-other")
    snapshots = []
    for replay in (False, True):
        conn = sqlcipher3.connect(":memory:")
        conn.row_factory = sqlcipher3.Row
        try:
            conn.executescript(projection.schema_path.read_text())
            for event in events:
                projection.apply(event, conn)
            if not replay:
                # Re-registering the same release is harmless and cannot replace v1.
                projection.apply(events[-1], conn)
            historical = get_operating_agreement(
                conn,
                member,
                agreement_id=CONSTITUTIONAL_AGREEMENT_ID,
                revision=1,
                as_of_iso=AS_OF,
            )
            assert historical is not None
            assert historical["effective_state"] == "superseded"
            assert historical["rule"] == prior["rule"]
            assert historical["definition_digest"] == prior["definition_digest"]
            assert historical["last_event_id"] == "constitution-v1"
            active = effective_operating_agreements(conn, member, as_of_iso=AS_OF)
            assert len(active) == 1
            assert active[0]["revision"] == 2
            assert active[0]["rule"] == current["rule"]
            assert active[0]["provider_execution_permitted"] is False
            rows = list_operating_agreements(conn, member, as_of_iso=AS_OF)
            assert len(rows) == 2
            assert list_operating_agreements(conn, other_tenant, as_of_iso=AS_OF) == []
            snapshots.append(rows)
        finally:
            conn.close()
    assert snapshots[0] == snapshots[1]
