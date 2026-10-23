"""A revoked grant cannot turn an uncertain prior effect into 'never called'."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from adminme_bridge.config import BridgeConfig
from adminme_bridge.main import BridgeSupervisor


async def test_revoked_permission_preserves_prior_unknown_attempt(tmp_path):
    command = {
        "schema_version": 2,
        "event_id": "event-fixture",
        "command_id": "command-fixture",
        "member_id": "member-fixture",
        "provider": "apple_notes",
        "provider_account_id": "provider-account:" + "a" * 64,
        "operation": "create_note",
        "payload": {
            "folder_external_id": "fixture-folder",
            "apple_service_permission": {"event_id": "grant-fixture"},
        },
        "domain_approval_event_id": "domain-fixture",
        "openclaw_approval_event_id": "native-fixture",
        "request_fingerprint": "b" * 64,
        "apple_authorization_denied": True,
    }
    config = BridgeConfig(
        member_id="member-fixture",
        enrollment_id="enrollment-fixture",
        central_endpoint="https://fixture.ts.net",
        tailscale_identity="fixture.ts.net",
        configured_packs=["knowledge-apple-notes"],
        enabled_packs=["knowledge-apple-notes"],
        pack_config={"knowledge-apple-notes": {"member_id": "member-fixture"}},
        cursor_db_path=str(tmp_path / "ledger.db"),
        observation_only=True,
        provider_writes="deny",
    )
    received, done = [], asyncio.Event()

    class Ingest:
        async def fetch_commands(self, _providers):
            return {"commands": [command]}

        async def submit(self, envelope):
            received.append(envelope)
            done.set()
            return SimpleNamespace(ok=True)

    class Provider:
        provider = "apple_notes"

        async def apply_write(self, *_args):
            raise AssertionError("uncertain effect was retried")

    supervisor = BridgeSupervisor(
        config=config, ingest_client=Ingest(), adapters=[Provider()]
    )
    assert (
        supervisor._command_ledger.begin(
            command["command_id"], command["request_fingerprint"]
        )
        == "new"
    )
    task = asyncio.create_task(supervisor._command_loop())
    try:
        await asyncio.wait_for(done.wait(), timeout=2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        supervisor._command_ledger.close()
    assert received[0]["payload"]["provider_called"] is True
    assert received[0]["payload"]["result"]["outcome"] == "ambiguous"
    assert received[0]["payload"]["result"]["automatic_retry"] is False
