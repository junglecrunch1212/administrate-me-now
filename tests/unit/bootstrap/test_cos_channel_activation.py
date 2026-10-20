"""Stopped-service operation, durable policy compensation and restart recovery."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from adminme.cos_channel_operation import live_authorization
from adminme.cos_response_policy import policy_from_event_log
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.cos_channel_activation import (
    ActivationRequest,
    CoSActivationError,
    CoSChannelActivator,
)
from tests.acceptance.test_cos_channel_acquisition import _configuration
from tests.acceptance.test_forwarded_channel_intake import EMAIL_ACCOUNT, MEMBER_A, ROOT


def _write(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value).encode())


class _Services:
    def __init__(self, paths: DeploymentPaths) -> None:
        self.paths = paths
        self.stops = 0
        self.starts = 0
        self.effects = 0

    def runtime_diagnostics(self) -> dict[str, Any]:
        runtime = self.paths.instance_dir / "config/runtime.yaml"
        value = yaml.safe_load(runtime.read_text()) if runtime.exists() else {}
        return {
            "ready": True,
            "observation_active": (value.get("observation_mode_override") or {}).get(
                "active", True
            ),
            "deployment_readiness": {
                "dimensions": {
                    "observation_effects": {
                        "evidence": {"provider_called_true": self.effects}
                    }
                }
            },
        }

    def stop(self) -> None:
        self.stops += 1

    def start(self) -> None:
        self.starts += 1


class _Backups:
    def create(self, target: Path) -> dict[str, Any]:
        atomic_write(target, b"synthetic-encrypted-backup-boundary")
        return {"path": str(target), "encrypted": True}

    def rehearse(self, _path: Path, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "healthy",
            "artifact": {"authenticated": True, "sha256": "b" * 64},
        }


def _controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[CoSChannelActivator, ActivationRequest, dict[str, Any]]:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "operation", production=False)
    paths.create()
    config = _configuration(tmp_path / "synthetic-source")
    original = copy.deepcopy(config)
    original.pop("cos_channel_acquisition")
    config["adapters"][1]["config"]["permission_scope"].append(
        "https://www.googleapis.com/auth/gmail.send"
    )
    private = paths.instance_dir / "config"
    atomic_write(
        private / "instance.yaml", b"tenant_id: tenant-cos-operation-fixture\n"
    )
    _write(private / "adapters.json", original)
    _write(
        private / "member-mapping.json",
        {"members": [{"member_id": MEMBER_A, "role": "principal"}]},
    )
    _write(
        private / "observation.json",
        {"active": True, "briefing_complete": True, "trusted": False},
    )
    now = datetime.now(timezone.utc)
    _write(
        private / "deployment-readiness.json",
        {
            "provider_effect_window": {
                "started_at": (now - timedelta(days=5)).isoformat()
            }
        },
    )
    _write(
        private / "disposable-test-dataset.json",
        {"retained": "exact original marker bytes"},
    )
    instance = load_instance_config(paths.instance_dir)
    initial = EventLog(instance, b"o" * 32)
    asyncio.run(initial.close())
    controller = CoSChannelActivator(paths, repository=ROOT, test_mode=True)
    controller.services = _Services(paths)  # type: ignore[assignment]
    controller.backups = _Backups()  # type: ignore[assignment]
    monkeypatch.setattr(controller, "_selection", lambda: ("a" * 40, ROOT))
    monkeypatch.setattr(
        controller,
        "_event_log",
        lambda *, read_only: EventLog(instance, b"o" * 32, read_only=read_only),
    )
    monkeypatch.setattr(controller, "_settle", lambda _release: {"status": "healthy"})
    monkeypatch.setattr(
        controller,
        "_refresh_binding",
        lambda readiness, _commit: _write(
            private / "deployment-readiness.json", readiness
        ),
    )
    request = ActivationRequest(
        adapter_config=config,
        owner_member_id=MEMBER_A,
        operator_principal_id="operator:fixture-channel-enrollment",
        owner_authorization_sha256="e" * 64,
        recipient_entries=[
            {
                "member_id": MEMBER_A,
                "channel": "email",
                "address": "member-a@example.invalid",
            }
        ],
        sender_proofs=[
            {
                "channel": "email",
                "provider_account_sha256": hashlib.sha256(
                    EMAIL_ACCOUNT.encode()
                ).hexdigest(),
                "sender_evidence_sha256": "c" * 64,
                "transport_evidence_sha256": "d" * 64,
                "observed_at": (now - timedelta(minutes=1)).isoformat(),
            }
        ],
    )
    return controller, request, original


async def _audit(
    controller: CoSChannelActivator,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    log = controller._event_log(read_only=True)
    try:
        tenant_id = load_instance_config(controller.paths.instance_dir).tenant_id
        return (
            await policy_from_event_log(log, tenant_id),
            [event async for event in log.read_since()],
        )
    finally:
        await log.close()


def test_authorized_transition_preserves_selection_and_commits_only_after_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, request, original = _controller(tmp_path, monkeypatch)
    marker = controller.config_dir / "disposable-test-dataset.json"
    marker_bytes = marker.read_bytes()
    result = controller.activate(request, enable_live=True)
    assert result["observation_active"] is False
    assert result["provider_test_messages_sent"] == 0
    assert marker.read_bytes() == marker_bytes
    configured = json.loads((controller.config_dir / "adapters.json").read_text())
    assert (
        configured["disposable_test_acquisition"]
        == original["disposable_test_acquisition"]
    )
    assert configured["adapters"][0]["enabled"] is False
    assert not controller.journal_path.exists()
    history = next((controller.directory / "history").glob("*.json"))
    journal = json.loads(history.read_text())
    assert journal["initial_policy"] == {"event_id": None, "revision": 0}
    assert "member-a@example.invalid" not in history.read_text()
    policy, events = asyncio.run(_audit(controller))
    assert policy["revision"] == 1
    assert {event["type"] for event in events} >= {
        "settings.cos_response_allowlist_changed",
        "cos.channels.activated",
        "observation.disabled",
    }
    receipt = json.loads(
        (controller.config_dir / "cos-channel-operation.json").read_text()
    )

    async def verify() -> dict[str, Any]:
        log = controller._event_log(read_only=True)
        try:
            return await live_authorization(
                log,
                load_instance_config(controller.paths.instance_dir).tenant_id,
                configured,
                receipt,
            )
        finally:
            await log.close()

    assert asyncio.run(verify())["authorized"] is True


def test_failed_settlement_compensates_policy_and_never_resumes_personal_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, request, original = _controller(tmp_path, monkeypatch)
    attempts = 0

    def settle(_release: Path) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise CoSActivationError("synthetic Doctor rejection")
        return {"status": "healthy"}

    monkeypatch.setattr(controller, "_settle", settle)
    with pytest.raises(CoSActivationError, match="Doctor rejection"):
        controller.activate(request, enable_live=True)
    assert attempts == 2
    assert controller.services.runtime_diagnostics()["observation_active"] is True
    configured = json.loads((controller.config_dir / "adapters.json").read_text())
    assert all(entry["enabled"] is False for entry in configured["adapters"])
    assert (
        configured["disposable_test_acquisition"]
        == original["disposable_test_acquisition"]
    )
    policy, events = asyncio.run(_audit(controller))
    assert policy["revision"] == 2
    assert policy["entries"] == []
    assert [event["type"] for event in events].count("cos.channels.activated") == 1
    assert [event["type"] for event in events].count("cos.channels.deactivated") == 1
    assert not controller.journal_path.exists()
    evidence = json.loads(
        (controller.config_dir / "deployment-readiness.json").read_text()
    )
    assert evidence["prior_provider_effect_windows"][0]["ended_at"]


def test_interrupted_journal_blocks_new_activation_and_can_be_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, request, _original = _controller(tmp_path, monkeypatch)
    real_finish = controller._finish

    def crash_finish(_journal: dict[str, Any], _phase: str) -> None:
        raise KeyboardInterrupt("synthetic controller crash")

    monkeypatch.setattr(controller, "_finish", crash_finish)
    with pytest.raises(KeyboardInterrupt):
        controller.activate(request, enable_live=True)
    assert controller.journal_path.exists()
    with pytest.raises(CoSActivationError, match="interrupted"):
        controller.activate(request, enable_live=True)
    monkeypatch.setattr(controller, "_finish", real_finish)
    assert controller.recover()["observation_active"] is True
    assert not controller.journal_path.exists()


def test_compensation_resolves_prior_contacts_only_from_encrypted_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, request, _original = _controller(tmp_path, monkeypatch)
    original_entries = [
        {
            "member_id": MEMBER_A,
            "channel": "email",
            "address": "prior-recipient@example.invalid",
        }
    ]
    prior = asyncio.run(
        controller._write_policy(
            {
                "activation_id": "fixture-original-policy",
                "initial_policy": {"event_id": None, "revision": 0},
                "owner_member_id": MEMBER_A,
                "operator_principal_id": "operator:fixture-channel-enrollment",
            },
            original_entries,
            restore=False,
        )
    )
    calls = 0

    def settle(_release: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CoSActivationError("synthetic settlement rejection")
        return {"status": "healthy"}

    monkeypatch.setattr(controller, "_settle", settle)
    with pytest.raises(CoSActivationError, match="settlement rejection"):
        controller.activate(request, enable_live=True)
    policy, _events = asyncio.run(_audit(controller))
    assert policy["revision"] == 3
    assert policy["entries"] == original_entries
    history = next((controller.directory / "history").glob("*.json"))
    journal = json.loads(history.read_text())
    assert journal["initial_policy"] == {
        "event_id": prior["event_id"],
        "revision": 1,
    }
    assert "prior-recipient@example.invalid" not in history.read_text()


def test_unrelated_gateway_change_is_rejected_before_stopping_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bootstrap.cos_imessage import prepare_channel

    controller, request, _original = _controller(tmp_path, monkeypatch)
    guard = {
        "account_id": "cos-messages-fixture",
        "wrapper_path": "/fixture/reviewed-wrapper",
        "db_path": "/fixture/dedicated-messages.db",
    }
    senders = ["+12025550101"]  # fixture:tenant_data:ok
    adapters = copy.deepcopy(request.adapter_config)
    adapters["cos_channel_acquisition"]["imessage_channel_id"] = (
        "channel:chief-of-staff:apple-imessage"
    )
    adapters["service_identities"]["channels"][0]["response_transport_contract"] = (
        "cos-imessage-guard-v1"
    )
    adapters["openclaw_actor_identities"] = {
        f"imessage|{guard['account_id']}|{senders[0]}": MEMBER_A
    }
    original_gateway = {
        "models": {"preserve": "reviewed-model"},
        "agents": {"list": [{"id": "administrator"}]},
    }
    gateway_path = controller.paths.instance_dir / "openclaw/runtime/openclaw.json"
    _write(gateway_path, original_gateway)
    prepared = prepare_channel(original_gateway, guard, allowed_senders=senders)
    prepared["models"]["preserve"] = "unrelated-replacement"
    # Workspace preparation has separate real-directory coverage. Here the
    # real controller must refuse an unrelated delta before any service/write.
    monkeypatch.setattr(
        "bootstrap.openclaw_activation.prepare_existing_member_topology",
        lambda config, **_kwargs: copy.deepcopy(config),
    )
    changed = request.model_copy(
        update={
            "adapter_config": adapters,
            "prepared_openclaw_config": prepared,
            "cos_imsg_guard_config": guard,
        }
    )
    with pytest.raises(CoSActivationError, match="unrelated gateway"):
        controller.activate(changed, enable_live=True)
    assert controller.services.stops == 0
    assert not controller.journal_path.exists()
    assert json.loads(gateway_path.read_text()) == original_gateway
