"""Short-lived central export and per-member bridge installation workflow."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import importlib
import json
import os
import plistlib
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import yaml
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from adminme.tailscale_local import tailscale_executable
from adminme.bridge_authorization import (
    BRIDGE_PACK_POLICIES,
    configuration_from_record,
    validate_bridge_record_set,
    validate_configured_pack_config,
)
from adminme.bridge_enrollment import (
    BridgeEnrollmentError as WizardBridgeEnrollmentError,
    WIZARD_CONTRACT,
    device_proof_key,
    inspect_transfer_package,
    reviewed_release,
)
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from bootstrap.contracts import DeploymentPaths, atomic_write, repository_root
from bootstrap.secrets import reference_for, secret_store
from bootstrap.services import ServiceManager


TRANSFER_FORMAT = "administrateme-bridge-transfer-v1"
INTERNAL_FORMAT = "administrateme-bridge-enrollment-v2"
BRIDGE_LABEL = "com.administrateme.member-bridge"
TRANSFER_TTL_MINUTES = 30
BUILT_IN_BRIDGE_PACKS = frozenset(
    {
        "knowledge-apple-notes",
        "knowledge-apple-voice-memos",
        "knowledge-obsidian",
        "calendaring-apple-calendar-bridge",
        "contacts-apple-contacts-bridge",
        "reminders-apple-eventkit-bridge",
    }
)


def _unique_pack_names(value: object, *, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise BridgeEnrollmentError(f"{label} must be a unique string list")
    return list(value)


def _uid() -> int:
    return int(getattr(os, "getuid", lambda: 0)())


class BridgeEnrollmentError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise BridgeEnrollmentError("bridge package timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BridgeEnrollmentError("bridge package timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise BridgeEnrollmentError("bridge package timestamp has no timezone")
    return parsed


def _git_commit(root: Path) -> str:
    try:
        return reviewed_release(root)
    except WizardBridgeEnrollmentError as exc:
        raise BridgeEnrollmentError(str(exc)) from exc


def _validate_central_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".ts.net")
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise BridgeEnrollmentError(
            "central URL must be the private Tailscale Serve origin, for example https://host.tailnet.ts.net"
        )
    return f"https://{parsed.hostname}"


def _decode_transfer_code(value: str) -> bytes:
    try:
        padded = value.strip() + "=" * (-len(value.strip()) % 4)
        key = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise BridgeEnrollmentError("the one-time transfer code is invalid") from exc
    if len(key) != 32:
        raise BridgeEnrollmentError("the one-time transfer code is invalid")
    return key


@dataclass(frozen=True)
class BridgeExport:
    public: dict[str, Any]
    transfer_code: str


class BridgeEnrollmentManager:
    """Central-host bridge enrollment lifecycle."""

    def __init__(
        self,
        paths: DeploymentPaths,
        *,
        root: Path | None = None,
        test_mode: bool = False,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.paths = paths
        self.root = (root or repository_root()).resolve()
        self.test_mode = test_mode
        self.now = now
        self.store = secret_store(paths, test_mode=test_mode)
        self.services = ServiceManager(paths, test_mode=test_mode)

    @property
    def index_path(self) -> Path:
        return self.paths.instance_dir / "bridges" / "member-mapping.json"

    def _index(self) -> dict[str, dict[str, Any]]:
        try:
            value = json.loads(self.index_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BridgeEnrollmentError(
                "central bridge enrollment index is unavailable"
            ) from exc
        if not isinstance(value, dict):
            raise BridgeEnrollmentError("central bridge enrollment index is invalid")
        return value

    def _save_index(self, value: dict[str, dict[str, Any]]) -> None:
        atomic_write(
            self.index_path,
            json.dumps(value, indent=2, sort_keys=True).encode("utf-8"),
        )

    def _sync_public_report(self, index: dict[str, dict[str, Any]]) -> None:
        path = self.paths.public_config_path
        if not path.is_file():
            return
        report = json.loads(path.read_text("utf-8"))
        pending = sum(
            record.get("status") not in {"active", "revoked"}
            for record in index.values()
        )
        sections = report.get("sections", {})
        report["bootstrap_complete"] = (
            len(sections) == 10
            and set(sections.values()) == {"complete"}
            and pending == 0
        )
        report["bridge_enrollment"] = {
            "selected": len(index),
            "pending": pending,
            "credential_free_fixture": self.test_mode,
        }
        atomic_write(path, json.dumps(report, indent=2, sort_keys=True).encode())

    def _record_for_member(
        self, index: dict[str, dict[str, Any]], member_id: str
    ) -> tuple[str, dict[str, Any]]:
        matches = [
            (identity, record)
            for identity, record in index.items()
            if record.get("member_id") == member_id
        ]
        if len(matches) != 1:
            raise BridgeEnrollmentError(
                "member has no unique bridge enrollment; rerun the reviewed bootstrap section"
            )
        return matches[0]

    def _internal_path(self, member_id: str) -> Path:
        return (
            self.paths.instance_dir
            / "bridges"
            / "enrollment"
            / f"{member_id}.bridgepkg.json.enc"
        )

    def _bootstrap_key(self) -> bytes:
        reference = reference_for(
            self.paths, "bootstrap-state", test_mode=self.test_mode
        )
        return self.store.get(reference)

    def _load_internal(self, member_id: str) -> dict[str, Any]:
        path = self._internal_path(member_id)
        try:
            envelope = json.loads(path.read_text("utf-8"))
            if envelope.get("format") != INTERNAL_FORMAT:
                raise BridgeEnrollmentError("unsupported internal enrollment format")
            plaintext = AESGCM(self._bootstrap_key()).decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                member_id.encode("utf-8"),
            )
            package = json.loads(plaintext)
        except BridgeEnrollmentError:
            raise
        except Exception as exc:
            raise BridgeEnrollmentError(
                "internal bridge enrollment is corrupt or belongs to another member"
            ) from exc
        if package.get("member_id") != member_id:
            raise BridgeEnrollmentError("internal bridge enrollment member mismatch")
        return package

    def _save_internal(self, member_id: str, package: dict[str, Any]) -> None:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._bootstrap_key()).encrypt(
            nonce,
            json.dumps(package, sort_keys=True).encode("utf-8"),
            member_id.encode("utf-8"),
        )
        atomic_write(
            self._internal_path(member_id),
            json.dumps(
                {
                    "format": INTERNAL_FORMAT,
                    "member_id": member_id,
                    "nonce": base64.b64encode(nonce).decode("ascii"),
                    "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                },
                sort_keys=True,
            ).encode("utf-8"),
        )

    def _restart_if_running(self) -> bool:
        status = self.services.status()
        running = status.status != "stopped"
        if running:
            self.services.stop()
            restarted = self.services.start()
            if restarted.status in {"failed", "degraded"}:
                raise BridgeEnrollmentError(
                    "bridge enrollment changed but the central services did not restart cleanly"
                )
        return running

    def configure(
        self, member_id: str, configuration: dict[str, Any]
    ) -> dict[str, Any]:
        """Seal reviewed pack coordinates without enrolling or touching providers."""

        index = self._index()
        _identity, record = self._record_for_member(index, member_id)
        if record.get("status") != "package-created-not-live":
            raise BridgeEnrollmentError(
                "bridge pack configuration must be sealed before export"
            )
        package = self._load_internal(member_id)
        configured = _unique_pack_names(
            configuration.get("configured_packs"), label="configured_packs"
        )
        allowed = _unique_pack_names(
            package.get("allowed_packs"), label="allowed_packs"
        )
        if not set(configured) <= set(allowed):
            raise BridgeEnrollmentError(
                "configured bridge packs exceed the reviewed member allowlist"
            )
        try:
            normalized = validate_configured_pack_config(
                member_id=member_id,
                enrollment_id=None,
                configured_packs=configured,
                pack_config=configuration.get("pack_config", {}),
            )
        except ValueError as exc:
            raise BridgeEnrollmentError(str(exc)) from exc
        prior_package = copy.deepcopy(package)
        prior_index = copy.deepcopy(index)
        package.update(
            {
                "configured_packs": sorted(configured),
                # Provider packs never start as a side effect of transport
                # export/accept. The member explicitly enables one later.
                "enabled_packs": [],
                "pack_config": normalized,
            }
        )
        digest = hashlib.sha256(
            json.dumps(normalized, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest()
        record.update(
            {
                "configured_packs": sorted(configured),
                "enabled_packs": [],
                "configured_pack_count": len(configured),
                "pack_config_sha256": digest,
                "provider_capability_state": (
                    "not_attested"
                    if any(
                        BRIDGE_PACK_POLICIES[pack].requires_capability_attestation
                        for pack in configured
                    )
                    else "not_required"
                ),
            }
        )
        try:
            self._save_internal(member_id, package)
            self._save_index(index)
            self._sync_public_report(index)
        except Exception:
            self._save_internal(member_id, prior_package)
            self._save_index(prior_index)
            self._sync_public_report(prior_index)
            raise
        return {
            "status": "configured-not-live",
            "member_id": member_id,
            "configured_pack_count": len(configured),
            "provider_capability_state": record["provider_capability_state"],
            "content_exposed": False,
        }

    def export(
        self,
        member_id: str,
        output: Path,
        *,
        central_url: str,
        transfer_key: bytes | None = None,
    ) -> BridgeExport:
        central_url = _validate_central_url(central_url)
        output = output.expanduser().resolve()
        if output.suffix != ".adminmebridge":
            raise BridgeEnrollmentError(
                "bridge transfer file must end in .adminmebridge"
            )
        if output.exists() and output.is_symlink():
            raise BridgeEnrollmentError("refusing a symlinked bridge transfer target")
        package = self._load_internal(member_id)
        index = self._index()
        prior_package = copy.deepcopy(package)
        prior_index = copy.deepcopy(index)
        prior_output = output.read_bytes() if output.is_file() else None
        services_were_running = self.services.status().status != "stopped"
        identity, record = self._record_for_member(index, member_id)
        if record.get("status") == "revoked":
            raise BridgeEnrollmentError("this bridge enrollment is revoked")
        old_reference = package.get("bootstrap_token_reference")
        key = transfer_key or os.urandom(32)
        if len(key) != 32:
            raise BridgeEnrollmentError("bridge transfer key must be 32 bytes")
        enrollment_id = f"bridge-enrollment-{os.urandom(12).hex()}"
        token_reference = self.store.put(
            f"bridge-{member_id}-{enrollment_id.removeprefix('bridge-enrollment-')}",
            key,
        )
        issued_at = self.now()
        expires_at = issued_at + timedelta(minutes=TRANSFER_TTL_MINUTES)
        package.update(
            {
                "bootstrap_token_reference": token_reference,
                "enrollment_id": enrollment_id,
            }
        )
        transfer = {
            "schema_version": 1,
            "enrollment_id": enrollment_id,
            "tenant_id": package["tenant_id"],
            "member_id": member_id,
            "owner_scope": package["owner_scope"],
            "tailscale_identity": identity,
            "role": package["role"],
            "allowed_packs": package["allowed_packs"],
            "configured_packs": package.get("configured_packs", []),
            # Transport acceptance never starts provider packs.
            "enabled_packs": [],
            "pack_config": package.get("pack_config", {}),
            "restrictions": package["restrictions"],
            "central_url": central_url,
            "release_commit": package["release_commit"],
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(
            nonce,
            json.dumps(transfer, sort_keys=True).encode("utf-8"),
            enrollment_id.encode("utf-8"),
        )
        try:
            self._save_internal(member_id, package)
            atomic_write(
                output,
                json.dumps(
                    {
                        "format": TRANSFER_FORMAT,
                        "enrollment_id": enrollment_id,
                        "nonce": base64.b64encode(nonce).decode("ascii"),
                        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                    },
                    sort_keys=True,
                ).encode("utf-8"),
            )
            record.update(
                {
                    "status": "package-exported",
                    "enrollment_id": enrollment_id,
                    "exported_at": issued_at.isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "export_path": str(output),
                    "allowed_packs": sorted(
                        _unique_pack_names(
                            package.get("allowed_packs"), label="allowed_packs"
                        )
                    ),
                    "configured_packs": sorted(
                        _unique_pack_names(
                            package.get("configured_packs", []),
                            label="configured_packs",
                        )
                    ),
                    "enabled_packs": [],
                    "pack_config_sha256": hashlib.sha256(
                        json.dumps(
                            package.get("pack_config", {}),
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest(),
                    "provider_capability_state": "not_attested",
                }
            )
            self._save_index(index)
            self._sync_public_report(index)
            restarted = self._restart_if_running()
        except Exception:
            self._save_internal(member_id, prior_package)
            self._save_index(prior_index)
            self._sync_public_report(prior_index)
            if prior_output is None:
                output.unlink(missing_ok=True)
            else:
                atomic_write(output, prior_output)
            self.store.delete(token_reference)
            if services_were_running:
                try:
                    if self.services.status().status != "stopped":
                        self.services.stop()
                    self.services.start()
                except Exception:
                    pass
            raise
        if (
            isinstance(old_reference, str)
            and old_reference != token_reference
            and self.store.has(old_reference)
        ):
            self.store.delete(old_reference)
        code = base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")
        return BridgeExport(
            public={
                "status": "package-exported",
                "member_id": member_id,
                "enrollment_id": enrollment_id,
                "path": str(output),
                "expires_at": expires_at.isoformat(),
                "central_url": central_url,
                "services_restarted": restarted,
                "transfer_code_stored": False,
            },
            transfer_code=code,
        )

    async def _find_enrollment_event(
        self, record: dict[str, Any]
    ) -> dict[str, Any] | None:
        references = json.loads(
            (self.paths.instance_dir / "config" / "secret-references.json").read_text(
                "utf-8"
            )
        )["references"]
        log = EventLog(
            self.paths.instance_dir / "events" / "events.db",
            self.store.get(references["event_log"]),
        )
        try:
            async for event in log.read_since(types=["bridge.enrolled"]):
                if event.get("source_account_id") == record.get(
                    "enrollment_id"
                ) and event.get("payload", {}).get("member_id") == record.get(
                    "member_id"
                ):
                    return event
        finally:
            await log.close()
        return None

    async def _find_capability_events(
        self, record: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        configured = configuration_from_record(record)
        if configured is None:
            raise BridgeEnrollmentError("active bridge configuration is invalid")
        references = json.loads(
            (self.paths.instance_dir / "config" / "secret-references.json").read_text(
                "utf-8"
            )
        )["references"]
        log = EventLog(
            self.paths.instance_dir / "events" / "events.db",
            self.store.get(references["event_log"]),
        )
        matches: dict[str, dict[str, Any]] = {}
        activation_event_id = record.get("event_id")
        if not isinstance(activation_event_id, str) or not activation_event_id:
            await log.close()
            raise BridgeEnrollmentError("active bridge activation event is missing")
        configuration_sha256 = hashlib.sha256(
            json.dumps(
                configured.pack_config,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        try:
            # Event ids are the durable log ordering boundary.  Comparing the
            # receipt's microsecond timestamp with SQLite's second-precision
            # ``recorded_at`` can incorrectly discard valid evidence emitted
            # immediately after activation in the same wall-clock second.
            async for event in log.read_since(
                activation_event_id, types=["adapter.authenticated"]
            ):
                for pack in configured.packs:
                    policy = BRIDGE_PACK_POLICIES[pack]
                    if not policy.requires_capability_attestation:
                        continue
                    payload = event.get("payload", {})
                    expected_account = configured.source_account_for(policy)
                    expected_scope = f"private:{configured.member_id}"
                    if (
                        event.get("source_adapter") == policy.source_adapter
                        and event.get("source_account_id") == expected_account
                        and event.get("owner_scope") == expected_scope
                        and event.get("visibility_scope") == expected_scope
                        and int(event.get("schema_version") or 1) == 2
                        and payload.get("adapter_name") == policy.source_adapter
                        and payload.get("adapter_family") == policy.family
                        and payload.get("token_storage_ref")
                        == policy.permission_reference
                        and frozenset(payload.get("scopes") or [])
                        == policy.permission_scopes
                        and payload.get("bridge_enrollment_id")
                        == configured.enrollment_id
                        and payload.get("configuration_sha256") == configuration_sha256
                        and payload.get("provider_account_id") == expected_account
                    ):
                        matches[pack] = event
        finally:
            await log.close()
        return matches

    def attest(self, member_id: str, packs: list[str] | None = None) -> dict[str, Any]:
        """Promote exact provider permission evidence; never call a provider."""

        index = self._index()
        prior_index = copy.deepcopy(index)
        services_were_running = self.services.status().status != "stopped"
        _identity, record = self._record_for_member(index, member_id)
        configured = configuration_from_record(record)
        if configured is None:
            raise BridgeEnrollmentError("member bridge transport is not active")
        requested = sorted(configured.packs if packs is None else set(packs))
        if not set(requested) <= configured.packs:
            raise BridgeEnrollmentError(
                "capability attestation names an unconfigured pack"
            )
        states = record.get("pack_capability_states")
        attestations = record.get("capability_attestations")
        if not isinstance(states, dict) or not isinstance(attestations, dict):
            raise BridgeEnrollmentError("bridge capability record is invalid")
        evidence = asyncio.run(self._find_capability_events(record))
        missing = [
            pack
            for pack in requested
            if BRIDGE_PACK_POLICIES[pack].requires_capability_attestation
            and pack not in evidence
        ]
        if missing:
            raise BridgeEnrollmentError(
                f"no exact adapter permission evidence for bridge packs: {missing}"
            )
        now = self.now().isoformat()
        for pack in requested:
            policy = BRIDGE_PACK_POLICIES[pack]
            if not policy.requires_capability_attestation:
                states[pack] = "not_required"
                continue
            event = evidence[pack]
            states[pack] = "attested"
            attestations[pack] = {
                "event_id": event["event_id"],
                "authenticated_at": event["payload"]["authenticated_at"],
                "attested_at": now,
                "permission_count": len(policy.permission_scopes),
                "content_exposed": False,
            }
        authorized = sorted(
            pack
            for pack in configured.packs
            if states.get(pack)
            == (
                "attested"
                if BRIDGE_PACK_POLICIES[pack].requires_capability_attestation
                else "not_required"
            )
        )
        provider_states = [
            states.get(pack)
            for pack in configured.packs
            if BRIDGE_PACK_POLICIES[pack].requires_capability_attestation
        ]
        provider_state = (
            "not_required"
            if not provider_states
            else "attested"
            if all(state == "attested" for state in provider_states)
            else "partial"
            if any(state == "attested" for state in provider_states)
            else "not_attested"
        )
        record.update(
            {
                "authorized_packs": authorized,
                "adapters_active": sorted(attestations),
                "pack_capability_states": states,
                "capability_attestations": attestations,
                "provider_capability_state": provider_state,
                "capability_attested_at": now,
            }
        )
        try:
            self._save_index(index)
            self._sync_public_report(index)
            restarted = self._restart_if_running()
        except Exception:
            self._save_index(prior_index)
            self._sync_public_report(prior_index)
            if services_were_running:
                try:
                    if self.services.status().status != "stopped":
                        self.services.stop()
                    self.services.start()
                except Exception:
                    pass
            raise
        return {
            "status": "attested",
            "member_id": member_id,
            "authorized_pack_count": len(authorized),
            "provider_capability_state": provider_state,
            "services_restarted": restarted,
            "content_exposed": False,
        }

    def _delete_transfer_material(
        self,
        member_id: str,
        record: dict[str, Any],
    ) -> None:
        try:
            package = self._load_internal(member_id)
        except BridgeEnrollmentError:
            package = {}
        reference = package.get("bootstrap_token_reference")
        if isinstance(reference, str):
            self.store.delete(reference)
        self._internal_path(member_id).unlink(missing_ok=True)
        export_path = record.get("export_path")
        if isinstance(export_path, str):
            candidate = Path(export_path)
            if candidate.is_file() and not candidate.is_symlink():
                candidate.unlink()

    def finalize(self, member_id: str) -> dict[str, Any]:
        index = self._index()
        _identity, record = self._record_for_member(index, member_id)
        if record.get("status") == "active":
            self._delete_transfer_material(member_id, record)
            for key in ("expires_at", "export_path"):
                record.pop(key, None)
            self._save_index(index)
            self._sync_public_report(index)
            return {
                "status": "active",
                "member_id": member_id,
                "idempotent": True,
                "bootstrap_material_revoked": True,
            }
        if record.get("status") != "package-exported":
            raise BridgeEnrollmentError("export this member's bridge package first")
        event = asyncio.run(self._find_enrollment_event(record))
        if event is None:
            raise BridgeEnrollmentError(
                "no owner-scoped enrollment proof has reached the central event log"
            )
        package = self._load_internal(member_id)
        allowed = _unique_pack_names(
            package.get("allowed_packs"), label="allowed_packs"
        )
        configured = _unique_pack_names(
            package.get("configured_packs", []), label="configured_packs"
        )
        claimed_active = _unique_pack_names(
            event.get("payload", {}).get("adapters_active", []),
            label="bridge.enrolled adapters_active",
        )
        if (
            not set(configured) <= set(allowed)
            or not set(allowed) <= BUILT_IN_BRIDGE_PACKS
        ):
            raise BridgeEnrollmentError("sealed bridge pack permissions are invalid")
        if not set(claimed_active) <= set(configured):
            raise BridgeEnrollmentError(
                "bridge enrollment claimed a pack outside the sealed enabled set"
            )
        if claimed_active:
            raise BridgeEnrollmentError(
                "transport enrollment cannot attest provider capability"
            )
        try:
            normalized_config = validate_configured_pack_config(
                member_id=member_id,
                enrollment_id=str(record.get("enrollment_id") or ""),
                configured_packs=configured,
                pack_config=package.get("pack_config", {}),
            )
        except ValueError as exc:
            raise BridgeEnrollmentError(str(exc)) from exc
        prior_index = copy.deepcopy(index)
        services_were_running = self.services.status().status != "stopped"
        capability_states = {
            pack: (
                "not_attested"
                if BRIDGE_PACK_POLICIES[pack].requires_capability_attestation
                else "not_required"
            )
            for pack in configured
        }
        initially_authorized = sorted(
            pack for pack, state in capability_states.items() if state == "not_required"
        )
        provider_states = [
            state
            for pack, state in capability_states.items()
            if BRIDGE_PACK_POLICIES[pack].requires_capability_attestation
        ]
        record.update(
            {
                "status": "active",
                "activated_at": self.now().isoformat(),
                "event_id": event["event_id"],
                "bridge_node_id": event.get("payload", {}).get("bridge_node_id"),
                "allowed_packs": sorted(allowed),
                "configured_packs": sorted(configured),
                "enabled_packs": [],
                "authorized_packs": initially_authorized,
                "adapters_active": [],
                "provider_capability_state": (
                    "not_attested" if provider_states else "not_required"
                ),
                "pack_capability_states": capability_states,
                "capability_attestations": {},
                "pack_config": normalized_config,
                "pack_config_sha256": hashlib.sha256(
                    json.dumps(
                        normalized_config,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
        try:
            # The active record is not independently safe merely because its
            # local fields validate.  Enforce the household-wide one-member /
            # one-enrollment / one-Apple-account ownership invariants before
            # the authoritative index is published.
            validate_bridge_record_set(index)
            self._save_index(index)
            self._sync_public_report(index)
            restarted = self._restart_if_running()
        except Exception:
            self._save_index(prior_index)
            self._sync_public_report(prior_index)
            if services_were_running:
                try:
                    if self.services.status().status != "stopped":
                        self.services.stop()
                    self.services.start()
                except Exception:
                    pass
            raise
        self._delete_transfer_material(member_id, record)
        for key in ("expires_at", "export_path"):
            record.pop(key, None)
        self._save_index(index)
        self._sync_public_report(index)
        return {
            "status": "active",
            "member_id": member_id,
            "event_id": event["event_id"],
            "bootstrap_material_revoked": True,
            "services_restarted": restarted,
        }

    def revoke(self, member_id: str) -> dict[str, Any]:
        index = self._index()
        _identity, record = self._record_for_member(index, member_id)
        running = self.services.status().status != "stopped"
        if running:
            self.services.stop()
        self._delete_transfer_material(member_id, record)
        record.update({"status": "revoked", "revoked_at": self.now().isoformat()})
        for key in ("expires_at", "export_path"):
            record.pop(key, None)
        self._save_index(index)
        self._sync_public_report(index)
        restarted = False
        if running:
            service = self.services.start()
            if service.status in {"failed", "degraded"}:
                raise BridgeEnrollmentError(
                    "bridge enrollment was revoked and services were left fail-closed because restart did not become healthy"
                )
            restarted = True
        return {
            "status": "revoked",
            "member_id": member_id,
            "portal_identity_unchanged": True,
            "services_restarted": restarted,
        }

    def status(self) -> dict[str, Any]:
        index = self._index()
        return {
            "status": "ok",
            "members": [
                {
                    key: value
                    for key, value in record.items()
                    if key not in {"export_path"}
                }
                for _identity, record in sorted(index.items())
            ],
        }


@dataclass(frozen=True)
class MemberBridgePaths:
    root: Path
    config_path: Path
    receipt_path: Path
    log_dir: Path
    launch_agent_path: Path
    service_state_path: Path
    device_proof_key_path: Path

    @classmethod
    def from_root(cls, root: Path, *, production: bool = False) -> "MemberBridgePaths":
        resolved = root.expanduser().resolve()
        if not resolved.is_absolute() or resolved in {
            Path(resolved.anchor),
            Path.home(),
        }:
            raise BridgeEnrollmentError("refusing a broad member-bridge root")
        launch_dir = (
            Path.home() / "Library" / "LaunchAgents"
            if production
            else resolved / "launchd"
        )
        log_dir = (
            Path.home() / "Library" / "Logs" / "AdministrateMe Bridge"
            if production
            else resolved / "logs"
        )
        return cls(
            root=resolved,
            config_path=resolved / "config.yaml",
            receipt_path=resolved / "enrollment-receipt.json",
            log_dir=log_dir,
            launch_agent_path=launch_dir / f"{BRIDGE_LABEL}.plist",
            service_state_path=resolved / "service-state.json",
            device_proof_key_path=resolved / "secrets" / "device-proof-key.bin",
        )

    @classmethod
    def default(cls) -> "MemberBridgePaths":
        return cls.from_root(
            Path.home() / "Library" / "Application Support" / "AdministrateMe Bridge",
            production=True,
        )

    def create(self) -> None:
        for path in (self.root, self.log_dir, self.launch_agent_path.parent):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)


Submitter = Callable[[dict[str, Any], dict[str, Any]], str]


class MemberBridgeInstaller:
    """Target-member install transaction; transfer material is never retained."""

    def __init__(
        self,
        paths: MemberBridgePaths,
        *,
        root: Path | None = None,
        test_mode: bool = False,
        now: Callable[[], datetime] = _now,
        submitter: Submitter | None = None,
    ) -> None:
        self.paths = paths
        self.root = (root or repository_root()).resolve()
        self.test_mode = test_mode
        self.now = now
        self.submitter = submitter or self._submit

    def _decrypt(self, transfer_path: Path, transfer_code: str) -> dict[str, Any]:
        try:
            envelope = json.loads(transfer_path.read_text("utf-8"))
            if envelope.get("format") != TRANSFER_FORMAT:
                raise BridgeEnrollmentError("unsupported bridge transfer format")
            if "signature" in envelope:
                try:
                    return inspect_transfer_package(
                        transfer_path.read_bytes(), transfer_code
                    )
                except WizardBridgeEnrollmentError as exc:
                    raise BridgeEnrollmentError(str(exc)) from exc
            enrollment_id = envelope["enrollment_id"]
            plaintext = AESGCM(_decode_transfer_code(transfer_code)).decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                enrollment_id.encode("utf-8"),
            )
            transfer = json.loads(plaintext)
        except BridgeEnrollmentError:
            raise
        except Exception as exc:
            raise BridgeEnrollmentError(
                "the bridge package or one-time transfer code is invalid"
            ) from exc
        if transfer.get("enrollment_id") != envelope.get("enrollment_id"):
            raise BridgeEnrollmentError("bridge transfer enrollment mismatch")
        return transfer

    def _tailscale_identity(self) -> tuple[str, str]:
        if self.test_mode:
            return "fixture@example.invalid", "fixture-bridge-node"
        completed = subprocess.run(
            [tailscale_executable(), "status", "--json"],
            env={**os.environ, "TAILSCALE_BE_CLI": "1"},
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise BridgeEnrollmentError("Tailscale is not connected on the member Mac")
        try:
            status = json.loads(completed.stdout)
            self_status = status["Self"]
            user_id = str(self_status["UserID"])
            user = status["User"][user_id]
            login = user["LoginName"]
            node_id = self_status.get("ID") or self_status.get("DNSName")
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise BridgeEnrollmentError(
                "could not resolve the signed-in Tailscale identity"
            ) from exc
        if not isinstance(login, str) or not isinstance(node_id, str):
            raise BridgeEnrollmentError(
                "Tailscale returned an incomplete self identity"
            )
        return login, node_id

    def _install_launch_agent(self) -> None:
        python_path = self.root / ".venv" / "bin" / "python"
        if self.test_mode:
            executable = shutil.which("python3") or shutil.which("python") or "python3"
        elif python_path.is_file():
            executable = str(python_path)
        else:
            raise BridgeEnrollmentError(
                "the reviewed member-bridge Python environment is missing"
            )
        definition = {
            "Label": BRIDGE_LABEL,
            "ProgramArguments": [
                executable,
                "-m",
                "adminme_bridge.main",
            ],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 30,
            "EnvironmentVariables": {
                "ADMINME_BRIDGE_CONFIG": str(self.paths.config_path),
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            },
            "StandardOutPath": str(self.paths.log_dir / "bridge.log"),
            "StandardErrorPath": str(self.paths.log_dir / "bridge-error.log"),
        }
        atomic_write(
            self.paths.launch_agent_path,
            plistlib.dumps(definition, fmt=plistlib.FMT_XML, sort_keys=True),
        )
        plistlib.loads(self.paths.launch_agent_path.read_bytes())

    def _start(self) -> None:
        if self.test_mode:
            atomic_write(self.paths.service_state_path, b'{"status":"running"}')
            return
        uid = _uid()
        domain = f"gui/{uid}"
        subprocess.run(
            ["launchctl", "bootout", f"{domain}/{BRIDGE_LABEL}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        completed = subprocess.run(
            ["launchctl", "bootstrap", domain, str(self.paths.launch_agent_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise BridgeEnrollmentError(
                f"launchd could not load the member bridge: {completed.stderr.strip()}"
            )
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"{domain}/{BRIDGE_LABEL}"],
            check=True,
        )

    def _stop(self) -> None:
        if self.test_mode:
            atomic_write(self.paths.service_state_path, b'{"status":"stopped"}')
            return
        uid = _uid()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{BRIDGE_LABEL}"],
            check=False,
        )

    @staticmethod
    def _submit(transfer: dict[str, Any], envelope: dict[str, Any]) -> str:
        ingest_client_class = importlib.import_module(
            "adminme_bridge.ingest_client"
        ).IngestClient

        async def send() -> str:
            client = ingest_client_class(
                central_endpoint_url=transfer["central_url"],
                tailscale_identity_header=transfer["tailscale_identity"],
                enrollment_id=transfer["enrollment_id"],
            )
            try:
                result = await client.submit(envelope)
            finally:
                await client.aclose()
            if not result.ok or not isinstance(result.event_id, str):
                raise BridgeEnrollmentError(
                    f"central owner-scoped enrollment proof failed: {result.error}"
                )
            return result.event_id

        return asyncio.run(send())

    def accept(
        self,
        transfer_path: Path,
        transfer_code: str,
        *,
        identity_override: str | None = None,
        node_override: str | None = None,
    ) -> dict[str, Any]:
        transfer_path = transfer_path.expanduser().resolve()
        if not transfer_path.is_file() or transfer_path.is_symlink():
            raise BridgeEnrollmentError("bridge transfer package is missing or unsafe")
        transfer = self._decrypt(transfer_path, transfer_code)
        if transfer.get("schema_version") != 1:
            raise BridgeEnrollmentError("unsupported bridge transfer schema")
        if self.now() >= _parse_time(transfer.get("expires_at")):
            raise BridgeEnrollmentError(
                "bridge transfer package expired; export a new one"
            )
        central_url = _validate_central_url(str(transfer.get("central_url", "")))
        if transfer.get("release_commit") != _git_commit(self.root):
            raise BridgeEnrollmentError(
                "bridge package and checked-out reviewed release do not match"
            )
        allowed = _unique_pack_names(
            transfer.get("allowed_packs"), label="allowed_packs"
        )
        configured = _unique_pack_names(
            transfer.get("configured_packs", []), label="configured_packs"
        )
        requested = _unique_pack_names(
            transfer.get("requested_packs", configured), label="requested_packs"
        )
        enabled = _unique_pack_names(
            transfer.get("enabled_packs", []), label="enabled_packs"
        )
        pack_config = transfer.get("pack_config")
        if (
            not set(allowed) <= BUILT_IN_BRIDGE_PACKS
            or not set(requested) <= set(allowed)
            or not set(configured) <= set(allowed)
            or enabled
            or not isinstance(pack_config, dict)
            or set(pack_config) != set(configured)
        ):
            raise BridgeEnrollmentError("bridge pack permissions are invalid")
        try:
            normalized_config = validate_configured_pack_config(
                member_id=str(transfer["member_id"]),
                enrollment_id=str(transfer["enrollment_id"]),
                configured_packs=configured,
                pack_config=pack_config,
            )
        except ValueError as exc:
            raise BridgeEnrollmentError(str(exc)) from exc
        restrictions = transfer.get("restrictions")
        if not isinstance(restrictions, dict):
            raise BridgeEnrollmentError("bridge restrictions are missing")
        from adminme.adapters.cos_channel_acquisition import (
            MEMBER_BRIDGE_ACQUISITION_CONTRACT,
            MEMBER_BRIDGE_CAPABILITIES,
        )

        acquisition_policy = restrictions.get("acquisition_policy")
        if "acquisition_policy" in restrictions and (
            acquisition_policy != MEMBER_BRIDGE_ACQUISITION_CONTRACT
            or transfer.get("wizard_contract") != WIZARD_CONTRACT
        ):
            raise BridgeEnrollmentError("bridge acquisition ceiling is invalid")
        if restrictions.get("kid") and "knowledge-obsidian" in allowed:
            raise BridgeEnrollmentError(
                "child bridge package incorrectly allows Obsidian"
            )
        if transfer.get("wizard_contract") == WIZARD_CONTRACT:
            requested_capabilities = _unique_pack_names(
                transfer.get("requested_capabilities"),
                label="requested_capabilities",
            )
            if len(requested_capabilities) != len(requested):
                raise BridgeEnrollmentError(
                    "bridge wizard capability and pack bindings disagree"
                )
            if restrictions.get("observation_only") is not True or (
                restrictions.get("provider_writes") != "deny"
            ):
                raise BridgeEnrollmentError(
                    "bridge wizard package is not observation-only"
                )
            if acquisition_policy == MEMBER_BRIDGE_ACQUISITION_CONTRACT:
                from adminme.bridge_enrollment import CAPABILITIES

                if (
                    not requested_capabilities
                    or not set(requested_capabilities) <= MEMBER_BRIDGE_CAPABILITIES
                    or set(allowed)
                    != {
                        str(CAPABILITIES[item]["pack"])
                        for item in requested_capabilities
                    }
                    or set(requested) != set(allowed)
                    or transfer.get("role") != "principal"
                    or any(
                        config.get("shared_container_ids", []) != []
                        for config in normalized_config.values()
                    )
                ):
                    raise BridgeEnrollmentError("bridge acquisition ceiling is invalid")
        observed_identity, observed_node = self._tailscale_identity()
        if identity_override is not None:
            if not self.test_mode:
                raise BridgeEnrollmentError("identity override is test-only")
            observed_identity = identity_override
        if node_override is not None:
            if not self.test_mode:
                raise BridgeEnrollmentError("node override is test-only")
            observed_node = node_override
        if (
            observed_identity.casefold()
            != str(transfer["tailscale_identity"]).casefold()
        ):
            raise BridgeEnrollmentError(
                "this Mac is signed into a different Tailscale identity than the package"
            )
        if self.paths.receipt_path.is_file():
            try:
                existing_receipt = json.loads(
                    self.paths.receipt_path.read_text("utf-8")
                )
            except json.JSONDecodeError as exc:
                raise BridgeEnrollmentError(
                    "an existing member-bridge receipt is invalid; preserve it for troubleshooting"
                ) from exc
            if existing_receipt.get("status") == "active":
                raise BridgeEnrollmentError(
                    "this Mac already has an active member bridge; disable and revoke it before enrolling a replacement"
                )
        self.paths.create()
        prior_config = (
            self.paths.config_path.read_bytes()
            if self.paths.config_path.exists()
            else None
        )
        prior_plist = (
            self.paths.launch_agent_path.read_bytes()
            if self.paths.launch_agent_path.exists()
            else None
        )
        prior_receipt = (
            self.paths.receipt_path.read_bytes()
            if self.paths.receipt_path.exists()
            else None
        )
        prior_device_key = (
            self.paths.device_proof_key_path.read_bytes()
            if self.paths.device_proof_key_path.exists()
            else None
        )
        pointer_path = self.paths.root / "app" / "current-release.json"
        prior_pointer = pointer_path.read_bytes() if pointer_path.exists() else None
        config = {
            "member_id": transfer["member_id"],
            "enrollment_id": transfer["enrollment_id"],
            "tenant_id": transfer["tenant_id"],
            "central_endpoint": central_url,
            "tailscale_identity": transfer["tailscale_identity"],
            "packs_dir": str(self.paths.root / "packs"),
            "cursor_db_path": str(self.paths.root / "adapter-cursors.db"),
            "configured_packs": configured,
            "requested_packs": requested,
            "wizard_contract": transfer.get("wizard_contract"),
            "request_id": transfer.get("request_id"),
            "instance_id": transfer.get("instance_id"),
            "release_commit": transfer.get("release_commit"),
            "capability_set_sha256": transfer.get("capability_set_sha256"),
            "requested_capabilities": list(transfer.get("requested_capabilities", [])),
            "enabled_packs": [],
            "pack_config": normalized_config,
            "log_level": "info",
        }
        if acquisition_policy == MEMBER_BRIDGE_ACQUISITION_CONTRACT:
            config["acquisition_policy"] = acquisition_policy
            config["observation_only"] = restrictions["observation_only"]
            config["provider_writes"] = restrictions["provider_writes"]
        try:
            atomic_write(
                self.paths.config_path,
                yaml.safe_dump(config, sort_keys=True).encode("utf-8"),
            )
            if transfer.get("wizard_contract") == WIZARD_CONTRACT:
                atomic_write(
                    self.paths.device_proof_key_path,
                    device_proof_key(_decode_transfer_code(transfer_code)),
                )
            self._install_launch_agent()
            self._start()
            now = self.now()
            envelope = EventEnvelope(
                event_at_ms=int(now.timestamp() * 1000),
                tenant_id=transfer["tenant_id"],
                type="bridge.enrolled",
                schema_version=1,
                occurred_at=now.isoformat(),
                source_adapter="member-bridge-bootstrap",
                source_account_id=transfer["enrollment_id"],
                owner_scope=transfer["owner_scope"],
                visibility_scope=transfer["owner_scope"],
                sensitivity="normal",
                idempotency_key=f"bridge-enrollment:{transfer['enrollment_id']}",
                payload={
                    "member_id": transfer["member_id"],
                    "bridge_node_id": observed_node,
                    "tailscale_identity": transfer["tailscale_identity"],
                    "adapters_active": [],
                    "enrolled_at": now.isoformat(),
                },
            ).model_dump(mode="json")
            event_id = self.submitter(transfer, envelope)
            receipt = {
                "schema_version": 1,
                "status": "active",
                "enrollment_id": transfer["enrollment_id"],
                "member_id": transfer["member_id"],
                "bridge_node_id": observed_node,
                "central_url": central_url,
                "event_id": event_id,
                "accepted_at": now.isoformat(),
                "configured_packs": configured,
                "requested_packs": requested,
                "requested_capabilities": list(
                    transfer.get("requested_capabilities", [])
                ),
                "wizard_contract": transfer.get("wizard_contract"),
                "request_id": transfer.get("request_id"),
                "instance_id": transfer.get("instance_id"),
                "release_commit": transfer.get("release_commit"),
                "capability_set_sha256": transfer.get("capability_set_sha256"),
                "enabled_packs": [],
                "provider_capability_state": (
                    "not_attested"
                    if any(
                        BRIDGE_PACK_POLICIES[pack].requires_capability_attestation
                        for pack in configured
                    )
                    else "not_required"
                ),
                "transfer_material_retained": False,
            }
            if acquisition_policy == MEMBER_BRIDGE_ACQUISITION_CONTRACT:
                receipt.update(
                    acquisition_policy=acquisition_policy,
                    observation_only=True,
                    provider_writes="deny",
                )
            atomic_write(
                self.paths.receipt_path,
                json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8"),
            )
            atomic_write(
                pointer_path,
                json.dumps(
                    {
                        "path": str(self.root),
                        "commit": transfer["release_commit"],
                    },
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8"),
            )
        except Exception:
            self._stop()
            if prior_config is None:
                self.paths.config_path.unlink(missing_ok=True)
            else:
                atomic_write(self.paths.config_path, prior_config)
            if prior_plist is None:
                self.paths.launch_agent_path.unlink(missing_ok=True)
            else:
                atomic_write(self.paths.launch_agent_path, prior_plist)
            if prior_receipt is None:
                self.paths.receipt_path.unlink(missing_ok=True)
            else:
                atomic_write(self.paths.receipt_path, prior_receipt)
            if prior_device_key is None:
                self.paths.device_proof_key_path.unlink(missing_ok=True)
            else:
                atomic_write(self.paths.device_proof_key_path, prior_device_key)
            if prior_pointer is None:
                pointer_path.unlink(missing_ok=True)
            else:
                atomic_write(pointer_path, prior_pointer)
            raise
        transfer_path.unlink()
        return receipt

    def status(self) -> dict[str, Any]:
        receipt: dict[str, Any] = {}
        if self.paths.receipt_path.is_file():
            try:
                receipt = json.loads(self.paths.receipt_path.read_text("utf-8"))
            except json.JSONDecodeError:
                receipt = {"status": "invalid-receipt"}
        if self.test_mode:
            try:
                service = json.loads(self.paths.service_state_path.read_text("utf-8"))[
                    "status"
                ]
            except (OSError, KeyError, json.JSONDecodeError):
                service = "stopped"
        else:
            uid = _uid()
            completed = subprocess.run(
                ["launchctl", "print", f"gui/{uid}/{BRIDGE_LABEL}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            service = "running" if completed.returncode == 0 else "stopped"
        provider_state = str(receipt.get("provider_capability_state") or "not_attested")
        transport_active = service == "running" and receipt.get("status") == "active"
        return {
            "status": "healthy"
            if transport_active and provider_state in {"attested", "not_required"}
            else "transport-ready"
            if transport_active
            else "stopped"
            if service == "stopped"
            else "degraded",
            "service": service,
            "transport_state": "active" if transport_active else "inactive",
            "provider_capability_state": provider_state,
            "configuration_present": self.paths.config_path.is_file(),
            "launch_agent_present": self.paths.launch_agent_path.is_file(),
            "enrollment": {
                key: receipt.get(key)
                for key in (
                    "status",
                    "member_id",
                    "bridge_node_id",
                    "central_url",
                    "accepted_at",
                    "enabled_packs",
                    "requested_capabilities",
                )
                if key in receipt
            },
        }

    def enable_pack(self, pack: str) -> dict[str, Any]:
        """Explicit member-side provider/permission action.

        Transport acceptance never calls this method. Running it may cause the
        selected adapter to request a macOS permission, so the stable CLI keeps
        it behind its own owner-invoked command.
        """

        if not self.paths.config_path.is_file():
            raise BridgeEnrollmentError("member bridge configuration is missing")
        raw = self.paths.config_path.read_bytes()
        value = yaml.safe_load(raw) or {}
        if not isinstance(value, dict):
            raise BridgeEnrollmentError("member bridge configuration is invalid")
        configured = _unique_pack_names(
            value.get("configured_packs", []), label="configured_packs"
        )
        enabled = _unique_pack_names(
            value.get("enabled_packs", []), label="enabled_packs"
        )
        if pack not in configured:
            raise BridgeEnrollmentError(
                "pack is not in the sealed bridge configuration"
            )
        if pack in enabled:
            return {
                "status": "enabled-awaiting-central-attestation",
                "pack": pack,
                "idempotent": True,
            }
        enabled.append(pack)
        value["enabled_packs"] = sorted(enabled)
        service_was_running = self.status().get("service") == "running"
        try:
            if service_was_running:
                self._stop()
            atomic_write(
                self.paths.config_path,
                yaml.safe_dump(value, sort_keys=True).encode("utf-8"),
            )
            self._start()
        except Exception:
            atomic_write(self.paths.config_path, raw)
            if service_was_running:
                try:
                    self._start()
                except Exception:
                    pass
            raise
        return {
            "status": "enabled-awaiting-central-attestation",
            "pack": pack,
            "provider_capability_state": "not_attested",
            "central_provider_authority": False,
        }

    def disable(self) -> dict[str, Any]:
        self._stop()
        receipt: dict[str, Any] = {}
        if self.paths.receipt_path.is_file():
            receipt = json.loads(self.paths.receipt_path.read_text("utf-8"))
        receipt.update({"status": "disabled", "disabled_at": self.now().isoformat()})
        atomic_write(
            self.paths.receipt_path,
            json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8"),
        )
        return {
            "status": "disabled",
            "configuration_preserved": self.paths.config_path.is_file(),
            "central_revocation_still_required": True,
        }

    def revoke_and_uninstall(self) -> dict[str, Any]:
        """Stop the helper and remove only reviewed bridge-owned material."""

        self._stop()
        removed: list[str] = []
        for path in (
            self.paths.config_path,
            self.paths.receipt_path,
            self.paths.launch_agent_path,
            self.paths.service_state_path,
            self.paths.device_proof_key_path,
        ):
            if path.is_file() and not path.is_symlink():
                path.unlink()
                removed.append(path.name)
        return {
            "status": "uninstalled",
            "removed": sorted(removed),
            "central_revocation_required": True,
            "provider_effects": False,
        }


__all__ = [
    "BRIDGE_LABEL",
    "BUILT_IN_BRIDGE_PACKS",
    "BridgeEnrollmentError",
    "BridgeEnrollmentManager",
    "BridgeExport",
    "MemberBridgeInstaller",
    "MemberBridgePaths",
    "TRANSFER_FORMAT",
]
