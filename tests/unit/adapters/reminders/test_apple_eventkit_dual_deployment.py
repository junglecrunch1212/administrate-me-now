"""Framework-level tests for the Apple Reminders dual-deployment branch
(prompt 11b-ii-α-2 c2 implementation).

Closes prompt 11b-ii-α-2 §7 c3 deliverable 5a (framework
dual-deployment tests). Verifies that
``AppleEventKitAdapter.start(ctx)`` reads ``ctx.config["deployment_target"]``
and branches credential resolution + emit dispatch + the
``sharing_model_discriminator`` row value per [D18] / [D22] / memo 04
§2.2 / memo 05 §5.4:

  - ``"central"`` (default — F-2 byte-for-byte preserved): existing
    constructor-config credential source; ``_sharing_model_value ==
    "shared_central"``; ``_append`` routes via
    ``ctx.event_sink.write(...)``.
  - ``"bridge"``: ``_bridge_member_id`` overrides ``config["member_id"]``;
    ``_bridge_sink`` is required and is the emit destination;
    ``_sharing_model_value == "per_member_isolated"``; ``_append`` routes
    via the injected ``BridgeEventSink`` per memo 04 §2.2 / [§6.19].

F-4 (HARD): observation-mode suppression skips ALL emits uniformly —
the deployment_target branch does NOT alter the F-3 invariant from 11b-i
(neither legacy ``reminder.list_item.synced@v1`` nor canonical
``list_item.*@v1`` fires when the wrap returns ``suppressed=True``).
Both deployment paths share this rule.

F-5 (HARD): basename ``test_apple_eventkit_dual_deployment.py`` is
unique across the repo (verified against
``test_apple_eventkit_dual_emit.py`` / ``test_apple_eventkit_retrofit.py``
/ pack-internal siblings).
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.envelope import EventEnvelope

REPO_ROOT = Path(__file__).resolve().parents[4]
REMINDERS_PACK_DIR = REPO_ROOT / "packs" / "adapters" / "reminders" / "apple-eventkit"


def _load_apple_eventkit_module() -> Any:
    """Load ``adapter.py`` from the pack root by file path. Mirrors the
    sibling ``test_apple_eventkit_dual_emit.py`` loader; the importlib
    indirection lets monkeypatching ``observation_outbound`` in the
    module namespace exercise the [§6.20] wrap path."""
    spec = importlib.util.spec_from_file_location(
        "apple_eventkit_adapter_dual_deployment_under_test",
        REMINDERS_PACK_DIR / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_apple_eventkit_module()
AppleEventKitAdapter: Any = _adapter_mod.AppleEventKitAdapter


_FROZEN_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


class _FakeEventLog:
    """Captures typed asynchronous event-sink writes for the central path."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **_kwargs: Any) -> str:
        self.calls.append(envelope.model_dump())
        return f"event-{len(self.calls)}"


class _FakeBridgeSink:
    """Implements the :class:`BridgeEventSink` Protocol structurally;
    captures every envelope handed to ``emit`` so tests can assert the
    sink-injection contract."""

    def __init__(self) -> None:
        self.envelopes: list[EventEnvelope] = []

    async def emit(self, envelope: EventEnvelope) -> Any:
        # Import lazily so the test file has no hard dependency on the
        # sinks module's EmitResult shape (the Protocol only requires
        # ``async def emit(envelope) -> EmitResult``).
        from adminme_bridge.adapters.sinks import EmitResult

        self.envelopes.append(envelope)
        return EmitResult(ok=True, event_id="evt-bridge-1", error=None)


def _make_adapter() -> Any:
    """Construct an adapter with the minimal constructor config the
    central-path start() validates. Pack-level fixture state
    (``_ek_store``) is set by callers as needed."""
    return AppleEventKitAdapter(
        config={
            "target_list_identifier": "list-household-001",
            "member_id": "member-central",
        },
        ek_event_store_factory=lambda: object(),
        member_role_resolver=lambda _mid: "adult",
        max_outbound_cycles=0,
        max_inbound_cycles=0,
    )


def _make_ctx(
    *,
    event_log: _FakeEventLog | None = None,
    deployment_target: str | None = None,
    bridge_member_id: str | None = None,
    bridge_sink: Any = None,
    extra_config: dict[str, Any] | None = None,
) -> AdapterContext:
    config: dict[str, Any] = dict(extra_config or {})
    if deployment_target is not None:
        config["deployment_target"] = deployment_target
    if bridge_member_id is not None:
        config["_bridge_member_id"] = bridge_member_id
    if bridge_sink is not None:
        config["_bridge_sink"] = bridge_sink
    return AdapterContext(
        event_sink=event_log if event_log is not None else _FakeEventLog(),  # type: ignore[arg-type]
        config=config,
        correlation_id_factory=lambda: "corr-dual-dep",
        now_factory=lambda: _FROZEN_NOW,
    )


def _start(adapter: Any, ctx: AdapterContext) -> None:
    """Drive ``start()`` to completion. ``max_*_cycles=0`` makes the
    spawned poll loops exit immediately so the ``asyncio.gather`` returns
    without an explicit ``stop()``."""
    asyncio.run(adapter.start(ctx=ctx))


# ===========================================================================
# Deployment-target resolution + sharing-model-value branching
# ===========================================================================


def test_deployment_target_defaults_to_central_when_unspecified() -> None:
    """Backwards-compat F-2: when ``ctx.config`` omits ``deployment_target``,
    the adapter defaults to ``"central"`` and the existing 11b-i + 11r-c
    behavior is byte-for-byte preserved."""
    adapter = _make_adapter()
    ctx = _make_ctx()  # no deployment_target in config
    _start(adapter, ctx)
    assert adapter._deployment_target == "central"
    assert adapter._sharing_model_value == "shared_central"
    assert adapter._bridge_sink is None


def test_deployment_target_central_explicit_keeps_shared_central_value() -> None:
    """Explicitly setting ``deployment_target=central`` is semantically
    identical to the unset default (A-1)."""
    adapter = _make_adapter()
    ctx = _make_ctx(deployment_target="central")
    _start(adapter, ctx)
    assert adapter._deployment_target == "central"
    assert adapter._sharing_model_value == "shared_central"


def test_deployment_target_bridge_sets_per_member_isolated_value() -> None:
    """``deployment_target=bridge`` sets ``_sharing_model_value`` to
    ``"per_member_isolated"`` per memo 05 §2.5 / §5.4 — the row
    discriminator the future ``lists`` projection annotates per-row
    when both deployments share the projection."""
    adapter = _make_adapter()
    sink = _FakeBridgeSink()
    ctx = _make_ctx(
        deployment_target="bridge",
        bridge_member_id="member-bridge-alice",
        bridge_sink=sink,
    )
    _start(adapter, ctx)
    assert adapter._deployment_target == "bridge"
    assert adapter._sharing_model_value == "per_member_isolated"


def test_deployment_target_unknown_value_raises_value_error() -> None:
    """Unknown ``deployment_target`` enum value rejected with a typed
    ValueError — config validation runs before any side effect."""
    adapter = _make_adapter()
    ctx = _make_ctx(deployment_target="hybrid")
    with pytest.raises(ValueError, match="deployment_target"):
        _start(adapter, ctx)


# ===========================================================================
# Bridge deployment: credential synthesis + sink injection
# ===========================================================================


def test_deployment_target_bridge_uses_synthesized_member_id_from_config() -> None:
    """A-3: bridge deployment's ``_bridge_member_id`` overrides the
    constructor-config ``member_id`` so the adapter's ``self._config[
    "member_id"]`` resolves to the bridge daemon's per-member identity
    (the actual iCloud-grant member)."""
    adapter = _make_adapter()
    sink = _FakeBridgeSink()
    ctx = _make_ctx(
        deployment_target="bridge",
        bridge_member_id="member-bridge-bob",
        bridge_sink=sink,
    )
    _start(adapter, ctx)
    # The constructor config's "member-central" was overridden in start.
    assert adapter._config["member_id"] == "member-bridge-bob"


def test_deployment_target_bridge_requires_bridge_sink() -> None:
    """Bridge deployment without ``_bridge_sink`` in ``ctx.config`` is
    rejected — the wrapper is responsible for sink injection, and a
    missing sink would silently lose every emit."""
    adapter = _make_adapter()
    ctx = _make_ctx(
        deployment_target="bridge",
        bridge_member_id="member-bridge-c",
    )  # no bridge_sink
    with pytest.raises(ValueError, match="_bridge_sink"):
        _start(adapter, ctx)


def test_deployment_target_bridge_requires_member_id_string() -> None:
    """Bridge deployment with neither ``_bridge_member_id`` nor a
    config-level ``member_id`` is rejected — the per-member iCloud
    identity is load-bearing on the bridge path."""
    adapter = AppleEventKitAdapter(
        config={
            "target_list_identifier": "list-h",
            "member_id": "stub-required-for-construct",
        },
        ek_event_store_factory=lambda: object(),
        member_role_resolver=lambda _mid: "adult",
        max_outbound_cycles=0,
        max_inbound_cycles=0,
    )
    sink = _FakeBridgeSink()
    ctx = _make_ctx(
        deployment_target="bridge",
        bridge_sink=sink,
        extra_config={"_bridge_member_id": ""},
    )
    with pytest.raises(ValueError, match="_bridge_member_id"):
        _start(adapter, ctx)


# ===========================================================================
# Emit dispatch: central via event_log, bridge via sink
# ===========================================================================


def test_central_path_uses_event_log_append_for_emit() -> None:
    """In central mode ``_append`` routes to ``ctx.event_sink.write``
    — the existing 11b-i / 11r-c shape preserved byte-for-byte (F-2)."""
    adapter = _make_adapter()
    event_log = _FakeEventLog()
    ctx = _make_ctx(event_log=event_log)  # central default
    _start(adapter, ctx)
    event_log.calls.clear()  # drop the adapter.authenticated emit from start()

    asyncio.run(
        adapter._append(
            ctx=ctx,
            event_type="reminder.list_item.synced",
            payload={"member_id": "member-central", "direction": "outbound"},
            causation_id="evt-up-1",
        )
    )
    assert len(event_log.calls) == 1
    call = event_log.calls[0]
    assert call["type"] == "reminder.list_item.synced"
    assert call["causation_id"] == "evt-up-1"
    assert call["correlation_id"] == "corr-dual-dep"


def test_bridge_path_uses_injected_sink_for_emit() -> None:
    """In bridge mode ``_append`` routes through ``_append_via_bridge_sink``
    which builds an :class:`EventEnvelope` and dispatches via the
    injected sink (memo 04 §2.2)."""
    adapter = _make_adapter()
    sink = _FakeBridgeSink()
    ctx = _make_ctx(
        deployment_target="bridge",
        bridge_member_id="member-bridge-alice",
        bridge_sink=sink,
    )
    _start(adapter, ctx)
    sink.envelopes.clear()  # drop the adapter.authenticated emit from start()

    async def _drive_emit() -> None:
        await adapter._append(
            ctx=ctx,
            event_type="reminder.list_item.synced",
            payload={"member_id": "member-bridge-alice", "direction": "outbound"},
            causation_id="evt-up-2",
        )
        # The bridge path fires-and-forgets via loop.create_task; yield
        # to the event loop to let the dispatch coroutine run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_drive_emit())

    assert len(sink.envelopes) == 1
    envelope = sink.envelopes[0]
    assert envelope.type == "reminder.list_item.synced"
    assert envelope.causation_id == "evt-up-2"
    assert envelope.correlation_id == "corr-dual-dep"
    # Per memo 04 §2.2: provisional owner_scope reflects the per-member
    # bridge identity; central :3337 router re-stamps from Tailscale.
    assert envelope.owner_scope == "private:member-bridge-alice"


def test_bridge_path_emit_carries_provisional_private_owner_scope() -> None:
    """Memo 04 §2.2: bridge-side emit stamps a provisional
    ``private:<member_id>`` owner_scope. The central :3337 ingest router
    is authoritative and re-stamps from Tailscale identity post-receipt
    — but the provisional value on the wire is the per-member scope."""
    adapter = _make_adapter()
    sink = _FakeBridgeSink()
    ctx = _make_ctx(
        deployment_target="bridge",
        bridge_member_id="member-bridge-carol",
        bridge_sink=sink,
    )
    _start(adapter, ctx)
    sink.envelopes.clear()

    async def _drive() -> None:
        await adapter._append(
            ctx=ctx,
            event_type="list_item.added",
            payload={"member_id": "member-bridge-carol"},
            causation_id=None,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_drive())
    assert sink.envelopes[0].owner_scope == "private:member-bridge-carol"
    assert sink.envelopes[0].visibility_scope == "private:member-bridge-carol"


def test_bridge_path_does_not_invoke_central_writer() -> None:
    """F-3: bridge transport leaves the injected central writer unused."""
    adapter = _make_adapter()
    sink = _FakeBridgeSink()
    event_sink = _FakeEventLog()
    # A typed central sink remains present but bridge transport is authoritative.
    ctx = AdapterContext(
        event_sink=event_sink,
        config={
            "deployment_target": "bridge",
            "_bridge_member_id": "member-bridge-dave",
            "_bridge_sink": sink,
        },
        correlation_id_factory=lambda: "corr-bridge-only",
        now_factory=lambda: _FROZEN_NOW,
    )
    _start(adapter, ctx)
    sink.envelopes.clear()

    async def _drive() -> None:
        await adapter._append(
            ctx=ctx,
            event_type="list_item.added",
            payload={"member_id": "member-bridge-dave"},
            causation_id=None,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_drive())
    assert len(sink.envelopes) == 1
    assert event_sink.calls == []


# ===========================================================================
# F-4 invariant: observation-suppression skips ALL emits uniformly
# ===========================================================================


def test_dual_deployment_observation_suppressed_skips_all_emits_uniformly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-4 (HARD): when ``observation_outbound`` returns
    ``OutboundResult(suppressed=True)``, ``_apply_write_impl`` returns
    ``WriteResult(applied=False, suppressed=True)`` and
    ``_write_one_outbound`` early-returns before EITHER emit fires —
    regardless of ``deployment_target``. Pinned for BOTH central and
    bridge deployments to guarantee the suppression invariant from
    11b-i F-3 is uniform across the deployment branch."""

    async def _suppressing_outbound(
        _session: Any,
        _action: str,
        _payload: dict[str, Any],
        _action_fn: Any,
        **_kwargs: Any,
    ) -> Any:
        from adminme.lib.observation import OutboundResult

        return OutboundResult(suppressed=True, result=None)

    monkeypatch.setattr(_adapter_mod, "observation_outbound", _suppressing_outbound)

    # ----- central deployment -----
    adapter_c = _make_adapter()
    adapter_c._observation_session = object()
    adapter_c._observation_manager = object()
    adapter_c._observation_event_log = object()
    event_log = _FakeEventLog()
    ctx_c = _make_ctx(event_log=event_log)
    _start(adapter_c, ctx_c)
    event_log.calls.clear()  # drop the adapter.authenticated emit

    # ----- bridge deployment -----
    adapter_b = _make_adapter()
    adapter_b._observation_session = object()
    adapter_b._observation_manager = object()
    adapter_b._observation_event_log = object()
    sink = _FakeBridgeSink()
    ctx_b = _make_ctx(
        deployment_target="bridge",
        bridge_member_id="member-bridge-suppress",
        bridge_sink=sink,
    )
    _start(adapter_b, ctx_b)
    sink.envelopes.clear()  # drop the adapter.authenticated emit

    # Drive each adapter's outbound write through one observation-suppressed
    # path. The shape of the WriteOperation matches the one
    # _write_one_outbound builds internally; we exercise it through the
    # ABC gate so the suppressed-branch return runs.
    from adminme.lib.adapters import WriteOperation

    async def _attempt_suppressed_write(adapter: Any) -> Any:
        operation = WriteOperation(
            kind="create_reminder",
            target_external_id=None,
            payload={
                "member_id": "member-x",
                "target_list_id": "list-household-001",
                "target_list_name": "AdministrateMe Household",
                "title": "Pick up groceries",
                "notes": None,
                "due_at": None,
                "priority": 1,
            },
        )
        # _apply_write_impl returns directly; we don't need apply_write
        # (the [UT-27] gate) to assert the suppression contract here.
        return await adapter._apply_write_impl(operation)

    # Need an EKEventStore fixture for _apply_write_impl to reach the
    # observation wrap (rather than its early return on ek_store=None).
    class _FakeEKStore:
        def __init__(self) -> None:
            self.saves: list[Any] = []

        def build_reminder(self, **_kw: Any) -> Any:
            class _R:
                calendarItemIdentifier = "EKID-suppress"

            return _R()

        def save(self, _r: Any, *, commit: bool) -> None:  # noqa: ARG002
            self.saves.append((commit,))

        def list_name_for_identifier(self, lid: str) -> str:
            return lid

    store_c = _FakeEKStore()
    adapter_c._ek_store = store_c
    result_c = asyncio.run(_attempt_suppressed_write(adapter_c))
    assert result_c.applied is False
    assert result_c.suppressed is True
    # Central path: no emits fired; the wrapped save did NOT execute.
    assert event_log.calls == []
    assert store_c.saves == []

    store_b = _FakeEKStore()
    adapter_b._ek_store = store_b
    result_b = asyncio.run(_attempt_suppressed_write(adapter_b))
    assert result_b.applied is False
    assert result_b.suppressed is True
    # Bridge path: no envelopes dispatched to the sink; save suppressed.
    assert sink.envelopes == []
    assert store_b.saves == []
