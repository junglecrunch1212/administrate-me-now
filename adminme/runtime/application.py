"""Production composition root for the single AdministrateMe v2 process."""

from __future__ import annotations

import asyncio
import os
import time
from functools import partial
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import yaml
import sqlcipher3
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from adminme.adapters.base import AdapterContext
from adminme.adapters.fixture_messaging import FixtureMessagingAdapter
from adminme.daemons.adapter_supervisor import AdapterSupervisor
from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog, new_correlation_id
from adminme.events.writer import EventWriter
from adminme.lib.governance import (
    ActionGateConfig,
    AgentAllowlist,
    GuardedWrite,
    RateLimiter,
)
from adminme.lib.instance_config import (
    InstanceConfig,
    load_instance_config,
    resolve_instance_dir,
)
from adminme.lib.observation import ObservationManager
from adminme.lib.session import build_internal_session
from adminme.lib.skill_runner import set_default_event_sink
from adminme.lib.skill_runner.fixture import build_fixture_runtime
from adminme.lib.skill_runner.wrapper import run_skill
from adminme.pipelines.runner import PipelineRunner
from adminme.products.automation.main import router as automation_router
from adminme.products.bridge.auth import DictMemberIdResolver
from adminme.products.bridge.routers import ingest_router
from adminme.products.capture.main import router as capture_router
from adminme.products.comms.main import router as comms_router
from adminme.products.core.main import router as core_router
from adminme.projections.base import Projection
from adminme.projections.runner import DEFAULT_PROJECTION_CLASSES, ProjectionRunner
from adminme.projections.xlsx_workbooks import XlsxWorkbooksProjection
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext

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
    adapter_config: dict[str, Any] = field(
        default_factory=lambda: {"adapter_supervisor_port": 0, "adapters": []}
    )
    openclaw_enabled: bool = False
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
        return cls(instance=instance, encryption_key=key, repository_root=root)

    @classmethod
    def credential_free(
        cls,
        instance_dir: Path,
        *,
        encryption_key: bytes,
        repository_root: Path,
        member_identities: dict[str, str] | None = None,
        deterministic_openclaw_fixture: bool = False,
        member_roles: dict[str, str] | None = None,
        fixture_member_id: str | None = None,
    ) -> "RuntimeSettings":
        """Build a no-network Phase-A configuration with generated test keys."""

        return cls(
            instance=load_instance_config(instance_dir),
            encryption_key=encryption_key,
            repository_root=repository_root,
            member_identities=dict(member_identities or {}),
            deterministic_openclaw_fixture=deterministic_openclaw_fixture,
            member_roles=dict(member_roles or {}),
            fixture_member_id=fixture_member_id,
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

    PRODUCT_NAMES = ("core", "comms", "capture", "automation", "bridge")

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        self.event_log: EventLog | None = None
        self.bus: EventBus | None = None
        self.events: EventWriter | None = None
        self.projections: ProjectionRunner | None = None
        self.xlsx_projection: XlsxWorkbooksProjection | None = None
        self.pipelines: PipelineRunner | None = None
        self.adapters: AdapterSupervisor | None = None
        self.fixture_adapter: FixtureMessagingAdapter | None = None
        self.commitment_command_locks: dict[str, asyncio.Lock] = {}
        self.observation: ObservationManager | None = None
        self.guarded_write: GuardedWrite | None = None
        self.member_id_resolver = DictMemberIdResolver(settings.member_identities)
        self.capabilities: dict[str, CapabilityRecord] = {}
        self.standing_orders: dict[str, dict[str, Any]] = {}
        self.started = False
        self.stopping = False
        self.startup_error: str | None = None
        self._observation_active = True

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
            set_default_event_sink(self.events)
            self.observation = ObservationManager(
                self.events, self.settings.instance.config_dir / "runtime.yaml"
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
            self.guarded_write = GuardedWrite(
                ActionGateConfig(action_gates={}, rate_limits={}),
                RateLimiter(),
                AgentAllowlist([("user:*", ["commitment.*", "communication.*"])]),
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

            self.pipelines = PipelineRunner(
                self.bus,
                self.event_log,
                self.events,
                self.settings.instance,
                observation_manager=self.observation,
                guarded_write=self.guarded_write,
                parties_conn_factory=self._parties_connection_factory,
                run_skill_fn=(
                    partial(
                        run_skill,
                        _runtime=build_fixture_runtime(
                            event_sink=self.events,
                            event_log=self.event_log,
                            raw_events_dir=self.settings.instance.raw_events_dir,
                        ),
                    )
                    if self.settings.deterministic_openclaw_fixture
                    else run_skill
                ),
            )
            await self.pipelines.discover(
                builtin_root=self.settings.repository_root / "packs" / "pipelines",
                installed_root=self.settings.instance.packs_dir / "pipelines",
            )
            self._load_standing_orders()
            self._inventory_optional_adapters()

            # All subscriptions are explicit before the one bus owner starts.
            await self.projections.start(start_bus=False)
            await self.pipelines.start(start_bus=False)
            await self.bus.start()

            self.adapters = AdapterSupervisor(
                config_path=self.settings.instance.config_dir / "adapters.yaml",
                config_data=self.settings.adapter_config,
                adapter_loader=lambda _config: [],
                ctx_factory=lambda: AdapterContext(
                    event_sink=self._require_events(),
                    config={},
                    correlation_id_factory=new_correlation_id,
                    now_factory=lambda: datetime.now(timezone.utc),
                ),
            )
            await self.adapters.start()
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
            if self.adapters is not None:
                await self.adapters.stop()
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
            if isinstance(item, dict)
        }
        for manifest_path in sorted(root.rglob("pack.yaml")):
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            pack_block = manifest.get("pack") if isinstance(manifest, dict) else None
            pack_id = None
            if isinstance(pack_block, dict):
                pack_id = pack_block.get("id") or pack_block.get("name")
            pack_id = str(pack_id or manifest_path.parent.name)
            enabled = pack_id in configured
            self.capabilities[f"adapter:{pack_id}"] = CapabilityRecord(
                name=pack_id,
                kind="adapter",
                state="degraded" if enabled else "disabled",
                reason=(
                    "configured adapter loader is not enabled in Phase-A composition"
                    if enabled
                    else "optional provider not configured; no credential loaded"
                ),
                details={"manifest": str(manifest_path.relative_to(root))},
            )

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
                },
            )
        for pack_id in self.pipelines.registered_pack_ids():
            self.capabilities[f"pipeline:{pack_id}"] = CapabilityRecord(
                name=pack_id, kind="reactive_pipeline", state="healthy"
            )
        standing_state: CapabilityState = (
            "healthy" if self.settings.openclaw_enabled else "blocked"
        )
        standing_reason = (
            None
            if self.settings.openclaw_enabled
            else (
                "required OpenClaw gateway intentionally absent in credential-free mode"
            )
        )
        for program_id in sorted(self.standing_orders):
            self.capabilities[f"standing_order:{program_id}"] = CapabilityRecord(
                name=program_id,
                kind="openclaw_standing_order",
                state=standing_state,
                reason=standing_reason,
            )
        for product in self.PRODUCT_NAMES:
            self.capabilities[f"product:{product}"] = CapabilityRecord(
                name=product, kind="product_namespace", state="healthy"
            )

    def overall_state(
        self,
    ) -> Literal["healthy", "observation_only", "degraded", "blocked"]:
        states = {record.state for record in self.capabilities.values()}
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
