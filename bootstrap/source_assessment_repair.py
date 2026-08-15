"""Release-scoped validation and activation for one degraded source baseline."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.log import EventLog
from adminme.source_assessment_recovery import (
    SourceAssessmentRecoveryBatch,
    SourceAssessmentRecoveryTarget,
    build_recovery_request_envelopes,
    latest_source_assessment,
)
from adminme.source_assurance import audit_source_assurance
from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import secret_store


POLICY_RELATIVE_PATH = Path(
    "bootstrap/release-exceptions/co-018-release-race-recovery.json"
)
_SOURCE_DERIVED_CAPABILITIES = frozenset(
    {
        "policy:source-assurance",
        "product:member",
        "surface:triage-processing",
    }
)
_MISSING_DETAIL_LIMIT = 20
_MISSING_SUMMARY_KEYS = frozenset(
    {"contract", "total_count", "detail_limit", "groups", "content_exposed"}
)
_MISSING_GROUP_KEYS = frozenset(
    {
        "event_type",
        "mode",
        "reason",
        "count",
        "first_event_id",
        "last_event_id",
    }
)


class BaselineCounts(BaseModel):
    model_config = {"extra": "forbid"}

    minimum_obligations: int = Field(ge=1)
    pending: Literal[0]
    maximum_missing: int = Field(ge=0, le=256)
    failed: int = Field(ge=1, le=20)


class BaselineTarget(BaseModel):
    model_config = {"extra": "forbid"}

    source_event_id: str = Field(min_length=1, max_length=240)
    expected_failure_reason: Literal[
        "language_seam_IdempotencyConflict",
        "language_seam_OpenClawTimeout",
        "language_seam_OpenClawUnreachable",
    ]


class PausedBacklogRule(BaseModel):
    model_config = {"extra": "forbid"}

    event_type: Literal[
        "messaging.received",
        "messaging.sent",
    ]
    reason: Literal["assessment_event_absent_after_checkpoint_or_grace"]
    maximum_count: int = Field(ge=1, le=256)


class PausedConsumerBaseline(BaseModel):
    model_config = {"extra": "forbid"}

    subscriber_id: Literal["pipeline:pipeline:noise_filtering"]
    consumer_version: Literal["20.0.0"]
    checkpoint_event_id: str = Field(min_length=1, max_length=240)
    poison_event_id: str = Field(min_length=1, max_length=240)
    consecutive_failures: Literal[5]
    missing_backlog: list[PausedBacklogRule] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def require_unique_backlog_event_types(self) -> "PausedConsumerBaseline":
        event_types = [rule.event_type for rule in self.missing_backlog]
        if len(event_types) != len(set(event_types)):
            raise ValueError("paused backlog rules must contain unique event types")
        return self


class SourceAssessmentRepairPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    contract: Literal["source-assessment-degraded-baseline-v2"]
    required_active_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    observation_mode_required: Literal[True]
    expected_counts: BaselineCounts
    targets: list[BaselineTarget] = Field(min_length=1, max_length=20)
    paused_consumer: PausedConsumerBaseline | None = None
    expires_after_activation: Literal[True]

    @model_validator(mode="after")
    def require_exact_missing_ceiling(self) -> "SourceAssessmentRepairPolicy":
        if self.paused_consumer is None:
            if self.expected_counts.maximum_missing != 0:
                raise ValueError(
                    "a nonzero missing ceiling requires a paused consumer declaration"
                )
            return self
        rule_ceiling = sum(
            rule.maximum_count for rule in self.paused_consumer.missing_backlog
        )
        if rule_ceiling != self.expected_counts.maximum_missing:
            raise ValueError(
                "paused backlog ceilings must equal the total missing ceiling"
            )
        return self


class SourceAssessmentRepairError(RuntimeError):
    pass


def _noise_filtering_checkpoint_token(paths: DeploymentPaths) -> str:
    """Bind a guarded request to the stopped consumer's read-only position.

    A failed candidate can roll back to v19, whose event filter does not include
    recovery requests. If a later message advances that older checkpoint past
    an unprocessed request, the next guarded attempt must append a new request
    beyond it instead of resolving to the skipped idempotent event.
    """

    checkpoint_db = paths.instance_dir / "events" / "bus_checkpoints.db"
    checkpoint = "absent"
    if checkpoint_db.is_file():
        try:
            connection = sqlite3.connect(
                f"file:{checkpoint_db}?mode=ro",
                uri=True,
            )
            try:
                row = connection.execute(
                    "SELECT checkpoint_event_id FROM bus_consumer_offsets "
                    "WHERE subscriber_id=?",
                    ("pipeline:pipeline:noise_filtering",),
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise SourceAssessmentRepairError(
                "noise-filtering recovery checkpoint is unreadable"
            ) from exc
        checkpoint = str(row[0] or "none") if row is not None else "none"
    return hashlib.sha256(checkpoint.encode("utf-8")).hexdigest()[:12]


async def _matching_recovery_refusal_token(
    event_log: EventLog,
    targets: list[SourceAssessmentRecoveryTarget],
) -> str:
    """Change identity only after a prior exact-target command was refused."""

    target_pairs = {
        (target.source_event_id, target.failed_assessment_event_id)
        for target in targets
    }
    request_pairs: dict[str, tuple[str, str]] = {}
    refusal_ids: list[str] = []
    through = await event_log.latest_event_id()
    if through is not None:
        event_types = [
            "source.assessment.recovery_requested",
            "source.assessment.recovery_refused",
        ]
        async for event in event_log.read_metadata_since(
            None,
            through=through,
            types=event_types,
            include_payload_for=event_types,
        ):
            payload = event.get("payload") or {}
            if event["type"] == "source.assessment.recovery_requested":
                request_pairs[str(event["event_id"])] = (
                    str(payload.get("source_event_id") or ""),
                    str(payload.get("failed_assessment_event_id") or ""),
                )
                continue
            request_pair = request_pairs.get(str(payload.get("request_event_id") or ""))
            if request_pair in target_pairs:
                refusal_ids.append(str(event["event_id"]))
    identity = "\n".join(refusal_ids) if refusal_ids else "none"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def load_repair_policy(candidate: Path) -> SourceAssessmentRepairPolicy:
    path = candidate / POLICY_RELATIVE_PATH
    if not path.is_file():
        raise SourceAssessmentRepairError(
            "approved source-assessment repair policy is missing"
        )
    try:
        document = json.loads(path.read_text("utf-8"))
        policy = SourceAssessmentRepairPolicy.model_validate(document)
    except Exception as exc:
        raise SourceAssessmentRepairError(
            "approved source-assessment repair policy is invalid"
        ) from exc
    source_ids = [target.source_event_id for target in policy.targets]
    if len(source_ids) != len(set(source_ids)):
        raise SourceAssessmentRepairError("repair policy repeats a source event id")
    if policy.expected_counts.failed != len(policy.targets):
        raise SourceAssessmentRepairError(
            "repair policy failed count does not equal its exact target set"
        )
    return policy


def _failed_doctor_checks(doctor: dict[str, Any]) -> set[str]:
    return {
        str(check.get("name"))
        for check in doctor.get("checks", [])
        if isinstance(check, dict) and not check.get("ok")
    }


def _assurance_details(diagnostics: dict[str, Any]) -> dict[str, Any]:
    return (
        (diagnostics.get("capabilities") or {}).get("policy:source-assurance") or {}
    ).get("details") or {}


def _fallback_missing_summary(
    missing: list[dict[str, Any]],
) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in missing:
        event_id = str(item.get("event_id") or "")
        key = (
            str(item.get("event_type") or ""),
            str(item.get("mode") or ""),
            str(item.get("reason") or ""),
        )
        group = groups.setdefault(
            key,
            {
                "event_type": key[0],
                "mode": key[1],
                "reason": key[2],
                "count": 0,
                "first_event_id": event_id,
                "last_event_id": event_id,
            },
        )
        group["count"] += 1
        group["first_event_id"] = min(group["first_event_id"], event_id)
        group["last_event_id"] = max(group["last_event_id"], event_id)
    return {
        "contract": "source-assurance-missing-summary-v1",
        "total_count": len(missing),
        "detail_limit": _MISSING_DETAIL_LIMIT,
        "groups": [groups[key] for key in sorted(groups)],
        "content_exposed": False,
    }


def _validate_missing_backlog(
    policy: SourceAssessmentRepairPolicy,
    *,
    details: dict[str, Any],
    missing_count: int,
) -> dict[str, Any]:
    missing = [item for item in details.get("missing") or [] if isinstance(item, dict)]
    summary = details.get("missing_summary")
    if not isinstance(summary, dict):
        if len(missing) != missing_count:
            raise SourceAssessmentRepairError(
                "complete missing-source summary is unavailable"
            )
        summary = _fallback_missing_summary(missing)
    groups = summary.get("groups")
    if not isinstance(groups, list):
        raise SourceAssessmentRepairError("missing-source summary is invalid")
    try:
        summary_total = int(summary.get("total_count", -1))
        detail_limit = int(summary.get("detail_limit", -1))
        group_counts = [
            int(group.get("count", -1)) for group in groups if isinstance(group, dict)
        ]
        group_total = sum(group_counts)
    except (TypeError, ValueError) as exc:
        raise SourceAssessmentRepairError("missing-source summary is invalid") from exc
    if (
        summary.get("contract") != "source-assurance-missing-summary-v1"
        or frozenset(summary) != _MISSING_SUMMARY_KEYS
        or summary.get("content_exposed") is not False
        or summary_total != missing_count
        or detail_limit != _MISSING_DETAIL_LIMIT
        or len(groups) != sum(isinstance(group, dict) for group in groups)
        or any(frozenset(group) != _MISSING_GROUP_KEYS for group in groups)
        or any(count < 1 for count in group_counts)
        or group_total != missing_count
        or len(missing) != min(missing_count, detail_limit)
    ):
        raise SourceAssessmentRepairError("missing-source summary is invalid")
    if missing_count and policy.paused_consumer is None:
        raise SourceAssessmentRepairError(
            "missing sources require an exact paused-consumer declaration"
        )
    if policy.paused_consumer is None:
        if groups:
            raise SourceAssessmentRepairError("missing-source summary is invalid")
        return summary

    expected = policy.paused_consumer
    rules_by_type: dict[str, PausedBacklogRule] = {
        rule.event_type: rule for rule in expected.missing_backlog
    }
    if len(groups) > len(rules_by_type):
        raise SourceAssessmentRepairError("missing-source summary is invalid")
    detail_event_ids = [str(item.get("event_id") or "") for item in missing]
    if detail_event_ids != sorted(set(detail_event_ids)):
        raise SourceAssessmentRepairError("missing-source details are invalid")
    groups_by_type: dict[str, dict[str, Any]] = {}
    for group in groups:
        event_type = str(group.get("event_type") or "")
        rule = rules_by_type.get(event_type)
        if event_type in groups_by_type:
            raise SourceAssessmentRepairError("missing-source summary is invalid")
        if rule is None:
            raise SourceAssessmentRepairError(
                "missing sources differ from the declared paused-consumer backlog"
            )
        if int(group.get("count") or 0) > rule.maximum_count:
            raise SourceAssessmentRepairError(
                "declared paused-consumer backlog ceiling exceeded"
            )
        groups_by_type[event_type] = group
    for item in [*missing, *groups]:
        first_event_id = str(item.get("first_event_id") or item.get("event_id") or "")
        last_event_id = str(item.get("last_event_id") or first_event_id)
        event_type = str(item.get("event_type") or "")
        rule = rules_by_type.get(event_type)
        if (
            rule is None
            or item.get("mode") != "candidate_assessment"
            or item.get("reason") != rule.reason
            or first_event_id <= expected.poison_event_id
            or last_event_id < first_event_id
        ):
            raise SourceAssessmentRepairError(
                "missing sources differ from the declared paused-consumer backlog"
            )
    detail_ids_by_type: dict[str, list[str]] = {}
    for item in missing:
        detail_ids_by_type.setdefault(str(item["event_type"]), []).append(
            str(item["event_id"])
        )
    for event_type, event_ids in detail_ids_by_type.items():
        group = groups_by_type.get(event_type)
        if group is None or (
            str(group["first_event_id"]) != event_ids[0]
            or str(group["last_event_id"]) < event_ids[-1]
            or int(group["count"]) < len(event_ids)
        ):
            raise SourceAssessmentRepairError(
                "missing-source summary differs from displayed details"
            )
    return summary


def _validate_paused_consumer(
    policy: SourceAssessmentRepairPolicy,
    diagnostics: dict[str, Any],
) -> bool:
    expected = policy.paused_consumer
    if expected is None:
        return False
    capability = (diagnostics.get("capabilities") or {}).get(expected.subscriber_id)
    if not isinstance(capability, dict) or capability.get("state") != "degraded":
        raise SourceAssessmentRepairError(
            "declared paused consumer is not the only expected degraded worker"
        )
    details = capability.get("details") or {}
    actual = {
        "consumer_version": str(details.get("version") or ""),
        "checkpoint_event_id": str(details.get("checkpoint_event_id") or ""),
        "poison_event_id": str(details.get("poison_event_id") or ""),
        "consecutive_failures": int(details.get("consecutive_failures") or 0),
    }
    expected_values = {
        "consumer_version": expected.consumer_version,
        "checkpoint_event_id": expected.checkpoint_event_id,
        "poison_event_id": expected.poison_event_id,
        "consecutive_failures": expected.consecutive_failures,
    }
    if actual != expected_values:
        raise SourceAssessmentRepairError(
            "paused consumer state differs from the declared release baseline"
        )
    return True


def validate_declared_baseline(
    policy: SourceAssessmentRepairPolicy,
    *,
    active_commit: str,
    doctor: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """Fail unless the active machine differs from healthy in exactly one way."""

    if active_commit != policy.required_active_commit:
        raise SourceAssessmentRepairError(
            "active release does not match the declared repair predecessor"
        )
    if diagnostics.get("observation_active") is not True:
        raise SourceAssessmentRepairError("repair requires active observation mode")
    if diagnostics.get("ready") is not True:
        raise SourceAssessmentRepairError("repair requires a ready live runtime")
    paused_consumer_expected = _validate_paused_consumer(policy, diagnostics)
    failed_doctor_checks = _failed_doctor_checks(doctor)
    permitted_doctor_failures = {
        frozenset({"source_assurance_terminal_dispositions"}),
        frozenset(
            {
                "launchd_and_process_state",
                "source_assurance_terminal_dispositions",
            }
        ),
    }
    if paused_consumer_expected:
        permitted_doctor_failures = {
            frozenset(
                {
                    "required_reactive_pipeline_delivery",
                    "source_assurance_terminal_dispositions",
                }
            ),
            frozenset(
                {
                    "required_reactive_pipeline_delivery",
                    "launchd_and_process_state",
                    "source_assurance_terminal_dispositions",
                }
            ),
        }
    if frozenset(failed_doctor_checks) not in permitted_doctor_failures:
        raise SourceAssessmentRepairError(
            "active Doctor differs from the declared source-only degradation"
        )
    capabilities = diagnostics.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        raise SourceAssessmentRepairError("runtime capabilities are unavailable")
    paused_subscriber_id = (
        policy.paused_consumer.subscriber_id
        if policy.paused_consumer is not None
        else None
    )
    unexpected = sorted(
        capability_id
        for capability_id, capability in capabilities.items()
        if capability_id not in _SOURCE_DERIVED_CAPABILITIES
        and not (paused_consumer_expected and capability_id == paused_subscriber_id)
        and isinstance(capability, dict)
        and capability.get("state") in {"degraded", "blocked"}
    )
    if unexpected:
        raise SourceAssessmentRepairError(
            "unexpected capability degradation is present: " + ", ".join(unexpected)
        )
    source_capability = capabilities.get("policy:source-assurance") or {}
    details = _assurance_details(diagnostics)
    if (
        source_capability.get("state") != "degraded"
        or details.get("state") != "degraded"
    ):
        raise SourceAssessmentRepairError(
            "source-assurance state does not match the declared degradation"
        )
    counts = details.get("counts") or {}
    try:
        actual_counts = {
            name: int(counts.get(name, -1))
            for name in ("obligations", "accounted", "pending", "missing", "failed")
        }
    except (TypeError, ValueError) as exc:
        raise SourceAssessmentRepairError(
            "source-assurance counts are unavailable"
        ) from exc
    expected = policy.expected_counts
    if (
        actual_counts["obligations"] < expected.minimum_obligations
        or actual_counts["accounted"]
        + actual_counts["pending"]
        + actual_counts["missing"]
        != actual_counts["obligations"]
        or actual_counts["pending"] != expected.pending
        or actual_counts["missing"] > expected.maximum_missing
        or actual_counts["failed"] != expected.failed
    ):
        raise SourceAssessmentRepairError(
            "source-assurance counts differ from the declared repair baseline"
        )
    if details.get("content_exposed") is not False:
        raise SourceAssessmentRepairError("source diagnostics exposed content")
    watermark_event_id = details.get("watermark_event_id")
    if not isinstance(watermark_event_id, str) or not watermark_event_id:
        raise SourceAssessmentRepairError(
            "source-assurance evidence has no exact event watermark"
        )
    freshness = details.get("freshness") or {}
    if not (
        freshness.get("state") == "fresh"
        and freshness.get("revision_current") is True
        and freshness.get("pending_recheck_required") is False
        and freshness.get("settled_revision") is True
        and freshness.get("refresh_error_type") is None
    ):
        raise SourceAssessmentRepairError(
            "source-assurance evidence is not a fresh settled revision"
        )
    actual_failures = {
        (str(item.get("event_id") or ""), str(item.get("reason") or ""))
        for item in details.get("failed") or []
        if isinstance(item, dict)
    }
    expected_failures = {
        (target.source_event_id, target.expected_failure_reason)
        for target in policy.targets
    }
    if actual_failures != expected_failures:
        raise SourceAssessmentRepairError(
            "failed source ids or reasons differ from the declared repair baseline"
        )
    missing_summary = _validate_missing_backlog(
        policy,
        details=details,
        missing_count=actual_counts["missing"],
    )
    return {
        "minimum_obligations": expected.minimum_obligations,
        "maximum_missing": expected.maximum_missing,
        "counts": actual_counts,
        "failed_target_count": len(actual_failures),
        "paused_consumer": (
            policy.paused_consumer.model_dump(mode="json")
            if policy.paused_consumer is not None
            else None
        ),
        "missing_summary": missing_summary,
        "watermark_event_id": watermark_event_id,
        "content_exposed": False,
    }


def _capability_snapshot(diagnostics: dict[str, Any]) -> dict[str, Any]:
    capabilities = diagnostics.get("capabilities") or {}
    return {
        str(key): value
        for key, value in capabilities.items()
        if str(key).startswith(("pipeline:", "projection:"))
    }


async def _hydrate_online_missing_summary(
    paths: DeploymentPaths,
    *,
    repository_root: Path,
    diagnostics: dict[str, Any],
    test_mode: bool,
) -> dict[str, Any]:
    details = _assurance_details(diagnostics)
    counts = details.get("counts") or {}
    summary = details.get("missing_summary")
    try:
        missing_count = int(counts.get("missing", -1))
        summary_count = (
            int(summary.get("total_count", -1)) if isinstance(summary, dict) else -1
        )
    except (TypeError, ValueError):
        missing_count = -1
        summary_count = -1
    if (
        isinstance(summary, dict)
        and summary.get("contract") == "source-assurance-missing-summary-v1"
        and summary_count == missing_count
    ):
        return diagnostics

    watermark_event_id = details.get("watermark_event_id")
    if not isinstance(watermark_event_id, str) or not watermark_event_id:
        raise SourceAssessmentRepairError(
            "source-assurance evidence has no exact event watermark"
        )
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    event_key = secret_store(paths, test_mode=test_mode).get(references["event_log"])
    event_log = EventLog(
        paths.instance_dir / "events" / "events.db",
        event_key,
        read_only=True,
    )
    try:
        audit = await audit_source_assurance(
            event_log,
            repository_root=repository_root,
            capabilities=_capability_snapshot(diagnostics),
            through_event_id=watermark_event_id,
        )
    finally:
        await event_log.close()
    live_counts = {
        name: int(counts.get(name, -1))
        for name in ("obligations", "accounted", "pending", "missing", "failed")
    }
    audited_counts = {
        name: int((audit.get("counts") or {}).get(name, -1))
        for name in ("obligations", "accounted", "pending", "missing", "failed")
    }
    if (
        audit.get("watermark_event_id") != watermark_event_id
        or audited_counts != live_counts
        or audit.get("content_exposed") is not False
    ):
        raise SourceAssessmentRepairError(
            "candidate missing-source audit differs from live source assurance"
        )
    hydrated = copy.deepcopy(diagnostics)
    hydrated_details = _assurance_details(hydrated)
    hydrated_details["missing_summary"] = audit["missing_summary"]
    return hydrated


def hydrate_online_repair_diagnostics(
    paths: DeploymentPaths,
    *,
    repository_root: Path,
    diagnostics: dict[str, Any],
    test_mode: bool,
) -> dict[str, Any]:
    """Add an exact all-row backlog summary at the live diagnostic watermark."""

    return asyncio.run(
        _hydrate_online_missing_summary(
            paths,
            repository_root=repository_root,
            diagnostics=diagnostics,
            test_mode=test_mode,
        )
    )


def _stopped_consumer_health(
    paths: DeploymentPaths,
    diagnostics: dict[str, Any],
    policy: SourceAssessmentRepairPolicy,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prove stopped projection/pipeline workers persisted no hidden failure."""

    checkpoint_db = paths.instance_dir / "events" / "bus_checkpoints.db"
    if not checkpoint_db.is_file():
        raise SourceAssessmentRepairError(
            "stopped consumer health database is unavailable"
        )
    try:
        connection = sqlite3.connect(f"file:{checkpoint_db}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = list(
                connection.execute(
                    "SELECT subscriber_id, checkpoint_event_id, "
                    "consecutive_failures, degraded, poison_event_id "
                    ", consumer_version "
                    "FROM bus_consumer_offsets "
                    "WHERE subscriber_id LIKE 'pipeline:%' "
                    "OR subscriber_id LIKE 'projection:%'"
                )
            )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise SourceAssessmentRepairError(
            "stopped consumer health database is unreadable"
        ) from exc
    expected_ids = set(_capability_snapshot(diagnostics))
    actual_ids = {str(row["subscriber_id"]) for row in rows}
    if not expected_ids or actual_ids != expected_ids:
        raise SourceAssessmentRepairError(
            "stopped consumer health inventory differs from final live readiness"
        )
    pipeline_count = sum(value.startswith("pipeline:") for value in actual_ids)
    projection_count = sum(value.startswith("projection:") for value in actual_ids)
    if pipeline_count == 0 or projection_count == 0:
        raise SourceAssessmentRepairError(
            "stopped consumer health inventory is incomplete"
        )
    unhealthy = [
        row
        for row in rows
        if bool(row["degraded"])
        or int(row["consecutive_failures"] or 0) > 0
        or row["poison_event_id"] is not None
    ]
    expected_paused = policy.paused_consumer
    if expected_paused is None and unhealthy:
        raise SourceAssessmentRepairError(
            "stopped consumer health contains a degraded or poisoned worker"
        )
    if expected_paused is not None:
        if len(unhealthy) != 1:
            raise SourceAssessmentRepairError(
                "stopped consumer health differs from the declared paused worker"
            )
        row = unhealthy[0]
        actual = {
            "subscriber_id": str(row["subscriber_id"]),
            "consumer_version": str(row["consumer_version"] or ""),
            "checkpoint_event_id": str(row["checkpoint_event_id"] or ""),
            "poison_event_id": str(row["poison_event_id"] or ""),
            "consecutive_failures": int(row["consecutive_failures"] or 0),
        }
        declared = {
            "subscriber_id": expected_paused.subscriber_id,
            "consumer_version": expected_paused.consumer_version,
            "checkpoint_event_id": expected_paused.checkpoint_event_id,
            "poison_event_id": expected_paused.poison_event_id,
            "consecutive_failures": expected_paused.consecutive_failures,
        }
        if actual != declared or not bool(row["degraded"]):
            raise SourceAssessmentRepairError(
                "stopped consumer poison differs from the declared release baseline"
            )
    evidence = {
        "pipeline_count": pipeline_count,
        "projection_count": projection_count,
        "unhealthy_count": len(unhealthy),
        "declared_paused_subscriber": (
            expected_paused.subscriber_id if expected_paused is not None else None
        ),
        "content_exposed": False,
    }
    capabilities = {
        str(row["subscriber_id"]): {
            "state": (
                "degraded"
                if expected_paused is not None
                and row["subscriber_id"] == expected_paused.subscriber_id
                else "healthy"
            ),
            "details": {
                "checkpoint_event_id": row["checkpoint_event_id"],
                "version": row["consumer_version"],
                "poison_event_id": row["poison_event_id"],
                "consecutive_failures": int(row["consecutive_failures"] or 0),
            },
        }
        for row in rows
    }
    return evidence, capabilities


async def _offline_validate_and_append(
    paths: DeploymentPaths,
    *,
    repository_root: Path,
    candidate_commit: str,
    policy: SourceAssessmentRepairPolicy,
    diagnostics: dict[str, Any],
    gateway_live_at_quiescence: bool,
    append_requests: bool,
    test_mode: bool,
) -> dict[str, Any]:
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    event_key = secret_store(paths, test_mode=test_mode).get(references["event_log"])
    event_log = EventLog(paths.instance_dir / "events" / "events.db", event_key)
    try:
        if gateway_live_at_quiescence is not True:
            raise SourceAssessmentRepairError(
                "OpenClaw Gateway was not live at the repair quiescence boundary"
            )
        live_watermark = _assurance_details(diagnostics).get("watermark_event_id")
        if not isinstance(live_watermark, str) or not live_watermark:
            raise SourceAssessmentRepairError(
                "final live health receipt has no exact event watermark"
            )
        consumer_health, stopped_capabilities = _stopped_consumer_health(
            paths, diagnostics, policy
        )
        audit = await audit_source_assurance(
            event_log,
            repository_root=repository_root,
            capabilities=stopped_capabilities,
        )
        if audit.get("watermark_event_id") != live_watermark:
            raise SourceAssessmentRepairError(
                "event log advanced after final live health validation"
            )
        synthetic_diagnostics = {
            "observation_active": True,
            "ready": True,
            "capabilities": {
                **(diagnostics.get("capabilities") or {}),
                "policy:source-assurance": {
                    "state": audit["state"],
                    "details": {
                        **audit,
                        # A stopped-log audit closes over the exact latest
                        # event watermark and is therefore the authoritative
                        # fresh revision for activation.
                        "freshness": {
                            "state": "fresh",
                            "revision_current": True,
                            "pending_recheck_required": False,
                            "settled_revision": True,
                            "refresh_error_type": None,
                        },
                    },
                },
            },
        }
        stopped_snapshot = validate_declared_baseline(
            policy,
            active_commit=policy.required_active_commit,
            doctor={
                "checks": [
                    *(
                        [
                            {
                                "name": "required_reactive_pipeline_delivery",
                                "ok": False,
                            }
                        ]
                        if policy.paused_consumer is not None
                        else []
                    ),
                    {
                        "name": "source_assurance_terminal_dispositions",
                        "ok": False,
                    },
                ]
            },
            diagnostics=synthetic_diagnostics,
        )
        targets: list[SourceAssessmentRecoveryTarget] = []
        for declared in policy.targets:
            assessment = await latest_source_assessment(
                event_log, declared.source_event_id
            )
            if assessment is None:
                raise SourceAssessmentRepairError(
                    "declared source has no terminal assessment"
                )
            targets.append(
                SourceAssessmentRecoveryTarget(
                    source_event_id=declared.source_event_id,
                    failed_assessment_event_id=str(assessment["event_id"]),
                    expected_failure_reason=declared.expected_failure_reason,
                )
            )
        if not append_requests:
            return {
                "contract": policy.contract,
                "validated_targets": len(targets),
                "stopped_log_snapshot": {
                    **stopped_snapshot,
                    "consumer_health": consumer_health,
                    "gateway_live_at_quiescence": True,
                },
                "content_exposed": False,
            }
        target_digest = hashlib.sha256(
            json.dumps(
                [target.model_dump(mode="json") for target in targets],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        checkpoint_token = _noise_filtering_checkpoint_token(paths)
        refusal_token = await _matching_recovery_refusal_token(event_log, targets)
        batch = SourceAssessmentRecoveryBatch(
            request_id=(
                "guarded-release:"
                f"{candidate_commit}:{target_digest}:{checkpoint_token}:{refusal_token}"
            ),
            targets=targets,
        )
        # A raw path does not carry a tenant hint; bind requests to the exact
        # tenant already present on their retained source events.
        tenant_ids = {
            str((await event_log.get(target.source_event_id) or {}).get("tenant_id"))
            for target in targets
        }
        if len(tenant_ids) != 1 or "None" in tenant_ids:
            raise SourceAssessmentRepairError(
                "declared recovery targets do not share one tenant"
            )
        tenant_id = next(iter(tenant_ids))
        envelopes = await build_recovery_request_envelopes(
            event_log,
            batch,
            tenant_id=tenant_id,
            requested_by_actor=f"guarded-updater:{candidate_commit}",
            authority="guarded_release_exception",
        )
        event_ids = await event_log.append_batch(
            envelopes,
            correlation_id=f"guarded-release:{candidate_commit}",
        )
        return {
            "contract": policy.contract,
            "request_event_ids": event_ids,
            "requested": len(event_ids),
            "stopped_log_snapshot": {
                **stopped_snapshot,
                "consumer_health": consumer_health,
                "gateway_live_at_quiescence": True,
            },
            "content_exposed": False,
            "expires_after_activation": True,
        }
    finally:
        await event_log.close()


def offline_validate_repair_baseline(
    paths: DeploymentPaths,
    *,
    repository_root: Path,
    candidate_commit: str,
    policy: SourceAssessmentRepairPolicy,
    diagnostics: dict[str, Any],
    gateway_live_at_quiescence: bool,
    append_requests: bool,
    test_mode: bool,
) -> dict[str, Any]:
    return asyncio.run(
        _offline_validate_and_append(
            paths,
            repository_root=repository_root,
            candidate_commit=candidate_commit,
            policy=policy,
            diagnostics=diagnostics,
            gateway_live_at_quiescence=gateway_live_at_quiescence,
            append_requests=append_requests,
            test_mode=test_mode,
        )
    )


__all__ = [
    "POLICY_RELATIVE_PATH",
    "PausedBacklogRule",
    "PausedConsumerBaseline",
    "SourceAssessmentRepairError",
    "SourceAssessmentRepairPolicy",
    "hydrate_online_repair_diagnostics",
    "load_repair_policy",
    "offline_validate_repair_baseline",
    "validate_declared_baseline",
]
