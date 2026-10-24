"""One governed CoS-native reminder attempt on the existing provider command spine."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
from typing import Any

from adminme.adapters.cos_reminders_helper import (
    HelperRemindersWriter,
    NativeWriteResult,
    redacted_error,
)
from adminme.adapters.cos_reminders_write import exact_due_observed
from adminme.cos_reminders_commands import (
    CONTRACT,
    DISPATCHER,
    CoSReminderIntent,
    command_owner_matches,
)
from adminme.events.envelope import EventEnvelope
from adminme.lib.observation import (
    OutboundAuditContext,
    outbound,
    reset_outbound_audit_context,
    set_outbound_audit_context,
)
from adminme.lib.session import Session
from adminme.projections.parties.queries import resolve_service_delegation
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_SERVICE_PRINCIPAL_ID,
)


def is_cos_reminder(event: dict[str, Any]) -> bool:
    return bool(
        event.get("type") == "external_sync.write_requested"
        and event.get("schema_version") == 2
        and event.get("payload", {}).get("payload", {}).get("contract") == CONTRACT
    )


def _exact_result(event: dict[str, Any], command: dict[str, Any]) -> bool:
    p, requested = event.get("payload") or {}, command["payload"]
    return bool(
        event.get("schema_version") == 2
        and event.get("source_adapter") == DISPATCHER
        and event.get("source_account_id") == requested["provider_account_id"]
        and event.get("actor_identity") == COS_SERVICE_PRINCIPAL_ID
        and event.get("causation_id") == command["event_id"]
        and p.get("command_event_id") == command["event_id"]
        and all(
            event.get(k) == command.get(k)
            for k in ("tenant_id", "owner_scope", "visibility_scope")
        )
        and all(
            p.get(k) == requested.get(k)
            for k in (
                "command_id",
                "member_id",
                "provider",
                "provider_account_id",
                "operation",
                "request_fingerprint",
                "domain_approval_event_id",
                "openclaw_approval_event_id",
            )
        )
    )


def _session(runtime: Any, command: dict[str, Any]) -> Session:
    member = command["payload"]["member_id"]
    if (
        command["tenant_id"] != runtime.settings.instance.tenant_id
        or runtime.settings.member_roles.get(member) != "principal"
        or len(
            [
                item
                for item in runtime.settings.service_principals
                if item.service_principal_id == COS_SERVICE_PRINCIPAL_ID
                and item.kind == "chief_of_staff"
            ]
        )
        != 1
    ):
        raise PermissionError("Current requester or CoS identity is unavailable")
    context = resolve_service_delegation(
        runtime.projections.connection("parties"),
        tenant_id=command["tenant_id"],
        service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
        context_scope="member_private",
        context_member_id=member,
    )
    if "prepare_external_actions" not in context.permissions:
        raise PermissionError("Current request authority is unavailable")
    return Session(
        tenant_id=command["tenant_id"],
        auth_member_id=member,
        auth_role="principal",
        view_member_id=member,
        view_role="principal",
        dm_scope="per_account_channel_peer",
        source="openclaw_plugin",
        correlation_id=command.get("correlation_id"),
        service_context=context,
    )


async def dispatch(
    runtime: Any, command: dict[str, Any], *, writer: Any | None = None
) -> None:
    """Never retry an uncertain native create, including after process restart."""
    if not is_cos_reminder(command):
        raise ValueError("Not a CoS-owned reminder command")
    p = command["payload"]
    async with runtime.settings_command_locks.setdefault(
        f"cos-reminder-dispatch:{p['command_id']}", asyncio.Lock()
    ):
        if not await command_owner_matches(
            runtime.event_log, command, through=command["event_id"]
        ):
            raise ValueError("CoS reminder command lineage is invalid")
        terminal_key = f"external-sync-result:{p['command_id']}"
        previous = await runtime.event_log.get_by_idempotency(
            command["tenant_id"], terminal_key
        )
        if previous is not None:
            if previous.get("type") not in {
                "external_sync.write_completed",
                "external_sync.write_failed",
            } or not _exact_result(previous, command):
                raise ValueError("CoS reminder identity conflicts with its terminal")
            return
        intent = CoSReminderIntent.model_validate(p["payload"])
        common = {
            key: p.get(key)
            for key in (
                "command_id",
                "member_id",
                "provider",
                "provider_account_id",
                "operation",
                "request_fingerprint",
                "domain_approval_event_id",
                "openclaw_approval_event_id",
            )
        }
        common["command_event_id"] = command["event_id"]
        provider_called = False
        audit_token = None

        async def emit(event_type: str, payload: dict[str, Any], key: str) -> str:
            return await runtime.events.write(
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=command["tenant_id"],
                    type=event_type,
                    schema_version=2,
                    occurred_at=EventEnvelope.now_utc_iso(),
                    source_adapter=DISPATCHER,
                    source_account_id=p["provider_account_id"],
                    owner_scope=command["owner_scope"],
                    visibility_scope=command["visibility_scope"],
                    sensitivity=command["sensitivity"],
                    correlation_id=command.get("correlation_id"),
                    causation_id=command["event_id"],
                    actor_identity=COS_SERVICE_PRINCIPAL_ID,
                    idempotency_key=key,
                    payload=payload,
                )
            )

        try:
            attempt_key = f"external-sync-attempt:{p['command_id']}"
            prior_attempt = await runtime.event_log.get_by_idempotency(
                command["tenant_id"], attempt_key
            )
            if prior_attempt is not None:
                provider_called = True
                if prior_attempt.get(
                    "type"
                ) != "external_sync.write_attempted" or not _exact_result(
                    prior_attempt, command
                ):
                    raise ValueError("CoS reminder identity conflicts with its attempt")
                raise RuntimeError("Previous native attempt has no terminal result")
            session = _session(runtime, command)
            from adminme.products.openclaw.request_authorization import (
                require_current_bridge_request,
            )
            from adminme.products.openclaw.main import _guard

            async def admit(*, final: bool = False) -> None:
                intent.require_current(runtime.settings.adapter_config)
                await require_current_bridge_request(runtime, command)
                if final:
                    await _guard(runtime, session, "external_sync.write_requested", p)

            await admit()
            audit_token = set_outbound_audit_context(
                OutboundAuditContext(
                    initiating_command_id=p["command_id"],
                    initiating_event_id=command["event_id"],
                    domain_approval_event_id=p["domain_approval_event_id"],
                    openclaw_approval_event_id=p["openclaw_approval_event_id"],
                    provider=p["provider"],
                    provider_account_id=p["provider_account_id"],
                    operation=p["operation"],
                    content_fingerprint=p["request_fingerprint"],
                    owner_scope=command["owner_scope"],
                    visibility_scope=command["visibility_scope"],
                    sensitivity=command["sensitivity"],
                )
            )

            async def create() -> NativeWriteResult:
                nonlocal provider_called
                await admit(final=True)
                if await runtime.observation.is_active():
                    raise PermissionError(
                        "Observation mode changed before native attempt"
                    )
                await emit(
                    "external_sync.write_attempted",
                    {
                        **common,
                        "source_id": intent.selection.source_id,
                        "list_id": intent.selection.list_id,
                        "selection_sha256": intent.write_policy.selection_sha256,
                        "resource_owner": intent.resource_owner,
                        "operating_principal": intent.operating_principal,
                        "attempted_at": EventEnvelope.now_utc_iso(),
                        "automatic_retry": False,
                    },
                    attempt_key,
                )
                # Any exception beyond this durable boundary is potentially an
                # applied native save. No error class may turn it into a safe retry.
                provider_called = True
                native = await asyncio.to_thread(
                    (writer or HelperRemindersWriter()).create,
                    intent.selection,
                    intent.native_command(command),
                )
                if (
                    not isinstance(native, NativeWriteResult)
                    or native.provider_account_id
                    != intent.selection.provider_account_id
                    or native.source_id != intent.selection.source_id
                    or native.list_id != intent.selection.list_id
                    or native.requested_due_at != intent.due_at
                    or not exact_due_observed(native.observed_due, intent.due_at)
                ):
                    raise ValueError("Native receipt does not match the exact request")
                return native

            result = await outbound(
                session,
                "reminders.create_reminder",
                intent.action(),
                create,
                manager=runtime.observation,
                event_sink=runtime.events,
                target_channel="apple_reminders",
                target_identifier=intent.selection.list_id,
            )
            native = result.result
            terminal_type = "external_sync.write_completed"
            result_payload = {
                **common,
                "provider_called": provider_called,
                "suppressed": result.suppressed,
                "external_id": None if result.suppressed else native.external_id,
                "result": {}
                if result.suppressed
                else {"applied": True, "native": native.model_dump(mode="json")},
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            terminal_type = "external_sync.write_failed"
            result_payload = {
                **common,
                "provider_called": provider_called,
                "retryable": False,
                "error_kind": "provider" if provider_called else "permission",
                "message": (
                    "Native outcome uncertain; automatic retry refused. "
                    if provider_called
                    else "Native admission refused; no provider call. "
                )
                + redacted_error(exc),
                "failed_at": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            if audit_token is not None:
                reset_outbound_audit_context(audit_token)
        await emit(terminal_type, result_payload, terminal_key)
