"""paralysis_detection proactive pipeline.

Per [BUILD.md §1297-1302] and [§7]. Cron-driven via OpenClaw standing
orders ([D1] Corollary, `bootstrap/openclaw/programs/
paralysis_detection.md`); the in-process PipelineRunner skips
proactive packs on absence of `triggers.events`
(`runner.py:131-138`). This handler is exercised by direct call from
the proactive driver (or in tests via fakes) — there is no bus
subscription.

**Deterministic** per [BUILD.md §1297-1302] and operating rule 20
([BUILD.md §124]): NEVER invokes an LLM. Template selection is a
deterministic round-robin seeded by ``(member_id, today_iso)`` so
the same member gets the same template on the same day across
re-invocations, but different members get different templates.

Pre-conditions per [BUILD.md §1300]: zero completions in prior 2
hours AND energy level ≤ low AND now within member's fog window.
Any miss → defensive skip (no event emits, no error raises).

v1 surface is inbox-only per [BUILD.md §1302] ("inbox + optional
outbound"); the optional-outbound branch is deferred to a future
per-profile-config-aware prompt. **paralysis_detection does NOT call
``outbound()`` in v1.** Tests assert this absence.

Constructor-injection loaders per PM-27 (mirroring 10c-i's
reward_dispatch shape): real loader modules don't exist on main yet,
so the pack ships with no-op defaults that exercise the
defensive-skip paths. Tests inject fakes; future bootstrap wiring
or a runner-side seam injects real loaders. The ``energy_loader``
returns ``dict | None`` per [BUILD.md §1299]; the
``adminme_energy_states`` projection is not yet on main, so the
default loader returns ``None`` and the pre-condition check fails
defensively.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Callable

from adminme.events.envelope import EventEnvelope
from adminme.pipelines.base import PipelineContext, Triggers

_log = logging.getLogger(__name__)

ProfileLoader = Callable[[str], dict[str, Any] | None]
PersonaLoader = Callable[[], dict[str, Any] | None]
TasksLoader = Callable[[str, str], list[dict[str, Any]]]
CommitmentsLoader = Callable[[str, str], list[dict[str, Any]]]
EnergyLoader = Callable[[str], dict[str, Any] | None]

DEFAULT_FOG_WINDOW = ["15:00", "17:00"]
ENERGY_LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}


def _seed_from_member_and_date(member_id: str, today_iso: str) -> int:
    """Deterministic round-robin seed. SHA-1 of the joined string keyed
    on (member, day) so the same member/day always produces the same
    seed, but different members or different days rotate the
    selection. Truncate to 8 hex chars for a stable ~2^32 seed."""
    raw = f"{member_id}|{today_iso}".encode("utf-8")
    digest = hashlib.sha1(raw).hexdigest()[:8]
    return int(digest, 16)


def _now_hhmm(now_iso: str) -> str:
    """Extract HH:MM from an ISO 8601 datetime or HH:MM string. The
    pre-condition compares against fog_window members in HH:MM form.
    Defensive default on parse failure: empty string (which fails the
    fog-window check and triggers defensive skip)."""
    if len(now_iso) >= 5 and now_iso[2] == ":":
        return now_iso[:5]
    if "T" in now_iso:
        suffix = now_iso.split("T", 1)[1]
        return suffix[:5] if len(suffix) >= 5 else ""
    return ""


def _within_fog_window(
    now_hhmm: str, fog_window: list[str] | None
) -> bool:
    """Fog window is [start_hhmm, end_hhmm] inclusive. Defensive
    default: if the window is missing or malformed, treat as
    out-of-window."""
    if not fog_window or len(fog_window) != 2:
        return False
    start, end = fog_window[0], fog_window[1]
    if not now_hhmm:
        return False
    return start <= now_hhmm <= end


def _energy_is_low_or_below(energy: dict[str, Any] | None) -> bool:
    if not isinstance(energy, dict):
        return False
    level = energy.get("level")
    if not isinstance(level, str):
        return False
    return ENERGY_LEVEL_ORDER.get(level, 99) <= ENERGY_LEVEL_ORDER["low"]


def _has_recent_completion(
    tasks: list[dict[str, Any]],
    commitments: list[dict[str, Any]],
) -> bool:
    """For v1, the loader contract is to return ONLY items completed
    in the prior 2 hours. Caller-shaped: if either list is non-empty,
    a recent completion exists."""
    return bool(tasks or commitments)


class ParalysisDetectionPipeline:
    pack_id: str = "pipeline:paralysis_detection"
    version: str = "1.0.0"
    triggers: Triggers = {
        "schedule": "0 15,17 * * *",
        "proactive": True,
    }

    def __init__(
        self,
        *,
        profile_loader: ProfileLoader | None = None,
        persona_loader: PersonaLoader | None = None,
        tasks_loader: TasksLoader | None = None,
        commitments_loader: CommitmentsLoader | None = None,
        energy_loader: EnergyLoader | None = None,
    ) -> None:
        self._profile_loader: ProfileLoader = profile_loader or (lambda _mid: None)
        self._persona_loader: PersonaLoader = persona_loader or (lambda: None)
        self._tasks_loader: TasksLoader = tasks_loader or (
            lambda _mid, _now: []
        )
        self._commitments_loader: CommitmentsLoader = commitments_loader or (
            lambda _mid, _now: []
        )
        self._energy_loader: EnergyLoader = energy_loader or (
            lambda _mid: None
        )

    def check_preconditions(
        self,
        member_id: str,
        now_iso: str,
    ) -> bool:
        """Returns True iff: zero completions in prior 2 hours AND
        energy ≤ low AND now within member's fog window. If profile
        is None, the entire pipeline defensively skips — no
        member-tuned signal to fire on per [§7.7]."""
        profile = self._profile_loader(member_id)
        if profile is None:
            return False
        tasks = list(self._tasks_loader(member_id, now_iso))
        commitments = list(self._commitments_loader(member_id, now_iso))
        if _has_recent_completion(tasks, commitments):
            return False
        energy = self._energy_loader(member_id)
        if not _energy_is_low_or_below(energy):
            return False
        fog_window = profile.get("fog_window") or DEFAULT_FOG_WINDOW
        return _within_fog_window(_now_hhmm(now_iso), fog_window)

    def select_template(
        self,
        persona: dict[str, Any] | None,
        member_id: str,
        today_iso: str,
    ) -> dict[str, str] | None:
        """Deterministic round-robin seeded by (member_id, today_iso).
        Returns a `{"id": ..., "text": ...}` mapping or None when
        persona is None or paralysis_templates is empty (defensive
        skip per [§7.7])."""
        if not isinstance(persona, dict):
            return None
        templates = persona.get("paralysis_templates") or []
        if not isinstance(templates, list) or not templates:
            return None
        seed = _seed_from_member_and_date(member_id, today_iso)
        chosen = templates[seed % len(templates)]
        if isinstance(chosen, dict):
            tid = str(chosen.get("id") or f"paralysis-{seed % len(templates)}")
            ttext = str(chosen.get("text") or "")
            if not ttext:
                return None
            return {"id": tid, "text": ttext}
        if isinstance(chosen, str) and chosen:
            return {"id": f"paralysis-{seed % len(templates)}", "text": chosen}
        return None

    async def _emit_paralysis_triggered(
        self,
        ctx: PipelineContext,
        member_id: str,
        template_id: str,
        template_text: str,
        triggered_at: str,
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
            type="paralysis.triggered",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="paralysis_detection",
            source_account_id="pipeline",
            owner_scope=owner_scope,
            visibility_scope=visibility_scope,
            sensitivity="normal",
            payload={
                "member_id": member_id,
                "template_id": template_id,
                "template_text": template_text,
                "triggered_at": triggered_at,
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
        now_iso: str,
        ctx: PipelineContext,
        triggering_event: dict[str, Any] | None = None,
    ) -> None:
        """Drive one paralysis-detection cycle for one member. Used by
        the proactive driver (cron / OpenClaw standing orders) in
        production and by tests directly. On any pre-condition miss
        or persona-empty case, defensively skips (no event emits per
        [§7.7]).

        Today-iso is derived from now_iso's date prefix (first 10
        chars of an ISO date or the value as-is for HH:MM)."""
        if not self.check_preconditions(member_id, now_iso):
            return
        persona = self._persona_loader()
        today_iso = now_iso[:10] if len(now_iso) >= 10 else now_iso
        template = self.select_template(persona, member_id, today_iso)
        if template is None:
            return
        await self._emit_paralysis_triggered(
            ctx,
            member_id=member_id,
            template_id=template["id"],
            template_text=template["text"],
            triggered_at=now_iso,
            triggering_event=triggering_event,
        )

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        """Pipeline protocol entrypoint. Proactive packs are not
        currently dispatched by the in-process runner; this method is
        provided for protocol compliance and is exercised in tests by
        direct call. The expected payload carries `member_id` and
        `now_iso`."""
        payload = event.get("payload") or {}
        member_id = payload.get("member_id")
        now_iso = payload.get("now_iso")
        if not isinstance(member_id, str) or not isinstance(now_iso, str):
            _log.warning(
                "paralysis_detection: handle() called without "
                "member_id / now_iso; defensively skipping"
            )
            return
        await self.dispatch(member_id, now_iso, ctx, triggering_event=event)
