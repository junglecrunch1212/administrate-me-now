"""noise_filtering reactive pipeline.

Per [BUILD.md §1132-1138] and [§7.4] / [§8] / [D6]. Calls the
``classify_message_nature`` skill once per inbound messaging event,
emits a ``messaging.classified`` event with the classification +
confidence + skill provenance. Downstream (a future ``interactions``
projection update) decides whether to suppress the row.

Defensive-default discipline ([§7.7]): if the skill raises (timeout,
unreachable, input/output schema reject), the pipeline emits a
classified event with classification=``personal`` / confidence 0.0
rather than letting the exception propagate up to the runner — which
would non-advance the bus checkpoint and block subsequent events on
every classification failure. Mis-classifying a real message as noise
(and dropping it from authorized interactions) is a worse failure than mis-classifying
noise as personal.

Per [ADR-0002], skill calls go through ``ctx.run_skill_fn`` (bound to
``adminme.lib.skill_runner.run_skill``) and never through provider
SDKs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import weakref
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from adminme.candidate_policy import (
    default_policy,
    filter_candidates,
    source_id_for_event,
)
from adminme.events.envelope import EventEnvelope
from adminme.events.log import IdempotencyConflict
from adminme.events.schemas.triage import (
    TriageIntentFeaturesExtractedV3,
    TriageSourcePublicationPlannedV1,
    TriageSourcePublicationCompletedV1,
)
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
from adminme.message_context import MessageContextPack, build_message_context_pack
from adminme.pipelines.base import PipelineContext, Triggers, skill_output
from adminme.source_publication import (
    publication_completion_valid,
    source_assessment_identity_valid,
    publication_plan_key,
    publication_completion_key,
)
from adminme.source_assessment_recovery import (
    RECOVERY_ATTEMPT_LIMIT,
    RECOVERY_MAX_CONCURRENCY,
    RECOVERY_POLICY,
    RecoveryRefusalReason,
    SourceAssessmentRecoveryRefused,
    resolved_assessment_disposition,
    latest_source_assessment,
    recovery_assessment_idempotency_key,
    recovery_refusal_idempotency_key,
    target_from_request_event,
    validate_recovery_target,
)
from adminme.source_contract_recovery import (
    ASSESSMENT_CONTRACT,
    CONTRACT_ATTEMPT_TYPE,
    CONTRACT_RECOVERY_POLICY,
    CONTRACT_REQUEST_TYPE,
    CONTRACT_REFUSAL_TYPE,
    CONTRACT_RESULT_TYPE,
    AmbiguousContractAttempt,
    ContractRecoveryRefused,
    ContractRecoveryTarget,
    contract_recovery_requirement,
    contract_recovery_terminal,
    contract_refusal_key,
    contract_result_key,
    current_contract_result,
    deterministic_contract_terminal,
    validate_contract_language_receipt,
    validate_contract_recovery_target,
)
from adminme.triage_intent import (
    CONTEXTUAL_NLP_EXTRACTOR_ID,
    GROUNDED_NLP_EXTRACTOR_ID,
    MESSAGE_FALLBACK_EXTRACTOR_ID,
    extract_grounded_nlp_candidates,
    extract_grounded_context_candidates,
    extract_message_fallback_candidates,
    is_inbound_reply_request,
    merge_intent_candidates,
)

_log = logging.getLogger(__name__)

SKILL_ID = "skill:classify_message_nature"
SKILL_NAME = "classify_message_nature"
SKILL_VERSION = "8.0.1"
_RETAINED_CLASSIFICATION_VERSION = "8.0.0"
DEFAULT_CLASSIFICATION = "personal"
_REPLAY_GENERATION = 23
_DEFERRED_THREAD_ASSESSOR_ID = "thread-anchor-deferred-v1"
_CURRENT_MESSAGE_FALLBACK_RULE_IDS = frozenset(
    {
        "inbound_explicit_directive_or_due_obligation_v1",
        "outbound_first_person_promise_v1",
    }
)

_SKILL_FAILURE_TYPES: tuple[type[BaseException], ...] = (
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillSensitivityRefused,
    SkillScopeInsufficient,
    SkillHandlerError,
    OpenClawTimeout,
    OpenClawUnreachable,
    OpenClawResponseMalformed,
    # The language seam is non-authoritative.  A configuration or transport
    # RuntimeError must not poison durable message ingestion; deterministic
    # extraction and a defensive classification can still proceed safely.
    RuntimeError,
)

_TRANSIENT_SKILL_FAILURE_TYPES = (OpenClawTimeout, OpenClawUnreachable)
_TRANSIENT_RECOVERY_DELAYS_SECONDS = (0.5, 2.0)


class _ProposalInputHistory:
    def __init__(self) -> None:
        self.cursor: str | None = None
        self.by_source_event_id: dict[str, list[dict[str, Any]]] = {}
        self.lock = asyncio.Lock()


def _versioned_idempotency_keys(prefix: str, source_event_id: str) -> list[str]:
    """Return the historical baseline plus reviewed replay generations."""

    return [
        f"{prefix}:{source_event_id}",
        *(
            f"{prefix}:v{generation}:{source_event_id}"
            for generation in range(4, _REPLAY_GENERATION + 1)
        ),
    ]


async def _select_payload_idempotency_key(
    *,
    ctx: PipelineContext,
    tenant_id: str,
    prefix: str,
    source_event_id: str,
    payload: dict[str, Any],
) -> str:
    """Reuse byte-identical history or append at the current replay boundary."""

    first_unused: str | None = None
    for key in _versioned_idempotency_keys(prefix, source_event_id):
        prior = await ctx.event_log.get_by_idempotency(tenant_id, key)
        if prior is None:
            first_unused = first_unused or key
        elif prior.get("payload") == payload:
            return key
    if first_unused is not None:
        return first_unused
    raise RuntimeError(
        f"{prefix} exhausted reviewed replay generations for {source_event_id}"
    )


async def _write_first_equivalent_event(
    *,
    ctx: PipelineContext,
    envelope: EventEnvelope,
    correlation_id: str | None,
    causation_id: str | None,
    ignored_payload_fields: frozenset[str],
) -> str:
    """Make deliberate cross-source deduplication safe under concurrency.

    A read-before-append check is useful for avoiding work but cannot be the
    correctness boundary: two context-linked sources can observe the same
    absent key before either append commits.  The event log remains the atomic
    arbiter.  If another writer wins, accept only an equivalent domain event;
    unrelated reuse of the key remains a hard ``IdempotencyConflict``.
    """

    try:
        return await ctx.event_sink.write(
            envelope,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
    except IdempotencyConflict:
        key = envelope.idempotency_key
        existing = (
            await ctx.event_log.get_by_idempotency(envelope.tenant_id, key)
            if key is not None
            else None
        )
        if existing is None:
            raise
        stable_envelope = {
            "type": envelope.type,
            "schema_version": envelope.schema_version,
            "owner_scope": envelope.owner_scope,
            "visibility_scope": envelope.visibility_scope,
            "sensitivity": envelope.sensitivity,
            "source_adapter": envelope.source_adapter,
            "source_account_id": envelope.source_account_id,
        }
        if any(existing.get(name) != value for name, value in stable_envelope.items()):
            raise
        existing_payload = {
            name: value
            for name, value in (existing.get("payload") or {}).items()
            if name not in ignored_payload_fields
        }
        incoming_payload = {
            name: value
            for name, value in envelope.payload.items()
            if name not in ignored_payload_fields
        }
        if existing_payload != incoming_payload:
            raise
        return str(existing["event_id"])


def _extract_text_and_inputs(
    event_type: str,
    payload: dict[str, Any],
    *,
    canonical_body_text: str | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Build the skill input payload, or return None if there's nothing
    to classify (empty body)."""
    outbound_message = False
    if event_type == "telephony.sms_received":
        text = (
            canonical_body_text
            if canonical_body_text is not None
            else str(payload.get("body") or "").strip()
        )
        from_id = str(payload.get("from_number", ""))
        source_channel = "sms"
    else:
        text = (
            canonical_body_text
            if canonical_body_text is not None
            else str(payload.get("body_text") or "").strip()
        )
        provider_labels = {
            str(label).upper() for label in payload.get("provider_labels") or []
        }
        outbound_message = event_type == "messaging.sent" or "SENT" in provider_labels
        from_id = str(
            payload.get("to_identifier" if outbound_message else "from_identifier", "")
        )
        source_channel = str(payload.get("source_channel", ""))
    if not text:
        return None
    inputs: dict[str, Any] = {
        "body_text": text,
        "source_channel": source_channel,
        "from_identifier": from_id,
        "counterparty_identifier": from_id,
        "message_direction": "outbound" if outbound_message else "inbound",
        # Conservative — the runner does not yet expose the parties DB
        # to the pipeline (see identity_resolution module docstring), so
        # we cannot cheaply check whether the sender is resolved. The
        # skill prompt is robust to this hint being conservative.
        "from_party_known": False,
    }
    subject = payload.get("subject")
    if subject:
        inputs["subject"] = str(subject)
    grounded_source = f"Subject: {subject}\n\n{text}" if subject else text
    return grounded_source, inputs


def _deduplicate_candidate_keys(candidates: list[Any]) -> list[Any]:
    """Collapse one recorded interpretation to one typed candidate identity.

    Candidate keys are model-output aliases validated by fixed code. If one
    recorded result repeats an alias for the same target kind, its first
    occurrence is the canonical review candidate. Confidence never ranks or
    authorizes the choice; recorded order is stable on replay.
    """

    deduplicated: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        identity = (str(candidate.target_kind), str(candidate.candidate_key))
        if identity in seen:
            continue
        seen.add(identity)
        deduplicated.append(candidate)
    return deduplicated


class NoiseFilteringPipeline:
    pack_id: str = "pipeline:noise_filtering"
    version: str = "23.0.0"
    triggers: Triggers = {
        "events": [
            "messaging.received",
            "messaging.sent",
            "telephony.sms_received",
            "source.assessment.recovery_requested",
            CONTRACT_REQUEST_TYPE,
            "triage.source_publication.planned",
        ]
    }

    def __init__(self) -> None:
        # Explicit operator recovery keeps its separate reviewed concurrency.
        self._recovery_request_semaphore = asyncio.Semaphore(RECOVERY_MAX_CONCURRENCY)
        # One immutable source may be named by distinct command ids. Serialize
        # selection through terminal append so only the first request can
        # supersede the exact failed assessment selected by both commands.
        self._recovery_source_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._proposal_histories: weakref.WeakKeyDictionary[
            Any, dict[str, _ProposalInputHistory]
        ] = weakref.WeakKeyDictionary()

    async def _proposals_for_sources(
        self,
        *,
        event_log: Any,
        owner_scope: str,
        source_event_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Read each immutable proposal once, then serve exact source buckets."""

        try:
            owner_histories = self._proposal_histories.setdefault(event_log, {})
        except TypeError:
            return [
                proposal
                async for proposal in event_log.read_since(
                    owner_scope=owner_scope,
                    types=["triage.item.proposed"],
                )
                if str((proposal.get("payload") or {}).get("source_event_id") or "")
                in source_event_ids
            ]
        history = owner_histories.setdefault(owner_scope, _ProposalInputHistory())
        async with history.lock:
            suffix = [
                proposal
                async for proposal in event_log.read_since(
                    cursor=history.cursor,
                    owner_scope=owner_scope,
                    types=["triage.item.proposed"],
                )
            ]
            for proposal in suffix:
                source_event_id = str(
                    (proposal.get("payload") or {}).get("source_event_id") or ""
                )
                if source_event_id:
                    history.by_source_event_id.setdefault(source_event_id, []).append(
                        proposal
                    )
            if suffix:
                history.cursor = str(suffix[-1]["event_id"])
            return [
                proposal
                for source_event_id in source_event_ids
                for proposal in history.by_source_event_id.get(source_event_id, ())
            ]

    @staticmethod
    def _recovery_assessment_key(request_event_id: str, source_event_id: str) -> str:
        return recovery_assessment_idempotency_key(request_event_id, source_event_id)

    @staticmethod
    def _recovery_refusal_key(request_event_id: str) -> str:
        return recovery_refusal_idempotency_key(request_event_id)

    @staticmethod
    def _derived_event_at_ms(event: dict[str, Any]) -> int:
        """Place a derived append after its command even after a cold reopen."""

        return max(
            int(datetime.now(timezone.utc).timestamp() * 1000),
            int(event.get("event_at_ms") or 0) + 1,
        )

    async def _emit_recovery_refusal(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        reason: RecoveryRefusalReason,
    ) -> None:
        payload = event.get("payload") or {}
        request_event_id = str(event.get("event_id") or "")
        source_event_id = str(payload.get("source_event_id") or "")
        await ctx.event_sink.write(
            EventEnvelope(
                event_at_ms=self._derived_event_at_ms(event),
                tenant_id=event["tenant_id"],
                type="source.assessment.recovery_refused",
                schema_version=1,
                occurred_at=str(
                    event.get("recorded_at")
                    or event.get("occurred_at")
                    or EventEnvelope.now_utc_iso()
                ),
                source_adapter="noise_filtering",
                source_account_id="pipeline",
                owner_scope=str(event.get("owner_scope") or "system"),
                visibility_scope=str(
                    event.get("visibility_scope")
                    or event.get("owner_scope")
                    or "system"
                ),
                sensitivity="normal",
                idempotency_key=self._recovery_refusal_key(request_event_id),
                payload={
                    "request_event_id": request_event_id,
                    "source_event_id": source_event_id,
                    "reason": reason,
                    "policy": RECOVERY_POLICY,
                },
            ),
            correlation_id=ctx.correlation_id,
            causation_id=request_event_id,
        )

    async def _handle_recovery_request(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        payload = event.get("payload") or {}
        source_event_id = str(payload.get("source_event_id") or "")
        lock_key = f"{event.get('tenant_id')}:{source_event_id}"
        source_lock = self._recovery_source_locks.setdefault(lock_key, asyncio.Lock())
        async with source_lock:
            await self._handle_recovery_request_locked(event, ctx)

    async def _handle_recovery_request_locked(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        payload = event.get("payload") or {}
        request_event_id = str(event.get("event_id") or "")
        source_event_id = str(payload.get("source_event_id") or "")
        tenant_id = str(event.get("tenant_id") or "")
        assessment = await ctx.event_log.get_by_idempotency(
            tenant_id,
            self._recovery_assessment_key(request_event_id, source_event_id),
        )
        refusal = await ctx.event_log.get_by_idempotency(
            tenant_id,
            self._recovery_refusal_key(request_event_id),
        )
        if assessment is not None:
            source = await ctx.event_log.get(source_event_id)
            if (
                source is not None
                and event.get("source_adapter") == "source_assessment_recovery"
                and tenant_id == ctx.session.tenant_id
                and payload.get("policy") == RECOVERY_POLICY
                and payload.get("attempt_limit") == RECOVERY_ATTEMPT_LIMIT
                and payload.get("max_concurrency") == RECOVERY_MAX_CONCURRENCY
                and all(
                    event.get(field) == source.get(field)
                    for field in ("tenant_id", "owner_scope", "visibility_scope")
                )
                and assessment.get("payload", {}).get("recovery_request_event_id")
                == request_event_id
            ):
                latest = await latest_source_assessment(ctx.event_log, source_event_id)
                if latest is not None and latest["event_id"] == assessment["event_id"]:
                    await self._resume_source_publication(source, ctx, assessment)
            return
        if refusal is not None:
            return
        try:
            if (
                event.get("source_adapter") != "source_assessment_recovery"
                or payload.get("policy") != RECOVERY_POLICY
                or payload.get("attempt_limit") != RECOVERY_ATTEMPT_LIMIT
                or payload.get("max_concurrency") != RECOVERY_MAX_CONCURRENCY
            ):
                raise SourceAssessmentRecoveryRefused("request_scope_mismatch")
            if tenant_id != ctx.session.tenant_id:
                raise SourceAssessmentRecoveryRefused("tenant_mismatch")
            if (
                ctx.observation_manager is None
                or not await ctx.observation_manager.is_active()
            ):
                raise SourceAssessmentRecoveryRefused("observation_inactive")
            target = target_from_request_event(event)
            selected = await validate_recovery_target(
                ctx.event_log,
                target,
                required_owner_scope=str(event.get("owner_scope") or ""),
                required_tenant_id=tenant_id,
            )
            if payload.get("owner_member_id") != selected.owner_member_id:
                raise SourceAssessmentRecoveryRefused("request_scope_mismatch")
        except SourceAssessmentRecoveryRefused as exc:
            await self._emit_recovery_refusal(
                event=event,
                ctx=ctx,
                reason=exc.code,
            )
            return

        source = dict(selected.source)
        # Recovery can be the first writer after EventLog reopens. Its ID
        # generator is intentionally process-local, so reusing the retained
        # source's old append timestamp could mint an event behind the durable
        # request/checkpoint (or collide with an old ID). Recovery operates on
        # a copy and gives every derived append a position strictly after the
        # request while preserving the source's immutable occurred_at/payload.
        source["event_at_ms"] = self._derived_event_at_ms(event)
        source["_assessment_recovery"] = {
            "request_event_id": request_event_id,
            "failed_assessment_event_id": selected.failed_assessment["event_id"],
            "failure_reason": selected.failure_reason,
        }
        async with self._recovery_request_semaphore:
            # The recovery command already owns this source's publication lock.
            await self._handle_source(source, ctx)

    async def _run_skill_with_transient_recovery(
        self,
        ctx: PipelineContext,
        inputs: dict[str, Any],
        skill_ctx: SkillContext,
        contract_request: dict[str, Any] | None = None,
    ) -> Any:
        try:
            return await self._run_reserved_contract_attempt(
                ctx, inputs, skill_ctx, contract_request, 0
            )
        except _TRANSIENT_SKILL_FAILURE_TYPES as exc:
            _log.warning(
                "noise_filtering transient skill failure (%s); "
                "queueing bounded recovery",
                type(exc).__name__,
            )
            invocation_identity = skill_ctx.invocation_idempotency_key
            if not invocation_identity:
                # The production composition always supplies the private
                # message fingerprint. Without it, a second provider attempt
                # could not be durably bounded or replayed.
                raise
            # Each source keeps its existing EventBus delivery slot through
            # both retries. A shared retry lock would serialize eight failing
            # sources and starve the batch's ordered checkpoint. There are
            # still at most eight calls, and at most three calls per source.
            for attempt, delay_seconds in enumerate(
                _TRANSIENT_RECOVERY_DELAYS_SECONDS, start=1
            ):
                recovery_ctx = replace(
                    skill_ctx,
                    invocation_idempotency_key=(
                        f"{invocation_identity}:recovery-{attempt}"
                    ),
                    retry_prior_failure=True,
                )
                await asyncio.sleep(delay_seconds)
                try:
                    return await self._run_reserved_contract_attempt(
                        ctx, inputs, recovery_ctx, contract_request, attempt
                    )
                except _TRANSIENT_SKILL_FAILURE_TYPES as recovery_exc:
                    if attempt == len(_TRANSIENT_RECOVERY_DELAYS_SECONDS):
                        raise
                    _log.warning(
                        "noise_filtering recovery attempt %s failed (%s); "
                        "queueing final recovery",
                        attempt,
                        type(recovery_exc).__name__,
                    )

            raise AssertionError("unreachable transient recovery state")

    async def _run_reserved_contract_attempt(
        self,
        ctx: PipelineContext,
        inputs: dict[str, Any],
        skill_ctx: SkillContext,
        request: dict[str, Any] | None,
        slot: int,
    ) -> Any:
        if request is None:
            return await ctx.run_skill_fn(SKILL_ID, inputs, skill_ctx)
        source_id = request["payload"]["source_event_id"]
        request_id = str(request["event_id"])
        identity = str(skill_ctx.invocation_idempotency_key)
        reservation_key = f"source-assessment-contract-attempt:{request_id}:{slot}"
        reservation = await ctx.event_log.get_by_idempotency(
            request["tenant_id"], reservation_key
        )
        if reservation is not None:
            if (
                reservation.get("type") != CONTRACT_ATTEMPT_TYPE
                or reservation.get("schema_version") != 1
                or reservation.get("source_adapter")
                != "deterministic_candidate_eligibility"
                or reservation.get("source_account_id") != "pipeline"
                or reservation.get("causation_id") != source_id
                or reservation["event_id"] <= request_id
                or any(
                    reservation.get(field) != request.get(field)
                    for field in (
                        "tenant_id",
                        "owner_scope",
                        "visibility_scope",
                        "sensitivity",
                    )
                )
                or reservation.get("payload")
                != {
                    "request_event_id": request_id,
                    "source_event_id": source_id,
                    "required_assessment_contract": ASSESSMENT_CONTRACT,
                    "invocation_identity": identity,
                    "classifier_version": SKILL_VERSION,
                    "slot": slot,
                }
            ):
                raise AmbiguousContractAttempt(
                    "contract recovery inputs changed after reservation"
                )
            terminals = await ctx.event_log.get_by_causation(source_id)
            if not any(
                event.get("source_adapter") == "skill_runner"
                and event.get("source_account_id") == "system"
                and event["event_id"] > reservation["event_id"]
                and event.get("payload", {}).get("skill_name") == SKILL_ID
                and event.get("payload", {}).get("skill_version") == SKILL_VERSION
                and all(
                    event.get(field) == request.get(field)
                    for field in (
                        "tenant_id",
                        "owner_scope",
                        "visibility_scope",
                        "sensitivity",
                    )
                )
                and (
                    event.get("type") == "skill.call.recorded"
                    and event.get("schema_version") == 2
                    and event.get("idempotency_key")
                    == f"skill:{identity}:{SKILL_ID}:{SKILL_VERSION}"
                    or event.get("type") == "skill.call.failed"
                    and event.get("schema_version") == 1
                    and str(event.get("idempotency_key") or "").startswith(
                        f"skill:failed:{identity}:{SKILL_ID}:{SKILL_VERSION}:"
                    )
                )
                for event in terminals
            ):
                raise AmbiguousContractAttempt(
                    "reserved contract attempt has no terminal receipt"
                )
        else:
            await ctx.event_sink.write(
                EventEnvelope(
                    event_at_ms=self._derived_event_at_ms(request),
                    tenant_id=request["tenant_id"],
                    type=CONTRACT_ATTEMPT_TYPE,
                    schema_version=1,
                    occurred_at=EventEnvelope.now_utc_iso(),
                    source_adapter="deterministic_candidate_eligibility",
                    source_account_id="pipeline",
                    owner_scope=request["owner_scope"],
                    visibility_scope=request["visibility_scope"],
                    sensitivity=request["sensitivity"],
                    idempotency_key=reservation_key,
                    payload={
                        "request_event_id": request_id,
                        "source_event_id": source_id,
                        "required_assessment_contract": ASSESSMENT_CONTRACT,
                        "invocation_identity": identity,
                        "classifier_version": SKILL_VERSION,
                        "slot": slot,
                    },
                ),
                causation_id=source_id,
                correlation_id=ctx.correlation_id,
            )
        return await ctx.run_skill_fn(SKILL_ID, inputs, skill_ctx)

    async def _handle_contract_recovery_request(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        payload = event.get("payload") or {}
        source_id = str(payload.get("source_event_id") or "")
        lock = self._recovery_source_locks.setdefault(
            f"{event.get('tenant_id')}:{source_id}", asyncio.Lock()
        )
        async with lock:
            canonical = await ctx.event_log.get(str(event.get("event_id") or ""))
            if canonical != event:
                raise RuntimeError(
                    "contract recovery command is not its canonical envelope"
                )
            terminal = await contract_recovery_terminal(
                ctx.event_log, str(event["event_id"])
            )
            if terminal["status"] != "pending":
                if terminal.get("assessment_event_id"):
                    source = await ctx.event_log.get(source_id)
                    assessment = await ctx.event_log.get(
                        terminal["assessment_event_id"]
                    )
                    if source is None or assessment is None:
                        raise RuntimeError(
                            "contract recovery terminal source or assessment is unavailable"
                        )
                    await self._resume_source_publication(source, ctx, assessment)
                return
            try:
                if (
                    event.get("source_adapter") != "source_assessment_contract_recovery"
                    or event.get("tenant_id") != ctx.session.tenant_id
                    or payload.get("policy") != CONTRACT_RECOVERY_POLICY
                    or payload.get("required_assessment_contract")
                    != ASSESSMENT_CONTRACT
                    or payload.get("attempt_limit") != RECOVERY_ATTEMPT_LIMIT
                    or payload.get("max_concurrency") != RECOVERY_MAX_CONCURRENCY
                ):
                    raise ContractRecoveryRefused("request_scope_mismatch")
                if (
                    ctx.observation_manager is None
                    or not await ctx.observation_manager.is_active()
                ):
                    raise ContractRecoveryRefused("observation_inactive")
                target = ContractRecoveryTarget.model_validate(
                    {key: payload[key] for key in ContractRecoveryTarget.model_fields}
                )
                selected = await validate_contract_recovery_target(
                    ctx.event_log,
                    target,
                    required_tenant_id=ctx.session.tenant_id,
                    required_owner_scope=str(event.get("owner_scope") or ""),
                )
            except ContractRecoveryRefused as exc:
                await ctx.event_sink.write(
                    EventEnvelope(
                        event_at_ms=self._derived_event_at_ms(event),
                        tenant_id=event["tenant_id"],
                        type=CONTRACT_REFUSAL_TYPE,
                        schema_version=1,
                        occurred_at=EventEnvelope.now_utc_iso(),
                        source_adapter="noise_filtering",
                        source_account_id="pipeline",
                        owner_scope=event["owner_scope"],
                        visibility_scope=event["visibility_scope"],
                        sensitivity=event["sensitivity"],
                        idempotency_key=contract_refusal_key(str(event["event_id"])),
                        payload={
                            "request_event_id": event["event_id"],
                            "source_event_id": source_id,
                            "reason": exc.code,
                            "policy": CONTRACT_RECOVERY_POLICY,
                        },
                    ),
                    correlation_id=ctx.correlation_id,
                    causation_id=event["event_id"],
                )
                return
            source = dict(selected.source)
            source["event_at_ms"] = self._derived_event_at_ms(event)
            source["_assessment_recovery"] = {
                "request_event_id": event["event_id"],
                # The compatible v4 receipt supersedes the selected current
                # assessment; the v2 command separately retains the exact failure.
                "failed_assessment_event_id": selected.latest_assessment["event_id"],
                "failure_reason": selected.failure_reason,
                "contract_request": event,
            }
            async with self._recovery_request_semaphore:
                await self._handle_source(source, ctx)

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        event_type = event.get("type", "")
        if event_type == CONTRACT_REQUEST_TYPE:
            await self._handle_contract_recovery_request(event, ctx)
            return
        if event_type == "triage.source_publication.planned":
            payload = TriageSourcePublicationPlannedV1.model_validate(event["payload"])
            source = await ctx.event_log.get(payload.source_event_id)
            assessment = await ctx.event_log.get_by_idempotency(
                event["tenant_id"], payload.assessment_idempotency_key
            )
            if source is None or assessment is None:
                raise RuntimeError("source publication lost its atomic assessment")
            lock_key = f"{event.get('tenant_id')}:{payload.source_event_id}"
            source_lock = self._recovery_source_locks.setdefault(
                lock_key, asyncio.Lock()
            )
            async with source_lock:
                await self._resume_source_publication(
                    source, ctx, assessment, expected_plan=event
                )
            return
        if event_type == "source.assessment.recovery_requested":
            await self._handle_recovery_request(event, ctx)
            return
        if event_type not in (
            "messaging.received",
            "messaging.sent",
            "telephony.sms_received",
        ):
            return
        lock_key = f"{event.get('tenant_id')}:{event.get('event_id')}"
        source_lock = self._recovery_source_locks.setdefault(lock_key, asyncio.Lock())
        async with source_lock:
            await self._handle_source(event, ctx)

    async def _reuse_current_negative_assessment(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> bool:
        """An occupied current negative is immutable when thread context grows."""
        if not hasattr(ctx.event_log, "get_by_idempotency"):
            return False
        source_id = str(event.get("event_id") or "")
        assessment = await ctx.event_log.get_by_idempotency(
            event["tenant_id"],
            f"triage:message-features:v{_REPLAY_GENERATION}:{source_id}",
        )
        if (
            assessment is None
            or (assessment.get("payload") or {}).get("assessment_outcome")
            != "no_candidate"
        ):
            return False
        owner = str(event.get("owner_scope") or "")
        if (
            assessment.get("schema_version") != 3
            or not source_assessment_identity_valid(source=event, assessment=assessment)
            or not owner.startswith("private:")
            or not owner.removeprefix("private:")
            or event.get("sensitivity") not in {"normal", "sensitive"}
            or (event.get("payload") or {}).get(
                "member_id", owner.removeprefix("private:")
            )
            != owner.removeprefix("private:")
        ):
            raise RuntimeError(
                "retained current negative assessment identity is invalid"
            )
        try:
            payload = TriageIntentFeaturesExtractedV3.model_validate(
                assessment.get("payload")
            )
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "retained current negative assessment payload is invalid"
            ) from exc
        outcome, _reason = await resolved_assessment_disposition(
            ctx.event_log, assessment, source_event=event
        )
        if outcome == "failed":
            # A recorded defensive default cannot become a clean result merely
            # because its legacy receipt used a successful-looking outcome.
            return True
        if (
            payload.extraction_state != "complete"
            or payload.candidates
            or outcome != "no_candidate"
        ):
            raise RuntimeError("retained current negative assessment is not complete")
        return True

    async def _handle_source(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        event_type = event.get("type", "")
        source_event_id = str(event.get("event_id") or "")
        recovery = event.get("_assessment_recovery")
        if not isinstance(recovery, dict) and hasattr(
            ctx.event_log, "get_by_causation"
        ):
            prior_assessment = await latest_source_assessment(
                ctx.event_log, source_event_id
            )
            # CO-017 failures require the exact, bounded recovery command.
            # A later full consumer replay must not silently consume that
            # authority or make the selected failed assessment stale.
            if prior_assessment is not None:
                if (
                    await current_contract_result(
                        ctx.event_log, event, prior_assessment
                    )
                    is not None
                ):
                    await self._resume_source_publication(event, ctx, prior_assessment)
                    return
                if (
                    await contract_recovery_requirement(
                        ctx.event_log, event, prior_assessment
                    )
                    is not None
                ):
                    # A legacy recovery cannot fulfill this attempted current
                    # contract. Neither replay nor a generation bump renews it.
                    return
                if await self._resume_source_publication(event, ctx, prior_assessment):
                    return
                prior_outcome, _ = await resolved_assessment_disposition(
                    ctx.event_log, prior_assessment, source_event=event
                )
                if prior_outcome == "failed":
                    return
            if await self._reuse_current_negative_assessment(event, ctx):
                return
        if event.get("sensitivity") == "privileged":
            await self._emit_triage_assessment(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                candidates=[],
                outcome="quarantined",
                reason_codes=["privileged_source_not_sent_to_language_seam"],
                recovery=recovery if isinstance(recovery, dict) else None,
            )
            return

        payload = event.get("payload") or {}
        owner_scope = str(event.get("owner_scope") or "")
        if not owner_scope.startswith("private:"):
            await self._emit_triage_assessment(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                candidates=[],
                outcome="quarantined",
                reason_codes=["source_scope_is_not_private_member"],
                recovery=recovery if isinstance(recovery, dict) else None,
            )
            return
        member_id = owner_scope.removeprefix("private:")
        payload_member = payload.get("member_id")
        if payload_member is not None and payload_member != member_id:
            await self._emit_triage_assessment(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                candidates=[],
                outcome="quarantined",
                reason_codes=["payload_member_scope_mismatch"],
                recovery=recovery if isinstance(recovery, dict) else None,
            )
            return
        if not member_id:
            await self._emit_triage_assessment(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                candidates=[],
                outcome="quarantined",
                reason_codes=["source_member_missing"],
                recovery=recovery if isinstance(recovery, dict) else None,
            )
            return
        policy_source_id = source_id_for_event(event) or "adapter:gmail"
        policy = (
            ctx.candidate_policy_fn(member_id, policy_source_id)
            if ctx.candidate_policy_fn is not None
            else default_policy(policy_source_id)
        )
        context_pack = await build_message_context_pack(event, ctx.event_log)
        current_context_message = next(
            (
                message
                for message in context_pack.messages
                if message.source_event_id == source_event_id
            ),
            None,
        )
        if current_context_message is None:
            await self._emit_triage_assessment(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                candidates=[],
                outcome="quarantined",
                reason_codes=["context_pack_missing_current_source"],
                recovery=recovery if isinstance(recovery, dict) else None,
            )
            return
        if not current_context_message.authored_text_reliable:
            await self._emit_triage_assessment(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                candidates=[],
                outcome="no_candidate",
                reason_codes=list(
                    current_context_message.authored_text_reason_codes
                    or ("authored_text_uncertain",)
                ),
                recovery=recovery if isinstance(recovery, dict) else None,
            )
            await self._supersede_unreliable_source_proposals(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
            )
            return
        prepared = _extract_text_and_inputs(
            event_type,
            payload,
            canonical_body_text=current_context_message.body_text,
        )
        if prepared is None:
            await self._emit_triage_assessment(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                candidates=[],
                outcome="no_candidate",
                reason_codes=["source_has_no_authored_text"],
                recovery=recovery if isinstance(recovery, dict) else None,
            )
            return
        source_text, inputs = prepared
        # Candidate fallback, model input, admission, and read-time evidence
        # all use the same authored-text v2 representation.
        source_text = current_context_message.source_text()
        if not context_pack.current_is_thread_anchor:
            policy_rule = (
                f"candidate_policy_v1:{policy_source_id}:{policy.get('mode', 'review')}"
            )
            policy_reason = (
                "candidate_policy_manual_only"
                if policy.get("mode") == "manual"
                else "candidate_policy_quiet"
                if policy.get("mode") == "quiet"
                else "candidate_language_disabled"
                if not policy.get("language_enabled")
                else None
            )
            await self._emit_triage_assessment(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                candidates=[],
                outcome="no_candidate",
                reason_codes=[
                    "language_assessment_deferred_to_latest_thread_message",
                    "no_eligible_candidate",
                    "deferred_to_latest_thread_message",
                    *([policy_reason] if policy_reason else []),
                    policy_rule,
                ],
                extractor_id=_DEFERRED_THREAD_ASSESSOR_ID,
                recovery=recovery if isinstance(recovery, dict) else None,
            )
            return
        inputs["body_text"] = current_context_message.body_text
        inputs["candidate_mode"] = policy["mode"]
        inputs["candidate_kinds"] = list(policy["candidate_kinds"])
        inputs["candidate_language_enabled"] = bool(policy["language_enabled"])
        inputs["context_pack"] = context_pack.skill_payload()
        inputs["assessment_contract"] = "personal-obligation-assessment-v1"
        inputs["candidate_scope"] = (
            "primary_thread"
            if context_pack.current_is_thread_anchor
            else "deferred_to_latest_thread_message"
        )

        invocation_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "tenant_id": event.get("tenant_id"),
                    "owner_scope": event.get("owner_scope"),
                    "visibility_scope": event.get("visibility_scope"),
                    "sensitivity": event.get("sensitivity", "normal"),
                    "source_adapter": event.get("source_adapter"),
                    "source_account_id": event.get("source_account_id"),
                    "event_type": event_type,
                    "source_at": payload.get("received_at") or payload.get("sent_at"),
                    "native_message_id": payload.get("native_message_id")
                    or payload.get("carrier_message_id")
                    or payload.get("rfc_message_id"),
                    "thread_id": payload.get("thread_id"),
                    "inputs": inputs,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        invocation_identity = f"message-nature:{invocation_fingerprint}"
        retry_prior_failure = True
        if isinstance(recovery, dict):
            invocation_identity = (
                "message-nature:recovery:"
                f"{recovery['request_event_id']}:{invocation_fingerprint}"
            )
            # The selected v3 failure shares the immutable source causation.
            # The wrapper may reuse success, but must bypass that older
            # terminal failure once for this request-bound invocation key.
            retry_prior_failure = True
        skill_ctx = SkillContext(
            session=ctx.session,
            correlation_id=ctx.correlation_id,
            triggering_event_id=str(source_event_id),
            # Preserve any successful exact-fingerprint interpretation from
            # an earlier replay while permitting one reviewed retry of an old
            # terminal failure. The wrapper records that retry against this
            # fingerprint, so replay cannot turn it into repeated model work.
            invocation_idempotency_key=invocation_identity,
            retry_prior_failure=retry_prior_failure,
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity=event.get("sensitivity", "normal"),
            compatible_failure_recovery_authorized=isinstance(recovery, dict),
            require_clean_result=True,
            compatible_success_identities=(
                invocation_identity,
                *(
                    f"{invocation_identity}:recovery-{attempt}"
                    for attempt in range(1, len(_TRANSIENT_RECOVERY_DELAYS_SECONDS) + 1)
                ),
            ),
        )

        try:
            if isinstance(recovery, dict) and recovery.get("contract_request"):
                result = await self._run_skill_with_transient_recovery(
                    ctx,
                    inputs,
                    skill_ctx,
                    contract_request=recovery["contract_request"],
                )
                await validate_contract_language_receipt(
                    ctx.event_log,
                    event,
                    str(recovery["request_event_id"]),
                    str(getattr(result, "recorded_event_id", None) or ""),
                    str(getattr(result, "skill_version", None) or ""),
                )
                recovery["contract_result"] = {
                    "completion_kind": "language",
                    "classifier_version": getattr(result, "skill_version", None),
                    "skill_call_event_id": getattr(result, "recorded_event_id", None),
                }
            else:
                result = await self._run_skill_with_transient_recovery(
                    ctx, inputs, skill_ctx
                )
        except _SKILL_FAILURE_TYPES as exc:
            if isinstance(recovery, dict) and recovery.get("contract_request"):
                recovery["contract_result"] = {
                    "completion_kind": "failed",
                    "classifier_version": str(
                        getattr(exc, "skill_version", SKILL_VERSION)
                    ),
                    "skill_call_event_id": None,
                }
            if not isinstance(recovery, dict) and await self._retain_assessed_default(
                event, ctx, skill_ctx, exc
            ):
                _log.warning(
                    "noise_filtering retained immutable assessment after refusing "
                    "failed compatible execution history"
                )
                return
            _log.warning(
                "noise_filtering skill failure (%s); emitting defensive default",
                type(exc).__name__,
            )
            await self._emit_results(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                invocation_fingerprint=invocation_fingerprint,
                output={
                    "classification": DEFAULT_CLASSIFICATION,
                    "confidence": 0.0,
                    "reasons": ["skill_failure_defensive_default"],
                    "reply_signal": "unknown",
                    "automated": False,
                    "transactional_due": False,
                    "security_code": False,
                    "person_to_person": False,
                    "deadline": None,
                    "candidates": [],
                },
                source_text=source_text,
                assessment_failure_reason=f"language_seam_{type(exc).__name__}",
                policy_source_id=policy_source_id,
                policy=policy,
                context_pack=context_pack,
                recovery=recovery if isinstance(recovery, dict) else None,
                skill_version=str(getattr(exc, "skill_version", SKILL_VERSION)),
            )
            return

        output = skill_output(result)
        await self._emit_results(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            invocation_fingerprint=invocation_fingerprint,
            output=output,
            source_text=source_text,
            assessment_failure_reason=None,
            policy_source_id=policy_source_id,
            policy=policy,
            context_pack=context_pack,
            recovery=recovery if isinstance(recovery, dict) else None,
            skill_version=str(getattr(result, "skill_version", None) or SKILL_VERSION),
        )

    async def _retain_assessed_default(
        self,
        event: dict[str, Any],
        ctx: PipelineContext,
        skill_ctx: SkillContext,
        error: BaseException,
    ) -> bool:
        """An execution patch cannot rewrite an already completed v23 receipt."""
        receipt_id = getattr(error, "compatible_receipt_event_id", None)
        receipt_key = getattr(error, "compatible_receipt_key", None)
        paired_default = bool(
            receipt_id
            and receipt_key
            in {
                f"skill:{identity}:{SKILL_ID}:8.0.0"
                for identity in skill_ctx.compatible_success_identities
            }
        )
        failure_id = getattr(error, "compatible_failure_event_id", None)
        failure_key = getattr(error, "compatible_failure_key", None)
        failure_class = getattr(error, "compatible_failure_class", None)
        failed_fallback = bool(
            failure_id
            and failure_key
            in {
                f"skill:failed:{identity}:{SKILL_ID}:8.0.0:{failure_class}"
                for identity in skill_ctx.compatible_success_identities
            }
        )
        if not paired_default and not failed_fallback:
            return False
        source_id = str(event["event_id"])
        assessment = await ctx.event_log.get_by_idempotency(
            event["tenant_id"],
            f"triage:message-features:v{_REPLAY_GENERATION}:{source_id}",
        )
        if (
            assessment is None
            or assessment.get("type") != "triage.intent.features_extracted"
            or assessment.get("schema_version") != 3
            or assessment.get("causation_id") != source_id
            or assessment.get("source_adapter") != "deterministic_candidate_eligibility"
            or assessment.get("source_account_id") != "pipeline"
            or assessment.get("payload", {}).get("source_event_id") != source_id
            or assessment.get("payload", {}).get("extraction_state") != "complete"
            or assessment.get("payload", {}).get("assessment_outcome")
            not in {"eligible_candidates", "no_candidate"}
            or any(
                assessment.get(key) != event.get(key)
                for key in (
                    "tenant_id",
                    "owner_scope",
                    "visibility_scope",
                    "sensitivity",
                )
            )
        ):
            return False
        # v23 predates an explicit assessment -> skill-receipt link. Require
        # the validated exact-fingerprint receipt to be the last classification
        # recorded for this source before its assessment, not an unrelated call.
        related = await ctx.event_log.get_by_causation(source_id)
        recorded = [
            prior
            for prior in related
            if prior.get("type") == "skill.call.recorded"
            and prior.get("payload", {}).get("skill_name") == SKILL_ID
            and prior["event_id"] < assessment["event_id"]
        ]
        if paired_default:
            return bool(recorded and recorded[-1]["event_id"] == receipt_id)
        if f"language_seam_{type(error).__name__}" not in assessment["payload"].get(
            "reason_codes", []
        ):
            return False
        terminals = [
            prior
            for prior in related
            if prior.get("type") in {"skill.call.recorded", "skill.call.failed"}
            and prior.get("payload", {}).get("skill_name") == SKILL_ID
            and prior["event_id"] < assessment["event_id"]
        ]
        return bool(terminals and terminals[-1]["event_id"] == failure_id)

    async def _emit_results(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        invocation_fingerprint: str,
        output: dict[str, Any],
        source_text: str,
        assessment_failure_reason: str | None,
        policy_source_id: str,
        policy: dict[str, Any],
        context_pack: MessageContextPack,
        recovery: dict[str, str] | None,
        skill_version: str = SKILL_VERSION,
    ) -> None:
        await self._emit_classified(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            classification=str(output.get("classification", DEFAULT_CLASSIFICATION)),
            confidence=float(output.get("confidence", 0.0)),
            skill_version=skill_version,
        )
        await self._emit_semantic_features(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            output=output,
            skill_version=skill_version,
        )
        await self._emit_triage_candidates(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            invocation_fingerprint=invocation_fingerprint,
            source_text=source_text,
            raw_candidates=output.get("candidates"),
            semantic_assessment_complete=output.get("assessment_contract")
            == "personal-obligation-assessment-v1"
            and assessment_failure_reason is None,
            message_direction=str(
                "outbound"
                if event.get("type") == "messaging.sent"
                or "SENT"
                in {
                    str(value).upper()
                    for value in (event.get("payload") or {}).get("provider_labels")
                    or []
                }
                else "inbound"
            ),
            assessment_failure_reason=assessment_failure_reason,
            policy_source_id=policy_source_id,
            policy=policy,
            context_pack=context_pack,
            recovery=recovery,
        )

    async def _emit_classified(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        classification: str,
        confidence: float,
        skill_version: str = _RETAINED_CLASSIFICATION_VERSION,
    ) -> None:
        payload = {
            "source_event_id": source_event_id,
            "classification": classification,
            "confidence": round(confidence, 4),
            "skill_name": SKILL_NAME,
            "skill_version": skill_version,
        }
        idempotency_key = await _select_payload_idempotency_key(
            ctx=ctx,
            tenant_id=event["tenant_id"],
            prefix="messaging:classified",
            source_event_id=source_event_id,
            payload=payload,
        )
        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="messaging.classified",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="noise_filtering",
            source_account_id="pipeline",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity=event.get("sensitivity", "normal"),
            idempotency_key=idempotency_key,
            payload=payload,
        )
        await ctx.event_sink.write(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def _emit_semantic_features(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        output: dict[str, Any],
        skill_version: str = _RETAINED_CLASSIFICATION_VERSION,
    ) -> None:
        payload = {
            "source_event_id": source_event_id,
            "reply_signal": str(output.get("reply_signal") or "unknown"),
            "automated": bool(output.get("automated", False)),
            "transactional_due": bool(output.get("transactional_due", False)),
            "security_code": bool(output.get("security_code", False)),
            "person_to_person": bool(output.get("person_to_person", False)),
            "deadline": output.get("deadline"),
            "confidence": round(float(output.get("confidence", 0.0)), 4),
            "reasons": [str(value) for value in output.get("reasons") or []][:20],
            "skill_name": SKILL_NAME,
            "skill_version": skill_version,
        }
        idempotency_key = await _select_payload_idempotency_key(
            ctx=ctx,
            tenant_id=event["tenant_id"],
            prefix="messaging:semantic-features",
            source_event_id=source_event_id,
            payload=payload,
        )
        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="messaging.semantic_features_extracted",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="noise_filtering",
            source_account_id="pipeline",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity=event.get("sensitivity", "normal"),
            idempotency_key=idempotency_key,
            payload=payload,
        )
        await ctx.event_sink.write(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def _emit_triage_candidates(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        invocation_fingerprint: str,
        source_text: str,
        raw_candidates: Any,
        semantic_assessment_complete: bool = False,
        message_direction: str,
        assessment_failure_reason: str | None,
        policy_source_id: str,
        policy: dict[str, Any],
        context_pack: MessageContextPack,
        recovery: dict[str, str] | None,
    ) -> None:
        owner_scope = str(event.get("owner_scope") or "")
        member_id = owner_scope.removeprefix("private:")
        try:
            submitted_at = datetime.fromisoformat(
                str(event["occurred_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            submitted_at = datetime.fromtimestamp(
                event["event_at_ms"] / 1000, timezone.utc
            )
        has_context_contract = isinstance(raw_candidates, list) and any(
            isinstance(value, dict) and value.get("evidence_source_id")
            for value in raw_candidates
        )
        language_candidates = []
        if policy.get("language_enabled") and context_pack.current_is_thread_anchor:
            language_candidates = (
                extract_grounded_context_candidates(context_pack, raw_candidates)
                if has_context_contract
                else extract_grounded_nlp_candidates(source_text, raw_candidates)
            )
        candidates = []
        if context_pack.current_is_thread_anchor and semantic_assessment_complete:
            # A completed semantic assessment owns both positive and negative
            # findings. A keyword fallback must not resurrect rejected surveys,
            # quoted instructions or already-paid bills after that assessment.
            candidates = language_candidates
        elif context_pack.current_is_thread_anchor:
            candidates = merge_intent_candidates(
                extract_message_fallback_candidates(
                    source_text,
                    submitted_at=submitted_at,
                    direction=message_direction,
                ),
                language_candidates,
            )
        if message_direction == "inbound" and not semantic_assessment_complete:
            candidates = [
                candidate
                for candidate in candidates
                if not (
                    candidate.target_kind in {"task", "commitment"}
                    and is_inbound_reply_request(candidate.title)
                )
            ]
        candidates = filter_candidates(candidates, policy)
        policy_rule = (
            f"candidate_policy_v1:{policy_source_id}:{policy.get('mode', 'review')}"
        )
        candidates = [
            replace(
                candidate,
                eligibility_rule_ids=list(
                    dict.fromkeys([*candidate.eligibility_rule_ids, policy_rule])
                ),
                reasons=list(dict.fromkeys([*candidate.reasons, policy_rule])),
            )
            for candidate in candidates
        ]
        (
            candidates,
            replaced_proposal_event_ids,
        ) = await self._reconcile_thread_candidates(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            context_pack=context_pack,
            candidates=candidates,
        )
        candidates = _deduplicate_candidate_keys(candidates)
        intent_id = f"source:{source_event_id}"
        # Replay must produce byte-identical event identities.  Never place a
        # wall-clock retry time in an idempotent payload.
        extracted_at = str(
            event.get("recorded_at")
            or event.get("occurred_at")
            or datetime.fromtimestamp(
                event["event_at_ms"] / 1000, timezone.utc
            ).isoformat()
        )
        extraction_keys = _versioned_idempotency_keys(
            "triage:message-features", source_event_id
        )
        prior_extractions = [
            prior
            for key in extraction_keys
            if (
                prior := await ctx.event_log.get_by_idempotency(event["tenant_id"], key)
            )
            is not None
        ]
        new_candidates = list(candidates)
        if prior_extractions:
            prior_proposals = []
            for prior_extraction in prior_extractions:
                prior_proposals.extend(
                    await ctx.event_log.get_by_causation(prior_extraction["event_id"])
                )
            await self._supersede_retired_fallbacks(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                proposals=prior_proposals,
                superseded_at=extracted_at,
            )
            # A proposal superseded by this reconciliation is immutable
            # history, not an active duplicate. Its candidate identity must
            # not suppress a still-eligible replacement. Member-decided and
            # unchanged context-free proposals are absent from the replacement
            # set and continue to suppress reopening/duplication.
            existing = {
                (
                    str(proposal["payload"].get("target_kind") or ""),
                    str(proposal["payload"].get("candidate_key") or ""),
                )
                for proposal in prior_proposals
                if proposal.get("type") == "triage.item.proposed"
                and str(proposal.get("event_id") or "")
                not in replaced_proposal_event_ids
            }
            new_candidates = [
                candidate
                for candidate in candidates
                if (candidate.target_kind, candidate.candidate_key) not in existing
            ]

        # Historical adapters could append the same provider observation more
        # than once under different source event ids.  A reviewed full replay
        # must not multiply Triage work.  The private-scope fingerprint covers
        # the exact bounded model inputs plus source/privacy identity; proposal
        # keys deliberately omit the append id so one exact observation yields
        # one candidate set.  The first causal source remains the provenance
        # anchor in the append-only log.
        deduplicated_candidates = []
        proposal_keys: dict[tuple[str, str], str] = {}
        for candidate in new_candidates:
            proposal_fingerprint = context_pack.fingerprint
            proposal_key = (
                f"triage:message-candidate:v{_REPLAY_GENERATION}:"
                f"{proposal_fingerprint}:{candidate.candidate_key}:"
                f"{candidate.target_kind}"
            )
            if (
                await ctx.event_log.get_by_idempotency(event["tenant_id"], proposal_key)
                is not None
            ):
                continue
            deduplicated_candidates.append(candidate)
            proposal_keys[(candidate.target_kind, candidate.candidate_key)] = (
                proposal_key
            )
        new_candidates = deduplicated_candidates
        # A useful deterministic proposal is not proof that the required
        # language assessment completed. Keep its failure visible to source
        # assurance and the bounded recovery path while retaining the proposal.
        outcome = (
            "failed"
            if assessment_failure_reason
            and policy.get("mode") == "review"
            and policy.get("language_enabled")
            else "eligible_candidates"
            if candidates
            else "no_candidate"
        )
        policy_reason = (
            "candidate_policy_manual_only"
            if policy.get("mode") == "manual"
            else "candidate_policy_quiet"
            if policy.get("mode") == "quiet"
            else "candidate_language_disabled"
            if not policy.get("language_enabled")
            else None
        )
        reason_codes = [
            *(
                [assessment_failure_reason]
                if assessment_failure_reason is not None
                else ["language_assessment_completed"]
            ),
            "eligible_candidate_found" if candidates else "no_eligible_candidate",
            *(
                ["conversation_context_assessed"]
                if context_pack.current_is_thread_anchor
                else ["deferred_to_latest_thread_message"]
            ),
            *([policy_reason] if policy_reason else []),
            policy_rule,
        ]
        proposal_drafts: list[dict[str, Any]] = []
        for candidate in new_candidates:
            target_kind = candidate.target_kind
            candidate_fields = dict(candidate.fields)
            if (candidate.interpretation or {}).get(
                "contract"
            ) == "triage-context-interpretation-v3":
                primary_refs = (candidate.interpretation or {}).get(
                    "evidence_refs"
                ) or []
                primary_id = (
                    primary_refs[0].get("source_event_id") if primary_refs else None
                )
                evidence_message = next(
                    (
                        message
                        for message in context_pack.messages
                        if message.source_event_id == primary_id
                    ),
                    None,
                )
                if evidence_message and evidence_message.sender:
                    candidate_fields["sender_digest"] = hashlib.sha256(
                        evidence_message.sender.strip().casefold().encode()
                    ).hexdigest()
            proposal_key = proposal_keys[(target_kind, candidate.candidate_key)]
            # Proposal identity follows the replay-versioned idempotency
            # identity. A stale contextual proposal and its context-free
            # replacement must project as two immutable rows; reusing the old
            # source/key/title-only ID would make ON CONFLICT hide the latter.
            fingerprint = hashlib.sha256(
                (f"{proposal_key}:{source_event_id}:{candidate.title}").encode()
            ).hexdigest()[:24]
            proposal_id = f"triage:message:{fingerprint}"
            proposal_drafts.append(
                {
                    "idempotency_key": proposal_key,
                    "proposal_schema_version": 5
                    if (candidate.interpretation or {}).get("contract")
                    == "triage-context-interpretation-v3"
                    else 4,
                    "payload": {
                        "proposal_id": proposal_id,
                        "target_kind": target_kind,
                        "owner_member_id": member_id,
                        "title": candidate.title,
                        "candidate": candidate_fields,
                        "route": {"member_id": member_id},
                        "source_event_id": source_event_id,
                        "parent_intent_id": None,
                        "candidate_key": candidate.candidate_key,
                        "extraction_event_id": None,
                        "eligibility_rule_ids": candidate.eligibility_rule_ids,
                        "confidence": candidate.confidence,
                        "reasons": candidate.reasons,
                        "interpretation": candidate.interpretation,
                        "source_occurred_at": (
                            (event.get("payload") or {}).get("received_at")
                            or (event.get("payload") or {}).get("sent_at")
                            or event.get("occurred_at")
                        ),
                    },
                }
            )
        await self._emit_triage_assessment(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            candidates=candidates,
            outcome=outcome,
            reason_codes=reason_codes,
            occurred_at=extracted_at,
            intent_id=intent_id,
            recovery=recovery,
            proposal_drafts=proposal_drafts,
        )

    async def _reconcile_thread_candidates(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        context_pack: MessageContextPack,
        candidates: list[Any],
    ) -> tuple[list[Any], set[str]]:
        """Replace untouched older thread interpretations; preserve decisions.

        A new message can answer or supersede an earlier ask.  Conversation
        candidates are therefore a versioned set rooted at the latest message.
        Member enrichments and decisions are permanent; an exact candidate the
        member already acted on is never silently re-opened. The returned event
        ID set names only proposals this call superseded, allowing downstream
        historical dedupe to admit their still-eligible replacements.
        """

        if not context_pack.current_is_thread_anchor or not hasattr(
            ctx.event_log, "read_since"
        ):
            return candidates, set()
        thread_source_ids = set(context_pack.episode_source_event_ids)
        current_same_source_fallbacks = {
            (
                candidate.target_kind,
                candidate.candidate_key,
                candidate.title,
            )
            for candidate in candidates
            if str(candidate.candidate_key).startswith("message-fallback-")
            and _CURRENT_MESSAGE_FALLBACK_RULE_IDS.intersection(
                candidate.eligibility_rule_ids
            )
        }
        decided_keys: set[tuple[str, str]] = set()
        decided_titles: set[tuple[str, str]] = set()
        replaced_proposal_event_ids: set[str] = set()
        prior_proposals: list[dict[str, Any]] = []
        for prior in await self._proposals_for_sources(
            event_log=ctx.event_log,
            owner_scope=str(event.get("owner_scope") or ""),
            source_event_ids=thread_source_ids,
        ):
            payload = prior.get("payload") or {}
            if prior.get("source_adapter") not in {
                "deterministic_candidate_eligibility",
                "noise_filtering",
                "legacy-message-extractor",
            }:
                continue
            interpretation = payload.get("interpretation")
            if (
                str(payload.get("source_event_id") or "") == source_event_id
                and isinstance(interpretation, dict)
                and interpretation.get("context_fingerprint")
                == context_pack.fingerprint
                and any(
                    candidate.target_kind == payload.get("target_kind")
                    and candidate.candidate_key == payload.get("candidate_key")
                    and candidate.title == payload.get("title")
                    and candidate.interpretation == interpretation
                    for candidate in candidates
                )
            ):
                # Source context alone cannot prove an interpretation current:
                # a new assessment contract may reject or change the same text.
                continue
            prior_rules = {
                str(value) for value in payload.get("eligibility_rule_ids") or []
            }
            prior_fallback_identity = (
                str(payload.get("target_kind") or ""),
                str(payload.get("candidate_key") or ""),
                str(payload.get("title") or ""),
            )
            if (
                str(payload.get("source_event_id") or "") == source_event_id
                and not isinstance(interpretation, dict)
                and _CURRENT_MESSAGE_FALLBACK_RULE_IDS.intersection(prior_rules)
                and prior_fallback_identity in current_same_source_fallbacks
            ):
                # A context-free deterministic fallback is not a stale
                # interpretation merely because the replay generation changed.
                # Keep the existing review item when the current rules derive
                # the exact same source/kind/key/title identity; the later
                # same-source dedupe then prevents a duplicate proposal.
                continue
            prior_proposals.append(prior)

        for prior in prior_proposals:
            payload = prior.get("payload") or {}
            descendants = await ctx.event_log.get_by_causation(prior["event_id"])
            member_decided = any(
                child.get("type")
                in {
                    "triage.item.enriched",
                    "triage.item.managed",
                    "triage.item.confirmed",
                    "triage.item.materialized",
                    "triage.item.dismissed",
                }
                for child in descendants
            )
            kind = str(payload.get("target_kind") or "")
            key = str(payload.get("candidate_key") or "")
            title = str(payload.get("title") or "")
            if member_decided:
                if key:
                    decided_keys.add((kind, key))
                if title:
                    decided_titles.add((kind, title))
                continue
            proposal_id = str(payload.get("proposal_id") or "")
            if not proposal_id:
                continue
            await _write_first_equivalent_event(
                ctx=ctx,
                envelope=EventEnvelope(
                    event_at_ms=event["event_at_ms"],
                    tenant_id=event["tenant_id"],
                    type="triage.item.superseded",
                    schema_version=4,
                    occurred_at=str(
                        event.get("recorded_at")
                        or event.get("occurred_at")
                        or EventEnvelope.now_utc_iso()
                    ),
                    source_adapter="deterministic_candidate_eligibility",
                    source_account_id="pipeline",
                    owner_scope=event["owner_scope"],
                    visibility_scope=event["visibility_scope"],
                    sensitivity=event.get("sensitivity", "normal"),
                    idempotency_key=(
                        f"triage:message-thread-replaced:v{_REPLAY_GENERATION}:"
                        f"{proposal_id}:{source_event_id}"
                    ),
                    payload={
                        "proposal_id": proposal_id,
                        "superseded_at": str(
                            event.get("recorded_at")
                            or event.get("occurred_at")
                            or EventEnvelope.now_utc_iso()
                        ),
                        "replacement_source_event_id": source_event_id,
                        "reason": "eligibility_rule_replaced",
                        "expected_content_revision_event_id": prior["event_id"],
                        "expected_last_event_id": prior["event_id"],
                    },
                ),
                correlation_id=ctx.correlation_id,
                causation_id=prior["event_id"],
                ignored_payload_fields=frozenset(
                    {
                        "superseded_at",
                        "replacement_source_event_id",
                        "expected_last_event_id",
                    }
                ),
            )
            replaced_proposal_event_ids.add(str(prior["event_id"]))
        return (
            [
                candidate
                for candidate in candidates
                if (candidate.target_kind, candidate.candidate_key) not in decided_keys
                and (candidate.target_kind, candidate.title) not in decided_titles
            ],
            replaced_proposal_event_ids,
        )

    async def _supersede_unreliable_source_proposals(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
    ) -> None:
        """Retire only untouched system proposals rooted in unreliable text.

        A stricter authored-text contract may invalidate an interpretation made
        by an older replay generation. The immutable proposal remains in the
        log, but it must leave the pending queue. Member enrichment and every
        terminal member/system disposition continue to dominate replay.
        """

        if not hasattr(ctx.event_log, "read_since"):
            return
        proposals: list[dict[str, Any]] = []
        for prior in await self._proposals_for_sources(
            event_log=ctx.event_log,
            owner_scope=str(event.get("owner_scope") or ""),
            source_event_ids={source_event_id},
        ):
            payload = prior.get("payload") or {}
            if prior.get("tenant_id") != event.get("tenant_id"):
                continue
            if prior.get("source_adapter") not in {
                "deterministic_candidate_eligibility",
                "noise_filtering",
                "legacy-message-extractor",
            }:
                continue
            if str(payload.get("source_event_id") or "") != source_event_id:
                continue
            proposals.append(prior)

        superseded_at = str(
            event.get("recorded_at")
            or event.get("occurred_at")
            or EventEnvelope.now_utc_iso()
        )
        for proposal in proposals:
            descendants = await ctx.event_log.get_by_causation(proposal["event_id"])
            if any(
                child.get("type")
                in {
                    "triage.item.enriched",
                    "triage.item.managed",
                    "triage.item.confirmed",
                    "triage.item.materialized",
                    "triage.item.dismissed",
                    "triage.item.superseded",
                }
                for child in descendants
            ):
                continue
            proposal_id = str((proposal.get("payload") or {}).get("proposal_id") or "")
            if not proposal_id:
                continue
            await _write_first_equivalent_event(
                ctx=ctx,
                envelope=EventEnvelope(
                    event_at_ms=event["event_at_ms"],
                    tenant_id=event["tenant_id"],
                    type="triage.item.superseded",
                    schema_version=4,
                    occurred_at=superseded_at,
                    source_adapter="deterministic_candidate_eligibility",
                    source_account_id="pipeline",
                    owner_scope=event["owner_scope"],
                    visibility_scope=event["visibility_scope"],
                    sensitivity=event.get("sensitivity", "normal"),
                    idempotency_key=(
                        "triage:message-unreliable-source-replaced:"
                        f"v{_REPLAY_GENERATION}:{proposal_id}:{source_event_id}"
                    ),
                    payload={
                        "proposal_id": proposal_id,
                        "superseded_at": superseded_at,
                        "replacement_source_event_id": source_event_id,
                        "reason": "eligibility_rule_replaced",
                        "expected_content_revision_event_id": proposal["event_id"],
                        "expected_last_event_id": proposal["event_id"],
                    },
                ),
                correlation_id=ctx.correlation_id,
                causation_id=proposal["event_id"],
                ignored_payload_fields=frozenset(
                    {
                        "superseded_at",
                        "replacement_source_event_id",
                        "expected_last_event_id",
                    }
                ),
            )

    async def _supersede_retired_fallbacks(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        proposals: list[dict[str, Any]],
        superseded_at: str,
    ) -> None:
        """Retire untouched proposals admitted by the overbroad v1 rule.

        Any member enrichment or decision wins permanently.  Only proposals
        whose sole semantic eligibility was the retired generic action-verb
        fallback receive a deterministic system supersession.
        """

        for proposal in proposals:
            if proposal.get("type") != "triage.item.proposed":
                continue
            payload = proposal.get("payload") or {}
            rule_ids = {
                str(value) for value in payload.get("eligibility_rule_ids") or []
            }
            if "explicit_action_verb_v1" not in rule_ids or (
                "exact_source_quote_grounded_v1" in rule_ids
            ):
                continue
            descendants = await ctx.event_log.get_by_causation(proposal["event_id"])
            if any(
                child.get("type")
                in {
                    "triage.item.enriched",
                    "triage.item.managed",
                    "triage.item.confirmed",
                    "triage.item.materialized",
                    "triage.item.dismissed",
                    "triage.item.superseded",
                }
                for child in descendants
            ):
                continue
            proposal_id = str(payload.get("proposal_id") or "")
            if not proposal_id:
                continue
            await _write_first_equivalent_event(
                ctx=ctx,
                envelope=EventEnvelope(
                    event_at_ms=event["event_at_ms"],
                    tenant_id=event["tenant_id"],
                    type="triage.item.superseded",
                    schema_version=4,
                    occurred_at=superseded_at,
                    source_adapter="deterministic_candidate_eligibility",
                    source_account_id="pipeline",
                    owner_scope=event["owner_scope"],
                    visibility_scope=event["visibility_scope"],
                    sensitivity=event.get("sensitivity", "normal"),
                    idempotency_key=(
                        f"triage:message-retire-action-verb-v1:v4:{proposal_id}"
                    ),
                    payload={
                        "proposal_id": proposal_id,
                        "superseded_at": superseded_at,
                        "replacement_source_event_id": source_event_id,
                        "reason": "eligibility_rule_replaced",
                        "expected_content_revision_event_id": proposal["event_id"],
                        "expected_last_event_id": proposal["event_id"],
                    },
                ),
                correlation_id=ctx.correlation_id,
                causation_id=proposal["event_id"],
                ignored_payload_fields=frozenset(
                    {
                        "superseded_at",
                        "replacement_source_event_id",
                        "expected_last_event_id",
                    }
                ),
            )

    async def _emit_triage_assessment(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        candidates: list[Any],
        outcome: str,
        reason_codes: list[str],
        occurred_at: str | None = None,
        intent_id: str | None = None,
        recovery: dict[str, Any] | None = None,
        extractor_id: str | None = None,
        proposal_drafts: list[dict[str, Any]] | None = None,
    ) -> str:
        """Record one terminal assessment even when zero candidates exist."""

        assessed_at = occurred_at or str(
            event.get("recorded_at")
            or event.get("occurred_at")
            or datetime.fromtimestamp(
                event["event_at_ms"] / 1000, timezone.utc
            ).isoformat()
        )
        recovery_request_event_id = (
            str(recovery["request_event_id"]) if recovery is not None else None
        )
        payload: dict[str, Any] = {
            "intent_id": intent_id or f"source:{source_event_id}",
            "source_event_id": source_event_id,
            "skill_call_event_id": None,
            "extractor_id": extractor_id
            or (
                f"{MESSAGE_FALLBACK_EXTRACTOR_ID}+{GROUNDED_NLP_EXTRACTOR_ID}"
                f"+{CONTEXTUAL_NLP_EXTRACTOR_ID}"
            ),
            "language": None,
            "candidates": [candidate.feature_payload() for candidate in candidates],
            "extraction_state": (
                "complete"
                if outcome in {"eligible_candidates", "no_candidate"}
                else "unavailable"
                if outcome == "failed"
                else "invalid"
            ),
            "extracted_at": assessed_at,
            "assessment_outcome": outcome,
            "reason_codes": list(dict.fromkeys(reason_codes))[:20],
        }
        if recovery is not None:
            payload.update(
                {
                    "supersedes_assessment_event_id": recovery[
                        "failed_assessment_event_id"
                    ],
                    "recovery_request_event_id": recovery_request_event_id,
                    "recovery_reason": recovery["failure_reason"],
                    "recovery_policy": RECOVERY_POLICY,
                    "recovery_attempt_limit": RECOVERY_ATTEMPT_LIMIT,
                }
            )
        if recovery is not None and recovery.get("contract_request"):
            result = recovery.get("contract_result")
            if result is None and not deterministic_contract_terminal(payload):
                # Unexplained context loss is not a completed current contract.
                outcome = "failed"
                payload.update(
                    assessment_outcome="failed",
                    extraction_state="unavailable",
                    reason_codes=["current_contract_terminal_not_proven"],
                )
                recovery["contract_result"] = {
                    "completion_kind": "failed",
                    "classifier_version": None,
                    "skill_call_event_id": None,
                }
        envelope = EventEnvelope(
            event_at_ms=self._derived_event_at_ms(event)
            if recovery is not None and recovery.get("contract_request")
            else event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="triage.intent.features_extracted",
            schema_version=4 if recovery is not None else 3,
            occurred_at=assessed_at,
            source_adapter="deterministic_candidate_eligibility",
            source_account_id="pipeline",
            owner_scope=str(event.get("owner_scope") or "system"),
            visibility_scope=str(
                event.get("visibility_scope") or event.get("owner_scope") or "system"
            ),
            sensitivity=event.get("sensitivity", "normal"),
            idempotency_key=(
                self._recovery_assessment_key(
                    str(recovery_request_event_id), source_event_id
                )
                if recovery is not None
                else f"triage:message-features:v{_REPLAY_GENERATION}:{source_event_id}"
            ),
            payload=payload,
        )
        contract_receipt = None
        if recovery is not None and recovery.get("contract_request"):
            result = recovery.get("contract_result") or {
                "completion_kind": "deterministic_terminal",
                "classifier_version": None,
                "skill_call_event_id": None,
            }
            contract_receipt = envelope.model_copy(
                update={
                    "type": CONTRACT_RESULT_TYPE,
                    "schema_version": 1,
                    "idempotency_key": contract_result_key(
                        str(recovery_request_event_id), source_event_id
                    ),
                    "payload": {
                        **result,
                        "request_event_id": recovery_request_event_id,
                        "source_event_id": source_event_id,
                        "assessment_idempotency_key": envelope.idempotency_key,
                        "publication_plan_idempotency_key": self._publication_plan_key(
                            str(envelope.idempotency_key)
                        )
                        if proposal_drafts
                        else None,
                        "required_assessment_contract": ASSESSMENT_CONTRACT,
                        "completed_assessment_contract": ASSESSMENT_CONTRACT
                        if outcome in {"eligible_candidates", "no_candidate"}
                        else None,
                    },
                }
            )
        if not proposal_drafts:
            if contract_receipt is not None:
                ids = await ctx.event_sink.write_batch(
                    [envelope, contract_receipt],
                    correlation_id=ctx.correlation_id,
                    causation_id=source_event_id,
                )
                return ids[0]
            return await ctx.event_sink.write(
                envelope,
                correlation_id=ctx.correlation_id,
                causation_id=source_event_id,
            )
        existing = await ctx.event_log.get_by_idempotency(
            event["tenant_id"], str(envelope.idempotency_key)
        )
        if existing is not None:
            # Retained v3/v4 assessments predate publication plans. Do not
            # rewrite or backfill their private receipt. The normal writer
            # still validates exact historical assessment equivalence.
            extraction_id = await ctx.event_sink.write(
                envelope,
                correlation_id=ctx.correlation_id,
                causation_id=source_event_id,
            )
            await self._publish_source_drafts(event, ctx, existing, proposal_drafts)
            return extraction_id
        plan_payload = {
            "contract": "triage-source-publication-v1",
            "source_event_id": source_event_id,
            "assessment_idempotency_key": envelope.idempotency_key,
            "assessment_schema_version": envelope.schema_version,
            "proposals": proposal_drafts,
        }
        TriageSourcePublicationPlannedV1.model_validate(plan_payload)
        envelope = envelope.model_copy(
            update={"event_at_ms": self._derived_event_at_ms(event)}
        )
        if contract_receipt is not None:
            contract_receipt = contract_receipt.model_copy(
                update={"event_at_ms": envelope.event_at_ms}
            )
        plan = envelope.model_copy(
            update={
                "type": "triage.source_publication.planned",
                "schema_version": 1,
                "idempotency_key": self._publication_plan_key(
                    str(envelope.idempotency_key)
                ),
                "payload": plan_payload,
            }
        )
        ids = await ctx.event_sink.write_batch(
            [
                envelope,
                plan,
                *([contract_receipt] if contract_receipt is not None else []),
            ],
            correlation_id=ctx.correlation_id,
            causation_id=source_event_id,
        )
        extraction_id = ids[0]
        assessment = {
            **envelope.model_dump(mode="json"),
            "event_id": extraction_id,
            "causation_id": source_event_id,
        }
        counts = await self._publish_source_drafts(
            event, ctx, assessment, proposal_drafts
        )
        stored_plan = await ctx.event_log.get_by_idempotency(
            event["tenant_id"], str(plan.idempotency_key)
        )
        if stored_plan is not None:
            await self._complete_source_publication(
                event, ctx, assessment, stored_plan, counts
            )
        return extraction_id

    @staticmethod
    def _publication_plan_key(assessment_key: str) -> str:
        return publication_plan_key(assessment_key)

    async def _resume_source_publication(
        self,
        source: dict[str, Any],
        ctx: PipelineContext,
        assessment: dict[str, Any],
        *,
        expected_plan: dict[str, Any] | None = None,
    ) -> bool:
        key = str(assessment.get("idempotency_key") or "")
        plan = await ctx.event_log.get_by_idempotency(
            source["tenant_id"], self._publication_plan_key(key)
        )
        if plan is None:
            if expected_plan is not None:
                raise RuntimeError(
                    "source publication trigger is not its canonical receipt"
                )
            return False
        if expected_plan is not None and expected_plan != plan:
            raise RuntimeError(
                "source publication trigger is not its canonical receipt"
            )
        source_id = str(source["event_id"])
        owner = str(source.get("owner_scope") or "")
        recovery_id = assessment.get("payload", {}).get("recovery_request_event_id")
        expected_key = (
            self._recovery_assessment_key(str(recovery_id), source_id)
            if recovery_id
            else f"triage:message-features:v{_REPLAY_GENERATION}:{source_id}"
        )
        if (
            not owner.startswith("private:")
            or not owner.removeprefix("private:")
            or source.get("type")
            not in {"messaging.received", "messaging.sent", "telephony.sms_received"}
            or source.get("tenant_id") != ctx.session.tenant_id
            or (
                source.get("payload", {}).get("member_id") is not None
                and source["payload"]["member_id"] != owner.removeprefix("private:")
            )
            or source.get("visibility_scope") != owner
            or source.get("sensitivity") == "privileged"
            or key != expected_key
            or assessment.get("type") != "triage.intent.features_extracted"
            or plan.get("type") != "triage.source_publication.planned"
            or plan.get("schema_version") != 1
            or any(
                record.get(field) != source.get(field)
                for record in (assessment, plan)
                for field in (
                    "tenant_id",
                    "owner_scope",
                    "visibility_scope",
                    "sensitivity",
                )
            )
            or any(
                record.get("causation_id") != source_id
                or record.get("source_adapter") != "deterministic_candidate_eligibility"
                or record.get("source_account_id") != "pipeline"
                for record in (assessment, plan)
            )
        ):
            raise RuntimeError("source publication receipt scope is invalid")
        payload = TriageSourcePublicationPlannedV1.model_validate(plan["payload"])
        if (
            payload.source_event_id != source_id
            or assessment.get("payload", {}).get("source_event_id") != source_id
            or payload.assessment_idempotency_key != key
            or payload.assessment_schema_version != assessment.get("schema_version")
        ):
            raise RuntimeError("source publication receipt identity is invalid")
        completion = await ctx.event_log.get_by_idempotency(
            source["tenant_id"], self._publication_completion_key(str(plan["event_id"]))
        )
        if completion is not None:
            self._validate_publication_completion(source, assessment, plan, completion)
            return True
        counts = await self._publish_source_drafts(
            source, ctx, assessment, plan["payload"]["proposals"]
        )
        await self._complete_source_publication(source, ctx, assessment, plan, counts)
        return True

    @staticmethod
    def _publication_completion_key(plan_id: str) -> str:
        return publication_completion_key(plan_id)

    def _validate_publication_completion(
        self, source, assessment, plan, completion
    ) -> None:
        if not publication_completion_valid(
            source=source, assessment=assessment, plan=plan, completion=completion
        ):
            raise RuntimeError("source publication completion identity is invalid")

    async def _complete_source_publication(
        self, source, ctx, assessment, plan, counts
    ) -> None:
        payload = {
            "contract": "triage-source-publication-completed-v1",
            "plan_event_id": plan["event_id"],
            "source_event_id": source["event_id"],
            "assessment_event_id": assessment["event_id"],
            **counts,
        }
        TriageSourcePublicationCompletedV1.model_validate(payload)
        await ctx.event_sink.write(
            EventEnvelope(
                event_at_ms=self._derived_event_at_ms(plan),
                tenant_id=source["tenant_id"],
                type="triage.source_publication.completed",
                schema_version=1,
                occurred_at=assessment["occurred_at"],
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=source["owner_scope"],
                visibility_scope=source["visibility_scope"],
                sensitivity=source.get("sensitivity", "normal"),
                idempotency_key=self._publication_completion_key(str(plan["event_id"])),
                payload=payload,
            ),
            correlation_id=ctx.correlation_id,
            causation_id=plan["event_id"],
        )

    async def _publish_source_drafts(
        self,
        source: dict[str, Any],
        ctx: PipelineContext,
        assessment: dict[str, Any],
        drafts: list[dict[str, Any]],
    ) -> dict[str, int]:
        source_id = str(source["event_id"])
        owner = str(source["owner_scope"])
        member = owner.removeprefix("private:")
        counts = {
            "planned_count": len(drafts),
            "published_count": 0,
            "already_present_count": 0,
            "member_decided_count": 0,
            "superseded_count": 0,
        }
        for index, draft in enumerate(drafts):
            latest = await latest_source_assessment(ctx.event_log, source_id)
            if latest is not None and latest["event_id"] != assessment["event_id"]:
                if not source_assessment_identity_valid(
                    source=source, assessment=latest
                ):
                    raise RuntimeError("newer source assessment identity is invalid")
                # A newer terminal assessment owns current work. Missing rows
                # from the superseded plan must not resurrect its old meaning.
                counts["superseded_count"] += len(drafts) - index
                return counts
            payload = dict(draft["payload"])
            if (
                payload.get("source_event_id") != source_id
                or payload.get("owner_member_id") != member
                or payload.get("route", {}).get("member_id") != member
                or not str(draft["idempotency_key"]).startswith(
                    f"triage:message-candidate:v{_REPLAY_GENERATION}:"
                )
            ):
                raise RuntimeError("source publication proposal scope is invalid")
            proposal_key = str(draft["idempotency_key"])
            context_fingerprint = proposal_key.removeprefix(
                f"triage:message-candidate:v{_REPLAY_GENERATION}:"
            ).split(":", 1)[0]
            if (
                len(context_fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in context_fingerprint)
                or proposal_key
                != (
                    f"triage:message-candidate:v{_REPLAY_GENERATION}:"
                    f"{context_fingerprint}:{payload.get('candidate_key')}:{payload['target_kind']}"
                )
                or payload.get("proposal_id")
                != "triage:message:"
                + hashlib.sha256(
                    f"{proposal_key}:{source_id}:{payload['title']}".encode()
                ).hexdigest()[:24]
            ):
                raise RuntimeError("source publication proposal identity is invalid")
            existing = await ctx.event_log.get_by_idempotency(
                source["tenant_id"], proposal_key
            )
            decided = False
            prior_proposals = (
                await self._proposals_for_sources(
                    event_log=ctx.event_log,
                    owner_scope=owner,
                    source_event_ids={source_id},
                )
                if existing is None
                else []
            )
            for prior in prior_proposals:
                prior_payload = prior.get("payload") or {}
                if prior_payload.get("target_kind") != payload["target_kind"] or not (
                    prior_payload.get("candidate_key") == payload.get("candidate_key")
                    or prior_payload.get("title") == payload.get("title")
                ):
                    continue
                descendants = await ctx.event_log.get_by_causation(prior["event_id"])
                if any(
                    child.get("type")
                    in {
                        "triage.item.enriched",
                        "triage.item.managed",
                        "triage.item.confirmed",
                        "triage.item.materialized",
                        "triage.item.dismissed",
                    }
                    for child in descendants
                ):
                    decided = True
                    break
            if decided:
                counts["member_decided_count"] += 1
                continue
            payload["extraction_event_id"] = assessment["event_id"]
            await _write_first_equivalent_event(
                ctx=ctx,
                envelope=EventEnvelope(
                    event_at_ms=self._derived_event_at_ms(assessment),
                    tenant_id=source["tenant_id"],
                    type="triage.item.proposed",
                    schema_version=draft["proposal_schema_version"],
                    occurred_at=assessment["occurred_at"],
                    source_adapter="deterministic_candidate_eligibility",
                    source_account_id="pipeline",
                    owner_scope=owner,
                    visibility_scope=owner,
                    sensitivity=source.get("sensitivity", "normal"),
                    idempotency_key=draft["idempotency_key"],
                    payload=payload,
                ),
                correlation_id=ctx.correlation_id,
                causation_id=assessment["event_id"],
                ignored_payload_fields=frozenset(
                    {"proposal_id", "source_event_id", "extraction_event_id"}
                ),
            )
            counts[
                "already_present_count" if existing is not None else "published_count"
            ] += 1
        return counts
