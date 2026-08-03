"""Plaid adapter pack-load canary + auth + sync + webhook handler-direct tests.

Catches structural drift in ``pack.yaml`` / ``config.schema.json`` /
``config.example.yaml`` and exercises ``PlaidAdapter``'s
``start()`` / ``webhook_handler`` / ``_sync_transactions`` against fake
factories + a stub event log + recorded fixtures (no live Plaid client,
no supervisor lifecycle, no HTTP).

F-3 rename pattern: unique basename
(``test_plaid_adapter_pack_internal.py``) avoids pytest's
rootdir-relative-module-name collision across the supervised build's
many ``test_*_pack_internal.py`` files.

Commit-3 scope: auth (carry-forward from c2) + the
``/transactions/sync`` cycle + the webhook dispatcher's two real
branches (``SYNC_UPDATES_AVAILABLE`` triggers a sync;
``ITEM_LOGIN_REQUIRED`` emits ``adapter.error`` at ``severity=fatal``).
Institution-health tests arrive in commit 4.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from packs.adapters.financial.plaid.adapter import (
    APIChangedError,
    AuthError,
    NetworkError,
    PlaidAdapter,
    PlaidAdapterError,
    QuotaExceededError,
    RateLimitError,
    TransientError,
    UnknownError,
)
from adminme.adapters.base import AdapterContext

PACK_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingEventLog:
    """Recording typed asynchronous event sink for tests."""

    def __init__(self) -> None:
        self.appended: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **kwargs: Any) -> str:
        self.appended.append({**envelope.model_dump(), **kwargs})
        return f"event-{len(self.appended)}"


def _make_ctx(
    config: dict[str, Any],
    *,
    now_factory: Any = None,
) -> tuple[AdapterContext, _RecordingEventLog]:
    log = _RecordingEventLog()
    ctx = AdapterContext(
        event_sink=log,  # type: ignore[arg-type]
        config=config,
        correlation_id_factory=lambda: "corr-plaid-test",
        now_factory=now_factory
        or (lambda: datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)),
    )
    return ctx, log


class _FakePlaidClient:
    """Minimal stand-in for the Plaid Python SDK client. Production
    instantiates ``plaid_api.PlaidApi(...)``; tests build this with a
    canned response (or an exception to raise) so the adapter's sync
    loop is exercisable without a live SDK."""

    def __init__(
        self,
        *,
        responses: list[dict[str, Any]] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def transactions_sync(
        self,
        *,
        access_token: str,
        cursor: str,
    ) -> dict[str, Any]:
        self.calls.append({"access_token": access_token, "cursor": cursor})
        if self._raises is not None:
            raise self._raises
        if not self._responses:
            return {
                "added": [],
                "modified": [],
                "removed": [],
                "accounts": [],
                "next_cursor": cursor,
                "has_more": False,
            }
        return self._responses.pop(0)


async def _set_up_adapter_via_start(
    adapter: PlaidAdapter, ctx: AdapterContext
) -> asyncio.Task[None]:
    """Spawn ``start()`` so the auth path runs and ``_ctx`` is captured;
    return the task so the test can ``stop()`` + await afterwards. Use
    in tests that exercise webhook_handler / _sync_transactions
    post-start."""
    task = asyncio.create_task(adapter.start(ctx=ctx))
    for _ in range(20):
        if adapter._stop_event is not None:  # type: ignore[attr-defined]
            break
        await asyncio.sleep(0)
    return task


async def _tear_down(adapter: PlaidAdapter, task: asyncio.Task[None]) -> None:
    await adapter.stop()
    await task


def _valid_config(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "item_access_token_ref": "op://adminme-plaid/ins_109508/access_token",
        "plaid_client_id_ref": "op://adminme-plaid/api-credentials/client_id",
        "plaid_secret_ref": "op://adminme-plaid/api-credentials/secret",
        "webhook_route": "/webhooks/plaid",
        "member_id": "member-001",
        "plaid_item_id": "item-sandbox-abc",
        "institution_id": "ins_109508",
    }
    base.update(overrides)
    return base


async def _start_and_stop(adapter: PlaidAdapter, ctx: AdapterContext) -> None:
    """``start()`` blocks on the stop event; spawn it as a task, give it
    a tick to set up auth, then ``stop()`` so the task can resolve."""
    task = asyncio.create_task(adapter.start(ctx=ctx))
    # Allow start() to reach `_stop_event.wait()`.
    for _ in range(10):
        if adapter._stop_event is not None:  # type: ignore[attr-defined]
            break
        await asyncio.sleep(0)
    await adapter.stop()
    await task


# ---------------------------------------------------------------------------
# Pack-load canary
# ---------------------------------------------------------------------------


def test_pack_yaml_loads_cleanly() -> None:
    """The supervisor's discovery walks ``packs/adapters/<family>/<name>/``;
    ``pack.yaml`` must parse + contain the manifest fields the discovery
    pattern reads."""
    raw = (PACK_ROOT / "pack.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    assert data["pack"]["id"] == "adapter:financial-plaid"
    assert data["pack"]["kind"] == "adapter"
    assert data["pack"]["family"] == "financial"
    assert data["pack"]["version"] == "1.0.0"
    # [§6 invariant 10, D11]: financial adapters are privileged-floor.
    assert data["sensitivity_floor"] == "privileged"
    # Webhook-driven trigger with daily backstop cron.
    assert data["triggers"]["kind"] == "webhook_driven"
    assert data["triggers"]["schedule"] == "0 4 * * *"
    # Emits the 4 expected event types (3 cross-cutting + 1 new).
    declared = set(data["depends_on"]["event_types"])
    assert {
        "adapter.error",
        "adapter.authenticated",
        "adapter.cursor_advanced",
        "money_flow.plaid_synced",
    } <= declared


def test_config_schema_json_loads_and_enforces_privileged_const() -> None:
    """The JSON schema's ``sensitivity_floor`` is a const that mirrors
    the pack's hardcoded class attribute — any other value is rejected at
    load time before the adapter sees the config."""
    raw = (PACK_ROOT / "config.schema.json").read_text(encoding="utf-8")
    schema = json.loads(raw)
    assert schema["properties"]["sensitivity_floor"]["const"] == "privileged"
    # additionalProperties=false rejects keys the loader doesn't recognize.
    assert schema["additionalProperties"] is False
    # The 7 required fields match the prompt §7 deliverables table.
    assert set(schema["required"]) == {
        "item_access_token_ref",
        "plaid_client_id_ref",
        "plaid_secret_ref",
        "webhook_route",
        "member_id",
        "plaid_item_id",
        "institution_id",
    }


def test_config_example_yaml_satisfies_op_ref_patterns() -> None:
    """The example config exists for the operator hand-edit phase before
    the wizard ships. Every secret-bearing field must be an
    ``op://<vault>/<item>/<field>`` reference, never the secret itself."""
    raw = (PACK_ROOT / "config.example.yaml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw)
    for key in (
        "item_access_token_ref",
        "plaid_client_id_ref",
        "plaid_secret_ref",
    ):
        assert isinstance(cfg[key], str)
        assert cfg[key].startswith("op://"), f"{key} must use op:// reference"
    assert cfg["webhook_route"].startswith("/")
    # sensitivity_floor is intentionally omitted from the example (the
    # pack hardcodes it) — confirm the example does NOT try to set it
    # to anything else.
    assert "sensitivity_floor" not in cfg or cfg["sensitivity_floor"] == "privileged"


# ---------------------------------------------------------------------------
# Adapter class-attribute canaries
# ---------------------------------------------------------------------------


def test_plaid_adapter_class_attributes_match_prompt() -> None:
    """Per §7 Deliverables: ``name``, ``family``, ``version``,
    ``sensitivity_floor`` are class-level constants the supervisor reads
    before instantiation."""
    assert PlaidAdapter.name == "financial-plaid"
    assert PlaidAdapter.family == "financial"
    assert PlaidAdapter.version == "1.0.0"
    assert PlaidAdapter.sensitivity_floor == "privileged"


# ---------------------------------------------------------------------------
# Auth path
# ---------------------------------------------------------------------------


def test_start_emits_adapter_authenticated_with_op_ref() -> None:
    """``start()`` must emit one ``adapter.authenticated`` v1 event whose
    ``token_storage_ref`` is the literal op:// reference from config —
    NEVER the resolved secret. The OAuth-style ``scopes`` list reflects
    the read-only permission set the financial adapter requires."""
    captured: dict[str, Any] = {}

    def _factory(client_id: str, secret: str, access_token: str) -> object:
        captured["client_id"] = client_id
        captured["secret"] = secret
        captured["access_token"] = access_token
        return object()

    adapter = PlaidAdapter(plaid_client_factory=_factory)
    ctx, log = _make_ctx(_valid_config())

    asyncio.run(_start_and_stop(adapter, ctx))

    # Factory received the op:// reference strings — not resolved secrets.
    assert captured["access_token"] == "op://adminme-plaid/ins_109508/access_token"
    assert captured["client_id"].startswith("op://")
    assert captured["secret"].startswith("op://")

    # One adapter.authenticated event with the expected shape.
    auth_events = [e for e in log.appended if e["type"] == "adapter.authenticated"]
    assert len(auth_events) == 1
    payload = auth_events[0]["payload"]
    assert payload["adapter_name"] == "financial-plaid"
    assert payload["adapter_family"] == "financial"
    assert payload["token_storage_ref"] == "op://adminme-plaid/ins_109508/access_token"
    assert payload["authenticated_at"] == "2026-05-16T12:00:00Z"
    assert "transactions:read" in payload["scopes"]
    assert auth_events[0]["correlation_id"] == "corr-plaid-test"


def test_start_rejects_sensitivity_floor_downgrade() -> None:
    """[§6 invariant 10, D11]: even though JSON-schema validation
    normally catches a ``sensitivity_floor: normal`` config first,
    ``start()`` re-asserts at runtime as defense in depth."""
    adapter = PlaidAdapter()
    ctx, _log = _make_ctx(_valid_config(sensitivity_floor="normal"))

    with pytest.raises(PlaidAdapterError, match="sensitivity_floor"):
        asyncio.run(adapter.start(ctx=ctx))


def test_start_rejects_non_op_reference_for_access_token() -> None:
    """A raw secret (or anything not matching ``op://...``) is a
    constitutional violation per [§L1 token-storage discipline] — the
    config schema's regex catches this at load time; this is the
    in-adapter belt-and-braces."""
    adapter = PlaidAdapter()
    ctx, _log = _make_ctx(_valid_config(item_access_token_ref="plaintext-token-leaked"))

    with pytest.raises(PlaidAdapterError, match="item_access_token_ref"):
        asyncio.run(adapter.start(ctx=ctx))


def test_start_rejects_missing_webhook_route() -> None:
    adapter = PlaidAdapter()
    config = _valid_config()
    del config["webhook_route"]
    ctx, _log = _make_ctx(config)

    with pytest.raises(PlaidAdapterError, match="webhook_route"):
        asyncio.run(adapter.start(ctx=ctx))


def test_start_invokes_webhook_verifier_factory_with_route() -> None:
    """The webhook-verifier factory receives the configured route at
    ``start()`` so the verifier can register against the supervisor's
    mounted endpoint. Commit 3 will wire the verifier's actual call site
    (the webhook handler); commit 2 just validates the wiring."""
    seen_routes: list[str] = []

    def _verifier_factory(route: str) -> Any:
        seen_routes.append(route)
        return lambda _payload: True

    adapter = PlaidAdapter(webhook_verifier_factory=_verifier_factory)
    ctx, _log = _make_ctx(_valid_config())

    asyncio.run(_start_and_stop(adapter, ctx))

    assert seen_routes == ["/webhooks/plaid"]


# ---------------------------------------------------------------------------
# /transactions/sync — single-page cycle
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict[str, Any]:
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    data = json.loads(raw)
    data.pop("_comment", None)
    return data


def test_sync_transactions_emits_money_flow_plaid_synced_from_fixture() -> None:
    """Happy-path single-page sync. The fake client returns the recorded
    fixture's response shape; the adapter must accumulate counts, persist
    the post-pagination cursor, and emit exactly one
    ``money_flow.plaid_synced`` event whose payload mirrors §7 Deliverables."""
    fixture = _load_fixture("transactions_sync_initial.json")
    fake = _FakePlaidClient(responses=[fixture])

    adapter = PlaidAdapter(plaid_client_factory=lambda *_a: fake)
    ctx, log = _make_ctx(_valid_config())

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await adapter._sync_transactions("item-sandbox-abc", webhook_triggered=False)
        await _tear_down(adapter, task)

    asyncio.run(_go())

    synced = [e for e in log.appended if e["type"] == "money_flow.plaid_synced"]
    assert len(synced) == 1, log.appended
    payload = synced[0]["payload"]
    assert payload["member_id"] == "member-001"
    assert payload["plaid_item_id"] == "item-sandbox-abc"
    assert payload["institution_id"] == "ins_109508"
    assert payload["institution_name"] == "First Platypus Bank"
    assert payload["sync_cursor"] == "cursor-after-initial-sync-abc"
    assert payload["transactions_added"] == 2
    assert payload["transactions_modified"] == 1
    assert payload["transactions_removed"] == 0
    assert payload["accounts_modified_count"] == 2
    assert payload["webhook_triggered"] is False
    assert payload["synced_at"] == "2026-05-16T12:00:00Z"

    # Cursor persisted at the (member_id, plaid_item_id) granularity per
    # BUILD.md PLAID DETAILED SPEC §"Sync cursor".
    assert adapter._cursors[("member-001", "item-sandbox-abc")] == (
        "cursor-after-initial-sync-abc"
    )
    # The fake client saw exactly one call (has_more=false ended pagination).
    assert len(fake.calls) == 1


def test_sync_transactions_paginates_and_persists_post_pagination_cursor() -> None:
    """Multi-page sync: the adapter loops while ``has_more`` is true,
    accumulates counts across pages, and persists only the final
    ``next_cursor`` — not the page-1 cursor."""
    paginated = _load_fixture("transactions_sync_paginated.json")
    fake = _FakePlaidClient(responses=[paginated["page1"], paginated["page2"]])

    adapter = PlaidAdapter(plaid_client_factory=lambda *_a: fake)
    ctx, log = _make_ctx(_valid_config())

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await adapter._sync_transactions("item-sandbox-abc", webhook_triggered=True)
        await _tear_down(adapter, task)

    asyncio.run(_go())

    synced = [e for e in log.appended if e["type"] == "money_flow.plaid_synced"]
    assert len(synced) == 1
    payload = synced[0]["payload"]
    # Page 1 added 2 + page 2 added 1 = 3; page 2 removed 1 = 1.
    assert payload["transactions_added"] == 3
    assert payload["transactions_modified"] == 0
    assert payload["transactions_removed"] == 1
    assert payload["sync_cursor"] == "cursor-final"
    assert payload["webhook_triggered"] is True

    # Two calls — page 1 with the stored cursor (""), page 2 with the
    # page-1 next_cursor ("cursor-page2").
    assert len(fake.calls) == 2
    assert fake.calls[0]["cursor"] == ""
    assert fake.calls[1]["cursor"] == "cursor-page2"

    # Only the final cursor is persisted.
    assert adapter._cursors[("member-001", "item-sandbox-abc")] == "cursor-final"


# ---------------------------------------------------------------------------
# Webhook dispatch
# ---------------------------------------------------------------------------


def test_webhook_handler_sync_updates_available_triggers_transactions_sync() -> None:
    """``SYNC_UPDATES_AVAILABLE`` is the Plaid webhook signal that new
    transactions are ready; the adapter must call ``/transactions/sync``
    with the persisted cursor and emit ``money_flow.plaid_synced`` with
    ``webhook_triggered=True``."""
    fixture = _load_fixture("transactions_sync_initial.json")
    fake = _FakePlaidClient(responses=[fixture])

    adapter = PlaidAdapter(plaid_client_factory=lambda *_a: fake)
    ctx, log = _make_ctx(_valid_config())

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await adapter.webhook_handler(
            {
                "webhook_type": "TRANSACTIONS",
                "webhook_code": "SYNC_UPDATES_AVAILABLE",
                "item_id": "item-sandbox-abc",
                "initial_update_complete": True,
                "historical_update_complete": False,
            }
        )
        await _tear_down(adapter, task)

    asyncio.run(_go())

    synced = [e for e in log.appended if e["type"] == "money_flow.plaid_synced"]
    assert len(synced) == 1
    assert synced[0]["payload"]["webhook_triggered"] is True
    assert len(fake.calls) == 1


def test_webhook_handler_item_login_required_emits_fatal_adapter_error() -> None:
    """``ITEM_LOGIN_REQUIRED`` requires operator re-Link via update mode
    per ``docs/reference/plaid/link-update-mode.md``; retrying without
    re-auth keeps failing forever, so the adapter emits
    ``severity=fatal`` + ``error_kind=auth_failed`` for the supervisor
    to surface."""
    fake = _FakePlaidClient()
    adapter = PlaidAdapter(plaid_client_factory=lambda *_a: fake)
    ctx, log = _make_ctx(_valid_config())

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await adapter.webhook_handler(
            {
                "webhook_type": "ITEM",
                "webhook_code": "ITEM_LOGIN_REQUIRED",
                "item_id": "item-sandbox-abc",
                "error": {
                    "error_type": "ITEM_ERROR",
                    "error_code": "ITEM_LOGIN_REQUIRED",
                },
            }
        )
        await _tear_down(adapter, task)

    asyncio.run(_go())

    # No sync occurred — login required short-circuits the sync path.
    assert fake.calls == []
    errors = [e for e in log.appended if e["type"] == "adapter.error"]
    assert len(errors) == 1
    payload = errors[0]["payload"]
    assert payload["severity"] == "fatal"
    assert payload["error_kind"] == "auth_failed"
    assert payload["adapter_name"] == "financial-plaid"
    assert payload["adapter_family"] == "financial"
    assert "item-sandbox-abc" in payload["error_message"]
    # status.last_error is populated for the supervisor's snapshot.
    assert adapter.status().last_error == payload


def test_webhook_handler_drops_unverified_signature() -> None:
    """Plaid webhook payloads carry a JWT; the adapter's verifier checks
    it before any dispatch. A failing verifier means the payload is
    dropped silently (could be a probe / replay) — no sync, no error
    event."""
    fake = _FakePlaidClient()
    adapter = PlaidAdapter(
        plaid_client_factory=lambda *_a: fake,
        webhook_verifier_factory=lambda _route: lambda _p: False,
    )
    ctx, log = _make_ctx(_valid_config())

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await adapter.webhook_handler(
            {
                "webhook_type": "TRANSACTIONS",
                "webhook_code": "SYNC_UPDATES_AVAILABLE",
                "item_id": "item-sandbox-abc",
            }
        )
        await _tear_down(adapter, task)

    asyncio.run(_go())

    assert fake.calls == []
    assert not [e for e in log.appended if e["type"] == "money_flow.plaid_synced"]
    assert not [e for e in log.appended if e["type"] == "adapter.error"]


# ---------------------------------------------------------------------------
# Cursor-advance milestones
# ---------------------------------------------------------------------------


def test_sync_transactions_emits_cursor_advanced_at_100_event_milestone() -> None:
    """Per the cross-cutting cursor-advance discipline, the adapter emits
    ``adapter.cursor_advanced`` only at milestones — every 100 accumulated
    transactions OR every 5 minutes. A single sync cycle delivering ≥100
    transactions should fire the milestone exactly once."""
    big_added = [
        {"transaction_id": f"txn-{i}", "account_id": "acct-checking-001"}
        for i in range(120)
    ]
    response = {
        "added": big_added,
        "modified": [],
        "removed": [],
        "accounts": [{"account_id": "acct-checking-001"}],
        "item": {"institution_name": "First Platypus Bank"},
        "next_cursor": "cursor-milestone",
        "has_more": False,
    }
    fake = _FakePlaidClient(responses=[response])

    adapter = PlaidAdapter(plaid_client_factory=lambda *_a: fake)
    ctx, log = _make_ctx(_valid_config())

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await adapter._sync_transactions("item-sandbox-abc")
        await _tear_down(adapter, task)

    asyncio.run(_go())

    advances = [e for e in log.appended if e["type"] == "adapter.cursor_advanced"]
    assert len(advances) == 1, log.appended
    payload = advances[0]["payload"]
    assert payload["adapter_name"] == "financial-plaid"
    assert payload["adapter_family"] == "financial"
    assert payload["cursor_kind"] == "transactions_cursor"
    assert payload["cursor_value"] == "cursor-milestone"
    assert payload["events_since_last"] == 120
    # Counter resets on emit so the next sub-100 cycle doesn't double-fire.
    assert adapter._events_since_last_cursor_advance == 0


def test_sync_transactions_skips_cursor_advanced_below_milestone() -> None:
    """A small sync cycle (≪100 events, within the 5-min window) must
    NOT emit ``adapter.cursor_advanced`` — milestone-only discipline."""
    fixture = _load_fixture("transactions_sync_initial.json")
    fake = _FakePlaidClient(responses=[fixture])

    # now_factory pinned so the time-elapsed check stays under 5 minutes
    # for both start() and the post-sync milestone check.
    base = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
    times = iter(
        [base, base, base + timedelta(seconds=30), base + timedelta(seconds=31)]
    )

    def _now() -> datetime:
        try:
            return next(times)
        except StopIteration:
            return base + timedelta(seconds=31)

    adapter = PlaidAdapter(plaid_client_factory=lambda *_a: fake)
    ctx, log = _make_ctx(_valid_config(), now_factory=_now)

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await adapter._sync_transactions("item-sandbox-abc")
        await _tear_down(adapter, task)

    asyncio.run(_go())

    advances = [e for e in log.appended if e["type"] == "adapter.cursor_advanced"]
    assert advances == [], (
        "fewer than 100 events + <5min elapsed should not emit cursor_advanced"
    )


# ---------------------------------------------------------------------------
# Defensive 7-tuple error catch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected_severity", "expected_kind"),
    [
        (TransientError("temporary blip"), "transient", "unknown"),
        (RateLimitError("slow down"), "transient", "rate_limited"),
        (NetworkError("DNS failure"), "transient", "network"),
        (APIChangedError("unknown field"), "transient", "api_changed"),
        (QuotaExceededError("daily cap"), "transient", "quota_exceeded"),
        (AuthError("token expired"), "fatal", "auth_failed"),
        (UnknownError("???"), "transient", "unknown"),
    ],
)
def test_sync_transactions_classifies_plaid_sdk_exceptions(
    exc: BaseException, expected_severity: str, expected_kind: str
) -> None:
    """Each of the 7 Plaid-SDK-style exception classes maps to a single
    deterministic ``(severity, error_kind)`` pair. The defensive catch
    in ``_sync_transactions`` must emit ``adapter.error`` with that pair
    and NOT re-raise (the supervisor's backoff loop handles retries)."""
    fake = _FakePlaidClient(raises=exc)
    adapter = PlaidAdapter(plaid_client_factory=lambda *_a: fake)
    ctx, log = _make_ctx(_valid_config())

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await adapter._sync_transactions("item-sandbox-abc")
        await _tear_down(adapter, task)

    asyncio.run(_go())

    errors = [e for e in log.appended if e["type"] == "adapter.error"]
    assert len(errors) == 1
    payload = errors[0]["payload"]
    assert payload["severity"] == expected_severity
    assert payload["error_kind"] == expected_kind
    assert payload["adapter_name"] == "financial-plaid"
    assert type(exc).__name__ in payload["error_message"]
    # No money_flow event on the error path — sync did not complete.
    assert not [e for e in log.appended if e["type"] == "money_flow.plaid_synced"]


# ---------------------------------------------------------------------------
# Institution health
# ---------------------------------------------------------------------------


class _FakePlaidClientWithInstitutions(_FakePlaidClient):
    """Variant that adds the ``institutions_get_by_id`` method the
    institution-health probe uses. ``status_response`` is the dict the
    real Plaid SDK returns from ``/institutions/get_by_id`` with
    ``include_status=True``; ``raises`` lets a test exercise the
    defensive 7-tuple catch on the institution path."""

    def __init__(
        self,
        *,
        status_response: dict[str, Any] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        super().__init__()
        self._status_response = status_response or {}
        self._status_raises = raises
        self.institution_calls: list[dict[str, Any]] = []

    def institutions_get_by_id(
        self,
        *,
        institution_id: str,
        country_codes: list[str],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        self.institution_calls.append(
            {
                "institution_id": institution_id,
                "country_codes": country_codes,
                "options": options,
            }
        )
        if self._status_raises is not None:
            raise self._status_raises
        return self._status_response


def test_fetch_institution_health_emits_transient_on_degraded_product() -> None:
    """A non-``HEALTHY`` per-product status surfaces as one
    ``adapter.error`` per degraded product, ``severity=transient`` +
    ``error_kind=api_changed`` per the prompt §7 deliverable. ``HEALTHY``
    products produce no events — the adapter must not flood the inbox on
    healthy institutions."""
    status_response = {
        "institution": {
            "institution_id": "ins_109508",
            "name": "First Platypus Bank",
            "status": {
                "item_logins": {"status": "HEALTHY"},
                "transactions_updates": {"status": "DEGRADED"},
                "auth": {"status": "HEALTHY"},
                "identity": {"status": "DOWN"},
            },
        }
    }
    fake = _FakePlaidClientWithInstitutions(status_response=status_response)
    adapter = PlaidAdapter(plaid_client_factory=lambda *_a: fake)
    ctx, log = _make_ctx(_valid_config())

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await _tear_down(adapter, task)

    asyncio.run(_go())

    # The startup probe in start() already fired one institution call;
    # subsequent calls (e.g. from backstop loop) don't happen in this
    # test because stop() races in immediately. Assert the call landed
    # and that exactly the degraded sub-products produced errors.
    assert len(fake.institution_calls) == 1
    call = fake.institution_calls[0]
    assert call["institution_id"] == "ins_109508"
    assert call["options"] == {"include_status": True}

    errors = [e for e in log.appended if e["type"] == "adapter.error"]
    # Two non-HEALTHY entries → two adapter.error events. HEALTHY
    # products do not emit.
    assert len(errors) == 2, [e["payload"] for e in errors]
    products_flagged = sorted(
        e["payload"]["error_message"].split("product ")[1].split(" ")[0] for e in errors
    )
    assert products_flagged == ["'identity'", "'transactions_updates'"]
    for err in errors:
        payload = err["payload"]
        assert payload["severity"] == "transient"
        assert payload["error_kind"] == "api_changed"
        assert payload["adapter_name"] == "financial-plaid"
        assert payload["adapter_family"] == "financial"
        assert "First Platypus Bank" in payload["error_message"]


def test_fetch_institution_health_silent_on_all_healthy() -> None:
    """The common case — all sub-product statuses ``HEALTHY`` — must
    produce zero ``adapter.error`` events. Otherwise the inbox would
    fill with noise from healthy institutions."""
    status_response = {
        "institution": {
            "institution_id": "ins_109508",
            "name": "First Platypus Bank",
            "status": {
                "item_logins": {"status": "HEALTHY"},
                "transactions_updates": {"status": "HEALTHY"},
                "auth": {"status": "HEALTHY"},
            },
        }
    }
    fake = _FakePlaidClientWithInstitutions(status_response=status_response)
    adapter = PlaidAdapter(plaid_client_factory=lambda *_a: fake)
    ctx, log = _make_ctx(_valid_config())

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await _tear_down(adapter, task)

    asyncio.run(_go())

    assert fake.institution_calls, "startup probe should have run"
    assert not [e for e in log.appended if e["type"] == "adapter.error"]


def test_fetch_institution_health_classifies_sdk_exception_defensively() -> None:
    """A Plaid SDK exception inside ``/institutions/get_by_id`` must
    classify through the same 7-tuple as ``/transactions/sync`` and not
    crash the adapter. ``RateLimitError`` → ``transient`` /
    ``rate_limited`` with the cached retry-after hint."""
    fake = _FakePlaidClientWithInstitutions(
        raises=RateLimitError("plaid status endpoint over quota")
    )
    adapter = PlaidAdapter(plaid_client_factory=lambda *_a: fake)
    ctx, log = _make_ctx(_valid_config())

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await _tear_down(adapter, task)

    asyncio.run(_go())

    errors = [e for e in log.appended if e["type"] == "adapter.error"]
    assert len(errors) == 1
    payload = errors[0]["payload"]
    assert payload["severity"] == "transient"
    assert payload["error_kind"] == "rate_limited"
    assert payload["retry_after_s"] == 60
    assert "institutions_get_by_id" in payload["error_message"]
    assert "RateLimitError" in payload["error_message"]
    # Adapter remains running — defensive catch did not propagate.
    assert adapter.status().state == "stopped"  # stop() flipped to stopped


def test_start_skips_institution_probe_when_id_missing() -> None:
    """If ``institution_id`` isn't in the config the startup probe is
    a no-op — the adapter doesn't fabricate an id to call against. The
    common cause is a partially-wired config from the wizard before the
    Link flow completes; the adapter must come up cleanly."""
    fake = _FakePlaidClientWithInstitutions()
    adapter = PlaidAdapter(plaid_client_factory=lambda *_a: fake)
    config = _valid_config()
    del config["institution_id"]
    ctx, log = _make_ctx(config)

    async def _go() -> None:
        task = await _set_up_adapter_via_start(adapter, ctx)
        await _tear_down(adapter, task)

    asyncio.run(_go())

    assert fake.institution_calls == []
    assert not [e for e in log.appended if e["type"] == "adapter.error"]
