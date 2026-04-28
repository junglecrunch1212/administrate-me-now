"""commitment_extraction reactive pipeline.

Per [BUILD.md §1139-1148] and [REFERENCE_EXAMPLES.md §2]. Subscribes to
inbound (`messaging.received`) and outbound (`messaging.sent`) messaging
events; resolves the sender against the parties projection via
`ctx.parties_conn_factory`, classifies the message via
`classify_commitment_candidate@^3.0.0`, and on a positive classification
extracts structured fields via `extract_commitment_fields@^2.1.0`.
Emits `commitment.proposed` (above review_threshold → confident; between
min and review_threshold → weak) or `commitment.suppressed` (below
min_confidence, on skill failure, on sender resolution failure).

Defensive-default discipline ([§7.7]): the pipeline catches the seven
declared skill-error types and emits `commitment.suppressed` with
reason ``skill_failure_defensive_default`` rather than letting the
exception propagate up to the runner — which would non-advance the bus
checkpoint and block subsequent events on every classify failure. The
`except` list includes ``SkillSensitivityRefused`` and
``SkillScopeInsufficient`` defense-in-depth (10b-i build_log F-2
carry-forward) — today both skill packs declare ``sensitivity_required:
normal`` and ``context_scopes_required: []`` so neither can fire, but
catching them now eliminates the F-2 risk for future-prompt skill
upgrades.

Per [ADR-0002], skill calls go through ``ctx.run_skill_fn`` (bound to
``adminme.lib.skill_runner.run_skill``) and never through provider
SDKs.

Per [§7.3] this pipeline NEVER writes a projection row directly — it
emits events and projections consume them. The pipeline opens its
parties-DB connection inside ``handle()`` and closes via context
manager (10a connection-management note); the runner does NOT own
per-pipeline DB connections.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Any

import yaml

from adminme.events.envelope import EventEnvelope
from adminme.lib.skill_runner import (
    OpenClawResponseMalformed,
    OpenClawTimeout,
    OpenClawUnreachable,
    SkillContext,
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillScopeInsufficient,
    SkillSensitivityRefused,
)
from adminme.pipelines.base import PipelineContext, Triggers
from adminme.projections.parties.queries import find_party_by_identifier

_log = logging.getLogger(__name__)

CLASSIFY_SKILL_ID = "skill:classify_commitment_candidate"
CLASSIFY_SKILL_VERSION = "3.0.0"
EXTRACT_SKILL_ID = "skill:extract_commitment_fields"
EXTRACT_SKILL_VERSION = "2.1.0"

# Defensive-default exception bundle. The first five mirror noise_filtering's
# 10b-i list; the last two are the F-2 widening per
# docs/build_log.md §"Prompt 10b-i" Carry-forward for prompt 10b-ii.
_SKILL_FAILURE_TYPES: tuple[type[BaseException], ...] = (
    SkillInputInvalid,
    SkillOutputInvalid,
    OpenClawTimeout,
    OpenClawUnreachable,
    OpenClawResponseMalformed,
    SkillSensitivityRefused,
    SkillScopeInsufficient,
)


def _classify_identifier(payload: dict[str, Any]) -> tuple[str, str]:
    """Derive (kind, value_normalized) from a `messaging.received` /
    `messaging.sent` payload's `from_identifier` per the 10b-i mapping
    pattern (mirrored inline here per the prompt's "no shared helper
    yet" guidance).

    `MessagingSentV1` does NOT carry `from_identifier` — for outbound
    events the receiving member IS the sender, so the sender-resolution
    path returns None for kind/value (handler treats this as
    unresolvable and emits suppressed)."""
    from_id = str(payload.get("from_identifier", ""))
    if not from_id:
        return "", ""
    channel = str(payload.get("source_channel", "")).lower()
    if "sms" in channel:
        kind = "phone"
        value_normalized = "".join(c for c in from_id if c.isdigit())
    elif "imessage" in channel:
        kind = "imessage_handle"
        value_normalized = from_id.lower().strip()
    else:
        kind = "email"
        value_normalized = from_id.lower().strip()
    return kind, value_normalized


def _new_commitment_id() -> str:
    return f"cmt_{secrets.token_hex(11)}"


def _load_config(pack_root: Path) -> dict[str, Any]:
    """Load the pipeline's config from the packaged ``config.example.yaml``.

    Bootstrap §3 / §16 will copy this to
    ``<instance_dir>/packs/pipelines/commitment_extraction/config.yaml``
    on first run; that copy is the production override. For the in-pack
    default we just read the example."""
    config_path = pack_root / "config.example.yaml"
    with config_path.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise RuntimeError(
            f"commitment_extraction config at {config_path} did not parse "
            f"to a mapping"
        )
    return data


def _thresholds_for_member(
    config: dict[str, Any], member_id: str
) -> tuple[float, float]:
    """Resolve (min_confidence, review_threshold) for a specific member,
    honoring `per_member_overrides` per [REFERENCE_EXAMPLES.md §2]."""
    base_min = float(config.get("min_confidence", 0.55))
    base_review = float(config.get("review_threshold", 0.75))
    overrides = config.get("per_member_overrides") or {}
    member_block = overrides.get(member_id) or {}
    return (
        float(member_block.get("min_confidence", base_min)),
        float(member_block.get("review_threshold", base_review)),
    )


class CommitmentExtractionPipeline:
    pack_id: str = "pipeline:commitment_extraction"
    version: str = "4.2.0"
    triggers: Triggers = {
        "events": ["messaging.received", "messaging.sent"]
    }

    # `_PACK_ROOT` is set at module import for the in-pack default config
    # lookup; tests that want a custom config patch ``self._config_override``
    # before calling ``handle()``.
    _PACK_ROOT = Path(__file__).resolve().parent

    def __init__(self) -> None:
        self._config_override: dict[str, Any] | None = None

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        event_type = event.get("type", "")
        if event_type not in ("messaging.received", "messaging.sent"):
            return

        config = self._config_override or _load_config(self._PACK_ROOT)
        payload = event.get("payload") or {}
        source_event_id = str(event.get("event_id", ""))

        # 1. Sender resolution. Outbound (`messaging.sent`) does not
        # carry `from_identifier` — treat as unresolvable and suppress
        # (defense-in-depth path). The inbox surface gets a record of
        # the suppressed evaluation either way.
        if ctx.parties_conn_factory is None:
            await self._emit_suppressed(
                event=event,
                ctx=ctx,
                reason="skill_failure_defensive_default",
                confidence=0.0,
                threshold=float(config.get("min_confidence", 0.55)),
                source_event_id=source_event_id,
            )
            return

        kind, value_normalized = _classify_identifier(payload)
        if not value_normalized:
            await self._emit_suppressed(
                event=event,
                ctx=ctx,
                reason="skill_failure_defensive_default",
                confidence=0.0,
                threshold=float(config.get("min_confidence", 0.55)),
                source_event_id=source_event_id,
            )
            return

        with ctx.parties_conn_factory() as conn:
            sender_party = find_party_by_identifier(
                conn,
                ctx.session,
                kind=kind,
                value_normalized=value_normalized,
            )

        if sender_party is None:
            # Unresolvable sender: identity_resolution will mint a party
            # asynchronously, but for THIS event we cannot attribute the
            # commitment correctly. Suppress per the audit-trail rule
            # ([REFERENCE_EXAMPLES.md §2 line 1024]).
            await self._emit_suppressed(
                event=event,
                ctx=ctx,
                reason="skill_failure_defensive_default",
                confidence=0.0,
                threshold=float(config.get("min_confidence", 0.55)),
                source_event_id=source_event_id,
            )
            return

        # 2. Receiving member. The receiving member id rides on the
        # event's `to_identifier`; v1 of this pipeline treats it as a
        # member identifier directly. Real routing-owner resolution
        # lands in a future prompt with a router-table seam — for now,
        # if `to_identifier` is empty, suppress.
        receiving_member_id = str(payload.get("to_identifier", ""))
        if not receiving_member_id:
            await self._emit_suppressed(
                event=event,
                ctx=ctx,
                reason="skill_failure_defensive_default",
                confidence=0.0,
                threshold=float(config.get("min_confidence", 0.55)),
                source_event_id=source_event_id,
            )
            return

        # 3. Skip rules — privileged / opposing-counsel / etc. via the
        # event's `party_tags` payload field (an optional array; absent
        # if the inbound adapter doesn't supply tags). Cite [§13]
        # (privacy floors): silent skip is intentional — the audit
        # trail is the absence of any commitment event for this
        # source_event_id.
        skip_tags = set(config.get("skip_party_tags") or [])
        party_tags = set(payload.get("party_tags") or [])
        if skip_tags & party_tags:
            return

        # 4. Resolve thresholds (per-member overrides applied here).
        min_confidence, review_threshold = _thresholds_for_member(
            config, receiving_member_id
        )

        # 5. Classification skill call.
        skill_ctx = SkillContext(
            session=ctx.session,
            correlation_id=ctx.correlation_id,
        )
        message_text = str(payload.get("body_text") or "")
        classify_inputs: dict[str, Any] = {
            "message_text": message_text,
            "sender_party_id": str(sender_party["party_id"]),
            "receiving_member_id": receiving_member_id,
            "thread_context": [],
        }

        try:
            classify_result = await ctx.run_skill_fn(
                CLASSIFY_SKILL_ID, classify_inputs, skill_ctx
            )
        except _SKILL_FAILURE_TYPES as exc:
            _log.warning(
                "commitment_extraction classify failure (%s); emitting "
                "defensive default",
                type(exc).__name__,
            )
            await self._emit_suppressed(
                event=event,
                ctx=ctx,
                reason="skill_failure_defensive_default",
                confidence=0.0,
                threshold=min_confidence,
                source_event_id=source_event_id,
            )
            return

        classify_output = (
            classify_result.output
            if hasattr(classify_result, "output")
            else classify_result
        )
        confidence = float(classify_output.get("confidence", 0.0))
        classify_reasons = list(classify_output.get("reasons", []))

        if confidence < min_confidence:
            await self._emit_suppressed(
                event=event,
                ctx=ctx,
                reason="below_confidence_threshold",
                confidence=confidence,
                threshold=min_confidence,
                source_event_id=source_event_id,
            )
            return

        # 6. Field-extraction skill call.
        extract_inputs: dict[str, Any] = {
            "message_text": message_text,
            "sender_party_id": str(sender_party["party_id"]),
            "receiving_member_id": receiving_member_id,
            "classify_reasons": classify_reasons,
        }

        try:
            extract_result = await ctx.run_skill_fn(
                EXTRACT_SKILL_ID, extract_inputs, skill_ctx
            )
        except _SKILL_FAILURE_TYPES as exc:
            _log.warning(
                "commitment_extraction extract failure (%s); emitting "
                "defensive default",
                type(exc).__name__,
            )
            await self._emit_suppressed(
                event=event,
                ctx=ctx,
                reason="skill_failure_defensive_default",
                confidence=0.0,
                threshold=min_confidence,
                source_event_id=source_event_id,
            )
            return

        extract_output = (
            extract_result.output
            if hasattr(extract_result, "output")
            else extract_result
        )

        # 7. Build & emit commitment.proposed.
        strength = "confident" if confidence >= review_threshold else "weak"
        suggested_due = extract_output.get("suggested_due")
        message_preview = message_text[:240] if message_text else None

        proposed_payload: dict[str, Any] = {
            "commitment_id": _new_commitment_id(),
            "kind": str(extract_output.get("kind", "other")),
            "owed_by_member_id": str(
                extract_output.get("owed_by_member_id") or receiving_member_id
            ),
            "owed_to_party_id": str(
                extract_output.get("owed_to_party_id")
                or sender_party["party_id"]
            ),
            "text_summary": str(extract_output.get("text_summary", ""))[:500]
            or "(no summary extracted)",
            "suggested_due": suggested_due,
            "urgency": str(extract_output.get("urgency", "this_week")),
            "confidence": confidence,
            "strength": strength,
            "source_interaction_id": payload.get("thread_id"),
            "source_message_preview": message_preview,
            "classify_reasons": classify_reasons,
        }

        # TODO(prompt-XX): dedupe against open commitments referencing
        # this thread within dedupe_window_hours; currently always
        # emits. Requires reading the `commitments` projection, which
        # has its own seam considerations beyond this prompt's scope
        # per docs/02-split-memo-10b-ii.md §10b-ii-α.
        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="commitment.proposed",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="commitment_extraction",
            source_account_id="pipeline",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity="normal",
            payload=proposed_payload,
        )
        await ctx.event_log.append(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def _emit_suppressed(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        reason: str,
        confidence: float,
        threshold: float,
        source_event_id: str,
    ) -> None:
        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="commitment.suppressed",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="commitment_extraction",
            source_account_id="pipeline",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity="normal",
            payload={
                "reason": reason,
                "confidence": round(confidence, 4),
                "threshold": round(threshold, 4),
                "source_event_id": source_event_id,
            },
        )
        await ctx.event_log.append(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )
