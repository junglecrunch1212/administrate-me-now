"""Contained recovery for the requested-email v5 audit hydration defect.

This never repairs events or excuses a provider effect. The corrected selective
reader and the complete retained-chain auditor must agree. The old process stays
visibly degraded until a genuinely healthy successor replaces it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, model_validator

from adminme.cos_response_assurance import AUDIT_TYPES, cos_provider_effect_counts
from adminme.events.log import EventLog
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.deployment_budget import deployment_clock
from bootstrap.secrets import secret_store


class EffectContextRepairError(ValueError):
    pass


class EffectContextRepairRequest(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    kind: Literal["requested_email_v5_context_hydration"]
    approval_event_ids: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique(self) -> EffectContextRepairRequest:
        if len(set(self.approval_event_ids)) != len(self.approval_event_ids) or any(
            not value or len(value) > 240 for value in self.approval_event_ids
        ):
            raise ValueError("repair approval inventory is invalid")
        return self


class EffectContextProof(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    contract: Literal["requested-email-effect-context-repair-v1"] = (
        "requested-email-effect-context-repair-v1"
    )
    request: EffectContextRepairRequest
    predecessor_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_auditor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chain_auditor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_actions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_effect_windows: dict[str, Any]
    initial_watermark: str = Field(min_length=1, max_length=240)
    chain_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_called_true: int = Field(ge=1)
    legacy_effect_counts: dict[str, int]
    corrected_effect_counts: dict[str, int]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def _private(path: Path) -> bytes:
    from bootstrap.candidate_readiness import _read_private_file

    return _read_private_file(path, maximum_size=1024 * 1024)[0]


def require_disabled(paths: DeploymentPaths, expected: str | None = None) -> str:
    from adminme.request_actions_control import FILENAME

    payload = _private(paths.instance_dir / "config" / FILENAME)
    value = json.loads(payload)
    if (
        set(value) != {"contract", "disabled", "revoked_before"}
        or value["contract"] != "request-actions-control-v1"
        or value["disabled"] is not True
        or not isinstance(value["revoked_before"], str)
        or datetime.fromisoformat(value["revoked_before"]).tzinfo is None
    ):
        raise EffectContextRepairError(
            "repair requires explicit request-action revocation"
        )
    digest = hashlib.sha256(payload).hexdigest()
    if expected is not None and digest != expected:
        raise EffectContextRepairError("request-action recovery control changed")
    return digest


def _windows(paths: DeploymentPaths) -> dict[str, Any]:
    from bootstrap.candidate_readiness import provider_effect_window_evidence

    return provider_effect_window_evidence(
        json.loads(
            _private(paths.instance_dir / "config" / "deployment-readiness.json")
        ),
        now=datetime.now(timezone.utc),
    )


def _window_rows(value: dict[str, Any]) -> list[dict[str, Any]]:
    return [*value["prior_provider_effect_windows"], value["provider_effect_window"]]


def _zero_window(window: dict[str, Any]) -> None:
    from bootstrap.htr10_export import _INTEGRITY_ZERO_COUNTS

    counts = window.get("counts") or {}
    if (
        window.get("state") != "healthy"
        or window.get("started_at_configured") is not True
        or not window.get("started_at")
        or any(
            type(counts.get(k)) is not int or counts[k] != 0
            for k in (
                *_INTEGRITY_ZERO_COUNTS,
                "provider_called_true",
                "suppressed_false_or_unknown",
            )
        )
    ):
        raise EffectContextRepairError(
            "observation-window effect accounting is not healthy and zero"
        )


def _legacy_view(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The exact pre-fix envelope-selection predicate, never its validator."""
    ids = {
        e["event_id"]
        for e in events
        if e["type"]
        in {"cos.response.approved", "cos.response.attempted", "cos.response.result"}
    }
    host_ids = {
        e["payload"].get("openclaw_approval_event_id")
        for e in events
        if e["type"] == "cos.response.attempted"
    }
    standing = {
        str(e["payload"].get(key) or "")
        for e in events
        if e["type"] == "cos.response.approved"
        and e["payload"].get("authorization_method")
        in {"standing_self_reply", "requested_self_reminder"}
        for key in (
            "standing_authorization_event_id",
            "source_message_event_id",
            "reminder_event_id",
            "reminder_binding_event_id",
        )
    } - {""}
    return [
        e
        for e in events
        if (
            e["event_id"] in ids | host_ids | standing
            or (
                standing
                and e["type"] in {"cos.channels.activated", "cos.channels.deactivated"}
            )
            or (
                e["type"] == "cos.reply.reminder_cancelled"
                and e["payload"].get("reminder_event_id") in standing
            )
            or (ids and e["type"] == "settings.cos_response_allowlist_changed")
            or any(
                e["payload"].get(k) == "cos.response.send"
                for k in ("operation", "action", "attempted_action")
            )
            or e["payload"].get("initiating_event_id") in ids
            or e["payload"].get("domain_approval_event_id") in ids
        )
    ]


def validate_diagnostics(
    value: dict[str, Any], *, observation: bool, doctor: dict[str, Any] | None = None
) -> dict[str, Any]:
    from bootstrap.submission_repair import (
        REPAIR_DOCTOR_CHECKS,
        REPAIR_READINESS_DIMENSIONS,
        _accepted_readiness_dimension,
    )
    from bootstrap.recovery_readiness import recovery_consumers_settled

    if (
        value.get("ready") is not False
        or value.get("observation_active") is not observation
        or any(
            value.get(k) is not True
            for k in ("service_ready", "safe_reads", "event_ingest_ready")
        )
        or value.get("write_admission") != "blocked"
        or value.get("startup_error") is not None
    ):
        raise EffectContextRepairError(
            "repair requires exact degraded operational containment"
        )
    readiness = value.get("deployment_readiness") or {}
    dimensions = readiness.get("dimensions") or {}
    if (
        readiness.get("contract") != "deployment-readiness-v1"
        or readiness.get("ready") is not False
        or readiness.get("blocking_dimensions") != ["observation_effects"]
        or not REPAIR_READINESS_DIMENSIONS <= set(dimensions)
        or any(
            not _accepted_readiness_dimension(k, v)
            for k, v in dimensions.items()
            if k != "observation_effects"
        )
    ):
        raise EffectContextRepairError("repair has unrelated readiness blockers")
    capabilities = value.get("capabilities") or {}
    from bootstrap.contracts import PROJECTION_NAMES
    from bootstrap.doctor import _required_reactive_pipeline_ids

    expected_consumers = {
        f"projection:{name}" for name in PROJECTION_NAMES
    } | _required_reactive_pipeline_ids(Path(__file__).resolve().parents[1])
    if not expected_consumers <= set(capabilities):
        raise EffectContextRepairError("repair consumer inventory is incomplete")
    permitted = {
        "policy:source-assurance",
        "product:member",
        "surface:triage-processing",
    }
    if (
        not capabilities
        or any(
            not isinstance(v, dict)
            or (
                v.get("state") not in {"healthy", "observation_only", "disabled"}
                and not (k in permitted and v.get("state") == "degraded")
            )
            for k, v in capabilities.items()
        )
        or not recovery_consumers_settled(value)
    ):
        raise EffectContextRepairError(
            "repair has unrelated capability or consumer degradation"
        )
    source = capabilities.get("policy:source-assurance", {}).get("details") or {}
    fresh = source.get("freshness") or {}
    if (
        source.get("state") != "degraded"
        or source.get("content_exposed") is not False
        or any(
            (source.get("counts") or {}).get(k) != 0
            for k in ("missing", "failed", "pending")
        )
        or fresh.get("state") != "fresh"
        or fresh.get("revision_current") is not True
        or fresh.get("settled_revision") is not True
        or fresh.get("pending_recheck_required") is not False
        or fresh.get("refresh_in_progress") is not False
        or fresh.get("refresh_error_type") is not None
        or not source.get("watermark_event_id")
    ):
        raise EffectContextRepairError(
            "repair requires fresh settled source accounting"
        )
    _zero_window((source.get("provider_effects") or {}).get("observation_window") or {})
    if doctor is not None:
        rows = doctor.get("checks")
        if (
            doctor.get("status") != "failed"
            or not isinstance(rows, list)
            or any(
                not isinstance(r, dict)
                or not isinstance(r.get("name"), str)
                or type(r.get("ok")) is not bool
                for r in rows
            )
            or len({r.get("name") for r in rows}) != len(rows)
            or not REPAIR_DOCTOR_CHECKS <= {r.get("name") for r in rows}
            or not {
                "source_assurance_terminal_dispositions",
                "deployment_topology_and_recovery_readiness",
            }
            <= {r["name"] for r in rows if not r["ok"]}
            or not {r["name"] for r in rows if not r["ok"]}
            <= {
                "source_assurance_terminal_dispositions",
                "launchd_and_process_state",
                "deployment_topology_and_recovery_readiness",
            }
        ):
            raise EffectContextRepairError("repair Doctor has unrelated failures")
    return source


_EXTERNAL_TYPES = {
    "external_sync.write_requested",
    "external_sync.write_completed",
    "external_sync.write_failed",
    "external_sync.legacy_effect_reconciled",
}


def _effect_counts(
    events: list[dict[str, Any]], *, include: Any = None
) -> dict[str, int]:
    """Reuse production effect validators on one closed envelope snapshot."""
    from adminme.source_assurance import _provider_effect_counts

    requests: list[dict[str, Any]] = []
    terminals: dict[str, list[dict[str, Any]]] = {}
    reconciliations: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if include is not None and not include(event):
            continue
        kind = event["type"]
        command = str(event["payload"].get("command_id") or "")
        if kind == "external_sync.write_requested":
            requests.append(event)
        elif kind in {"external_sync.write_completed", "external_sync.write_failed"}:
            terminals.setdefault(command, []).append(event)
        elif kind == "external_sync.legacy_effect_reconciled" and include is None:
            # Match production: interval audits do not apply all-history repairs.
            reconciliations.setdefault(command, []).append(event)
    counts = _provider_effect_counts(requests, terminals, reconciliations)
    for key, value in cos_provider_effect_counts(
        [e for e in events if e["type"] in AUDIT_TYPES], include=include
    ).items():
        counts[key] += value
    return counts


def _audit_windows(
    events: list[dict[str, Any]], windows: dict[str, Any]
) -> dict[str, Any]:
    from adminme.source_assurance import _aware_datetime, _event_at_or_after

    result: dict[str, Any] = {}
    for window in _window_rows(windows):
        start = _aware_datetime(window["started_at"])
        end = _aware_datetime(window.get("ended_at"))
        if start is None or (
            window.get("ended_at") is not None and (end is None or end <= start)
        ):
            raise EffectContextRepairError("attested observation bounds are invalid")

        def inside(
            event: dict[str, Any],
            *,
            window_start: datetime = cast(datetime, start),
            window_end: datetime | None = end,
        ) -> bool:
            return _event_at_or_after(event, window_start) and (
                window_end is None or not _event_at_or_after(event, window_end)
            )

        result = {
            "state": "healthy",
            "started_at_configured": True,
            "started_at": start.isoformat(),
            "ended_at": end.isoformat() if end else None,
            "counts": _effect_counts(events, include=inside),
            "content_exposed": False,
        }
        _zero_window(result)
    return result


async def audit_context(
    log: EventLog,
    *,
    repository: Path,
    request: EffectContextRepairRequest,
    through: str,
    capabilities: dict[str, Any],
    windows: dict[str, Any],
    tenant_id: str,
    reported: dict[str, Any] | None = None,
    baseline: EffectContextProof | None = None,
) -> dict[str, Any]:
    from adminme.source_assurance import audit_source_assurance
    from bootstrap.htr10_export import _INTEGRITY_ZERO_COUNTS

    # The envelope snapshot is reused for all retained intervals and the original
    # proof prefix. Only one complete source/assessment audit is needed, regardless
    # of interval count. No cache or proof survives this verification call.
    events = [
        e
        async for e in log.read_since(
            types=sorted(AUDIT_TYPES | _EXTERNAL_TYPES), through=through
        )
    ]
    if any(e.get("tenant_id") != tenant_id for e in events):
        raise EffectContextRepairError("repair context crosses the configured tenant")
    full = _effect_counts(events)
    cos_events = [e for e in events if e["type"] in AUDIT_TYPES]
    cos_full = cos_provider_effect_counts(cos_events)
    old_events = _legacy_view(cos_events)
    old = cos_provider_effect_counts(old_events)
    old_ids = {e["event_id"] for e in old_events}
    attempts = {
        e["payload"].get("approval_event_id")
        for e in events
        if e["type"] == "cos.response.attempted"
    }
    affected = {
        e["event_id"]
        for e in events
        if e["type"] == "cos.response.approved"
        and e.get("schema_version") == 5
        and e["payload"].get("authorization_method") == "requested_email"
        and any(
            e["payload"].get(k) not in old_ids
            for k in ("request_source_event_id", "request_authorization_event_id")
        )
        and e["event_id"] in attempts
    }
    if affected != set(request.approval_event_ids):
        raise EffectContextRepairError(
            "repair target approvals do not exactly explain missing context"
        )
    if any(full.get(k, 0) != 0 for k in _INTEGRITY_ZERO_COUNTS):
        raise EffectContextRepairError("complete retained effect chain is not healthy")
    delta = {
        k: old.get(k, 0) - cos_full.get(k, 0)
        for k in set(old) | set(cos_full)
        if old.get(k, 0) != cos_full.get(k, 0)
    }
    if delta != {
        "malformed_request": len(affected),
        "approval_correlation_missing": 3 * len(affected),
    }:
        raise EffectContextRepairError(
            "effect failure is not the closed requested-email hydration defect"
        )
    if baseline is not None:
        prefix = [e for e in events if e["event_id"] <= baseline.initial_watermark]
        if (
            through < baseline.initial_watermark
            or _digest(prefix) != baseline.chain_sha256
            or _effect_counts(prefix) != baseline.corrected_effect_counts
        ):
            raise EffectContextRepairError("retained repair history differs")
    current_window = _audit_windows(events, windows)
    window = windows["provider_effect_window"]
    corrected = await audit_source_assurance(
        log,
        repository_root=repository,
        capabilities=capabilities,
        through_event_id=through,
        provider_effect_window_started_at=window["started_at"],
        provider_effect_window_ended_at=window.get("ended_at"),
    )
    effects = corrected["provider_effects"]
    if (
        effects.get("state") != "healthy"
        or effects["counts"] != full
        or effects["observation_window"] != current_window
    ):
        raise EffectContextRepairError(
            "corrected production audit does not prove retained integrity"
        )
    expected_old = {k: v + delta.get(k, 0) for k, v in full.items()}
    if reported is not None and (
        reported.get("watermark_event_id", "") > through
        or reported.get("provider_effects", {}).get("observation_window")
        != current_window
        or reported.get("provider_effects", {}).get("counts") != expected_old
    ):
        raise EffectContextRepairError(
            "reported failure or window is not explained by retained context"
        )
    return {
        "watermark": through,
        "chain_sha256": _digest(events),
        "corrected_effect_counts": full,
        "legacy_effect_counts": expected_old,
        "provider_called_true": full["provider_called_true"],
    }


async def _offline(
    paths: DeploymentPaths,
    repository: Path,
    request: EffectContextRepairRequest,
    *,
    through: str | None,
    capabilities: dict[str, Any],
    reported: dict[str, Any] | None = None,
    test_mode: bool = False,
    baseline: EffectContextProof | None = None,
) -> dict[str, Any]:
    from adminme.lib.instance_config import load_instance_config

    refs = json.loads(
        _private(paths.instance_dir / "config" / "secret-references.json")
    )["references"]
    # Test mode is supplied only by the enclosing reviewed operation/fixture.
    key = secret_store(paths, test_mode=test_mode).get(refs["event_log"])
    log = EventLog(paths.instance_dir / "events" / "events.db", key, read_only=True)
    try:
        watermark = through or await log.latest_event_id()
        if not watermark:
            raise EffectContextRepairError("repair audit watermark is absent")
        return await audit_context(
            log,
            repository=repository,
            request=request,
            through=watermark,
            capabilities=capabilities,
            windows=_windows(paths),
            tenant_id=load_instance_config(paths.instance_dir).tenant_id,
            reported=reported,
            baseline=baseline,
        )
    finally:
        await log.close()


def _read_audit(
    paths: DeploymentPaths,
    repository: Path,
    request: EffectContextRepairRequest,
    *,
    test_mode: bool,
    **kwargs: Any,
) -> dict[str, Any]:
    return asyncio.run(
        _offline(paths, repository, request, test_mode=test_mode, **kwargs)
    )


def prepare_proof(
    paths: DeploymentPaths,
    repository: Path,
    request: EffectContextRepairRequest,
    diagnostics: dict[str, Any],
    *,
    predecessor: str,
    candidate: str,
    test_mode: bool,
) -> EffectContextProof:
    source = validate_diagnostics(diagnostics, observation=False)
    proof = _read_audit(
        paths,
        repository,
        request,
        through=source["watermark_event_id"],
        capabilities=diagnostics["capabilities"],
        reported=source,
        test_mode=test_mode,
    )
    return EffectContextProof(
        request=request,
        predecessor_commit=predecessor,
        candidate_commit=candidate,
        source_auditor_sha256=hashlib.sha256(
            (repository / "adminme/source_assurance.py").read_bytes()
        ).hexdigest(),
        chain_auditor_sha256=hashlib.sha256(
            (repository / "adminme/cos_response_assurance.py").read_bytes()
        ).hexdigest(),
        request_actions_sha256=require_disabled(paths),
        provider_effect_windows=_windows(paths),
        initial_watermark=proof["watermark"],
        **{
            k: proof[k]
            for k in (
                "chain_sha256",
                "provider_called_true",
                "legacy_effect_counts",
                "corrected_effect_counts",
            )
        },
    )


def verify_proof(
    paths: DeploymentPaths,
    repository: Path,
    proof: EffectContextProof,
    *,
    test_mode: bool,
    diagnostics: dict[str, Any] | None = None,
    observation: bool = True,
    doctor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_disabled(paths, proof.request_actions_sha256)
    current_windows = _window_rows(_windows(paths))
    original_windows = _window_rows(proof.provider_effect_windows)
    if current_windows[: len(original_windows)] != original_windows or len(
        current_windows
    ) not in (
        {len(original_windows)}
        if not observation
        else {len(original_windows), len(original_windows) + 1}
    ):
        raise EffectContextRepairError("attested observation windows changed")
    for filename, expected in (
        ("source_assurance.py", proof.source_auditor_sha256),
        ("cos_response_assurance.py", proof.chain_auditor_sha256),
    ):
        if (
            hashlib.sha256((repository / "adminme" / filename).read_bytes()).hexdigest()
            != expected
        ):
            raise EffectContextRepairError("reviewed repair auditor changed")
    source = (
        validate_diagnostics(diagnostics, observation=observation, doctor=doctor)
        if diagnostics is not None
        else None
    )
    capabilities = diagnostics["capabilities"] if diagnostics is not None else {}
    current = _read_audit(
        paths,
        repository,
        proof.request,
        through=None,
        capabilities=capabilities,
        reported=source,
        baseline=proof,
        test_mode=test_mode,
    )
    if current["provider_called_true"] != proof.provider_called_true:
        raise EffectContextRepairError(
            "provider contact occurred after repair preparation"
        )
    return {
        "status": "contained_degraded" if observation else "repair_pending",
        "watermark_event_id": current["watermark"],
        "provider_called_true": current["provider_called_true"],
        "content_exposed": False,
    }


def load_plan(
    path: Path,
    paths: DeploymentPaths,
    repository: Path,
    *,
    candidate: str,
    predecessor: str,
    test_mode: bool = False,
) -> Any:
    from bootstrap.cos_channel_activation import MaintenanceRestorationPlan
    from bootstrap.source_identity import reviewed_candidate_commit

    plan = MaintenanceRestorationPlan.model_validate(json.loads(_private(path)))
    proof = plan.effect_context_proof
    if (
        proof is None
        or plan.effect_context_repair != proof.request
        or plan.target_commit != candidate
        or plan.predecessor_commit != predecessor
        or proof.candidate_commit != candidate
        or proof.predecessor_commit != predecessor
    ):
        raise EffectContextRepairError("repair plan release binding differs")
    prepared = datetime.fromisoformat(plan.prepared_at)
    expires = datetime.fromisoformat(plan.expires_at)
    now = datetime.now(timezone.utc)
    if (
        prepared.tzinfo is None
        or expires.tzinfo is None
        or not prepared <= now < expires
    ):
        raise EffectContextRepairError("repair maintenance plan is expired or invalid")
    if not test_mode and reviewed_candidate_commit(repository) != candidate:
        raise EffectContextRepairError("repair requires the exact reviewed candidate")
    history = json.loads(
        _private(
            paths.app_dir / "cos-channel-operation" / "history" / f"{plan.plan_id}.json"
        )
    )
    if (
        history.get("phase") != "committed"
        or history.get("operation") != "enter_observation"
        or history.get("maintenance_plan_sha256") != plan.digest()
        or history.get("effect_context_proof") != proof.model_dump(mode="json")
        or history.get("release_commit") != predecessor
        or history.get("controller_commit") != candidate
    ):
        raise EffectContextRepairError("repair plan has no exact committed containment")
    from bootstrap.candidate_readiness import capture_deployment_snapshot

    if history.get("initial_snapshot") != capture_deployment_snapshot(paths).public():
        raise EffectContextRepairError("contained repair configuration changed")
    require_disabled(paths, proof.request_actions_sha256)
    return plan


def retain_doctor_receipt(paths: DeploymentPaths, report: dict[str, Any]) -> Path:
    """Retain only closed diagnostic metadata, including on validation failure."""
    from bootstrap.submission_repair import REPAIR_DOCTOR_CHECKS

    names = REPAIR_DOCTOR_CHECKS | {"canonical_synthetic_round_trip"}
    rows = report.get("checks")
    checks = (
        [
            {"name": row["name"], "ok": row["ok"]}
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("name"), str)
            and row.get("name") in names
            and type(row.get("ok")) is bool
        ]
        if isinstance(rows, list)
        else []
    )
    receipt = {
        "status": report.get("status")
        if report.get("status") in ("healthy", "failed")
        else "invalid",
        "checks": checks,
    }
    path = paths.app_dir / "effect-context-repair" / f"doctor-{uuid.uuid4().hex}.json"
    atomic_write(path, json.dumps(receipt, sort_keys=True).encode())
    return path


def wait_contained(
    paths: DeploymentPaths,
    repository: Path,
    proof: EffectContextProof,
    *,
    diagnostics: Callable[[], dict[str, Any] | None],
    doctor: Callable[[], dict[str, Any]],
    deadline: float,
    test_mode: bool,
) -> dict[str, Any]:
    from bootstrap.services import RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS
    from bootstrap.service_settlement import RELEASE_DOCTOR_TIMEOUT_SECONDS

    reserve = RELEASE_DOCTOR_TIMEOUT_SECONDS + 2 * RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS
    while deployment_clock() + reserve < deadline:
        try:
            before = diagnostics() or {}
            validate_diagnostics(before, observation=True)
        except EffectContextRepairError:
            time.sleep(min(2, max(0, deadline - deployment_clock())))
            continue
        verify_proof(paths, repository, proof, diagnostics=before, test_mode=test_mode)
        report = doctor()
        retain_doctor_receipt(paths, report)
        verify_proof(
            paths,
            repository,
            proof,
            diagnostics=diagnostics() or {},
            doctor=report,
            test_mode=test_mode,
        )
        return report
    raise EffectContextRepairError("contained repair predecessor did not settle")
