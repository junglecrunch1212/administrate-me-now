"""Deterministic member Settings control-plane composition and desired state.

This module is deliberately not a projection and not another application
runtime.  It combines authenticated member context with secret-free runtime
evidence and stores only bounded operational preferences.  Provider tokens,
OAuth payloads, and credential references never cross this boundary.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

from adminme.adapters.disposable_test_acquisition import (
    policy_from_adapter_config,
    restrict_adapter_config,
)
from adminme.adapters.cos_channel_acquisition import (
    policy_from_config as cos_policy_from_config,
)
from adminme.bridge_enrollment import (
    CAPABILITY_STATES,
    ENROLLMENT_STATES,
    PROGRESS_CONTRACT,
    SUPPORTED_CAPABILITIES,
    WIZARD_CONTRACT,
    BridgeEnrollmentError,
    capability_catalog,
    validate_capability_transition,
    validate_enrollment_transition,
)
from adminme.capability_catalog import compose_member_catalog
from adminme.cos_compiled_context import (
    compile_effective_cos_context,
    plain_operating_agreement,
)
from adminme.cos_response_policy import policy_from_event_log
from adminme.openclaw_topology import member_agent_id
from adminme.openclaw_contract import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.messages_reply import reply_control_for
from adminme.projections.context_fabric.queries import (
    source_policy_proposals_for_member,
    source_policy_rules_for_member,
)
from adminme.projections.parties.queries import (
    effective_operating_agreements,
    list_operating_agreements,
    list_service_delegations,
)
from adminme.service_identities import public_service_identity_inventory
from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID

from adminme.candidate_policy import (
    SOURCE_CATALOG,
    effective_policy,
    validate_policy_override,
)
from adminme.source_policy import (
    PURPOSE_OPTIONS,
    V2_CANDIDATE_KINDS,
    SourceCoordinate,
    SourcePolicyV2,
    policy_for_preset,
    parent_source_coordinate,
)


ConnectionAction = Literal["pause", "resume"]


PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    "messaging-gmail-api": {
        "provider": "gmail",
        "label": "Gmail",
        "product": "Communications",
        "runtime": "central",
        "read": ["messages", "threads", "labels"],
        "write": ["reply", "send", "mark read", "archive"],
    },
    "messaging-microsoft-graph": {
        "provider": "microsoft_graph",
        "label": "Outlook / Microsoft 365",
        "product": "Communications",
        "runtime": "central",
        "read": ["messages", "threads", "folders"],
        "write": ["reply", "send", "mark read", "archive"],
    },
    "messaging-bluebubbles": {
        "provider": "bluebubbles",
        "label": "iMessage / SMS",
        "product": "Communications",
        "runtime": "member bridge",
        "read": ["messages", "threads", "attachment metadata"],
        "write": ["reply to exact existing observed chat (ask every time)"],
    },
    "messaging-twilio-sms": {
        "provider": "twilio_sms",
        "label": "Twilio SMS",
        "product": "Communications",
        "runtime": "central",
        "read": ["messages", "delivery state"],
        "write": ["reply", "send"],
    },
    "messaging-twilio-whatsapp": {
        "provider": "twilio_whatsapp",
        "label": "WhatsApp",
        "product": "Communications",
        "runtime": "central",
        "read": ["messages", "delivery state"],
        "write": ["reply", "send"],
    },
    "calendaring-google-calendar": {
        "provider": "google_calendar",
        "label": "Google Calendar",
        "product": "Calendar",
        "runtime": "central",
        "read": ["calendars", "events", "availability"],
        "write": ["create", "update", "delete", "attendance"],
    },
    "tasks-google-tasks": {
        "provider": "google_tasks",
        "label": "Google Tasks",
        "product": "Reminders",
        "runtime": "central",
        "read": ["lists", "items", "completion"],
        "write": ["create", "update", "complete", "delete"],
    },
    "reminders-apple-eventkit": {
        "provider": "apple_reminders",
        "label": "Apple Reminders",
        "product": "Reminders",
        "runtime": "member bridge",
        "read": ["lists", "items", "alarms", "location cues"],
        "write": ["create", "update", "complete", "delete"],
    },
    "calendaring-apple-calendar": {
        "provider": "apple_calendar",
        "label": "Apple Calendar",
        "product": "Calendar",
        "runtime": "member bridge",
        "read": ["calendars", "events", "availability"],
        "write": ["create"],
    },
    "knowledge-apple-notes": {
        "provider": "apple_notes",
        "label": "Apple Notes",
        "product": "Notes",
        "runtime": "member bridge",
        "read": ["folders", "notes", "attachments"],
        "write": [],
    },
    "financial-plaid": {
        "provider": "plaid",
        "label": "Plaid",
        "product": "Finance",
        "runtime": "central",
        "read": ["accounts", "balances", "transactions"],
        "write": [],
    },
    "contacts-apple-contacts": {
        "provider": "apple_contacts",
        "label": "Apple Contacts",
        "product": "People",
        "runtime": "member bridge",
        "read": ["contacts", "groups", "containers"],
        "write": ["create", "update"],
    },
    "contacts-google-people": {
        "provider": "google_people",
        "label": "Google Contacts",
        "product": "People",
        "runtime": "central",
        "read": ["contacts", "groups"],
        "write": ["create", "update", "groups"],
    },
    "contacts-microsoft-graph-contacts": {
        "provider": "microsoft_graph",
        "label": "Microsoft Contacts",
        "product": "People",
        "runtime": "central",
        "read": ["contacts", "folders"],
        "write": ["create", "update", "delete"],
    },
    "contacts-carddav-contacts": {
        "provider": "carddav",
        "label": "CardDAV Contacts",
        "product": "People",
        "runtime": "central or member bridge",
        "read": ["contacts", "address books"],
        "write": ["provider negotiated"],
    },
}


PREFERENCE_BOUNDS: dict[str, tuple[int, int]] = {
    "information_density": (1, 5),
    "planning_horizon_days": (1, 14),
    "task_chunk_minutes": (5, 90),
    "interruption_budget": (0, 12),
    "reminder_escalation": (0, 3),
    "celebration_intensity": (0, 3),
    "digest_hour": (0, 23),
    "quiet_start_hour": (0, 23),
    "quiet_end_hour": (0, 23),
}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RuntimeError("refusing a symlinked Settings journal")
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class SettingsMutation:
    previous: dict[str, Any]
    current: dict[str, Any]
    revision: int


class SettingsControlStore:
    """Atomic, secret-free desired-state journal for member controls."""

    SCHEMA_VERSION = 6

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    async def cos_response_policy(
        self, event_log: Any, tenant_id: str
    ) -> dict[str, Any]:
        """Read authoritative encrypted receipts; never journal contact details."""

        return await policy_from_event_log(event_log, tenant_id)

    async def prepare_cos_response_policy(
        self, event_log: Any, tenant_id: str, *, expected_revision: int
    ) -> int:
        current = await self.cos_response_policy(event_log, tenant_id)
        if current["revision"] != expected_revision:
            raise ValueError("CoS reply recipients changed; refresh before saving")
        return int(current["revision"]) + 1

    @staticmethod
    def _default() -> dict[str, Any]:
        return {
            "schema_version": SettingsControlStore.SCHEMA_VERSION,
            "revision": 0,
            "connections": {},
            "member_preferences": {},
            "bridge_requests": {},
            "bridge_request_revisions": {},
            "candidate_policies": {},
            "source_policies_v2": {},
            "source_policy_v2_revisions": {},
            "source_policy_v2_migrations": {},
            "capability_requests": {},
            "capability_request_revisions": {},
            "message_reply_controls": {},
            "message_reply_control_revisions": {},
        }

    def read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._default()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Settings control journal is unreadable") from exc
        if not isinstance(value, dict) or value.get("schema_version") not in {
            1,
            2,
            3,
            4,
            5,
            6,
        }:
            raise RuntimeError("Settings control journal has an unsupported schema")
        if value["schema_version"] == 1:
            value = copy.deepcopy(value)
            value["schema_version"] = 2
            value["candidate_policies"] = {}
        if value["schema_version"] == 2:
            value = copy.deepcopy(value)
            value["schema_version"] = 3
            value["source_policies_v2"] = {}
            value["source_policy_v2_revisions"] = {}
            value["source_policy_v2_migrations"] = self._candidate_policy_migrations(
                value.get("candidate_policies") or {}
            )
        if value["schema_version"] == 3:
            value = copy.deepcopy(value)
            value["schema_version"] = 4
            value["capability_requests"] = {}
            value["capability_request_revisions"] = {}
        if value["schema_version"] == 4:
            value = copy.deepcopy(value)
            value["schema_version"] = 5
            value["bridge_request_revisions"] = {}
            migrated: dict[str, dict[str, Any]] = {}
            for member_id, record in value.get("bridge_requests", {}).items():
                if not isinstance(record, dict):
                    continue
                capabilities = sorted(
                    set(record.get("capabilities") or []) & SUPPORTED_CAPABILITIES
                )
                if not capabilities:
                    continue
                request_id = (
                    "legacy-"
                    + hashlib.sha256(str(member_id).encode("utf-8")).hexdigest()[:24]
                )
                migrated[str(member_id)] = {
                    "request_id": request_id,
                    "member_id": str(member_id),
                    "requested_by_member_id": str(
                        record.get("requested_by_member_id") or member_id
                    ),
                    "device_class": "another_mac",
                    "capabilities": capabilities,
                    "capability_states": {
                        item: {
                            "state": "requested",
                            "reason": None,
                            "last_successful_observation": None,
                        }
                        for item in capabilities
                    },
                    "state": "requested",
                    "revision": 1,
                    "requested_at": str(record.get("requested_at") or "unknown"),
                    "updated_at": str(record.get("requested_at") or "unknown"),
                    "observation_only": True,
                    "writes_authorized": False,
                    "provider_effects": False,
                }
                value["bridge_request_revisions"][str(member_id)] = 1
            value["bridge_requests"] = migrated
        if value["schema_version"] == 5:
            value = copy.deepcopy(value)
            value["schema_version"] = self.SCHEMA_VERSION
            value["message_reply_controls"] = {}
            value["message_reply_control_revisions"] = {}
        if not isinstance(value.get("revision"), int):
            raise RuntimeError("Settings control journal revision is invalid")
        for key in (
            "connections",
            "member_preferences",
            "bridge_requests",
            "bridge_request_revisions",
            "candidate_policies",
            "source_policies_v2",
            "source_policy_v2_revisions",
            "source_policy_v2_migrations",
            "capability_requests",
            "capability_request_revisions",
            "message_reply_controls",
            "message_reply_control_revisions",
        ):
            if not isinstance(value.get(key), dict):
                raise RuntimeError(f"Settings control journal {key} is invalid")
        for member_id, record in value["bridge_requests"].items():
            revision = value["bridge_request_revisions"].get(member_id)
            if (
                not isinstance(member_id, str)
                or not isinstance(record, dict)
                or record.get("member_id") != member_id
                or not isinstance(record.get("request_id"), str)
                or record.get("state") not in ENROLLMENT_STATES
                or not isinstance(revision, int)
                or revision < 1
                or record.get("revision") != revision
            ):
                raise RuntimeError("Settings bridge enrollment state is invalid")
            bridge_capabilities = record.get("capabilities")
            capability_states = record.get("capability_states")
            if (
                not isinstance(bridge_capabilities, list)
                or not bridge_capabilities
                or set(bridge_capabilities) - SUPPORTED_CAPABILITIES
                or not isinstance(capability_states, dict)
                or set(capability_states) != set(bridge_capabilities)
            ):
                raise RuntimeError("Settings bridge capability state is invalid")
            if any(
                not isinstance(item, dict) or item.get("state") not in CAPABILITY_STATES
                for item in capability_states.values()
            ):
                raise RuntimeError("Settings bridge capability state is invalid")
        if set(value["bridge_request_revisions"]) - set(value["bridge_requests"]):
            raise RuntimeError("Settings bridge enrollment revision is invalid")
        for member_rows in value["candidate_policies"].values():
            if not isinstance(member_rows, dict):
                raise RuntimeError(
                    "Settings control journal candidate policies are invalid"
                )
            for source_id, override in member_rows.items():
                if not isinstance(override, dict):
                    raise RuntimeError(
                        "Settings control journal candidate policy is invalid"
                    )
                language_enabled = override.get("language_enabled")
                candidate_kinds = override.get("candidate_kinds")
                if not isinstance(language_enabled, bool) or not isinstance(
                    candidate_kinds, list
                ):
                    raise RuntimeError(
                        "Settings control journal candidate policy is invalid"
                    )
                try:
                    validate_policy_override(
                        str(source_id),
                        mode=str(override.get("mode") or ""),
                        language_enabled=language_enabled,
                        candidate_kinds=candidate_kinds,
                    )
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "Settings control journal candidate policy is invalid"
                    ) from exc
        for owner_id, owner_rows in value["source_policies_v2"].items():
            if not isinstance(owner_rows, dict):
                raise RuntimeError("Settings source policies V2 are invalid")
            owner_revision = value["source_policy_v2_revisions"].get(owner_id)
            if not isinstance(owner_revision, int) or owner_revision < 0:
                raise RuntimeError("Settings source policy V2 revision is invalid")
            for policy_key, record in owner_rows.items():
                if (
                    not isinstance(policy_key, str)
                    or not policy_key.startswith("spv2_")
                    or not isinstance(record, dict)
                    or not isinstance(record.get("revision"), int)
                    or record["revision"] < 1
                    or not isinstance(record.get("policy"), dict)
                ):
                    raise RuntimeError("Settings source policy V2 is invalid")
                try:
                    SourcePolicyV2.model_validate(record["policy"])
                    if record.get("coordinate") is not None:
                        coordinate = SourceCoordinate.model_validate(
                            record["coordinate"]
                        )
                        if (
                            coordinate.key != policy_key
                            or coordinate.owner_principal_id != owner_id
                        ):
                            raise ValueError(
                                "cached source coordinate contradicts its owner or key"
                            )
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("Settings source policy V2 is invalid") from exc
        if set(value["source_policy_v2_revisions"]) - set(value["source_policies_v2"]):
            raise RuntimeError("Settings source policy V2 owner revision is invalid")
        for migrations in value["source_policy_v2_migrations"].values():
            if not isinstance(migrations, dict) or any(
                not isinstance(record, dict)
                or record.get("state") != "migration_needed"
                or record.get("live_behavior_preserved") is not True
                for record in migrations.values()
            ):
                raise RuntimeError("Settings source policy migration state is invalid")
        for member_id, rows in value["capability_requests"].items():
            if not isinstance(member_id, str) or not isinstance(rows, dict):
                raise RuntimeError("Settings capability requests are invalid")
            revision = value["capability_request_revisions"].get(member_id)
            if not isinstance(revision, int) or revision < 0:
                raise RuntimeError("Settings capability request revision is invalid")
            for capability_id, record in rows.items():
                if (
                    not isinstance(capability_id, str)
                    or not capability_id
                    or not isinstance(record, dict)
                    or record.get("state") not in {"requested", "cancelled"}
                    or not isinstance(record.get("revision"), int)
                    or record["revision"] < 1
                ):
                    raise RuntimeError("Settings capability request is invalid")
        if set(value["capability_request_revisions"]) - set(
            value["capability_requests"]
        ):
            raise RuntimeError("Settings capability request owner revision is invalid")
        for member_id, record in value["message_reply_controls"].items():
            revision = value["message_reply_control_revisions"].get(member_id)
            if (
                not isinstance(member_id, str)
                or not isinstance(record, dict)
                or record.get("member_id") != member_id
                or record.get("state")
                not in {"enabled_ask_every_time", "paused", "revoked"}
                or not isinstance(revision, int)
                or revision < 1
                or record.get("revision") != revision
                or record.get("ask_every_time") is not True
                or record.get("provider_effects") is not False
                or not isinstance(record.get("coordinates"), dict)
            ):
                raise RuntimeError("Settings Messages reply control is invalid")
        if set(value["message_reply_control_revisions"]) - set(
            value["message_reply_controls"]
        ):
            raise RuntimeError("Settings Messages reply revision is invalid")
        return value

    @staticmethod
    def _candidate_policy_migrations(
        candidate_policies: dict[str, Any],
    ) -> dict[str, dict[str, dict[str, Any]]]:
        migrations: dict[str, dict[str, dict[str, Any]]] = {}
        for owner_id, rows in candidate_policies.items():
            if not isinstance(rows, dict):
                continue
            for source_id, override in rows.items():
                if not isinstance(override, dict):
                    continue
                migrations.setdefault(str(owner_id), {})[str(source_id)] = {
                    "state": "migration_needed",
                    "reason": "candidate_policy_v1_lacks_exact_source_coordinate",
                    "legacy_mode": str(override.get("mode") or "review"),
                    "live_behavior_preserved": True,
                    "adapter_wide_expansion": False,
                    "write_authority_created": False,
                }
        return migrations

    def connection_lifecycle(self, connection_id: str) -> str | None:
        value = self.read()["connections"].get(connection_id)
        return value.get("state") if isinstance(value, dict) else None

    def preview_connection(
        self,
        connection_id: str,
        action: ConnectionAction,
        *,
        expected_revision: int,
        actor_member_id: str,
    ) -> dict[str, Any]:
        state = self.read()
        self._expect_revision(state, expected_revision)
        candidate = copy.deepcopy(state)
        candidate["connections"][connection_id] = {
            "state": "paused" if action == "pause" else "connected",
            "changed_by_member_id": actor_member_id,
            "changed_at": datetime.now(timezone.utc).isoformat(),
        }
        candidate["revision"] += 1
        return candidate

    def save_candidate(self, candidate: dict[str, Any]) -> None:
        if candidate.get("schema_version") != self.SCHEMA_VERSION:
            raise RuntimeError("refusing an invalid Settings journal candidate")
        _atomic_json(self.path, candidate)

    def set_preferences(
        self,
        member_id: str,
        values: dict[str, int | bool | str],
        *,
        expected_revision: int,
    ) -> SettingsMutation:
        state = self.read()
        self._expect_revision(state, expected_revision)
        normalized = validate_preferences(values)
        previous = copy.deepcopy(state["member_preferences"].get(member_id) or {})
        state["member_preferences"][member_id] = normalized
        state["revision"] += 1
        _atomic_json(self.path, state)
        return SettingsMutation(previous, normalized, state["revision"])

    def request_bridge(
        self,
        member_id: str,
        capabilities: list[str],
        *,
        expected_revision: int,
        requested_by_member_id: str,
    ) -> SettingsMutation:
        state = self.read()
        self._expect_revision(state, expected_revision)
        allowed = {
            "apple_calendar",
            "apple_reminders",
            "apple_contacts",
            "apple_notes",
            "apple_voice_memos",
            "obsidian",
        }
        selected = sorted(set(capabilities))
        if not selected or set(selected) - allowed:
            raise ValueError("bridge capabilities are invalid")
        previous = copy.deepcopy(state["bridge_requests"].get(member_id) or {})
        requested_at = datetime.now(timezone.utc).isoformat()
        bridge_revision = self.bridge_request_revision(member_id, state=state) + 1
        request_id = (
            "legacy-"
            + hashlib.sha256(
                f"{member_id}:{requested_at}:{bridge_revision}".encode("utf-8")
            ).hexdigest()[:24]
        )
        current = {
            "request_id": request_id,
            "member_id": member_id,
            "requested_by_member_id": requested_by_member_id,
            "device_class": "another_mac",
            "capabilities": selected,
            "capability_states": {
                item: {
                    "state": "requested",
                    "reason": None,
                    "last_successful_observation": None,
                }
                for item in selected
            },
            "state": "requested",
            "revision": bridge_revision,
            "requested_at": requested_at,
            "updated_at": requested_at,
            "observation_only": True,
            "writes_authorized": False,
            "provider_effects": False,
        }
        state["bridge_requests"][member_id] = current
        state["bridge_request_revisions"][member_id] = bridge_revision
        state["revision"] += 1
        _atomic_json(self.path, state)
        return SettingsMutation(previous, current, state["revision"])

    def bridge_request_revision(
        self, member_id: str, *, state: dict[str, Any] | None = None
    ) -> int:
        current = state or self.read()
        value = current["bridge_request_revisions"].get(member_id, 0)
        return int(value) if isinstance(value, int) and value >= 0 else 0

    def prepare_bridge_start(self, member_id: str, *, expected_revision: int) -> int:
        state = self.read()
        self._expect_revision(state, expected_revision)
        existing = state["bridge_requests"].get(member_id)
        if isinstance(existing, dict) and existing.get("state") not in {
            "revoked",
            "expired",
            "failed",
        }:
            raise ValueError("member already has an active bridge enrollment wizard")
        return self.bridge_request_revision(member_id, state=state) + 1

    def prepare_bridge_progress(self, member_id: str, *, request_id: str) -> int:
        state = self.read()
        record = state["bridge_requests"].get(member_id)
        if not isinstance(record, dict) or record.get("request_id") != request_id:
            raise ValueError("bridge enrollment request is missing")
        if record.get("state") in {"revoked", "expired"}:
            raise ValueError("bridge enrollment request is terminal")
        return self.bridge_request_revision(member_id, state=state) + 1

    @staticmethod
    def _apply_bridge_enrollment_payload(
        state: dict[str, Any], payload: dict[str, Any], *, increment_global: bool
    ) -> SettingsMutation:
        member_id = str(payload.get("member_id") or "")
        request_id = str(payload.get("request_id") or "")
        revision = payload.get("revision")
        requested_state = str(payload.get("state") or "")
        if not member_id or len(request_id) < 8:
            raise ValueError("bridge enrollment requires member and request")
        if requested_state not in ENROLLMENT_STATES:
            raise ValueError("bridge enrollment state is invalid")
        current_revision = int(state["bridge_request_revisions"].get(member_id, 0))
        if not isinstance(revision, int) or revision != current_revision + 1:
            raise ValueError(
                "bridge enrollment revision is duplicate, missing, or out of order"
            )
        rows = state["bridge_requests"]
        existing = rows.get(member_id)
        previous = copy.deepcopy(existing or {})
        if existing is None:
            if requested_state != "requested":
                raise ValueError("bridge enrollment must begin in requested")
            capabilities = sorted(set(payload.get("capabilities") or []))
            if not capabilities or set(capabilities) - SUPPORTED_CAPABILITIES:
                raise ValueError("bridge enrollment capabilities are invalid")
            current = {
                "request_id": request_id,
                "member_id": member_id,
                "requested_by_member_id": str(
                    payload.get("requested_by_member_id") or ""
                ),
                "device_class": str(payload.get("device_class") or ""),
                "capabilities": capabilities,
                "capability_states": {
                    item: {
                        "state": "requested",
                        "reason": None,
                        "last_successful_observation": None,
                    }
                    for item in capabilities
                },
                "state": requested_state,
                "revision": revision,
                "requested_at": str(payload.get("requested_at") or ""),
                "updated_at": str(payload.get("requested_at") or ""),
                "observation_only": True,
                "writes_authorized": False,
                "provider_effects": False,
            }
        else:
            if existing.get("request_id") != request_id:
                if existing.get("state") not in {"revoked", "expired", "failed"}:
                    raise ValueError(
                        "bridge enrollment request conflicts with active state"
                    )
                if requested_state != "requested":
                    raise ValueError(
                        "replacement bridge enrollment must begin in requested"
                    )
                capabilities = sorted(set(payload.get("capabilities") or []))
                if not capabilities or set(capabilities) - SUPPORTED_CAPABILITIES:
                    raise ValueError("bridge enrollment capabilities are invalid")
                current = {
                    "request_id": request_id,
                    "member_id": member_id,
                    "requested_by_member_id": str(
                        payload.get("requested_by_member_id") or ""
                    ),
                    "device_class": str(payload.get("device_class") or ""),
                    "capabilities": capabilities,
                    "capability_states": {
                        item: {
                            "state": "requested",
                            "reason": None,
                            "last_successful_observation": None,
                        }
                        for item in capabilities
                    },
                    "state": "requested",
                    "revision": revision,
                    "requested_at": str(payload.get("requested_at") or ""),
                    "updated_at": str(payload.get("requested_at") or ""),
                    "observation_only": True,
                    "writes_authorized": False,
                    "provider_effects": False,
                }
            else:
                current = copy.deepcopy(existing)
                previous_state = str(payload.get("previous_state") or "")
                if previous_state != existing.get("state"):
                    raise ValueError("bridge enrollment previous state does not match")
                capability_id = payload.get("capability_id")
                capability_state = payload.get("capability_state")
                if (capability_id is None) != (capability_state is None):
                    raise ValueError("bridge capability progress is incomplete")
                if previous_state != requested_state or capability_id is None:
                    try:
                        validate_enrollment_transition(previous_state, requested_state)
                    except BridgeEnrollmentError as exc:
                        raise ValueError(str(exc)) from exc
                if capability_id is not None:
                    capability_rows = cast(dict[str, Any], current["capability_states"])
                    prior_capability = capability_rows.get(capability_id)
                    if not isinstance(prior_capability, dict):
                        raise ValueError("bridge capability is not selected")
                    try:
                        validate_capability_transition(
                            str(prior_capability.get("state")), str(capability_state)
                        )
                    except BridgeEnrollmentError as exc:
                        raise ValueError(str(exc)) from exc
                    capability_rows[str(capability_id)] = {
                        "state": str(capability_state),
                        "reason": payload.get("reason"),
                        "last_successful_observation": payload.get(
                            "last_successful_observation"
                        ),
                        "updated_at": str(
                            payload.get("progress_at")
                            or payload.get("changed_at")
                            or ""
                        ),
                    }
                action = payload.get("action")
                controlled_capabilities = cast(
                    dict[str, dict[str, Any]], current["capability_states"]
                )
                if action == "pause":
                    current["paused_from_state"] = previous_state
                    for capability_row in controlled_capabilities.values():
                        prior_capability_state = str(capability_row.get("state") or "")
                        if prior_capability_state in {"revoked", "unsupported"}:
                            continue
                        validate_capability_transition(prior_capability_state, "paused")
                        capability_row["paused_from_state"] = prior_capability_state
                        capability_row["state"] = "paused"
                elif action == "resume":
                    for capability_row in controlled_capabilities.values():
                        if capability_row.get("state") != "paused":
                            continue
                        resume_state = str(
                            capability_row.pop(
                                "paused_from_state", "permission_required"
                            )
                        )
                        validate_capability_transition("paused", resume_state)
                        capability_row["state"] = resume_state
                    current.pop("paused_from_state", None)
                elif action == "revoke":
                    for capability_row in controlled_capabilities.values():
                        if capability_row.get("state") != "unsupported":
                            capability_row["state"] = "revoked"
                            capability_row.pop("paused_from_state", None)
                    current.pop("paused_from_state", None)
                current.update(
                    {
                        "state": requested_state,
                        "revision": revision,
                        "updated_at": str(
                            payload.get("progress_at")
                            or payload.get("changed_at")
                            or ""
                        ),
                    }
                )
                # Lifecycle diagnostics belong to the request, separately from
                # per-capability reasons. Clear them when that lifecycle moves
                # on so a successful retry never keeps an obsolete failure.
                if capability_id is None or previous_state != requested_state:
                    current["reason"] = payload.get("reason")
                for key in (
                    "enrollment_id",
                    "expires_at",
                    "release_commit",
                    "package_sha256",
                    "capability_set_sha256",
                    "device_identity_sha256",
                ):
                    value = payload.get(key)
                    if value is not None:
                        current[key] = value
        current["content_exposed"] = False
        current["writes_authorized"] = False
        current["provider_effects"] = False
        rows[member_id] = current
        state["bridge_request_revisions"][member_id] = revision
        if increment_global:
            state["revision"] += 1
        return SettingsMutation(previous, copy.deepcopy(current), revision)

    def apply_bridge_enrollment_event(
        self, payload: dict[str, Any]
    ) -> SettingsMutation:
        state = self.read()
        mutation = self._apply_bridge_enrollment_payload(
            state, payload, increment_global=payload.get("state") == "requested"
        )
        _atomic_json(self.path, state)
        return mutation

    async def replay_bridge_enrollment_events(self, event_log: Any) -> int:
        """Rebuild wizard state from typed encrypted events after restart/restore."""

        state = self.read()
        state["bridge_requests"] = {}
        state["bridge_request_revisions"] = {}
        count = 0
        events = []
        async for event in event_log.read_since(
            types=[
                "settings.bridge_enrollment_requested",
                "settings.bridge_enrollment_started",
                "bridge.enrollment_progressed",
                "settings.bridge_enrollment_controlled",
            ]
        ):
            events.append(event)
        members_with_wizard = {
            str(event.get("payload", {}).get("member_id") or "")
            for event in events
            if event.get("type") == "settings.bridge_enrollment_started"
            and isinstance(event.get("payload"), dict)
        }
        for event in events:
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise RuntimeError("bridge enrollment event payload is invalid")
            if event.get("type") == "settings.bridge_enrollment_requested":
                member_id = str(payload.get("member_id") or "")
                if member_id in members_with_wizard:
                    continue
                payload = {
                    **payload,
                    "request_id": "legacy-"
                    + hashlib.sha256(member_id.encode("utf-8")).hexdigest()[:24],
                    "device_class": "another_mac",
                    "state": "requested",
                    "revision": self.bridge_request_revision(member_id, state=state)
                    + 1,
                }
            try:
                self._apply_bridge_enrollment_payload(
                    state, payload, increment_global=False
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError("bridge enrollment replay failed closed") from exc
            count += 1
        _atomic_json(self.path, state)
        return count

    def bridge_request_for(
        self, member_id: str, *, state: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        current = state or self.read()
        value = current["bridge_requests"].get(member_id)
        return copy.deepcopy(value) if isinstance(value, dict) else None

    def preferences_for(self, member_id: str) -> dict[str, Any]:
        value = self.read()["member_preferences"].get(member_id)
        return copy.deepcopy(value) if isinstance(value, dict) else {}

    def set_candidate_policy(
        self,
        member_id: str,
        source_id: str,
        *,
        mode: str,
        language_enabled: bool,
        candidate_kinds: list[str],
        expected_revision: int,
    ) -> SettingsMutation:
        state = self.read()
        self._expect_revision(state, expected_revision)
        normalized = validate_policy_override(
            source_id,
            mode=mode,
            language_enabled=language_enabled,
            candidate_kinds=candidate_kinds,
        )
        member_rows = state["candidate_policies"].setdefault(member_id, {})
        previous = copy.deepcopy(member_rows.get(source_id) or {})
        current = {
            **normalized,
            "changed_at": datetime.now(timezone.utc).isoformat(),
        }
        member_rows[source_id] = current
        state["source_policy_v2_migrations"].setdefault(member_id, {})[source_id] = {
            "state": "migration_needed",
            "reason": "candidate_policy_v1_lacks_exact_source_coordinate",
            "legacy_mode": normalized["mode"],
            "live_behavior_preserved": True,
            "adapter_wide_expansion": False,
            "write_authority_created": False,
        }
        state["revision"] += 1
        _atomic_json(self.path, state)
        return SettingsMutation(previous, current, state["revision"])

    def effective_candidate_policy(
        self,
        member_id: str,
        source_id: str,
        *,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = state or self.read()
        rows = current["candidate_policies"].get(member_id)
        override = rows.get(source_id) if isinstance(rows, dict) else None
        return effective_policy(
            source_id, override if isinstance(override, dict) else None
        )

    def source_policy_v2_revision(
        self, owner_principal_id: str, *, state: dict[str, Any] | None = None
    ) -> int:
        current = state or self.read()
        value = current["source_policy_v2_revisions"].get(owner_principal_id, 0)
        return int(value) if isinstance(value, int) and value >= 0 else 0

    def prepare_source_policy_v2_change(
        self,
        owner_principal_id: str,
        coordinate: SourceCoordinate,
        *,
        expected_revision: int,
    ) -> int:
        if coordinate.owner_principal_id != owner_principal_id:
            raise ValueError("source policy owner does not match the source coordinate")
        state = self.read()
        current = self.source_policy_v2_revision(owner_principal_id, state=state)
        if current != expected_revision:
            raise ValueError("Source policy settings changed; refresh before saving")
        return current + 1

    @staticmethod
    def _apply_source_policy_v2_payload(
        state: dict[str, Any], payload: dict[str, Any]
    ) -> SettingsMutation:
        coordinate = SourceCoordinate.model_validate(payload.get("coordinate"))
        owner_id = str(payload.get("owner_principal_id") or "")
        if coordinate.owner_principal_id != owner_id:
            raise ValueError("source policy owner does not match exact coordinate")
        policy_key = str(payload.get("policy_key") or "")
        if coordinate.key != policy_key:
            raise ValueError("source policy key does not match exact coordinate")
        revision = payload.get("revision")
        if not isinstance(revision, int) or revision < 1:
            raise ValueError("source policy revision is invalid")
        current_revision = int(state["source_policy_v2_revisions"].get(owner_id, 0))
        owner_rows = state["source_policies_v2"].setdefault(owner_id, {})
        previous = copy.deepcopy(owner_rows.get(policy_key) or {})
        if revision <= current_revision:
            raise ValueError(
                "source policy event revision is duplicate or moved backwards"
            )
        if revision != current_revision + 1:
            raise ValueError("source policy event revision has a gap")
        action = payload.get("action")
        if action == "reset":
            owner_rows.pop(policy_key, None)
            current: dict[str, Any] = {}
        elif action == "set":
            policy = SourcePolicyV2.model_validate(payload.get("policy"))
            current = {
                "coordinate": coordinate.model_dump(mode="json"),
                "policy": policy.model_dump(mode="json"),
                "revision": revision,
                "changed_at": str(payload.get("changed_at") or ""),
                "changed_by_principal_id": str(
                    payload.get("changed_by_principal_id") or ""
                ),
            }
            owner_rows[policy_key] = current
        else:
            raise ValueError("source policy event action is invalid")
        state["source_policy_v2_revisions"][owner_id] = revision
        return SettingsMutation(previous, copy.deepcopy(current), revision)

    def apply_source_policy_v2_event(self, payload: dict[str, Any]) -> SettingsMutation:
        state = self.read()
        mutation = self._apply_source_policy_v2_payload(state, payload)
        _atomic_json(self.path, state)
        return mutation

    async def replay_source_policy_v2_events(self, event_log: Any) -> int:
        """Rebuild the V2 cache only from encrypted append-only receipts."""

        state = self.read()
        state["source_policies_v2"] = {}
        state["source_policy_v2_revisions"] = {}
        count = 0
        async for event in event_log.read_since(
            types=["settings.source_policy_v2_changed"]
        ):
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise RuntimeError("source policy event payload is invalid")
            try:
                self._apply_source_policy_v2_payload(state, payload)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("source policy V2 replay failed closed") from exc
            count += 1
        _atomic_json(self.path, state)
        return count

    def capability_request_revision(
        self, member_id: str, *, state: dict[str, Any] | None = None
    ) -> int:
        current = state or self.read()
        value = current["capability_request_revisions"].get(member_id, 0)
        return int(value) if isinstance(value, int) and value >= 0 else 0

    def prepare_capability_request_change(
        self, member_id: str, *, expected_revision: int
    ) -> int:
        current = self.capability_request_revision(member_id)
        if current != expected_revision:
            raise ValueError("Capability requests changed; refresh before saving")
        return current + 1

    @staticmethod
    def _apply_capability_request_payload(
        state: dict[str, Any], payload: dict[str, Any]
    ) -> SettingsMutation:
        member_id = str(payload.get("member_id") or "")
        capability_id = str(payload.get("capability_id") or "")
        revision = payload.get("revision")
        request_state = payload.get("state")
        if not member_id or not capability_id:
            raise ValueError("capability request requires member and capability")
        if not isinstance(revision, int) or revision < 1:
            raise ValueError("capability request revision is invalid")
        if request_state not in {"requested", "cancelled"}:
            raise ValueError("capability request state is invalid")
        current_revision = int(state["capability_request_revisions"].get(member_id, 0))
        if revision <= current_revision:
            raise ValueError(
                "capability request revision is duplicate or moved backwards"
            )
        if revision != current_revision + 1:
            raise ValueError("capability request revision has a gap")
        rows = state["capability_requests"].setdefault(member_id, {})
        previous = copy.deepcopy(rows.get(capability_id) or {})
        current = {
            "member_id": member_id,
            "capability_id": capability_id,
            "catalog_version": str(payload.get("catalog_version") or ""),
            "desired_outcome": str(payload.get("desired_outcome") or ""),
            "requested_scope": str(payload.get("requested_scope") or ""),
            "provider_option": payload.get("provider_option"),
            "state": request_state,
            "revision": revision,
            "changed_by_member_id": str(payload.get("changed_by_member_id") or ""),
            "changed_at": str(payload.get("changed_at") or ""),
            "installs_software": False,
            "pairs_bridge": False,
            "enrolls_account": False,
            "changes_openclaw_configuration": False,
            "accesses_credentials": False,
            "grants_authority": False,
            "provider_effects": False,
        }
        rows[capability_id] = current
        state["capability_request_revisions"][member_id] = revision
        return SettingsMutation(previous, copy.deepcopy(current), revision)

    def apply_capability_request_event(
        self, payload: dict[str, Any]
    ) -> SettingsMutation:
        state = self.read()
        mutation = self._apply_capability_request_payload(state, payload)
        _atomic_json(self.path, state)
        return mutation

    async def replay_capability_request_events(self, event_log: Any) -> int:
        """Rebuild inert capability requests from encrypted audit events."""

        state = self.read()
        state["capability_requests"] = {}
        state["capability_request_revisions"] = {}
        count = 0
        async for event in event_log.read_since(
            types=[
                "settings.capability_requested",
                "settings.capability_request_cancelled",
            ]
        ):
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise RuntimeError("capability request event payload is invalid")
            try:
                self._apply_capability_request_payload(state, payload)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("capability request replay failed closed") from exc
            count += 1
        _atomic_json(self.path, state)
        return count

    def capability_requests_for(
        self, member_id: str, *, state: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        current = state or self.read()
        rows = current["capability_requests"].get(member_id) or {}
        return [
            copy.deepcopy(record)
            for _, record in sorted(rows.items())
            if isinstance(record, dict)
        ]

    def message_reply_control_revision(
        self, member_id: str, *, state: dict[str, Any] | None = None
    ) -> int:
        current = state or self.read()
        value = current["message_reply_control_revisions"].get(member_id, 0)
        return int(value) if isinstance(value, int) and value >= 0 else 0

    def prepare_message_reply_control(
        self, member_id: str, *, expected_revision: int, action: str
    ) -> int:
        current_state = self.read()
        current_revision = self.message_reply_control_revision(
            member_id, state=current_state
        )
        if current_revision != expected_revision:
            raise ValueError("Messages reply setting changed; refresh before saving")
        existing = current_state["message_reply_controls"].get(member_id)
        existing_state = existing.get("state") if isinstance(existing, dict) else None
        if action == "enable":
            if existing_state == "revoked":
                raise ValueError("Revoked Messages reply authority cannot be resumed")
        elif action == "pause":
            if existing_state != "enabled_ask_every_time":
                raise ValueError("Messages replies are not enabled")
        elif action == "revoke":
            if existing_state == "revoked":
                raise ValueError("Messages replies are already revoked")
        else:
            raise ValueError("Messages reply control action is invalid")
        return current_revision + 1

    @staticmethod
    def _apply_message_reply_control_payload(
        state: dict[str, Any], payload: dict[str, Any], *, increment_global: bool
    ) -> SettingsMutation:
        member_id = str(payload.get("member_id") or "")
        revision = payload.get("revision")
        requested_state = payload.get("state")
        if not member_id or requested_state not in {
            "enabled_ask_every_time",
            "paused",
            "revoked",
        }:
            raise ValueError("Messages reply control payload is invalid")
        if not isinstance(revision, int) or revision < 1:
            raise ValueError("Messages reply control revision is invalid")
        current_revision = int(
            state["message_reply_control_revisions"].get(member_id, 0)
        )
        if revision != current_revision + 1:
            raise ValueError("Messages reply control revision is not consecutive")
        coordinates = payload.get("coordinates")
        if (
            not isinstance(coordinates, dict)
            or coordinates.get("member_id") != member_id
            or payload.get("changed_by_member_id") != member_id
            or payload.get("coordinates_sha256")
            != hashlib.sha256(
                json.dumps(
                    coordinates,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        ):
            raise ValueError("Messages reply coordinates do not bind the exact member")
        rows = state["message_reply_controls"]
        previous = copy.deepcopy(rows.get(member_id) or {})
        current = {
            "member_id": member_id,
            "state": requested_state,
            "revision": revision,
            "coordinates": copy.deepcopy(coordinates),
            "coordinates_sha256": str(payload.get("coordinates_sha256") or ""),
            "ask_every_time": True,
            "changed_by_member_id": str(payload.get("changed_by_member_id") or ""),
            "changed_at": str(payload.get("changed_at") or ""),
            "provider_effects": False,
        }
        rows[member_id] = current
        state["message_reply_control_revisions"][member_id] = revision
        if increment_global:
            state["revision"] += 1
        return SettingsMutation(previous, copy.deepcopy(current), revision)

    def apply_message_reply_control_event(
        self, payload: dict[str, Any]
    ) -> SettingsMutation:
        state = self.read()
        mutation = self._apply_message_reply_control_payload(
            state, payload, increment_global=True
        )
        _atomic_json(self.path, state)
        return mutation

    async def replay_message_reply_control_events(self, event_log: Any) -> int:
        """Rebuild reply authority from the encrypted append-only receipts."""

        state = self.read()
        state["message_reply_controls"] = {}
        state["message_reply_control_revisions"] = {}
        count = 0
        async for event in event_log.read_since(
            types=["settings.apple_messages_reply_controlled"]
        ):
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise RuntimeError("Messages reply control event is invalid")
            try:
                self._apply_message_reply_control_payload(
                    state, payload, increment_global=False
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "Messages reply control replay failed closed"
                ) from exc
            count += 1
        _atomic_json(self.path, state)
        return count

    def effective_source_policy_v2(
        self,
        owner_principal_id: str,
        coordinate: SourceCoordinate,
        *,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if coordinate.owner_principal_id != owner_principal_id:
            raise ValueError("source policy owner does not match exact coordinate")
        current = state or self.read()
        owner_rows = current["source_policies_v2"].get(owner_principal_id)
        record = (
            owner_rows.get(coordinate.key) if isinstance(owner_rows, dict) else None
        )
        inherited_from = None
        if not isinstance(record, dict) and isinstance(owner_rows, dict):
            parent = parent_source_coordinate(coordinate)
            if parent.key != coordinate.key:
                record = owner_rows.get(parent.key)
                if isinstance(record, dict):
                    inherited_from = parent.key
        migrations = current["source_policy_v2_migrations"].get(owner_principal_id)
        migration = (
            migrations.get(coordinate.source_kind)
            if isinstance(migrations, dict)
            else None
        )
        if isinstance(record, dict):
            policy = SourcePolicyV2.model_validate(record["policy"])
            origin = "exact_override"
            migration_state = "resolved" if migration is not None else "not_required"
        else:
            policy = policy_for_preset("balanced")
            origin = (
                "migration_needed" if migration is not None else "recommended_default"
            )
            migration_state = (
                "migration_needed" if migration is not None else "not_required"
            )
        return {
            "policy": policy.model_dump(mode="json"),
            "policy_key": coordinate.key,
            "revision": self.source_policy_v2_revision(
                owner_principal_id, state=current
            ),
            "origin": origin,
            "migration_state": migration_state,
            "migration": copy.deepcopy(migration) if migration is not None else None,
            "exact_coordinate": coordinate.exact,
            "inherited_from_policy_key": inherited_from,
            "live_admission": "candidate_policy_v1",
            "admission_active": False,
        }

    def source_policy_v2_migrations(
        self, owner_principal_id: str, *, state: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        current = state or self.read()
        rows = current["source_policy_v2_migrations"].get(owner_principal_id)
        if not isinstance(rows, dict):
            return []
        return [
            {"source_id": source_id, **copy.deepcopy(record)}
            for source_id, record in sorted(rows.items())
            if isinstance(record, dict)
        ]

    def effective_adapter_config(
        self, value: dict[str, Any], *, state: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Apply persisted pause state without mutating reviewed base config."""

        effective = copy.deepcopy(value)
        lifecycle = (state or self.read())["connections"]
        for index, entry in enumerate(effective.get("adapters", [])):
            if not isinstance(entry, dict):
                continue
            connection_id = str(entry.get("id") or f"adapter-{index}")
            record = lifecycle.get(connection_id)
            if isinstance(record, dict) and record.get("state") == "paused":
                entry["enabled"] = False
        return restrict_adapter_config(effective)

    @staticmethod
    def _expect_revision(state: dict[str, Any], expected: int) -> None:
        if state["revision"] != expected:
            raise ValueError("Settings changed; refresh before saving")


def validate_preferences(values: dict[str, Any]) -> dict[str, int | bool | str]:
    unknown = set(values) - (set(PREFERENCE_BOUNDS) | {"location_cues"})
    if unknown:
        raise ValueError("unsupported CoS preference")
    normalized: dict[str, int | bool | str] = {}
    for key, value in values.items():
        if key == "location_cues":
            if not isinstance(value, bool):
                raise ValueError("location_cues must be true or false")
            normalized[key] = value
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
        lower, upper = PREFERENCE_BOUNDS[key]
        if value < lower or value > upper:
            raise ValueError(f"{key} must be between {lower} and {upper}")
        normalized[key] = value
    return normalized


def member_label(runtime: Any, member_id: str) -> str:
    for member in runtime.settings.household_mapping.get("members", []):
        if isinstance(member, dict) and member.get("member_id") == member_id:
            label = member.get("label")
            if isinstance(label, str) and label.strip():
                return label.strip()
    return "Household member"


def tailscale_login_for(runtime: Any, member_id: str) -> str | None:
    matches = [
        login
        for login, mapped_member in runtime.settings.member_identities.items()
        if mapped_member == member_id
    ]
    return matches[0] if len(matches) == 1 else None


def _credential_reference_present(config: dict[str, Any]) -> bool:
    return any(
        isinstance(value, str) and value.startswith(("op://", "keychain://"))
        for key, value in config.items()
        if key.endswith("_ref") or key.endswith("_reference")
    )


def _account_label(config: dict[str, Any], fallback: str) -> str:
    for key in ("account_label", "calendar_name", "list_name"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _account_hint(config: dict[str, Any]) -> str | None:
    for key in ("provider_account_id", "user_email", "subject_email"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def public_capability_summary(
    capabilities: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Render aggregate health without copying private adapter diagnostics.

    Per-account state belongs in the owner-scoped Connections/source controls.
    Internal runtime diagnostics retain their complete instance evidence.
    Public health checks retain states while excluding account/cursor details.
    """

    result = copy.deepcopy(capabilities)
    for record in result.values():
        if record.get("kind") != "adapter":
            continue
        details = record.get("details") or {}
        record["details"] = {
            key: details[key]
            for key in (
                "manifest",
                "categories",
                "runtimes",
                "provider_health",
                "production_loader",
                "deterministic_fixture_coverage",
            )
            if key in details
        }
    return result


def compose_connections(runtime: Any, member_id: str) -> list[dict[str, Any]]:
    adapter_supervisor = getattr(runtime, "adapters", None)
    inventory = adapter_supervisor.inventory() if adapter_supervisor is not None else {}
    control_store = getattr(runtime, "settings_control", None)
    lifecycle = control_store.read()["connections"] if control_store is not None else {}
    rows: list[dict[str, Any]] = []
    effective_config = restrict_adapter_config(runtime.settings.adapter_config)
    test_policy = policy_from_adapter_config(effective_config)
    cos_policy = cos_policy_from_config(effective_config)
    for index, entry in enumerate(effective_config.get("adapters", [])):
        if not isinstance(entry, dict):
            continue
        config = entry.get("config") or {}
        if not isinstance(config, dict) or config.get("member_id") != member_id:
            continue
        kind = str(entry.get("kind") or "").removeprefix("adapter:")
        meta = PROVIDER_CATALOG.get(kind)
        if meta is None:
            continue
        connection_id = str(entry.get("id") or f"adapter-{index}")
        override = lifecycle.get(connection_id)
        override_state = override.get("state") if isinstance(override, dict) else None
        configured_enabled = entry.get("enabled") is not False
        credentials_present = _credential_reference_present(config)
        runtime_row = inventory.get(connection_id)
        binding = next(
            (
                item
                for item in getattr(runtime.settings, "provider_bindings", ())
                if item.adapter_id == connection_id
            ),
            None,
        )
        runtime_state = "unknown"
        last_sync = None
        error = None
        if override_state == "paused" or not configured_enabled:
            runtime_state = "not_running"
        elif isinstance(runtime_row, dict):
            runtime_state = str(runtime_row.get("state") or "unknown")
            last_sync = runtime_row.get("last_cursor_advance_at")
            raw_error = runtime_row.get("last_error")
            if isinstance(raw_error, dict):
                error = {
                    "kind": raw_error.get("error_kind") or "unknown",
                    "severity": raw_error.get("severity") or "unknown",
                    "occurred_at": raw_error.get("occurred_at"),
                }
        # Disabled adapters intentionally have no active provider binding.
        # Keep that binding unverified without presenting a repair obligation.
        if override_state == "paused":
            setup_state = "paused"
        elif not configured_enabled:
            setup_state = "disabled"
        elif (
            getattr(runtime.settings, "deployment_evidence_required", False)
            and binding is None
        ):
            setup_state = "binding_blocked"
        elif not credentials_present:
            setup_state = "setup_incomplete"
        elif runtime_state == "running":
            # A credential reference is configuration; only live adapter
            # evidence proves the provider handshake completed.
            setup_state = "connected"
        else:
            setup_state = "configured"

        code_write_capabilities = (
            list(runtime_row.get("write_capabilities") or [])
            if isinstance(runtime_row, dict)
            else []
        )
        if kind == "messaging-gmail-api" and not config.get("enable_writes"):
            code_write_capabilities = []
        write_permissions = list(meta["write"]) if code_write_capabilities else []
        rows.append(
            {
                "id": connection_id,
                "provider": meta["provider"],
                "provider_label": meta["label"],
                "label": _account_label(config, meta["label"]),
                "account_hint": _account_hint(config),
                "product": meta["product"],
                "workspace": (
                    str(config["workspace"])
                    if config.get("workspace") in {"personal", "work"}
                    else None
                ),
                "binding": {
                    "state": "healthy" if binding is not None else "unverified",
                    "ownership_attested": config.get("ownership_attested") is True,
                    "provenance": config.get("binding_provenance"),
                    "permission_count": len(config.get("permission_scope") or []),
                    "content_exposed": False,
                },
                "deployment": meta["runtime"],
                "setup": setup_state,
                "runtime": runtime_state,
                "permissions": {
                    "read": list(meta["read"]),
                    "write": write_permissions,
                    "credential_reference_present": credentials_present,
                    "write_capability_source": "reviewed_adapter_code",
                },
                "safety": {
                    "observation_only": bool(runtime._observation_active),
                    "human_gate_required": bool(write_permissions),
                    "openclaw_approval_required": bool(write_permissions),
                    "writes_execute": bool(write_permissions)
                    and (
                        binding is not None
                        or not getattr(
                            runtime.settings, "deployment_evidence_required", False
                        )
                    )
                    and not runtime._observation_active,
                },
                "last_sync_at": last_sync,
                "error": error,
                "inactive_reason": (
                    "cos_forwarded_intake"
                    if cos_policy is not None and not configured_enabled
                    else "acquisition_restricted"
                    if test_policy is not None and not configured_enabled
                    else "configuration_disabled"
                    if not configured_enabled
                    else None
                ),
                "actions": (
                    []
                    if not configured_enabled
                    else ["resume"]
                    if setup_state == "paused"
                    else ["pause"]
                ),
            }
        )
    for identity, record in sorted(
        getattr(runtime.settings, "bridge_enrollments", {}).items()
    ):
        if (
            not isinstance(record, dict)
            or record.get("status") != "active"
            or record.get("member_id") != member_id
            or "messaging-bluebubbles" not in record.get("configured_packs", [])
        ):
            continue
        config = (record.get("pack_config") or {}).get("messaging-bluebubbles")
        if not isinstance(config, dict):
            continue
        connection_id = f"bridge:{record.get('enrollment_id')}:messaging-bluebubbles"
        authorized = "messaging-bluebubbles" in record.get("authorized_packs", [])
        enabled = "messaging-bluebubbles" in record.get("enabled_packs", [])
        active = "messaging-bluebubbles" in record.get("adapters_active", [])
        if test_policy is not None:
            enabled = False
            active = False
        rows.append(
            {
                "id": connection_id,
                "provider": "bluebubbles",
                "provider_label": "Apple Messages",
                "label": "Apple Messages (read-only)",
                "account_hint": config.get("provider_account_id"),
                "product": "Communications",
                "workspace": "personal",
                "binding": {
                    "state": "healthy" if authorized else "unverified",
                    "ownership_attested": config.get("ownership_attested") is True,
                    "provenance": config.get("binding_provenance"),
                    "permission_count": len(config.get("permission_scope") or []),
                    "content_exposed": False,
                    "identity_bound": bool(identity),
                    "bridge_enrollment_id": record.get("enrollment_id"),
                    "device_identity_sha256": config.get("device_identity_sha256"),
                    "protected_source_id": config.get("protected_source_id"),
                },
                "deployment": "member bridge",
                "setup": "connected" if authorized and enabled else "configured",
                "runtime": "running"
                if active
                else "starting"
                if enabled
                else "not_running",
                "permissions": {
                    "read": ["messages", "threads", "attachment metadata"],
                    "write": [],
                    "credential_reference_present": _credential_reference_present(
                        config
                    ),
                    "write_capability_source": "none_g10_observation_only",
                },
                "safety": {
                    "observation_only": True,
                    "human_gate_required": False,
                    "openclaw_approval_required": False,
                    "writes_execute": False,
                    "network_containment_required": True,
                },
                "last_sync_at": record.get("last_successful_observation"),
                "error": None,
                "actions": ["pause"],
            }
        )
    return sorted(rows, key=lambda row: (row["product"], row["label"].casefold()))


def _source_policy_adapter_shape(kind: str) -> tuple[str, str]:
    if kind.startswith("messaging-"):
        return "communication", "passive_communication"
    if kind.startswith(("calendaring-", "tasks-", "reminders-", "contacts-")):
        return "external_state_mirror", "passive_state_mirror"
    if kind.startswith("knowledge-"):
        return "personal_knowledge", "personal_knowledge"
    if kind.startswith("financial-"):
        return "inbound_data", "financial_observation"
    if kind.startswith("automation-"):
        return "inbound_data", "context_observation"
    return "unknown", "unknown"


def compose_source_policy_v2_sources(
    runtime: Any, member_id: str, *, state: dict[str, Any]
) -> list[dict[str, Any]]:
    """Union configured, retained and catalog sources without inventing access."""

    connections = {row["id"]: row for row in compose_connections(runtime, member_id)}
    test_policy = policy_from_adapter_config(runtime.settings.adapter_config)
    sources: list[dict[str, Any]] = []
    for index, entry in enumerate(runtime.settings.adapter_config.get("adapters", [])):
        if not isinstance(entry, dict):
            continue
        config = entry.get("config")
        if not isinstance(config, dict) or config.get("member_id") != member_id:
            continue
        kind = str(entry.get("kind") or "").removeprefix("adapter:")
        meta = PROVIDER_CATALOG.get(kind) or {
            "provider": str(config.get("provider") or "unknown"),
            "label": str(entry.get("label") or kind),
        }
        connection_id = str(entry.get("id") or f"adapter-{index}")
        binding = next(
            (
                item
                for item in getattr(runtime.settings, "provider_bindings", ())
                if item.adapter_id == connection_id and item.owner_id == member_id
            ),
            None,
        )
        provider_account_id = str(
            config.get("provider_account_id")
            or (binding.provider_account_id if binding is not None else "")
            or "unattributed"
        )
        workspace = str(
            config.get("workspace")
            or (binding.workspace if binding is not None else "")
            or "unknown"
        )
        adapter_category, source_class = _source_policy_adapter_shape(kind)
        catalog_source = next(
            (source for source in SOURCE_CATALOG if source.get("adapter_kind") == kind),
            None,
        )
        source_kind = str(
            catalog_source.get("id")
            if isinstance(catalog_source, dict)
            else f"adapter:{meta['provider']}"
        )
        native_kind = None
        native_id = None
        for candidate_kind, key in (
            ("calendar", "calendar_external_id"),
            ("calendar", "calendar_id"),
            ("list", "list_external_id"),
            ("list", "list_id"),
            ("label", "label_id"),
            ("label", "label"),
            ("folder", "folder_id"),
        ):
            value = config.get(key)
            if isinstance(value, str) and value.strip():
                native_kind = candidate_kind
                native_id = value.strip()
                break
        coordinate = SourceCoordinate.model_validate(
            {
                "tenant_id": runtime.settings.instance.tenant_id,
                "owner_principal_id": member_id,
                "owner_kind": "member",
                "context_member_id": member_id,
                "adapter_category": adapter_category,
                "adapter_kind": kind,
                "adapter_instance_id": connection_id,
                "provider": str(meta["provider"]),
                "provider_account_id": provider_account_id,
                "workspace": workspace,
                "source_kind": source_kind,
                "source_class": source_class,
                "trusted_route": f"member_private:{member_id}",
                "native_container_kind": native_kind,
                "native_container_id": native_id,
            }
        )
        effective = runtime.settings_control.effective_source_policy_v2(
            member_id, coordinate, state=state
        )
        connection = connections.get(connection_id, {})
        sources.append(
            {
                "policy_key": coordinate.key,
                "label": _account_label(config, str(meta["label"])),
                "provider_label": str(meta["label"]),
                "account_hint": _account_hint(config),
                "workspace": workspace,
                "adapter_instance_id": connection_id,
                "source_kind": source_kind,
                "coordinate": coordinate.model_dump(mode="json"),
                "coordinate_state": "exact" if coordinate.exact else "ambiguous",
                "configurable": coordinate.exact,
                "connection_state": "connected"
                if connection.get("setup") == "connected"
                else "configured",
                "retained_candidate_count": 0,
                "source_scope": "container" if native_id else "account",
                "acquisition": {
                    "state": effective["policy"]["acquisition_state"],
                    "coverage": "unknown",
                    "freshness": "current"
                    if connection.get("last_sync_at")
                    else "unknown",
                    "provider_capability": "available"
                    if connection.get("setup") in {"connected", "configured", "paused"}
                    else "unknown",
                    "scope_widening": False,
                    "credential_creation": False,
                    **(
                        test_policy.coverage()
                        if test_policy is not None
                        and connection_id == test_policy.adapter_id
                        else {
                            "state": "paused",
                            "coverage": "excluded_from_test_sample",
                            "mailbox_coverage_complete": False,
                        }
                        if test_policy is not None
                        else {}
                    ),
                },
                **effective,
            }
        )

    for record in getattr(runtime.settings, "bridge_enrollments", {}).values():
        if (
            not isinstance(record, dict)
            or record.get("status") != "active"
            or record.get("member_id") != member_id
            or "messaging-bluebubbles" not in record.get("configured_packs", [])
        ):
            continue
        config = (record.get("pack_config") or {}).get("messaging-bluebubbles")
        if not isinstance(config, dict):
            continue
        connection_id = f"bridge:{record.get('enrollment_id')}:messaging-bluebubbles"
        coordinate = SourceCoordinate(
            tenant_id=runtime.settings.instance.tenant_id,
            owner_principal_id=member_id,
            owner_kind="member",
            context_member_id=member_id,
            adapter_category="communication",
            adapter_kind="messaging-bluebubbles",
            adapter_instance_id=str(record.get("enrollment_id") or connection_id),
            provider="bluebubbles",
            provider_family="apple_messages",
            provider_account_id=str(
                config.get("provider_account_id") or "unattributed"
            ),
            workspace="personal",
            source_kind="adapter:bluebubbles",
            source_class="passive_communication",
            trusted_route=f"member_private:{member_id}",
            bridge_enrollment_id=str(record.get("enrollment_id") or "unknown"),
            device_identity_sha256=config.get("device_identity_sha256"),
            connector_id="messaging-bluebubbles",
            protected_source_id=str(config.get("protected_source_id") or "unknown"),
            native_container_kind="source",
            native_container_id=str(config.get("protected_source_id") or "unknown"),
        )
        effective = runtime.settings_control.effective_source_policy_v2(
            member_id, coordinate, state=state
        )
        connection = connections.get(connection_id, {})
        sources.append(
            {
                "policy_key": coordinate.key,
                "label": "Apple Messages (read-only)",
                "provider_label": "Apple Messages",
                "account_hint": config.get("provider_account_id"),
                "workspace": "personal",
                "adapter_instance_id": coordinate.adapter_instance_id,
                "source_kind": coordinate.source_kind,
                "coordinate": coordinate.model_dump(mode="json"),
                "coordinate_state": "exact" if coordinate.exact else "ambiguous",
                "configurable": coordinate.exact,
                "connection_state": "connected"
                if connection.get("setup") == "connected"
                else "configured",
                "retained_candidate_count": 0,
                "source_scope": "account",
                "acquisition": {
                    "state": effective["policy"]["acquisition_state"],
                    "coverage": "partial",
                    "freshness": "current"
                    if connection.get("last_sync_at")
                    else "unknown",
                    "provider_capability": "available"
                    if connection.get("setup") in {"connected", "configured"}
                    else "unknown",
                    "scope_widening": False,
                    "credential_creation": False,
                },
                "transport_assurance": {
                    "version": config.get("transport_version"),
                    "coverage": "bounded_best_effort",
                    "global_history_complete": False,
                    "network_containment_required": True,
                },
                **effective,
            }
        )

    for source in SOURCE_CATALOG:
        source_id = str(source["id"])
        if not source_id.startswith("surface:"):
            continue
        coordinate = SourceCoordinate(
            tenant_id=runtime.settings.instance.tenant_id,
            owner_principal_id=member_id,
            owner_kind="member",
            context_member_id=member_id,
            adapter_category="member_surface",
            adapter_kind="member-surface",
            adapter_instance_id=source_id,
            provider="administrateme",
            provider_account_id=member_id,
            workspace="private_portal",
            source_kind=source_id,
            source_class="direct_authenticated_intent",
            trusted_route=f"member_private:{member_id}",
        )
        effective = runtime.settings_control.effective_source_policy_v2(
            member_id, coordinate, state=state
        )
        sources.append(
            {
                "policy_key": coordinate.key,
                "label": str(source["label"]),
                "provider_label": "AdministrateMe",
                "account_hint": None,
                "workspace": "private_portal",
                "adapter_instance_id": source_id,
                "source_kind": source_id,
                "coordinate": coordinate.model_dump(mode="json"),
                "coordinate_state": "exact",
                "configurable": False,
                "connection_state": "connected",
                "retained_candidate_count": 0,
                "source_scope": "direct_intent",
                "direct_authenticated_intent": True,
                "acquisition": {
                    "state": "active",
                    "coverage": "complete",
                    "freshness": "current",
                    "provider_capability": "available",
                    "scope_widening": False,
                    "credential_creation": False,
                },
                **effective,
            }
        )
    sources = _merge_retained_source_inventory(runtime, member_id, state, sources)
    if test_policy is not None:
        for source in sources:
            if source.get("direct_authenticated_intent"):
                continue
            source.setdefault("acquisition", {}).update(
                test_policy.coverage()
                if source.get("adapter_instance_id") == test_policy.adapter_id
                else {
                    "state": "paused",
                    "coverage": "excluded_from_test_sample",
                    "mailbox_coverage_complete": False,
                }
            )
    represented = {str(row["source_kind"]) for row in sources}
    for catalog_source in SOURCE_CATALOG:
        source_id = str(catalog_source["id"])
        if source_id in represented:
            continue
        sources.append(
            {
                "policy_key": None,
                "coordinate": None,
                "coordinate_state": "not_observed",
                "configurable": False,
                "connection_state": "not_connected",
                "implementation": catalog_source["implementation"],
                "policy_execution": catalog_source["policy_execution"],
                "source_kind": source_id,
                "source_scope": "catalog",
                "label": str(catalog_source["label"]),
                "provider_label": str(catalog_source["label"]),
                "account_hint": None,
                "workspace": None,
                "adapter_instance_id": None,
                "retained_candidate_count": 0,
                "reason_codes": ["catalog_is_not_connection_or_permission_evidence"],
            }
        )
    return sorted(
        sources,
        key=lambda row: (
            not bool(row.get("direct_authenticated_intent")),
            str(row["provider_label"]),
            str(row["label"]),
        ),
    )


def _merge_retained_source_inventory(
    runtime: Any, member_id: str, state: dict[str, Any], sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Read content-free projected coordinates, including disconnected providers.

    Only exact private tenant/member rows are considered. Coordinates are
    provenance, not credentials or a claim that a connector remains available.
    """

    tenant_id = runtime.settings.instance.tenant_id
    private_scope = f"private:{member_id}"
    by_key = {row["policy_key"]: row for row in sources}
    retained: dict[str, tuple[SourceCoordinate, int]] = {}
    observed_counts: dict[str, int] = {}
    represented_proposals: set[str] = set()
    projections = getattr(runtime, "projections", None)
    if projections is not None:
        observed = (
            projections.connection("context_fabric")
            .execute(
                """SELECT source_coordinate_json, COUNT(*) AS source_count FROM source_policy_inventory
               WHERE tenant_id=? AND owner_member_id=? AND owner_scope=? AND visibility_scope=?
               GROUP BY policy_key, source_coordinate_json""",
                (tenant_id, member_id, private_scope, private_scope),
            )
            .fetchall()
        )
        for row in observed:
            try:
                coordinate = SourceCoordinate.model_validate_json(
                    row["source_coordinate_json"]
                )
            except (TypeError, ValueError):
                continue
            if (
                coordinate.tenant_id != tenant_id
                or coordinate.owner_principal_id != member_id
                or coordinate.owner_kind != "member"
            ):
                continue
            retained[coordinate.key] = (coordinate, 0)
            observed_counts[coordinate.key] = int(row["source_count"])
        rows = (
            projections.connection("context_fabric")
            .execute(
                """SELECT proposal_id, source_coordinate_json FROM reconciliation_decisions
               WHERE tenant_id=? AND owner_member_id=? AND owner_scope=? AND visibility_scope=?
               ORDER BY proposal_id, decision_revision DESC""",
                (tenant_id, member_id, private_scope, private_scope),
            )
            .fetchall()
        )
        for row in rows:
            proposal_id = str(row["proposal_id"])
            if proposal_id in represented_proposals:
                continue
            try:
                coordinate = SourceCoordinate.model_validate_json(
                    row["source_coordinate_json"]
                )
            except (TypeError, ValueError):
                continue
            if (
                coordinate.tenant_id != tenant_id
                or coordinate.owner_principal_id != member_id
                or coordinate.owner_kind != "member"
            ):
                continue
            represented_proposals.add(proposal_id)
            count = retained.get(coordinate.key, (coordinate, 0))[1]
            retained[coordinate.key] = (coordinate, count + 1)

    for record in state["source_policies_v2"].get(member_id, {}).values():
        if not isinstance(record, dict) or not record.get("coordinate"):
            continue
        coordinate = SourceCoordinate.model_validate(record["coordinate"])
        if (
            coordinate.tenant_id == tenant_id
            and coordinate.owner_principal_id == member_id
        ):
            retained.setdefault(coordinate.key, (coordinate, 0))

    for coordinate, count in list(retained.values()):
        parent = parent_source_coordinate(coordinate)
        if parent.key != coordinate.key and parent.key not in retained:
            retained[parent.key] = (parent, 0)
    for key, (coordinate, count) in retained.items():
        if key in by_key:
            by_key[key]["retained_candidate_count"] = count
            by_key[key]["retained_source_count"] = observed_counts.get(key, 0)
            continue
        parent_row = by_key.get(parent_source_coordinate(coordinate).key)
        meta = PROVIDER_CATALOG.get(coordinate.adapter_kind, {})
        catalog_source = next(
            (row for row in SOURCE_CATALOG if row["id"] == coordinate.source_kind), {}
        )
        label = str(
            meta.get("label") or catalog_source.get("label") or coordinate.provider
        )
        row = {
            "policy_key": key,
            "coordinate": coordinate.model_dump(mode="json"),
            "coordinate_state": "exact" if coordinate.exact else "ambiguous",
            "configurable": coordinate.exact,
            "connection_state": parent_row["connection_state"]
            if parent_row
            else "disconnected"
            if coordinate.exact
            else "missing_attribution",
            "label": label,
            "provider_label": label,
            "account_hint": coordinate.provider_account_id
            if coordinate.exact
            else None,
            "workspace": coordinate.workspace,
            "adapter_instance_id": coordinate.adapter_instance_id,
            "source_kind": coordinate.source_kind,
            "source_scope": "container"
            if coordinate.native_container_kind not in {None, "source"}
            else "account",
            "native_container_label": coordinate.native_container_id,
            "retained_candidate_count": count,
            "retained_source_count": observed_counts.get(key, 0),
            "acquisition": {
                "state": "paused"
                if parent_row is None
                else parent_row.get("acquisition", {}).get("state", "active"),
                "coverage": "partial",
                "freshness": "unknown",
                "provider_capability": "unavailable"
                if parent_row is None
                else "unknown",
                "scope_widening": False,
                "credential_creation": False,
            },
            **runtime.settings_control.effective_source_policy_v2(
                member_id, coordinate, state=state
            ),
        }
        by_key[key] = row
        sources.append(row)

    if projections is not None:
        missing: dict[str, int] = {}
        rows = (
            projections.connection("triage")
            .execute(
                """SELECT proposal_id, source_id FROM triage_items
               WHERE tenant_id=? AND owner_member_id=? AND owner_scope=? AND visibility_scope=?""",
                (tenant_id, member_id, private_scope, private_scope),
            )
            .fetchall()
        )
        for row in rows:
            if str(row["proposal_id"]) not in represented_proposals:
                source_id = str(row["source_id"])
                missing[source_id] = missing.get(source_id, 0) + 1
        for source_id, count in sorted(missing.items()):
            catalog_source = next(
                (row for row in SOURCE_CATALOG if row["id"] == source_id), {}
            )
            label = str(
                catalog_source.get("label") or "Retained source awaiting attribution"
            )
            sources.append(
                {
                    "policy_key": None,
                    "coordinate": None,
                    "coordinate_state": "ambiguous",
                    "configurable": False,
                    "connection_state": "missing_attribution",
                    "source_kind": source_id,
                    "source_scope": "unattributed",
                    "label": label,
                    "provider_label": label,
                    "account_hint": None,
                    "workspace": None,
                    "adapter_instance_id": None,
                    "retained_candidate_count": count,
                    "reason_codes": [
                        "retained_candidates_require_exact_source_attribution"
                    ],
                }
            )
    return sources


def compose_settings_control(runtime: Any, session: Any) -> dict[str, Any]:
    state = runtime.settings_control.read()
    auth_id = session.auth_member_id
    view_id = session.view_member_id
    viewing_as = auth_id != view_id
    private_owner_visible = not viewing_as
    auth_label = member_label(runtime, auth_id)
    view_label = member_label(runtime, view_id)
    connections = compose_connections(runtime, view_id) if private_owner_visible else []
    needs_attention: list[dict[str, Any]] = []
    for connection in connections:
        if connection["setup"] == "binding_blocked":
            needs_attention.append(
                {
                    "severity": "blocked",
                    "title": f"Review account ownership for {connection['label']}",
                    "detail": "Member, account, workspace, permission scope, and provenance must all be explicit.",
                    "section": "connections",
                }
            )
        elif connection["setup"] == "setup_incomplete":
            needs_attention.append(
                {
                    "severity": "blocked",
                    "title": f"Finish connecting {connection['label']}",
                    "detail": "A reviewed credential reference is still required.",
                    "section": "connections",
                }
            )
        elif connection["runtime"] in {"backoff", "crashed", "stopped"}:
            needs_attention.append(
                {
                    "severity": "degraded",
                    "title": f"Check {connection['label']}",
                    "detail": "The provider runtime is not currently healthy.",
                    "section": "connections",
                }
            )
        elif connection["runtime"] in {"unknown", "starting"}:
            needs_attention.append(
                {
                    "severity": "pending",
                    "title": f"Verify {connection['label']}",
                    "detail": "No current healthy synchronization evidence is available yet.",
                    "section": "connections",
                }
            )

    profiles = [
        {
            "id": pack.pack_id,
            "label": str(pack.data.get("label") or pack.pack_id),
            "format": pack.data.get("profile_format"),
            "behavior": pack.data.get("behavior", {}),
            "version": pack.version,
        }
        for pack in runtime.extensions.packs("profile")
        if pack.data.get("profile_format") not in {"ambient", "scoreboard"}
    ]
    active_profile = runtime.extensions.profile_for(view_id)
    persona = runtime.extensions.active_persona()
    bridges = []
    bridge_requests = state["bridge_requests"]
    for identity, record in sorted(runtime.settings.bridge_enrollments.items()):
        record_member = str(record.get("member_id") or "")
        record_member_role = runtime.settings.member_roles.get(record_member)
        guardian_visible = (
            session.auth_role == "principal" and record_member_role == "child"
        )
        if not private_owner_visible or (
            record_member != view_id and not guardian_visible
        ):
            continue
        bridges.append(
            {
                "member_id": record.get("member_id"),
                "member_label": member_label(
                    runtime, str(record.get("member_id") or "")
                ),
                "status": (
                    bridge_requests.get(record_member, {}).get("state")
                    if isinstance(bridge_requests.get(record_member), dict)
                    else record.get("status")
                ),
                "role": record.get("role"),
                "identity_bound": bool(identity),
                "adapters_active": list(record.get("adapters_active") or []),
                "configured_pack_count": len(record.get("configured_packs") or []),
                "authorized_pack_count": len(record.get("authorized_packs") or []),
                "provider_capability_state": record.get(
                    "provider_capability_state", "not_attested"
                ),
                "activated_at": record.get("activated_at"),
                "request_id": (
                    bridge_requests.get(record_member, {}).get("request_id")
                    if isinstance(bridge_requests.get(record_member), dict)
                    else record.get("request_id")
                ),
                "capability_states": copy.deepcopy(
                    bridge_requests.get(record_member, {}).get("capability_states", {})
                    if isinstance(bridge_requests.get(record_member), dict)
                    else {}
                ),
            }
        )
    for requested_member, record in sorted(bridge_requests.items()):
        if not isinstance(record, dict):
            continue
        requested_member_role = runtime.settings.member_roles.get(requested_member)
        guardian_visible = (
            session.auth_role == "principal" and requested_member_role == "child"
        )
        if not private_owner_visible or (
            requested_member != view_id and not guardian_visible
        ):
            continue
        if any(item["member_id"] == requested_member for item in bridges):
            continue
        bridges.append(
            {
                "member_id": requested_member,
                "member_label": member_label(runtime, requested_member),
                "status": record.get("state"),
                "role": runtime.settings.member_roles.get(requested_member),
                "identity_bound": False,
                "requested_capabilities": list(record.get("capabilities") or []),
                "requested_at": record.get("requested_at"),
                "request_id": record.get("request_id"),
                "capability_states": copy.deepcopy(
                    record.get("capability_states") or {}
                ),
            }
        )

    household = [
        {
            "member_id": member_id,
            "label": member_label(runtime, member_id),
            "role": role,
            "portal_identity_bound": bool(tailscale_login_for(runtime, member_id)),
            "can_enroll_bridge": role != "device",
        }
        for member_id, role in sorted(
            runtime.settings.member_roles.items(),
            key=lambda item: member_label(runtime, item[0]).casefold(),
        )
        if role not in {"device", "ambient"}
    ]
    configured_adapter_kinds = {
        str(entry.get("kind") or "").removeprefix("adapter:")
        for entry in runtime.settings.adapter_config.get("adapters", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("config"), dict)
        and private_owner_visible
        and entry["config"].get("member_id") == view_id
    }
    explicit_submissions = not runtime.settings.legacy_personal_obligation_compatibility
    passive_source_ids = {
        str(source["id"])
        for source in SOURCE_CATALOG
        if source["group"] == "Messages and conversations"
    }
    candidate_sources: list[dict[str, Any]] = []
    for source in SOURCE_CATALOG:
        source_id = str(source["id"])
        policy = runtime.settings_control.effective_candidate_policy(
            view_id, source_id, state=state
        )
        adapter_kind = source.get("adapter_kind")
        if source_id.startswith("surface:"):
            availability = "available"
        elif source["implementation"] == "planned":
            availability = "planned"
        elif adapter_kind in configured_adapter_kinds:
            availability = "configured"
        else:
            availability = "available_not_configured"
        candidate_sources.append(
            {
                **copy.deepcopy(source),
                "availability": availability,
                "policy": policy,
                "explicit_submission_required": explicit_submissions
                and source_id in passive_source_ids,
            }
        )
    source_policy_owner_visible = auth_id == view_id
    source_policy_sources = (
        compose_source_policy_v2_sources(runtime, auth_id, state=state)
        if source_policy_owner_visible
        else []
    )
    for source in source_policy_sources:
        source["explicit_submission_required"] = explicit_submissions and (
            source.get("source_kind") in passive_source_ids
            or (source.get("coordinate") or {}).get("source_class")
            == "passive_communication"
        )
    source_policy_migrations = (
        runtime.settings_control.source_policy_v2_migrations(auth_id, state=state)
        if source_policy_owner_visible
        else []
    )
    catalog = compose_member_catalog(runtime, session, settings_state=state)
    agreements = effective_operating_agreements(
        runtime.projections.connection("parties"),
        session,
        as_of_iso=datetime.now(timezone.utc).isoformat(),
    )
    if viewing_as:
        agreements = [
            value
            for value in agreements
            if value.get("authority_scope") == "household_shared"
        ]
    rendered_agreements = [plain_operating_agreement(value) for value in agreements]
    delegations = (
        list_service_delegations(
            runtime.projections.connection("parties"),
            session,
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        )
        if private_owner_visible
        else []
    )
    learning_proposals = (
        source_policy_proposals_for_member(
            runtime.projections.connection("context_fabric"),
            session,
            member_id=auth_id,
        )
        if private_owner_visible
        else []
    )
    learning_rules = (
        source_policy_rules_for_member(
            runtime.projections.connection("context_fabric"),
            session,
            member_id=auth_id,
        )
        if private_owner_visible
        else []
    )
    operating_agreement_rows = (
        list_operating_agreements(
            runtime.projections.connection("parties"),
            session,
            as_of_iso=datetime.now(timezone.utc).isoformat(),
            include_inactive=True,
        )
        if private_owner_visible
        else []
    )
    capability_requests = (
        runtime.settings_control.capability_requests_for(view_id, state=state)
        if private_owner_visible
        else []
    )
    admin_request_rows: list[dict[str, Any]] = []
    if session.auth_role == "principal" and not viewing_as:
        for requesting_member, member_rows in (
            (auth_id, state["capability_requests"].get(auth_id) or {}),
        ):
            if not isinstance(member_rows, dict):
                continue
            for capability_id, record in sorted(member_rows.items()):
                if not isinstance(record, dict) or record.get("state") != "requested":
                    continue
                admin_request_rows.append(
                    {
                        "requesting_member_id": requesting_member,
                        "requesting_member_label": member_label(
                            runtime, requesting_member
                        ),
                        "capability_id": capability_id,
                        "desired_outcome": record.get("desired_outcome"),
                        "requested_scope": record.get("requested_scope"),
                        "provider_option": record.get("provider_option"),
                        "revision": record.get("revision"),
                        "state": "requested",
                        "contains_credentials": False,
                        "contains_provider_content": False,
                        "admin_may_complete_private_consent": False,
                    }
                )
    compiled_context = (
        compile_effective_cos_context(
            runtime,
            session,
            settings_state=state,
            as_of_iso=datetime.now(timezone.utc).isoformat(),
        )
        if private_owner_visible
        else None
    )
    return {
        "revision": state["revision"],
        "identity": {
            "authenticated_member_id": auth_id,
            "authenticated_display_name": auth_label,
            "authenticated_role": session.auth_role,
            "view_member_id": view_id,
            "view_display_name": view_label,
            "view_role": session.view_role,
            "authentication_method": "Tailscale",
            "identity_bound": bool(tailscale_login_for(runtime, auth_id)),
            "viewing_as": viewing_as,
            "can_mutate": not viewing_as
            and session.auth_role not in {"child", "device", "ambient"},
            "actions_as": auth_id,
            "reads_as": view_id,
            "active_cos_context_member_id": view_id,
            "member_agent_id": member_agent_id(view_id),
            "context_isolated": True,
        },
        "cos_identity": {
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "party_id": COS_PARTY_ID,
            "same_household_cos": True,
            "active_member_context": view_id,
            "on_behalf_of_member_id": view_id,
            "openclaw_agent_id": member_agent_id(view_id),
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
            "generic_member_memory_shared": False,
            "raw_cross_member_context": False,
        },
        "needs_attention": needs_attention,
        "connections": connections,
        "service_channels": public_service_identity_inventory(
            runtime.settings.service_principals,
            runtime.settings.service_channel_accounts,
        ),
        "candidate_identification": {
            "contract": "deterministic-candidate-policy-v1",
            "sources": candidate_sources,
            "candidate_kinds": list(
                dict.fromkeys(
                    kind
                    for source in SOURCE_CATALOG
                    for kind in source["candidate_kinds"]
                )
            ),
            "non_configurable": [
                "source retention and terminal accounting",
                "privacy and member isolation",
                "exact-evidence grounding",
                "deterministic eligibility and routing",
                "human confirmation before canonical truth",
                "governance and observation before provider effects",
            ],
        },
        "source_policy_v2": {
            "explicit_communication_submissions": explicit_submissions,
            "contract": "per-principal-per-source-policy-v2",
            "owner_only": True,
            "owner_visible": source_policy_owner_visible,
            "owner_principal_id": auth_id if source_policy_owner_visible else None,
            "revision": runtime.settings_control.source_policy_v2_revision(
                auth_id, state=state
            )
            if source_policy_owner_visible
            else None,
            "live_admission": "reconciled_source_policy_v2"
            if getattr(runtime.settings, "reconciliation_admission_enabled", False)
            else "candidate_policy_v1",
            "shadow_only": not bool(
                getattr(runtime.settings, "reconciliation_admission_enabled", False)
            ),
            "admission_active": bool(
                getattr(runtime.settings, "reconciliation_admission_enabled", False)
            ),
            "attention_modes": [
                {"value": "obligations", "label": "Focus on obligations"},
                {"value": "possibilities", "label": "Include possible opportunities"},
                {"value": "manual", "label": "Only when I ask"},
            ],
            "purpose_options": [
                {
                    "value": value,
                    "label": label,
                    "description": "Applies to this assessed action; other actions in the same source keep their own routes.",
                }
                for value, label in PURPOSE_OPTIONS
            ],
            "route_options": [
                {"value": "review", "label": "Needs you"},
                {"value": "reference", "label": "Reference"},
                {"value": "alert", "label": "Alerts when permitted"},
            ],
            "provider_execution_is_a_policy_plane": False,
            "presets": [
                {
                    "id": "focused",
                    "label": "Focus on obligations",
                    "explanation": "Prioritizes grounded obligations. Existing context, access and alert choices stay unchanged.",
                },
                {
                    "id": "balanced",
                    "label": "Include possible opportunities",
                    "explanation": "Includes possible work while preserving context, access and alert choices.",
                },
                {
                    "id": "manual",
                    "label": "Only when I ask",
                    "explanation": "Stops automatic work suggestions while preserving context and alert choices.",
                },
                {
                    "id": "review_everything",
                    "label": "Review Everything",
                    "explanation": "Sends every permitted source fact to review without granting action authority.",
                },
            ],
            "candidate_kinds": list(V2_CANDIDATE_KINDS),
            "sources": source_policy_sources,
            "migrations": source_policy_migrations,
            "safety_limitations": [
                "Policies cannot widen provider scopes or create credentials.",
                "Policies cannot establish identity, ownership, priority, disclosure, or write authority.",
                "Provider labels, sender urgency, and language claims are non-authoritative evidence.",
                "Direct authenticated member intent cannot be suppressed by passive-source presets.",
                "Attention preferences affect local review only; source access and provider actions remain separately governed.",
            ],
            "preview": {
                "mutates": False,
                "uses": "recent authorized sources and verified action assessments, including sources with no candidate",
                "content_returned": False,
            },
        },
        "behavior": {
            "active_profile_id": active_profile.get("id")
            if isinstance(active_profile, dict)
            else None,
            "profiles": profiles,
            "preferences": runtime.settings_control.preferences_for(view_id)
            if private_owner_visible
            else {},
            "persona": {
                "id": persona.get("id"),
                "label": persona.get("label"),
                "instance_wide": True,
                "authority_changes": False,
                "model_routing_changes": False,
            },
        },
        "bridges": bridges,
        "apple_messages_replies": (
            reply_control_for(runtime, view_id, settings_state=state)
            if private_owner_visible
            else {
                "state": "bridge_not_connected",
                "reason": "Reply controls are owner-only and unavailable in view-as.",
                "member_id": view_id,
                "ask_every_time": True,
                "enabled": False,
                "revision": None,
                "provider_effects": False,
            }
        ),
        "bridge_wizard": {
            "contract": WIZARD_CONTRACT,
            "progress_contract": PROGRESS_CONTRACT,
            "enrollment_states": list(ENROLLMENT_STATES),
            "capability_states": list(CAPABILITY_STATES),
            "capabilities": capability_catalog(),
            "current": copy.deepcopy(bridge_requests.get(view_id))
            if private_owner_visible and isinstance(bridge_requests.get(view_id), dict)
            else None,
            "native_access_stays_on_member_mac": True,
            "observation_only_default": True,
            "writes_authorized": False,
            "admin_may_complete_adult_consent": False,
            "view_as_may_mutate": False,
            "ordinary_json_contains_transfer_secret": False,
            "recovery_actions": ["pause", "resume", "repair", "retry", "revoke"],
        },
        "capabilities": catalog,
        "capability_request_revision": runtime.settings_control.capability_request_revision(
            view_id, state=state
        )
        if private_owner_visible
        else None,
        "capability_requests": capability_requests,
        "how_my_cos_works": {
            "contract": "household-cos-operating-agreement-v1",
            "private_owner_visible": private_owner_visible,
            "agreements": rendered_agreements,
            "delegations": delegations,
            "source_policy_contract": "separate",
            "global_trust_score": None,
            "preferences_grant_authority": False,
            "compiled_context": compiled_context,
        },
        "learning_and_trust": {
            "proposals": learning_proposals,
            "operating_agreement_proposals": [
                plain_operating_agreement(value)
                for value in operating_agreement_rows
                if value.get("effective_state") == "proposed"
            ],
            "rules": learning_rules,
            "trials": [
                value
                for value in learning_rules
                if value.get("state") == "trial" or value.get("trial_proposal_ids")
            ],
            "suspended_or_revoked": [
                value
                for value in learning_rules
                if value.get("state") in {"suspended", "revoked", "expired"}
            ],
            "inactive_operating_agreements": [
                plain_operating_agreement(value)
                for value in operating_agreement_rows
                if value.get("effective_state")
                in {"suspended", "revoked", "expired", "rejected", "superseded"}
            ],
            "trust_is_capability_specific": True,
            "silent_cross_domain_promotion": False,
        },
        "household_privacy": {
            "private_sources_owner_only": True,
            "busy_only_is_separate_policy": True,
            "other_adult_approval_required_for_shared_authority": True,
            "professional_data_excluded_without_exact_grant": True,
            "surprise_planning_remains_private": True,
            "admin_may_complete_other_adult_consent": False,
        },
        "admin_requests": {
            "visible": session.auth_role == "principal" and not viewing_as,
            "items": admin_request_rows,
            "secret_free": True,
            "other_adult_consent_controls": False,
        },
        "bridge_candidates": [
            member
            for member in household
            if member["member_id"] == auth_id
            or (
                session.auth_role == "principal"
                and member["role"] in {"child", "dependent"}
            )
        ],
        "household": household,
    }


__all__ = [
    "ConnectionAction",
    "PREFERENCE_BOUNDS",
    "PROVIDER_CATALOG",
    "SettingsControlStore",
    "SettingsMutation",
    "compose_connections",
    "compose_settings_control",
    "member_label",
    "public_capability_summary",
    "tailscale_login_for",
    "validate_preferences",
]
