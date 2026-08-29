from __future__ import annotations

import json
from pathlib import Path

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.schemas.external_sync import LegacyProviderEffectReconciledV1
from bootstrap.contracts import DeploymentPaths
from bootstrap.reconcile_legacy_provider_effect import (
    _legacy_terminal_outcome,
    reconcile_legacy_provider_effect,
)
from bootstrap.secrets import secret_store


def _pair(*, suppressed: object) -> tuple[dict, dict]:
    request = {
        "type": "external_sync.write_requested",
        "payload": {
            "command_id": "command-fixture",
            "member_id": "member-fixture",
            "provider": "google_tasks",
            "provider_account_id": "account@invalid.example",
            "operation": "task.create",
        },
    }
    terminal = {
        "type": "external_sync.write_completed",
        "payload": {
            "command_id": "command-fixture",
            "member_id": "member-fixture",
            "provider": "google_tasks",
            "operation": "task.create",
            "suppressed": suppressed,
        },
    }
    return request, terminal


@pytest.mark.parametrize(
    ("suppressed", "expected"),
    [(True, (False, True)), (False, (True, False))],
)
def test_legacy_terminal_outcome_preserves_terminal_suppression(
    suppressed: bool,
    expected: tuple[bool, bool],
) -> None:
    request, terminal = _pair(suppressed=suppressed)

    assert (
        _legacy_terminal_outcome(
            request,
            terminal,
            command_id="command-fixture",
        )
        == expected
    )


def test_legacy_terminal_outcome_refuses_untyped_suppression() -> None:
    request, terminal = _pair(suppressed="true")

    assert (
        _legacy_terminal_outcome(
            request,
            terminal,
            command_id="command-fixture",
        )
        is None
    )


def test_reconciliation_schema_refuses_contradictory_provider_outcome() -> None:
    with pytest.raises(ValueError, match="inverse of suppressed"):
        LegacyProviderEffectReconciledV1.model_validate(
            {
                "request_event_id": "request-fixture",
                "terminal_event_id": "terminal-fixture",
                "command_id": "command-fixture",
                "provider_account_id": "account@invalid.example",
                "provider_called": True,
                "suppressed": True,
                "legacy_approval_state": "pre_governance_absent",
                "reconciliation_basis": "v1_completed_terminal_evidence",
                "request_fingerprint": "a" * 64,
                "terminal_fingerprint": "b" * 64,
                "reconciled_at": "2026-08-29T03:00:00Z",
            }
        )


@pytest.mark.asyncio
async def test_live_reconciliation_records_suppressed_terminal_without_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    paths.create()
    config = paths.instance_dir / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "adapters.json").write_text(
        json.dumps(
            {
                "adapters": [
                    {
                        "kind": "tasks-google-tasks",
                        "config": {
                            "member_id": "member-fixture",
                            "user_email": "account@invalid.example",
                            "workspace": "personal",
                            "oauth_credentials_ref": "op://fixture/google",
                            "tasklist_id": "@default",
                        },
                    }
                ]
            }
        )
    )
    (config / "member-mapping.json").write_text(
        json.dumps({"members": [{"member_id": "member-fixture", "role": "principal"}]})
    )
    store = secret_store(paths, test_mode=True)
    event_reference = store.put("event-log", b"e" * 32)
    (config / "secret-references.json").write_text(
        json.dumps({"references": {"event_log": event_reference}})
    )
    log = EventLog(paths.instance_dir / "events" / "events.db", b"e" * 32)
    try:
        request_id = await log.append(
            EventEnvelope(
                event_at_ms=1_788_000_000_000,
                tenant_id="tenant-fixture",
                type="external_sync.write_requested",
                schema_version=1,
                occurred_at="2026-08-29T03:00:00Z",
                source_adapter="fixture-request",
                source_account_id="account@invalid.example",
                owner_scope="private:member-fixture",
                visibility_scope="private:member-fixture",
                sensitivity="normal",
                idempotency_key="request-fixture",
                payload={
                    "command_id": "command-fixture",
                    "member_id": "member-fixture",
                    "provider": "google_tasks",
                    "provider_account_id": "account@invalid.example",
                    "operation": "task.create",
                    "payload": {},
                    "requested_at": "2026-08-29T03:00:00Z",
                },
            )
        )
        await log.append(
            EventEnvelope(
                event_at_ms=1_788_000_000_001,
                tenant_id="tenant-fixture",
                type="external_sync.write_completed",
                schema_version=1,
                occurred_at="2026-08-29T03:00:01Z",
                source_adapter="fixture-observation-gate",
                source_account_id="account@invalid.example",
                owner_scope="private:member-fixture",
                visibility_scope="private:member-fixture",
                sensitivity="normal",
                causation_id=request_id,
                idempotency_key="terminal-fixture",
                payload={
                    "command_id": "command-fixture",
                    "member_id": "member-fixture",
                    "provider": "google_tasks",
                    "operation": "task.create",
                    "result": {"reason": "observation_mode_active"},
                    "completed_at": "2026-08-29T03:00:01Z",
                    "suppressed": True,
                },
            )
        )
    finally:
        await log.close()

    result = await reconcile_legacy_provider_effect(
        paths,
        owner_reviewed=True,
        test_mode=True,
    )

    assert result["status"] == "legacy_provider_effect_reconciled"
    assert result["provider_called"] is False
    assert result["suppressed"] is True
    audit_log = EventLog(
        paths.instance_dir / "events" / "events.db",
        b"e" * 32,
        read_only=True,
    )
    try:
        rows = []
        watermark = await audit_log.latest_event_id()
        async for event in audit_log.read_metadata_since(
            through=watermark or "",
            types=["external_sync.legacy_effect_reconciled"],
            include_payload_for=["external_sync.legacy_effect_reconciled"],
        ):
            rows.append(event)
    finally:
        await audit_log.close()
    assert len(rows) == 1
    assert rows[0]["payload"]["provider_called"] is False
    assert rows[0]["payload"]["suppressed"] is True
