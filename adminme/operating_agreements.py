"""Code-owned constitutional floor for structured CoS operating agreements."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID


CONSTITUTIONAL_AGREEMENT_ID = "constitutional:household-cos-safety"
CONSTITUTIONAL_AGREEMENT_REVISION = 1
CONSTITUTIONAL_AGREEMENT_VERSION = "household-cos-safety-v1"


def _release_commit(repository_root: Path, definition_digest: str) -> str:
    configured = os.environ.get("ADMINME_REVIEWED_COMMIT", "").strip()
    if re.fullmatch(r"[0-9a-f]{7,64}", configured):
        return configured
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        commit = result.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{40}", commit):
            return commit
    except (OSError, subprocess.SubprocessError):
        pass
    # Exported release trees may omit .git.  The definition digest remains an
    # immutable release-content identity instead of inventing a semantic label.
    return definition_digest[:40]


def constitutional_agreement_payload(
    *,
    adult_principal_ids: list[str],
    registered_at: str,
    repository_root: Path,
) -> dict[str, Any]:
    adults = sorted(set(adult_principal_ids)) or ["constitutional:household"]
    rule: dict[str, Any] = {
        "rule_id": CONSTITUTIONAL_AGREEMENT_VERSION,
        "acting_principal_ids": [COS_SERVICE_PRINCIPAL_ID],
        "represented_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "active_member_ids": adults,
        "on_behalf_of_member_ids": adults,
        "resource_owner_principal_ids": [*adults, COS_SERVICE_PRINCIPAL_ID],
        "provider_account_owner_principal_ids": [*adults, COS_SERVICE_PRINCIPAL_ID],
        "responsible_party_ids": [COS_PARTY_ID],
        "beneficiary_party_ids": adults,
        "purpose": "Constitutional safety and privacy floor for household CoS work",
        "domains": ["household_operations"],
        "trigger": "Every Chief-of-Staff observation, coordination, preparation, or action",
        "object_kinds": [
            "task",
            "commitment",
            "coordination",
            "calendar_event",
            "list_item",
            "operating_agreement",
        ],
        "provider_ids": [],
        "provider_account_references": [],
        "workspace_references": [],
        "resource_references": [],
        "destination_ids": [],
        "audience_ids": adults,
        "required_fact_types": [
            "exact_actor",
            "exact_scope",
            "fresh_authority_grant",
            "privacy_classification",
        ],
        "maximum_fact_age_seconds": 300,
        "required_fact_coverage": "complete",
        "permitted_observations": ["authorized_scoped_observation"],
        "permitted_internal_processing": [
            "deterministic_projection",
            "typed_coordination",
            "bounded_openclaw_llm_task",
        ],
        "permitted_disclosures": ["minimum_typed_coordination_fields"],
        "permitted_proposal_preparation": ["propose", "prepare", "hold"],
        "prohibitions": [
            "cross_member_private_evidence_disclosure",
            "provider_execution_without_independent_domain_and_openclaw_approval",
            "service_principal_impersonating_member",
            "self_activating_policy_or_trust_expansion",
            "global_trust_score",
            "source_policy_embedded_in_operating_agreement",
        ],
        "independent_openclaw_approval_required": True,
        "ceilings": {
            "financial_micros": 0,
            "maximum_duration_minutes": None,
            "maximum_frequency_count": None,
            "frequency_period": None,
            "maximum_quantity": 0,
            "permitted_destination_ids": [],
        },
        "verification_requirements": [
            "terminal_result_if_execution_is_later_authorized",
            "privacy_and_scope_verification",
        ],
        "reconciliation_requirements": [
            "later_observation_if_execution_is_later_authorized",
            "suspend_on_missing_or_conflicting_result",
        ],
        "trust_scope": {
            "granting_member_ids": adults,
            "capability_ids": ["household_cos_internal_coordination"],
            "domains": ["household_operations"],
            "providers": [],
            "provider_account_references": [],
            "action_verbs": ["observe", "suggest", "draft", "prepare", "hold"],
            "destination_ids": [],
            "sensitivity_levels": ["normal", "sensitive", "privileged"],
            "stage": "permanent_ceiling_or_refusal",
            "effective_from": None,
            "effective_until": None,
        },
        "provider_execution_permitted": False,
        "source_policy_embedded": False,
    }
    definition = json.dumps(rule, sort_keys=True, separators=(",", ":"))
    definition_digest = hashlib.sha256(definition.encode("utf-8")).hexdigest()
    return {
        "agreement_id": CONSTITUTIONAL_AGREEMENT_ID,
        "revision": CONSTITUTIONAL_AGREEMENT_REVISION,
        "rule": rule,
        "registered_at": registered_at,
        "release_commit": _release_commit(repository_root, definition_digest),
        "definition_digest": definition_digest,
        "provider_effect_count": 0,
    }


__all__ = [
    "CONSTITUTIONAL_AGREEMENT_ID",
    "CONSTITUTIONAL_AGREEMENT_REVISION",
    "CONSTITUTIONAL_AGREEMENT_VERSION",
    "constitutional_agreement_payload",
]
