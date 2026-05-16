"""Round-trip integration test for prompt 11b — Plaid adapter under the
adapter supervisor.

Spawns the real ``AdapterSupervisor`` from ``adminme.daemons.adapter_supervisor``
against a tmp ``config/channels.yaml`` with a single Plaid entry, hands the
supervisor a loader that returns a configured ``PlaidAdapter`` with fake
``plaid_client_factory`` / ``webhook_verifier_factory`` injections per PM-27,
and exercises two end-to-end paths:

1. **Happy webhook path** — webhook arrives at the adapter's
   ``webhook_handler`` with ``SYNC_UPDATES_AVAILABLE``; the adapter calls
   the fake Plaid client's ``transactions_sync``; one
   ``money_flow.plaid_synced`` event lands in the shared in-memory event
   log; supervisor's ``/status`` snapshot shows the adapter in
   ``running`` state.

2. **ITEM_LOGIN_REQUIRED path** — webhook arrives with
   ``ITEM_LOGIN_REQUIRED``; the adapter emits ``adapter.error``
   ``severity=fatal`` ``error_kind=auth_failed``; supervisor preserves
   its own backoff state (no restart triggered — the adapter task is
   still running on its stop_event wait, so the supervisor's restart
   path doesn't fire). The operator (or the prompt-16 wizard) is
   expected to handle re-Linking.

Marked ``requires_live_services`` per ``tests/integration/README.md`` to
skip in Phase A CI; the supervisor lifecycle is otherwise too heavy for
the unit-test budget and historically flakes on shared-runner timing.
Run manually via ``python -m pytest -m requires_live_services
tests/integration/test_adapter_plaid_supervisor.py``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from adminme.adapters.base import AdapterContext
from adminme.daemons.adapter_supervisor import AdapterSupervisor
from packs.adapters.financial.plaid.adapter import PlaidAdapter

pytestmark = pytest.mark.requires_live_services


class _RecordingEventLog:
    """Minimal append-only stub log shared between adapter + supervisor.
    Production wires a real ``EventLog``; here we record into a list so
    the test can assert end-to-end event emission ordering."""

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


class _FakePlaidClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.transactions_calls: list[dict[str, Any]] = []
        self.institution_calls: list[dict[str, Any]] = []

    def transactions_sync(self, *, access_token: str, cursor: str) -> dict[str, Any]:
        self.transactions_calls.append(
            {"access_token": access_token, "cursor": cursor}
        )
        return self._response

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
        return {
            "institution": {
                "institution_id": institution_id,
                "name": "First Platypus Bank",
                "status": {
                    "item_logins": {"status": "HEALTHY"},
                    "transactions_updates": {"status": "HEALTHY"},
                    "auth": {"status": "HEALTHY"},
                },
            }
        }


def _adapter_config() -> dict[str, Any]:
    return {
        "item_access_token_ref": "op://adminme-plaid/ins_109508/access_token",
        "plaid_client_id_ref": "op://adminme-plaid/api-credentials/client_id",
        "plaid_secret_ref": "op://adminme-plaid/api-credentials/secret",
        "webhook_route": "/webhooks/plaid",
        "member_id": "member-001",
        "plaid_item_id": "item-sandbox-abc",
        "institution_id": "ins_109508",
    }


def _write_channels_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "channels.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                # The /status HTTP endpoint is unused by this test — set
                # port=0 so the supervisor skips ``asyncio.start_server``
                # and the test doesn't bind a real socket.
                "adapter_supervisor_port": 0,
                "adapter_supervisor_host": "127.0.0.1",
                "adapters": [
                    {
                        "kind": "financial-plaid",
                        "family": "financial",
                        "sensitivity_floor": "privileged",
                        "config": _adapter_config(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_supervisor_plaid_webhook_sync_round_trip(tmp_path: Path) -> None:
    """End-to-end: supervisor spawns the Plaid adapter; a webhook
    arrives at the adapter's ``webhook_handler``; the fake Plaid client
    returns one added transaction; one ``money_flow.plaid_synced`` lands
    in the shared event log; supervisor status reports ``running``."""
    log = _RecordingEventLog()
    sync_response = {
        "added": [
            {"transaction_id": "txn-1", "account_id": "acct-1", "amount": -10.0}
        ],
        "modified": [],
        "removed": [],
        "accounts": [{"account_id": "acct-1"}],
        "item": {
            "institution_id": "ins_109508",
            "institution_name": "First Platypus Bank",
        },
        "next_cursor": "cursor-after-webhook-sync",
        "has_more": False,
    }
    fake_plaid = _FakePlaidClient(sync_response)
    adapter = PlaidAdapter(
        plaid_client_factory=lambda *_a: fake_plaid,
        webhook_verifier_factory=lambda _route: (lambda _p: True),
    )

    def _ctx_factory() -> AdapterContext:
        return AdapterContext(
            event_log=log,  # type: ignore[arg-type]
            config=_adapter_config(),
            correlation_id_factory=lambda: "corr-supervisor-plaid",
            now_factory=lambda: datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc),
        )

    supervisor = AdapterSupervisor(
        config_path=_write_channels_yaml(tmp_path),
        ctx_factory=_ctx_factory,
        adapter_loader=lambda _cfg: [adapter],
    )
    await supervisor.start()

    # Give the start() task one tick to reach its stop_event wait so the
    # webhook handler has the adapter context captured.
    for _ in range(20):
        if adapter._stop_event is not None:  # type: ignore[attr-defined]
            break
        await asyncio.sleep(0)

    await adapter.webhook_handler(
        {
            "webhook_type": "TRANSACTIONS",
            "webhook_code": "SYNC_UPDATES_AVAILABLE",
            "item_id": "item-sandbox-abc",
        }
    )

    snapshot = supervisor.snapshot()
    assert snapshot["adapters"]["financial-plaid"]["state"] == "running"

    await supervisor.stop()

    synced = [e for e in log.appended if e["type"] == "money_flow.plaid_synced"]
    assert len(synced) == 1, log.appended
    payload = synced[0]["payload"]
    assert payload["transactions_added"] == 1
    assert payload["webhook_triggered"] is True
    assert payload["sync_cursor"] == "cursor-after-webhook-sync"
    # Auth event landed once during start().
    assert (
        len([e for e in log.appended if e["type"] == "adapter.authenticated"])
        == 1
    )


@pytest.mark.asyncio
async def test_supervisor_plaid_item_login_required_emits_fatal(
    tmp_path: Path,
) -> None:
    """End-to-end: ``ITEM_LOGIN_REQUIRED`` webhook produces
    ``adapter.error`` ``severity=fatal``; supervisor sees the adapter
    still running (no crash, no restart) — the operator surface picks
    up the fatal event and re-runs the prompt-16 Link wizard."""
    log = _RecordingEventLog()
    fake_plaid = _FakePlaidClient(
        response={
            "added": [],
            "modified": [],
            "removed": [],
            "accounts": [],
            "next_cursor": "",
            "has_more": False,
        }
    )
    adapter = PlaidAdapter(
        plaid_client_factory=lambda *_a: fake_plaid,
        webhook_verifier_factory=lambda _route: (lambda _p: True),
    )

    def _ctx_factory() -> AdapterContext:
        return AdapterContext(
            event_log=log,  # type: ignore[arg-type]
            config=_adapter_config(),
            correlation_id_factory=lambda: "corr-supervisor-plaid-fatal",
            now_factory=lambda: datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc),
        )

    supervisor = AdapterSupervisor(
        config_path=_write_channels_yaml(tmp_path),
        ctx_factory=_ctx_factory,
        adapter_loader=lambda _cfg: [adapter],
    )
    await supervisor.start()

    for _ in range(20):
        if adapter._stop_event is not None:  # type: ignore[attr-defined]
            break
        await asyncio.sleep(0)

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

    snapshot = supervisor.snapshot()
    # Adapter is still running — the supervisor doesn't crash-restart on
    # a fatal-event emission; that's a surface concern, not a process
    # concern.
    assert snapshot["adapters"]["financial-plaid"]["state"] == "running"
    assert snapshot["adapters"]["financial-plaid"]["restart_count"] == 0

    await supervisor.stop()

    errors = [
        e
        for e in log.appended
        if e["type"] == "adapter.error" and e["payload"]["severity"] == "fatal"
    ]
    assert len(errors) == 1
    payload = errors[0]["payload"]
    assert payload["error_kind"] == "auth_failed"
    assert payload["adapter_name"] == "financial-plaid"
    # No sync fired on the fatal path.
    assert fake_plaid.transactions_calls == []
