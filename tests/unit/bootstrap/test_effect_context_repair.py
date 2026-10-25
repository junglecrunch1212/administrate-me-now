"""Closed admission and unchanged interval integrity for one hydration defect."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json

import pytest

from bootstrap import effect_context_repair as repair
from bootstrap.contracts import atomic_write
from bootstrap.candidate_readiness import CandidateReadinessError
from tests.unit.bootstrap.test_activation_transaction import _paths
from tests.unit.bootstrap.test_submission_repair import diagnostics, doctor


def window():
    return {
        "state": "healthy",
        "started_at_configured": True,
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T01:00:00+00:00",
        "counts": repair._effect_counts([]),
        "content_exposed": False,
    }


def diagnostic():
    value = diagnostics()
    source = value["capabilities"]["policy:source-assurance"]["details"]
    source["counts"]["failed"] = 0
    source["failed"] = []
    source["provider_effects"].update(
        state="degraded",
        zero_effect_state="nonzero",
        counts={
            **repair._effect_counts([]),
            "provider_called_true": 1,
            "malformed_request": 1,
            "approval_correlation_missing": 3,
        },
        observation_window=window(),
    )
    return value


def test_exact_degraded_diagnostic_retains_real_historical_contact():
    source = repair.validate_diagnostics(
        diagnostic(), observation=True, doctor=doctor()
    )
    assert source["provider_effects"]["counts"]["provider_called_true"] == 1


@pytest.mark.parametrize(
    "defect",
    [
        "ready",
        "observation",
        "extra_blocker",
        "missing_consumer",
        "stale",
        "pending",
        "capability",
        "window_contact",
        "window_unknown",
        "window_missing",
        "window_boolean",
        "doctor_failure",
        "doctor_missing",
        "doctor_false_healthy",
    ],
)
def test_unrelated_faults_cannot_enter_contained_repair(defect):
    value = diagnostic()
    source = value["capabilities"]["policy:source-assurance"]["details"]
    report = doctor()
    if defect == "ready":
        value["ready"] = True
    elif defect == "observation":
        value["observation_active"] = False
    elif defect == "extra_blocker":
        value["deployment_readiness"]["blocking_dimensions"].append("openclaw")
    elif defect == "missing_consumer":
        value["capabilities"].pop("projection:context_fabric")
    elif defect == "stale":
        source["freshness"]["revision_current"] = False
    elif defect == "pending":
        source["counts"]["pending"] = 1
    elif defect == "capability":
        value["capabilities"]["adapter:unexpected"] = {"state": "degraded"}
    elif defect == "window_contact":
        source["provider_effects"]["observation_window"]["counts"][
            "provider_called_true"
        ] = 1
    elif defect == "window_unknown":
        source["provider_effects"]["observation_window"]["counts"][
            "provider_called_unknown"
        ] = 1
    elif defect == "window_missing":
        source["provider_effects"].pop("observation_window")
    elif defect == "window_boolean":
        source["provider_effects"]["observation_window"]["counts"][
            "provider_called_true"
        ] = False
    elif defect == "doctor_failure":
        report["checks"].append({"name": "new_fault", "ok": False})
    elif defect == "doctor_missing":
        report["checks"].pop()
    elif defect == "doctor_false_healthy":
        report["status"] = "healthy"
        for row in report["checks"]:
            row["ok"] = True
    with pytest.raises(repair.EffectContextRepairError):
        repair.validate_diagnostics(value, observation=True, doctor=report)


def test_revocation_bytes_are_private_and_immutable(tmp_path):
    paths = _paths(tmp_path)
    path = paths.instance_dir / "config/request-actions-control.json"
    document = {
        "contract": "request-actions-control-v1",
        "disabled": True,
        "revoked_before": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write(path, json.dumps(document).encode())
    digest = repair.require_disabled(paths)
    assert repair.require_disabled(paths, digest) == digest
    document["revoked_before"] = (
        datetime.now(timezone.utc) + timedelta(seconds=1)
    ).isoformat()
    atomic_write(path, json.dumps(document).encode())
    with pytest.raises(repair.EffectContextRepairError, match="changed"):
        repair.require_disabled(paths, digest)
    document["disabled"] = False
    atomic_write(path, json.dumps(document).encode())
    with pytest.raises(repair.EffectContextRepairError, match="revocation"):
        repair.require_disabled(paths)
    path.chmod(0o644)
    with pytest.raises(CandidateReadinessError, match="private|owner|unsafe"):
        repair.require_disabled(paths)


def test_closed_request_rejects_duplicate_and_unrelated_defect():
    with pytest.raises(ValueError):
        repair.EffectContextRepairRequest(
            kind="requested_email_v5_context_hydration",
            approval_event_ids=["same", "same"],
        )
    with pytest.raises(ValueError):
        repair.EffectContextRepairRequest(
            kind="any_readiness_failure", approval_event_ids=["selected"]
        )


def test_all_retained_intervals_are_checked_without_source_rescans():
    rows = []
    for hour in range(8):
        start = datetime(2026, 1, 1, hour, tzinfo=timezone.utc)
        rows.append(
            {
                "started_at": start.isoformat(),
                "ended_at": (start + timedelta(minutes=10)).isoformat(),
            }
        )
    windows = {
        "prior_provider_effect_windows": rows[:-1],
        "provider_effect_window": rows[-1],
    }
    assert repair._audit_windows([], windows)["counts"]["provider_called_true"] == 0
    # Even a non-current interval with a terminal lacking its real request fails.
    event = {
        "event_id": "fixture-terminal",
        "type": "external_sync.write_completed",
        "occurred_at": "2026-01-01T01:05:00+00:00",
        "recorded_at": "2026-01-01T01:05:00+00:00",
        "payload": {
            "command_id": "fixture-command",
            "provider_called": True,
            "suppressed": False,
        },
    }
    with pytest.raises(repair.EffectContextRepairError, match="observation-window"):
        repair._audit_windows([event], windows)
    broken = copy.deepcopy(windows)
    broken["prior_provider_effect_windows"][0]["ended_at"] = "invalid"
    with pytest.raises(repair.EffectContextRepairError, match="bounds"):
        repair._audit_windows([], broken)
