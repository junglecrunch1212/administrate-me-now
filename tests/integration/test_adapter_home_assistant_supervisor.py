"""Cross-module Cat-C+E integration tests for the Home Assistant adapter
(prompt 11g §7 c4 deliverable 2 — first canonical Cat-E reference
implementation; multi-capability Cat-C reads + Cat-E writes in the same
pack per [PM-35] / [D24]; default-CI sibling to the framework + pack-internal
suites).

Sits alongside ``test_adapter_google_calendar_supervisor.py`` (11b-iii first
fresh Cat-B) and ``test_adapter_google_tasks_supervisor.py`` (11b-iv) as the
first integration test file for a fresh Cat-E canonical-build pack. Unlike
the Cat-B siblings, Home Assistant declares TWO capability blocks in the
same manifest — Cat-E ``execute_action`` for ``ha.service_call`` POSTs +
Cat-C inbound ``_poll_states_once`` GETs — and the three tests in this file
pin the contract surface the supervisor's loader path observes for a fresh
Cat-E multi-capability pack:

- the supervisor's loader (file-path importlib — same path the supervisor
  uses at runtime because ``home-assistant``'s hyphen forbids a
  ``from packs...home-assistant.adapter`` import) produces a class that
  inherits from :class:`OutboundActionAdapter` and from none of the other
  four ABCs ([PM-34] mutual-exclusion). Even though the pack declares
  Cat-C in its capabilities list, the inherited ABC is the Cat-E one
  per [PM-34] (inherit ONE ABC, declare many capabilities);

- the Cat-E ``execute_action`` dispatch through the loaded class rejects
  any ``kind`` other than ``"ha.service_call"`` via ``ValueError`` BEFORE
  the HA REST API is touched (defense-in-depth gate; Cat-E has no
  ``write_capabilities`` surface, so the kind check is in-pack — F-3
  for Cat-E mirrors the Cat-B [UT-27] gate);

- the Cat-C inbound poll's ``_poll_states_once`` emits
  ``ha.state_changed@v1`` on first observation of an HA ``entity_id``
  with a payload that validates byte-for-byte against the on-main
  :class:`HaStateChangedV1` schema. Pins F-2 (MAP DON'T REGISTER) at
  the integration seam — the schema-source layer is pinned by the c1
  registry-lookup test in the framework suite, the pack layer by the
  pack-internal test, and this layer pins the same contract through
  the supervisor's loader path. Re-poll on the same
  ``(entity_id, last_changed)`` pair is a no-op per the inbound dedupe
  gate (F-3 of c2).

Cat-E specifics vs the Cat-B siblings: Cat-E has no ``write_capabilities``
gate (that's Cat-B's [UT-27] check); Cat-E discriminates the outbound
surface via :class:`ActionRequest`'s ``kind`` field in-pack. The
suppression contract (observation seam slots populated →
``OutboundResult(suppressed=True)`` returns ``ActionResult(executed=False,
suppressed=True)`` AND emits NEITHER ``action.executed`` NOR
``action.failed`` per F-3 HARD) is exercised in the framework test;
this integration sibling exercises the Phase A (seams unset, direct
call) path through the loader to pin the contract on the instance the
supervisor would actually run.

``packs/adapters/automation/home-assistant`` includes a hyphen in the
final directory segment and is therefore not a valid Python package
name; the adapter class is loaded via the file-path importlib pattern
shared with the framework test at
``tests/unit/adapters/automation/test_home_assistant.py`` and the
pack-internal test at
``packs/adapters/automation/home-assistant/tests/test_home_assistant_pack_internal.py``.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.schemas.automation import HaStateChangedV1
from adminme.lib.adapters import (
    ActionRequest,
    CommunicationAdapter,
    ExternalStateMirrorAdapter,
    InboundDataAdapter,
    OutboundActionAdapter,
    PersonalKnowledgeAdapter,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HA_PACK_ROOT = (
    REPO_ROOT / "packs" / "adapters" / "automation" / "home-assistant"
)


def _load_home_assistant_module() -> Any:
    """Load ``adapter.py`` from the pack root by file path. Mirrors the
    supervisor's runtime loader: ``packs/`` is not a Python package under
    poetry's config, and ``home-assistant``'s hyphen forbids a
    ``from packs...home-assistant.adapter`` statement entirely."""
    spec = importlib.util.spec_from_file_location(
        "home_assistant_adapter_integration_under_test",
        HA_PACK_ROOT / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_home_assistant_module()
HomeAssistantAdapter: Any = _adapter_mod.HomeAssistantAdapter


_INTEGRATION_FROZEN_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


class _IntegrationEventLog:
    """Captures synchronous ``append_sync`` calls for assertion. Mirrors
    the duck-typed contract :meth:`HomeAssistantAdapter._append` reads
    via ``getattr(ctx.event_log, "append_sync", ...)``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append_sync(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _make_integration_ctx(event_log: _IntegrationEventLog) -> AdapterContext:
    return AdapterContext(
        event_log=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-integration-ha-1",
        now_factory=lambda: _INTEGRATION_FROZEN_NOW,
    )


class _IntegrationResponse:
    """httpx-style response — status_code + headers + raise_for_status +
    json."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:  # pragma: no cover — error paths in c3
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._json_data


class _IntegrationAsyncClient:
    """Minimal httpx.AsyncClient duck-type used by the supervisor-loaded
    adapter. ``get`` / ``post`` return pre-canned responses; ``aclose``
    is a no-op coroutine."""

    def __init__(
        self,
        *,
        get_response: _IntegrationResponse | None = None,
        post_response: _IntegrationResponse | None = None,
    ) -> None:
        self._get_response = get_response
        self._post_response = post_response
        self.get_calls: list[str] = []
        self.post_calls: list[dict[str, Any]] = []

    async def get(self, path: str) -> _IntegrationResponse:
        self.get_calls.append(path)
        assert self._get_response is not None
        return self._get_response

    async def post(
        self, path: str, *, json: Any = None
    ) -> _IntegrationResponse:
        self.post_calls.append({"path": path, "json": json})
        assert self._post_response is not None
        return self._post_response

    async def aclose(self) -> None:
        return None


def _make_adapter(
    *, client: _IntegrationAsyncClient | None = None,
) -> Any:
    """Construct a :class:`HomeAssistantAdapter` skipping ``start()``.

    The full lifecycle (start / stop / poll loop) lives in a future
    ``test_adapter_home_assistant_supervisor_live.py`` under
    ``requires_live_services``. The three tests here only pin the loader
    + ABC + dispatch + emit contract at the integration seam, so the
    httpx client is seeded directly via ``self._client`` without going
    through the token resolver."""
    adapter = HomeAssistantAdapter(
        config={
            "access_token_ref": "op://test/v/ha/token",
            "base_url": "http://homeassistant.local:8123",
        },
    )
    if client is not None:
        adapter._client = client
    return adapter


# ===========================================================================
# 1) [PM-34] mutual-exclusion + Cat-E inheritance through the loader.
# ===========================================================================


def test_supervisor_loaded_adapter_inherits_outbound_action_adapter() -> None:
    """The supervisor's adapter-loading code path (file-path importlib —
    same path the supervisor uses at runtime) yields a class that
    inherits from :class:`OutboundActionAdapter` and is NOT a subclass of
    any of the other four ABCs per [PM-34]. The pack ALSO declares a
    Cat-C capability in its multi-capability manifest per [PM-35] /
    [D24], but the inherited ABC is the Cat-E one (the [PM-34] rule is
    INHERIT one ABC, DECLARE many capabilities). The validator's
    ``base_class_conformance`` check observes this against
    ``capabilities[0]`` (which is Cat-E for this pack) via AST walk; this
    integration test pins the same contract at the runtime-loader
    seam."""
    assert issubclass(HomeAssistantAdapter, OutboundActionAdapter)
    # [PM-34] mutual-exclusion across the 5 ABCs.
    assert not issubclass(HomeAssistantAdapter, CommunicationAdapter)
    assert not issubclass(HomeAssistantAdapter, ExternalStateMirrorAdapter)
    assert not issubclass(HomeAssistantAdapter, InboundDataAdapter)
    assert not issubclass(HomeAssistantAdapter, PersonalKnowledgeAdapter)


# ===========================================================================
# 2) Cat-E execute_action kind-dispatch gate through the loader.
# ===========================================================================


def test_supervisor_execute_action_kind_gate_through_loader() -> None:
    """The pack's ``execute_action`` kind-dispatch gate is what makes the
    Cat-E outbound surface explicit at the in-pack layer (Cat-E has no
    framework ``write_capabilities`` gate — that's Cat-B's [UT-27]). The
    only declared outbound kind is ``"ha.service_call"``; any other kind
    raises :class:`ValueError` BEFORE the HA REST POST is touched.
    The framework test pins the gate at the class-shape layer; this
    integration test pins it on the instance the supervisor would
    actually run, closing the loop between the contract
    (``ACTION_KIND`` ClassVar + in-pack kind check) and the supervisor's
    runtime invariant. The HA REST API is NOT touched — the gate rejects
    the request BEFORE the impl is dispatched."""
    client = _IntegrationAsyncClient(
        post_response=_IntegrationResponse(status_code=200, json_data=[])
    )
    adapter = _make_adapter(client=client)
    event_log = _IntegrationEventLog()
    adapter._ctx = _make_integration_ctx(event_log)

    bad_request = ActionRequest(
        kind="ha.script.run",
        target_external_id=None,
        payload={"script_id": "morning_routine"},
        correlation_id="corr-bad-kind",
    )
    with pytest.raises(ValueError):
        asyncio.run(adapter.execute_action(bad_request))
    # External API was NOT touched.
    assert client.post_calls == []

    # Defense-in-depth: another undeclared kind ("ha.event.fire") is
    # also rejected by the same gate.
    another_bad = ActionRequest(
        kind="ha.event.fire",
        target_external_id=None,
        payload={"event_type": "test_event"},
        correlation_id="corr-bad-kind-2",
    )
    with pytest.raises(ValueError):
        asyncio.run(adapter.execute_action(another_bad))
    assert client.post_calls == []


# ===========================================================================
# 3) Cat-C inbound poll emits ``ha.state_changed@v1`` via the loader path.
# ===========================================================================


def test_supervisor_inbound_poll_emits_ha_state_changed() -> None:
    """The supervisor-loaded ``HomeAssistantAdapter`` (loaded via the
    file-path importlib pattern the supervisor uses at runtime) emits
    ``ha.state_changed@v1`` on first observation of an HA ``entity_id``,
    with a payload that validates byte-for-byte against the on-main
    :class:`HaStateChangedV1` schema. F-2 (MAP DON'T REGISTER) pinned
    at the integration seam: any future loader-side regression that
    mangles the import surface or the module-load registration would
    fail here first. ``extra="forbid"`` on the schema means any drift
    introducing a new field would fail validation; the ``min_length=1``
    on ``entity_id`` / ``state`` would reject empties; the
    ``Literal["home_assistant"]`` on ``source`` pins the source
    discriminant. Re-poll on the same ``(entity_id, last_changed)``
    pair is a no-op per the inbound dedupe gate (F-3 of c2) — the
    second emit count is 0 and the event-log call list is unchanged."""
    ha_state = {
        "entity_id": "light.kitchen",
        "state": "on",
        "attributes": {"brightness": 220, "friendly_name": "Kitchen Light"},
        "last_changed": "2026-05-18T11:00:00+00:00",
        "last_updated": "2026-05-18T11:00:05+00:00",
    }
    client = _IntegrationAsyncClient(
        get_response=_IntegrationResponse(
            status_code=200, json_data=[ha_state]
        )
    )
    adapter = _make_adapter(client=client)
    event_log = _IntegrationEventLog()
    ctx = _make_integration_ctx(event_log)
    adapter._ctx = ctx

    emitted = asyncio.run(adapter._poll_states_once(ctx=ctx))

    assert emitted == 1
    state_calls = [
        c for c in event_log.calls if c["type"] == "ha.state_changed"
    ]
    assert len(state_calls) == 1
    payload = state_calls[0]["payload"]
    instance = HaStateChangedV1.model_validate(payload)
    assert instance.entity_id == "light.kitchen"
    assert instance.state == "on"
    assert instance.source == "home_assistant"
    assert instance.attributes["brightness"] == 220
    assert instance.last_changed == datetime(
        2026, 5, 18, 11, 0, 0, tzinfo=timezone.utc
    )
    assert instance.last_updated == datetime(
        2026, 5, 18, 11, 0, 5, tzinfo=timezone.utc
    )

    # Re-poll on the same (entity_id, last_changed) pair is a no-op per
    # the inbound dedupe gate (F-3 of c2): _seen_states populated → no
    # second emit.
    client._get_response = _IntegrationResponse(
        status_code=200, json_data=[ha_state]
    )
    emitted_second = asyncio.run(adapter._poll_states_once(ctx=ctx))
    assert emitted_second == 0
    state_calls_after = [
        c for c in event_log.calls if c["type"] == "ha.state_changed"
    ]
    assert len(state_calls_after) == 1
