"""Encrypted per-owner operational ceiling; never a CoS delegation or model grant."""

from __future__ import annotations

import asyncio
from typing import Any

from adminme.events.schemas.finance_metadata import FinanceMetadataControlV1
from adminme.lib.session import Session

CONTROL_EVENT = "settings.finance_metadata_changed"


def control_lock(runtime: Any, session: Session) -> asyncio.Lock:
    return runtime.settings_command_locks.setdefault(
        f"finance-metadata-control:{session.tenant_id}:{session.auth_member_id}",
        asyncio.Lock(),
    )


def owner_allowed(runtime: Any, session: Session) -> bool:
    return bool(
        session.auth_role == "principal"
        and session.service_context is None
        and not session.is_view_as
        and session.tenant_id == runtime.settings.instance.tenant_id
        and runtime.settings.member_roles.get(session.auth_member_id) == "principal"
    )


async def control_state(runtime: Any, session: Session) -> dict[str, Any]:
    if runtime.event_log is None:
        raise RuntimeError("finance_metadata_control_unavailable")
    value = await runtime.event_log.read_finance_metadata_control(
        session.tenant_id, session.auth_member_id
    )
    if value is None:
        return {
            "revision": 0,
            "event_id": None,
            "owner_read_enabled": False,
            "cos_read_enabled": False,
        }
    try:
        payload = FinanceMetadataControlV1.model_validate(value["payload"])
        if (
            value["schema_version"] != 1
            or value["actor_identity"] != session.auth_member_id
            or value["visibility_scope"] != f"private:{session.auth_member_id}"
            or payload.owner_id != session.auth_member_id
        ):
            raise ValueError("metadata control authority invalid")
    except (TypeError, ValueError, KeyError):
        raise RuntimeError("finance_metadata_control_invalid") from None
    return {
        "revision": payload.revision,
        "event_id": value["event_id"],
        "owner_read_enabled": payload.owner_read_enabled,
        "cos_read_enabled": payload.cos_read_enabled,
    }
