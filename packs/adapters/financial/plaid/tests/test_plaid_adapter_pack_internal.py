"""Plaid adapter pack-load canary + auth-path handler-direct tests.

Catches structural drift in ``pack.yaml`` / ``config.schema.json`` /
``config.example.yaml`` and exercises ``PlaidAdapter.start()``'s auth
path directly against fake factories + a stub event log (no live Plaid
client, no supervisor lifecycle, no HTTP).

F-3 rename pattern: unique basename
(``test_plaid_adapter_pack_internal.py``) avoids pytest's
rootdir-relative-module-name collision across the supervised build's
many ``test_*_pack_internal.py`` files.

Commit-2 scope: the auth path only. Sync + webhook + institution-health
test bodies arrive in commits 3-4.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from packs.adapters.financial.plaid.adapter import (
    PlaidAdapter,
    PlaidAdapterError,
)
from adminme.adapters.base import AdapterContext

PACK_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingEventLog:
    """Append-only stub event log for tests. Mirrors the supervisor's
    duck-typed expectation: any object that has ``append_sync(...)``."""

    def __init__(self) -> None:
        self.appended: list[dict[str, Any]] = []

    def append_sync(
        self,
        *,
        type: str,
        schema_version: int,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> None:
        self.appended.append(
            {
                "type": type,
                "schema_version": schema_version,
                "payload": payload,
                "correlation_id": correlation_id,
            }
        )


def _make_ctx(config: dict[str, Any]) -> tuple[AdapterContext, _RecordingEventLog]:
    log = _RecordingEventLog()
    ctx = AdapterContext(
        event_log=log,  # type: ignore[arg-type]
        config=config,
        correlation_id_factory=lambda: "corr-plaid-test",
        now_factory=lambda: datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc),
    )
    return ctx, log


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
    ctx, _log = _make_ctx(
        _valid_config(item_access_token_ref="plaintext-token-leaked")
    )

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
# Commit-3 stubs are honest at import time
# ---------------------------------------------------------------------------


def test_webhook_handler_stub_raises_not_implemented() -> None:
    """The Protocol surface advertises ``webhook_handler``; calling it
    in commit 2 must raise NotImplementedError loudly so a caller who
    wires it up early gets a clear signal rather than a silent no-op."""
    adapter = PlaidAdapter()
    with pytest.raises(NotImplementedError, match="commit 3"):
        asyncio.run(adapter.webhook_handler({"webhook_code": "SYNC_UPDATES_AVAILABLE"}))


def test_sync_transactions_stub_raises_not_implemented() -> None:
    adapter = PlaidAdapter()
    with pytest.raises(NotImplementedError, match="commit 3"):
        asyncio.run(adapter._sync_transactions("item-sandbox-abc"))


def test_fetch_institution_health_stub_raises_not_implemented() -> None:
    adapter = PlaidAdapter()
    with pytest.raises(NotImplementedError, match="commit 4"):
        asyncio.run(adapter._fetch_institution_health("ins_109508"))
