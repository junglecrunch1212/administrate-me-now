"""Absent Doctor metadata can retry; present proof is never replaced."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bootstrap.service_settlement import wait_for_settled_doctor
from bootstrap.services import ServiceManager, source_assurance_terminal_evidence


def absent_report() -> dict:
    return {
        "status": "failed",
        "checks": [
            {
                "name": "source_assurance_terminal_dispositions",
                "ok": False,
                "evidence": source_assurance_terminal_evidence(None)[1],
            },
            {
                "name": "deployment_topology_and_recovery_readiness",
                "ok": False,
                "evidence": {},
            },
            {
                "name": "launchd_and_process_state",
                "ok": False,
                "evidence": {
                    "definitions": {"fixture-runtime": True, "fixture-gateway": True},
                    "services": {"status": "failed"},
                },
            },
            {"name": "encrypted_event_log_and_migrations", "ok": True},
        ],
    }


def fresh_readiness() -> dict:
    return {
        "ready": True,
        "observation_active": True,
        "write_admission": "observation_suppressed",
        "deployment_readiness": {
            "contract": "deployment-readiness-v1",
            "state": "ready",
            "ready": True,
            "blocking_dimensions": [],
        },
        "capabilities": {
            "policy:source-assurance": {
                "state": "healthy",
                "details": {
                    "contract": "source-assurance-v1",
                    "state": "healthy",
                    "counts": {
                        "obligations": 1,
                        "accounted": 1,
                        "pending": 0,
                        "missing": 0,
                        "failed": 0,
                    },
                    "declared_obligations": 1,
                    "content_exposed": False,
                    "freshness": {
                        "state": "fresh",
                        "revision_current": True,
                        "refresh_error_type": None,
                    },
                },
            }
        },
    }


def present_report(*, pending: bool = False) -> dict:
    readiness = fresh_readiness()
    report = absent_report()
    source = report["checks"][0]
    source["ok"] = not pending
    source["evidence"] = source_assurance_terminal_evidence(readiness)[1]
    if pending:
        source["evidence"]["counts"]["accounted"] = 0
        source["evidence"]["counts"]["pending"] = 1
    report["checks"][1] = {
        "name": "deployment_topology_and_recovery_readiness",
        "ok": not pending,
        "evidence": {
            "contract": "deployment-readiness-v1",
            "state": "blocked" if pending else "ready",
            "ready": not pending,
            "blocking_dimensions": ["observation_effects"] if pending else [],
        },
    }
    report["checks"][2]["ok"] = True
    report["status"] = "failed" if pending else "healthy"
    return report


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(seconds):
        value.now += seconds
        value.sleeps.append(seconds)

    monkeypatch.setattr(
        "bootstrap.service_settlement.deployment_clock", lambda: value.now
    )
    monkeypatch.setattr(
        "bootstrap.service_settlement.time", SimpleNamespace(sleep=sleep)
    )
    return value


def _services(*observations):
    services = Mock(spec=ServiceManager)
    services.runtime_is_live.return_value = True
    if observations:
        services.runtime_diagnostics.side_effect = observations
    else:
        services.runtime_diagnostics.return_value = fresh_readiness()
    return services


@pytest.mark.parametrize("blackout_at", ["initial", "retry", "full_proof"])
def test_absent_initial_retry_or_full_doctor_still_requires_actual_full_proof(
    clock, blackout_at
):
    healthy = present_report()
    if blackout_at == "initial":
        reports = [absent_report(), healthy, healthy]
        expected = [False, False, True]
    elif blackout_at == "retry":
        reports = [present_report(pending=True), absent_report(), healthy, healthy]
        expected = [False, False, False, True]
    else:
        reports = [healthy, absent_report(), healthy, healthy]
        expected = [False, True, False, True]
    doctor = Mock(side_effect=reports)
    services = _services()
    result = wait_for_settled_doctor(
        run_doctor=doctor, services=services, test_mode=False
    )
    assert result is healthy
    assert [
        call.kwargs["include_synthetic_proof"] for call in doctor.call_args_list
    ] == expected
    assert services.runtime_is_live.call_count == 1


@pytest.mark.parametrize("observations", ["none", "fresh", "alternating"])
def test_safe_metadata_cannot_renew_missing_doctor_three_retry_budget(
    clock, observations
):
    report = absent_report()
    doctor = Mock(return_value=report)
    services = _services()
    if observations == "none":
        services.runtime_diagnostics.return_value = None
    elif observations == "alternating":
        services.runtime_diagnostics.side_effect = [
            None,
            fresh_readiness(),
            None,
            fresh_readiness(),
        ]
    result = wait_for_settled_doctor(
        run_doctor=doctor, services=services, test_mode=False
    )
    assert result is report
    assert doctor.call_count == 4
    assert len(clock.sleeps) == 3
    assert all(
        not call.kwargs["include_synthetic_proof"] for call in doctor.call_args_list
    )


@pytest.mark.parametrize(
    "failure",
    [
        "privacy",
        "provider",
        "projection",
        "unknown_deployment",
        "present_source",
        "launch_definition",
    ],
)
def test_blackout_shape_never_masks_independent_or_present_unknown_evidence(
    clock, failure
):
    report = absent_report()
    if failure in {"privacy", "provider", "projection"}:
        report["checks"].append({"name": f"fixture_{failure}_proof", "ok": False})
    elif failure == "unknown_deployment":
        report["checks"][1]["evidence"] = {"contract": "unknown-v9"}
    elif failure == "present_source":
        report["checks"][0]["evidence"]["contract"] = "source-assurance-v1"
    else:
        report["checks"][2]["evidence"]["definitions"]["fixture-runtime"] = False
    doctor = Mock(return_value=report)
    services = _services()
    assert (
        wait_for_settled_doctor(run_doctor=doctor, services=services, test_mode=False)
        is report
    )
    doctor.assert_called_once_with(include_synthetic_proof=False)
    services.runtime_diagnostics.assert_not_called()
    assert clock.sleeps == []


@pytest.mark.parametrize(
    "failure", ["unknown_contract", "source_failed", "consumer_failed", "structural"]
)
def test_fresh_observation_must_be_safe_before_retrying_missing_doctor(clock, failure):
    readiness = fresh_readiness()
    if failure == "unknown_contract":
        readiness["deployment_readiness"]["contract"] = "unknown-v9"
    elif failure == "source_failed":
        readiness["capabilities"]["policy:source-assurance"]["details"]["counts"][
            "failed"
        ] = 1
    else:
        readiness["capabilities"]["pipeline:fixture"] = {
            "kind": "reactive_pipeline",
            "state": "degraded" if failure == "structural" else "healthy",
            "details": {"consecutive_failures": 1},
        }
    doctor = Mock(side_effect=[absent_report(), present_report()])
    result = wait_for_settled_doctor(
        run_doctor=doctor, services=_services(readiness), test_mode=False
    )
    assert result["status"] == "failed"
    assert result["checks"][-1]["name"] == "bounded_service_settlement"
    assert clock.sleeps == []


def test_structural_actual_full_proof_is_not_replaced_by_safe_metadata(clock):
    full = present_report()
    full["status"] = "failed"
    full["checks"].append({"name": "canonical_synthetic_round_trip", "ok": False})
    doctor = Mock(side_effect=[absent_report(), present_report(), full])
    result = wait_for_settled_doctor(
        run_doctor=doctor, services=_services(), test_mode=False
    )
    assert result is full
    assert doctor.call_args.kwargs == {"include_synthetic_proof": True}


def test_blackout_retries_preserve_selected_release_and_absolute_deadline(clock):
    selected = Mock()
    original = absent_report()
    retained = copy.deepcopy(original)
    doctor = Mock(return_value=original)
    result = wait_for_settled_doctor(
        run_doctor=doctor,
        services=_services(),
        test_mode=False,
        absolute_deadline=121,
        poll_seconds=2,
        validate_selection=selected,
    )
    assert result["status"] == "failed"
    assert result["checks"][-1]["evidence"]["reason"] == "proof_budget_exhausted"
    assert original == retained
    assert clock.now == 2
    assert selected.call_count > 1
    assert doctor.call_count == 1


def test_present_doctor_evidence_ends_blackout_before_a_later_new_episode(clock):
    report = absent_report()
    doctor = Mock(
        side_effect=[
            report,
            report,
            present_report(pending=True),
            report,
            report,
            report,
            report,
        ]
    )
    assert (
        wait_for_settled_doctor(
            run_doctor=doctor, services=_services(), test_mode=False
        )
        is report
    )
    assert doctor.call_count == 7


@pytest.mark.parametrize("next_blackout", ["readiness", "full_doctor"])
def test_present_last_retry_ends_blackout_before_next_missing_observation(
    clock, next_blackout
):
    report = absent_report()
    healthy = present_report()
    if next_blackout == "readiness":
        doctor = Mock(
            side_effect=[
                report,
                report,
                report,
                present_report(pending=True),
                healthy,
                healthy,
            ]
        )
        services = _services(
            fresh_readiness(), fresh_readiness(), fresh_readiness(), None
        )
        expected = [False, False, False, False, False, True]
    else:
        doctor = Mock(
            side_effect=[report, report, report, healthy, report, healthy, healthy]
        )
        services = _services()
        expected = [False, False, False, False, True, False, True]
    assert (
        wait_for_settled_doctor(run_doctor=doctor, services=services, test_mode=False)
        is healthy
    )
    assert [
        call.kwargs["include_synthetic_proof"] for call in doctor.call_args_list
    ] == expected


def test_absent_doctor_does_not_reset_pending_consumer_watchdog(clock):
    readiness = fresh_readiness()
    source = readiness["capabilities"]["policy:source-assurance"]["details"]
    source["counts"]["pending"] = 1
    source["counts"]["accounted"] = 0
    readiness["capabilities"]["pipeline:fixture"] = {
        "kind": "reactive_pipeline",
        "state": "healthy",
        "details": {
            "version": 23,
            "checkpoint_event_id": "ev_fixture_0001",
            "lag_count": 1,
            "consecutive_failures": 0,
        },
    }
    doctor = Mock(side_effect=[present_report(pending=True), absent_report()])
    result = wait_for_settled_doctor(
        run_doctor=doctor,
        services=_services(readiness, None, readiness, readiness),
        test_mode=False,
        poll_seconds=200,
    )
    assert result["checks"][-1]["evidence"]["reason"] == "consumer_stalled"
    assert clock.now == 600
    assert doctor.call_count == 2
