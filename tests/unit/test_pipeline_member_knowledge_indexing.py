"""Deterministic Notes indexing, revocation, and Triage proposal boundaries."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.lib.skill_runner import SkillContext
from adminme.pipelines.base import PipelineContext


PACK_ROOT = (
    Path(__file__).resolve().parents[2]
    / "packs"
    / "pipelines"
    / "member_knowledge_indexing"
)


def _pipeline_class() -> type[Any]:
    spec = importlib.util.spec_from_file_location(
        "test_member_knowledge_indexing_handler", PACK_ROOT / "handler.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MemberKnowledgeIndexingPipeline


MemberKnowledgeIndexingPipeline = _pipeline_class()


@dataclass
class _Recorded:
    envelope: EventEnvelope
    correlation_id: str | None
    causation_id: str | None


@dataclass
class _FakeLog:
    existing: list[dict[str, Any]] = field(default_factory=list)
    calls: list[_Recorded] = field(default_factory=list)

    async def read_since(
        self, *, types: list[str] | None = None, **_: Any
    ) -> AsyncIterator[dict[str, Any]]:
        for event in self.existing:
            if types is None or event.get("type") in types:
                yield event

    async def write(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        self.calls.append(_Recorded(envelope, correlation_id, causation_id))
        return f"derived-{len(self.calls)}"


def _context(log: _FakeLog, run_skill: Any) -> PipelineContext:
    from adminme.lib.session import build_internal_session

    return PipelineContext(
        session=build_internal_session("pipeline_runner", "device", "tenant-notes"),
        event_log=log,  # type: ignore[arg-type]
        event_sink=log,  # type: ignore[arg-type]
        run_skill_fn=run_skill,
        outbound_fn=lambda *a, **kw: None,  # type: ignore[arg-type, return-value]
        guarded_write=None,
        observation_manager=None,
        triggering_event_id="note-event-1",
        correlation_id="notes-correlation",
    )


def _note_event(
    *,
    event_id: str = "note-event-1",
    event_type: str = "member_knowledge.note_added",
    sensitivity: str = "normal",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "knowledge_id": "note-1",
        "member_id": "member-a",
        "title": "School call",
        "body": "Please return the school form by Friday.",
        "body_format": "transcript",
        "source_kind": "fathom",
        "source_external_id": "source-note-1",
        "content_sha256": "a" * 64,
        "captured_at": "2026-08-06T12:00:00Z",
    }
    if event_type == "member_knowledge.note_deleted":
        payload = {
            "knowledge_id": "note-1",
            "member_id": "member-a",
            "source_kind": "fathom",
            "source_external_id": "source-note-1",
            "deleted_at": "2026-08-06T13:00:00Z",
        }
    return {
        "event_id": event_id,
        "event_at_ms": 1_786_018_400_000,
        "tenant_id": "tenant-notes",
        "type": event_type,
        "occurred_at": "2026-08-06T12:00:00Z",
        "source_adapter": "notes-fixture",
        "source_account_id": "member-a-source",
        "owner_scope": "private:member-a",
        "visibility_scope": "private:member-a",
        "sensitivity": sensitivity,
        "payload": payload,
    }


@pytest.mark.asyncio
async def test_exact_evidence_candidate_is_sanitized_and_sent_only_to_triage() -> None:
    log = _FakeLog()

    async def run_skill(
        skill_id: str, inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> dict[str, Any]:
        assert skill_id == "skill:extract_note_semantics"
        assert inputs["body_text"] == "Please return the school form by Friday."
        assert skill_ctx.owner_scope == "private:member-a"
        assert skill_ctx.invocation_idempotency_key == "note-semantics:v3:note-event-1"
        return {
            "candidates": [
                {
                    "target_kind": "task",
                    "title": "Return the school form",
                    "candidate": {
                        "due": "2026-08-07",
                        "energy": "low",
                        "provider": "forbidden",
                    },
                    "evidence_quote": "return the school form by Friday",
                    "confidence": 0.96,
                    "reasons": ["Explicit requested action"],
                },
                {
                    "target_kind": "calendar_event",
                    "title": "Fabricated event",
                    "candidate": {"start": "2026-08-07T09:00:00Z"},
                    "evidence_quote": "not in the note",
                    "confidence": 0.99,
                    "reasons": [],
                },
            ]
        }

    await MemberKnowledgeIndexingPipeline().handle(
        _note_event(), _context(log, run_skill)
    )
    assert [call.envelope.type for call in log.calls] == [
        "embedding.generated",
        "triage.intent.features_extracted",
        "triage.item.proposed",
    ]
    assert log.calls[0].envelope.idempotency_key == "knowledge-index:note-event-1"
    assessment = log.calls[1].envelope.payload
    assert assessment["assessment_outcome"] == "eligible_candidates"
    proposal = log.calls[2].envelope.payload
    assert log.calls[2].envelope.occurred_at == "2026-08-06T12:00:00Z"
    assert proposal["target_kind"] == "task"
    assert proposal["candidate"] == {"due": "2026-08-07", "energy": "low"}
    assert proposal["route"] == {"member_id": "member-a"}
    assert proposal["source_event_id"] == "note-event-1"
    assert proposal["title"] == "return the school form by Friday"
    assert proposal["extraction_event_id"] == "derived-2"


@pytest.mark.asyncio
async def test_privileged_note_revokes_index_and_never_reaches_model() -> None:
    log = _FakeLog()

    async def forbidden(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError("privileged content reached the skill seam")

    await MemberKnowledgeIndexingPipeline().handle(
        _note_event(sensitivity="privileged"), _context(log, forbidden)
    )
    assert [call.envelope.type for call in log.calls] == [
        "embedding.revoked",
        "member_knowledge.index_skipped",
        "triage.intent.features_extracted",
    ]
    assert log.calls[0].envelope.payload["reason"] == "privileged_content"
    assert log.calls[-1].envelope.payload["assessment_outcome"] == "quarantined"


@pytest.mark.asyncio
async def test_unroutable_note_scope_is_quarantined_before_model_processing() -> None:
    log = _FakeLog()

    async def forbidden(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError("unroutable shared content reached the skill seam")

    event = _note_event()
    event["owner_scope"] = "shared:household"
    event["visibility_scope"] = "shared:household"
    await MemberKnowledgeIndexingPipeline().handle(event, _context(log, forbidden))

    assert [call.envelope.type for call in log.calls] == [
        "embedding.generated",
        "triage.intent.features_extracted",
    ]
    assert log.calls[-1].envelope.payload["assessment_outcome"] == "quarantined"
    assert log.calls[-1].envelope.payload["reason_codes"] == [
        "source_scope_is_not_private_member"
    ]


@pytest.mark.asyncio
async def test_changed_or_deleted_source_supersedes_prior_pending_candidate() -> None:
    old_proposal = {
        "event_id": "old-proposal-event",
        "type": "triage.item.proposed",
        "payload": {
            "proposal_id": "triage:note:ead5c825e50fe50dca4d:old-note-event:0:task",
            "source_event_id": "old-note-event",
        },
    }
    # Use the pipeline's stable note key rather than coupling the fixture to a
    # handwritten hash.
    import hashlib

    key = hashlib.sha256(b"tenant-notes:note-1").hexdigest()[:20]
    old_proposal["payload"]["proposal_id"] = f"triage:note:{key}:old-note-event:0:task"
    log = _FakeLog(existing=[old_proposal])

    async def empty(*_: Any, **__: Any) -> dict[str, Any]:
        return {"candidates": []}

    await MemberKnowledgeIndexingPipeline().handle(
        _note_event(
            event_id="corrected-note-event",
            event_type="member_knowledge.note_corrected",
        ),
        _context(log, empty),
    )
    assert [call.envelope.type for call in log.calls[:2]] == [
        "triage.item.superseded",
        "embedding.generated",
    ]
    assert log.calls[0].envelope.payload["reason"] == "source_changed"

    deleted_log = _FakeLog(existing=[old_proposal])
    await MemberKnowledgeIndexingPipeline().handle(
        _note_event(
            event_id="deleted-note-event", event_type="member_knowledge.note_deleted"
        ),
        _context(deleted_log, empty),
    )
    assert [call.envelope.type for call in deleted_log.calls] == [
        "triage.item.superseded",
        "embedding.revoked",
    ]
    assert deleted_log.calls[0].envelope.payload["reason"] == "source_deleted"
    assert deleted_log.calls[1].envelope.payload["reason"] == "source_deleted"
