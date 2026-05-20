"""AppleEventKitBridgeAdapter — bridge-runtime wrapper for the Apple
Reminders dual-deployment adapter (11b-ii-α-2).

Per docs/10-split-memo-11b-ii-alpha.md §2 + the 11b-ii-α-2 v4 prompt §1
item 4 + §3 F-3: this wrapper is THIN. It does NOT re-implement EventKit
polling, the never-sync filter, dual-emit, or the observation wrap — all
of that lives in the central ``AppleEventKitAdapter``
(``packs/adapters/reminders/apple-eventkit/adapter.py``). The wrapper
exists solely to:

  (a) satisfy 11b-ii-α-1's ``StateMirrorBridgeAdapter`` Protocol
      structurally (declare the ``family`` ClassVar + the
      ``start``/``stop``/``status`` method set) so the bridge
      ``BridgeSupervisor`` can host it alongside Cat-D ``KnowledgeAdapter``
      packs;
  (b) synthesize a central ``AdapterContext`` from the bridge's
      ``BridgeContext`` — the two context types are structurally distinct
      (``BridgeContext`` carries ``ingest_client``/``sink``;
      ``AdapterContext`` carries ``event_log``/``config``), which is the
      exact gap 11b-ii-α-1's on-ramp split memo identified;
  (c) inject an ``IngestClientBridgeSink`` (α-1) into the synthesized
      ``AdapterContext``'s config under ``_bridge_sink`` so the central
      adapter's bridge-mode emit path (``deployment_target == \"bridge\"``)
      lands envelopes at the bridge sink → central :3337 ingest router →
      event log, with ``owner_scope`` stamped from Tailscale identity per
      [§6.19] / memo 04 §2.2;
  (d) forward ``stop()`` / ``status()`` to the internal adapter,
      translating the central ``AdapterStatus`` to the bridge
      ``AdapterStatus`` (the two are structurally near-identical but
      distinct dataclasses).

[PM-34]: composition, not multi-inheritance — the central adapter stays a
single-ABC ``ExternalStateMirrorAdapter`` subclass; bridge-runtime support
is a wrapper, not a second base class.

NOTE (Partner sidecar, 2026-05-19): this module was authored by Partner as
an EXECUTOR_DOUBLE_FAILURE recovery sidecar after the 11b-ii-α-2 c3 cycle
step silent-committed four consecutive times. It has NOT been
execution-verified (``poetry run pytest`` / ``poetry run mypy``); an
operator sandbox pass is required before this is relied upon. See the PR
body.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from adminme.adapters.base import AdapterContext
from adminme_bridge.adapters.base import AdapterStatus, BridgeContext
from adminme_bridge.adapters.sinks import BridgeEventSink, IngestClientBridgeSink

if TYPE_CHECKING:  # pragma: no cover — import-cycle / central-pack-path avoidance
    from packs.adapters.reminders.apple_eventkit.adapter import AppleEventKitAdapter


class AppleEventKitBridgeAdapter:
    """Bridge-runtime wrapper around the central ``AppleEventKitAdapter``.

    Satisfies 11b-ii-α-1's ``StateMirrorBridgeAdapter`` Protocol
    structurally: ``family`` ClassVar + ``async start(*, ctx:
    BridgeContext)`` + ``async stop()`` + ``status() -> AdapterStatus``.
    """

    family: ClassVar[Literal["reminders"]] = "reminders"

    def __init__(self) -> None:
        # The internal central adapter is constructed lazily on first
        # start() so importing this module never requires the central
        # pack to be on sys.path (the bridge daemon resolves it at
        # runtime via the editable install per 11c-i F-7).
        self._internal: "AppleEventKitAdapter | None" = None

    async def start(self, *, ctx: BridgeContext) -> None:
        """Synthesize a central ``AdapterContext`` from ``ctx`` and start
        the internal adapter in ``deployment_target=bridge`` mode."""
        from packs.adapters.reminders.apple_eventkit.adapter import (
            AppleEventKitAdapter,
        )

        # A-5: prefer an explicitly-injected sink; else build the
        # production IngestClientBridgeSink from the bridge ingest client.
        sink: BridgeEventSink = (
            ctx.sink
            if ctx.sink is not None
            else IngestClientBridgeSink(ctx.ingest_client)
        )

        # The central adapter's bridge branch reads deployment_target +
        # _bridge_member_id + _bridge_sink from ctx.config (per the
        # 11b-ii-α-2 c2 deployment_target branching shipped in adapter.py).
        # event_log is unused on the bridge path — the bridge emit path
        # dispatches to _bridge_sink.emit(); the type-ignore documents the
        # intentional None.
        synthesized: AdapterContext = AdapterContext(
            event_log=None,  # type: ignore[arg-type]
            config={
                "deployment_target": "bridge",
                "member_id": ctx.member_id,
                "_bridge_member_id": ctx.member_id,
                "_bridge_sink": sink,
            },
            correlation_id_factory=ctx.correlation_id_factory,
            now_factory=ctx.now_factory,
        )

        adapter = AppleEventKitAdapter()
        self._internal = adapter
        await adapter.start(ctx=synthesized)

    async def stop(self) -> None:
        """Forward to the internal adapter (no-op if never started)."""
        if self._internal is not None:
            await self._internal.stop()

    def status(self) -> AdapterStatus:
        """Return a bridge ``AdapterStatus``, translating from the central
        adapter's ``AdapterStatus`` (structurally near-identical; the
        bridge variant omits ``next_backoff_s``)."""
        if self._internal is None:
            return AdapterStatus()
        central: Any = self._internal.status()
        return AdapterStatus(
            state=central.state,
            last_error=central.last_error,
            last_cursor_advance_at=central.last_cursor_advance_at,
            started_at=central.started_at,
            restart_count=central.restart_count,
            extra=central.extra,
        )


__all__ = ["AppleEventKitBridgeAdapter"]
