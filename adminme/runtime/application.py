"""Production composition root for the single AdministrateMe v2 process."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal

import yaml
import sqlcipher3
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from adminme.adapters.base import AdapterContext
from adminme.adapters.cursor_store import AdapterCursorStore
from adminme import source_currentness_revision as currentness_revision
from adminme.source_assessment_revision import (
    RevisionManifest,
    RevisionPreview,
    RevisionRefused,
    inspect_target as inspect_revision_target,
    validate_target as validate_revision_target,
    request_key as revision_request_key,
    request_envelope as revision_request_envelope,
    command_status as revision_command_status,
    execution_policy as revision_execution_policy,
)
from adminme.adapters.disposable_test_acquisition import (
    policy_from_adapter_config,
    restrict_adapter_config,
)
from adminme.adapters.fixture_messaging import FixtureMessagingAdapter
from adminme.adapters.fixture_delegated_actions import FixtureDelegatedActionAdapter
from adminme.adapters.loader import load_builtin_adapters
from adminme.daemons.adapter_supervisor import AdapterSupervisor
from adminme.daemons.xlsx_sync.reverse import XlsxReverseDaemon
from adminme.communication_intake_retirement import retire_passive_obligation_candidates
from adminme.communication_intake_policy import read_policy_epoch
from adminme.runtime.communication_intake import (
    CommunicationIntakeRelease,
    load_communication_intake_release,
)
from adminme.submission_interpreter import SubmissionInterpreter
from adminme.provider_write_reconciliation import ProviderWriteReconciler
from adminme.conversation_intake import GmailConversationIntake
from adminme.direct_intake import (
    DirectIntakeProcessor,
    public_direct_channel_readiness,
)
from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog, IdempotencyConflict, new_correlation_id
from adminme.events.writer import EventWriter
from adminme.lib.governance import (
    GuardedWrite,
    RateLimiter,
    load_agent_allowlist,
    load_governance_config,
)
from adminme.extensions import ExtensionCatalog
from adminme.lib.instance_config import (
    InstanceConfig,
    load_instance_config,
    resolve_instance_dir,
)
from adminme.lib.observation import (
    ObservationManager,
    OutboundAuditContext,
    ScopedOutboundAdmission,
    reset_outbound_audit_context,
    set_outbound_audit_context,
)
from adminme.google_tasks_execution import (
    ADAPTER_IDENTITY as B03_ADAPTER_IDENTITY,
    DELEGATION_PERMISSION as B03_DELEGATION_PERMISSION,
    EXECUTION_POLICY_VERSION as B03_POLICY_VERSION,
    OPERATION as B03_OPERATION,
    PROVIDER as B03_PROVIDER,
    canonical_sha256 as b03_canonical_sha256,
    canonical_task_content as b03_canonical_task_content,
    returned_state_fingerprint as b03_returned_state_fingerprint,
    task_content_fingerprint as b03_task_content_fingerprint,
    public_envelope_state as b03_public_envelope_state,
)
from adminme.lib.adapters import OutboundMessage, WriteOperation
from adminme.lib.session import Session, build_internal_session
from adminme.lib.skill_runner import build_skill_runtime, set_default_event_sink
from adminme.lib.skill_runner.fixture import build_fixture_runtime
from adminme.lib.skill_runner.wrapper import run_skill
from adminme.historical_attribution import audit_historical_attribution
from adminme.member_chat import (
    DeterministicOpenClawChatTransport,
    MemberChatTransport,
    OpenClawGatewayChatTransport,
    UnavailableMemberChatTransport,
)
from adminme.openclaw_approvals import OpenClawApprovalStore
from adminme.operating_agreements import constitutional_agreement_payload
from adminme.openclaw_topology import inspect_runtime_topology, member_agent_id
from adminme.bridge_authorization import validate_bridge_record_set
from adminme.provider_bindings import (
    ProviderBinding,
    public_service_source_readiness,
    validate_historical_provider_bindings,
    validate_provider_bindings,
)
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_PARTY_ID,
    COS_SERVICE_PRINCIPAL_ID,
    ServiceChannelAccount,
    ServicePrincipal,
    cos_message_channel_for_account,
    validate_service_identity_config,
)
from adminme.adapters.member_google_connections import (
    selected_entries as selected_google_entries,
)
from adminme.deployment_readiness import evaluate_deployment_readiness
from adminme.pipelines.runner import PipelineRunner
from adminme.products.automation.main import router as automation_router
from adminme.products.bridge.auth import (
    DictMemberIdResolver,
    EnrollmentMemberIdResolver,
)
from adminme.products.bridge.routers import ingest_router
from adminme.products.capture.main import router as capture_router
from adminme.products.comms.main import router as comms_router
from adminme.products.core.main import router as core_router
from adminme.products.member.main import router as member_router
from adminme.products.member.bridge_setup import router as member_bridge_setup_router
from adminme.products.member.cos import router as member_cos_router
from adminme.products.member.finance_metadata import router as finance_metadata_router
from adminme.products.member.apple_service_settings import (
    router as apple_service_settings_router,
)
from adminme.products.member.apple_notes_commands import (
    router as apple_notes_commands_router,
)
from adminme.products.auth import require_principal, session_from_request
from adminme.products.openclaw.main import router as openclaw_router
from adminme.products.openclaw.cos_responses import (
    recover_response_attempts,
    router as cos_response_router,
)
from adminme.projections.runner import ProjectionRunner, register_default_projections
from adminme.projections.xlsx_workbooks import XlsxWorkbooksProjection
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.reconciliation import ReconciliationAdmissionEvaluator
from adminme.settings_control import SettingsControlStore, public_capability_summary
from adminme.messages_reply_reconciliation import MessagesReplyReconciler
from adminme.cos_reminder_work import ReminderWorkReconciler
from adminme.google_tasks_execution_reconciliation import GoogleTasksExecutionReconciler
from adminme.bridge_enrollment import BridgeEnrollmentCoordinator, BridgeIndexCommitter
from adminme.source_policy import source_policy_event_types
from adminme.source_policy_shadow import SourcePolicyShadowEvaluator
from adminme.source_assurance import audit_source_assurance
from adminme.source_assessment_recovery import (
    SourceAssessmentRecoveryBatch,
    SourceAssessmentRecoveryRefused,
    build_recovery_request_envelopes,
    recovery_batch_fingerprint,
)
from adminme.temporary_surfaces import TemporarySurfaceStore

_log = logging.getLogger(__name__)

CapabilityState = Literal[
    "healthy", "observation_only", "degraded", "blocked", "disabled"
]


@dataclass(frozen=True)
class RuntimeSettings:
    """Validated, secret-safe inputs required to construct the runtime."""

    instance: InstanceConfig
    encryption_key: bytes = field(repr=False)
    repository_root: Path
    member_identities: dict[str, str] = field(default_factory=dict)
    household_mapping: dict[str, Any] = field(default_factory=dict)
    bridge_enrollments: dict[str, dict[str, Any]] = field(default_factory=dict)
    bridge_index_committer: BridgeIndexCommitter | None = field(
        default=None, repr=False
    )
    google_config_committer: Any = field(default=None, repr=False)
    google_oauth: Any = field(default=None, repr=False)
    adapter_config: dict[str, Any] = field(
        default_factory=lambda: {"adapter_supervisor_port": 0, "adapters": []}
    )
    openclaw_enabled: bool = False
    openclaw_shared_secret: str | None = field(default=None, repr=False)
    openclaw_actor_identities: dict[str, str] = field(default_factory=dict)
    openclaw_gateway_url: str = "ws://127.0.0.1:18789"
    openclaw_gateway_token: str | None = field(default=None, repr=False)
    openclaw_model_configured: bool = False
    openclaw_runtime_config_path: Path | None = None
    member_chat_enabled_members: frozenset[str] = field(default_factory=frozenset)
    child_chat_enabled_members: frozenset[str] = field(default_factory=frozenset)
    deterministic_member_chat_fixture: bool = False
    deterministic_openclaw_fixture: bool = False
    member_roles: dict[str, str] = field(default_factory=dict)
    fixture_member_id: str | None = None
    host_topology: dict[str, Any] = field(default_factory=dict)
    deployment_evidence: dict[str, Any] = field(default_factory=dict)
    deployment_evidence_required: bool = False
    test_dataset_marker: dict[str, Any] = field(default_factory=dict, init=False)
    reconciliation_admission_enabled: bool = False
    human_triage_management_enabled: bool = False
    legacy_personal_obligation_compatibility: bool = False
    _credential_free_fixture: bool = field(default=False, repr=False)
    communication_intake_release: CommunicationIntakeRelease = field(init=False)
    provider_bindings: tuple[ProviderBinding, ...] = field(
        default_factory=tuple, init=False
    )
    service_principals: tuple[ServicePrincipal, ...] = field(
        default_factory=tuple, init=False
    )
    service_channel_accounts: tuple[ServiceChannelAccount, ...] = field(
        default_factory=tuple, init=False
    )

    def __post_init__(self) -> None:
        if (
            self.legacy_personal_obligation_compatibility
            and not self._credential_free_fixture
        ):
            raise ValueError(
                "legacy obligation extraction is limited to credential-free fixtures"
            )
        if len(self.encryption_key) != 32:
            raise ValueError("runtime encryption_key must be exactly 32 bytes")
        root = self.repository_root.resolve()
        if not (root / "packs" / "pipelines").is_dir():
            raise ValueError(f"pipeline pack root missing under {root}")
        object.__setattr__(
            self,
            "communication_intake_release",
            load_communication_intake_release(root),
        )
        adapters = self.adapter_config.get("adapters", [])
        if not isinstance(adapters, list):
            raise ValueError("adapter_config.adapters must be a list")
        test_policy = policy_from_adapter_config(self.adapter_config)
        if (
            self.instance.config_dir / "disposable-test-dataset.json"
        ).exists() and test_policy is None:
            raise ValueError("disposable test dataset requires its acquisition policy")
        restrict_adapter_config(self.adapter_config)
        marker_path = self.instance.config_dir / "disposable-test-dataset.json"
        if marker_path.exists():
            marker = json.loads(marker_path.read_text("utf-8"))
            if (
                not isinstance(marker, dict)
                or marker.get("contract") != "disposable-test-dataset-v1"
                or marker.get("acquisition_contract")
                != "disposable-test-acquisition-v1"
                or not isinstance(marker.get("dataset_id"), str)
                or len(marker["dataset_id"]) != 32
                or not isinstance(marker.get("reset_manifest_sha256"), str)
                or len(marker["reset_manifest_sha256"]) != 64
            ):
                raise ValueError("disposable test dataset marker is invalid")
            object.__setattr__(self, "test_dataset_marker", marker)
        self._validate_household_mapping()
        self._validate_identity_bindings()
        known_members = set(self.member_roles) | set(self.member_identities.values())
        known_members.update(
            str(member["member_id"])
            for member in self.household_mapping.get("members", [])
            if isinstance(member, dict) and isinstance(member.get("member_id"), str)
        )
        household_party_ids = set(known_members)
        household_party_ids.update(
            str(party["party_id"])
            for party in self.household_mapping.get("parties", [])
            if isinstance(party, dict) and isinstance(party.get("party_id"), str)
        )
        service_principals, service_channels = validate_service_identity_config(
            self.adapter_config.get("service_identities"),
            member_ids=known_members,
            household_party_ids=household_party_ids,
        )
        chief_of_staff = [
            principal
            for principal in service_principals
            if principal.kind == "chief_of_staff"
        ]
        if len(chief_of_staff) != 1 or (
            chief_of_staff[0].service_principal_id != COS_SERVICE_PRINCIPAL_ID
            or chief_of_staff[0].party_id != COS_PARTY_ID
        ):
            raise ValueError("runtime requires one stable household CoS identity")
        service_coordinates = {
            value
            for principal in service_principals
            for value in (principal.service_principal_id, principal.party_id)
        }
        if service_coordinates & set(self.member_identities.values()):
            raise ValueError("service principals cannot resolve through member ingress")
        object.__setattr__(self, "service_principals", service_principals)
        object.__setattr__(self, "service_channel_accounts", service_channels)
        channel_by_id = {channel.channel_id: channel for channel in service_channels}
        for index, entry in enumerate(adapters):
            if not isinstance(entry, dict):
                continue
            adapter_config = entry.get("config") or {}
            if not isinstance(adapter_config, dict):
                continue
            routes = adapter_config.get("trusted_sender_routes") or []
            if routes and not adapter_config.get("service_principal_id"):
                raise ValueError("trusted direct routes require service ownership")
            if not adapter_config.get("service_principal_id"):
                continue
            kind = str(entry.get("kind") or "").removeprefix("adapter:")
            if kind != "messaging-gmail-api":
                continue
            channel = channel_by_id.get(str(adapter_config.get("channel_id") or ""))
            if (
                channel is None
                or channel.provider != "gmail"
                or channel.service_principal_id
                != adapter_config.get("service_principal_id")
            ):
                raise ValueError(
                    f"service Gmail adapter {index} requires its exact reviewed channel"
                )
            if not isinstance(routes, list):
                raise ValueError("trusted_sender_routes must be a list")
            reviewed_route_keys = {route.route_key for route in channel.routes}
            exact_identifiers: set[str] = set()
            route_keys: set[str] = set()
            for route in routes:
                if not isinstance(route, dict):
                    raise ValueError("trusted sender route must be an object")
                identifier = (
                    str(route.get("exact_from_identifier") or "").strip().casefold()
                )
                route_key = str(route.get("route_key") or "")
                if not identifier or identifier in exact_identifiers:
                    raise ValueError(
                        "trusted sender identifiers must be exact and unique"
                    )
                if route_key not in reviewed_route_keys or route_key in route_keys:
                    raise ValueError(
                        "trusted sender route must name one unique reviewed channel route"
                    )
                exact_identifiers.add(identifier)
                route_keys.add(route_key)
        valid_bridge_states = {
            "package-created-not-live",
            "package-exported",
            "active",
            "paused",
            "revoked",
        }
        if not all(
            isinstance(identity, str)
            and bool(identity.strip())
            and isinstance(record, dict)
            and isinstance(record.get("member_id"), str)
            and bool(record["member_id"].strip())
            and record.get("status") in valid_bridge_states
            for identity, record in self.bridge_enrollments.items()
        ):
            raise ValueError("bridge enrollment mapping is invalid")
        normalized_bridge_identities = [
            identity.strip().casefold() for identity in self.bridge_enrollments
        ]
        bridge_members = [
            str(record["member_id"]) for record in self.bridge_enrollments.values()
        ]
        if self.deployment_evidence_required and (
            len(normalized_bridge_identities) != len(set(normalized_bridge_identities))
            or len(bridge_members) != len(set(bridge_members))
        ):
            raise ValueError(
                "strict topology requires one unambiguous bridge identity per member"
            )
        messages_bridge_records = {
            identity: record
            for identity, record in self.bridge_enrollments.items()
            if record.get("status") == "active"
            and "messaging-bluebubbles" in record.get("configured_packs", [])
        }
        if messages_bridge_records:
            # A private Messages account cannot be accepted under two members,
            # including in credential-free Phase-A composition tests. This is
            # a source-ownership boundary, not merely deployment evidence.
            validate_bridge_record_set(messages_bridge_records)
        if self.deployment_evidence_required:
            validate_bridge_record_set(self.bridge_enrollments)
        bindings: tuple[ProviderBinding, ...] = ()
        b03_binding_declared = any(
            isinstance(entry, dict)
            and isinstance(entry.get("config"), dict)
            and entry["config"].get("b03_google_tasks_create") is not None
            for entry in adapters
        )
        if self.deployment_evidence_required or b03_binding_declared:
            bindings = validate_provider_bindings(
                self.adapter_config,
                member_roles=self.member_roles,
                household_mapping=self.household_mapping,
                service_principal_ids={
                    principal.service_principal_id for principal in service_principals
                },
                service_principal_provider_authorizations={},
            )
            if self.host_topology.get("central_identity_collides_with_member") is True:
                raise ValueError(
                    "central infrastructure identity must not map to a household member"
                )
        object.__setattr__(self, "provider_bindings", bindings)
        if self.openclaw_enabled:
            if not self.openclaw_shared_secret or len(self.openclaw_shared_secret) < 24:
                raise ValueError(
                    "enabled OpenClaw transport requires an injected 24+ character secret"
                )
            if (
                not self.openclaw_actor_identities
                and not self.member_chat_enabled_members
            ):
                raise ValueError("enabled OpenClaw transport requires actor mappings")
            mapped_members = set(self.openclaw_actor_identities.values()) | set(
                self.member_chat_enabled_members
            )
            if mapped_members & service_coordinates:
                raise ValueError(
                    "service principals cannot share household-member OpenClaw bindings"
                )
            unmapped_roles = mapped_members - set(self.member_roles)
            if unmapped_roles:
                raise ValueError(
                    "enabled OpenClaw transport requires a role for every mapped member"
                )
            account_owners: dict[tuple[str, str], str] = {}
            account_routes: dict[tuple[str, str], tuple[str, str]] = {}
            for route, member in self.openclaw_actor_identities.items():
                parts = route.split("|", 2)
                if len(parts) != 3 or not all(parts):
                    raise ValueError(
                        "OpenClaw actor identity must be channel|account|actor"
                    )
                account = (parts[0].casefold(), parts[1].casefold())
                exact_account = (parts[0], parts[1])
                original_account = account_routes.setdefault(account, exact_account)
                owner = account_owners.setdefault(account, member)
                if exact_account != original_account:
                    raise ValueError(
                        "OpenClaw channel account cannot be shared with ambiguous casing"
                    )
                if owner != member:
                    service_channel = cos_message_channel_for_account(
                        service_channels,
                        transport_channel_id=parts[0],
                        account_id=parts[1],
                    )
                    if (
                        service_channel is None
                        or service_channel.intake_mode != "member_submission"
                    ):
                        raise ValueError("OpenClaw channel account cannot be shared")
            agent_ids = {member_agent_id(member) for member in mapped_members}
            if len(agent_ids) != len(mapped_members):
                raise ValueError("OpenClaw member agent identities must be unique")
        if not self.child_chat_enabled_members.issubset(
            self.member_chat_enabled_members
        ):
            raise ValueError("child chat members must also be chat-enabled members")
        if any(
            self.member_roles.get(member) != "child"
            for member in self.child_chat_enabled_members
        ):
            raise ValueError("child chat allowlist may contain only child-role members")
        if self.openclaw_gateway_url not in {
            "ws://127.0.0.1:18789",
            "ws://[::1]:18789",
        }:
            raise ValueError(
                "member chat Gateway must remain on pinned loopback port 18789"
            )

    def _validate_household_mapping(self) -> None:
        mapping = self.household_mapping
        if not mapping:
            return
        if not isinstance(mapping, dict):
            raise ValueError("household mapping must be an object")
        members = mapping.get("members")
        memberships = mapping.get("memberships")
        relationships = mapping.get("relationships")
        parties = mapping.get("parties", [])
        if (
            not isinstance(members, list)
            or not isinstance(memberships, list)
            or not isinstance(relationships, list)
            or not isinstance(parties, list)
        ):
            raise ValueError("household mapping collections must be lists")
        known: set[str] = set()
        for member in members:
            if not isinstance(member, dict):
                raise ValueError("household members must be objects")
            member_id = member.get("member_id")
            label = member.get("label")
            role = member.get("role")
            if (
                not isinstance(member_id, str)
                or not member_id.strip()
                or not isinstance(label, str)
                or not label.strip()
                or not isinstance(role, str)
                or not role.strip()
            ):
                raise ValueError("household members require member_id, label, and role")
            if member_id in known:
                raise ValueError("household Party ids must be unique")
            known.add(member_id)
        for party in parties:
            if not isinstance(party, dict):
                raise ValueError("additional household Parties must be objects")
            party_id = party.get("party_id")
            label = party.get("label")
            if (
                not isinstance(party_id, str)
                or not party_id.strip()
                or not isinstance(label, str)
                or not label.strip()
                or party.get("kind", "person") not in {"person", "organization"}
                or party_id in known
            ):
                raise ValueError(
                    "additional household Parties are invalid or duplicated"
                )
            scope = party.get("visibility_scope", "shared:household")
            if scope != "shared:household" and not (
                isinstance(scope, str) and scope.startswith("private:")
            ):
                raise ValueError("additional household Party scope is invalid")
            known.add(party_id)
        for membership in memberships:
            if (
                not isinstance(membership, dict)
                or not isinstance(membership.get("membership_id"), str)
                or not isinstance(membership.get("party_id"), str)
                or not isinstance(membership.get("role"), str)
                or membership["party_id"] not in known
            ):
                raise ValueError("household membership is invalid")
        for relationship in relationships:
            if (
                not isinstance(relationship, dict)
                or not all(
                    isinstance(relationship.get(key), str)
                    and bool(relationship[key].strip())
                    for key in (
                        "relationship_id",
                        "from_party_id",
                        "to_party_id",
                        "kind",
                    )
                )
                or relationship["from_party_id"] not in known
                or relationship["to_party_id"] not in known
            ):
                raise ValueError("household relationship is invalid")

    def _validate_identity_bindings(self) -> None:
        normalized: dict[str, str] = {}
        identities_by_member: dict[str, set[str]] = {}
        household_roles = {
            str(member.get("member_id")): str(member.get("role"))
            for member in self.household_mapping.get("members", [])
            if isinstance(member, dict)
            and isinstance(member.get("member_id"), str)
            and isinstance(member.get("role"), str)
        }
        for identity, member_id in self.member_identities.items():
            if (
                not isinstance(identity, str)
                or not identity.strip()
                or not isinstance(member_id, str)
                or not member_id.strip()
            ):
                raise ValueError("portal identity bindings must be non-empty strings")
            key = identity.strip().casefold()
            prior = normalized.setdefault(key, member_id)
            if prior != member_id or identity != identity.strip():
                raise ValueError(
                    "portal identity bindings are ambiguous after normalization"
                )
            identities_by_member.setdefault(member_id, set()).add(key)
            if self.deployment_evidence_required and member_id not in self.member_roles:
                raise ValueError(
                    "every portal identity requires an explicit member role"
                )
        if self.deployment_evidence_required and any(
            len(identities) != 1 for identities in identities_by_member.values()
        ):
            raise ValueError(
                "strict topology requires exactly one trusted ingress identity per member"
            )
        for member_id, role in self.member_roles.items():
            mapped_role = household_roles.get(member_id)
            if mapped_role is not None and mapped_role != role:
                raise ValueError("member role map contradicts the household mapping")

    @classmethod
    def from_environment(cls) -> "RuntimeSettings":
        instance = load_instance_config(resolve_instance_dir())
        key_hex = os.environ.get("ADMINME_EVENT_LOG_KEY_HEX", "")
        try:
            key = bytes.fromhex(key_hex)
        except ValueError as exc:
            raise RuntimeError("ADMINME_EVENT_LOG_KEY_HEX is not valid hex") from exc
        if len(key) != 32:
            raise RuntimeError(
                "ADMINME_EVENT_LOG_KEY_HEX must resolve to a 32-byte key"
            )
        root_value = os.environ.get("ADMINME_REPOSITORY_ROOT")
        root = Path(root_value) if root_value else Path(__file__).resolve().parents[2]
        secret = os.environ.get("ADMINME_OPENCLAW_SHARED_SECRET")
        identities_value = os.environ.get(
            "ADMINME_OPENCLAW_ACTOR_IDENTITIES_JSON", "{}"
        )
        roles_value = os.environ.get("ADMINME_OPENCLAW_MEMBER_ROLES_JSON", "{}")
        chat_members_value = os.environ.get("ADMINME_MEMBER_CHAT_MEMBERS_JSON", "[]")
        child_chat_value = os.environ.get("ADMINME_CHILD_CHAT_MEMBERS_JSON", "[]")
        try:
            identities = json.loads(identities_value)
            roles = json.loads(roles_value)
            chat_members = json.loads(chat_members_value)
            child_chat_members = json.loads(child_chat_value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "OpenClaw identity or role mapping environment is not valid JSON"
            ) from exc
        if not isinstance(identities, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in identities.items()
        ):
            raise RuntimeError(
                "ADMINME_OPENCLAW_ACTOR_IDENTITIES_JSON must be a string map"
            )
        if not isinstance(roles, dict) or not all(
            isinstance(key, str) and value in {"principal", "child", "device"}
            for key, value in roles.items()
        ):
            raise RuntimeError(
                "ADMINME_OPENCLAW_MEMBER_ROLES_JSON must map members to interactive roles"
            )
        if not isinstance(chat_members, list) or not all(
            isinstance(value, str) and value for value in chat_members
        ):
            raise RuntimeError("ADMINME_MEMBER_CHAT_MEMBERS_JSON must be a string list")
        if not isinstance(child_chat_members, list) or not all(
            isinstance(value, str) and value for value in child_chat_members
        ):
            raise RuntimeError("ADMINME_CHILD_CHAT_MEMBERS_JSON must be a string list")
        gateway_token = os.environ.get("ADMINME_OPENCLAW_GATEWAY_TOKEN")
        return cls(
            instance=instance,
            encryption_key=key,
            repository_root=root,
            openclaw_enabled=bool(secret and (identities or chat_members)),
            openclaw_shared_secret=secret,
            openclaw_actor_identities=identities,
            member_roles=roles,
            openclaw_gateway_token=gateway_token,
            openclaw_model_configured=os.environ.get(
                "ADMINME_OPENCLAW_MODEL_CONFIGURED", "0"
            )
            == "1",
            member_chat_enabled_members=frozenset(chat_members),
            child_chat_enabled_members=frozenset(child_chat_members),
            reconciliation_admission_enabled=os.environ.get(
                "ADMINME_RECONCILIATION_ADMISSION_ENABLED", "1"
            )
            == "1",
            human_triage_management_enabled=os.environ.get(
                "ADMINME_HUMAN_TRIAGE_MANAGEMENT_ENABLED", "1"
            )
            == "1",
        )

    @classmethod
    def credential_free(
        cls,
        instance_dir: Path,
        *,
        encryption_key: bytes,
        repository_root: Path,
        member_identities: dict[str, str] | None = None,
        household_mapping: dict[str, Any] | None = None,
        bridge_enrollments: dict[str, dict[str, Any]] | None = None,
        bridge_index_committer: BridgeIndexCommitter | None = None,
        google_config_committer: Any = None,
        google_oauth: Any = None,
        deterministic_openclaw_fixture: bool = False,
        member_roles: dict[str, str] | None = None,
        fixture_member_id: str | None = None,
        openclaw_enabled: bool = False,
        openclaw_shared_secret: str | None = None,
        openclaw_actor_identities: dict[str, str] | None = None,
        openclaw_gateway_url: str = "ws://127.0.0.1:18789",
        openclaw_gateway_token: str | None = None,
        openclaw_model_configured: bool = False,
        openclaw_runtime_config_path: Path | None = None,
        member_chat_enabled_members: frozenset[str] | None = None,
        child_chat_enabled_members: frozenset[str] | None = None,
        deterministic_member_chat_fixture: bool = False,
        adapter_config: dict[str, Any] | None = None,
        host_topology: dict[str, Any] | None = None,
        deployment_evidence: dict[str, Any] | None = None,
        deployment_evidence_required: bool = False,
        reconciliation_admission_enabled: bool = False,
        human_triage_management_enabled: bool = False,
        legacy_personal_obligation_compatibility: bool = False,
    ) -> "RuntimeSettings":
        """Build a no-network credential-free configuration with test keys."""

        return cls(
            instance=load_instance_config(instance_dir),
            encryption_key=encryption_key,
            repository_root=repository_root,
            member_identities=dict(member_identities or {}),
            household_mapping=dict(household_mapping or {}),
            bridge_enrollments={
                key: dict(value) for key, value in (bridge_enrollments or {}).items()
            },
            bridge_index_committer=bridge_index_committer,
            google_config_committer=google_config_committer,
            google_oauth=google_oauth,
            deterministic_openclaw_fixture=deterministic_openclaw_fixture,
            member_roles=dict(member_roles or {}),
            fixture_member_id=fixture_member_id,
            openclaw_enabled=openclaw_enabled,
            openclaw_shared_secret=openclaw_shared_secret,
            openclaw_actor_identities=dict(openclaw_actor_identities or {}),
            openclaw_gateway_url=openclaw_gateway_url,
            openclaw_gateway_token=openclaw_gateway_token,
            openclaw_model_configured=openclaw_model_configured,
            openclaw_runtime_config_path=openclaw_runtime_config_path,
            member_chat_enabled_members=frozenset(member_chat_enabled_members or ()),
            child_chat_enabled_members=frozenset(child_chat_enabled_members or ()),
            deterministic_member_chat_fixture=deterministic_member_chat_fixture,
            adapter_config=dict(
                adapter_config or {"adapter_supervisor_port": 0, "adapters": []}
            ),
            host_topology=dict(host_topology or {}),
            deployment_evidence=dict(deployment_evidence or {}),
            deployment_evidence_required=deployment_evidence_required,
            reconciliation_admission_enabled=reconciliation_admission_enabled,
            human_triage_management_enabled=human_triage_management_enabled,
            legacy_personal_obligation_compatibility=legacy_personal_obligation_compatibility,
            _credential_free_fixture=True,
        )


@dataclass
class CapabilityRecord:
    name: str
    kind: str
    state: CapabilityState
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return asdict(self)


class DeploymentReadinessBlocked(RuntimeError):
    """A provider command reached dispatch while deployment admission was closed."""


class AdministrateMeRuntime:
    """Own all central resources and their ordered startup/shutdown."""

    SOURCE_ASSURANCE_REFRESH_SECONDS = 30.0
    SOURCE_ASSURANCE_MAX_STALE_SECONDS = 300.0
    OPENCLAW_HEARTBEAT_MAX_STALE_SECONDS = 90.0

    PRODUCT_NAMES = (
        "core",
        "comms",
        "capture",
        "automation",
        "bridge",
        "member",
        "system",
    )

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        self.event_log: EventLog | None = None
        self.bus: EventBus | None = None
        self.events: EventWriter | None = None
        self.projections: ProjectionRunner | None = None
        self.xlsx_projection: XlsxWorkbooksProjection | None = None
        self.xlsx_reverse: XlsxReverseDaemon | None = None
        self.pipelines: PipelineRunner | None = None
        self.adapters: AdapterSupervisor | None = None
        self.adapter_cursor_store: AdapterCursorStore | None = None
        self.openclaw_approval_store: OpenClawApprovalStore | None = None
        self.fixture_adapter: FixtureMessagingAdapter | None = None
        self.fixture_action_adapter: FixtureDelegatedActionAdapter | None = None
        self.google_tasks_execution_reconciler: (
            GoogleTasksExecutionReconciler | None
        ) = None
        self.direct_intake: DirectIntakeProcessor | None = None
        self.gmail_conversations: GmailConversationIntake | None = None
        self.submission_interpreter: SubmissionInterpreter | None = None
        self.provider_write_reconciler: ProviderWriteReconciler | None = None
        self.run_skill_fn: Any = run_skill
        self.commitment_command_locks: dict[str, asyncio.Lock] = {}
        self.triage_command_locks: dict[str, asyncio.Lock] = {}
        self.triage_intent_command_locks: dict[str, asyncio.Lock] = {}
        self.triage_intent_command_lock_users: dict[str, int] = {}
        self.today_command_locks: dict[str, asyncio.Lock] = {}
        self.communication_command_locks: dict[str, asyncio.Lock] = {}
        self.settings_command_locks: dict[str, asyncio.Lock] = {}
        self.member_chat_locks: dict[str, asyncio.Lock] = {}
        self.member_chat_limiter = RateLimiter()
        self.member_chat: MemberChatTransport = UnavailableMemberChatTransport()
        self.member_chat_last_failure: dict[str, Any] | None = None
        self.observation: ObservationManager | None = None
        self.guarded_write: GuardedWrite | None = None
        self.member_id_resolver = (
            EnrollmentMemberIdResolver(settings.bridge_enrollments)
            if settings.bridge_enrollments
            else DictMemberIdResolver({})
        )
        self.extensions = ExtensionCatalog(
            settings.repository_root, settings.instance.instance_dir
        )
        self.settings_control = SettingsControlStore(
            settings.instance.config_dir / "settings-control.json",
            adapter_config=settings.adapter_config,
        )
        deployment_binding = settings.host_topology.get("deployment_binding")
        self.bridge_enrollment = BridgeEnrollmentCoordinator(
            instance_dir=settings.instance.instance_dir,
            encryption_key=settings.encryption_key,
            repository_root=settings.repository_root,
            tenant_id=settings.instance.tenant_id,
            member_identities=settings.member_identities,
            member_roles=settings.member_roles,
            bridge_enrollments=settings.bridge_enrollments,
            adapter_config=settings.adapter_config,
            deployment_release_commit=(
                deployment_binding.get("release_commit")
                if isinstance(deployment_binding, dict)
                else None
            ),
            require_deployment_release=settings.deployment_evidence_required,
            index_committer=settings.bridge_index_committer,
            on_index_committed=self._bridge_index_committed,
        )
        self.source_policy_shadow: SourcePolicyShadowEvaluator | None = None
        self.reconciliation_admission: ReconciliationAdmissionEvaluator | None = None
        self.temporary_surfaces = TemporarySurfaceStore(
            settings.instance.config_dir / "temporary-surfaces.json"
        )
        self.capabilities: dict[str, CapabilityRecord] = {}
        self._capability_refresh_lock = asyncio.Lock()
        self._source_assurance_cache: dict[str, Any] | None = None
        self._source_assurance_refreshed_at: str | None = None
        self._source_assurance_refreshed_monotonic = 0.0
        self._source_assurance_last_attempt_monotonic = 0.0
        self._source_assurance_revision: tuple[Any, ...] | None = None
        self._source_assurance_refresh_task: asyncio.Task[None] | None = None
        self._source_assurance_refresh_error: str | None = None
        self.standing_orders: dict[str, dict[str, Any]] = {}
        self.started = False
        self.stopping = False
        self._stop_task: asyncio.Task[None] | None = None
        self.startup_error: str | None = None
        self._observation_active = True
        self._cos_live_authorization: dict[str, Any] = {"authorized": False}
        self.openclaw_state = "degraded" if settings.openclaw_enabled else "unavailable"
        self.openclaw_last_report_monotonic: float | None = None
        self.openclaw_topology_report: dict[str, Any] = {}
        self.openclaw_last_reported_at: str | None = None

    async def start(self) -> None:
        if self.started:
            return
        if self.stopping:
            raise RuntimeError("runtime is stopping")
        try:
            self.event_log = EventLog(
                self.settings.instance, self.settings.encryption_key
            )
            self.bus = EventBus(
                self.event_log, self.settings.instance.bus_checkpoint_path
            )
            self.events = EventWriter(self.event_log, self.bus)
            await recover_response_attempts(self)
            if not self.settings.legacy_personal_obligation_compatibility:
                epoch = await read_policy_epoch(
                    self.event_log, self.settings.instance.tenant_id
                )
                if (
                    self.settings.communication_intake_release.activates_cutover
                    or epoch is not None
                ):
                    await retire_passive_obligation_candidates(
                        event_log=self.event_log,
                        event_sink=self.events,
                        tenant_id=self.settings.instance.tenant_id,
                    )
            self.submission_interpreter = SubmissionInterpreter(self)
            self.provider_write_reconciler = ProviderWriteReconciler(
                self.event_log, self.events
            )
            self.direct_intake = DirectIntakeProcessor(
                event_sink=self.events,
                event_log=self.event_log,
                channels=self.settings.service_channel_accounts,
            )
            self.messages_reply_reconciler = MessagesReplyReconciler(
                self.event_log, self.events
            )
            self.reminder_work_reconciler = ReminderWorkReconciler(self)
            self.google_tasks_execution_reconciler = GoogleTasksExecutionReconciler(
                self.event_log, self.events
            )
            await self.settings_control.replay_source_policy_v2_events(self.event_log)
            await self.settings_control.replay_capability_request_events(self.event_log)
            await self.settings_control.replay_bridge_enrollment_events(self.event_log)
            await self.settings_control.replay_message_reply_control_events(
                self.event_log
            )
            await self.settings_control.replay_apple_service_permission_events(
                self.event_log, tenant_id=self.settings.instance.tenant_id
            )
            self.source_policy_shadow = SourcePolicyShadowEvaluator(
                self.events,
                self.settings_control,
                self.settings_control.effective_candidate_policy,
                self.settings.provider_bindings,
            )
            self.reconciliation_admission = ReconciliationAdmissionEvaluator(
                self.events,
                self.event_log,
                self.settings_control,
                provider_bindings=self.settings.provider_bindings,
                admission_active=self.settings.reconciliation_admission_enabled,
            )
            await self._hydrate_openclaw_state()
            set_default_event_sink(self.events)
            self.observation = ObservationManager(
                self.events, self.settings.instance.config_dir / "runtime.yaml"
            )
            self.gmail_conversations = GmailConversationIntake(self)
            await self.gmail_conversations.initialize()
            if self.settings.deterministic_member_chat_fixture:
                self.member_chat = DeterministicOpenClawChatTransport()
            elif self.settings.openclaw_gateway_token:
                self.member_chat = OpenClawGatewayChatTransport(
                    url=self.settings.openclaw_gateway_url,
                    token=self.settings.openclaw_gateway_token,
                )
            if (
                self.settings.deterministic_openclaw_fixture
                and policy_from_adapter_config(self.settings.adapter_config) is None
            ):
                fixture_member = self.settings.fixture_member_id or next(
                    (
                        member_id
                        for member_id, role in sorted(
                            self.settings.member_roles.items()
                        )
                        if role == "principal"
                    ),
                    "member_fixture_principal",
                )
                self.fixture_adapter = FixtureMessagingAdapter(
                    event_sink=self.events,
                    observation_manager=self.observation,
                    member_id=fixture_member,
                    default_session=build_internal_session(
                        fixture_member,
                        "principal",
                        self.settings.instance.tenant_id,
                    ),
                )
                self.fixture_action_adapter = FixtureDelegatedActionAdapter(
                    event_sink=self.events,
                    observation_manager=self.observation,
                )
            policy_root = self.settings.repository_root / "bootstrap" / "policy"
            governance_path = self.settings.instance.config_dir / "governance.yaml"
            authority_path = self.settings.instance.config_dir / "authority.yaml"
            self.guarded_write = GuardedWrite(
                load_governance_config(
                    governance_path
                    if governance_path.is_file()
                    else policy_root / "governance.yaml"
                ),
                RateLimiter(),
                load_agent_allowlist(
                    authority_path
                    if authority_path.is_file()
                    else policy_root / "authority.yaml"
                ),
                self.events,
            )

            self.projections = ProjectionRunner(
                self.bus,
                self.event_log,
                self.settings.instance,
                encryption_key=self.settings.encryption_key,
            )
            self.xlsx_projection = register_default_projections(
                self.projections,
                self.settings.instance,
                self.event_log,
                self.events,
            )

            skill_runtime = (
                build_fixture_runtime(
                    event_sink=self.events,
                    event_log=self.event_log,
                    raw_events_dir=self.settings.instance.raw_events_dir,
                )
                if self.settings.deterministic_openclaw_fixture
                else build_skill_runtime(
                    event_sink=self.events,
                    event_log=self.event_log,
                    raw_events_dir=self.settings.instance.raw_events_dir,
                    gateway_token=self.settings.openclaw_gateway_token,
                )
            )
            self.run_skill_fn = partial(run_skill, _runtime=skill_runtime)
            self.pipelines = PipelineRunner(
                self.bus,
                self.event_log,
                self.events,
                self.settings.instance,
                observation_manager=self.observation,
                guarded_write=self.guarded_write,
                parties_conn_factory=self._parties_connection_factory,
                run_skill_fn=self.run_skill_fn,
                profile_loader=self.extensions.profile_for,
                persona_loader=self.extensions.active_persona,
                candidate_policy_fn=self.settings_control.effective_candidate_policy,
                legacy_personal_obligation_compatibility=self.settings.legacy_personal_obligation_compatibility,
                source_revision_authority_fn=lambda command: (
                    currentness_revision.execution_policy(
                        command,
                        adapter_config=self.settings.adapter_config,
                        dataset_marker=self.settings.test_dataset_marker,
                        repository_root=self.settings.repository_root,
                    )
                ),
            )
            await self.pipelines.discover(
                builtin_root=self.settings.repository_root / "packs" / "pipelines",
                installed_root=self.settings.instance.packs_dir / "pipelines",
            )
            self._load_standing_orders()
            self._inventory_optional_adapters()
            self._inventory_data_extensions()

            self.adapter_cursor_store = AdapterCursorStore(
                self.settings.instance.adapter_cursor_path
            )
            self.openclaw_approval_store = OpenClawApprovalStore(
                self.settings.instance.config_dir / "openclaw-approvals.sqlite3"
            )
            self.adapters = AdapterSupervisor(
                config_path=self.settings.instance.config_dir / "adapters.yaml",
                config_data=self.settings_control.effective_adapter_config(
                    self.settings.adapter_config
                ),
                adapter_loader=lambda config: load_builtin_adapters(
                    config,
                    repository_root=self.settings.repository_root,
                    member_roles=self.settings.member_roles,
                    household_mapping=self.settings.household_mapping,
                    service_principal_ids={
                        principal.service_principal_id
                        for principal in self.settings.service_principals
                    },
                    service_principal_provider_authorizations={},
                    validate_bindings=self.settings.deployment_evidence_required,
                ),
                ctx_factory=lambda: AdapterContext(
                    event_sink=self._require_events(),
                    config={},
                    correlation_id_factory=new_correlation_id,
                    now_factory=lambda: datetime.now(timezone.utc),
                    observation_session=build_internal_session(
                        "adapter_supervisor",
                        "device",
                        self.settings.instance.tenant_id,
                    ),
                    observation_manager=self.observation,
                    observation_event_sink=self._require_events(),
                    cursor_store=self.adapter_cursor_store,
                    source_events=self.event_log,
                ),
            )
            self.adapters.prepare()
            self.bus.subscribe(
                "source-policy-v2:shadow",
                source_policy_event_types(self.settings.repository_root),
                self.source_policy_shadow.handle,
                consumer_version=self.source_policy_shadow.CONSUMER_VERSION,
                replay_on_version_change=True,
            )
            self.bus.subscribe(
                "triage-reconciliation:admission",
                list(
                    dict.fromkeys(
                        [
                            *source_policy_event_types(self.settings.repository_root),
                            "triage.item.proposed",
                            "context.reconciliation.typed_fact_shared",
                            "settings.source_policy_v2_changed",
                        ]
                    )
                ),
                self.reconciliation_admission.handle,
                consumer_version=self.reconciliation_admission.CONSUMER_VERSION,
                replay_on_version_change=True,
            )
            self.bus.subscribe(
                "external-sync:central-providers",
                ["external_sync.write_requested"],
                self._dispatch_external_write,
            )
            self.bus.subscribe(
                "member-submissions:interpretation",
                ["triage.intent.submitted"],
                self.submission_interpreter.handle,
                consumer_version=self.submission_interpreter.CONSUMER_VERSION,
                replay_on_version_change=True,
            )
            self.bus.subscribe(
                "member-submissions:recovery",
                ["triage.intent.recovery_requested"],
                self.submission_interpreter.handle_recovery,
                consumer_version="member-submission-recovery-v1",
                replay_on_version_change=True,
                delivery_concurrency=2,
            )
            self.bus.subscribe(
                "provider-writes:reconciliation",
                [
                    "external_sync.write_completed",
                    "calendar.event_added",
                    "calendar.event_updated",
                    "calendar.event_deleted",
                    "list_item.added",
                    "list_item.updated",
                    "list_item.completed",
                    "list_item.uncompleted",
                    "list_item.deleted",
                    "list.created",
                    "list.updated",
                    "list.deleted",
                ],
                self.provider_write_reconciler.handle,
                consumer_version=self.provider_write_reconciler.CONSUMER_VERSION,
                replay_on_version_change=True,
            )
            self.bus.subscribe(
                "direct-intake:routing",
                ["messaging.received"],
                self.direct_intake.handle,
                consumer_version=self.direct_intake.CONSUMER_VERSION,
                replay_on_version_change=True,
            )
            self.bus.subscribe(
                "cos-gmail:conversation",
                [
                    "messaging.received",
                    "cos.channels.activated",
                    "observation.disabled",
                ],
                self.gmail_conversations.handle,
                consumer_version=self.gmail_conversations.CONSUMER_VERSION,
            )
            self.bus.subscribe(
                "apple-messages-reply:reconciliation",
                ["messaging.sent"],
                self.messages_reply_reconciler.handle,
                consumer_version=self.messages_reply_reconciler.CONSUMER_VERSION,
                replay_on_version_change=True,
            )
            self.bus.subscribe(
                "google-tasks-create-v2:reconciliation",
                [
                    "list_item.added",
                    "list_item.updated",
                    "list_item.completed",
                    "list_item.uncompleted",
                    "list_item.deleted",
                ],
                self.google_tasks_execution_reconciler.handle,
                consumer_version=self.google_tasks_execution_reconciler.CONSUMER_VERSION,
                replay_on_version_change=True,
            )

            self.bus.subscribe(
                "cos-reminders:work",
                [
                    "cos.reply.reminder_requested",
                    "cos.reply.reminder_bound",
                    "cos.reply.reminder_cancelled",
                    "cos.response.result",
                ],
                self.reminder_work_reconciler.handle,
                consumer_version=self.reminder_work_reconciler.CONSUMER_VERSION,
                replay_on_version_change=True,
            )

            # All subscriptions are explicit before the one bus owner starts.
            await self.projections.start(start_bus=False)
            await self.pipelines.start(start_bus=False)
            await self.pipelines.reconcile_pending_source_assessment_recoveries()
            await self.pipelines.reconcile_pending_source_publications()
            await self.adapters.start()
            await asyncio.sleep(0)
            await self.bus.start()
            # The workbook reverse path is part of the same production
            # composition, not a prose-only or separately managed central
            # process. It starts only after projection connections and the bus
            # are ready, and shares the reviewed GuardedWrite policy.
            self.xlsx_reverse = XlsxReverseDaemon(
                self.settings.instance,
                self._xlsx_query_context(),
                event_sink=self.events,
                guarded_write=self.guarded_write,
            )
            if policy_from_adapter_config(self.settings.adapter_config) is None:
                await self.xlsx_reverse.start()
            await self._reconcile_household_enrollment()
            await self._reconcile_constitutional_operating_agreement()
            await self._refresh_capability_registry()
            self.started = True

            await self.events.write(
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=self.settings.instance.tenant_id,
                    type="runtime.started",
                    schema_version=1,
                    occurred_at=EventEnvelope.now_utc_iso(),
                    source_adapter="adminme-runtime",
                    source_account_id="system",
                    owner_scope="shared:household",
                    visibility_scope="shared:household",
                    sensitivity="normal",
                    payload={
                        "projection_names": self.projections.projection_names(),
                        "reactive_pipeline_ids": self.pipelines.registered_pack_ids(),
                        "standing_order_ids": sorted(self.standing_orders),
                        "observation_active": await self.observation.is_active(),
                        "capability_state": self.overall_state(),
                    },
                )
            )
        except BaseException as exc:
            self.startup_error = f"{type(exc).__name__}: {exc}"
            await self.stop()
            raise

    async def stop(self) -> None:
        cleanup_task = self._stop_task
        if cleanup_task is not None and cleanup_task.done():
            return
        if cleanup_task is None:
            self.stopping = True
            cleanup_task = asyncio.create_task(
                self._stop_components(),
                name="administrateme-runtime-stop",
            )
            self._stop_task = cleanup_task
        cancellation: asyncio.CancelledError | None = None
        cleanup_failure: BaseException | None = None
        try:
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                except BaseException:
                    break
            try:
                cleanup_task.result()
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
            except BaseException as exc:
                cleanup_failure = exc
        finally:
            set_default_event_sink(None)
            self.started = False
        if cancellation is not None:
            raise cancellation
        if cleanup_failure is not None:
            raise cleanup_failure

    async def _stop_components(self) -> None:
        """Settle every owned resource even when one shutdown step fails."""

        first_failure: BaseException | None = None

        async def _await_step(label: str, awaitable: Any) -> None:
            nonlocal first_failure
            try:
                await awaitable
            except BaseException as exc:
                first_failure = first_failure or exc
                _log.exception("runtime shutdown step failed: %s", label)

        def _sync_step(label: str, callback: Any) -> None:
            nonlocal first_failure
            try:
                callback()
            except BaseException as exc:
                first_failure = first_failure or exc
                _log.exception("runtime shutdown step failed: %s", label)

        if self._source_assurance_refresh_task is not None:
            self._source_assurance_refresh_task.cancel()
            try:
                await self._source_assurance_refresh_task
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                first_failure = first_failure or exc
                _log.exception("runtime shutdown step failed: source assurance refresh")
            self._source_assurance_refresh_task = None
        if self.xlsx_reverse is not None:
            await _await_step("xlsx reverse", self.xlsx_reverse.stop())
            self.xlsx_reverse = None
        if self.adapters is not None:
            await _await_step("adapter supervisor", self.adapters.stop())
        if self.adapter_cursor_store is not None:
            _sync_step("adapter cursor store", self.adapter_cursor_store.close)
            self.adapter_cursor_store = None
        if self.openclaw_approval_store is not None:
            _sync_step("OpenClaw approval store", self.openclaw_approval_store.close)
            self.openclaw_approval_store = None
        if self.events is not None:
            _sync_step("event admission", self.events.stop_accepting)
        if self.pipelines is not None:
            await _await_step("pipelines", self.pipelines.stop())
        if self.projections is not None:
            await _await_step("projections and bus", self.projections.stop())
        elif self.bus is not None:
            await _await_step("event bus", self.bus.stop())
        if self.event_log is not None:
            await _await_step("event log", self.event_log.close())
        if first_failure is not None:
            raise first_failure

    def _require_events(self) -> EventWriter:
        if self.events is None:
            raise RuntimeError("event writer unavailable")
        return self.events

    def _household_enrollment_envelopes(self) -> list[EventEnvelope]:
        """Translate reviewed enrollment config into canonical domain events.

        The member mapping remains the portal-authentication input.  These
        idempotent events are its one-way receipt into household truth so the
        People projection never has to query mutable bootstrap files.
        """

        mapping = self.settings.household_mapping
        now = EventEnvelope.now_utc_iso()
        household_party_id = str(
            mapping.get("household_party_id")
            or (
                "household_"
                + hashlib.sha256(
                    self.settings.instance.tenant_id.encode("utf-8")
                ).hexdigest()[:20]
            )
        )

        def envelope(
            event_type: str,
            payload: dict[str, Any],
            *,
            domain_id: str,
            scope: str = "shared:household",
        ) -> EventEnvelope:
            identity = json.dumps(
                {
                    "event_type": event_type,
                    "payload": payload,
                    "scope": scope,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
            domain_digest = hashlib.sha256(domain_id.encode("utf-8")).hexdigest()[:16]
            return EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=self.settings.instance.tenant_id,
                type=event_type,
                schema_version=1,
                occurred_at=now,
                source_adapter="bootstrap-household-enrollment",
                source_account_id="member-mapping",
                owner_scope=scope,
                visibility_scope=scope,
                sensitivity="normal",
                idempotency_key=(
                    f"household-enrollment:{event_type}:{domain_digest}:{digest}"
                ),
                payload=payload,
            )

        values: list[EventEnvelope] = []
        for principal in self.settings.service_principals:
            values.append(
                envelope(
                    "party.created",
                    {
                        "party_id": principal.party_id,
                        "kind": "organization",
                        "display_name": principal.display_name,
                        "sort_name": principal.display_name.casefold(),
                        "attributes": {
                            "source": "service_identity_reconciliation",
                            "nonhuman_service": True,
                            "service_principal_id": principal.service_principal_id,
                            "service_kind": principal.kind,
                            "private_scope": principal.private_scope,
                            "provider_authority": False,
                        },
                    },
                    domain_id=principal.party_id,
                )
            )
        if not mapping:
            return values

        values.insert(
            0,
            envelope(
                "party.created",
                {
                    "party_id": household_party_id,
                    "kind": "household",
                    "display_name": str(mapping.get("household_label") or "Household"),
                    "sort_name": str(
                        mapping.get("household_label") or "Household"
                    ).casefold(),
                    "attributes": {"source": "household_enrollment"},
                },
                domain_id=household_party_id,
            ),
        )
        for principal in self.settings.service_principals:
            values.append(
                envelope(
                    "membership.added",
                    {
                        "membership_id": (
                            "membership:household-service:chief-of-staff"
                            if principal.service_principal_id
                            == COS_SERVICE_PRINCIPAL_ID
                            else f"membership:household-service:{principal.party_id}"
                        ),
                        "party_id": principal.party_id,
                        "parent_party_id": household_party_id,
                        "role": "household_service",
                        "attributes": {
                            "source": "service_identity_reconciliation",
                            "nonhuman": True,
                            "provider_authority": False,
                        },
                    },
                    domain_id=f"service-membership:{principal.party_id}",
                )
            )
        for member in mapping["members"]:
            party_id = str(member["member_id"])
            label = str(member["label"]).strip()
            values.append(
                envelope(
                    "party.created",
                    {
                        "party_id": party_id,
                        "kind": "person",
                        "display_name": label,
                        "sort_name": label.casefold(),
                        "attributes": {
                            "source": "household_enrollment",
                            "enrollment_role": str(member["role"]),
                        },
                    },
                    domain_id=party_id,
                )
            )
        for party in mapping.get("parties", []):
            party_id = str(party["party_id"])
            label = str(party["label"]).strip()
            scope = str(party.get("visibility_scope") or "shared:household")
            values.append(
                envelope(
                    "party.created",
                    {
                        "party_id": party_id,
                        "kind": str(party.get("kind") or "person"),
                        "display_name": label,
                        "sort_name": str(party.get("sort_name") or label).casefold(),
                        "nickname": party.get("nickname"),
                        "attributes": {"source": "reviewed_household_party"},
                    },
                    domain_id=party_id,
                    scope=scope,
                )
            )
        for membership in mapping["memberships"]:
            payload: dict[str, Any] = {
                "membership_id": str(membership["membership_id"]),
                "party_id": str(membership["party_id"]),
                "parent_party_id": household_party_id,
                "role": str(membership["role"]),
                "attributes": {"source": "household_enrollment"},
            }
            values.append(
                envelope(
                    "membership.added",
                    payload,
                    domain_id=payload["membership_id"],
                )
            )
        mutual_kinds = {"coparent", "partner", "sibling", "spouse"}
        for relationship in mapping["relationships"]:
            relationship_id = str(relationship["relationship_id"])
            label = str(relationship["kind"])
            direction = str(
                relationship.get("direction")
                or ("mutual" if label in mutual_kinds else "a_to_b")
            )
            if direction not in {"a_to_b", "b_to_a", "mutual"}:
                raise ValueError("household relationship direction is invalid")
            scope = str(relationship.get("visibility_scope") or "shared:household")
            values.append(
                envelope(
                    "relationship.added",
                    {
                        "relationship_id": relationship_id,
                        "party_a": str(relationship["from_party_id"]),
                        "party_b": str(relationship["to_party_id"]),
                        "label": label,
                        "direction": direction,
                        "attributes": {"source": "household_enrollment"},
                    },
                    domain_id=relationship_id,
                    scope=scope,
                )
            )
        return values

    async def _reconcile_household_enrollment(self) -> None:
        envelopes = self._household_enrollment_envelopes()
        if not envelopes:
            return
        event_ids = await self._require_events().write_batch(envelopes)
        await self.wait_for_projection("parties", max(event_ids))

    async def _reconcile_constitutional_operating_agreement(self) -> None:
        principals = sorted(
            member_id
            for member_id, role in self.settings.member_roles.items()
            if role == "principal"
        )
        now = EventEnvelope.now_utc_iso()
        payload = constitutional_agreement_payload(
            adult_principal_ids=principals,
            registered_at=now,
            repository_root=self.settings.repository_root,
        )
        if self.projections is None:
            raise RuntimeError("projection runner unavailable")
        existing = (
            self.projections.connection("parties")
            .execute(
                "SELECT definition_digest FROM operating_agreements "
                "WHERE tenant_id=? AND agreement_id=? AND revision=?",
                (
                    self.settings.instance.tenant_id,
                    payload["agreement_id"],
                    payload["revision"],
                ),
            )
            .fetchone()
        )
        if existing is not None and str(existing["definition_digest"]) == str(
            payload["definition_digest"]
        ):
            return
        event_id = await self._require_events().write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=self.settings.instance.tenant_id,
                type="operating_agreement.constitution_registered",
                schema_version=1,
                occurred_at=now,
                source_adapter="constitutional-operating-agreement",
                source_account_id="release-baseline",
                owner_scope="shared:household",
                visibility_scope="shared:household",
                sensitivity="normal",
                actor_identity="system:constitutional-safety",
                idempotency_key=(
                    "constitutional-operating-agreement:"
                    f"{payload['agreement_id']}:{payload['revision']}:"
                    f"{payload['definition_digest']}"
                ),
                raw_ref=f"definition-sha256:{payload['definition_digest']}",
                payload=payload,
            )
        )
        await self.wait_for_projection("parties", event_id)

    async def _dispatch_external_write(self, event: dict[str, Any]) -> None:
        """Execute central-hosted provider commands; Apple commands wait for bridges."""

        from adminme.runtime.cos_reminders import dispatch, is_cos_reminder

        if is_cos_reminder(event):
            await dispatch(self, event)
            return
        payload = event["payload"]
        is_v2 = int(event.get("schema_version") or 1) >= 2
        if self.event_log is not None:
            terminal = await self.event_log.get_by_idempotency(
                str(event["tenant_id"]),
                f"external-sync-result:{payload['command_id']}",
            )
            if terminal is not None:
                terminal_payload = terminal.get("payload") or {}
                if is_v2:
                    valid_terminal = (
                        int(terminal.get("schema_version") or 1) >= 2
                        and terminal.get("type")
                        in {
                            "external_sync.write_completed",
                            "external_sync.write_failed",
                        }
                        and terminal.get("causation_id") == event["event_id"]
                        and terminal_payload.get("command_event_id")
                        == event["event_id"]
                        and all(
                            terminal.get(field) == event.get(field)
                            for field in (
                                "tenant_id",
                                "owner_scope",
                                "visibility_scope",
                            )
                        )
                        and all(
                            terminal_payload.get(field) == payload.get(field)
                            for field in (
                                "command_id",
                                "member_id",
                                "provider",
                                "provider_account_id",
                                "operation",
                                "request_fingerprint",
                                "domain_approval_event_id",
                                "openclaw_approval_event_id",
                                "origin_proposal_id",
                            )
                        )
                    )
                else:
                    valid_terminal = terminal_payload.get("command_event_id") in {
                        None,
                        event["event_id"],
                    }
                if not valid_terminal:
                    raise RuntimeError(
                        "provider command identity conflicts with its terminal"
                    )
                return
        communication_providers = {
            "gmail",
            "microsoft_graph",
            "bluebubbles",
            "twilio_sms",
            "twilio_whatsapp",
            "fixture",
        }
        provider = payload.get("provider")
        if provider == "bluebubbles":
            # Apple Messages is bridge-hosted.  The protected bridge command
            # queue owns suppression, delivery, and terminal result handling.
            return
        if (
            provider
            not in {"google_calendar", "google_tasks"} | communication_providers
        ):
            return
        if self.adapters is None:
            raise RuntimeError("adapter supervisor unavailable")
        now = EventEnvelope.now_utc_iso()
        result_type = "external_sync.write_completed"
        provider_called = False
        applied = False
        suppressed = False
        external_id: str | None = None
        result_provider_revision: str | None = None
        result_body: dict[str, Any] = {}
        details = (
            payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        )
        b03 = bool(details.get("b03_production_v2"))
        b03_contact_event_id: str | None = None
        b03_provider_result_event_id: str | None = None
        b03_binding: ProviderBinding | None = None
        creation_attempted = False
        content_fingerprint = str(
            payload.get("request_fingerprint")
            or hashlib.sha256(
                json.dumps(
                    payload.get("payload") or {},
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()
        )
        audit_token = None
        if is_v2:
            audit_token = set_outbound_audit_context(
                OutboundAuditContext(
                    initiating_command_id=str(payload["command_id"]),
                    initiating_event_id=str(event["event_id"]),
                    domain_approval_event_id=str(payload["domain_approval_event_id"]),
                    openclaw_approval_event_id=str(
                        payload["openclaw_approval_event_id"]
                    ),
                    provider=str(payload["provider"]),
                    provider_account_id=str(payload["provider_account_id"]),
                    operation=str(payload["operation"]),
                    content_fingerprint=content_fingerprint,
                    owner_scope=str(event["owner_scope"]),
                    visibility_scope=str(event["visibility_scope"]),
                    sensitivity=str(event["sensitivity"]),
                )
            )
        try:
            if b03:
                assert self.event_log is not None
                if (
                    provider != B03_PROVIDER
                    or payload.get("operation") != B03_OPERATION
                    or details.get("adapter_identity") != B03_ADAPTER_IDENTITY
                    or details.get("represented_cos") != COS_SERVICE_PRINCIPAL_ID
                    or details.get("actual_operator") != COS_AUTOMATION_OPERATOR_ID
                    or details.get("provider_owner") != COS_SERVICE_PRINCIPAL_ID
                    or details.get("maximum_effect_count") != 1
                ):
                    raise DeploymentReadinessBlocked("B03 command coordinate changed")
                desired = b03_canonical_task_content(
                    {
                        key: details[key]
                        for key in (
                            "title",
                            "notes",
                            "due",
                            "status",
                            "parent",
                            "previous",
                        )
                        if key in details
                    }
                )
                if b03_task_content_fingerprint(desired) != content_fingerprint:
                    raise DeploymentReadinessBlocked(
                        "B03 task content fingerprint changed"
                    )
                exact_coordinate = (
                    str(details.get("resource_owner")),
                    str(details.get("provider_owner")),
                    str(details.get("represented_cos")),
                    str(details.get("context_scope")),
                    str(details.get("on_behalf_of")),
                    B03_PROVIDER,
                    str(payload.get("provider_account_id") or "").casefold(),
                    str(details.get("adapter_identity")),
                    str(details.get("tasklist_id")),
                    B03_OPERATION,
                )
                b03_matches = [
                    binding
                    for binding in self.settings.provider_bindings
                    if binding.b03_dispatch_coordinate == exact_coordinate
                ]
                if len(b03_matches) != 1:
                    raise DeploymentReadinessBlocked(
                        f"B03 route expected one full owner/destination binding, found {len(b03_matches)}"
                    )
                b03_binding = b03_matches[0]
                prior_terminal = await self.event_log.get_by_idempotency(
                    str(event["tenant_id"]), f"b03-terminal:{payload['command_id']}"
                )
                if prior_terminal is not None:
                    return
                prior_contact = await self.event_log.get_by_idempotency(
                    str(event["tenant_id"]), f"b03-contact:{payload['command_id']}"
                )
                if prior_contact is not None:
                    prior_provider_result = await self.event_log.get_by_idempotency(
                        str(event["tenant_id"]),
                        f"b03-provider-result:{payload['command_id']}",
                    )
                    recovered = (
                        prior_provider_result
                        if prior_provider_result is not None
                        and prior_provider_result["payload"].get(
                            "execution_envelope_id"
                        )
                        == details.get("execution_envelope_id")
                        and prior_provider_result["payload"].get(
                            "contact_attempt_event_id"
                        )
                        == prior_contact["event_id"]
                        and prior_provider_result["payload"].get(
                            "desired_state_fingerprint"
                        )
                        == content_fingerprint
                        and prior_provider_result["payload"].get(
                            "provider_account_coordinate"
                        )
                        == payload.get("provider_account_id")
                        and prior_provider_result["payload"].get("task_list_id")
                        == details.get("tasklist_id")
                        else None
                    )
                    recovered_at = EventEnvelope.now_utc_iso()
                    await self._require_events().write(
                        EventEnvelope(
                            event_at_ms=int(time.time() * 1000),
                            tenant_id=str(event["tenant_id"]),
                            type="cos.google_tasks_execution.terminal",
                            schema_version=2,
                            occurred_at=recovered_at,
                            source_adapter="external-sync-central-dispatcher",
                            source_account_id=str(payload["provider_account_id"]),
                            owner_scope=str(event["owner_scope"]),
                            visibility_scope=str(event["visibility_scope"]),
                            sensitivity=event["sensitivity"],
                            causation_id=str(prior_contact["event_id"]),
                            idempotency_key=f"b03-terminal:{payload['command_id']}",
                            raw_ref=f"content-sha256:{content_fingerprint}",
                            payload={
                                "command_id": payload["command_id"],
                                "execution_envelope_id": details[
                                    "execution_envelope_id"
                                ],
                                "command_event_id": event["event_id"],
                                "contact_attempt_event_id": prior_contact["event_id"],
                                "provider_result_event_id": (
                                    recovered["event_id"] if recovered else None
                                ),
                                "outcome": (
                                    "success"
                                    if recovered
                                    else "ambiguous_after_possible_contact"
                                ),
                                "provider": B03_PROVIDER,
                                "provider_account_coordinate": payload[
                                    "provider_account_id"
                                ],
                                "task_list_id": details["tasklist_id"],
                                "native_task_id": (
                                    recovered["payload"]["native_task_id"]
                                    if recovered
                                    else None
                                ),
                                "provider_revision": (
                                    recovered["payload"].get("provider_revision")
                                    if recovered
                                    else None
                                ),
                                "provider_task_status": (
                                    recovered["payload"].get("provider_task_status")
                                    if recovered
                                    else None
                                ),
                                "desired_state_fingerprint": content_fingerprint,
                                "returned_state_fingerprint": (
                                    recovered["payload"]["returned_state_fingerprint"]
                                    if recovered
                                    else None
                                ),
                                "provider_contact_at": prior_contact["payload"][
                                    "committed_at"
                                ],
                                "terminal_at": recovered_at,
                                "provider_called": True,
                                "applied": bool(recovered),
                                "suppressed": False,
                                "effect_count": 1 if recovered else 0,
                                "retry_permitted": False,
                                "reason_code": (
                                    None
                                    if recovered
                                    else "restart_or_redelivery_after_contact_without_terminal_result"
                                ),
                            },
                        )
                    )
                    return
                if self.projections is None:
                    raise DeploymentReadinessBlocked(
                        "B03 execution projection is unavailable before provider contact"
                    )
                execution_row = (
                    self.projections.connection("tasks")
                    .execute(
                        "SELECT * FROM cos_google_tasks_execution_v2 "
                        "WHERE tenant_id=? AND command_id=?",
                        (str(event["tenant_id"]), str(payload["command_id"])),
                    )
                    .fetchone()
                )
                if (
                    execution_row is None
                    or execution_row["state"] != "approved"
                    or execution_row["used_at"] is not None
                    or execution_row["revoked_at"] is not None
                    or execution_row["contact_attempt_event_id"] is not None
                    or execution_row["terminal_result_event_id"] is not None
                    or execution_row["envelope_id"]
                    != details.get("execution_envelope_id")
                    or execution_row["envelope_revision"]
                    != details.get("execution_envelope_revision")
                    or execution_row["domain_approval_event_id"]
                    != payload.get("domain_approval_event_id")
                    or execution_row["openclaw_approval_event_id"]
                    != payload.get("openclaw_approval_event_id")
                    or execution_row["content_fingerprint"] != content_fingerprint
                    or execution_row["expires_at"] != details.get("expires_at")
                    or datetime.fromisoformat(
                        str(execution_row["expires_at"]).replace("Z", "+00:00")
                    )
                    <= datetime.now(timezone.utc)
                ):
                    raise DeploymentReadinessBlocked(
                        "B03 envelope is no longer exact, current, unused, and approved"
                    )
            if self.settings.deployment_evidence_required:
                observation_active = (
                    await self.observation.is_active()
                    if self.observation is not None
                    else True
                )
                readiness = self._deployment_readiness(
                    observation_active=observation_active
                )
                if not readiness["ready"]:
                    dimensions = ",".join(readiness.get("blocking_dimensions") or [])
                    raise DeploymentReadinessBlocked(
                        f"deployment readiness blocked provider dispatch: {dimensions}"
                    )
                provider_kind_token = {
                    "google_calendar": "calendar",
                    "google_tasks": "tasks",
                    "gmail": "gmail",
                    "microsoft_graph": "microsoft",
                    "bluebubbles": "bluebubbles",
                    "twilio_sms": "twilio",
                    "twilio_whatsapp": "twilio",
                }.get(str(provider), str(provider))
                command_binding = next(
                    (
                        binding
                        for binding in self.settings.provider_bindings
                        if (
                            (b03 and binding is b03_binding)
                            or (
                                not b03
                                and binding.member_id == payload.get("member_id")
                            )
                        )
                        and binding.provider_account_id.casefold()
                        == str(payload.get("provider_account_id") or "").casefold()
                        and provider_kind_token in binding.kind.casefold()
                    ),
                    None,
                )
                if command_binding is None:
                    raise DeploymentReadinessBlocked(
                        "provider command has no exact reviewed member/account binding"
                    )
            if b03:
                admission_payload = {
                    "command_id": payload["command_id"],
                    "execution_envelope_id": details["execution_envelope_id"],
                    "execution_envelope_revision": details[
                        "execution_envelope_revision"
                    ],
                    "initiating_event_id": event["event_id"],
                    "domain_approval_event_id": payload["domain_approval_event_id"],
                    "openclaw_approval_event_id": payload["openclaw_approval_event_id"],
                    "provider": B03_PROVIDER,
                    "provider_account_coordinate": payload["provider_account_id"],
                    "operation": B03_OPERATION,
                    "destination_task_list_coordinate": details["tasklist_id"],
                    "content_fingerprint": content_fingerprint,
                    "owner_scope": event["owner_scope"],
                    "visibility_scope": event["visibility_scope"],
                    "expires_at": details["expires_at"],
                    "maximum_effect_count": 1,
                }
                committed_at = EventEnvelope.now_utc_iso()
                b03_contact_event_id = await self._require_events().write(
                    EventEnvelope(
                        event_at_ms=int(time.time() * 1000),
                        tenant_id=str(event["tenant_id"]),
                        type="cos.google_tasks_execution.contact_attempt_committed",
                        schema_version=2,
                        occurred_at=committed_at,
                        source_adapter="external-sync-central-dispatcher",
                        source_account_id=str(payload["provider_account_id"]),
                        owner_scope=str(event["owner_scope"]),
                        visibility_scope=str(event["visibility_scope"]),
                        sensitivity=event["sensitivity"],
                        causation_id=str(event["event_id"]),
                        idempotency_key=f"b03-contact:{payload['command_id']}",
                        raw_ref=f"content-sha256:{content_fingerprint}",
                        payload={
                            **admission_payload,
                            "scoped_admission_fingerprint": b03_canonical_sha256(
                                admission_payload
                            ),
                            "committed_at": committed_at,
                            "retry_permitted": False,
                        },
                    )
                )
                if audit_token is not None:
                    reset_outbound_audit_context(audit_token)
                audit_token = set_outbound_audit_context(
                    OutboundAuditContext(
                        initiating_command_id=str(payload["command_id"]),
                        initiating_event_id=str(event["event_id"]),
                        domain_approval_event_id=str(
                            payload["domain_approval_event_id"]
                        ),
                        openclaw_approval_event_id=str(
                            payload["openclaw_approval_event_id"]
                        ),
                        provider=B03_PROVIDER,
                        provider_account_id=str(payload["provider_account_id"]),
                        operation=B03_OPERATION,
                        content_fingerprint=content_fingerprint,
                        owner_scope=str(event["owner_scope"]),
                        visibility_scope=str(event["visibility_scope"]),
                        sensitivity=str(event["sensitivity"]),
                        scoped_admission=ScopedOutboundAdmission(
                            command_id=str(payload["command_id"]),
                            initiating_event_id=str(event["event_id"]),
                            execution_envelope_id=str(details["execution_envelope_id"]),
                            execution_envelope_revision=int(
                                details["execution_envelope_revision"]
                            ),
                            domain_approval_event_id=str(
                                payload["domain_approval_event_id"]
                            ),
                            openclaw_approval_event_id=str(
                                payload["openclaw_approval_event_id"]
                            ),
                            provider=B03_PROVIDER,
                            provider_account_id=str(payload["provider_account_id"]),
                            operation=B03_OPERATION,
                            destination_id=str(details["tasklist_id"]),
                            content_fingerprint=content_fingerprint,
                            owner_scope=str(event["owner_scope"]),
                            visibility_scope=str(event["visibility_scope"]),
                            expires_at=str(details["expires_at"]),
                            maximum_effect_count=1,
                            contact_attempt_event_id=b03_contact_event_id,
                        ),
                    )
                )
            fixture_delegated = (
                provider == "fixture"
                and isinstance(payload.get("payload"), dict)
                and payload["payload"].get("delegated_action") is True
            )
            if fixture_delegated:
                if self.fixture_action_adapter is None:
                    raise LookupError("fixture delegated-action adapter is unavailable")
                command_body = dict(payload)
                fixture_result = await self.fixture_action_adapter.apply(
                    session=build_internal_session(
                        str(payload["member_id"]),
                        "principal",
                        str(event["tenant_id"]),
                    ),
                    command=command_body,
                )
                # This adapter mutates only credential-free in-memory fixture
                # state.  Record the exact fixture effect in its result while
                # keeping provider-effect accounting at zero.
                provider_called = False
                applied = fixture_result.applied
                suppressed = fixture_result.suppressed
                external_id = fixture_result.external_id
                result_provider_revision = fixture_result.provider_revision
                result_body = {
                    "applied": applied,
                    "state": fixture_result.state,
                    "fixture_only": True,
                }
                action_id = str(payload["payload"]["action_id"])
                if action_id in self.fixture_action_adapter.suppress_terminal_for:
                    self.fixture_action_adapter.suppress_terminal_for.remove(action_id)
                    return
            elif provider in communication_providers:
                command_body = {
                    **dict(payload.get("payload") or {}),
                    "target_external_id": payload.get("target_external_id"),
                    "native_message_id": payload.get("target_external_id"),
                }
                communication_result: dict[str, Any]
                if provider == "fixture":
                    if self.fixture_adapter is None or payload["operation"] != "reply":
                        raise LookupError(
                            "fixture communication adapter is unavailable"
                        )
                    recipient = command_body.get("recipient")
                    body = command_body.get("body")
                    if not isinstance(recipient, str) or not recipient:
                        raise ValueError("reply recipient is unavailable")
                    if not isinstance(body, str) or not body.strip():
                        raise ValueError("reply body is empty")
                    provider_called = True
                    send_result = await self.fixture_adapter.send(
                        OutboundMessage(
                            channel="fixture",
                            recipient=recipient,
                            body=body,
                            metadata={
                                "thread_id": command_body.get("thread_id"),
                                "interaction_id": command_body.get("interaction_id"),
                            },
                        )
                    )
                    communication_result = {
                        "applied": send_result.delivered,
                        "suppressed": send_result.suppressed,
                        "external_id": send_result.external_id,
                        "state": {},
                    }
                    provider_called = not send_result.suppressed
                else:
                    provider_called = True
                    communication_result = (
                        await self.adapters.dispatch_communication_write(
                            provider=str(provider),
                            member_id=str(payload["member_id"]),
                            provider_account_id=str(payload["provider_account_id"]),
                            operation=str(payload["operation"]),
                            payload=command_body,
                        )
                    )
                    provider_called = not bool(communication_result.get("suppressed"))
                applied = bool(communication_result.get("applied"))
                suppressed = bool(communication_result.get("suppressed"))
                external_id = communication_result.get("external_id")
                result_body = {
                    "applied": applied,
                    "interaction_id": command_body.get("interaction_id"),
                    "state": communication_result.get("state") or {},
                }
            else:
                if (
                    not b03
                    and is_v2
                    and provider in {"google_calendar", "google_tasks"}
                    and payload.get("operation") in {"create_event", "create_task"}
                ):
                    assert self.event_log is not None
                    attempt_key = f"external-sync-attempt:{payload['command_id']}"
                    prior_attempt = await self.event_log.get_by_idempotency(
                        str(event["tenant_id"]), attempt_key
                    )
                    creation_attempted = True
                    if prior_attempt is not None:
                        attempt_payload = prior_attempt.get("payload") or {}
                        if (
                            prior_attempt.get("type") != "external_sync.write_attempted"
                            or prior_attempt.get("causation_id") != event["event_id"]
                            or attempt_payload.get("command_event_id")
                            != event["event_id"]
                            or any(
                                prior_attempt.get(field) != event.get(field)
                                for field in (
                                    "tenant_id",
                                    "owner_scope",
                                    "visibility_scope",
                                )
                            )
                            or any(
                                attempt_payload.get(field) != payload.get(field)
                                for field in (
                                    "command_id",
                                    "member_id",
                                    "provider",
                                    "provider_account_id",
                                    "operation",
                                    "request_fingerprint",
                                )
                            )
                        ):
                            raise ValueError(
                                "provider command identity conflicts with its attempt"
                            )
                        provider_called = True
                        raise RuntimeError(
                            "ambiguous_after_possible_contact: automatic creation retry refused"
                        )
                    await self._require_events().write(
                        EventEnvelope(
                            event_at_ms=int(time.time() * 1000),
                            tenant_id=event["tenant_id"],
                            type="external_sync.write_attempted",
                            schema_version=1,
                            occurred_at=now,
                            source_adapter="external-sync-central-dispatcher",
                            source_account_id=str(payload["provider_account_id"]),
                            owner_scope=event["owner_scope"],
                            visibility_scope=event["visibility_scope"],
                            sensitivity=event["sensitivity"],
                            causation_id=event["event_id"],
                            idempotency_key=attempt_key,
                            payload={
                                "command_id": payload["command_id"],
                                "command_event_id": event["event_id"],
                                "member_id": payload["member_id"],
                                "provider": provider,
                                "provider_account_id": payload["provider_account_id"],
                                "operation": payload["operation"],
                                "request_fingerprint": content_fingerprint,
                                "attempted_at": now,
                            },
                        )
                    )
                provider_called = True
                result = await self.adapters.dispatch_write(
                    provider=str(payload["provider"]),
                    member_id=str(payload["member_id"]),
                    provider_account_id=str(payload["provider_account_id"]),
                    operation=WriteOperation(
                        kind=str(payload["operation"]),
                        target_external_id=payload.get("target_external_id"),
                        payload=dict(payload.get("payload") or {}),
                    ),
                    exact_coordinate=(
                        b03_binding.b03_dispatch_coordinate
                        if b03 and b03_binding is not None
                        else None
                    ),
                )
                applied = result.applied
                suppressed = result.suppressed
                provider_called = not result.suppressed
                external_id = result.external_id
                result_provider_revision = result.provider_revision
                result_body = {
                    "applied": applied,
                    "provider_status": result.provider_status,
                    "returned_state": result.returned_state,
                }
            result_payload: dict[str, Any] = {
                "command_id": payload["command_id"],
                "member_id": payload["member_id"],
                "provider": payload["provider"],
                "operation": payload["operation"],
                "external_id": external_id,
                "provider_revision": result_provider_revision,
                "result": result_body,
                "completed_at": now,
                "suppressed": suppressed,
            }
        except Exception as exc:  # provider failures are terminal, typed evidence
            if isinstance(exc, (DeploymentReadinessBlocked, LookupError, ValueError)):
                provider_called = False
            result_type = "external_sync.write_failed"
            result_payload = {
                "command_id": payload["command_id"],
                "member_id": payload["member_id"],
                "provider": payload["provider"],
                "operation": payload["operation"],
                "error_kind": (
                    "conflict"
                    if getattr(getattr(exc, "resp", None), "status", None) == 412
                    or "conflict" in type(exc).__name__.casefold()
                    else "permission"
                    if isinstance(exc, DeploymentReadinessBlocked)
                    else "not_found"
                    if isinstance(exc, LookupError)
                    else "provider"
                ),
                "message": f"{type(exc).__name__}: {exc}",
                "retryable": not isinstance(
                    exc, (DeploymentReadinessBlocked, LookupError, ValueError)
                )
                and not b03
                and not creation_attempted,
                "failed_at": now,
            }
        finally:
            if audit_token is not None:
                reset_outbound_audit_context(audit_token)
        if b03:
            terminal_at = EventEnvelope.now_utc_iso()
            returned_state = (
                result_body.get("returned_state", {})
                if isinstance(result_body, dict)
                else {}
            )
            if (
                result_type == "external_sync.write_completed"
                and applied
                and not suppressed
                and external_id
                and b03_contact_event_id
            ):
                b03_provider_result_event_id = await self._require_events().write(
                    EventEnvelope(
                        event_at_ms=int(time.time() * 1000),
                        tenant_id=str(event["tenant_id"]),
                        type="cos.google_tasks_execution.provider_result_received",
                        schema_version=2,
                        occurred_at=terminal_at,
                        source_adapter="external-sync-central-dispatcher",
                        source_account_id=str(payload["provider_account_id"]),
                        owner_scope=str(event["owner_scope"]),
                        visibility_scope=str(event["visibility_scope"]),
                        sensitivity=event["sensitivity"],
                        causation_id=b03_contact_event_id,
                        idempotency_key=f"b03-provider-result:{payload['command_id']}",
                        raw_ref=f"content-sha256:{content_fingerprint}",
                        payload={
                            "command_id": payload["command_id"],
                            "execution_envelope_id": details["execution_envelope_id"],
                            "command_event_id": event["event_id"],
                            "contact_attempt_event_id": b03_contact_event_id,
                            "provider": B03_PROVIDER,
                            "provider_account_coordinate": payload[
                                "provider_account_id"
                            ],
                            "task_list_id": details["tasklist_id"],
                            "native_task_id": external_id,
                            "provider_revision": result_provider_revision,
                            "provider_task_status": result_body.get("provider_status"),
                            "desired_state_fingerprint": content_fingerprint,
                            "returned_state_fingerprint": b03_returned_state_fingerprint(
                                returned_state
                            ),
                            "provider_contact_at": now,
                            "result_received_at": terminal_at,
                            "applied": True,
                            "effect_count": 1,
                            "retry_permitted": False,
                        },
                    )
                )
                b03_outcome = "success"
                effect_count = 1
            elif b03_contact_event_id is not None:
                b03_outcome = "ambiguous_after_possible_contact"
                effect_count = 0
            else:
                b03_outcome = "failure_before_contact"
                effect_count = 0
            await self._require_events().write(
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=str(event["tenant_id"]),
                    type="cos.google_tasks_execution.terminal",
                    schema_version=2,
                    occurred_at=terminal_at,
                    source_adapter="external-sync-central-dispatcher",
                    source_account_id=str(payload["provider_account_id"]),
                    owner_scope=str(event["owner_scope"]),
                    visibility_scope=str(event["visibility_scope"]),
                    sensitivity=event["sensitivity"],
                    causation_id=b03_contact_event_id or str(event["event_id"]),
                    idempotency_key=f"b03-terminal:{payload['command_id']}",
                    raw_ref=f"content-sha256:{content_fingerprint}",
                    payload={
                        "command_id": payload["command_id"],
                        "execution_envelope_id": details["execution_envelope_id"],
                        "command_event_id": event["event_id"],
                        "contact_attempt_event_id": b03_contact_event_id,
                        "provider_result_event_id": b03_provider_result_event_id,
                        "outcome": b03_outcome,
                        "provider": B03_PROVIDER,
                        "provider_account_coordinate": payload["provider_account_id"],
                        "task_list_id": details["tasklist_id"],
                        "native_task_id": external_id
                        if b03_outcome == "success"
                        else None,
                        "provider_revision": result_provider_revision,
                        "provider_task_status": result_body.get("provider_status")
                        if isinstance(result_body, dict)
                        else None,
                        "desired_state_fingerprint": content_fingerprint,
                        "returned_state_fingerprint": (
                            b03_returned_state_fingerprint(returned_state)
                            if b03_outcome == "success"
                            else None
                        ),
                        "provider_contact_at": now if b03_contact_event_id else None,
                        "terminal_at": terminal_at,
                        "provider_called": bool(b03_contact_event_id),
                        "applied": b03_outcome == "success",
                        "suppressed": bool(suppressed)
                        if "suppressed" in locals()
                        else False,
                        "effect_count": effect_count,
                        "retry_permitted": False,
                        "reason_code": (
                            None
                            if b03_outcome == "success"
                            else result_payload.get("message", b03_outcome)
                        ),
                    },
                )
            )
        if is_v2:
            result_payload.update(
                {
                    "command_event_id": event["event_id"],
                    "domain_approval_event_id": payload["domain_approval_event_id"],
                    "openclaw_approval_event_id": payload["openclaw_approval_event_id"],
                    "provider_account_id": payload["provider_account_id"],
                    "request_fingerprint": content_fingerprint,
                    "provider_called": provider_called,
                    "origin_proposal_id": payload.get("origin_proposal_id"),
                }
            )
        await self._require_events().write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=event["tenant_id"],
                type=result_type,
                schema_version=2 if is_v2 else 1,
                occurred_at=now,
                source_adapter="external-sync-central-dispatcher",
                source_account_id=str(payload["provider_account_id"]),
                owner_scope=event["owner_scope"],
                visibility_scope=event["visibility_scope"],
                sensitivity=event["sensitivity"],
                correlation_id=event.get("correlation_id"),
                causation_id=event["event_id"],
                idempotency_key=f"external-sync-result:{payload['command_id']}",
                raw_ref=f"content-sha256:{content_fingerprint}",
                payload=result_payload,
            )
        )

    def member_chat_binding(self, member_id: str) -> tuple[str, str]:
        """Return an opaque actor reference and signed OpenClaw session key."""

        material = (
            self.settings.openclaw_shared_secret.encode("utf-8")
            if self.settings.openclaw_shared_secret
            else self.settings.encryption_key
        )
        peer = hmac.new(
            material,
            f"portal-peer|{self.settings.instance.tenant_id}|{member_id}".encode(),
            hashlib.sha256,
        ).hexdigest()[:20]
        signature = hmac.new(
            material, f"portal-session|{peer}".encode(), hashlib.sha256
        ).hexdigest()[:32]
        return (
            f"portal:{peer}",
            f"agent:{member_agent_id(member_id)}:administrateme-portal-{peer}-{signature}",
        )

    def resolve_member_chat_actor(self, actor_id: str) -> str | None:
        for member_id in sorted(self.settings.member_chat_enabled_members):
            expected, _ = self.member_chat_binding(member_id)
            if hmac.compare_digest(actor_id, expected):
                return member_id
        return None

    def member_chat_policy(self, member_id: str, role: str) -> dict[str, Any]:
        enabled = member_id in self.settings.member_chat_enabled_members
        if role in {"ambient", "device"}:
            return {
                "state": "unauthorized",
                "enabled": False,
                "reason": "role_has_no_chat",
            }
        if (
            role == "child"
            and member_id not in self.settings.child_chat_enabled_members
        ):
            return {
                "state": "disabled",
                "enabled": False,
                "reason": "guardian_disabled_child_chat",
            }
        if not enabled:
            return {
                "state": "disabled",
                "enabled": False,
                "reason": "member_chat_disabled",
            }
        if not self.settings.openclaw_model_configured:
            return {
                "state": "unavailable",
                "enabled": True,
                "reason": "model_provider_not_configured",
            }
        if self.settings.deterministic_member_chat_fixture:
            return {
                "state": "healthy",
                "enabled": True,
                "transport": "deterministic-openclaw-fixture",
            }
        if not self.settings.openclaw_gateway_token:
            return {
                "state": "unavailable",
                "enabled": True,
                "reason": "openclaw_chat_transport_not_configured",
            }
        openclaw_state = self.effective_openclaw_state()
        if openclaw_state != "healthy":
            return {
                "state": "degraded",
                "enabled": True,
                "reason": f"openclaw_{openclaw_state}",
            }
        return {"state": "healthy", "enabled": True, "transport": "openclaw-gateway"}

    async def wait_for_projection(
        self, projection_name: str, event_id: str, *, timeout_s: float = 8.0
    ) -> None:
        if self.bus is None:
            raise RuntimeError("event bus unavailable")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = await self.bus.subscriber_status(f"projection:{projection_name}")
            checkpoint = status.get("checkpoint_event_id")
            if checkpoint is not None and checkpoint >= event_id:
                return
            if status.get("degraded"):
                raise RuntimeError(
                    f"projection {projection_name} degraded at "
                    f"{status.get('poison_event_id')}"
                )
            await asyncio.sleep(0.01)
        raise TimeoutError(f"projection {projection_name} did not reach {event_id}")

    async def wait_for_subscriber(
        self, subscriber_id: str, event_id: str, *, timeout_s: float = 8.0
    ) -> None:
        if self.bus is None:
            raise RuntimeError("event bus unavailable")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = await self.bus.subscriber_status(subscriber_id)
            checkpoint = status.get("checkpoint_event_id")
            if checkpoint is not None and checkpoint >= event_id:
                return
            if status.get("degraded"):
                raise RuntimeError(
                    f"subscriber {subscriber_id} degraded at "
                    f"{status.get('poison_event_id')}"
                )
            await asyncio.sleep(0.01)
        raise TimeoutError(f"subscriber {subscriber_id} did not reach {event_id}")

    def _xlsx_query_context(self) -> XlsxQueryContext:
        if self.projections is None:
            raise RuntimeError("projection runner unavailable")
        return XlsxQueryContext(
            parties_conn=self.projections.connection("parties"),
            tasks_conn=self.projections.connection("tasks"),
            commitments_conn=self.projections.connection("commitments"),
            recurrences_conn=self.projections.connection("recurrences"),
            calendars_conn=self.projections.connection("calendars"),
            places_assets_accounts_conn=self.projections.connection(
                "places_assets_accounts"
            ),
            money_conn=self.projections.connection("money"),
        )

    def _parties_connection_factory(self) -> Any:
        db_path = self.settings.instance.projection_db_path("parties")
        conn = sqlcipher3.connect(
            str(db_path), isolation_level=None, check_same_thread=False
        )
        conn.row_factory = sqlcipher3.Row
        key_pragma = f"x'{self.settings.encryption_key.hex()}'"
        conn.execute(f'PRAGMA key = "{key_pragma}"')
        conn.execute("PRAGMA query_only=ON")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return conn

    def _load_standing_orders(self) -> None:
        cron_path = (
            self.settings.repository_root / "bootstrap" / "openclaw" / "cron.yaml"
        )
        data = yaml.safe_load(cron_path.read_text(encoding="utf-8")) or {}
        entries = data.get("entries") or []
        if not isinstance(entries, list):
            raise ValueError("OpenClaw standing-order entries must be a list")
        self.standing_orders = {
            str(entry["program_id"]): dict(entry)
            for entry in entries
            if isinstance(entry, dict) and entry.get("program_id")
        }

    def _inventory_optional_adapters(self) -> None:
        root = self.settings.repository_root / "packs" / "adapters"
        effective_config = restrict_adapter_config(self.settings.adapter_config)
        test_policy = policy_from_adapter_config(self.settings.adapter_config)
        configured = {
            str(item.get("kind"))
            for item in effective_config.get("adapters", [])
            if isinstance(item, dict) and item.get("enabled") is not False
        }
        for manifest_path in sorted(root.rglob("pack.yaml")):
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            pack_block = manifest.get("pack") if isinstance(manifest, dict) else None
            pack_id = None
            if isinstance(pack_block, dict):
                pack_id = pack_block.get("id") or pack_block.get("name")
            pack_id = str(pack_id or manifest_path.parent.name)
            capability_id = pack_id.removeprefix("adapter:")
            configured_aliases = {pack_id, capability_id}
            enabled = bool(configured_aliases & configured)
            capability_blocks = manifest.get("capabilities") or []
            categories = sorted(
                {
                    str(item.get("kind"))
                    for item in capability_blocks
                    if isinstance(item, dict) and item.get("kind")
                }
            )
            runtimes = sorted(
                {
                    str(item.get("runtime"))
                    for item in capability_blocks
                    if isinstance(item, dict) and item.get("runtime")
                }
            )
            self.capabilities[f"adapter:{capability_id}"] = CapabilityRecord(
                name=capability_id,
                kind="adapter",
                state="observation_only" if enabled else "disabled",
                reason=(
                    "configured for the production adapter supervisor; inbound sync "
                    "is active while outbound writes remain governed by observation mode"
                    if enabled
                    else "local acquisition excluded by the bounded test sample"
                    if test_policy is not None
                    else "optional provider not configured; no credential loaded"
                ),
                details={
                    "manifest": str(manifest_path.relative_to(root)),
                    "categories": categories,
                    "runtimes": runtimes,
                    "provider_health": "not_configured" if not enabled else "starting",
                    "production_loader": enabled,
                    "deterministic_fixture_coverage": True,
                },
            )

        if self.fixture_adapter is not None:
            self.capabilities["adapter:fixture-messaging"] = CapabilityRecord(
                name="fixture-messaging",
                kind="adapter",
                state="healthy",
                details={
                    "category": "Cat-A",
                    "provider": "deterministic-fixture",
                    "event_sink": "runtime-event-writer",
                },
            )

    def _inventory_data_extensions(self) -> None:
        active_persona = self.extensions.active_persona()["id"]
        for pack in self.extensions.packs():
            details: dict[str, Any] = {
                "version": pack.version,
                "sha256": pack.sha256,
                "active": pack.kind == "persona" and pack.pack_id == active_persona,
                "authority_changes": False,
                "model_routing_changes": False,
            }
            self.capabilities[f"{pack.kind}:{pack.pack_id}"] = CapabilityRecord(
                name=pack.pack_id,
                kind=f"{pack.kind}_extension",
                state="healthy",
                details=details,
            )

    async def _hydrate_openclaw_state(self) -> None:
        if not self.settings.openclaw_enabled or self.event_log is None:
            return
        latest: dict[str, Any] | None = None
        async for event in self.event_log.read_since(
            types=["openclaw.integration.state_changed"]
        ):
            latest = event
        if latest is not None:
            state = latest["payload"].get("state")
            # A prior process's healthy report is not evidence that the current
            # Gateway process is alive. Require a fresh heartbeat after every
            # Python runtime start; durable non-healthy states remain truthful.
            if state == "healthy":
                self.openclaw_state = "degraded"
            elif state in {"degraded", "unavailable", "incompatible"}:
                self.openclaw_state = state

    def record_openclaw_health_report(self, state: str) -> None:
        self.openclaw_state = state
        self.openclaw_last_report_monotonic = time.monotonic()
        self.openclaw_last_reported_at = EventEnvelope.now_utc_iso()

    def effective_openclaw_state(self) -> str:
        if self.openclaw_state != "healthy":
            return self.openclaw_state
        last_report = self.openclaw_last_report_monotonic
        if (
            last_report is None
            or time.monotonic() - last_report
            > self.OPENCLAW_HEARTBEAT_MAX_STALE_SECONDS
        ):
            return "degraded"
        return "healthy"

    async def _refresh_capability_registry(self) -> None:
        # Readiness, plugin heartbeats and member operations can overlap.
        # Collect under the same lock as publication: an older projection
        # snapshot must never overwrite a newer checkpoint after an await.
        # The reads below do not call back into this refresh boundary.
        async with self._capability_refresh_lock:
            await self._refresh_capability_registry_locked()

    async def _refresh_capability_registry_locked(self) -> None:
        assert self.projections is not None
        assert self.pipelines is not None
        assert self.observation is not None
        self._observation_active = await self.observation.is_active()
        from adminme.cos_channel_operation import runtime_live_authorization

        try:
            self._cos_live_authorization = await runtime_live_authorization(
                self, allow_verification=True
            )
        except (OSError, ValueError):
            self._cos_live_authorization = {"authorized": False}
        projection_status = await self.projections.status(include_row_counts=False)
        for name in self.projections.projection_names():
            status = projection_status[name]
            state: CapabilityState = "degraded" if status.get("degraded") else "healthy"
            self.capabilities[f"projection:{name}"] = CapabilityRecord(
                name=name,
                kind="projection",
                state=state,
                details={
                    "version": status["version"],
                    "lag_count": status["lag_count"],
                    "checkpoint_event_id": status["checkpoint"],
                    "last_success_at": status["last_success_at"],
                    "last_failure_at": status["last_failure_at"],
                    "consecutive_failures": status["consecutive_failures"],
                    "poison_event_id": status["poison_event_id"],
                    "retry_policy": status["retry_policy"],
                },
            )
        pipeline_status = await self.pipelines.status()
        for pack_id in self.pipelines.registered_pack_ids():
            status = pipeline_status.get(pack_id) or {}
            subscriber = status.get("subscriber_status") or {}
            degraded = bool(
                subscriber.get("degraded")
                or subscriber.get("poison_event_id")
                or int(subscriber.get("consecutive_failures") or 0) > 0
            )
            self.capabilities[f"pipeline:{pack_id}"] = CapabilityRecord(
                name=pack_id,
                kind="reactive_pipeline",
                state="degraded" if degraded else "healthy",
                reason=("subscriber delivery is degraded" if degraded else None),
                details={
                    "version": status.get("version"),
                    "checkpoint_event_id": subscriber.get("checkpoint_event_id"),
                    "lag_count": subscriber.get("lag_count"),
                    "last_success_at": subscriber.get("last_success_at"),
                    "last_failure_at": subscriber.get("last_failure_at"),
                    "consecutive_failures": subscriber.get("consecutive_failures", 0),
                    "poison_event_id": subscriber.get("poison_event_id"),
                    "retry_policy": subscriber.get("retry_policy"),
                },
            )

        if self.adapters is not None:
            live_adapters = self.adapters.inventory()
            by_name: dict[str, list[dict[str, Any]]] = {}
            for live in live_adapters.values():
                by_name.setdefault(str(live["adapter_name"]), []).append(live)
            for capability_key, existing in list(self.capabilities.items()):
                if not capability_key.startswith("adapter:"):
                    continue
                matching = by_name.get(existing.name) or []
                if not matching:
                    continue
                unhealthy = [
                    item for item in matching if item.get("state") not in {"running"}
                ]
                live_state: CapabilityState = (
                    "degraded"
                    if unhealthy
                    else "observation_only"
                    if self._observation_active
                    else "healthy"
                )
                existing.state = live_state
                existing.reason = (
                    "one or more configured adapter instances are not running"
                    if unhealthy
                    else (
                        "inbound sync is running; outbound writes are observation-only"
                        if self._observation_active
                        else None
                    )
                )
                existing.details.update(
                    {
                        "provider_health": live_state,
                        "instances": matching,
                        "durable_watermarks": [
                            (item.get("extra") or {}).get("durable_cursor")
                            for item in matching
                        ],
                    }
                )
        source_assurance = await self._source_assurance_status()
        source_assurance_state: CapabilityState = (
            "degraded" if source_assurance["state"] == "degraded" else "healthy"
        )
        self.capabilities["policy:source-assurance"] = CapabilityRecord(
            name="source-assurance",
            kind="source_disposition_policy",
            state=source_assurance_state,
            reason=(
                "source-assurance refresh failed"
                if source_assurance["freshness"]["state"] == "failed"
                else "one or more source facts lack their required terminal disposition"
                if source_assurance_state == "degraded"
                else None
            ),
            details=source_assurance,
        )
        effective_openclaw_state = self.effective_openclaw_state()
        if not self.settings.openclaw_enabled:
            standing_state: CapabilityState = "blocked"
        elif effective_openclaw_state == "healthy":
            standing_state = "healthy"
        elif effective_openclaw_state == "incompatible":
            standing_state = "blocked"
        else:
            standing_state = "degraded"
        standing_reason = (
            None
            if standing_state == "healthy"
            else (
                f"OpenClaw integration state: {effective_openclaw_state}"
                if self.settings.openclaw_enabled
                else "required OpenClaw gateway intentionally absent in credential-free mode"
            )
        )
        for program_id in sorted(self.standing_orders):
            self.capabilities[f"standing_order:{program_id}"] = CapabilityRecord(
                name=program_id,
                kind="openclaw_standing_order",
                state=standing_state,
                reason=standing_reason,
            )
        triage_projection = self.capabilities.get("projection:triage")
        extraction_pipeline = self.capabilities.get("pipeline:pipeline:noise_filtering")
        assurance_capability = self.capabilities.get("policy:source-assurance")
        triage_degraded = any(
            record is not None and record.state in {"degraded", "blocked"}
            for record in (
                triage_projection,
                extraction_pipeline,
                assurance_capability,
            )
        )
        self.capabilities["surface:triage-processing"] = CapabilityRecord(
            name="triage-processing",
            kind="member_surface_processing",
            state="degraded" if triage_degraded else "healthy",
            reason=(
                "Triage projection, extraction, or source accounting is degraded"
                if triage_degraded
                else None
            ),
            details={
                "projection_checkpoint_event_id": (
                    triage_projection.details.get("checkpoint_event_id")
                    if triage_projection is not None
                    else None
                ),
                "projection_lag_count": (
                    triage_projection.details.get("lag_count")
                    if triage_projection is not None
                    else None
                ),
                "extraction_checkpoint_event_id": (
                    extraction_pipeline.details.get("checkpoint_event_id")
                    if extraction_pipeline is not None
                    else None
                ),
                "source_assurance": source_assurance,
            },
        )

        for product in self.PRODUCT_NAMES:
            product_degraded = product == "member" and triage_degraded
            self.capabilities[f"product:{product}"] = CapabilityRecord(
                name=product,
                kind="product_namespace",
                state="degraded" if product_degraded else "healthy",
                reason=(
                    "one or more member-surface processing capabilities are degraded"
                    if product_degraded
                    else None
                ),
            )
        self.capabilities["read_model:activity-feed"] = CapabilityRecord(
            name="activity-feed",
            kind="read_model",
            state="healthy",
            details={
                "source": "encrypted-event-log",
                "scope_filtered": True,
                "raw_payload_exposed": False,
            },
        )
        self.capabilities["policy:notifications"] = CapabilityRecord(
            name="notifications",
            kind="domain_policy",
            state="healthy",
            details={
                "event_chain": [
                    "notification.signal.raised",
                    "notification.decision.made",
                    "notification.delivery.result",
                ],
                "delivery_owner": "openclaw",
            },
        )
        b03_bindings = [
            binding
            for binding in self.settings.provider_bindings
            if binding.write_binding_state == "trial"
        ]
        self.capabilities["action:google-tasks-create-production-v2"] = (
            CapabilityRecord(
                name="google_tasks_create",
                kind="single_use_provider_action",
                state="observation_only" if len(b03_bindings) == 1 else "disabled",
                reason=(
                    "one reviewed trial binding; every command still requires its exact envelope and two approvals"
                    if len(b03_bindings) == 1
                    else "no single reviewed B03 trial binding is active"
                ),
                details={
                    "execution_policy_version": B03_POLICY_VERSION,
                    "operation": B03_OPERATION,
                    "maximum_effect_count": 1,
                    "binding_match_count": len(b03_bindings),
                    "global_observation_mode_active": self._observation_active,
                    "scoped_admission_required": True,
                    "remaining_google_tasks_mutations": "prohibited",
                    "gmail_writes": "prohibited",
                    "calendar_writes": "prohibited",
                    "content_exposed": False,
                },
            )
        )
        configured_chat_members = len(self.settings.member_chat_enabled_members)
        if configured_chat_members == 0:
            chat_state: CapabilityState = "disabled"
            chat_reason = "member chat is disabled by policy"
        elif not self.settings.openclaw_model_configured:
            chat_state = "blocked"
            chat_reason = "OpenClaw model provider is not configured"
        elif self.member_chat_last_failure is not None:
            chat_state = "degraded"
            chat_reason = (
                f"OpenClaw chat method failed: {self.member_chat_last_failure['code']}"
            )
        elif self.settings.deterministic_member_chat_fixture:
            chat_state = "healthy"
            chat_reason = None
        elif (
            not self.settings.openclaw_gateway_token
            or effective_openclaw_state != "healthy"
        ):
            chat_state = "degraded"
            chat_reason = "OpenClaw chat transport is unavailable or degraded"
        else:
            chat_state = "healthy"
            chat_reason = None
        self.capabilities["surface:member-portal"] = CapabilityRecord(
            name="member-portal",
            kind="member_surface",
            state="healthy",
            details={"server_authorized": True, "operator_access_required": False},
        )
        self.capabilities["openclaw:member-chat"] = CapabilityRecord(
            name="member-chat",
            kind="openclaw_chat_transport",
            state=chat_state,
            reason=chat_reason,
            details={
                "configured_members": configured_chat_members,
                "child_chat_members": len(self.settings.child_chat_enabled_members),
                "model_provider_configured": self.settings.openclaw_model_configured,
                "transcripts_owned_by": "openclaw",
                "provider_credentials_exposed": False,
                "last_method_failure": self.member_chat_last_failure,
            },
        )

    def _source_assurance_capability_snapshot(self) -> dict[str, Any]:
        """Freeze the relevant public state for one asynchronous audit."""

        return {
            key: record.public()
            for key, record in self.capabilities.items()
            if key.startswith(("pipeline:", "projection:"))
        }

    async def _run_source_assurance_refresh(
        self,
        capabilities: dict[str, Any],
        revision: tuple[Any, ...],
        *,
        initial: bool = False,
    ) -> None:
        assert self.event_log is not None
        self._source_assurance_last_attempt_monotonic = time.monotonic()
        try:
            result = await audit_source_assurance(
                self.event_log,
                repository_root=self.settings.repository_root,
                capabilities=capabilities,
                through_event_id=revision[0],
                provider_effect_window_started_at=(
                    self.settings.deployment_evidence.get("provider_effect_window")
                    or {}
                ).get("started_at"),
                provider_effect_window_ended_at=(
                    self.settings.deployment_evidence.get("provider_effect_window")
                    or {}
                ).get("ended_at"),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._source_assurance_refresh_error = type(exc).__name__
            if initial:
                raise
        else:
            self._source_assurance_cache = result
            self._source_assurance_refreshed_at = EventEnvelope.now_utc_iso()
            self._source_assurance_refreshed_monotonic = time.monotonic()
            self._source_assurance_revision = revision
            self._source_assurance_refresh_error = None
        finally:
            if (
                not initial
                and self._source_assurance_refresh_task is asyncio.current_task()
            ):
                self._source_assurance_refresh_task = None

    async def _source_assurance_status(self) -> dict[str, Any]:
        """Serve bounded cached proof while one background refresh catches up.

        The first audit remains a startup invariant. Later event appends and
        checkpoint advances never make readiness decrypt and scan the entire
        event log synchronously. A fixed-watermark proof with an unchanged
        append-only head, unchanged checkpoints, and zero pending obligations
        is still current and is not rescanned merely because time passed.
        Changed revisions refresh immediately; pending work is rechecked on a
        timer. Failure or an over-age unsettled proof degrades health.
        """

        capabilities = self._source_assurance_capability_snapshot()
        assert self.event_log is not None
        latest_event_id = await self.event_log.latest_event_id()
        capability_revision = tuple(
            sorted(
                (
                    key,
                    capability.get("state"),
                    (capability.get("details") or {}).get("checkpoint_event_id"),
                    (capability.get("details") or {}).get("consecutive_failures"),
                    (capability.get("details") or {}).get("poison_event_id"),
                )
                for key, capability in capabilities.items()
            )
        )
        revision = (latest_event_id, capability_revision)
        if self._source_assurance_cache is None:
            await self._run_source_assurance_refresh(
                capabilities, revision, initial=True
            )

        assert self._source_assurance_cache is not None
        age_seconds = max(
            0.0, time.monotonic() - self._source_assurance_refreshed_monotonic
        )
        retry_age_seconds = max(
            0.0, time.monotonic() - self._source_assurance_last_attempt_monotonic
        )
        revision_current = revision == self._source_assurance_revision
        pending_count = int(
            (self._source_assurance_cache.get("counts") or {}).get("pending") or 0
        )
        pending_recheck_due = (
            pending_count > 0 and age_seconds >= self.SOURCE_ASSURANCE_REFRESH_SECONDS
        )
        refresh_due = not revision_current or pending_recheck_due
        retry_allowed = (
            self._source_assurance_refresh_error is None
            or retry_age_seconds >= self.SOURCE_ASSURANCE_REFRESH_SECONDS
        )
        if (
            refresh_due
            and retry_allowed
            and self._source_assurance_refresh_task is None
        ):
            self._source_assurance_refresh_task = asyncio.create_task(
                self._run_source_assurance_refresh(capabilities, revision),
                name="source-assurance-refresh",
            )

        refresh_in_progress = self._source_assurance_refresh_task is not None
        time_sensitive_proof = not revision_current or pending_count > 0
        refresh_state = (
            "failed"
            if self._source_assurance_refresh_error
            else "stale"
            if time_sensitive_proof
            and age_seconds > self.SOURCE_ASSURANCE_MAX_STALE_SECONDS
            else "fresh"
        )
        result = dict(self._source_assurance_cache)
        result["freshness"] = {
            "state": refresh_state,
            "refreshed_at": self._source_assurance_refreshed_at,
            "stale_seconds": round(age_seconds, 3),
            "refresh_interval_seconds": self.SOURCE_ASSURANCE_REFRESH_SECONDS,
            "max_stale_seconds": self.SOURCE_ASSURANCE_MAX_STALE_SECONDS,
            "refresh_in_progress": refresh_in_progress,
            "revision_current": revision_current,
            "pending_recheck_required": pending_count > 0,
            "settled_revision": revision_current and pending_count == 0,
            "refresh_error_type": self._source_assurance_refresh_error,
        }
        if refresh_state in {"failed", "stale"}:
            result["state"] = "degraded"
        return result

    def overall_state(
        self,
    ) -> Literal["healthy", "observation_only", "degraded", "blocked"]:
        blocking_states = {
            key: record.state
            for key, record in self.capabilities.items()
            if key != "openclaw:member-chat"
        }
        states = set(blocking_states.values())
        if "blocked" in states:
            return "blocked"
        if "degraded" in states:
            return "degraded"
        if self._observation_active:
            return "observation_only"
        return "healthy"

    def product_status(self, product: str) -> dict[str, Any]:
        record = self.capabilities.get(f"product:{product}")
        if record is None:
            return {"name": product, "state": "blocked", "reason": "not registered"}
        return record.public()

    def validate_google_config(self, config: dict[str, Any]) -> RuntimeSettings:
        candidate = replace(self.settings, adapter_config=config)
        # Construct reviewed adapters before publication, without starting or
        # contacting them. The selected instances alone are restarted below.
        list(
            load_builtin_adapters(
                self.settings_control.effective_adapter_config(config),
                repository_root=candidate.repository_root,
                member_roles=candidate.member_roles,
                household_mapping=candidate.household_mapping,
                service_principal_ids={
                    row.service_principal_id for row in candidate.service_principals
                },
                service_principal_provider_authorizations={},
                validate_bindings=candidate.deployment_evidence_required,
            )
        )
        return candidate

    async def apply_google_config(
        self, config: dict[str, Any], evidence: dict[str, Any], affected: set[str]
    ) -> None:
        # Publish permission/binding caches before the first new adapter event.
        # A provider outage cannot restore a revoked permission or old grant.
        self.settings = replace(self.settings, adapter_config=config)
        self.settings_control.rebind_google_config(config)
        self.bridge_enrollment.adapter_config = config
        if self.source_policy_shadow is not None:
            self.source_policy_shadow.rebind_provider_bindings(
                self.settings.provider_bindings
            )
        if self.reconciliation_admission is not None:
            self.reconciliation_admission.rebind_provider_bindings(
                self.settings.provider_bindings
            )
        self._bridge_index_committed(evidence)
        self._source_assurance_cache = None
        if self.adapters is None:
            raise RuntimeError("adapter supervisor is unavailable")
        try:
            await self.adapters.reconcile(
                self.settings_control.effective_adapter_config(config),
                affected_instance_ids=affected,
            )
        except Exception:
            await self.adapters.stop_instances(affected)
            raise

    def _bridge_index_committed(self, evidence: dict[str, Any]) -> None:
        # This callback runs synchronously only after both durable publications.
        self.settings.deployment_evidence.clear()
        self.settings.deployment_evidence.update(evidence)
        self.settings.host_topology["deployment_binding"] = dict(
            evidence["deployment_binding"]
        )

    def _deployment_readiness(self, *, observation_active: bool) -> dict[str, Any]:
        reported_topology = dict(self.openclaw_topology_report)
        if (
            self.settings.deployment_evidence_required
            and self.settings.openclaw_enabled
        ):
            current_topology = inspect_runtime_topology(
                self.settings.openclaw_runtime_config_path,
                actor_identities=self.settings.openclaw_actor_identities,
                member_chat_enabled_members=self.settings.member_chat_enabled_members,
            )
            if current_topology.get("state") != "healthy":
                reported_topology.update(
                    {
                        "member_ids_with_agents": [],
                        "member_ids_with_account_bindings": [],
                        "unique_agents": False,
                        "unique_accounts": False,
                        "runtime_config_state": current_topology.get("state"),
                        "runtime_config_reason": current_topology.get("reason"),
                    }
                )
        source_assurance_capability = self.capabilities.get("policy:source-assurance")
        current_source_assurance = (
            source_assurance_capability.details
            if source_assurance_capability is not None
            else self._source_assurance_cache
        )
        host_topology = self.settings.host_topology
        committer = self.settings.bridge_index_committer
        google_committer = self.settings.google_config_committer
        if (
            (google_committer is not None and google_committer.pending)
            or self.bridge_enrollment.index_commit_failed
            or (committer is not None and committer.pending)
        ):
            host_topology = {**host_topology, "deployment_binding": {}}
        google_source_ids = selected_google_entries(self.settings.adapter_config)
        return evaluate_deployment_readiness(
            required=self.settings.deployment_evidence_required,
            host_topology=host_topology,
            deployment_evidence=self.settings.deployment_evidence,
            member_roles=self.settings.member_roles,
            portal_member_ids=set(self.settings.member_identities.values()),
            portal_identity_count=len(self.settings.member_identities),
            provider_bindings=self.settings.provider_bindings,
            service_channel_accounts=self.settings.service_channel_accounts,
            bridge_enrollments=self.settings.bridge_enrollments,
            openclaw_enabled=self.settings.openclaw_enabled,
            openclaw_member_ids=set(self.settings.member_chat_enabled_members)
            | set(self.settings.openclaw_actor_identities.values()),
            openclaw_topology=reported_topology,
            observation_active=observation_active,
            source_assurance=current_source_assurance,
            disposable_test_dataset=self.settings.test_dataset_marker,
            cos_live_authorization=self._cos_live_authorization,
            cos_acquisition_configured="cos_channel_acquisition"
            in self.settings.adapter_config,
            member_google_source_count=sum(
                adapter_id in google_source_ids
                for adapter_id in (self.adapters.inventory() if self.adapters else {})
            ),
        )

    async def diagnostics(self, *, refresh_capabilities: bool = True) -> dict[str, Any]:
        """Read diagnostics, optionally reusing the caller's completed refresh."""
        if self.started and refresh_capabilities:
            await self._refresh_capability_registry()
        observation_active = (
            await self.observation.is_active() if self.observation is not None else True
        )
        state = self.overall_state()
        if observation_active and state == "healthy":
            state = "observation_only"
        event_ingest_ready = bool(self.events and self.events.accepting)
        service_ready = self.started and state != "blocked"
        safe_reads = self.started and self.projections is not None
        runtime_release_ready = (
            service_ready
            and event_ingest_ready
            and state in {"healthy", "observation_only"}
        )
        deployment_readiness = self._deployment_readiness(
            observation_active=observation_active
        )
        release_ready = runtime_release_ready and deployment_readiness["ready"]
        if (
            not event_ingest_ready
            or not self.started
            or (
                self.settings.deployment_evidence_required
                and not deployment_readiness["ready"]
            )
        ):
            write_admission = "blocked"
        elif observation_active:
            write_admission = "observation_suppressed"
        elif state in {"degraded", "blocked"}:
            write_admission = "blocked"
        else:
            write_admission = "governed_per_request"
        openclaw_age = (
            None
            if self.openclaw_last_report_monotonic is None
            else max(0.0, time.monotonic() - self.openclaw_last_report_monotonic)
        )
        openclaw_fresh = (
            openclaw_age is not None
            and openclaw_age <= self.OPENCLAW_HEARTBEAT_MAX_STALE_SECONDS
        )
        b03_row: dict[str, Any] | None = None
        if self.projections is not None:
            raw = (
                self.projections.connection("tasks")
                .execute(
                    "SELECT * FROM cos_google_tasks_execution_v2 WHERE tenant_id=? "
                    "ORDER BY valid_from DESC, command_id LIMIT 1",
                    (self.settings.instance.tenant_id,),
                )
                .fetchone()
            )
            b03_row = dict(raw) if raw is not None else None
        b03_status = b03_public_envelope_state(b03_row)
        required_b03 = b03_row is not None
        b03_binding_count = 0
        delegation_current = False
        agreement_current = False
        envelope_current = False
        single_use_available = False
        if b03_row is not None:
            expected_binding_coordinate = (
                str(b03_row["resource_owner"]),
                str(b03_row["provider_owner"]),
                str(b03_row["represented_cos"]),
                str(b03_row["context_scope"]),
                str(b03_row["on_behalf_of"]),
                str(b03_row["provider"]),
                str(b03_row["provider_account"]).casefold(),
                str(b03_row["adapter_identity"]),
                str(b03_row["destination_task_list"]),
                B03_OPERATION,
            )
            b03_binding_count = sum(
                binding.b03_dispatch_coordinate == expected_binding_coordinate
                for binding in self.settings.provider_bindings
            )
            current = datetime.now(timezone.utc)
            envelope_current = bool(
                b03_row.get("revoked_at") is None
                and datetime.fromisoformat(
                    str(b03_row["valid_from"]).replace("Z", "+00:00")
                )
                <= current
                < datetime.fromisoformat(
                    str(b03_row["expires_at"]).replace("Z", "+00:00")
                )
            )
            single_use_available = b03_row.get("used_at") is None
            if self.projections is not None:
                parties = self.projections.connection("parties")
                grant = parties.execute(
                    "SELECT * FROM service_delegation_grants "
                    "WHERE tenant_id=? AND grant_id=?",
                    (
                        self.settings.instance.tenant_id,
                        b03_row["authority_grant_id"],
                    ),
                ).fetchone()
                if grant is not None:
                    approvals = {
                        str(row["approved_by_member_id"])
                        for row in parties.execute(
                            "SELECT approved_by_member_id FROM service_delegation_approvals "
                            "WHERE tenant_id=? AND grant_id=?",
                            (
                                self.settings.instance.tenant_id,
                                b03_row["authority_grant_id"],
                            ),
                        ).fetchall()
                    }
                    required_approvals = set(
                        json.loads(str(grant["required_approvals_json"]))
                    )
                    permissions = set(json.loads(str(grant["permissions_json"])))
                    grant_expiry = (
                        datetime.fromisoformat(
                            str(grant["expires_at"]).replace("Z", "+00:00")
                        )
                        if grant["expires_at"]
                        else None
                    )
                    delegation_current = bool(
                        grant["revoked_at"] is None
                        and (grant_expiry is None or grant_expiry > current)
                        and required_approvals == approvals
                        and B03_DELEGATION_PERMISSION in permissions
                        and grant["context_scope"] == b03_row["context_scope"]
                        and grant["resource_owner_principal_id"]
                        == b03_row["resource_owner"]
                    )
                agreement = parties.execute(
                    "SELECT * FROM operating_agreements "
                    "WHERE tenant_id=? AND agreement_id=? "
                    "ORDER BY revision DESC LIMIT 1",
                    (
                        self.settings.instance.tenant_id,
                        b03_row["operating_agreement_id"],
                    ),
                ).fetchone()
                if agreement is not None:
                    effective_from = (
                        datetime.fromisoformat(
                            str(agreement["effective_from"]).replace("Z", "+00:00")
                        )
                        if agreement["effective_from"]
                        else None
                    )
                    agreement_expiry = (
                        datetime.fromisoformat(
                            str(agreement["expires_at"]).replace("Z", "+00:00")
                        )
                        if agreement["expires_at"]
                        else None
                    )
                    rule = json.loads(str(agreement["rule_json"]))
                    trust = rule.get("trust_scope") or {}
                    ceilings = rule.get("ceilings") or {}
                    agreement_current = bool(
                        agreement["state"] == "active"
                        and (effective_from is None or effective_from <= current)
                        and (agreement_expiry is None or agreement_expiry > current)
                        and rule.get("represented_service_principal_id")
                        == b03_row["represented_cos"]
                        and b03_row["provider_account"]
                        in set(rule.get("provider_account_references") or [])
                        and b03_row["destination_task_list"]
                        in set(rule.get("destination_ids") or [])
                        and "google_tasks_create"
                        in set(trust.get("capability_ids") or [])
                        and B03_OPERATION in set(trust.get("action_verbs") or [])
                        and rule.get("independent_openclaw_approval_required") is True
                        and ceilings.get("maximum_frequency_count") == 1
                    )
        else:
            b03_binding_count = sum(
                binding.write_binding_state == "trial"
                for binding in self.settings.provider_bindings
            )
        b03_prerequisites = {
            "exact_binding": b03_binding_count == 1,
            "exact_coordinate": bool(
                b03_row
                and b03_row.get("execution_policy_version") == B03_POLICY_VERSION
                and b03_row.get("action_class") == "google_tasks_create"
                and b03_row.get("represented_cos") == COS_SERVICE_PRINCIPAL_ID
                and b03_row.get("actual_operator") == COS_AUTOMATION_OPERATOR_ID
                and b03_row.get("provider") == B03_PROVIDER
                and b03_row.get("adapter_identity") == B03_ADAPTER_IDENTITY
                and int(b03_row.get("maximum_effect_count") or 0) == 1
            ),
            "current_delegation": delegation_current,
            "effective_operating_agreement": agreement_current,
            "envelope_current": envelope_current,
            "single_use_available": single_use_available,
            "global_observation_active": observation_active,
            "domain_approval": bool(
                b03_row and b03_row.get("domain_approval_event_id")
            ),
            "openclaw_approval": bool(
                b03_row and b03_row.get("openclaw_approval_event_id")
            ),
            "openclaw_runtime": self.effective_openclaw_state() == "healthy",
            "idempotency": bool(b03_row and b03_row.get("idempotency_key")),
            "terminal_path": self.events is not None,
            "observation_path": self.google_tasks_execution_reconciler is not None,
            "reconciliation_path": self.google_tasks_execution_reconciler is not None,
        }
        b03_status["doctor"] = {
            "state": (
                "not_configured"
                if not required_b03
                else "ready"
                if all(b03_prerequisites.values())
                else "degraded"
            ),
            "prerequisites": b03_prerequisites,
        }
        return {
            "status": state,
            # ``ready`` remains the backward-compatible service/readiness bit.
            # Explicit fields prevent a degraded HTTP 200 from being mistaken
            # for release or activation approval.
            "ready": (
                release_ready
                if self.settings.deployment_evidence_required
                else service_ready
            ),
            "service_ready": service_ready,
            "release_ready": release_ready,
            "activation_eligible": release_ready,
            "safe_reads": safe_reads,
            "write_admission": write_admission,
            "accepting_writes": event_ingest_ready,
            "event_ingest_ready": event_ingest_ready,
            "observation_active": observation_active,
            "deployment_readiness": deployment_readiness,
            "openclaw_health": {
                "state": self.effective_openclaw_state(),
                "reported_state": self.openclaw_state,
                "fresh": openclaw_fresh,
                "last_reported_at": self.openclaw_last_reported_at,
                "stale_seconds": None
                if openclaw_age is None
                else round(openclaw_age, 3),
                "max_stale_seconds": self.OPENCLAW_HEARTBEAT_MAX_STALE_SECONDS,
            },
            "direct_channel_readiness": public_direct_channel_readiness(
                self.settings.service_channel_accounts
            ),
            "service_source_readiness": public_service_source_readiness(
                self.settings.provider_bindings,
                adapter_inventory=(
                    self.adapters.inventory() if self.adapters is not None else {}
                ),
            ),
            "google_tasks_create_trial": b03_status,
            "member_bridge_count": len(self.settings.bridge_enrollments),
            "messages_read_enrollment": "not_enrolled"
            if not self.settings.bridge_enrollments
            else "configuration_present",
            "messages_reply": "disabled"
            if not self.settings.bridge_enrollments
            else "per_member_controlled",
            "capabilities": {
                key: value.public() for key, value in sorted(self.capabilities.items())
            },
            "startup_error": self.startup_error,
            "member_portal": {
                "health": "healthy" if self.started else "stopped",
                "domain_views": "healthy"
                if self.projections is not None
                else "degraded",
                "chat_transport": self.capabilities.get(
                    "openclaw:member-chat",
                    CapabilityRecord(
                        "member-chat", "openclaw_chat_transport", "disabled"
                    ),
                ).public(),
                "model_provider_configured": self.settings.openclaw_model_configured,
            },
        }


system_router = APIRouter(prefix="/api/system", tags=["system"])
SystemSessionDep = Annotated[Session, Depends(session_from_request)]


@system_router.get("/live")
async def live(request: Request) -> dict[str, Any]:
    runtime: AdministrateMeRuntime = request.app.state.runtime
    return {"status": "alive", "runtime_started": runtime.started}


@system_router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    runtime: AdministrateMeRuntime = request.app.state.runtime
    payload = await runtime.diagnostics()
    payload = {
        **payload,
        "capabilities": public_capability_summary(payload.get("capabilities") or {}),
    }
    return JSONResponse(payload, status_code=200 if payload["ready"] else 503)


@system_router.get("/historical-attribution")
async def historical_attribution(
    request: Request,
    session: SystemSessionDep,
) -> dict[str, Any]:
    """Return HMAC-pseudonymized attribution counts; never provider content."""

    require_principal(session)
    runtime: AdministrateMeRuntime = request.app.state.runtime
    if runtime.event_log is None:
        raise HTTPException(503, "historical attribution is unavailable")
    hash_key = hmac.new(
        runtime.settings.encryption_key,
        b"administrateme-historical-attribution-v1",
        hashlib.sha256,
    ).digest()
    try:
        historical_bindings = validate_historical_provider_bindings(
            runtime.settings.adapter_config,
            member_roles=runtime.settings.member_roles,
            household_mapping=runtime.settings.household_mapping,
            service_principal_ids={
                principal.service_principal_id
                for principal in runtime.settings.service_principals
            },
        )
    except ValueError as exc:
        raise HTTPException(503, "historical ownership evidence is invalid") from exc
    return await audit_historical_attribution(
        runtime.event_log,
        repository_root=runtime.settings.repository_root,
        provider_bindings=historical_bindings,
        hash_key=hash_key,
    )


@system_router.post("/source-assessment-recoveries", status_code=202)
async def request_source_assessment_recoveries(
    body: SourceAssessmentRecoveryBatch,
    request: Request,
    session: SystemSessionDep,
) -> dict[str, Any]:
    """Append exact, principal-authorized recovery requests; never source text."""

    require_principal(session)
    runtime: AdministrateMeRuntime = request.app.state.runtime
    if runtime.event_log is None or runtime.events is None:
        raise HTTPException(503, "source recovery is unavailable")
    if runtime.observation is None or not await runtime.observation.is_active():
        raise HTTPException(409, "source recovery requires observation mode")
    required_owner_scope = f"private:{session.view_member_id}"
    fingerprint = recovery_batch_fingerprint(body.request_id, body.targets)
    prior = [
        await runtime.event_log.get_by_idempotency(
            session.tenant_id,
            f"source-assessment-recovery:{body.request_id}:{target.source_event_id}",
        )
        for target in body.targets
    ]
    if any(event is not None for event in prior):
        if not all(
            event is not None
            and (event.get("payload") or {}).get("batch_fingerprint") == fingerprint
            and event.get("owner_scope") == required_owner_scope
            for event in prior
        ):
            raise HTTPException(409, "recovery request_id conflicts with prior use")
        return {
            "contract": "source-assessment-recovery-v1",
            "request_id": body.request_id,
            "accepted": len(prior),
            "request_event_ids": [str(event["event_id"]) for event in prior if event],
            "duplicate": True,
            "content_exposed": False,
            "canonical_objects_created": 0,
            "external_write_requests": 0,
        }
    try:
        envelopes = await build_recovery_request_envelopes(
            runtime.event_log,
            body,
            tenant_id=session.tenant_id,
            requested_by_actor=session.auth_member_id,
            authority="member_principal",
            required_owner_scope=required_owner_scope,
        )
    except SourceAssessmentRecoveryRefused as exc:
        raise HTTPException(409, exc.code) from exc
    event_ids = await runtime.events.write_batch(
        envelopes,
        correlation_id=session.correlation_id,
    )
    return {
        "contract": "source-assessment-recovery-v1",
        "request_id": body.request_id,
        "accepted": len(event_ids),
        "request_event_ids": event_ids,
        "duplicate": False,
        "content_exposed": False,
        "canonical_objects_created": 0,
        "external_write_requests": 0,
    }


def _revision_policy(runtime: AdministrateMeRuntime, session):
    from adminme.source_assessment_revision import RevisionRefused

    policy = policy_from_adapter_config(runtime.settings.adapter_config)
    if (
        policy is None
        or not runtime.settings.test_dataset_marker
        or policy.member_id != session.view_member_id
        or session.auth_member_id != session.view_member_id
    ):
        raise RevisionRefused("owned_disposable_test_dataset_required")
    return policy


@system_router.post("/source-assessment-revisions/preview")
async def preview_source_assessment_revision(
    body: RevisionPreview,
    request: Request,
    session: SystemSessionDep,
) -> dict[str, Any]:
    """Read the exact retained episode manifest without invoking a model."""
    require_principal(session)
    runtime = request.app.state.runtime
    if runtime.event_log is None:
        raise HTTPException(503, "source revision is unavailable")
    try:
        policy = _revision_policy(runtime, session)
        targets = [
            await inspect_revision_target(
                runtime.event_log,
                source_id,
                tenant_id=session.tenant_id,
                owner_scope=f"private:{session.view_member_id}",
                policy=policy,
            )
            for source_id in body.source_event_ids
        ]
        manifest = RevisionManifest(
            dataset_id=runtime.settings.test_dataset_marker["dataset_id"],
            targets=targets,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "manifest": manifest.model_dump(mode="json"),
        "manifest_sha256": manifest.fingerprint(),
        "max_classifier_attempts": len(targets) * 3,
        "source_acquisition": False,
        "external_write_requests": 0,
    }


@system_router.post("/source-assessment-revisions", status_code=202)
async def request_source_assessment_revision(
    body: RevisionManifest,
    request: Request,
    session: SystemSessionDep,
) -> dict[str, Any]:
    """Accept one immutable manifest; changed or repeated IDs cannot renew it."""
    require_principal(session)
    runtime = request.app.state.runtime
    if runtime.event_log is None or runtime.events is None:
        raise HTTPException(503, "source revision is unavailable")
    if runtime.observation is None or not await runtime.observation.is_active():
        raise HTTPException(409, "source revision requires observation mode")
    owner_scope = f"private:{session.view_member_id}"
    try:
        policy = _revision_policy(runtime, session)
        if body.dataset_id != runtime.settings.test_dataset_marker["dataset_id"]:
            raise RevisionRefused("disposable_dataset_changed")
        prior = await runtime.event_log.get_by_idempotency(
            session.tenant_id, revision_request_key(owner_scope)
        )
        if prior is None:
            for target in body.targets:
                await validate_revision_target(
                    runtime.event_log,
                    target,
                    tenant_id=session.tenant_id,
                    owner_scope=owner_scope,
                    policy=policy,
                )
            envelope = revision_request_envelope(
                body,
                tenant_id=session.tenant_id,
                owner_scope=owner_scope,
                actor=session.auth_member_id,
            )
            revision_execution_policy(
                envelope.model_dump(mode="json"),
                adapter_config=runtime.settings.adapter_config,
                dataset_marker=runtime.settings.test_dataset_marker,
                repository_root=runtime.settings.repository_root,
            )
            try:
                event_id = await runtime.events.write(
                    envelope, correlation_id=session.correlation_id
                )
                prior = await runtime.event_log.get(event_id)
            except IdempotencyConflict:
                prior = await runtime.event_log.get_by_idempotency(
                    session.tenant_id, revision_request_key(owner_scope)
                )
        if prior is None or prior["payload"]["manifest_sha256"] != body.fingerprint():
            raise RevisionRefused("reviewed_revision_manifest_already_spent")
        return await revision_command_status(runtime.event_log, prior)
    except RevisionRefused as exc:
        raise HTTPException(409, str(exc)) from exc


@system_router.get("/source-assessment-revisions/{request_event_id}")
async def source_assessment_revision_status(
    request_event_id: str,
    request: Request,
    session: SystemSessionDep,
) -> dict[str, Any]:
    require_principal(session)
    runtime = request.app.state.runtime
    if runtime.event_log is None:
        raise HTTPException(503, "source revision is unavailable")
    command = await runtime.event_log.get(request_event_id)
    if (
        command is None
        or command.get("type") != "source.assessment.revision_requested"
        or command.get("tenant_id") != session.tenant_id
        or command.get("owner_scope") != f"private:{session.view_member_id}"
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(404, "source revision not found")
    return await revision_command_status(runtime.event_log, command)


@system_router.post("/source-currentness-revisions/preview")
async def preview_source_currentness_revision(
    body: currentness_revision.CurrentnessPreview,
    request: Request,
    session: SystemSessionDep,
) -> dict[str, Any]:
    """Read the exact retained episode manifest without invoking a model."""
    require_principal(session)
    runtime = request.app.state.runtime
    if runtime.event_log is None:
        raise HTTPException(503, "source revision is unavailable")
    try:
        policy = _revision_policy(runtime, session)
        targets = [
            await inspect_revision_target(
                runtime.event_log,
                source_id,
                tenant_id=session.tenant_id,
                owner_scope=f"private:{session.view_member_id}",
                policy=policy,
            )
            for source_id in body.source_event_ids
        ]
        manifest = currentness_revision.CurrentnessManifest(
            dataset_id=runtime.settings.test_dataset_marker["dataset_id"],
            targets=targets,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "manifest": manifest.model_dump(mode="json"),
        "manifest_sha256": manifest.fingerprint(),
        "max_classifier_attempts": len(targets) * 3,
        "source_acquisition": False,
        "external_write_requests": 0,
    }


@system_router.post("/source-currentness-revisions", status_code=202)
async def request_source_currentness_revision(
    body: currentness_revision.CurrentnessManifest,
    request: Request,
    session: SystemSessionDep,
) -> dict[str, Any]:
    """Accept one immutable manifest; changed or repeated IDs cannot renew it."""
    require_principal(session)
    runtime = request.app.state.runtime
    if runtime.event_log is None or runtime.events is None:
        raise HTTPException(503, "source revision is unavailable")
    if runtime.observation is None or not await runtime.observation.is_active():
        raise HTTPException(409, "source revision requires observation mode")
    owner_scope = f"private:{session.view_member_id}"
    try:
        policy = _revision_policy(runtime, session)
        if body.dataset_id != runtime.settings.test_dataset_marker["dataset_id"]:
            raise RevisionRefused("disposable_dataset_changed")
        prior = await runtime.event_log.get_by_idempotency(
            session.tenant_id, currentness_revision.request_key(owner_scope)
        )
        if prior is None:
            for target in body.targets:
                await validate_revision_target(
                    runtime.event_log,
                    target,
                    tenant_id=session.tenant_id,
                    owner_scope=owner_scope,
                    policy=policy,
                )
            envelope = currentness_revision.request_envelope(
                body,
                tenant_id=session.tenant_id,
                owner_scope=owner_scope,
                actor=session.auth_member_id,
            )
            currentness_revision.execution_policy(
                envelope.model_dump(mode="json"),
                adapter_config=runtime.settings.adapter_config,
                dataset_marker=runtime.settings.test_dataset_marker,
                repository_root=runtime.settings.repository_root,
            )
            try:
                event_id = await runtime.events.write(
                    envelope, correlation_id=session.correlation_id
                )
                prior = await runtime.event_log.get(event_id)
            except IdempotencyConflict:
                prior = await runtime.event_log.get_by_idempotency(
                    session.tenant_id, currentness_revision.request_key(owner_scope)
                )
        if prior is None or prior["payload"]["manifest_sha256"] != body.fingerprint():
            raise RevisionRefused("reviewed_revision_manifest_already_spent")
        return await currentness_revision.command_status(runtime.event_log, prior)
    except RevisionRefused as exc:
        raise HTTPException(409, str(exc)) from exc


@system_router.get("/source-currentness-revisions/{request_event_id}")
async def source_currentness_revision_status(
    request_event_id: str,
    request: Request,
    session: SystemSessionDep,
) -> dict[str, Any]:
    require_principal(session)
    runtime = request.app.state.runtime
    if runtime.event_log is None:
        raise HTTPException(503, "source revision is unavailable")
    command = await runtime.event_log.get(request_event_id)
    if (
        command is None
        or command.get("type") != currentness_revision.REQUEST_TYPE
        or command.get("tenant_id") != session.tenant_id
        or command.get("owner_scope") != f"private:{session.view_member_id}"
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(404, "source revision not found")
    return await currentness_revision.command_status(runtime.event_log, command)


def create_application(settings: RuntimeSettings) -> FastAPI:
    runtime = AdministrateMeRuntime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await runtime.start()
        app.state.event_log = runtime.event_log
        app.state.event_sink = runtime.events
        app.state.member_id_resolver = runtime.member_id_resolver
        app.state.bridge_enrollments = settings.bridge_enrollments
        app.state.ingest_count = 0
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(
        title="AdministrateMe",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.include_router(core_router)
    app.include_router(comms_router)
    app.include_router(capture_router)
    app.include_router(automation_router)
    app.include_router(ingest_router)
    app.include_router(openclaw_router)
    from adminme.products.openclaw.cos_self_replies import router as self_reply_router

    app.include_router(cos_response_router)
    from adminme.products.openclaw.cos_reminders import router as reminder_router

    app.include_router(self_reply_router)
    app.include_router(reminder_router)
    from adminme.products.openclaw.service_work import router as service_work_router

    app.include_router(service_work_router)
    from adminme.products.openclaw.apple_reminders import (
        router as apple_reminder_router,
    )

    app.include_router(apple_reminder_router)
    from adminme.products.openclaw.reminder_choice_tools import (
        router as reminder_choice_router,
    )

    app.include_router(reminder_choice_router)
    from adminme.products.openclaw.reminder_mode_tools import (
        router as reminder_mode_router,
    )

    app.include_router(reminder_mode_router)
    from adminme.products.openclaw.cos_apple_reminders import router as cos_apple_router

    app.include_router(cos_apple_router)
    from adminme.products.openclaw.requested_email import (
        router as requested_email_router,
    )

    app.include_router(requested_email_router)
    from adminme.products.openclaw.workstation import router as workstation_router

    app.include_router(workstation_router)

    from adminme.products.openclaw.workstation_work import (
        router as workstation_work_router,
    )

    app.include_router(workstation_work_router)

    from adminme.products.openclaw.workstation_browser import (
        router as workstation_browser_router,
    )

    app.include_router(workstation_browser_router)

    from adminme.products.openclaw.workstation_source_policy import (
        router as workstation_source_policy_router,
    )

    app.include_router(workstation_source_policy_router)
    app.include_router(member_router)
    app.include_router(member_bridge_setup_router)
    app.include_router(member_cos_router)
    from adminme.products.member.google_connections import (
        router as google_connections_router,
    )

    app.include_router(google_connections_router)
    app.include_router(apple_service_settings_router)
    app.include_router(apple_notes_commands_router)
    app.include_router(system_router)
    app.include_router(finance_metadata_router)
    return app


def app_from_environment() -> FastAPI:
    return create_application(RuntimeSettings.from_environment())


__all__ = [
    "AdministrateMeRuntime",
    "CapabilityRecord",
    "RuntimeSettings",
    "app_from_environment",
    "create_application",
]
