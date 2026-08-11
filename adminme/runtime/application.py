"""Production composition root for the single AdministrateMe v2 process."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import yaml
import sqlcipher3
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from adminme.adapters.base import AdapterContext
from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.adapters.fixture_messaging import FixtureMessagingAdapter
from adminme.adapters.loader import load_builtin_adapters
from adminme.daemons.adapter_supervisor import AdapterSupervisor
from adminme.daemons.xlsx_sync.reverse import XlsxReverseDaemon
from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog, new_correlation_id
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
    reset_outbound_audit_context,
    set_outbound_audit_context,
)
from adminme.lib.adapters import OutboundMessage, WriteOperation
from adminme.lib.session import build_internal_session
from adminme.lib.skill_runner import build_skill_runtime, set_default_event_sink
from adminme.lib.skill_runner.fixture import build_fixture_runtime
from adminme.lib.skill_runner.wrapper import run_skill
from adminme.member_chat import (
    DeterministicOpenClawChatTransport,
    MemberChatTransport,
    OpenClawGatewayChatTransport,
    UnavailableMemberChatTransport,
)
from adminme.openclaw_approvals import OpenClawApprovalStore
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
from adminme.products.openclaw.main import router as openclaw_router
from adminme.projections.base import Projection
from adminme.projections.runner import DEFAULT_PROJECTION_CLASSES, ProjectionRunner
from adminme.projections.xlsx_workbooks import XlsxWorkbooksProjection
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.settings_control import SettingsControlStore
from adminme.source_assurance import audit_source_assurance
from adminme.temporary_surfaces import TemporarySurfaceStore

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
    adapter_config: dict[str, Any] = field(
        default_factory=lambda: {"adapter_supervisor_port": 0, "adapters": []}
    )
    openclaw_enabled: bool = False
    openclaw_shared_secret: str | None = field(default=None, repr=False)
    openclaw_actor_identities: dict[str, str] = field(default_factory=dict)
    openclaw_gateway_url: str = "ws://127.0.0.1:18789"
    openclaw_gateway_token: str | None = field(default=None, repr=False)
    openclaw_model_configured: bool = False
    member_chat_enabled_members: frozenset[str] = field(default_factory=frozenset)
    child_chat_enabled_members: frozenset[str] = field(default_factory=frozenset)
    deterministic_member_chat_fixture: bool = False
    deterministic_openclaw_fixture: bool = False
    member_roles: dict[str, str] = field(default_factory=dict)
    fixture_member_id: str | None = None

    def __post_init__(self) -> None:
        if len(self.encryption_key) != 32:
            raise ValueError("runtime encryption_key must be exactly 32 bytes")
        root = self.repository_root.resolve()
        if not (root / "packs" / "pipelines").is_dir():
            raise ValueError(f"pipeline pack root missing under {root}")
        adapters = self.adapter_config.get("adapters", [])
        if not isinstance(adapters, list):
            raise ValueError("adapter_config.adapters must be a list")
        self._validate_household_mapping()
        valid_bridge_states = {
            "package-created-not-live",
            "package-exported",
            "active",
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
            unmapped_roles = mapped_members - set(self.member_roles)
            if unmapped_roles:
                raise ValueError(
                    "enabled OpenClaw transport requires a role for every mapped member"
                )
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
        deterministic_openclaw_fixture: bool = False,
        member_roles: dict[str, str] | None = None,
        fixture_member_id: str | None = None,
        openclaw_enabled: bool = False,
        openclaw_shared_secret: str | None = None,
        openclaw_actor_identities: dict[str, str] | None = None,
        openclaw_gateway_url: str = "ws://127.0.0.1:18789",
        openclaw_gateway_token: str | None = None,
        openclaw_model_configured: bool = False,
        member_chat_enabled_members: frozenset[str] | None = None,
        child_chat_enabled_members: frozenset[str] | None = None,
        deterministic_member_chat_fixture: bool = False,
        adapter_config: dict[str, Any] | None = None,
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
            deterministic_openclaw_fixture=deterministic_openclaw_fixture,
            member_roles=dict(member_roles or {}),
            fixture_member_id=fixture_member_id,
            openclaw_enabled=openclaw_enabled,
            openclaw_shared_secret=openclaw_shared_secret,
            openclaw_actor_identities=dict(openclaw_actor_identities or {}),
            openclaw_gateway_url=openclaw_gateway_url,
            openclaw_gateway_token=openclaw_gateway_token,
            openclaw_model_configured=openclaw_model_configured,
            member_chat_enabled_members=frozenset(member_chat_enabled_members or ()),
            child_chat_enabled_members=frozenset(child_chat_enabled_members or ()),
            deterministic_member_chat_fixture=deterministic_member_chat_fixture,
            adapter_config=dict(
                adapter_config or {"adapter_supervisor_port": 0, "adapters": []}
            ),
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


class AdministrateMeRuntime:
    """Own all central resources and their ordered startup/shutdown."""

    SOURCE_ASSURANCE_REFRESH_SECONDS = 30.0
    SOURCE_ASSURANCE_MAX_STALE_SECONDS = 300.0

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
            else DictMemberIdResolver(settings.member_identities)
        )
        self.extensions = ExtensionCatalog(
            settings.repository_root, settings.instance.instance_dir
        )
        self.settings_control = SettingsControlStore(
            settings.instance.config_dir / "settings-control.json"
        )
        self.temporary_surfaces = TemporarySurfaceStore(
            settings.instance.config_dir / "temporary-surfaces.json"
        )
        self.capabilities: dict[str, CapabilityRecord] = {}
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
        self.startup_error: str | None = None
        self._observation_active = True
        self.openclaw_state = "degraded" if settings.openclaw_enabled else "unavailable"

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
            await self._hydrate_openclaw_state()
            set_default_event_sink(self.events)
            self.observation = ObservationManager(
                self.events, self.settings.instance.config_dir / "runtime.yaml"
            )
            if self.settings.deterministic_member_chat_fixture:
                self.member_chat = DeterministicOpenClawChatTransport()
            elif self.settings.openclaw_gateway_token:
                self.member_chat = OpenClawGatewayChatTransport(
                    url=self.settings.openclaw_gateway_url,
                    token=self.settings.openclaw_gateway_token,
                )
            if self.settings.deterministic_openclaw_fixture:
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
            for projection_class in DEFAULT_PROJECTION_CLASSES:
                projection: Projection
                if projection_class is XlsxWorkbooksProjection:
                    projection = XlsxWorkbooksProjection(
                        self.settings.instance,
                        None,
                        event_log=self.event_log,
                        event_sink=self.events,
                        query_context_factory=self._xlsx_query_context,
                    )
                    self.xlsx_projection = projection
                else:
                    projection = projection_class()
                self.projections.register(projection)

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
                    config, repository_root=self.settings.repository_root
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
                ),
            )
            self.adapters.prepare()
            self.bus.subscribe(
                "external-sync:central-providers",
                ["external_sync.write_requested"],
                self._dispatch_external_write,
            )

            # All subscriptions are explicit before the one bus owner starts.
            await self.projections.start(start_bus=False)
            await self.pipelines.start(start_bus=False)
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
            await self.xlsx_reverse.start()
            await self._reconcile_household_enrollment()
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
        if self.stopping:
            return
        self.stopping = True
        try:
            if self._source_assurance_refresh_task is not None:
                self._source_assurance_refresh_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._source_assurance_refresh_task
                self._source_assurance_refresh_task = None
            if self.xlsx_reverse is not None:
                await self.xlsx_reverse.stop()
                self.xlsx_reverse = None
            if self.adapters is not None:
                await self.adapters.stop()
            if self.adapter_cursor_store is not None:
                self.adapter_cursor_store.close()
                self.adapter_cursor_store = None
            if self.openclaw_approval_store is not None:
                self.openclaw_approval_store.close()
                self.openclaw_approval_store = None
            if self.events is not None:
                self.events.stop_accepting()
            if self.pipelines is not None:
                await self.pipelines.stop()
            if self.bus is not None:
                await self.bus.stop()
            if self.projections is not None:
                await self.projections.stop(stop_bus=False)
            if self.event_log is not None:
                await self.event_log.close()
        finally:
            set_default_event_sink(None)
            self.started = False

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
        if not mapping:
            return []
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
        values.append(
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

    async def _dispatch_external_write(self, event: dict[str, Any]) -> None:
        """Execute central-hosted provider commands; Apple commands wait for bridges."""

        payload = event["payload"]
        communication_providers = {
            "gmail",
            "microsoft_graph",
            "bluebubbles",
            "twilio_sms",
            "twilio_whatsapp",
            "fixture",
        }
        provider = payload.get("provider")
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
        is_v2 = int(event.get("schema_version") or 1) >= 2
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
            if provider in communication_providers:
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
                )
                applied = result.applied
                suppressed = result.suppressed
                provider_called = not result.suppressed
                external_id = result.external_id
                result_body = {"applied": applied}
            result_payload: dict[str, Any] = {
                "command_id": payload["command_id"],
                "member_id": payload["member_id"],
                "provider": payload["provider"],
                "operation": payload["operation"],
                "external_id": external_id,
                "provider_revision": None,
                "result": result_body,
                "completed_at": now,
                "suppressed": suppressed,
            }
        except Exception as exc:  # provider failures are terminal, typed evidence
            if isinstance(exc, (LookupError, ValueError)):
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
                    else "not_found"
                    if isinstance(exc, LookupError)
                    else "provider"
                ),
                "message": f"{type(exc).__name__}: {exc}",
                "retryable": not isinstance(exc, (LookupError, ValueError)),
                "failed_at": now,
            }
        finally:
            if audit_token is not None:
                reset_outbound_audit_context(audit_token)
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
        return f"portal:{peer}", f"agent:main:administrateme-portal-{peer}-{signature}"

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
        if self.openclaw_state != "healthy":
            return {
                "state": "degraded",
                "enabled": True,
                "reason": f"openclaw_{self.openclaw_state}",
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
        configured = {
            str(item.get("kind"))
            for item in self.settings.adapter_config.get("adapters", [])
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
            if state in {"healthy", "degraded", "unavailable", "incompatible"}:
                self.openclaw_state = state

    async def _refresh_capability_registry(self) -> None:
        assert self.projections is not None
        assert self.pipelines is not None
        assert self.observation is not None
        self._observation_active = await self.observation.is_active()
        projection_status = await self.projections.status()
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
        if not self.settings.openclaw_enabled:
            standing_state: CapabilityState = "blocked"
        elif self.openclaw_state == "healthy":
            standing_state = "healthy"
        elif self.openclaw_state == "incompatible":
            standing_state = "blocked"
        else:
            standing_state = "degraded"
        standing_reason = (
            None
            if standing_state == "healthy"
            else (
                f"OpenClaw integration state: {self.openclaw_state}"
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
            not self.settings.openclaw_gateway_token or self.openclaw_state != "healthy"
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

    async def diagnostics(self) -> dict[str, Any]:
        if self.started:
            await self._refresh_capability_registry()
        observation_active = (
            await self.observation.is_active() if self.observation is not None else True
        )
        state = self.overall_state()
        if observation_active and state == "healthy":
            state = "observation_only"
        return {
            "status": state,
            "ready": self.started and state != "blocked",
            "accepting_writes": bool(self.events and self.events.accepting),
            "observation_active": observation_active,
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


@system_router.get("/live")
async def live(request: Request) -> dict[str, Any]:
    runtime: AdministrateMeRuntime = request.app.state.runtime
    return {"status": "alive", "runtime_started": runtime.started}


@system_router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    runtime: AdministrateMeRuntime = request.app.state.runtime
    payload = await runtime.diagnostics()
    return JSONResponse(payload, status_code=200 if payload["ready"] else 503)


def create_application(settings: RuntimeSettings) -> FastAPI:
    runtime = AdministrateMeRuntime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await runtime.start()
        app.state.event_log = runtime.event_log
        app.state.event_sink = runtime.events
        app.state.member_id_resolver = runtime.member_id_resolver
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
    app.include_router(member_router)
    app.include_router(system_router)
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
