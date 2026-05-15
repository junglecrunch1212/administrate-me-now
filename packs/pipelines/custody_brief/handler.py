"""custody_brief proactive pipeline.

Per [BUILD.md §L4] and [§7]. Cron-driven via OpenClaw standing orders
([D1] Corollary, `bootstrap/openclaw/programs/custody_brief.md`); the
in-process PipelineRunner skips proactive packs on absence of
`triggers.events` (`runner.py:131-138`). This handler is exercised
by direct call from the proactive driver (or in tests via fakes) —
there is no bus subscription.

Scan source: a constructor-injected ``coparent_members_loader`` that
returns members with ``coparent_relationship_member_id`` set. Per
member, ``child_profiles_loader`` returns the child profiles,
``calendar_loader`` returns next-day calendar events for each child.

Per [D22] default-true opt-out: members with a coparent relationship
receive a brief unless their per-member override sets
``custody_brief_enabled: false``.

Per-row defensive default per [§7.7]: a skill failure on one parent
emits NO event for that parent but continues processing the rest.
A brief with no children is suppressed at the pipeline boundary
before emit — the schema enforces ``min_length=1`` on
``child_member_ids`` per [10c-iii Operating Context].

Single outbound call site per [§6.14]: ``_deliver()`` routes through
``adminme.lib.observation.outbound`` which gates on
``ObservationManager.is_active()`` per [§6.13]. On suppression the
``custody_brief.composed`` event is still appended.

Constructor-injection loaders per PM-27 (mirroring 10c-i/10c-ii).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
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
CoparentMembersLoader = Callable[[], list[dict[str, Any]]]
ChildProfilesLoader = Callable[[str], list[dict[str, Any]]]
CalendarLoader = Callable[[str, str], list[dict[str, Any]]]

DEFAULT_CHANNEL = "imessage"
_ALLOWED_CHANNELS = {"imessage", "sms", "email", "push"}

_SKILL_FAILURE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillSensitivityRefused,
    SkillScopeInsufficient,
    OpenClawTimeout,
    OpenClawUnreachable,
    OpenClawResponseMalformed,
)


def _coerce_channel(channel: Any) -> str:
    if isinstance(channel, str) and channel in _ALLOWED_CHANNELS:
        return channel
    return DEFAULT_CHANNEL


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


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
        profile_loader: ProfileLoader | None = None,
        coparent_members_loader: CoparentMembersLoader | None = None,
        child_profiles_loader: ChildProfilesLoader | None = None,
        calendar_loader: CalendarLoader | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._profile_loader: ProfileLoader = profile_loader or (
            lambda _mid: None
        )
        self._coparent_members_loader: CoparentMembersLoader = (
            coparent_members_loader or (lambda: [])
        )
        self._child_profiles_loader: ChildProfilesLoader = (
            child_profiles_loader or (lambda _mid: [])
        )
        self._calendar_loader: CalendarLoader = calendar_loader or (
            lambda _cid, _anchor: []
        )
        self._config: dict[str, Any] = config or {}

    def _config_for_member(self, member_id: str) -> dict[str, Any]:
        overrides = (
            self._config.get("per_member_overrides", {}).get(member_id)
            or {}
        )
        return {
            "custody_brief_enabled": overrides.get(
                "custody_brief_enabled", True
            ),
            "lookahead_days": overrides.get("lookahead_days", 1),
        }

    async def _compose(
        self,
        ctx: PipelineContext,
        parent_profile: dict[str, Any],
        coparent_profile: dict[str, Any] | None,
        child_profiles: list[dict[str, Any]],
        calendar_events: list[dict[str, Any]],
        handoff_timing: dict[str, Any],
        allergen_med_notes: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        skill_inputs = {
            "member_profile": parent_profile,
            "coparent_profile": coparent_profile or {},
            "child_profiles": child_profiles,
            "calendar_events": calendar_events,
            "handoff_timing": handoff_timing,
            "allergen_med_notes": allergen_med_notes,
        }
        try:
            result = await ctx.run_skill_fn(
                "compose_custody_brief", skill_inputs, ctx
            )
        except _SKILL_FAILURE_EXCEPTIONS as exc:
            _log.warning(
                "custody_brief: compose skill failed for member (%s); "
                "skipping row per [§7.7]",
                type(exc).__name__,
            )
            return None
        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            return None
        return output

    async def _emit_composed(
        self,
        ctx: PipelineContext,
        member_id: str,
        coparent_member_id: str,
        child_member_ids: list[str],
        composed_text: str,
        channel: str,
        brief_date_iso: str,
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
                "brief_date": brief_date_iso,
            },
        )
        await ctx.event_log.append(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def _deliver(
        self,
        ctx: PipelineContext,
        member_id: str,
        body_text: str,
        channel: str,
    ) -> bool:
        if not body_text:
            return False
        if ctx.observation_manager is None:
            _log.info(
                "custody_brief: no observation_manager wired; treating "
                "as observation-mode-active per [§6.16]"
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

    async def _process_parent(
        self,
        ctx: PipelineContext,
        parent: dict[str, Any],
        brief_date_iso: str,
        triggering_event: dict[str, Any] | None,
    ) -> None:
        """One parent member with coparent_relationship → maybe one
        custody_brief.composed + maybe one outbound send. Per [§7.7]
        a skill failure on one parent does not abort the tick."""
        member_id = str(parent.get("id", ""))
        coparent_member_id = str(
            parent.get("coparent_relationship_member_id", "")
        )
        if not member_id or not coparent_member_id:
            return
        cfg = self._config_for_member(member_id)
        if not cfg["custody_brief_enabled"]:
            return
        child_profiles = list(self._child_profiles_loader(member_id))
        if not child_profiles:
            # Schema requires min_length=1 on child_member_ids; a brief
            # with no children is suppressed before emit per
            # [10c-iii Operating Context].
            return
        child_ids = [
            str(c["id"])
            for c in child_profiles
            if isinstance(c, dict) and "id" in c
        ]
        if not child_ids:
            return
        calendar_events: list[dict[str, Any]] = []
        for child_id in child_ids:
            calendar_events.extend(
                list(self._calendar_loader(child_id, brief_date_iso))
            )
        handoff_timing = parent.get("coparent_handoff_pattern") or {}
        if not isinstance(handoff_timing, dict):
            handoff_timing = {}
        allergen_med_notes = parent.get("child_health_flags") or []
        if not isinstance(allergen_med_notes, list):
            allergen_med_notes = []
        coparent_profile = self._profile_loader(coparent_member_id)
        composed = await self._compose(
            ctx,
            parent_profile=parent,
            coparent_profile=coparent_profile,
            child_profiles=child_profiles,
            calendar_events=calendar_events,
            handoff_timing=handoff_timing,
            allergen_med_notes=allergen_med_notes,
        )
        if composed is None:
            return
        composed_text = composed.get("composed_text") or ""
        if not composed_text:
            return
        channel = _coerce_channel(composed.get("suggested_channel"))
        await self._emit_composed(
            ctx,
            member_id=member_id,
            coparent_member_id=coparent_member_id,
            child_member_ids=child_ids,
            composed_text=composed_text,
            channel=channel,
            brief_date_iso=brief_date_iso,
            triggering_event=triggering_event,
        )
        await self._deliver(ctx, member_id, composed_text, channel)

    async def dispatch(
        self,
        brief_date_iso: str,
        ctx: PipelineContext,
        triggering_event: dict[str, Any] | None = None,
    ) -> None:
        """Drive one nightly tick. Iterates parent members with coparent
        relationships; per-parent skill failure does not abort the
        tick per [§7.7]."""
        parents = list(self._coparent_members_loader())
        for parent in parents:
            try:
                await self._process_parent(
                    ctx, parent, brief_date_iso, triggering_event
                )
            except Exception:  # noqa: BLE001
                _log.exception(
                    "custody_brief: parent %s row failed; continuing tick",
                    parent.get("id"),
                )
                continue

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        """Pipeline protocol entrypoint. Proactive packs are not
        currently dispatched by the in-process runner; this method is
        provided for protocol compliance and is exercised in tests by
        direct call. The expected payload carries ``brief_date_iso``."""
        payload = event.get("payload") or {}
        brief_date_iso = payload.get("brief_date_iso")
        if not isinstance(brief_date_iso, str):
            _log.warning(
                "custody_brief: handle() called without brief_date_iso; "
                "defensively skipping"
            )
            return
        await self.dispatch(brief_date_iso, ctx, triggering_event=event)
