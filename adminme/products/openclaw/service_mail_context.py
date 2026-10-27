"""Exact retained member Gmail lookup; no provider query or new content authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from adminme.lib.principal import ServiceSession
from adminme.products.openclaw.service_context import fact_for
from adminme.projections.parties.queries import (
    ServiceDelegationResolutionError,
    resolve_service_delegation,
)
from adminme.service_identities import ServiceRequestContext
from adminme.source_policy import source_coordinate_for_event


class MemberGmailThreadSelector(BaseModel):
    """Exact coordinates, never an ambient account or a provider search."""

    model_config = {"extra": "forbid", "frozen": True}

    member_id: str = Field(min_length=1, max_length=240)
    provider_account_id: str = Field(min_length=1, max_length=500)
    workspace: Literal["personal", "work"]
    thread_id: str = Field(min_length=1, max_length=1000)
    native_message_id: str | None = Field(default=None, min_length=1, max_length=240)

    @field_validator(
        "member_id", "provider_account_id", "thread_id", "native_message_id"
    )
    @classmethod
    def exact_coordinate(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("mail coordinates must be exact non-control strings")
        return value


def _not_found() -> HTTPException:
    # Unknown coordinates, grants, policies and revisions remain indistinguishable.
    return HTTPException(404, "authorized member mail thread not found")


def _context(
    runtime: Any, session: ServiceSession, member_id: str
) -> ServiceRequestContext:
    try:
        context = resolve_service_delegation(
            runtime.projections.connection("parties"),
            tenant_id=session.tenant_id,
            service_principal_id=session.principal.principal_id,
            operator_principal_id=session.operator_principal_id,
            context_scope="member_private",
            context_member_id=member_id,
        )
    except ServiceDelegationResolutionError:
        raise _not_found()
    if "read_context" not in context.permissions:
        raise _not_found()
    return context


def _rows(
    runtime: Any,
    session: ServiceSession,
    selector: MemberGmailThreadSelector,
    after_event_id: str | None,
    limit: int,
) -> tuple[tuple[str, str], ...]:
    private = f"private:{selector.member_id}"
    predicate = (
        "tenant_id=? AND member_id=? AND owner_scope=? AND visibility_scope=? "
        "AND provider='gmail' AND provider_account_id=? AND workspace=? AND thread_id=?"
    )
    values: list[Any] = [
        session.tenant_id,
        selector.member_id,
        private,
        private,
        selector.provider_account_id,
        selector.workspace,
        selector.thread_id,
    ]
    if selector.native_message_id is not None:
        predicate += " AND native_message_id=?"
        values.append(selector.native_message_id)
    conn = runtime.projections.connection("interactions")
    accepted = "json_extract(raw_event_ids, '$[#-1]')"
    select = f"SELECT interaction_id, {accepted} AS accepted_event_id FROM interactions"
    cursor_rows = []
    if after_event_id is not None:
        cursor_rows = conn.execute(
            f"{select} WHERE {predicate} AND {accepted}=? LIMIT 2",
            (*values, after_event_id),
        ).fetchall()
        if len(cursor_rows) != 1:
            raise _not_found()
        predicate += f" AND {accepted}>?"
        values.append(after_event_id)
    page_rows = conn.execute(
        f"{select} WHERE {predicate} ORDER BY {accepted}, interaction_id LIMIT ?",
        (*values, limit + 1),
    ).fetchall()
    rows = [*cursor_rows, *page_rows]
    if not rows:
        raise _not_found()
    result = []
    seen: set[str] = set()
    for row in rows:
        event_id = row["accepted_event_id"]
        if not isinstance(event_id, str) or not event_id or event_id in seen:
            raise _not_found()
        seen.add(event_id)
        result.append((row["interaction_id"], event_id))
    return tuple(result)


def _read_metadata(
    runtime: Any, event: dict[str, Any], member_id: str
) -> dict[str, Any]:
    try:
        coordinate = source_coordinate_for_event(
            event, provider_bindings=runtime.settings.provider_bindings
        )
        effective = runtime.settings_control.effective_source_policy_v2(
            member_id, coordinate
        )
    except (KeyError, TypeError, ValueError):
        raise _not_found()
    policy = effective["policy"]
    payload = event["payload"]
    retained_revision = {
        "source_event_id": event["event_id"],
        "schema_version": event["schema_version"],
    }
    if isinstance(payload.get("native_revision"), str):
        retained_revision["native_revision"] = payload["native_revision"]
    if type(payload.get("native_revision_ms")) is int:
        retained_revision["native_revision_ms"] = payload["native_revision_ms"]
    return {
        "canonical": {
            "source_event_id": event["event_id"],
            "member_id": member_id,
            **{
                key: payload[key]
                for key in (
                    "provider",
                    "provider_account_id",
                    "workspace",
                    "thread_id",
                    "native_message_id",
                )
            },
        },
        "timestamps": {
            key: value
            for key, value in {
                "occurred_at": event.get("occurred_at"),
                "recorded_at": event.get("recorded_at"),
                **{
                    key: payload.get(key)
                    for key in ("observed_at", "received_at", "sent_at")
                },
            }.items()
            if isinstance(value, str) and value
        },
        "latest_retained_revision": retained_revision,
        "provider_freshness": {
            "state": "unknown",
            "reason": "retained_observation_is_not_a_live_provider_read",
        },
        "effective_policy": {
            "context_mode": policy["context_mode"],
            "acquisition_state": policy["acquisition_state"],
            **{
                key: effective[key]
                for key in (
                    "policy_key",
                    "origin",
                    "revision",
                    "migration_state",
                    "exact_coordinate",
                    "inherited_from_policy_key",
                )
            },
        },
        "implementation": {
            "provider_body_included": False,
            "body_reason": "member_body_not_implemented_by_this_reader",
        },
    }


def _fact(
    runtime: Any,
    session: ServiceSession,
    selector: MemberGmailThreadSelector,
    event: dict[str, Any],
    context: ServiceRequestContext,
) -> dict[str, Any]:
    payload = event.get("payload") or {}
    private = f"private:{selector.member_id}"
    version = event.get("schema_version")
    if not (
        event.get("tenant_id") == session.tenant_id
        and event.get("type") in {"messaging.received", "messaging.sent"}
        and type(version) is int
        and version >= 4
        and event.get("owner_scope") == event.get("visibility_scope") == private
        and event.get("source_account_id") == selector.provider_account_id
        and payload.get("member_id") == selector.member_id
        and payload.get("provider") == "gmail"
        and payload.get("provider_account_id") == selector.provider_account_id
        and payload.get("workspace") == selector.workspace
        and payload.get("thread_id") == selector.thread_id
        and isinstance(payload.get("native_message_id"), str)
        and payload["native_message_id"]
        and not payload.get("service_principal_id")
        and (
            selector.native_message_id is None
            or payload["native_message_id"] == selector.native_message_id
        )
    ):
        raise _not_found()
    # The shared path enforces exact source policy, scope and the accepted,
    # non-retracted message pointer (not projection last_event_id).
    fact = fact_for(runtime, session, event, [context])
    if fact is None or fact.get("provider_body_included") is not False:
        raise _not_found()
    fact["mail_read_metadata"] = _read_metadata(runtime, event, selector.member_id)
    return fact


@dataclass(frozen=True)
class ResolvedMailThread:
    """Internal evidence includes the cursor and lookahead; neither is returned as a fact."""

    selector: MemberGmailThreadSelector
    after_event_id: str | None
    limit: int
    rows: tuple[tuple[str, str], ...]
    events: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    context: ServiceRequestContext

    @property
    def facts(self) -> list[dict[str, Any]]:
        start = int(self.after_event_id is not None)
        return list(self.evidence[start : start + self.limit])

    @property
    def scan_truncated(self) -> bool:
        return len(self.rows) - int(self.after_event_id is not None) > self.limit

    @property
    def next_cursor(self) -> str | None:
        return self.facts[-1]["event_id"] if self.scan_truncated else None


def revalidate_member_gmail_thread(
    runtime: Any,
    session: ServiceSession,
    resolved: ResolvedMailThread,
) -> None:
    """No await: run again after audit publication, immediately before response."""
    context = _context(runtime, session, resolved.selector.member_id)
    if (
        context != resolved.context
        or _rows(
            runtime, session, resolved.selector, resolved.after_event_id, resolved.limit
        )
        != resolved.rows
        or tuple(
            _fact(runtime, session, resolved.selector, event, context)
            for event in resolved.events
        )
        != resolved.evidence
    ):
        raise _not_found()


async def resolve_member_gmail_thread(
    runtime: Any,
    session: ServiceSession,
    selector: MemberGmailThreadSelector,
    *,
    after_event_id: str | None,
    limit: int,
) -> ResolvedMailThread:
    # Check exact current member authority before even consulting cursor existence.
    context = _context(runtime, session, selector.member_id)
    rows = _rows(runtime, session, selector, after_event_id, limit)
    events, evidence = [], []
    for _, event_id in rows:
        event = await runtime.event_log.get(event_id)
        if event is None:
            raise _not_found()
        evidence.append(_fact(runtime, session, selector, event, context))
        events.append(event)
    resolved = ResolvedMailThread(
        selector,
        after_event_id,
        limit,
        rows,
        tuple(events),
        tuple(evidence),
        context,
    )
    revalidate_member_gmail_thread(runtime, session, resolved)
    return resolved
