"""Private member-knowledge to scope-filtered domain-search pipeline."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
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
from adminme.triage_intent import CANDIDATE_FIELD_ALLOWLISTS


_log = logging.getLogger(__name__)
_SKILL_ID = "skill:extract_note_semantics"
_CANDIDATE_FIELDS = CANDIDATE_FIELD_ALLOWLISTS


class MemberKnowledgeIndexingPipeline:
    pack_id = "pipeline:member_knowledge_indexing"
    version = "3.0.0"
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
            await self._emit_assessment(
                event,
                ctx,
                knowledge_id=knowledge_id,
                source_event_id=source_event_id,
                candidates=[],
                outcome="quarantined",
                reason_codes=["privileged_source_not_sent_to_language_seam"],
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
            superseded_at = str(
                event.get("recorded_at")
                or event.get("occurred_at")
                or datetime.fromtimestamp(
                    int(event["event_at_ms"]) / 1000, timezone.utc
                ).isoformat()
            )
            envelope = EventEnvelope(
                event_at_ms=int(event["event_at_ms"]),
                tenant_id=str(event["tenant_id"]),
                type="triage.item.superseded",
                schema_version=1,
                occurred_at=superseded_at,
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
                    "superseded_at": superseded_at,
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
            await self._emit_assessment(
                event,
                ctx,
                knowledge_id=knowledge_id,
                source_event_id=source_event_id,
                candidates=[],
                outcome="no_candidate",
                reason_codes=["source_has_no_text_content"],
            )
            return
        owner_scope = str(event["owner_scope"])
        if not owner_scope.startswith("private:"):
            await self._emit_assessment(
                event,
                ctx,
                knowledge_id=knowledge_id,
                source_event_id=source_event_id,
                candidates=[],
                outcome="quarantined",
                reason_codes=["source_scope_is_not_private_member"],
            )
            return
        member_id = owner_scope.removeprefix("private:")
        if payload.get("member_id") != member_id:
            await self._emit_assessment(
                event,
                ctx,
                knowledge_id=knowledge_id,
                source_event_id=source_event_id,
                candidates=[],
                outcome="quarantined",
                reason_codes=["payload_member_scope_mismatch"],
            )
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
            invocation_idempotency_key=f"note-semantics:v3:{source_event_id}",
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
            await self._emit_assessment(
                event,
                ctx,
                knowledge_id=knowledge_id,
                source_event_id=source_event_id,
                candidates=[],
                outcome="failed",
                reason_codes=[f"language_seam_{type(exc).__name__}"],
            )
            return
        candidates = output.get("candidates")
        if not isinstance(candidates, list):
            await self._emit_assessment(
                event,
                ctx,
                knowledge_id=knowledge_id,
                source_event_id=source_event_id,
                candidates=[],
                outcome="failed",
                reason_codes=["language_output_candidates_invalid"],
            )
            return
        note_key = hashlib.sha256(
            f"{event['tenant_id']}:{knowledge_id}".encode()
        ).hexdigest()[:20]
        eligible: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates[:10]):
            if not isinstance(candidate, dict):
                continue
            target_kind = str(candidate.get("target_kind") or "")
            raw_confidence = candidate.get("confidence")
            confidence = (
                float(raw_confidence)
                if isinstance(raw_confidence, (int, float))
                and not isinstance(raw_confidence, bool)
                and 0.0 <= float(raw_confidence) <= 1.0
                else 0.0
            )
            title = str(candidate.get("title") or "").strip()
            quote = str(candidate.get("evidence_quote") or "").strip()
            if (
                target_kind not in _CANDIDATE_FIELDS
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
            evidence_start = body.find(quote)
            eligible.append(
                {
                    "proposal_id": proposal_id,
                    "candidate_key": f"note-candidate-{index + 1}",
                    "target_kind": target_kind,
                    "title": quote[:1000],
                    "fields": sanitized,
                    "evidence_spans": [
                        {
                            "field": "title",
                            "start": evidence_start,
                            "end": evidence_start + len(quote),
                        }
                    ],
                    "confidence": round(confidence, 4),
                    "reasons": [
                        "exact_source_quote_grounded_v1",
                        *[str(value)[:500] for value in candidate.get("reasons") or []][
                            :18
                        ],
                    ][:20],
                }
            )

        extraction_event_id = await self._emit_assessment(
            event,
            ctx,
            knowledge_id=knowledge_id,
            source_event_id=source_event_id,
            candidates=eligible,
            outcome="eligible_candidates" if eligible else "no_candidate",
            reason_codes=[
                "language_assessment_completed",
                "eligible_candidate_found" if eligible else "no_eligible_candidate",
            ],
        )
        proposal_occurred_at = str(
            event.get("recorded_at")
            or event.get("occurred_at")
            or datetime.fromtimestamp(
                int(event["event_at_ms"]) / 1000, timezone.utc
            ).isoformat()
        )
        for candidate in eligible:
            envelope = EventEnvelope(
                event_at_ms=int(event["event_at_ms"]),
                tenant_id=str(event["tenant_id"]),
                type="triage.item.proposed",
                schema_version=2,
                occurred_at=proposal_occurred_at,
                source_adapter=self.pack_id,
                source_account_id="note-semantics",
                owner_scope=owner_scope,
                visibility_scope=owner_scope,
                sensitivity=event.get("sensitivity", "normal"),
                idempotency_key=(
                    f"triage:note-candidate:v2:{source_event_id}:"
                    f"{candidate['candidate_key']}"
                ),
                payload={
                    "proposal_id": candidate["proposal_id"],
                    "target_kind": candidate["target_kind"],
                    "owner_member_id": member_id,
                    "title": candidate["title"],
                    "candidate": candidate["fields"],
                    "route": {"member_id": member_id},
                    "source_event_id": source_event_id,
                    "parent_intent_id": None,
                    "candidate_key": candidate["candidate_key"],
                    "extraction_event_id": extraction_event_id,
                    "eligibility_rule_ids": [
                        "private_member_note_v1",
                        "exact_source_quote_grounded_v1",
                        f"candidate_fields_{candidate['target_kind']}_v1",
                    ],
                    "confidence": candidate["confidence"],
                    "reasons": candidate["reasons"],
                },
            )
            await ctx.event_sink.write(
                envelope,
                correlation_id=ctx.correlation_id,
                causation_id=extraction_event_id,
            )

    async def _emit_assessment(
        self,
        event: dict[str, Any],
        ctx: PipelineContext,
        *,
        knowledge_id: str,
        source_event_id: str,
        candidates: list[dict[str, Any]],
        outcome: str,
        reason_codes: list[str],
    ) -> str:
        assessed_at = str(
            event.get("recorded_at")
            or event.get("occurred_at")
            or datetime.fromtimestamp(
                int(event["event_at_ms"]) / 1000, timezone.utc
            ).isoformat()
        )
        feature_candidates = [
            {
                "candidate_key": candidate["candidate_key"],
                "target_kind": candidate["target_kind"],
                "title": candidate["title"],
                "fields": candidate["fields"],
                "evidence_spans": candidate["evidence_spans"],
                "confidence": candidate["confidence"],
                "reasons": candidate["reasons"],
            }
            for candidate in candidates
        ]
        return await ctx.event_sink.write(
            EventEnvelope(
                event_at_ms=int(event["event_at_ms"]),
                tenant_id=str(event["tenant_id"]),
                type="triage.intent.features_extracted",
                schema_version=2,
                occurred_at=assessed_at,
                source_adapter=self.pack_id,
                source_account_id="note-semantics",
                owner_scope=str(event["owner_scope"]),
                visibility_scope=str(event["visibility_scope"]),
                sensitivity=event.get("sensitivity", "normal"),
                idempotency_key=f"triage:note-features:v3:{source_event_id}",
                payload={
                    "intent_id": f"source:{source_event_id}",
                    "source_event_id": source_event_id,
                    "skill_call_event_id": None,
                    "extractor_id": "grounded-note-intent-features-v3",
                    "language": None,
                    "candidates": feature_candidates,
                    "extraction_state": (
                        "complete"
                        if outcome in {"eligible_candidates", "no_candidate"}
                        else "unavailable"
                        if outcome == "failed"
                        else "invalid"
                    ),
                    "extracted_at": assessed_at,
                    "assessment_outcome": outcome,
                    "reason_codes": reason_codes,
                },
            ),
            correlation_id=ctx.correlation_id,
            causation_id=source_event_id,
        )


__all__ = ["MemberKnowledgeIndexingPipeline"]
