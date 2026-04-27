"""identity_resolution reactive pipeline.

Per [BUILD.md §1120-1130] and [§7.3] / [§7.4] / [D6]. Heuristic-only
resolver — no skill calls, no provider SDK imports. On every inbound
messaging or telephony event, classify the sender's identifier and
either:

- find an existing party owning that identifier (no emit; nothing to
  do — the parties projection already has the link); or
- score the unresolved identifier against existing identifiers of the
  same kind. Above threshold 0.85, emit ``identity.merge_suggested``
  for human review (NEVER auto-merged per [§1130]); below threshold,
  emit ``party.created`` + ``identifier.added`` to mint a new party.

Connection-management note (carry-forward from 10a): the runner does
not currently expose per-projection DB access through PipelineContext.
Per the Commit-2 BUILD_LOG carry-forward, this pipeline ships in
"degenerate-clean" mode — the candidate loader returns an empty list
in production, so every miss creates a new party. The merge-threshold
branch is exercised by unit tests that inject a custom candidate
loader. A future prompt (10b-ii or its sequel) will thread a
parties_conn_factory through PipelineContext; the only change required
here will be replacing the default ``_default_candidate_loader`` with
one that opens the parties DB and reads its ``identifiers`` table.

Per [§3.2] (CRM identifiers): the source-channel-to-kind mapping below
is a placeholder until a real adapter ships and richer normalization
(E.164 phone parsing, IDN-aware email canonicalization) is needed.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.pipelines.base import PipelineContext, Triggers

MERGE_THRESHOLD = 0.85


def _classify_identifier(
    event_type: str, payload: dict[str, Any]
) -> tuple[str, str, str]:
    """Derive (from_identifier, kind, value_normalized) for an inbound event.

    Source-channel mapping ([BUILD.md §3.2] placeholder):
    - ``telephony.sms_received`` → kind ``"phone"`` (from ``from_number``);
      value_normalized strips non-digit characters.
    - messaging events: ``source_channel`` substring "sms" → ``"phone"``;
      "imessage" → ``"imessage_handle"``; otherwise → ``"email"``.
      value_normalized lowercases + strips for emails / handles; strips
      to digits for phones.
    """
    if event_type == "telephony.sms_received":
        from_id = str(payload.get("from_number", ""))
        kind = "phone"
        value_normalized = "".join(c for c in from_id if c.isdigit())
        return from_id, kind, value_normalized

    from_id = str(payload.get("from_identifier", ""))
    channel = str(payload.get("source_channel", "")).lower()
    if "sms" in channel:
        kind = "phone"
        value_normalized = "".join(c for c in from_id if c.isdigit())
    elif "imessage" in channel:
        kind = "imessage_handle"
        value_normalized = from_id.lower().strip()
    else:
        kind = "email"
        value_normalized = from_id.lower().strip()
    return from_id, kind, value_normalized


def _levenshtein(a: str, b: str) -> int:
    """Classic O(len(a)*len(b)) edit distance (stdlib only)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                curr[j - 1] + 1,
                prev[j] + 1,
                prev[j - 1] + cost,
            )
        prev = curr
    return prev[-1]


def _email_score(my_normalized: str, other_normalized: str) -> float:
    """Domain-tail-equality + Levenshtein on local part."""
    if "@" not in my_normalized or "@" not in other_normalized:
        return 0.0
    my_local, my_domain = my_normalized.rsplit("@", 1)
    other_local, other_domain = other_normalized.rsplit("@", 1)
    if my_domain != other_domain:
        return 0.0
    longest = max(len(my_local), len(other_local))
    if longest == 0:
        return 0.0
    return 1.0 - (_levenshtein(my_local, other_local) / longest)


def _phone_score(my_normalized: str, other_normalized: str) -> float:
    """Last-7-digit prefix match: 1.0 on equal tail, else 0.0."""
    if len(my_normalized) < 7 or len(other_normalized) < 7:
        return 0.0
    return 1.0 if my_normalized[-7:] == other_normalized[-7:] else 0.0


def _imessage_score(my_normalized: str, other_normalized: str) -> float:
    longest = max(len(my_normalized), len(other_normalized))
    if longest == 0:
        return 0.0
    return 1.0 - (_levenshtein(my_normalized, other_normalized) / longest)


_HEURISTIC_NAMES = {
    "email": "email_domain_match",
    "phone": "phone_prefix_match",
    "imessage_handle": "levenshtein_display_name",
}


def _score_for_kind(kind: str, mine: str, other: str) -> float:
    if kind == "email":
        return _email_score(mine, other)
    if kind == "phone":
        return _phone_score(mine, other)
    if kind == "imessage_handle":
        return _imessage_score(mine, other)
    return 0.0


def _pick_best_match(
    candidates: Iterable[dict[str, Any]],
    *,
    kind: str,
    my_value_normalized: str,
) -> tuple[float, dict[str, Any]] | None:
    best: tuple[float, dict[str, Any]] | None = None
    for cand in candidates:
        other = str(cand.get("value_normalized", ""))
        score = _score_for_kind(kind, my_value_normalized, other)
        if best is None or score > best[0]:
            best = (score, cand)
    return best


def _default_candidate_loader(kind: str) -> list[dict[str, Any]]:
    """Production-mode loader: returns an empty list because the runner
    does not yet thread a parties-projection connection through
    PipelineContext (see module docstring). When that wiring lands, the
    replacement loader will execute::

        SELECT party_id, value, value_normalized
          FROM identifiers
         WHERE tenant_id = ? AND kind = ?
         ORDER BY rowid DESC
         LIMIT 100

    against the parties projection."""
    return []


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(11)}"


class IdentityResolutionPipeline:
    pack_id: str = "pipeline:identity_resolution"
    version: str = "1.0.0"
    triggers: Triggers = {
        "events": [
            "messaging.received",
            "messaging.sent",
            "telephony.sms_received",
        ]
    }

    def __init__(
        self,
        candidate_loader: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> None:
        # Tests override candidate_loader to exercise the threshold paths;
        # production uses the empty-list default per the module docstring.
        self._candidate_loader = candidate_loader or _default_candidate_loader

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        event_type = event.get("type", "")
        # Outbound events skip — outbound resolution is the recipient's
        # responsibility per [BUILD.md §1120], deferred.
        if event_type == "messaging.sent":
            return
        if event_type not in (
            "messaging.received",
            "telephony.sms_received",
        ):
            return

        payload = event.get("payload") or {}
        from_id, kind, value_normalized = _classify_identifier(
            event_type, payload
        )
        if not value_normalized:
            return

        candidates = self._candidate_loader(kind)
        # If a candidate already owns this exact value_normalized, it's a
        # hit — nothing to emit (the parties projection already has the
        # link).
        for cand in candidates:
            if str(cand.get("value_normalized", "")) == value_normalized:
                return

        best = _pick_best_match(
            candidates, kind=kind, my_value_normalized=value_normalized
        )
        source_event_id = event.get("event_id", "")

        if best is not None and best[0] >= MERGE_THRESHOLD:
            score, surviving = best
            await self._emit_merge_suggested(
                event=event,
                ctx=ctx,
                surviving_party_id=str(surviving.get("party_id", "")),
                from_id=from_id,
                kind=kind,
                value_normalized=value_normalized,
                confidence=score,
                heuristic=_HEURISTIC_NAMES.get(kind, "unknown"),
                source_event_id=source_event_id,
            )
            return

        # Below threshold (or no candidates): mint a new party + first
        # identifier. Both events use causation_id=triggering_event_id
        # per the 10a carry-forward.
        await self._emit_party_and_identifier(
            event=event,
            ctx=ctx,
            from_id=from_id,
            kind=kind,
            value_normalized=value_normalized,
        )

    async def _emit_merge_suggested(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        surviving_party_id: str,
        from_id: str,
        kind: str,
        value_normalized: str,
        confidence: float,
        heuristic: str,
        source_event_id: str,
    ) -> None:
        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="identity.merge_suggested",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="identity_resolution",
            source_account_id="pipeline",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity="normal",
            payload={
                "surviving_party_id": surviving_party_id,
                "candidate_value": from_id,
                "candidate_kind": kind,
                "candidate_value_normalized": value_normalized,
                "confidence": round(confidence, 4),
                "heuristic": heuristic,
                "source_event_id": source_event_id,
            },
        )
        await ctx.event_log.append(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def _emit_party_and_identifier(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        from_id: str,
        kind: str,
        value_normalized: str,
    ) -> None:
        party_id = _new_id("party")
        identifier_id = _new_id("ident")
        party_envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="party.created",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="identity_resolution",
            source_account_id="pipeline",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity="normal",
            payload={
                "party_id": party_id,
                "kind": "person",
                "display_name": from_id,
                "sort_name": from_id.lower(),
            },
        )
        await ctx.event_log.append(
            party_envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )
        identifier_envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="identifier.added",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="identity_resolution",
            source_account_id="pipeline",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity="normal",
            payload={
                "identifier_id": identifier_id,
                "party_id": party_id,
                "kind": kind,
                "value": from_id,
                "value_normalized": value_normalized,
                "verified": False,
                "primary_for_kind": True,
            },
        )
        await ctx.event_log.append(
            identifier_envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )
