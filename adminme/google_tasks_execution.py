"""B03 single-use production Google Tasks creation policy.

This module is deliberately separate from ``delegated_execution``.  The G07
contract is historical fixture-v1 evidence; B03 is the only production-v2
action and every coordinate is exact and immutable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_SERVICE_PRINCIPAL_ID,
)


EXECUTION_POLICY_VERSION = "google-tasks-create-production-v2"
ACTION_CLASS = "google_tasks_create"
PROVIDER = "google_tasks"
OPERATION = "create_task"
ADAPTER_IDENTITY = "tasks-google-tasks"
GOOGLE_TASKS_WRITE_SCOPE = "https://www.googleapis.com/auth/tasks"
DELEGATION_PERMISSION = "execute_google_tasks_create_trial"
MAX_EFFECT_COUNT = 1
TASK_CONTENT_FIELDS = frozenset({"title", "notes", "due", "status", "parent", "previous"})
PROHIBITED_OPERATIONS = (
    "update_task",
    "complete_task",
    "uncomplete_task",
    "move_task",
    "delete_task",
    "create_list",
    "update_list",
    "delete_list",
    "google_calendar_write",
    "gmail_write",
)
LIFECYCLE_STATES = (
    "prepared",
    "held_for_approval",
    "approved",
    "contact_attempt_committed",
    "provider_result_received",
    "terminal_success",
    "terminal_failure_before_contact",
    "ambiguous_result_after_possible_contact",
    "later_observation_received",
    "reconciled",
    "drifted",
    "conflicted",
    "revoked",
)
RECONCILIATION_OUTCOMES = frozenset(
    {"matched", "missing", "drifted", "conflicted", "ambiguous", "failed"}
)


class B03PolicyError(ValueError):
    """The requested effect falls outside the single-use B03 envelope."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_task_content(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact allowlisted Google task body without normalizing text."""

    unknown = set(value) - TASK_CONTENT_FIELDS
    if unknown:
        raise B03PolicyError(f"Google Tasks create content has unapproved fields: {sorted(unknown)}")
    title = value.get("title")
    if not isinstance(title, str) or not title.strip():
        raise B03PolicyError("Google Tasks create requires a non-empty title")
    if len(title) > 1024:
        raise B03PolicyError("Google Tasks title exceeds the reviewed limit")
    if "notes" in value and value["notes"] is not None and not isinstance(value["notes"], str):
        raise B03PolicyError("Google Tasks notes must be text or null")
    for key in ("due", "parent", "previous"):
        if key in value and value[key] is not None and (
            not isinstance(value[key], str) or not value[key]
        ):
            raise B03PolicyError(f"Google Tasks {key} must be non-empty text or null")
    if "status" in value and value["status"] not in {None, "needsAction"}:
        raise B03PolicyError("B03 cannot create a completed task")
    return {key: value[key] for key in sorted(value) if value[key] is not None}


def task_content_fingerprint(value: Mapping[str, Any]) -> str:
    return canonical_sha256(canonical_task_content(value))


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise B03PolicyError("B03 timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ExecutionCoordinate:
    command_id: str
    initiating_event_id: str
    envelope_id: str
    envelope_revision: int
    represented_cos: str
    actual_operator: str
    context_scope: str
    on_behalf_of: str
    authority_grant_id: str
    operating_agreement_id: str
    resource_owner: str
    provider_owner: str
    provider: str
    provider_account: str
    adapter_identity: str
    destination_task_list: str
    content_fingerprint: str
    domain_approval_event_id: str
    openclaw_approval_event_id: str
    valid_from: str
    expires_at: str
    idempotency_key: str
    maximum_effect_count: int = MAX_EFFECT_COUNT

    def validate(self, *, now: datetime | None = None) -> None:
        required = {
            key: value
            for key, value in self.__dict__.items()
            if key not in {"envelope_revision", "maximum_effect_count"}
        }
        if any(not isinstance(value, str) or not value for value in required.values()):
            raise B03PolicyError("every B03 execution coordinate is required")
        if self.envelope_revision < 1:
            raise B03PolicyError("execution envelope revision must be positive")
        if self.represented_cos != COS_SERVICE_PRINCIPAL_ID:
            raise B03PolicyError("wrong represented CoS")
        if self.actual_operator != COS_AUTOMATION_OPERATOR_ID:
            raise B03PolicyError("wrong actual operator")
        if self.provider_owner != self.represented_cos:
            raise B03PolicyError("provider owner must be the represented service CoS")
        if self.provider != PROVIDER or self.adapter_identity != ADAPTER_IDENTITY:
            raise B03PolicyError("wrong provider or adapter")
        if self.context_scope not in {"member_private", "household_shared"}:
            raise B03PolicyError("wrong member or household scope")
        if self.context_scope == "member_private" and self.resource_owner != self.on_behalf_of:
            raise B03PolicyError("member-private resource owner mismatch")
        if self.context_scope == "household_shared" and self.resource_owner != "shared:household":
            raise B03PolicyError("household-shared resource owner mismatch")
        if self.maximum_effect_count != MAX_EFFECT_COUNT:
            raise B03PolicyError("B03 permits no more than one effect")
        valid_from = parse_time(self.valid_from)
        expires_at = parse_time(self.expires_at)
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if expires_at <= valid_from or current < valid_from or current >= expires_at:
            raise B03PolicyError("execution envelope is outside its approved trial window")

    def approval_material(self) -> dict[str, Any]:
        """The coordinates both independent approvals must bind."""

        return {
            "command_id": self.command_id,
            "action_class": ACTION_CLASS,
            "represented_cos": self.represented_cos,
            "context_scope": self.context_scope,
            "on_behalf_of": self.on_behalf_of,
            "provider": self.provider,
            "provider_account": self.provider_account,
            "destination_task_list": self.destination_task_list,
            "content_fingerprint": self.content_fingerprint,
            "expires_at": self.expires_at,
            "maximum_effect_count": self.maximum_effect_count,
        }

    @property
    def approval_fingerprint(self) -> str:
        return canonical_sha256(self.approval_material())

    @property
    def dispatch_coordinate(self) -> tuple[str, ...]:
        return (
            self.resource_owner,
            self.provider_owner,
            self.represented_cos,
            self.context_scope,
            self.on_behalf_of,
            self.provider,
            self.provider_account.casefold(),
            self.adapter_identity,
            self.destination_task_list,
            OPERATION,
        )


def returned_state_fingerprint(response: Mapping[str, Any]) -> str:
    """Fingerprint only provider state relevant to the approved create."""

    return canonical_sha256(
        {
            key: response.get(key)
            for key in ("title", "notes", "due", "status", "parent")
            if response.get(key) is not None
        }
    )


def public_envelope_state(row: Mapping[str, Any] | None, *, now: datetime | None = None) -> dict[str, Any]:
    """Return the content-free Settings/Doctor view for the exact envelope."""

    if row is None:
        return {
            "contract": EXECUTION_POLICY_VERSION,
            "support": "production-v2",
            "action_class": ACTION_CLASS,
            "state": "not_configured",
            "global_observation_mode_required": True,
            "remaining_operations": "read_only_or_prohibited",
            "content_exposed": False,
        }
    current = now or datetime.now(timezone.utc)
    expired = parse_time(str(row["expires_at"])) <= current.astimezone(timezone.utc)
    state = "revoked" if row.get("revoked_at") else "expired" if expired else str(row["state"])
    return {
        "contract": EXECUTION_POLICY_VERSION,
        "support": "production-v2",
        "action_class": ACTION_CLASS,
        "represented_cos": row.get("represented_cos"),
        "scope_class": row.get("context_scope"),
        "provider": PROVIDER,
        "provider_account_class": "service-owned",
        "destination_class": "pre-existing dedicated task list",
        "envelope_revision": row.get("envelope_revision"),
        "valid_from": row.get("valid_from"),
        "expires_at": row.get("expires_at"),
        "state": state,
        "used": bool(row.get("used_at")),
        "revoked": bool(row.get("revoked_at")),
        "domain_approval_state": "approved" if row.get("domain_approval_event_id") else "missing",
        "openclaw_approval_state": "approved" if row.get("openclaw_approval_event_id") else "missing",
        "provider_contact_state": row.get("provider_contact_state") or "not_contacted",
        "terminal_result_state": row.get("terminal_result_state") or "missing",
        "later_observation_state": row.get("observation_state") or "missing",
        "reconciliation_state": row.get("reconciliation_state") or "not_applicable",
        "global_observation_mode_required": True,
        "remaining_operations": "read_only_or_prohibited",
        "content_exposed": False,
    }


__all__ = [
    "ACTION_CLASS",
    "ADAPTER_IDENTITY",
    "B03PolicyError",
    "DELEGATION_PERMISSION",
    "EXECUTION_POLICY_VERSION",
    "ExecutionCoordinate",
    "GOOGLE_TASKS_WRITE_SCOPE",
    "LIFECYCLE_STATES",
    "MAX_EFFECT_COUNT",
    "OPERATION",
    "PROHIBITED_OPERATIONS",
    "PROVIDER",
    "RECONCILIATION_OUTCOMES",
    "canonical_sha256",
    "canonical_task_content",
    "public_envelope_state",
    "returned_state_fingerprint",
    "task_content_fingerprint",
]
