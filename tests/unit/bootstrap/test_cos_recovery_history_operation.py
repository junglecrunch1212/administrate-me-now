"""Guarded repair of one retained recovery-window defect, never event editing."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.events.envelope import EventEnvelope
from bootstrap.contracts import atomic_write
from bootstrap.cos_channel_activation import CoSActivationError, CoSChannelActivator
from bootstrap.cos_recovery_history import (
    RecoveryHistoryRepairRequest,
)
from tests.acceptance.test_forwarded_channel_intake import MEMBER_A, ROOT
from tests.unit.bootstrap.test_cos_channel_activation import _audit, _controller

TENANT = "tenant-cos-operation-fixture"
OPERATOR = "operator:fixture-channel-enrollment"


def _bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _repair_case() -> tuple[Any, ...]:
    """Exact historical defect with real schemas and legacy second truncation."""
    base = {
        "started_at": "2026-08-01T07:00:00+00:00",
        "ended_at": "2026-08-01T07:30:00+00:00",
        "reason": "cos_enter_observation",
    }
    omitted = {
        "started_at": "2026-08-01T08:00:00+00:00",
        "reason": "cos_enter_observation",
    }
    first = {
        "started_at": "2026-08-01T09:00:00.400000+00:00",
        "reason": "cos_operation_recovery",
    }
    second = {
        "started_at": "2026-08-01T10:00:00.400000+00:00",
        "reason": "cos_operation_recovery",
    }
    enabled_at = "2026-08-01T11:00:00.400000+00:00"
    observed_at = "2026-08-01T12:00:00.400000+00:00"
    snapshot = {
        "predecessor_commit": "a" * 40,
        "pointer_sha256": "1" * 64,
        "pointer_chain_sha256": "2" * 64,
        "instance_fingerprint": "3" * 64,
        "host_fingerprint": "4" * 64,
        "service_account_fingerprint": "5" * 64,
        "configuration_fingerprint": "6" * 64,
    }
    original = {
        "deployment_binding": {
            "release_commit": "a" * 40,
            "instance_fingerprint": snapshot["instance_fingerprint"],
            "host_fingerprint": snapshot["host_fingerprint"],
            "configuration_fingerprint": snapshot["configuration_fingerprint"],
        },
        "provider_effect_window": omitted,
        "prior_provider_effect_windows": [base],
        "retained_evidence": {"fixture": "preserve every unrelated readiness field"},
    }
    captured = {
        **copy.deepcopy(original),
        "provider_effect_window": second,
        "prior_provider_effect_windows": [base, first],
    }
    before_maintenance = {
        **copy.deepcopy(captured),
        "provider_effect_window": {**second, "ended_at": enabled_at},
    }
    readiness = {
        **copy.deepcopy(before_maintenance),
        "provider_effect_window": {
            "started_at": observed_at,
            "reason": "cos_enter_observation",
        },
        "prior_provider_effect_windows": [
            base,
            first,
            {**second, "ended_at": enabled_at},
        ],
    }

    def journal(operation_id: str, initial: dict[str, Any], phase: str):
        return {
            "contract": "cos-channel-operation-journal-v1",
            "activation_id": operation_id,
            "release_commit": "a" * 40,
            "operator_principal_id": OPERATOR,
            "owner_member_id": MEMBER_A,
            "owner_authorization_sha256": "e" * 64,
            "phase": phase,
            "enabled_live": True,
            "initial_files": {
                "deployment-readiness.json": base64.b64encode(_bytes(initial)).decode()
            },
        }

    failed = journal("1" * 32, original, "recovered")
    activated = journal("2" * 32, captured, "committed")
    maintenance = journal("3" * 32, before_maintenance, "committed")
    maintenance.update(
        operation="enter_observation",
        prior_activation_id=activated["activation_id"],
        observation_started_at=observed_at,
    )
    maintenance.pop("enabled_live")
    events = []

    def control(event_type: str, time: str, payload: dict[str, Any], version=1):
        event = EventEnvelope(
            event_id=f"ev_recovery_history_{len(events) + 1:02d}",
            event_at_ms=int(datetime.fromisoformat(time).timestamp() * 1000),
            tenant_id=TENANT,
            type=event_type,
            schema_version=version,
            occurred_at=time,
            recorded_at=time,
            source_adapter="observation"
            if event_type.startswith("observation.")
            else "cos-channel-operation",
            source_account_id="system",
            owner_scope="shared:household",
            visibility_scope="shared:household",
            actor_identity=OPERATOR,
            payload=payload,
        )
        events.append(event.model_dump())

    for hour, reason in ((9, "operation_failed"), (10, "interrupted_operation")):
        stamp = f"2026-08-01T{hour:02d}:00:00"
        control(
            "cos.channels.deactivated",
            stamp + ".200000+00:00",
            {
                "activation_id": failed["activation_id"],
                "operator_principal_id": OPERATOR,
                "reason": reason,
                "observation_active": True,
            },
        )
        control(
            "observation.enabled",
            stamp + "+00:00",
            {
                "actor": OPERATOR,
                "reason": "CoS operation recovery",
                "prior_state": True,
                "enabled_at": stamp + "+00:00",
            },
        )
    control(
        "cos.channels.activated",
        enabled_at,
        {
            "contract": "cos-channel-operation-v1",
            "activation_id": activated["activation_id"],
            "release_commit": "a" * 40,
            "enabled_at": enabled_at,
            "acquisition_sha256": "7" * 64,
            "allowlist_event_id": "ev_fixture_retained_allowlist",
            "allowlist_revision": 1,
            "channels": [
                {
                    "channel": "email",
                    "provider_account_sha256": "8" * 64,
                    "sender_evidence_sha256": "9" * 64,
                    "transport_evidence_sha256": "a" * 64,
                    "observed_at": "2026-08-01T11:00:00+00:00",
                }
            ],
            "backup_sha256": "b" * 64,
            "rehearsal_sha256": "c" * 64,
            "owner_authorization_sha256": "e" * 64,
            "operator_principal_id": OPERATOR,
            "observation_window_started_at": second["started_at"],
            "suppressed_backlog_released": False,
        },
    )
    control(
        "observation.disabled",
        "2026-08-01T11:00:00+00:00",
        {
            "actor": OPERATOR,
            "reason": "owner-authorized CoS channel replies",
            "prior_state": True,
            "disabled_at": "2026-08-01T11:00:00+00:00",
        },
    )
    control(
        "cos.channels.deactivated",
        observed_at,
        {
            "activation_id": activated["activation_id"],
            "operator_principal_id": OPERATOR,
            "reason": "deployment_observation",
            "observation_active": True,
        },
        version=2,
    )
    control(
        "observation.enabled",
        "2026-08-01T12:00:00+00:00",
        {
            "actor": OPERATOR,
            "reason": f"CoS deployment observation:{maintenance['activation_id']}",
            "prior_state": False,
            "enabled_at": "2026-08-01T12:00:00+00:00",
        },
    )
    request = RecoveryHistoryRepairRequest(
        owner_authorization_sha256="e" * 64,
        operator_principal_id=OPERATOR,
        owner_member_id=MEMBER_A,
        expected_release_commit="a" * 40,
        expected_controller_commit="b" * 40,
        initial_snapshot=snapshot,
        readiness_sha256=_sha(_bytes(readiness)),
        failed_operation={
            "operation_id": failed["activation_id"],
            "sha256": _sha(_bytes(failed)),
        },
        activation_operation={
            "operation_id": activated["activation_id"],
            "sha256": _sha(_bytes(activated)),
        },
        maintenance_operation={
            "operation_id": maintenance["activation_id"],
            "sha256": _sha(_bytes(maintenance)),
        },
        recovery_deactivation_event_ids=[events[0]["event_id"], events[2]["event_id"]],
        observation_enabled_event_ids=[events[1]["event_id"], events[3]["event_id"]],
        activation_event_id=events[4]["event_id"],
        observation_disabled_event_id=events[5]["event_id"],
        maintenance_deactivation_event_id=events[6]["event_id"],
        maintenance_observation_enabled_event_id=events[7]["event_id"],
    )
    return request, readiness, failed, activated, maintenance, events


def _operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, change=None):
    controller, _activation, _original = _controller(tmp_path, monkeypatch)
    request, readiness, failed, activated, maintenance, events = _repair_case()
    if change is not None:
        request = change(request, readiness, failed, activated, maintenance, events)
    atomic_write(controller.config_dir / "deployment-readiness.json", _bytes(readiness))
    for journal in (failed, activated, maintenance):
        atomic_write(
            controller.directory / "history" / f"{journal['activation_id']}.json",
            _bytes(journal),
        )
    atomic_write(
        controller.config_dir / "runtime.yaml",
        b"observation_mode_override:\n  active: true\n",
    )

    async def seed():
        log = controller._event_log(read_only=False)
        try:
            for event in events:
                await log.append(EventEnvelope.model_validate(event))
        finally:
            await log.close()

    asyncio.run(seed())
    snapshot = copy.deepcopy(request.initial_snapshot)
    monkeypatch.setattr(
        controller,
        "_observation_selection",
        lambda: ("a" * 40, ROOT, "b" * 40),
    )
    monkeypatch.setattr(
        controller, "_bound_snapshot", lambda _commit: copy.deepcopy(snapshot)
    )
    monkeypatch.setattr(
        "bootstrap.cos_channel_activation.capture_deployment_snapshot",
        lambda _paths: SimpleNamespace(
            **snapshot, public=lambda: copy.deepcopy(snapshot)
        ),
    )
    monkeypatch.setattr(
        controller,
        "_refresh_binding",
        CoSChannelActivator._refresh_binding.__get__(controller),
    )

    def rehearse(path, **_kwargs):
        return {
            "status": "healthy",
            "artifact": {"authenticated": True, "sha256": _sha(path.read_bytes())},
            "candidate": {"commit": "b" * 40},
            "deployment_binding": copy.deepcopy(request.initial_snapshot),
        }

    monkeypatch.setattr(controller.backups, "rehearse", rehearse)
    return controller, request, snapshot


def _protected(controller) -> dict[Path, bytes]:
    return {
        path: path.read_bytes()
        for path in [
            *controller.config_dir.iterdir(),
            *(controller.directory / "history").glob("*.json"),
        ]
        if path.is_file() and path.name != "deployment-readiness.json"
    }


def _assert_protected(values: dict[Path, bytes]) -> None:
    assert all(path.read_bytes() == payload for path, payload in values.items())


def _expected() -> dict[str, Any]:
    # A concrete expected outcome, independent of the production derivation.
    _, readiness, *_rest = _repair_case()
    expected = copy.deepcopy(readiness)
    expected["prior_provider_effect_windows"] = [
        readiness["prior_provider_effect_windows"][0],
        {
            "started_at": "2026-08-01T08:00:00+00:00",
            "ended_at": "2026-08-01T09:00:00.400000+00:00",
            "reason": "cos_enter_observation",
        },
        {
            "started_at": "2026-08-01T09:00:00.400000+00:00",
            "ended_at": "2026-08-01T10:00:00.400000+00:00",
            "reason": "cos_operation_recovery",
        },
        readiness["prior_provider_effect_windows"][2],
    ]
    return expected


def test_guarded_repair_restores_exact_windows_and_preserves_all_old_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, request, _snapshot = _operation(tmp_path, monkeypatch)
    unchanged = _protected(controller)
    original_policy, original_events = asyncio.run(_audit(controller))
    result = controller.repair_recovery_history(request)
    assert result["observation_active"] is True
    assert not controller.journal_path.exists()
    assert (
        json.loads((controller.config_dir / "deployment-readiness.json").read_bytes())
        == _expected()
    )
    _assert_protected(unchanged)
    assert asyncio.run(_audit(controller)) == (original_policy, original_events)
    assert asyncio.run(controller._effect_count()) == 0
    archives = list((controller.directory / "history").glob("*.json"))
    assert len(archives) == 4
    repair = next(
        json.loads(path.read_bytes()) for path in archives if path not in unchanged
    )
    assert repair["operation"] == "repair_recovery_history"
    assert repair["phase"] == "committed"
    assert repair["backup"]["encrypted"] is True
    assert repair["rehearsal"]["artifact"]["authenticated"] is True
    assert repair["doctor"]["status"] == "healthy"
    # The identical completed request has become stale; it cannot append a
    # second repaired prefix or manufacture another historical operation.
    with pytest.raises(CoSActivationError):
        controller.repair_recovery_history(request)
    assert len(list((controller.directory / "history").glob("*.json"))) == 4
    assert not controller.journal_path.exists()
    assert (
        json.loads((controller.config_dir / "deployment-readiness.json").read_bytes())
        == _expected()
    )


@pytest.mark.parametrize(
    "failure", ["backup", "backup_published", "rehearsal", "write", "doctor", "history"]
)
def test_interrupted_repair_reuses_its_exact_plan_without_duplicate_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    controller, request, _snapshot = _operation(tmp_path, monkeypatch)
    unchanged = _protected(controller)
    before = asyncio.run(_audit(controller))
    with monkeypatch.context() as crash:

        def fail(*_args, **_kwargs):
            raise KeyboardInterrupt("synthetic history repair interruption")

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
        elif failure == "write":

            def after_write(path, payload, *args, **kwargs):
                atomic_write(path, payload, *args, **kwargs)
                if path == controller.config_dir / "deployment-readiness.json":
                    fail()

            crash.setattr("bootstrap.cos_channel_activation.atomic_write", after_write)
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
        with pytest.raises(KeyboardInterrupt, match="history repair"):
            controller.repair_recovery_history(request)
    assert controller.journal_path.exists()
    retained_backups = {
        path: path.read_bytes()
        for path in controller.paths.backup_dir.glob("*.ambackup")
    }
    assert controller.recover()["observation_active"] is True
    assert not controller.journal_path.exists()
    assert (
        json.loads((controller.config_dir / "deployment-readiness.json").read_bytes())
        == _expected()
    )
    _assert_protected(unchanged)
    _assert_protected(retained_backups)
    assert asyncio.run(_audit(controller)) == before
    assert len(list((controller.directory / "history").glob("*.json"))) == 4
    assert asyncio.run(controller._effect_count()) == 0


@pytest.mark.parametrize(
    "change",
    [
        "request_release",
        "request_controller",
        "request_snapshot",
        "current_snapshot",
        "readiness",
        "readiness_hash",
        "archive_hash",
        "archive_owner",
        "event_source",
        "missing_event",
        "intervening_event",
        "nonobservation",
        "not_principal",
        "active_upgrade",
    ],
)
def test_unproved_repair_is_rejected_before_control_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    def changed_case(request, _readiness, failed, _activated, _maintenance, events):
        if change == "archive_owner":
            failed["owner_member_id"] = "member-fixture-other-owner"
            return request.model_copy(
                update={
                    "failed_operation": request.failed_operation.model_copy(
                        update={"sha256": _sha(_bytes(failed))}
                    )
                }
            )
        if change == "event_source":
            events[1]["source_adapter"] = "fixture-untrusted-observation-source"
        elif change == "missing_event":
            events.pop(3)
        elif change == "intervening_event":
            extra = copy.deepcopy(events[3])
            extra["event_id"] += "a"
            events.insert(4, extra)
        return request

    controller, request, snapshot = _operation(
        tmp_path, monkeypatch, change=changed_case
    )
    if change == "request_release":
        request = request.model_copy(update={"expected_release_commit": "c" * 40})
    elif change == "request_controller":
        request = request.model_copy(update={"expected_controller_commit": "c" * 40})
    elif change == "request_snapshot":
        request = request.model_copy(
            update={"initial_snapshot": {**snapshot, "pointer_sha256": "c" * 64}}
        )
    elif change == "current_snapshot":
        snapshot["configuration_fingerprint"] = "c" * 64
    elif change == "readiness":
        path = controller.config_dir / "deployment-readiness.json"
        value = json.loads(path.read_bytes())
        value["retained_evidence"]["changed"] = True
        atomic_write(path, _bytes(value))
    elif change == "readiness_hash":
        request = request.model_copy(update={"readiness_sha256": "c" * 64})
    elif change == "archive_hash":
        request = request.model_copy(
            update={
                "failed_operation": request.failed_operation.model_copy(
                    update={"sha256": "c" * 64}
                )
            }
        )
    elif change == "nonobservation":
        atomic_write(
            controller.config_dir / "runtime.yaml",
            b"observation_mode_override:\n  active: false\n",
        )
    elif change == "not_principal":
        atomic_write(
            controller.config_dir / "member-mapping.json",
            _bytes({"members": [{"member_id": MEMBER_A, "role": "member"}]}),
        )
    elif change == "active_upgrade":
        atomic_write(controller.paths.app_dir / "activation/active.json", b"{}")
    unchanged = _protected(controller)
    readiness_before = (
        controller.config_dir / "deployment-readiness.json"
    ).read_bytes()
    before = asyncio.run(_audit(controller))
    stopped = controller.services.stops
    with pytest.raises(CoSActivationError):
        controller.repair_recovery_history(request)
    assert not controller.journal_path.exists()
    assert controller.services.stops == stopped
    assert (
        controller.config_dir / "deployment-readiness.json"
    ).read_bytes() == readiness_before
    _assert_protected(unchanged)
    assert asyncio.run(_audit(controller)) == before


@pytest.mark.parametrize("change", ["configuration", "readiness", "provider_effect"])
def test_stopped_repair_rechecks_live_bindings_after_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    controller, request, snapshot = _operation(tmp_path, monkeypatch)
    unchanged = _protected(controller)
    before = asyncio.run(_audit(controller))
    rehearse = controller.backups.rehearse
    readiness_before = (
        controller.config_dir / "deployment-readiness.json"
    ).read_bytes()

    def drift(*args, **kwargs):
        value = rehearse(*args, **kwargs)
        if change == "configuration":
            snapshot["configuration_fingerprint"] = "d" * 64
        elif change == "readiness":
            changed = json.loads(readiness_before)
            changed["retained_evidence"]["new_evidence"] = (
                "preserve this concurrent change"
            )
            atomic_write(
                controller.config_dir / "deployment-readiness.json", _bytes(changed)
            )
        else:

            async def changed_effect_count():
                return 1

            monkeypatch.setattr(controller, "_effect_count", changed_effect_count)
        return value

    monkeypatch.setattr(controller.backups, "rehearse", drift)
    with pytest.raises(CoSActivationError):
        controller.repair_recovery_history(request)
    assert controller.journal_path.exists()
    assert controller.services.starts == 0
    current = json.loads(
        (controller.config_dir / "deployment-readiness.json").read_bytes()
    )
    assert (
        current["prior_provider_effect_windows"]
        == json.loads(readiness_before)["prior_provider_effect_windows"]
    )
    if change == "readiness":
        assert (
            current["retained_evidence"]["new_evidence"]
            == "preserve this concurrent change"
        )
    _assert_protected(unchanged)
    assert asyncio.run(_audit(controller)) == before


def test_repair_refuses_failed_encrypted_restore_proof_before_readiness_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, request, _snapshot = _operation(tmp_path, monkeypatch)
    before = (controller.config_dir / "deployment-readiness.json").read_bytes()
    monkeypatch.setattr(
        controller.backups, "rehearse", lambda *_a, **_k: {"status": "failed"}
    )
    with pytest.raises(CoSActivationError):
        controller.repair_recovery_history(request)
    assert controller.journal_path.exists()
    assert (controller.config_dir / "deployment-readiness.json").read_bytes() == before
    assert controller.services.starts == 0


@pytest.mark.parametrize("provider_called", [True, None], ids=["called", "unknown"])
def test_retained_provider_effect_in_omitted_interval_blocks_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_called: bool | None
) -> None:
    from tests.unit.test_source_assurance import _append_write_pair

    controller, request, _snapshot = _operation(tmp_path, monkeypatch)

    async def retain_effect():
        log = controller._event_log(read_only=False)

        class HistoricalLog:
            """Retain an ordinary typed effect pair inside the omitted interval."""

            async def append(self, event):
                at = "2026-08-01T08:30:00+00:00"
                payload = dict(event.payload)
                for key in ("requested_at", "completed_at"):
                    if key in payload:
                        payload[key] = at
                return await log.append(
                    event.model_copy(
                        update={
                            "tenant_id": TENANT,
                            "event_at_ms": int(
                                datetime.fromisoformat(at).timestamp() * 1000
                            ),
                            "occurred_at": at,
                            "recorded_at": at,
                            "payload": payload,
                        }
                    )
                )

        try:
            await _append_write_pair(
                HistoricalLog(),
                suffix="retained-recovery-window-effect",
                version=2 if provider_called is True else 1,
                provider_called=provider_called,
                suppressed=False,
            )
        finally:
            await log.close()

    asyncio.run(retain_effect())
    before = asyncio.run(_audit(controller))
    unchanged = _protected(controller)
    readiness_before = (
        controller.config_dir / "deployment-readiness.json"
    ).read_bytes()
    with pytest.raises(CoSActivationError, match="effects"):
        controller.repair_recovery_history(request)
    assert not controller.journal_path.exists()
    assert controller.services.stops == 0
    assert (
        controller.config_dir / "deployment-readiness.json"
    ).read_bytes() == readiness_before
    assert asyncio.run(_audit(controller)) == before
    _assert_protected(unchanged)
