"""custody_brief proactive pipeline.

Per [BUILD.md §L4] and [§7]. Cron-driven via OpenClaw standing orders
(`bootstrap/openclaw/programs/custody_brief.md`); the in-process
PipelineRunner skips proactive packs on absence of `triggers.events`
(`runner.py:131-138`). This handler is exercised by direct call from
the proactive driver (or in tests via fakes).

Scan loop: enumerate parent members with `coparent_relationship_member_id`
set; for each, gather the parent's profile, coparent profile, child
profiles, next-day calendar events, handoff timing, and allergen/med
notes. Compose a long-form nightly brief via `compose_custody_brief`,
then call `outbound()` per [§6.14]. Observation mode short-circuits
delivery transparently (the bus records `observation.suppressed`); on
suppression `delivered=False` — the `custody_brief.composed` event
still lands.

A brief with no children is suppressed before emit (the event schema
requires `child_member_ids` non-empty); an empty next-day calendar is
fine — the brief still composes with handoff timing + allergen
reminders.

Per-row defensive default per [§7.7]: a skill failure on one parent
member skips that row and does NOT abort the tick — sibling parents
are still processed.

Cite [§7.3], [§8.9], [D17], [D22].
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

CoparentMembersLoader = Callable[[], list[dict[str, Any]]]
MemberProfileLoader = Callable[[str], dict[str, Any] | None]
ChildProfilesLoader = Callable[[str], list[dict[str, Any]]]
CalendarEventsLoader = Callable[[str, str], list[dict[str, Any]]]

DEFAULT_BRIEF_HOUR = 20
DEFAULT_CHANNEL = "imessage"

_SKILL_FAILURE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillSensitivityRefused,
    SkillScopeInsufficient,
    OpenClawTimeout,
    OpenClawUnreachable,
    OpenClawResponseMalformed,
)


class CustodyBriefPipeline:
    pack_id: str = "pipeline:custody_brief"
    version: str = "1.0.0"
    triggers: Triggers = {
        "schedule": "0 20 * * *",
        "proactive": True,
    }

    def __init__(
        self,
        *,
        coparent_members_loader: CoparentMembersLoader | None = None,
        member_profile_loader: MemberProfileLoader | None = None,
        child_profiles_loader: ChildProfilesLoader | None = None,
        calendar_events_loader: CalendarEventsLoader | None = None,
        brief_local_hour: int = DEFAULT_BRIEF_HOUR,
        per_member_overrides: dict[str, dict[str, Any]] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._coparent_members_loader: CoparentMembersLoader = (
            coparent_members_loader or (lambda: [])
        )
        self._member_profile_loader: MemberProfileLoader = (
            member_profile_loader or (lambda _mid: None)
        )
        self._child_profiles_loader: ChildProfilesLoader = (
            child_profiles_loader or (lambda _mid: [])
        )
        self._calendar_events_loader: CalendarEventsLoader = (
            calendar_events_loader or (lambda _child, _date: [])
        )
        self._brief_local_hour = brief_local_hour
        self._per_member_overrides = per_member_overrides or {}
        self._now_fn: Callable[[], float] = now_fn or time.time

    def _effective_overrides(self, member_id: str) -> dict[str, Any]:
        override = self._per_member_overrides.get(member_id)
        if isinstance(override, dict):
            return override
        return {}

    def _is_enabled(self, member_id: str) -> bool:
        override = self._effective_overrides(member_id)
        if "custody_brief_enabled" in override:
            return bool(override["custody_brief_enabled"])
        return True

    async def compose(
        self,
        ctx: PipelineContext,
        member_profile: dict[str, Any],
        coparent_profile: dict[str, Any] | None,
        child_profiles: list[dict[str, Any]],
        calendar_events: list[dict[str, Any]],
        handoff_timing: dict[str, Any],
        allergen_med_notes: list[Any],
    ) -> dict[str, Any] | None:
        inputs = {
            "member_profile": member_profile,
            "coparent_profile": coparent_profile or {},
            "child_profiles": child_profiles,
            "calendar_events": calendar_events,
            "handoff_timing": handoff_timing,
            "allergen_med_notes": allergen_med_notes,
        }
        try:
            result = await ctx.run_skill_fn(
                "compose_custody_brief", inputs, ctx
            )
        except _SKILL_FAILURE_EXCEPTIONS as exc:
            _log.warning(
                "custody_brief: compose skill failed (%s); skipping row "
                "per [§7.7]",
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
        if ctx.observation_manager is None:
            _log.info(
                "custody_brief: no observation_manager wired; "
                "treating as observation-mode-active per [D22]"
            )
            return False

        async def _no_op_send() -> None:
            return None

        from adminme.lib.observation import outbound

        result = await outbound(
            ctx.session,
            "custody_brief_send",
            {"member_id": member_id, "body_text": body_text},
            _no_op_send,
            manager=ctx.observation_manager,
            event_log=ctx.event_log,
            target_channel=channel,
            target_identifier=member_id,
        )
        return not result.suppressed

    async def _emit_composed(
        self,
        ctx: PipelineContext,
        *,
        member_id: str,
        coparent_member_id: str,
        child_member_ids: list[str],
        composed_text: str,
        channel: str,
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
        envelope = EventEnvelope(
            event_at_ms=event_at_ms,
            tenant_id=tenant_id,
            type="custody_brief.composed",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="custody_brief",
            source_account_id="pipeline",
            owner_scope=owner_scope,
            visibility_scope=visibility_scope,
            sensitivity="normal",
            payload={
                "member_id": member_id,
                "coparent_member_id": coparent_member_id,
                "child_member_ids": child_member_ids,
                "composed_text": composed_text,
                "channel": channel,
                "delivered": delivered,
            },
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
        """Iterate parent members with coparent relationship; compose a
        brief for each. Per-row failure does NOT abort the tick."""
        members = list(self._coparent_members_loader())
        for member in members:
            if not isinstance(member, dict):
                continue
            member_id = member.get("id")
            if not isinstance(member_id, str) or not member_id:
                continue
            if not self._is_enabled(member_id):
                continue

            profile = self._member_profile_loader(member_id) or {}
            coparent_member_id = (
                profile.get("coparent_relationship_member_id")
                or member.get("coparent_relationship_member_id")
            )
            if not isinstance(coparent_member_id, str) or not coparent_member_id:
                continue
            coparent_profile = self._member_profile_loader(coparent_member_id) or {}

            child_profiles = list(self._child_profiles_loader(member_id))
            child_ids: list[str] = []
            for child in child_profiles:
                if not isinstance(child, dict):
                    continue
                cid = child.get("id")
                if isinstance(cid, str) and cid:
                    child_ids.append(cid)
            if not child_ids:
                continue

            anchor_date = ""
            calendar_events: list[dict[str, Any]] = []
            for cid in child_ids:
                calendar_events.extend(
                    list(self._calendar_events_loader(cid, anchor_date))
                )

            handoff_timing = profile.get("coparent_handoff_pattern") or {}
            if not isinstance(handoff_timing, dict):
                handoff_timing = {}
            allergen_med_notes_raw = profile.get("child_health_flags") or []
            if isinstance(allergen_med_notes_raw, list):
                allergen_med_notes = list(allergen_med_notes_raw)
            else:
                allergen_med_notes = []

            composed = await self.compose(
                ctx,
                profile,
                coparent_profile,
                child_profiles,
                calendar_events,
                handoff_timing,
                allergen_med_notes,
            )
            if composed is None:
                continue

            composed_text = str(composed.get("composed_text", ""))
            if not composed_text:
                continue
            channel = str(composed.get("suggested_channel") or DEFAULT_CHANNEL)
            delivered = await self.deliver(ctx, member_id, composed_text, channel)
            await self._emit_composed(
                ctx,
                member_id=member_id,
                coparent_member_id=coparent_member_id,
                child_member_ids=child_ids,
                composed_text=composed_text,
                channel=channel,
                delivered=delivered,
                triggering_event=triggering_event,
            )

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        await self.dispatch(ctx, triggering_event=event)
