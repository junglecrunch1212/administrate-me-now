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
from typing import Any, Literal, cast

from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.lib.governance import GuardedWrite
from adminme.lib.instance_config import InstanceConfig
from adminme.lib.observation import ObservationManager, outbound
from adminme.lib.session import build_internal_session
from adminme.lib.skill_runner import run_skill
from adminme.pipelines.base import PipelineContext
from adminme.pipelines.pack_loader import LoadedPipelinePack, load_pipeline_pack

_log = logging.getLogger(__name__)


class PipelineRunner:
    """Owns the bus subscriptions for registered reactive pipeline packs.

    Lifecycle mirrors :class:`adminme.projections.runner.ProjectionRunner`:
    ``register()`` is called before ``start()``; ``start()`` builds one
    bus subscription per registered pack with a closure that constructs a
    fresh :class:`PipelineContext` per delivered event and awaits the
    pack's ``handle()``. The bus owns checkpointing and failure
    semantics ([§1.10], [§7.7]).
    """

    def __init__(
        self,
        bus: EventBus,
        event_log: EventLog,
        instance_config: InstanceConfig,
        *,
        observation_manager: ObservationManager | None = None,
        guarded_write: GuardedWrite | None = None,
    ) -> None:
        self._bus = bus
        self._log = event_log
        self._config = instance_config
        self._observation_manager = observation_manager
        self._guarded_write = guarded_write
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

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
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
            self._bus.subscribe(subscriber_id, types, self._make_callback(pack))
        # `EventBus.start()` is a no-op if already running, so this is
        # safe for both test and production wiring orderings.
        await self._bus.start()

    async def stop(self) -> None:
        """No-op; bus shutdown is owned by the bus's own ``stop()``.
        Documented for symmetry with :class:`ProjectionRunner`."""
        if not self._started:
            return
        self._started = False

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
                run_skill_fn=run_skill,
                outbound_fn=outbound,
                guarded_write=self._guarded_write,
                observation_manager=self._observation_manager,
                triggering_event_id=event["event_id"],
                correlation_id=correlation,
            )
            await pack.instance.handle(event, ctx)

        return _cb


__all__ = ["PipelineRunner"]
