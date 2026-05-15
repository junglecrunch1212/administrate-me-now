"""custody_brief proactive pipeline.

Per [BUILD.md §L4 custody_brief] and [§7]. Cron-driven via OpenClaw
standing orders ([D1] Corollary, `bootstrap/openclaw/programs/
custody_brief.md`); the in-process PipelineRunner skips proactive
packs on absence of `triggers.events` (`runner.py:131-138`). This
handler is exercised by direct call from the proactive driver (or in
tests via fakes) — there is no bus subscription.

Clones `MorningDigestPipeline`'s shape per [§1] 10c-iii grounding,
with two intentional shape choices:

1. **Per-member opt-OUT (default ON).** Per [§7] table,
   `custody_brief_enabled` defaults to true; the per-member override
   gates each member; explicit `false` skips that member.
2. **Coparent-anchored loader.** Loader returns one row per parent
   member with a coparent relationship set; row payload carries
   `coparent_member_id`, `child_member_ids`, `next_day_events`,
   `handoff_timing`, allergen / med notes.

Empty next-day calendar still composes (handoff timing + allergen
notes are load-bearing); per [§8] C3 unit case.

Constructor-injection loaders per PM-27: real loader modules don't
exist on main yet (UT-14 carry-forward). Tests inject fakes.
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

ProfileLoader = Callable[[str], dict[str, Any] | None]
CoparentTargetsLoader = Callable[[str], list[dict[str, Any]]]
MemberOverrideLoader = Callable[[str], dict[str, Any] | None]

DEFAULT_PROFILE_FORMAT = "none"
DEFAULT_DELIVERY_CHANNEL = "imessage"

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
        coparent_targets_loader: CoparentTargetsLoader | None = None,
        member_override_loader: MemberOverrideLoader | None = None,
    ) -> None:
        self._profile_loader: ProfileLoader = profile_loader or (lambda _mid: None)
        self._coparent_targets_loader: CoparentTargetsLoader = (
            coparent_targets_loader or (lambda _brief_date: [])
        )
        self._member_override_loader: MemberOverrideLoader = (
            member_override_loader or (lambda _mid: None)
        )

    async def gather(
        self,
        brief_date: str,
    ) -> list[dict[str, Any]]:
        return list(self._coparent_targets_loader(brief_date))

    async def compose(
        self,
        ctx: PipelineContext,
        member_id: str,
        coparent_member_id: str,
        child_member_ids: list[str],
        brief_date: str,
        profile_format: str,
        next_day_events: list[dict[str, Any]],
        handoff_timing: dict[str, Any] | None,
        allergen_notes: list[dict[str, Any]],
        medication_notes: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Call compose_custody_brief@v1 for one parent member."""
        skill_inputs: dict[str, Any] = {
            "member_id": member_id,
            "coparent_member_id": coparent_member_id,
            "child_member_ids": child_member_ids,
            "brief_date": brief_date,
            "profile_format": profile_format,
            "next_day_events": next_day_events,
            "handoff_timing": handoff_timing,
            "allergen_notes": allergen_notes,
            "medication_notes": medication_notes,
        }
        try:
            result = await ctx.run_skill_fn(
                "compose_custody_brief", skill_inputs, ctx
            )
        except _SKILL_FAILURE_EXCEPTIONS as exc:
            _log.warning(
                "custody_brief: compose skill failed (%s); emitting "
                "sentinel per [§7.7]",
                type(exc).__name__,
            )
            return None
        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            return None
        return output

    def validate(
        self,
        target: dict[str, Any],
        composed: dict[str, Any],
    ) -> bool:
        """Minimal validate: skill's composed_text must be non-empty
        and validation_failed flag must not be set. coparent_member_id
        and child_member_ids are loader-anchored — no fabrication
        surface."""
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
        member_id: str,
        body_text: str,
        channel: str,
    ) -> bool:
        """Single `outbound()` call site per [§6.14]."""
        if ctx.observation_manager is None:
            _log.info(
                "custody_brief: no observation_manager wired; treating "
                "as observation-mode-active per [D22]"
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

    async def _emit_custody_brief_composed(
        self,
        ctx: PipelineContext,
        member_id: str,
        coparent_member_id: str,
        child_member_ids: list[str],
        composed_text: str,
        channel: str,
        brief_date: str,
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
                "brief_date": brief_date,
                "delivered": delivered,
                "validation_failed": validation_failed,
            },
        )
        await ctx.event_log.append(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def dispatch_one(
        self,
        target: dict[str, Any],
        brief_date: str,
        ctx: PipelineContext,
        triggering_event: dict[str, Any] | None = None,
    ) -> None:
        """Drive one custody brief for one parent member. Per-row skill
        failures emit the sentinel but do NOT propagate."""
        member_id = target["member_id"]
        coparent_member_id = target.get("coparent_member_id")
        if not isinstance(coparent_member_id, str) or not coparent_member_id:
            return
        child_member_ids = [
            str(c) for c in (target.get("child_member_ids") or []) if c
        ]
        if not child_member_ids:
            return

        profile = self._profile_loader(member_id)
        profile_format = _coerce_profile_format(profile)

        composed = await self.compose(
            ctx,
            member_id=member_id,
            coparent_member_id=coparent_member_id,
            child_member_ids=child_member_ids,
            brief_date=brief_date,
            profile_format=profile_format,
            next_day_events=list(target.get("next_day_events") or []),
            handoff_timing=target.get("handoff_timing"),
            allergen_notes=list(target.get("allergen_notes") or []),
            medication_notes=list(target.get("medication_notes") or []),
        )
        if composed is None:
            await self._emit_custody_brief_composed(
                ctx,
                member_id=member_id,
                coparent_member_id=coparent_member_id,
                child_member_ids=child_member_ids,
                composed_text="",
                channel=DEFAULT_DELIVERY_CHANNEL,
                brief_date=brief_date,
                validation_failed=True,
                delivered=False,
                triggering_event=triggering_event,
            )
            return

        if not self.validate(target, composed):
            await self._emit_custody_brief_composed(
                ctx,
                member_id=member_id,
                coparent_member_id=coparent_member_id,
                child_member_ids=child_member_ids,
                composed_text="",
                channel=DEFAULT_DELIVERY_CHANNEL,
                brief_date=brief_date,
                validation_failed=True,
                delivered=False,
                triggering_event=triggering_event,
            )
            return

        composed_text = composed["composed_text"]
        channel = self._resolve_delivery_channel(
            profile, composed.get("suggested_channel")
        )
        delivered = await self.deliver(ctx, member_id, composed_text, channel)
        await self._emit_custody_brief_composed(
            ctx,
            member_id=member_id,
            coparent_member_id=coparent_member_id,
            child_member_ids=child_member_ids,
            composed_text=composed_text,
            channel=channel,
            brief_date=brief_date,
            validation_failed=False,
            delivered=delivered,
            triggering_event=triggering_event,
        )

    async def dispatch(
        self,
        brief_date: str,
        ctx: PipelineContext,
        triggering_event: dict[str, Any] | None = None,
    ) -> None:
        """Drive one tick: load coparent-anchored targets, iterate,
        dispatch each via dispatch_one()."""
        targets = await self.gather(brief_date)
        for target in targets:
            member_id = target.get("member_id") if isinstance(target, dict) else None
            if not isinstance(member_id, str):
                continue
            override = self._member_override_loader(member_id)
            opted_out = False
            if isinstance(override, dict):
                opted_out = override.get("custody_brief_enabled") is False
            if opted_out:
                continue
            await self.dispatch_one(
                target,
                brief_date,
                ctx,
                triggering_event=triggering_event,
            )

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        """Pipeline protocol entrypoint. Proactive packs are not
        currently dispatched by the in-process runner; this method is
        provided for protocol compliance and is exercised in tests by
        direct call. The expected payload carries `brief_date`."""
        payload = event.get("payload") or {}
        brief_date = payload.get("brief_date")
        if not isinstance(brief_date, str):
            _log.warning(
                "custody_brief: handle() called without brief_date; "
                "defensively skipping"
            )
            return
        await self.dispatch(brief_date, ctx, triggering_event=event)
