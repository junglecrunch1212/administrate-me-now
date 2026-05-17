"""Round-trip integration tests for the adapter supervisor + Gmail adapter.

Closes prompt 11a's C4 deliverable: the supervisor (``adminme/daemons/
adapter_supervisor.py``) and the Gmail adapter pack (``packs/adapters/
messaging/gmail/``) are exercised together end-to-end through one
authenticate + one full poll cycle.

The supervisor mints an ``AdapterContext`` per managed adapter via the
injected ``ctx_factory``; the Gmail adapter writes via the context's
``event_log.append_sync`` (the pack's adapter.py prefers ``append_sync``
when present so tests can inject a recording fake without instantiating
the SQLite-backed ``adminme.events.log.EventLog``). The recording log
captures every typed event the adapter emits and lets us assert the full
end-to-end emission shape for ``adapter.authenticated`` +
``messaging.received`` + ``adapter.cursor_advanced`` + ``adapter.error``.

Per the 10c-iii integration template at
``tests/integration/test_pipeline_10c_iii_integration.py``: shared seam,
typed events, observation-mode-aware assertions transposed for adapters.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import Adapter, AdapterContext
from adminme.daemons.adapter_supervisor import (
    INITIAL_BACKOFF_S,
    AdapterSupervisor,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GMAIL_PACK_ROOT = REPO_ROOT / "packs" / "adapters" / "messaging" / "gmail"
FIXTURES = GMAIL_PACK_ROOT / "tests" / "fixtures"

_FROZEN_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


def _load_gmail_module() -> Any:
    """Load ``adapter.py`` from the Gmail pack root by file path.

    ``packs/`` is not on the import path under poetry's package config
    (only ``adminme`` is), so the pack-internal tests use the same
    file-path import pattern. Mirrors
    ``test_gmail_adapter_pack_internal._load_adapter_module``."""
    spec = importlib.util.spec_from_file_location(
        "gmail_adapter_integration", GMAIL_PACK_ROOT / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_gmail_mod: Any = _load_gmail_module()
GmailAdapter: Any = _gmail_mod.GmailAdapter
GMAIL_READONLY_SCOPE: str = _gmail_mod.GMAIL_READONLY_SCOPE


def _fixture(name: str) -> dict[str, Any]:
    with (FIXTURES / name).open() as f:
        return json.load(f)


class _RecordingEventLog:
    """Recording event log shared by the supervisor + adapter contexts.

    The Gmail adapter and the supervisor both probe for ``append_sync``
    first per ``adapter.py:_append`` and ``adapter_supervisor.py
    :_emit_adapter_error``; this fake satisfies that surface without
    pulling in the SQLite-backed ``EventLog`` (whose ``append`` is async
    + requires a constructed ``EventEnvelope``, mismatched against the
    keyword-only kwargs the adapter passes)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append_sync(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    def by_type(self, event_type: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c.get("type") == event_type]


# ---------------------------------------------------------------------------
# fake Gmail service — mirrors the c3 unit-test shape, kept local so this
# file is readable in isolation.
# ---------------------------------------------------------------------------


class _FakeGmailService:
    def __init__(
        self,
        *,
        profile: dict[str, Any] | None = None,
        history_pages: list[dict[str, Any]] | None = None,
        messages: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._profile = profile
        self._history_pages = list(history_pages or [])
        self._messages = dict(messages or {})
        self.history_list_calls: list[dict[str, Any]] = []
        self.messages_get_calls: list[dict[str, Any]] = []
        self.get_profile_calls: list[dict[str, Any]] = []

    def users(self) -> Any:
        return _FakeUsers(self)


class _FakeUsers:
    def __init__(self, service: _FakeGmailService) -> None:
        self._service = service

    def getProfile(self, *, userId: str) -> Any:
        self._service.get_profile_calls.append({"userId": userId})
        return _FakeExecute(self._service, kind="getProfile")

    def history(self) -> Any:
        return _FakeHistory(self._service)

    def messages(self) -> Any:
        return _FakeMessages(self._service)


class _FakeHistory:
    def __init__(self, service: _FakeGmailService) -> None:
        self._service = service

    def list(self, **kwargs: Any) -> Any:
        self._service.history_list_calls.append(kwargs)
        return _FakeExecute(self._service, kind="history.list")


class _FakeMessages:
    def __init__(self, service: _FakeGmailService) -> None:
        self._service = service

    def get(self, **kwargs: Any) -> Any:
        self._service.messages_get_calls.append(kwargs)
        return _FakeExecute(self._service, kind="messages.get", params=kwargs)


class _FakeExecute:
    def __init__(
        self,
        service: _FakeGmailService,
        *,
        kind: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        self._service = service
        self._kind = kind
        self._params = params or {}

    def execute(self) -> Any:
        if self._kind == "getProfile":
            return dict(self._service._profile or {})
        if self._kind == "history.list":
            if not self._service._history_pages:
                return {"historyId": "0"}
            return self._service._history_pages.pop(0)
        if self._kind == "messages.get":
            msg_id = self._params.get("id")
            if msg_id not in self._service._messages:
                raise KeyError(f"unknown message id {msg_id!r}")
            return dict(self._service._messages[msg_id])
        raise AssertionError(f"unhandled fake call {self._kind!r}")


# ---------------------------------------------------------------------------
# shared builders
# ---------------------------------------------------------------------------


def _build_gmail_adapter(
    *,
    service: _FakeGmailService,
    max_poll_cycles: int = 1,
    op_ref: str = "op://test/vault/gmail/sa",
    user_email: str = "sandbox@example.test",
    poll_interval_s: float = 0.0,
) -> Any:
    """Construct a real ``GmailAdapter`` with an injected fake service +
    deterministic credentials resolver. The adapter is otherwise the
    real production class — supervisor lifecycle exercises the same code
    paths as in production.

    ``poll_interval_s`` defaults to 0 so the inter-cycle sleep yields
    immediately back to the event loop instead of stalling the test for
    the production 120-second cadence. Set higher to exercise the real
    backoff window (the c1 supervisor test does this for the
    restart-on-crash path via monkey-patched asyncio.sleep)."""

    def _resolver(_ref: str) -> dict[str, Any]:
        return {"type": "service_account", "client_email": "ci@test"}

    def _factory(
        _creds: dict[str, Any], _scopes: list[str], _subject: str
    ) -> Any:
        return service

    adapter = GmailAdapter(
        config={
            "service_account_key_ref": op_ref,
            "user_email": user_email,
        },
        credentials_resolver=_resolver,
        service_factory=_factory,
        max_poll_cycles=max_poll_cycles,
    )
    # Per-instance override of the class attr — adapter reads
    # ``self.POLL_INTERVAL_S`` so this shadows the 120s production value.
    adapter.POLL_INTERVAL_S = poll_interval_s
    return adapter


def _write_channels_yaml(path: Path, *, port: int = 0) -> None:
    path.write_text(
        f"""
adapter_supervisor_host: 127.0.0.1
adapter_supervisor_port: {port}
adapters:
  - kind: messaging-gmail-api
    family: messaging
    sensitivity_floor: normal
""".lstrip(),
        encoding="utf-8",
    )


def _supervisor(
    *,
    config_path: Path,
    event_log: _RecordingEventLog,
    adapters: list[Adapter],
    corr_seq: list[str] | None = None,
) -> AdapterSupervisor:
    """Build a supervisor whose ``ctx_factory`` hands each adapter a
    context tied to the shared recording log + frozen clock + a
    deterministic correlation-id sequence (defaults to ``corr-N``)."""

    counter = {"n": 0}

    def _next_corr() -> str:
        counter["n"] += 1
        if corr_seq is not None:
            corr_seq.append(f"corr-{counter['n']}")
        return f"corr-{counter['n']}"

    def _ctx_factory() -> AdapterContext:
        return AdapterContext(
            event_log=event_log,  # type: ignore[arg-type]
            config={},
            correlation_id_factory=_next_corr,
            now_factory=lambda: _FROZEN_NOW,
        )

    return AdapterSupervisor(
        config_path=config_path,
        ctx_factory=_ctx_factory,
        adapter_loader=lambda _cfg: list(adapters),
    )


async def _wait_for(predicate: Any, *, timeout: float = 2.0) -> None:
    """Spin-wait helper. Async tasks settle within milliseconds in this
    suite; the timeout guards against hangs if the supervisor's task
    machinery regresses."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {predicate!r}")


# ---------------------------------------------------------------------------
# 1) full round-trip — supervisor + adapter together emit the expected
#    cross-cutting events on one poll cycle.
# ---------------------------------------------------------------------------


async def test_supervisor_round_trip_emits_auth_received_and_cursor(
    tmp_path: Path,
) -> None:
    """End-to-end happy path. Supervisor spawns the real ``GmailAdapter``,
    which authenticates against the fake Gmail service, bootstraps its
    cursor, runs one full poll cycle that ingests two messages, trips the
    event-count milestone (counter pre-seeded), and emits each cross-
    cutting event type exactly once. The supervisor then stops cleanly."""

    channels = tmp_path / "channels.yaml"
    _write_channels_yaml(channels)

    history = _fixture("history_response.json")
    service = _FakeGmailService(
        profile=_fixture("profile_response.json"),
        history_pages=[history],
        messages={
            "msg-alice": _fixture("message_alice.json"),
            "msg-bob": _fixture("message_bob.json"),
        },
    )
    # Bootstrap cycle + one ingest cycle = max_poll_cycles=2.
    adapter = _build_gmail_adapter(service=service, max_poll_cycles=2)
    # Seed the counter so the 2-event cycle trips the >=100 milestone.
    adapter._events_since_advance = adapter.CURSOR_MILESTONE_EVENTS - 2

    event_log = _RecordingEventLog()
    sup = _supervisor(
        config_path=channels, event_log=event_log, adapters=[adapter]
    )

    await sup.start()
    try:
        await _wait_for(
            lambda: len(event_log.by_type("adapter.cursor_advanced")) == 1
        )

        # adapter.authenticated exactly once with the right shape.
        auth = event_log.by_type("adapter.authenticated")
        assert len(auth) == 1
        assert auth[0]["schema_version"] == 1
        assert auth[0]["payload"]["adapter_name"] == "messaging-gmail-api"
        assert auth[0]["payload"]["adapter_family"] == "messaging"
        assert auth[0]["payload"]["scopes"] == [GMAIL_READONLY_SCOPE]
        # token_storage_ref carries the op-ref, NEVER raw secret material.
        assert (
            auth[0]["payload"]["token_storage_ref"]
            == "op://test/vault/gmail/sa"
        )
        assert "PRIVATE KEY" not in auth[0]["payload"]["token_storage_ref"]

        # messaging.received fires once per added Gmail message
        # (history fixture has 3 messagesAdded rows but msg-alice is
        # repeated — dedupe expected per adapter contract).
        received = event_log.by_type("messaging.received")
        assert len(received) == 2
        threads = sorted(r["payload"]["thread_id"] for r in received)
        assert threads == ["thr-alice", "thr-bob"]
        for r in received:
            assert r["payload"]["source_channel"] == "gmail"
            assert r["payload"]["to_identifier"] == "sandbox@example.test"

        # adapter.cursor_advanced emits exactly once at the milestone.
        advances = event_log.by_type("adapter.cursor_advanced")
        assert len(advances) == 1
        adv_payload = advances[0]["payload"]
        assert adv_payload["adapter_name"] == "messaging-gmail-api"
        assert adv_payload["cursor_kind"] == "history_id"
        assert adv_payload["cursor_value"] == "9002"
        assert adv_payload["events_since_last"] == 100

        # No adapter.error events on the happy path.
        assert event_log.by_type("adapter.error") == []

        # Supervisor's status snapshot reflects the running adapter.
        snap = sup.snapshot()
        assert (
            snap["adapters"]["messaging-gmail-api"]["state"]
            in {"running", "stopped"}
        )
    finally:
        await sup.stop()
        snap_after = sup.snapshot()
        assert (
            snap_after["adapters"]["messaging-gmail-api"]["state"] == "stopped"
        )


# ---------------------------------------------------------------------------
# 2) supervisor's restart-with-backoff path emits ``adapter.error`` and
#    re-runs the adapter on the next backoff window.
# ---------------------------------------------------------------------------


class _CrashOnceThenStableAdapter:
    """Adapter that raises on its first ``start()`` and stabilises on the
    second — the minimal shape for asserting the supervisor's
    ``adapter.error`` + restart-with-backoff loop end-to-end."""

    name = "crashy-gmail"
    family = "messaging"
    version = "0.0.0"
    sensitivity_floor = "normal"

    def __init__(self) -> None:
        self.start_count = 0
        from adminme.adapters.base import AdapterStatus

        self._status = AdapterStatus()

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        self.start_count += 1
        if self.start_count == 1:
            raise RuntimeError("simulated transient crash")
        await asyncio.Event().wait()  # stabilise: wait for cancellation

    async def stop(self) -> None:
        return None

    def status(self) -> Any:
        return self._status


async def test_supervisor_emits_adapter_error_on_crash_then_restarts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supervisor's per-adapter loop catches the crash, emits an
    ``adapter.error`` event v1 with severity=transient, and restarts the
    adapter after the backoff window. Backoff sleep is monkey-patched so
    the test settles in milliseconds rather than seconds."""

    real_sleep = asyncio.sleep
    sleeps: list[float] = []

    async def _fast_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(
        "adminme.daemons.adapter_supervisor.asyncio.sleep", _fast_sleep
    )

    channels = tmp_path / "channels.yaml"
    _write_channels_yaml(channels)
    event_log = _RecordingEventLog()
    adapter = _CrashOnceThenStableAdapter()

    sup = _supervisor(
        config_path=channels, event_log=event_log, adapters=[adapter]
    )
    await sup.start()
    try:
        await _wait_for(lambda: adapter.start_count >= 2)
        errors = event_log.by_type("adapter.error")
        assert len(errors) == 1
        err = errors[0]
        assert err["schema_version"] == 1
        assert err["payload"]["adapter_name"] == "crashy-gmail"
        assert err["payload"]["adapter_family"] == "messaging"
        assert err["payload"]["severity"] == "transient"
        assert err["payload"]["retry_after_s"] == int(INITIAL_BACKOFF_S)
        # The supervisor scheduled at least one INITIAL_BACKOFF_S sleep.
        assert any(abs(s - INITIAL_BACKOFF_S) < 1e-6 for s in sleeps)

        snap = sup.snapshot()
        assert snap["adapters"]["crashy-gmail"]["restart_count"] >= 1
    finally:
        await sup.stop()


# ---------------------------------------------------------------------------
# 3) the repo's real config/channels.yaml is shape-valid: supervisor
#    parses it without error and exposes the Gmail adapter entry.
# ---------------------------------------------------------------------------


async def test_supervisor_parses_real_repo_channels_yaml(tmp_path: Path) -> None:
    """The on-disk ``config/channels.yaml`` shipped in C1 round-trips
    through ``AdapterSupervisor.load_config()``. Documents the
    canonical-config contract that prompt 16 (bootstrap wizard) will
    later own — any drift in the YAML shape breaks this test."""

    repo_channels = REPO_ROOT / "config" / "channels.yaml"
    # Copy the repo file so the test never holds a live port on the
    # operator's machine (load_config doesn't bind, but defensive).
    copied = tmp_path / "channels.yaml"
    copied.write_text(repo_channels.read_text(encoding="utf-8"), encoding="utf-8")

    sup = _supervisor(
        config_path=copied, event_log=_RecordingEventLog(), adapters=[]
    )
    config = sup.load_config()

    assert config["adapter_supervisor_port"] == 8081
    assert config["adapter_supervisor_host"] == "127.0.0.1"
    adapters_entries = config["adapters"]
    assert isinstance(adapters_entries, list)
    assert len(adapters_entries) >= 1
    gmail = next(
        (a for a in adapters_entries if a.get("kind") == "messaging-gmail-api"),
        None,
    )
    assert gmail is not None, "config/channels.yaml is missing the Gmail entry"
    assert gmail["family"] == "messaging"
    assert gmail["config"]["service_account_key_ref"].startswith("op://")


# ---------------------------------------------------------------------------
# 4) the supervisor's stdlib HTTP /status endpoint serves a JSON snapshot
#    reflecting the live Gmail adapter — proves the supervisor's
#    out-of-band introspection surface works against the real adapter.
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Bind a transient socket to port 0 to ask the OS for an unused
    port, then release it. Avoids hardcoding 8081 (collides with the
    operator's local supervisor) and avoids retrying on EADDRINUSE."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_supervisor_status_endpoint_serves_snapshot(tmp_path: Path) -> None:
    """The supervisor's ``/status`` endpoint serves a JSON snapshot
    aggregating each managed adapter's state. Hits the live socket via
    stdlib ``urllib`` to confirm the HTTP/1.1 handler isn't dependent on
    a third-party HTTP client and works in the supervisor's real
    asyncio.start_server path."""

    port = _free_port()
    channels = tmp_path / "channels.yaml"
    _write_channels_yaml(channels, port=port)

    service = _FakeGmailService(profile=_fixture("profile_response.json"))
    # max_poll_cycles=0 so start() returns immediately after authenticating;
    # the supervisor's task transitions to "stopped" but the supervisor
    # itself (and its /status server) remains up until sup.stop().
    adapter = _build_gmail_adapter(service=service, max_poll_cycles=0)

    event_log = _RecordingEventLog()
    sup = _supervisor(
        config_path=channels, event_log=event_log, adapters=[adapter]
    )
    await sup.start()
    try:
        # Wait for the adapter task to authenticate (one round trip).
        await _wait_for(
            lambda: len(event_log.by_type("adapter.authenticated")) == 1
        )

        # Hit /status via stdlib urllib in a worker thread so we don't
        # block the supervisor's event loop.
        def _get_status() -> dict[str, Any]:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/status")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                assert resp.status == 200
                ctype = resp.headers.get("Content-Type", "")
                assert ctype.startswith("application/json")
                return json.loads(resp.read().decode("utf-8"))

        body = await asyncio.to_thread(_get_status)
        assert "supervisor" in body
        assert "adapters" in body
        assert "messaging-gmail-api" in body["adapters"]
        gmail_state = body["adapters"]["messaging-gmail-api"]["state"]
        assert gmail_state in {"starting", "running", "stopped", "backoff", "crashed"}
    finally:
        await sup.stop()


# ---------------------------------------------------------------------------
# 5) correlation_id flows from the supervisor's factory through each
#    typed event the adapter emits.
# ---------------------------------------------------------------------------


async def test_supervisor_correlation_ids_propagate_to_adapter_events(
    tmp_path: Path,
) -> None:
    """[D8] correlation_id propagation. Every emitted event from the
    adapter carries a correlation_id minted by the supervisor's
    ``ctx_factory``; the adapter never fabricates its own."""

    channels = tmp_path / "channels.yaml"
    _write_channels_yaml(channels)

    history = _fixture("history_response.json")
    service = _FakeGmailService(
        profile=_fixture("profile_response.json"),
        history_pages=[history],
        messages={
            "msg-alice": _fixture("message_alice.json"),
            "msg-bob": _fixture("message_bob.json"),
        },
    )
    adapter = _build_gmail_adapter(service=service, max_poll_cycles=2)
    # Pre-seed the event-count counter so the ingest cycle's 2 emits
    # crosses the >=100 milestone (the time-milestone path is reset by
    # ``start()``'s ``_last_advance_at`` write, so the event-count path
    # is the deterministic seam here).
    adapter._events_since_advance = adapter.CURSOR_MILESTONE_EVENTS - 2

    event_log = _RecordingEventLog()
    minted: list[str] = []
    sup = _supervisor(
        config_path=channels,
        event_log=event_log,
        adapters=[adapter],
        corr_seq=minted,
    )

    await sup.start()
    try:
        await _wait_for(
            lambda: len(event_log.by_type("adapter.cursor_advanced")) >= 1
        )

        # All emitted events from this adapter carry one of the minted
        # correlation ids — the adapter never invented its own.
        relevant = (
            event_log.by_type("adapter.authenticated")
            + event_log.by_type("messaging.received")
            + event_log.by_type("adapter.cursor_advanced")
        )
        assert relevant, "expected at least one emitted event"
        for call in relevant:
            corr = call.get("correlation_id")
            assert isinstance(corr, str) and corr.startswith("corr-")
            assert corr in minted
    finally:
        await sup.stop()


# ---------------------------------------------------------------------------
# 6) Cohort C retrofit (prompt 11r-a) — the supervisor-loaded GmailAdapter
#    satisfies the CommunicationAdapter ABC end-to-end, the send stub
#    raises NotImplementedError, the _on_inbound hook is a no-op, and the
#    on-disk pack passes the 11-β validator with NO advisory_pending_retrofit
#    finding. These tests sit at the cross-module seam (supervisor loader +
#    pack adapter + framework ABC + manifest parser + validator) where the
#    retrofit's contract is observable end-to-end.
# ---------------------------------------------------------------------------


def test_retrofit_supervisor_loaded_gmail_inherits_communication_adapter() -> (
    None
):
    """The supervisor's adapter-loading code path (file-path importlib,
    mirrored by ``_load_gmail_module``) yields a class that inherits from
    :class:`CommunicationAdapter`. Per memo 08 §3.3, this is the contract
    change the retrofit ships — the validator's ``base_class_conformance``
    check observes this via AST walk against the same file."""
    from adminme.lib.adapters import CommunicationAdapter

    service = _FakeGmailService(profile=_fixture("profile_response.json"))
    adapter = _build_gmail_adapter(service=service, max_poll_cycles=0)

    assert isinstance(adapter, CommunicationAdapter)
    assert issubclass(GmailAdapter, CommunicationAdapter)


async def test_retrofit_send_method_is_a_real_implementation_through_loader() -> (
    None
):
    """The ``send`` abstract method is now a REAL Gmail v1
    ``users.messages.send`` implementation (post-11a c2 — supersedes the
    11r-a ``NotImplementedError`` stub). Verifying through the same
    loader the supervisor uses confirms the real implementation is on
    the production class, not a test-only shim. Deep coverage of the
    send contract lives at section (7) below; this is the retrofit-
    sibling pin that the stub is gone."""
    from adminme.lib.adapters import OutboundMessage, SendResult

    service = _FakeGmailService(profile=_fixture("profile_response.json"))
    adapter = _build_gmail_adapter(service=service, max_poll_cycles=0)

    # Bypass start()/supervisor lifecycle — :meth:`send` only needs
    # ``_service`` populated to call the Gmail API and ``_ctx`` is
    # optional (without it the emit path falls back to UTC now()).
    class _Execute:
        def execute(self) -> dict[str, Any]:
            return {"id": "gmail-retrofit-1", "threadId": "thr-retrofit-1"}

    class _Messages:
        def send(self, **_kwargs: Any) -> Any:
            return _Execute()

    class _Users:
        def messages(self) -> Any:
            return _Messages()

    class _Service:
        def users(self) -> Any:
            return _Users()

    adapter._service = _Service()
    message = OutboundMessage(
        channel="gmail",
        recipient="someone@example.test",
        body="hello from integration test",
    )

    result = await adapter.send(message)

    assert isinstance(result, SendResult)
    assert result.delivered is True
    assert result.external_id == "gmail-retrofit-1"


async def test_retrofit_validator_clean_after_supervisor_lifecycle(
    tmp_path: Path,
) -> None:
    """End-to-end retrofit contract: the supervisor runs one full poll
    cycle against the retrofitted GmailAdapter (preserving the existing
    inbound emit path per memo 08 §5), the ``_on_inbound`` hook is
    callable on the live instance, and the on-disk pack passes the 11-β
    validator with NO ``advisory_pending_retrofit`` finding remaining
    on ``base_class_conformance``. Closes the goal of the entire 11r-a
    prompt at the integration seam."""
    from adminme.lib.adapters import InboundEnvelope
    from adminme.lib.pack_registry.validator import validate_adapter_pack

    channels = tmp_path / "channels.yaml"
    _write_channels_yaml(channels)

    history = _fixture("history_response.json")
    service = _FakeGmailService(
        profile=_fixture("profile_response.json"),
        history_pages=[history],
        messages={
            "msg-alice": _fixture("message_alice.json"),
            "msg-bob": _fixture("message_bob.json"),
        },
    )
    adapter = _build_gmail_adapter(service=service, max_poll_cycles=2)

    event_log = _RecordingEventLog()
    sup = _supervisor(
        config_path=channels, event_log=event_log, adapters=[adapter]
    )

    await sup.start()
    try:
        # memo 08 §5: the existing inbound emit path stays unchanged.
        # The retrofit must not regress messaging.received emission.
        await _wait_for(
            lambda: len(event_log.by_type("messaging.received")) == 2
        )

        # The _on_inbound hook is callable on the live adapter instance
        # (the ABC abstract method satisfied by an honest no-op stub).
        envelope = InboundEnvelope(
            external_id="msg-integration-test",
            channel="gmail",
            sender="someone@example.test",
            received_at="2026-05-17T00:00:00Z",
            payload={"subject": "from supervisor flow"},
        )
        assert await adapter._on_inbound(envelope) is None
    finally:
        await sup.stop()

    # The on-disk pack passes the 11-β validator with NO
    # advisory_pending_retrofit finding on base_class_conformance.
    result = validate_adapter_pack(GMAIL_PACK_ROOT)
    pending = [
        f
        for f in result.advisories()
        if f.check == "base_class_conformance"
        and "advisory_pending_retrofit" in f.message
    ]
    assert pending == [], (
        f"advisory_pending_retrofit still present post-retrofit: {pending}"
    )
    assert not result.has_errors(), (
        f"validator reports errors post-retrofit: {result.errors()}"
    )


# ---------------------------------------------------------------------------
# 7) Cohort D step 1 (prompt 11a) — real Gmail outbound replaces the 11r-a
#    `send` stub. These three tests exercise the supervisor-loaded
#    GmailAdapter's real send path end-to-end (Phase A, no observation
#    seams propagated by the loader yet; Phase B is the future loader
#    extension covered by the @skip test below).
# ---------------------------------------------------------------------------


async def test_supervisor_loads_gmail_send_path_works_through_loader(
    tmp_path: Path,
) -> None:
    """The supervisor-loaded ``GmailAdapter`` (loaded via the same
    file-path importlib pattern as in production) exposes a real
    :meth:`send` that calls Gmail v1 ``users.messages.send`` against an
    injected fake service and returns a populated :class:`SendResult`.

    The 11r-a `send` stub raised ``NotImplementedError("Phase B")``;
    after 11a c2 the stub is gone — the supervisor's loader path now
    yields an adapter whose ``send`` actually sends, closing the
    Cohort-D-step-1 gap end-to-end at the loader+adapter seam."""
    from adminme.lib.adapters import OutboundMessage, SendResult

    channels = tmp_path / "channels.yaml"
    _write_channels_yaml(channels)

    service = _FakeGmailService(profile=_fixture("profile_response.json"))
    # max_poll_cycles=0 so start() returns after authenticating but
    # leaves the supervisor-bound ``_ctx`` populated for the send call.
    adapter = _build_gmail_adapter(service=service, max_poll_cycles=0)

    captured_send: list[dict[str, Any]] = []

    class _SendExecute:
        def execute(self) -> dict[str, Any]:
            return {"id": "gmail-supervisor-msg-1", "threadId": "thr-sup-1"}

    class _SendMessages:
        def send(self, **kwargs: Any) -> Any:
            captured_send.append(kwargs)
            return _SendExecute()

    class _SendUsers:
        def messages(self) -> Any:
            return _SendMessages()

    class _SendService:
        def users(self) -> Any:
            return _SendUsers()

    event_log = _RecordingEventLog()
    sup = _supervisor(
        config_path=channels, event_log=event_log, adapters=[adapter]
    )

    await sup.start()
    try:
        # Wait for start() to complete so adapter._ctx is populated by
        # the supervisor's ctx_factory (start() then yields immediately
        # because max_poll_cycles=0).
        await _wait_for(
            lambda: len(event_log.by_type("adapter.authenticated")) == 1
        )

        # Override the fake service so the send chain hits our recorder.
        adapter._service = _SendService()
        message = OutboundMessage(
            channel="gmail",
            recipient="alice@example.test",
            body="hello via supervisor",
            metadata={"subject": "Supervisor send"},
        )

        result = await adapter.send(message)

        assert isinstance(result, SendResult)
        assert result.delivered is True
        assert result.external_id == "gmail-supervisor-msg-1"
        assert result.suppressed is False
        # Gmail v1 wire-shape was actually exercised through the
        # supervisor-loaded adapter.
        assert len(captured_send) == 1
        assert captured_send[0]["userId"] == "me"
        assert "raw" in captured_send[0]["body"]

        # ``messaging.sent`` was emitted through the supervisor-bound
        # event log with the existing ``MessagingSentV1`` field set.
        sent = event_log.by_type("messaging.sent")
        assert len(sent) == 1
        payload = sent[0]["payload"]
        assert payload["source_channel"] == "gmail"
        assert payload["to_identifier"] == "alice@example.test"
        assert payload["thread_id"] == "thr-sup-1"
        assert payload["subject"] == "Supervisor send"
        assert payload["body_text"] == "hello via supervisor"
        assert payload["delivery_status"] == "sent"
    finally:
        await sup.stop()


@pytest.mark.skip(
    reason=(
        "Phase B work: the supervisor's adapter loader does not yet "
        "propagate observation-mode seam slots into the adapter "
        "constructor; the Phase-A-conditional `send` path covers the "
        "current loader contract (test (1) above), and the framework "
        "test ``test_gmail_send.test_send_happy_path_with_seams_"
        "populated`` covers the Phase-B-conditional branch with seams "
        "populated directly. This test reserves the integration-seam "
        "slot for the future Phase B loader extension per memo 08 §3.4."
    )
)
async def test_supervisor_seam_slots_propagate_through_loader(
    tmp_path: Path,
) -> None:
    """Phase B contract: the supervisor's adapter loader will populate
    the three ``observation_session`` / ``observation_manager`` /
    ``observation_event_log`` seam slots from the ``AdapterContext`` (or
    a sibling factory) so ``GmailAdapter.send`` routes through
    :func:`adminme.lib.observation.outbound` per [§6.20] without per-
    adapter wiring. Skipped today because the loader does not yet
    populate those slots — kept as the integration-seam placeholder."""


async def test_supervisor_observation_off_send_calls_gmail_directly(
    tmp_path: Path,
) -> None:
    """The Phase-A-conditional contract: when the supervisor-loaded
    adapter has no observation-mode seams populated (today's loader
    behavior), :meth:`send` MUST call the Gmail API directly rather
    than routing through :func:`adminme.lib.observation.outbound`. This
    pins the Phase A behavior at the integration seam — sister of the
    Phase B branch covered by the framework-level
    ``test_send_happy_path_with_seams_populated`` and the @skip Phase B
    integration test above."""
    from adminme.lib.adapters import OutboundMessage

    channels = tmp_path / "channels.yaml"
    _write_channels_yaml(channels)

    service = _FakeGmailService(profile=_fixture("profile_response.json"))
    adapter = _build_gmail_adapter(service=service, max_poll_cycles=0)

    # Sanity check: the supervisor-loaded adapter does NOT have
    # observation-mode seams populated. If a future loader change
    # populates them, this test must be rewritten as the Phase B
    # variant (and the @skip above can lift).
    assert adapter._observation_session is None
    assert adapter._observation_manager is None
    assert adapter._observation_event_log is None

    captured_send: list[dict[str, Any]] = []
    outbound_called: list[Any] = []

    class _SendExecute:
        def execute(self) -> dict[str, Any]:
            return {"id": "gmail-direct-1", "threadId": "thr-direct-1"}

    class _SendMessages:
        def send(self, **kwargs: Any) -> Any:
            captured_send.append(kwargs)
            return _SendExecute()

    class _SendUsers:
        def messages(self) -> Any:
            return _SendMessages()

    class _SendService:
        def users(self) -> Any:
            return _SendUsers()

    event_log = _RecordingEventLog()
    sup = _supervisor(
        config_path=channels, event_log=event_log, adapters=[adapter]
    )

    # Monkey-patch the adapter module's ``outbound`` to a recorder; the
    # Phase-A path must NOT call it.
    async def _recording_outbound(*args: Any, **kwargs: Any) -> Any:
        outbound_called.append((args, kwargs))
        raise AssertionError(
            "outbound() must not be called when observation seams are unset"
        )

    await sup.start()
    try:
        await _wait_for(
            lambda: len(event_log.by_type("adapter.authenticated")) == 1
        )

        adapter._service = _SendService()
        # Patch via the adapter's loaded module (file-path importlib
        # creates a fresh module instance per _load_gmail_module).
        original_outbound = _gmail_mod.outbound
        _gmail_mod.outbound = _recording_outbound
        try:
            await adapter.send(
                OutboundMessage(
                    channel="gmail",
                    recipient="alice@example.test",
                    body="Phase A direct call",
                    metadata={"subject": "Direct"},
                )
            )
        finally:
            _gmail_mod.outbound = original_outbound

        # Gmail API hit; outbound() not.
        assert len(captured_send) == 1
        assert outbound_called == []
    finally:
        await sup.stop()
