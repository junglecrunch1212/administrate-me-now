"""Minimum structured CoS instructions compiled for one active member context."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from adminme.capability_catalog import compose_member_catalog
from adminme.finance.capabilities import core_pointer
from adminme.openclaw_topology import member_agent_id
from adminme.projections.parties.queries import (
    effective_operating_agreements,
    list_service_delegations,
)
from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID


COMPILED_CONTEXT_CONTRACT = "administrateme-effective-cos-context-v1"


def plain_operating_agreement(agreement: dict[str, Any]) -> dict[str, Any]:
    """Render bounded structured truth without turning prose into authority."""

    rule = agreement.get("rule") or {}
    trust = rule.get("trust_scope") or {}
    action_verbs = [str(value) for value in trust.get("action_verbs") or []]
    stage = str(trust.get("stage") or "observe")
    prohibitions = [str(value) for value in rule.get("prohibitions") or []]
    return {
        "agreement_id": agreement.get("agreement_id"),
        "revision": agreement.get("revision"),
        "layer": agreement.get("layer"),
        "authority_scope": agreement.get("authority_scope"),
        "effective_state": agreement.get("effective_state"),
        "effective_from": agreement.get("effective_from"),
        "expires_at": agreement.get("expires_at"),
        "approvals": [
            {
                "member_id": value.get("member_id"),
                "approved_at": value.get("approved_at"),
            }
            for value in agreement.get("approvals") or []
        ],
        "trust_stage": stage,
        "capability_ids": [str(value) for value in trust.get("capability_ids") or []],
        "domains": [str(value) for value in trust.get("domains") or []],
        "may_observe": [
            str(value) for value in rule.get("permitted_observations") or []
        ],
        "may_suggest": "suggest" in action_verbs,
        "may_draft": "draft" in action_verbs,
        "may_prepare": [
            str(value) for value in rule.get("permitted_proposal_preparation") or []
        ],
        "may_execute_after_asking": bool(
            "execute" in action_verbs
            and stage in {"execute_with_exact_approval", "time_limited_trial"}
        ),
        "may_execute_within_standing_envelope": bool(
            "execute" in action_verbs and stage == "standing_bounded_authority"
        ),
        "must_always_ask": [
            "OpenClaw approval is separate from AdministrateMe authority"
        ]
        if rule.get("independent_openclaw_approval_required") is True
        else [],
        "prohibited": prohibitions,
        "provider_execution_permitted": agreement.get("provider_execution_permitted")
        is True,
        "markdown_is_authority": False,
    }


def compile_effective_cos_context(
    runtime: Any,
    session: Any,
    *,
    settings_state: dict[str, Any] | None = None,
    as_of_iso: str | None = None,
) -> dict[str, Any]:
    """Compile only minimum member-scoped instructions from authoritative state."""

    state = settings_state or runtime.settings_control.read()
    as_of = as_of_iso or datetime.now(timezone.utc).isoformat()
    agreements = effective_operating_agreements(
        runtime.projections.connection("parties"), session, as_of_iso=as_of
    )
    delegations = list_service_delegations(
        runtime.projections.connection("parties"),
        session,
        service_principal_id=COS_SERVICE_PRINCIPAL_ID,
    )
    catalog = compose_member_catalog(runtime, session, settings_state=state)
    member_id = session.view_member_id
    preferences = (state.get("member_preferences") or {}).get(member_id) or {}
    source_rows = (state.get("source_policies_v2") or {}).get(member_id) or {}
    effective_capabilities = [
        {
            "capability_id": item["capability_id"],
            "trust_stage": item["trust_stage"],
            "read_available": item["effective"]["read_available"],
            "execute_available": item["effective"]["execute_available"],
            "effective_tools": item["openclaw"]["effective_tools"],
        }
        for item in catalog["items"]
        if item["effective"]["read_available"]
        or item["effective"]["execute_available"]
        or item["openclaw"]["effective_tools"]
    ]
    value: dict[str, Any] = {
        "contract": COMPILED_CONTEXT_CONTRACT,
        "as_of": as_of,
        "authority_source": "typed events and deterministic projections",
        "markdown_is_authority": False,
        "identity": {
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "service_party_id": COS_PARTY_ID,
            "member_agent_id": member_agent_id(member_id),
            "active_member_id": member_id,
            "read_member_id": session.view_member_id,
            "action_member_id": session.auth_member_id,
            "on_behalf_of_member_id": member_id,
            "role": session.view_role,
            "view_as": session.auth_member_id != session.view_member_id,
            "isolated_member_context": True,
        },
        "preferences": dict(sorted(preferences.items())),
        "agreements": [plain_operating_agreement(value) for value in agreements],
        "delegations": [
            {
                "grant_id": value["grant_id"],
                "context_scope": value["context_scope"],
                "active": value["active"],
                "permissions": value["permissions"],
                "expires_at": value.get("expires_at"),
                "revoked_at": value.get("revoked_at"),
            }
            for value in delegations
            if value.get("active") is True
            if value.get("context_member_id") in {None, member_id}
        ],
        "source_policy": {
            "contract": "per-principal-per-source-policy-v2-shadow",
            "revision": (state.get("source_policy_v2_revisions") or {}).get(
                member_id, 0
            ),
            "entries": [
                {
                    "policy_key": policy_key,
                    "revision": record.get("revision"),
                    "preset": (record.get("policy") or {}).get("preset"),
                    "acquisition_state": (record.get("policy") or {}).get(
                        "acquisition_state"
                    ),
                    "triage_mode": (record.get("policy") or {}).get("triage_mode"),
                    "alert_mode": (record.get("policy") or {}).get("alert_mode"),
                    "context_mode": (record.get("policy") or {}).get("context_mode"),
                    "explanation": (
                        f"Acquisition is {(record.get('policy') or {}).get('acquisition_state', 'unknown')}; "
                        f"Triage is {(record.get('policy') or {}).get('triage_mode', 'unknown')}; "
                        f"alerts are {(record.get('policy') or {}).get('alert_mode', 'unknown')}; "
                        f"context is {(record.get('policy') or {}).get('context_mode', 'unknown')}."
                    ),
                }
                for policy_key, record in sorted(source_rows.items())
                if isinstance(record, dict)
            ],
            "provider_execution_authority": False,
        },
        "capabilities": effective_capabilities,
        "finance": core_pointer(
            enabled=bool(
                getattr(runtime.settings, "finance_publications_enabled", False)
            )
        ),
        "safety": {
            "observation_mode": bool(runtime._observation_active),
            "catalog_grants_authority": False,
            "preferences_grant_authority": False,
            "openclaw_approval_is_domain_authority": False,
            "provider_effect_count": 0,
        },
        "excluded": [
            "credentials",
            "secure_references",
            "raw_private_evidence",
            "other_member_private_rules",
            "provider_bodies",
            "shared_raw_inboxes",
            "generic_household_memory",
            "conversation_transcripts",
            "infrastructure_administration",
        ],
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    value["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return value


__all__ = [
    "COMPILED_CONTEXT_CONTRACT",
    "compile_effective_cos_context",
    "plain_operating_agreement",
]
