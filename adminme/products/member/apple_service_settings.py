"""Owner-only Apple write permissions, separate from immutable read enrollment."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool, model_validator

from adminme.events.envelope import EventEnvelope
from adminme.lib.session import Session
from adminme.products.auth import session_from_request


router = APIRouter()
SessionDep = Annotated[Session, Depends(session_from_request)]
EVENT = "settings.apple_service_permission_controlled"
PROVIDERS = {
    "apple_calendar": "Apple Calendar",
    "apple_reminders": "Apple Reminders",
    "apple_notes": "Apple Notes",
}


class AppleServicePermissionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=160)
    expected_revision: int = Field(ge=0)
    enabled: StrictBool
    scope_ids: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def unique_selection(self) -> AppleServicePermissionRequest:
        if len(set(self.scope_ids)) != len(self.scope_ids):
            raise ValueError("Select each destination only once")
        if self.enabled and not self.scope_ids:
            raise ValueError("Select at least one destination before enabling writes")
        if not self.enabled and self.scope_ids:
            raise ValueError("Revocation must use an empty destination selection")
        return self


def _owner(session: Session) -> None:
    from adminme.products.member.main import _require_self_settings

    _require_self_settings(session)
    if session.auth_role != "principal":
        raise HTTPException(403, "Apple write permissions require their adult owner")


def permission_items(runtime: Any, member: str) -> list[dict[str, Any]]:
    from adminme.apple_service_permissions import permission_options, service_permission

    saved = (
        runtime.settings_control.read()
        .get("apple_service_permissions", {})
        .get(member, {})
    )
    items = []
    for provider, label in PROVIDERS.items():
        previous = saved.get(provider, {})
        active = service_permission(runtime, member, provider)
        reason = None
        try:
            options = permission_options(runtime, member, provider)
        except ValueError as exc:
            options = {"scopes": [], "operations": [], "enrollment": {}}
            reason = str(exc)
        available = bool(options.get("scopes"))
        state = (
            "enabled"
            if active
            else "unavailable"
            if not available
            else "revoked"
            if previous and not previous.get("enabled")
            else "disabled"
        )
        if previous.get("enabled") and not active:
            reason = (
                reason
                or "The bridge or selected destinations changed. Review and grant permission again."
            )
        items.append(
            {
                "provider": provider,
                "label": label,
                "revision": int(previous.get("revision", 0)),
                "state": state,
                "read_access": bool(options.get("enrollment")),
                "write_access": bool(active),
                "can_revoke": bool(previous.get("enabled")),
                "reason": reason,
                "operations": options.get("operations", []),
                "scopes": options.get("scopes", []),
                "selected_scope_ids": previous.get("scope_ids", []) if active else [],
                "bridge_enrollment_id": options.get("enrollment", {}).get(
                    "enrollment_id"
                ),
                "approvals_required": True,
                "provider_effects": 0,
                "observation_mode": bool(runtime._observation_active),
                "native_permission": "checked_on_member_mac",
                "request_id": previous.get("request_id"),
            }
        )
    return items


@router.get("/api/member/v1/settings/apple-services")
async def apple_service_settings(
    request: Request,
    session: SessionDep,
    request_id: str | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    _owner(session)
    runtime = request.app.state.runtime
    if runtime.event_log is None:
        raise HTTPException(503, "Apple permission history is unavailable")
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        # Reconcile durable writes before reporting an uncertain save as absent.
        await runtime.settings_control.replay_apple_service_permission_events(
            runtime.event_log, tenant_id=session.tenant_id
        )
        result: dict[str, Any] = {
            "items": permission_items(runtime, session.auth_member_id),
            "can_mutate": True,
        }
        if request_id is not None:
            if provider not in PROVIDERS or not request_id or len(request_id) > 200:
                raise HTTPException(422, "Permission request coordinates are invalid")
            prior = await runtime.event_log.get_by_idempotency(
                session.tenant_id,
                f"member-settings:{EVENT}:{session.auth_member_id}:{request_id}",
            )
            confirmation: dict[str, Any] = {
                "state": "not_recorded",
                "request_id": request_id,
                "provider": provider,
            }
            if prior is not None:
                value = prior["payload"]
                if value.get("provider") != provider:
                    raise HTTPException(
                        409, "Request identifier belongs to a different service"
                    )
                confirmation.update(
                    {
                        "state": "recorded",
                        "expected_revision": value["revision"] - 1,
                        "enabled": value["enabled"],
                        "scope_ids": value["scope_ids"],
                        "event_id": prior["event_id"],
                    }
                )
            result["request_confirmation"] = confirmation
        return result


@router.post("/api/member/v1/settings/apple-services/{provider}", status_code=202)
async def control_apple_service(
    provider: Literal["apple_calendar", "apple_reminders", "apple_notes"],
    body: AppleServicePermissionRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    from adminme.apple_service_permissions import permission_coordinates
    from adminme.products.member.main import (
        _guard_member_write,
        _prior_settings_receipt,
        _settings_request_fingerprint,
        _write_settings_receipt,
    )

    _owner(session)
    runtime = request.app.state.runtime
    values = {
        "provider": provider,
        "expected_revision": body.expected_revision,
        "enabled": body.enabled,
        "scope_ids": sorted(body.scope_ids),
    }
    fingerprint = _settings_request_fingerprint(EVENT, values)
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        prior = await _prior_settings_receipt(
            runtime,
            session,
            event_type=EVENT,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        await runtime.settings_control.replay_apple_service_permission_events(
            runtime.event_log, tenant_id=session.tenant_id
        )
        if prior is not None:
            return {
                "accepted": True,
                "idempotent": True,
                "event_id": prior["event_id"],
                "revision": prior["payload"]["revision"],
                "provider_effects": 0,
            }
        saved = (
            runtime.settings_control.read()
            .get("apple_service_permissions", {})
            .get(session.auth_member_id, {})
            .get(provider, {})
        )
        if int(saved.get("revision", 0)) != body.expected_revision:
            raise HTTPException(
                409,
                {
                    "code": "stale_revision",
                    "message": "Apple permission changed; refresh before saving",
                },
            )
        try:
            coordinates = (
                permission_coordinates(
                    runtime, session.auth_member_id, provider, scope_ids=body.scope_ids
                )
                if body.enabled
                else saved.get("coordinates")
            )
            if not coordinates:
                raise ValueError("This service has no permission to revoke")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        digest = hashlib.sha256(
            json.dumps(
                coordinates, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        payload = {
            "member_id": session.auth_member_id,
            "provider": provider,
            "action": "enable" if body.enabled else "revoke",
            "revision": body.expected_revision + 1,
            "request_id": body.request_id,
            "scope_ids": sorted(body.scope_ids),
            "changed_by_member_id": session.auth_member_id,
            "changed_at": EventEnvelope.now_utc_iso(),
            "coordinates": coordinates,
            "coordinates_sha256": digest,
            "enabled": body.enabled,
            "provider_effects": False,
        }
        await _guard_member_write(runtime, session, EVENT, payload)
        event_id = await _write_settings_receipt(
            runtime,
            session,
            event_type=EVENT,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            owner_member_id=session.auth_member_id,
            payload=payload,
        )
        runtime.settings_control.apply_apple_service_permission_event(
            payload, event_id=event_id
        )
        return {
            "accepted": True,
            "event_id": event_id,
            "revision": payload["revision"],
            "provider_effects": 0,
            "approvals_required": True,
        }
