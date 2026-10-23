"""Stopped-service operation, durable policy compensation and restart recovery."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from adminme.cos_channel_operation import live_authorization
from adminme.cos_response_policy import policy_from_event_log
from adminme.events.log import EventLog
from adminme.events.registry import EventValidationError, ensure_autoloaded, registry
from adminme.lib.instance_config import load_instance_config
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.cos_channel_activation import (
    ActivationRequest,
    CoSActivationError,
    CoSChannelActivator,
    EnterObservationRequest,
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
        if target.exists():
            raise FileExistsError("encrypted backup evidence cannot be replaced")
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
    service = config["adapters"][1]["config"]
    service.pop("service_account_key_ref")
    service.update(
        oauth_credentials_ref="keychain://fixture-cos/credential",
        binding_provenance="oauth-subject",
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
        {
            "contract": "disposable-test-dataset-v1",
            "acquisition_contract": "disposable-test-acquisition-v1",
            "dataset_id": "a" * 32,
            "reset_manifest_sha256": "b" * 64,
        },
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


def _live_controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    controller, request, _original = _controller(tmp_path, monkeypatch)
    activated = controller.activate(request, enable_live=True)
    snapshot = {
        "predecessor_commit": "a" * 40,
        "pointer_sha256": "1" * 64,
        "pointer_chain_sha256": "2" * 64,
        "instance_fingerprint": "3" * 64,
        "host_fingerprint": "4" * 64,
        "service_account_fingerprint": "5" * 64,
        "configuration_fingerprint": "6" * 64,
    }
    monkeypatch.setattr(
        controller, "_observation_selection", lambda: ("a" * 40, ROOT, "b" * 40)
    )
    monkeypatch.setattr(
        controller, "_bound_snapshot", lambda _commit: copy.deepcopy(snapshot)
    )
    monkeypatch.setattr(
        "bootstrap.cos_channel_activation.capture_deployment_snapshot",
        lambda _paths: SimpleNamespace(public=lambda: copy.deepcopy(snapshot)),
    )
    pause = EnterObservationRequest(
        owner_authorization_sha256=request.owner_authorization_sha256,
        operator_principal_id=request.operator_principal_id,
        owner_member_id=request.owner_member_id,
        expected_release_commit="a" * 40,
        expected_activation_id=activated["activation_id"],
        expected_configuration_fingerprint=snapshot["configuration_fingerprint"],
    )
    return controller, request, pause


def test_enter_observation_preserves_enrollment_policy_receipt_and_reactivates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adminme.cos_channel_operation import read_receipt

    controller, request, pause = _live_controller(tmp_path, monkeypatch)
    private = controller.config_dir
    unchanged = {
        name: (private / name).read_bytes()
        for name in (
            "adapters.json",
            "cos-channel-operation.json",
            "disposable-test-dataset.json",
        )
    }
    prior_window = json.loads((private / "deployment-readiness.json").read_bytes())[
        "provider_effect_window"
    ]
    policy_before, events_before = asyncio.run(_audit(controller))
    original_settle = controller._settle

    def held(release):
        assert read_receipt(private) == {}
        assert controller.services.runtime_diagnostics()["observation_active"] is True
        return original_settle(release)

    monkeypatch.setattr(controller, "_settle", held)
    result = controller.enter_observation(pause)
    assert result["observation_active"] is True
    assert all(
        (private / name).read_bytes() == value for name, value in unchanged.items()
    )
    evidence = json.loads((private / "deployment-readiness.json").read_bytes())
    assert evidence["prior_provider_effect_windows"] == [prior_window]
    assert "ended_at" not in evidence["provider_effect_window"]
    policy_after, events_after = asyncio.run(_audit(controller))
    assert policy_after == policy_before
    assert events_after[: len(events_before)] == events_before
    deactivated = [
        event for event in events_after if event["type"] == "cos.channels.deactivated"
    ]
    assert len(deactivated) == 1
    assert deactivated[0]["schema_version"] == 2
    assert deactivated[0]["payload"] == {
        "activation_id": pause.expected_activation_id,
        "operator_principal_id": pause.operator_principal_id,
        "reason": "deployment_observation",
        "observation_active": True,
    }
    with pytest.raises(CoSActivationError, match="no current durable"):
        asyncio.run(controller._live_predecessor(ROOT))
    monkeypatch.setattr(controller, "_settle", original_settle)
    reactivated = controller.activate(
        request.model_copy(update={"recipient_entries": None}), enable_live=True
    )
    assert reactivated["activation_id"] != pause.expected_activation_id
    assert asyncio.run(_audit(controller))[0] == policy_before
    assert json.loads((private / "deployment-readiness.json").read_bytes())[
        "prior_provider_effect_windows"
    ] == [prior_window]


@pytest.mark.parametrize(
    "failure",
    ["backup", "backup_published", "rehearsal", "toggle", "doctor", "history"],
)
def test_interrupted_observation_recovers_to_containment_without_reseeding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    controller, _request, pause = _live_controller(tmp_path, monkeypatch)
    original_policy, _ = asyncio.run(_audit(controller))
    readiness = json.loads(
        (controller.config_dir / "deployment-readiness.json").read_bytes()
    )
    with monkeypatch.context() as crash:

        def fail(*_args, **_kwargs):
            raise KeyboardInterrupt("synthetic containment interruption")

        if failure == "backup":
            crash.setattr(controller.backups, "create", fail)
        elif failure == "backup_published":
            create = controller.backups.create

            def published(*args, **kwargs):
                create(*args, **kwargs)
                fail()

            crash.setattr(controller.backups, "create", published)
        elif failure == "rehearsal":
            crash.setattr(controller.backups, "rehearse", fail)
        elif failure == "toggle":
            original = controller._audit_and_toggle

            async def after_toggle(*args, **kwargs):
                await original(*args, **kwargs)
                fail()

            crash.setattr(controller, "_audit_and_toggle", after_toggle)
        elif failure == "doctor":
            crash.setattr(controller, "_settle", fail)
        else:

            def history_only(journal, phase):
                controller._write_journal(journal, phase)
                atomic_write(
                    controller.directory
                    / "history"
                    / f"{journal['activation_id']}.json",
                    controller.journal_path.read_bytes(),
                )
                fail()

            crash.setattr(controller, "_finish", history_only)
        with pytest.raises(KeyboardInterrupt, match="containment"):
            controller.enter_observation(pause)
    assert controller.journal_path.exists()
    retained_backups = {
        path: path.read_bytes()
        for path in controller.paths.backup_dir.glob("*.ambackup")
    }
    assert controller.recover()["observation_active"] is True
    assert all(path.read_bytes() == value for path, value in retained_backups.items())
    assert not controller.journal_path.exists()
    policy, events = asyncio.run(_audit(controller))
    assert policy == original_policy
    assert [event["type"] for event in events].count("cos.channels.deactivated") == 1
    assert [event["type"] for event in events].count("observation.enabled") == 1
    current = json.loads(
        (controller.config_dir / "deployment-readiness.json").read_bytes()
    )
    assert current["prior_provider_effect_windows"] == [
        readiness["provider_effect_window"]
    ]


@pytest.mark.parametrize(
    "reason", ["operation_failed", "operator_requested", "interrupted_operation"]
)
def test_deactivation_v2_preserves_historical_schema_and_reason(reason: str) -> None:
    ensure_autoloaded()
    payload = {
        "activation_id": "a" * 32,
        "operator_principal_id": "operator:fixture-channel-enrollment",
        "reason": reason,
        "observation_active": True,
    }
    assert (
        registry.validate("cos.channels.deactivated", 1, payload).model_dump()
        == payload
    )
    upgraded = registry.upcast(
        "cos.channels.deactivated", payload, from_version=1, to_version=2
    )
    assert upgraded == payload and upgraded is not payload
    assert (
        registry.validate("cos.channels.deactivated", 2, upgraded).model_dump()
        == payload
    )
    new_payload = {**payload, "reason": "deployment_observation"}
    assert (
        registry.validate("cos.channels.deactivated", 2, new_payload).model_dump()
        == new_payload
    )
    with pytest.raises(EventValidationError):
        registry.validate("cos.channels.deactivated", 1, new_payload)
    for invalid in (
        {**new_payload, "reason": "unknown_reason"},
        {**new_payload, "observation_active": False},
        {**new_payload, "unexpected": True},
    ):
        with pytest.raises(EventValidationError):
            registry.validate("cos.channels.deactivated", 2, invalid)


@pytest.mark.parametrize(
    "field",
    [
        "expected_activation_id",
        "expected_release_commit",
        "expected_configuration_fingerprint",
        "owner_member_id",
    ],
)
def test_observation_refuses_mismatched_predecessor_before_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    controller, _request, pause = _live_controller(tmp_path, monkeypatch)
    stopped = controller.services.stops
    with pytest.raises(CoSActivationError):
        controller.enter_observation(pause.model_copy(update={field: "wrong"}))
    assert controller.services.stops == stopped
    assert not controller.journal_path.exists()


def test_observation_refuses_config_drift_after_backup_and_keeps_reply_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _request, pause = _live_controller(tmp_path, monkeypatch)
    original = controller.backups.rehearse

    def drift(*args, **kwargs):
        value = original(*args, **kwargs)
        atomic_write(controller.config_dir / "adapters.json", b'{"changed":true}')
        return value

    monkeypatch.setattr(controller.backups, "rehearse", drift)
    with pytest.raises(CoSActivationError, match="control changed"):
        controller.enter_observation(pause)
    assert controller.journal_path.exists()


@pytest.mark.parametrize("failure", ["main", "python", "source"])
def test_observation_successor_source_is_verified_before_live_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from bootstrap.source_identity import SourceIdentityError

    controller, _request, _original = _controller(tmp_path, monkeypatch)
    controller.test_mode = False
    selected = controller.paths.app_dir / "releases" / ("a" * 40)
    _write(
        controller.paths.app_dir / "current-release.json",
        {"commit": "a" * 40, "path": str(selected)},
    )
    monkeypatch.setattr(
        "bootstrap.cos_channel_activation.reviewed_candidate_commit",
        lambda _repo: "b" * 40,
    )
    monkeypatch.setattr(
        "bootstrap.cos_channel_activation._run_git",
        lambda *_args: SimpleNamespace(
            stdout=(("c" if failure == "main" else "b") * 40).encode()
        ),
    )
    monkeypatch.setattr(
        "bootstrap.cos_channel_activation.sys.prefix",
        str(selected / ("wrong-python" if failure == "python" else ".venv")),
    )

    def unreviewed(*_args, **_kwargs):
        raise SourceIdentityError("fixture predecessor source changed")

    monkeypatch.setattr(
        "bootstrap.cos_channel_activation.verify_release_source_identity", unreviewed
    )
    with pytest.raises((CoSActivationError, SourceIdentityError)):
        controller._observation_selection()
    assert controller.services.stops == 0
    assert not controller.journal_path.exists()


def test_legacy_observation_control_is_preserved_then_privately_republished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64
    import stat

    controller, _request, pause = _live_controller(tmp_path, monkeypatch)
    runtime = controller.config_dir / "runtime.yaml"
    original = runtime.read_bytes()
    runtime.chmod(0o644)
    result = controller.enter_observation(pause)
    assert result["observation_active"] is True
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o600
    history = [
        json.loads(path.read_bytes())
        for path in (controller.directory / "history").glob("*.json")
    ]
    paused = next(row for row in history if row.get("operation") == "enter_observation")
    assert base64.b64decode(paused["initial_files"]["runtime.yaml"]) == original


@pytest.mark.parametrize(
    "invalid",
    [
        "extra_key",
        "extra_state_key",
        "duplicate_override",
        "duplicate_active",
        "active",
        "timestamp",
        "writable",
        "symlink",
        "hardlink",
        "parent",
        "other_file",
    ],
)
def test_legacy_observation_compatibility_never_widens_other_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    import os
    from bootstrap.cos_channel_activation import _observation_control_bytes

    controller, _request, _pause = _live_controller(tmp_path, monkeypatch)
    path = controller.config_dir / "runtime.yaml"
    document = yaml.safe_load(path.read_text())
    if invalid == "extra_key":
        document["unreviewed"] = True
    elif invalid == "extra_state_key":
        document["observation_mode_override"]["unreviewed"] = True
    elif invalid == "active":
        document["observation_mode_override"]["active"] = True
    elif invalid == "timestamp":
        document["observation_mode_override"]["enabled_at"] = "unverified"
    payload = yaml.safe_dump(document).encode()
    if invalid == "duplicate_override":
        payload += payload
    elif invalid == "duplicate_active":
        payload += b"  active: false\n"
    atomic_write(path, payload)
    path.chmod(0o666 if invalid == "writable" else 0o644)
    if invalid == "symlink":
        target = tmp_path / "redirected.yaml"
        path.rename(target)
        path.symlink_to(target)
    elif invalid == "hardlink":
        os.link(path, tmp_path / "other-link")
    elif invalid == "parent":
        controller.config_dir.chmod(0o755)
    elif invalid == "other_file":
        path = controller.config_dir / "adapters.json"
        path.chmod(0o644)
    with pytest.raises((CoSActivationError, OSError)):
        _observation_control_bytes(controller.config_dir, path.name)


@pytest.mark.parametrize("outcome", ["accepted", "wrong_owner", "settlement_failure"])
@pytest.mark.parametrize("read_write", [False, True])
def test_google_connection_activation_checks_owner_before_live_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str, read_write: bool
) -> None:
    from adminme.adapters.member_google_connections import CONFIG_KEY, binding_digest
    from tests.acceptance.test_member_google_connections import google_configuration

    controller, request, original = _controller(tmp_path, monkeypatch)
    google = google_configuration(tmp_path / "google-source", read_write=read_write)
    # Reuse the operation's enrolled service entry; add only existing member
    # records to both predecessor and request, as a real retained deployment has.
    original["adapters"] = [
        google["adapters"][0],
        original["adapters"][1],
        *google["adapters"][2:],
    ]
    for entry in original["adapters"]:
        entry["enabled"] = False
    _write(controller.config_dir / "adapters.json", original)
    requested = copy.deepcopy(request.adapter_config)
    requested["adapters"] = [
        google["adapters"][0],
        requested["adapters"][1],
        *google["adapters"][2:],
    ]
    for entry in requested["adapters"]:
        if entry["config"].get("member_id") == MEMBER_A:
            entry["enabled"] = True
    requested[CONFIG_KEY] = google[CONFIG_KEY]
    for selection in requested[CONFIG_KEY]["selections"]:
        selection["binding_sha256"] = binding_digest(
            next(
                e for e in requested["adapters"] if e["id"] == selection["adapter_id"]
            ),
            access=selection["access"],
        )
        if outcome == "wrong_owner":
            selection["owner_authorization_sha256"] = "f" * 64
    changed = request.model_copy(update={"adapter_config": requested})
    before = (controller.config_dir / "adapters.json").read_bytes()
    if outcome == "wrong_owner":
        with pytest.raises(CoSActivationError, match="exact authorization"):
            controller.activate(changed, enable_live=False)
        assert (controller.config_dir / "adapters.json").read_bytes() == before
        assert not controller.journal_path.exists()
    elif outcome == "settlement_failure":
        attempts = 0

        def settle(_release: Path) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise CoSActivationError("selected source failed its handshake")
            return {"status": "healthy"}

        monkeypatch.setattr(controller, "_settle", settle)
        with pytest.raises(CoSActivationError, match="handshake"):
            controller.activate(changed, enable_live=False)
        restored = json.loads((controller.config_dir / "adapters.json").read_text())
        assert restored == json.loads(before)
        assert CONFIG_KEY not in restored
        assert not controller.journal_path.exists()
    else:
        result = controller.activate(changed, enable_live=False)
        assert result["personal_channel_polling"] is True
        installed = json.loads((controller.config_dir / "adapters.json").read_text())
        selected = [
            e for e in installed["adapters"] if e["config"].get("member_id") == MEMBER_A
        ]
        assert len(selected) == 3 and all(e["enabled"] for e in selected)
        for entry in selected:
            writable = read_write and entry["kind"] != "messaging-gmail-api"
            assert entry["config"]["read_only"] is not writable
        assert installed["adapters"][-1]["enabled"] is False


@pytest.mark.parametrize("choice", [None, False, True])
@pytest.mark.parametrize("prior", [False, True])
def test_standing_owner_reply_activation_is_explicit_retained_and_revocable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: bool | None, prior: bool
) -> None:
    from adminme.cos_self_replies import self_reply_binding
    from bootstrap.cos_channel_activation import owner_self_reply_bindings

    _controller_value, request, _original = _controller(tmp_path, monkeypatch)
    actor = "+12025550101"  # fixture:tenant_data:ok
    other = "+12025550102"  # fixture:tenant_data:ok
    digest = self_reply_binding(MEMBER_A, "fixture-account", actor)
    request = request.model_copy(update={"allow_owner_self_replies": choice})
    prepared = {
        "openclaw_actor_identities": {
            f"imessage|fixture-account|{actor}": MEMBER_A,
            f"imessage|fixture-account|{other}": "member-other",
        }
    }
    recipients = [
        {"member_id": MEMBER_A, "channel": "imessage", "address": actor},
        {"member_id": "member-other", "channel": "imessage", "address": other},
    ]
    prior_bindings = [digest] if prior else []
    result = owner_self_reply_bindings(
        request, prepared, recipients, prior_bindings, enable_live=True
    )
    assert result == ([digest] if choice is True or (choice is None and prior) else [])
    other_digest = self_reply_binding("member-other", "fixture-account", other)
    with_other = owner_self_reply_bindings(
        request, prepared, recipients, [*prior_bindings, other_digest], enable_live=True
    )
    assert set(with_other) == set(result) | {other_digest}
    if choice is None:
        assert (
            owner_self_reply_bindings(
                request, prepared, recipients, prior_bindings, enable_live=False
            )
            == prior_bindings
        )
    if choice:
        with pytest.raises(CoSActivationError, match="live guarded"):
            owner_self_reply_bindings(
                request, prepared, recipients, prior_bindings, enable_live=False
            )
        with pytest.raises(CoSActivationError, match="no exact"):
            owner_self_reply_bindings(
                request, prepared, [], prior_bindings, enable_live=True
            )
    else:
        assert (
            owner_self_reply_bindings(
                request, prepared, [], prior_bindings, enable_live=True
            )
            == []
        )


@pytest.mark.parametrize("settlement_fails", [False, True])
@pytest.mark.parametrize("timed_reminders", [False, True])
def test_standing_reply_operation_publishes_both_grants_or_restores_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settlement_fails: bool,
    timed_reminders: bool,
) -> None:
    from adminme.cos_self_replies import self_reply_binding
    from adminme.openclaw_topology import member_agent_id
    from bootstrap.cos_imessage import prepare_channel
    from tests.unit.bootstrap.test_cos_imessage import _guard

    controller, request, _original = _controller(tmp_path, monkeypatch)
    guard = _guard(tmp_path)
    guard["account_id"] = "cos-messages-fixture"
    phone = "+12025550101"  # fixture:tenant_data:ok
    adapters = copy.deepcopy(request.adapter_config)
    adapters["cos_channel_acquisition"]["imessage_channel_id"] = (
        "channel:chief-of-staff:apple-imessage"
    )
    adapters["service_identities"]["channels"][0]["response_transport_contract"] = (
        "cos-imessage-guard-v1"
    )
    adapters["openclaw_actor_identities"] = {
        f"imessage|{guard['account_id']}|{phone}": MEMBER_A
    }
    previous = {
        "agents": {"list": [{"id": member_agent_id(MEMBER_A)}]},
        "models": {"retained": "fixture"},
    }
    gateway_path = controller.paths.instance_dir / "openclaw/runtime/openclaw.json"
    _write(gateway_path, previous)
    prepared = prepare_channel(previous, guard, allowed_senders=[phone])
    monkeypatch.setattr(
        "bootstrap.openclaw_activation.prepare_existing_member_topology",
        lambda config, **_kwargs: copy.deepcopy(config),
    )
    monkeypatch.setattr(
        "bootstrap.cos_imessage.verify_guard_files", lambda *_args, **_kwargs: None
    )
    proof = request.sender_proofs[0].model_copy(
        update={
            "channel": "imessage",
            "provider_account_sha256": hashlib.sha256(
                guard["account_id"].encode()
            ).hexdigest(),
        }
    )
    request = request.model_copy(
        update={
            "adapter_config": adapters,
            "prepared_openclaw_config": prepared,
            "cos_imsg_guard_config": guard,
            "allow_owner_self_replies": True,
            "allow_owner_self_reminders": True if timed_reminders else None,
            "sender_proofs": [*request.sender_proofs, proof],
            "recipient_entries": [
                *(request.recipient_entries or []),
                {"member_id": MEMBER_A, "channel": "imessage", "address": phone},
            ],
        }
    )
    if settlement_fails:
        calls = 0

        def settle(_release: Path) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise CoSActivationError("synthetic standing settlement refusal")
            return {"status": "healthy"}

        monkeypatch.setattr(controller, "_settle", settle)
        with pytest.raises(CoSActivationError, match="standing settlement"):
            controller.activate(request, enable_live=True)
        assert json.loads(gateway_path.read_text()) == previous
        assert not (controller.config_dir / "cos-channel-operation.json").exists()
        _policy_value, events = asyncio.run(_audit(controller))
        assert events[-1]["type"] != "cos.channels.activated"
    else:
        controller.activate(request, enable_live=True)
        expected = [self_reply_binding(MEMBER_A, guard["account_id"], phone)]
        host = json.loads(gateway_path.read_text())
        assert (
            host["plugins"]["entries"]["administrateme"]["config"][
                "standingSelfReplyBindings"
            ]
            == expected
        )
        assert host["plugins"]["entries"]["administrateme"]["config"].get(
            "scheduledSelfReminderBindings", []
        ) == (expected if timed_reminders else [])
        if timed_reminders:
            assert host["cron"]["triggers"]["enabled"] is True
        assert host["models"] == previous["models"]
        assert host["plugins"]["entries"]["administrateme"]["hooks"] == {
            "allowConversationAccess": True
        }
        receipt = json.loads(
            (controller.config_dir / "cos-channel-operation.json").read_text()
        )
        assert receipt["activation"]["self_reply_bindings"] == expected
        _policy_value, events = asyncio.run(_audit(controller))
        activation = next(
            event for event in events if event["event_id"] == receipt["event_id"]
        )
        assert activation["schema_version"] == 2
        assert activation["payload"] == receipt["activation"]


def _maintenance_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, messages: bool = True
):
    from adminme.openclaw_topology import member_agent_id
    from bootstrap.cos_imessage import prepare_channel
    from tests.unit.bootstrap.test_cos_imessage import _guard

    controller, request, _original = _controller(tmp_path, monkeypatch)
    if messages:
        guard = _guard(tmp_path)
        guard["account_id"] = "cos-messages-fixture"
        wrapper = ROOT / "openclaw/administrateme/bin/cos-imsg-guard.mjs"
        guard.update(
            wrapper_path=str(wrapper),
            wrapper_sha256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),
        )
        phone = "+12025550101"  # fixture:tenant_data:ok
        adapters = copy.deepcopy(request.adapter_config)
        adapters["cos_channel_acquisition"]["imessage_channel_id"] = (
            "channel:chief-of-staff:apple-imessage"
        )
        adapters["service_identities"]["channels"][0]["response_transport_contract"] = (
            "cos-imessage-guard-v1"
        )
        adapters["openclaw_actor_identities"] = {
            f"imessage|{guard['account_id']}|{phone}": MEMBER_A
        }
        gateway = {
            "agents": {"list": [{"id": member_agent_id(MEMBER_A)}]},
            "models": {"retained": "fixture"},
        }
        _write(
            controller.paths.instance_dir / "openclaw/runtime/openclaw.json", gateway
        )
        monkeypatch.setattr(
            "bootstrap.openclaw_activation.prepare_existing_member_topology",
            lambda config, **_kwargs: copy.deepcopy(config),
        )
        monkeypatch.setattr(
            "bootstrap.cos_imessage.verify_guard_files", lambda *_args, **_kwargs: None
        )
        request = request.model_copy(
            update={
                "adapter_config": adapters,
                "prepared_openclaw_config": prepare_channel(
                    gateway, guard, allowed_senders=[phone]
                ),
                "cos_imsg_guard_config": guard,
                "allow_owner_self_replies": True,
                "allow_owner_self_reminders": True,
                "sender_proofs": [
                    *request.sender_proofs,
                    request.sender_proofs[0].model_copy(
                        update={
                            "channel": "imessage",
                            "provider_account_sha256": hashlib.sha256(
                                guard["account_id"].encode()
                            ).hexdigest(),
                        }
                    ),
                ],
                "recipient_entries": [
                    *(request.recipient_entries or []),
                    {"member_id": MEMBER_A, "channel": "imessage", "address": phone},
                ],
            }
        )
    controller.activate(request, enable_live=True)
    snapshot = {
        "predecessor_commit": "a" * 40,
        "pointer_sha256": "1" * 64,
        "pointer_chain_sha256": "2" * 64,
        "instance_fingerprint": "3" * 64,
        "host_fingerprint": "4" * 64,
        "service_account_fingerprint": "5" * 64,
        "configuration_fingerprint": "6" * 64,
    }
    monkeypatch.setattr(
        controller, "_observation_selection", lambda: ("a" * 40, ROOT, "b" * 40)
    )
    monkeypatch.setattr(
        controller, "_bound_snapshot", lambda _commit: copy.deepcopy(snapshot)
    )
    monkeypatch.setattr(
        "bootstrap.cos_channel_activation.capture_deployment_snapshot",
        lambda _paths: SimpleNamespace(public=lambda: copy.deepcopy(snapshot)),
    )
    return controller, request


def _prepare_maintenance(controller, request):
    from bootstrap.cos_channel_activation import MaintenancePreparationRequest

    return controller.prepare_maintenance(
        MaintenancePreparationRequest(
            owner_member_id=request.owner_member_id,
            operator_principal_id=request.operator_principal_id,
            owner_authorization_sha256=request.owner_authorization_sha256,
        )
    )


@pytest.mark.parametrize("messages", [False, True])
def test_prepared_maintenance_restores_exact_authority_without_new_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, messages: bool
) -> None:
    controller, request = _maintenance_controller(
        tmp_path, monkeypatch, messages=messages
    )
    _, events_before = asyncio.run(_audit(controller))
    controls_before = {
        path: path.read_bytes()
        for path in controller.config_dir.iterdir()
        if path.is_file()
    }
    stops_before = controller.services.stops
    plan = _prepare_maintenance(controller, request)
    assert controller.services.stops == stops_before
    assert controls_before == {path: path.read_bytes() for path in controls_before}
    assert asyncio.run(_audit(controller))[1] == events_before
    assert plan.self_reply_bindings == plan.self_reminder_bindings
    assert bool(plan.self_reply_bindings) is messages
    assert plan.sender_proofs == request.sender_proofs
    controller.pause_maintenance(plan)
    monkeypatch.setattr(controller, "_selection", lambda: ("b" * 40, ROOT))
    if messages:
        # Plugin reinstallation can remove the host copy. The plan may restore
        # only the exact previously effective intersection, not recipient ceilings.
        path = controller.paths.instance_dir / "openclaw/runtime/openclaw.json"
        gateway = json.loads(path.read_text())
        config = gateway["plugins"]["entries"]["administrateme"]["config"]
        config.pop("standingSelfReplyBindings")
        config.pop("scheduledSelfReminderBindings")
        _write(path, gateway)
    result = controller.activate(plan, enable_live=True)
    assert result["observation_active"] is False
    assert result["provider_test_messages_sent"] == 0
    receipt = json.loads(
        (controller.config_dir / "cos-channel-operation.json").read_text()
    )
    assert receipt["activation"]["self_reply_bindings"] == plan.self_reply_bindings
    if messages:
        config = json.loads(path.read_text())["plugins"]["entries"]["administrateme"][
            "config"
        ]
        assert config["standingSelfReplyBindings"] == plan.self_reply_bindings
        assert config["scheduledSelfReminderBindings"] == plan.self_reminder_bindings
    # Successful activation consumes the predecessor receipt; replay cannot send
    # or recreate a live boundary even if the operator keeps the private artifact.
    stops = controller.services.stops
    with pytest.raises(CoSActivationError, match="drifted"):
        controller.activate(plan, enable_live=True)
    assert controller.services.stops == stops


@pytest.mark.parametrize(
    "mutation", ["grant", "expired", "gateway", "member", "candidate", "sender"]
)
def test_maintenance_rejects_changed_plan_before_pausing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    controller, request = _maintenance_controller(tmp_path, monkeypatch)
    plan = _prepare_maintenance(controller, request)
    stops = controller.services.stops
    if mutation == "grant":
        plan = plan.model_copy(update={"self_reply_bindings": ["f" * 64]})
    elif mutation == "expired":
        plan = plan.model_copy(
            update={
                "expires_at": (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                ).isoformat()
            }
        )
    elif mutation == "candidate":
        plan = plan.model_copy(update={"target_commit": "c" * 40})
    elif mutation == "sender":
        plan = plan.model_copy(
            update={
                "sender_proofs": [
                    request.sender_proofs[0].model_copy(
                        update={"provider_account_sha256": "f" * 64}
                    )
                ]
            }
        )
    elif mutation == "member":
        _write(
            controller.config_dir / "member-mapping.json",
            {"members": [{"member_id": MEMBER_A, "role": "standard"}]},
        )
    else:
        path = controller.paths.instance_dir / "openclaw/runtime/openclaw.json"
        gateway = json.loads(path.read_text())
        gateway["models"]["retained"] = "changed-after-preparation"
        _write(path, gateway)
    with pytest.raises(CoSActivationError):
        controller.pause_maintenance(plan)
    assert controller.services.stops == stops
    assert not controller.journal_path.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "plan",
        "gateway",
        "recipient",
        "guard",
        "revocation",
        "missing_pause",
        "candidate",
    ],
)
def test_restoration_refuses_drift_or_new_authority_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    controller, request = _maintenance_controller(tmp_path, monkeypatch)
    plan = _prepare_maintenance(controller, request)
    if mutation != "missing_pause":
        controller.pause_maintenance(plan)
    monkeypatch.setattr(
        controller,
        "_selection",
        lambda: ("c" * 40 if mutation == "candidate" else "b" * 40, ROOT),
    )
    if mutation == "plan":
        plan = plan.model_copy(update={"self_reply_bindings": ["f" * 64]})
    elif mutation == "gateway":
        path = controller.paths.instance_dir / "openclaw/runtime/openclaw.json"
        gateway = json.loads(path.read_text())
        gateway["models"]["retained"] = "unrelated-model-change"
        _write(path, gateway)
    elif mutation == "guard":
        path = controller.config_dir / "cos-imsg-guard.json"
        guard = json.loads(path.read_text())
        guard["db_path"] = "/fixture/different-messages.db"
        _write(path, guard)
    elif mutation == "recipient":
        current = asyncio.run(controller._policy())
        current["revision"] += 1

        async def changed_policy():
            return current

        monkeypatch.setattr(controller, "_policy", changed_policy)
    elif mutation == "revocation":
        # A later deliberate revocation must supersede pre-maintenance authority.
        asyncio.run(
            controller._audit_and_toggle(
                None,
                journal={
                    "activation_id": plan.prior_activation_id,
                    "operator_principal_id": plan.operator_principal_id,
                },
                deactivate_reason="operation_failed",
            )
        )
    stops = controller.services.stops
    with pytest.raises((CoSActivationError, FileNotFoundError)):
        controller.activate(plan, enable_live=True)
    assert controller.services.stops == stops
    assert not controller.journal_path.exists()


def test_maintenance_rejects_expired_original_proofs_before_downtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bootstrap.cos_channel_activation import MaintenancePreparationRequest

    controller, request = _maintenance_controller(tmp_path, monkeypatch, messages=False)
    stops = controller.services.stops
    with pytest.raises(CoSActivationError, match="before maintenance"):
        controller.prepare_maintenance(
            MaintenancePreparationRequest(
                owner_member_id=request.owner_member_id,
                operator_principal_id=request.operator_principal_id,
                owner_authorization_sha256=request.owner_authorization_sha256,
                sender_proofs=[
                    request.sender_proofs[0].model_copy(
                        update={
                            "observed_at": (
                                datetime.now(timezone.utc) - timedelta(days=2)
                            ).isoformat()
                        }
                    )
                ],
            )
        )
    assert controller.services.stops == stops


def test_maintenance_cli_consumes_same_private_artifact_through_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from bootstrap.cos_channel_activation import main

    controller, request = _maintenance_controller(tmp_path, monkeypatch, messages=False)
    monkeypatch.setattr(
        "bootstrap.cos_channel_activation.CoSChannelActivator",
        lambda *_args, **_kwargs: controller,
    )
    authority = tmp_path / "maintenance-authority.json"
    plan = tmp_path / "maintenance-plan.json"
    _write(
        authority,
        {
            key: getattr(request, key)
            for key in (
                "owner_member_id",
                "operator_principal_id",
                "owner_authorization_sha256",
            )
        },
    )
    base = ["cos_channel_activation", "--instance-root", str(controller.paths.root)]
    monkeypatch.setattr(
        "sys.argv",
        [
            *base,
            "--request",
            str(authority),
            "--prepare-maintenance",
            "--output",
            str(plan),
        ],
    )
    main()
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "maintenance_prepared"
    assert summary["runtime_changed"] is False
    assert "sender_proofs" not in summary
    assert plan.stat().st_mode & 0o777 == 0o600
    original = plan.read_bytes()
    with pytest.raises(FileExistsError):
        main()
    assert plan.read_bytes() == original
    monkeypatch.setattr(
        "sys.argv", [*base, "--request", str(plan), "--pause-maintenance"]
    )
    main()
    assert json.loads(capsys.readouterr().out)["observation_active"] is True
    monkeypatch.setattr(controller, "_selection", lambda: ("b" * 40, ROOT))
    monkeypatch.setattr(
        "sys.argv", [*base, "--request", str(plan), "--restore-maintenance"]
    )
    main()
    assert json.loads(capsys.readouterr().out)["observation_active"] is False


def test_maintenance_gateway_fingerprint_bounds_release_metadata_only() -> None:
    from bootstrap.cos_channel_activation import _maintenance_gateway_fingerprint

    source, target = Path("/fixture/source"), Path("/fixture/target")
    gateway = {
        "meta": {"lastTouchedAt": "old"},
        "plugins": {
            "load": {"paths": [str(source / "openclaw/administrateme")]},
            "installs": {
                "administrateme": {"sourcePath": str(source), "installedAt": "old"}
            },
            "entries": {
                "administrateme": {
                    "config": {"standingSelfReplyBindings": ["a" * 64]},
                    "hooks": {"allowConversationAccess": True},
                }
            },
        },
        "channels": {
            "imessage": {
                "accounts": {
                    "fixture": {
                        "cliPath": str(
                            source / "openclaw/administrateme/bin/cos-imsg-guard.mjs"
                        ),
                        "allowFrom": ["fixture-private-sender"],
                    }
                }
            }
        },
        "models": {"retained": "fixture"},
    }
    fingerprint = _maintenance_gateway_fingerprint(gateway, source)
    selected = copy.deepcopy(gateway)
    selected["meta"]["lastTouchedAt"] = "new"
    selected["plugins"]["load"]["paths"] = [str(target / "openclaw/administrateme")]
    selected["plugins"]["installs"]["administrateme"] = {
        "sourcePath": str(target),
        "installedAt": "new",
    }
    selected["plugins"]["entries"]["administrateme"]["config"] = {}
    selected["plugins"]["entries"]["administrateme"]["hooks"] = {}
    selected["channels"]["imessage"]["accounts"]["fixture"]["cliPath"] = str(
        target / "openclaw/administrateme/bin/cos-imsg-guard.mjs"
    )
    assert _maintenance_gateway_fingerprint(selected, target) == fingerprint
    selected["models"]["retained"] = "changed"
    assert _maintenance_gateway_fingerprint(selected, target) != fingerprint
