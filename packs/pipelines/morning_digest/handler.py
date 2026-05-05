"""morning_digest proactive pipeline.

Per [BUILD.md §1287-1290] and [§7]. Cron-driven via OpenClaw
standing orders ([D1] Corollary, `bootstrap/openclaw/programs/
morning_digest.md`); the in-process PipelineRunner skips proactive
packs on absence of `triggers.events` (`runner.py:131-138`). This
handler is exercised by direct call from the proactive driver (or in
tests via fakes) — there is no bus subscription.

Validation guard per [BUILD.md §1289]: every claimed calendar event /
commitment / task id in the skill's `claimed_event_ids` is verified
against the gather payload post-composition. Any fabrication zeroes
the message with the sentinel "No morning brief available; underlying
data changed." On the sentinel path: `digest.composed` emits with
`validation_failed=true` and `delivered=false`; `outbound()` is NOT
called.

The single `outbound()` call site is `deliver()` per [§6.14].
Observation mode short-circuits delivery transparently (the bus
records `observation.suppressed`); on suppression `delivered=false`.

Constructor-injection loaders per PM-27 (mirroring 10c-i's
reward_dispatch shape): real loader modules don't exist on main yet,
so the pack ships with no-op defaults that exercise the
defensive-default sentinel paths. Tests inject fakes; future bootstrap
wiring or a runner-side seam injects real loaders.

Scope notes:
- This pack does NOT introduce real on-disk profile / persona /
  projection-loader modules — UT-14 carry-forward.
- Per-profile `delivery_channel` resolution happens at the call site
  (or via config.yaml in a future bootstrap §3 prompt); v1 hardcodes
  the channel string in `deliver()`.
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
PersonaLoader = Callable[[], dict[str, Any] | None]
TasksLoader = Callable[[str, str], list[dict[str, Any]]]
CommitmentsLoader = Callable[[str, str], list[dict[str, Any]]]
CalendarsLoader = Callable[[str, str], list[dict[str, Any]]]
RecurrencesLoader = Callable[[str, str], list[dict[str, Any]]]

SENTINEL_BODY = "No morning brief available; underlying data changed."
DEFAULT_PROFILE_FORMAT = "none"
DEFAULT_DELIVERY_CHANNEL = "imessage"

_VALID_FORMATS = {"fog_aware", "compressed", "carousel", "child", "none"}

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
    """Resolve `profile_format` from the member profile; defensive default
    `none` per the SKILL.md fallback behavior."""
    if not isinstance(profile, dict):
        return DEFAULT_PROFILE_FORMAT
    fmt = profile.get("profile_format")
    if isinstance(fmt, str) and fmt in _VALID_FORMATS:
        return fmt
    return DEFAULT_PROFILE_FORMAT


def _ids_from(items: list[dict[str, Any]] | None) -> set[str]:
    if not items:
        return set()
    return {str(item["id"]) for item in items if isinstance(item, dict) and "id" in item}


class MorningDigestPipeline:
    pack_id: str = "pipeline:morning_digest"
    version: str = "1.0.0"
    triggers: Triggers = {
        "schedule": "0 7 * * *",
        "proactive": True,
    }

    def __init__(
        self,
        *,
        profile_loader: ProfileLoader | None = None,
        persona_loader: PersonaLoader | None = None,
        tasks_loader: TasksLoader | None = None,
        commitments_loader: CommitmentsLoader | None = None,
        calendars_loader: CalendarsLoader | None = None,
        recurrences_loader: RecurrencesLoader | None = None,
    ) -> None:
        self._profile_loader: ProfileLoader = profile_loader or (lambda _mid: None)
        self._persona_loader: PersonaLoader = persona_loader or (lambda: None)
        self._tasks_loader: TasksLoader = tasks_loader or (lambda _mid, _today: [])
        self._commitments_loader: CommitmentsLoader = commitments_loader or (
            lambda _mid, _today: []
        )
        self._calendars_loader: CalendarsLoader = calendars_loader or (
            lambda _mid, _today: []
        )
        self._recurrences_loader: RecurrencesLoader = recurrences_loader or (
            lambda _mid, _today: []
        )

    async def gather(
        self, member_id: str, today_iso: str
    ) -> dict[str, Any]:
        """Aggregate projection state into a single payload. Loaders are
        synchronous callables; real on-disk loaders will likely become
        thin wrappers around `asyncio.to_thread(...)` per [D14]."""
        profile = self._profile_loader(member_id)
        persona = self._persona_loader()
        tasks = list(self._tasks_loader(member_id, today_iso))
        commitments = list(self._commitments_loader(member_id, today_iso))
        calendars = list(self._calendars_loader(member_id, today_iso))
        recurrences = list(self._recurrences_loader(member_id, today_iso))
        profile_format = _coerce_profile_format(profile)
        return {
            "member_id": member_id,
            "today_iso": today_iso,
            "profile": profile,
            "persona": persona,
            "profile_format": profile_format,
            "tasks": tasks,
            "commitments": commitments,
            "calendars": calendars,
            "recurrences": recurrences,
            "inbox_count": 0,
            "streak_status": {},
            "reward_stats": {},
        }

    async def compose(
        self,
        ctx: PipelineContext,
        gather_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Call compose_morning_digest@v3. Returns the skill's output dict
        on success, or None on a defensive-default skill failure
        (caller emits the sentinel digest.composed event)."""
        skill_inputs = {
            "member_id": gather_result["member_id"],
            "today_iso": gather_result["today_iso"],
            "profile_format": gather_result["profile_format"],
            "tasks": gather_result["tasks"],
            "commitments": gather_result["commitments"],
            "calendars": gather_result["calendars"],
            "recurrences": gather_result["recurrences"],
            "inbox_count": gather_result["inbox_count"],
            "streak_status": gather_result["streak_status"],
            "reward_stats": gather_result["reward_stats"],
        }
        try:
            result = await ctx.run_skill_fn(
                "compose_morning_digest", skill_inputs, ctx
            )
        except _SKILL_FAILURE_EXCEPTIONS as exc:
            _log.warning(
                "morning_digest: compose skill failed (%s); "
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
        gather_result: dict[str, Any],
        composed: dict[str, Any],
    ) -> bool:
        """Run the validation guard per [BUILD.md §1289]: every id the
        skill claimed to reference must appear in the gather payload.
        Returns True if every claim is verified, False if any miss."""
        claimed = composed.get("claimed_event_ids", [])
        if not isinstance(claimed, list):
            return False
        if composed.get("validation_failed", False):
            return False
        body = composed.get("body_text")
        if not isinstance(body, str) or not body.strip():
            return False
        valid_ids: set[str] = set()
        valid_ids |= _ids_from(gather_result.get("tasks"))
        valid_ids |= _ids_from(gather_result.get("commitments"))
        valid_ids |= _ids_from(gather_result.get("calendars"))
        valid_ids |= _ids_from(gather_result.get("recurrences"))
        for cid in claimed:
            if str(cid) not in valid_ids:
                return False
        return True

    def _resolve_delivery_channel(
        self, profile: dict[str, Any] | None
    ) -> str:
        if isinstance(profile, dict):
            channel = profile.get("delivery_channel")
            if isinstance(channel, str) and channel:
                return channel
        return DEFAULT_DELIVERY_CHANNEL

    async def deliver(
        self,
        ctx: PipelineContext,
        member_id: str,
        body_text: str,
        channel: str,
    ) -> bool:
        """Single `outbound()` call site per [§6.14]. Returns True when
        the bus recorded `external.sent`, False on suppression. The
        outbound action_fn is a no-op in v1 (the real channel-transport
        adapter wires in via a future per-channel adapter prompt)."""
        if ctx.observation_manager is None:
            _log.info(
                "morning_digest: no observation_manager wired; "
                "treating as observation-mode-active (default-on for "
                "proactive pipelines per [BUILD.md §98])"
            )
            return False

        async def _no_op_send() -> None:
            return None

        from adminme.lib.observation import outbound

        result = await outbound(
            ctx.session,
            "digest_send",
            {"member_id": member_id, "body_text": body_text},
            _no_op_send,
            manager=ctx.observation_manager,
            event_log=ctx.event_log,
            target_channel=channel,
            target_identifier=member_id,
        )
        return not result.suppressed

    async def _emit_digest_composed(
        self,
        ctx: PipelineContext,
        member_id: str,
        body_text: str,
        profile_format: str,
        validation_failed: bool,
        delivered: bool,
        today_iso: str,
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
            type="digest.composed",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="morning_digest",
            source_account_id="pipeline",
            owner_scope=owner_scope,
            visibility_scope=visibility_scope,
            sensitivity="normal",
            payload={
                "member_id": member_id,
                "body_text": body_text,
                "profile_format": profile_format,
                "validation_failed": validation_failed,
                "delivered": delivered,
                "today_iso": today_iso,
            },
        )
        await ctx.event_log.append(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def dispatch(
        self,
        member_id: str,
        today_iso: str,
        ctx: PipelineContext,
        triggering_event: dict[str, Any] | None = None,
    ) -> None:
        """Drive one digest cycle for one member. Used by the proactive
        driver (cron / OpenClaw standing orders) in production and by
        tests directly. Idempotence + once-per-dispatch outbound() are
        guaranteed by the caller (one cron tick = one dispatch)."""
        gather_result = await self.gather(member_id, today_iso)
        profile_format = gather_result["profile_format"]
        composed = await self.compose(ctx, gather_result)
        if composed is None:
            await self._emit_digest_composed(
                ctx,
                member_id=member_id,
                body_text=SENTINEL_BODY,
                profile_format=profile_format,
                validation_failed=True,
                delivered=False,
                today_iso=today_iso,
                triggering_event=triggering_event,
            )
            return

        if not self.validate(gather_result, composed):
            await self._emit_digest_composed(
                ctx,
                member_id=member_id,
                body_text=SENTINEL_BODY,
                profile_format=profile_format,
                validation_failed=True,
                delivered=False,
                today_iso=today_iso,
                triggering_event=triggering_event,
            )
            return

        body_text = composed["body_text"]
        channel = self._resolve_delivery_channel(gather_result.get("profile"))
        delivered = await self.deliver(ctx, member_id, body_text, channel)
        await self._emit_digest_composed(
            ctx,
            member_id=member_id,
            body_text=body_text,
            profile_format=profile_format,
            validation_failed=False,
            delivered=delivered,
            today_iso=today_iso,
            triggering_event=triggering_event,
        )

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        """Pipeline protocol entrypoint. Proactive packs are not
        currently dispatched by the in-process runner (the runner skips
        on absence of `triggers.events` per `runner.py:131-138`); this
        method is provided for protocol compliance and is exercised in
        tests by direct call. The expected payload carries `member_id`
        and `today_iso`."""
        payload = event.get("payload") or {}
        member_id = payload.get("member_id")
        today_iso = payload.get("today_iso")
        if not isinstance(member_id, str) or not isinstance(today_iso, str):
            _log.warning(
                "morning_digest: handle() called without member_id / "
                "today_iso; defensively skipping"
            )
            return
        await self.dispatch(member_id, today_iso, ctx, triggering_event=event)
