"""Ten-section encrypted, resumable, idempotent bootstrap wizard."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import os
import plistlib
import secrets
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bootstrap.contracts import (
    OPENCLAW_LABEL,
    RUNTIME_LABEL,
    SECTION_NAMES,
    DeploymentPaths,
    atomic_write,
    load_versions,
    repository_root,
)
from bootstrap.preflight import PreflightFailure, public_checks, run_preflight
from bootstrap.secrets import reference_for, secret_store
from bootstrap.state import EncryptedStateStore, new_state, now_utc


class BootstrapInterrupted(RuntimeError):
    """Test/rehearsal interruption that leaves a resumable journal."""


class WizardFailure(RuntimeError):
    pass


def install_launch_agents(
    paths: DeploymentPaths,
    root: Path,
    *,
    test_mode: bool = False,
) -> None:
    """Write validated user LaunchAgents for one immutable release."""
    python_path = str(root / ".venv" / "bin" / "python")
    if test_mode:
        python_path = shutil.which("python3") or shutil.which("python") or "python3"
    elif not Path(python_path).is_file():
        raise WizardFailure(f"release Python environment is missing: {python_path}")
    common_environment = {
        "ADMINME_INSTANCE_ROOT": str(paths.root),
        "ADMINME_REPOSITORY_ROOT": str(root),
        "PATH": (
            "/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:"
            "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        ),
    }
    definitions = {
        RUNTIME_LABEL: {
            "Label": RUNTIME_LABEL,
            "ProgramArguments": [python_path, "-m", "bootstrap.runtime_service"],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 10,
            "EnvironmentVariables": common_environment,
            "StandardOutPath": str(paths.log_dir / "runtime.log"),
            "StandardErrorPath": str(paths.log_dir / "runtime-error.log"),
        },
        OPENCLAW_LABEL: {
            "Label": OPENCLAW_LABEL,
            "ProgramArguments": [python_path, "-m", "bootstrap.openclaw_service"],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 10,
            "EnvironmentVariables": common_environment,
            "StandardOutPath": str(paths.log_dir / "openclaw.log"),
            "StandardErrorPath": str(paths.log_dir / "openclaw-error.log"),
        },
    }
    for label, definition in definitions.items():
        payload = plistlib.dumps(definition, fmt=plistlib.FMT_XML, sort_keys=True)
        target = paths.launch_agent_dir / f"{label}.plist"
        atomic_write(target, payload, mode=0o600)
        plistlib.loads(target.read_bytes())
        if not test_mode and shutil.which("plutil"):
            subprocess.run(["plutil", "-lint", str(target)], check=True)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _fixture_answers(name: str | None) -> dict[str, Any]:
    if name != "two-adult-one-child":
        raise WizardFailure(f"unknown bootstrap fixture: {name}")
    return {
        "assistant": {"persona": "quiet_assistant", "identity": "Household Assistant"},
        "household": {
            "kind": "apple",
            "members": [
                {
                    "member_id": "member_fixture_principal_a",
                    "role": "principal",
                    "label": "Principal A",
                },
                {
                    "member_id": "member_fixture_principal_b",
                    "role": "principal",
                    "label": "Principal B",
                },
                {
                    "member_id": "member_fixture_child",
                    "role": "child",
                    "label": "Child Member",
                },
            ],
            "ambient_members": [],
            "coparents": [
                "member_fixture_principal_a",
                "member_fixture_principal_b",
            ],
            "helpers": [],
        },
        "profiles": {
            "member_fixture_principal_a": "adhd_executive",
            "member_fixture_principal_b": "minimalist_parent",
            "member_fixture_child": "kid_scoreboard",
        },
        "providers": {"assistant": "fixture", "optional": []},
        "plaid": {"selected": True, "environment": "sandbox", "fake_link": True},
        "seed": {
            "places": True,
            "assets": False,
            "accounts": False,
            "recurrences": True,
            "providers": False,
            "schools": False,
            "projects": True,
            "vendors": False,
            "crm_relationships": True,
        },
        "integrations": {
            "shared_lists": True,
            "calendars": False,
            "contacts": False,
            "home_assistant": False,
            "fixture_channel": True,
            "openclaw_actor_identities": [
                {
                    "channel_id": "fixture-channel",
                    "account_id": "fixture-account",
                    "actor_id": "fixture-actor",
                    "member_id": "member_fixture_principal_a",
                }
            ],
        },
        "bridges": {"apple": True, "obsidian": False},
    }


class BootstrapWizard:
    """Run each constitutional section as one separately journaled transaction."""

    SECTION_VERSIONS = {str(index): 1 for index in range(1, 11)}

    def __init__(
        self,
        paths: DeploymentPaths,
        *,
        root: Path | None = None,
        fixture: str | None = None,
        test_mode: bool = False,
        dry_run: bool = False,
        preflight_overrides: dict[str, str] | None = None,
        answers: dict[str, Any] | None = None,
    ) -> None:
        self.paths = paths
        self.root = (root or repository_root()).resolve()
        self.fixture = fixture
        self.test_mode = test_mode
        self.dry_run = dry_run
        self.preflight_overrides = dict(preflight_overrides or {})
        self.answers = answers if answers is not None else _fixture_answers(fixture)
        self._validate_answers(self.answers)
        self.store = secret_store(paths, test_mode=test_mode)

    @staticmethod
    def _validate_answers(answers: dict[str, Any]) -> None:
        required = {
            "assistant",
            "household",
            "profiles",
            "providers",
            "plaid",
            "seed",
            "integrations",
            "bridges",
        }
        if set(answers) != required:
            raise WizardFailure("bootstrap answers do not match the versioned section schema")
        rendered_keys = " ".join(_walk_keys(answers)).lower()
        if any(word in rendered_keys for word in ("password", "api_key", "token", "secret")):
            raise WizardFailure("bootstrap answers must contain references, never secret values")
        members = answers["household"].get("members", [])
        if not members or not any(member.get("role") == "principal" for member in members):
            raise WizardFailure("household requires at least one principal")
        member_ids = [member.get("member_id") for member in members]
        if len(member_ids) != len(set(member_ids)) or not all(member_ids):
            raise WizardFailure("member ids must be present and unique")
        roles = {member["member_id"]: member.get("role") for member in members}
        if not set(roles.values()) <= {"principal", "child", "helper", "ambient"}:
            raise WizardFailure("household contains an unsupported member role")
        expected_roles = {
            "ambient_members": "ambient",
            "coparents": "principal",
            "helpers": "helper",
        }
        for group, expected_role in expected_roles.items():
            group_ids = answers["household"].get(group, [])
            if len(group_ids) != len(set(group_ids)) or any(
                roles.get(member_id) != expected_role for member_id in group_ids
            ):
                raise WizardFailure(f"{group} must reference unique {expected_role} members")
        if set(answers["profiles"]) != set(member_ids):
            raise WizardFailure("every household member requires exactly one profile")

    def _state_store(self) -> EncryptedStateStore:
        reference = reference_for(
            self.paths,
            "bootstrap-state",
            test_mode=self.test_mode,
        )
        return EncryptedStateStore(self.paths.state_path, self.store.get(reference))

    def _initialize(self) -> tuple[dict[str, Any], EncryptedStateStore]:
        existed = self.paths.root.exists()
        existing_children = {path.name for path in self.paths.root.iterdir()} if existed else set()
        if existing_children - {"app"}:
            raise WizardFailure(
                "deployment root is non-empty but has no resumable bootstrap state"
            )
        mutable_paths = (
            self.paths.instance_dir,
            self.paths.log_dir,
            self.paths.backup_dir,
            self.paths.rollback_dir,
            self.paths.launch_agent_dir,
            self.paths.run_dir,
        )
        existed_before = {path: path.exists() for path in mutable_paths}
        created_references: list[str] = []
        try:
            checks = run_preflight(
                self.paths.root,
                system=self.preflight_overrides.get("system"),
                machine=self.preflight_overrides.get("machine"),
                macos_version=self.preflight_overrides.get("macos_version"),
                require_dependencies=not self.test_mode,
                check_filevault=not self.test_mode,
            )
            self.paths.create()
            bootstrap_reference = self.store.put("bootstrap-state", os.urandom(32))
            created_references.append(bootstrap_reference)
            event_reference = self.store.put("event-log", os.urandom(32))
            created_references.append(event_reference)
            backup_reference = self.store.put("backup", os.urandom(32))
            created_references.append(backup_reference)
            state = new_state(
                fixture=self.fixture,
                secret_provider=self.store.provider_name,
            )
            state["answers"] = self.answers
            state["secret_references"].update(
                {
                    "bootstrap_state": bootstrap_reference,
                    "event_log": event_reference,
                    "backup": backup_reference,
                }
            )
            state_store = EncryptedStateStore(
                self.paths.state_path,
                self.store.get(bootstrap_reference),
            )
            state["preflight"] = public_checks(checks)
            self._complete_section(state, 1, {"checks": len(checks)})
            state_store.save(state)
            return state, state_store
        except Exception:
            for reference in reversed(created_references):
                self.store.delete(reference)
            for path in sorted(mutable_paths, key=lambda item: len(item.parts), reverse=True):
                if existed_before[path] or not path.exists():
                    continue
                if path.is_dir():
                    if path == self.paths.launch_agent_dir:
                        # This is a shared macOS user directory, not an
                        # AdministrateMe-owned tree. Remove it only if the
                        # failed preflight transaction left it empty.
                        try:
                            path.rmdir()
                        except OSError:
                            pass
                    else:
                        shutil.rmtree(path)
                else:
                    path.unlink()
            if not existed and self.paths.root.exists() and not any(self.paths.root.iterdir()):
                self.paths.root.rmdir()
            raise

    def load_or_initialize(self) -> tuple[dict[str, Any], EncryptedStateStore]:
        if not self.paths.state_path.exists():
            return self._initialize()
        state_store = self._state_store()
        state = state_store.load()
        return state, state_store

    def run(
        self,
        *,
        stop_after_section: int | None = None,
        fail_section: int | None = None,
    ) -> dict[str, Any]:
        state, state_store = self.load_or_initialize()
        handlers: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
            2: self._section_persona,
            3: self._section_household,
            4: self._section_profiles,
            5: self._section_credentials,
            6: self._section_plaid,
            7: self._section_seed,
            8: self._section_integrations,
            9: self._section_observation,
            10: self._section_bridges,
        }
        if stop_after_section == 1:
            return state
        for number in range(2, 11):
            section = state["sections"][str(number)]
            inputs = self._section_inputs(number)
            digest = _digest(inputs)
            current_version = self.SECTION_VERSIONS[str(number)]
            if (
                section["status"] == "complete"
                and section.get("input_digest") == digest
                and section.get("version") == current_version
            ):
                if stop_after_section == number:
                    break
                continue
            section.update(
                {
                    "status": "in_progress",
                    "attempts": int(section.get("attempts", 0)) + 1,
                    "started_at": now_utc(),
                    "version": current_version,
                    "input_digest": digest,
                }
            )
            state_store.save(state)
            try:
                if fail_section == number:
                    raise BootstrapInterrupted(
                        f"rehearsal interruption in section {number}"
                    )
                result = handlers[number](state)
                self._complete_section(state, number, result)
                state_store.save(state)
            except Exception as exc:
                section["status"] = "failed"
                section["failed_at"] = now_utc()
                section["failure"] = type(exc).__name__
                state_store.save(state)
                raise
            if stop_after_section == number:
                break
        self._write_public_report(state)
        return state

    def rollback_section(self, number: int) -> dict[str, Any]:
        if number <= 1 or number > 10:
            raise WizardFailure("only sections 2 through 10 support local rollback")
        state, state_store = self.load_or_initialize()
        section = state["sections"][str(number)]
        for artifact in section.get("artifacts", []):
            path = (self.paths.root / artifact).resolve()
            if self.paths.root.resolve() not in path.parents:
                raise WizardFailure("section artifact escaped the deployment root")
            if path.is_file():
                path.unlink()
        section.update({"status": "rolled_back", "rolled_back_at": now_utc()})
        state_store.save(state)
        return state

    def _section_inputs(self, number: int) -> Any:
        keys = {
            2: "assistant",
            3: "household",
            4: "profiles",
            5: "providers",
            6: "plaid",
            7: "seed",
            8: "integrations",
            9: "assistant",
            10: "bridges",
        }
        return self.answers[keys[number]]

    def _complete_section(
        self,
        state: dict[str, Any],
        number: int,
        result: dict[str, Any],
    ) -> None:
        section = state["sections"][str(number)]
        section.update(
            {
                "status": "complete",
                "completed_at": now_utc(),
                "result": result,
            }
        )
        state["events"].append(
            {
                "type": "bootstrap.section.completed",
                "section": number,
                "name": SECTION_NAMES[number - 1],
                "version": section.get("version", 1),
                "at": section["completed_at"],
            }
        )

    def _section_persona(self, state: dict[str, Any]) -> dict[str, Any]:
        workspace = self.root / "openclaw" / "administrateme" / "workspace"
        required = ("SOUL.md", "IDENTITY.md", "USER.md", "AGENTS.md")
        missing = [name for name in required if not (workspace / name).is_file()]
        if missing:
            raise WizardFailure(f"compiled persona workspace is incomplete: {missing}")
        if not self.dry_run:
            subprocess.run(
                ["npm", "run", "workspace:compile"],
                cwd=self.root / "openclaw" / "administrateme",
                check=True,
            )
        destination = self.paths.instance_dir / "openclaw" / "workspace"
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        artifacts = []
        for name in required:
            target = destination / name
            atomic_write(target, (workspace / name).read_bytes(), mode=0o600)
            artifacts.append(str(target.relative_to(self.paths.root)))
        state["sections"]["2"]["artifacts"] = artifacts
        return {"persona": self.answers["assistant"]["persona"], "files": list(required)}

    def _section_household(self, state: dict[str, Any]) -> dict[str, Any]:
        household = self.answers["household"]
        tenant_id = f"tenant-{secrets.token_hex(8)}"
        members = household["members"]
        principals = [
            member["member_id"] for member in members if member["role"] == "principal"
        ]
        children = [
            member["member_id"] for member in members if member["role"] == "child"
        ]
        relationships: list[dict[str, str]] = []
        for index, first in enumerate(household["coparents"]):
            for second in household["coparents"][index + 1 :]:
                relationships.append(
                    {
                        "relationship_id": f"relationship_coparent_{first}_{second}",
                        "from_party_id": first,
                        "to_party_id": second,
                        "kind": "coparent",
                    }
                )
        for principal in principals:
            for child in children:
                relationships.append(
                    {
                        "relationship_id": f"relationship_caregiver_{principal}_{child}",
                        "from_party_id": principal,
                        "to_party_id": child,
                        "kind": "caregiver",
                    }
                )
            for helper in household["helpers"]:
                relationships.append(
                    {
                        "relationship_id": f"relationship_helper_{helper}_{principal}",
                        "from_party_id": helper,
                        "to_party_id": principal,
                        "kind": "helper_for",
                    }
                )
            for ambient in household["ambient_members"]:
                relationships.append(
                    {
                        "relationship_id": f"relationship_ambient_{ambient}_{principal}",
                        "from_party_id": ambient,
                        "to_party_id": principal,
                        "kind": "ambient_context_for",
                    }
                )
        config_dir = self.paths.instance_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        instance = {
            "schema_version": 1,
            "tenant_id": tenant_id,
            "runtime_host": "127.0.0.1",
            "runtime_port": load_versions()["administrateme"]["runtime_port"],
            "observation_mode": True,
        }
        instance_path = config_dir / "instance.yaml"
        atomic_write(
            instance_path,
            yaml.safe_dump(instance, sort_keys=True).encode("utf-8"),
        )
        mapping = {
            "schema_version": 1,
            "members": members,
            "memberships": [
                {
                    "membership_id": f"membership_{member['member_id']}",
                    "party_id": member["member_id"],
                    "role": member["role"],
                }
                for member in members
            ],
            "relationships": relationships,
            "ambient_members": household["ambient_members"],
            "coparents": household["coparents"],
            "helpers": household["helpers"],
        }
        mapping_path = config_dir / "member-mapping.json"
        atomic_write(mapping_path, json.dumps(mapping, indent=2).encode("utf-8"))
        state["tenant_id"] = tenant_id
        state["sections"]["3"]["artifacts"] = [
            str(instance_path.relative_to(self.paths.root)),
            str(mapping_path.relative_to(self.paths.root)),
        ]
        return {"member_count": len(members), "roles": sorted({m["role"] for m in members})}

    def _section_profiles(self, state: dict[str, Any]) -> dict[str, Any]:
        profiles = self.answers["profiles"]
        allowed = {
            "adhd_executive",
            "minimalist_parent",
            "power_user",
            "kid_scoreboard",
            "ambient_entity",
        }
        if not set(profiles.values()) <= allowed:
            raise WizardFailure("profile assignment contains an unsupported profile")
        preview = {
            member: {
                "profile": profile,
                "authority_changes": False,
                "model_routing_changes": False,
            }
            for member, profile in profiles.items()
        }
        path = self.paths.instance_dir / "config" / "profiles.json"
        atomic_write(path, json.dumps(preview, indent=2).encode("utf-8"))
        state["sections"]["4"]["artifacts"] = [str(path.relative_to(self.paths.root))]
        return {"assignments": len(preview), "previewed": True}

    def _section_credentials(self, state: dict[str, Any]) -> dict[str, Any]:
        references = state["secret_references"]
        openclaw_ref = reference_for(
            self.paths,
            "openclaw-shared",
            test_mode=self.test_mode,
        )
        if not self.store.has(openclaw_ref):
            openclaw_ref = self.store.put(
                "openclaw-shared",
                secrets.token_urlsafe(32).encode("ascii"),
            )
        references["openclaw_shared"] = openclaw_ref
        config = {
            "schema_version": 1,
            "provider": self.store.provider_name,
            "references": references,
            "assistant_provider": self.answers["providers"]["assistant"],
            "optional_providers": self.answers["providers"]["optional"],
        }
        path = self.paths.instance_dir / "config" / "secret-references.json"
        atomic_write(path, json.dumps(config, indent=2, sort_keys=True).encode("utf-8"))
        state["sections"]["5"]["artifacts"] = [str(path.relative_to(self.paths.root))]
        return {"provider": self.store.provider_name, "references_tested": len(references)}

    def _section_plaid(self, state: dict[str, Any]) -> dict[str, Any]:
        plaid = self.answers["plaid"]
        if plaid["selected"] and plaid["environment"] != "sandbox":
            raise WizardFailure("bootstrap permits Plaid sandbox only")
        result = {
            "selected": plaid["selected"],
            "environment": "sandbox" if plaid["selected"] else "disabled",
            "link_state": "fixture-linked" if plaid.get("fake_link") else "pending-human-link",
            "live_claim": False,
        }
        if not plaid["selected"]:
            state["follow_ups"].append(
                {"kind": "integration", "action": "Review whether Plaid sandbox is wanted"}
            )
        return result

    def _section_seed(self, state: dict[str, Any]) -> dict[str, Any]:
        selected = self.answers["seed"]
        seeded = sorted(name for name, enabled in selected.items() if enabled)
        skipped = sorted(name for name, enabled in selected.items() if not enabled)
        for name in skipped:
            state["follow_ups"].append(
                {"kind": "seed_data", "action": f"Add or explicitly dismiss {name}"}
            )
        path = self.paths.instance_dir / "data" / "seed-manifest.json"
        atomic_write(
            path,
            json.dumps({"seeded": seeded, "skipped": skipped}, indent=2).encode("utf-8"),
        )
        state["sections"]["7"]["artifacts"] = [str(path.relative_to(self.paths.root))]
        return {"seeded": seeded, "inbox_follow_ups": len(skipped)}

    def _section_integrations(self, state: dict[str, Any]) -> dict[str, Any]:
        selected = self.answers["integrations"]
        members = {
            member["member_id"]: member["role"]
            for member in self.answers["household"]["members"]
        }
        actor_identities: dict[str, str] = {}
        for actor in selected.get("openclaw_actor_identities", []):
            if not isinstance(actor, dict):
                raise WizardFailure("OpenClaw actor mappings must be objects")
            required = {"channel_id", "account_id", "actor_id", "member_id"}
            if set(actor) != required or not all(
                isinstance(actor[key], str) and actor[key].strip() for key in required
            ):
                raise WizardFailure("OpenClaw actor mapping fields must be non-empty strings")
            member_id = actor["member_id"]
            if members.get(member_id) not in {"principal", "child", "device"}:
                raise WizardFailure(
                    "OpenClaw actors may map only to interactive household members"
                )
            key = (
                f"{actor['channel_id']}|{actor['account_id']}|{actor['actor_id']}"
            )
            if key in actor_identities:
                raise WizardFailure("OpenClaw actor mappings must be unique")
            actor_identities[key] = member_id
        if self.answers["providers"]["assistant"] == "openclaw" and not actor_identities:
            raise WizardFailure("OpenClaw requires at least one explicit actor mapping")
        for name in ("shared_lists", "calendars", "contacts", "home_assistant"):
            if not selected[name]:
                follow_up = {
                    "kind": "integration",
                    "action": f"Review whether to pair {name}",
                }
                if follow_up not in state["follow_ups"]:
                    state["follow_ups"].append(follow_up)
        permissions = [
            "macOS will ask separately for Calendar, Reminders, Contacts, Notes, and microphone access only when you enable each bridge.",
            "OAuth opens the provider's own sign-in page. AdministrateMe stores the resulting credential in the selected secure provider, never in this repository.",
            "Tailscale identifies the member device; it does not grant household authority by itself.",
            "Home Assistant is not contacted unless you explicitly select it and complete its token setup.",
        ]
        config = {
            "schema_version": 1,
            "selected": selected,
            "adapter_categories": {
                "communication": "fixture" if selected["fixture_channel"] else "disabled",
                "external_state_mirror": "disabled",
                "inbound_data": "fixture" if self.answers["plaid"]["selected"] else "disabled",
                "personal_knowledge": "pending-bridge",
                "outbound_action": "disabled",
            },
            "permissions": permissions,
            "live_provider_claims": [],
            "openclaw_actor_identities": actor_identities,
            "member_roles": members,
        }
        path = self.paths.instance_dir / "config" / "adapters.json"
        atomic_write(path, json.dumps(config, indent=2).encode("utf-8"))
        manifests = []
        for manifest in sorted((self.root / "packs").rglob("pack.yaml")):
            manifests.append(
                {
                    "path": str(manifest.relative_to(self.root)),
                    "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                }
            )
        pack_path = self.paths.instance_dir / "config" / "pack-manifests.json"
        atomic_write(pack_path, json.dumps(manifests, indent=2).encode("utf-8"))
        self._install_launch_agents()
        state["sections"]["8"]["artifacts"] = [
            str(path.relative_to(self.paths.root)),
            str(pack_path.relative_to(self.paths.root)),
        ]
        return {
            "selected": sorted(
                key for key, value in selected.items() if isinstance(value, bool) and value
            ),
            "openclaw_actor_mappings": len(actor_identities),
            "live_claims": 0,
        }

    def _section_observation(self, state: dict[str, Any]) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        state["observation"].update(
            {
                "active": True,
                "briefing_complete": True,
                "started_at": started.isoformat(),
                "trust_not_before": (started + timedelta(days=5)).isoformat(),
                "recommended_review_at": (started + timedelta(days=7)).isoformat(),
                "trusted": False,
                "trust_prerequisites": [
                    "five full days elapsed",
                    "suppression history reviewed",
                    "backup healthy",
                    "identity and privacy findings resolved",
                    "Doctor passes",
                    "principal confirms explicitly",
                ],
            }
        )
        proof = {
            "kind": "canonical-synthetic-round-trip",
            "status": "pending-final-doctor",
            "external_side_effects": 0,
            "observation_suppression_required": True,
        }
        path = self.paths.instance_dir / "config" / "observation.json"
        atomic_write(
            path,
            json.dumps(state["observation"], indent=2).encode("utf-8"),
        )
        asyncio.run(self._initialize_runtime_state(state))
        state["sections"]["9"]["artifacts"] = [str(path.relative_to(self.paths.root))]
        return {"briefing": True, "proof": proof, "minimum_days": 5}

    async def _initialize_runtime_state(self, state: dict[str, Any]) -> None:
        from adminme.runtime.application import AdministrateMeRuntime, RuntimeSettings

        members = self.answers["household"]["members"]
        roles = {member["member_id"]: member["role"] for member in members}
        principal = next(
            member["member_id"] for member in members if member["role"] == "principal"
        )
        settings = RuntimeSettings.credential_free(
            self.paths.instance_dir,
            encryption_key=self.store.get(state["secret_references"]["event_log"]),
            repository_root=self.root,
            member_identities={"fixture-principal.ts.net": principal},
            member_roles=roles,
            deterministic_openclaw_fixture=True,
            fixture_member_id=principal,
            openclaw_enabled=True,
            openclaw_shared_secret="fixture-openclaw-shared-secret-32",
            openclaw_actor_identities={
                "fixture-channel|fixture-account|fixture-actor": principal
            },
        )
        runtime = AdministrateMeRuntime(settings)
        await runtime.start()
        try:
            diagnostics = await runtime.diagnostics()
            if not diagnostics["ready"]:
                raise WizardFailure("initialized runtime is not ready")
        finally:
            await runtime.stop()

    def _section_bridges(self, state: dict[str, Any]) -> dict[str, Any]:
        if not self.answers["bridges"]["apple"]:
            return {"explicit_non_apple_skip": True, "packages": 0}
        package_dir = self.paths.instance_dir / "bridges" / "enrollment"
        package_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = self.store.get(state["secret_references"]["bootstrap_state"])
        members = self.answers["household"]["members"]
        artifacts = []
        enrollment_index: dict[str, dict[str, Any]] = {}
        for member in members:
            member_id = member["member_id"]
            is_child = member["role"] == "child"
            adapters = [
                "apple_notes",
                "voice_memos",
                "apple_calendar",
                "apple_reminders",
                "apple_contacts",
                "notes_checklists",
            ]
            if self.answers["bridges"]["obsidian"] and not is_child:
                adapters.append("obsidian")
            tailscale_identity = member.get("tailscale_identity")
            if not tailscale_identity:
                if not self.dry_run:
                    raise WizardFailure(
                        f"member {member_id} requires a verified Tailscale identity"
                    )
                tailscale_identity = f"{member_id}.fixture.ts.net"
            package = {
                "schema_version": 1,
                "member_id": member_id,
                "owner_scope": f"private:{member_id}",
                "tailscale_identity": tailscale_identity,
                "allowed_adapters": adapters,
                "restrictions": {
                    "kid": is_child,
                    "obsidian_allowed": not is_child,
                    "cross_owner_claims": "deny",
                    "central_owner_stamping": True,
                },
                "bootstrap_token_reference": self.store.put(
                    f"bridge-{member_id}", os.urandom(32)
                ),
                "live_provider_claims": [],
            }
            nonce = os.urandom(12)
            encrypted = AESGCM(key).encrypt(
                nonce,
                json.dumps(package, sort_keys=True).encode("utf-8"),
                member_id.encode("utf-8"),
            )
            target = package_dir / f"{member_id}.bridgepkg.json.enc"
            atomic_write(
                target,
                json.dumps(
                    {
                        "format": "administrateme-bridge-enrollment-v1",
                        "member_id": member_id,
                        "nonce": base64.b64encode(nonce).decode("ascii"),
                        "ciphertext": base64.b64encode(encrypted).decode("ascii"),
                    },
                    sort_keys=True,
                ).encode("utf-8"),
            )
            artifacts.append(str(target.relative_to(self.paths.root)))
            enrollment_index[package["tailscale_identity"]] = {
                "member_id": member_id,
                "owner_scope": package["owner_scope"],
                "role": member["role"],
                "kid_restrictions": is_child,
                "status": "package-created-not-live",
            }
        index_path = self.paths.instance_dir / "bridges" / "member-mapping.json"
        atomic_write(
            index_path,
            json.dumps(enrollment_index, indent=2, sort_keys=True).encode("utf-8"),
        )
        artifacts.append(str(index_path.relative_to(self.paths.root)))
        state["sections"]["10"]["artifacts"] = artifacts
        return {"packages": len(members), "kid_packages": sum(m["role"] == "child" for m in members)}

    def _install_launch_agents(self) -> None:
        install_launch_agents(self.paths, self.root, test_mode=self.test_mode)

    def _write_public_report(self, state: dict[str, Any]) -> None:
        complete = all(
            section["status"] == "complete" for section in state["sections"].values()
        )
        report = {
            "schema_version": 1,
            "generated_at": now_utc(),
            "bootstrap_complete": complete,
            "mode": "credential-free-dry-run" if self.dry_run else "installation",
            "sections": {
                number: section["status"]
                for number, section in state["sections"].items()
            },
            "versions": load_versions(),
            "observation": state["observation"],
            "follow_up_count": len(state["follow_ups"]),
            "live_provider_claims": [],
        }
        atomic_write(
            self.paths.public_config_path,
            json.dumps(report, indent=2, sort_keys=True).encode("utf-8"),
        )
        releases = self.paths.app_dir / "releases"
        if releases in self.root.parents:
            pointer = self.paths.app_dir / "current-release.json"
            if not pointer.exists():
                completed = subprocess.run(
                    ["git", "-C", str(self.root), "rev-parse", "HEAD"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                commit = completed.stdout.strip() if completed.returncode == 0 else self.root.name
                atomic_write(
                    pointer,
                    json.dumps(
                        {"path": str(self.root), "commit": commit, "previous": None},
                        indent=2,
                    ).encode("utf-8"),
                )


__all__ = [
    "BootstrapInterrupted",
    "BootstrapWizard",
    "PreflightFailure",
    "WizardFailure",
    "install_launch_agents",
]


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_walk_keys(item))
    return keys
