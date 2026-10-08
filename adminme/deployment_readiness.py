"""One secret-safe readiness contract shared by API, Settings, and Doctor."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from adminme.bridge_authorization import (
    BRIDGE_PACK_POLICIES,
    authorization_from_record,
    configuration_from_record,
    provider_capability_summary,
)


CENTRAL_HOST_TAG = "tag:administrateme-central"
REQUIRED_DM_SCOPE = "per_account_channel_peer"
DEVICE_CHECK_EXCEPTION_REASON = "owner_requested_device_check_omission"


def device_check_exception_members(value: Any) -> set[str] | None:
    """Validate the exact optional owner-selected device coverage exception."""

    if (
        not isinstance(value, dict)
        or set(value) != {"member_ids", "owner_requested", "reason"}
        or value.get("owner_requested") is not True
        or value.get("reason") != DEVICE_CHECK_EXCEPTION_REASON
    ):
        return None
    members = value.get("member_ids")
    if (
        not isinstance(members, list)
        or not members
        or not all(isinstance(member, str) and member for member in members)
        or members != sorted(set(members))
    ):
        return None
    return set(members)


def _dimension(
    state: str,
    reason: str,
    *,
    required: bool = True,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "required": required,
        "reason": reason,
        "evidence": evidence or {},
        "content_exposed": False,
    }


def _recent_iso(value: Any, *, max_age_days: int = 31) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return 0 <= age.total_seconds() <= max_age_days * 86400


def _historical_boundary(value: Any) -> datetime | None:
    """Validate an observation origin without expiring its retained history."""

    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    parsed = parsed.astimezone(timezone.utc)
    return parsed if parsed <= datetime.now(timezone.utc) else None


def backup_rollback_ready(backup: dict[str, Any]) -> bool:
    """The existing ordinary activation proof; shared without weakening it."""
    return all(
        (
            backup.get("encrypted_backup_authenticated") is True,
            backup.get("restore_rehearsed") is True,
            backup.get("prior_release_retained") is True,
            backup.get("automatic_rollback_eligible") is True,
            _recent_iso(backup.get("tested_at")),
        )
    )


def ordinary_activation_backup_ready(evidence: dict[str, Any]) -> bool:
    """Recognize proof emitted by a fresh ordinary journaled activation."""
    activation = evidence.get("activation_evidence") or {}
    return (
        backup_rollback_ready(evidence.get("backup_rollback") or {})
        and activation.get("contract") == "deployment-activation-evidence-v1"
        and activation.get("crash_recoverable_replacement") is True
        and (
            activation.get("candidate_live_doctor") is True
            or activation.get("candidate_live_doctor") == "pending_post_switch"
        )
        and all(
            isinstance(activation.get(field), str)
            and len(activation[field]) == 64
            and all(character in "0123456789abcdef" for character in activation[field])
            for field in (
                "candidate_pending_attestation_sha256",
                "fresh_backup_artifact_sha256",
                "fresh_rehearsal_receipt_sha256",
                "predecessor_rollback_eligibility_sha256",
            )
        )
    )


def evaluate_deployment_readiness(
    *,
    required: bool,
    host_topology: dict[str, Any],
    deployment_evidence: dict[str, Any],
    member_roles: dict[str, str],
    portal_member_ids: set[str],
    portal_identity_count: int,
    provider_bindings: tuple[Any, ...],
    service_channel_accounts: tuple[Any, ...],
    bridge_enrollments: dict[str, dict[str, Any]],
    openclaw_enabled: bool,
    openclaw_member_ids: set[str],
    openclaw_topology: dict[str, Any],
    observation_active: bool,
    source_assurance: dict[str, Any] | None = None,
    disposable_test_dataset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate independent activation axes without exposing identities."""

    if not required:
        return {
            "contract": "deployment-readiness-v1",
            "state": "not_required",
            "ready": True,
            "dimensions": {},
            "content_exposed": False,
        }

    topology_ok = all(
        (
            host_topology.get("tailscale_node_online") is True,
            host_topology.get("central_host_tag") == CENTRAL_HOST_TAG,
            host_topology.get("central_identity_kind") == "infrastructure_tag",
            host_topology.get("central_identity_collides_with_member") is False,
            host_topology.get("serve_is_sole_edge") is True,
            host_topology.get("python_runtime_count") == 1,
            host_topology.get("openclaw_gateway_count") == 1,
        )
    )
    dimensions: dict[str, dict[str, Any]] = {
        "topology": _dimension(
            "healthy" if topology_ok else "blocked",
            "central host is a tagged infrastructure node behind the one reviewed edge"
            if topology_ok
            else "central host infrastructure identity or singleton topology is unproved",
            evidence={
                "required_tag": CENTRAL_HOST_TAG,
                "tag_present": host_topology.get("central_host_tag")
                == CENTRAL_HOST_TAG,
                "node_online": host_topology.get("tailscale_node_online") is True,
                "identity_kind": host_topology.get("central_identity_kind", "unknown"),
                "member_collision": host_topology.get(
                    "central_identity_collides_with_member"
                ),
                "serve_is_sole_edge": host_topology.get("serve_is_sole_edge"),
                "python_runtime_count": host_topology.get("python_runtime_count"),
                "openclaw_gateway_count": host_topology.get("openclaw_gateway_count"),
            },
        )
    }

    attested_binding = deployment_evidence.get("deployment_binding") or {}
    current_binding = host_topology.get("deployment_binding") or {}
    binding_fields = {
        "release_commit",
        "instance_fingerprint",
        "host_fingerprint",
        "configuration_fingerprint",
    }
    binding_ok = (
        isinstance(attested_binding, dict)
        and isinstance(current_binding, dict)
        and all(
            isinstance(attested_binding.get(field), str)
            and bool(attested_binding[field])
            and attested_binding.get(field) == current_binding.get(field)
            for field in binding_fields
        )
    )
    dimensions["attestation_binding"] = _dimension(
        "healthy" if binding_ok else "blocked",
        "physical attestations match this host, instance, configuration, and release"
        if binding_ok
        else "physical attestations are absent, stale, or belong to another deployment",
        evidence={
            "release_matches": bool(
                attested_binding.get("release_commit")
                and attested_binding.get("release_commit")
                == current_binding.get("release_commit")
            ),
            "instance_matches": bool(
                attested_binding.get("instance_fingerprint")
                and attested_binding.get("instance_fingerprint")
                == current_binding.get("instance_fingerprint")
            ),
            "host_matches": bool(
                attested_binding.get("host_fingerprint")
                and attested_binding.get("host_fingerprint")
                == current_binding.get("host_fingerprint")
            ),
            "configuration_matches": bool(
                attested_binding.get("configuration_fingerprint")
                and attested_binding.get("configuration_fingerprint")
                == current_binding.get("configuration_fingerprint")
            ),
        },
    )

    recovery = deployment_evidence.get("recovery") or {}
    service_account_ok = host_topology.get("service_account_standard") is True
    recovery_ok = all(
        (
            service_account_ok,
            recovery.get("separate_admin_available") is True,
            recovery.get("filevault_unlock_tested") is True,
            recovery.get("automatic_login_disabled") is True,
            _recent_iso(recovery.get("tested_at")),
        )
    )
    dimensions["service_account_recovery"] = _dimension(
        "healthy" if recovery_ok else "blocked",
        "standard service account and recent tested recovery path are ready"
        if recovery_ok
        else "standard-account or anti-lockout recovery evidence is missing",
        evidence={
            "service_account_standard": service_account_ok,
            "separate_admin_available": recovery.get("separate_admin_available")
            is True,
            "filevault_unlock_tested": recovery.get("filevault_unlock_tested") is True,
            "automatic_login_disabled": recovery.get("automatic_login_disabled")
            is True,
            "recent_test": _recent_iso(recovery.get("tested_at")),
        },
    )

    interactive_members = set(portal_member_ids)
    identity = deployment_evidence.get("identity_acceptance") or {}
    accepted_list = identity.get("member_ids")
    accepted_valid = (
        isinstance(accepted_list, list)
        and bool(accepted_list)
        and all(isinstance(member, str) and member for member in accepted_list)
        and len(accepted_list) == len(set(accepted_list))
    )
    accepted = set(accepted_list) if accepted_valid else set()
    exception_present = "device_check_exceptions" in identity
    exceptions = (
        device_check_exception_members(identity["device_check_exceptions"])
        if exception_present
        else set()
    )
    excepted = exceptions or set()
    identity_ok = (
        bool(interactive_members)
        and accepted_valid
        and exceptions is not None
        and not (accepted & excepted)
        and accepted | excepted == interactive_members
        and portal_identity_count == len(interactive_members)
        and (not exception_present or binding_ok)
    )
    dimensions["identity_acceptance"] = _dimension(
        "healthy" if identity_ok else "blocked",
        (
            "tested members passed trusted ingress; an explicit owner-requested "
            "device-check coverage exception remains"
            if exception_present
            else "each enabled interactive member passed the real trusted-ingress path"
        )
        if identity_ok
        else "real trusted-ingress acceptance is incomplete or contradictory",
        evidence={
            "required_member_count": len(interactive_members),
            "trusted_identity_count": portal_identity_count,
            "accepted_member_count": len(accepted & interactive_members),
            "unknown_acceptance_count": len(accepted - interactive_members),
            "device_check_exception_count": len(excepted & interactive_members),
            "unknown_device_check_exception_count": len(excepted - interactive_members),
            "owner_requested_device_check_exception": (
                exception_present and exceptions is not None
            ),
            "all_member_devices_tested": identity_ok and not exception_present,
            "distinct_identity_proof": identity.get("distinct_identities") is True,
            "tested_at_recent": _recent_iso(identity.get("tested_at")),
        },
    )
    if not (
        identity.get("distinct_identities") is True
        and _recent_iso(identity.get("tested_at"))
    ):
        dimensions["identity_acceptance"]["state"] = "blocked"

    dimensions["provider_bindings"] = _dimension(
        "healthy",
        "enabled provider bindings passed global ownership validation",
        evidence={
            "binding_count": len(provider_bindings),
            "owner_kind_counts": {
                owner_kind: sum(
                    binding.owner_kind == owner_kind for binding in provider_bindings
                )
                for owner_kind in ("member", "service_principal")
            },
            "workspace_counts": {
                workspace: sum(
                    binding.workspace == workspace for binding in provider_bindings
                )
                for workspace in ("personal", "work", "service")
            },
        },
    )

    channel_state_counts = {
        state: sum(channel.state == state for channel in service_channel_accounts)
        for state in (
            "planned",
            "account_not_created",
            "account_created",
            "channel_not_enrolled",
            "observation",
            "healthy",
            "degraded",
            "disabled",
            "revoked",
        )
    }
    required_channels = [
        channel
        for channel in service_channel_accounts
        if channel.required_for_activation
    ]
    required_channels_ready = all(
        channel.state == "healthy" for channel in required_channels
    )
    if required_channels and not required_channels_ready:
        channel_state = "blocked"
        channel_reason = "a required service channel is not healthy"
    elif channel_state_counts["degraded"]:
        channel_state = "degraded"
        channel_reason = "a service channel is degraded"
    elif channel_state_counts["observation"]:
        channel_state = "observation"
        channel_reason = "a service channel is enrolled in observation mode"
    elif channel_state_counts["channel_not_enrolled"]:
        channel_state = "channel_not_enrolled"
        channel_reason = "a service account exists but its channel is not enrolled"
    elif channel_state_counts["account_created"]:
        channel_state = "account_created"
        channel_reason = "a service account exists without channel readiness"
    elif channel_state_counts["planned"] or channel_state_counts["account_not_created"]:
        channel_state = "planned"
        channel_reason = (
            "the future service channel is planned and no account is claimed"
        )
    elif service_channel_accounts:
        channel_state = "healthy"
        channel_reason = "configured service-channel states are explicit"
    else:
        channel_state = "disabled"
        channel_reason = "no service channel is selected"
    dimensions["service_channel_identities"] = _dimension(
        channel_state,
        channel_reason,
        required=bool(required_channels),
        evidence={
            "principal_count": len(
                {channel.service_principal_id for channel in service_channel_accounts}
            ),
            "channel_count": len(service_channel_accounts),
            "required_channel_count": len(required_channels),
            "state_counts": channel_state_counts,
            "account_reference_count": sum(
                channel.provider_account_reference is not None
                for channel in service_channel_accounts
            ),
            "enrolled_count": sum(
                channel.enrolled for channel in service_channel_accounts
            ),
            "sending_enabled_count": sum(
                channel.sending_enabled for channel in service_channel_accounts
            ),
        },
    )

    if not bridge_enrollments:
        dimensions["bridges"] = _dimension(
            "not_applicable",
            "no member bridge is selected",
            required=False,
            evidence={"enrollment_count": 0},
        )
    else:
        statuses = [record.get("status") for record in bridge_enrollments.values()]
        capability_counts = provider_capability_summary(
            record for record in bridge_enrollments.values() if isinstance(record, dict)
        )
        invalid_active = 0
        provider_blocked = 0
        for record in bridge_enrollments.values():
            if not isinstance(record, dict) or record.get("status") != "active":
                continue
            configuration = configuration_from_record(record)
            if configuration is None:
                invalid_active += 1
                continue
            provider_packs = {
                pack
                for pack in configuration.packs
                if BRIDGE_PACK_POLICIES[pack].requires_capability_attestation
            }
            authorization = authorization_from_record(record)
            if provider_packs and (
                authorization is None or not provider_packs <= authorization.packs
            ):
                provider_blocked += 1
        bridges_ok = (
            all(status in {"active", "revoked"} for status in statuses)
            and invalid_active == 0
            and provider_blocked == 0
        )
        dimensions["bridges"] = _dimension(
            "healthy" if bridges_ok else "blocked",
            "every selected bridge has a valid transport and every configured provider capability is attested"
            if bridges_ok
            else "a bridge transport, configuration, or provider capability remains unproved",
            evidence={
                "enrollment_count": len(statuses),
                "active_count": statuses.count("active"),
                "revoked_count": statuses.count("revoked"),
                "provisional_count": len(statuses)
                - statuses.count("active")
                - statuses.count("revoked"),
                "invalid_active_count": invalid_active,
                "provider_blocked_count": provider_blocked,
                "provider_capability_counts": capability_counts,
            },
        )

    openclaw = openclaw_topology
    if not openclaw_enabled:
        dimensions["openclaw"] = _dimension(
            "blocked",
            "the required pinned OpenClaw Gateway is disabled",
        )
    else:
        agent_members = set(openclaw.get("member_ids_with_agents") or [])
        account_members = set(openclaw.get("member_ids_with_account_bindings") or [])
        expected = set(openclaw_member_ids)
        openclaw_ok = all(
            (
                openclaw.get("dm_scope") == REQUIRED_DM_SCOPE,
                agent_members == expected,
                account_members == expected,
                openclaw.get("unique_agents") is True,
                openclaw.get("unique_accounts") is True,
                _recent_iso(openclaw.get("tested_at")),
            )
        )
        dimensions["openclaw"] = _dimension(
            "healthy" if openclaw_ok else "blocked",
            "member agents, accounts, actor bindings, and session scope are isolated"
            if openclaw_ok
            else "OpenClaw member isolation topology is incomplete",
            evidence={
                "required_dm_scope": REQUIRED_DM_SCOPE,
                "dm_scope_matches": openclaw.get("dm_scope") == REQUIRED_DM_SCOPE,
                "member_agent_count": len(agent_members),
                "account_binding_count": len(account_members),
                "unique_agents": openclaw.get("unique_agents") is True,
                "unique_accounts": openclaw.get("unique_accounts") is True,
                "recent_test": _recent_iso(openclaw.get("tested_at")),
            },
        )

    backup = deployment_evidence.get("backup_rollback") or {}
    backup_ok = backup_rollback_ready(backup)
    dimensions["backup_rollback"] = _dimension(
        "healthy" if backup_ok else "blocked",
        "authenticated backup, disposable restore, and rollback predecessor are ready"
        if backup_ok
        else "backup authentication, restore rehearsal, or rollback evidence is missing",
        evidence={
            "encrypted_backup_authenticated": backup.get(
                "encrypted_backup_authenticated"
            )
            is True,
            "restore_rehearsed": backup.get("restore_rehearsed") is True,
            "prior_release_retained": backup.get("prior_release_retained") is True,
            "automatic_rollback_eligible": backup.get("automatic_rollback_eligible")
            is True,
            "recent_test": _recent_iso(backup.get("tested_at")),
        },
    )

    if disposable_test_dataset:
        # An explicitly reset test dataset has a different predecessor: its
        # abandoned test history is diagnostic evidence, never a healthy
        # production rollback claim. Require every new reversible-reset proof.
        reset = deployment_evidence.get("disposable_test_reset") or {}
        reset_ok = all(
            (
                observation_active,
                disposable_test_dataset.get("contract") == "disposable-test-dataset-v1",
                reset.get("contract") == "disposable-test-reset-v1",
                reset.get("dataset_id") == disposable_test_dataset.get("dataset_id"),
                reset.get("manifest_sha256")
                == disposable_test_dataset.get("reset_manifest_sha256"),
                reset.get("release_commit") == current_binding.get("release_commit"),
                reset.get("original_state_retained") is True,
                reset.get("original_journal_retained_unchanged") is True,
                reset.get("diagnostic_archive_authenticated") is True,
                reset.get("fresh_dataset_restore_rehearsed") is True,
                reset.get("automatic_state_reversal") is True,
                reset.get("production_rollback_proven") is False,
                isinstance(reset.get("fresh_rehearsal_sha256"), str)
                and len(reset["fresh_rehearsal_sha256"]) == 64,
                _recent_iso(reset.get("tested_at")),
            )
        )
        # A subsequent ordinary activation proves rollback to the accepted
        # bounded dataset using its own fresh artifact and existing predicates.
        # Its configuration binding includes the unchanged dataset marker; the
        # original reset proof is never reattributed to that later release.
        if (
            ordinary_activation_backup_ready(deployment_evidence)
            and binding_ok
            and observation_active
            and disposable_test_dataset.get("contract") == "disposable-test-dataset-v1"
        ):
            dimensions["backup_rollback"]["evidence"].update(
                mode="disposable_test_dataset",
                predecessor_scope="accepted_bounded_dataset",
                abandoned_original_health_proven=False,
            )
        else:
            dimensions["backup_rollback"] = _dimension(
                "healthy" if reset_ok else "blocked",
                "disposable test reset is reversible; production rollback remains unproven"
                if reset_ok
                else "disposable test reset preservation or restore proof is missing",
                evidence={
                    "mode": "disposable_test_dataset",
                    "reset_proofs_valid": reset_ok,
                    "production_rollback_proven": False,
                },
            )

    effects = (source_assurance or {}).get("provider_effects") or {}
    effect_counts = effects.get("counts") or {}
    window = effects.get("observation_window") or {}
    window_counts = window.get("counts") or {}
    window_evidence = deployment_evidence.get("provider_effect_window") or {}
    source_freshness = (source_assurance or {}).get("freshness") or {}
    source_proof_current = all(
        (
            (source_assurance or {}).get("state") == "healthy",
            source_freshness.get("state") == "fresh",
            source_freshness.get("revision_current") is True,
            source_freshness.get("refresh_error_type") is None,
        )
    )
    integrity_ok = effects.get("state") == "healthy"
    window_start = _historical_boundary(window_evidence.get("started_at"))
    audited_window_start = _historical_boundary(window.get("started_at"))
    window_matches = (
        window_start is not None
        and window.get("started_at_configured") is True
        and audited_window_start == window_start
    )
    effects_ok = (
        source_proof_current
        and integrity_ok
        and (
            not observation_active
            or (window.get("state") == "healthy" and window_matches)
        )
    )
    dimensions["observation_effects"] = _dimension(
        "healthy" if effects_ok else "blocked",
        (
            "terminal-effect accounting is complete and the current observation "
            "window proves zero provider calls"
            if observation_active
            else "terminal-effect accounting is complete for governed live operation"
        )
        if effects_ok
        else "provider-effect accounting is missing, ambiguous, or violates the active observation window",
        evidence={
            "observation_active": observation_active,
            "source_assurance_state": (source_assurance or {}).get("state", "missing"),
            "source_assurance_freshness": source_freshness.get("state", "missing"),
            "source_assurance_revision_current": source_freshness.get(
                "revision_current"
            )
            is True,
            "source_assurance_refresh_in_progress": source_freshness.get(
                "refresh_in_progress"
            )
            is True,
            "source_assurance_refresh_error": source_freshness.get(
                "refresh_error_type"
            ),
            "integrity_state": effects.get("state", "missing"),
            "window_state": window.get("state", "missing"),
            "window_boundary_valid": window_start is not None,
            "window_matches_attestation": window_matches,
            "provider_called_true": int(effect_counts.get("provider_called_true") or 0),
            "provider_called_false": int(
                effect_counts.get("provider_called_false") or 0
            ),
            "provider_called_unknown": int(
                effect_counts.get("provider_called_unknown") or 0
            ),
            "suppressed_false_or_unknown": int(
                effect_counts.get("suppressed_false_or_unknown") or 0
            ),
            "missing_terminal": int(effect_counts.get("missing_terminal") or 0),
            "duplicate_terminal": int(effect_counts.get("duplicate_terminal") or 0),
            "approval_correlation_missing": int(
                effect_counts.get("approval_correlation_missing") or 0
            ),
            "duplicate_request": int(effect_counts.get("duplicate_request") or 0),
            "malformed_request": int(effect_counts.get("malformed_request") or 0),
            "orphan_terminal": int(effect_counts.get("orphan_terminal") or 0),
            "window_provider_called_true": int(
                window_counts.get("provider_called_true") or 0
            ),
            "window_provider_called_unknown": int(
                window_counts.get("provider_called_unknown") or 0
            ),
            "window_suppressed_false_or_unknown": int(
                window_counts.get("suppressed_false_or_unknown") or 0
            ),
        },
    )

    blocking = [
        name
        for name, dimension in dimensions.items()
        if dimension["required"] and dimension["state"] != "healthy"
    ]
    return {
        "contract": "deployment-readiness-v1",
        "state": "healthy" if not blocking else "blocked",
        "ready": not blocking,
        "production_rollback_proven": not bool(disposable_test_dataset) and backup_ok,
        "blocking_dimensions": blocking,
        "dimensions": dimensions,
        "content_exposed": False,
    }


__all__ = [
    "CENTRAL_HOST_TAG",
    "REQUIRED_DM_SCOPE",
    "evaluate_deployment_readiness",
]
