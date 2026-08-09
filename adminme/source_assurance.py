"""End-to-end accounting for every declared adapter or member source fact.

The event log proves ingestion.  Source assurance proves the rest of the
contract: a candidate-bearing source was explicitly assessed, knowledge
metadata explicitly declared candidate review inapplicable, a state mirror
reached its named projection, or an observation/action event is itself a
terminal fact. Missing and still-pending are never conflated with "empty".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from adminme.events.log import EventLog
from adminme.lib.pack_registry.models import SourceAssuranceRule


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
            terminal_events=("triage.intent.features_extracted",),
            assessor="inline:member-intent",
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


async def audit_source_assurance(
    event_log: EventLog,
    *,
    repository_root: Path,
    capabilities: dict[str, Any],
    through_event_id: str | None = None,
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
    payload_types = {
        event_type
        for obligation in obligations
        for event_type in obligation.terminal_events
    } | {
        obligation.event_type
        for obligation in obligations
        if obligation.mode == "action_request_terminal"
    }
    watermark_event_id = through_event_id
    if watermark_event_id is None:
        watermark_event_id = await event_log.latest_event_id()
    source_events: list[dict[str, Any]] = []

    assessments: dict[str, dict[str, Any]] = {}
    action_terminals: set[str] = set()
    async for event in event_log.read_metadata_since(
        # An empty log has a null public watermark; the impossible empty
        # string still provides a closed upper bound if an append races the
        # first audit.
        through=watermark_event_id or "",
        types=sorted(interesting),
        include_payload_for=sorted(payload_types),
    ):
        payload = event.get("payload") or {}
        if event.get("type") == "triage.intent.features_extracted":
            assessments[str(payload.get("source_event_id") or "")] = event
        elif event.get("type") in {
            "external_sync.write_completed",
            "external_sync.write_failed",
        }:
            action_terminals.add(str(payload.get("command_id") or ""))
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
        "quarantined": 0,
        "failed": 0,
    }
    source_ids: set[str] = set()
    missing: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
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
            if obligation.mode == "candidate_assessment":
                assessment = assessments.get(event_id)
                if assessment is not None:
                    payload = assessment.get("payload") or {}
                    outcome = payload.get("assessment_outcome")
                    if outcome not in {
                        "eligible_candidates",
                        "no_candidate",
                        "quarantined",
                        "failed",
                    }:
                        extraction_state = str(payload.get("extraction_state") or "")
                        outcome = (
                            "eligible_candidates"
                            if payload.get("candidates")
                            else "no_candidate"
                            if extraction_state == "complete"
                            else "failed"
                        )
                    counts[str(outcome)] += 1
                    detail = str(outcome)
                    if outcome == "failed" and len(failures) < 20:
                        failures.append(
                            {
                                "event_id": event_id,
                                "event_type": event_type,
                                "mode": obligation.mode,
                                "reason": str(
                                    (
                                        payload.get("reason_codes")
                                        or ["assessment_failed"]
                                    )[0]
                                ),
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
    return {
        "contract": "source-assurance-v1",
        "state": ("degraded" if counts["missing"] or counts["failed"] else "healthy"),
        "counts": counts,
        "missing": missing,
        "pending": pending,
        "failed": failures,
        "by_event_type": by_event_type,
        "declared_source_event_types": len(by_type),
        "declared_obligations": len(obligations),
        "watermark_event_id": watermark_event_id,
        "content_exposed": False,
    }


__all__ = [
    "SourceObligation",
    "audit_source_assurance",
    "load_source_obligations",
]
