"""Fail-closed authorization for separately supervised member bridges.

Bridge transport enrollment proves only that one private Tailscale identity may
speak for one household member. Provider access is a second, per-pack gate.
This module owns the content-free policy and record validation used by
bootstrap, runtime startup, bridge ingest, and command delivery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping
from urllib.parse import urlparse


PROVIDER_ACCOUNT_PATTERN = re.compile(r"provider-account:[0-9a-f]{32,64}")
PERMISSION_REFERENCE_PATTERN = re.compile(
    r"(?:op://[^/]+/[^/]+/[^/]+|keychain://[^/]+/[^/]+)"
)

CapabilityState = Literal["not_required", "not_attested", "attested"]


@dataclass(frozen=True)
class BridgePackPolicy:
    pack: str
    source_adapter: str
    family: str
    event_types: frozenset[str]
    source_account_kind: str | None = None
    provider: str | None = None
    provider_family: str | None = None
    permission_scopes: frozenset[str] = frozenset()
    permission_reference: str | None = None
    container_key: str | None = None
    shareable: bool = False
    write_operations: frozenset[str] = frozenset()

    @property
    def requires_provider_account(self) -> bool:
        return self.provider is not None

    @property
    def requires_capability_attestation(self) -> bool:
        return bool(self.permission_scopes)


BRIDGE_PACK_POLICIES: dict[str, BridgePackPolicy] = {
    "messaging-bluebubbles": BridgePackPolicy(
        pack="messaging-bluebubbles",
        source_adapter="messaging-bluebubbles",
        family="messaging",
        event_types=frozenset(
            {
                "adapter.authenticated",
                "adapter.error",
                "adapter.record.quarantined",
                "messaging.received",
                "messaging.sent",
                "messaging.source_observed",
            }
        ),
        provider="bluebubbles",
        provider_family="apple_messages",
        permission_scopes=frozenset(
            {"bluebubbles.messages.read", "macos.full_disk_access"}
        ),
        permission_reference="keychain://device-permissions/apple-messages",
        container_key="protected_source_id",
        shareable=False,
        # G10 is observation-only. G11 must introduce any reply authority
        # through a separately reviewed policy and command path.
        write_operations=frozenset(),
    ),
    "calendaring-apple-calendar-bridge": BridgePackPolicy(
        pack="calendaring-apple-calendar-bridge",
        source_adapter="calendaring-apple-calendar",
        family="calendaring",
        event_types=frozenset(
            {
                "adapter.authenticated",
                "adapter.error",
                "calendar.event_added",
                "calendar.event_deleted",
                "calendar.event_outcome_recorded",
                "calendar.event_updated",
            }
        ),
        provider="apple_calendar",
        provider_family="apple",
        permission_scopes=frozenset(
            {"eventkit.calendar.read", "eventkit.calendar.write"}
        ),
        permission_reference="keychain://device-permissions/calendar",
        container_key="calendar_identifier",
        shareable=True,
        write_operations=frozenset(
            {"create_event", "delete_event", "move_event", "update_event"}
        ),
    ),
    "reminders-apple-eventkit-bridge": BridgePackPolicy(
        pack="reminders-apple-eventkit-bridge",
        source_adapter="reminders-apple-eventkit",
        family="reminders",
        event_types=frozenset(
            {
                "adapter.authenticated",
                "adapter.cursor_advanced",
                "adapter.error",
                "list.created",
                "list.deleted",
                "list.updated",
                "list_item.added",
                "list_item.completed",
                "list_item.deleted",
                "list_item.uncompleted",
                "list_item.updated",
                "reminder.list_item.synced",
            }
        ),
        provider="apple_reminders",
        provider_family="apple",
        permission_scopes=frozenset(
            {"eventkit.reminders.read", "eventkit.reminders.write"}
        ),
        permission_reference="keychain://device-permissions/reminders",
        container_key="target_list_identifier",
        shareable=True,
        write_operations=frozenset(
            {
                "complete_reminder",
                "create_list",
                "create_reminder",
                "delete_list",
                "delete_reminder",
                "move_reminder",
                "uncomplete_reminder",
                "update_list",
                "update_reminder",
            }
        ),
    ),
    "contacts-apple-contacts-bridge": BridgePackPolicy(
        pack="contacts-apple-contacts-bridge",
        source_adapter="contacts-apple-contacts",
        family="contacts",
        event_types=frozenset(
            {
                "adapter.authenticated",
                "adapter.error",
                "contact.group.observed",
                "contact.record.deleted",
                "contact.record.observed",
            }
        ),
        provider="apple_contacts",
        provider_family="apple",
        permission_scopes=frozenset({"contacts.read", "contacts.write"}),
        permission_reference="keychain://device-permissions/contacts",
        container_key="container_external_id",
        # D23 admits a sparse identifier-correction write seam, not general
        # address-book creation or deletion.
        write_operations=frozenset({"update_party_identifier"}),
    ),
    "knowledge-apple-notes": BridgePackPolicy(
        pack="knowledge-apple-notes",
        source_adapter="knowledge-apple-notes",
        family="knowledge",
        source_account_kind="apple_notes",
        event_types=frozenset(
            {
                "member_knowledge.folder_created",
                "member_knowledge.folder_deleted",
                "member_knowledge.folder_updated",
                "member_knowledge.note_added",
                "member_knowledge.note_corrected",
                "member_knowledge.note_deleted",
                "member_knowledge.note_updated",
            }
        ),
    ),
    "knowledge-apple-voice-memos": BridgePackPolicy(
        pack="knowledge-apple-voice-memos",
        source_adapter="knowledge-apple-voice-memos",
        family="knowledge",
        source_account_kind="apple_voice_memos",
        event_types=frozenset(
            {
                "member_knowledge.note_added",
                "member_knowledge.note_deleted",
                "member_knowledge.note_updated",
            }
        ),
    ),
    "knowledge-obsidian": BridgePackPolicy(
        pack="knowledge-obsidian",
        source_adapter="knowledge-obsidian",
        family="knowledge",
        source_account_kind="obsidian",
        event_types=frozenset(
            {
                "member_knowledge.note_added",
                "member_knowledge.note_deleted",
                "member_knowledge.note_updated",
            }
        ),
    ),
}


@dataclass(frozen=True)
class BridgeConfiguration:
    member_id: str
    enrollment_id: str
    packs: frozenset[str]
    pack_config: dict[str, dict[str, Any]]

    def policy_for_source(self, source_adapter: str) -> BridgePackPolicy | None:
        matches = [
            policy
            for pack, policy in BRIDGE_PACK_POLICIES.items()
            if pack in self.packs and policy.source_adapter == source_adapter
        ]
        return matches[0] if len(matches) == 1 else None

    def source_account_for(self, policy: BridgePackPolicy) -> str | None:
        if policy.source_account_kind is not None:
            return policy.source_account_kind
        value = self.pack_config.get(policy.pack, {}).get("provider_account_id")
        return value if isinstance(value, str) else None

    def shared_container_ids(self, policy: BridgePackPolicy) -> frozenset[str]:
        values = self.pack_config.get(policy.pack, {}).get("shared_container_ids", [])
        if not isinstance(values, list):
            return frozenset()
        return frozenset(str(value) for value in values)


@dataclass(frozen=True)
class BridgeAuthorization(BridgeConfiguration):
    capability_event_ids: dict[str, str]

    def policy_for_provider(self, provider: str) -> BridgePackPolicy | None:
        matches = [
            policy
            for pack, policy in BRIDGE_PACK_POLICIES.items()
            if pack in self.packs and policy.provider == provider
        ]
        return matches[0] if len(matches) == 1 else None

    def provider_account_for(self, pack: str) -> str | None:
        value = self.pack_config.get(pack, {}).get("provider_account_id")
        return value if isinstance(value, str) else None


def _unique_strings(value: Any, *, label: str, allow_empty: bool = True) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len({item.strip() for item in value}) != len(value)
        or (not allow_empty and not value)
    ):
        raise ValueError(f"{label} must be a unique string list")
    return [item.strip() for item in value]


def validate_configured_pack_config(
    *,
    member_id: str,
    enrollment_id: str | None,
    configured_packs: list[str],
    pack_config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Validate sealed bridge configuration without granting provider access."""

    packs = _unique_strings(configured_packs, label="configured bridge packs")
    unknown = set(packs) - set(BRIDGE_PACK_POLICIES)
    if unknown:
        raise ValueError(f"unreviewed bridge pack configuration: {sorted(unknown)}")
    if not isinstance(pack_config, dict) or set(pack_config) != set(packs):
        raise ValueError("bridge pack config must exactly match configured packs")

    normalized: dict[str, dict[str, Any]] = {}
    for pack in packs:
        policy = BRIDGE_PACK_POLICIES[pack]
        raw = pack_config.get(pack)
        if not isinstance(raw, dict):
            raise ValueError(f"bridge pack config for {pack} must be a mapping")
        config = dict(raw)
        if config.get("member_id") != member_id:
            raise ValueError(f"bridge pack {pack} must bind the exact member")
        if policy.requires_provider_account:
            account = config.get("provider_account_id")
            if (
                not isinstance(account, str)
                or PROVIDER_ACCOUNT_PATTERN.fullmatch(account) is None
            ):
                raise ValueError(
                    f"bridge pack {pack} requires a content-free provider account reference"
                )
            if account.casefold() == member_id.casefold() or (
                enrollment_id is not None
                and account.casefold() == enrollment_id.casefold()
            ):
                raise ValueError(f"bridge pack {pack} collapses identity coordinates")
            if config.get("deployment_target") != "bridge":
                raise ValueError(
                    f"bridge pack {pack} requires deployment_target=bridge"
                )
            if config.get("workspace") != "personal":
                raise ValueError(f"bridge pack {pack} requires workspace=personal")
            if config.get("binding_provenance") != "member-bridge-enrollment":
                raise ValueError(
                    f"bridge pack {pack} requires reviewed binding provenance"
                )
            if config.get("ownership_attested") is not True:
                raise ValueError(f"bridge pack {pack} requires ownership_attested=true")
            if policy.container_key is not None:
                container = config.get(policy.container_key)
                if not isinstance(container, str) or not container.strip():
                    raise ValueError(
                        f"bridge pack {pack} requires {policy.container_key}"
                    )
                allowed_containers = _unique_strings(
                    config.get("allowed_container_ids"),
                    label=f"bridge pack {pack} allowed_container_ids",
                    allow_empty=False,
                )
                if container not in allowed_containers:
                    raise ValueError(
                        f"bridge pack {pack} write container must be in the read allowlist"
                    )
                config["allowed_container_ids"] = sorted(allowed_containers)
            scopes = frozenset(
                _unique_strings(
                    config.get("permission_scope"),
                    label=f"bridge pack {pack} permission_scope",
                    allow_empty=not policy.permission_scopes,
                )
            )
            if scopes != policy.permission_scopes:
                raise ValueError(
                    f"bridge pack {pack} permission_scope does not match reviewed policy"
                )
            shared = _unique_strings(
                config.get("shared_container_ids", []),
                label=f"bridge pack {pack} shared_container_ids",
            )
            if shared and not policy.shareable:
                raise ValueError(
                    f"bridge pack {pack} cannot authorize shared containers"
                )
            if policy.container_key is not None and not set(shared) <= set(
                config["allowed_container_ids"]
            ):
                raise ValueError(
                    f"bridge pack {pack} shared containers must be read-authorized"
                )
            config["permission_scope"] = sorted(scopes)
            config["shared_container_ids"] = sorted(shared)
            if pack == "messaging-bluebubbles":
                expected_keys = {
                    "member_id",
                    "provider_account_id",
                    "deployment_target",
                    "workspace",
                    "permission_scope",
                    "binding_provenance",
                    "ownership_attested",
                    "protected_source_id",
                    "allowed_container_ids",
                    "shared_container_ids",
                    "base_url",
                    "credential_ref",
                    "transport_version",
                    "transport_commit",
                    "history_limit",
                    "poll_interval_seconds",
                    "network_containment_verified",
                    "remote_services_verified_disabled",
                    "device_identity_sha256",
                    "bridge_enrollment_id",
                }
                if set(config) != expected_keys:
                    raise ValueError(
                        "Messages bridge config must contain only the reviewed fields"
                    )
                source_id = str(config.get("protected_source_id") or "")
                if re.fullmatch(r"protected-source:[0-9a-f]{64}", source_id) is None:
                    raise ValueError(
                        "Messages bridge requires an opaque protected source"
                    )
                parsed = urlparse(str(config.get("base_url") or ""))
                if (
                    parsed.scheme != "http"
                    or parsed.hostname != "127.0.0.1"
                    or parsed.port is None
                    or parsed.username
                    or parsed.password
                    or parsed.path not in {"", "/"}
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError(
                        "Messages transport must be bound to an exact loopback origin"
                    )
                credential_ref = str(config.get("credential_ref") or "")
                if (
                    PERMISSION_REFERENCE_PATTERN.fullmatch(credential_ref) is None
                    or "bluebubbles" not in credential_ref.casefold()
                ):
                    raise ValueError(
                        "Messages credential must remain in the local Keychain"
                    )
                if (
                    config.get("transport_version") != "1.9.9"
                    or config.get("transport_commit")
                    != "ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1"
                ):
                    raise ValueError(
                        "Messages transport does not match the reviewed pin"
                    )
                history_limit = config.get("history_limit")
                poll_interval = config.get("poll_interval_seconds")
                if (
                    not isinstance(history_limit, int)
                    or isinstance(history_limit, bool)
                    or not 1 <= history_limit <= 1000
                    or not isinstance(poll_interval, int | float)
                    or isinstance(poll_interval, bool)
                    or not 2 <= float(poll_interval) <= 300
                ):
                    raise ValueError("Messages observation bounds are invalid")
                if (
                    re.fullmatch(
                        r"[0-9a-f]{64}", str(config.get("device_identity_sha256") or "")
                    )
                    is None
                ):
                    raise ValueError("Messages bridge device identity is invalid")
                if (
                    config.get("network_containment_verified") is not True
                    or config.get("remote_services_verified_disabled") is not True
                ):
                    raise ValueError(
                        "Messages bridge requires local containment verification"
                    )
                if config.get("bridge_enrollment_id") != enrollment_id:
                    raise ValueError(
                        "Messages bridge enrollment binding is contradictory"
                    )
        normalized[pack] = config
    return normalized


def validate_authorized_pack_config(
    *,
    member_id: str,
    enrollment_id: str,
    authorized_packs: list[str],
    pack_config: dict[str, Any],
) -> dict[str, str]:
    """Compatibility helper returning provider-account coordinates."""

    normalized = validate_configured_pack_config(
        member_id=member_id,
        enrollment_id=enrollment_id,
        configured_packs=authorized_packs,
        pack_config=pack_config,
    )
    return {
        pack: str(config["provider_account_id"])
        for pack, config in normalized.items()
        if BRIDGE_PACK_POLICIES[pack].requires_provider_account
    }


def configuration_from_record(record: Mapping[str, Any]) -> BridgeConfiguration | None:
    if record.get("status") != "active":
        return None
    member_id = record.get("member_id")
    enrollment_id = record.get("enrollment_id")
    packs = record.get("configured_packs", record.get("enabled_packs", []))
    pack_config = record.get("pack_config", {})
    if (
        not isinstance(member_id, str)
        or not member_id
        or not isinstance(enrollment_id, str)
        or not enrollment_id
        or not isinstance(packs, list)
        or not isinstance(pack_config, dict)
    ):
        return None
    try:
        normalized = validate_configured_pack_config(
            member_id=member_id,
            enrollment_id=enrollment_id,
            configured_packs=packs,
            pack_config=pack_config,
        )
    except ValueError:
        return None
    return BridgeConfiguration(
        member_id=member_id,
        enrollment_id=enrollment_id,
        packs=frozenset(packs),
        pack_config=normalized,
    )


def authorization_from_record(record: dict[str, Any]) -> BridgeAuthorization | None:
    configured = configuration_from_record(record)
    if configured is None:
        return None
    packs = record.get("authorized_packs")
    states = record.get("pack_capability_states")
    attestations = record.get("capability_attestations", {})
    if (
        not isinstance(packs, list)
        or any(not isinstance(pack, str) for pack in packs)
        or len(set(packs)) != len(packs)
        or not set(packs) <= configured.packs
        or not isinstance(states, dict)
        or not isinstance(attestations, dict)
    ):
        return None

    event_ids: dict[str, str] = {}
    for pack in packs:
        policy = BRIDGE_PACK_POLICIES[pack]
        expected_state: CapabilityState = (
            "attested" if policy.requires_capability_attestation else "not_required"
        )
        if states.get(pack) != expected_state:
            return None
        if expected_state == "attested":
            receipt = attestations.get(pack)
            if not isinstance(receipt, dict) or not isinstance(
                receipt.get("event_id"), str
            ):
                return None
            event_ids[pack] = receipt["event_id"]

    provider_states = [
        states.get(pack)
        for pack in configured.packs
        if BRIDGE_PACK_POLICIES[pack].requires_capability_attestation
    ]
    expected_global = (
        "not_required"
        if not provider_states
        else "attested"
        if all(state == "attested" for state in provider_states)
        else "partial"
        if any(state == "attested" for state in provider_states)
        else "not_attested"
    )
    if record.get("provider_capability_state") != expected_global:
        return None
    if expected_global == "not_attested" and any(
        BRIDGE_PACK_POLICIES[pack].requires_provider_account for pack in packs
    ):
        return None
    return BridgeAuthorization(
        member_id=configured.member_id,
        enrollment_id=configured.enrollment_id,
        packs=frozenset(packs),
        pack_config=configured.pack_config,
        capability_event_ids=event_ids,
    )


def validate_bridge_record_set(records: Mapping[str, Any]) -> None:
    """Reject ambiguous identities and cross-member Apple account reuse."""

    seen_members: set[str] = set()
    seen_enrollments: set[str] = set()
    account_owners: dict[tuple[str, str], str] = {}
    for identity, value in records.items():
        if not isinstance(identity, str) or not isinstance(value, dict):
            raise ValueError("bridge enrollment mapping is invalid")
        if value.get("status") != "active":
            continue
        configured = configuration_from_record(value)
        if configured is None:
            raise ValueError("active bridge configuration is invalid")
        if configured.member_id in seen_members:
            raise ValueError("active bridge member bindings must be unique")
        if configured.enrollment_id in seen_enrollments:
            raise ValueError("active bridge enrollment identifiers must be unique")
        seen_members.add(configured.member_id)
        seen_enrollments.add(configured.enrollment_id)
        for pack in configured.packs:
            policy = BRIDGE_PACK_POLICIES[pack]
            if not policy.requires_provider_account:
                continue
            account = configured.pack_config[pack]["provider_account_id"]
            key = (str(policy.provider_family), str(account).casefold())
            prior = account_owners.setdefault(key, configured.member_id)
            if prior != configured.member_id:
                raise ValueError(
                    "bridge provider account has contradictory member ownership"
                )


def provider_capability_summary(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"attested": 0, "not_attested": 0, "partial": 0, "not_required": 0}
    for record in records:
        if record.get("status") == "active":
            state = str(record.get("provider_capability_state") or "not_attested")
            if state in counts:
                counts[state] += 1
            else:
                counts["not_attested"] += 1
    return counts


__all__ = [
    "BRIDGE_PACK_POLICIES",
    "PERMISSION_REFERENCE_PATTERN",
    "PROVIDER_ACCOUNT_PATTERN",
    "BridgeAuthorization",
    "BridgeConfiguration",
    "BridgePackPolicy",
    "authorization_from_record",
    "configuration_from_record",
    "provider_capability_summary",
    "validate_authorized_pack_config",
    "validate_bridge_record_set",
    "validate_configured_pack_config",
]
