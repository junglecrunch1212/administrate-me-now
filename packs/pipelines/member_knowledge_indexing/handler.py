"""Private member-knowledge to scope-filtered domain-search pipeline."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from adminme.domain_search import (
    DOMAIN_VECTOR_DIMENSIONS,
    DOMAIN_VECTOR_MODEL,
    domain_vector,
)
from adminme.events.envelope import EventEnvelope
from adminme.lib.skill_runner import (
    OpenClawResponseMalformed,
    OpenClawTimeout,
    OpenClawUnreachable,
    SkillContext,
    SkillHandlerError,
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillScopeInsufficient,
    SkillSensitivityRefused,
)
from adminme.pipelines.base import PipelineContext, Triggers, skill_output


_log = logging.getLogger(__name__)
_SKILL_ID = "skill:extract_note_semantics"
_CANDIDATE_FIELDS: dict[str, frozenset[str]] = {
    "task": frozenset({"description", "due", "energy", "effort_min"}),
    "calendar_event": frozenset({"start", "end", "all_day", "event_type", "location"}),
    "reminder": frozenset({"due", "notes", "priority"}),
    "list": frozenset({"notes"}),
    "list_item": frozenset({"due", "notes", "priority"}),
    "recurrence": frozenset(
        {
            "rrule",
            "next_occurrence",
            "recurrence_text",
            "kind",
            "lead_time_days",
            "trackable",
            "notes",
        }
    ),
}


class MemberKnowledgeIndexingPipeline:
    pack_id = "pipeline:member_knowledge_indexing"
    version = "2.0.0"
    triggers: Triggers = {
        "events": [
            "member_knowledge.note_added",
            "member_knowledge.note_updated",
            "member_knowledge.note_corrected",
            "member_knowledge.note_deleted",
        ]
    }

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        if event.get("type") not in (self.triggers.get("events") or []):
            return
        payload = event["payload"]
        knowledge_id = str(payload["knowledge_id"])
        source_event_id = str(event["event_id"])
        await self._supersede_prior_candidates(
            event,
            ctx,
            knowledge_id=knowledge_id,
            source_event_id=source_event_id,
            reason=(
                "source_deleted"
                if event.get("type") == "member_knowledge.note_deleted"
                else "source_privileged"
                if event.get("sensitivity") == "privileged"
                else "source_changed"
            ),
        )
        if event.get("type") == "member_knowledge.note_deleted":
            await self._revoke(
                event,
                ctx,
                knowledge_id=knowledge_id,
                source_event_id=source_event_id,
                reason="source_deleted",
            )
            return
        if event.get("sensitivity") == "privileged":
            await self._revoke(
                event,
                ctx,
                knowledge_id=knowledge_id,
                source_event_id=source_event_id,
                reason="privileged_content",
            )
            skipped = EventEnvelope(
                event_at_ms=int(event["event_at_ms"]),
                tenant_id=str(event["tenant_id"]),
                type="member_knowledge.index_skipped",
                schema_version=1,
                occurred_at=str(event["occurred_at"]),
                source_adapter=self.pack_id,
                source_account_id="domain-search",
                owner_scope=str(event["owner_scope"]),
                visibility_scope=str(event["visibility_scope"]),
                sensitivity="privileged",
                idempotency_key=f"knowledge-index-skip:{source_event_id}",
                payload={
                    "knowledge_id": knowledge_id,
                    "source_event_id": source_event_id,
                    "reason": "privileged_content",
                },
            )
            await ctx.event_sink.write(
                skipped,
                correlation_id=ctx.correlation_id,
                causation_id=source_event_id,
            )
            return

        search_text = "\n".join(
            part for part in (payload.get("title"), payload.get("body")) if part
        )
        text_hash = hashlib.sha256(search_text.encode("utf-8")).hexdigest()
        envelope = EventEnvelope(
            event_at_ms=int(event["event_at_ms"]),
            tenant_id=str(event["tenant_id"]),
            type="embedding.generated",
            schema_version=1,
            occurred_at=str(event["occurred_at"]),
            source_adapter=self.pack_id,
            source_account_id=str(event["source_account_id"]),
            owner_scope=str(event["owner_scope"]),
            visibility_scope=str(event["visibility_scope"]),
            sensitivity=event.get("sensitivity", "normal"),
            # One derived observation per immutable source event. A provider
            # may advance revision metadata without changing note text; keying
            # by text alone would make that legitimate update conflict with
            # the prior envelope's different provenance/timestamps.
            idempotency_key=f"knowledge-index:{source_event_id}",
            payload={
                "embedding_id": (
                    f"member_knowledge:{event['tenant_id']}:{knowledge_id}"
                ),
                "linked_kind": "member_knowledge",
                "linked_id": knowledge_id,
                "embedding_dimensions": DOMAIN_VECTOR_DIMENSIONS,
                "embedding": domain_vector(search_text),
                "model_name": DOMAIN_VECTOR_MODEL,
                "sensitivity": event.get("sensitivity", "normal"),
                "source_text_sha256": text_hash,
            },
        )
        await ctx.event_sink.write(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=source_event_id,
        )
        await self._extract_candidates(
            event,
            ctx,
            knowledge_id=knowledge_id,
            source_event_id=source_event_id,
            payload=payload,
        )

    async def _revoke(
        self,
        event: dict[str, Any],
        ctx: PipelineContext,
        *,
        knowledge_id: str,
        source_event_id: str,
        reason: str,
    ) -> None:
        envelope = EventEnvelope(
            event_at_ms=int(event["event_at_ms"]),
            tenant_id=str(event["tenant_id"]),
            type="embedding.revoked",
            schema_version=1,
            occurred_at=str(event["occurred_at"]),
            source_adapter=self.pack_id,
            source_account_id="domain-search",
            owner_scope=str(event["owner_scope"]),
            visibility_scope=str(event["visibility_scope"]),
            sensitivity=event.get("sensitivity", "normal"),
            idempotency_key=f"knowledge-index-revoke:{source_event_id}:{reason}",
            payload={
                "embedding_id": (
                    f"member_knowledge:{event['tenant_id']}:{knowledge_id}"
                ),
                "linked_kind": "member_knowledge",
                "linked_id": knowledge_id,
                "reason": reason,
                "source_event_id": source_event_id,
            },
        )
        await ctx.event_sink.write(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=source_event_id,
        )

    async def _supersede_prior_candidates(
        self,
        event: dict[str, Any],
        ctx: PipelineContext,
        *,
        knowledge_id: str,
        source_event_id: str,
        reason: str,
    ) -> None:
        note_key = hashlib.sha256(
            f"{event['tenant_id']}:{knowledge_id}".encode()
        ).hexdigest()[:20]
        prefix = f"triage:note:{note_key}:"
        events: list[dict[str, Any]] = []
        async for existing in ctx.event_log.read_since(
            types=[
                "triage.item.proposed",
                "triage.item.confirmed",
                "triage.item.dismissed",
                "triage.item.superseded",
            ]
        ):
            events.append(existing)
        terminal_ids = {
            str((existing.get("payload") or {}).get("proposal_id") or "")
            for existing in events
            if existing.get("type") != "triage.item.proposed"
        }
        for existing in events:
            if existing.get("type") != "triage.item.proposed":
                continue
            payload = existing.get("payload") or {}
            proposal_id = str(payload.get("proposal_id") or "")
            if not proposal_id.startswith(prefix):
                continue
            if proposal_id in terminal_ids:
                continue
            if str(payload.get("source_event_id") or "") == source_event_id:
                continue
            envelope = EventEnvelope(
                event_at_ms=int(event["event_at_ms"]),
                tenant_id=str(event["tenant_id"]),
                type="triage.item.superseded",
                schema_version=1,
                occurred_at=EventEnvelope.now_utc_iso(),
                source_adapter=self.pack_id,
                source_account_id="note-semantics",
                owner_scope=str(event["owner_scope"]),
                visibility_scope=str(event["visibility_scope"]),
                sensitivity=event.get("sensitivity", "normal"),
                idempotency_key=(
                    f"triage:note-supersede:{proposal_id}:{source_event_id}"
                ),
                payload={
                    "proposal_id": proposal_id,
                    "superseded_at": EventEnvelope.now_utc_iso(),
                    "replacement_source_event_id": source_event_id,
                    "reason": reason,
                },
            )
            await ctx.event_sink.write(
                envelope,
                correlation_id=ctx.correlation_id,
                causation_id=source_event_id,
            )

    async def _extract_candidates(
        self,
        event: dict[str, Any],
        ctx: PipelineContext,
        *,
        knowledge_id: str,
        source_event_id: str,
        payload: dict[str, Any],
    ) -> None:
        body = str(payload.get("body") or "")
        if not body.strip():
            return
        inputs = {
            "title": payload.get("title"),
            "body_text": body[:50_000],
            "body_format": str(payload.get("body_format") or "plain"),
            "source_kind": str(payload.get("source_kind") or "fixture"),
            "content_sha256": str(payload["content_sha256"]),
        }
        skill_ctx = SkillContext(
            session=ctx.session,
            correlation_id=ctx.correlation_id,
            triggering_event_id=source_event_id,
            owner_scope=str(event["owner_scope"]),
            visibility_scope=str(event["visibility_scope"]),
            sensitivity=event.get("sensitivity", "normal"),
        )
        try:
            result = await ctx.run_skill_fn(_SKILL_ID, inputs, skill_ctx)
            output = skill_output(result)
        except (
            RuntimeError,
            SkillInputInvalid,
            SkillOutputInvalid,
            SkillSensitivityRefused,
            SkillScopeInsufficient,
            SkillHandlerError,
            OpenClawTimeout,
            OpenClawUnreachable,
            OpenClawResponseMalformed,
        ) as exc:
            _log.info("note extraction defaulted closed: %s", type(exc).__name__)
            return
        candidates = output.get("candidates")
        if not isinstance(candidates, list):
            return
        owner_scope = str(event["owner_scope"])
        if not owner_scope.startswith("private:"):
            return
        member_id = owner_scope.removeprefix("private:")
        if payload.get("member_id") != member_id:
            return
        note_key = hashlib.sha256(
            f"{event['tenant_id']}:{knowledge_id}".encode()
        ).hexdigest()[:20]
        for index, candidate in enumerate(candidates[:10]):
            if not isinstance(candidate, dict):
                continue
            target_kind = str(candidate.get("target_kind") or "")
            confidence = float(candidate.get("confidence") or 0.0)
            title = str(candidate.get("title") or "").strip()
            quote = str(candidate.get("evidence_quote") or "").strip()
            if (
                target_kind not in _CANDIDATE_FIELDS
                or confidence < 0.72
                or not title
                or not quote
                or quote not in body
            ):
                continue
            raw_fields = candidate.get("candidate")
            fields = raw_fields if isinstance(raw_fields, dict) else {}
            sanitized = {
                key: value
                for key, value in fields.items()
                if key in _CANDIDATE_FIELDS[target_kind]
            }
            proposal_id = (
                f"triage:note:{note_key}:{source_event_id}:{index}:{target_kind}"
            )
            envelope = EventEnvelope(
                event_at_ms=int(event["event_at_ms"]),
                tenant_id=str(event["tenant_id"]),
                type="triage.item.proposed",
                schema_version=1,
                occurred_at=EventEnvelope.now_utc_iso(),
                source_adapter=self.pack_id,
                source_account_id="note-semantics",
                owner_scope=owner_scope,
                visibility_scope=owner_scope,
                sensitivity=event.get("sensitivity", "normal"),
                idempotency_key=f"triage:note-candidate:{source_event_id}:{index}",
                payload={
                    "proposal_id": proposal_id,
                    "target_kind": target_kind,
                    "owner_member_id": member_id,
                    "title": title[:1000],
                    "candidate": sanitized,
                    "route": {"member_id": member_id},
                    "source_event_id": source_event_id,
                    "confidence": round(confidence, 4),
                    "reasons": [
                        *[str(value)[:500] for value in candidate.get("reasons") or []][
                            :18
                        ],
                        f"note evidence: {quote[:240]}",
                    ],
                },
            )
            await ctx.event_sink.write(
                envelope,
                correlation_id=ctx.correlation_id,
                causation_id=source_event_id,
            )


__all__ = ["MemberKnowledgeIndexingPipeline"]
