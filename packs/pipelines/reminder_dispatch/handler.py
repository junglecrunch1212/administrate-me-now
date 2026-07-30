"""reminder_dispatch proactive pipeline.

Per [BUILD.md §L4 reminder_dispatch] and [§7]. Cron-driven via
OpenClaw standing orders ([D1] Corollary, `bootstrap/openclaw/programs/
reminder_dispatch.md`); the in-process PipelineRunner skips proactive
packs on absence of `triggers.events` (`runner.py:131-138`). This
handler is exercised by direct call from the proactive driver (or in
tests via fakes) — there is no bus subscription.

Clones `MorningDigestPipeline`'s shape per [§1] 10c-iii grounding: a
structural `Pipeline`-Protocol class with `pack_id`/`version`/`triggers`
attrs, constructor-injection loaders per PM-27 with no-op defaults,
single-site `outbound()` call from `deliver()` per [§6.14], defensive
`_SKILL_FAILURE_EXCEPTIONS` catch on the same 7-tuple as
`morning_digest`, sentinel emit on skill failure / validation failure
with `validation_failed=True` + `delivered=False` and no `outbound()`
call.

Dedupe pattern: a `recent_reminders_loader` callable returns the set of
`commitment_id`s with a `reminder.dispatched` event within the
configured `dedupe_window_minutes`. The v1 default is a no-op
returning an empty set; future bootstrap wiring injects an
`EventLog.read_since`-backed loader (the on-main read surface is
async-iterator-shaped per `adminme/events/log.py:376`, so the
loader-callable wraps that read into a synchronous set lookup for the
pipeline's per-tick filter).

Per-member opt-in: a `member_override_loader` returns the
`per_member_overrides[member_id]` dict (or None). When
`reminders_enabled=False`, the commitment is skipped before the skill
call. Lookahead / dedupe window can be overridden per-member.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable

from adminme.events.envelope import EventEnvelope
from adminme.lib.skill_runner import (
    OpenClawResponseMalformed,
    OpenClawTimeout,
    OpenClawUnreachable,
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillScopeInsufficient,
    SkillSensitivityRefused,
)
from adminme.pipelines.base import PipelineContext, Triggers

_log = logging.getLogger(__name__)

ProfileLoader = Callable[[str], dict[str, Any] | None]
CommitmentsDueLoader = Callable[[str, int], list[dict[str, Any]]]
RecentRemindersLoader = Callable[[str, int], set[str]]
MemberOverrideLoader = Callable[[str], dict[str, Any] | None]

DEFAULT_PROFILE_FORMAT = "none"
DEFAULT_DELIVERY_CHANNEL = "imessage"
DEFAULT_LOOKAHEAD_MINUTES = 60
DEFAULT_DEDUPE_MINUTES = 90

_VALID_FORMATS = {"fog_aware", "compressed", "carousel", "child", "none"}
_VALID_CHANNELS = {"imessage", "sms", "email", "push"}

_SKILL_FAILURE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillSensitivityRefused,
    SkillScopeInsufficient,
    OpenClawTimeout,
    OpenClawUnreachable,
    OpenClawResponseMalformed,
)


def _coerce_profile_format(profile: dict[str, Any] | None) -> str:
    if not isinstance(profile, dict):
        return DEFAULT_PROFILE_FORMAT
    fmt = profile.get("profile_format")
    if isinstance(fmt, str) and fmt in _VALID_FORMATS:
        return fmt
    return DEFAULT_PROFILE_FORMAT


def _coerce_due_at_iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return None


class ReminderDispatchPipeline:
    pack_id: str = "pipeline:reminder_dispatch"
    version: str = "1.0.0"
    triggers: Triggers = {
        "schedule": "*/15 * * * *",
        "proactive": True,
    }

    def __init__(
        self,
        *,
        profile_loader: ProfileLoader | None = None,
        commitments_due_loader: CommitmentsDueLoader | None = None,
        recent_reminders_loader: RecentRemindersLoader | None = None,
        member_override_loader: MemberOverrideLoader | None = None,
    ) -> None:
        self._profile_loader: ProfileLoader = profile_loader or (lambda _mid: None)
        self._commitments_due_loader: CommitmentsDueLoader = commitments_due_loader or (
            lambda _now, _look: []
        )
        self._recent_reminders_loader: RecentRemindersLoader = (
            recent_reminders_loader or (lambda _now, _ded: set())
        )
        self._member_override_loader: MemberOverrideLoader = member_override_loader or (
            lambda _mid: None
        )

    async def gather(
        self,
        now_iso: str,
        lookahead_minutes: int,
        dedupe_minutes: int,
    ) -> dict[str, Any]:
        commitments = list(self._commitments_due_loader(now_iso, lookahead_minutes))
        recent_ids = set(self._recent_reminders_loader(now_iso, dedupe_minutes))
        return {
            "now_iso": now_iso,
            "commitments": commitments,
            "recent_reminder_commitment_ids": recent_ids,
        }

    async def compose(
        self,
        ctx: PipelineContext,
        commitment: dict[str, Any],
        profile_format: str,
    ) -> dict[str, Any] | None:
        """Call compose_reminder@v1 for one commitment. Returns the
        skill's output dict on success or None on a defensive-default
        skill failure (caller emits the sentinel reminder.dispatched
        event with validation_failed=True, delivered=False)."""
        skill_inputs = {
            "commitment_id": commitment["commitment_id"],
            "owner_member_id": commitment["owner_member_id"],
            "recipient_party_id": commitment["recipient_party_id"],
            "commitment_title": commitment.get("title", ""),
            "due_at": _coerce_due_at_iso(commitment.get("due_at")) or "",
            "profile_format": profile_format,
            "recipient_name": commitment.get("recipient_name"),
            "owed_to": commitment.get("owed_to"),
            "notes": commitment.get("notes"),
        }
        try:
            result = await ctx.run_skill_fn("compose_reminder", skill_inputs, ctx)
        except _SKILL_FAILURE_EXCEPTIONS as exc:
            _log.warning(
                "reminder_dispatch: compose skill failed (%s); "
                "emitting sentinel per [§7.7]",
                type(exc).__name__,
            )
            return None
        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            return None
        return output

    def validate(
        self,
        commitment: dict[str, Any],
        composed: dict[str, Any],
    ) -> bool:
        """Minimal validate per [§1] 10c-iii grounding: the skill must
        return a non-empty composed_text and not signal its own
        validation_failed flag. compose_reminder does not emit
        claimed_event_ids (no morning-digest-style fabrication guard);
        the commitment_id is loader-anchored so cannot be fabricated."""
        if composed.get("validation_failed", False):
            return False
        body = composed.get("composed_text")
        if not isinstance(body, str) or not body.strip():
            return False
        return True

    def _resolve_delivery_channel(
        self,
        profile: dict[str, Any] | None,
        suggested: str | None,
    ) -> str:
        if isinstance(profile, dict):
            channel = profile.get("delivery_channel")
            if isinstance(channel, str) and channel in _VALID_CHANNELS:
                return channel
        if isinstance(suggested, str) and suggested in _VALID_CHANNELS:
            return suggested
        return DEFAULT_DELIVERY_CHANNEL

    async def deliver(
        self,
        ctx: PipelineContext,
        recipient_party_id: str,
        body_text: str,
        channel: str,
    ) -> bool:
        """Single `outbound()` call site per [§6.14]. Returns True when
        the bus recorded `external.sent`, False on suppression."""
        if ctx.observation_manager is None:
            _log.info(
                "reminder_dispatch: no observation_manager wired; "
                "treating as observation-mode-active per [D22]"
            )
            return False

        async def _no_op_send() -> None:
            return None

        from adminme.lib.observation import outbound

        result = await outbound(
            ctx.session,
            "reminder_send",
            {"recipient_party_id": recipient_party_id, "body_text": body_text},
            _no_op_send,
            manager=ctx.observation_manager,
            event_sink=ctx.event_sink,
            target_channel=channel,
            target_identifier=recipient_party_id,
        )
        return not result.suppressed

    async def _emit_reminder_dispatched(
        self,
        ctx: PipelineContext,
        commitment_id: str,
        owner_member_id: str,
        recipient_party_id: str,
        composed_text: str,
        channel: str,
        due_at_iso: str,
        validation_failed: bool,
        delivered: bool,
        triggering_event: dict[str, Any] | None,
    ) -> None:
        if triggering_event is not None:
            event_at_ms = triggering_event["event_at_ms"]
            tenant_id = triggering_event["tenant_id"]
            owner_scope = triggering_event["owner_scope"]
            visibility_scope = triggering_event["visibility_scope"]
        else:
            event_at_ms = int(time.time() * 1000)
            tenant_id = ctx.session.tenant_id
            owner_scope = "shared:household"
            visibility_scope = "shared:household"
        envelope = EventEnvelope(
            event_at_ms=event_at_ms,
            tenant_id=tenant_id,
            type="reminder.dispatched",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="reminder_dispatch",
            source_account_id="pipeline",
            owner_scope=owner_scope,
            visibility_scope=visibility_scope,
            sensitivity="normal",
            payload={
                "commitment_id": commitment_id,
                "owner_member_id": owner_member_id,
                "recipient_party_id": recipient_party_id,
                "composed_text": composed_text,
                "channel": channel,
                "due_at": due_at_iso,
                "delivered": delivered,
                "validation_failed": validation_failed,
            },
        )
        await ctx.event_sink.write(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def dispatch_one(
        self,
        commitment: dict[str, Any],
        ctx: PipelineContext,
        triggering_event: dict[str, Any] | None = None,
    ) -> None:
        """Drive one reminder for one commitment. Per-commitment skill
        failures emit the sentinel reminder.dispatched but do NOT
        propagate — a per-row failure must not abort the tick."""
        owner_member_id = commitment["owner_member_id"]
        commitment_id = commitment["commitment_id"]
        recipient_party_id = commitment["recipient_party_id"]
        due_at_iso = _coerce_due_at_iso(commitment.get("due_at")) or ""

        profile = self._profile_loader(owner_member_id)
        profile_format = _coerce_profile_format(profile)

        composed = await self.compose(ctx, commitment, profile_format)
        if composed is None:
            await self._emit_reminder_dispatched(
                ctx,
                commitment_id=commitment_id,
                owner_member_id=owner_member_id,
                recipient_party_id=recipient_party_id,
                composed_text="",
                channel=DEFAULT_DELIVERY_CHANNEL,
                due_at_iso=due_at_iso,
                validation_failed=True,
                delivered=False,
                triggering_event=triggering_event,
            )
            return

        if not self.validate(commitment, composed):
            await self._emit_reminder_dispatched(
                ctx,
                commitment_id=commitment_id,
                owner_member_id=owner_member_id,
                recipient_party_id=recipient_party_id,
                composed_text="",
                channel=DEFAULT_DELIVERY_CHANNEL,
                due_at_iso=due_at_iso,
                validation_failed=True,
                delivered=False,
                triggering_event=triggering_event,
            )
            return

        composed_text = composed["composed_text"]
        channel = self._resolve_delivery_channel(
            profile, composed.get("suggested_channel")
        )
        delivered = await self.deliver(ctx, recipient_party_id, composed_text, channel)
        await self._emit_reminder_dispatched(
            ctx,
            commitment_id=commitment_id,
            owner_member_id=owner_member_id,
            recipient_party_id=recipient_party_id,
            composed_text=composed_text,
            channel=channel,
            due_at_iso=due_at_iso,
            validation_failed=False,
            delivered=delivered,
            triggering_event=triggering_event,
        )

    async def dispatch(
        self,
        now_iso: str,
        ctx: PipelineContext,
        triggering_event: dict[str, Any] | None = None,
        lookahead_minutes: int = DEFAULT_LOOKAHEAD_MINUTES,
        dedupe_minutes: int = DEFAULT_DEDUPE_MINUTES,
    ) -> None:
        """Drive one tick: load commitments due within lookahead,
        filter out dedupe-hits and opt-outs, dispatch each surviving
        commitment via dispatch_one()."""
        gather_result = await self.gather(now_iso, lookahead_minutes, dedupe_minutes)
        recent_ids: set[str] = gather_result["recent_reminder_commitment_ids"]
        for commitment in gather_result["commitments"]:
            commitment_id = commitment.get("commitment_id")
            owner_member_id = commitment.get("owner_member_id")
            recipient_party_id = commitment.get("recipient_party_id")
            if not (
                isinstance(commitment_id, str)
                and isinstance(owner_member_id, str)
                and isinstance(recipient_party_id, str)
            ):
                continue
            if commitment_id in recent_ids:
                continue
            override = self._member_override_loader(owner_member_id)
            if isinstance(override, dict):
                if override.get("reminders_enabled") is False:
                    continue
            await self.dispatch_one(commitment, ctx, triggering_event=triggering_event)

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        """Pipeline protocol entrypoint. Proactive packs are not
        currently dispatched by the in-process runner; this method is
        provided for protocol compliance and exercised in tests by
        direct call. The expected payload carries `now_iso` and,
        optionally, `lookahead_window_minutes` / `dedupe_window_minutes`
        overrides."""
        payload = event.get("payload") or {}
        now_iso = payload.get("now_iso")
        if not isinstance(now_iso, str):
            _log.warning(
                "reminder_dispatch: handle() called without now_iso; "
                "defensively skipping"
            )
            return
        lookahead = payload.get("lookahead_window_minutes")
        dedupe = payload.get("dedupe_window_minutes")
        await self.dispatch(
            now_iso,
            ctx,
            triggering_event=event,
            lookahead_minutes=(
                lookahead
                if isinstance(lookahead, int) and lookahead > 0
                else DEFAULT_LOOKAHEAD_MINUTES
            ),
            dedupe_minutes=(
                dedupe
                if isinstance(dedupe, int) and dedupe > 0
                else DEFAULT_DEDUPE_MINUTES
            ),
        )
