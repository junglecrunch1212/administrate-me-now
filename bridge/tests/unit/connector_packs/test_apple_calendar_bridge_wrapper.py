"""Unit tests for :class:`AppleCalendarBridgeAdapter` — the bridge-runtime
wrapper that satisfies 11b-ii-α-1's :class:`StateMirrorBridgeAdapter`
Protocol around the central ``AppleCalendarAdapter`` (11b-ii-β-β c3).

Verifies:

  - Structural Protocol satisfaction (``family`` ClassVar + the
    ``start``/``stop``/``status`` method set).
  - :class:`AdapterContext` synthesis: ``deployment_target=bridge`` +
    ``_bridge_member_id`` + ``_bridge_sink`` populated from the
    :class:`BridgeContext`.
  - A-5 sink preference: ``ctx.sink`` wins over a fresh
    :class:`IngestClientBridgeSink` when both could be sourced.
  - ``stop()`` + ``status()`` forwarding to the internal adapter.

F-3 (HARD): the wrapper is THIN — it MUST NOT re-implement EventKit
polling, dedupe, observation wrap, or any adapter logic. This test file
exercises only the Protocol surface + the synthesis contract; the
central adapter's own behavior is covered by
``tests/unit/adapters/calendaring/test_apple_calendar_dual_deployment.py``.

F-6 (HARD): basename ``test_apple_calendar_bridge_wrapper.py`` is unique
across the repo (verified against
``test_apple_calendar.py`` / pack-internal siblings /
``test_apple_calendar_dual_deployment.py``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from adminme.adapters.base import AdapterContext
from adminme_bridge.adapters.base import (
    AdapterStatus,
    BridgeContext,
    StateMirrorBridgeAdapter,
)
from adminme_bridge.adapters.sinks import (
    BridgeEventSink,
    EmitResult,
    IngestClientBridgeSink,
)
from adminme_bridge.connector_packs.calendaring_apple_calendar_bridge import (
    AppleCalendarBridgeAdapter,
)
from adminme_bridge.connector_packs.calendaring_apple_calendar_bridge import (
    wrapper as wrapper_mod,
)
from adminme_bridge.ingest_client import IngestClient


_FROZEN_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


class _FakeBridgeSink:
    """Captures every envelope emitted via the wrapper-injected sink."""

    def __init__(self) -> None:
        self.envelopes: list[Any] = []

    async def emit(self, envelope: Any) -> EmitResult:
        self.envelopes.append(envelope)
        return EmitResult(ok=True, event_id="evt-x", error=None)


def _make_ingest_client() -> IngestClient:
    """Construct an IngestClient with a non-network httpx transport.
    The wrapper never actually calls ``submit`` in the start() unit
    tests — the IngestClient is just the construction-arg for the
    fallback :class:`IngestClientBridgeSink` per A-5."""
    import httpx

    return IngestClient(
        central_endpoint_url="http://127.0.0.1:0",
        tailscale_identity_header="member-bridge-test",
        client=httpx.AsyncClient(timeout=httpx.Timeout(0.1)),
    )


def _make_bridge_ctx(
    *,
    member_id: str = "member-bridge-alice",
    sink: BridgeEventSink | None = None,
) -> BridgeContext:
    return BridgeContext(
        member_id=member_id,
        ingest_client=_make_ingest_client(),
        correlation_id_factory=lambda: "corr-wrapper-test",
        now_factory=lambda: _FROZEN_NOW,
        sink=sink,
    )


class _CapturingInternal:
    """Stand-in for the central ``AppleCalendarAdapter`` used to
    capture the synthesized ``AdapterContext`` without actually
    starting the EventKit poll loop."""

    def __init__(self, *, config: dict[str, Any]) -> None:
        self.constructor_config = dict(config)
        self.start_ctx: AdapterContext | None = None
        self.stopped = False
        self._status = AdapterStatus(
            state="running",
            started_at=_FROZEN_NOW,
            restart_count=0,
        )

    async def start(self, *, ctx: AdapterContext) -> None:
        self.start_ctx = ctx

    async def stop(self) -> None:
        self.stopped = True
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status


def _swap_internal_with_capture(monkeypatch: Any) -> list[_CapturingInternal]:
    """Patch the wrapper's central-adapter loader so tests can drive
    ``start()`` without the real central adapter (which would require a
    full EventKit fixture stack).

    Returns a mutable list that captures each constructed
    ``_CapturingInternal`` so tests can assert on the constructor config
    + the synthesized AdapterContext.
    """
    captured: list[_CapturingInternal] = []

    def _factory(*, config: dict[str, Any]) -> _CapturingInternal:
        instance = _CapturingInternal(config=config)
        captured.append(instance)
        return instance

    # The wrapper resolves the central adapter class via
    # _load_central_adapter_class(); monkeypatching the loader keeps the
    # test independent of the file-path importlib indirection.
    monkeypatch.setattr(wrapper_mod, "_load_central_adapter_class", lambda: _factory)
    return captured


# ===========================================================================
# Protocol satisfaction + class shape
# ===========================================================================


def test_apple_calendar_bridge_adapter_satisfies_state_mirror_bridge_adapter_protocol() -> (
    None
):
    """Structural Protocol pin: ``AppleCalendarBridgeAdapter`` carries
    ``family`` + the ``start``/``stop``/``status`` methods so an
    instance passes the runtime-checkable Protocol isinstance check."""
    wrapper = AppleCalendarBridgeAdapter()
    assert isinstance(wrapper, StateMirrorBridgeAdapter)


def test_wrapper_declares_family_calendaring_classvar() -> None:
    """The ``family`` ClassVar literal matches the central Cat-B ABC's
    discriminator (``family: ClassVar[Literal["reminders",
    "calendaring", ...]]`` on
    ``adminme/lib/adapters/external_state_mirror_base.py``)."""
    assert AppleCalendarBridgeAdapter.family == "calendaring"


def test_status_returns_default_when_never_started() -> None:
    """``status()`` MUST NOT raise when called before ``start()`` — the
    bridge supervisor's snapshot endpoint polls every registered
    wrapper, including ones whose ``start()`` task hasn't fired yet."""
    wrapper = AppleCalendarBridgeAdapter()
    status = wrapper.status()
    assert isinstance(status, AdapterStatus)
    assert status.state == "starting"


# ===========================================================================
# AdapterContext synthesis from BridgeContext
# ===========================================================================


def test_wrapper_synthesizes_adapter_context_with_deployment_target_bridge(
    monkeypatch: Any,
) -> None:
    """The synthesized :class:`AdapterContext` carries
    ``deployment_target=bridge`` + ``_bridge_member_id`` (from
    ``ctx.member_id``) + ``_bridge_sink`` (resolved per A-5). The
    central adapter's start() reads these to take the bridge branch."""
    captured = _swap_internal_with_capture(monkeypatch)

    sink = _FakeBridgeSink()
    wrapper = AppleCalendarBridgeAdapter()
    bridge_ctx = _make_bridge_ctx(member_id="member-bridge-bob", sink=sink)

    asyncio.run(wrapper.start(ctx=bridge_ctx))

    assert len(captured) == 1
    synthesized = captured[0].start_ctx
    assert synthesized is not None
    assert synthesized.config["deployment_target"] == "bridge"
    assert synthesized.config["_bridge_member_id"] == "member-bridge-bob"
    assert synthesized.config["_bridge_sink"] is sink
    # A typed bridge writer is supplied on the bridge path (central
    # adapter's _append routes through _bridge_sink in bridge mode).
    assert synthesized.event_sink.tenant_id == "default"


def test_wrapper_passes_member_id_into_internal_constructor_config(
    monkeypatch: Any,
) -> None:
    """The internal adapter's constructor config (``config={...}``) gets
    ``member_id`` set to ``ctx.member_id`` per [§6.19] / memo 04 §2.2:
    the bridge daemon's configured member_id owns the bridge's
    per-member emit scope."""
    captured = _swap_internal_with_capture(monkeypatch)

    wrapper = AppleCalendarBridgeAdapter()
    bridge_ctx = _make_bridge_ctx(member_id="member-bridge-carol")

    asyncio.run(wrapper.start(ctx=bridge_ctx))

    assert captured[0].constructor_config["member_id"] == "member-bridge-carol"
    # The placeholder calendar_identifier is propagated so the central
    # adapter's non-empty-string check in start() passes.
    assert captured[0].constructor_config["calendar_identifier"]
    # The synthesized AdapterContext.config also carries member_id so
    # in-loop reads of ctx.config["member_id"] stay consistent.
    assert captured[0].start_ctx is not None
    assert captured[0].start_ctx.config["member_id"] == "member-bridge-carol"


def test_wrapper_propagates_factories_from_bridge_context(
    monkeypatch: Any,
) -> None:
    """The synthesized AdapterContext carries the bridge's
    correlation_id_factory + now_factory unchanged — the central
    adapter's emit sites mint ids via these factories."""
    captured = _swap_internal_with_capture(monkeypatch)

    wrapper = AppleCalendarBridgeAdapter()
    bridge_ctx = _make_bridge_ctx()

    asyncio.run(wrapper.start(ctx=bridge_ctx))

    synth = captured[0].start_ctx
    assert synth is not None
    assert synth.correlation_id_factory() == "corr-wrapper-test"
    assert synth.now_factory() == _FROZEN_NOW


# ===========================================================================
# A-5 sink resolution: prefer ctx.sink, fall back to IngestClientBridgeSink
# ===========================================================================


def test_wrapper_prefers_ctx_sink_when_present_per_a5(
    monkeypatch: Any,
) -> None:
    """A-5: when ``ctx.sink`` is set, the wrapper uses it directly
    rather than constructing a fresh :class:`IngestClientBridgeSink`.
    This is the seam tests inject fakes through."""
    captured = _swap_internal_with_capture(monkeypatch)

    injected = _FakeBridgeSink()
    wrapper = AppleCalendarBridgeAdapter()
    bridge_ctx = _make_bridge_ctx(sink=injected)

    asyncio.run(wrapper.start(ctx=bridge_ctx))

    synth = captured[0].start_ctx
    assert synth is not None
    assert synth.config["_bridge_sink"] is injected


def test_wrapper_falls_back_to_ingest_client_bridge_sink_when_ctx_sink_none(
    monkeypatch: Any,
) -> None:
    """A-5: when ``ctx.sink`` is ``None`` (the production default), the
    wrapper constructs an :class:`IngestClientBridgeSink` over
    ``ctx.ingest_client``."""
    captured = _swap_internal_with_capture(monkeypatch)

    wrapper = AppleCalendarBridgeAdapter()
    bridge_ctx = _make_bridge_ctx(sink=None)

    asyncio.run(wrapper.start(ctx=bridge_ctx))

    synth = captured[0].start_ctx
    assert synth is not None
    fallback_sink = synth.config["_bridge_sink"]
    assert isinstance(fallback_sink, IngestClientBridgeSink)


# ===========================================================================
# Lifecycle forwarding: stop() + status()
# ===========================================================================


def test_wrapper_forwards_stop_to_internal_adapter(monkeypatch: Any) -> None:
    """``stop()`` on the wrapper invokes ``stop()`` on the internal
    adapter so the supervisor's shutdown sweep is uniform across Cat-D
    and Cat-B bridge adapters."""
    captured = _swap_internal_with_capture(monkeypatch)

    wrapper = AppleCalendarBridgeAdapter()
    bridge_ctx = _make_bridge_ctx()

    asyncio.run(wrapper.start(ctx=bridge_ctx))
    assert captured[0].stopped is False

    asyncio.run(wrapper.stop())
    assert captured[0].stopped is True


def test_wrapper_stop_is_safe_before_start() -> None:
    """``stop()`` MUST be safe to call on a wrapper that never started
    — the bridge supervisor's shutdown sweep iterates every registered
    wrapper regardless of start state."""
    wrapper = AppleCalendarBridgeAdapter()
    asyncio.run(wrapper.stop())  # MUST NOT raise


def test_wrapper_status_forwards_to_internal_after_start(
    monkeypatch: Any,
) -> None:
    """``status()`` after a successful ``start()`` returns a bridge
    :class:`AdapterStatus` translated from the central adapter's
    status — same ``state`` + ``started_at`` + ``restart_count``."""
    _swap_internal_with_capture(monkeypatch)

    wrapper = AppleCalendarBridgeAdapter()
    bridge_ctx = _make_bridge_ctx()
    asyncio.run(wrapper.start(ctx=bridge_ctx))

    status = wrapper.status()
    assert status.state == "running"
    assert status.started_at == _FROZEN_NOW
    assert status.restart_count == 0
