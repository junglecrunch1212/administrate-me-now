"""Exact legacy proof and retry prevention, exercised only in isolated CI."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from bootstrap.candidate_readiness import provider_effect_window_evidence
from bootstrap.cos_channel_activation import CoSActivationError
from bootstrap.cos_recovery_history import RecoveryHistoryError, derive_recovery_history
from tests.unit.bootstrap.test_cos_channel_activation import _audit, _controller
from tests.unit.bootstrap.test_cos_recovery_history_operation import (
    TENANT,
    _repair_case,
)


def test_exact_proof_restores_omitted_interval_and_preserves_every_retained_field() -> (
    None
):
    request, readiness, failed, activated, maintenance, events = _repair_case()
    original = copy.deepcopy((readiness, failed, activated, maintenance, events))
    repaired = derive_recovery_history(
        request, readiness, failed, activated, maintenance, events, tenant_id=TENANT
    )
    assert (readiness, failed, activated, maintenance, events) == original
    before, after = (
        readiness["prior_provider_effect_windows"],
        repaired["prior_provider_effect_windows"],
    )
    assert after[0] == before[0]
    assert after[1] == {
        "started_at": "2026-08-01T08:00:00+00:00",
        "ended_at": before[1]["started_at"],
        "reason": "cos_enter_observation",
    }
    assert after[2] == {**before[1], "ended_at": before[2]["started_at"]}
    assert after[3] == before[2]
    assert {
        key: value
        for key, value in repaired.items()
        if key != "prior_provider_effect_windows"
    } == {
        key: value
        for key, value in readiness.items()
        if key != "prior_provider_effect_windows"
    }
    provider_effect_window_evidence(repaired, now=datetime.now(timezone.utc))
    with pytest.raises(RecoveryHistoryError, match="maintenance prefix"):
        derive_recovery_history(
            request, repaired, failed, activated, maintenance, events, tenant_id=TENANT
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_event",
        "extra_transition",
        "reordered",
        "other_tenant",
        "other_actor",
        "other_scope",
        "other_adapter",
        "other_account",
        "other_owner",
        "other_release",
        "journal_role_overlap",
        "closed_orphan",
        "modified_prefix",
        "different_current",
        "activation_boundary",
        "missing_initial",
        "different_first_time",
        "new_recovery_plan",
    ],
)
def test_ambiguous_or_different_legacy_proof_is_rejected(mutation: str) -> None:
    request, readiness, failed, activated, maintenance, events = _repair_case()
    if mutation == "missing_event":
        events.pop(1)
    elif mutation == "extra_transition":
        extra = copy.deepcopy(events[5])
        extra["event_id"] = "ev_extra_transition"
        events.insert(4, extra)
    elif mutation == "reordered":
        events[0], events[1] = events[1], events[0]
    elif mutation == "other_tenant":
        events[1]["tenant_id"] = "tenant-other-fixture"
    elif mutation == "other_actor":
        events[1]["actor_identity"] = "operator:other-fixture"
    elif mutation == "other_scope":
        events[1]["visibility_scope"] = "member:other-fixture"
    elif mutation == "other_adapter":
        events[1]["source_adapter"] = "other-fixture"
    elif mutation == "other_account":
        events[1]["source_account_id"] = "other-fixture"
    elif mutation == "other_owner":
        failed["owner_member_id"] = "member-other-fixture"
    elif mutation == "other_release":
        activated["release_commit"] = "f" * 40
    elif mutation == "journal_role_overlap":
        request = request.model_copy(
            update={"maintenance_operation": request.activation_operation}
        )
    elif mutation == "closed_orphan":
        readiness["prior_provider_effect_windows"][1]["ended_at"] = (
            "2026-08-01T09:30:00+00:00"
        )
    elif mutation == "modified_prefix":
        readiness["prior_provider_effect_windows"][0]["reason"] = "different_fixture"
    elif mutation == "different_current":
        readiness["provider_effect_window"]["started_at"] = "2026-08-01T13:00:00+00:00"
    elif mutation == "activation_boundary":
        events[4]["payload"]["observation_window_started_at"] = (
            "2026-08-01T10:01:00+00:00"
        )
    elif mutation == "missing_initial":
        failed["initial_files"] = {}
    elif mutation == "different_first_time":
        events[0]["occurred_at"] = "2026-08-01T09:01:00+00:00"
    elif mutation == "new_recovery_plan":
        failed["recovery_window_plan"] = {}
    with pytest.raises((RecoveryHistoryError, ValueError)):
        derive_recovery_history(
            request, readiness, failed, activated, maintenance, events, tenant_id=TENANT
        )


def test_failed_recovery_settlement_reuses_one_boundary_and_durable_control_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, request, _original = _controller(tmp_path, monkeypatch)
    path = controller.config_dir / "deployment-readiness.json"
    original = json.loads(path.read_text())
    audit_and_toggle = controller._audit_and_toggle

    async def fail_activation_before_live(activation: Any, **kwargs: Any) -> str:
        if activation is not None:
            raise CoSActivationError("synthetic pre-live failure")
        return await audit_and_toggle(activation, **kwargs)

    monkeypatch.setattr(controller, "_audit_and_toggle", fail_activation_before_live)
    monkeypatch.setattr(
        controller,
        "_settle",
        lambda _release: (_ for _ in ()).throw(
            CoSActivationError("synthetic recovery settlement failure")
        ),
    )
    with pytest.raises(CoSActivationError, match="recovery settlement"):
        controller.activate(request, enable_live=True)
    first_journal = json.loads(controller.journal_path.read_text())
    first = json.loads(path.read_text())
    plan = first_journal["recovery_window_plan"]
    assert first["prior_provider_effect_windows"] == [
        {**original["provider_effect_window"], "ended_at": plan["started_at"]}
    ]
    assert first["provider_effect_window"]["started_at"] == plan["started_at"]
    provider_effect_window_evidence(first, now=datetime.now(timezone.utc))
    _, first_events = asyncio.run(_audit(controller))
    assert not any(event["type"] == "cos.channels.activated" for event in first_events)
    monkeypatch.setattr(controller, "_settle", lambda _release: {"status": "healthy"})
    assert controller.recover()["status"] == "recovered"
    assert json.loads(path.read_text()) == first
    _, second_events = asyncio.run(_audit(controller))
    assert second_events == first_events
    archived = json.loads(
        next((controller.directory / "history").glob("*.json")).read_text()
    )
    assert archived["recovery_window_plan"] == plan
    assert archived["initial_files"] == first_journal["initial_files"]


def test_recovery_refuses_history_drift_before_restoring_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, request, _original = _controller(tmp_path, monkeypatch)
    monkeypatch.setattr(
        controller,
        "_finish",
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt("synthetic crash")),
    )
    with pytest.raises(KeyboardInterrupt):
        controller.activate(request, enable_live=True)
    path = controller.config_dir / "deployment-readiness.json"
    readiness = json.loads(path.read_text())
    readiness["provider_effect_window"]["reason"] = "different_fixture"
    path.write_text(json.dumps(readiness))
    before = {
        item.name: item.read_bytes()
        for item in controller.config_dir.iterdir()
        if item.is_file()
    }
    with pytest.raises(CoSActivationError, match="retained history changed"):
        controller.recover()
    assert {
        item.name: item.read_bytes()
        for item in controller.config_dir.iterdir()
        if item.is_file()
    } == before
