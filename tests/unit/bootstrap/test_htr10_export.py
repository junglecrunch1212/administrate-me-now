from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import bootstrap.htr10_export as htr10_export
from bootstrap.contracts import DeploymentPaths
from bootstrap.contracts import atomic_write
from bootstrap.candidate_readiness import CandidateReadinessError, build_htr10_review
from bootstrap.secrets import secret_store
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from tests.unit.test_cos_response_assurance import _chain
from bootstrap.provider_binding_transition import (
    NoEligibleProviderBindingTransitionError,
    ProviderBindingTransitionError,
)


def test_reviewed_binding_audit_view_uses_already_active_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = DeploymentPaths.from_root(tmp_path)
    active = {"adapters": [{"kind": "fixture"}]}

    def no_remaining_transition(*args: Any, **kwargs: Any) -> None:
        raise NoEligibleProviderBindingTransitionError(
            "provider-binding transition has no eligible changes"
        )

    monkeypatch.setattr(
        htr10_export,
        "prepare_live_provider_binding_transition",
        no_remaining_transition,
    )

    reviewed, transition = htr10_export._reviewed_binding_audit_view(
        paths,
        adapter_payload=b"{}",
        adapter_config=active,
        mapping={"members": []},
        test_mode=True,
    )

    assert reviewed is active
    assert transition is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "placement", ["between", "prior", "current", "unknown_between"]
)
async def test_export_audits_every_observation_interval_without_reclassifying_live_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, placement: str
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    fixed_now = datetime(2026, 10, 21, tzinfo=timezone.utc)

    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (
                fixed_now.astimezone(tz)
                if tz is not None
                else fixed_now.replace(tzinfo=None)
            )

    monkeypatch.setattr(htr10_export, "datetime", FixtureClock)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    key = b"w" * 32
    reference = secret_store(paths, test_mode=True).put("event-log", key)
    current = {
        "started_at": "2026-10-20T12:00:07Z",
        "ended_at": "2026-10-20T13:00:00Z",
        "reason": "cos_enter_observation",
    }
    prior = {"started_at": "2026-10-19T00:00:00Z", "ended_at": "2026-10-20T12:00:04Z"}
    if placement == "prior":
        prior["ended_at"] = "2026-10-20T12:00:07Z"
    elif placement == "current":
        current["started_at"] = "2026-10-20T12:00:04Z"
    evidence = {
        "provider_effect_window": current,
        "prior_provider_effect_windows": [prior],
    }
    config = paths.instance_dir / "config"
    for name, value in (
        ("adapters.json", {"adapters": []}),
        ("member-mapping.json", {"members": []}),
        (
            "deployment-readiness.json",
            {"contract": "deployment-readiness-v1", **evidence},
        ),
        ("secret-references.json", {"references": {"event_log": reference}}),
    ):
        atomic_write(config / name, json.dumps(value).encode())
    monkeypatch.setattr(
        htr10_export,
        "_reviewed_binding_audit_view",
        lambda *args, **kwargs: ({"adapters": []}, None),
    )
    monkeypatch.setattr(
        htr10_export.ServiceManager,
        "runtime_diagnostics",
        lambda _self: {"ready": True, "observation_active": True, "capabilities": {}},
    )
    log = EventLog(paths.instance_dir / "events" / "events.db", key)
    try:
        for event in _chain("unknown" if placement == "unknown_between" else "sent"):
            if event["type"] in {"cos.response.approved", "cos.response.attempted"}:
                event.update(
                    source_adapter="openclaw-plugin", source_account_id="cos-response"
                )
            await log.append(EventEnvelope(**event))
    finally:
        await log.close()
    historical = paths.backup_dir / "historical.json"
    source = paths.backup_dir / "source.json"
    await htr10_export.export_htr10_audits(
        paths,
        repository_root=Path(__file__).resolve().parents[3],
        historical_output=historical,
        source_output=source,
        ownership_reviewed=True,
        test_mode=True,
    )
    audit = json.loads(source.read_bytes())
    assert json.loads(historical.read_bytes())["state"] == "healthy"
    effects = audit["provider_effects"]
    assert audit["provider_effect_windows"] == evidence
    assert effects["observation_window"]["ended_at"] == "2026-10-20T13:00:00+00:00"
    assert (
        effects["prior_observation_windows"][0]["ended_at"]
        == datetime.fromisoformat(prior["ended_at"].replace("Z", "+00:00")).isoformat()
    )
    if placement == "unknown_between":
        assert effects["counts"]["provider_called_unknown"] == 1
        assert effects["state"] == "degraded"
    else:
        assert effects["state"] == "healthy"
        assert effects["counts"]["provider_called_true"] == 1
    if placement == "between":
        assert effects["observation_window"]["counts"]["provider_called_true"] == 0
        assert (
            effects["prior_observation_windows"][0]["counts"]["provider_called_true"]
            == 0
        )
        review = build_htr10_review(
            paths,
            historical_attribution=historical,
            source_assurance=source,
            observed_at=fixed_now.isoformat(),
            now=fixed_now,
        )
        assert review["provider_effect_windows"] == evidence
    else:
        with pytest.raises(CandidateReadinessError, match="unhealthy"):
            build_htr10_review(
                paths,
                historical_attribution=historical,
                source_assurance=source,
                observed_at=fixed_now.isoformat(),
                now=fixed_now,
            )
    assert "member-audit@example.invalid" not in source.read_text()
    assert "Synthetic private response" not in source.read_text()


def test_reviewed_binding_audit_view_preserves_prepared_transition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = DeploymentPaths.from_root(tmp_path)
    prepared = {"adapters": [{"kind": "prepared"}]}
    public = {"contract": "provider-binding-config-transition-v1"}

    class _Transition:
        def __init__(self) -> None:
            self.prepared = prepared

        def public(self) -> dict[str, Any]:
            return public

    monkeypatch.setattr(
        htr10_export,
        "prepare_live_provider_binding_transition",
        lambda *args, **kwargs: _Transition(),
    )

    reviewed, transition = htr10_export._reviewed_binding_audit_view(
        paths,
        adapter_payload=b"{}",
        adapter_config={"adapters": []},
        mapping={"members": []},
        test_mode=True,
    )

    assert reviewed is prepared
    assert transition is public


def test_reviewed_binding_audit_view_rejects_other_transition_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = DeploymentPaths.from_root(tmp_path)

    def ambiguous_transition(*args: Any, **kwargs: Any) -> None:
        raise ProviderBindingTransitionError("ambiguous reviewed binding")

    monkeypatch.setattr(
        htr10_export,
        "prepare_live_provider_binding_transition",
        ambiguous_transition,
    )

    with pytest.raises(
        ProviderBindingTransitionError, match="ambiguous reviewed binding"
    ):
        htr10_export._reviewed_binding_audit_view(
            paths,
            adapter_payload=b"{}",
            adapter_config={"adapters": []},
            mapping={"members": []},
            test_mode=True,
        )


def _repair_diagnostics() -> dict[str, Any]:
    return {
        "status": "degraded",
        "ready": False,
        "safe_reads": True,
        "event_ingest_ready": True,
        "observation_active": True,
        "write_admission": "observation_suppressed",
        "capabilities": {
            "adapter:provider": {"kind": "adapter", "state": "degraded"},
            "projection:items": {"kind": "projection", "state": "healthy"},
            "policy:source-assurance": {
                "kind": "source_disposition_policy",
                "state": "healthy",
            },
        },
    }


def test_runtime_supports_isolated_degraded_adapter_repair_audit() -> None:
    assert htr10_export._runtime_supports_read_only_repair_audit(_repair_diagnostics())


@pytest.mark.parametrize(
    ("mutate",),
    [
        (lambda value: value.update({"safe_reads": False}),),
        (lambda value: value.update({"observation_active": False}),),
        (
            lambda value: value["capabilities"]["adapter:provider"].update(
                {"state": "blocked"}
            ),
        ),
        (
            lambda value: value["capabilities"]["projection:items"].update(
                {"state": "degraded"}
            ),
        ),
    ],
)
def test_runtime_rejects_unsafe_or_non_adapter_repair_audit(
    mutate: Any,
) -> None:
    diagnostics = _repair_diagnostics()
    mutate(diagnostics)

    assert not htr10_export._runtime_supports_read_only_repair_audit(diagnostics)


def _expired_attestation_diagnostics() -> dict[str, Any]:
    zero = {
        name: 0
        for name in (
            "provider_called_true",
            "provider_called_unknown",
            "missing_terminal",
            "duplicate_terminal",
            "approval_correlation_missing",
            "duplicate_request",
            "malformed_request",
            "orphan_terminal",
            "invalid_legacy_reconciliation",
            "duplicate_legacy_reconciliation",
            "suppressed_false_or_unknown",
        )
    }
    blockers = [
        "attestation_binding",
        "service_account_recovery",
        "identity_acceptance",
        "backup_rollback",
        "observation_effects",
    ]
    dimensions = {name: {"required": True, "state": "blocked"} for name in blockers}
    dimensions.update(
        {
            name: {"required": True, "state": "healthy"}
            for name in ("topology", "provider_bindings", "openclaw")
        }
    )
    dimensions["bridges"] = {"required": False, "state": "not_applicable"}
    dimensions["service_channel_identities"] = {
        "required": False,
        "state": "observation",
    }
    return {
        "status": "observation_only",
        "ready": False,
        "service_ready": True,
        "safe_reads": True,
        "event_ingest_ready": True,
        "observation_active": True,
        "write_admission": "blocked",
        "startup_error": None,
        "deployment_readiness": {
            "contract": "deployment-readiness-v1",
            "ready": False,
            "blocking_dimensions": blockers,
            "dimensions": dimensions,
        },
        "capabilities": {
            "pipeline:fixture": {
                "state": "healthy",
                "kind": "reactive_pipeline",
                "details": {
                    "lag_count": 0,
                    "version": 1,
                    "checkpoint_event_id": None,
                    "consecutive_failures": 0,
                },
            },
            "projection:fixture": {
                "state": "healthy",
                "kind": "projection",
                "details": {
                    "lag_count": 0,
                    "version": 1,
                    "checkpoint_event_id": None,
                    "consecutive_failures": 0,
                },
            },
            "policy:source-assurance": {
                "state": "healthy",
                "details": {
                    "state": "healthy",
                    "counts": {"missing": 0, "pending": 0, "failed": 0},
                    "freshness": {
                        "state": "fresh",
                        "revision_current": True,
                        "refresh_in_progress": False,
                        "refresh_error_type": None,
                    },
                    "provider_effects": {
                        "state": "healthy",
                        "counts": dict(zero),
                        "observation_window": {
                            "state": "healthy",
                            "started_at_configured": True,
                            "counts": dict(zero),
                        },
                    },
                },
            },
        },
    }


def test_expired_attestations_allow_only_read_only_evidence_collection() -> None:
    diagnostics = _expired_attestation_diagnostics()
    assert htr10_export._runtime_supports_read_only_attestation_refresh(diagnostics)
    assert htr10_export._runtime_supports_read_only_repair_audit(diagnostics)
    assert diagnostics["ready"] is False
    assert (
        diagnostics["deployment_readiness"]["dimensions"]["service_account_recovery"][
            "state"
        ]
        == "blocked"
    )


@pytest.mark.parametrize(
    "defect",
    [
        "unsafe_reads",
        "not_started",
        "no_ingest",
        "observation_off",
        "writes",
        "startup_error",
        "topology",
        "provider_bindings",
        "openclaw",
        "bridges",
        "unknown_dimension",
        "missing_dimension",
        "contradictory_blockers",
        "pipeline",
        "projection",
        "unknown_capability",
        "source_failed",
        "source_stale",
        "source_revision",
        "source_error",
        "source_pending",
        "provider_called_true",
        "provider_called_unknown",
        "suppressed_false_or_unknown",
        "missing_terminal",
    ],
)
def test_attestation_refresh_refuses_operational_or_effect_failures(
    defect: str,
) -> None:
    diagnostics = _expired_attestation_diagnostics()
    readiness = diagnostics["deployment_readiness"]
    source = diagnostics["capabilities"]["policy:source-assurance"]["details"]
    if defect in {"unsafe_reads", "not_started", "no_ingest", "observation_off"}:
        field = {
            "unsafe_reads": "safe_reads",
            "not_started": "service_ready",
            "no_ingest": "event_ingest_ready",
            "observation_off": "observation_active",
        }[defect]
        diagnostics[field] = False
    elif defect == "writes":
        diagnostics["write_admission"] = "governed_per_request"
    elif defect == "startup_error":
        diagnostics["startup_error"] = "fixture_error"
    elif defect in {"topology", "provider_bindings", "openclaw", "bridges"}:
        readiness["dimensions"][defect]["state"] = "blocked"
    elif defect == "unknown_dimension":
        readiness["dimensions"]["unknown"] = {"required": True, "state": "blocked"}
        readiness["blocking_dimensions"].append("unknown")
    elif defect == "missing_dimension":
        readiness["dimensions"].pop("topology")
    elif defect == "contradictory_blockers":
        readiness["blocking_dimensions"].pop()
    elif defect in {"pipeline", "projection"}:
        diagnostics["capabilities"][f"{defect}:fixture"]["state"] = "degraded"
    elif defect == "unknown_capability":
        diagnostics["capabilities"]["unknown"] = {"state": "unknown"}
    elif defect == "source_failed":
        source["state"] = "degraded"
    elif defect == "source_stale":
        source["freshness"]["state"] = "stale"
    elif defect == "source_revision":
        source["freshness"]["revision_current"] = False
    elif defect == "source_error":
        source["freshness"]["refresh_error_type"] = "fixture_error"
    elif defect == "source_pending":
        source["counts"]["pending"] = 1
    else:
        source["provider_effects"]["observation_window"]["counts"][defect] = 1
    assert not htr10_export._runtime_supports_read_only_attestation_refresh(diagnostics)
