"""Gmail adapter pack — manifest + auth + ingest canaries.

C2 ships:
  1. Pack-load canary — manifest parses, sentinel fields match.
  2. Service-account auth flow (mocked) emits ``adapter.authenticated``.
  3. Sensitivity-floor downgrade attempt is refused per [§6 invariant 10].

C3 adds (recorded-fixture ingest, milestone cursor, defensive error):
  4. ``_run_poll_cycle`` bootstraps the cursor via getProfile on first call.
  5. History → messages.get round trip emits ``messaging.received`` per added msg.
  6. ``adapter.cursor_advanced`` emits only when an event-count milestone fires.
  7. Rate-limit (HTTP 429) emits ``adapter.error`` with severity=transient + retry_after.
  8. Auth failure (HTTP 401) emits ``adapter.error`` severity=fatal and halts loop.

F-3 (10c-iii): basename is unique
(``test_gmail_adapter_pack_internal.py``) so collection from the repo
root does not collide with sibling packs.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from adminme.adapters.base import Adapter, AdapterContext
from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.message_context import authored_message_text

PACK_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root via importlib — the same
    file-path import pattern the pipeline pack-loader uses. Packs are
    not a top-level Python package under poetry (only ``adminme`` is)."""
    spec = importlib.util.spec_from_file_location(
        "gmail_adapter_under_test", PACK_ROOT / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Loaded dynamically — packs/ is not a Python package under poetry's
# package config. mypy gets ``Any`` (the alternative is a TYPE_CHECKING
# import path that doesn't resolve, since packs/adapters/messaging/gmail
# is not on the import path).
_adapter_mod: Any = _load_adapter_module()
GmailAdapter: Any = _adapter_mod.GmailAdapter
GMAIL_READONLY_SCOPE: str = _adapter_mod.GMAIL_READONLY_SCOPE


def _fixture(name: str) -> dict[str, Any]:
    with (FIXTURES / name).open() as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1) pack-load canary
# ---------------------------------------------------------------------------


def test_pack_manifest_loads_cleanly() -> None:
    """``pack.yaml`` parses, declares ``kind: adapter``, names the
    GmailAdapter class, and lists the four event types the adapter
    depends on / emits."""
    manifest_path = PACK_ROOT / "pack.yaml"
    with manifest_path.open() as f:
        manifest = yaml.safe_load(f)

    assert manifest["pack"]["id"] == "adapter:messaging-gmail-api"
    assert manifest["pack"]["kind"] == "adapter"
    assert manifest["pack"]["version"] == "1.3.0"
    assert manifest["runtime"]["entrypoint"] == "adapter.py"
    assert manifest["runtime"]["class"] == "GmailAdapter"
    assert manifest["family"] == "messaging"
    assert manifest["triggers"]["kind"] == "external_poll"
    assert manifest["triggers"]["schedule"] == "*/2 * * * *"

    declared = set(manifest["depends_on"]["event_types"])
    for required in (
        "adapter.error",
        "adapter.authenticated",
        "adapter.cursor_advanced",
        "messaging.received",
    ):
        assert required in declared, f"manifest missing {required!r}"

    # The two sibling config files exist and are referenced.
    assert (PACK_ROOT / manifest["config"]["schema"]).exists()
    assert (PACK_ROOT / manifest["config"]["example"]).exists()


def test_gmail_adapter_satisfies_protocol() -> None:
    """The runtime class implements the L1 Adapter Protocol —
    the supervisor's loader will not accept an instance that doesn't."""
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/vault/item/field",
            "user_email": "sandbox@example.test",
        },
    )
    assert isinstance(adapter, Adapter)
    assert adapter.name == "messaging-gmail-api"
    assert adapter.family == "messaging"
    assert adapter.version == "1.3.0"
    assert adapter.sensitivity_floor == "normal"


# ---------------------------------------------------------------------------
# 2) service-account auth flow (mocked)
# ---------------------------------------------------------------------------


class _FakeEventLog:
    """Captures synchronous append calls for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **kwargs: Any) -> str:
        self.calls.append({**envelope.model_dump(), **kwargs})
        return f"event-{len(self.calls)}"


_FROZEN_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(
    event_log: _FakeEventLog,
    *,
    now: datetime | None = None,
    cursor_store: AdapterCursorStore | None = None,
) -> AdapterContext:
    """Build an AdapterContext with deterministic clock + correlation id."""
    fixed_now = now or _FROZEN_NOW
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-test",
        now_factory=lambda: fixed_now,
        cursor_store=cursor_store,
    )


class _FakeGmailService:
    """A minimal stand-in for the ``googleapiclient`` Gmail service
    object. Implements ``service.users().getProfile()/.history().list()
    /.messages().get()`` chains by returning preconfigured fixture
    responses. ``execute()`` may also be configured to raise."""

    def __init__(
        self,
        *,
        profile: dict[str, Any] | None = None,
        history_pages: list[dict[str, Any]] | None = None,
        backfill_pages: list[dict[str, Any]] | None = None,
        messages: dict[str, dict[str, Any]] | None = None,
        history_raises: Exception | None = None,
        get_profile_raises: Exception | None = None,
        messages_list_gate: threading.Event | None = None,
    ) -> None:
        self._profile = profile
        # Use a queue of responses so paging is testable.
        self._history_pages = list(history_pages or [])
        self._backfill_pages = list(backfill_pages or [])
        self._messages = dict(messages or {})
        self._history_raises = history_raises
        self._get_profile_raises = get_profile_raises
        self._messages_list_gate = messages_list_gate
        self.messages_list_started = threading.Event()
        self.history_list_calls: list[dict[str, Any]] = []
        self.messages_list_calls: list[dict[str, Any]] = []
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

    def list(self, **kwargs: Any) -> Any:
        self._service.messages_list_calls.append(kwargs)
        return _FakeExecute(self._service, kind="messages.list")

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
            if self._service._get_profile_raises is not None:
                raise self._service._get_profile_raises
            return dict(self._service._profile or {})
        if self._kind == "history.list":
            if self._service._history_raises is not None:
                raise self._service._history_raises
            if not self._service._history_pages:
                return {"historyId": "0"}
            return self._service._history_pages.pop(0)
        if self._kind == "messages.list":
            self._service.messages_list_started.set()
            if self._service._messages_list_gate is not None:
                assert self._service._messages_list_gate.wait(timeout=2.0)
            if not self._service._backfill_pages:
                return {"messages": []}
            return self._service._backfill_pages.pop(0)
        if self._kind == "messages.get":
            msg_id = self._params.get("id")
            if msg_id not in self._service._messages:
                raise KeyError(f"unknown message id {msg_id!r}")
            return dict(self._service._messages[msg_id])
        raise AssertionError(f"unhandled fake call {self._kind!r}")


class _FakeHttpResponse:
    """httplib2-style mapping with a ``status`` attribute. Subset
    sufficient to exercise ``GmailAdapter._classify_gmail_error``."""

    def __init__(self, *, status: int, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    def get(self, key: str, default: Any = None) -> Any:
        if key == "status":
            return self.status
        return self._headers.get(key.lower(), default)


class _FakeHttpError(Exception):
    """Duck-type for ``googleapiclient.errors.HttpError`` — adapter
    classification reads ``.resp.status`` and ``.resp.get('retry-after')``."""

    def __init__(self, resp: _FakeHttpResponse, content: bytes = b"") -> None:
        super().__init__(f"HTTP {resp.status}")
        self.resp = resp
        self.content = content


def _seeded_adapter(service: _FakeGmailService) -> Any:
    """Construct a GmailAdapter without going through start(): service
    is pre-injected, cursor pre-seeded so _run_poll_cycle skips bootstrap.
    Used by ingest / cursor / error tests."""
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
        },
    )
    adapter._service = service
    adapter._cursor = "9000"
    adapter._last_advance_at = _FROZEN_NOW
    return adapter


async def test_service_account_auth_emits_authenticated() -> None:
    """``start()`` resolves the service-account ref, builds a fake
    Gmail service, emits ``adapter.authenticated`` v1, then exits the
    polling loop after ``max_poll_cycles=0`` cycles."""
    resolved_credentials: list[str] = []
    built_services: list[tuple[dict[str, Any], list[str], str]] = []

    def fake_resolver(op_ref: str) -> dict[str, Any]:
        resolved_credentials.append(op_ref)
        return {"type": "service_account", "client_email": "fake@fake"}

    def fake_factory(creds: dict[str, Any], scopes: list[str], subject: str) -> object:
        built_services.append((creds, scopes, subject))
        return _FakeGmailService()

    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/vault/gmail/sa",
            "user_email": "sandbox@example.test",
        },
        credentials_resolver=fake_resolver,
        service_factory=fake_factory,
        max_poll_cycles=0,
    )
    event_log = _FakeEventLog()
    await adapter.start(ctx=_ctx(event_log))

    # Resolver was called with the configured op-ref.
    assert resolved_credentials == ["op://test/vault/gmail/sa"]
    # Service factory got the resolved creds + readonly scope + subject.
    assert len(built_services) == 1
    _creds, scopes, subject = built_services[0]
    assert scopes == [GMAIL_READONLY_SCOPE]
    assert subject == "sandbox@example.test"

    # adapter.authenticated was emitted exactly once at v1.
    auth_events = [c for c in event_log.calls if c["type"] == "adapter.authenticated"]
    assert len(auth_events) == 1
    payload = auth_events[0]["payload"]
    assert auth_events[0]["schema_version"] == 1
    assert payload["adapter_name"] == "messaging-gmail-api"
    assert payload["adapter_family"] == "messaging"
    assert payload["token_storage_ref"] == "op://test/vault/gmail/sa"
    assert payload["scopes"] == [GMAIL_READONLY_SCOPE]
    # token_storage_ref is the op-ref — NEVER the raw key.
    assert "PRIVATE KEY" not in payload["token_storage_ref"]
    assert auth_events[0]["correlation_id"] == "corr-test"


async def test_start_rejects_non_op_ref() -> None:
    """A non-op:// ``service_account_key_ref`` is refused at start()
    before any network call — defends against accidental raw-secret
    placement in config."""
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "ya29.A0AfH6SMB_RAW_TOKEN",
            "user_email": "sandbox@example.test",
        },
        credentials_resolver=lambda _r: {},
        service_factory=lambda _c, _s, _u: object(),
    )
    with pytest.raises(ValueError, match="1Password reference"):
        await adapter.start(ctx=_ctx(_FakeEventLog()))


# ---------------------------------------------------------------------------
# 3) sensitivity-floor invariant [§6 invariant 10]
# ---------------------------------------------------------------------------


class _PrivilegedGmailAdapter(GmailAdapter):
    """Hypothetical privileged-by-default Gmail subclass — e.g., the
    law-practice email pattern. Used only to exercise the downgrade
    refusal; real privileged adapters live in 11b/c/d or later."""

    HARDCODED_FLOOR = "privileged"  # type: ignore[assignment]


def test_sensitivity_floor_config_can_raise_to_privileged() -> None:
    """Raising the floor in config is allowed — config sets
    'privileged' on a 'normal'-default adapter."""
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
            "sensitivity_floor": "privileged",
        },
    )
    assert adapter.sensitivity_floor == "privileged"


def test_sensitivity_floor_cannot_be_lowered_from_privileged() -> None:
    """Lowering 'privileged' to 'normal' via config is refused per
    SYSTEM_INVARIANTS.md §6 invariant 10. The constructor raises before
    any auth / network work runs."""
    with pytest.raises(ValueError, match="downgrade"):
        _PrivilegedGmailAdapter(
            config={
                "service_account_key_ref": "op://test/v/i/f",
                "user_email": "sandbox@example.test",
                "sensitivity_floor": "normal",
            },
        )


def test_sensitivity_floor_rejects_unknown_value() -> None:
    """Unknown floor strings are refused — defends against typos /
    fabricated values bleeding into the supervisor's adapter set."""
    with pytest.raises(ValueError, match="must be"):
        GmailAdapter(
            config={
                "service_account_key_ref": "op://test/v/i/f",
                "user_email": "sandbox@example.test",
                "sensitivity_floor": "high",
            },
        )


# ---------------------------------------------------------------------------
# 4) C3 — ingest, cursor advance, error path
# ---------------------------------------------------------------------------


async def test_first_poll_cycle_bootstraps_cursor_via_get_profile() -> None:
    """On the first cycle (no cursor), the adapter MUST snapshot the
    mailbox's current historyId before it performs the bounded recent-history
    query.  The snapshot closes the race with mail that arrives during the
    backfill."""
    service = _FakeGmailService(profile=_fixture("profile_response.json"))
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
        },
    )
    adapter._service = service
    event_log = _FakeEventLog()

    emitted = await adapter._run_poll_cycle(ctx=_ctx(event_log))

    assert emitted == 0
    assert adapter._cursor == "9000"
    assert len(service.get_profile_calls) == 1
    assert service.get_profile_calls[0]["userId"] == "me"
    # The bounded query runs after the cursor snapshot. This empty fixture
    # emits no message observations and does not perform incremental history.
    assert service.history_list_calls == []
    assert service.messages_get_calls == []
    assert service.messages_list_calls == [
        {
            "userId": "me",
            "q": "newer_than:730d -label:drafts",
            "includeSpamTrash": False,
            "maxResults": 500,
        }
    ]
    assert event_log.calls == []


async def test_initial_backfill_pages_emits_and_is_durable(tmp_path: Path) -> None:
    """The initial mailbox query pages to its configured cap, emits normal
    typed observations, and never runs again once its content-free durable
    policy marker has been recorded."""
    store = AdapterCursorStore(tmp_path / "gmail-cursors.sqlite3")
    service = _FakeGmailService(
        profile=_fixture("profile_response.json"),
        backfill_pages=[
            {"messages": [{"id": "msg-alice"}], "nextPageToken": "page-2"},
            {"messages": [{"id": "msg-bob"}]},
        ],
        history_pages=[{"historyId": "9000", "history": []}],
        messages={
            "msg-alice": _fixture("message_alice.json"),
            "msg-bob": _fixture("message_bob.json"),
        },
    )
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
            "initial_backfill_days": 365,
            "initial_backfill_max_messages": 2,
        },
    )
    adapter._service = service
    adapter._cursor_id = "gmail-test"
    adapter._last_advance_at = _FROZEN_NOW
    event_log = _FakeEventLog()
    ctx = _ctx(event_log, cursor_store=store)

    assert await adapter._run_poll_cycle(ctx=ctx) == 2
    assert adapter._cursor == "9000"
    assert [call["maxResults"] for call in service.messages_list_calls] == [2, 1]
    assert service.messages_list_calls[0]["q"] == "newer_than:365d -label:drafts"
    assert sorted(
        call["payload"]["native_message_id"]
        for call in event_log.calls
        if call["type"] == "messaging.received"
    ) == ["msg-alice", "msg-bob"]
    policy = adapter._initial_backfill_settings()
    backfill_id = adapter._backfill_cursor_id("gmail-test", policy=policy)
    assert store.get(backfill_id).provider_watermark == "complete"
    assert adapter.status().extra["initial_backfill"] == {
        "contract": "gmail-initial-backfill-v2",
        "state": "complete",
        "recent_days": 365,
        "max_messages": 2,
        "seed_query_count": 0,
        "discovered": 2,
        "emitted": 2,
        "quarantined": 0,
        "seed_discovered": 0,
        "related_discovered": 0,
        "recent_discovered": 2,
        "related_participant_count": 0,
        "related_participants_truncated": False,
        "truncated": False,
    }

    # The next cycle uses incremental history and does not list or re-fetch
    # any backfill message.
    assert await adapter._run_poll_cycle(ctx=ctx) == 0
    assert len(service.messages_list_calls) == 2
    assert len(service.messages_get_calls) == 2
    assert len(service.history_list_calls) == 1
    store.close()


async def test_invalid_internal_date_is_quarantined_without_message_fact() -> None:
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
        },
    )
    message = {**_fixture("message_alice.json"), "internalDate": "not-a-date"}
    event_log = _FakeEventLog()

    emitted, quarantined = await adapter._ingest_message_ids(
        ctx=_ctx(event_log),
        store=None,
        cursor_id="gmail-invalid-source-time",
        message_ids=[str(message["id"])],
        provider_watermark="fixture-watermark",
        prefetched_messages={str(message["id"]): message},
    )

    assert (emitted, quarantined) == (0, 1)
    assert [call["type"] for call in event_log.calls] == ["adapter.record.quarantined"]


async def test_initial_backfill_provider_io_does_not_block_runtime_loop() -> None:
    """A slow synchronous Gmail list call cannot starve central runtime health."""
    gate = threading.Event()
    service = _FakeGmailService(
        profile=_fixture("profile_response.json"),
        backfill_pages=[{"messages": []}],
        messages_list_gate=gate,
    )
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
            "initial_backfill_days": 30,
            "initial_backfill_max_messages": 1,
        },
    )
    adapter._service = service
    adapter._last_advance_at = _FROZEN_NOW

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    cycle = asyncio.create_task(adapter._run_poll_cycle(ctx=_ctx(_FakeEventLog())))
    try:
        await asyncio.sleep(0.02)
        assert loop.time() - started_at < 0.5
        assert service.messages_list_started.is_set()
        assert not cycle.done()
    finally:
        gate.set()

    assert await asyncio.wait_for(cycle, timeout=1.0) == 0


async def test_initial_backfill_hard_cap_reports_truncation() -> None:
    service = _FakeGmailService(
        profile=_fixture("profile_response.json"),
        backfill_pages=[
            {
                "messages": [{"id": "msg-alice"}, {"id": "msg-bob"}],
                "nextPageToken": "more",
            }
        ],
        messages={"msg-alice": _fixture("message_alice.json")},
    )
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
            "initial_backfill_days": 30,
            "initial_backfill_max_messages": 1,
        },
    )
    adapter._service = service
    adapter._last_advance_at = _FROZEN_NOW

    assert await adapter._run_poll_cycle(ctx=_ctx(_FakeEventLog())) == 1
    assert adapter.status().extra["initial_backfill"]["truncated"] is True
    assert [call["id"] for call in service.messages_get_calls] == ["msg-alice"]


async def test_initial_backfill_repeated_page_token_fails_bounded() -> None:
    service = _FakeGmailService(
        backfill_pages=[
            {"messages": [], "nextPageToken": "same"},
            {"messages": [], "nextPageToken": "same"},
        ]
    )
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
        },
    )
    adapter._service = service

    message_ids, truncated = adapter._list_message_ids(query="newer_than:30d", limit=10)

    assert message_ids == []
    assert truncated is True
    assert len(service.messages_list_calls) == 2


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("initial_backfill_days", True),
        ("initial_backfill_days", 3651),
        ("initial_backfill_max_messages", False),
        ("initial_backfill_max_messages", 5001),
        ("historical_seed_queries", "subject:fixture"),
        ("historical_seed_queries", ["line one\nline two"]),
        ("historical_seed_max_messages_per_query", 501),
        ("historical_related_max_participants", True),
        ("historical_related_messages_per_participant", 101),
        ("historical_related_context_days", 3651),
    ],
)
def test_initial_backfill_config_rejects_unsafe_values(key: str, value: object) -> None:
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
            key: value,
        },
    )
    with pytest.raises(ValueError, match=key):
        adapter._initial_backfill_settings()


async def test_historical_seed_precedes_related_context_and_recent_fill(
    tmp_path: Path,
) -> None:
    """A reviewed old matter wins the cap, expands through exact participants,
    and leaves only the remaining capacity for generic recent mail. Private
    query text is absent from both the policy cursor and runtime status.
    """
    store = AdapterCursorStore(tmp_path / "gmail-seed-cursors.sqlite3")
    fixture_messages = {
        "msg-alice": _fixture("message_alice.json"),
        "msg-bob": _fixture("message_bob.json"),
        "msg-related": {**_fixture("message_alice.json"), "id": "msg-related"},
        "msg-recent": {**_fixture("message_bob.json"), "id": "msg-recent"},
    }
    service = _FakeGmailService(
        profile=_fixture("profile_response.json"),
        backfill_pages=[
            # Gmail search commonly returns newest-first.
            {"messages": [{"id": "msg-bob"}, {"id": "msg-alice"}]},
            {"messages": [{"id": "msg-related"}]},
            {"messages": [{"id": "msg-recent"}]},
        ],
        messages=fixture_messages,
    )
    private_query = "subject:fixture-matter"
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
            "initial_backfill_days": 10,
            "initial_backfill_max_messages": 4,
            "historical_seed_queries": [private_query],
            "historical_seed_max_messages_per_query": 2,
            "historical_related_max_participants": 1,
            "historical_related_messages_per_participant": 1,
            "historical_related_context_days": 30,
        },
    )
    adapter._service = service
    adapter._cursor_id = "gmail-private-fixture"
    adapter._last_advance_at = _FROZEN_NOW
    event_log = _FakeEventLog()

    assert await adapter._run_poll_cycle(ctx=_ctx(event_log, cursor_store=store)) == 4
    assert [call["q"] for call in service.messages_list_calls] == [
        f"{private_query} -label:drafts",
        'after:2024/05/06 before:2024/07/06 "alice@example.test" -label:drafts',
        "newer_than:10d -label:drafts",
    ]
    assert [call["id"] for call in service.messages_get_calls] == [
        "msg-bob",
        "msg-alice",
        "msg-related",
        "msg-recent",
    ]
    assert [
        call["payload"]["native_message_id"]
        for call in event_log.calls
        if call["type"] == "messaging.received"
    ] == ["msg-recent", "msg-related", "msg-alice", "msg-bob"]
    status = adapter.status().extra["initial_backfill"]
    assert status == {
        "contract": "gmail-initial-backfill-v2",
        "state": "complete",
        "recent_days": 10,
        "max_messages": 4,
        "seed_query_count": 1,
        "discovered": 4,
        "emitted": 4,
        "quarantined": 0,
        "seed_discovered": 2,
        "related_discovered": 1,
        "recent_discovered": 1,
        "related_participant_count": 1,
        "related_participants_truncated": True,
        "truncated": True,
    }
    policy = adapter._initial_backfill_settings()
    backfill_id = adapter._backfill_cursor_id("gmail-private-fixture", policy=policy)
    assert private_query not in backfill_id
    assert private_query not in json.dumps(status, sort_keys=True)
    changed_query_adapter = GmailAdapter(
        config={**adapter._config, "historical_seed_queries": ["subject:other"]}
    )
    assert (
        changed_query_adapter._backfill_cursor_id(
            "gmail-private-fixture",
            policy=changed_query_adapter._initial_backfill_settings(),
        )
        != backfill_id
    )
    assert store.get(backfill_id).provider_watermark == "complete"
    store.close()


async def test_history_sync_emits_messaging_received_per_added_message() -> None:
    """A history page with two ``messagesAdded`` rows triggers two
    ``users.messages.get`` calls and two ``messaging.received`` events
    with body/subject/from/to derived from the fixture."""
    history = _fixture("history_response.json")
    service = _FakeGmailService(
        history_pages=[history],
        messages={
            "msg-alice": _fixture("message_alice.json"),
            "msg-bob": _fixture("message_bob.json"),
        },
    )
    adapter = _seeded_adapter(service)
    event_log = _FakeEventLog()

    emitted = await adapter._run_poll_cycle(ctx=_ctx(event_log))

    # Fixture has msg-alice listed twice (dedup expected) + msg-bob once.
    assert emitted == 2
    # Cursor advances to the response's historyId.
    assert adapter._cursor == "9002"
    # history.list called with startHistoryId at the seeded cursor.
    assert len(service.history_list_calls) == 1
    assert service.history_list_calls[0]["startHistoryId"] == "9000"
    # messages.get called twice (deduped), format=full.
    fetched_ids = [c["id"] for c in service.messages_get_calls]
    assert sorted(fetched_ids) == ["msg-alice", "msg-bob"]
    assert all(c["format"] == "full" for c in service.messages_get_calls)

    received = [c for c in event_log.calls if c["type"] == "messaging.received"]
    assert len(received) == 2
    by_thread = {c["payload"]["thread_id"]: c["payload"] for c in received}

    alice = by_thread["thr-alice"]
    assert alice["source_channel"] == "gmail"
    assert alice["from_identifier"] == "alice@example.test"
    assert alice["to_identifier"] == "sandbox@example.test"
    assert alice["subject"] == "Quick note"
    assert alice["body_text"] == "Hi sandbox"
    assert alice["body_html"] is None
    # 1717545600000 ms = 2024-06-05T00:00:00Z
    assert alice["received_at"] == "2024-06-05T00:00:00Z"
    assert alice["attachments"] == []

    bob = by_thread["thr-bob"]
    assert bob["from_identifier"] == "bob@example.test"
    assert bob["body_text"] == "Hello plain world"
    assert bob["body_html"] == "<p>Hello html world</p>"


async def test_history_sync_preserves_sent_direction() -> None:
    sent_message = _fixture("message_alice.json")
    sent_message["id"] = "msg-sent"
    sent_message["threadId"] = "thr-sent"
    sent_message["labelIds"] = ["SENT"]
    for header in sent_message["payload"]["headers"]:
        if header["name"] == "From":
            header["value"] = "sandbox@example.test"
        elif header["name"] == "To":
            header["value"] = "recipient@example.test"
    service = _FakeGmailService(
        history_pages=[
            {
                "historyId": "9003",
                "history": [
                    {
                        "id": "9003",
                        "messagesAdded": [{"message": {"id": "msg-sent"}}],
                    }
                ],
            }
        ],
        messages={"msg-sent": sent_message},
    )
    adapter = _seeded_adapter(service)
    event_log = _FakeEventLog()

    emitted = await adapter._run_poll_cycle(ctx=_ctx(event_log))

    assert emitted == 1
    [sent] = [call for call in event_log.calls if call["type"] == "messaging.sent"]
    assert sent["schema_version"] == 4
    assert sent["payload"]["to_identifier"] == "recipient@example.test"
    assert sent["payload"]["body_text"] == "Hi sandbox"
    assert sent["payload"]["body_html"] is None
    assert sent["payload"]["native_message_id"] == "msg-sent"
    assert not [
        call for call in event_log.calls if call["type"] == "messaging.received"
    ]


async def test_sent_multipart_history_preserves_html_quote_boundary() -> None:
    sent_message = _fixture("message_bob.json")
    sent_message["id"] = "msg-sent-multipart"
    sent_message["threadId"] = "thr-sent-multipart"
    sent_message["labelIds"] = ["SENT"]
    for header in sent_message["payload"]["headers"]:
        if header["name"] == "From":
            header["value"] = "sandbox@example.test"
        elif header["name"] == "To":
            header["value"] = "recipient@example.test"
    quoted_text = "I will authorize the old fixture transfer."
    sent_message["payload"]["parts"][0]["body"]["data"] = base64.urlsafe_b64encode(
        quoted_text.encode()
    ).decode()
    sent_message["payload"]["parts"][1]["body"]["data"] = base64.urlsafe_b64encode(
        f'<div class="gmail_quote">{quoted_text}</div>'.encode()
    ).decode()
    service = _FakeGmailService(
        history_pages=[
            {
                "historyId": "9004",
                "history": [
                    {
                        "id": "9004",
                        "messagesAdded": [{"message": {"id": "msg-sent-multipart"}}],
                    }
                ],
            }
        ],
        messages={"msg-sent-multipart": sent_message},
    )
    adapter = _seeded_adapter(service)
    event_log = _FakeEventLog()

    await adapter._run_poll_cycle(ctx=_ctx(event_log))

    [sent] = [call for call in event_log.calls if call["type"] == "messaging.sent"]
    assert sent["schema_version"] == 4
    assert sent["payload"]["body_text"] == quoted_text
    assert "gmail_quote" in sent["payload"]["body_html"]
    authored = authored_message_text(sent["payload"])
    assert authored.text == ""
    assert authored.quoted_history_removed is True


async def test_cursor_advanced_emits_on_event_milestone() -> None:
    """When the events-since-last-advance counter hits the milestone
    threshold (100), the adapter MUST emit ``adapter.cursor_advanced``
    v1 with the new cursor value, then reset the counter."""
    history = _fixture("history_response.json")
    service = _FakeGmailService(
        history_pages=[history],
        messages={
            "msg-alice": _fixture("message_alice.json"),
            "msg-bob": _fixture("message_bob.json"),
        },
    )
    adapter = _seeded_adapter(service)
    # Pre-seed counter just below the milestone so the cycle's 2 new
    # events trips it (98 + 2 = 100, milestone is >=).
    adapter._events_since_advance = adapter.CURSOR_MILESTONE_EVENTS - 2
    event_log = _FakeEventLog()

    await adapter._run_poll_cycle(ctx=_ctx(event_log))

    advances = [c for c in event_log.calls if c["type"] == "adapter.cursor_advanced"]
    assert len(advances) == 1
    payload = advances[0]["payload"]
    assert payload["adapter_name"] == "messaging-gmail-api"
    assert payload["adapter_family"] == "messaging"
    assert payload["cursor_kind"] == "history_id"
    assert payload["cursor_value"] == "9002"
    assert payload["events_since_last"] == 100
    assert advances[0]["schema_version"] == 1
    # Counter was reset on emission.
    assert adapter._events_since_advance == 0
    assert adapter._last_advance_at == _FROZEN_NOW


async def test_cursor_advanced_skipped_below_milestone() -> None:
    """Anti-vision §"Quiet by default": with only a handful of events
    in the cycle and the clock not yet past the 5-minute milestone,
    the adapter MUST NOT emit ``adapter.cursor_advanced``."""
    history = _fixture("history_response.json")
    service = _FakeGmailService(
        history_pages=[history],
        messages={
            "msg-alice": _fixture("message_alice.json"),
            "msg-bob": _fixture("message_bob.json"),
        },
    )
    adapter = _seeded_adapter(service)
    event_log = _FakeEventLog()

    await adapter._run_poll_cycle(ctx=_ctx(event_log))

    advances = [c for c in event_log.calls if c["type"] == "adapter.cursor_advanced"]
    assert advances == []
    # Counter reflects the 2 emitted events.
    assert adapter._events_since_advance == 2


async def test_cursor_advanced_emits_on_time_milestone() -> None:
    """If wall-clock elapsed since the last advance exceeds 5 minutes
    AND at least one event was emitted in the cycle, the adapter emits
    ``adapter.cursor_advanced`` even though the event count is below
    100."""
    history = _fixture("history_response.json")
    service = _FakeGmailService(
        history_pages=[history],
        messages={
            "msg-alice": _fixture("message_alice.json"),
            "msg-bob": _fixture("message_bob.json"),
        },
    )
    adapter = _seeded_adapter(service)
    # Pretend the last advance was 6 minutes ago — well past the 5-min
    # milestone window.
    adapter._last_advance_at = _FROZEN_NOW - timedelta(minutes=6)
    event_log = _FakeEventLog()

    await adapter._run_poll_cycle(ctx=_ctx(event_log))

    advances = [c for c in event_log.calls if c["type"] == "adapter.cursor_advanced"]
    assert len(advances) == 1


async def test_rate_limit_emits_adapter_error_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 response from history.list emits ``adapter.error`` with
    severity=transient, error_kind=rate_limited, and retry_after_s
    parsed from the Retry-After header. The loop continues (not fatal)."""
    error = _FakeHttpError(
        resp=_FakeHttpResponse(status=429, headers={"Retry-After": "30"})
    )
    service = _FakeGmailService(history_raises=error)
    adapter = _seeded_adapter(service)
    event_log = _FakeEventLog()

    # Stub asyncio.sleep so the loop's post-cycle wait is instant AND
    # flips stop_requested so the loop exits after one cycle. Without
    # this the loop would block for POLL_INTERVAL_S seconds.
    async def _instant_sleep(_seconds: float) -> None:
        adapter._stop_requested = True

    monkeypatch.setattr(_adapter_mod.asyncio, "sleep", _instant_sleep)

    await adapter._poll_loop(ctx=_ctx(event_log))

    errors = [c for c in event_log.calls if c["type"] == "adapter.error"]
    assert len(errors) == 1
    payload = errors[0]["payload"]
    assert payload["adapter_name"] == "messaging-gmail-api"
    assert payload["adapter_family"] == "messaging"
    assert payload["severity"] == "transient"
    assert payload["error_kind"] == "rate_limited"
    assert payload["retry_after_s"] == 30
    assert errors[0]["schema_version"] == 1
    # Loop did NOT crash; status reflects backoff (transient).
    assert adapter._status.state == "backoff"


async def test_auth_failure_emits_fatal_and_halts_loop() -> None:
    """A 401 response classifies as fatal — adapter emits
    ``adapter.error`` severity=fatal, transitions to ``crashed`` state,
    and exits the loop so the supervisor can restart-with-backoff."""
    error = _FakeHttpError(resp=_FakeHttpResponse(status=401))
    service = _FakeGmailService(history_raises=error)
    adapter = _seeded_adapter(service)
    event_log = _FakeEventLog()

    await adapter._poll_loop(ctx=_ctx(event_log))

    errors = [c for c in event_log.calls if c["type"] == "adapter.error"]
    assert len(errors) == 1
    assert errors[0]["payload"]["severity"] == "fatal"
    assert errors[0]["payload"]["error_kind"] == "auth_failed"
    assert adapter._status.state == "crashed"
