"""identity_resolution reactive pipeline.

Per [BUILD.md §1120-1130] and [§7.3] / [§7.4] / [D6]. Heuristic-only
resolver — no skill calls, no provider SDK imports. On every inbound
messaging or telephony event, classify the sender's identifier and
either:

- find an existing party owning that identifier (no emit; nothing to
  do — the parties projection already has the link); or
- score the unresolved identifier against existing identifiers of the
  same kind. Above threshold 0.85, emit ``identity.merge_suggested``
  for human review (NEVER auto-merged per [§1130]); below threshold, retain
  only an ``endpoint.observed`` fact. An inbox sender is not a Person.

The production runner supplies a read-only Parties connection so exact matches
and merge suggestions execute against the real projection. Tests may inject a
candidate loader at the same provider boundary. Source adapters normalize
phone/email identifiers before this pipeline applies its conservative mapping.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.pipelines.base import PipelineContext, Triggers, parties_connection

MERGE_THRESHOLD = 0.85


def _classify_identifier(
    event_type: str, payload: dict[str, Any]
) -> tuple[str, str, str]:
    """Derive (from_identifier, kind, value_normalized) for an inbound event.

    Source-channel mapping ([BUILD.md §3.2]):
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

    from_id = str(
        payload.get("to_identifier")
        if event_type == "messaging.sent"
        else payload.get("from_identifier", "")
    )
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
    """Fallback used only when no projection connection was supplied."""
    return []


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:22]}"


class IdentityResolutionPipeline:
    pack_id: str = "pipeline:identity_resolution"
    version: str = "2.0.0"
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
        # Tests may override the real read-model boundary deterministically.
        self._candidate_loader = candidate_loader or _default_candidate_loader

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        event_type = event.get("type", "")
        if event_type not in (
            "messaging.received",
            "messaging.sent",
            "telephony.sms_received",
        ):
            return

        payload = event.get("payload") or {}
        from_id, kind, value_normalized = _classify_identifier(event_type, payload)
        if not value_normalized:
            return

        if self._candidate_loader is not _default_candidate_loader:
            candidates = self._candidate_loader(kind)
        elif ctx.parties_conn_factory is not None:
            with parties_connection(ctx.parties_conn_factory) as conn:
                private_scope = f"private:{ctx.session.auth_member_id}"
                candidates = [
                    dict(row)
                    for row in conn.execute(
                        """SELECT i.party_id, i.value, i.value_normalized
                             FROM identifiers i
                             JOIN parties p
                               ON p.tenant_id=i.tenant_id AND p.party_id=i.party_id
                            WHERE i.tenant_id = ? AND i.kind = ?
                              AND i.revoked_at IS NULL
                              AND p.qualification = 'qualified'
                              AND i.owner_scope IN (?, 'shared:household')
                            ORDER BY i.rowid DESC LIMIT 100""",
                        (ctx.session.tenant_id, kind, private_scope),
                    ).fetchall()
                ]
        else:
            candidates = []
        # If a candidate already owns this exact value_normalized, it's a
        # hit — nothing to emit (the parties projection already has the
        # link).
        exact = {
            str(cand["party_id"])
            for cand in candidates
            if str(cand.get("value_normalized", "")) == value_normalized
            and cand.get("party_id")
        }
        if len(exact) == 1:
            if ctx.parties_conn_factory is not None:
                await self._emit_participant_resolved(
                    event=event,
                    ctx=ctx,
                    party_id=next(iter(exact)),
                    kind=kind,
                    value_normalized=value_normalized,
                )
            return
        if len(exact) > 1:
            await self._emit_endpoint(
                event=event,
                ctx=ctx,
                value=from_id,
                kind=kind,
                value_normalized=value_normalized,
                evidence=["ambiguous_exact_match"],
            )
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

        # Below threshold (or no candidates), retain the addressable fact but
        # make no identity claim. Promotion is a human-gated Triage action.
        await self._emit_endpoint(
            event=event,
            ctx=ctx,
            value=from_id,
            kind=kind,
            value_normalized=value_normalized,
            evidence=["observed_interaction_endpoint", "identity_unconfirmed"],
        )

    async def _emit_participant_resolved(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        party_id: str,
        kind: str,
        value_normalized: str,
    ) -> None:
        source_event_id = str(event["event_id"])
        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="interaction.participant_resolved",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="identity_resolution",
            source_account_id="pipeline",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity=event.get("sensitivity", "normal"),
            idempotency_key=(
                f"identity:resolved:{source_event_id}:"
                f"{'to' if event.get('type') == 'messaging.sent' else 'from'}"
            ),
            payload={
                "source_event_id": source_event_id,
                "interaction_id": f"ix_{source_event_id}",
                "party_id": party_id,
                "role": "to" if event.get("type") == "messaging.sent" else "from",
                "identifier_kind": kind,
                "identifier_normalized": value_normalized,
            },
        )
        await ctx.event_sink.write(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
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
            idempotency_key=f"identity:merge-suggested:{source_event_id}",
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
        await ctx.event_sink.write(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def _emit_endpoint(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        value: str,
        kind: str,
        value_normalized: str,
        evidence: list[str],
    ) -> None:
        payload = event.get("payload") or {}
        source_event_id = str(event.get("event_id") or "")
        member_id = str(payload.get("member_id") or "")
        if not member_id and str(event.get("owner_scope") or "").startswith("private:"):
            member_id = str(event["owner_scope"]).removeprefix("private:")
        if not member_id:
            return
        stable_seed = f"{member_id}:{kind}:{value_normalized}"
        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="endpoint.observed",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="identity_resolution",
            source_account_id="pipeline",
            owner_scope=f"private:{member_id}",
            visibility_scope=f"private:{member_id}",
            sensitivity=event.get("sensitivity", "normal"),
            # Each source observation is an immutable fact.  Reusing one
            # endpoint-wide key while changing source_interaction_id caused a
            # durable idempotency conflict on the second email from a sender.
            idempotency_key=f"identity:endpoint:{source_event_id}",
            payload={
                "endpoint_id": _stable_id("endpoint", stable_seed),
                "member_id": member_id,
                "kind": kind,
                "value": value,
                "value_normalized": value_normalized,
                "classification": "unresolved",
                "provider": payload.get("provider") or payload.get("source_channel"),
                "provider_account_id": payload.get("provider_account_id"),
                "source_interaction_id": f"ix_{event.get('event_id', '')}",
                "observed_at": event.get("occurred_at") or EventEnvelope.now_utc_iso(),
                "evidence": evidence,
            },
        )
        await ctx.event_sink.write(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )
