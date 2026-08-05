"""Private member-knowledge to scope-filtered domain-search pipeline."""

from __future__ import annotations

import hashlib
from typing import Any

from adminme.domain_search import (
    DOMAIN_VECTOR_DIMENSIONS,
    DOMAIN_VECTOR_MODEL,
    domain_vector,
)
from adminme.events.envelope import EventEnvelope
from adminme.pipelines.base import PipelineContext, Triggers


class MemberKnowledgeIndexingPipeline:
    pack_id = "pipeline:member_knowledge_indexing"
    version = "1.0.0"
    triggers: Triggers = {
        "events": [
            "member_knowledge.note_added",
            "member_knowledge.note_updated",
            "member_knowledge.note_corrected",
        ]
    }

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        if event.get("type") not in (self.triggers.get("events") or []):
            return
        payload = event["payload"]
        knowledge_id = str(payload["knowledge_id"])
        source_event_id = str(event["event_id"])
        if event.get("sensitivity") == "privileged":
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
            idempotency_key=(
                f"knowledge-index:{event['tenant_id']}:{knowledge_id}:{text_hash}"
            ),
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


__all__ = ["MemberKnowledgeIndexingPipeline"]
