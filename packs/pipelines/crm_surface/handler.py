"""crm_surface proactive pipeline.

Per [BUILD.md §L4] and [§7]. Cron-driven via OpenClaw standing orders
([D1] Corollary, `bootstrap/openclaw/programs/crm_surface.md`); the
in-process PipelineRunner skips proactive packs on absence of
`triggers.events` (`runner.py:131-138`). This handler is exercised
by direct call from the proactive driver (or in tests via fakes) —
there is no bus subscription.

Scan source: a constructor-injected ``opted_in_members_loader`` that
returns members whose effective ``crm_proactive_enabled`` flag is
true, and a ``stale_parties_loader`` that returns stale parties per
member. Per [D22] opt-in (not opt-out): the default is OFF; this
pipeline does NOT fire for members who haven't explicitly enabled
proactive CRM surfacing.

Per-row defensive default per [§7.7]: a skill failure on one member
emits NO event for that member but continues processing the rest.
Unlike reminder_dispatch (which emits with composed_text=""), CRM
surface composes only when there's text worth surfacing; an empty
weekly digest is no digest.

Single outbound call site per [§6.14]: ``_deliver()`` routes through
``adminme.lib.observation.outbound`` which gates on
``ObservationManager.is_active()`` per [§6.13]. On suppression the
``crm_surface.composed`` event is still appended.

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
OptedInMembersLoader = Callable[[], list[str]]
StalePartiesLoader = Callable[[str, int], list[dict[str, Any]]]

DEFAULT_THRESHOLD_DAYS = 90
DEFAULT_MAX_SURFACED = 5
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
        profile_loader: ProfileLoader | None = None,
        opted_in_members_loader: OptedInMembersLoader | None = None,
        stale_parties_loader: StalePartiesLoader | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._profile_loader: ProfileLoader = profile_loader or (
            lambda _mid: None
        )
        self._opted_in_members_loader: OptedInMembersLoader = (
            opted_in_members_loader or (lambda: [])
        )
        self._stale_parties_loader: StalePartiesLoader = (
            stale_parties_loader or (lambda _mid, _days: [])
        )
        self._config: dict[str, Any] = config or {}

    def _config_for_member(self, member_id: str) -> dict[str, Any]:
        overrides = (
            self._config.get("per_member_overrides", {}).get(member_id)
            or {}
        )
        return {
            "crm_proactive_enabled": overrides.get(
                "crm_proactive_enabled", False
            ),
            "threshold_days": overrides.get(
                "threshold_days",
                self._config.get("threshold_days", DEFAULT_THRESHOLD_DAYS),
            ),
            "max_surfaced_per_week": self._config.get(
                "max_surfaced_per_week", DEFAULT_MAX_SURFACED
            ),
        }

    async def _compose(
        self,
        ctx: PipelineContext,
        member_profile: dict[str, Any] | None,
        surfaced_parties: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        skill_inputs = {
            "member_profile": member_profile or {},
            "surfaced_parties": surfaced_parties,
        }
        try:
            result = await ctx.run_skill_fn(
                "compose_crm_surface", skill_inputs, ctx
            )
        except _SKILL_FAILURE_EXCEPTIONS as exc:
            _log.warning(
                "crm_surface: compose skill failed for member (%s); "
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
        surfaced_party_ids: list[str],
        composed_text: str,
        channel: str,
        week_of_iso: str,
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
                "week_of_date": week_of_iso,
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
                "crm_surface: no observation_manager wired; treating "
                "as observation-mode-active per [§6.16]"
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

    async def _process_member(
        self,
        ctx: PipelineContext,
        member_id: str,
        cfg: dict[str, Any],
        week_of_iso: str,
        triggering_event: dict[str, Any] | None,
    ) -> None:
        """One opted-in member → maybe one crm_surface.composed +
        maybe one outbound send. Per [§7.7] one member's skill failure
        does not abort the tick."""
        if not cfg["crm_proactive_enabled"]:
            return
        stale = list(
            self._stale_parties_loader(member_id, cfg["threshold_days"])
        )
        if not stale:
            return
        clamped = stale[: cfg["max_surfaced_per_week"]]
        surfaced_party_ids = [
            str(p["id"]) for p in clamped if isinstance(p, dict) and "id" in p
        ]
        if not surfaced_party_ids:
            return
        member_profile = self._profile_loader(member_id)
        composed = await self._compose(ctx, member_profile, clamped)
        if composed is None:
            return
        composed_text = composed.get("composed_text") or ""
        if not composed_text:
            return
        channel = _coerce_channel(composed.get("suggested_channel"))
        sort_order = composed.get("sort_order")
        if isinstance(sort_order, list) and sort_order:
            ordered: list[str] = []
            for sid in sort_order:
                ssid = str(sid)
                if ssid in surfaced_party_ids and ssid not in ordered:
                    ordered.append(ssid)
            for sid in surfaced_party_ids:
                if sid not in ordered:
                    ordered.append(sid)
            surfaced_party_ids = ordered
        await self._emit_composed(
            ctx,
            member_id=member_id,
            surfaced_party_ids=surfaced_party_ids,
            composed_text=composed_text,
            channel=channel,
            week_of_iso=week_of_iso,
            triggering_event=triggering_event,
        )
        await self._deliver(ctx, member_id, composed_text, channel)

    async def dispatch(
        self,
        week_of_iso: str,
        ctx: PipelineContext,
        triggering_event: dict[str, Any] | None = None,
    ) -> None:
        """Drive one weekly tick. Iterates opted-in members; per-member
        skill failure does not abort the tick per [§7.7]."""
        member_ids = list(self._opted_in_members_loader())
        for member_id in member_ids:
            cfg = self._config_for_member(member_id)
            try:
                await self._process_member(
                    ctx, member_id, cfg, week_of_iso, triggering_event
                )
            except Exception:  # noqa: BLE001
                _log.exception(
                    "crm_surface: member %s row failed; continuing tick",
                    member_id,
                )
                continue

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        """Pipeline protocol entrypoint. Proactive packs are not
        currently dispatched by the in-process runner; this method is
        provided for protocol compliance and is exercised in tests by
        direct call. The expected payload carries ``week_of_iso``."""
        payload = event.get("payload") or {}
        week_of_iso = payload.get("week_of_iso")
        if not isinstance(week_of_iso, str):
            _log.warning(
                "crm_surface: handle() called without week_of_iso; "
                "defensively skipping"
            )
            return
        await self.dispatch(week_of_iso, ctx, triggering_event=event)
