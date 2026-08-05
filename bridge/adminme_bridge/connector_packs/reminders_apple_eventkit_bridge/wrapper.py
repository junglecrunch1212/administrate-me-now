"""AppleEventKitBridgeAdapter — bridge-runtime wrapper for the Apple
Reminders dual-deployment adapter (11b-ii-α-2).

Per ``docs/10-split-memo-11b-ii-alpha.md`` §2 + the 11b-ii-α-2 v4 prompt
§1 item 4 + §3 F-3: this wrapper is THIN. It does NOT re-implement
EventKit polling, the never-sync filter, dual-emit, or the observation
wrap — all of that lives in the central ``AppleEventKitAdapter``
(``packs/adapters/reminders/apple-eventkit/adapter.py``). The wrapper
exists solely to:

  (a) satisfy 11b-ii-α-1's :class:`StateMirrorBridgeAdapter` Protocol
      structurally (declare the ``family`` ClassVar + the
      ``start``/``stop``/``status`` method set) so the bridge
      ``BridgeSupervisor`` can host it alongside Cat-D
      ``KnowledgeAdapter`` packs;
  (b) synthesize a central :class:`AdapterContext` from the bridge's
      :class:`BridgeContext` — the two context types are structurally
      distinct (``BridgeContext`` carries ``ingest_client``/``sink``;
      ``AdapterContext`` carries the typed ``event_sink``/``config``), which is the
      gap 11b-ii-α-1's on-ramp split memo identified;
  (c) inject an :class:`IngestClientBridgeSink` (α-1) into the
      synthesized ``AdapterContext`` config under ``_bridge_sink`` so
      the central adapter's bridge-mode emit path
      (``deployment_target == "bridge"``) lands envelopes at the bridge
      sink → central :3330 ingest namespace → event log, with
      ``owner_scope`` stamped from Tailscale identity per [§6.19] /
      memo 04 §2.2;
  (d) forward ``stop()`` / ``status()`` to the internal adapter,
      translating the central :class:`AdapterStatus` to the bridge
      :class:`AdapterStatus` (the two are structurally near-identical
      but distinct dataclasses).

[PM-34]: composition, not multi-inheritance — the central adapter stays
a single-ABC ``ExternalStateMirrorAdapter`` subclass; bridge-runtime
support is a wrapper, not a second base class.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, ClassVar, Literal

from adminme.adapters.base import AdapterContext
from adminme.lib.adapters import WriteOperation
from adminme_bridge.adapters.base import AdapterStatus, BridgeContext
from adminme_bridge.adapters.sinks import (
    BridgeEventSink,
    BridgeTransportEventSink,
    IngestClientBridgeSink,
)


# The central pack lives under ``packs/adapters/reminders/apple-eventkit/``
# (hyphenated dirname, not importable via a dotted path). Mirror the
# importlib-by-file loader the central pack's own tests use
# (``tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py``).
# Resolved relative to the repo root so a single editable install of
# both adminme + adminme-bridge sees the same adapter source.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_CENTRAL_ADAPTER_PATH = (
    _REPO_ROOT / "packs" / "adapters" / "reminders" / "apple-eventkit" / "adapter.py"
)


def _load_central_adapter_class() -> Any:
    """Lazily resolve the central ``AppleEventKitAdapter`` class by file
    path. Tests monkeypatch the resolved module's ``AppleEventKitAdapter``
    attribute to inject a capturing fake — the loader caches the module
    in ``sys.modules`` under a stable name so monkeypatches stick across
    multiple wrapper instances within a single test."""
    import sys

    module_name = "_adminme_bridge_central_apple_eventkit"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached.AppleEventKitAdapter
    spec = importlib.util.spec_from_file_location(module_name, _CENTRAL_ADAPTER_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover — defensive
        raise ImportError(f"failed to spec central adapter at {_CENTRAL_ADAPTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.AppleEventKitAdapter


class AppleEventKitBridgeAdapter:
    """Bridge-runtime wrapper around the central ``AppleEventKitAdapter``.

    Satisfies 11b-ii-α-1's :class:`StateMirrorBridgeAdapter` Protocol
    structurally: ``family`` ClassVar + ``async start(*, ctx:
    BridgeContext)`` + ``async stop()`` + ``status() -> AdapterStatus``.
    """

    family: ClassVar[Literal["reminders"]] = "reminders"
    provider: ClassVar[str] = "apple_reminders"

    def __init__(
        self,
        *,
        internal_config: dict[str, Any] | None = None,
    ) -> None:
        if not internal_config or not internal_config.get("target_list_identifier"):
            raise ValueError(
                "reminders bridge requires pack_config.target_list_identifier"
            )
        # The internal central adapter is constructed lazily on first
        # start() so importing this module never requires the central
        # pack to be on sys.path at import time.
        self._internal_config: dict[str, Any] = dict(internal_config)
        self._internal: Any = None
        self._bridge_status: AdapterStatus = AdapterStatus()

    async def start(self, *, ctx: BridgeContext) -> None:
        """Synthesize a central :class:`AdapterContext` from ``ctx`` and
        start the internal adapter in ``deployment_target=bridge`` mode."""
        AppleEventKitAdapter = _load_central_adapter_class()

        # A-5: prefer an explicitly-injected sink; else build the
        # production IngestClientBridgeSink from the bridge ingest client.
        sink: BridgeEventSink = (
            ctx.sink
            if ctx.sink is not None
            else IngestClientBridgeSink(ctx.ingest_client)
        )

        # Internal adapter constructor config: pack-level fields the
        # central adapter validates in start() (target_list_identifier,
        # member_id, sensitivity_floor, never-sync substring lists).
        # member_id is forced to the bridge daemon's configured
        # member_id per [§6.19] / memo 04 §2.2 — the per-member iCloud
        # identity owns the bridge's emit scope.
        internal_config: dict[str, Any] = dict(self._internal_config)
        internal_config["member_id"] = ctx.member_id
        internal_config["tenant_id"] = ctx.tenant_id
        internal_config.setdefault("provider_account_id", ctx.member_id)
        internal_config.setdefault("emit_schema_version", 2)

        internal = AppleEventKitAdapter(config=internal_config)
        self._internal = internal

        # Synthesize a complete central AdapterContext. Bridge transport is
        # adapted to the typed EventSink while _bridge_sink preserves the
        # existing dual-deployment transport branch. ``config`` carries the bridge-only
        # discriminators in addition to the constructor config so
        # ctx.config["member_id"] / ["target_list_identifier"] reads
        # remain consistent across emit-site call shapes.
        synthesized_config: dict[str, Any] = {
            **internal_config,
            "deployment_target": "bridge",
            "_bridge_member_id": ctx.member_id,
            "_bridge_sink": sink,
        }
        synthesized: AdapterContext = AdapterContext(
            event_sink=BridgeTransportEventSink(
                sink,
                tenant_id=str(internal_config.get("tenant_id") or "default"),
            ),
            config=synthesized_config,
            correlation_id_factory=ctx.correlation_id_factory,
            now_factory=ctx.now_factory,
        )

        await internal.start(ctx=synthesized)

    async def stop(self) -> None:
        """Forward to the internal adapter (no-op if never started)."""
        if self._internal is not None:
            await self._internal.stop()

    async def apply_write(
        self, operation: str, payload: dict[str, Any], target_external_id: str | None = None
    ) -> dict[str, Any]:
        if self._internal is None:
            raise RuntimeError("Apple Reminders adapter has not finished starting")
        result = await self._internal.apply_write(
            WriteOperation(operation, target_external_id, payload)
        )
        return {
            "applied": result.applied,
            "external_id": result.external_id,
            "suppressed": result.suppressed,
        }

    def status(self) -> AdapterStatus:
        """Return a bridge :class:`AdapterStatus`, translating from the
        central adapter's :class:`AdapterStatus` (the bridge variant
        omits ``next_backoff_s``)."""
        if self._internal is None:
            return self._bridge_status
        central: Any = self._internal.status()
        return AdapterStatus(
            state=central.state,
            last_error=central.last_error,
            last_cursor_advance_at=central.last_cursor_advance_at,
            started_at=central.started_at,
            restart_count=central.restart_count,
            extra=dict(central.extra),
        )


__all__ = ["AppleEventKitBridgeAdapter"]
