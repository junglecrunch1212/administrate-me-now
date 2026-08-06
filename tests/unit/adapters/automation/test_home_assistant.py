"""Framework-level tests for the Home Assistant Cat-C+E adapter pack.

Closes prompt 11g §7 c3 deliverable (≥17 tests; basename ``test_home_assistant``
unique per F-4 against all prior pack-internal basenames). Verifies the
first canonical Cat-E reference implementation shipped in 11g c2 against:

- :class:`OutboundActionAdapter` inheritance + ``family="home_automation"``
  literal + multi-capability ``capabilities`` list per [PM-35] / [D24];
- the pack-level ``observation_mode_required=True`` (Cat-E gate per
  §6.20) coexisting with a per-capability Cat-C override
  (``observation_mode_required=False`` since reads are NOT side effects
  per memo §8);
- the Cat-C inbound :meth:`_poll_states_once` emit contract: first
  observation of an HA ``entity_id`` → ``ha.state_changed@v1``; re-poll on
  the same ``(entity_id, last_changed)`` → skip; payload validates against
  :class:`HaStateChangedV1` byte-for-byte;
- the Cat-E :meth:`execute_action` dispatch: ``kind="ha.service_call"`` is
  the only declared kind; any other kind raises :class:`ValueError`
  BEFORE the external API is touched;
- the [§6.20] observation-wrap pattern: seam slots populated → routes
  through :func:`adminme.lib.observation.outbound`; unset → direct
  ``POST /api/services/<domain>/<service>`` call; F-3 (HARD): an
  ``OutboundResult(suppressed=True)`` returns
  ``ActionResult(executed=False, suppressed=True)`` AND emits NEITHER
  ``action.executed`` NOR ``action.failed`` (``observation.suppressed`` is
  the audit trail, emitted by :func:`outbound` itself);
- the ``_classify_ha_error`` taxonomy for 401 / 429 / 503 HA REST errors,
  each emitting exactly one ``action.failed@v1`` event;
- the c1 registry-lookup contract: all three v1 schemas
  (``ha.state_changed``, ``action.executed``, ``action.failed``) resolve
  to the c1 Pydantic models registered at
  ``adminme/events/schemas/automation.py``.

These tests live under ``tests/unit/adapters/automation/`` (framework
location, sibling to ``tests/unit/adapters/calendaring/test_google_calendar.py``)
because they exercise the framework-level Cat-C+E contract + the canonical
event-schema layer — not the pack's internal manifest / validator surface
(that's the c4 pack-internal deliverable).
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.registry import registry
from adminme.events.schemas.automation import (
    ActionExecutedV1,
    ActionFailedV1,
    HaStateChangedV1,
)
from adminme.lib.adapters import (
    ActionRequest,
    ActionResult,
    OutboundActionAdapter,
)
from adminme.lib.observation import OutboundResult

REPO_ROOT = Path(__file__).resolve().parents[4]
HA_PACK_DIR = REPO_ROOT / "packs" / "adapters" / "automation" / "home-assistant"


def _load_home_assistant_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root by file path. Mirrors the
    Google Calendar loader: ``packs/`` is not a Python package under
    poetry's config, so file-path importlib is the canonical loader for
    pack code from framework tests."""
    spec = importlib.util.spec_from_file_location(
        "home_assistant_adapter_under_test",
        HA_PACK_DIR / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_home_assistant_adapter_module()
HomeAssistantAdapter: Any = _adapter_mod.HomeAssistantAdapter


_FROZEN_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


class _FakeEventLog:
    """Captures typed asynchronous event-sink writes for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **_kwargs: Any) -> str:
        self.calls.append(envelope.model_dump())
        return f"event-{len(self.calls)}"


def _ctx(event_log: _FakeEventLog) -> AdapterContext:
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-ha-test",
        now_factory=lambda: _FROZEN_NOW,
    )


class _FakeHttpStatusError(Exception):
    """Duck-type for ``httpx.HTTPStatusError`` — the classifier reads
    ``.response.status_code`` and ``.response.headers.get("retry-after")``.
    """

    def __init__(self, response: "_FakeResponse") -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


class _FakeResponse:
    """httpx-style response carrying ``status_code`` + ``headers`` +
    ``raise_for_status`` + ``json``. ``raise_for_status`` raises a
    :class:`_FakeHttpStatusError` for any 4xx/5xx status."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _FakeHttpStatusError(self)

    def json(self) -> Any:
        return self._json_data


class _FakeAsyncClient:
    """Minimal httpx.AsyncClient duck-type. ``get`` and ``post`` return
    pre-canned :class:`_FakeResponse` objects (or raise the configured
    exception); ``aclose`` is a no-op coroutine."""

    def __init__(
        self,
        *,
        get_response: _FakeResponse | None = None,
        post_response: _FakeResponse | None = None,
        get_raises: BaseException | None = None,
        post_raises: BaseException | None = None,
    ) -> None:
        self._get_response = get_response
        self._post_response = post_response
        self._get_raises = get_raises
        self._post_raises = post_raises
        self.get_calls: list[str] = []
        self.post_calls: list[dict[str, Any]] = []

    async def get(self, path: str) -> _FakeResponse:
        self.get_calls.append(path)
        if self._get_raises is not None:
            raise self._get_raises
        assert self._get_response is not None
        return self._get_response

    async def post(self, path: str, *, json: Any = None) -> _FakeResponse:
        self.post_calls.append({"path": path, "json": json})
        if self._post_raises is not None:
            raise self._post_raises
        assert self._post_response is not None
        return self._post_response

    async def aclose(self) -> None:
        return None


def _make_adapter(
    *,
    client: _FakeAsyncClient | None = None,
    observation_session: Any = None,
    observation_manager: Any = None,
    observation_event_log: Any = None,
    base_url: str = "http://homeassistant.local:8123",
    config_overrides: dict[str, Any] | None = None,
) -> Any:
    """Construct a HomeAssistantAdapter with optional pre-installed client
    and observation seam slots. Mirrors the Google Calendar test's
    ``_make_adapter`` factory shape for structural alignment."""
    adapter = HomeAssistantAdapter(
        config={
            "access_token_ref": "op://test/v/ha/token",
            "base_url": base_url,
            **(config_overrides or {}),
        },
        observation_session=observation_session,
        observation_manager=observation_manager,
        observation_event_log=observation_event_log,
    )
    if client is not None:
        adapter._client = client
    return adapter


def _seed_ctx(adapter: Any, event_log: _FakeEventLog) -> AdapterContext:
    """Attach an AdapterContext to the adapter — what
    :meth:`HomeAssistantAdapter.start` does in production via
    ``self._ctx = ctx``."""
    ctx = _ctx(event_log)
    adapter._ctx = ctx
    return ctx


def _state(
    *,
    entity_id: str,
    state: str = "on",
    attributes: dict[str, Any] | None = None,
    last_changed: str = "2026-05-18T11:00:00+00:00",
    last_updated: str | None = None,
) -> dict[str, Any]:
    """Shape-only fake of an HA ``GET /api/states`` element carrying the
    fields the adapter's inbound mapping reads."""
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": dict(attributes or {}),
        "last_changed": last_changed,
        "last_updated": last_updated or last_changed,
    }


# ===========================================================================
# 1) Cat-E inheritance + ClassVars + multi-capability declaration
# ===========================================================================


def test_home_assistant_adapter_inherits_outbound_action_adapter() -> None:
    """The pack's adapter is a Cat-E :class:`OutboundActionAdapter`
    subclass — load-bearing per [PM-34] (inherit ONE ABC even though the
    pack declares Cat-C + Cat-E capabilities). Failing this would mean
    the validator's ``base_class_conformance`` check rejects the pack at
    install time."""
    assert issubclass(HomeAssistantAdapter, OutboundActionAdapter)


def test_family_class_attribute_is_home_automation_literal() -> None:
    """``family`` is the ``"home_automation"`` literal — pinned at the
    class level so the validator can read it without instantiation. Memo
    05 §3.2 + [D19] anchor the three-value Cat-E family union; this is
    the home-automation tip."""
    assert HomeAssistantAdapter.family == "home_automation"


def test_capabilities_contains_cat_c_and_cat_e_entries() -> None:
    """[PM-35] / [D24] multi-capability: the ``capabilities`` ClassVar
    is a list of TWO blocks — one ``Cat-C`` entry covering the inbound
    state-read stream (``ha.state_changed``) + one ``Cat-E`` entry
    covering the outbound service-call stream (``action.executed`` /
    ``action.failed``). This is the canonical multi-capability shape on
    which the validator's per-capability gate runs."""
    caps = HomeAssistantAdapter.capabilities
    assert isinstance(caps, list)
    assert len(caps) == 2
    kinds = {entry["kind"] for entry in caps}
    assert kinds == {"Cat-C", "Cat-E"}
    cat_c = next(e for e in caps if e["kind"] == "Cat-C")
    cat_e = next(e for e in caps if e["kind"] == "Cat-E")
    assert "ha.state_changed" in cat_c["event_families"]
    assert "action.executed" in cat_e["event_families"]
    assert "action.failed" in cat_e["event_families"]


def test_observation_mode_required_is_true_at_pack_level() -> None:
    """[§6.20] / Cat-E ABC contract: ``observation_mode_required`` is
    ``True`` at the class level and CANNOT be opted out. The validator
    rejects any Cat-E pack manifest setting this to ``False``."""
    assert HomeAssistantAdapter.observation_mode_required is True


def test_cat_c_capability_overrides_observation_mode_required_false() -> None:
    """[PM-35] per-capability override: the Cat-C state-read entry sets
    its own ``observation_mode_required: False`` even though the
    pack-level gate is ``True``. Memo 05 §8 — reads are NOT side effects;
    the observation wrap only gates Cat-E writes."""
    caps = HomeAssistantAdapter.capabilities
    cat_c = next(e for e in caps if e["kind"] == "Cat-C")
    cat_e = next(e for e in caps if e["kind"] == "Cat-E")
    assert cat_c["observation_mode_required"] is False
    assert cat_e["observation_mode_required"] is True


# ===========================================================================
# 2) Cat-C inbound poll — dedup + emit
# ===========================================================================


async def test_poll_states_once_emits_ha_state_changed_on_first_observation() -> None:
    """First observation of an HA ``entity_id`` (``_seen_states`` empty) →
    one ``ha.state_changed@v1`` emit per state. The dedupe cache populates
    by side-effect so re-polls on the same ``(entity_id, last_changed)``
    pair are no-ops (covered in the next test)."""
    state = _state(entity_id="light.kitchen", state="on")
    client = _FakeAsyncClient(
        get_response=_FakeResponse(status_code=200, json_data=[state])
    )
    adapter = _make_adapter(client=client)
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    emitted = await adapter._poll_states_once(ctx=ctx)

    assert emitted == 1
    state_changes = [c for c in event_log.calls if c["type"] == "ha.state_changed"]
    assert len(state_changes) == 1
    assert state_changes[0]["schema_version"] == 1
    assert state_changes[0]["payload"]["entity_id"] == "light.kitchen"
    assert state_changes[0]["payload"]["state"] == "on"
    assert state_changes[0]["payload"]["source"] == "home_assistant"


async def test_poll_states_once_skips_seen_entity_with_unchanged_last_changed() -> None:
    """A re-poll on the same ``(entity_id, last_changed)`` pair emits NO
    new events. The dedupe gate keyed on ``_seen_states[entity_id]`` is
    the load-bearing invariant: without it every poll cycle would
    re-emit every entity, drowning downstream consumers."""
    state = _state(
        entity_id="switch.porch",
        state="off",
        last_changed="2026-05-18T10:00:00+00:00",
    )
    client = _FakeAsyncClient(
        get_response=_FakeResponse(status_code=200, json_data=[state])
    )
    adapter = _make_adapter(client=client)
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    first = await adapter._poll_states_once(ctx=ctx)
    second = await adapter._poll_states_once(ctx=ctx)

    assert first == 1
    assert second == 0
    state_changes = [c for c in event_log.calls if c["type"] == "ha.state_changed"]
    assert len(state_changes) == 1


async def test_allowlisted_entity_emits_private_expiring_reminder_context() -> None:
    state = _state(
        entity_id="person.member_a",
        state="home",
        attributes={"latitude": 40.0, "longitude": -73.0, "secret": "drop"},
        last_changed="2026-05-18T10:00:00+00:00",
    )
    client = _FakeAsyncClient(
        get_response=_FakeResponse(status_code=200, json_data=[state])
    )
    adapter = _make_adapter(
        client=client,
        config_overrides={
            "reminder_context_signals": [
                {
                    "entity_id": "person.member_a",
                    "member_id": "member-a",
                    "signal_key": "presence:home",
                    "kind": "presence",
                    "ttl_seconds": 120,
                    "attribute_allowlist": [],
                }
            ]
        },
    )
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    assert await adapter._poll_states_once(ctx=ctx) == 1
    signal = next(
        call for call in event_log.calls if call["type"] == "context.signal.observed"
    )
    assert signal["owner_scope"] == "private:member-a"
    assert signal["payload"]["signal_key"] == "presence:home"
    assert signal["payload"]["expires_at"] == "2026-05-18T10:02:00+00:00"
    assert signal["payload"]["attributes"] == {}


async def test_poll_states_emit_payload_validates_against_ha_state_changed_v1() -> None:
    """The emitted payload validates byte-for-byte against the c1
    :class:`HaStateChangedV1` Pydantic model. ``extra="forbid"`` on the
    model means any drift introducing a new field would fail validation
    — pinning the c1 schema contract: 11g registers three new v1
    schemas, and the inbound emit shape conforms exactly to the
    ``ha.state_changed`` one."""
    state = _state(
        entity_id="climate.bedroom",
        state="heat",
        attributes={"current_temperature": 18.5, "target_temperature": 21.0},
        last_changed="2026-05-18T09:30:00+00:00",
        last_updated="2026-05-18T09:30:05+00:00",
    )
    client = _FakeAsyncClient(
        get_response=_FakeResponse(status_code=200, json_data=[state])
    )
    adapter = _make_adapter(client=client)
    event_log = _FakeEventLog()
    ctx = _seed_ctx(adapter, event_log)

    await adapter._poll_states_once(ctx=ctx)

    state_call = next(c for c in event_log.calls if c["type"] == "ha.state_changed")
    payload = state_call["payload"]
    instance = HaStateChangedV1.model_validate(payload)
    assert instance.entity_id == "climate.bedroom"
    assert instance.state == "heat"
    assert instance.attributes["current_temperature"] == 18.5
    assert instance.attributes["target_temperature"] == 21.0
    assert instance.source == "home_assistant"
    assert instance.last_changed == datetime(2026, 5, 18, 9, 30, 0, tzinfo=timezone.utc)
    assert instance.last_updated == datetime(2026, 5, 18, 9, 30, 5, tzinfo=timezone.utc)


# ===========================================================================
# 3) Cat-E execute_action — kind dispatch + ValueError gate
# ===========================================================================


async def test_execute_action_dispatches_to_ha_service_call_kind() -> None:
    """``execute_action`` accepts ``kind="ha.service_call"`` and routes
    through to the POST seam. The fake client records exactly one POST to
    ``/api/services/<domain>/<service>`` carrying the request's
    ``service_data`` as the JSON body, and the result is
    ``ActionResult(executed=True, suppressed=False)``."""
    client = _FakeAsyncClient(
        post_response=_FakeResponse(status_code=200, json_data=[])
    )
    adapter = _make_adapter(client=client)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    request = ActionRequest(
        kind="ha.service_call",
        target_external_id=None,
        payload={
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": "light.kitchen", "brightness": 200},
        },
        correlation_id="corr-action-001",
    )
    result = await adapter.execute_action(request)

    assert isinstance(result, ActionResult)
    assert result.executed is True
    assert result.suppressed is False
    assert len(client.post_calls) == 1
    call = client.post_calls[0]
    assert call["path"] == "/api/services/light/turn_on"
    assert call["json"] == {
        "entity_id": "light.kitchen",
        "brightness": 200,
    }


async def test_execute_action_undeclared_kind_raises_value_error() -> None:
    """Defense-in-depth: any ``kind`` other than ``"ha.service_call"``
    raises :class:`ValueError` BEFORE the external API is touched. The
    Cat-E ABC has no ``write_capabilities`` gate (that's Cat-B), so the
    pack enforces the dispatch surface in-pack via a kind check at the
    top of :meth:`execute_action`."""
    client = _FakeAsyncClient(
        post_response=_FakeResponse(status_code=200, json_data=[])
    )
    adapter = _make_adapter(client=client)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    request = ActionRequest(
        kind="ha.script.run",
        target_external_id=None,
        payload={"script_id": "morning_routine"},
    )
    with pytest.raises(ValueError):
        await adapter.execute_action(request)
    # The external API was NOT touched.
    assert client.post_calls == []


# ===========================================================================
# 4) Observation-mode wrap — [§6.20] + F-3 (HARD)
# ===========================================================================


async def test_execute_action_routes_through_observation_outbound_when_seams_populated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """:meth:`execute_action` MUST route the service-call POST through
    :func:`adminme.lib.observation.outbound` per [§6.20] when the three
    observation seam slots are populated. The adapter imports the
    function under the alias ``observation_outbound`` in its own module
    namespace; monkeypatching THAT name records the wrap without needing
    real session / manager / event-log instances. The Phase A path
    (seams unset) bypasses the wrap (see the next test)."""
    client = _FakeAsyncClient(
        post_response=_FakeResponse(status_code=200, json_data=[])
    )
    session_sentinel = object()
    manager_sentinel = object()
    obs_log_sentinel = object()
    adapter = _make_adapter(
        client=client,
        observation_session=session_sentinel,
        observation_manager=manager_sentinel,
        observation_event_log=obs_log_sentinel,
    )
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    captured: list[dict[str, Any]] = []

    async def _fake_outbound(
        session: Any,
        action: str,
        payload: dict[str, Any],
        action_fn: Any,
        *,
        manager: Any,
        event_sink: Any,
        target_channel: str = "unknown",
        target_identifier: str = "unknown",
    ) -> OutboundResult:
        captured.append(
            {
                "session": session,
                "action": action,
                "payload": dict(payload),
                "manager": manager,
                "event_log": event_sink,
                "target_channel": target_channel,
                "target_identifier": target_identifier,
            }
        )
        # Observation mode INACTIVE: actually call through so the fake
        # POST registers on the client mock.
        result = await action_fn()
        return OutboundResult(suppressed=False, result=result)

    monkeypatch.setattr(_adapter_mod, "observation_outbound", _fake_outbound)

    request = ActionRequest(
        kind="ha.service_call",
        target_external_id=None,
        payload={
            "domain": "switch",
            "service": "toggle",
            "service_data": {"entity_id": "switch.lamp"},
        },
        correlation_id="corr-wrap-001",
    )
    result = await adapter.execute_action(request)

    assert len(captured) == 1
    call = captured[0]
    assert call["session"] is session_sentinel
    assert call["manager"] is manager_sentinel
    assert call["event_log"] is obs_log_sentinel
    assert call["action"] == "ha.service_call"
    assert call["target_channel"] == "home_assistant"
    assert call["target_identifier"] == "switch.toggle"
    assert call["payload"]["domain"] == "switch"
    assert call["payload"]["service"] == "toggle"
    # The wrapped POST actually fired against the fake client.
    assert len(client.post_calls) == 1
    assert result.executed is True
    assert result.suppressed is False


async def test_execute_action_observation_suppressed_returns_unapplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-3 (HARD): when :func:`observation_outbound` returns
    ``OutboundResult(suppressed=True)``, :meth:`execute_action` returns
    ``ActionResult(executed=False, external_id=None, suppressed=True)``
    AND emits NEITHER ``action.executed`` NOR ``action.failed``. The
    only audit-trail event for the suppressed path is
    ``observation.suppressed@v1``, emitted by :func:`outbound` itself —
    NOT by this adapter. The pack's HA REST POST is NOT called: the
    observation toggle is the FINAL outbound filter per [§6.13]."""
    client = _FakeAsyncClient(
        post_response=_FakeResponse(status_code=200, json_data=[])
    )
    adapter = _make_adapter(
        client=client,
        observation_session=object(),
        observation_manager=object(),
        observation_event_log=object(),
    )
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    async def _suppressing_outbound(
        _session: Any,
        _action: str,
        _payload: dict[str, Any],
        _action_fn: Any,
        **_kwargs: Any,
    ) -> OutboundResult:
        return OutboundResult(suppressed=True, result=None)

    monkeypatch.setattr(_adapter_mod, "observation_outbound", _suppressing_outbound)

    request = ActionRequest(
        kind="ha.service_call",
        target_external_id=None,
        payload={
            "domain": "light",
            "service": "turn_off",
            "service_data": {"entity_id": "light.suppress"},
        },
        correlation_id="corr-suppress-001",
    )
    result = await adapter.execute_action(request)

    assert result.executed is False
    assert result.external_id is None
    assert result.suppressed is True
    # F-3: NEITHER action.executed NOR action.failed emitted on suppress.
    executed_calls = [c for c in event_log.calls if c["type"] == "action.executed"]
    failed_calls = [c for c in event_log.calls if c["type"] == "action.failed"]
    assert executed_calls == []
    assert failed_calls == []
    # The wrapped POST was NOT invoked.
    assert client.post_calls == []


async def test_execute_action_direct_call_when_seams_unset_returns_applied() -> None:
    """Phase A (today): the supervisor's adapter loader does NOT yet
    populate the three observation-seam slots, so :meth:`execute_action`
    falls through the wrap branch and direct-calls
    ``client.post(...)``. The Phase-A-conditional pattern matches
    Google Calendar's 11b-iii. One ``action.executed@v1`` event emitted
    with the request's ``correlation_id`` carried forward per [D8]."""
    client = _FakeAsyncClient(
        post_response=_FakeResponse(status_code=200, json_data=[])
    )
    adapter = _make_adapter(client=client)  # seams default to None
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    request = ActionRequest(
        kind="ha.service_call",
        target_external_id=None,
        payload={
            "domain": "scene",
            "service": "turn_on",
            "service_data": {"entity_id": "scene.movie_night"},
        },
        correlation_id="corr-direct-001",
    )
    result = await adapter.execute_action(request)

    assert result.executed is True
    assert result.suppressed is False
    assert len(client.post_calls) == 1
    executed_calls = [c for c in event_log.calls if c["type"] == "action.executed"]
    assert len(executed_calls) == 1
    payload = executed_calls[0]["payload"]
    assert payload["action_kind"] == "ha.service_call"
    assert payload["correlation_id"] == "corr-direct-001"
    assert payload["payload"]["domain"] == "scene"
    assert payload["payload"]["service"] == "turn_on"


# ===========================================================================
# 5) Error classification — ActionFailedV1 taxonomy
# ===========================================================================


async def test_execute_action_4xx_auth_failure_classifies_fatal_emits_action_failed() -> (
    None
):
    """A 401 from the HA REST POST classifies as ``fatal`` /
    ``auth_failed``, emits exactly one ``action.failed@v1`` event, AND
    re-raises so the supervisor's backoff handles retry. Mirrors the
    Google Calendar canonical 401 outbound error path on the Cat-E side."""
    error = _FakeHttpStatusError(_FakeResponse(status_code=401))
    client = _FakeAsyncClient(post_raises=error)
    adapter = _make_adapter(client=client)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    request = ActionRequest(
        kind="ha.service_call",
        target_external_id=None,
        payload={
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": "light.401"},
        },
        correlation_id="corr-401",
    )
    with pytest.raises(_FakeHttpStatusError):
        await adapter.execute_action(request)

    failed_calls = [c for c in event_log.calls if c["type"] == "action.failed"]
    assert len(failed_calls) == 1
    payload = failed_calls[0]["payload"]
    assert payload["severity"] == "fatal"
    assert payload["error_kind"] == "auth_failed"
    assert payload["correlation_id"] == "corr-401"


async def test_execute_action_5xx_classifies_transient_emits_action_failed() -> None:
    """A 503 from the HA REST POST classifies as ``transient`` /
    ``network`` so the supervisor's backoff retries. Exactly one
    ``action.failed@v1`` emitted; the exception propagates so the
    supervisor's task-loop sees the failure."""
    error = _FakeHttpStatusError(_FakeResponse(status_code=503))
    client = _FakeAsyncClient(post_raises=error)
    adapter = _make_adapter(client=client)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    request = ActionRequest(
        kind="ha.service_call",
        target_external_id=None,
        payload={
            "domain": "switch",
            "service": "turn_off",
            "service_data": {"entity_id": "switch.503"},
        },
        correlation_id="corr-503",
    )
    with pytest.raises(_FakeHttpStatusError):
        await adapter.execute_action(request)

    failed_calls = [c for c in event_log.calls if c["type"] == "action.failed"]
    assert len(failed_calls) == 1
    payload = failed_calls[0]["payload"]
    assert payload["severity"] == "transient"
    assert payload["error_kind"] == "network"


async def test_execute_action_429_classifies_rate_limited_transient() -> None:
    """A 429 from the HA REST POST classifies as ``transient`` /
    ``rate_limited``. Exactly one ``action.failed@v1`` emitted. The
    severity discriminates the supervisor's backoff policy: rate-limited
    is retry-after-window, not retry-immediately."""
    error = _FakeHttpStatusError(
        _FakeResponse(status_code=429, headers={"Retry-After": "30"})
    )
    client = _FakeAsyncClient(post_raises=error)
    adapter = _make_adapter(client=client)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    request = ActionRequest(
        kind="ha.service_call",
        target_external_id=None,
        payload={
            "domain": "scene",
            "service": "turn_on",
            "service_data": {"entity_id": "scene.429"},
        },
        correlation_id="corr-429",
    )
    with pytest.raises(_FakeHttpStatusError):
        await adapter.execute_action(request)

    failed_calls = [c for c in event_log.calls if c["type"] == "action.failed"]
    assert len(failed_calls) == 1
    payload = failed_calls[0]["payload"]
    assert payload["severity"] == "transient"
    assert payload["error_kind"] == "rate_limited"


async def test_execute_action_failed_payload_validates_against_action_failed_v1() -> (
    None
):
    """The emitted ``action.failed@v1`` payload validates byte-for-byte
    against the c1 :class:`ActionFailedV1` Pydantic model.
    ``extra="forbid"`` on the model means any drift introducing a new
    field would fail validation — pinning the c1 schema contract on the
    error path."""
    error = _FakeHttpStatusError(_FakeResponse(status_code=401))
    client = _FakeAsyncClient(post_raises=error)
    adapter = _make_adapter(client=client)
    event_log = _FakeEventLog()
    _seed_ctx(adapter, event_log)

    request = ActionRequest(
        kind="ha.service_call",
        target_external_id="ha-extid-401",
        payload={
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": "light.validate"},
        },
        correlation_id="corr-validate-failed",
    )
    with pytest.raises(_FakeHttpStatusError):
        await adapter.execute_action(request)

    failed_call = next(c for c in event_log.calls if c["type"] == "action.failed")
    payload = failed_call["payload"]
    instance = ActionFailedV1.model_validate(payload)
    assert instance.action_kind == "ha.service_call"
    assert instance.target_external_id == "ha-extid-401"
    assert instance.severity == "fatal"
    assert instance.error_kind == "auth_failed"
    assert instance.correlation_id == "corr-validate-failed"


# ===========================================================================
# 6) c1 registry contract — three new v1 schemas resolve to c1 models
# ===========================================================================


def test_three_v1_schemas_registry_lookup_returns_models() -> None:
    """The c1 verify-only contract pinned in the framework test suite:
    ``registry.get(<type>, 1)`` for each of the three new v1 schemas
    registered at ``adminme/events/schemas/automation.py`` returns the
    exact Pydantic model class — proving the c1 registrations are wired
    through the registry that supervisors / projectors / pipelines
    consume per [D7]."""
    assert registry.get("ha.state_changed", 1) is HaStateChangedV1
    assert registry.get("action.executed", 1) is ActionExecutedV1
    assert registry.get("action.failed", 1) is ActionFailedV1
