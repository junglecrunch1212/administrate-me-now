"""
PipelineRunner — in-process reactive pipeline executor.

Implemented for prompt 10a per [BUILD.md L4] and [§7].

Reactive pipelines subscribe via ``triggers.events`` in their
``pipeline.yaml`` manifest and run inside this runner ([§7.1]). Proactive
pipelines register as OpenClaw standing orders at product boot — NOT
here — so they share OpenClaw's approval, observation-mode, and
rate-limit machinery ([§7.2], [§14], [cheatsheet Q3]).

Key rules:
- No pipeline writes directly to a projection or an xlsx file; pipelines
  emit events, projections consume them ([§7.3]).
- Pipelines invoke skills ONLY through ``await run_skill(skill_id,
  inputs, ctx)`` which wraps OpenClaw's ``/tools/invoke`` per [ADR-0002];
  pipelines NEVER import provider SDKs ([§7.4], [§8], [D6]).
- A pipeline failure on one event does not halt the bus ([§7.7]); the
  bus owns failure semantics — checkpoint not advanced on raise — and
  this runner just constructs context and dispatches.

Pipeline packs are discovered at runtime under InstanceConfig-resolved
paths ([§15], [D15]); this runner takes ``builtin_root`` and
``installed_root`` as explicit arguments and never hardcodes a path.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.events.writer import EventSink
from adminme.lib.governance import GuardedWrite
from adminme.lib.instance_config import InstanceConfig
from adminme.lib.observation import ObservationManager, outbound
from adminme.lib.session import build_internal_session
from adminme.lib.skill_runner import run_skill
from adminme.pipelines.base import PipelineContext
from adminme.pipelines.pack_loader import LoadedPipelinePack, load_pipeline_pack
from adminme.source_assessment_recovery import (
    recovery_assessment_idempotency_key,
    recovery_refusal_idempotency_key,
)
from adminme.source_publication import (
    COMPLETION_EVENT_TYPE,
    PLAN_EVENT_TYPE,
    publication_completion_key,
    publication_completion_valid,
)

if TYPE_CHECKING:
    import sqlcipher3

_log = logging.getLogger(__name__)

_SOURCE_RECOVERY_EVENT = "source.assessment.recovery_requested"
_SOURCE_RECOVERY_PACK_ID = "pipeline:noise_filtering"
_SOURCE_RECOVERY_MAX_PENDING = 64
_SOURCE_PUBLICATION_MAX_PENDING = 64


class PipelineRunner:
    """Owns the bus subscriptions for registered reactive pipeline packs.

    Lifecycle mirrors :class:`adminme.projections.runner.ProjectionRunner`:
    ``register()`` is called before ``start()``; ``start()`` builds one
    bus subscription per registered pack with a closure that constructs a
    fresh :class:`PipelineContext` per delivered event and awaits the
    pack's ``handle()``. The bus owns checkpointing and failure
    semantics ([§1.10], [§7.7]).

    The optional ``parties_conn_factory`` keyword arg is threaded into
    every :class:`PipelineContext` constructed by ``_make_callback`` so
    pipelines that need to resolve party identifiers (e.g.
    ``commitment_extraction``) can open a SQLCipher connection to the
    parties projection DB inside ``handle()``. Default ``None`` keeps
    backward compatibility with all existing 10a runner-construction
    sites; pipelines that depend on the factory must check for ``None``
    and degrade cleanly.
    """

    def __init__(
        self,
        bus: EventBus,
        event_log: EventLog,
        event_sink: EventSink,
        instance_config: InstanceConfig,
        *,
        observation_manager: ObservationManager | None = None,
        guarded_write: GuardedWrite | None = None,
        parties_conn_factory: Callable[[], "sqlcipher3.Connection"] | None = None,
        run_skill_fn: Callable[..., Awaitable[Any]] | None = None,
        profile_loader: Callable[[str], dict[str, Any] | None] | None = None,
        persona_loader: Callable[[], dict[str, Any] | None] | None = None,
        candidate_policy_fn: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self._bus = bus
        self._log = event_log
        self._events = event_sink
        self._config = instance_config
        self._observation_manager = observation_manager
        self._guarded_write = guarded_write
        self._parties_conn_factory = parties_conn_factory
        self._run_skill_fn = run_skill_fn or run_skill
        self._profile_loader = profile_loader
        self._persona_loader = persona_loader
        self._candidate_policy_fn = candidate_policy_fn
        self._packs: dict[str, LoadedPipelinePack] = {}
        self._started = False

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------
    def register(self, pack: LoadedPipelinePack) -> None:
        """Register a loaded pipeline pack. Must be called before
        :meth:`start`. Raises on duplicate ``pack_id``."""
        if self._started:
            raise RuntimeError("register() must be called before start()")
        if pack.pack_id in self._packs:
            raise ValueError(f"pipeline pack already registered: {pack.pack_id}")
        self._packs[pack.pack_id] = pack

    def registered_pack_ids(self) -> list[str]:
        return sorted(self._packs)

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------
    async def discover(
        self,
        *,
        builtin_root: Path,
        installed_root: Path,
    ) -> None:
        """Walk ``builtin_root`` and ``installed_root`` for ``pipeline.yaml``
        files; load and register each pack found.

        Per UT-9, both paths must be absolute; the loader does not fall
        back to slug resolution. ``installed_root`` is allowed to not
        exist on a fresh instance (treated as zero installed packs);
        ``builtin_root`` is expected to exist in any production layout
        but a missing directory is also tolerated for test isolation.
        """
        for root in (builtin_root, installed_root):
            if not root.exists() or not root.is_dir():
                continue
            for manifest in sorted(root.rglob("pipeline.yaml")):
                pack_root = manifest.parent
                pack = await asyncio.to_thread(load_pipeline_pack, pack_root)
                self._wire_data_extensions(pack)
                events = pack.triggers.get("events") if pack.triggers else None
                if not events:
                    # Proactive (schedule / proactive) pipelines are
                    # registered via OpenClaw standing orders [cheatsheet
                    # Q3]; skip here with a one-line log.
                    _log.info(
                        "skipping non-reactive pipeline pack %s (no triggers.events)",
                        pack.pack_id,
                    )
                    continue
                if pack.pack_id in self._packs:
                    # Idempotent re-discovery is OK; skip duplicates.
                    continue
                self.register(pack)

    def _wire_data_extensions(self, pack: LoadedPipelinePack) -> None:
        """Inject data readers into legacy pack constructors at composition time.

        The packs remain independently testable with explicit fakes, while the
        production runner no longer leaves their default ``None`` loaders in
        place.
        """

        if self._profile_loader is not None and hasattr(
            pack.instance, "_profile_loader"
        ):
            setattr(pack.instance, "_profile_loader", self._profile_loader)
        if self._persona_loader is not None and hasattr(
            pack.instance, "_persona_loader"
        ):
            setattr(pack.instance, "_persona_loader", self._persona_loader)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self, *, start_bus: bool = True) -> None:
        """Subscribe each reactive pack to the bus, then start the bus
        if it isn't already running. Subscriptions must be registered
        before ``bus.start()`` per [§1.10]."""
        if self._started:
            return
        self._started = True
        for pack in self._packs.values():
            events = pack.triggers.get("events") if pack.triggers else None
            if not events:
                continue
            subscriber_id = f"pipeline:{pack.pack_id}"
            types = cast("list[str] | Literal['*']", list(events))
            self._bus.subscribe(
                subscriber_id,
                types,
                self._make_callback(pack),
                consumer_version=pack.version,
                replay_on_version_change=pack.replay_on_version_change,
                delivery_concurrency=pack.delivery_concurrency,
            )
        # `EventBus.start()` is a no-op if already running, so this is
        # safe for both test and production wiring orderings.
        if start_bus:
            await self._bus.start()

    async def stop(self) -> None:
        """No-op; bus shutdown is owned by the bus's own ``stop()``.
        Documented for symmetry with :class:`ProjectionRunner`."""
        if not self._started:
            return
        self._started = False

    async def reconcile_pending_source_assessment_recoveries(
        self, *, max_pending: int = _SOURCE_RECOVERY_MAX_PENDING
    ) -> int:
        """Terminalize bounded recovery controls before normal bus delivery.

        Recovery requests are content-free commands, but their ordinary bus
        checkpoint may already be past them after a crash or a handler defect.
        Read only those controls through one fixed encrypted-log watermark,
        select commands without either exact terminal idempotency identity,
        and deliver them through the production callback/context. The bus
        checkpoint is never changed and source facts are never replayed.
        """

        if not self._started:
            raise RuntimeError("pipeline runner must be started before reconciliation")
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        pack = self._packs.get(_SOURCE_RECOVERY_PACK_ID)
        if pack is None:
            return 0
        triggered_events = pack.triggers.get("events") if pack.triggers else None
        if not triggered_events or _SOURCE_RECOVERY_EVENT not in triggered_events:
            raise RuntimeError("noise-filtering recovery trigger is not registered")

        watermark = await self._log.latest_event_id()
        pending: list[dict[str, Any]] = []
        async for metadata in self._log.read_metadata_since(
            through=watermark or "",
            types=[_SOURCE_RECOVERY_EVENT],
        ):
            request = await self._log.get(str(metadata["event_id"]))
            if request is None:
                raise RuntimeError("recovery request disappeared from append-only log")
            if await self._recovery_terminal(request):
                continue
            pending.append(request)
            if len(pending) > max_pending:
                raise RuntimeError("pending source-recovery ceiling exceeded")

        callback = self._make_callback(pack)
        for request in pending:
            await callback(request)
            if not await self._recovery_terminal(request):
                raise RuntimeError("source-recovery command remained non-terminal")
        return len(pending)

    async def _recovery_terminal(self, request: dict[str, Any]) -> bool:
        request_event_id = str(request.get("event_id") or "")
        payload = request.get("payload") or {}
        source_event_id = str(payload.get("source_event_id") or "")
        tenant_id = str(request.get("tenant_id") or "")
        assessment, refusal = await asyncio.gather(
            self._log.get_by_idempotency(
                tenant_id,
                recovery_assessment_idempotency_key(request_event_id, source_event_id),
            ),
            self._log.get_by_idempotency(
                tenant_id, recovery_refusal_idempotency_key(request_event_id)
            ),
        )
        return assessment is not None or refusal is not None

    async def reconcile_pending_source_publications(
        self, *, max_pending: int = _SOURCE_PUBLICATION_MAX_PENDING
    ) -> int:
        """Resume committed proposal suffixes independently of bus checkpoints.

        A predecessor with the same pipeline version can skip these new derived
        events while advancing its checkpoint. One fixed metadata snapshot plus
        exact content-free header proof selects only unfinished plans. Their
        ordinary production handler resumes already-assessed drafts without a
        language call; source facts and completed draft bodies are never scanned.
        """
        if not self._started:
            raise RuntimeError("pipeline runner must be started before reconciliation")
        if max_pending <= 0 or max_pending > _SOURCE_PUBLICATION_MAX_PENDING:
            raise ValueError("max_pending must be between 1 and 64")
        pack = self._packs.get(_SOURCE_RECOVERY_PACK_ID)
        if pack is None:
            return 0
        triggered_events = pack.triggers.get("events") if pack.triggers else None
        if not triggered_events or PLAN_EVENT_TYPE not in triggered_events:
            raise RuntimeError("noise-filtering publication trigger is not registered")

        watermark = await self._log.latest_event_id()
        plans: list[str] = []
        completions: dict[str, list[dict[str, Any]]] = {}
        async for metadata in self._log.read_metadata_since(
            through=watermark or "",
            types=[PLAN_EVENT_TYPE, COMPLETION_EVENT_TYPE],
            include_payload_for=[COMPLETION_EVENT_TYPE],
        ):
            if metadata["type"] == PLAN_EVENT_TYPE:
                plans.append(str(metadata["event_id"]))
            else:
                completions.setdefault(
                    str(metadata.get("causation_id") or ""), []
                ).append(metadata)

        pending: list[str] = []
        for offset in range(0, len(plans), 32):
            plan_headers = await self._publication_headers(plans[offset : offset + 32])
            candidates = [
                completion
                for plan_id in plan_headers
                for completion in completions.get(plan_id, [])
            ]
            proof_ids = [str(completion["event_id"]) for completion in candidates]
            for completion in candidates:
                payload = completion.get("payload") or {}
                proof_ids.extend(
                    [
                        str(payload.get("source_event_id") or ""),
                        str(payload.get("assessment_event_id") or ""),
                    ]
                )
            headers = await self._publication_headers(
                [event_id for event_id in proof_ids if event_id <= (watermark or "")]
            )
            for plan_id, plan in plan_headers.items():
                terminal = False
                for metadata in completions.get(plan_id, []):
                    payload = metadata.get("payload") or {}
                    completion = {
                        **headers.get(str(metadata["event_id"]), {}),
                        "payload": payload,
                    }
                    if publication_completion_valid(
                        plan=plan,
                        completion=completion,
                        source=headers.get(
                            str(payload.get("source_event_id") or ""), {}
                        ),
                        assessment=headers.get(
                            str(payload.get("assessment_event_id") or ""), {}
                        ),
                    ):
                        terminal = True
                        break
                if not terminal:
                    pending.append(plan_id)
                    if len(pending) > max_pending:
                        raise RuntimeError(
                            "pending source-publication ceiling exceeded"
                        )

        callback = self._make_callback(pack)
        for plan_id in pending:
            pending_plan = await self._log.get(plan_id)
            if pending_plan is None:
                raise RuntimeError("publication plan disappeared from append-only log")
            await callback(pending_plan)
            if not await self._publication_terminal(pending_plan):
                raise RuntimeError("source-publication plan remained non-terminal")
        return len(pending)

    async def _publication_headers(
        self, event_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(event_id for event_id in event_ids if event_id))
        headers: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(ids), 32):
            for header in await self._log.get_event_headers(
                self._config.tenant_id, ids[offset : offset + 32]
            ):
                headers[str(header["event_id"])] = header
        return headers

    async def _publication_terminal(self, plan: dict[str, Any]) -> bool:
        completion = await self._log.get_by_idempotency(
            self._config.tenant_id, publication_completion_key(str(plan["event_id"]))
        )
        if completion is None:
            return False
        payload = completion.get("payload") or {}
        headers = await self._publication_headers(
            [
                str(payload.get("source_event_id") or ""),
                str(payload.get("assessment_event_id") or ""),
            ]
        )
        return publication_completion_valid(
            plan=plan,
            completion=completion,
            source=headers.get(str(payload.get("source_event_id") or ""), {}),
            assessment=headers.get(str(payload.get("assessment_event_id") or ""), {}),
        )

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    async def status(self) -> dict[str, dict[str, Any]]:
        """Per-pack status snapshot. Values include the bus-level
        subscriber status (checkpoint, lag, degraded flag) so the future
        bootstrap CLI can render a single-screen view."""
        out: dict[str, dict[str, Any]] = {}
        for pack in self._packs.values():
            subscriber_id = f"pipeline:{pack.pack_id}"
            try:
                bus_status = await self._bus.subscriber_status(subscriber_id)
            except KeyError:
                bus_status = {}
            out[pack.pack_id] = {
                "version": pack.version,
                "subscriber_id": subscriber_id,
                "subscriber_status": bus_status,
            }
        return out

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _make_callback(
        self,
        pack: LoadedPipelinePack,
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
        async def _cb(event: dict[str, Any]) -> None:
            correlation = event.get("correlation_id")
            session = build_internal_session(
                "pipeline_runner",
                "device",
                self._config.tenant_id,
                correlation_id=correlation,
            )
            ctx = PipelineContext(
                session=session,
                event_log=self._log,
                event_sink=self._events,
                run_skill_fn=self._run_skill_fn,
                outbound_fn=outbound,
                guarded_write=self._guarded_write,
                observation_manager=self._observation_manager,
                triggering_event_id=event["event_id"],
                correlation_id=correlation,
                parties_conn_factory=self._parties_conn_factory,
                candidate_policy_fn=self._candidate_policy_fn,
            )
            await pack.instance.handle(event, ctx)

        return _cb


__all__ = ["PipelineRunner"]
