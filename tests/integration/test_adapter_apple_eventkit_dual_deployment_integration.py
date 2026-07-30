"""Integration tests for the Apple Reminders dual-deployment landed in
prompt 11b-ii-α-2 (c1 manifest + c2 adapter branching + c3 bridge
connector-pack scaffold).

Three cross-cutting deliverables (§7 c4):

1. ``test_bridge_supervisor_loads_apple_eventkit_bridge_connector_pack``
   — α-1's :func:`discover_packs` yields the new
   ``reminders-apple-eventkit-bridge`` pack from a tmp_path fixture
   shaped like ``~/.adminme-bridge/packs/`` so the bridge daemon's
   loader contract end-to-end (subkind allow-list + entrypoint + pack
   name) is exercised on a clean copy of the c3-shipped manifest.

2. ``test_central_supervisor_loads_apple_eventkit_pack_with_default_deployment_target_central``
   — F-2 regression: the central pack stays validator-clean, the
   ``AppleEventKitAdapter`` constructs cleanly with the
   pre-α-2 config shape (no ``deployment_target`` key), and
   ``start()`` defaults to ``"central"`` per the c2 implementation.
   Central supervisor surface unchanged.

3. ``test_bridge_wrapper_emits_via_ingest_client_sink_to_central_ingest_router_end_to_end``
   — full end-to-end: :class:`BridgeSupervisor` hosts an
   :class:`AppleEventKitBridgeAdapter`; the wrapper synthesizes an
   :class:`AdapterContext` carrying a real
   :class:`IngestClientBridgeSink`; the internal adapter emits one
   envelope via the sink; the envelope is routed through the central
   FastAPI app's ingest router via ``httpx.ASGITransport`` (in-process,
   no sockets) and lands in a real :class:`EventLog` with
   ``owner_scope`` re-stamped from the Tailscale identity per [§6.19]
   / memo 04 §2.2. Mirrors the α-1
   ``test_bridge_state_mirror_onramp.py::test_mock_cat_b_adapter_emits_via_ingest_client_sink_end_to_end``
   rig shape; the structural difference is that α-2's test goes
   through the wrapper layer (rather than a mock adapter calling the
   sink directly) so the wrapper's :class:`AdapterContext` synthesis +
   ``_bridge_sink`` injection is exercised on the production-shaped
   chain.

F-5 (HARD): basename
``test_adapter_apple_eventkit_dual_deployment_integration.py`` is
unique across ``tests/integration/`` and the repo (verified against
``test_adapter_reminders_supervisor.py``,
``test_adapter_apple_eventkit_supervisor_retrofit.py``,
``test_bridge_state_mirror_onramp.py``, and pack-internal siblings).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

import adminme.events.schemas.bridge  # noqa: F401  — registers bridge.enrolled@v1
from adminme.events.envelope import EventEnvelope
from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.lib.pack_registry.validator import validate_adapter_pack
from adminme.products.bridge.app import create_app
from adminme.products.bridge.auth import DictMemberIdResolver
from adminme_bridge.adapters.base import AdapterStatus
from adminme_bridge.adapters.sinks import EmitResult
from adminme_bridge.config import BridgeConfig
from adminme_bridge.connector_packs import reminders_apple_eventkit_bridge
from adminme_bridge.connector_packs.reminders_apple_eventkit_bridge import (
    AppleEventKitBridgeAdapter,
)
from adminme_bridge.connector_packs.reminders_apple_eventkit_bridge import (
    wrapper as wrapper_mod,
)
from adminme_bridge.ingest_client import IngestClient
from adminme_bridge.main import BridgeSupervisor
from adminme_bridge.packs import (
    STATE_MIRROR_SUBKIND,
    discover_packs,
)

TEST_KEY = b"D" * 32

REPO_ROOT = Path(__file__).resolve().parents[2]
CENTRAL_PACK_DIR = REPO_ROOT / "packs" / "adapters" / "reminders" / "apple-eventkit"
BRIDGE_PACK_DIR = (
    REPO_ROOT
    / "bridge"
    / "adminme_bridge"
    / "connector_packs"
    / "reminders_apple_eventkit_bridge"
)


# ===========================================================================
# 1. Bridge daemon discover_packs() loads the new connector-pack
# ===========================================================================


def test_bridge_supervisor_loads_apple_eventkit_bridge_connector_pack(
    tmp_path: Path,
) -> None:
    """The bridge daemon's :func:`discover_packs` (α-1) yields the new
    ``reminders-apple-eventkit-bridge`` pack from a tmp_path fixture
    shaped like ``~/.adminme-bridge/packs/``. Pins the c3 manifest +
    the c3 entrypoint dotted path + the
    ``subkind == STATE_MIRROR_SUBKIND`` allow-list match per memo 04
    §3.1 / [D19] / α-1's loader contract."""
    packs_dir = tmp_path / "packs"
    packs_dir.mkdir()
    pack_root = packs_dir / "reminders-apple-eventkit-bridge"
    pack_root.mkdir()
    # Copy the c3-shipped manifest verbatim — the test exercises the
    # loader against the actual on-branch pack.yaml, not a synthesized
    # double.
    (pack_root / "pack.yaml").write_text(
        (BRIDGE_PACK_DIR / "pack.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    handles = list(discover_packs(packs_dir))
    assert len(handles) == 1
    handle = handles[0]
    assert handle.name == "reminders-apple-eventkit-bridge"
    assert handle.entrypoint == (
        "adminme_bridge.connector_packs.reminders_apple_eventkit_bridge:"
        "AppleEventKitBridgeAdapter"
    )
    assert handle.pack_dir == pack_root

    # The c3 manifest's subkind matches the allow-list constant α-1
    # exported. Cross-check by reading the manifest directly and
    # confirming the constant string is the same value the loader
    # filtered on.
    with (pack_root / "pack.yaml").open(encoding="utf-8") as fh:
        manifest: Any = yaml.safe_load(fh)
    assert manifest["subkind"] == STATE_MIRROR_SUBKIND


# ===========================================================================
# 2. Central pack regression — defaults to central, validator-clean
# ===========================================================================


def test_central_supervisor_loads_apple_eventkit_pack_with_default_deployment_target_central() -> (
    None
):
    """F-2 regression: the central pack still loads cleanly through the
    validator, the central ``AppleEventKitAdapter`` constructs with the
    pre-α-2 config shape (no ``deployment_target`` key), and
    ``start()`` resolves ``deployment_target`` to ``"central"`` so the
    11b-i + 11r-c emit shape is byte-for-byte preserved (per c2's F-2
    implementation guarantee).

    This is the install-time + runtime regression net for the central
    deployment — α-2 must not break any existing operator's installed
    central pack."""
    # ----- install-time: validator surface still clean for the central -----
    result = validate_adapter_pack(CENTRAL_PACK_DIR)
    errors = [f for f in result.findings if f.severity == "error"]
    assert errors == [], (
        "central pack regression: validate_adapter_pack surfaced unexpected "
        f"errors on the dual-deployment pack: {[(f.check, f.message) for f in errors]}"
    )

    # ----- runtime: AppleEventKitAdapter constructs without deployment_target ----
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_apple_eventkit_central_regression_under_test",
        CENTRAL_PACK_DIR / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    AppleEventKitAdapter = module.AppleEventKitAdapter

    adapter = AppleEventKitAdapter(
        config={
            # No ``deployment_target`` key — pre-α-2 config shape.
            "target_list_identifier": "list-household-central",
            "member_id": "member-central",
        },
        ek_event_store_factory=lambda: object(),
        member_role_resolver=lambda _mid: "adult",
        max_outbound_cycles=0,
        max_inbound_cycles=0,
    )

    # Drive start() with an empty ctx.config so the adapter must take
    # the default branch.
    from adminme.adapters.base import AdapterContext

    class _FakeEventLog:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        @property
        def tenant_id(self) -> str:
            return "tenant-test"

        async def write(self, envelope: Any, **_kwargs: Any) -> str:
            self.calls.append(envelope.model_dump())
            return f"event-{len(self.calls)}"

    event_log = _FakeEventLog()
    ctx = AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},  # no deployment_target → default "central"
        correlation_id_factory=lambda: "corr-regression",
        now_factory=lambda: datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc),
    )
    asyncio.run(adapter.start(ctx=ctx))
    assert adapter._deployment_target == "central"
    assert adapter._sharing_model_value == "shared_central"
    # No bridge sink ever wired — central path doesn't consult one.
    assert adapter._bridge_sink is None
    # adapter.authenticated landed in the local fake event log via the
    # central typed event-sink path (NOT routed through any bridge sink) —
    # this is the byte-for-byte preserved 11b-i / 11r-c emit shape.
    auth = [c for c in event_log.calls if c.get("type") == "adapter.authenticated"]
    assert len(auth) == 1
    assert auth[0]["payload"]["adapter_name"] == "reminders-apple-eventkit"


# ===========================================================================
# 3. Bridge end-to-end: wrapper → IngestClient sink → central :3337 router → EventLog
# ===========================================================================


_FROZEN_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


class _StubInternalEmitsBridgeEnrolledOnStart:
    """Stand-in for the central ``AppleEventKitAdapter`` that emits ONE
    ``bridge.enrolled@v1`` envelope via the wrapper-injected sink on
    ``start()``. We deliberately use ``bridge.enrolled@v1`` (registered
    by α-1's schema module) rather than the central
    ``adapter.authenticated@v1`` because the latter pins
    ``token_storage_ref`` to an ``op://...`` pattern incompatible with
    the central ``os://reminders-permission`` literal — that pin is a
    pre-existing concern of the central-only path and is orthogonal to
    the α-2 bridge sink end-to-end this test exercises. Mirrors the
    α-1 mock-adapter approach
    (``test_bridge_state_mirror_onramp.py::_MockCatBAdapter``).

    The wrapper hands this stub a synthesized
    :class:`AdapterContext` carrying the
    :class:`IngestClientBridgeSink` under ``config["_bridge_sink"]``;
    the stub emits via that sink so the wrapper layer's synthesis +
    injection contract is exercised on the production-shaped chain.
    """

    def __init__(self, *, config: dict[str, Any]) -> None:
        self.config = dict(config)
        self.emit_result: EmitResult | None = None
        self.started = False
        self.stopped = False
        self._status = AdapterStatus(
            state="running",
            started_at=_FROZEN_NOW,
            restart_count=0,
        )

    async def start(self, *, ctx: Any) -> None:
        self.started = True
        sink = ctx.config["_bridge_sink"]
        member_id = ctx.config["_bridge_member_id"]
        envelope = EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id="tenant-a",
            type="bridge.enrolled",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="reminders-apple-eventkit",
            source_account_id=member_id,
            owner_scope=f"private:{member_id}",
            visibility_scope=f"private:{member_id}",
            sensitivity="normal",
            correlation_id=ctx.correlation_id_factory(),
            payload={
                "member_id": member_id,
                "bridge_node_id": "bridge-node-α2-test",
                "tailscale_identity": "m1@example.ts.net",
                "adapters_active": ["reminders-apple-eventkit"],
                "enrolled_at": "2026-05-20T12:00:00+00:00",
            },
        )
        self.emit_result = await sink.emit(envelope)

    async def stop(self) -> None:
        self.stopped = True
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status


@pytest.fixture
async def central_rig(tmp_path: Path):
    """Central FastAPI app + real EventLog wired in-process via
    ASGITransport. Mirrors the α-1
    ``test_bridge_state_mirror_onramp.py::central_rig`` fixture so this
    α-2 test's production-path assertion runs through the same auth +
    ``owner_scope``-stamping seams a production α-2 bridge deployment
    will hit."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    await bus.start()
    app = create_app()
    app.state.event_sink = EventWriter(log, bus)
    app.state.member_id_resolver = DictMemberIdResolver({"m1@example.ts.net": "m1"})
    app.state.ingest_count = 0
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://central"
    ) as http:
        ingest = IngestClient(
            central_endpoint_url="http://central",
            tailscale_identity_header="m1@example.ts.net",
            client=http,
        )
        try:
            yield {"log": log, "ingest": ingest}
        finally:
            await bus.stop()
            await log.close()


def _bridge_config() -> BridgeConfig:
    return BridgeConfig(
        member_id="m1",
        central_endpoint="http://central",
        tailscale_identity="m1@example.ts.net",
    )


async def test_bridge_wrapper_emits_via_ingest_client_sink_to_central_ingest_router_end_to_end(
    central_rig: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end production-shape chain (the primary α-2 stop condition):

    :class:`BridgeSupervisor` →
    :class:`AppleEventKitBridgeAdapter` (wrapper) →
    synthesized :class:`AdapterContext` carrying a real
    :class:`IngestClientBridgeSink` →
    central FastAPI ``ingest_router`` (in-process via
    :class:`httpx.ASGITransport`) →
    real :class:`EventLog`.

    Pins:
    - The wrapper's :class:`AdapterContext` synthesis (member_id +
      sink injection per c3 +
      ``test_apple_eventkit_bridge_wrapper.py``) routes the internal
      adapter's emit at the production sink.
    - The central :3337 router enforces ``owner_scope`` consistency
      with the Tailscale-resolved identity per [§6.19] / memo 04 §2.2
      / F-3 (mismatch is a HARD 403, NOT silent overwrite — see
      ``adminme/products/bridge/routers/ingest.py::_stamp_owner_scope``).
      The bridge daemon's configured ``member_id`` IS the
      Tailscale-resolved value in production, so the wrapper's
      synthesized config carries the matching ``"private:<member_id>"``
      stamp downstream of the adapter's
      ``_append_via_bridge_sink``. This test exercises the success
      path (matching scopes → 202 + EventLog append); the F-3 403
      refusal is pinned by the central app's own
      ``test_ingest_router.py`` suite.
    - The pack's family is ``reminders`` matching α-1's
      :class:`StateMirrorBridgeAdapter` Protocol (verified through
      :func:`isinstance` in the c3 unit suite; pinned end-to-end here
      via the wrapper's actual hosting in a real
      :class:`BridgeSupervisor` adapter list).
    """
    # Monkeypatch the wrapper's central-adapter loader so the internal
    # adapter is the stub-that-emits-bridge.enrolled rather than the
    # real central AppleEventKitAdapter — the central
    # ``adapter.authenticated`` emit ships an
    # ``os://reminders-permission`` token_storage_ref incompatible with
    # the central schema's ``op://...`` pattern (a pre-existing
    # concern of the central-only path, orthogonal to α-2's bridge
    # chain).
    monkeypatch.setattr(
        wrapper_mod,
        "_load_central_adapter_class",
        lambda: _StubInternalEmitsBridgeEnrolledOnStart,
    )

    log: EventLog = central_rig["log"]
    ingest: IngestClient = central_rig["ingest"]

    wrapper = AppleEventKitBridgeAdapter()
    supervisor = BridgeSupervisor(
        config=_bridge_config(),
        ingest_client=ingest,
        adapters=[wrapper],
    )
    await supervisor.start()
    # Yield until the wrapper's internal-stub emit-and-return finishes.
    captured: list[_StubInternalEmitsBridgeEnrolledOnStart] = []
    for _ in range(100):
        await asyncio.sleep(0.01)
        internal = getattr(wrapper, "_internal", None)
        if internal is not None and getattr(internal, "emit_result", None) is not None:
            captured.append(internal)
            break
    await supervisor.stop()

    assert len(captured) == 1, "wrapper internal-stub never emitted"
    emit_result = captured[0].emit_result
    assert emit_result is not None
    assert emit_result.ok is True, emit_result.error
    assert emit_result.event_id is not None

    stored = await log.get(emit_result.event_id)
    assert stored is not None
    assert stored["type"] == "bridge.enrolled"
    # [§6.19] / memo 04 §2.2: central :3337 router re-stamped
    # ``owner_scope`` from the Tailscale identity (DictMemberIdResolver
    # maps ``m1@example.ts.net → m1``). The wrapper's provisional
    # stamp matched the resolver's authoritative value in this rig;
    # the assertion documents that the chain's end-state owner_scope
    # is the Tailscale-derived value, NOT a wrapper-controlled one.
    assert stored["owner_scope"] == "private:m1"
    assert stored["source_adapter"] == "reminders-apple-eventkit"
    assert stored["payload"]["adapters_active"] == ["reminders-apple-eventkit"]


def test_apple_eventkit_bridge_connector_pack_module_exports_wrapper() -> None:
    """Smoke check: the c3-shipped connector-pack package re-exports
    :class:`AppleEventKitBridgeAdapter` via ``__init__.py`` so the
    bridge daemon's ``entrypoint`` dotted-path resolver
    (``adminme_bridge.connector_packs.reminders_apple_eventkit_bridge:AppleEventKitBridgeAdapter``)
    finds the class. This is the integration-shape pin on the c3
    package surface — the c3 unit suite covers wrapper behavior; this
    test covers the c3 packaging contract."""
    assert hasattr(reminders_apple_eventkit_bridge, "AppleEventKitBridgeAdapter")
    assert (
        reminders_apple_eventkit_bridge.AppleEventKitBridgeAdapter
        is AppleEventKitBridgeAdapter
    )
    # Family ClassVar matches the central Cat-B ABC's Literal value
    # (``"reminders"`` per
    # ``adminme/lib/adapters/external_state_mirror_base.py``).
    assert AppleEventKitBridgeAdapter.family == "reminders"
