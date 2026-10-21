"""A sealed self-service read ceiling is enforced at the provider-process edge."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from adminme.bridge_authorization import BRIDGE_PACK_POLICIES
from adminme_bridge.config import BridgeConfig
from adminme_bridge.main import BridgeSupervisor


def _base(tmp_path: Path) -> dict:
    return {
        "member_id": "member-fixture",
        "enrollment_id": "enrollment-fixture",
        "central_endpoint": "https://fixture.ts.net",
        "tailscale_identity": "member-fixture.ts.net",
        "cursor_db_path": str(tmp_path / "cursors.db"),
    }


def test_old_non_wizard_configuration_retains_existing_write_policy(
    tmp_path: Path,
) -> None:
    config = BridgeConfig.model_validate(_base(tmp_path))
    assert config.observation_only is None
    assert config.provider_writes is None
    assert BridgeConfig.model_validate(config.model_dump()) == config


@pytest.mark.parametrize(
    "ceiling",
    [
        {"observation_only": True},
        {"provider_writes": "deny"},
        {"observation_only": False, "provider_writes": "deny"},
        {"observation_only": "true", "provider_writes": "deny"},
        {"observation_only": 1, "provider_writes": "deny"},
        {"observation_only": True, "provider_writes": "allow"},
    ],
)
def test_explicit_ceiling_cannot_be_missing_coerced_or_widened(
    tmp_path, ceiling
) -> None:
    with pytest.raises(ValidationError):
        BridgeConfig.model_validate({**_base(tmp_path), **ceiling})


def test_only_scoped_wizard_requires_its_sealed_read_only_restrictions(
    tmp_path: Path,
) -> None:
    value = {
        **_base(tmp_path),
        "wizard_contract": "administrateme-member-bridge-wizard-v1",
        "request_id": "request-fixture",
        "instance_id": "tenant-fixture",
        "release_commit": "a" * 40,
        "capability_set_sha256": "b" * 64,
        "requested_capabilities": ["apple_calendar"],
    }
    ordinary = BridgeConfig.model_validate(value)
    assert ordinary.observation_only is None
    assert ordinary.provider_writes is None
    assert BridgeConfig.model_validate(ordinary.model_dump()) == ordinary
    value["acquisition_policy"] = "cos-member-source-observation-v1"
    with pytest.raises(ValidationError, match="sealed read-only ceiling"):
        BridgeConfig.model_validate(value)
    config = BridgeConfig.model_validate(
        {**value, "observation_only": True, "provider_writes": "deny"}
    )
    assert config.observation_only is True
    assert config.provider_writes == "deny"


async def test_read_only_bridge_audits_injected_command_without_provider_call(
    tmp_path: Path,
) -> None:
    command = {
        "schema_version": 2,
        "command_id": "command-fixture",
        "event_id": "event-command-fixture",
        "member_id": "member-fixture",
        "provider": "apple_calendar",
        "provider_account_id": "provider-account:" + "a" * 32,
        "operation": "create_event",
        "payload": {"calendar_identifier": "calendar-fixture"},
        "domain_approval_event_id": "domain-fixture",
        "openclaw_approval_event_id": "native-fixture",
        "request_fingerprint": "c" * 64,
    }
    recorded = []
    submitted = asyncio.Event()

    class Provider:
        provider = "apple_calendar"
        calls = 0

        async def apply_write(self, *args):
            self.calls += 1
            raise AssertionError("read-only bridge reached the provider")

    class Ingest:
        async def fetch_commands(self, providers):
            return {"commands": [command]}

        async def submit(self, envelope):
            recorded.append(envelope)
            submitted.set()
            return SimpleNamespace(ok=True)

    provider = Provider()
    pack = "calendaring-apple-calendar-bridge"
    source_config = {
        **_base(tmp_path),
        "configured_packs": [pack],
        "enabled_packs": [pack],
        "pack_config": {
            pack: {
                "member_id": "member-fixture",
                "provider_account_id": command["provider_account_id"],
                "deployment_target": "bridge",
                "workspace": "personal",
                "binding_provenance": "member-bridge-enrollment",
                "ownership_attested": True,
                "calendar_identifier": "calendar-fixture",
                "allowed_container_ids": ["calendar-fixture"],
                "shared_container_ids": [],
                "permission_scope": sorted(
                    BRIDGE_PACK_POLICIES[pack].permission_scopes
                ),
            }
        },
    }
    legacy = BridgeSupervisor(
        config=BridgeConfig.model_validate(source_config),
        ingest_client=Ingest(),  # type: ignore[arg-type]
        adapters=[provider],  # type: ignore[list-item]
    )
    try:
        # Prove the same exact command passes the existing edge guards; only
        # the sealed enrollment ceiling distinguishes the denied execution.
        assert (
            legacy._authorized_command_adapter(command, {provider.provider: provider})
            is provider
        )
    finally:
        legacy._command_ledger.close()
    config = BridgeConfig.model_validate(
        {**source_config, "observation_only": True, "provider_writes": "deny"}
    )
    supervisor = BridgeSupervisor(
        config=config,
        ingest_client=Ingest(),  # type: ignore[arg-type]
        adapters=[provider],  # type: ignore[list-item]
    )
    task = asyncio.create_task(supervisor._command_loop())
    try:
        await asyncio.wait_for(submitted.wait(), timeout=2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        supervisor._command_ledger.close()
    assert provider.calls == 0
    assert len(recorded) == 1
    result = recorded[0]
    assert result["type"] == "external_sync.write_failed"
    assert result["payload"]["provider_called"] is False
    assert result["payload"]["retryable"] is False
    assert "read-only" in result["payload"]["message"]
    assert result["payload"]["domain_approval_event_id"] == "domain-fixture"
    assert result["payload"]["openclaw_approval_event_id"] == "native-fixture"
    assert result["causation_id"] == "event-command-fixture"
