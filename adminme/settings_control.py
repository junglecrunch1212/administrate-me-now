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

from adminme.service_identities import public_service_identity_inventory

from adminme.candidate_policy import (
    SOURCE_CATALOG,
    effective_policy,
    validate_policy_override,
)
from adminme.source_policy import (
    V2_CANDIDATE_KINDS,
    SourceCoordinate,
    SourcePolicyV2,
    policy_for_preset,
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

    SCHEMA_VERSION = 3

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
            "candidate_policies": {},
            "source_policies_v2": {},
            "source_policy_v2_revisions": {},
            "source_policy_v2_migrations": {},
        }

    def read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._default()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Settings control journal is unreadable") from exc
        if not isinstance(value, dict) or value.get("schema_version") not in {1, 2, 3}:
            raise RuntimeError("Settings control journal has an unsupported schema")
        if value["schema_version"] == 1:
            value = copy.deepcopy(value)
            value["schema_version"] = 2
            value["candidate_policies"] = {}
        if value["schema_version"] == 2:
            value = copy.deepcopy(value)
            value["schema_version"] = self.SCHEMA_VERSION
            value["source_policies_v2"] = {}
            value["source_policy_v2_revisions"] = {}
            value["source_policy_v2_migrations"] = self._candidate_policy_migrations(
                value.get("candidate_policies") or {}
            )
        if not isinstance(value.get("revision"), int):
            raise RuntimeError("Settings control journal revision is invalid")
        for key in (
            "connections",
            "member_preferences",
            "bridge_requests",
            "candidate_policies",
            "source_policies_v2",
            "source_policy_v2_revisions",
            "source_policy_v2_migrations",
        ):
            if not isinstance(value.get(key), dict):
                raise RuntimeError(f"Settings control journal {key} is invalid")
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
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("Settings source policy V2 is invalid") from exc
        if set(value["source_policy_v2_revisions"]) - set(
            value["source_policies_v2"]
        ):
            raise RuntimeError("Settings source policy V2 owner revision is invalid")
        for migrations in value["source_policy_v2_migrations"].values():
            if not isinstance(migrations, dict) or any(
                not isinstance(record, dict)
                or record.get("state") != "migration_needed"
                or record.get("live_behavior_preserved") is not True
                for record in migrations.values()
            ):
                raise RuntimeError("Settings source policy migration state is invalid")
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
        state["source_policy_v2_migrations"].setdefault(member_id, {})[
            source_id
        ] = {
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
            raise ValueError("source policy event revision is duplicate or moved backwards")
        if revision != current_revision + 1:
            raise ValueError("source policy event revision has a gap")
        action = payload.get("action")
        if action == "reset":
            owner_rows.pop(policy_key, None)
            current: dict[str, Any] = {}
        elif action == "set":
            policy = SourcePolicyV2.model_validate(payload.get("policy"))
            current = {
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

    def apply_source_policy_v2_event(
        self, payload: dict[str, Any]
    ) -> SettingsMutation:
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
            origin = "migration_needed" if migration is not None else "recommended_default"
            migration_state = "migration_needed" if migration is not None else "not_required"
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
        if (
            getattr(runtime.settings, "deployment_evidence_required", False)
            and binding is None
        ):
            setup_state = "binding_blocked"
        elif override_state == "paused":
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
                "actions": ["resume"] if setup_state == "paused" else ["pause"],
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
    """Compose only exact owner-visible coordinates; never adapter-wide rows."""

    connections = {row["id"]: row for row in compose_connections(runtime, member_id)}
    sources: list[dict[str, Any]] = []
    for index, entry in enumerate(runtime.settings.adapter_config.get("adapters", [])):
        if not isinstance(entry, dict):
            continue
        config = entry.get("config")
        if not isinstance(config, dict) or config.get("member_id") != member_id:
            continue
        kind = str(entry.get("kind") or "").removeprefix("adapter:")
        meta = PROVIDER_CATALOG.get(kind)
        if meta is None:
            continue
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
            (
                source
                for source in SOURCE_CATALOG
                if source.get("adapter_kind") == kind
            ),
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
    return sorted(
        sources,
        key=lambda row: (
            not bool(row.get("direct_authenticated_intent")),
            str(row["provider_label"]),
            str(row["label"]),
        ),
    )


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
                "configured_pack_count": len(record.get("configured_packs") or []),
                "authorized_pack_count": len(record.get("authorized_packs") or []),
                "provider_capability_state": record.get(
                    "provider_capability_state", "not_attested"
                ),
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
                "identity_bound": False,
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
    configured_adapter_kinds = {
        str(entry.get("kind") or "").removeprefix("adapter:")
        for entry in runtime.settings.adapter_config.get("adapters", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("config"), dict)
        and entry["config"].get("member_id") == view_id
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
            }
        )
    source_policy_owner_visible = auth_id == view_id
    source_policy_sources = (
        compose_source_policy_v2_sources(runtime, auth_id, state=state)
        if source_policy_owner_visible
        else []
    )
    source_policy_migrations = (
        runtime.settings_control.source_policy_v2_migrations(auth_id, state=state)
        if source_policy_owner_visible
        else []
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
            "contract": "per-principal-per-source-policy-v2-shadow",
            "owner_only": True,
            "owner_visible": source_policy_owner_visible,
            "owner_principal_id": auth_id if source_policy_owner_visible else None,
            "revision": runtime.settings_control.source_policy_v2_revision(
                auth_id, state=state
            )
            if source_policy_owner_visible
            else None,
            "live_admission": "candidate_policy_v1",
            "shadow_only": True,
            "provider_execution_is_a_policy_plane": False,
            "presets": [
                {
                    "id": "focused",
                    "label": "Focused",
                    "explanation": "Reviews only typed actionable signals; keeps critical financial and security alerts.",
                },
                {
                    "id": "balanced",
                    "label": "Balanced",
                    "explanation": "Reviews useful source facts while filtering typed low-actionability signals.",
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
                "V2 admission remains shadow-only until a later, separately approved activation.",
            ],
            "preview": {
                "mutates": False,
                "uses": "synthetic or already authorized source metadata",
                "content_returned": False,
            },
        },
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
