"""Deterministic member Settings control-plane composition and desired state.

This module is deliberately not a projection and not another application
runtime.  It combines authenticated member context with secret-free runtime
evidence and stores only bounded operational preferences.  Provider tokens,
OAuth payloads, and credential references never cross this boundary.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


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
        "read": ["messages", "threads", "attachments"],
        "write": ["reply", "send"],
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

    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @staticmethod
    def _default() -> dict[str, Any]:
        return {
            "schema_version": SettingsControlStore.SCHEMA_VERSION,
            "revision": 0,
            "connections": {},
            "member_preferences": {},
            "bridge_requests": {},
        }

    def read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._default()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Settings control journal is unreadable") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise RuntimeError("Settings control journal has an unsupported schema")
        if not isinstance(value.get("revision"), int):
            raise RuntimeError("Settings control journal revision is invalid")
        for key in ("connections", "member_preferences", "bridge_requests"):
            if not isinstance(value.get(key), dict):
                raise RuntimeError(f"Settings control journal {key} is invalid")
        return value

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
            "apple_notes",
            "apple_voice_memos",
            "obsidian",
        }
        selected = sorted(set(capabilities))
        if not selected or set(selected) - allowed:
            raise ValueError("bridge capabilities are invalid")
        previous = copy.deepcopy(state["bridge_requests"].get(member_id) or {})
        current = {
            "member_id": member_id,
            "capabilities": selected,
            "state": "enrollment_preparation_required",
            "requested_by_member_id": requested_by_member_id,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        state["bridge_requests"][member_id] = current
        state["revision"] += 1
        _atomic_json(self.path, state)
        return SettingsMutation(previous, current, state["revision"])

    def preferences_for(self, member_id: str) -> dict[str, Any]:
        value = self.read()["member_preferences"].get(member_id)
        return copy.deepcopy(value) if isinstance(value, dict) else {}

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
        return effective

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
    return sorted(matches, key=str.casefold)[0] if matches else None


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


def _account_hint(config: dict[str, Any]) -> str:
    for key in ("provider_account_id", "user_email", "subject_email"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Account identity confirmed after provider handshake"


def compose_connections(runtime: Any, member_id: str) -> list[dict[str, Any]]:
    adapter_supervisor = getattr(runtime, "adapters", None)
    inventory = adapter_supervisor.inventory() if adapter_supervisor is not None else {}
    control_store = getattr(runtime, "settings_control", None)
    lifecycle = control_store.read()["connections"] if control_store is not None else {}
    rows: list[dict[str, Any]] = []
    for index, entry in enumerate(runtime.settings.adapter_config.get("adapters", [])):
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
        if override_state == "paused":
            setup_state = "paused"
        elif not configured_enabled:
            setup_state = "disabled"
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
                "workspace": str(config.get("workspace") or "personal"),
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
                    and not runtime._observation_active,
                },
                "last_sync_at": last_sync,
                "error": error,
                "actions": ["resume"] if setup_state == "paused" else ["pause"],
            }
        )
    return sorted(rows, key=lambda row: (row["product"], row["label"].casefold()))


def compose_settings_control(runtime: Any, session: Any) -> dict[str, Any]:
    state = runtime.settings_control.read()
    auth_id = session.auth_member_id
    view_id = session.view_member_id
    viewing_as = auth_id != view_id
    auth_label = member_label(runtime, auth_id)
    view_label = member_label(runtime, view_id)
    connections = compose_connections(runtime, view_id)
    needs_attention: list[dict[str, Any]] = []
    for connection in connections:
        if connection["setup"] == "setup_incomplete":
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
        if record.get("member_id") != view_id and session.auth_role != "principal":
            continue
        bridges.append(
            {
                "member_id": record.get("member_id"),
                "member_label": member_label(
                    runtime, str(record.get("member_id") or "")
                ),
                "status": record.get("status"),
                "role": record.get("role"),
                "identity_bound": bool(identity),
                "adapters_active": list(record.get("adapters_active") or []),
                "activated_at": record.get("activated_at"),
            }
        )
    for requested_member, record in sorted(bridge_requests.items()):
        if not isinstance(record, dict):
            continue
        if requested_member != view_id and session.auth_role != "principal":
            continue
        if any(item["member_id"] == requested_member for item in bridges):
            continue
        bridges.append(
            {
                "member_id": requested_member,
                "member_label": member_label(runtime, requested_member),
                "status": record.get("state"),
                "role": runtime.settings.member_roles.get(requested_member),
                "identity_bound": bool(tailscale_login_for(runtime, requested_member)),
                "requested_capabilities": list(record.get("capabilities") or []),
                "requested_at": record.get("requested_at"),
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
        },
        "needs_attention": needs_attention,
        "connections": connections,
        "behavior": {
            "active_profile_id": active_profile.get("id")
            if isinstance(active_profile, dict)
            else None,
            "profiles": profiles,
            "preferences": runtime.settings_control.preferences_for(view_id),
            "persona": {
                "id": persona.get("id"),
                "label": persona.get("label"),
                "instance_wide": True,
                "authority_changes": False,
                "model_routing_changes": False,
            },
        },
        "bridges": bridges,
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
    "tailscale_login_for",
    "validate_preferences",
]
