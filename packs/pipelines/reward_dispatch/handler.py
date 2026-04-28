"""reward_dispatch reactive pipeline.

Per [BUILD.md §1207-1210] and [§7]. Subscribes to ``task.completed`` and
``commitment.completed`` per [BUILD.md §3130 bullet 1]. Emits ``reward.ready``
v1 per [BUILD.md §1620, CONSOLE_PATTERNS.md §8 — supersedes §1210 typo
``adminme.reward.dispatched``]. The console's SSE layer fans the event out
to the member's open tabs per [CONSOLE_PATTERNS.md §8]; this pipeline does
NOT call ``outbound()`` — there's no channel-transport invocation here.

Tier sampling is deterministic: seed from the source ``event_id`` so the
same event always rolls the same tier. Re-processing on subscriber rewind
must not double-toast a different reward. Distribution comes from the
member's profile manifest's ``reward_distribution`` per [BUILD.md §1884].
Template comes from the persona pack's ``reward_templates.yaml`` per
[BUILD.md §PERSONA PACKS].

Defensive-default discipline per [§7.7]: a missing profile or missing
persona template is absorbed by the default-done sentinel — emit
``reward.ready`` with tier=``done`` and a sentinel template — never raise.

Profile and persona loaders are not yet on disk in 10c-i; this handler
takes them as constructor parameters so the integration test can supply
fakes and a future bootstrap-side wiring can pass real loaders. The
constructor defaults to no-op loaders that always return ``None``,
exercising the defensive-default path. This carry-forward will resolve
once the profile / persona loader modules ship.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable

from adminme.events.envelope import EventEnvelope
from adminme.pipelines.base import PipelineContext, Triggers

_log = logging.getLogger(__name__)

ProfileLoader = Callable[[str], dict[str, Any] | None]
PersonaLoader = Callable[[], dict[str, Any] | None]

DEFAULT_TIER = "done"
DEFAULT_TEMPLATE_ID = "default-done"
DEFAULT_TEMPLATE_TEXT = "✓"

_TIER_ORDER = ("done", "warm", "delight", "jackpot")


def _seed_from_event_id(event_id: str) -> int:
    """Deterministic seed from the source event_id. Truncating the ULID
    body to 8 hex chars gives a stable ~2^32 seed; collision is fine for
    a per-event reward draw."""
    if not event_id:
        return 0
    body = event_id
    if body.startswith("ev_"):
        body = body[3:]
    digest = "".join(c for c in body if c.isalnum())[:8] or "00000000"
    try:
        return int(digest, 36)
    except ValueError:
        return sum(ord(c) for c in digest)


def _sample_tier(distribution: dict[str, float], event_id: str) -> str:
    """Roll a tier deterministically from the source event_id against
    the member's reward_distribution. Defensive default = ``done`` when
    the distribution is empty / missing / malformed."""
    if not distribution:
        return DEFAULT_TIER
    tiers_with_weight = [
        (tier, float(distribution.get(tier, 0.0))) for tier in _TIER_ORDER
    ]
    total = sum(w for _, w in tiers_with_weight)
    if total <= 0:
        return DEFAULT_TIER
    seed = _seed_from_event_id(event_id)
    draw = random.Random(seed).random() * total
    cumulative = 0.0
    for tier, weight in tiers_with_weight:
        cumulative += weight
        if draw <= cumulative:
            return tier
    return tiers_with_weight[-1][0]


def _pick_template(
    persona: dict[str, Any] | None,
    tier: str,
) -> tuple[str, str]:
    """Resolve the persona pack's reward_templates.yaml entry for the
    rolled tier. Falls back to the done-tier template if the rolled
    tier is missing; falls back to a sentinel if done is also missing.
    """
    if not persona:
        return DEFAULT_TEMPLATE_ID, DEFAULT_TEMPLATE_TEXT
    templates = persona.get("reward_templates") or {}
    if not isinstance(templates, dict):
        return DEFAULT_TEMPLATE_ID, DEFAULT_TEMPLATE_TEXT
    candidates = templates.get(tier) or templates.get(DEFAULT_TIER)
    if not candidates:
        _log.warning(
            "reward_dispatch: persona has no template for tier=%s and no done fallback",
            tier,
        )
        return DEFAULT_TEMPLATE_ID, DEFAULT_TEMPLATE_TEXT
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            tid = str(first.get("id") or f"{tier}-0")
            ttext = str(first.get("text") or DEFAULT_TEMPLATE_TEXT)
            return tid, ttext
        return f"{tier}-0", str(first)
    if isinstance(candidates, dict):
        tid = str(candidates.get("id") or f"{tier}-default")
        ttext = str(candidates.get("text") or DEFAULT_TEMPLATE_TEXT)
        return tid, ttext
    return DEFAULT_TEMPLATE_ID, DEFAULT_TEMPLATE_TEXT


def _resolve_member_id(event_type: str, payload: dict[str, Any]) -> str | None:
    """Per the v1 task / commitment payload schemas in
    ``adminme/events/schemas/domain.py``: ``task.completed`` carries
    ``completed_by_member_id``; ``commitment.completed`` carries
    ``completed_by_party_id`` (a household member is also a party in
    [§3.4]). The handler treats either as the recipient."""
    if event_type == "task.completed":
        mid = payload.get("completed_by_member_id")
    else:
        mid = payload.get("completed_by_party_id")
    if isinstance(mid, str) and mid:
        return mid
    return None


class RewardDispatchPipeline:
    pack_id: str = "pipeline:reward_dispatch"
    version: str = "1.0.0"
    triggers: Triggers = {
        "events": ["task.completed", "commitment.completed"]
    }

    def __init__(
        self,
        *,
        profile_loader: ProfileLoader | None = None,
        persona_loader: PersonaLoader | None = None,
    ) -> None:
        self._profile_loader: ProfileLoader = profile_loader or (lambda _mid: None)
        self._persona_loader: PersonaLoader = persona_loader or (lambda: None)

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        event_type = event.get("type", "")
        if event_type not in ("task.completed", "commitment.completed"):
            return

        payload = event.get("payload") or {}
        member_id = _resolve_member_id(event_type, payload)
        if member_id is None:
            _log.warning(
                "reward_dispatch: source event %s missing member id; "
                "skipping defensively per [§7.7]",
                event.get("event_id"),
            )
            return

        source_event_id = str(event.get("event_id", ""))
        triggering_task_id: str | None = None
        triggering_commitment_id: str | None = None
        if event_type == "task.completed":
            triggering_task_id = (
                str(payload["task_id"]) if "task_id" in payload else None
            )
        else:
            triggering_commitment_id = (
                str(payload["commitment_id"])
                if "commitment_id" in payload
                else None
            )

        profile = self._profile_loader(member_id)
        if profile is None:
            _log.warning(
                "reward_dispatch: no profile for member %s; "
                "emitting defensive-default reward",
                member_id,
            )
            tier = DEFAULT_TIER
        else:
            mode = str(profile.get("rewards_mode") or profile.get("mode") or "")
            distribution = profile.get("reward_distribution") or {}
            if mode == "event_based":
                tier = DEFAULT_TIER
            elif mode == "child_warmth":
                tier = "warm"
            elif mode == "variable_ratio" and isinstance(distribution, dict):
                tier = _sample_tier(distribution, source_event_id)
            elif isinstance(distribution, dict) and distribution:
                tier = _sample_tier(distribution, source_event_id)
            else:
                tier = DEFAULT_TIER

        persona = self._persona_loader()
        template_id, template_text = _pick_template(persona, tier)

        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="reward.ready",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="reward_dispatch",
            source_account_id="pipeline",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity="normal",
            payload={
                "member_id": member_id,
                "tier": tier,
                "template_id": template_id,
                "template_text": template_text,
                "triggering_task_id": triggering_task_id,
                "triggering_commitment_id": triggering_commitment_id,
            },
        )
        await ctx.event_log.append(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )
