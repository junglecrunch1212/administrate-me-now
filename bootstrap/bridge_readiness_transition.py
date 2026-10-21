"""Crash-safe readiness rebinding for authenticated bridge enrollment changes.

This changes no physical attestation. Only the full configuration fingerprint
may advance, and only alongside the exact owner-scoped bridge-index mutation.
The journal is authenticated with the instance event-log key and shares the
updater lock. An interrupted publication is recovered before runtime startup.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import os
import stat
from pathlib import Path
from typing import Any, Mapping

import yaml

from adminme.bridge_enrollment import CAPABILITIES, capability_set_digest, parse_time
from bootstrap.candidate_readiness import (
    DeploymentSnapshot,
    _canonical,
    _json_no_duplicates,
    _read_private_file,
    capture_deployment_snapshot,
    deployment_operation_lock,
)
from bootstrap.contracts import DeploymentPaths, atomic_write


CONTRACT = "bridge-readiness-transition-v1"
_MAX_BYTES = 4 * 1024 * 1024
_FIELDS = {
    "bind": {"device_identity_sha256"},
    "activate": {
        "status",
        "activated_at",
        "acceptance_consumed_at",
        "event_id",
        "bridge_node_id",
        "configured_packs",
        "enabled_packs",
        "authorized_packs",
        "adapters_active",
        "pack_config",
        "pack_capability_states",
        "capability_attestations",
        "provider_capability_state",
        "expires_at",
    },
    "configure": {
        "configured_packs",
        "enabled_packs",
        "authorized_packs",
        "pack_config",
        "pack_capability_states",
        "provider_capability_state",
    },
    "attest": {
        "authorized_packs",
        "adapters_active",
        "pack_capability_states",
        "capability_attestations",
        "provider_capability_state",
    },
    "start": {"enabled_packs"},
    "pause": {
        "status",
        "paused_at",
        "paused_authorized_packs",
        "paused_enabled_packs",
        "enabled_packs",
    },
    "resume": {
        "status",
        "resumed_at",
        "authorized_packs",
        "enabled_packs",
        "paused_authorized_packs",
        "paused_enabled_packs",
    },
    "revoke": {
        "status",
        "revoked_at",
        "enabled_packs",
        "authorized_packs",
        "adapters_active",
        "paused_authorized_packs",
        "paused_enabled_packs",
    },
    "expire": {"status", "expired_at"},
}
_AUTHORITY_FIELDS = {
    "kind",
    "actor_member_id",
    "member_id",
    "request_id",
    "proof_sha256",
    "operation",
    "tenant_id",
    "identity",
    "role",
}
_DOCUMENT_FIELDS = {
    "contract",
    "authority",
    "previous_index",
    "next_index",
    "previous_readiness",
    "next_readiness",
    "previous_snapshot",
    "next_snapshot",
}


class BridgeReadinessTransitionError(RuntimeError):
    """The exact enrollment/readiness transaction could not be proven."""


def _binding(snapshot: DeploymentSnapshot) -> dict[str, str]:
    return {
        "release_commit": snapshot.predecessor_commit,
        "instance_fingerprint": snapshot.instance_fingerprint,
        "host_fingerprint": snapshot.host_fingerprint,
        "configuration_fingerprint": snapshot.configuration_fingerprint,
    }


def _encode(payload: bytes | None) -> str | None:
    return None if payload is None else base64.b64encode(payload).decode("ascii")


def _decode(value: Any, *, absent: bool = False) -> bytes | None:
    if value is None and absent:
        return None
    if not isinstance(value, str):
        raise BridgeReadinessTransitionError("invalid transaction payload")
    try:
        payload = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise BridgeReadinessTransitionError("invalid transaction encoding") from exc
    if len(payload) > _MAX_BYTES:
        raise BridgeReadinessTransitionError("transaction payload exceeds boundary")
    return payload


class BridgeReadinessTransition:
    def __init__(self, paths: DeploymentPaths, encryption_key: bytes) -> None:
        if len(encryption_key) != 32:
            raise BridgeReadinessTransitionError("instance key is invalid")
        self.paths = paths
        self.index_path = paths.instance_dir / "bridges" / "member-mapping.json"
        self.readiness_path = (
            paths.instance_dir / "config" / "deployment-readiness.json"
        )
        self.journal_path = paths.app_dir / "bridge-readiness-transition.json"
        self._key = hmac.digest(encryption_key, CONTRACT.encode("ascii"), "sha256")

    @property
    def pending(self) -> bool:
        return os.path.lexists(self.journal_path)

    def _safe_paths(self) -> None:
        # The journal contains no caller-selected path. Refuse parent symlinks
        # as well as unsafe final files before any publication or recovery.
        for directory in (
            self.paths.root,
            self.paths.app_dir,
            self.paths.instance_dir,
            self.paths.instance_dir / "config",
            self.index_path.parent,
        ):
            if directory == self.index_path.parent and not directory.exists():
                directory.mkdir(mode=0o700)
                self._sync_directory(directory.parent)
            metadata = directory.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or directory.is_symlink()
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o022
            ):
                raise BridgeReadinessTransitionError("unsafe transaction directory")

    def _read_index(self) -> bytes | None:
        if not os.path.lexists(self.index_path):
            return None
        return _read_private_file(self.index_path)[0]

    @staticmethod
    def _sync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write(self, path: Path, payload: bytes) -> None:
        atomic_write(path, payload)
        self._sync_directory(path.parent)

    def _remove_journal(self) -> None:
        self.journal_path.unlink()
        self._sync_directory(self.journal_path.parent)

    def _snapshot(self, payload: bytes | None) -> DeploymentSnapshot:
        return capture_deployment_snapshot(
            self.paths,
            bridge_payload_override=payload,
            bridge_index_absent=payload is None,
        )

    def _validate_delta(
        self, previous: bytes | None, next_payload: bytes, authority: Mapping[str, Any]
    ) -> None:
        if set(authority) != _AUTHORITY_FIELDS or any(
            not isinstance(value, str) or not value for value in authority.values()
        ):
            raise BridgeReadinessTransitionError("invalid enrollment authority")
        proof = authority["proof_sha256"]
        if len(proof) != 64 or any(char not in "0123456789abcdef" for char in proof):
            raise BridgeReadinessTransitionError("invalid enrollment proof digest")
        kind, operation = authority["kind"], authority["operation"]
        if operation not in {
            "member": {"prepare", "pause", "resume", "revoke"},
            "helper": {"bind", "activate", "configure", "attest", "start"},
            "expiry": {"expire"},
        }.get(kind, set()):
            raise BridgeReadinessTransitionError(
                "operation exceeds enrollment authority"
            )
        mapping = _json_no_duplicates(
            _read_private_file(
                self.paths.instance_dir / "config" / "member-mapping.json"
            )[0],
            label="member mapping",
        )
        members = mapping.get("members")
        if not isinstance(members, list):
            raise BridgeReadinessTransitionError("member authority map is invalid")
        member = authority["member_id"]
        identity = authority["identity"]
        matches = [
            row
            for row in members
            if isinstance(row, dict) and row.get("member_id") == member
        ]
        identity_matches = [
            row
            for row in members
            if isinstance(row, dict)
            and str(row.get("tailscale_identity") or "").casefold()
            == identity.casefold()
        ]
        actors = [
            row
            for row in members
            if isinstance(row, dict)
            and row.get("member_id") == authority["actor_member_id"]
        ]
        instance = yaml.safe_load(
            _read_private_file(self.paths.instance_dir / "config" / "instance.yaml")[0]
        )
        if (
            len(matches) != 1
            or len(identity_matches) != 1
            or matches[0].get("tailscale_identity") != identity
            or matches[0].get("role") != authority["role"]
            or authority["role"] not in {"principal", "child"}
            or not isinstance(instance, dict)
            or instance.get("tenant_id") != authority["tenant_id"]
            or (
                kind == "member"
                and (len(actors) != 1 or actors[0].get("role") != "principal")
            )
            or (
                authority["actor_member_id"] != member
                and (kind != "member" or authority["role"] != "child")
            )
        ):
            raise BridgeReadinessTransitionError(
                "enrollment authority disagrees with household binding"
            )
        before = (
            {}
            if previous is None
            else _json_no_duplicates(previous, label="previous bridge index")
        )
        after = _json_no_duplicates(next_payload, label="next bridge index")
        if {key: value for key, value in before.items() if key != identity} != {
            key: value for key, value in after.items() if key != identity
        }:
            raise BridgeReadinessTransitionError(
                "bridge mutation changes another identity"
            )
        old, new = before.get(identity), after.get(identity)
        if not isinstance(new, dict) or any(
            new.get(key) != value
            for key, value in {
                "member_id": member,
                "owner_scope": f"private:{member}",
                "role": authority["role"],
                "request_id": authority["request_id"],
                "instance_id": authority["tenant_id"],
            }.items()
        ):
            raise BridgeReadinessTransitionError(
                "bridge mutation changes owner or request"
            )
        if operation == "prepare":
            capabilities = new.get("requested_capabilities")
            if (
                (
                    old is not None
                    and (
                        not isinstance(old, dict)
                        or old.get("status") != "revoked"
                        or old.get("member_id") != member
                        or old.get("owner_scope") != f"private:{member}"
                    )
                )
                or not isinstance(capabilities, list)
                or not capabilities
                or any(
                    not isinstance(value, str) or value not in CAPABILITIES
                    for value in capabilities
                )
                or new.get("capability_set_sha256")
                != capability_set_digest(capabilities)
                or new.get("allowed_packs")
                != sorted({CAPABILITIES[value]["pack"] for value in capabilities})
                or new.get("status") != "package-exported"
                or new.get("observation_only") is not True
                or any(
                    new.get(key) != []
                    for key in (
                        "configured_packs",
                        "enabled_packs",
                        "authorized_packs",
                        "adapters_active",
                    )
                )
                or new.get("pack_config") != {}
            ):
                raise BridgeReadinessTransitionError(
                    "new enrollment exceeds selected package"
                )
        else:
            if (
                not isinstance(old, dict)
                or old.get("request_id") != authority["request_id"]
            ):
                raise BridgeReadinessTransitionError(
                    "prior enrollment does not match mutation"
                )
            changed = {
                key
                for key in old.keys() | new.keys()
                if old.get(key) != new.get(key) or (key in old) != (key in new)
            }
            if changed - _FIELDS[operation]:
                raise BridgeReadinessTransitionError(
                    "enrollment mutation changes immutable coordinates"
                )
            if operation == "expire" and (
                old.get("status") != "package-exported"
                or new.get("status") != "revoked"
                or parse_time(new.get("expired_at")) < parse_time(old.get("expires_at"))
                or hashlib.sha256(_canonical(old)).hexdigest() != proof
            ):
                raise BridgeReadinessTransitionError(
                    "expiry is not an exact narrowing transition"
                )

    def _verify_document(
        self, document: dict[str, Any]
    ) -> tuple[bytes | None, bytes, bytes, bytes]:
        if set(document) != _DOCUMENT_FIELDS or document.get("contract") != CONTRACT:
            raise BridgeReadinessTransitionError(
                "transaction journal schema is invalid"
            )
        previous = _decode(document["previous_index"], absent=True)
        next_payload = _decode(document["next_index"])
        prior_readiness = _decode(document["previous_readiness"])
        next_readiness = _decode(document["next_readiness"])
        assert (
            next_payload is not None
            and prior_readiness is not None
            and next_readiness is not None
        )
        authority = document["authority"]
        if not isinstance(authority, dict):
            raise BridgeReadinessTransitionError("transaction authority is invalid")
        self._validate_delta(previous, next_payload, authority)
        previous_snapshot, next_snapshot = (
            self._snapshot(previous),
            self._snapshot(next_payload),
        )
        if (
            document["previous_snapshot"] != previous_snapshot.public()
            or document["next_snapshot"] != next_snapshot.public()
        ):
            raise BridgeReadinessTransitionError(
                "transaction deployment snapshot changed"
            )
        old_evidence = _json_no_duplicates(prior_readiness, label="prior readiness")
        new_evidence = _json_no_duplicates(next_readiness, label="next readiness")
        if old_evidence.get(
            "contract"
        ) != "deployment-readiness-v1" or old_evidence.get(
            "deployment_binding"
        ) != _binding(previous_snapshot):
            raise BridgeReadinessTransitionError(
                "existing physical readiness binding is stale"
            )
        expected = copy.deepcopy(old_evidence)
        expected["deployment_binding"]["configuration_fingerprint"] = (
            next_snapshot.configuration_fingerprint
        )
        if new_evidence != expected:
            raise BridgeReadinessTransitionError(
                "transaction changes physical readiness evidence"
            )
        if authority["operation"] == "prepare":
            new = _json_no_duplicates(next_payload, label="next bridge index")[
                authority["identity"]
            ]
            if new.get("release_commit") != previous_snapshot.predecessor_commit:
                raise BridgeReadinessTransitionError(
                    "handoff does not bind selected release"
                )
        return previous, next_payload, prior_readiness, next_readiness

    def _recover_locked(self) -> bool:
        """Return true only if the interrupted transaction durably committed."""
        self._safe_paths()
        wrapper = _json_no_duplicates(
            _read_private_file(self.journal_path, maximum_size=6 * _MAX_BYTES)[0],
            label="bridge readiness journal",
        )
        if (
            set(wrapper) != {"document", "hmac_sha256"}
            or not isinstance(wrapper["document"], dict)
            or not isinstance(wrapper["hmac_sha256"], str)
        ):
            raise BridgeReadinessTransitionError("transaction journal is invalid")
        document = wrapper["document"]
        if not hmac.compare_digest(
            wrapper["hmac_sha256"],
            hmac.digest(self._key, _canonical(document), "sha256").hex(),
        ):
            raise BridgeReadinessTransitionError(
                "transaction journal authentication failed"
            )
        previous, next_payload, prior_readiness, next_readiness = self._verify_document(
            document
        )
        index = self._read_index()
        readiness = _read_private_file(self.readiness_path)[0]
        if index not in (previous, next_payload) or readiness not in (
            prior_readiness,
            next_readiness,
        ):
            raise BridgeReadinessTransitionError(
                "transaction files changed independently"
            )
        committed = readiness == next_readiness
        if committed and index != next_payload:
            raise BridgeReadinessTransitionError(
                "readiness committed without its exact index"
            )
        if not committed and index != previous:
            if previous is None:
                self.index_path.unlink()
                self._sync_directory(self.index_path.parent)
            else:
                self._write(self.index_path, previous)
        self._remove_journal()
        return committed

    def recover(self) -> None:
        # The updater holds this lock while starting the selected service. A
        # clean startup needs no mutation and must remain possible under it.
        if not self.pending:
            return
        with deployment_operation_lock(self.paths):
            if self.pending:
                self._recover_locked()

    def commit(
        self,
        *,
        previous_payload: bytes | None,
        next_payload: bytes,
        authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        with deployment_operation_lock(self.paths):
            self._safe_paths()
            if self.pending:
                raise BridgeReadinessTransitionError(
                    "bridge transaction recovery is pending"
                )
            if self._read_index() != previous_payload:
                raise BridgeReadinessTransitionError(
                    "bridge index changed since it was loaded"
                )
            self._validate_delta(previous_payload, next_payload, authority)
            current_snapshot = capture_deployment_snapshot(self.paths)
            previous_snapshot = self._snapshot(previous_payload)
            if current_snapshot != previous_snapshot:
                raise BridgeReadinessTransitionError(
                    "current deployment snapshot is inconsistent"
                )
            prior_readiness = _read_private_file(self.readiness_path)[0]
            old_evidence = _json_no_duplicates(
                prior_readiness, label="readiness evidence"
            )
            next_snapshot = self._snapshot(next_payload)
            evidence = copy.deepcopy(old_evidence)
            evidence.setdefault("deployment_binding", {})[
                "configuration_fingerprint"
            ] = next_snapshot.configuration_fingerprint
            next_readiness = _canonical(evidence) + b"\n"
            document = {
                "contract": CONTRACT,
                "authority": dict(authority),
                "previous_index": _encode(previous_payload),
                "next_index": _encode(next_payload),
                "previous_readiness": _encode(prior_readiness),
                "next_readiness": _encode(next_readiness),
                "previous_snapshot": previous_snapshot.public(),
                "next_snapshot": next_snapshot.public(),
            }
            self._verify_document(document)
            if previous_payload == next_payload:
                return old_evidence
            self._write(
                self.journal_path,
                _canonical(
                    {
                        "document": document,
                        "hmac_sha256": hmac.digest(
                            self._key, _canonical(document), "sha256"
                        ).hex(),
                    }
                )
                + b"\n",
            )
            try:
                self._write(self.index_path, next_payload)
                self._write(self.readiness_path, next_readiness)
                self._remove_journal()
            except Exception:
                if self.pending:
                    try:
                        committed = self._recover_locked()
                    except Exception:
                        # Readiness is the commit point. Failed journal cleanup
                        # still blocks runtime readiness through `pending`, but
                        # must not discard the matching committed enrollment.
                        if (
                            self._read_index() == next_payload
                            and _read_private_file(self.readiness_path)[0]
                            == next_readiness
                        ):
                            return evidence
                        raise
                    if not committed:
                        raise
                elif (
                    self._read_index() != next_payload
                    or _read_private_file(self.readiness_path)[0] != next_readiness
                ):
                    raise
            return evidence
