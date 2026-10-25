"""Exact CoS-owned reminder intent and immutable governed-command attribution.

The requester owns the command receipt. CoS owns the native account and list.
Historical validation never uses today's mutable operational permission state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, ValidationError, model_validator

from adminme.adapters.cos_apple_reminders import CoSRemindersSelection, digest
from adminme.adapters.cos_reminders_write import (
    CoSReminderCreate,
    selected_source_sha256,
)
from adminme.adapters.cos_reminders_write_policy import (
    RULE_ID,
    CoSRemindersWritePolicy,
    policy_from_config,
    write_selection,
)
from adminme.events.log import EventLog
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

CONTRACT: Literal["cos-owned-reminder-create-v1"] = "cos-owned-reminder-create-v1"
SOURCE_ADAPTER = "openclaw-cos-apple-reminder"
DISPATCHER = "cos-native-reminder-dispatcher"


class CoSReminderIntent(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    contract: Literal["cos-owned-reminder-create-v1"] = CONTRACT
    resource_owner: Literal["service-principal:chief-of-staff"]
    operating_principal: Literal["service-principal:chief-of-staff"]
    selection: CoSRemindersSelection
    write_policy: CoSRemindersWritePolicy
    source_event_id: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=1000)
    notes: str | None = Field(default=None, max_length=8000)
    due_at: AwareDatetime
    authorization_expires_at: AwareDatetime
    selected_by_policy: bool

    @model_validator(mode="after")
    def exact_source_and_content(self) -> "CoSReminderIntent":
        if (
            self.write_policy.enabled is not True
            or self.selection.native_helper != self.write_policy.native_helper
            or selected_source_sha256(self.selection)
            != self.write_policy.selection_sha256
            or self.title != CoSReminderCreate.exact_title(self.title)
            or self.due_at != CoSReminderCreate.second_precision(self.due_at)
        ):
            raise ValueError(
                "CoS reminder intent differs from its exact source or content"
            )
        return self

    def action(self) -> dict[str, Any]:
        """The explicit action independently grounded and checked by both engines."""
        selected = self.selection
        return {
            "provider": "apple_reminders",
            "operation": "create_reminder",
            "resource_owner": self.resource_owner,
            "operating_principal": self.operating_principal,
            "provider_account_id": selected.provider_account_id,
            "source_id": selected.source_id,
            "list_id": selected.list_id,
            "device_identity_sha256": selected.device_identity_sha256,
            "os_uid": selected.os_uid,
            "selection_sha256": self.write_policy.selection_sha256,
            "write_policy_sha256": digest(self.write_policy.model_dump(mode="json")),
            "title": self.title,
            "notes": self.notes,
            "due_at": self.due_at.astimezone(timezone.utc).isoformat(),
            "selected_by_policy": self.selected_by_policy,
        }

    def require_current(self, config: dict[str, Any]) -> None:
        if (
            write_selection(config) != self.selection
            or policy_from_config(config) != self.write_policy
            or self.authorization_expires_at <= datetime.now(timezone.utc)
            or self.due_at <= datetime.now(timezone.utc)
        ):
            raise PermissionError(
                "CoS reminder source, request authority or due time changed"
            )

    def native_command(self, command: dict[str, Any]) -> CoSReminderCreate:
        payload = command["payload"]
        return CoSReminderCreate(
            contract=CONTRACT,
            command_id=payload["command_id"],
            requesting_member_id=payload["member_id"],
            resource_owner=self.resource_owner,
            operating_principal=self.operating_principal,
            domain_approval_event_id=payload["domain_approval_event_id"],
            openclaw_approval_event_id=payload["openclaw_approval_event_id"],
            source_event_id=self.source_event_id,
            request_fingerprint=payload["request_fingerprint"],
            selection_sha256=self.write_policy.selection_sha256,
            provider_account_id=self.selection.provider_account_id,
            title=self.title,
            notes=self.notes,
            due_at=self.due_at,
            expires_at=self.authorization_expires_at,
        )


def _private(event: dict[str, Any], tenant: str, member: str) -> bool:
    return bool(
        tenant
        and member
        and event.get("tenant_id") == tenant
        and event.get("owner_scope")
        == event.get("visibility_scope")
        == f"private:{member}"
    )


async def command_owner_matches(
    event_log: EventLog, command: dict[str, Any], *, through: str
) -> bool:
    """Validate immutable source/owner and both decisions, without reviving authority."""
    try:
        return await _command_owner_matches(event_log, command, through=through)
    except (KeyError, TypeError, ValueError, ValidationError):
        return False


async def _command_owner_matches(
    event_log: EventLog, command: dict[str, Any], *, through: str
) -> bool:
    from adminme.products.openclaw.request_authorization import evidence_matches

    payload = command.get("payload") or {}
    nested = payload.get("payload") or {}
    intent = CoSReminderIntent.model_validate(nested)
    member, tenant = (
        str(payload.get("member_id") or ""),
        str(command.get("tenant_id") or ""),
    )
    command_id = str(command.get("event_id") or "")
    domain_id = str(payload.get("domain_approval_event_id") or "")
    host_id = str(payload.get("openclaw_approval_event_id") or "")
    if (
        command.get("type") != "external_sync.write_requested"
        or command.get("schema_version") != 2
        or command.get("source_adapter") != "openclaw-plugin"
        or command.get("actor_identity") != member
        or payload.get("provider") != "apple_reminders"
        or payload.get("operation") != "create_reminder"
        or payload.get("provider_account_id") != intent.selection.provider_account_id
        or payload.get("target_external_id") is not None
        or payload.get("expected_provider_revision") is not None
        or not _private(command, tenant, member)
        or not intent.source_event_id < domain_id < host_id < command_id <= through
        or command.get("causation_id") != host_id
        or command.get("idempotency_key") != f"external-sync-approved:{domain_id}"
    ):
        return False
    source = await event_log.get(intent.source_event_id)
    domain = await event_log.get(domain_id)
    host = await event_log.get(host_id)
    if source is None or domain is None or host is None:
        return False
    if not all(_private(row, tenant, member) for row in (source, domain, host)):
        return False
    decision, approval = domain["payload"], host["payload"]
    grounded_id = str(approval.get("request_authorization_event_id") or "")
    grounded = await event_log.get(grounded_id)
    if grounded is None or not _private(grounded, tenant, member):
        return False
    p = grounded["payload"]
    mode = grounded.get("type") == "cos.reminder.mode_grounded"
    if mode:
        from adminme.products.openclaw.reminder_choice_authorization import (
            historical_choice_domain_matches,
        )

        if not await historical_choice_domain_matches(event_log, domain, grounded):
            return False
    rule = "current-human-request.cos-reminder-mode-choice.v1" if mode else RULE_ID
    action = intent.action()
    source_text = source["payload"]["text"]
    issued, consumed, expires = (
        datetime.fromisoformat(str(approval.get(key)))
        for key in ("issued_at", "consumed_at", "expires_at")
    )
    grounded_issued = datetime.fromisoformat(p["issued_at"])
    grounded_expires = datetime.fromisoformat(p["expires_at"])
    timely = bool(
        all(
            value.tzinfo
            for value in (issued, consumed, expires, grounded_issued, grounded_expires)
        )
        and grounded_issued <= issued <= consumed <= expires <= grounded_expires
        and grounded_expires == intent.authorization_expires_at
    )
    native_fingerprint = digest(
        {
            "domain_approval_event_id": domain_id,
            "request_authorization_event_id": grounded_id,
        }
    )
    exact = {
        "member_id": member,
        "provider": "apple_reminders",
        "provider_account_id": payload["provider_account_id"],
        "operation": "create_reminder",
        "target_external_id": None,
        "expected_provider_revision": None,
        "request_fingerprint": payload["request_fingerprint"],
    }
    return bool(
        (source.get("type"), source.get("schema_version"))
        in {("triage.intent.submitted", 2), ("cos.conversation.instruction", 1)}
        and (
            (
                source["type"] == "triage.intent.submitted"
                and source["payload"].get("submission_kind") == "member_forwarded"
                and source["payload"].get("intake_channel")
                in {"imessage", "apple_imessage"}
            )
            or (
                source["type"] == "cos.conversation.instruction"
                and source.get("source_adapter") == "cos-conversation"
                and source["payload"].get("channel_id")
                in {"administrateme-portal", "gmail"}
            )
        )
        and source.get("actor_identity") == source["payload"].get("member_id") == member
        and domain.get("type") == "member.provider_action.confirmed"
        and domain.get("schema_version") == 1
        and domain.get("source_adapter") == SOURCE_ADAPTER
        and domain.get("actor_identity") == domain.get("source_account_id") == member
        and domain.get("causation_id") == intent.source_event_id
        and domain.get("raw_ref") == f"request-sha256:{payload['request_fingerprint']}"
        and all(decision.get(key) == value for key, value in exact.items())
        and decision.get("action_id") == payload["command_id"]
        and decision.get("command_payload") == nested
        and grounded.get("type")
        == ("cos.reminder.mode_grounded" if mode else "cos.action.request_grounded")
        and grounded.get("schema_version") == 1
        and grounded.get("actor_identity") == COS_SERVICE_PRINCIPAL_ID
        and intent.source_event_id < grounded_id < domain_id
        and p.get("member_id") == member
        and p.get("source_event_id") == intent.source_event_id
        and p.get("action_id") == payload["command_id"]
        and p.get("request_fingerprint") == payload["request_fingerprint"]
        and p.get("source_fingerprint") == digest(source_text)
        and p.get("action_fingerprint") == digest(action)
        and p.get("action") == action
        and p.get("rule_id") == rule
        and evidence_matches(source_text, p.get("evidence"))
        and host.get("type") == "openclaw.approval.received"
        and host.get("schema_version") == 2
        and host.get("source_adapter") == "openclaw-plugin"
        and host.get("actor_identity") == COS_SERVICE_PRINCIPAL_ID
        and host.get("causation_id") == domain_id
        and approval.get("member_id") == member
        and approval.get("tool_name")
        == "administrateme_execute_pending_provider_action"
        and approval.get("action") == "create_reminder"
        and approval.get("approval_method") == "authenticated_request"
        and approval.get("single_use") is True
        and approval.get("request_fingerprint") == native_fingerprint
        and approval.get("resource_fingerprint") == native_fingerprint
        and approval.get("source_event_id") == intent.source_event_id
        and approval.get("domain_approval_event_id") == domain_id
        and approval.get("action_fingerprint") == digest(action)
        and approval.get("rule_id") == rule
        and timely
    )
