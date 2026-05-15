"""crm_surface proactive pipeline.

Per [BUILD.md §L4] and [§7]. Cron-driven via OpenClaw standing orders
(`bootstrap/openclaw/programs/crm_surface.md`); the in-process
PipelineRunner skips proactive packs on absence of `triggers.events`
(`runner.py:131-138`). This handler is exercised by direct call from
the proactive driver (or in tests via fakes).

Scan loop: enumerate members with `crm_proactive_enabled=true` (opt-in
per [D22] / BUILD.md §L4); for each, pull parties with `last_contact_at`
older than `threshold_days`. Clamp to `max_surfaced_per_week`. Compose
a single short brief via `compose_crm_surface` summarizing the surfaced
parties, then call `outbound()` per [§6.14]. Observation mode
short-circuits delivery transparently (the bus records
`observation.suppressed`); on suppression `delivered=False` — the
`crm_surface.composed` event still lands.

Per-row defensive default per [§7.7]: a skill failure on one member
skips that row and does NOT abort the tick — sibling members are
still processed.

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

MembersLoader = Callable[[], list[dict[str, Any]]]
StalePartiesLoader = Callable[[str, int], list[dict[str, Any]]]
MemberProfileLoader = Callable[[str], dict[str, Any] | None]

DEFAULT_THRESHOLD_DAYS = 90
DEFAULT_MAX_SURFACED = 5
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


class CrmSurfacePipeline:
    pack_id: str = "pipeline:crm_surface"
    version: str = "1.0.0"
    triggers: Triggers = {
        "schedule": "0 18 * * 0",
        "proactive": True,
    }

    def __init__(
        self,
        *,
        members_loader: MembersLoader | None = None,
        member_profile_loader: MemberProfileLoader | None = None,
        stale_parties_loader: StalePartiesLoader | None = None,
        threshold_days: int = DEFAULT_THRESHOLD_DAYS,
        max_surfaced_per_week: int = DEFAULT_MAX_SURFACED,
        per_member_overrides: dict[str, dict[str, Any]] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._members_loader: MembersLoader = members_loader or (lambda: [])
        self._member_profile_loader: MemberProfileLoader = (
            member_profile_loader or (lambda _mid: None)
        )
        self._stale_parties_loader: StalePartiesLoader = (
            stale_parties_loader or (lambda _mid, _d: [])
        )
        self._threshold_days = threshold_days
        self._max_surfaced_per_week = max_surfaced_per_week
        self._per_member_overrides = per_member_overrides or {}
        self._now_fn: Callable[[], float] = now_fn or time.time

    def _effective_overrides(self, member_id: str) -> dict[str, Any]:
        override = self._per_member_overrides.get(member_id)
        if isinstance(override, dict):
            return override
        return {}

    def _is_opted_in(
        self, member_id: str, profile: dict[str, Any]
    ) -> bool:
        override = self._effective_overrides(member_id)
        if "crm_proactive_enabled" in override:
            return bool(override["crm_proactive_enabled"])
        return bool(profile.get("crm_proactive_enabled"))

    async def compose(
        self,
        ctx: PipelineContext,
        member_profile: dict[str, Any],
        surfaced_parties: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        inputs = {
            "member_profile": member_profile,
            "surfaced_parties": surfaced_parties,
        }
        try:
            result = await ctx.run_skill_fn(
                "compose_crm_surface", inputs, ctx
            )
        except _SKILL_FAILURE_EXCEPTIONS as exc:
            _log.warning(
                "crm_surface: compose skill failed (%s); skipping row "
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
                "crm_surface: no observation_manager wired; "
                "treating as observation-mode-active per [D22]"
            )
            return False

        async def _no_op_send() -> None:
            return None

        from adminme.lib.observation import outbound

        result = await outbound(
            ctx.session,
            "crm_surface_send",
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
        surfaced_party_ids: list[str],
        composed_text: str,
        channel: str,
        sort_order: list[str],
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
            type="crm_surface.composed",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="crm_surface",
            source_account_id="pipeline",
            owner_scope=owner_scope,
            visibility_scope=visibility_scope,
            sensitivity="normal",
            payload={
                "member_id": member_id,
                "surfaced_party_ids": surfaced_party_ids,
                "composed_text": composed_text,
                "channel": channel,
                "sort_order": sort_order,
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
        """Iterate opted-in members; surface stale-contact parties for
        each. Per-row failure does NOT abort the tick per [§7.7]."""
        members = list(self._members_loader())
        for member in members:
            if not isinstance(member, dict):
                continue
            member_id = member.get("id")
            if not isinstance(member_id, str) or not member_id:
                continue

            profile = self._member_profile_loader(member_id) or {}
            if not self._is_opted_in(member_id, profile):
                continue

            override = self._effective_overrides(member_id)
            threshold = int(override.get("threshold_days", self._threshold_days))
            stale = list(self._stale_parties_loader(member_id, threshold))
            if not stale:
                continue

            surfaced = stale[: self._max_surfaced_per_week]
            party_ids: list[str] = []
            for party in surfaced:
                if not isinstance(party, dict):
                    continue
                pid = party.get("id")
                if isinstance(pid, str) and pid:
                    party_ids.append(pid)
            if not party_ids:
                continue

            composed = await self.compose(ctx, profile, surfaced)
            if composed is None:
                continue

            composed_text = str(composed.get("composed_text", ""))
            if not composed_text:
                continue
            channel = str(composed.get("suggested_channel") or DEFAULT_CHANNEL)
            sort_order_raw = composed.get("sort_order") or party_ids
            sort_order = [str(x) for x in sort_order_raw if isinstance(x, str)]
            delivered = await self.deliver(ctx, member_id, composed_text, channel)
            await self._emit_composed(
                ctx,
                member_id=member_id,
                surfaced_party_ids=party_ids,
                composed_text=composed_text,
                channel=channel,
                sort_order=sort_order,
                delivered=delivered,
                triggering_event=triggering_event,
            )

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        await self.dispatch(ctx, triggering_event=event)
