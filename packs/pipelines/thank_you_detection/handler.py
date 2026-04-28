"""thank_you_detection reactive pipeline.

Per [BUILD.md §1150] and `docs/02-split-memo-10b-ii.md` §10b-ii-β.
Subscribes to inbound messaging only (`messaging.received`); resolves
the sender against the parties projection via
`ctx.parties_conn_factory`, classifies the message via
`classify_thank_you_candidate@^1.3.0` (existing 09b skill, NOT modified
by this prompt), and on a positive classification extracts structured
fields via `extract_thank_you_fields@^1.0.0`. Emits
`commitment.proposed` with `kind="other"` (above review_threshold →
confident; between min and review_threshold → weak) or
`commitment.suppressed` (below min_confidence, on
`is_candidate=False`, on skill failure, on sender resolution failure).

**F-5 carry-forward (10b-ii-α build_log):** unlike
`commitment_extraction`, this pipeline does NOT subscribe to
`messaging.sent`. Thank-yous are extracted from inbound only — what
the household received from someone else. The outbound counterpart
(`messaging.sent`) is what the household themselves sent and carries
no thank-you to extract. Subscribing-and-then-suppressing on outbound
would generate audit-trail noise (a `commitment.suppressed` per
outbound message) so the trigger list is inbound-only.

**`kind="other"` v1 disposition:** `BUILD.md §1150` describes this
pipeline as "specialization of commitment extraction for gratitude"
and uses the phrase "thank_you commitment". Per the 10b-ii-α
build_log carry-forward and the prompt-10b-ii-β default disposition,
this is read as descriptive (a commitment that is a thank-you), not
as requiring a new value in `CommitmentProposedV1.kind`'s Literal.
The kind enum is therefore NOT extended; thank-you commitments emit
with `kind="other"` and a `classify_reasons` array conveying the
thank-you signals. A future Literal-extension migration may revisit
this if downstream consumers need to disambiguate.

**`urgency` vocabulary asymmetry:** `classify_thank_you_candidate`'s
output uses urgency Literal `within_24h | this_week | within_month |
no_rush`, which does NOT match `CommitmentProposedV1.urgency`'s
Literal `today | this_week | this_month | no_rush`. The pipeline does
not coerce/translate the classify-side urgency at this layer; it lets
`extract_thank_you_fields` produce the canonical urgency value, which
round-trips into `CommitmentProposedV1` cleanly. The classify-side
urgency is therefore not even passed through to the extractor — the
extractor sees the same message text and decides for itself.

Defensive-default discipline ([§7.7]): the pipeline catches the seven
declared skill-error types and emits `commitment.suppressed` with
reason ``skill_failure_defensive_default`` rather than letting the
exception propagate up to the runner — which would non-advance the
bus checkpoint and block subsequent events on every classify failure.
The `except` list is identical to `commitment_extraction`'s, including
the F-2 widening (`SkillSensitivityRefused`,
`SkillScopeInsufficient`).

Per [ADR-0002], skill calls go through ``ctx.run_skill_fn`` and never
through provider SDKs.

Per [§7.3] this pipeline NEVER writes a projection row directly — it
emits events and projections consume them. The pipeline opens its
parties-DB connection inside ``handle()`` and closes via context
manager (10a connection-management note); the runner does NOT own
per-pipeline DB connections.

The five inlined helpers (`_classify_identifier`, `_load_config`,
`_thresholds_for_member`, `_new_commitment_id`, `_emit_suppressed`)
are duplicated from ``commitment_extraction/handler.py`` rather than
shared. Per `docs/02-split-memo-10b-ii.md` §10b-ii-β the no-shared-
helper-yet stance holds for two pipelines; a future refactor extracts
them once a third pipeline needs them.
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

CLASSIFY_SKILL_ID = "skill:classify_thank_you_candidate"
CLASSIFY_SKILL_VERSION = "1.3.0"
EXTRACT_SKILL_ID = "skill:extract_thank_you_fields"
EXTRACT_SKILL_VERSION = "1.0.0"

# Defensive-default exception bundle. Identical to
# commitment_extraction's tuple — same five skill-error types plus the
# F-2 widening pair (per docs/build_log.md §"Prompt 10b-i" Carry-forward
# for prompt 10b-ii).
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
    """Derive (kind, value_normalized) from a `messaging.received`
    payload's `from_identifier` per the 10b-i mapping pattern (mirrored
    inline here per the prompt's "no shared helper yet" guidance)."""
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
    ``<instance_dir>/packs/pipelines/thank_you_detection/config.yaml``
    on first run; that copy is the production override. For the in-pack
    default we just read the example."""
    config_path = pack_root / "config.example.yaml"
    with config_path.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise RuntimeError(
            f"thank_you_detection config at {config_path} did not parse "
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


class ThankYouDetectionPipeline:
    pack_id: str = "pipeline:thank_you_detection"
    version: str = "1.0.0"
    triggers: Triggers = {"events": ["messaging.received"]}

    # `_PACK_ROOT` is set at module import for the in-pack default config
    # lookup; tests that want a custom config patch ``self._config_override``
    # before calling ``handle()``.
    _PACK_ROOT = Path(__file__).resolve().parent

    def __init__(self) -> None:
        self._config_override: dict[str, Any] | None = None

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        """Inbound-only thank-you detection.

        F-5 carry-forward: returns silently on any non-`messaging.received`
        event type. The trigger list excludes `messaging.sent` already, so
        this guard is defense-in-depth for any future broader subscription
        change. Outbound messages have no thank-you to extract; routing
        them through the defensive-default suppression path would generate
        audit-trail noise (a suppressed event per outbound message)."""
        event_type = event.get("type", "")
        if event_type != "messaging.received":
            return

        config = self._config_override or _load_config(self._PACK_ROOT)
        payload = event.get("payload") or {}
        source_event_id = str(event.get("event_id", ""))

        # 1. Defense-in-depth: parties_conn_factory missing → suppress.
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
            await self._emit_suppressed(
                event=event,
                ctx=ctx,
                reason="skill_failure_defensive_default",
                confidence=0.0,
                threshold=float(config.get("min_confidence", 0.55)),
                source_event_id=source_event_id,
            )
            return

        # 2. Receiving member.
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

        # 3. Skip rules.
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
                "thank_you_detection classify failure (%s); emitting "
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
        is_candidate = bool(classify_output.get("is_candidate", False))
        confidence = float(classify_output.get("confidence", 0.0))
        classify_reasons = list(classify_output.get("reasons", []))

        # 5a. Two-axis early-suppress: not a candidate OR below threshold.
        # The reason-string overload is intentional — both axes represent
        # the same "this is not a thank-you candidate worth proposing"
        # decision; the audit trail captures the confidence value either
        # way. Note: when is_candidate=False, classify's `urgency` and
        # `suggested_medium` fields are absent per the upstream skill's
        # JSON-Schema if/then — we never read them on this path.
        if not is_candidate or confidence < min_confidence:
            await self._emit_suppressed(
                event=event,
                ctx=ctx,
                reason="below_confidence_threshold",
                confidence=confidence,
                threshold=min_confidence,
                source_event_id=source_event_id,
            )
            return

        # 6. Field-extraction skill call. Note: classify's urgency hint
        # is NOT forwarded — vocabulary mismatch with
        # CommitmentProposedV1.urgency. The extractor sees the same
        # message text and produces the canonical urgency value.
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
                "thank_you_detection extract failure (%s); emitting "
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

        # 7. Build & emit commitment.proposed with kind="other".
        strength = "confident" if confidence >= review_threshold else "weak"
        message_preview = message_text[:240] if message_text else None
        suggested_text = str(extract_output.get("suggested_text", ""))[:500]

        proposed_payload: dict[str, Any] = {
            "commitment_id": _new_commitment_id(),
            "kind": "other",
            "owed_by_member_id": receiving_member_id,
            "owed_to_party_id": str(
                extract_output.get("recipient_party_id")
                or sender_party["party_id"]
            ),
            "text_summary": suggested_text or "(no summary extracted)",
            "urgency": str(extract_output.get("urgency", "this_week")),
            "confidence": confidence,
            "strength": strength,
            "source_interaction_id": payload.get("thread_id"),
            "source_message_preview": message_preview,
            "classify_reasons": classify_reasons,
        }

        # TODO(prompt-XX): dedupe against open thank-you commitments
        # referencing this thread within dedupe_window_hours; currently
        # always emits. Requires reading the `commitments` projection,
        # which has its own seam considerations beyond this prompt's
        # scope per docs/02-split-memo-10b-ii.md §10b-ii-β.
        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="commitment.proposed",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="thank_you_detection",
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
            source_adapter="thank_you_detection",
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
