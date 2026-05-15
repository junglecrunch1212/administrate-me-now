"""crm_surface proactive pipeline.

Per [BUILD.md §L4 crm_surface] and [§7]. Cron-driven via OpenClaw
standing orders ([D1] Corollary, `bootstrap/openclaw/programs/
crm_surface.md`); the in-process PipelineRunner skips proactive packs
on absence of `triggers.events` (`runner.py:131-138`). This handler is
exercised by direct call from the proactive driver (or in tests via
fakes) — there is no bus subscription.

Clones `MorningDigestPipeline`'s shape per [§1] 10c-iii grounding,
with two intentional differences flagged in §7:

1. **No `validate()` method.** compose_crm_surface does not return
   `claimed_event_ids`; the load-bearing payload field is `gap_kind`
   (a Literal-typed enum) plus the loader-anchored
   `surfaced_party_ids`. No fabrication surface — the validation
   guard from morning_digest does not apply here.
2. **Opt-in default FALSE.** Per [§7] table, `crm_proactive_enabled`
   defaults to false. The per-member override loader gates each
   member; without an explicit opt-in the tick skips that member
   entirely (no skill call, no emit).

`max_surfaced_per_week` clamps the loader-returned `surfaced_parties`
list inline before the skill call — the clamp is load-bearing, not
advisory.

Constructor-injection loaders per PM-27: real loader modules don't
exist on main yet, so the pack ships with no-op defaults that
exercise the defensive-default sentinel paths. Tests inject fakes.
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
CrmTargetsLoader = Callable[[str], list[dict[str, Any]]]
MemberOverrideLoader = Callable[[str], dict[str, Any] | None]

DEFAULT_PROFILE_FORMAT = "none"
DEFAULT_DELIVERY_CHANNEL = "imessage"
DEFAULT_MAX_SURFACED = 5
DEFAULT_GAP_KIND = "stale_contact"

_VALID_FORMATS = {"fog_aware", "compressed", "carousel", "child", "none"}
_VALID_CHANNELS = {"imessage", "sms", "email", "push"}
_VALID_GAP_KINDS = {"stale_contact", "upcoming_birthday", "hosting_imbalance"}

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


def _dominant_gap_kind(parties: list[dict[str, Any]]) -> str:
    """Pick a gap_kind for the emitted event payload. Returns the most
    common kind across the surfaced parties; ties broken by first
    occurrence. Defaults to stale_contact if absent / malformed.
    The skill body still carries each party's individual gap_kind via
    its prompt rendering; this is the discriminator on the emitted
    event for downstream filtering."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for p in parties:
        if not isinstance(p, dict):
            continue
        gk = p.get("gap_kind")
        if not isinstance(gk, str) or gk not in _VALID_GAP_KINDS:
            continue
        if gk not in counts:
            order.append(gk)
            counts[gk] = 0
        counts[gk] += 1
    if not counts:
        return DEFAULT_GAP_KIND
    best = order[0]
    for kind in order:
        if counts[kind] > counts[best]:
            best = kind
    return best


class CrmSurfacePipeline:
    pack_id: str = "pipeline:crm_surface"
    version: str = "1.0.0"
    triggers: Triggers = {
        "schedule": "0 19 * * 0",
        "proactive": True,
    }

    def __init__(
        self,
        *,
        profile_loader: ProfileLoader | None = None,
        crm_targets_loader: CrmTargetsLoader | None = None,
        member_override_loader: MemberOverrideLoader | None = None,
    ) -> None:
        self._profile_loader: ProfileLoader = profile_loader or (lambda _mid: None)
        self._crm_targets_loader: CrmTargetsLoader = (
            crm_targets_loader or (lambda _week: [])
        )
        self._member_override_loader: MemberOverrideLoader = (
            member_override_loader or (lambda _mid: None)
        )

    async def gather(
        self,
        week_of_date: str,
    ) -> list[dict[str, Any]]:
        return list(self._crm_targets_loader(week_of_date))

    async def compose(
        self,
        ctx: PipelineContext,
        member_id: str,
        week_of_date: str,
        profile_format: str,
        surfaced_parties: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Call compose_crm_surface@v1 for one member. Returns the
        skill's output dict on success or None on a defensive-default
        skill failure (caller emits the sentinel crm.surfaced event)."""
        skill_inputs = {
            "member_id": member_id,
            "week_of_date": week_of_date,
            "profile_format": profile_format,
            "surfaced_parties": surfaced_parties,
        }
        try:
            result = await ctx.run_skill_fn(
                "compose_crm_surface", skill_inputs, ctx
            )
        except _SKILL_FAILURE_EXCEPTIONS as exc:
            _log.warning(
                "crm_surface: compose skill failed (%s); emitting "
                "sentinel per [§7.7]",
                type(exc).__name__,
            )
            return None
        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            return None
        return output

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
                "crm_surface: no observation_manager wired; treating "
                "as observation-mode-active per [D22]"
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

    async def _emit_crm_surfaced(
        self,
        ctx: PipelineContext,
        member_id: str,
        surfaced_party_ids: list[str],
        gap_kind: str,
        composed_text: str,
        channel: str,
        week_of_date: str,
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
            type="crm.surfaced",
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
                "gap_kind": gap_kind,
                "composed_text": composed_text,
                "channel": channel,
                "week_of_date": week_of_date,
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
        week_of_date: str,
        ctx: PipelineContext,
        max_surfaced: int = DEFAULT_MAX_SURFACED,
        triggering_event: dict[str, Any] | None = None,
    ) -> None:
        """Drive one CRM-surface emit for one member. Per-row skill
        failures emit the sentinel but do NOT propagate — a per-row
        failure must not abort the tick."""
        member_id = target["member_id"]
        parties: list[dict[str, Any]] = list(target.get("surfaced_parties") or [])
        clamped = parties[: max(0, max_surfaced)]
        if not clamped:
            return
        party_ids = [
            str(p["party_id"]) for p in clamped if isinstance(p, dict) and "party_id" in p
        ]
        if not party_ids:
            return
        gap_kind = _dominant_gap_kind(clamped)

        profile = self._profile_loader(member_id)
        profile_format = _coerce_profile_format(profile)
        composed = await self.compose(
            ctx, member_id, week_of_date, profile_format, clamped
        )
        if composed is None or not isinstance(composed.get("composed_text"), str) or not composed["composed_text"].strip() or composed.get("validation_failed", False):
            await self._emit_crm_surfaced(
                ctx,
                member_id=member_id,
                surfaced_party_ids=party_ids,
                gap_kind=gap_kind,
                composed_text="",
                channel=DEFAULT_DELIVERY_CHANNEL,
                week_of_date=week_of_date,
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
        await self._emit_crm_surfaced(
            ctx,
            member_id=member_id,
            surfaced_party_ids=party_ids,
            gap_kind=gap_kind,
            composed_text=composed_text,
            channel=channel,
            week_of_date=week_of_date,
            validation_failed=False,
            delivered=delivered,
            triggering_event=triggering_event,
        )

    async def dispatch(
        self,
        week_of_date: str,
        ctx: PipelineContext,
        triggering_event: dict[str, Any] | None = None,
        max_surfaced: int = DEFAULT_MAX_SURFACED,
    ) -> None:
        """Drive one tick: load CRM targets (one row per opt-in
        member), iterate, dispatch each via dispatch_one()."""
        targets = await self.gather(week_of_date)
        for target in targets:
            member_id = target.get("member_id") if isinstance(target, dict) else None
            if not isinstance(member_id, str):
                continue
            override = self._member_override_loader(member_id)
            opt_in = False
            if isinstance(override, dict):
                opt_in = bool(override.get("crm_proactive_enabled", False))
            if not opt_in:
                continue
            await self.dispatch_one(
                target,
                week_of_date,
                ctx,
                max_surfaced=max_surfaced,
                triggering_event=triggering_event,
            )

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        """Pipeline protocol entrypoint. Proactive packs are not
        currently dispatched by the in-process runner; this method is
        provided for protocol compliance and is exercised in tests by
        direct call. The expected payload carries `week_of_date` and,
        optionally, `max_surfaced_per_week` override."""
        payload = event.get("payload") or {}
        week_of_date = payload.get("week_of_date")
        if not isinstance(week_of_date, str):
            _log.warning(
                "crm_surface: handle() called without week_of_date; "
                "defensively skipping"
            )
            return
        max_surfaced = payload.get("max_surfaced_per_week")
        await self.dispatch(
            week_of_date,
            ctx,
            triggering_event=event,
            max_surfaced=(
                max_surfaced
                if isinstance(max_surfaced, int) and max_surfaced > 0
                else DEFAULT_MAX_SURFACED
            ),
        )
