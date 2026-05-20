"""AppleCalendarBridgeAdapter — bridge-runtime wrapper for the Apple
Calendar dual-deployment adapter (11b-ii-β-β).

Per ``docs/11-split-memo-11b-ii-beta.md`` §2 + the 11b-ii-β-β v4 prompt
§1 item 4 + §3 F-3: this wrapper is THIN. It does NOT re-implement
EventKit polling, dedupe, observation wrap, or any adapter logic — all
of that lives in the central ``AppleCalendarAdapter``
(``packs/adapters/calendaring/apple-calendar/adapter.py``). The wrapper
exists solely to:

  (a) satisfy 11b-ii-α-1's :class:`StateMirrorBridgeAdapter` Protocol
      structurally (declare the ``family`` ClassVar + the
      ``start``/``stop``/``status`` method set) so the bridge
      ``BridgeSupervisor`` can host it alongside Cat-D
      ``KnowledgeAdapter`` packs;
  (b) synthesize a central :class:`AdapterContext` from the bridge's
      :class:`BridgeContext` — the two context types are structurally
      distinct (``BridgeContext`` carries ``ingest_client``/``sink``;
      ``AdapterContext`` carries ``event_log``/``config``);
  (c) inject an :class:`IngestClientBridgeSink` (α-1) into the
      synthesized ``AdapterContext`` config under ``_bridge_sink`` so
      the central adapter's bridge-mode emit path
      (``deployment_target == "bridge"``) lands envelopes at the bridge
      sink → central :3337 ingest router → event log, with
      ``owner_scope`` stamped from Tailscale identity per [§6.19] /
      memo 04 §2.2;
  (d) forward ``stop()`` / ``status()`` to the internal adapter,
      translating the central :class:`AdapterStatus` (with
      ``next_backoff_s``) to the bridge :class:`AdapterStatus`
      (without it).

[PM-34]: composition, not multi-inheritance — the central adapter stays
a single-ABC ``ExternalStateMirrorAdapter`` subclass; bridge-runtime
support is a wrapper, not a second base class.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, ClassVar, Literal

from adminme.adapters.base import AdapterContext
from adminme_bridge.adapters.base import AdapterStatus, BridgeContext
from adminme_bridge.adapters.sinks import BridgeEventSink, IngestClientBridgeSink


# The central pack lives under ``packs/adapters/calendaring/apple-calendar/``
# (hyphenated dirname, not importable via a dotted path). Mirror the
# importlib-by-file loader the central pack's own tests use.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_CENTRAL_ADAPTER_PATH = (
    _REPO_ROOT / "packs" / "adapters" / "calendaring" / "apple-calendar" / "adapter.py"
)


def _load_central_adapter_class() -> Any:
    """Lazily resolve the central ``AppleCalendarAdapter`` class by file
    path. Tests monkeypatch this loader to inject a capturing fake — the
    loader caches the module in ``sys.modules`` under a stable name so
    monkeypatches stick across multiple wrapper instances within a
    single test."""
    import sys

    module_name = "_adminme_bridge_central_apple_calendar"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached.AppleCalendarAdapter
    spec = importlib.util.spec_from_file_location(
        module_name, _CENTRAL_ADAPTER_PATH
    )
    if spec is None or spec.loader is None:  # pragma: no cover — defensive
        raise ImportError(
            f"failed to spec central adapter at {_CENTRAL_ADAPTER_PATH}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.AppleCalendarAdapter


# Default config for the internal AppleCalendarAdapter constructor when
# the bridge supervisor invokes the wrapper with no explicit pack-config.
# 11c-ii's instantiate-from-PackHandle hook will wire ``PackHandle.config``
# through; for now the wrapper substitutes minimal-bootstrap defaults so
# the central adapter's start() validation passes. ``calendar_identifier``
# is set during the per-member bootstrap §16-§10 enrollment wizard and
# normally overridden via ``internal_config``; the placeholder satisfies
# the central adapter's non-empty-string check on start().
_DEFAULT_BRIDGE_INTERNAL_CONFIG: dict[str, Any] = {
    "calendar_identifier": "bridge:pending-bootstrap-calendar",
    "sensitivity_floor": "normal",
}


class AppleCalendarBridgeAdapter:
    """Bridge-runtime wrapper around the central ``AppleCalendarAdapter``.

    Satisfies 11b-ii-α-1's :class:`StateMirrorBridgeAdapter` Protocol
    structurally: ``family`` ClassVar + ``async start(*, ctx:
    BridgeContext)`` + ``async stop()`` + ``status() -> AdapterStatus``.
    """

    family: ClassVar[Literal["calendaring"]] = "calendaring"

    def __init__(
        self,
        *,
        internal_config: dict[str, Any] | None = None,
    ) -> None:
        # The internal central adapter is constructed lazily on first
        # start() so importing this module never requires the central
        # pack to be on sys.path at import time (11c-ii will wire
        # PackHandle.config through; today's default is the placeholder).
        self._internal_config: dict[str, Any] = dict(
            internal_config or _DEFAULT_BRIDGE_INTERNAL_CONFIG
        )
        self._internal: Any = None
        self._bridge_status: AdapterStatus = AdapterStatus()

    async def start(self, *, ctx: BridgeContext) -> None:
        """Synthesize a central :class:`AdapterContext` from ``ctx`` and
        start the internal adapter in ``deployment_target=bridge`` mode."""
        AppleCalendarAdapter = _load_central_adapter_class()

        # A-5: prefer an explicitly-injected sink; else build the
        # production IngestClientBridgeSink from the bridge ingest client.
        sink: BridgeEventSink = (
            ctx.sink
            if ctx.sink is not None
            else IngestClientBridgeSink(ctx.ingest_client)
        )

        # Internal adapter constructor config: pack-level fields the
        # central adapter validates in start() (calendar_identifier,
        # member_id, sensitivity_floor). member_id is forced to the
        # bridge daemon's configured member_id per [§6.19] / memo 04
        # §2.2 — the per-member iCloud identity owns the bridge's
        # emit scope.
        internal_config: dict[str, Any] = dict(self._internal_config)
        internal_config["member_id"] = ctx.member_id

        internal = AppleCalendarAdapter(config=internal_config)
        self._internal = internal

        # Synthesize a central AdapterContext. event_log=None is
        # intentional on the bridge path — the central adapter's
        # _append() routes through _bridge_sink when
        # deployment_target=="bridge" (see adapter.py
        # _append_via_bridge_sink). ``config`` carries the bridge-only
        # discriminators in addition to the constructor config so
        # ctx.config["member_id"] / ["calendar_identifier"] reads
        # remain consistent across emit-site call shapes.
        synthesized_config: dict[str, Any] = {
            **internal_config,
            "deployment_target": "bridge",
            "_bridge_member_id": ctx.member_id,
            "_bridge_sink": sink,
        }
        synthesized: AdapterContext = AdapterContext(
            event_log=None,  # type: ignore[arg-type]
            config=synthesized_config,
            correlation_id_factory=ctx.correlation_id_factory,
            now_factory=ctx.now_factory,
        )

        await internal.start(ctx=synthesized)

    async def stop(self) -> None:
        """Forward to the internal adapter (no-op if never started)."""
        if self._internal is not None:
            await self._internal.stop()

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


__all__ = ["AppleCalendarBridgeAdapter"]
