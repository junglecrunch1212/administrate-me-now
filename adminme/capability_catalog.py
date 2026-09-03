"""Versioned, tenant-neutral capability catalog and per-member state composition.

The catalog describes reviewed code and explicit planned descriptors in this
repository.  It deliberately keeps catalog knowledge separate from
installation, account setup, provider permission, AdministrateMe authority,
OpenClaw exposure, runtime health, trust, and verification.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from adminme.openclaw_topology import member_agent_id
from adminme.openclaw_contract import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.projections.parties.queries import (
    effective_operating_agreements,
    list_service_delegations,
)
from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID


CATALOG_VERSION = "1.2.0"
CATALOG_CONTRACT = "administrateme-capability-catalog-v1"
VERBS = ("read", "observe", "draft", "prepare", "hold", "execute")

_TOOL_PERMISSION_OPTIONS: dict[str, frozenset[str]] = {
    "administrateme_state": frozenset({"read_context"}),
    "administrateme_what_now": frozenset({"read_context"}),
    "administrateme_capabilities": frozenset({"read_context"}),
    "administrateme_settings_context": frozenset({"read_context"}),
    "administrateme_capability_request": frozenset({"read_context"}),
    "administrateme_policy_proposal": frozenset({"read_context"}),
    "administrateme_service_work": frozenset(
        {
            "create_internal_work",
            "manage_member_obligations",
            "coordinate_household_work",
        }
    ),
    "administrateme_service_work_state": frozenset(
        {
            "manage_internal_work",
            "manage_member_obligations",
            "coordinate_household_work",
        }
    ),
    "administrateme_prepare_delegated_action": frozenset({"prepare_external_actions"}),
    "administrateme_execute_delegated_action": frozenset({"execute_fixture_actions"}),
    "administrateme_observe_delegated_action": frozenset({"execute_fixture_actions"}),
}


_DOMAIN_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "capability_id": "domain.member_chat",
        "category": "foundation",
        "label": "Talk with my household CoS",
        "outcome": "Use one household CoS through a private, member-isolated conversation context.",
        "source": "product:member-chat",
        "verbs": {"read": "supported", "observe": "supported"},
        "openclaw_tools": [],
    },
    {
        "capability_id": "domain.household_state",
        "category": "foundation",
        "label": "Read my household state",
        "outcome": "Read only the typed household facts authorized for the active member context.",
        "source": "product:member-surfaces",
        "verbs": {"read": "supported", "observe": "supported"},
        "openclaw_tools": ["administrateme_state"],
    },
    {
        "capability_id": "domain.what_now",
        "category": "planning",
        "label": "Choose what to do next",
        "outcome": "Receive a deterministic, explainable next-action briefing.",
        "source": "product:today",
        "verbs": {"read": "supported", "observe": "supported"},
        "openclaw_tools": ["administrateme_what_now"],
    },
    {
        "capability_id": "domain.source_policy",
        "category": "learning_and_context",
        "label": "Control source intake and context",
        "outcome": "Preview, explain, pause, reset, and decide scoped source-policy proposals.",
        "source": "product:triage",
        "verbs": {
            "read": "supported",
            "observe": "supported",
            "draft": "supported",
            "prepare": "supported",
        },
        "openclaw_tools": [
            "administrateme_settings_context",
            "administrateme_policy_proposal",
        ],
    },
    {
        "capability_id": "domain.operating_agreements",
        "category": "trust_and_authority",
        "label": "Set how my CoS works",
        "outcome": "Review and govern structured private and shared operating agreements.",
        "source": "product:cos-authority",
        "verbs": {
            "read": "supported",
            "observe": "supported",
            "draft": "supported",
            "prepare": "supported",
        },
        "openclaw_tools": ["administrateme_settings_context"],
    },
    {
        "capability_id": "domain.cos_work",
        "category": "planning",
        "label": "Coordinate authorized CoS work",
        "outcome": "Create and advance CoS-owned or CoS-managed work under one exact grant.",
        "source": "product:cos-work",
        "verbs": {
            "read": "supported",
            "observe": "supported",
            "draft": "supported",
            "prepare": "supported",
            "hold": "supported",
        },
        "openclaw_tools": [
            "administrateme_service_work",
            "administrateme_service_work_state",
        ],
    },
    {
        "capability_id": "domain.delegated_fixture_actions",
        "category": "delegated_actions",
        "label": "Prepare delegated provider actions",
        "outcome": "Prepare, hold, execute, observe, and reconcile only the G07 fixture action classes.",
        "source": "product:delegated-execution",
        "verbs": {
            "read": "supported",
            "observe": "fixture_only",
            "draft": "supported",
            "prepare": "fixture_only",
            "hold": "fixture_only",
            "execute": "fixture_only",
        },
        "openclaw_tools": [
            "administrateme_prepare_delegated_action",
            "administrateme_execute_delegated_action",
            "administrateme_observe_delegated_action",
        ],
        "terminal_result_support": True,
        "later_observation_support": True,
        "reconciliation_support": True,
    },
    {
        "capability_id": "domain.capability_settings",
        "category": "foundation",
        "label": "Understand and request capabilities",
        "outcome": "Inspect truthful capability state and record or cancel a secret-free request.",
        "source": "product:settings",
        "verbs": {
            "read": "supported",
            "observe": "supported",
            "draft": "supported",
        },
        "openclaw_tools": [
            "administrateme_capabilities",
            "administrateme_capability_request",
        ],
    },
)


_DOMAIN_IMPLEMENTATION_PATHS = {
    "product:member-chat": "adminme/member_chat.py",
    "product:member-surfaces": "adminme/products/member/surfaces.py",
    "product:today": "adminme/what_now.py",
    "product:triage": "adminme/source_policy.py",
    "product:cos-authority": "adminme/products/member/cos.py",
    "product:cos-work": "adminme/products/member/cos.py",
    "product:delegated-execution": "adminme/delegated_execution.py",
    "product:settings": "adminme/settings_control.py",
}


_OPENCLAW_VERIFIED_SURFACES = (
    "gateway_and_agents",
    "workspaces",
    "sessions_and_account_aware_peers",
    "tools_and_plugin_policy",
    "approval_hooks",
    "skills",
    "standing_orders_and_automations",
    "mcp",
    "browser_and_nodes",
    "doctor",
    "llm-task",
)


_ARTIFACT_OUTCOMES: dict[str, dict[str, Any]] = {
    "automation-home-assistant": {
        "category": "home",
        "label": "Observe Home Assistant",
        "outcome": "Receive reviewed home-state observations; high-risk controls remain unavailable.",
        "sensitivity": ["normal", "sensitive"],
    },
    "calendaring-apple-calendar": {
        "category": "planning",
        "label": "Apple Calendar",
        "outcome": "Observe member-owned Apple calendar state through a separately enrolled bridge.",
        "sensitivity": ["normal", "sensitive"],
    },
    "calendaring-google-calendar": {
        "category": "planning",
        "label": "Google Calendar",
        "outcome": "Observe Google calendar state and prepare G07 fixture calendar representations.",
        "sensitivity": ["normal", "sensitive"],
        "g07_verification": True,
    },
    "contacts-apple-contacts": {
        "category": "people",
        "label": "Apple Contacts",
        "outcome": "Observe member-owned Apple contact records through a separately enrolled bridge.",
        "sensitivity": ["sensitive"],
    },
    "contacts-carddav-contacts": {
        "category": "people",
        "label": "CardDAV Contacts",
        "outcome": "Observe contacts through the reviewed CardDAV adapter when configured.",
        "sensitivity": ["sensitive"],
    },
    "contacts-google-people": {
        "category": "people",
        "label": "Google Contacts",
        "outcome": "Observe contacts through the reviewed Google People adapter when configured.",
        "sensitivity": ["sensitive"],
    },
    "contacts-microsoft-graph-contacts": {
        "category": "people",
        "label": "Microsoft Contacts",
        "outcome": "Observe contacts through the reviewed Microsoft Graph adapter when configured.",
        "sensitivity": ["sensitive"],
    },
    "financial-plaid": {
        "category": "finance",
        "label": "Plaid read-only finance",
        "outcome": "Observe scoped financial accounts and transactions without payment authority.",
        "sensitivity": ["privileged"],
    },
    "messaging-gmail-api": {
        "category": "communications",
        "label": "Gmail",
        "outcome": "Observe an exactly bound member or CoS-owned mailbox through the reviewed adapter.",
        "sensitivity": ["normal", "sensitive", "privileged"],
    },
    "messaging-bluebubbles": {
        "category": "communications",
        "label": "Apple Messages bridge",
        "outcome": (
            "Observe a member-owned Apple Messages source through the pinned "
            "read-only local transport."
        ),
        "sensitivity": ["sensitive", "privileged"],
        "request_key": "apple_messages_read",
    },
    "reminders-apple-eventkit": {
        "category": "planning",
        "label": "Apple Reminders",
        "outcome": "Observe member-owned reminder lists through a separately enrolled bridge.",
        "sensitivity": ["normal", "sensitive"],
    },
    "tasks-google-tasks": {
        "category": "planning",
        "label": "Google Tasks",
        "outcome": "Observe Google Tasks and prepare G07 fixture task representations.",
        "sensitivity": ["normal", "sensitive"],
        "g07_verification": True,
    },
    "calendaring-apple-calendar-bridge": {
        "category": "planning",
        "label": "Apple Calendar bridge",
        "outcome": "Observe Apple Calendar on the member's Mac after pairing and local permission verification.",
        "sensitivity": ["normal", "sensitive"],
        "request_key": "apple_calendar",
    },
    "contacts-apple-contacts-bridge": {
        "category": "people",
        "label": "Apple Contacts bridge",
        "outcome": "Observe Apple Contacts on the member's Mac after pairing and local permission verification.",
        "sensitivity": ["sensitive"],
        "request_key": "apple_contacts",
    },
    "knowledge-apple-notes": {
        "category": "knowledge",
        "label": "Apple Notes bridge",
        "outcome": "Observe authorized Apple Notes exports without treating every note as shared context.",
        "sensitivity": ["normal", "sensitive", "privileged"],
        "request_key": "apple_notes",
    },
    "knowledge-apple-voice-memos": {
        "category": "knowledge",
        "label": "Apple Voice Memos bridge",
        "outcome": "Observe authorized local Voice Memo transcripts through the member bridge.",
        "sensitivity": ["sensitive"],
        "request_key": "apple_voice_memos",
    },
    "knowledge-obsidian": {
        "category": "knowledge",
        "label": "Obsidian bridge",
        "outcome": "Observe authorized files from a member-owned Obsidian vault.",
        "sensitivity": ["normal", "sensitive", "privileged"],
        "request_key": "obsidian",
    },
    "reminders-apple-eventkit-bridge": {
        "category": "planning",
        "label": "Apple Reminders bridge",
        "outcome": "Observe Apple Reminders on the member's Mac after pairing and local permission verification.",
        "sensitivity": ["normal", "sensitive"],
        "request_key": "apple_reminders",
    },
}


_PLANNED_DESCRIPTORS: tuple[dict[str, Any], ...] = (
    {
        "capability_id": "planned.apple_messages_observation",
        "category": "communications",
        "label": "Apple Messages / iMessage",
        "outcome": "Observe authorized Apple Messages through a reviewed member-bridge path.",
        "source": "reviewed-descriptor:G10",
        "source_kind": "reviewed_optional_descriptor",
        "implementation": "superseded_by_bridge.messaging-bluebubbles",
        "implementation_present": True,
        "runtime_location": "member_bridge",
        "verbs": {"read": "supported", "observe": "supported"},
        "prerequisites": [
            "local_helper",
            "member_bridge",
            "member_consent",
            "local_permission",
        ],
        "provider_options": ["messaging-bluebubbles"],
        "unavailable_reason": None,
    },
    {
        "capability_id": "planned.apple_messages_reply",
        "category": "communications",
        "label": "Apple Messages reply",
        "outcome": "Prepare and send a reply only through an accepted, separately approved path.",
        "source": "reviewed-descriptor:G11",
        "source_kind": "reviewed_optional_descriptor",
        "implementation": "superseded_by_bridge.messaging-bluebubbles",
        "implementation_present": True,
        "runtime_location": "member_bridge",
        "verbs": {
            "draft": "member_only",
            "prepare": "member_only",
            "hold": "supported",
            "execute": "ask_every_time",
        },
        "prerequisites": [
            "local_helper",
            "member_bridge",
            "member_consent",
            "openclaw_approval",
        ],
        "provider_options": ["apple_messages_member_bridge"],
        "terminal_result_support": True,
        "later_observation_support": True,
        "reconciliation_support": True,
        "unavailable_reason": None,
    },
)


_BRIDGE_REQUEST_KEYS = {
    "messaging-bluebubbles": "apple_messages_read",
    "calendaring-apple-calendar-bridge": "apple_calendar",
    "reminders-apple-eventkit-bridge": "apple_reminders",
    "contacts-apple-contacts-bridge": "apple_contacts",
    "knowledge-apple-notes": "apple_notes",
    "knowledge-apple-voice-memos": "apple_voice_memos",
    "knowledge-obsidian": "obsidian",
}


_PREREQUISITE_CLASSES = (
    "local_helper",
    "browser",
    "partner_service",
    "commercial_service",
    "human",
)


def _prerequisite_matrix(values: list[str]) -> dict[str, str]:
    """Keep unlike prerequisite classes visible even when none is required."""

    required = set(values)
    return {
        "local_helper": "required" if "local_helper" in required else "not_required",
        "browser": "required" if "browser" in required else "not_required",
        "partner_service": "required"
        if "partner_service" in required
        else "not_required",
        "commercial_service": "required"
        if "commercial_service" in required
        else "not_required",
        "human": "member_consent_required"
        if "member_consent" in required
        else "not_required",
    }


def _verb_matrix(overrides: dict[str, str] | None = None) -> dict[str, str]:
    values = {verb: "unsupported" for verb in VERBS}
    values.update(overrides or {})
    return values


def _read_manifest(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise RuntimeError(f"capability manifest is not a mapping: {path}")
    return value


def _central_definitions(repository_root: Path) -> list[dict[str, Any]]:
    root = repository_root / "packs" / "adapters"
    definitions: list[dict[str, Any]] = []
    for path in sorted(root.rglob("pack.yaml")):
        manifest = _read_manifest(path)
        pack = manifest.get("pack") or {}
        if not isinstance(pack, dict):
            raise RuntimeError(f"capability pack block is invalid: {path}")
        raw_id = str(pack.get("id") or "")
        artifact_id = raw_id.removeprefix("adapter:")
        descriptor = _ARTIFACT_OUTCOMES.get(artifact_id)
        if descriptor is None:
            raise RuntimeError(
                f"adapter lacks a reviewed capability descriptor: {raw_id}"
            )
        runtime = manifest.get("runtime") or {}
        entrypoint = runtime.get("entrypoint") if isinstance(runtime, dict) else None
        implementation_present = bool(
            (isinstance(entrypoint, str) and (path.parent / entrypoint).is_file())
            or (path.parent / "adapter.py").is_file()
        )
        capabilities = [
            value
            for value in manifest.get("capabilities") or []
            if isinstance(value, dict)
        ]
        emitted = [str(value) for value in manifest.get("events_emitted") or []]
        emitted.extend(
            str(value)
            for capability in capabilities
            for value in capability.get("event_families") or []
        )
        can_observe = bool(emitted)
        declared_runtimes = {
            str(capability.get("runtime"))
            for capability in capabilities
            if capability.get("runtime")
        }
        deployment_targets = {
            str(value) for value in manifest.get("deployment_targets") or []
        }
        runtime_location = (
            "member_bridge"
            if declared_runtimes == {"bridge"} or deployment_targets == {"bridge"}
            else "central"
        )
        g07 = descriptor.get("g07_verification") is True
        verbs = _verb_matrix(
            {
                "read": "supported" if can_observe else "unsupported",
                "observe": "supported" if can_observe else "unsupported",
                "draft": "supported" if g07 else "unsupported",
                "prepare": "fixture_only" if g07 else "unsupported",
                "hold": "fixture_only" if g07 else "unsupported",
                "execute": "fixture_only" if g07 else "unsupported",
            }
        )
        definitions.append(
            {
                "capability_id": f"adapter.{artifact_id}",
                **copy.deepcopy(descriptor),
                "source": str(path.relative_to(repository_root)),
                "source_kind": "adapter_manifest",
                "artifact_id": artifact_id,
                "artifact_version": str(pack.get("version") or "unknown"),
                "implementation": "reviewed_connector_present"
                if implementation_present
                else "unsupported",
                "implementation_present": implementation_present,
                "owner_kinds": list(
                    manifest.get("supported_owner_kinds") or ["member"]
                ),
                "runtime_location": runtime_location,
                "verbs": verbs,
                "provider_options": [artifact_id],
                "prerequisites": (
                    [
                        "local_helper",
                        "member_bridge",
                        "member_consent",
                        "local_permission",
                    ]
                    if runtime_location == "member_bridge"
                    else [
                        "member_consent",
                        "provider_account",
                        "partner_service",
                        *(
                            ["commercial_service"]
                            if artifact_id == "financial-plaid"
                            else []
                        ),
                    ]
                ),
                "terminal_result_support": g07,
                "later_observation_support": g07,
                "reconciliation_support": g07,
            }
        )
    return definitions


def _bridge_definitions(repository_root: Path) -> list[dict[str, Any]]:
    root = repository_root / "bridge" / "adminme_bridge" / "connector_packs"
    definitions: list[dict[str, Any]] = []
    for path in sorted(root.rglob("pack.yaml")):
        manifest = _read_manifest(path)
        pack = manifest.get("pack") or {}
        if not isinstance(pack, dict):
            raise RuntimeError(f"bridge capability pack block is invalid: {path}")
        artifact_id = str(pack.get("name") or "")
        descriptor = _ARTIFACT_OUTCOMES.get(artifact_id)
        if descriptor is None:
            raise RuntimeError(
                f"bridge pack lacks a reviewed capability descriptor: {artifact_id}"
            )
        entrypoint = manifest.get("entrypoint")
        module_name = str(entrypoint or "").partition(":")[0]
        module_path = (
            repository_root / "bridge" / (module_name.replace(".", "/") + ".py")
        )
        package_path = (
            repository_root / "bridge" / module_name.replace(".", "/") / "__init__.py"
        )
        implementation_present = bool(entrypoint) and (
            module_path.is_file() or package_path.is_file()
        )
        writes = [str(value) for value in manifest.get("write_capabilities") or []]
        assurance_modes = {
            str(value.get("mode"))
            for value in manifest.get("source_assurance") or []
            if isinstance(value, dict)
        }
        definitions.append(
            {
                "capability_id": f"bridge.{artifact_id}",
                **copy.deepcopy(descriptor),
                "source": str(path.relative_to(repository_root)),
                "source_kind": "bridge_manifest",
                "artifact_id": artifact_id,
                "artifact_version": str(pack.get("version") or "unknown"),
                "implementation": "reviewed_connector_present"
                if implementation_present
                else "unsupported",
                "implementation_present": implementation_present,
                "owner_kinds": ["member"],
                "runtime_location": "member_bridge",
                "verbs": _verb_matrix(
                    {
                        "read": "supported",
                        "observe": "supported",
                        **(
                            {
                                "draft": "supported",
                                "prepare": "supported",
                                "hold": "supported",
                            }
                            if writes
                            else {}
                        ),
                        **(
                            {"execute": "ask_every_time"}
                            if artifact_id == "messaging-bluebubbles" and writes
                            else {}
                        ),
                    }
                ),
                "provider_options": [artifact_id],
                "prerequisites": [
                    "local_helper",
                    "member_bridge",
                    "member_consent",
                    "local_permission",
                ],
                "terminal_result_support": bool(
                    artifact_id == "messaging-bluebubbles" and writes
                ),
                "later_observation_support": True,
                "reconciliation_support": "candidate_assessment" in assurance_modes,
                "request_key": _BRIDGE_REQUEST_KEYS[artifact_id],
            }
        )
    return definitions


def _openclaw_manifest(repository_root: Path) -> tuple[dict[str, Any], set[str]]:
    path = repository_root / "openclaw" / "administrateme" / "openclaw.plugin.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError("AdministrateMe OpenClaw manifest is invalid")
    tools = (manifest.get("contracts") or {}).get("tools") or []
    if not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools):
        raise RuntimeError("AdministrateMe OpenClaw tool contract is invalid")
    return manifest, set(tools)


def load_catalog(repository_root: Path) -> dict[str, Any]:
    """Load and validate only reviewed repository artifacts and descriptors."""

    root = repository_root.resolve()
    plugin_manifest, plugin_tools = _openclaw_manifest(root)
    versions = json.loads(
        (root / "bootstrap" / "versions.json").read_text(encoding="utf-8")
    )
    host_pin = versions.get("openclaw") or {}
    plugin_pin = versions.get("administrateme") or {}
    if (
        host_pin.get("version") != OPENCLAW_VERSION
        or host_pin.get("commit") != OPENCLAW_COMMIT
        or plugin_pin.get("plugin_package_version") != plugin_manifest.get("version")
    ):
        raise RuntimeError("capability catalog OpenClaw pins are inconsistent")
    definitions = [copy.deepcopy(value) for value in _DOMAIN_DEFINITIONS]
    definitions.extend(_central_definitions(root))
    definitions.extend(_bridge_definitions(root))
    definitions.extend(copy.deepcopy(value) for value in _PLANNED_DESCRIPTORS)
    ids: set[str] = set()
    for definition in definitions:
        capability_id = definition.get("capability_id")
        if (
            not isinstance(capability_id, str)
            or not capability_id
            or capability_id in ids
        ):
            raise RuntimeError("capability ids must be stable and unique")
        ids.add(capability_id)
        definition["catalog_version"] = CATALOG_VERSION
        definition["owner_kinds"] = list(definition.get("owner_kinds") or ["member"])
        definition["verbs"] = _verb_matrix(definition.get("verbs"))
        definition["implementation_present"] = bool(
            definition.get(
                "implementation_present", definition.get("implementation") is None
            )
        )
        definition.setdefault("implementation", "built_in")
        definition.setdefault("runtime_location", "central")
        definition.setdefault("source_kind", "domain_product")
        definition.setdefault("provider_options", ["administrateme"])
        definition.setdefault("prerequisites", [])
        definition["prerequisite_classes"] = _prerequisite_matrix(
            list(definition["prerequisites"])
        )
        if set(definition["prerequisite_classes"]) != set(_PREREQUISITE_CLASSES):
            raise RuntimeError(
                f"capability {capability_id} has incomplete prerequisites"
            )
        definition.setdefault("sensitivity", ["normal", "sensitive"])
        definition.setdefault("terminal_result_support", False)
        definition.setdefault("later_observation_support", False)
        definition.setdefault("reconciliation_support", False)
        if definition["source_kind"] == "domain_product":
            implementation_path = _DOMAIN_IMPLEMENTATION_PATHS.get(
                str(definition.get("source") or "")
            )
            if (
                implementation_path is None
                or not (root / implementation_path).is_file()
            ):
                raise RuntimeError(
                    f"domain capability {capability_id} lacks reviewed implementation"
                )
            definition["implementation_path"] = implementation_path
        declared = set(definition.get("openclaw_tools") or [])
        missing = declared - plugin_tools
        if missing:
            raise RuntimeError(
                f"capability {capability_id} names undeclared OpenClaw tools: {sorted(missing)}"
            )
    return {
        "contract": CATALOG_CONTRACT,
        "catalog_version": CATALOG_VERSION,
        "tenant_neutral": True,
        "source_classes": [
            "domain_products",
            "adapter_manifests",
            "member_bridge_manifests",
            "exact_pinned_openclaw_plugin",
            "reviewed_optional_descriptors",
        ],
        "openclaw_plugin": {
            "id": plugin_manifest.get("id"),
            "plugin_version": plugin_manifest.get("version"),
            "host_version": OPENCLAW_VERSION,
            "host_commit": OPENCLAW_COMMIT,
            "source_archive_sha256": host_pin.get("source_archive_sha256"),
            "proof": "exact_pinned_source_and_contract_fixture",
            "newer_documentation_is_proof": False,
            "verified_surfaces": list(_OPENCLAW_VERIFIED_SURFACES),
            "tools": sorted(plugin_tools),
        },
        "items": sorted(definitions, key=lambda value: str(value["capability_id"])),
    }


def _configured_adapter(
    runtime: Any, artifact_id: str, member_id: str
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for index, row in enumerate(runtime.settings.adapter_config.get("adapters", [])):
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "").removeprefix("adapter:")
        config = row.get("config") or {}
        if kind != artifact_id or not isinstance(config, dict):
            continue
        owner = config.get("member_id")
        if owner not in {None, member_id}:
            continue
        matches.append(
            {**row, "connection_id": str(row.get("id") or f"adapter-{index}")}
        )
    return matches[0] if len(matches) == 1 else None


def _bridge_record(
    runtime: Any, member_id: str
) -> tuple[str | None, dict[str, Any] | None]:
    matches = [
        (identity, record)
        for identity, record in runtime.settings.bridge_enrollments.items()
        if isinstance(record, dict) and record.get("member_id") == member_id
    ]
    return matches[0] if len(matches) == 1 else (None, None)


def _active_authority(
    runtime: Any, session: Any
) -> tuple[list[dict[str, Any]], set[str]]:
    if runtime.projections is None or session.auth_member_id != session.view_member_id:
        return [], set()
    grants = list_service_delegations(
        runtime.projections.connection("parties"),
        session,
        service_principal_id=COS_SERVICE_PRINCIPAL_ID,
    )
    permissions = {
        str(permission)
        for grant in grants
        if grant.get("active") is True
        and grant.get("context_member_id") == session.auth_member_id
        for permission in grant.get("permissions") or []
    }
    return grants, permissions


def _effective_trust(
    runtime: Any, session: Any
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if runtime.projections is None:
        return [], {}
    agreements = effective_operating_agreements(
        runtime.projections.connection("parties"),
        session,
        as_of_iso=datetime.now(timezone.utc).isoformat(),
    )
    if session.auth_member_id != session.view_member_id:
        agreements = [
            value
            for value in agreements
            if value.get("authority_scope") == "household_shared"
        ]
    stages: dict[str, str] = {}
    for agreement in agreements:
        rule = agreement.get("rule") or {}
        trust = rule.get("trust_scope") or {}
        stage = str(trust.get("stage") or "observe")
        for capability_id in trust.get("capability_ids") or []:
            stages[str(capability_id)] = stage
    return agreements, stages


def _bridge_state(
    runtime: Any,
    member_id: str,
    definition: dict[str, Any],
    settings_state: dict[str, Any],
) -> dict[str, Any]:
    identity, record = _bridge_record(runtime, member_id)
    artifact_id = str(definition["artifact_id"])
    request_key = str(definition["request_key"])
    request = (settings_state.get("bridge_requests") or {}).get(member_id) or {}
    requested = request_key in set(request.get("capabilities") or [])
    status = str((record or {}).get("status") or "enrollment_not_requested")
    configured = set((record or {}).get("configured_packs") or [])
    authorized = set((record or {}).get("authorized_packs") or [])
    active_adapters = set((record or {}).get("adapters_active") or [])
    source_adapter = artifact_id.removesuffix("-bridge")
    package_ready = status in {"package-created-not-live", "package-exported", "active"}
    paired = status == "active"
    permission_verified = bool(
        paired
        and artifact_id in authorized
        and (record or {}).get("provider_capability_state") == "attested"
    )
    adapter_authenticated = paired and (
        artifact_id in active_adapters or source_adapter in active_adapters
    )
    observation_active = bool(adapter_authenticated and runtime._observation_active)
    revoked = status == "revoked"
    unsupported = not definition.get("implementation_present")
    local_permission = (
        "verified"
        if permission_verified
        else "required"
        if requested or artifact_id in configured or artifact_id in authorized
        else "not_requested"
    )
    return {
        "bridge_software_available": bool(definition.get("implementation_present")),
        "compatible_package_available": bool(definition.get("implementation_present")),
        "enrollment": "requested" if requested else status,
        "package": "ready" if package_ready else "not_ready",
        "device_pairing": "paired" if paired else "not_paired",
        "tailscale_identity": "verified" if paired and bool(identity) else "unverified",
        "capability_request": "requested"
        if requested or artifact_id in configured or artifact_id in authorized
        else "not_requested",
        "local_permission": local_permission,
        "adapter_authentication": "authenticated"
        if adapter_authenticated
        else "not_authenticated",
        "observation": "active" if observation_active else "inactive",
        "source_settlement": "settled"
        if observation_active and bool((record or {}).get("activated_at"))
        else "unsettled",
        "degraded": bool(paired and not permission_verified),
        "paused": status == "paused",
        "revoked": revoked,
        "unsupported": unsupported,
        "effective_readiness": "unsupported"
        if unsupported
        else "revoked"
        if revoked
        else "permission_required"
        if paired and not permission_verified
        else "healthy"
        if observation_active
        else "pairing_required"
        if requested or package_ready
        else "available_to_request",
    }


def compose_member_catalog(
    runtime: Any,
    session: Any,
    *,
    settings_state: dict[str, Any],
) -> dict[str, Any]:
    """Overlay member-scoped effective state without mutating catalog truth."""

    catalog = load_catalog(runtime.settings.repository_root)
    member_id = session.view_member_id
    viewing_as = session.auth_member_id != member_id
    grants, permissions = _active_authority(runtime, session)
    agreements, trust_stages = _effective_trust(runtime, session)
    plugin_tools = set(catalog["openclaw_plugin"]["tools"])
    member_requests = (
        {}
        if viewing_as
        else (settings_state.get("capability_requests") or {}).get(member_id) or {}
    )
    openclaw_state = runtime.effective_openclaw_state()
    rows: list[dict[str, Any]] = []
    for definition in catalog["items"]:
        row = copy.deepcopy(definition)
        capability_id = str(row["capability_id"])
        implementation_present = bool(row["implementation_present"])
        configured: dict[str, Any] | None = None
        bridge_state: dict[str, Any] | None = None
        if (
            row["source_kind"] == "adapter_manifest"
            and row["runtime_location"] == "central"
            and not viewing_as
        ):
            configured = _configured_adapter(
                runtime, str(row["artifact_id"]), member_id
            )
        elif row["source_kind"] == "bridge_manifest" and not viewing_as:
            bridge_state = _bridge_state(runtime, member_id, row, settings_state)
        request = member_requests.get(capability_id)
        requested = isinstance(request, dict) and request.get("state") == "requested"
        paused = False
        runtime_state = "healthy" if runtime.started else "starting"
        freshness = "current" if runtime.started else "unknown"
        setup = "not_required"
        provider_permission = "not_required"
        installation = "installed" if implementation_present else "absent"
        if configured is not None:
            connection_id = str(configured["connection_id"])
            lifecycle = (settings_state.get("connections") or {}).get(
                connection_id
            ) or {}
            paused = (
                lifecycle.get("state") == "paused" or configured.get("enabled") is False
            )
            installation = "installed"
            config = configured.get("config") or {}
            setup = "configured" if config else "setup_incomplete"
            provider_permission = (
                "reviewed_scope_declared"
                if config.get("permission_scope")
                else "provider_grant_unknown"
            )
            inventory = (
                runtime.adapters.inventory() if runtime.adapters is not None else {}
            )
            live = inventory.get(connection_id) or {}
            observed_runtime_state = str(live.get("state") or "unknown")
            runtime_state = (
                "paused"
                if paused
                else "healthy"
                if observed_runtime_state == "running"
                else observed_runtime_state
            )
            last_success = live.get("last_success_at") or live.get(
                "last_cursor_advance_at"
            )
            freshness = "current" if last_success else "unknown"
            if runtime_state == "healthy" and freshness == "current":
                provider_permission = "runtime_read_verified"
        elif row["source_kind"] == "adapter_manifest":
            if row["runtime_location"] == "member_bridge":
                installation = "requires_member_bridge"
                setup = "use_member_bridge_capability"
                provider_permission = "local_permission_required"
                runtime_state = "not_applicable_centrally"
                freshness = "unknown"
            else:
                setup = "member_consent_required"
                provider_permission = "no_credential"
                runtime_state = "not_configured"
                freshness = "unknown"
        if bridge_state is not None:
            installation = (
                "installed"
                if bridge_state["package"] == "ready"
                else "requested"
                if bridge_state["capability_request"] == "requested"
                else "absent"
            )
            setup = bridge_state["device_pairing"]
            provider_permission = bridge_state["local_permission"]
            runtime_state = (
                "degraded" if bridge_state["degraded"] else bridge_state["observation"]
            )
            freshness = (
                "current"
                if bridge_state["source_settlement"] == "settled"
                else "unknown"
            )
        if requested and installation == "absent":
            installation = "requested"
        planned = str(row["implementation"]).startswith("planned_")
        if planned:
            setup = "unavailable"
            runtime_state = "stopped"
            provider_permission = "not_available"
            freshness = "unknown"

        read_supported = row["verbs"]["read"] in {"supported", "fixture_only"}
        execute_supported = row["verbs"]["execute"] == "fixture_only"
        read_available = bool(
            implementation_present
            and not paused
            and not planned
            and (
                bridge_state is None or bridge_state["effective_readiness"] == "healthy"
            )
            and (
                row["source_kind"] != "adapter_manifest"
                or (
                    configured is not None
                    and runtime_state == "healthy"
                    and freshness == "current"
                )
            )
        )
        declared_tools = sorted(set(row.get("openclaw_tools") or []) & plugin_tools)
        required_permissions = set().union(
            *(
                _TOOL_PERMISSION_OPTIONS.get(tool, frozenset())
                for tool in declared_tools
            )
        )
        granted_permissions = required_permissions & permissions
        if required_permissions:
            authority_state = (
                "granted"
                if required_permissions <= permissions
                else "partial_grant"
                if granted_permissions
                else "exact_grant_required"
            )
        elif row["source_kind"] == "domain_product":
            authority_state = "member_session_authorized"
        elif configured is not None or (
            bridge_state is not None
            and bridge_state["effective_readiness"] == "healthy"
        ):
            authority_state = "owner_scope_bound"
        else:
            authority_state = "member_consent_required"
        execute_authorized = bool(
            execute_supported
            and "execute_fixture_actions" in permissions
            and openclaw_state == "healthy"
            and not runtime._observation_active
            and not paused
        )
        effective_tools = [
            tool
            for tool in declared_tools
            if read_available
            and bool(_TOOL_PERMISSION_OPTIONS.get(tool, frozenset()) & permissions)
        ]
        member_consent_required = "member_consent" in set(row["prerequisites"])
        member_consent_complete = bool(
            not member_consent_required
            or provider_permission in {"runtime_read_verified", "verified"}
        )
        row.update(
            {
                "catalog": "known",
                "installation": installation,
                "account_setup": setup,
                "provider_permission": provider_permission,
                "administrateme_authority": {
                    "state": authority_state,
                    "required_permissions": sorted(required_permissions),
                    "granted_permissions": sorted(granted_permissions),
                    "grant_ids": [
                        grant["grant_id"]
                        for grant in grants
                        if grant.get("active") is True
                    ],
                    "execute_authorized": execute_authorized,
                },
                "openclaw": {
                    "plugin_declared": bool(declared_tools),
                    "runtime_state": openclaw_state,
                    "declared_tools": declared_tools,
                    "effective_tools": effective_tools,
                    "approval_required_for_side_effects": any(
                        tool
                        not in {
                            "administrateme_state",
                            "administrateme_what_now",
                            "administrateme_capabilities",
                            "administrateme_settings_context",
                        }
                        for tool in declared_tools
                    ),
                },
                "runtime_health": runtime_state,
                "freshness": freshness,
                "trust_stage": trust_stages.get(capability_id, "not_established"),
                "verification": {
                    "terminal_result_support": bool(row["terminal_result_support"]),
                    "later_observation_support": bool(row["later_observation_support"]),
                    "reconciliation_support": bool(row["reconciliation_support"]),
                    "last_verified_result": None,
                },
                "consent": {
                    "member_required": member_consent_required,
                    "member_complete": member_consent_complete,
                    "member_state": "complete"
                    if member_consent_complete
                    else "required",
                    "admin_action_required": row["source_kind"]
                    in {"adapter_manifest", "bridge_manifest"}
                    and not read_available,
                    "household_approval_required": False,
                    "household_approval_policy": "required_for_household_shared"
                    if capability_id
                    in {
                        "domain.operating_agreements",
                        "domain.cos_work",
                        "domain.delegated_fixture_actions",
                    }
                    else "not_required",
                    "admin_may_complete_member_consent": False,
                },
                "required_actions": {
                    "member": "complete_consent_or_request"
                    if row["source_kind"] in {"adapter_manifest", "bridge_manifest"}
                    else "none",
                    "admin": "make_reviewed_option_available"
                    if row["source_kind"] in {"adapter_manifest", "bridge_manifest"}
                    and not read_available
                    else "none",
                    "household": "approve_if_household_shared"
                    if capability_id
                    in {
                        "domain.operating_agreements",
                        "domain.cos_work",
                        "domain.delegated_fixture_actions",
                    }
                    else "none",
                },
                "effective": {
                    "read_available": read_supported and read_available,
                    "execute_available": execute_authorized,
                    "paused": paused,
                    "requested": requested,
                    "view_as_read_only": viewing_as,
                },
                "bridge_state": bridge_state,
                "request": copy.deepcopy(request)
                if isinstance(request, dict)
                else None,
                "unavailable_reason": row.get("unavailable_reason")
                or (
                    "This adapter implementation is available only through its reviewed member-bridge capability."
                    if row["source_kind"] == "adapter_manifest"
                    and row["runtime_location"] == "member_bridge"
                    else "The compatible member-bridge package is not paired with verified local permission."
                    if bridge_state is not None and not read_available
                    else "A reviewed provider adapter is present but this member has not completed setup."
                    if row["source_kind"] == "adapter_manifest" and configured is None
                    else "Observation mode or exact authority prevents execution."
                    if execute_supported and not execute_authorized
                    else None
                ),
                "prohibited_reason": (
                    "Live provider execution is not authorized by G08."
                    if execute_supported and not execute_authorized
                    else None
                ),
            }
        )
        rows.append(row)
    attention = [
        {
            "capability_id": row["capability_id"],
            "label": row["label"],
            "reason": row["unavailable_reason"],
        }
        for row in rows
        if row["request"] is not None
        and row["request"].get("state") == "requested"
        and not row["effective"]["read_available"]
    ]
    return {
        **{key: value for key, value in catalog.items() if key != "items"},
        "member_id": member_id,
        "member_agent_id": member_agent_id(member_id),
        "same_cos_identity": {
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "party_id": COS_PARTY_ID,
        },
        "items": rows,
        "summary": {
            "known": len(rows),
            "implemented": sum(bool(row["implementation_present"]) for row in rows),
            "installed": sum(row["installation"] == "installed" for row in rows),
            "read_available": sum(row["effective"]["read_available"] for row in rows),
            "execute_available": sum(
                row["effective"]["execute_available"] for row in rows
            ),
            "degraded": sum(row["runtime_health"] == "degraded" for row in rows),
            "attention": len(attention),
        },
        "attention": attention,
        "agreements_considered": [
            {
                "agreement_id": value["agreement_id"],
                "revision": value["revision"],
                "layer": value["layer"],
                "effective_state": value["effective_state"],
            }
            for value in agreements
        ],
        "catalog_presence_grants_nothing": True,
        "provider_effect_count": 0,
    }


def catalog_item(catalog: dict[str, Any], capability_id: str) -> dict[str, Any] | None:
    return next(
        (
            value
            for value in catalog.get("items") or []
            if value.get("capability_id") == capability_id
        ),
        None,
    )


__all__ = [
    "CATALOG_CONTRACT",
    "CATALOG_VERSION",
    "VERBS",
    "catalog_item",
    "compose_member_catalog",
    "load_catalog",
]
