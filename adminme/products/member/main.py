"""Responsive member portal and its narrow, authenticated APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel, Field

from adminme.comms import validate_outbound_communication
from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.events.log import IdempotencyConflict
from adminme.extensions import ExtensionError
from adminme.events.schemas.triage import TriageRouteV1, TriageTargetKind
from adminme.events.schemas.knowledge import KnowledgeGrantPermission
from adminme.events.schemas.reminder_context import (
    ContextKind,
    ContextSource,
    CueOperator,
)
from adminme.events.schemas.calendar_policy import (
    CalendarRuleConfiguredV1,
    CalendarSchedulePolicyConfiguredV1,
    ScheduleWindowV1,
)
from adminme.lib.scope import allowed_read
from adminme.lib.session import Session
from adminme.member_chat import MemberChatError
from adminme.products.auth import require_principal, session_from_request
from adminme.products.comms import main as comms
from adminme.products.core import main as core
from adminme.products.member.surfaces import (
    authorize_surface,
    build_surface,
    communication_accounts,
    crm_detail,
    crm_search,
    evaluate_query,
    experience_mode,
    navigation,
    reminder_accounts,
)
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.products.openclaw.main import (
    LifecycleRequest as OpenClawLifecycleRequest,
    Provenance,
    _task_lifecycle,
)
from adminme.projections.parties.queries import get_party
from adminme.projections.interactions.queries import get_interaction, participants_for
from adminme.projections.calendars.queries import (
    get_calendar_event,
    get_calendar_outcome,
)
from adminme.projections.recurrences.queries import (
    get_recurrence,
    get_routine_occurrence,
)
from adminme.projections.member_knowledge.queries import (
    get_visible as get_visible_note,
    grants_for_note,
    list_visible as list_visible_notes,
    search_visible as search_visible_notes,
)
from adminme.projections.triage.queries import get_item as get_triage_item
from adminme.settings_control import (
    compose_connections,
    member_label,
)
from adminme.projections.lists.queries import (
    get_item as get_list_item,
    items_for_member as list_items_for_member,
    lists_for_member,
)
from adminme.temporary_surfaces import (
    CHILD_ACTIONS,
    CHILD_QUERIES,
    CreateTemporarySurface,
    TemporarySurfaceError,
    TemporarySurfaceSpec,
)


router = APIRouter(tags=["member-portal"])
SessionDep = Annotated[Session, Depends(session_from_request)]
ASSET_ROOT = Path(__file__).with_name("assets")
_MEMBER_INDEX = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
_MEMBER_ASSET_VERSION = hashlib.sha256(
    (ASSET_ROOT / "member.css").read_bytes() + (ASSET_ROOT / "member.js").read_bytes()
).hexdigest()[:12]


def _member_index_response() -> Response:
    return Response(
        content=_MEMBER_INDEX.replace("__ASSET_VERSION__", _MEMBER_ASSET_VERSION),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


class ChatRequest(BaseModel):
    model_config = {"extra": "forbid"}
    message: str = Field(min_length=1, max_length=4_000)
    request_id: str = Field(min_length=8, max_length=120)


class SettingsProfileRequest(BaseModel):
    model_config = {"extra": "forbid"}
    profile_id: str = Field(min_length=1, max_length=120)
    request_id: str = Field(min_length=8, max_length=120)


class SettingsPreferencesRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=120)
    values: dict[str, int | bool | str] = Field(default_factory=dict)


class SettingsConnectionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=120)
    action: Literal["pause", "resume"]


class SettingsBridgeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=120)
    member_id: str = Field(min_length=1, max_length=240)
    capabilities: list[
        Literal[
            "apple_calendar",
            "apple_reminders",
            "apple_notes",
            "apple_voice_memos",
            "obsidian",
        ]
    ] = Field(min_length=1, max_length=5)


class PromotionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    audience_member_ids: list[str] = Field(min_length=1, max_length=20)


class TemporaryActionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    verb: Literal[
        "task.complete", "task.defer", "task.snooze", "task.block", "task.unblock"
    ]
    item_id: str = Field(min_length=1, max_length=160)
    expected_last_event_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=120)
    until: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)


class MemberLifecycleRequest(BaseModel):
    model_config = {"extra": "forbid"}
    item_kind: Literal["task", "commitment", "recurrence"]
    item_id: str = Field(min_length=1, max_length=160)
    action: Literal["complete", "defer", "snooze", "block", "unblock"]
    expected_last_event_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=120)
    until: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)


class CalendarOutcomeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1)
    outcome: Literal["attended", "missed"]


class CalendarRuleRequest(BaseModel):
    """A principal-approved interpretation of one observed calendar."""

    model_config = {"extra": "forbid"}
    source_member_id: str = Field(min_length=1, max_length=240)
    provider: Literal["google_calendar", "apple_calendar"]
    provider_account_id: str = Field(min_length=1, max_length=500)
    calendar_external_id: str = Field(min_length=1, max_length=1000)
    purpose: Literal[
        "personal",
        "work",
        "household",
        "coparenting",
        "property",
        "family_context",
        "holidays",
        "test",
        "other",
    ]
    effect: Literal["hard_block", "soft_hold", "context", "ignore"]
    tentative_effect: Literal["hard_block", "soft_hold", "context", "ignore"] = (
        "soft_hold"
    )
    caregiving_load: Literal["hard_block", "soft_hold", "context", "ignore"] = "ignore"
    caregiving_summary_values: list[str] = Field(default_factory=list, max_length=20)
    buffer_kind: Literal[
        "auto",
        "none",
        "remote",
        "appointment",
        "medical",
        "handoff",
        "deep_work",
    ] = "auto"
    write_target: bool = False
    destination_kind: (
        Literal["personal", "work", "household", "coparenting", "property"] | None
    ) = None
    enabled: bool = True


class CalendarOnboardingRequest(BaseModel):
    """Complete deterministic calendar policy for one household member."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    member_id: str = Field(min_length=1, max_length=240)
    timezone: str = Field(min_length=1, max_length=240)
    rules: list[CalendarRuleRequest] = Field(min_length=1, max_length=80)
    windows: list[ScheduleWindowV1] = Field(default_factory=list, max_length=40)
    buffer_minutes: dict[str, int] = Field(default_factory=dict)
    accepted_chores_required: bool = True
    one_confirm_write: bool = True


class RoutineStepOutcomeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1)
    outcome: Literal["completed", "skipped"]


class CommsCommandRequest(BaseModel):
    """One explicit member decision against a visible communication."""

    model_config = {"extra": "forbid"}
    operation: Literal[
        "reply",
        "mark_read",
        "mark_unread",
        "archive",
        "restore",
        "snooze",
        "unsnooze",
        "quiet",
        "unquiet",
    ]
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    request_id: str = Field(min_length=8, max_length=120)
    body: str | None = Field(default=None, max_length=20_000)
    snoozed_until: str | None = None
    openclaw_approved: bool = False


class ReminderCommandRequest(BaseModel):
    """One explicit member mutation of a provider-owned reminder/list."""

    model_config = {"extra": "forbid"}
    provider: Literal["apple_reminders", "google_tasks"]
    provider_account_id: str = Field(min_length=1, max_length=500)
    operation: Literal[
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "complete_reminder",
        "uncomplete_reminder",
        "move_reminder",
        "create_task",
        "update_task",
        "delete_task",
        "complete_task",
        "uncomplete_task",
        "move_task",
        "clear_completed_tasks",
        "create_list",
        "update_list",
        "delete_list",
    ]
    list_external_id: str | None = Field(default=None, max_length=1000)
    item_external_id: str | None = Field(default=None, max_length=1000)
    expected_last_event_id: str | None = Field(default=None, max_length=240)
    expected_provider_revision: str | None = Field(default=None, max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(min_length=8, max_length=120)
    openclaw_approved: bool = False


class ReminderCueRequest(BaseModel):
    """A typed comparison against one scoped context signal."""

    model_config = {"extra": "forbid"}
    cue_id: str = Field(min_length=8, max_length=240)
    list_external_id: str = Field(min_length=1, max_length=1000)
    item_external_id: str = Field(min_length=1, max_length=1000)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    signal_key: str = Field(min_length=1, max_length=500)
    source: ContextSource
    kind: ContextKind
    operator: CueOperator
    expected_state: str | None = Field(default=None, max_length=500)
    expected_number: float | None = None
    enabled: bool = True


class TriageEnrichmentRequest(BaseModel):
    """Member corrections applied before a candidate can be confirmed."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    target_kind: TriageTargetKind | None = None
    candidate_updates: dict[str, Any] = Field(default_factory=dict)
    route: TriageRouteV1 | None = None


class TriageDecisionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=500)


class KnowledgeGrantRequest(BaseModel):
    """One explicit owner decision to expose one note to one adult member."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    grantee_member_id: str = Field(min_length=1, max_length=240)
    permissions: list[KnowledgeGrantPermission] = Field(min_length=1, max_length=3)
    expires_at: datetime | None = None


class KnowledgeGrantRevokeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)


class PeopleProfileRequest(BaseModel):
    """Member-owned relationship cue policy; never inferred by a model."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str | None = Field(default=None, max_length=240)
    circle: str | None = Field(default=None, max_length=240)
    desired_cadence_days: int | None = Field(default=None, ge=1, le=3650)
    cues_enabled: bool = False
    favorite: bool = False
    suppressed: bool = False
    suppression_reason: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=50)


class PeopleLifecycleRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    action: Literal["archive", "restore"]
    reason: str | None = Field(default=None, max_length=500)


def _runtime(request: Request) -> Any:
    return request.app.state.runtime


def _require_self_settings(session: Session) -> None:
    if session.auth_role in {"child", "device", "ambient"}:
        raise HTTPException(403, "Settings changes require an authenticated adult")
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "Settings are read-only while viewing another member")


async def _write_settings_receipt(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    request_id: str,
    request_fingerprint: str,
    owner_member_id: str,
) -> str:
    if runtime.events is None:
        raise HTTPException(503, "Settings audit log is unavailable")
    now = EventEnvelope.now_utc_iso()
    return await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type=event_type,
            schema_version=1,
            occurred_at=now,
            source_adapter="member-settings-control-plane",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{owner_member_id}",
            visibility_scope=f"private:{owner_member_id}",
            sensitivity="normal",
            correlation_id=session.correlation_id,
            actor_identity=session.auth_member_id,
            idempotency_key=(
                f"member-settings:{event_type}:{session.auth_member_id}:{request_id}"
            ),
            raw_ref=f"request-sha256:{request_fingerprint}",
            payload=payload,
        )
    )


def _settings_request_fingerprint(event_type: str, values: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"event_type": event_type, "values": values},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


async def _prior_settings_receipt(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    request_id: str,
    request_fingerprint: str,
) -> dict[str, Any] | None:
    if runtime.event_log is None:
        raise HTTPException(503, "Settings audit log is unavailable")
    key = f"member-settings:{event_type}:{session.auth_member_id}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is None:
        return None
    if prior.get("raw_ref") != f"request-sha256:{request_fingerprint}":
        raise HTTPException(409, "request id conflicts with a prior Settings command")
    return prior


_EXTERNAL_OPERATIONS = {
    "google_calendar": {
        "create_event",
        "update_event",
        "delete_event",
        "move_event",
        "respond_event",
    },
    "google_tasks": {
        "create_task",
        "update_task",
        "delete_task",
        "complete_task",
        "uncomplete_task",
        "move_task",
        "clear_completed_tasks",
        "create_list",
        "update_list",
        "delete_list",
    },
    "apple_calendar": {"create_event", "update_event", "delete_event", "move_event"},
    "apple_reminders": {
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "complete_reminder",
        "uncomplete_reminder",
        "move_reminder",
        "create_list",
        "update_list",
        "delete_list",
    },
}
_COMMS_EXTERNAL_OPERATIONS = {
    "gmail": {"reply", "mark_read", "mark_unread", "archive", "restore"},
    "microsoft_graph": {"reply", "mark_read", "mark_unread", "archive", "restore"},
    "bluebubbles": {"reply"},
    "twilio_sms": {"reply"},
    "twilio_whatsapp": {"reply"},
    "fixture": {"reply"},
}

_REMINDER_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "create_reminder": frozenset(
        {
            "title",
            "notes",
            "url",
            "priority",
            "start",
            "due",
            "alarms",
            "recurrence_rules",
        }
    ),
    "update_reminder": frozenset(
        {
            "title",
            "notes",
            "url",
            "priority",
            "start",
            "due",
            "alarms",
            "recurrence_rules",
        }
    ),
    "delete_reminder": frozenset(),
    "complete_reminder": frozenset(),
    "uncomplete_reminder": frozenset(),
    "move_reminder": frozenset({"destination_list_external_id"}),
    "create_task": frozenset({"title", "notes", "due", "parent", "previous"}),
    "update_task": frozenset({"title", "notes", "due"}),
    "delete_task": frozenset(),
    "complete_task": frozenset(),
    "uncomplete_task": frozenset(),
    "move_task": frozenset({"destination_list_external_id", "parent", "previous"}),
    "clear_completed_tasks": frozenset(),
    "create_list": frozenset({"title", "name", "color"}),
    "update_list": frozenset({"title", "name", "color"}),
    "delete_list": frozenset(),
}


def _owned_triage_item(
    runtime: Any, session: Session, proposal_id: str
) -> dict[str, Any]:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "Triage decisions are unavailable while viewing another member"
        )
    if runtime.projections is None:
        raise HTTPException(503, "triage projection unavailable")
    item = get_triage_item(
        runtime.projections.connection("triage"), session, proposal_id=proposal_id
    )
    if item is None or item["owner_member_id"] != session.auth_member_id:
        raise HTTPException(404, "triage item not found")
    return item


def _require_current_triage(item: dict[str, Any], expected_last_event_id: str) -> None:
    if item["state"] != "pending_review":
        raise HTTPException(409, "triage item is no longer awaiting review")
    if item["last_event_id"] != expected_last_event_id:
        raise HTTPException(409, "triage item changed; refresh before deciding")


def _triage_event(
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    request_id: str,
    request_fingerprint: str,
    causation_id: str,
    sensitivity: Sensitivity = "normal",
) -> EventEnvelope:
    now = EventEnvelope.now_utc_iso()
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=now,
        source_adapter="member-portal",
        source_account_id=session.auth_member_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        sensitivity=sensitivity,
        correlation_id=session.correlation_id,
        causation_id=causation_id,
        actor_identity=session.auth_member_id,
        idempotency_key=f"triage:{event_type}:{session.auth_member_id}:{request_id}",
        raw_ref=f"request-sha256:{request_fingerprint}",
        payload=payload,
    )


def _triage_request_fingerprint(proposal_id: str, body: BaseModel) -> str:
    canonical = json.dumps(
        {"proposal_id": proposal_id, "body": body.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _triage_state_fingerprint(item: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "proposal_id": item["proposal_id"],
            "target_kind": item["target_kind"],
            "owner_member_id": item["owner_member_id"],
            "title": item["title"],
            "candidate": item.get("candidate") or {},
            "route": item.get("route"),
            "sensitivity": item.get("sensitivity") or "normal",
            "last_event_id": item["last_event_id"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _prior_triage_command(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    request_id: str,
    request_fingerprint: str,
) -> dict[str, Any] | None:
    if runtime.event_log is None:
        raise HTTPException(503, "event log unavailable")
    key = f"triage:{event_type}:{session.auth_member_id}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is None:
        return None
    if prior["raw_ref"] != f"request-sha256:{request_fingerprint}":
        raise HTTPException(409, "idempotency key conflicts with prior decision body")
    return prior


async def _guard_triage_write(
    runtime: Any, session: Session, action: str, payload: dict[str, Any]
) -> None:
    if runtime.guarded_write is None:
        raise HTTPException(503, "write authorization unavailable")
    decision = await runtime.guarded_write.check(session, action, payload)
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {"reason": decision.reason, "correlation_id": decision.correlation_id},
        )


def _target_member(runtime: Any, item: dict[str, Any]) -> str:
    route = item.get("route") or {}
    member_id = str(route.get("member_id") or item["owner_member_id"])
    if member_id not in runtime.settings.member_roles:
        raise HTTPException(422, "route must name a configured household member")
    if runtime.settings.member_roles.get(member_id) == "device":
        raise HTTPException(422, "a device cannot own household truth")
    if member_id != item["owner_member_id"] and item.get("sensitivity") != "normal":
        raise HTTPException(
            403, "sensitive candidates cannot be rerouted to another member"
        )
    if (
        runtime.settings.member_roles.get(member_id) == "child"
        and member_id != item["owner_member_id"]
        and not (item.get("candidate") or {}).get("child_safe")
    ):
        raise HTTPException(
            403, "child routing requires an explicit child-safe classification"
        )
    return member_id


def _require_configured_external_route(
    runtime: Any,
    *,
    provider: str,
    target_member: str,
    provider_account_id: str,
    container_external_id: str | None,
) -> None:
    if provider in {"google_calendar", "google_tasks"}:
        expected_fragment = (
            "google-calendar" if provider == "google_calendar" else "google-tasks"
        )
        container_key = (
            "calendar_id" if provider == "google_calendar" else "tasklist_id"
        )
        for record in runtime.settings.adapter_config.get("adapters", []):
            if not isinstance(record, dict):
                continue
            connection_id = str(record.get("id") or "")
            if (
                connection_id
                and runtime.settings_control.connection_lifecycle(connection_id)
                == "paused"
            ):
                continue
            adapter_id = str(
                record.get("id")
                or record.get("kind")
                or record.get("name")
                or record.get("pack_id")
                or ""
            )
            raw_config = record.get("config")
            config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
            if (
                expected_fragment in adapter_id
                and config.get("member_id") == target_member
                and (config.get("provider_account_id") or config.get("user_email"))
                == provider_account_id
                and (
                    config.get(container_key) == container_external_id
                    or (
                        provider == "google_calendar"
                        and bool(config.get("discover_calendars"))
                        and container_external_id
                        not in set(config.get("exclude_calendar_ids") or [])
                        and (
                            not config.get("include_calendar_ids")
                            or container_external_id
                            in set(config.get("include_calendar_ids") or [])
                        )
                    )
                )
            ):
                if provider == "google_calendar" and config.get("discover_calendars"):
                    if runtime.projections is None:
                        raise HTTPException(503, "calendar projection unavailable")
                    row = (
                        runtime.projections.connection("calendars")
                        .execute(
                            """
                        SELECT 1 FROM calendar_member_rules r
                        JOIN calendar_sources s
                          ON s.tenant_id=r.tenant_id
                         AND s.provider=r.provider
                         AND s.provider_account_id=r.provider_account_id
                         AND s.calendar_external_id=r.calendar_external_id
                        WHERE r.tenant_id=? AND r.subject_member_id=?
                          AND r.provider='google_calendar'
                          AND r.provider_account_id=?
                          AND r.calendar_external_id=?
                          AND r.enabled=1 AND r.write_target=1 AND s.write_events=1
                        """,
                            (
                                runtime.settings.instance.tenant_id,
                                target_member,
                                provider_account_id,
                                container_external_id,
                            ),
                        )
                        .fetchone()
                    )
                    if row is None:
                        continue
                return
        raise HTTPException(
            422, "the Google provider route is not configured for this member"
        )
    if provider in {"apple_calendar", "apple_reminders"}:
        expected_fragment = (
            "apple-calendar" if provider == "apple_calendar" else "apple-eventkit"
        )
        container_key = (
            "calendar_identifier"
            if provider == "apple_calendar"
            else "target_list_identifier"
        )
        for record in runtime.settings.adapter_config.get("adapters", []):
            if not isinstance(record, dict):
                continue
            connection_id = str(record.get("id") or "")
            if (
                connection_id
                and runtime.settings_control.connection_lifecycle(connection_id)
                == "paused"
            ):
                continue
            adapter_id = str(record.get("id") or record.get("kind") or "")
            raw_config = record.get("config")
            config = raw_config if isinstance(raw_config, dict) else {}
            configured_account = config.get("provider_account_id") or config.get(
                "member_id"
            )
            if (
                expected_fragment in adapter_id
                and config.get("member_id") == target_member
                and configured_account == provider_account_id
                and config.get(container_key) == container_external_id
            ):
                return
        active_members = {
            str(record.get("member_id"))
            for record in runtime.settings.bridge_enrollments.values()
            if isinstance(record, dict) and record.get("status") == "active"
        }
        if target_member not in active_members or provider_account_id != target_member:
            raise HTTPException(
                422, "the member's Apple bridge is not active for this route"
            )
        return
    raise HTTPException(422, "the external route is not a configured writable adapter")


def _require_reviewed_calendar_destination(
    runtime: Any,
    *,
    target_member: str,
    provider: str,
    provider_account_id: str,
    calendar_external_id: str,
) -> None:
    """Fail closed unless provider capability and member policy approve the route."""

    if runtime.projections is None:
        raise HTTPException(503, "calendar projection unavailable")
    row = (
        runtime.projections.connection("calendars")
        .execute(
            """
        SELECT 1
          FROM calendar_member_rules r
          JOIN calendar_sources s
            ON s.tenant_id=r.tenant_id
           AND s.member_id=r.source_member_id
           AND s.provider=r.provider
           AND s.provider_account_id=r.provider_account_id
           AND s.calendar_external_id=r.calendar_external_id
         WHERE r.tenant_id=? AND r.subject_member_id=?
           AND r.provider=? AND r.provider_account_id=?
           AND r.calendar_external_id=?
           AND r.enabled=1 AND r.write_target=1 AND s.write_events=1
         LIMIT 1
        """,
            (
                runtime.settings.instance.tenant_id,
                target_member,
                provider,
                provider_account_id,
                calendar_external_id,
            ),
        )
        .fetchone()
    )
    if row is None:
        raise HTTPException(
            422, "calendar route is not a reviewed writable destination"
        )


def _materialization_envelopes(
    runtime: Any,
    session: Session,
    item: dict[str, Any],
    *,
    request_id: str,
) -> list[EventEnvelope]:
    """Translate a reviewed candidate through a closed, typed allowlist."""

    now = EventEnvelope.now_utc_iso()
    candidate = dict(item.get("candidate") or {})
    title = str(item.get("title") or candidate.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "a confirmed candidate requires a title")
    target_kind = item["target_kind"]
    target_member = _target_member(runtime, item)
    common = {
        "event_at_ms": int(time.time() * 1000),
        "tenant_id": session.tenant_id,
        "occurred_at": now,
        "source_adapter": "member-portal",
        "source_account_id": session.auth_member_id,
        "owner_scope": f"private:{target_member}",
        "visibility_scope": f"private:{target_member}",
        "sensitivity": item.get("sensitivity") or "normal",
        "correlation_id": session.correlation_id,
        "causation_id": item["last_event_id"],
        "actor_identity": session.auth_member_id,
        "raw_ref": f"triage-state-sha256:{_triage_state_fingerprint(item)}",
    }
    if target_kind == "task":
        return [
            EventEnvelope(
                **common,
                type="task.created",
                schema_version=1,
                idempotency_key=f"triage:materialize:{item['proposal_id']}:task",
                payload={
                    "task_id": f"triage_task_{item['proposal_id']}",
                    "title": title,
                    "description": candidate.get("description"),
                    "owner_member_id": target_member,
                    "due": candidate.get("due"),
                    "energy": candidate.get("energy"),
                    "effort_min": candidate.get("effort_min"),
                    "source_commitment_id": candidate.get("source_commitment_id"),
                },
            )
        ]
    if target_kind == "recurrence":
        rrule = str(candidate.get("rrule") or "").strip()
        next_occurrence = str(candidate.get("next_occurrence") or "").strip()
        if not rrule or not next_occurrence:
            raise HTTPException(
                422, "a recurrence requires an RRULE and next occurrence"
            )
        linked_kind = str(candidate.get("linked_kind") or "party")
        linked_id = str(candidate.get("linked_id") or target_member)
        if linked_kind not in {"party", "asset", "account", "household"}:
            raise HTTPException(422, "unsupported recurrence relationship")
        return [
            EventEnvelope(
                **common,
                type="recurrence.added",
                schema_version=1,
                idempotency_key=(
                    f"triage:materialize:{item['proposal_id']}:recurrence"
                ),
                payload={
                    "recurrence_id": f"triage_recurrence_{item['proposal_id']}",
                    "linked_kind": linked_kind,
                    "linked_id": linked_id,
                    "kind": str(candidate.get("kind") or "household_rhythm"),
                    "rrule": rrule,
                    "next_occurrence": next_occurrence,
                    "lead_time_days": int(candidate.get("lead_time_days") or 0),
                    "trackable": bool(candidate.get("trackable", False)),
                    "notes": candidate.get("notes") or title,
                },
            )
        ]
    if target_kind == "commitment":
        owed_to = candidate.get("owed_to_party_id")
        if not owed_to:
            raise HTTPException(422, "a commitment requires an owed-to party")
        commitment_id = f"triage_commitment_{item['proposal_id']}"
        proposed = EventEnvelope(
            **common,
            type="commitment.proposed",
            schema_version=1,
            idempotency_key=f"triage:materialize:{item['proposal_id']}:commitment",
            payload={
                "commitment_id": commitment_id,
                "kind": candidate.get("kind", "other"),
                "owed_by_member_id": target_member,
                "owed_to_party_id": str(owed_to),
                "text_summary": title,
                "suggested_due": candidate.get("due"),
                "urgency": candidate.get("urgency", "this_week"),
                "confidence": float(item.get("confidence") or 1.0),
                "strength": "confident",
                "source_interaction_id": candidate.get("source_interaction_id"),
                "source_message_preview": candidate.get("source_message_preview"),
                "classify_reasons": list(
                    item.get("reasons") or ["member-confirmed triage"]
                ),
            },
        )
        confirmed = EventEnvelope(
            **common,
            type="commitment.confirmed",
            schema_version=1,
            idempotency_key=f"triage:materialize:{item['proposal_id']}:commitment-confirmed",
            payload={
                "commitment_id": commitment_id,
                "confirmed_by_member_id": session.auth_member_id,
                "confirmed_at": now,
                "note": "Confirmed through Triage",
            },
        )
        return [proposed, confirmed]
    if target_kind == "party_identifier":
        required = {
            "surviving_party_id",
            "candidate_kind",
            "candidate_value",
            "candidate_value_normalized",
        }
        if not required.issubset(candidate):
            raise HTTPException(422, "an identity candidate is missing required fields")
        identifier_kind = str(candidate["candidate_kind"])
        if identifier_kind not in {"email", "phone", "imessage_handle"}:
            raise HTTPException(422, "unsupported identity candidate kind")
        if runtime.projections is None:
            raise HTTPException(503, "parties projection unavailable")
        parties = runtime.projections.connection("parties")
        party_id = str(candidate["surviving_party_id"])
        if get_party(parties, session, party_id=party_id) is None:
            raise HTTPException(422, "identity candidate party is unavailable")
        existing = parties.execute(
            """
            SELECT party_id
              FROM identifiers
             WHERE tenant_id = ? AND kind = ? AND value_normalized = ?
             LIMIT 1
            """,
            (
                session.tenant_id,
                identifier_kind,
                str(candidate["candidate_value_normalized"]),
            ),
        ).fetchone()
        if existing is not None:
            raise HTTPException(409, "identity candidate is already assigned")
        return [
            EventEnvelope(
                **common,
                type="identifier.added",
                schema_version=1,
                idempotency_key=(
                    f"triage:materialize:{item['proposal_id']}:party-identifier"
                ),
                payload={
                    "identifier_id": f"triage_identifier_{item['proposal_id']}",
                    "party_id": party_id,
                    "kind": identifier_kind,
                    "value": str(candidate["candidate_value"]),
                    "value_normalized": str(candidate["candidate_value_normalized"]),
                    "verified": False,
                    "primary_for_kind": False,
                },
            )
        ]

    if target_kind == "party_candidate":
        party_kind = str(candidate.get("kind") or "")
        if party_kind not in {"person", "organization"}:
            raise HTTPException(
                422, "a People candidate must be a person or organization"
            )
        display_name = str(candidate.get("display_name") or title).strip()
        if not display_name:
            raise HTTPException(422, "a People candidate requires a display name")
        seed = f"{session.tenant_id}:{target_member}:{item['proposal_id']}"
        party_id = f"party_triage_{hashlib.sha256(seed.encode()).hexdigest()[:22]}"
        envelopes = [
            EventEnvelope(
                **common,
                type="party.created",
                schema_version=1,
                idempotency_key=f"triage:materialize:{item['proposal_id']}:party",
                payload={
                    "party_id": party_id,
                    "kind": party_kind,
                    "display_name": display_name,
                    "sort_name": str(
                        candidate.get("sort_name") or display_name
                    ).casefold(),
                    "nickname": candidate.get("nickname"),
                    "attributes": {
                        "organization_name": candidate.get("organization_name"),
                        "job_title": candidate.get("job_title"),
                    },
                },
            )
        ]
        identifier_value = str(candidate.get("identifier_value") or "").strip()
        normalized = str(candidate.get("identifier_value_normalized") or "").strip()
        identifier_kind = str(candidate.get("identifier_kind") or "").strip()
        if any((identifier_value, normalized, identifier_kind)):
            if not all((identifier_value, normalized, identifier_kind)):
                raise HTTPException(
                    422, "a People endpoint requires kind, value, and normalized value"
                )
            envelopes.append(
                EventEnvelope(
                    **common,
                    type="identifier.added",
                    schema_version=1,
                    idempotency_key=f"triage:materialize:{item['proposal_id']}:identifier",
                    payload={
                        "identifier_id": f"triage_identifier_{item['proposal_id']}",
                        "party_id": party_id,
                        "kind": identifier_kind,
                        "value": identifier_value,
                        "value_normalized": normalized,
                        "verified": False,
                        "primary_for_kind": True,
                    },
                )
            )
        return envelopes

    if target_kind == "party_relationship":
        party_a = str(candidate.get("party_a") or "")
        party_b = str(candidate.get("party_b") or "")
        direction = str(candidate.get("direction") or "")
        if direction not in {"a_to_b", "b_to_a", "mutual"}:
            raise HTTPException(422, "a relationship requires a supported direction")
        if not party_a or not party_b or party_a == party_b:
            raise HTTPException(422, "a relationship requires two different Parties")
        if runtime.projections is None:
            raise HTTPException(503, "parties projection unavailable")
        parties = runtime.projections.connection("parties")
        if any(
            get_party(parties, session, party_id=value) is None
            for value in (party_a, party_b)
        ):
            raise HTTPException(422, "a relationship Party is unavailable")
        return [
            EventEnvelope(
                **common,
                type="relationship.added",
                schema_version=1,
                idempotency_key=f"triage:materialize:{item['proposal_id']}:relationship",
                payload={
                    "relationship_id": f"triage_relationship_{item['proposal_id']}",
                    "party_a": party_a,
                    "party_b": party_b,
                    "label": str(candidate.get("label") or title),
                    "direction": direction,
                    "since": candidate.get("since"),
                    "attributes": {},
                },
            )
        ]

    if target_kind == "relationship_fact":
        fact_kind = str(candidate.get("kind") or "")
        if fact_kind not in {
            "birthday",
            "important_date",
            "employer",
            "role",
            "preference",
            "address",
            "context",
        }:
            raise HTTPException(422, "unsupported relationship-fact kind")
        party_id = str(candidate.get("party_id") or "")
        if (
            runtime.projections is None
            or get_party(
                runtime.projections.connection("parties"), session, party_id=party_id
            )
            is None
        ):
            raise HTTPException(422, "relationship-fact Party is unavailable")
        return [
            EventEnvelope(
                **common,
                type="relationship_fact.confirmed",
                schema_version=1,
                idempotency_key=f"triage:materialize:{item['proposal_id']}:relationship-fact",
                payload={
                    "fact_id": f"triage_fact_{item['proposal_id']}",
                    "member_id": target_member,
                    "party_id": party_id,
                    "kind": fact_kind,
                    "label": candidate.get("label"),
                    "value": str(candidate.get("value") or ""),
                    "effective_date": candidate.get("effective_date"),
                    "source_event_id": item["source_event_id"],
                    "evidence_quote": candidate.get("evidence_quote"),
                    "confirmed_at": now,
                    "confirmed_by_member_id": session.auth_member_id,
                },
            )
        ]

    route = item.get("route") or {}
    provider = route.get("provider")
    provider_account_id = route.get("provider_account_id")
    container = route.get("container_external_id")
    if not provider or not provider_account_id:
        raise HTTPException(
            422, "external candidates require a provider and account route"
        )
    _require_configured_external_route(
        runtime,
        provider=str(provider),
        target_member=target_member,
        provider_account_id=str(provider_account_id),
        container_external_id=str(container) if container is not None else None,
    )
    if target_kind == "calendar_event":
        if provider not in {"google_calendar", "apple_calendar"} or not container:
            raise HTTPException(
                422, "calendar candidates require a writable calendar route"
            )
        _require_reviewed_calendar_destination(
            runtime,
            target_member=target_member,
            provider=str(provider),
            provider_account_id=str(provider_account_id),
            calendar_external_id=str(container),
        )
        if not candidate.get("start") or not candidate.get("end"):
            raise HTTPException(422, "calendar candidates require start and end")
        operation = "create_event"
        candidate.setdefault("calendar_id", container)
        candidate.setdefault("calendar_external_id", container)
        candidate.setdefault("summary", title)
    elif target_kind in {"reminder", "list_item"}:
        if provider not in {"apple_reminders", "google_tasks"} or not container:
            raise HTTPException(
                422, "reminder candidates require a writable task-list route"
            )
        operation = (
            "create_reminder" if provider == "apple_reminders" else "create_task"
        )
        candidate.setdefault(
            "target_list_id" if provider == "apple_reminders" else "tasklist_id",
            container,
        )
        candidate.setdefault("title", title)
        candidate.setdefault("member_id", target_member)
    elif target_kind == "list":
        if provider != "apple_reminders":
            raise HTTPException(422, "list creation currently requires Apple Reminders")
        operation = "create_list"
        candidate.setdefault("list_name", title)
        candidate.setdefault("member_id", target_member)
    else:
        raise HTTPException(422, "unsupported triage target")
    if operation not in _EXTERNAL_OPERATIONS.get(str(provider), set()):
        raise HTTPException(422, "the routed provider does not support this write")
    payload = {
        "command_id": f"triage:{item['proposal_id']}:{request_id}",
        "member_id": target_member,
        "provider": provider,
        "provider_account_id": provider_account_id,
        "operation": operation,
        "target_external_id": None,
        "expected_provider_revision": None,
        "payload": candidate,
        "requested_at": now,
    }
    return [
        EventEnvelope(
            **common,
            type="external_sync.write_requested",
            schema_version=1,
            idempotency_key=f"triage:materialize:{item['proposal_id']}:external",
            payload=payload,
        )
    ]


def _reminder_account_for_command(
    runtime: Any, session: Session, body: ReminderCommandRequest
) -> dict[str, Any]:
    conn = runtime.projections.connection("lists")
    items = list_items_for_member(conn, session, member_id=session.auth_member_id)
    lists = lists_for_member(conn, session, member_id=session.auth_member_id)
    account = next(
        (
            account
            for account in reminder_accounts(
                runtime,
                session.auth_member_id,
                projected_items=items,
                projected_lists=lists,
            )
            if account["provider"] == body.provider
            and account["provider_account_id"] == body.provider_account_id
        ),
        None,
    )
    if account is None:
        raise HTTPException(404, "reminder account not found")
    if body.operation not in account["operations"]:
        raise HTTPException(
            409,
            account.get("remedy")
            or "this provider account does not support that reminder action",
        )
    return account


def _validate_reminder_command_target(
    runtime: Any,
    session: Session,
    body: ReminderCommandRequest,
    provider_payload: dict[str, Any],
) -> dict[str, Any] | None:
    conn = runtime.projections.connection("lists")
    owned_lists = lists_for_member(conn, session, member_id=session.auth_member_id)

    def owned_list(list_external_id: str | None) -> dict[str, Any] | None:
        if not list_external_id:
            return None
        return next(
            (
                row
                for row in owned_lists
                if row["provider"] == body.provider
                and row["provider_account_id"] == body.provider_account_id
                and row["list_external_id"] == list_external_id
            ),
            None,
        )

    item_operations = {
        "update_reminder",
        "delete_reminder",
        "complete_reminder",
        "uncomplete_reminder",
        "move_reminder",
        "update_task",
        "delete_task",
        "complete_task",
        "uncomplete_task",
        "move_task",
    }
    if body.operation in item_operations:
        if not body.list_external_id or not body.item_external_id:
            raise HTTPException(422, "this action requires a source list and item")
        item = get_list_item(
            conn,
            session,
            list_external_id=body.list_external_id,
            item_external_id=body.item_external_id,
        )
        if (
            item is None
            or item.get("member_id") != session.auth_member_id
            or item.get("provider") != body.provider
            or item.get("provider_account_id") != body.provider_account_id
        ):
            raise HTTPException(404, "reminder not found")
        if item.get("read_only_reason"):
            raise HTTPException(409, str(item["read_only_reason"]))
        if not body.expected_last_event_id:
            raise HTTPException(422, "an item action requires expected_last_event_id")
        if item["last_event_id"] != body.expected_last_event_id:
            raise HTTPException(409, "reminder changed; refresh before acting")
        if body.operation in {"move_reminder", "move_task"}:
            destination = provider_payload.get("destination_list_external_id")
            if not isinstance(destination, str) or not destination:
                raise HTTPException(422, "a move requires a destination list")
            if owned_list(destination) is None:
                raise HTTPException(404, "destination reminder list not found")
        return item

    if body.operation in {"create_reminder", "create_task", "clear_completed_tasks"}:
        if not body.list_external_id:
            raise HTTPException(422, "this action requires a destination list")
        row = owned_list(body.list_external_id)
        if row is None:
            raise HTTPException(404, "destination reminder list not found")
        if body.operation == "clear_completed_tasks":
            if not body.expected_last_event_id:
                raise HTTPException(
                    422, "clearing completed tasks requires expected_last_event_id"
                )
            if row["last_event_id"] != body.expected_last_event_id:
                raise HTTPException(409, "reminder list changed; refresh before acting")
        return row
    if body.operation in {"update_list", "delete_list"}:
        if not body.list_external_id:
            raise HTTPException(422, "this action requires a list")
        row = owned_list(body.list_external_id)
        if row is None:
            raise HTTPException(404, "reminder list not found")
        if not body.expected_last_event_id:
            raise HTTPException(422, "a list action requires expected_last_event_id")
        if row["last_event_id"] != body.expected_last_event_id:
            raise HTTPException(409, "reminder list changed; refresh before acting")
        return row
    return None


def _provider_reminder_payload(
    body: ReminderCommandRequest,
) -> tuple[dict[str, Any], frozenset[str]]:
    """Return the exact, provider-native payload or fail before governance."""

    allowed_fields = _REMINDER_PAYLOAD_FIELDS[body.operation]
    unexpected = set(body.payload) - allowed_fields
    if unexpected:
        raise HTTPException(
            422, f"unsupported provider fields: {', '.join(sorted(unexpected))}"
        )
    provider_payload = {
        key: body.payload[key] for key in body.payload if key in allowed_fields
    }
    if (
        body.operation in {"create_reminder", "create_task"}
        and not str(provider_payload.get("title") or "").strip()
    ):
        raise HTTPException(422, "a new reminder requires a title")

    if body.provider == "google_tasks":
        if body.operation in {"create_list", "update_list"}:
            unexpected_list_fields = set(provider_payload) - {"title"}
            if unexpected_list_fields:
                raise HTTPException(
                    422,
                    "Google Tasks lists only expose a title through this adapter",
                )
            if not str(provider_payload.get("title") or "").strip():
                raise HTTPException(422, "a Google Tasks list requires a title")
        due = provider_payload.get("due")
        if isinstance(due, str) and "T" not in due:
            # Google accepts RFC 3339 but stores only the date component.
            try:
                parsed_due = datetime.strptime(due, "%Y-%m-%d")
            except ValueError as exc:
                raise HTTPException(422, "due must be YYYY-MM-DD") from exc
            provider_payload["due"] = parsed_due.strftime("%Y-%m-%dT00:00:00.000Z")
    else:
        if body.operation in {"create_list", "update_list"}:
            unexpected_list_fields = set(provider_payload) - {"name", "color"}
            if unexpected_list_fields:
                raise HTTPException(
                    422,
                    "Apple Reminders lists expose name and color through this adapter",
                )
            if (
                body.operation == "create_list"
                and not str(provider_payload.get("name") or "").strip()
            ):
                raise HTTPException(422, "an Apple Reminders list requires a name")
            if "name" in provider_payload:
                provider_payload["list_name"] = provider_payload.pop("name")
        for field in ("start", "due"):
            value = provider_payload.get(field)
            if isinstance(value, str):
                try:
                    parsed = datetime.strptime(value, "%Y-%m-%d")
                except ValueError as exc:
                    raise HTTPException(
                        422,
                        f"Apple Reminders {field} must be YYYY-MM-DD or date components",
                    ) from exc
                provider_payload[field] = {
                    "year": parsed.year,
                    "month": parsed.month,
                    "day": parsed.day,
                    "all_day": True,
                    "floating": True,
                }
    return provider_payload, allowed_fields


@router.post("/api/member/v1/reminders/commands", status_code=202)
async def reminder_command(
    body: ReminderCommandRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    """Validate, authorize, and enqueue one provider-shaped reminder mutation."""

    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "reminder actions are unavailable while viewing another member"
        )
    if not body.openclaw_approved:
        raise HTTPException(403, "independent OpenClaw action approval is required")
    runtime = _runtime(request)
    if runtime.projections is None or runtime.events is None:
        raise HTTPException(503, "reminder state is unavailable")
    _reminder_account_for_command(runtime, session, body)
    provider_payload, allowed_fields = _provider_reminder_payload(body)
    target = _validate_reminder_command_target(runtime, session, body, provider_payload)
    if body.provider == "apple_reminders" and body.operation == "move_reminder":
        provider_payload["destination_list_id"] = provider_payload.pop(
            "destination_list_external_id"
        )

    expected_provider_revision = body.expected_provider_revision or (
        (target or {}).get("provider_revision")
    )

    provider_payload.update(
        {
            "member_id": session.auth_member_id,
            "list_external_id": body.list_external_id,
            "tasklist_id": body.list_external_id,
            "item_external_id": body.item_external_id,
            "expected_provider_revision": expected_provider_revision,
            "content_privacy_guard": {
                "member_bound": True,
                "provider_fields_allowlisted": sorted(allowed_fields),
                "source_scope": f"private:{session.auth_member_id}",
            },
            "openclaw_approved": True,
        }
    )
    target_external_id = body.item_external_id or body.list_external_id
    now = EventEnvelope.now_utc_iso()
    command_payload = {
        "command_id": body.request_id,
        "member_id": session.auth_member_id,
        "provider": body.provider,
        "provider_account_id": body.provider_account_id,
        "operation": body.operation,
        "target_external_id": target_external_id,
        "expected_provider_revision": expected_provider_revision,
        "payload": provider_payload,
        "requested_at": now,
    }
    decision = await runtime.guarded_write.check(
        session, "external_sync.write_requested", command_payload
    )
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {"reason": decision.reason, "correlation_id": decision.correlation_id},
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    key = f"reminder-command:{session.auth_member_id}:{body.request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is not None:
        if prior.get("raw_ref") != f"request-sha256:{fingerprint}":
            raise HTTPException(
                409, "idempotency key conflicts with prior reminder action"
            )
        return {
            "accepted": True,
            "duplicate": True,
            "command_id": body.request_id,
            "event_id": prior["event_id"],
        }
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="external_sync.write_requested",
            schema_version=1,
            occurred_at=now,
            source_adapter="member-reminders",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity=cast(
                Sensitivity, (target or {}).get("sensitivity") or "normal"
            ),
            correlation_id=decision.correlation_id,
            causation_id=(target or {}).get("last_event_id"),
            actor_identity=session.auth_member_id,
            idempotency_key=key,
            raw_ref=f"request-sha256:{fingerprint}",
            payload=command_payload,
        )
    )
    return {
        "accepted": True,
        "duplicate": False,
        "command_id": body.request_id,
        "event_id": event_id,
        "observation_mode": runtime._observation_active,
    }


@router.post("/api/member/v1/reminders/cues", status_code=202)
async def configure_reminder_cue(
    body: ReminderCueRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Attach one deterministic cue rule to an owned reminder item."""

    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "reminder cues are unavailable in view-as mode")
    runtime = _runtime(request)
    item = get_list_item(
        runtime.projections.connection("lists"),
        session,
        list_external_id=body.list_external_id,
        item_external_id=body.item_external_id,
    )
    if item is None or item.get("member_id") != session.auth_member_id:
        raise HTTPException(404, "reminder not found")
    if item["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "reminder changed; refresh before adding the cue")
    if body.operator in {"equals", "not_equals"} and body.expected_state is None:
        raise HTTPException(422, f"{body.operator} requires expected_state")
    if body.operator in {"above", "below"} and body.expected_number is None:
        raise HTTPException(422, f"{body.operator} requires expected_number")
    payload = {
        "cue_id": body.cue_id,
        "member_id": session.auth_member_id,
        "provider": item["provider"],
        "provider_account_id": item["provider_account_id"],
        "list_external_id": body.list_external_id,
        "item_external_id": body.item_external_id,
        "signal_key": body.signal_key,
        "source": body.source,
        "kind": body.kind,
        "operator": body.operator,
        "expected_state": body.expected_state,
        "expected_number": body.expected_number,
        "enabled": body.enabled,
        "configured_at": EventEnvelope.now_utc_iso(),
    }
    await _guard_member_write(runtime, session, "reminder.cue.configured", payload)
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="reminder.cue.configured",
            schema_version=1,
            occurred_at=payload["configured_at"],
            source_adapter="member-reminders",
            source_account_id=session.auth_member_id,
            owner_scope=item["owner_scope"],
            visibility_scope=item["visibility_scope"],
            sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
            correlation_id=session.correlation_id,
            causation_id=item["last_event_id"],
            actor_identity=session.auth_member_id,
            idempotency_key=f"reminder-cue:{session.auth_member_id}:{body.cue_id}",
            payload=payload,
        )
    )
    await runtime.wait_for_projection("lists", event_id)
    return {"accepted": True, "event_id": event_id, "cue_id": body.cue_id}


@router.post("/api/member/v1/triage/{proposal_id}/enrich")
async def enrich_triage_item(
    proposal_id: str,
    body: TriageEnrichmentRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.triage_command_locks.setdefault(
        f"{session.tenant_id}:{proposal_id}", asyncio.Lock()
    )
    async with lock:
        return await _enrich_triage_item_locked(
            runtime, proposal_id, body, request, session
        )


async def _enrich_triage_item_locked(
    runtime: Any,
    proposal_id: str,
    body: TriageEnrichmentRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    item = _owned_triage_item(runtime, session, proposal_id)
    if item["source_kind"] == "commitment_proposal":
        if body.target_kind not in {None, "commitment"} or body.route is not None:
            raise HTTPException(
                422,
                "existing commitment proposals may change only description or due date",
            )
        unexpected = set(body.candidate_updates) - {"due", "due_at"}
        if unexpected:
            raise HTTPException(422, "unsupported commitment enrichment fields")
        result = await comms.edit_commitment(
            proposal_id,
            comms.EditRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                description=body.title,
                due_at=body.candidate_updates.get("due_at")
                or body.candidate_updates.get("due"),
            ),
            request,
            session,
        )
        await runtime.wait_for_projection("triage", result["event_id"])
        return {**result, "triage": _owned_triage_item(runtime, session, proposal_id)}
    request_fingerprint = _triage_request_fingerprint(proposal_id, body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.item.enriched",
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior is not None:
        await runtime.wait_for_projection("triage", prior["event_id"])
        return {
            "event_id": prior["event_id"],
            "triage": _owned_triage_item(runtime, session, proposal_id),
            "duplicate": True,
        }
    _require_current_triage(item, body.expected_last_event_id)
    payload = {
        "proposal_id": proposal_id,
        "enriched_at": EventEnvelope.now_utc_iso(),
        "enriched_by_member_id": session.auth_member_id,
        "title": body.title,
        "target_kind": body.target_kind,
        "candidate_updates": body.candidate_updates,
        "route": body.route.model_dump(mode="json") if body.route else None,
    }
    if not any(
        (body.title, body.target_kind, body.candidate_updates, body.route is not None)
    ):
        raise HTTPException(422, "an enrichment must change at least one field")
    await _guard_triage_write(runtime, session, "triage.item.enriched", payload)
    envelope = _triage_event(
        session,
        event_type="triage.item.enriched",
        payload=payload,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        causation_id=item["last_event_id"],
        sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
    )
    event_id = await runtime.events.write(envelope)
    await runtime.wait_for_projection("triage", event_id)
    return {
        "event_id": event_id,
        "triage": _owned_triage_item(runtime, session, proposal_id),
    }


@router.post("/api/member/v1/triage/{proposal_id}/approve")
async def approve_triage_item(
    proposal_id: str,
    body: TriageDecisionRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.triage_command_locks.setdefault(
        f"{session.tenant_id}:{proposal_id}", asyncio.Lock()
    )
    async with lock:
        return await _approve_triage_item_locked(
            runtime, proposal_id, body, request, session
        )


async def _approve_triage_item_locked(
    runtime: Any,
    proposal_id: str,
    body: TriageDecisionRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    item = _owned_triage_item(runtime, session, proposal_id)
    if item["source_kind"] == "commitment_proposal":
        result = await comms.approve_commitment(
            proposal_id,
            comms.ApproveRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                send_acknowledgement=False,
                openclaw_approved=False,
            ),
            request,
            session,
        )
        await runtime.wait_for_projection("triage", result["event_id"])
        return {**result, "triage_state": "confirmed"}

    request_fingerprint = _triage_request_fingerprint(proposal_id, body)
    prior_confirmation = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.item.confirmed",
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior_confirmation is not None:
        await runtime.wait_for_projection("triage", prior_confirmation["event_id"])
        return {
            "event_id": prior_confirmation["event_id"],
            "state": "confirmed",
            "materialized_event_ids": prior_confirmation["payload"][
                "materialized_event_ids"
            ],
            "duplicate": True,
        }
    _require_current_triage(item, body.expected_last_event_id)
    materializations = _materialization_envelopes(
        runtime, session, item, request_id=body.request_id
    )
    prior_materializations = [
        await runtime.event_log.get_by_idempotency(
            session.tenant_id, cast(str, envelope.idempotency_key)
        )
        for envelope in materializations
    ]
    if any(prior_materializations) and not all(prior_materializations):
        raise HTTPException(409, "triage materialization is incomplete; run Doctor")
    if all(prior_materializations) and any(
        cast(dict[str, Any], prior).get("raw_ref") != envelope.raw_ref
        for prior, envelope in zip(
            prior_materializations, materializations, strict=True
        )
    ):
        raise HTTPException(
            409, "triage candidate changed after materialization; run Doctor"
        )
    confirm_payload_for_gate = {
        "proposal_id": proposal_id,
        "confirmed_by_member_id": session.auth_member_id,
        "target_kind": item["target_kind"],
    }
    await _guard_triage_write(
        runtime, session, "triage.item.confirmed", confirm_payload_for_gate
    )
    if all(prior_materializations):
        materialized_event_ids = [
            cast(dict[str, Any], prior)["event_id"] for prior in prior_materializations
        ]
    else:
        for envelope in materializations:
            await _guard_triage_write(runtime, session, envelope.type, envelope.payload)
        materialized_event_ids = await runtime.events.write_batch(materializations)
    confirmed_at = EventEnvelope.now_utc_iso()
    confirm = _triage_event(
        session,
        event_type="triage.item.confirmed",
        payload={
            "proposal_id": proposal_id,
            "confirmed_at": confirmed_at,
            "confirmed_by_member_id": session.auth_member_id,
            "materialized_event_ids": materialized_event_ids,
        },
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        causation_id=materialized_event_ids[-1],
        sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
    )
    event_id = await runtime.events.write(confirm)
    await runtime.wait_for_projection("triage", event_id)
    return {
        "event_id": event_id,
        "state": "confirmed",
        "materialized_event_ids": materialized_event_ids,
    }


@router.post("/api/member/v1/triage/{proposal_id}/dismiss")
async def dismiss_triage_item(
    proposal_id: str,
    body: TriageDecisionRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.triage_command_locks.setdefault(
        f"{session.tenant_id}:{proposal_id}", asyncio.Lock()
    )
    async with lock:
        return await _dismiss_triage_item_locked(
            runtime, proposal_id, body, request, session
        )


async def _dismiss_triage_item_locked(
    runtime: Any,
    proposal_id: str,
    body: TriageDecisionRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    item = _owned_triage_item(runtime, session, proposal_id)
    if item["source_kind"] == "commitment_proposal":
        result = await comms.dismiss_commitment(
            proposal_id,
            comms.DismissRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                reason=body.reason,
            ),
            request,
            session,
        )
        await runtime.wait_for_projection("triage", result["event_id"])
        return {**result, "triage_state": "dismissed"}
    request_fingerprint = _triage_request_fingerprint(proposal_id, body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.item.dismissed",
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior is not None:
        await runtime.wait_for_projection("triage", prior["event_id"])
        return {"event_id": prior["event_id"], "state": "dismissed", "duplicate": True}
    _require_current_triage(item, body.expected_last_event_id)
    payload = {
        "proposal_id": proposal_id,
        "dismissed_at": EventEnvelope.now_utc_iso(),
        "dismissed_by_member_id": session.auth_member_id,
        "reason": body.reason,
    }
    await _guard_triage_write(runtime, session, "triage.item.dismissed", payload)
    envelope = _triage_event(
        session,
        event_type="triage.item.dismissed",
        payload=payload,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        causation_id=item["last_event_id"],
        sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
    )
    event_id = await runtime.events.write(envelope)
    await runtime.wait_for_projection("triage", event_id)
    return {"event_id": event_id, "state": "dismissed"}


def _chat_policy(runtime: Any, session: Session) -> dict[str, Any]:
    policy = runtime.member_chat_policy(session.auth_member_id, session.auth_role)
    if session.auth_member_id != session.view_member_id:
        return {
            "state": "unauthorized",
            "enabled": False,
            "reason": "view_as_has_no_chat",
        }
    return policy


def _require_chat(runtime: Any, session: Session) -> tuple[str, str]:
    policy = _chat_policy(runtime, session)
    if not policy.get("enabled"):
        raise HTTPException(404, "chat not found")
    if policy.get("state") != "healthy":
        raise HTTPException(503, {"code": policy.get("reason", "chat_unavailable")})
    return runtime.member_chat_binding(session.auth_member_id)


def _safe_error(exc: MemberChatError, correlation_id: str) -> dict[str, Any]:
    return {
        "kind": "error",
        "code": exc.code,
        "retryable": exc.retryable,
        "correlation_id": correlation_id,
    }


def _sse(value: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(value, separators=(',', ':'))}\n\n".encode("utf-8")


async def _expire_due(runtime: Any, session: Session) -> None:
    for spec in runtime.temporary_surfaces.expire_due():
        await _audit_surface(runtime, session, spec, "expired")


async def _audit_surface(
    runtime: Any,
    session: Session,
    spec: TemporarySurfaceSpec,
    transition: Literal["created", "activated", "promoted", "expired", "archived"],
) -> str:
    now = EventEnvelope.now_utc_iso()
    return await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type=f"temporary_surface.{transition}",
            schema_version=1,
            occurred_at=now,
            source_adapter="member-portal",
            source_account_id="temporary-surfaces",
            owner_scope=f"private:{spec.owner_member_id}",
            visibility_scope=f"private:{spec.owner_member_id}",
            sensitivity="normal",
            actor_identity=session.auth_member_id,
            payload={
                "stable_id": spec.stable_id,
                "owner_member_id": spec.owner_member_id,
                "actor_member_id": session.auth_member_id,
                "transition": transition,
                "audience_member_ids": list(spec.audience_member_ids),
                "specification_version": spec.updated_version,
                "occurred_at": now,
            },
            idempotency_key=f"temporary-surface:{spec.stable_id}:{transition}:{spec.updated_version}",
        ),
        correlation_id=session.correlation_id,
    )


def _visible_spec(
    runtime: Any, session: Session, stable_id: str
) -> TemporarySurfaceSpec:
    spec = runtime.temporary_surfaces.get(stable_id)
    if (
        spec is None
        or spec.status != "active"
        or session.auth_member_id not in spec.audience_member_ids
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(404, "temporary surface not found")
    if session.auth_role in {"device", "ambient"}:
        raise HTTPException(404, "temporary surface not found")
    if session.auth_role == "child":
        if "child" not in spec.compatible_modes:
            raise HTTPException(404, "temporary surface not found")
        if {query["name"] for query in spec.queries} - CHILD_QUERIES:
            raise HTTPException(404, "temporary surface not found")
        if set(spec.action_verbs) - CHILD_ACTIONS:
            raise HTTPException(404, "temporary surface not found")
    return spec


def _member_command_fingerprint(item_kind: str, item_id: str, body: BaseModel) -> str:
    canonical = json.dumps(
        {
            "item_kind": item_kind,
            "item_id": item_id,
            "body": body.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _prior_member_command(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    request_id: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    if runtime.event_log is None:
        raise HTTPException(503, "event log unavailable")
    key = f"member-command:{event_type}:{session.auth_member_id}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is None:
        return None
    if prior["raw_ref"] != f"request-sha256:{fingerprint}":
        raise HTTPException(409, "idempotency key conflicts with prior action body")
    return prior


async def _guard_member_write(
    runtime: Any, session: Session, event_type: str, payload: dict[str, Any]
) -> None:
    if runtime.guarded_write is None:
        raise HTTPException(503, "write authorization unavailable")
    decision = await runtime.guarded_write.check(session, event_type, payload)
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {"reason": decision.reason, "correlation_id": decision.correlation_id},
        )


def _member_command_event(
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    request_id: str,
    fingerprint: str,
    causation_id: str,
    owner_scope: str,
    visibility_scope: str,
    sensitivity: Sensitivity,
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="member-portal",
        source_account_id=session.auth_member_id,
        owner_scope=owner_scope,
        visibility_scope=visibility_scope,
        sensitivity=sensitivity,
        correlation_id=session.correlation_id,
        causation_id=causation_id,
        actor_identity=session.auth_member_id,
        idempotency_key=(
            f"member-command:{event_type}:{session.auth_member_id}:{request_id}"
        ),
        raw_ref=f"request-sha256:{fingerprint}",
        payload=payload,
    )


async def _complete_recurrence(
    body: MemberLifecycleRequest, request: Request, session: Session
) -> dict[str, Any]:
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "routine completion is unavailable while viewing another member"
        )
    if body.action != "complete":
        raise HTTPException(422, "trackable recurrences support completion only")
    runtime = _runtime(request)
    fingerprint = _member_command_fingerprint("recurrence", body.item_id, body)
    prior = await _prior_member_command(
        runtime,
        session,
        event_type="recurrence.completed",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    if prior is not None:
        return {
            "event_id": prior["event_id"],
            "duplicate": True,
            "recurrence": get_recurrence(
                runtime.projections.connection("recurrences"),
                session,
                recurrence_id=body.item_id,
            ),
            "what_now": core._what_now(runtime, session),
        }
    row = get_recurrence(
        runtime.projections.connection("recurrences"),
        session,
        recurrence_id=body.item_id,
    )
    if row is None:
        raise HTTPException(404, "trackable recurrence not found")
    owned = (
        row["linked_kind"] == "party" and row["linked_id"] == session.auth_member_id
    ) or (row["linked_kind"] == "household" and session.auth_role == "principal")
    if not owned or not row.get("trackable"):
        raise HTTPException(404, "trackable recurrence not found")
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "recurrence changed; refresh before completing it")
    now = EventEnvelope.now_utc_iso()
    payload = {
        "recurrence_id": body.item_id,
        "completed_at": now,
        "completed_by_party_id": session.auth_member_id,
        "occurrence_date": str(row["next_occurrence"])[:10],
    }
    await _guard_member_write(runtime, session, "recurrence.completed", payload)
    event = _member_command_event(
        session,
        event_type="recurrence.completed",
        payload=payload,
        request_id=body.request_id,
        fingerprint=fingerprint,
        causation_id=row["last_event_id"],
        owner_scope=row["owner_scope"],
        visibility_scope=row["visibility_scope"],
        sensitivity=cast(Sensitivity, row.get("sensitivity") or "normal"),
    )
    try:
        event_id = await runtime.events.write(event)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior action body"
        ) from exc
    await runtime.wait_for_projection("recurrences", event_id)
    return {
        "event_id": event_id,
        "duplicate": False,
        "recurrence": get_recurrence(
            runtime.projections.connection("recurrences"),
            session,
            recurrence_id=body.item_id,
        ),
        "what_now": core._what_now(runtime, session),
    }


def _calendar_belongs_to_member(row: dict[str, Any], member_id: str) -> bool:
    if row.get("member_id") == member_id or row.get("owner_party") == member_id:
        return True
    try:
        attendees = json.loads(row.get("attendees_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        return False
    return any(
        attendee == member_id
        or (isinstance(attendee, dict) and attendee.get("party_id") == member_id)
        for attendee in attendees
    )


async def _record_calendar_outcome(
    calendar_event_id: str,
    body: CalendarOutcomeRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "calendar outcomes are unavailable while viewing another member"
        )
    runtime = _runtime(request)
    fingerprint = _member_command_fingerprint("calendar_event", calendar_event_id, body)
    prior = await _prior_member_command(
        runtime,
        session,
        event_type="calendar.event_outcome_recorded",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    if prior is not None:
        return {
            "event_id": prior["event_id"],
            "duplicate": True,
            "brief": build_surface(runtime, session, "today")["brief"],
        }
    row = get_calendar_event(
        runtime.projections.connection("calendars"),
        session,
        calendar_event_id=calendar_event_id,
    )
    if row is None or not _calendar_belongs_to_member(row, session.auth_member_id):
        raise HTTPException(404, "calendar event not found")
    if row["calendar_source"] not in {"google_calendar", "apple_calendar"}:
        raise HTTPException(409, "this calendar source cannot record an outcome")
    prior_outcome = get_calendar_outcome(
        runtime.projections.connection("calendars"),
        session,
        member_id=session.auth_member_id,
        calendar_source=row["calendar_source"],
        external_uid=row["external_uid"],
    )
    if prior_outcome is not None:
        raise HTTPException(
            409, "a calendar outcome is already recorded; use a correction flow"
        )
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "calendar event changed; refresh before recording it")
    try:
        start = datetime.fromisoformat(str(row["start_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(409, "calendar event has an invalid start time") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) < start.astimezone(timezone.utc):
        raise HTTPException(
            409, "attendance cannot be recorded before the event starts"
        )
    payload = {
        "member_id": session.auth_member_id,
        "provider": row["calendar_source"],
        "external_event_id": row["external_uid"],
        "outcome": body.outcome,
        "attendance_status": body.outcome,
        "evidence": "member_confirmation",
        "confidence": 1.0,
        "recorded_at": EventEnvelope.now_utc_iso(),
        "linked_item_kind": row.get("linked_item_kind"),
        "linked_item_id": row.get("linked_item_id"),
        "completion_policy": row.get("completion_policy") or "manual",
    }
    await _guard_member_write(
        runtime, session, "calendar.event_outcome_recorded", payload
    )
    event = _member_command_event(
        session,
        event_type="calendar.event_outcome_recorded",
        payload=payload,
        request_id=body.request_id,
        fingerprint=fingerprint,
        causation_id=row["last_event_id"],
        owner_scope=row["owner_scope"],
        visibility_scope=row["visibility_scope"],
        sensitivity=cast(Sensitivity, row.get("sensitivity") or "normal"),
    )
    try:
        event_id = await runtime.events.write(event)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior action body"
        ) from exc
    for projection in ("calendars", "tasks", "commitments"):
        await runtime.wait_for_projection(projection, event_id)
    return {
        "event_id": event_id,
        "duplicate": False,
        "brief": build_surface(runtime, session, "today")["brief"],
    }


async def _record_routine_step_outcome(
    routine_id: str,
    occurrence_date: str,
    step_id: str,
    body: RoutineStepOutcomeRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "routine progress is unavailable while viewing another member"
        )
    runtime = _runtime(request)
    fingerprint = _member_command_fingerprint(
        "routine_step", f"{routine_id}:{occurrence_date}:{step_id}", body
    )
    prior = await _prior_member_command(
        runtime,
        session,
        event_type="routine.step_outcome_recorded",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    now = datetime.now(timezone.utc)
    if prior is not None:
        return {
            "event_id": prior["event_id"],
            "duplicate": True,
            "routine": get_routine_occurrence(
                runtime.projections.connection("recurrences"),
                session,
                member_party_id=session.auth_member_id,
                routine_id=routine_id,
                occurrence_date=occurrence_date,
                as_of_iso=now.isoformat(),
            ),
            "brief": build_surface(runtime, session, "today")["brief"],
        }
    row = get_routine_occurrence(
        runtime.projections.connection("recurrences"),
        session,
        member_party_id=session.auth_member_id,
        routine_id=routine_id,
        occurrence_date=occurrence_date,
        as_of_iso=now.isoformat(),
    )
    if row is None:
        raise HTTPException(404, "routine occurrence not found")
    if row["status"] in {"completed", "completed_with_skips"}:
        raise HTTPException(409, "routine occurrence is already closed")
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "routine changed; refresh before recording progress")
    current_step = row.get("current_step")
    if not isinstance(current_step, dict) or current_step.get("step_id") != step_id:
        raise HTTPException(409, "routine steps must be handled in order")
    payload = {
        "routine_id": routine_id,
        "occurrence_date": occurrence_date,
        "step_id": step_id,
        "outcome": body.outcome,
        "recorded_at": EventEnvelope.now_utc_iso(),
        "recorded_by_member_id": session.auth_member_id,
    }
    await _guard_member_write(
        runtime, session, "routine.step_outcome_recorded", payload
    )
    event = _member_command_event(
        session,
        event_type="routine.step_outcome_recorded",
        payload=payload,
        request_id=body.request_id,
        fingerprint=fingerprint,
        causation_id=row["last_event_id"],
        owner_scope=row["owner_scope"],
        visibility_scope=row["visibility_scope"],
        sensitivity=cast(Sensitivity, row.get("sensitivity") or "normal"),
    )
    try:
        event_id = await runtime.events.write(event)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior action body"
        ) from exc
    await runtime.wait_for_projection("recurrences", event_id)
    refreshed_at = datetime.now(timezone.utc).isoformat()
    return {
        "event_id": event_id,
        "duplicate": False,
        "routine": get_routine_occurrence(
            runtime.projections.connection("recurrences"),
            session,
            member_party_id=session.auth_member_id,
            routine_id=routine_id,
            occurrence_date=occurrence_date,
            as_of_iso=refreshed_at,
        ),
        "brief": build_surface(runtime, session, "today")["brief"],
    }


async def _execute_lifecycle(
    body: MemberLifecycleRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    runtime = _runtime(request)
    if body.item_kind == "recurrence":
        lock = runtime.today_command_locks.setdefault(
            f"{session.tenant_id}:recurrence:{body.item_id}", asyncio.Lock()
        )
        async with lock:
            return await _complete_recurrence(body, request, session)
    if body.item_kind == "task":
        actor_id, signed_session_key = runtime.member_chat_binding(
            session.auth_member_id
        )
        return await _task_lifecycle(
            OpenClawLifecycleRequest(
                provenance=Provenance(
                    actor_id=actor_id,
                    agent_id="administrateme-member-portal",
                    session_key=signed_session_key,
                    session_id=None,
                    channel_id="administrateme-portal",
                    account_id="member-portal",
                    message_id=body.request_id,
                    thread_id=None,
                    correlation_id=session.correlation_id or body.request_id,
                    call_kind="tool",
                    openclaw_version=cast(Literal["2026.7.2"], OPENCLAW_VERSION),
                    openclaw_commit=cast(
                        Literal["944cf08f94c334cff2b0fdf7e01187a9efa42715"],
                        OPENCLAW_COMMIT,
                    ),
                ),
                request_id=body.request_id,
                item_kind="task",
                item_id=body.item_id,
                action=body.action,
                expected_last_event_id=body.expected_last_event_id,
                until=body.until,
                reason=body.reason,
                note=body.note,
            ),
            request,
            session,
        )
    return await core.lifecycle(
        body.item_id,
        body.action,
        core.LifecycleRequest(
            request_id=body.request_id,
            expected_last_event_id=body.expected_last_event_id,
            until=body.until,
            reason=body.reason,
            note=body.note,
        ),
        request,
        session,
    )


@router.get("/", response_class=RedirectResponse)
async def dashboard_root(session: SessionDep) -> RedirectResponse:
    """Send an authenticated household member to the canonical portal URL."""
    return RedirectResponse(url="/member", status_code=307)


@router.get("/member")
@router.get("/member/{surface}")
async def member_page(
    request: Request,
    session: SessionDep,
    surface: str = "today",
) -> Response:
    if surface == "inbox":
        authorize_surface(session, "triage")
        return RedirectResponse(url="/member/triage", status_code=308)
    if surface == "knowledge":
        authorize_surface(session, "notes")
        return RedirectResponse(url="/member/notes", status_code=308)
    authorize_surface(session, surface)
    return _member_index_response()


@router.get("/member/quests/{stable_id}")
async def temporary_surface_page(
    stable_id: str, request: Request, session: SessionDep
) -> Response:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    spec = runtime.temporary_surfaces.get(stable_id)
    if spec is None or session.auth_member_id != session.view_member_id:
        raise HTTPException(404, "temporary surface not found")
    if spec.status == "active":
        _visible_spec(runtime, session, stable_id)
    elif spec.owner_member_id != session.auth_member_id:
        raise HTTPException(404, "temporary surface not found")
    return _member_index_response()


@router.get("/member-assets/{filename}")
async def member_asset(filename: str) -> FileResponse:
    if filename not in {"member.css", "member.js"}:
        raise HTTPException(404, "asset not found")
    media = "text/css" if filename.endswith(".css") else "text/javascript"
    return FileResponse(
        ASSET_ROOT / filename,
        media_type=media,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/member/v1/bootstrap")
async def bootstrap(request: Request, session: SessionDep) -> dict[str, Any]:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    return {
        "member": {
            "id": session.view_member_id,
            "role": session.view_role,
            "auth_role": session.auth_role,
            "display_name": member_label(runtime, session.view_member_id),
            "authenticated_id": session.auth_member_id,
            "authenticated_display_name": member_label(runtime, session.auth_member_id),
            "authenticated_role": session.auth_role,
            "authentication_method": "Tailscale",
            "viewing_as": session.auth_member_id != session.view_member_id,
        },
        "navigation": navigation(session),
        "mode": experience_mode(runtime, session),
        "observation_mode": runtime._observation_active,
        "chat": _chat_policy(runtime, session),
        "temporary_surfaces": [
            {
                "stable_id": spec.stable_id,
                "title": spec.title,
                "purpose": spec.purpose,
                "compatible_modes": list(spec.compatible_modes),
                "href": f"/member/quests/{spec.stable_id}",
            }
            for spec in runtime.temporary_surfaces.visible_to(session.auth_member_id)
            if session.auth_member_id == session.view_member_id
            and session.auth_role not in {"device", "ambient"}
            and (session.auth_role != "child" or "child" in spec.compatible_modes)
        ],
        "trust": {
            "operator_access": False,
            "actions_as": session.auth_member_id,
            "reads_as": session.view_member_id,
        },
    }


@router.get("/api/member/v1/surfaces/{surface}")
async def surface(
    surface: str,
    request: Request,
    session: SessionDep,
    calendar_date: date | None = Query(default=None, alias="date"),
    view: Literal["day", "week", "month", "agenda", "find", "rules"] = "week",
) -> dict[str, Any]:
    if surface == "inbox":
        return {
            **build_surface(_runtime(request), session, "triage"),
            "deprecated_alias": "inbox",
            "canonical_path": "/api/member/v1/surfaces/triage",
        }
    if surface == "knowledge":
        return {
            **build_surface(_runtime(request), session, "notes"),
            "deprecated_alias": "knowledge",
            "canonical_path": "/api/member/v1/surfaces/notes",
        }
    return build_surface(
        _runtime(request),
        session,
        surface,
        calendar_anchor=calendar_date,
        calendar_view=view,
    )


@router.post("/api/member/v1/settings/profile", status_code=202)
async def change_settings_profile(
    body: SettingsProfileRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Activate one validated data-only profile for the authenticated member."""

    _require_self_settings(session)
    runtime = _runtime(request)
    fingerprint = _settings_request_fingerprint(
        "settings.profile_changed", {"profile_id": body.profile_id}
    )
    lock = runtime.settings_command_locks.setdefault("profile-catalog", asyncio.Lock())
    async with lock:
        prior_receipt = await _prior_settings_receipt(
            runtime,
            session,
            event_type="settings.profile_changed",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior_receipt is not None:
            return {
                "accepted": True,
                "profile_id": prior_receipt["payload"]["profile_id"],
                "event_id": prior_receipt["event_id"],
                "idempotent": True,
                "deterministic": True,
                "authority_changes": False,
                "model_routing_changes": False,
            }
        try:
            requested_profile = runtime.extensions.get("profile", body.profile_id)
        except ExtensionError as exc:
            raise HTTPException(422, str(exc)) from exc
        if requested_profile.data.get("profile_format") in {"ambient", "scoreboard"}:
            raise HTTPException(422, "profile is unavailable to an adult member")
        prior = runtime.extensions.profile_for(session.auth_member_id)
        previous_id = prior.get("id") if isinstance(prior, dict) else None
        await _guard_member_write(
            runtime,
            session,
            "settings.profile_changed",
            {
                "member_id": session.auth_member_id,
                "previous_profile_id": previous_id,
                "profile_id": body.profile_id,
                "authority_changes": False,
                "model_routing_changes": False,
            },
        )
        activated = False
        if previous_id != body.profile_id:
            try:
                token = runtime.extensions.stage("profile", body.profile_id)
                tested = runtime.extensions.test_stage(token)
                if tested["authority_changes"] or tested["model_routing_changes"]:
                    raise HTTPException(409, "profile attempted to change authority")
                runtime.extensions.activate(token, member_id=session.auth_member_id)
                activated = True
            except ExtensionError as exc:
                raise HTTPException(422, str(exc)) from exc
        changed_at = EventEnvelope.now_utc_iso()
        try:
            event_id = await _write_settings_receipt(
                runtime,
                session,
                event_type="settings.profile_changed",
                request_id=body.request_id,
                request_fingerprint=fingerprint,
                owner_member_id=session.auth_member_id,
                payload={
                    "member_id": session.auth_member_id,
                    "previous_profile_id": previous_id,
                    "profile_id": body.profile_id,
                    "changed_by_member_id": session.auth_member_id,
                    "authority_changes": False,
                    "model_routing_changes": False,
                    "changed_at": changed_at,
                },
            )
        except Exception:
            if activated:
                runtime.extensions.rollback()
            raise
        return {
            "accepted": True,
            "profile_id": body.profile_id,
            "event_id": event_id,
            "deterministic": True,
            "authority_changes": False,
            "model_routing_changes": False,
        }


@router.post("/api/member/v1/settings/preferences", status_code=202)
async def change_settings_preferences(
    body: SettingsPreferencesRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Save bounded, replayable CoS presentation and cue preferences."""

    _require_self_settings(session)
    runtime = _runtime(request)
    fingerprint = _settings_request_fingerprint(
        "settings.preferences_changed",
        {"expected_revision": body.expected_revision, "values": body.values},
    )
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        prior_receipt = await _prior_settings_receipt(
            runtime,
            session,
            event_type="settings.preferences_changed",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior_receipt is not None:
            return {
                "accepted": True,
                "revision": prior_receipt["payload"]["revision"],
                "event_id": prior_receipt["event_id"],
                "idempotent": True,
                "preferences": prior_receipt["payload"]["values"],
            }
        previous_state = runtime.settings_control.read()
        await _guard_member_write(
            runtime,
            session,
            "settings.preferences_changed",
            {
                "member_id": session.auth_member_id,
                "expected_revision": body.expected_revision,
                "values": body.values,
            },
        )
        try:
            mutation = runtime.settings_control.set_preferences(
                session.auth_member_id,
                body.values,
                expected_revision=body.expected_revision,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        changed_at = EventEnvelope.now_utc_iso()
        try:
            event_id = await _write_settings_receipt(
                runtime,
                session,
                event_type="settings.preferences_changed",
                request_id=body.request_id,
                request_fingerprint=fingerprint,
                owner_member_id=session.auth_member_id,
                payload={
                    "member_id": session.auth_member_id,
                    "changed_by_member_id": session.auth_member_id,
                    "revision": mutation.revision,
                    "values": mutation.current,
                    "changed_at": changed_at,
                },
            )
        except Exception:
            runtime.settings_control.save_candidate(previous_state)
            raise
        return {
            "accepted": True,
            "revision": mutation.revision,
            "event_id": event_id,
            "preferences": mutation.current,
        }


@router.post("/api/member/v1/settings/connections/{connection_id}", status_code=202)
async def change_settings_connection(
    connection_id: str,
    body: SettingsConnectionRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Pause or resume one exact member-bound adapter instance."""

    _require_self_settings(session)
    runtime = _runtime(request)
    visible = {
        row["id"]: row for row in compose_connections(runtime, session.auth_member_id)
    }
    if connection_id not in visible:
        raise HTTPException(404, "connection not found")
    if runtime.adapters is None:
        raise HTTPException(503, "adapter supervisor is unavailable")
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    fingerprint = _settings_request_fingerprint(
        "settings.connection_lifecycle_changed",
        {
            "connection_id": connection_id,
            "expected_revision": body.expected_revision,
            "action": body.action,
        },
    )
    async with lock:
        prior_receipt = await _prior_settings_receipt(
            runtime,
            session,
            event_type="settings.connection_lifecycle_changed",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior_receipt is not None:
            return {
                "accepted": True,
                "connection_id": connection_id,
                "state": (
                    "paused"
                    if prior_receipt["payload"]["action"] == "pause"
                    else "connected"
                ),
                "revision": prior_receipt["payload"]["revision"],
                "event_id": prior_receipt["event_id"],
                "idempotent": True,
            }
        previous_state = runtime.settings_control.read()
        await _guard_member_write(
            runtime,
            session,
            "settings.connection_lifecycle_changed",
            {
                "connection_id": connection_id,
                "member_id": session.auth_member_id,
                "action": body.action,
                "expected_revision": body.expected_revision,
            },
        )
        try:
            candidate = runtime.settings_control.preview_connection(
                connection_id,
                body.action,
                expected_revision=body.expected_revision,
                actor_member_id=session.auth_member_id,
            )
            effective = runtime.settings_control.effective_adapter_config(
                runtime.settings.adapter_config, state=candidate
            )
            snapshot = await runtime.adapters.reconcile(effective)
            runtime.settings_control.save_candidate(candidate)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            try:
                await runtime.adapters.reconcile(
                    runtime.settings_control.effective_adapter_config(
                        runtime.settings.adapter_config, state=previous_state
                    )
                )
            except Exception:
                pass
            raise HTTPException(
                503, "connection change failed; the prior runtime was restored"
            ) from exc
        changed_at = EventEnvelope.now_utc_iso()
        try:
            event_id = await _write_settings_receipt(
                runtime,
                session,
                event_type="settings.connection_lifecycle_changed",
                request_id=body.request_id,
                request_fingerprint=fingerprint,
                owner_member_id=session.auth_member_id,
                payload={
                    "connection_id": connection_id,
                    "member_id": session.auth_member_id,
                    "action": body.action,
                    "revision": candidate["revision"],
                    "runtime_reconciled": True,
                    "changed_by_member_id": session.auth_member_id,
                    "changed_at": changed_at,
                },
            )
        except Exception as exc:
            try:
                await runtime.adapters.reconcile(
                    runtime.settings_control.effective_adapter_config(
                        runtime.settings.adapter_config, state=previous_state
                    )
                )
                runtime.settings_control.save_candidate(previous_state)
            except Exception as rollback_exc:
                raise HTTPException(
                    503,
                    "connection audit failed and rollback is incomplete; run Doctor",
                ) from rollback_exc
            raise HTTPException(
                503, "connection audit failed; the prior runtime was restored"
            ) from exc
        return {
            "accepted": True,
            "connection_id": connection_id,
            "state": "paused" if body.action == "pause" else "connected",
            "revision": candidate["revision"],
            "event_id": event_id,
            "runtime": snapshot,
        }


@router.post("/api/member/v1/settings/bridges", status_code=202)
async def request_settings_bridge(
    body: SettingsBridgeRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Create a scoped, resumable bridge-enrollment request without a token."""

    _require_self_settings(session)
    runtime = _runtime(request)
    role = runtime.settings.member_roles.get(body.member_id)
    if role is None or role in {"device", "ambient"}:
        raise HTTPException(422, "bridge target must be an enrolled household member")
    if body.member_id != session.auth_member_id:
        if session.auth_role != "principal" or role != "child":
            raise HTTPException(403, "an adult must enroll their own private bridge")
    if role == "child" and "obsidian" in body.capabilities:
        raise HTTPException(403, "Obsidian is unavailable on a child bridge")
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    fingerprint = _settings_request_fingerprint(
        "settings.bridge_enrollment_requested",
        {
            "expected_revision": body.expected_revision,
            "member_id": body.member_id,
            "capabilities": sorted(set(body.capabilities)),
        },
    )
    async with lock:
        prior_receipt = await _prior_settings_receipt(
            runtime,
            session,
            event_type="settings.bridge_enrollment_requested",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior_receipt is not None:
            return {
                "accepted": True,
                "member_id": body.member_id,
                "state": prior_receipt["payload"]["state"],
                "revision": prior_receipt["payload"]["revision"],
                "event_id": prior_receipt["event_id"],
                "idempotent": True,
                "next_step": (
                    "Verify this member's Tailscale identity, then export the "
                    "short-lived reviewed bridge package on the central Mac."
                ),
            }
        previous_state = runtime.settings_control.read()
        await _guard_member_write(
            runtime,
            session,
            "settings.bridge_enrollment_requested",
            {
                "member_id": body.member_id,
                "requested_by_member_id": session.auth_member_id,
                "capabilities": sorted(set(body.capabilities)),
                "expected_revision": body.expected_revision,
            },
        )
        try:
            mutation = runtime.settings_control.request_bridge(
                body.member_id,
                body.capabilities,
                expected_revision=body.expected_revision,
                requested_by_member_id=session.auth_member_id,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        try:
            event_id = await _write_settings_receipt(
                runtime,
                session,
                event_type="settings.bridge_enrollment_requested",
                request_id=body.request_id,
                request_fingerprint=fingerprint,
                owner_member_id=body.member_id,
                payload={
                    "member_id": body.member_id,
                    "requested_by_member_id": session.auth_member_id,
                    "capabilities": mutation.current["capabilities"],
                    "state": "enrollment_preparation_required",
                    "revision": mutation.revision,
                    "requested_at": mutation.current["requested_at"],
                },
            )
        except Exception:
            runtime.settings_control.save_candidate(previous_state)
            raise
        return {
            "accepted": True,
            "member_id": body.member_id,
            "state": mutation.current["state"],
            "revision": mutation.revision,
            "event_id": event_id,
            "next_step": (
                "Verify this member's Tailscale identity, then export the "
                "short-lived reviewed bridge package on the central Mac."
            ),
        }


def _require_private_notes(session: Session) -> None:
    if session.auth_role in {"ambient", "device", "child"}:
        raise HTTPException(403, "Notes is private to an authenticated adult member")
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "Notes is unavailable while viewing another member")


def _notes_access_fingerprint(
    action: str,
    *,
    knowledge_id: str,
    grant_id: str | None,
    values: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "action": action,
            "knowledge_id": knowledge_id,
            "grant_id": grant_id,
            "values": values,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _prior_notes_access_command(
    runtime: Any,
    session: Session,
    *,
    action: str,
    request_id: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    if runtime.event_log is None:
        raise HTTPException(503, "Notes authorization storage unavailable")
    key = f"notes:{action}:{session.auth_member_id}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is None:
        return None
    if prior["raw_ref"] != f"request-sha256:{fingerprint}":
        raise HTTPException(
            409, "idempotency key conflicts with prior Notes authorization body"
        )
    return prior


@router.get("/api/member/v1/notes")
async def notes_list(request: Request, session: SessionDep) -> dict[str, Any]:
    _require_private_notes(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "Notes projection unavailable")
    return {
        "member_id": session.auth_member_id,
        "items": list_visible_notes(
            runtime.projections.connection("member_knowledge"), session
        ),
    }


@router.get("/api/member/v1/notes/search")
async def notes_search(
    request: Request,
    session: SessionDep,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    source: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> dict[str, Any]:
    _require_private_notes(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "Notes projection unavailable")
    items = search_visible_notes(
        runtime.projections.connection("member_knowledge"),
        session,
        query=q,
        limit=limit,
        source_kind=source,
    )
    return {
        "member_id": session.auth_member_id,
        "query": q,
        "source": source,
        "items": items,
        "contract": "deterministic-authorized-full-text-v1",
    }


@router.get("/api/member/v1/notes/{knowledge_id}")
async def notes_detail(
    knowledge_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    _require_private_notes(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "Notes projection unavailable")
    conn = runtime.projections.connection("member_knowledge")
    note = get_visible_note(conn, session, knowledge_id=knowledge_id)
    if note is None:
        raise HTTPException(404, "note not found")
    note["grants"] = (
        grants_for_note(conn, session, knowledge_id=knowledge_id)
        if note.get("access_mode") == "owner"
        else []
    )
    return note


@router.post("/api/member/v1/notes/{knowledge_id}/grants", status_code=202)
async def grant_note_access(
    knowledge_id: str,
    body: KnowledgeGrantRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_private_notes(session)
    runtime = _runtime(request)
    if runtime.projections is None or runtime.event_log is None:
        raise HTTPException(503, "Notes authorization storage unavailable")
    permissions = set(body.permissions)
    if "search" in permissions:
        permissions.add("read")
    expires_at = body.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise HTTPException(422, "note grant expiry requires a timezone")
        expires_at = expires_at.astimezone(timezone.utc)
    fingerprint = _notes_access_fingerprint(
        "grant",
        knowledge_id=knowledge_id,
        grant_id=None,
        values={
            "grantee_member_id": body.grantee_member_id,
            "permissions": sorted(permissions),
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    prior = await _prior_notes_access_command(
        runtime,
        session,
        action="grant",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    if prior is not None:
        return {
            "accepted": True,
            "duplicate": True,
            "grant_id": prior["payload"]["grant_id"],
            "event_id": prior["event_id"],
        }
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "note grant expiry must be in the future")
    conn = runtime.projections.connection("member_knowledge")
    note = get_visible_note(conn, session, knowledge_id=knowledge_id)
    if note is None or note.get("access_mode") != "owner":
        raise HTTPException(404, "owned note not found")
    if note.get("sensitivity") == "privileged":
        raise HTTPException(403, "privileged notes cannot be shared")
    role = runtime.settings.member_roles.get(body.grantee_member_id)
    if role in {None, "device", "child"}:
        raise HTTPException(
            422, "Notes may only be shared with an enrolled adult member"
        )
    if body.grantee_member_id == session.auth_member_id:
        raise HTTPException(422, "a note is already visible to its owner")
    grant_id = (
        "note_grant_"
        + hashlib.sha256(
            f"{session.tenant_id}:{knowledge_id}:{body.grantee_member_id}".encode()
        ).hexdigest()[:24]
    )
    now = EventEnvelope.now_utc_iso()
    payload = {
        "grant_id": grant_id,
        "knowledge_id": knowledge_id,
        "owner_member_id": session.auth_member_id,
        "grantee_member_id": body.grantee_member_id,
        "permissions": sorted(permissions),
        "granted_at": now,
        "expires_at": expires_at,
    }
    await _guard_member_write(
        runtime, session, "member_knowledge.access_granted", payload
    )
    envelope = EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type="member_knowledge.access_granted",
        schema_version=1,
        occurred_at=now,
        source_adapter="member-portal",
        source_account_id=session.auth_member_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        sensitivity="normal",
        actor_identity=session.auth_member_id,
        correlation_id=session.correlation_id,
        idempotency_key=f"notes:grant:{session.auth_member_id}:{body.request_id}",
        raw_ref=f"request-sha256:{fingerprint}",
        payload=payload,
    )
    try:
        event_id = await runtime.events.write(envelope)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior Notes authorization body"
        ) from exc
    await runtime.wait_for_projection("member_knowledge", event_id)
    return {
        "accepted": True,
        "duplicate": False,
        "grant_id": grant_id,
        "event_id": event_id,
    }


@router.post(
    "/api/member/v1/notes/{knowledge_id}/grants/{grant_id}/revoke",
    status_code=202,
)
async def revoke_note_access(
    knowledge_id: str,
    grant_id: str,
    body: KnowledgeGrantRevokeRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_private_notes(session)
    runtime = _runtime(request)
    if runtime.projections is None or runtime.event_log is None:
        raise HTTPException(503, "Notes authorization storage unavailable")
    fingerprint = _notes_access_fingerprint(
        "revoke",
        knowledge_id=knowledge_id,
        grant_id=grant_id,
        values={},
    )
    prior = await _prior_notes_access_command(
        runtime,
        session,
        action="revoke",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    if prior is not None:
        return {
            "accepted": True,
            "duplicate": True,
            "grant_id": prior["payload"]["grant_id"],
            "event_id": prior["event_id"],
        }
    conn = runtime.projections.connection("member_knowledge")
    grants = grants_for_note(conn, session, knowledge_id=knowledge_id)
    grant = next((row for row in grants if row["grant_id"] == grant_id), None)
    if grant is None:
        raise HTTPException(404, "note grant not found")
    now = EventEnvelope.now_utc_iso()
    payload = {
        "grant_id": grant_id,
        "owner_member_id": session.auth_member_id,
        "revoked_by_member_id": session.auth_member_id,
        "revoked_at": now,
    }
    await _guard_member_write(
        runtime, session, "member_knowledge.access_revoked", payload
    )
    envelope = EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type="member_knowledge.access_revoked",
        schema_version=1,
        occurred_at=now,
        source_adapter="member-portal",
        source_account_id=session.auth_member_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        sensitivity="normal",
        actor_identity=session.auth_member_id,
        correlation_id=session.correlation_id,
        idempotency_key=f"notes:revoke:{session.auth_member_id}:{body.request_id}",
        raw_ref=f"request-sha256:{fingerprint}",
        payload=payload,
    )
    try:
        event_id = await runtime.events.write(envelope)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior Notes authorization body"
        ) from exc
    await runtime.wait_for_projection("member_knowledge", event_id)
    return {
        "accepted": True,
        "duplicate": False,
        "grant_id": grant_id,
        "event_id": event_id,
    }


@router.post("/api/member/v1/calendar/onboarding", status_code=202)
async def configure_calendar_policy(
    body: CalendarOnboardingRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Record principal-approved calendar semantics without model decisions."""

    require_principal(session)
    runtime = _runtime(request)
    if runtime.projections is None or runtime.event_log is None:
        raise HTTPException(503, "calendar policy storage is unavailable")
    if body.member_id not in runtime.settings.member_roles:
        raise HTTPException(422, "calendar policy member is not configured")
    if runtime.settings.member_roles[body.member_id] == "device":
        raise HTTPException(422, "a device cannot own calendar policy")
    try:
        ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            422, "calendar policy timezone is not an IANA zone"
        ) from exc
    fingerprint = hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    now = EventEnvelope.now_utc_iso()
    conn = runtime.projections.connection("calendars")
    source_rows = conn.execute(
        "SELECT * FROM calendar_sources WHERE tenant_id=?",
        (session.tenant_id,),
    ).fetchall()
    sources = {
        (
            str(row["member_id"]),
            str(row["provider"]),
            str(row["provider_account_id"]),
            str(row["calendar_external_id"]),
        ): dict(row)
        for row in source_rows
    }
    routes: set[tuple[str, str, str, str]] = set()
    envelopes: list[EventEnvelope] = []
    for index, requested in enumerate(body.rules):
        route = (
            requested.source_member_id,
            requested.provider,
            requested.provider_account_id,
            requested.calendar_external_id,
        )
        if route in routes:
            raise HTTPException(422, "calendar policy contains a duplicate source")
        routes.add(route)
        source = sources.get(route)
        if source is None:
            raise HTTPException(422, "calendar source has not been observed")
        if requested.write_target and not bool(source.get("write_events")):
            raise HTTPException(422, "write destination is not provider-writable")
        route_hash = hashlib.sha256("\x1f".join(route).encode("utf-8")).hexdigest()[:20]
        payload = CalendarRuleConfiguredV1.model_validate(
            {
                **requested.model_dump(mode="json"),
                "rule_id": f"calendar-rule-{body.member_id}-{route_hash}",
                "subject_member_id": body.member_id,
                "configured_at": now,
            }
        ).model_dump(mode="json")
        envelopes.append(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000) + index,
                tenant_id=session.tenant_id,
                type="calendar.rule.configured",
                schema_version=1,
                occurred_at=now,
                source_adapter="member-calendar",
                source_account_id=session.auth_member_id,
                owner_scope=f"private:{body.member_id}",
                visibility_scope=f"private:{body.member_id}",
                sensitivity="normal",
                correlation_id=session.correlation_id,
                causation_id=source.get("last_event_id"),
                actor_identity=session.auth_member_id,
                idempotency_key=(
                    f"calendar-onboarding:{body.member_id}:{body.request_id}:rule:{route_hash}"
                ),
                raw_ref=f"request-sha256:{fingerprint}",
                payload=payload,
            )
        )
    schedule_payload = CalendarSchedulePolicyConfiguredV1.model_validate(
        {
            "member_id": body.member_id,
            "timezone": body.timezone,
            "windows": [window.model_dump(mode="json") for window in body.windows],
            "buffer_minutes": body.buffer_minutes,
            "accepted_chores_required": body.accepted_chores_required,
            "one_confirm_write": body.one_confirm_write,
            "configured_at": now,
        }
    ).model_dump(mode="json")
    envelopes.append(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000) + len(envelopes),
            tenant_id=session.tenant_id,
            type="calendar.schedule_policy.configured",
            schema_version=1,
            occurred_at=now,
            source_adapter="member-calendar",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{body.member_id}",
            visibility_scope=f"private:{body.member_id}",
            sensitivity="normal",
            correlation_id=session.correlation_id,
            actor_identity=session.auth_member_id,
            idempotency_key=(
                f"calendar-onboarding:{body.member_id}:{body.request_id}:schedule"
            ),
            raw_ref=f"request-sha256:{fingerprint}",
            payload=schedule_payload,
        )
    )
    event_ids = await runtime.events.write_batch(envelopes)
    await runtime.wait_for_projection("calendars", event_ids[-1])
    return {
        "accepted": True,
        "member_id": body.member_id,
        "event_ids": event_ids,
        "rule_count": len(body.rules),
        "deterministic": True,
    }


@router.post("/api/member/v1/comms/{interaction_id}/commands")
async def communication_command(
    interaction_id: str,
    body: CommsCommandRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.communication_command_locks.setdefault(
        f"{session.auth_member_id}:{interaction_id}", asyncio.Lock()
    )
    async with lock:
        return await _communication_command_locked(
            interaction_id, body, request, session
        )


async def _communication_command_locked(
    interaction_id: str,
    body: CommsCommandRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    """Apply local focus state or enqueue one governed provider mutation."""

    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "communication actions are unavailable while viewing another member"
        )
    runtime = _runtime(request)
    if runtime.projections is None or runtime.events is None:
        raise HTTPException(503, "communication state is unavailable")
    interactions_conn = runtime.projections.connection("interactions")
    interaction = get_interaction(
        interactions_conn, session, interaction_id=interaction_id
    )
    if interaction is None or (
        interaction.get("member_id") != session.auth_member_id
        and interaction.get("owner_scope") != f"private:{session.auth_member_id}"
    ):
        raise HTTPException(404, "communication not found")
    if interaction["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "communication changed; refresh before acting")

    now = EventEnvelope.now_utc_iso()
    local_operations = {"snooze", "unsnooze", "quiet", "unquiet"}
    if body.operation in local_operations:
        if body.operation == "snooze":
            if body.snoozed_until is None:
                raise HTTPException(422, "snooze requires a wake time")
            try:
                wake = datetime.fromisoformat(body.snoozed_until.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(422, "snooze wake time is invalid") from exc
            if wake.tzinfo is None or wake <= datetime.now(timezone.utc):
                raise HTTPException(422, "snooze wake time must be in the future")
        payload = {
            "interaction_id": interaction_id,
            "member_id": session.auth_member_id,
            "operation": body.operation,
            "snoozed_until": body.snoozed_until if body.operation == "snooze" else None,
            "quieted": True
            if body.operation == "quiet"
            else False
            if body.operation == "unquiet"
            else None,
            "changed_at": now,
            "expected_last_event_id": body.expected_last_event_id,
        }
        decision = await runtime.guarded_write.check(
            session, "communication.state_changed", payload
        )
        if not decision.pass_:
            raise HTTPException(
                403,
                {
                    "reason": decision.reason,
                    "correlation_id": decision.correlation_id,
                },
            )
        event_id = await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=session.tenant_id,
                type="communication.state_changed",
                schema_version=1,
                occurred_at=now,
                source_adapter="member-portal",
                source_account_id=session.auth_member_id,
                owner_scope=f"private:{session.auth_member_id}",
                visibility_scope=f"private:{session.auth_member_id}",
                sensitivity=interaction.get("sensitivity") or "normal",
                correlation_id=decision.correlation_id,
                actor_identity=session.auth_member_id,
                idempotency_key=(
                    f"communication-state:{session.auth_member_id}:{body.request_id}"
                ),
                payload=payload,
            )
        )
        await runtime.wait_for_projection("interactions", event_id)
        return {
            "accepted": True,
            "local": True,
            "event_id": event_id,
            "interaction": get_interaction(
                interactions_conn, session, interaction_id=interaction_id
            ),
        }

    provider = str(interaction.get("provider") or "")
    provider_account_id = str(interaction.get("provider_account_id") or "")
    operation_capability = {
        "reply": "reply",
        "mark_read": "mark_read",
        "mark_unread": "mark_read",
        "archive": "archive",
        "restore": "archive",
    }[body.operation]
    account = next(
        (
            item
            for item in communication_accounts(runtime, session.auth_member_id)
            if item["provider"] == provider
            and item["provider_account_id"] == provider_account_id
        ),
        None,
    )
    if account is None:
        raise HTTPException(409, "the source account is not bound to this member")
    if not account["capabilities"].get(operation_capability):
        raise HTTPException(
            409,
            account.get("write_access_remedy")
            or "the provider does not support this action",
        )
    if not body.openclaw_approved:
        raise HTTPException(403, "independent OpenClaw delivery approval is required")
    if body.operation == "reply" and not (body.body or "").strip():
        raise HTTPException(422, "reply body cannot be empty")
    if body.operation not in _COMMS_EXTERNAL_OPERATIONS.get(provider, set()):
        raise HTTPException(422, "the provider does not support this action")

    participant_map = participants_for(
        interactions_conn, session, interaction_ids=[interaction_id]
    )
    fixture_recipient = next(
        (
            item["party_id"]
            for item in participant_map.get(interaction_id, [])
            if item["role"] == "from"
        ),
        None,
    )
    recipient = interaction.get("reply_to_identifier")
    if provider == "fixture":
        recipient = recipient or fixture_recipient
    try:
        content_privacy_guard = validate_outbound_communication(
            operation=body.operation,
            member_id=session.auth_member_id,
            interaction=interaction,
            account=account,
            recipient=recipient,
            body=body.body,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    command_payload = {
        "command_id": body.request_id,
        "member_id": session.auth_member_id,
        "provider": provider,
        "provider_account_id": provider_account_id,
        "operation": body.operation,
        "target_external_id": interaction.get("native_message_id"),
        "expected_provider_revision": None,
        "payload": {
            "interaction_id": interaction_id,
            "expected_last_event_id": body.expected_last_event_id,
            "recipient": recipient,
            "body": (body.body or "").strip() or None,
            "thread_id": interaction.get("thread_id"),
            "subject": interaction.get("subject"),
            "rfc_message_id": interaction.get("rfc_message_id"),
            "workspace": interaction.get("workspace") or "personal",
            "openclaw_approved": True,
            "content_privacy_guard": content_privacy_guard,
        },
        "requested_at": now,
    }
    decision = await runtime.guarded_write.check(
        session, "external_sync.write_requested", command_payload
    )
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {
                "reason": decision.reason,
                "correlation_id": decision.correlation_id,
            },
        )
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="external_sync.write_requested",
            schema_version=1,
            occurred_at=now,
            source_adapter="member-portal",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity=interaction.get("sensitivity") or "normal",
            correlation_id=decision.correlation_id,
            actor_identity=session.auth_member_id,
            idempotency_key=f"communication-command:{session.auth_member_id}:{body.request_id}",
            payload=command_payload,
        )
    )
    await runtime.wait_for_projection("interactions", event_id)
    return {
        "accepted": True,
        "local": False,
        "command_id": body.request_id,
        "event_id": event_id,
        "observation_mode": runtime._observation_active,
    }


@router.post("/api/member/v1/people/{party_id}/profile", status_code=202)
async def set_people_profile(
    party_id: str,
    body: PeopleProfileRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "People settings are unavailable while viewing another member"
        )
    runtime = _runtime(request)
    if runtime.projections is None or runtime.events is None:
        raise HTTPException(503, "People state is unavailable")
    parties = runtime.projections.connection("parties")
    person = get_party(parties, session, party_id=party_id)
    if person is None or person.get("qualification") != "qualified":
        raise HTTPException(404, "person not found")
    existing = parties.execute(
        "SELECT last_event_id FROM member_relationship_profiles "
        "WHERE tenant_id=? AND member_id=? AND party_id=?",
        (session.tenant_id, session.auth_member_id, party_id),
    ).fetchone()
    if (
        existing is not None
        and body.expected_last_event_id != existing["last_event_id"]
    ):
        raise HTTPException(409, "relationship settings changed; refresh before saving")
    if body.suppressed and not (body.suppression_reason or "").strip():
        raise HTTPException(422, "suppression requires a reason")
    now = EventEnvelope.now_utc_iso()
    profile_id = f"people_profile_{hashlib.sha256(f'{session.auth_member_id}:{party_id}'.encode()).hexdigest()[:22]}"
    payload = {
        "profile_id": profile_id,
        "member_id": session.auth_member_id,
        "party_id": party_id,
        "circle": body.circle,
        "desired_cadence_days": body.desired_cadence_days,
        "cues_enabled": body.cues_enabled,
        "favorite": body.favorite,
        "suppressed": body.suppressed,
        "suppression_reason": body.suppression_reason,
        "tags": sorted({value.strip() for value in body.tags if value.strip()}),
        "changed_at": now,
    }
    await _guard_member_write(
        runtime, session, "member_relationship.profile_set", payload
    )
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="member_relationship.profile_set",
            schema_version=1,
            occurred_at=now,
            source_adapter="member-people",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity="normal",
            correlation_id=session.correlation_id,
            causation_id=person["last_event_id"],
            actor_identity=session.auth_member_id,
            idempotency_key=f"people-profile:{session.auth_member_id}:{body.request_id}",
            payload=payload,
        )
    )
    await runtime.wait_for_projection("parties", event_id)
    return {"accepted": True, "event_id": event_id, "profile_id": profile_id}


@router.post("/api/member/v1/people/{party_id}/lifecycle", status_code=202)
async def change_people_lifecycle(
    party_id: str,
    body: PeopleLifecycleRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "People lifecycle is unavailable while viewing another member"
        )
    runtime = _runtime(request)
    if runtime.projections is None or runtime.events is None:
        raise HTTPException(503, "People state is unavailable")
    party = get_party(
        runtime.projections.connection("parties"), session, party_id=party_id
    )
    if party is None or party.get("owner_scope") != f"private:{session.auth_member_id}":
        raise HTTPException(404, "member-owned person not found")
    if party["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "person changed; refresh before acting")
    if body.action == "archive" and party.get("qualification") == "archived":
        raise HTTPException(409, "person is already archived")
    if body.action == "restore" and party.get("qualification") != "archived":
        raise HTTPException(409, "person is not archived")
    now = EventEnvelope.now_utc_iso()
    event_type = "party.archived" if body.action == "archive" else "party.restored"
    payload = (
        {
            "party_id": party_id,
            "archived_at": now,
            "archived_by_member_id": session.auth_member_id,
            "reason": body.reason,
        }
        if body.action == "archive"
        else {
            "party_id": party_id,
            "restored_at": now,
            "restored_by_member_id": session.auth_member_id,
        }
    )
    await _guard_member_write(runtime, session, event_type, payload)
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type=event_type,
            schema_version=1,
            occurred_at=now,
            source_adapter="member-people",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity=cast(Sensitivity, party.get("sensitivity") or "normal"),
            correlation_id=session.correlation_id,
            causation_id=party["last_event_id"],
            actor_identity=session.auth_member_id,
            idempotency_key=f"people-lifecycle:{session.auth_member_id}:{body.request_id}",
            payload=payload,
        )
    )
    await runtime.wait_for_projection("parties", event_id)
    return {"accepted": True, "event_id": event_id, "state": body.action}


@router.get("/api/member/v1/people/search")
async def people_search(
    request: Request, session: SessionDep, q: str = ""
) -> dict[str, Any]:
    query = q.strip()
    if len(query) < 2:
        return {
            "query": query,
            "results": [],
            "contract": "authorized-people-search-v1",
        }
    if len(query) > 200:
        raise HTTPException(422, "search is too long")
    return crm_search(_runtime(request), session, query)


@router.get("/api/member/v1/people/{party_id}")
async def person_detail(
    party_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    return crm_detail(_runtime(request), session, party_id)


@router.get("/api/member/v1/chat/status")
async def chat_status(request: Request, session: SessionDep) -> dict[str, Any]:
    return _chat_policy(_runtime(request), session)


@router.get("/api/member/v1/chat/history")
async def chat_history(
    request: Request, session: SessionDep, limit: int = 30
) -> dict[str, Any]:
    runtime = _runtime(request)
    _, session_key = _require_chat(runtime, session)
    try:
        items = await runtime.member_chat.history(
            session_key=session_key, limit=min(max(limit, 1), 50)
        )
    except MemberChatError as exc:
        raise HTTPException(
            503, _safe_error(exc, session.correlation_id or str(uuid.uuid4()))
        ) from exc
    return {"items": items, "transcript_owner": "openclaw"}


@router.post("/api/member/v1/chat/stream")
async def chat_stream(
    body: ChatRequest, request: Request, session: SessionDep
) -> StreamingResponse:
    runtime = _runtime(request)
    _, session_key = _require_chat(runtime, session)
    decision = runtime.member_chat_limiter.decide(
        f"chat:{session.auth_member_id}", 60, 12
    )
    if not decision.allowed:
        raise HTTPException(
            429, {"code": "rate_limited", "retry_after_s": decision.retry_after_s}
        )
    correlation_id = session.correlation_id or str(uuid.uuid4())

    async def generate() -> AsyncIterator[bytes]:
        lock = runtime.member_chat_locks.setdefault(
            session.auth_member_id, asyncio.Lock()
        )
        try:
            async with lock:
                async for chunk in runtime.member_chat.stream(
                    session_key=session_key,
                    message=body.message,
                    idempotency_key=f"member:{session.auth_member_id}:{body.request_id}",
                ):
                    public = chunk.public()
                    public["correlation_id"] = correlation_id
                    yield _sse(public)
                    if await request.is_disconnected():
                        await runtime.member_chat.abort(session_key=session_key)
                        return
        except asyncio.CancelledError:
            await runtime.member_chat.abort(session_key=session_key)
            raise
        except MemberChatError as exc:
            yield _sse(_safe_error(exc, correlation_id))

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/api/member/v1/chat/abort")
async def chat_abort(request: Request, session: SessionDep) -> dict[str, bool]:
    runtime = _runtime(request)
    _, session_key = _require_chat(runtime, session)
    return {"aborted": await runtime.member_chat.abort(session_key=session_key)}


@router.get("/api/member/v1/evidence/{event_id}")
async def evidence(
    event_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    async for event in runtime.event_log.read_since():
        if event["event_id"] != event_id:
            continue
        visibility = str(event.get("visibility_scope") or "shared:household")
        if visibility not in session.allowed_scopes or not allowed_read(
            session,
            str(event.get("sensitivity") or "normal"),
            str(event.get("owner_scope") or "shared:household"),
        ):
            break
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        safe_fields = {
            key: payload[key]
            for key in (
                "task_id",
                "commitment_id",
                "title",
                "description",
                "decision",
                "outcome",
                "state",
                "reason",
                "confidence",
                "approval_state",
                "delivery_result",
                "correction_of",
            )
            if key in payload
        }
        return {
            "event_id": event["event_id"],
            "type": event["type"],
            "source": {
                "adapter": event["source_adapter"],
                "account": event["source_account_id"],
            },
            "actor": event.get("actor_identity"),
            "occurred_at": event["occurred_at"],
            "ingested_at": event["recorded_at"],
            "confidence": payload.get("confidence"),
            "scope": {
                "owner": event["owner_scope"],
                "visibility": event["visibility_scope"],
                "sensitivity": event["sensitivity"],
            },
            "why": {
                "correlation_id": event.get("correlation_id"),
                "causation_id": event.get("causation_id"),
            },
            "correction": payload.get("correction_of"),
            "approval": payload.get("approval_state") or payload.get("decision"),
            "delivery": payload.get("delivery_result") or payload.get("outcome"),
            "subject": safe_fields,
        }
    raise HTTPException(404, "evidence not found")


@router.post("/api/member/v1/lifecycle")
async def member_lifecycle(
    body: MemberLifecycleRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    result = await _execute_lifecycle(body, request, session)
    brief = build_surface(_runtime(request), session, "today")["brief"]
    return {**result, "next_action": result.get("what_now"), "brief": brief}


@router.post("/api/member/v1/calendar/{calendar_event_id}/outcome")
async def record_calendar_outcome(
    calendar_event_id: str,
    body: CalendarOutcomeRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.today_command_locks.setdefault(
        f"{session.tenant_id}:calendar:{calendar_event_id}", asyncio.Lock()
    )
    async with lock:
        return await _record_calendar_outcome(calendar_event_id, body, request, session)


@router.post(
    "/api/member/v1/routines/{routine_id}/occurrences/{occurrence_date}"
    "/steps/{step_id}/outcome"
)
async def record_routine_step_outcome(
    routine_id: str,
    occurrence_date: str,
    step_id: str,
    body: RoutineStepOutcomeRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.today_command_locks.setdefault(
        f"{session.tenant_id}:routine:{routine_id}:{occurrence_date}", asyncio.Lock()
    )
    async with lock:
        return await _record_routine_step_outcome(
            routine_id,
            occurrence_date,
            step_id,
            body,
            request,
            session,
        )


@router.post("/api/member/v1/temporary-surfaces", status_code=201)
async def create_temporary_surface(
    body: CreateTemporarySurface, request: Request, session: SessionDep
) -> dict[str, Any]:
    if (
        session.auth_role in {"child", "device", "ambient"}
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(403, "adult owner authority required")
    try:
        spec = _runtime(request).temporary_surfaces.create(session.auth_member_id, body)
    except TemporarySurfaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    runtime = _runtime(request)
    event_id = await _audit_surface(runtime, session, spec, "created")
    activation_event_id = await _audit_surface(runtime, session, spec, "activated")
    return {
        "surface": spec.public(),
        "audit_event_id": event_id,
        "activation_event_id": activation_event_id,
    }


@router.get("/api/member/v1/temporary-surfaces/{stable_id}")
async def get_temporary_surface(
    stable_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    return {"surface": _visible_spec(runtime, session, stable_id).public()}


@router.get("/api/member/v1/temporary-surfaces/{stable_id}/evaluate")
async def evaluate_temporary_surface(
    stable_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    spec = _visible_spec(runtime, session, stable_id)
    missing = [
        name
        for name in spec.required_capabilities
        if runtime.capabilities.get(name) is None
        or runtime.capabilities[name].state not in {"healthy", "observation_only"}
    ]
    if missing:
        return {
            "surface": {"stable_id": stable_id, "title": spec.title},
            "state": "degraded",
            "missing_capabilities": missing,
            "results": [],
        }
    results = [
        {
            "query": query["name"],
            "value": evaluate_query(
                runtime, session, query["name"], query.get("params", {})
            ),
        }
        for query in spec.queries
    ]
    return {
        "surface": {
            "stable_id": stable_id,
            "title": spec.title,
            "purpose": spec.purpose,
        },
        "mode": experience_mode(runtime, session),
        "state": "ready",
        "results": results,
        "actions": list(spec.action_verbs),
        "evaluated_fresh": True,
    }


@router.post("/api/member/v1/temporary-surfaces/{stable_id}/promote")
async def promote_temporary_surface(
    stable_id: str, body: PromotionRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    spec = runtime.temporary_surfaces.get(stable_id)
    if (
        spec is None
        or spec.status != "active"
        or spec.owner_member_id != session.auth_member_id
    ):
        raise HTTPException(404, "temporary surface not found")
    if (
        session.auth_role != "principal"
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(403, "adult review required")
    known = set(runtime.settings.member_roles)
    audience = set(body.audience_member_ids)
    if not audience or audience - known:
        raise HTTPException(422, "audience contains an unknown member")
    if any(
        runtime.settings.member_roles[item] in {"device", "ambient"}
        for item in audience
    ):
        raise HTTPException(422, "shared-screen audience is not supported")
    children = {
        item for item in audience if runtime.settings.member_roles[item] == "child"
    }
    if children and (
        "child" not in spec.compatible_modes
        or {query["name"] for query in spec.queries} - CHILD_QUERIES
        or set(spec.action_verbs) - CHILD_ACTIONS
    ):
        raise HTTPException(422, "child audience requires a guardian-safe view")
    try:
        promoted = runtime.temporary_surfaces.promote(
            stable_id,
            actor_member_id=session.auth_member_id,
            audience_member_ids=tuple(audience),
        )
    except TemporarySurfaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    event_id = await _audit_surface(runtime, session, promoted, "promoted")
    return {"surface": promoted.public(), "audit_event_id": event_id}


@router.post("/api/member/v1/temporary-surfaces/{stable_id}/archive")
async def archive_temporary_surface(
    stable_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    try:
        spec = runtime.temporary_surfaces.transition(
            stable_id, actor_member_id=session.auth_member_id, status="archived"
        )
    except TemporarySurfaceError as exc:
        raise HTTPException(404, "temporary surface not found") from exc
    event_id = await _audit_surface(runtime, session, spec, "archived")
    return {"archived": True, "audit_event_id": event_id}


@router.post("/api/member/v1/temporary-surfaces/{stable_id}/actions")
async def temporary_surface_action(
    stable_id: str, body: TemporaryActionRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    spec = _visible_spec(runtime, session, stable_id)
    if body.verb not in spec.action_verbs:
        raise HTTPException(403, "action is not declared by this surface")
    result = await _execute_lifecycle(
        MemberLifecycleRequest(
            item_kind="task",
            item_id=body.item_id,
            action=cast(
                Literal["complete", "defer", "snooze", "block", "unblock"],
                body.verb.removeprefix("task."),
            ),
            expected_last_event_id=body.expected_last_event_id,
            request_id=body.request_id,
            until=body.until,
            reason=body.reason,
            note=body.note,
        ),
        request,
        session,
    )
    return {**result, "next_action": result.get("what_now")}


__all__ = ["router"]
