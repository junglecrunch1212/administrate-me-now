"""End-to-end accounting for every declared adapter or member source fact.

The event log proves ingestion.  Source assurance proves the rest of the
contract: a candidate-bearing source was explicitly assessed, knowledge
metadata explicitly declared candidate review inapplicable, a state mirror
reached its named projection, or an observation/action event is itself a
terminal fact. Missing and still-pending are never conflated with "empty".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from adminme.events.log import EventLog
from adminme.communication_intake_policy import (
    RETIRED_COMMAND_TYPE,
    SKIPPED_EVENT_TYPE,
    command_retirement,
    source_skip_valid,
)
from adminme.lib.pack_registry.models import SourceAssuranceRule
from adminme.source_assessment_recovery import (
    assessment_outcome,
    assessment_reason,
    legacy_default_assessment_candidate,
    legacy_default_failure_candidate,
    legacy_required_language_failure_candidate,
    resolved_assessment_disposition,
)


@dataclass(frozen=True)
class SourceObligation:
    event_type: str
    mode: str
    terminal_events: tuple[str, ...] = ()
    projections: tuple[str, ...] = ()
    assessor: str | None = None

    @property
    def key(self) -> tuple[Any, ...]:
        return (
            self.event_type,
            self.mode,
            self.terminal_events,
            self.projections,
            self.assessor,
        )


def _manifest_paths(repository_root: Path) -> Iterable[Path]:
    yield from sorted((repository_root / "packs" / "adapters").rglob("pack.yaml"))
    bridge_root = repository_root / "bridge" / "adminme_bridge" / "connector_packs"
    if bridge_root.is_dir():
        yield from sorted(bridge_root.rglob("pack.yaml"))


def load_source_obligations(repository_root: Path) -> tuple[SourceObligation, ...]:
    """Load and de-duplicate the reviewed source contracts in pack manifests."""

    obligations: dict[tuple[Any, ...], SourceObligation] = {}
    for path in _manifest_paths(repository_root):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(f"cannot load source-assurance manifest {path}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"source-assurance manifest is not a mapping: {path}")
        for value in raw.get("source_assurance") or []:
            rule = SourceAssuranceRule.model_validate(value)
            for event_type in rule.source_events:
                obligation = SourceObligation(
                    event_type=event_type,
                    mode=rule.mode,
                    terminal_events=tuple(rule.terminal_events),
                    projections=tuple(rule.projections),
                    assessor=rule.assessor,
                )
                obligations[obligation.key] = obligation

    # Platform-owned sources do not belong to one provider adapter manifest.
    platform = (
        SourceObligation(
            event_type="telephony.sms_received",
            mode="candidate_assessment",
            terminal_events=("triage.intent.features_extracted",),
            assessor="pipeline:noise_filtering",
        ),
        SourceObligation(
            event_type="triage.intent.submitted",
            mode="candidate_assessment",
            terminal_events=(
                "triage.intent.features_extracted",
                "triage.intent.interpretation_refused",
            ),
            assessor="durable:member-submissions:interpretation",
        ),
        SourceObligation(
            event_type="member_knowledge.note_corrected",
            mode="canonical_projection",
            projections=("member_knowledge",),
        ),
        SourceObligation(
            event_type="member_knowledge.note_corrected",
            mode="candidate_assessment",
            terminal_events=("triage.intent.features_extracted",),
            assessor="pipeline:member_knowledge_indexing",
        ),
        SourceObligation(
            event_type="external_sync.write_requested",
            mode="action_request_terminal",
            terminal_events=(
                "external_sync.write_completed",
                "external_sync.write_failed",
            ),
        ),
    )
    for obligation in platform:
        obligations[obligation.key] = obligation
    return tuple(sorted(obligations.values(), key=lambda item: item.key))


def _capability_value(capabilities: dict[str, Any], key: str) -> dict[str, Any]:
    record = capabilities.get(key)
    if record is None:
        return {}
    if isinstance(record, dict):
        return record
    public = getattr(record, "public", None)
    if not callable(public):
        return {}
    value = public()
    return value if isinstance(value, dict) else {}


def _checkpoint_has_passed(capability: dict[str, Any], event_id: str) -> bool:
    details = capability.get("details") or {}
    checkpoint = details.get("checkpoint_event_id")
    return isinstance(checkpoint, str) and checkpoint >= event_id


def _is_overdue(event: dict[str, Any], *, grace_seconds: int = 300) -> bool:
    """Return true when a durable fact has waited beyond normal in-flight lag."""

    recorded = str(event.get("recorded_at") or event.get("occurred_at") or "")
    try:
        parsed = datetime.fromisoformat(recorded.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds() > grace_seconds
    except (TypeError, ValueError):
        return True


def _aware_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _event_at_or_after(event: dict[str, Any], boundary: datetime) -> bool:
    occurred = _aware_datetime(event.get("recorded_at") or event.get("occurred_at"))
    return occurred is not None and occurred >= boundary


def legacy_effect_event_fingerprint(event: dict[str, Any]) -> str:
    """Bind a reconciliation to the exact immutable event metadata and payload."""

    value = {
        key: event.get(key)
        for key in (
            "event_id",
            "type",
            "occurred_at",
            "recorded_at",
            "causation_id",
            "payload",
        )
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _verified_legacy_reconciliation(
    request: dict[str, Any],
    terminal: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if len(rows) != 1:
        return None
    payload = rows[0].get("payload") or {}
    request_payload = request.get("payload") or {}
    terminal_payload = terminal.get("payload") or {}
    terminal_suppressed = terminal_payload.get("suppressed")
    if (
        terminal.get("type") != "external_sync.write_completed"
        or not isinstance(terminal_suppressed, bool)
        or any(
            key in request_payload
            for key in (
                "domain_approval_event_id",
                "openclaw_approval_event_id",
                "request_fingerprint",
            )
        )
        or any(
            key in terminal_payload
            for key in (
                "provider_called",
                "command_event_id",
                "domain_approval_event_id",
                "openclaw_approval_event_id",
                "request_fingerprint",
            )
        )
        or payload.get("legacy_approval_state") != "pre_governance_absent"
        or payload.get("reconciliation_basis") != "v1_completed_terminal_evidence"
        or payload.get("request_event_id") != request.get("event_id")
        or payload.get("terminal_event_id") != terminal.get("event_id")
        or payload.get("command_id") != request_payload.get("command_id")
        or payload.get("command_id") != terminal_payload.get("command_id")
        or payload.get("provider_account_id")
        != request_payload.get("provider_account_id")
        or payload.get("provider_called") is not (not terminal_suppressed)
        or payload.get("suppressed") is not terminal_suppressed
        or payload.get("request_fingerprint")
        != legacy_effect_event_fingerprint(request)
        or payload.get("terminal_fingerprint")
        != legacy_effect_event_fingerprint(terminal)
    ):
        return None
    return payload


def _provider_effect_counts(
    source_events: list[dict[str, Any]],
    terminal_events: dict[str, list[dict[str, Any]]],
    reconciliations: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, int]:
    reconciliations = reconciliations or {}
    requests: dict[str, list[dict[str, Any]]] = {}
    malformed_request_count = 0
    for event in source_events:
        if event.get("type") != "external_sync.write_requested":
            continue
        command_id = str((event.get("payload") or {}).get("command_id") or "")
        if not command_id:
            malformed_request_count += 1
            continue
        requests.setdefault(command_id, []).append(event)
    request_ids = set(requests)
    counts = {
        "write_requests": sum(len(rows) for rows in requests.values())
        + malformed_request_count,
        "terminal_results": sum(len(rows) for rows in terminal_events.values()),
        "provider_called_true": 0,
        "provider_called_false": 0,
        "provider_called_unknown": 0,
        "suppressed_true": 0,
        "suppressed_false_or_unknown": 0,
        "missing_terminal": 0,
        "duplicate_terminal": 0,
        "approval_correlation_missing": 0,
        "duplicate_request": sum(max(0, len(rows) - 1) for rows in requests.values()),
        "malformed_request": malformed_request_count,
        "orphan_terminal": 0,
        "legacy_reconciled_terminal": 0,
        "invalid_legacy_reconciliation": 0,
        "duplicate_legacy_reconciliation": sum(
            max(0, len(rows) - 1) for rows in reconciliations.values()
        ),
    }
    for command_id in request_ids:
        request = requests[command_id][0]
        request_payload = request.get("payload") or {}
        rows = terminal_events.get(command_id, [])
        if not rows:
            counts["missing_terminal"] += 1
            continue
        if len(rows) > 1:
            counts["duplicate_terminal"] += len(rows) - 1
        for event in rows:
            payload = event.get("payload") or {}
            reconciliation = _verified_legacy_reconciliation(
                request,
                event,
                reconciliations.get(command_id, []),
            )
            provider_called = (
                reconciliation.get("provider_called")
                if reconciliation is not None
                else payload.get("provider_called")
            )
            if provider_called is True:
                counts["provider_called_true"] += 1
            elif provider_called is False:
                counts["provider_called_false"] += 1
            else:
                counts["provider_called_unknown"] += 1
            suppressed = (
                reconciliation.get("suppressed")
                if reconciliation is not None
                else payload.get("suppressed")
            )
            if suppressed is True:
                counts["suppressed_true"] += 1
            elif provider_called is not False:
                counts["suppressed_false_or_unknown"] += 1
            if reconciliation is not None:
                counts["legacy_reconciled_terminal"] += 1
                continue
            correlation_pairs = (
                (payload.get("command_event_id"), request.get("event_id")),
                (
                    payload.get("domain_approval_event_id"),
                    request_payload.get("domain_approval_event_id"),
                ),
                (
                    payload.get("openclaw_approval_event_id"),
                    request_payload.get("openclaw_approval_event_id"),
                ),
                (
                    payload.get("request_fingerprint"),
                    request_payload.get("request_fingerprint"),
                ),
                (
                    payload.get("provider_account_id"),
                    request_payload.get("provider_account_id"),
                ),
                (event.get("causation_id"), request.get("event_id")),
            )
            if any(
                not isinstance(actual, str) or not actual or actual != expected
                for actual, expected in correlation_pairs
            ):
                counts["approval_correlation_missing"] += 1
    counts["orphan_terminal"] = sum(
        len(rows)
        for command_id, rows in terminal_events.items()
        if command_id not in request_ids
    )
    for command_id, rows in reconciliations.items():
        if command_id not in request_ids:
            counts["invalid_legacy_reconciliation"] += len(rows)
            continue
        terminals = terminal_events.get(command_id, [])
        if len(rows) == 1 and len(terminals) == 1:
            if (
                _verified_legacy_reconciliation(
                    requests[command_id][0], terminals[0], rows
                )
                is None
            ):
                counts["invalid_legacy_reconciliation"] += 1
    return counts


def _provider_effect_integrity_healthy(counts: dict[str, int]) -> bool:
    return not any(
        counts[key]
        for key in (
            "provider_called_unknown",
            "missing_terminal",
            "duplicate_terminal",
            "approval_correlation_missing",
            "duplicate_request",
            "malformed_request",
            "orphan_terminal",
            "invalid_legacy_reconciliation",
            "duplicate_legacy_reconciliation",
        )
    )


def _zero_provider_effects(counts: dict[str, int]) -> bool:
    return (
        _provider_effect_integrity_healthy(counts)
        and counts["provider_called_true"] == 0
        and counts["suppressed_false_or_unknown"] == 0
    )


async def audit_source_assurance(
    event_log: EventLog,
    *,
    repository_root: Path,
    capabilities: dict[str, Any],
    through_event_id: str | None = None,
    provider_effect_window_started_at: str | None = None,
) -> dict[str, Any]:
    """Return secret-free source accounting suitable for Doctor and Settings."""

    obligations = load_source_obligations(repository_root)
    by_type: dict[str, list[SourceObligation]] = {}
    for obligation in obligations:
        by_type.setdefault(obligation.event_type, []).append(obligation)
    interesting = set(by_type) | {
        event_type
        for obligation in obligations
        for event_type in obligation.terminal_events
    }
    interesting.update(
        {
            "source.assessment.recovery_requested",
            "source.assessment.recovery_refused",
            "triage.currentness.features_extracted",
            "external_sync.legacy_effect_reconciled",
            "skill.call.failed",
            SKIPPED_EVENT_TYPE,
            RETIRED_COMMAND_TYPE,
        }
    )
    payload_types = {
        event_type
        for obligation in obligations
        for event_type in obligation.terminal_events
    } | {
        obligation.event_type
        for obligation in obligations
        if obligation.mode == "action_request_terminal"
    }
    payload_types.update(
        {
            "source.assessment.recovery_requested",
            "source.assessment.recovery_refused",
            "triage.currentness.features_extracted",
            "external_sync.legacy_effect_reconciled",
            "messaging.source_observed",
            "skill.call.failed",
            SKIPPED_EVENT_TYPE,
            RETIRED_COMMAND_TYPE,
        }
    )
    watermark_event_id = through_event_id
    if watermark_event_id is None:
        watermark_event_id = await event_log.latest_event_id()
    source_events: list[dict[str, Any]] = []

    assessments: dict[str, dict[str, Any]] = {}
    assessment_sources: dict[str, dict[str, Any]] = {}
    assessment_dispositions: dict[str, tuple[str, str]] = {}
    legacy_default_sources: set[str] = set()
    action_terminals: set[str] = set()
    action_terminal_events: dict[str, list[dict[str, Any]]] = {}
    effect_reconciliations: dict[str, list[dict[str, Any]]] = {}
    recovery_requests: dict[str, dict[str, Any]] = {}
    recovery_assessments: dict[str, dict[str, Any]] = {}
    recovery_refusals: set[str] = set()
    skipped_sources: dict[str, dict[str, Any]] = {}
    refused_submissions: set[str] = set()
    retired_commands: set[str] = set()
    messaging_cycles: list[dict[str, Any]] = []
    async for event in event_log.read_metadata_since(
        # An empty log has a null public watermark; the impossible empty
        # string still provides a closed upper bound if an append races the
        # first audit.
        through=watermark_event_id or "",
        types=sorted(interesting),
        include_payload_for=sorted(payload_types),
    ):
        payload = event.get("payload") or {}
        if event.get("type") == "triage.intent.interpretation_refused":
            source_id = str(payload.get("source_event_id") or "")
            source = await event_log.get(source_id) if source_id else None
            receipt = await event_log.get(str(event["event_id"]))
            if (
                source is not None
                and receipt is not None
                and source.get("type") == "triage.intent.submitted"
                and receipt.get("source_adapter") == "submission-interpreter"
                and receipt.get("actor_identity") == "system:submission-interpreter"
                and receipt.get("source_account_id") == "system"
                and receipt.get("causation_id") == source_id
                and receipt.get("idempotency_key")
                == f"triage-intent-refused:{source_id}"
                and all(
                    receipt.get(key) == source.get(key)
                    for key in (
                        "tenant_id",
                        "owner_scope",
                        "visibility_scope",
                        "sensitivity",
                    )
                )
                and payload.get("reason")
                in {"member_principal_not_active", "submission_identity_invalid"}
            ):
                refused_submissions.add(source_id)
        if event.get("type") == SKIPPED_EVENT_TYPE:
            source_id = str(payload.get("source_event_id") or "")
            source = await event_log.get(source_id) if source_id else None
            receipt = await event_log.get(str(event["event_id"]))
            if (
                source is not None
                and receipt is not None
                and source_skip_valid(source, receipt)
            ):
                skipped_sources[source_id] = receipt
        elif event.get("type") == RETIRED_COMMAND_TYPE:
            command_id = str(payload.get("command_event_id") or "")
            command = await event_log.get(command_id) if command_id else None
            if (
                command is not None
                and await command_retirement(event_log, command) is not None
            ):
                retired_commands.add(command_id)
        if event.get("type") in {
            "triage.intent.features_extracted",
            "triage.currentness.features_extracted",
        }:
            assessments[str(payload.get("source_event_id") or "")] = event
            recovery_request_event_id = str(
                payload.get("recovery_request_event_id") or ""
            )
            if recovery_request_event_id:
                recovery_assessments[recovery_request_event_id] = event
        elif legacy_default_failure_candidate(event):
            legacy_default_sources.add(str(event.get("causation_id") or ""))
        elif event.get("type") == "source.assessment.recovery_requested":
            recovery_requests[str(event.get("event_id") or "")] = event
        elif event.get("type") == "source.assessment.recovery_refused":
            recovery_refusals.add(str(payload.get("request_event_id") or ""))
        elif event.get("type") in {
            "external_sync.write_completed",
            "external_sync.write_failed",
        }:
            command_id = str(payload.get("command_id") or "")
            action_terminals.add(command_id)
            action_terminal_events.setdefault(command_id, []).append(event)
        elif event.get("type") == "external_sync.legacy_effect_reconciled":
            command_id = str(payload.get("command_id") or "")
            effect_reconciliations.setdefault(command_id, []).append(event)
        elif event.get("type") == "messaging.source_observed":
            messaging_cycles.append(event)
        if event.get("type") in by_type:
            source_events.append(event)

    counts = {
        "source_facts": 0,
        "obligations": 0,
        "accounted": 0,
        "pending": 0,
        "missing": 0,
        "eligible_candidates": 0,
        "no_candidate": 0,
        "not_selected": 0,
        "quarantined": 0,
        "failed": 0,
    }
    source_ids: set[str] = set()
    missing: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    missing_groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_event_type: dict[str, dict[str, int]] = {}

    for event in source_events:
        event_type = str(event.get("type") or "")
        source_obligations = by_type.get(event_type) or []
        if not source_obligations:
            continue
        event_id = str(event["event_id"])
        source_ids.add(event_id)
        row = by_event_type.setdefault(
            event_type, {"source_facts": 0, "accounted": 0, "pending": 0, "missing": 0}
        )
        row["source_facts"] += 1
        for obligation in source_obligations:
            counts["obligations"] += 1
            status = "accounted"
            detail = obligation.mode
            if (
                obligation.mode == "candidate_assessment"
                and event_id in refused_submissions
            ):
                counts["quarantined"] += 1
                detail = "submission_context_unavailable"
            elif (
                obligation.mode == "candidate_assessment"
                and event_id in skipped_sources
            ):
                counts["not_selected"] += 1
                detail = "explicit_member_submission_required"
            elif obligation.mode == "candidate_assessment":
                assessment = assessments.get(event_id)
                if assessment is not None:
                    assessment_id = str(assessment["event_id"])
                    check_default = (
                        event_id in legacy_default_sources
                        and legacy_default_assessment_candidate(assessment)
                    )
                    if (
                        legacy_required_language_failure_candidate(assessment)
                        or check_default
                    ) and assessment_id not in assessment_sources:
                        # Only the latest assessment matters. Metadata omits
                        # privacy/provenance headers, so this closed legacy
                        # contradiction needs indexed envelope/source proof.
                        # Successful replacements restore metadata-only reads.
                        envelope = await event_log.get(assessment_id)
                        source = await event_log.get(event_id)
                        if envelope is None or source is None:
                            raise RuntimeError(
                                "legacy assessment identity proof unavailable"
                            )
                        assessment = envelope
                        assessments[event_id] = envelope
                        assessment_sources[assessment_id] = source
                    payload = assessment.get("payload") or {}
                    if check_default:
                        if assessment_id not in assessment_dispositions:
                            assessment_dispositions[
                                assessment_id
                            ] = await resolved_assessment_disposition(
                                event_log,
                                assessment,
                                source_event=assessment_sources[assessment_id],
                            )
                        outcome, reason = assessment_dispositions[assessment_id]
                    else:
                        outcome = assessment_outcome(
                            assessment,
                            source_event=assessment_sources.get(assessment_id),
                        )
                        reason = assessment_reason(assessment)
                    counts[str(outcome)] += 1
                    detail = str(outcome)
                    if outcome == "failed" and len(failures) < 20:
                        failures.append(
                            {
                                "event_id": event_id,
                                "event_type": event_type,
                                "mode": obligation.mode,
                                "reason": reason,
                            }
                        )
                else:
                    assessor = obligation.assessor or ""
                    if assessor.startswith("pipeline:"):
                        capability = _capability_value(
                            capabilities, f"pipeline:{assessor}"
                        )
                        checkpoint_passed = _checkpoint_has_passed(capability, event_id)
                        unhealthy = capability.get("state") in {
                            "degraded",
                            "blocked",
                        }
                        status = (
                            "missing"
                            if checkpoint_passed or unhealthy or _is_overdue(event)
                            else "pending"
                        )
                    else:
                        status = "missing"
                    detail = (
                        "assessment_event_absent_after_checkpoint_or_grace"
                        if status == "missing"
                        else "assessment_pipeline_pending"
                    )
            elif obligation.mode == "canonical_projection":
                projection_states = [
                    _capability_value(capabilities, f"projection:{name}")
                    for name in obligation.projections
                ]
                if all(
                    _checkpoint_has_passed(capability, event_id)
                    for capability in projection_states
                ):
                    status = "accounted"
                elif any(
                    capability.get("state") in {"degraded", "blocked"}
                    for capability in projection_states
                ):
                    status = "missing"
                    detail = "projection_degraded_before_source"
                elif _is_overdue(event):
                    status = "missing"
                    detail = "projection_checkpoint_overdue"
                else:
                    status = "pending"
                    detail = "projection_checkpoint_pending"
            elif obligation.mode == "action_request_terminal":
                command_id = str((event.get("payload") or {}).get("command_id") or "")
                if command_id not in action_terminals:
                    status = "missing" if _is_overdue(event) else "pending"
                    detail = (
                        "terminal_action_result_overdue"
                        if status == "missing"
                        else "terminal_action_result_pending"
                    )

            counts[status] += 1
            row[status] += 1
            if status == "missing":
                group_key = (event_type, obligation.mode, detail)
                group = missing_groups.setdefault(
                    group_key,
                    {
                        "event_type": event_type,
                        "mode": obligation.mode,
                        "reason": detail,
                        "count": 0,
                        "first_event_id": event_id,
                        "last_event_id": event_id,
                    },
                )
                group["count"] += 1
                group["first_event_id"] = min(group["first_event_id"], event_id)
                group["last_event_id"] = max(group["last_event_id"], event_id)
            if status in {"missing", "pending"}:
                target = missing if status == "missing" else pending
                if len(target) < 20:
                    target.append(
                        {
                            "event_id": event_id,
                            "event_type": event_type,
                            "mode": obligation.mode,
                            "reason": detail,
                        }
                    )

    counts["source_facts"] = len(source_ids)
    recovery_counts = {
        "requested": len(recovery_requests),
        "succeeded": 0,
        "failed": 0,
        "refused": 0,
        "pending": 0,
    }
    for request_event_id in recovery_requests:
        assessment = recovery_assessments.get(request_event_id)
        if assessment is not None:
            if assessment_outcome(assessment) == "failed":
                recovery_counts["failed"] += 1
            else:
                recovery_counts["succeeded"] += 1
        elif (
            request_event_id in recovery_refusals
            or request_event_id in retired_commands
        ):
            recovery_counts["refused"] += 1
        else:
            recovery_counts["pending"] += 1
    effect_counts = _provider_effect_counts(
        source_events, action_terminal_events, effect_reconciliations
    )
    effects_healthy = _provider_effect_integrity_healthy(effect_counts)
    zero_effects = _zero_provider_effects(effect_counts)
    messaging_counts = {
        key: sum(
            int((event.get("payload") or {}).get(key) or 0)
            for event in messaging_cycles
        )
        for key in (
            "requested",
            "accepted",
            "duplicates",
            "quarantined",
            "failed",
            "missing",
        )
    }
    latest_messaging = (
        messaging_cycles[-1].get("payload", {}) if messaging_cycles else {}
    )
    window_start = _aware_datetime(provider_effect_window_started_at)
    if window_start is None:
        window_counts = _provider_effect_counts([], {})
        window_state = "missing"
        window_zero_effects = False
    else:
        window_sources = [
            event for event in source_events if _event_at_or_after(event, window_start)
        ]
        window_terminals = {
            command_id: [
                event for event in rows if _event_at_or_after(event, window_start)
            ]
            for command_id, rows in action_terminal_events.items()
        }
        window_terminals = {
            command_id: rows for command_id, rows in window_terminals.items() if rows
        }
        window_counts = _provider_effect_counts(window_sources, window_terminals)
        window_zero_effects = _zero_provider_effects(window_counts)
        window_state = (
            "healthy"
            if _provider_effect_integrity_healthy(window_counts) and window_zero_effects
            else "degraded"
        )
    return {
        "contract": "source-assurance-v1",
        "state": (
            "degraded"
            if counts["missing"] or counts["failed"] or not effects_healthy
            else "healthy"
        ),
        "counts": counts,
        "missing": missing,
        "missing_summary": {
            "contract": "source-assurance-missing-summary-v1",
            "total_count": counts["missing"],
            "detail_limit": 20,
            "groups": [missing_groups[key] for key in sorted(missing_groups)],
            "content_exposed": False,
        },
        "pending": pending,
        "failed": failures,
        "by_event_type": by_event_type,
        "declared_source_event_types": len(by_type),
        "declared_obligations": len(obligations),
        "recovery": {
            "contract": "source-assessment-recovery-v1",
            "counts": recovery_counts,
            "attempt_limit": 3,
            "max_concurrency": 2,
            "content_exposed": False,
        },
        "provider_effects": {
            "contract": "provider-effect-accounting-v1",
            "state": "healthy" if effects_healthy else "degraded",
            "counts": effect_counts,
            "zero_effect_state": "healthy" if zero_effects else "nonzero",
            "observation_window": {
                "state": window_state,
                "started_at_configured": window_start is not None,
                "counts": window_counts,
                "content_exposed": False,
            },
            "content_exposed": False,
        },
        "messaging_ingestion": {
            "contract": "messages-source-accounting-v1",
            "cycles": len(messaging_cycles),
            "counts": messaging_counts,
            "accounting_reconciles": messaging_counts["requested"]
            == sum(
                messaging_counts[key]
                for key in (
                    "accepted",
                    "duplicates",
                    "quarantined",
                    "failed",
                    "missing",
                )
            ),
            "latest": {
                "phase": latest_messaging.get("phase"),
                "coverage": latest_messaging.get("coverage"),
                "truncated": latest_messaging.get("truncated"),
                "observed_at": latest_messaging.get("observed_at"),
            },
            "global_history_complete": False,
            "content_exposed": False,
        },
        "watermark_event_id": watermark_event_id,
        "content_exposed": False,
    }


__all__ = [
    "SourceObligation",
    "audit_source_assurance",
    "load_source_obligations",
]
