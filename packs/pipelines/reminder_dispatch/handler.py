"""reminder_dispatch proactive pipeline.

Per [BUILD.md §L4] and [§7]. Cron-driven via OpenClaw standing orders
(`bootstrap/openclaw/programs/reminder_dispatch.md`); the in-process
PipelineRunner skips proactive packs on absence of `triggers.events`
(`runner.py:131-138`). This handler is exercised by direct call from
the proactive driver (or in tests via fakes) — there is no bus
subscription.

Scan loop: pull open commitments due within `lookahead_window_minutes`
but not sooner than `min_reminder_gap_minutes`; filter by event-log
dedupe (recent `reminder.dispatched` for the same commitment_id
within `dedupe_window_minutes` per [§7.3] / [D17] — there is no
projection-side `last_reminded_at` field in this prompt's scope, per
the work prompt's Out-of-Scope block). For each surviving commitment
compose a short reminder body via `compose_reminder` and call
`outbound()` per [§6.14]. Observation mode short-circuits delivery
transparently (the bus records `observation.suppressed`); on
suppression `delivered=False` — the `reminder.dispatched` event still
lands.

Per-row defensive default per [§7.7]: a skill failure on one
commitment emits `reminder.dispatched` with `composed_text=""` and
`tone_note="skill_failure_defensive_default"` and does NOT abort the
tick — sibling commitments are still processed.

Cite [§7.3] (pipelines never write projection rows directly),
[§8.9] (skill-runner-only LLM access per [D6]),
[D17] (proactive pipelines do not write projections directly),
[D22] (observation-mode default-on for new pipelines until per-member
opt-in).

Constructor-injection loaders per PM-27 (mirroring 10c-i's
reward_dispatch shape + 10c-ii's morning_digest shape): real loader
modules don't exist on main yet, so the pack ships with no-op
defaults that exercise the defensive-default sentinel paths. Tests
inject fakes; future bootstrap wiring or a runner-side seam injects
real loaders.
"""

from __future__ import annotations

import logging
import time
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

CommitmentsLoader = Callable[[int], list[dict[str, Any]]]
MemberProfileLoader = Callable[[str], dict[str, Any] | None]
PartyLoader = Callable[[str], dict[str, Any] | None]
RecentDispatchedLoader = Callable[[int], list[dict[str, Any]]]

DEFAULT_LOOKAHEAD_MINUTES = 60
DEFAULT_MIN_GAP_MINUTES = 15
DEFAULT_DEDUPE_WINDOW_MINUTES = 90
DEFAULT_CHANNEL = "imessage"
DEFENSIVE_TONE_NOTE = "skill_failure_defensive_default"

_SKILL_FAILURE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillSensitivityRefused,
    SkillScopeInsufficient,
    OpenClawTimeout,
    OpenClawUnreachable,
    OpenClawResponseMalformed,
)


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
        commitments_loader: CommitmentsLoader | None = None,
        member_profile_loader: MemberProfileLoader | None = None,
        party_loader: PartyLoader | None = None,
        recent_dispatched_loader: RecentDispatchedLoader | None = None,
        lookahead_window_minutes: int = DEFAULT_LOOKAHEAD_MINUTES,
        min_reminder_gap_minutes: int = DEFAULT_MIN_GAP_MINUTES,
        dedupe_window_minutes: int = DEFAULT_DEDUPE_WINDOW_MINUTES,
        per_member_overrides: dict[str, dict[str, Any]] | None = None,
        skip_party_tags: list[str] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._commitments_loader: CommitmentsLoader = (
            commitments_loader or (lambda _w: [])
        )
        self._member_profile_loader: MemberProfileLoader = (
            member_profile_loader or (lambda _mid: None)
        )
        self._party_loader: PartyLoader = party_loader or (lambda _pid: None)
        self._recent_dispatched_loader: RecentDispatchedLoader = (
            recent_dispatched_loader or (lambda _w: [])
        )
        self._lookahead_window_minutes = lookahead_window_minutes
        self._min_reminder_gap_minutes = min_reminder_gap_minutes
        self._dedupe_window_minutes = dedupe_window_minutes
        self._per_member_overrides = per_member_overrides or {}
        self._skip_party_tags = set(skip_party_tags or [])
        self._now_fn: Callable[[], float] = now_fn or time.time

    def _effective_overrides(self, member_id: str) -> dict[str, Any]:
        override = self._per_member_overrides.get(member_id)
        if isinstance(override, dict):
            return override
        return {}

    async def compose(
        self,
        ctx: PipelineContext,
        commitment: dict[str, Any],
        owner_profile: dict[str, Any],
        recipient_party: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Call compose_reminder. Returns the skill's output dict on
        success, or None on a defensive-default skill failure (caller
        emits the empty-text dispatched event)."""
        inputs = {
            "commitment": commitment,
            "owner_member_profile": owner_profile,
            "recipient_party": recipient_party or {},
        }
        try:
            result = await ctx.run_skill_fn(
                "compose_reminder", inputs, ctx
            )
        except _SKILL_FAILURE_EXCEPTIONS as exc:
            _log.warning(
                "reminder_dispatch: compose skill failed (%s); "
                "emitting defensive-default dispatched per [§7.7]",
                type(exc).__name__,
            )
            return None
        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            return None
        return output

    async def deliver(
        self,
        ctx: PipelineContext,
        member_id: str,
        body_text: str,
        channel: str,
    ) -> bool:
        """Single outbound() call site per [§6.14]. Returns True when
        the bus recorded external.sent, False on suppression."""
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
            {"member_id": member_id, "body_text": body_text},
            _no_op_send,
            manager=ctx.observation_manager,
            event_log=ctx.event_log,
            target_channel=channel,
            target_identifier=member_id,
        )
        return not result.suppressed

    async def _emit_dispatched(
        self,
        ctx: PipelineContext,
        *,
        commitment: dict[str, Any],
        composed_text: str,
        channel: str,
        tone_note: str,
        delivered: bool,
        triggering_event: dict[str, Any] | None,
    ) -> None:
        if triggering_event is not None:
            event_at_ms = triggering_event.get("event_at_ms", int(self._now_fn() * 1000))
            tenant_id = triggering_event.get("tenant_id", ctx.session.tenant_id)
            owner_scope = triggering_event.get("owner_scope", "shared:household")
            visibility_scope = triggering_event.get(
                "visibility_scope", "shared:household"
            )
        else:
            event_at_ms = int(self._now_fn() * 1000)
            tenant_id = ctx.session.tenant_id
            owner_scope = "shared:household"
            visibility_scope = "shared:household"
        due_at = commitment.get("due_at")
        payload: dict[str, Any] = {
            "commitment_id": commitment.get("id", ""),
            "owner_member_id": commitment.get("owner_member_id", ""),
            "recipient_party_id": commitment.get("recipient_party_id"),
            "composed_text": composed_text,
            "channel": channel,
            "due_at": due_at,
            "delivered": delivered,
            "tone_note": tone_note,
        }
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
            payload=payload,
        )
        await ctx.event_log.append(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def dispatch(
        self,
        ctx: PipelineContext,
        triggering_event: dict[str, Any] | None = None,
    ) -> None:
        """Drive one tick. Iterates due commitments and processes each;
        per-row failure does NOT abort the tick per [§7.7]."""
        commitments = list(
            self._commitments_loader(self._lookahead_window_minutes)
        )
        recent = list(
            self._recent_dispatched_loader(self._dedupe_window_minutes)
        )
        dedupe_set: set[str] = set()
        for ev in recent:
            if not isinstance(ev, dict):
                continue
            cid = ev.get("commitment_id")
            if isinstance(cid, str):
                dedupe_set.add(cid)

        now_ts = self._now_fn()

        for commitment in commitments:
            if not isinstance(commitment, dict):
                continue
            cid = commitment.get("id")
            if not isinstance(cid, str) or not cid:
                continue
            if cid in dedupe_set:
                continue
            due_at = commitment.get("due_at")
            if not isinstance(due_at, (int, float)):
                continue
            owner_id = commitment.get("owner_member_id")
            if not isinstance(owner_id, str) or not owner_id:
                continue

            override = self._effective_overrides(owner_id)
            lookahead = int(
                override.get("lookahead_window_minutes", self._lookahead_window_minutes)
            )
            delta_min = (float(due_at) - now_ts) / 60.0
            if delta_min > lookahead:
                continue
            if delta_min < self._min_reminder_gap_minutes:
                continue

            if override.get("reminders_enabled") is False:
                continue
            profile = self._member_profile_loader(owner_id) or {}
            if profile.get("reminders_enabled") is False:
                continue

            party_id = commitment.get("recipient_party_id")
            party = self._party_loader(party_id) if isinstance(party_id, str) else None
            if isinstance(party, dict):
                party_tags = set(party.get("tags") or [])
                if party_tags & self._skip_party_tags:
                    continue

            composed = await self.compose(ctx, commitment, profile, party)
            if composed is None:
                await self._emit_dispatched(
                    ctx,
                    commitment=commitment,
                    composed_text="",
                    channel=DEFAULT_CHANNEL,
                    tone_note=DEFENSIVE_TONE_NOTE,
                    delivered=False,
                    triggering_event=triggering_event,
                )
                continue

            composed_text = str(composed.get("composed_text", ""))
            channel = str(composed.get("suggested_channel") or DEFAULT_CHANNEL)
            tone_note = str(composed.get("tone_note", ""))
            delivered = False
            if composed_text:
                delivered = await self.deliver(ctx, owner_id, composed_text, channel)
            await self._emit_dispatched(
                ctx,
                commitment=commitment,
                composed_text=composed_text,
                channel=channel,
                tone_note=tone_note,
                delivered=delivered,
                triggering_event=triggering_event,
            )

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        """Pipeline protocol entrypoint. Proactive packs are not
        currently dispatched by the in-process runner (it skips on
        absence of `triggers.events` per `runner.py:131-138`); this
        method is provided for protocol compliance and is exercised in
        tests by direct call."""
        await self.dispatch(ctx, triggering_event=event)
