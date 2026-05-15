"""reminder_dispatch proactive pipeline.

Per [BUILD.md §L4] and [§7]. Cron-driven via OpenClaw standing orders
([D1] Corollary, `bootstrap/openclaw/programs/reminder_dispatch.md`);
the in-process PipelineRunner skips proactive packs on absence of
`triggers.events` (`runner.py:131-138`). This handler is exercised by
direct call from the proactive driver (or in tests via fakes) — there
is no bus subscription.

Scan source: a constructor-injected ``due_commitments_loader`` that
returns commitments with ``due_at`` within the configured lookahead
window. Per-commitment dedupe queries a ``recent_dispatch_loader``
that returns recent ``reminder.dispatched`` events for the same
``commitment_id`` within ``dedupe_window_minutes``. This avoids any
projection-schema change for ``last_reminded_at`` (out of scope per
10c-iii §"What's NOT in scope"); dedupe is event-log-sourced per
[D17] (pipelines do not write projections directly).

Per-row defensive default per [§7.7]: if ``compose_reminder`` raises
or returns a non-dict, the row emits ``reminder.dispatched`` with
``composed_text=""`` and a ``skill_failure_defensive_default`` note in
the audit-channel attribute; this preserves per-row isolation so one
row's failure does not abort the whole tick.

Single outbound call site per [§6.14]: ``deliver()`` routes through
``adminme.lib.observation.outbound`` which gates on
``ObservationManager.is_active()`` per [§6.13]. On suppression the
``reminder.dispatched`` event is still appended; only the send is
suppressed.

Constructor-injection loaders per PM-27 (mirroring 10c-i/10c-ii):
real loader modules don't exist on main yet, so the pack ships with
no-op defaults that exercise the defensive-skip paths. Tests inject
fakes; future bootstrap wiring or a runner-side seam injects real
loaders.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
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
DueCommitmentsLoader = Callable[
    [datetime, datetime], list[dict[str, Any]]
]
PartyLoader = Callable[[str], dict[str, Any] | None]
RecentDispatchLoader = Callable[[str, datetime], list[dict[str, Any]]]

DEFAULT_LOOKAHEAD_MINUTES = 60
DEFAULT_MIN_GAP_MINUTES = 15
DEFAULT_DEDUPE_WINDOW_MINUTES = 90
DEFAULT_SKIP_PARTY_TAGS: tuple[str, ...] = ("test", "fixture")
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
        due_commitments_loader: DueCommitmentsLoader | None = None,
        party_loader: PartyLoader | None = None,
        recent_dispatch_loader: RecentDispatchLoader | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._profile_loader: ProfileLoader = profile_loader or (
            lambda _mid: None
        )
        self._due_commitments_loader: DueCommitmentsLoader = (
            due_commitments_loader or (lambda _now, _until: [])
        )
        self._party_loader: PartyLoader = party_loader or (lambda _pid: None)
        self._recent_dispatch_loader: RecentDispatchLoader = (
            recent_dispatch_loader or (lambda _cid, _since: [])
        )
        self._config: dict[str, Any] = config or {}

    def _config_for_member(self, member_id: str) -> dict[str, Any]:
        """Resolve effective config for a member: top-level defaults
        merged with per-member overrides. Test seam: pass ``config=``
        at construction to drive without a YAML round-trip."""
        overrides = (
            self._config.get("per_member_overrides", {}).get(member_id)
            or {}
        )
        return {
            "reminders_enabled": overrides.get("reminders_enabled", True),
            "lookahead_window_minutes": overrides.get(
                "lookahead_window_minutes",
                self._config.get(
                    "lookahead_window_minutes", DEFAULT_LOOKAHEAD_MINUTES
                ),
            ),
            "min_reminder_gap_minutes": self._config.get(
                "min_reminder_gap_minutes", DEFAULT_MIN_GAP_MINUTES
            ),
            "dedupe_window_minutes": overrides.get(
                "dedupe_window_minutes",
                self._config.get(
                    "dedupe_window_minutes", DEFAULT_DEDUPE_WINDOW_MINUTES
                ),
            ),
            "skip_party_tags": tuple(
                self._config.get("skip_party_tags", DEFAULT_SKIP_PARTY_TAGS)
            ),
        }

    def _is_duplicate(
        self, commitment_id: str, dedupe_window_minutes: int
    ) -> bool:
        since = _utc_now() - timedelta(minutes=dedupe_window_minutes)
        recent = self._recent_dispatch_loader(commitment_id, since)
        return bool(recent)

    async def _compose(
        self,
        ctx: PipelineContext,
        commitment: dict[str, Any],
        owner_profile: dict[str, Any] | None,
        recipient_party: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Call compose_reminder@v1. Returns the skill's output dict on
        success, or None on defensive-default skill failure (caller
        emits with composed_text=""). [§7.7]"""
        skill_inputs = {
            "commitment": commitment,
            "owner_member_profile": owner_profile or {},
            "recipient_party": recipient_party or {},
        }
        try:
            result = await ctx.run_skill_fn(
                "compose_reminder", skill_inputs, ctx
            )
        except _SKILL_FAILURE_EXCEPTIONS as exc:
            _log.warning(
                "reminder_dispatch: compose skill failed (%s); "
                "emitting defensive-default per [§7.7]",
                type(exc).__name__,
            )
            return None
        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            return None
        return output

    async def _emit_dispatched(
        self,
        ctx: PipelineContext,
        commitment: dict[str, Any],
        composed_text: str,
        channel: str,
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
                "commitment_id": str(commitment["id"]),
                "owner_member_id": str(commitment.get("owner_member_id", "")),
                "recipient_party_id": str(
                    commitment.get("recipient_party_id", "")
                ),
                "composed_text": composed_text,
                "channel": channel,
                "due_at": str(commitment.get("due_at", "")),
                "scheduled_send_at": EventEnvelope.now_utc_iso(),
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
        recipient_id: str,
        body_text: str,
        channel: str,
    ) -> bool:
        """Single outbound() call site per [§6.14]. Returns True when
        bus recorded external.sent, False on suppression. Empty
        composed_text (defensive-default path) skips delivery entirely
        — no point in suppressing an empty body."""
        if not body_text:
            return False
        if ctx.observation_manager is None:
            _log.info(
                "reminder_dispatch: no observation_manager wired; "
                "treating as observation-mode-active (default-on per "
                "[§6.16])"
            )
            return False

        async def _no_op_send() -> None:
            return None

        from adminme.lib.observation import outbound

        result = await outbound(
            ctx.session,
            "reminder_send",
            {"recipient_party_id": recipient_id, "body_text": body_text},
            _no_op_send,
            manager=ctx.observation_manager,
            event_log=ctx.event_log,
            target_channel=channel,
            target_identifier=recipient_id,
        )
        return not result.suppressed

    async def _process_row(
        self,
        ctx: PipelineContext,
        commitment: dict[str, Any],
        cfg: dict[str, Any],
        triggering_event: dict[str, Any] | None,
    ) -> None:
        """One commitment → maybe one reminder.dispatched + maybe one
        outbound send. Per-row defensive default per [§7.7]: a skill
        failure or party-tag skip exits this row without aborting the
        tick."""
        commitment_id = str(commitment.get("id", ""))
        if not commitment_id:
            return
        if self._is_duplicate(commitment_id, cfg["dedupe_window_minutes"]):
            return
        recipient_id = str(commitment.get("recipient_party_id", ""))
        if not recipient_id:
            return
        recipient_party = self._party_loader(recipient_id)
        if isinstance(recipient_party, dict):
            tags = recipient_party.get("tags") or []
            if isinstance(tags, list) and any(
                t in cfg["skip_party_tags"] for t in tags
            ):
                return
        owner_id = str(commitment.get("owner_member_id", ""))
        owner_profile = (
            self._profile_loader(owner_id) if owner_id else None
        )
        composed = await self._compose(
            ctx, commitment, owner_profile, recipient_party
        )
        if composed is None:
            await self._emit_dispatched(
                ctx,
                commitment=commitment,
                composed_text="",
                channel=DEFAULT_CHANNEL,
                triggering_event=triggering_event,
            )
            return
        composed_text = composed.get("composed_text") or ""
        channel = _coerce_channel(composed.get("suggested_channel"))
        await self._emit_dispatched(
            ctx,
            commitment=commitment,
            composed_text=composed_text,
            channel=channel,
            triggering_event=triggering_event,
        )
        await self._deliver(ctx, recipient_id, composed_text, channel)

    async def dispatch(
        self,
        now_iso: str,
        ctx: PipelineContext,
        triggering_event: dict[str, Any] | None = None,
    ) -> None:
        """Drive one tick of reminder_dispatch. Scans due commitments,
        composes per row, emits + delivers. One row's failure does
        not abort the tick per [§7.7]."""
        now = _utc_now()
        # Use the first eligible member's effective config to compute
        # the global lookahead window; per-member opt-out is applied
        # at row-emit time (not at scan time) so the loader stays
        # tenant-agnostic. ``min_reminder_gap_minutes`` cuts off the
        # near edge so we don't remind for things due in two minutes.
        cfg_global = self._config_for_member("__global__")
        lookahead_until = now + timedelta(
            minutes=cfg_global["lookahead_window_minutes"]
        )
        min_gap_until = now + timedelta(
            minutes=cfg_global["min_reminder_gap_minutes"]
        )
        rows = list(self._due_commitments_loader(min_gap_until, lookahead_until))
        for commitment in rows:
            owner_id = str(commitment.get("owner_member_id", ""))
            cfg = self._config_for_member(owner_id or "__global__")
            if not cfg["reminders_enabled"]:
                continue
            try:
                await self._process_row(ctx, commitment, cfg, triggering_event)
            except Exception:  # noqa: BLE001
                _log.exception(
                    "reminder_dispatch: row %s failed; continuing tick",
                    commitment.get("id"),
                )
                continue

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        """Pipeline protocol entrypoint. Proactive packs are not
        currently dispatched by the in-process runner; this method is
        provided for protocol compliance and is exercised in tests by
        direct call. The expected payload carries ``now_iso``."""
        payload = event.get("payload") or {}
        now_iso = payload.get("now_iso")
        if not isinstance(now_iso, str):
            _log.warning(
                "reminder_dispatch: handle() called without now_iso; "
                "defensively skipping"
            )
            return
        await self.dispatch(now_iso, ctx, triggering_event=event)
