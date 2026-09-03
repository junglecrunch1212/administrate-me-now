"""Self-service orchestration for the existing member-bridge enrollment seam.

The authoritative transport record remains ``bridges/member-mapping.json`` and
the target still proves enrollment through ``bridge.enrolled``.  This module
adds the Settings-facing package ceremony around those contracts; it does not
create a second bridge transport or provider authorization path.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from adminme.bridge_authorization import (
    BRIDGE_PACK_POLICIES,
    validate_configured_pack_config,
)

TRANSFER_FORMAT = "administrateme-bridge-transfer-v1"
WIZARD_CONTRACT = "administrateme-member-bridge-wizard-v1"
PROGRESS_CONTRACT = "administrateme-member-bridge-progress-v1"
TRANSFER_TTL_MINUTES = 30

EnrollmentState = Literal[
    "not_requested",
    "requested",
    "package_preparing",
    "package_ready",
    "awaiting_transfer",
    "package_opened",
    "instance_verified",
    "member_confirmed",
    "device_identity_verified",
    "bridge_paired",
    "awaiting_permissions",
    "capabilities_testing",
    "settling",
    "active_observation",
    "partially_active",
    "degraded",
    "paused",
    "revoked",
    "expired",
    "failed",
]

ENROLLMENT_STATES: tuple[str, ...] = (
    "not_requested",
    "requested",
    "package_preparing",
    "package_ready",
    "awaiting_transfer",
    "package_opened",
    "instance_verified",
    "member_confirmed",
    "device_identity_verified",
    "bridge_paired",
    "awaiting_permissions",
    "capabilities_testing",
    "settling",
    "active_observation",
    "partially_active",
    "degraded",
    "paused",
    "revoked",
    "expired",
    "failed",
)

CAPABILITY_STATES: tuple[str, ...] = (
    "requested",
    "implementation_available",
    "transport_absent",
    "transport_incompatible",
    "transport_installed",
    "authentication_required",
    "authenticated",
    "prerequisite_missing",
    "permission_required",
    "permission_denied",
    "permission_verified",
    "adapter_authenticated",
    "observation_starting",
    "observing",
    "settling",
    "settled",
    "active",
    "degraded",
    "paused",
    "revoked",
    "unsupported",
)


CAPABILITIES: dict[str, dict[str, Any]] = {
    "apple_calendar": {
        "label": "Apple Calendar",
        "pack": "calendaring-apple-calendar-bridge",
        "implementation": "available",
        "data_categories": ["calendar containers", "events", "availability"],
        "local_permission": "Calendar access in Privacy & Security",
        "settings_url": "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars",
        "native_permission_ceiling": "EventKit access selected by the member",
        "administrateme_read_ceiling": "Observe selected calendar containers",
        "administrateme_write_ceiling": "None during enrollment and observation",
    },
    "apple_reminders": {
        "label": "Apple Reminders",
        "pack": "reminders-apple-eventkit-bridge",
        "implementation": "available",
        "data_categories": ["reminder lists", "reminders", "alarms"],
        "local_permission": "Reminders access in Privacy & Security",
        "settings_url": "x-apple.systempreferences:com.apple.preference.security?Privacy_Reminders",
        "native_permission_ceiling": "EventKit access selected by the member",
        "administrateme_read_ceiling": "Observe selected reminder lists",
        "administrateme_write_ceiling": "None during enrollment and observation",
    },
    "apple_contacts": {
        "label": "Apple Contacts",
        "pack": "contacts-apple-contacts-bridge",
        "implementation": "available",
        "data_categories": ["contact identifiers", "groups", "containers"],
        "local_permission": "Contacts access in Privacy & Security",
        "settings_url": "x-apple.systempreferences:com.apple.preference.security?Privacy_Contacts",
        "native_permission_ceiling": "Contacts access selected by the member",
        "administrateme_read_ceiling": "Observe selected contact containers",
        "administrateme_write_ceiling": "None during enrollment and observation",
    },
    "apple_notes": {
        "label": "Apple Notes",
        "pack": "knowledge-apple-notes",
        "implementation": "available",
        "data_categories": ["note metadata", "folders", "member-selected notes"],
        "local_permission": "Files and automation access required by the local connector",
        "settings_url": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
        "native_permission_ceiling": "Read access selected by the member",
        "administrateme_read_ceiling": "Observe the approved Notes source",
        "administrateme_write_ceiling": "None",
    },
    "apple_voice_memos": {
        "label": "Apple Voice Memos",
        "pack": "knowledge-apple-voice-memos",
        "implementation": "available",
        "data_categories": ["recording metadata", "member-selected transcripts"],
        "local_permission": "Files access required by the local connector",
        "settings_url": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
        "native_permission_ceiling": "Read access selected by the member",
        "administrateme_read_ceiling": "Observe the approved Voice Memos source",
        "administrateme_write_ceiling": "None",
    },
    "obsidian": {
        "label": "Obsidian",
        "pack": "knowledge-obsidian",
        "implementation": "available",
        "data_categories": ["member-selected vault files and metadata"],
        "local_permission": "Explicit folder selection",
        "settings_url": None,
        "native_permission_ceiling": "Read access to the selected vault",
        "administrateme_read_ceiling": "Observe the approved vault",
        "administrateme_write_ceiling": "None",
    },
    "apple_messages_read": {
        "label": "Apple Messages",
        "pack": "messaging-bluebubbles",
        "implementation": "available",
        "data_categories": [
            "message text",
            "chat and participant endpoints",
            "reply continuity",
            "attachment metadata only",
        ],
        "local_permission": (
            "Full Disk Access for reviewed BlueBubbles Server 1.9.9; "
            "AdministrateMe connects only to 127.0.0.1, and activation also "
            "requires host-firewall containment plus all BlueBubbles remote "
            "services and Private API disabled"
        ),
        "settings_url": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
        "native_permission_ceiling": (
            "Read the local Messages database through BlueBubbles Server 1.9.9"
        ),
        "administrateme_read_ceiling": (
            "Observe bounded personal message history/live changes and attachment metadata"
        ),
        "administrateme_write_ceiling": (
            "Exact text reply to an existing observed chat only after G11 is "
            "separately enabled and each reply is approved"
        ),
        "transport": {
            "name": "BlueBubbles Server",
            "version": "1.9.9",
            "commit": "ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1",
            "binary_sha256": (
                "fafd650c883f52e7494a6625e45249f2144d197378a4d57143ccf6198bb2e862"
            ),
            "install_mode": "guided_only_unnotarized_release",
            "network_boundary": "loopback_only",
            "private_api": False,
        },
    },
    "apple_messages_reply": {
        "label": "Apple Messages reply",
        "pack": "messaging-bluebubbles",
        "implementation": "available",
        "enrollment_selectable": False,
        "enrollment_note": (
            "Represented here for capability health; enable it separately only "
            "after Apple Messages read observation settles."
        ),
        "data_categories": ["exact text replies to existing observed chats"],
        "local_permission": (
            "BlueBubbles AppleScript text-message capability, verified locally; "
            "Private API remains disabled"
        ),
        "settings_url": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
        "native_permission_ceiling": "Text reply in one exact existing chat",
        "administrateme_read_ceiling": "None",
        "administrateme_write_ceiling": (
            "Ask every time; no new chat, attachment, reaction, edit, unsend, "
            "read receipt, typing indicator, bulk send, or autonomous CoS send"
        ),
        "transport": {
            "name": "BlueBubbles Server",
            "version": "1.9.9",
            "commit": "ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1",
            "mode": "bluebubbles-rest-apple-script-existing-chat-v1",
            "private_api": False,
        },
    },
}

SUPPORTED_CAPABILITIES = frozenset(
    key
    for key, value in CAPABILITIES.items()
    if value["implementation"] == "available"
    and value.get("enrollment_selectable", True) is True
)
TERMINAL_STATES = frozenset({"revoked", "expired", "failed"})

ENROLLMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "not_requested": frozenset({"requested"}),
    "requested": frozenset({"package_preparing", "revoked"}),
    "package_preparing": frozenset({"package_ready", "failed", "revoked"}),
    "package_ready": frozenset(
        {"awaiting_transfer", "package_opened", "expired", "revoked"}
    ),
    "awaiting_transfer": frozenset({"package_opened", "expired", "revoked"}),
    "package_opened": frozenset({"instance_verified", "expired", "failed", "revoked"}),
    "instance_verified": frozenset(
        {"member_confirmed", "expired", "failed", "revoked"}
    ),
    "member_confirmed": frozenset(
        {"device_identity_verified", "expired", "failed", "revoked"}
    ),
    "device_identity_verified": frozenset({"bridge_paired", "failed", "revoked"}),
    "bridge_paired": frozenset(
        {"awaiting_permissions", "degraded", "paused", "revoked"}
    ),
    "awaiting_permissions": frozenset(
        {"capabilities_testing", "partially_active", "degraded", "paused", "revoked"}
    ),
    "capabilities_testing": frozenset(
        {"settling", "partially_active", "degraded", "paused", "revoked"}
    ),
    "settling": frozenset(
        {"active_observation", "partially_active", "degraded", "paused", "revoked"}
    ),
    "active_observation": frozenset(
        {"partially_active", "degraded", "paused", "revoked"}
    ),
    "partially_active": frozenset(
        {
            "capabilities_testing",
            "settling",
            "active_observation",
            "degraded",
            "paused",
            "revoked",
        }
    ),
    "degraded": frozenset(
        {"capabilities_testing", "settling", "partially_active", "paused", "revoked"}
    ),
    "paused": frozenset(
        {
            "awaiting_permissions",
            "capabilities_testing",
            "settling",
            "active_observation",
            "partially_active",
            "degraded",
            "revoked",
        }
    ),
    "revoked": frozenset({"requested"}),
    "expired": frozenset({"requested"}),
    "failed": frozenset({"requested", "package_preparing", "revoked"}),
}

CAPABILITY_TRANSITIONS: dict[str, frozenset[str]] = {
    "requested": frozenset(
        {"implementation_available", "paused", "unsupported", "revoked"}
    ),
    "implementation_available": frozenset(
        {
            "transport_absent",
            "transport_incompatible",
            "transport_installed",
            "prerequisite_missing",
            "permission_required",
            "paused",
            "revoked",
        }
    ),
    "transport_absent": frozenset({"transport_installed", "paused", "revoked"}),
    "transport_incompatible": frozenset({"transport_installed", "paused", "revoked"}),
    "transport_installed": frozenset(
        {"authentication_required", "degraded", "paused", "revoked"}
    ),
    "authentication_required": frozenset(
        {"authenticated", "degraded", "paused", "revoked"}
    ),
    "authenticated": frozenset(
        {"permission_required", "degraded", "paused", "revoked"}
    ),
    "prerequisite_missing": frozenset({"permission_required", "paused", "revoked"}),
    "permission_required": frozenset(
        {"permission_denied", "permission_verified", "paused", "revoked"}
    ),
    "permission_denied": frozenset({"permission_required", "paused", "revoked"}),
    "permission_verified": frozenset(
        {"adapter_authenticated", "degraded", "paused", "revoked"}
    ),
    "adapter_authenticated": frozenset(
        {"observation_starting", "degraded", "paused", "revoked"}
    ),
    "observation_starting": frozenset({"observing", "degraded", "paused", "revoked"}),
    "observing": frozenset({"settling", "settled", "degraded", "paused", "revoked"}),
    "settling": frozenset({"settled", "active", "degraded", "paused", "revoked"}),
    "settled": frozenset({"observing", "active", "degraded", "paused", "revoked"}),
    "active": frozenset({"observing", "degraded", "paused", "revoked"}),
    "degraded": frozenset(
        {
            "permission_required",
            "adapter_authenticated",
            "observation_starting",
            "paused",
            "revoked",
        }
    ),
    "paused": frozenset(
        {
            "requested",
            "implementation_available",
            "transport_absent",
            "transport_incompatible",
            "transport_installed",
            "authentication_required",
            "authenticated",
            "prerequisite_missing",
            "permission_required",
            "permission_denied",
            "permission_verified",
            "adapter_authenticated",
            "observation_starting",
            "observing",
            "settling",
            "settled",
            "active",
            "degraded",
            "revoked",
        }
    ),
    "revoked": frozenset(),
    "unsupported": frozenset(),
}


class BridgeEnrollmentError(RuntimeError):
    """A stable, content-free bridge enrollment refusal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_enrollment_transition(previous: str, current: str) -> None:
    if previous not in ENROLLMENT_TRANSITIONS or current not in ENROLLMENT_STATES:
        raise BridgeEnrollmentError(
            "invalid_lifecycle_state", "Enrollment lifecycle state is invalid"
        )
    if current not in ENROLLMENT_TRANSITIONS[previous]:
        raise BridgeEnrollmentError(
            "invalid_lifecycle_transition",
            f"Enrollment cannot move from {previous} to {current}",
        )


def validate_capability_transition(previous: str, current: str) -> None:
    if previous not in CAPABILITY_TRANSITIONS or current not in CAPABILITY_STATES:
        raise BridgeEnrollmentError(
            "invalid_capability_state", "Capability lifecycle state is invalid"
        )
    if current not in CAPABILITY_TRANSITIONS[previous]:
        raise BridgeEnrollmentError(
            "invalid_capability_transition",
            f"Capability cannot move from {previous} to {current}",
        )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise BridgeEnrollmentError(
            "missing_timestamp", "Enrollment timestamp is missing"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BridgeEnrollmentError(
            "invalid_timestamp", "Enrollment timestamp is invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise BridgeEnrollmentError(
            "invalid_timestamp", "Enrollment timestamp has no timezone"
        )
    return parsed


def validate_central_url(value: str, *, allow_testserver: bool = False) -> str:
    parsed = urlparse(value.strip())
    test_host = allow_testserver and parsed.hostname in {"testserver", "localhost"}
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or (not parsed.hostname.endswith(".ts.net") and not test_host)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise BridgeEnrollmentError(
            "invalid_central_url",
            "Central URL must be the private HTTPS Tailscale Serve origin",
        )
    authority = (
        parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
    )
    return f"https://{authority}"


def reviewed_release(root: Path) -> str:
    supplied = os.environ.get("ADMINME_REVIEWED_COMMIT", "")
    if re.fullmatch(r"[0-9a-f]{40}", supplied):
        return supplied
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise BridgeEnrollmentError(
            "release_unavailable", "A reviewed release commit is required"
        )
    return value


def capability_set_digest(capabilities: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(capabilities), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def encode_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_key(value: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(
            (value + "=" * (-len(value) % 4)).encode("ascii")
        )
    except (UnicodeEncodeError, ValueError) as exc:
        raise BridgeEnrollmentError(
            "invalid_transfer_code", "Transfer code is invalid"
        ) from exc
    if len(raw) != 32:
        raise BridgeEnrollmentError("invalid_transfer_code", "Transfer code is invalid")
    return raw


def progress_signature(proof_key: bytes, payload: Mapping[str, Any]) -> str:
    return encode_key(hmac.digest(proof_key, canonical_bytes(payload), "sha256"))


def verify_progress_signature(
    proof_key: bytes, payload: Mapping[str, Any], signature: str
) -> bool:
    return hmac.compare_digest(progress_signature(proof_key, payload), signature)


def device_proof_key(transfer_key: bytes) -> bytes:
    return hmac.digest(
        transfer_key, b"administrateme-member-bridge-device-proof-v1", "sha256"
    )


def inspect_transfer_package(
    package_bytes: bytes, transfer_code: str
) -> dict[str, Any]:
    """Verify and decrypt a handoff without installing or reading a provider."""

    try:
        envelope = json.loads(package_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeEnrollmentError(
            "invalid_package", "Enrollment package is invalid"
        ) from exc
    if envelope.get("format") != TRANSFER_FORMAT:
        raise BridgeEnrollmentError(
            "unsupported_package", "Enrollment package format is unsupported"
        )
    key = decode_key(transfer_code)
    signed = {
        "format": envelope.get("format"),
        "enrollment_id": envelope.get("enrollment_id"),
        "nonce": envelope.get("nonce"),
        "ciphertext": envelope.get("ciphertext"),
    }
    signature = envelope.get("signature")
    if not isinstance(signature, str) or not hmac.compare_digest(
        progress_signature(key, signed), signature
    ):
        raise BridgeEnrollmentError(
            "package_signature_invalid", "Enrollment package signature is invalid"
        )
    try:
        plaintext = AESGCM(key).decrypt(
            base64.b64decode(str(envelope["nonce"])),
            base64.b64decode(str(envelope["ciphertext"])),
            str(envelope["enrollment_id"]).encode("utf-8"),
        )
        transfer = json.loads(plaintext)
    except Exception as exc:
        raise BridgeEnrollmentError(
            "package_decryption_failed",
            "Enrollment package or transfer code is invalid",
        ) from exc
    if transfer.get("enrollment_id") != envelope.get("enrollment_id"):
        raise BridgeEnrollmentError(
            "enrollment_mismatch", "Enrollment package identifier does not match"
        )
    if transfer.get("wizard_contract") != WIZARD_CONTRACT:
        raise BridgeEnrollmentError(
            "binding_missing", "Enrollment package lacks the wizard binding"
        )
    capabilities = transfer.get("requested_capabilities")
    if (
        not isinstance(capabilities, list)
        or any(item not in SUPPORTED_CAPABILITIES for item in capabilities)
        or transfer.get("capability_set_sha256") != capability_set_digest(capabilities)
    ):
        raise BridgeEnrollmentError(
            "capability_binding_invalid", "Enrollment capability binding is invalid"
        )
    return transfer


def _atomic_bytes(path: Path, value: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise BridgeEnrollmentError(
            "unsafe_path", "Refusing a symlinked enrollment artifact"
        )
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class EncryptedHandoffVault:
    """Small encrypted store for transfer and device-proof keys."""

    FORMAT = "administrateme-bridge-handoff-vault-v1"

    def __init__(self, path: Path, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("bridge handoff vault key must be 32 bytes")
        self.path = path
        self.key = key

    def read(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            envelope = json.loads(self.path.read_text("utf-8"))
            if envelope.get("format") != self.FORMAT:
                raise ValueError("format")
            plaintext = AESGCM(self.key).decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                self.FORMAT.encode("ascii"),
            )
            value = json.loads(plaintext)
        except Exception as exc:
            raise BridgeEnrollmentError(
                "handoff_vault_invalid", "Encrypted bridge handoff vault is unavailable"
            ) from exc
        if not isinstance(value, dict):
            raise BridgeEnrollmentError(
                "handoff_vault_invalid", "Encrypted bridge handoff vault is invalid"
            )
        return value

    def write(self, value: dict[str, dict[str, Any]]) -> None:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.key).encrypt(
            nonce, canonical_bytes(value), self.FORMAT.encode("ascii")
        )
        _atomic_bytes(
            self.path,
            canonical_bytes(
                {
                    "format": self.FORMAT,
                    "nonce": base64.b64encode(nonce).decode("ascii"),
                    "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                }
            ),
        )

    def put(self, request_id: str, value: dict[str, Any]) -> None:
        state = self.read()
        state[request_id] = value
        self.write(state)

    def get(self, request_id: str) -> dict[str, Any]:
        value = self.read().get(request_id)
        if not isinstance(value, dict):
            raise BridgeEnrollmentError(
                "handoff_secret_missing", "Enrollment handoff secret is unavailable"
            )
        return value

    def delete(self, request_id: str) -> None:
        state = self.read()
        if request_id in state:
            del state[request_id]
            self.write(state)


@dataclass(frozen=True)
class PreparedHandoff:
    request_id: str
    enrollment_id: str
    expires_at: str
    release_commit: str
    package_sha256: str
    capability_set_sha256: str


class BridgeEnrollmentCoordinator:
    """Persist a wizard handoff into the existing bridge transport records."""

    def __init__(
        self,
        *,
        instance_dir: Path,
        encryption_key: bytes,
        repository_root: Path,
        tenant_id: str,
        member_identities: Mapping[str, str],
        member_roles: Mapping[str, str],
        bridge_enrollments: dict[str, dict[str, Any]],
        now: Callable[[], datetime] = now_utc,
    ) -> None:
        self.instance_dir = instance_dir
        self.repository_root = repository_root.resolve()
        self.tenant_id = tenant_id
        self.member_identities = dict(member_identities)
        self.member_roles = dict(member_roles)
        self.bridge_enrollments = bridge_enrollments
        self.now = now
        self.package_dir = instance_dir / "bridges" / "handoffs"
        self.index_path = instance_dir / "bridges" / "member-mapping.json"
        self.vault = EncryptedHandoffVault(
            instance_dir / "bridges" / "handoff-secrets.json.enc", encryption_key
        )

    def _identity_for_member(self, member_id: str) -> str:
        matches = [
            identity
            for identity, mapped_member in self.member_identities.items()
            if mapped_member == member_id
        ]
        if len(matches) != 1:
            raise BridgeEnrollmentError(
                "member_identity_ambiguous",
                "The authenticated member needs one exact Tailscale identity before enrollment",
            )
        return matches[0]

    def _save_index(self) -> None:
        _atomic_bytes(
            self.index_path,
            (
                json.dumps(self.bridge_enrollments, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8"),
        )

    def _package_path(self, request_id: str) -> Path:
        digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        return self.package_dir / f"{digest}.adminmebridge"

    def prepare(
        self,
        *,
        request_id: str,
        member_id: str,
        device_class: str,
        capabilities: list[str],
        central_url: str,
        allow_testserver: bool = False,
    ) -> PreparedHandoff:
        if device_class not in {"this_mac", "another_mac"}:
            raise BridgeEnrollmentError(
                "invalid_device_class", "Choose this Mac or another Mac"
            )
        selected = sorted(set(capabilities))
        if not selected or set(selected) - SUPPORTED_CAPABILITIES:
            raise BridgeEnrollmentError(
                "invalid_capabilities", "Enrollment capabilities are invalid"
            )
        role = self.member_roles.get(member_id)
        if role not in {"principal", "child"}:
            raise BridgeEnrollmentError(
                "invalid_member_role",
                "Bridge enrollment requires a governed household member",
            )
        identity = self._identity_for_member(member_id)
        for record in self.bridge_enrollments.values():
            if not isinstance(record, dict) or record.get("member_id") != member_id:
                continue
            if (
                record.get("request_id") == request_id
                and record.get("status") == "package-exported"
            ):
                secret = self.vault.get(request_id)
                return PreparedHandoff(
                    request_id=request_id,
                    enrollment_id=str(record["enrollment_id"]),
                    expires_at=str(record["expires_at"]),
                    release_commit=str(record["release_commit"]),
                    package_sha256=str(secret["package_sha256"]),
                    capability_set_sha256=str(record["capability_set_sha256"]),
                )
            if record.get("status") in {
                "package-created-not-live",
                "package-exported",
                "active",
            }:
                raise BridgeEnrollmentError(
                    "duplicate_enrollment",
                    "This member already has a pending or active bridge enrollment",
                )

        central = validate_central_url(central_url, allow_testserver=allow_testserver)
        release = reviewed_release(self.repository_root)
        issued = self.now()
        expires = issued + timedelta(minutes=TRANSFER_TTL_MINUTES)
        enrollment_id = f"bridge-enrollment-{os.urandom(12).hex()}"
        capability_digest = capability_set_digest(selected)
        packs = sorted({str(CAPABILITIES[item]["pack"]) for item in selected})
        transfer = {
            "schema_version": 1,
            "wizard_contract": WIZARD_CONTRACT,
            "request_id": request_id,
            "enrollment_id": enrollment_id,
            "instance_id": self.tenant_id,
            "tenant_id": self.tenant_id,
            "member_id": member_id,
            "owner_scope": f"private:{member_id}",
            "tailscale_identity": identity,
            "role": role,
            "device_class": device_class,
            "requested_capabilities": selected,
            "capability_set_sha256": capability_digest,
            "requested_packs": packs,
            "allowed_packs": packs,
            "configured_packs": [],
            "enabled_packs": [],
            "pack_config": {},
            "restrictions": {
                "kid": role == "child",
                "obsidian_allowed": role != "child",
                "cross_owner_claims": "deny",
                "central_owner_stamping": True,
                "observation_only": True,
                "provider_writes": "deny",
                "remote_shell": "deny",
                "automatic_updates": "deny",
            },
            "central_url": central,
            "release_commit": release,
            "issued_at": issued.isoformat(),
            "expires_at": expires.isoformat(),
        }
        transfer_key = os.urandom(32)
        nonce = os.urandom(12)
        ciphertext = AESGCM(transfer_key).encrypt(
            nonce, canonical_bytes(transfer), enrollment_id.encode("utf-8")
        )
        signed = {
            "format": TRANSFER_FORMAT,
            "enrollment_id": enrollment_id,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        envelope = {**signed, "signature": progress_signature(transfer_key, signed)}
        package_bytes = canonical_bytes(envelope)
        package_sha256 = hashlib.sha256(package_bytes).hexdigest()
        package_path = self._package_path(request_id)

        prior_records = json.loads(json.dumps(self.bridge_enrollments))
        prior_package = package_path.read_bytes() if package_path.is_file() else None
        try:
            _atomic_bytes(package_path, package_bytes)
            self.vault.put(
                request_id,
                {
                    "enrollment_id": enrollment_id,
                    "member_id": member_id,
                    "expires_at": expires.isoformat(),
                    "transfer_key": encode_key(transfer_key),
                    "proof_key": encode_key(device_proof_key(transfer_key)),
                    "package_sha256": package_sha256,
                    "used_nonces": [],
                },
            )
            self.bridge_enrollments[identity] = {
                "member_id": member_id,
                "owner_scope": f"private:{member_id}",
                "role": role,
                "kid_restrictions": role == "child",
                "status": "package-exported",
                "request_id": request_id,
                "enrollment_id": enrollment_id,
                "instance_id": self.tenant_id,
                "release_commit": release,
                "capability_set_sha256": capability_digest,
                "requested_capabilities": selected,
                "allowed_packs": packs,
                "configured_packs": [],
                "enabled_packs": [],
                "authorized_packs": [],
                "adapters_active": [],
                "pack_config": {},
                "pack_capability_states": {},
                "capability_attestations": {},
                "provider_capability_state": "not_attested",
                "exported_at": issued.isoformat(),
                "expires_at": expires.isoformat(),
                "observation_only": True,
            }
            self._save_index()
        except Exception:
            self.bridge_enrollments.clear()
            self.bridge_enrollments.update(prior_records)
            if prior_package is None:
                package_path.unlink(missing_ok=True)
            else:
                _atomic_bytes(package_path, prior_package)
            raise
        return PreparedHandoff(
            request_id=request_id,
            enrollment_id=enrollment_id,
            expires_at=expires.isoformat(),
            release_commit=release,
            package_sha256=package_sha256,
            capability_set_sha256=capability_digest,
        )

    def download(self, *, request_id: str, member_id: str) -> tuple[bytes, str]:
        secret = self.vault.get(request_id)
        if secret.get("member_id") != member_id:
            raise BridgeEnrollmentError(
                "wrong_member", "Enrollment handoff belongs to another member"
            )
        if self.now() >= parse_time(secret.get("expires_at")):
            self.expire(request_id=request_id, member_id=member_id)
            raise BridgeEnrollmentError(
                "expired", "Enrollment package expired; regenerate it"
            )
        path = self._package_path(request_id)
        if not path.is_file() or path.is_symlink():
            raise BridgeEnrollmentError(
                "package_missing", "Enrollment package is unavailable"
            )
        package = path.read_bytes()
        if hashlib.sha256(package).hexdigest() != secret.get("package_sha256"):
            raise BridgeEnrollmentError(
                "package_tampered", "Enrollment package checksum changed"
            )
        return package, str(secret["transfer_key"])

    def verify_progress(
        self,
        *,
        request_id: str,
        enrollment_id: str,
        member_id: str,
        tailscale_identity: str,
        payload: Mapping[str, Any],
        signature: str,
    ) -> dict[str, Any]:
        secret = self.vault.get(request_id)
        if (
            secret.get("member_id") != member_id
            or secret.get("enrollment_id") != enrollment_id
        ):
            raise BridgeEnrollmentError(
                "wrong_member_or_enrollment",
                "Enrollment progress binding does not match",
            )
        expected_identity = self._identity_for_member(member_id)
        if not hmac.compare_digest(
            expected_identity.casefold(), tailscale_identity.casefold()
        ):
            raise BridgeEnrollmentError(
                "wrong_tailscale_identity",
                "Target Mac Tailscale identity does not match the member",
            )
        record = self.record(request_id=request_id, member_id=member_id)
        if record.get("status") == "package-exported" and self.now() >= parse_time(
            secret.get("expires_at")
        ):
            raise BridgeEnrollmentError("expired", "Enrollment package expired")
        bindings = {
            "contract": PROGRESS_CONTRACT,
            "request_id": request_id,
            "enrollment_id": enrollment_id,
            "member_id": member_id,
            "instance_id": record.get("instance_id"),
            "release_commit": record.get("release_commit"),
            "capability_set_sha256": record.get("capability_set_sha256"),
        }
        if any(payload.get(key) != value for key, value in bindings.items()):
            raise BridgeEnrollmentError(
                "progress_binding_mismatch",
                "Enrollment progress does not match the package bindings",
            )
        proof_key = decode_key(str(secret["proof_key"]))
        if not verify_progress_signature(proof_key, payload, signature):
            raise BridgeEnrollmentError(
                "progress_signature_invalid", "Enrollment progress signature is invalid"
            )
        nonce = payload.get("nonce")
        if not isinstance(nonce, str) or len(nonce) < 16:
            raise BridgeEnrollmentError(
                "progress_nonce_invalid", "Enrollment progress nonce is invalid"
            )
        used = secret.get("used_nonces")
        if not isinstance(used, list):
            raise BridgeEnrollmentError(
                "handoff_secret_invalid", "Enrollment replay state is invalid"
            )
        nonce_digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        if nonce_digest in used:
            raise BridgeEnrollmentError(
                "progress_replayed", "Enrollment progress proof was already used"
            )
        used.append(nonce_digest)
        secret["used_nonces"] = used[-256:]
        self.vault.put(request_id, secret)
        return dict(payload)

    def bind_device(self, *, request_id: str, member_id: str, node_id: str) -> None:
        record = self.record(request_id=request_id, member_id=member_id)
        digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()
        prior = record.get("device_identity_sha256")
        if prior is not None and not hmac.compare_digest(str(prior), digest):
            raise BridgeEnrollmentError(
                "wrong_device", "Enrollment package was opened on another Mac"
            )
        record["device_identity_sha256"] = digest
        self._save_index()

    def activate_transport(
        self, *, request_id: str, member_id: str, bridge_node_id: str, event_id: str
    ) -> None:
        record = self.record(request_id=request_id, member_id=member_id)
        if record.get("status") != "package-exported":
            raise BridgeEnrollmentError(
                "duplicate_acceptance", "Enrollment package has already been accepted"
            )
        self.bind_device(
            request_id=request_id, member_id=member_id, node_id=bridge_node_id
        )
        record.update(
            {
                "status": "active",
                "activated_at": self.now().isoformat(),
                "acceptance_consumed_at": self.now().isoformat(),
                "event_id": event_id,
                "bridge_node_id": bridge_node_id,
                "configured_packs": [],
                "enabled_packs": [],
                "authorized_packs": [],
                "adapters_active": [],
                "pack_config": {},
                "pack_capability_states": {},
                "capability_attestations": {},
                "provider_capability_state": "not_attested",
            }
        )
        record.pop("expires_at", None)
        self._save_index()
        self._package_path(request_id).unlink(missing_ok=True)
        secret = self.vault.get(request_id)
        secret.pop("transfer_key", None)
        self.vault.put(request_id, secret)

    def configure_capability(
        self,
        *,
        request_id: str,
        member_id: str,
        capability_id: str,
        pack_config: dict[str, Any],
    ) -> str:
        """Seal one exact, content-free source coordinate without starting it."""

        if capability_id not in SUPPORTED_CAPABILITIES:
            raise BridgeEnrollmentError(
                "unsupported_capability", "Capability is not supported"
            )
        record = self.record(request_id=request_id, member_id=member_id)
        if record.get("status") != "active":
            raise BridgeEnrollmentError(
                "bridge_not_active", "Bridge transport is not active"
            )
        pack = str(CAPABILITIES[capability_id]["pack"])
        if pack not in record.get("allowed_packs", []):
            raise BridgeEnrollmentError(
                "capability_not_requested",
                "Capability was not selected for this enrollment",
            )
        combined = dict(record.get("pack_config") or {})
        combined[pack] = dict(pack_config)
        configured = sorted(set(record.get("configured_packs") or []) | {pack})
        try:
            normalized = validate_configured_pack_config(
                member_id=member_id,
                enrollment_id=str(record.get("enrollment_id") or ""),
                configured_packs=configured,
                pack_config=combined,
            )
        except ValueError as exc:
            raise BridgeEnrollmentError("source_coordinate_invalid", str(exc)) from exc
        if (
            pack == "messaging-bluebubbles"
            and normalized[pack].get("device_identity_sha256")
            != hashlib.sha256(
                str(record.get("bridge_node_id") or "").encode()
            ).hexdigest()
        ):
            raise BridgeEnrollmentError(
                "device_identity_mismatch",
                "Messages source must bind to the enrolled bridge device",
            )
        policy = BRIDGE_PACK_POLICIES[pack]
        states = dict(record.get("pack_capability_states") or {})
        states[pack] = (
            "not_attested" if policy.requires_capability_attestation else "not_required"
        )
        record.update(
            {
                "configured_packs": configured,
                "enabled_packs": [
                    value
                    for value in record.get("enabled_packs", [])
                    if value in configured
                ],
                "authorized_packs": [
                    value
                    for value in record.get("authorized_packs", [])
                    if value in configured
                ],
                "pack_config": normalized,
                "pack_capability_states": states,
                "provider_capability_state": "not_attested",
            }
        )
        self._save_index()
        return hashlib.sha256(canonical_bytes(normalized[pack])).hexdigest()

    def attest_capability(
        self,
        *,
        request_id: str,
        member_id: str,
        capability_id: str,
        evidence_event: Mapping[str, Any] | None,
    ) -> None:
        """Apply central attestation after exact signed local evidence."""

        record = self.record(request_id=request_id, member_id=member_id)
        pack = str(CAPABILITIES.get(capability_id, {}).get("pack") or "")
        if pack not in record.get("configured_packs", []):
            raise BridgeEnrollmentError(
                "capability_not_configured",
                "Capability source coordinate is not configured",
            )
        policy = BRIDGE_PACK_POLICIES[pack]
        event_id: str | None = None
        if policy.requires_capability_attestation:
            if not isinstance(evidence_event, Mapping):
                raise BridgeEnrollmentError(
                    "attestation_missing",
                    "Exact adapter authentication evidence is missing",
                )
            payload = evidence_event.get("payload")
            config = record["pack_config"][pack]
            expected_account = config.get("provider_account_id")
            configuration_sha256 = hashlib.sha256(
                canonical_bytes(record["pack_config"])
            ).hexdigest()
            if (
                evidence_event.get("type") != "adapter.authenticated"
                or int(evidence_event.get("schema_version") or 0) != 2
                or evidence_event.get("source_adapter") != policy.source_adapter
                or evidence_event.get("source_account_id") != expected_account
                or evidence_event.get("owner_scope") != f"private:{member_id}"
                or not isinstance(payload, Mapping)
                or payload.get("adapter_name") != policy.source_adapter
                or payload.get("adapter_family") != policy.family
                or payload.get("token_storage_ref") != policy.permission_reference
                or frozenset(payload.get("scopes") or []) != policy.permission_scopes
                or payload.get("bridge_enrollment_id") != record.get("enrollment_id")
                or payload.get("configuration_sha256") != configuration_sha256
                or payload.get("provider_account_id") != expected_account
            ):
                raise BridgeEnrollmentError(
                    "attestation_mismatch",
                    "Adapter evidence does not match the exact enrollment coordinate",
                )
            event_id = str(evidence_event.get("event_id") or "")
            if not event_id:
                raise BridgeEnrollmentError(
                    "attestation_missing", "Adapter evidence event id is missing"
                )
        states = dict(record.get("pack_capability_states") or {})
        states[pack] = (
            "attested" if policy.requires_capability_attestation else "not_required"
        )
        attestations = dict(record.get("capability_attestations") or {})
        if event_id is not None:
            attestations[pack] = {
                "event_id": event_id,
                "attested_at": self.now().isoformat(),
                "content_exposed": False,
            }
        authorized = sorted(set(record.get("authorized_packs") or []) | {pack})
        provider_states = [
            states.get(value)
            for value in record.get("configured_packs", [])
            if BRIDGE_PACK_POLICIES[value].requires_capability_attestation
        ]
        global_state = (
            "not_required"
            if not provider_states
            else "attested"
            if all(value == "attested" for value in provider_states)
            else "partial"
            if any(value == "attested" for value in provider_states)
            else "not_attested"
        )
        record.update(
            {
                "authorized_packs": authorized,
                "adapters_active": sorted(attestations),
                "pack_capability_states": states,
                "capability_attestations": attestations,
                "provider_capability_state": global_state,
            }
        )
        self._save_index()

    def mark_observation_started(
        self, *, request_id: str, member_id: str, capability_id: str
    ) -> None:
        record = self.record(request_id=request_id, member_id=member_id)
        pack = str(CAPABILITIES.get(capability_id, {}).get("pack") or "")
        if pack not in record.get("authorized_packs", []):
            raise BridgeEnrollmentError(
                "central_attestation_missing", "Capability is not centrally attested"
            )
        record["enabled_packs"] = sorted(
            set(record.get("enabled_packs") or []) | {pack}
        )
        self._save_index()

    def record(self, *, request_id: str, member_id: str) -> dict[str, Any]:
        matches = [
            value
            for value in self.bridge_enrollments.values()
            if isinstance(value, dict)
            and value.get("request_id") == request_id
            and value.get("member_id") == member_id
        ]
        if len(matches) != 1:
            raise BridgeEnrollmentError(
                "enrollment_missing_or_ambiguous",
                "Enrollment record is missing or ambiguous",
            )
        return matches[0]

    def pause(self, *, request_id: str, member_id: str) -> None:
        record = self.record(request_id=request_id, member_id=member_id)
        if record.get("status") != "active":
            raise BridgeEnrollmentError(
                "bridge_not_active", "Only an active bridge can be paused"
            )
        record["status"] = "paused"
        record["paused_at"] = self.now().isoformat()
        record["paused_authorized_packs"] = list(record.get("authorized_packs") or [])
        record["paused_enabled_packs"] = list(record.get("enabled_packs") or [])
        record["enabled_packs"] = []
        self._save_index()

    def resume(self, *, request_id: str, member_id: str) -> None:
        record = self.record(request_id=request_id, member_id=member_id)
        if record.get("status") != "paused":
            raise BridgeEnrollmentError(
                "bridge_not_paused", "Only a paused bridge can be resumed"
            )
        record["status"] = "active"
        record["resumed_at"] = self.now().isoformat()
        record["authorized_packs"] = list(
            record.pop("paused_authorized_packs", record.get("authorized_packs") or [])
        )
        record["enabled_packs"] = list(record.pop("paused_enabled_packs", []))
        self._save_index()

    def revoke(self, *, request_id: str, member_id: str) -> None:
        record = self.record(request_id=request_id, member_id=member_id)
        record.update(
            {
                "status": "revoked",
                "revoked_at": self.now().isoformat(),
                "enabled_packs": [],
                "authorized_packs": [],
                "adapters_active": [],
            }
        )
        record.pop("paused_authorized_packs", None)
        record.pop("paused_enabled_packs", None)
        self._save_index()
        self._package_path(request_id).unlink(missing_ok=True)
        self.vault.delete(request_id)

    def expire(self, *, request_id: str, member_id: str) -> None:
        record = self.record(request_id=request_id, member_id=member_id)
        if record.get("status") == "package-exported":
            record["status"] = "revoked"
            record["expired_at"] = self.now().isoformat()
            self._save_index()
        self._package_path(request_id).unlink(missing_ok=True)
        self.vault.delete(request_id)

    def progress_proof_key_for_test(self, request_id: str) -> bytes:
        """Credential-free lab hook; callers must already own the fixture root."""

        if os.environ.get("ADMINME_TEST_MODE") != "1":
            raise BridgeEnrollmentError(
                "test_only", "Progress proof key access is test-only"
            )
        return decode_key(str(self.vault.get(request_id)["proof_key"]))


def capability_catalog() -> list[dict[str, Any]]:
    return [
        {"capability_id": capability_id, **json.loads(json.dumps(value))}
        for capability_id, value in CAPABILITIES.items()
    ]


__all__ = [
    "CAPABILITIES",
    "CAPABILITY_STATES",
    "ENROLLMENT_STATES",
    "PROGRESS_CONTRACT",
    "SUPPORTED_CAPABILITIES",
    "TRANSFER_FORMAT",
    "WIZARD_CONTRACT",
    "BridgeEnrollmentCoordinator",
    "BridgeEnrollmentError",
    "PreparedHandoff",
    "capability_catalog",
    "capability_set_digest",
    "device_proof_key",
    "inspect_transfer_package",
    "progress_signature",
    "reviewed_release",
    "validate_central_url",
    "validate_capability_transition",
    "validate_enrollment_transition",
]
