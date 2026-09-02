"""Nontechnical target-Mac flow over the reviewed bridge installer.

UI packaging calls this service layer; no method clicks TCC dialogs or reads a
provider before the member confirms and grants the selected local permission.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx
import yaml

from adminme.bridge_enrollment import (
    CAPABILITIES,
    PROGRESS_CONTRACT,
    BridgeEnrollmentError as PackageError,
    canonical_bytes,
    device_proof_key,
    inspect_transfer_package,
    progress_signature,
    reviewed_release,
)
from adminme.bridge_authorization import BRIDGE_PACK_POLICIES
from bootstrap.bridge_enrollment import (  # type: ignore[import-untyped]
    BridgeEnrollmentError,
    MemberBridgeInstaller,
    MemberBridgePaths,
    _decode_transfer_code,
)
from bootstrap.contracts import atomic_write  # type: ignore[import-untyped]


class ProgressReporter(Protocol):
    def __call__(
        self, transfer: dict[str, Any], payload: dict[str, Any], signature: str
    ) -> dict[str, Any]: ...


class EvidenceSubmitter(Protocol):
    def __call__(self, transfer: dict[str, Any], envelope: dict[str, Any]) -> str: ...


CapabilityProbe = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class PackagePreview:
    instance_id: str
    central_url: str
    member_id: str
    role: str
    device_class: str
    capabilities: tuple[str, ...]
    capability_labels: tuple[str, ...]
    release_commit: str
    expires_at: str
    package_sha256: str
    observation_only: bool
    provider_writes_authorized: bool


class MemberBridgeEnrollmentHelper:
    """Ordered local flow used by the macOS helper app and disposable lab."""

    def __init__(
        self,
        paths: MemberBridgePaths,
        *,
        repository_root: Path,
        package_path: Path,
        transfer_code: str,
        test_mode: bool = False,
        reporter: ProgressReporter | None = None,
        evidence_submitter: EvidenceSubmitter | None = None,
        installer: MemberBridgeInstaller | None = None,
    ) -> None:
        self.paths = paths
        self.repository_root = repository_root.resolve()
        self.package_path = package_path.expanduser().resolve()
        self.test_mode = test_mode
        self._transfer_code = transfer_code
        self._reporter = reporter or self._http_report
        self._evidence_submitter = evidence_submitter or self._http_evidence
        self.installer = installer or MemberBridgeInstaller(
            paths, root=self.repository_root, test_mode=test_mode
        )
        self.transfer: dict[str, Any] | None = None
        self.state = "awaiting_transfer"
        self.capability_states: dict[str, str] = {}
        self.device_node_id: str | None = None

    def inspect(self) -> PackagePreview:
        if not self.package_path.is_file() or self.package_path.is_symlink():
            raise BridgeEnrollmentError("enrollment package is missing or unsafe")
        package_bytes = self.package_path.read_bytes()
        try:
            transfer = inspect_transfer_package(package_bytes, self._transfer_code)
        except PackageError as exc:
            raise BridgeEnrollmentError(str(exc)) from exc
        expires = datetime.fromisoformat(str(transfer["expires_at"]))
        if datetime.now(timezone.utc) >= expires:
            raise BridgeEnrollmentError(
                "bridge transfer package expired; regenerate it"
            )
        if transfer.get("release_commit") != reviewed_release(self.repository_root):
            raise BridgeEnrollmentError(
                "bridge package and reviewed helper release do not match"
            )
        restrictions = transfer.get("restrictions") or {}
        if (
            restrictions.get("observation_only") is not True
            or restrictions.get("provider_writes") != "deny"
            or transfer.get("enabled_packs")
        ):
            raise BridgeEnrollmentError(
                "bridge enrollment package would exceed observation-only authority"
            )
        capabilities = tuple(transfer["requested_capabilities"])
        self.transfer = transfer
        self.capability_states = {value: "requested" for value in capabilities}
        return PackagePreview(
            instance_id=str(transfer["instance_id"]),
            central_url=str(transfer["central_url"]),
            member_id=str(transfer["member_id"]),
            role=str(transfer["role"]),
            device_class=str(transfer["device_class"]),
            capabilities=capabilities,
            capability_labels=tuple(
                str(CAPABILITIES[item]["label"]) for item in capabilities
            ),
            release_commit=str(transfer["release_commit"]),
            expires_at=str(transfer["expires_at"]),
            package_sha256=hashlib.sha256(package_bytes).hexdigest(),
            observation_only=True,
            provider_writes_authorized=False,
        )

    def _identity(self) -> tuple[str, str]:
        return self.installer._tailscale_identity()

    def _proof_key(self) -> bytes:
        if self._transfer_code:
            return device_proof_key(_decode_transfer_code(self._transfer_code))
        if not self.paths.device_proof_key_path.is_file():
            raise BridgeEnrollmentError("device proof key is unavailable")
        return self.paths.device_proof_key_path.read_bytes()

    def _report(
        self,
        state: str,
        *,
        capability_id: str | None = None,
        capability_state: str | None = None,
        reason: str | None = None,
        pack_config: dict[str, Any] | None = None,
        enrollment_event_id: str | None = None,
        capability_event_id: str | None = None,
        last_successful_observation: str | None = None,
    ) -> dict[str, Any]:
        if self.transfer is None or self.device_node_id is None:
            raise BridgeEnrollmentError("inspect the package and verify this Mac first")
        transfer = self.transfer
        payload = {
            "contract": PROGRESS_CONTRACT,
            "request_id": transfer["request_id"],
            "enrollment_id": transfer["enrollment_id"],
            "member_id": transfer["member_id"],
            "instance_id": transfer["instance_id"],
            "release_commit": transfer["release_commit"],
            "capability_set_sha256": transfer["capability_set_sha256"],
            "previous_state": self.state,
            "state": state,
            "nonce": os.urandom(16).hex(),
            "reported_at": datetime.now(timezone.utc).isoformat(),
            "device_node_id": self.device_node_id,
            "enrollment_event_id": enrollment_event_id,
            "capability_id": capability_id,
            "capability_state": capability_state,
            "reason": reason,
            "last_successful_observation": last_successful_observation,
            "pack_config": pack_config,
            "capability_event_id": capability_event_id,
        }
        signature = progress_signature(self._proof_key(), payload)
        result = self._reporter(transfer, payload, signature)
        self.state = str(result.get("state") or state)
        if capability_id and capability_state:
            self.capability_states[capability_id] = capability_state
        self._persist_progress()
        return result

    def _persist_progress(self) -> None:
        if not self.paths.receipt_path.is_file() or self.transfer is None:
            return
        try:
            receipt = json.loads(self.paths.receipt_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BridgeEnrollmentError("member bridge receipt is invalid") from exc
        receipt.update(
            {
                "wizard_state": self.state,
                "capability_states": dict(self.capability_states),
                "wizard_updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        atomic_write(
            self.paths.receipt_path,
            json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8"),
        )

    @classmethod
    def resume(
        cls,
        paths: MemberBridgePaths,
        *,
        repository_root: Path,
        test_mode: bool = False,
        reporter: ProgressReporter | None = None,
        evidence_submitter: EvidenceSubmitter | None = None,
        installer: MemberBridgeInstaller | None = None,
    ) -> "MemberBridgeEnrollmentHelper":
        """Resume after a target-Mac or bridge restart without transfer material."""

        try:
            config = yaml.safe_load(paths.config_path.read_text("utf-8")) or {}
            receipt = json.loads(paths.receipt_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise BridgeEnrollmentError(
                "installed bridge state is unavailable"
            ) from exc
        if not isinstance(config, dict) or not isinstance(receipt, dict):
            raise BridgeEnrollmentError("installed bridge state is invalid")
        required = (
            "request_id",
            "enrollment_id",
            "member_id",
            "instance_id",
            "release_commit",
            "capability_set_sha256",
            "central_endpoint",
            "tailscale_identity",
        )
        if any(
            not isinstance(config.get(key), str) or not config[key] for key in required
        ):
            raise BridgeEnrollmentError("installed bridge binding is incomplete")
        helper = cls(
            paths,
            repository_root=repository_root,
            package_path=paths.root / "consumed.adminmebridge",
            transfer_code="",
            test_mode=test_mode,
            reporter=reporter,
            evidence_submitter=evidence_submitter,
            installer=installer,
        )
        helper.transfer = {
            "wizard_contract": config.get("wizard_contract"),
            "request_id": config["request_id"],
            "enrollment_id": config["enrollment_id"],
            "member_id": config["member_id"],
            "instance_id": config["instance_id"],
            "tenant_id": config["instance_id"],
            "release_commit": config["release_commit"],
            "capability_set_sha256": config["capability_set_sha256"],
            "central_url": config["central_endpoint"],
            "tailscale_identity": config["tailscale_identity"],
            "requested_capabilities": config.get("requested_capabilities") or [],
        }
        helper.state = str(receipt.get("wizard_state") or "awaiting_permissions")
        helper.capability_states = {
            str(key): str(value)
            for key, value in (receipt.get("capability_states") or {}).items()
        }
        if not helper.capability_states:
            helper.capability_states = {
                str(value): "requested"
                for value in config.get("requested_capabilities") or []
            }
        helper.device_node_id = str(receipt.get("bridge_node_id") or "") or None
        if helper.device_node_id is None:
            raise BridgeEnrollmentError("installed bridge device identity is missing")
        return helper

    @staticmethod
    def _http_report(
        transfer: dict[str, Any], payload: dict[str, Any], signature: str
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{transfer['central_url']}/api/bridge/enrollment/progress",
            headers={
                "Tailscale-User-Login": str(transfer["tailscale_identity"]),
                "X-AdministrateMe-Bridge-Enrollment": str(transfer["enrollment_id"]),
            },
            json={**payload, "signature": signature},
            timeout=20.0,
        )
        if response.status_code != 202:
            raise BridgeEnrollmentError(
                f"central enrollment progress was refused: {response.status_code}"
            )
        value = response.json()
        if not isinstance(value, dict):
            raise BridgeEnrollmentError("central enrollment progress was malformed")
        return value

    @staticmethod
    def _http_evidence(transfer: dict[str, Any], envelope: dict[str, Any]) -> str:
        response = httpx.post(
            f"{transfer['central_url']}/api/bridge/ingest",
            headers={
                "Tailscale-User-Login": str(transfer["tailscale_identity"]),
                "X-AdministrateMe-Bridge-Enrollment": str(transfer["enrollment_id"]),
            },
            json=envelope,
            timeout=20.0,
        )
        if response.status_code != 202:
            raise BridgeEnrollmentError(
                f"central capability evidence was refused: {response.status_code}"
            )
        value = response.json()
        event_id = value.get("event_id") if isinstance(value, dict) else None
        if not isinstance(event_id, str) or not event_id:
            raise BridgeEnrollmentError("central capability evidence was malformed")
        return event_id

    def open_and_verify(self) -> PackagePreview:
        """Verify package, release, instance and Tailscale without installing."""

        preview = self.inspect()
        identity, node_id = self._identity()
        if self.transfer is None:
            raise BridgeEnrollmentError("enrollment package is unavailable")
        if identity.casefold() != str(self.transfer["tailscale_identity"]).casefold():
            raise BridgeEnrollmentError(
                "this Mac is signed into a different Tailscale identity"
            )
        self.device_node_id = node_id
        self._report("package_opened")
        self._report("instance_verified")
        return preview

    def accept_locally(self, *, member_confirmed: bool) -> dict[str, Any]:
        """Install the exact reviewed runtime only after explicit local consent."""

        if not member_confirmed:
            raise BridgeEnrollmentError("the named member must confirm locally")
        if self.transfer is None or self.device_node_id is None:
            raise BridgeEnrollmentError("open and verify the package first")
        self._report("member_confirmed")
        self._report("device_identity_verified")
        identity = str(self.transfer["tailscale_identity"])
        receipt = self.installer.accept(
            self.package_path,
            self._transfer_code,
            identity_override=identity if self.test_mode else None,
            node_override=self.device_node_id if self.test_mode else None,
        )
        self._report("bridge_paired", enrollment_event_id=str(receipt["event_id"]))
        self._report("awaiting_permissions")
        self._transfer_code = ""
        return {
            **receipt,
            "observation_only": True,
            "connectors_started": [],
            "provider_effects": False,
        }

    def open_permission_settings(self, capability_id: str) -> dict[str, Any]:
        capability = CAPABILITIES.get(capability_id)
        if capability is None or capability_id not in self.capability_states:
            raise BridgeEnrollmentError("capability was not selected")
        url = capability.get("settings_url")
        if url and not self.test_mode:
            subprocess.run(["open", str(url)], check=False)
        return {
            "capability_id": capability_id,
            "settings_url": url,
            "dialog_clicked": False,
            "permission_bypassed": False,
        }

    def pack_config_for(
        self,
        capability_id: str,
        *,
        source_coordinate: str | None = None,
        account_coordinate: str | None = None,
    ) -> dict[str, Any]:
        """Build one exact, content-free local source coordinate.

        Human-readable account/container names stay in the local helper.  Only
        a stable opaque account reference and the selected container or folder
        coordinate are placed in bridge configuration.
        """

        if self.transfer is None or self.device_node_id is None:
            raise BridgeEnrollmentError("the installed bridge binding is unavailable")
        if capability_id not in self.capability_states:
            raise BridgeEnrollmentError("capability was not selected")
        capability = CAPABILITIES[capability_id]
        pack = str(capability["pack"])
        policy = BRIDGE_PACK_POLICIES[pack]
        member_id = str(self.transfer["member_id"])
        if not policy.requires_provider_account:
            config: dict[str, Any] = {"member_id": member_id}
            if capability_id == "apple_notes":
                config.update(
                    {"automation_mode": "notes_scripting", "emit_schema_version": 2}
                )
            elif capability_id in {"apple_voice_memos", "obsidian"}:
                if not source_coordinate:
                    raise BridgeEnrollmentError("choose the local source folder")
                root = Path(source_coordinate).expanduser().resolve()
                if (
                    root in {Path(root.anchor), Path.home()}
                    or not root.is_dir()
                    or root.is_symlink()
                ):
                    raise BridgeEnrollmentError("the selected source folder is unsafe")
                config["root"] = str(root)
            return config

        if not source_coordinate or not source_coordinate.strip():
            raise BridgeEnrollmentError("choose one exact local source container")
        container = source_coordinate.strip()
        account_seed = account_coordinate or (
            f"{self.device_node_id}:{capability_id}:{container}"
        )
        provider_account_id = (
            "provider-account:"
            + hashlib.sha256(account_seed.encode("utf-8")).hexdigest()[:32]
        )
        config = {
            "member_id": member_id,
            "provider_account_id": provider_account_id,
            "deployment_target": "bridge",
            "workspace": "personal",
            "permission_scope": sorted(policy.permission_scopes),
            "binding_provenance": "member-bridge-enrollment",
            "ownership_attested": True,
            str(policy.container_key): container,
            "allowed_container_ids": [container],
            "shared_container_ids": [],
        }
        return config

    def record_permission(
        self,
        capability_id: str,
        *,
        granted: bool,
        pack_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if capability_id not in self.capability_states:
            raise BridgeEnrollmentError("capability was not selected")
        if self.capability_states[capability_id] == "requested":
            self._report(
                self.state,
                capability_id=capability_id,
                capability_state="implementation_available",
            )
            self._report(
                self.state,
                capability_id=capability_id,
                capability_state="permission_required",
            )
        if not granted:
            return self._report(
                "partially_active"
                if "settled" in self.capability_states.values()
                else "degraded",
                capability_id=capability_id,
                capability_state="permission_denied",
                reason="member_denied_local_permission",
            )
        result = self._report(
            "capabilities_testing"
            if self.state in {"awaiting_permissions", "degraded", "partially_active"}
            else self.state,
            capability_id=capability_id,
            capability_state="permission_verified",
            pack_config=pack_config,
        )
        if not isinstance(pack_config, dict) or not self.paths.config_path.is_file():
            raise BridgeEnrollmentError("exact local source coordinate is unavailable")
        local = yaml.safe_load(self.paths.config_path.read_text("utf-8")) or {}
        if not isinstance(local, dict):
            raise BridgeEnrollmentError("member bridge configuration is invalid")
        pack = str(CAPABILITIES[capability_id]["pack"])
        configured = set(local.get("configured_packs") or [])
        configured.add(pack)
        all_config = dict(local.get("pack_config") or {})
        all_config[pack] = dict(pack_config)
        local["configured_packs"] = sorted(configured)
        local["pack_config"] = all_config
        atomic_write(
            self.paths.config_path,
            yaml.safe_dump(local, sort_keys=True).encode("utf-8"),
        )
        return result

    def run_capability_test(
        self,
        capability_id: str,
        probe: CapabilityProbe,
    ) -> dict[str, Any]:
        """Run an injected content-free probe only after permission evidence."""

        if self.capability_states.get(capability_id) != "permission_verified":
            raise BridgeEnrollmentError("local permission must be verified first")
        result = probe(capability_id, {"content_allowed": False})
        if (
            not isinstance(result, dict)
            or result.get("content_exposed") is not False
            or result.get("status") != "passed"
        ):
            raise BridgeEnrollmentError(
                "capability probe did not prove content-free behavior"
            )
        return {
            "capability_id": capability_id,
            "status": str(result.get("status") or "unknown"),
            "content_exposed": False,
        }

    def record_prerequisite_missing(
        self, capability_id: str, *, reason: str
    ) -> dict[str, Any]:
        if self.capability_states.get(capability_id) == "requested":
            self._report(
                self.state,
                capability_id=capability_id,
                capability_state="implementation_available",
            )
        return self._report(
            "degraded" if self.state != "degraded" else self.state,
            capability_id=capability_id,
            capability_state="prerequisite_missing",
            reason=reason,
        )

    def record_capability_degraded(
        self, capability_id: str, *, reason: str
    ) -> dict[str, Any]:
        return self._report(
            "degraded" if self.state != "degraded" else self.state,
            capability_id=capability_id,
            capability_state="degraded",
            reason=reason,
        )

    def prove_and_start(self, capability_id: str) -> dict[str, Any]:
        """Submit content-free adapter evidence, attest centrally, then start."""

        if self.transfer is None:
            raise BridgeEnrollmentError("installed bridge state is unavailable")
        if self.capability_states.get(capability_id) != "permission_verified":
            raise BridgeEnrollmentError("local capability test has not completed")
        capability = CAPABILITIES[capability_id]
        policy = BRIDGE_PACK_POLICIES[str(capability["pack"])]
        event_id: str | None = None
        if policy.requires_capability_attestation:
            if not self.paths.config_path.is_file():
                raise BridgeEnrollmentError(
                    "member bridge configuration is unavailable"
                )
            local = yaml.safe_load(self.paths.config_path.read_text("utf-8")) or {}
            if not isinstance(local, dict):
                raise BridgeEnrollmentError("member bridge configuration is invalid")
            pack_config = local.get("pack_config") or {}
            config = (
                pack_config.get(policy.pack) if isinstance(pack_config, dict) else None
            )
            if not isinstance(config, dict):
                raise BridgeEnrollmentError(
                    "exact local source coordinate is unavailable"
                )
            now = datetime.now(timezone.utc)
            provider_account_id = str(config.get("provider_account_id") or "")
            envelope = {
                "event_at_ms": int(now.timestamp() * 1000),
                "tenant_id": str(self.transfer["tenant_id"]),
                "type": "adapter.authenticated",
                "schema_version": 2,
                "occurred_at": now.isoformat(),
                "source_adapter": policy.source_adapter,
                "source_account_id": provider_account_id,
                "owner_scope": f"private:{self.transfer['member_id']}",
                "visibility_scope": f"private:{self.transfer['member_id']}",
                "sensitivity": "normal",
                "idempotency_key": (
                    f"bridge-capability-evidence:{self.transfer['request_id']}:"
                    f"{capability_id}:"
                    + hashlib.sha256(canonical_bytes(pack_config)).hexdigest()
                ),
                "payload": {
                    "adapter_name": policy.source_adapter,
                    "adapter_family": policy.family,
                    "token_storage_ref": policy.permission_reference,
                    "authenticated_at": now.isoformat(),
                    "scopes": sorted(policy.permission_scopes),
                    "bridge_enrollment_id": self.transfer["enrollment_id"],
                    "configuration_sha256": hashlib.sha256(
                        canonical_bytes(pack_config)
                    ).hexdigest(),
                    "provider_account_id": provider_account_id,
                },
            }
            event_id = self._evidence_submitter(self.transfer, envelope)
        return self.attest_and_start(capability_id, capability_event_id=event_id)

    def attest_and_start(
        self,
        capability_id: str,
        *,
        capability_event_id: str | None = None,
    ) -> dict[str, Any]:
        authenticated = self._report(
            self.state,
            capability_id=capability_id,
            capability_state="adapter_authenticated",
            capability_event_id=capability_event_id,
        )
        if authenticated.get("may_start_connector") is not True:
            raise BridgeEnrollmentError("central capability attestation was withheld")
        pack = str(CAPABILITIES[capability_id]["pack"])
        self._report(
            self.state,
            capability_id=capability_id,
            capability_state="observation_starting",
        )
        enabled = self.installer.enable_pack(pack)
        return {**enabled, "writes_authorized": False, "provider_effects": False}

    def record_observing(self, capability_id: str) -> dict[str, Any]:
        return self._report(
            self.state,
            capability_id=capability_id,
            capability_state="observing",
        )

    def finish_permission_phase(self) -> dict[str, Any]:
        """Move the overall wizard to settling once any source is observing."""

        if "observing" in self.capability_states.values():
            if self.state == "settling":
                return {
                    "state": self.state,
                    "capability_states": dict(self.capability_states),
                    "provider_effects": False,
                }
            return self._report("settling")
        return {
            "state": self.state,
            "capability_states": dict(self.capability_states),
            "provider_effects": False,
        }

    def record_settled(
        self,
        capability_id: str,
        *,
        observation_event_id: str,
        observed_at: str,
    ) -> dict[str, Any]:
        self._report(
            "settling",
            capability_id=capability_id,
            capability_state="settling",
        )
        predicted = dict(self.capability_states)
        predicted[capability_id] = "settled"
        final_state = (
            "active_observation"
            if set(predicted.values()) == {"settled"}
            else "partially_active"
        )
        return self._report(
            final_state,
            capability_id=capability_id,
            capability_state="settled",
            capability_event_id=observation_event_id,
            last_successful_observation=observed_at,
        )

    def revoke_and_uninstall(self) -> dict[str, Any]:
        return self.installer.revoke_and_uninstall()


def helper_manifest() -> dict[str, Any]:
    """Static packaging contract consumed by the macOS helper app builder."""

    return {
        "contract": "administrateme-member-bridge-helper-app-v1",
        "document_type": "com.administrateme.bridge-enrollment",
        "extension": "adminmebridge",
        "runtime_install": "reviewed-immutable-release-only",
        "checksum_verification": True,
        "auto_updater": False,
        "remote_shell": False,
        "tcc_dialog_automation": False,
        "arbitrary_central_code": False,
        "provider_writes": False,
        "actions": [
            "open_package",
            "verify_instance",
            "confirm_member",
            "verify_tailscale",
            "install_reviewed_runtime",
            "open_macos_settings",
            "record_permission_result",
            "run_content_free_test",
            "start_attested_connector",
            "pause",
            "repair",
            "revoke_and_uninstall",
        ],
    }


__all__ = [
    "MemberBridgeEnrollmentHelper",
    "PackagePreview",
    "helper_manifest",
]
