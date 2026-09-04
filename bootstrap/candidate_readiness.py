"""Immutable, candidate-pending deployment-readiness evidence.

The pending contract is deliberately disjoint from the active
``deployment-readiness-v1`` object consumed by the runtime.  Recording this
evidence does not promote a release, call the updater, touch a provider, or
claim any of the mechanical work that belongs to the activation transaction.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import secrets
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml

from bootstrap.contracts import BACKUP_FORMAT_VERSION, DeploymentPaths, ensure_within
from bootstrap.provider_binding_transition import (
    PreparedProviderBindingTransition,
    ProviderBindingTransitionError,
    prepare_live_provider_binding_transition,
    valid_provider_binding_transition,
)
from bootstrap.source_identity import SourceIdentityError, reviewed_candidate_commit


PENDING_CONTRACT = "candidate-deployment-readiness-v1"
HTR10_CONTRACT = "htr-10-review-v1"
ACTIVE_CONTRACT = "deployment-readiness-v1"
MAX_EVIDENCE_AGE = timedelta(days=31)

_HEX_32 = re.compile(r"[0-9a-f]{32}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_BACKUP_BYTES = 8 * 1024 * 1024 * 1024
_MAX_POINTER_DEPTH = 64
_CONFIG_COORDINATES: tuple[tuple[str, bool], ...] = (
    ("state/config/deployment.json", True),
    ("state/config/instance.yaml", True),
    ("state/config/member-mapping.json", True),
    ("state/config/adapters.json", True),
    ("state/config/secret-references.json", True),
    ("state/bridges/member-mapping.json", False),
    ("state/config/openclaw-activation.json", False),
    # A preliminary rehearsal may precede final OpenClaw activation.  The
    # coordinate is still hashed as absent; the pending writer separately
    # requires it after HTR-07.
    ("state/openclaw/runtime/openclaw.json", False),
)
_HTR_IDS = ("htr_01", "htr_02", "htr_03", "htr_04", "htr_06", "htr_07")


class CandidateReadinessError(RuntimeError):
    """Candidate-pending evidence could not be proven or safely published."""


@dataclass(frozen=True)
class DeploymentSnapshot:
    predecessor_commit: str
    pointer_sha256: str
    pointer_chain_sha256: str
    instance_fingerprint: str
    host_fingerprint: str
    service_account_fingerprint: str
    configuration_fingerprint: str

    def public(self) -> dict[str, str]:
        return {
            "predecessor_commit": self.predecessor_commit,
            "pointer_sha256": self.pointer_sha256,
            "pointer_chain_sha256": self.pointer_chain_sha256,
            "instance_fingerprint": self.instance_fingerprint,
            "host_fingerprint": self.host_fingerprint,
            "service_account_fingerprint": self.service_account_fingerprint,
            "configuration_fingerprint": self.configuration_fingerprint,
        }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_domain(domain: str, value: Any) -> str:
    digest = hashlib.sha256()
    digest.update(domain.encode("ascii"))
    digest.update(b"\0")
    digest.update(_canonical(value))
    return digest.hexdigest()


def _json_no_duplicates(payload: bytes, *, label: str) -> dict[str, Any]:
    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise CandidateReadinessError(f"{label} contains duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateReadinessError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise CandidateReadinessError(f"{label} must contain one object")
    return value


def _read_private_file(
    path: Path,
    *,
    maximum_size: int = _MAX_JSON_BYTES,
    exact_mode: int = 0o600,
) -> tuple[bytes, os.stat_result]:
    descriptor: int | None = None
    try:
        before = path.lstat()
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != exact_mode
            or metadata.st_size <= 0
            or metadata.st_size > maximum_size
            or (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise CandidateReadinessError(f"unsafe private evidence file: {path.name}")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = path.lstat()
        final = os.fstat(descriptor)
        if (
            len(payload) != metadata.st_size
            or (after.st_dev, after.st_ino) != (metadata.st_dev, metadata.st_ino)
            or (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
            != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
        ):
            raise CandidateReadinessError(
                f"private evidence changed while read: {path.name}"
            )
        return payload, metadata
    except OSError as exc:
        raise CandidateReadinessError(
            f"private evidence is unavailable: {path.name}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _hash_private_file(path: Path, *, maximum_size: int) -> str:
    descriptor: int | None = None
    try:
        before = path.lstat()
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size <= 0
            or metadata.st_size > maximum_size
            or (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise CandidateReadinessError(f"unsafe private evidence file: {path.name}")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_size:
                raise CandidateReadinessError(
                    "private evidence exceeds its size boundary"
                )
            digest.update(chunk)
        after = path.lstat()
        final = os.fstat(descriptor)
        if (
            total != metadata.st_size
            or (after.st_dev, after.st_ino) != (metadata.st_dev, metadata.st_ino)
            or (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
            != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
        ):
            raise CandidateReadinessError(
                f"private evidence changed while hashed: {path.name}"
            )
        return digest.hexdigest()
    except OSError as exc:
        raise CandidateReadinessError(
            f"private evidence is unavailable: {path.name}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_config_coordinate(
    paths: DeploymentPaths, relative: str, required: bool
) -> dict[str, Any]:
    path = paths.root / relative
    if not os.path.lexists(path):
        if required:
            raise CandidateReadinessError(
                f"required deployment coordinate is missing: {relative}"
            )
        return {"path": relative, "present": False}
    payload, metadata = _read_private_file(path)
    return {
        "path": relative,
        "present": True,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": metadata.st_size,
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def _pointer_snapshot(paths: DeploymentPaths) -> tuple[str, str, str]:
    pointer = paths.app_dir / "current-release.json"
    payload, _metadata = _read_private_file(pointer)
    document = _json_no_duplicates(payload, label="active release pointer")
    releases = paths.app_dir / "releases"
    rows: list[dict[str, str]] = []
    commits: set[str] = set()
    coordinates: set[tuple[int, int]] = set()
    current: object = document
    for _depth in range(_MAX_POINTER_DEPTH):
        if (
            not isinstance(current, dict)
            or not {"commit", "path"}.issubset(current)
            or not set(current).issubset({"commit", "path", "previous"})
        ):
            raise CandidateReadinessError("active release pointer chain is invalid")
        commit = current.get("commit")
        raw_path = current.get("path")
        if not isinstance(commit, str) or _HEX_40.fullmatch(commit) is None:
            raise CandidateReadinessError("active release pointer commit is invalid")
        expected = releases / commit
        if not isinstance(raw_path, str) or Path(raw_path) != expected:
            raise CandidateReadinessError("active release pointer path is invalid")
        try:
            metadata = expected.lstat()
        except OSError as exc:
            raise CandidateReadinessError("active release is unavailable") from exc
        if (
            expected.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o022
        ):
            raise CandidateReadinessError("active release coordinate is unsafe")
        resolved = ensure_within(expected, releases)
        coordinate = (metadata.st_dev, metadata.st_ino)
        if commit in commits or coordinate in coordinates:
            raise CandidateReadinessError("active release pointer chain is duplicated")
        commits.add(commit)
        coordinates.add(coordinate)
        rows.append({"commit": commit, "path": str(resolved)})
        previous = current.get("previous")
        if previous is None:
            break
        current = previous
    else:
        raise CandidateReadinessError(
            "active release pointer chain exceeds its boundary"
        )
    return (
        rows[0]["commit"],
        hashlib.sha256(payload).hexdigest(),
        hashlib.sha256(_canonical(rows)).hexdigest(),
    )


def capture_deployment_snapshot(
    paths: DeploymentPaths,
    *,
    adapters_payload_override: bytes | None = None,
) -> DeploymentSnapshot:
    """Capture content-free binding material for rehearsal and pending evidence."""

    predecessor, pointer_sha256, pointer_chain_sha256 = _pointer_snapshot(paths)
    config_rows = []
    for relative, required in _CONFIG_COORDINATES:
        if (
            relative == "state/config/adapters.json"
            and adapters_payload_override is not None
        ):
            config_rows.append(
                {
                    "path": relative,
                    "present": True,
                    "sha256": hashlib.sha256(adapters_payload_override).hexdigest(),
                    "size": len(adapters_payload_override),
                    "mode": 0o600,
                }
            )
        else:
            config_rows.append(_read_config_coordinate(paths, relative, required))
    instance_path = paths.instance_dir / "config" / "instance.yaml"
    instance_payload, _metadata = _read_private_file(instance_path)
    try:
        instance = yaml.safe_load(instance_payload.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise CandidateReadinessError("instance configuration is invalid") from exc
    tenant_id = instance.get("tenant_id") if isinstance(instance, dict) else None
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise CandidateReadinessError(
            "instance configuration has no stable tenant identity"
        )
    try:
        root_metadata = paths.root.lstat()
        account = pwd.getpwuid(os.getuid())
    except (OSError, KeyError) as exc:
        raise CandidateReadinessError("service-account binding is unavailable") from exc
    if (
        paths.root.is_symlink()
        or not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != os.getuid()
        or root_metadata.st_mode & 0o077
    ):
        raise CandidateReadinessError("deployment root is unsafe")
    instance_fingerprint = _digest_domain(
        "administrateme-instance-v1",
        {
            "tenant_id_sha256": hashlib.sha256(tenant_id.encode()).hexdigest(),
            "root_device": root_metadata.st_dev,
            "root_inode": root_metadata.st_ino,
            "uid": os.getuid(),
        },
    )
    host_fingerprint = _digest_domain(
        "administrateme-host-v1",
        {
            "sysname": os.uname().sysname,
            "nodename_sha256": hashlib.sha256(os.uname().nodename.encode()).hexdigest(),
            "machine": os.uname().machine,
            "node": uuid.getnode(),
            "root_device": root_metadata.st_dev,
        },
    )
    service_account_fingerprint = _digest_domain(
        "administrateme-service-account-v1",
        {
            "uid": os.getuid(),
            "gid": os.getgid(),
            "home_sha256": hashlib.sha256(account.pw_dir.encode()).hexdigest(),
            "root_owner": root_metadata.st_uid,
        },
    )
    return DeploymentSnapshot(
        predecessor_commit=predecessor,
        pointer_sha256=pointer_sha256,
        pointer_chain_sha256=pointer_chain_sha256,
        instance_fingerprint=instance_fingerprint,
        host_fingerprint=host_fingerprint,
        service_account_fingerprint=service_account_fingerprint,
        configuration_fingerprint=_digest_domain(
            "administrateme-deployment-configuration-v1", config_rows
        ),
    )


def _binding_transition(
    paths: DeploymentPaths,
    *,
    approved: bool,
) -> PreparedProviderBindingTransition | None:
    if not approved:
        return None
    adapter_payload, _metadata = _read_private_file(
        paths.instance_dir / "config" / "adapters.json"
    )
    mapping_payload, _metadata = _read_private_file(
        paths.instance_dir / "config" / "member-mapping.json"
    )
    adapters = _json_no_duplicates(adapter_payload, label="adapter configuration")
    mapping = _json_no_duplicates(mapping_payload, label="member mapping")
    try:
        return prepare_live_provider_binding_transition(
            paths,
            adapters,
            household_mapping=mapping,
            ownership_reviewed=True,
            source_payload=adapter_payload,
            test_mode=os.environ.get("ADMINME_TEST_MODE") == "1",
        )
    except ProviderBindingTransitionError as exc:
        raise CandidateReadinessError(
            "provider-binding transition is not deterministic"
        ) from exc


def _parse_time(value: object, *, label: str, now: datetime) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise CandidateReadinessError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateReadinessError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandidateReadinessError(f"{label} timestamp must be timezone-aware")
    parsed = parsed.astimezone(timezone.utc)
    if parsed > now + timedelta(minutes=5):
        raise CandidateReadinessError(f"{label} timestamp is in the future")
    if now - parsed > MAX_EVIDENCE_AGE:
        raise CandidateReadinessError(f"{label} evidence is older than 31 days")
    return parsed


def _validate_htr10(
    paths: DeploymentPaths, path: Path, *, now: datetime
) -> tuple[dict[str, Any], str, datetime]:
    path = ensure_within(path, paths.backup_dir)
    payload, _metadata = _read_private_file(path)
    value = _json_no_duplicates(payload, label="HTR-10 review")
    _validate_htr10_value(value, now=now)
    observed = _parse_time(value["observed_at"], label="HTR-10", now=now)
    return value, hashlib.sha256(payload).hexdigest(), observed


def build_htr10_review(
    paths: DeploymentPaths,
    *,
    historical_attribution: Path,
    source_assurance: Path,
    observed_at: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reduce reviewed metadata audits to one strict, content-free HTR-10 receipt."""

    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    observed = _parse_time(observed_at, label="HTR-10", now=current_time)
    historical_path = ensure_within(historical_attribution, paths.backup_dir)
    source_path = ensure_within(source_assurance, paths.backup_dir)
    historical_payload, _historical_metadata = _read_private_file(historical_path)
    source_payload, _source_metadata = _read_private_file(source_path)
    historical = _json_no_duplicates(
        historical_payload, label="historical attribution audit"
    )
    assurance = _json_no_duplicates(source_payload, label="source-assurance audit")
    active_payload, _active_metadata = _read_private_file(
        paths.instance_dir / "config" / "deployment-readiness.json"
    )
    active = _json_no_duplicates(active_payload, label="active deployment readiness")
    window_value = (active.get("provider_effect_window") or {}).get("started_at")
    window = _parse_time(
        window_value,
        label="provider-effect window",
        now=current_time,
    )
    if window > observed:
        raise CandidateReadinessError(
            "provider-effect window starts after HTR-10 review"
        )
    historical_watermark = historical.get("watermark_event_id")
    source_watermark = assurance.get("watermark_event_id")
    counts = assurance.get("counts")
    effects = assurance.get("provider_effects")
    all_effect_counts = effects.get("counts") if isinstance(effects, dict) else None
    observation = (
        effects.get("observation_window") if isinstance(effects, dict) else None
    )
    observation_counts = (
        observation.get("counts") if isinstance(observation, dict) else None
    )
    historical_count = historical.get("source_event_count")
    historical_transition = historical.get("provider_binding_transition_sha256")
    source_transition = assurance.get("provider_binding_transition_sha256")
    integrity_fields = (
        "provider_called_unknown",
        "missing_terminal",
        "duplicate_terminal",
        "approval_correlation_missing",
        "duplicate_request",
        "malformed_request",
        "orphan_terminal",
        "invalid_legacy_reconciliation",
        "duplicate_legacy_reconciliation",
    )
    if (
        active.get("contract") != ACTIVE_CONTRACT
        or historical.get("contract") != "historical-attribution-v1"
        or historical.get("state") != "healthy"
        or historical.get("content_exposed") is not False
        or historical.get("raw_identifiers_exposed") is not False
        or historical.get("unattributed_event_count") != 0
        or not isinstance(historical_count, int)
        or historical_count < 0
        or historical.get("attributed_event_count") != historical_count
        or assurance.get("contract") != "source-assurance-v1"
        or assurance.get("state") != "healthy"
        or assurance.get("content_exposed") is not False
        or historical_watermark != source_watermark
        or not isinstance(counts, dict)
        or any(counts.get(name) != 0 for name in ("missing", "pending", "failed"))
        or not isinstance(effects, dict)
        or effects.get("state") != "healthy"
        or effects.get("content_exposed") is not False
        or not isinstance(all_effect_counts, dict)
        or any(all_effect_counts.get(name) != 0 for name in integrity_fields)
        or not isinstance(observation, dict)
        or observation.get("state") != "healthy"
        or observation.get("started_at_configured") is not True
        or observation.get("content_exposed") is not False
        or not isinstance(observation_counts, dict)
        or any(observation_counts.get(name) != 0 for name in integrity_fields)
        or observation_counts.get("provider_called_true") != 0
        or observation_counts.get("suppressed_false_or_unknown") != 0
        or historical_transition != source_transition
        or (
            historical_transition is not None
            and (
                not isinstance(historical_transition, str)
                or _HEX_64.fullmatch(historical_transition) is None
            )
        )
    ):
        raise CandidateReadinessError(
            "HTR-10 metadata audits are incomplete, unhealthy, or inconsistent"
        )
    value: dict[str, Any] = {
        "contract": HTR10_CONTRACT,
        "schema_version": 1,
        "status": "healthy",
        "observed_at": observed.isoformat(),
        "provider_effect_window_started_at": window.isoformat(),
        "event_watermark_sha256": _digest_domain(
            "administrateme-event-watermark-v1", historical_watermark
        ),
        "historical_attribution_sha256": hashlib.sha256(historical_payload).hexdigest(),
        "source_assurance_sha256": hashlib.sha256(source_payload).hexdigest(),
        "provider_binding_transition_sha256": historical_transition,
        "source_event_count": historical_count,
        "unattributed_event_count": 0,
        "provider_effect_integrity": "healthy",
        "provider_effect_true_count": 0,
        "provider_effect_unknown_count": 0,
        "content_exposed": False,
        "raw_identifiers_exposed": False,
    }
    value["receipt_sha256"] = hashlib.sha256(_canonical(value)).hexdigest()
    _validate_htr10_value(value, now=current_time)
    return value


def _validate_htr10_value(value: dict[str, Any], *, now: datetime) -> None:
    """Validate an in-memory review using the same rules as a private file."""

    required = {
        "contract",
        "schema_version",
        "status",
        "observed_at",
        "provider_effect_window_started_at",
        "event_watermark_sha256",
        "historical_attribution_sha256",
        "source_assurance_sha256",
        "provider_binding_transition_sha256",
        "source_event_count",
        "unattributed_event_count",
        "provider_effect_integrity",
        "provider_effect_true_count",
        "provider_effect_unknown_count",
        "content_exposed",
        "raw_identifiers_exposed",
        "receipt_sha256",
    }
    supplied = value.get("receipt_sha256")
    unsigned = dict(value)
    unsigned.pop("receipt_sha256", None)
    if (
        set(value) != required
        or value.get("contract") != HTR10_CONTRACT
        or value.get("schema_version") != 1
        or value.get("status") != "healthy"
        or value.get("content_exposed") is not False
        or value.get("raw_identifiers_exposed") is not False
        or value.get("unattributed_event_count") != 0
        or value.get("provider_effect_integrity") != "healthy"
        or value.get("provider_effect_true_count") != 0
        or value.get("provider_effect_unknown_count") != 0
        or not isinstance(value.get("source_event_count"), int)
        or value["source_event_count"] < 0
        or not all(
            isinstance(value.get(name), str) and _HEX_64.fullmatch(value[name])
            for name in (
                "event_watermark_sha256",
                "historical_attribution_sha256",
                "source_assurance_sha256",
                "receipt_sha256",
            )
        )
        or (
            value.get("provider_binding_transition_sha256") is not None
            and (
                not isinstance(value["provider_binding_transition_sha256"], str)
                or _HEX_64.fullmatch(value["provider_binding_transition_sha256"])
                is None
            )
        )
        or supplied != hashlib.sha256(_canonical(unsigned)).hexdigest()
    ):
        raise CandidateReadinessError("HTR-10 review is not healthy or authentic")
    observed = _parse_time(value["observed_at"], label="HTR-10", now=now)
    window = _parse_time(
        value["provider_effect_window_started_at"],
        label="provider-effect window",
        now=now,
    )
    if window > observed:
        raise CandidateReadinessError(
            "provider-effect window starts after HTR-10 review"
        )


def record_htr10_review(
    paths: DeploymentPaths,
    *,
    historical_attribution: Path,
    source_assurance: Path,
    observed_at: str,
    now: datetime | None = None,
    receipt_id: str | None = None,
) -> dict[str, Any]:
    """Publish one immutable HTR-10 review receipt inside the backup boundary."""

    identifier = receipt_id or secrets.token_hex(16)
    if _HEX_32.fullmatch(identifier) is None:
        raise CandidateReadinessError("HTR-10 receipt identifier is invalid")
    fixed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with deployment_operation_lock(paths):
        value = build_htr10_review(
            paths,
            historical_attribution=historical_attribution,
            source_assurance=source_assurance,
            observed_at=observed_at,
            now=fixed_now,
        )
        encoded = json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
        backup_descriptor = _open_bound_directory(paths.backup_dir, label="backup")
        name = f"htr-10-review-{identifier}.json"
        try:
            _write_new_private_at(backup_descriptor, name, encoded)
        finally:
            os.close(backup_descriptor)
        final = build_htr10_review(
            paths,
            historical_attribution=historical_attribution,
            source_assurance=source_assurance,
            observed_at=observed_at,
            now=fixed_now,
        )
        if final != value:
            raise CandidateReadinessError(
                "HTR-10 metadata changed before review receipt publication"
            )
    return {
        "status": "htr_10_review_recorded",
        "contract": HTR10_CONTRACT,
        "receipt_id": identifier,
        "receipt_sha256": hashlib.sha256(encoded).hexdigest(),
        "source_event_count": value["source_event_count"],
        "provider_effect_integrity": "healthy",
        "content_exposed": False,
    }


def _validate_rehearsal(
    paths: DeploymentPaths,
    receipt_path: Path,
    backup_path: Path,
    *,
    candidate_commit: str,
    snapshot: DeploymentSnapshot,
    now: datetime,
) -> tuple[dict[str, Any], str, str, str, datetime]:
    receipt_path = ensure_within(receipt_path, paths.backup_dir)
    payload, _metadata = _read_private_file(receipt_path)
    receipt = _json_no_duplicates(payload, label="preliminary rehearsal receipt")
    expected_receipt_keys = {
        "contract",
        "schema_version",
        "status",
        "tested_at",
        "candidate",
        "predecessor",
        "deployment_binding",
        "artifact",
        "watermark",
        "replay",
        "workbooks",
        "offline_replay_checks",
        "isolation",
        "source_events_appended",
        "content_emitted_in_receipt",
        "receipt_sha256",
    }
    supplied = receipt.get("receipt_sha256")
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    candidate = receipt.get("candidate")
    predecessor = receipt.get("predecessor")
    artifact = receipt.get("artifact")
    offline = receipt.get("offline_replay_checks")
    isolation = receipt.get("isolation")
    binding = receipt.get("deployment_binding")
    if (
        set(receipt) != expected_receipt_keys
        or receipt.get("contract") != "backup-rehearsal-v1"
        or receipt.get("schema_version") != 1
        or receipt.get("status") != "healthy"
        or receipt.get("content_emitted_in_receipt") is not False
        or not isinstance(supplied, str)
        or _HEX_64.fullmatch(supplied) is None
        or supplied != hashlib.sha256(_canonical(unsigned)).hexdigest()
        or not isinstance(candidate, dict)
        or candidate.get("commit") != candidate_commit
        or candidate.get("clean_checkout_at_start") is not True
        or candidate.get("clean_checkout_at_end") is not True
        or candidate.get("controller_coordinate_matches") is not True
        or candidate.get("execution_immutability") != "not_evaluated"
        or not isinstance(predecessor, dict)
        or predecessor.get("commit") != snapshot.predecessor_commit
        or predecessor.get("pointer_sha256") != snapshot.pointer_sha256
        or predecessor.get("retained") is not True
        or predecessor.get("immutable_release_valid") != "not_evaluated"
        or predecessor.get("rollback_compatible") != "not_evaluated"
        or predecessor.get("automatic_rollback_eligible") != "not_evaluated"
        or not isinstance(artifact, dict)
        or artifact.get("format") != "administrateme-backup-v2"
        or artifact.get("version") != BACKUP_FORMAT_VERSION
        or artifact.get("authenticated") is not True
        or artifact.get("exact_manifest_members") is not True
        or not isinstance(offline, dict)
        or offline.get("status") != "healthy"
        or offline.get("live_checks") != "not_evaluated"
        or offline.get("activation_eligible") is not False
        or offline.get("external_effects") != "not_evaluated"
        or offline.get("provider_components_instantiated") != 0
        or offline.get("event_sink_mode") != "rejecting"
        or not isinstance(isolation, dict)
        or isolation.get("active_state_replaced") is not False
        or isolation.get("active_release_pointer_changed") is not False
        or isolation.get("rollback_tree_changed") is not False
        or isolation.get("service_manager_constructed") is not False
        or isolation.get("plaintext_stage_destroyed") is not True
        or isolation.get("staging_destroyed") is not True
        or isolation.get("staging_outside_deployment_root") is not True
        or not isinstance(binding, dict)
        or binding != snapshot.public()
    ):
        raise CandidateReadinessError("preliminary rehearsal receipt is not eligible")
    tested_at = _parse_time(receipt.get("tested_at"), label="rehearsal", now=now)
    backup_path = ensure_within(backup_path, paths.backup_dir)
    artifact_sha256 = _hash_private_file(backup_path, maximum_size=_MAX_BACKUP_BYTES)
    if artifact.get("sha256") != artifact_sha256:
        raise CandidateReadinessError(
            "preliminary backup artifact does not match receipt"
        )
    watermark_sha256 = _digest_domain(
        "administrateme-rehearsal-watermark-v1", receipt.get("watermark")
    )
    return (
        receipt,
        hashlib.sha256(payload).hexdigest(),
        artifact_sha256,
        watermark_sha256,
        tested_at,
    )


def _member_ids(paths: DeploymentPaths) -> list[str]:
    payload, _metadata = _read_private_file(
        paths.instance_dir / "config" / "member-mapping.json"
    )
    mapping = _json_no_duplicates(payload, label="member mapping")
    members = mapping.get("members")
    if not isinstance(members, list):
        raise CandidateReadinessError("member mapping is invalid")
    result = sorted(
        member["member_id"]
        for member in members
        if isinstance(member, dict)
        and isinstance(member.get("member_id"), str)
        and member["member_id"]
        and isinstance(member.get("tailscale_identity"), str)
        and member["tailscale_identity"].strip()
    )
    if not result or len(result) != len(set(result)):
        raise CandidateReadinessError(
            "member mapping has no unique trusted-ingress member identities"
        )
    return result


def build_pending_attestation(
    paths: DeploymentPaths,
    *,
    candidate: Path,
    expected_candidate_commit: str,
    rehearsal_receipt: Path,
    backup_artifact: Path,
    htr10_review: Path,
    confirmations: Mapping[str, bool],
    observed_at: Mapping[str, str],
    approve_provider_binding_migration: bool = False,
    now: datetime | None = None,
    attestation_id: str | None = None,
) -> tuple[dict[str, Any], DeploymentSnapshot]:
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if _HEX_40.fullmatch(expected_candidate_commit) is None:
        raise CandidateReadinessError(
            "expected candidate commit must be 40 lowercase hex"
        )
    candidate_root = candidate.resolve()
    if candidate.is_symlink() or Path(__file__).resolve().parents[1] != candidate_root:
        raise CandidateReadinessError(
            "writer is not executing from the reviewed candidate"
        )
    try:
        candidate_commit = reviewed_candidate_commit(candidate_root)
    except SourceIdentityError as exc:
        raise CandidateReadinessError("candidate source identity is invalid") from exc
    if candidate_commit != expected_candidate_commit:
        raise CandidateReadinessError("candidate does not match the approved commit")
    snapshot = capture_deployment_snapshot(paths)
    transition = _binding_transition(paths, approved=approve_provider_binding_migration)
    transition_public = transition.public() if transition is not None else None
    transition_sha256 = (
        hashlib.sha256(_canonical(transition_public)).hexdigest()
        if transition_public is not None
        else None
    )
    prepared_snapshot = (
        capture_deployment_snapshot(
            paths,
            adapters_payload_override=transition.prepared_bytes,
        )
        if transition is not None
        else snapshot
    )
    for required_after_htr07 in (
        paths.instance_dir / "config" / "openclaw-activation.json",
        paths.instance_dir / "openclaw" / "runtime" / "openclaw.json",
    ):
        if not os.path.lexists(required_after_htr07):
            raise CandidateReadinessError(
                "final OpenClaw configuration is required after HTR-07"
            )
        _read_private_file(required_after_htr07)
    _predecessor, _pointer, _chain = _pointer_snapshot(paths)
    if candidate_commit in {row_commit for row_commit in _pointer_commits(paths)}:
        raise CandidateReadinessError("candidate is already active or retained")
    missing = [
        name for name in (*_HTR_IDS, "htr_10") if confirmations.get(name) is not True
    ]
    if missing:
        raise CandidateReadinessError(
            "every prerequisite confirmation is required: " + ", ".join(missing)
        )
    human: dict[str, Any] = {}
    expiries: list[datetime] = []
    for name in _HTR_IDS:
        parsed = _parse_time(
            observed_at.get(name), label=name.upper(), now=current_time
        )
        expiries.append(parsed + MAX_EVIDENCE_AGE)
        human[name] = {"confirmed": True, "observed_at": parsed.isoformat()}
    htr10, htr10_sha256, htr10_observed = _validate_htr10(
        paths, htr10_review, now=current_time
    )
    if htr10.get("provider_binding_transition_sha256") != transition_sha256:
        raise CandidateReadinessError(
            "HTR-10 review does not bind the prepared provider configuration"
        )
    expiries.append(htr10_observed + MAX_EVIDENCE_AGE)
    human["htr_10"] = {
        "confirmed": True,
        "observed_at": htr10_observed.isoformat(),
        "review_receipt_sha256": htr10_sha256,
        "event_watermark_sha256": htr10["event_watermark_sha256"],
    }
    human["htr_03"]["member_ids"] = _member_ids(paths)
    _receipt, receipt_sha256, artifact_sha256, watermark_sha256, rehearsal_time = (
        _validate_rehearsal(
            paths,
            rehearsal_receipt,
            backup_artifact,
            candidate_commit=candidate_commit,
            snapshot=snapshot,
            now=current_time,
        )
    )
    expiries.append(rehearsal_time + MAX_EVIDENCE_AGE)
    identifier = attestation_id or secrets.token_hex(16)
    if _HEX_32.fullmatch(identifier) is None:
        raise CandidateReadinessError("attestation identifier is invalid")
    expiry = min(expiries)
    if expiry <= current_time:
        raise CandidateReadinessError("candidate evidence has already expired")
    value: dict[str, Any] = {
        "contract": PENDING_CONTRACT,
        "schema_version": 1,
        "lifecycle": "candidate_pending",
        "promotes_to": ACTIVE_CONTRACT,
        "attestation_id": identifier,
        "recorded_at": current_time.isoformat(),
        "expires_at": expiry.isoformat(),
        "deployment_binding": {
            "candidate_commit": candidate_commit,
            **snapshot.public(),
            "prepared_configuration_fingerprint": (
                prepared_snapshot.configuration_fingerprint
            ),
            "provider_binding_transition": transition_public,
            "preliminary_rehearsal_receipt_sha256": receipt_sha256,
            "preliminary_backup_artifact_sha256": artifact_sha256,
            "preliminary_rehearsal_watermark_sha256": watermark_sha256,
        },
        "human_evidence": human,
        "pre_switch_state": {
            "observation_active": True,
            "write_admission": False,
            "provider_effect_window_started_at": htr10[
                "provider_effect_window_started_at"
            ],
            "htr_10_review_receipt_sha256": htr10_sha256,
        },
        "activation_boundary": {
            "candidate_active": False,
            "activation_authorized": False,
            "updater_fresh_backup": "not_evaluated",
            "predecessor_rollback_compatible": "not_evaluated",
            "automatic_rollback_eligible": "not_evaluated",
            "crash_recoverable_replacement": "not_evaluated",
            "candidate_live_doctor": "not_evaluated",
            "post_switch_acceptance": "not_evaluated",
        },
        "content_exposed": False,
    }
    validate_pending_attestation(value, now=current_time)
    return value, snapshot


def _pointer_commits(paths: DeploymentPaths) -> tuple[str, ...]:
    payload, _metadata = _read_private_file(paths.app_dir / "current-release.json")
    current: object = _json_no_duplicates(payload, label="active release pointer")
    commits: list[str] = []
    for _depth in range(_MAX_POINTER_DEPTH):
        if not isinstance(current, dict):
            break
        commit = current.get("commit")
        if isinstance(commit, str):
            commits.append(commit)
        previous = current.get("previous")
        if previous is None:
            return tuple(commits)
        current = previous
    return tuple(commits)


def validate_pending_attestation(
    value: object, *, now: datetime | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CandidateReadinessError("pending readiness evidence must be one object")
    required = {
        "contract",
        "schema_version",
        "lifecycle",
        "promotes_to",
        "attestation_id",
        "recorded_at",
        "expires_at",
        "deployment_binding",
        "human_evidence",
        "pre_switch_state",
        "activation_boundary",
        "content_exposed",
    }
    if set(value) != required:
        raise CandidateReadinessError("pending readiness schema is invalid")
    if (
        value.get("contract") != PENDING_CONTRACT
        or value.get("schema_version") != 1
        or value.get("lifecycle") != "candidate_pending"
        or value.get("promotes_to") != ACTIVE_CONTRACT
        or value.get("content_exposed") is not False
        or not isinstance(value.get("attestation_id"), str)
        or _HEX_32.fullmatch(value["attestation_id"]) is None
    ):
        raise CandidateReadinessError("pending readiness header is invalid")
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    recorded = _parse_time(value.get("recorded_at"), label="recorded", now=current_time)
    expires = _parse_time(
        value.get("expires_at"), label="expiry", now=recorded + MAX_EVIDENCE_AGE
    )
    if expires <= current_time or expires - recorded > MAX_EVIDENCE_AGE:
        raise CandidateReadinessError(
            "pending readiness evidence is expired or overlong"
        )
    binding = value.get("deployment_binding")
    expected_binding = {
        "candidate_commit",
        "predecessor_commit",
        "pointer_sha256",
        "pointer_chain_sha256",
        "instance_fingerprint",
        "host_fingerprint",
        "service_account_fingerprint",
        "configuration_fingerprint",
        "prepared_configuration_fingerprint",
        "provider_binding_transition",
        "preliminary_rehearsal_receipt_sha256",
        "preliminary_backup_artifact_sha256",
        "preliminary_rehearsal_watermark_sha256",
    }
    if not isinstance(binding, dict) or set(binding) != expected_binding:
        raise CandidateReadinessError("pending deployment binding is invalid")
    if (
        _HEX_40.fullmatch(str(binding.get("candidate_commit"))) is None
        or _HEX_40.fullmatch(str(binding.get("predecessor_commit"))) is None
    ):
        raise CandidateReadinessError("pending release binding is invalid")
    if not all(
        isinstance(binding.get(name), str) and _HEX_64.fullmatch(binding[name])
        for name in expected_binding
        - {
            "candidate_commit",
            "predecessor_commit",
            "provider_binding_transition",
        }
    ):
        raise CandidateReadinessError("pending fingerprint binding is invalid")
    transition = binding.get("provider_binding_transition")
    if transition is None:
        if (
            binding["prepared_configuration_fingerprint"]
            != binding["configuration_fingerprint"]
        ):
            raise CandidateReadinessError(
                "pending prepared configuration binding is invalid"
            )
    elif not valid_provider_binding_transition(transition):
        raise CandidateReadinessError("pending provider-binding transition is invalid")
    human = value.get("human_evidence")
    if not isinstance(human, dict) or set(human) != {*_HTR_IDS, "htr_10"}:
        raise CandidateReadinessError("pending human evidence is invalid")
    for name, row in human.items():
        expected = {"confirmed", "observed_at"}
        if name == "htr_03":
            expected.add("member_ids")
        if name == "htr_10":
            expected.update({"review_receipt_sha256", "event_watermark_sha256"})
        if (
            not isinstance(row, dict)
            or set(row) != expected
            or row.get("confirmed") is not True
        ):
            raise CandidateReadinessError("pending human evidence row is invalid")
        observed = _parse_time(
            row.get("observed_at"), label=name.upper(), now=current_time
        )
        if expires > observed + MAX_EVIDENCE_AGE:
            raise CandidateReadinessError(
                "pending expiry exceeds prerequisite freshness"
            )
        if name == "htr_03" and (
            not isinstance(row.get("member_ids"), list)
            or not row["member_ids"]
            or not all(
                isinstance(member, str) and member for member in row["member_ids"]
            )
            or row["member_ids"] != sorted(set(row["member_ids"]))
        ):
            raise CandidateReadinessError("pending HTR-03 member binding is invalid")
        if name == "htr_10" and not all(
            isinstance(row.get(field), str) and _HEX_64.fullmatch(row[field])
            for field in ("review_receipt_sha256", "event_watermark_sha256")
        ):
            raise CandidateReadinessError("pending HTR-10 review binding is invalid")
    pre_switch = value.get("pre_switch_state")
    if not isinstance(pre_switch, dict) or pre_switch != {
        "observation_active": True,
        "write_admission": False,
        "provider_effect_window_started_at": pre_switch.get(
            "provider_effect_window_started_at"
        ),
        "htr_10_review_receipt_sha256": pre_switch.get("htr_10_review_receipt_sha256"),
    }:
        raise CandidateReadinessError("pending pre-switch state is invalid")
    _parse_time(
        pre_switch.get("provider_effect_window_started_at"),
        label="provider-effect window",
        now=current_time,
    )
    if _HEX_64.fullmatch(str(pre_switch.get("htr_10_review_receipt_sha256"))) is None:
        raise CandidateReadinessError("pending HTR-10 binding is invalid")
    boundary = value.get("activation_boundary")
    expected_boundary = {
        "candidate_active": False,
        "activation_authorized": False,
        "updater_fresh_backup": "not_evaluated",
        "predecessor_rollback_compatible": "not_evaluated",
        "automatic_rollback_eligible": "not_evaluated",
        "crash_recoverable_replacement": "not_evaluated",
        "candidate_live_doctor": "not_evaluated",
        "post_switch_acceptance": "not_evaluated",
    }
    if boundary != expected_boundary:
        raise CandidateReadinessError("pending activation boundary is invalid")
    return value


def load_pending_attestation_for_activation(
    paths: DeploymentPaths,
    *,
    attestation_id: str,
    candidate: Path,
    candidate_commit: str,
    approve_provider_binding_migration: bool = False,
    now: datetime | None = None,
) -> tuple[dict[str, Any], str, DeploymentSnapshot]:
    """Load one explicit pending artifact and rebind it to current coordinates.

    The pending directory has no selector.  The guarded updater must receive an
    exact artifact identifier and repeat every machine-observable binding before
    backup creation and again immediately before the first selector mutation.
    """

    if _HEX_32.fullmatch(attestation_id) is None:
        raise CandidateReadinessError("pending attestation identifier is invalid")
    if _HEX_40.fullmatch(candidate_commit) is None:
        raise CandidateReadinessError("candidate commit is invalid")
    candidate_root = candidate.resolve()
    try:
        observed_commit = reviewed_candidate_commit(candidate_root)
    except SourceIdentityError as exc:
        raise CandidateReadinessError("candidate source identity is invalid") from exc
    if observed_commit != candidate_commit:
        raise CandidateReadinessError("candidate changed after pending attestation")
    pending_path = (
        paths.app_dir / "readiness" / "candidate-pending" / f"{attestation_id}.json"
    )
    payload, _metadata = _read_private_file(pending_path)
    value = validate_pending_attestation(
        _json_no_duplicates(payload, label="candidate-pending readiness"),
        now=now,
    )
    if value["attestation_id"] != attestation_id:
        raise CandidateReadinessError("pending attestation path does not match payload")
    binding = value["deployment_binding"]
    if binding["candidate_commit"] != candidate_commit:
        raise CandidateReadinessError(
            "pending attestation belongs to another candidate"
        )
    snapshot = capture_deployment_snapshot(paths)
    expected = snapshot.public()
    for field, observed in expected.items():
        if binding.get(field) != observed:
            raise CandidateReadinessError(
                f"pending attestation deployment binding drifted: {field}"
            )
    declared_transition = binding.get("provider_binding_transition")
    if declared_transition is not None:
        if approve_provider_binding_migration is not True:
            raise CandidateReadinessError(
                "provider-binding transition requires explicit activation approval"
            )
        transition = _binding_transition(paths, approved=True)
        assert transition is not None
        prepared_snapshot = capture_deployment_snapshot(
            paths,
            adapters_payload_override=transition.prepared_bytes,
        )
        if (
            transition.public() != declared_transition
            or prepared_snapshot.configuration_fingerprint
            != binding["prepared_configuration_fingerprint"]
        ):
            raise CandidateReadinessError("pending provider-binding transition drifted")
    elif approve_provider_binding_migration:
        raise CandidateReadinessError(
            "pending evidence does not authorize a provider-binding transition"
        )
    if candidate_commit in _pointer_commits(paths):
        raise CandidateReadinessError("pending candidate is already active or retained")
    if value["human_evidence"]["htr_03"]["member_ids"] != _member_ids(paths):
        raise CandidateReadinessError("pending member identity binding drifted")
    return value, hashlib.sha256(payload).hexdigest(), snapshot


def build_active_attestation_from_pending(
    pending: Mapping[str, Any],
    *,
    pending_sha256: str,
    fresh_backup_sha256: str,
    fresh_rehearsal_receipt_sha256: str,
    rollback_eligibility_sha256: str,
    tested_at: str,
) -> dict[str, Any]:
    """Derive active v1 evidence only from pending plus updater mechanics.

    This pure builder performs no publication.  Only the crash-journaled updater
    may call it, after rehearsing its own exact fresh artifact and proving the
    retained predecessor.  Runtime compatibility stays on the existing active
    contract while the extra activation evidence remains content-free.
    """

    value = validate_pending_attestation(dict(pending))
    for digest in (
        pending_sha256,
        fresh_backup_sha256,
        fresh_rehearsal_receipt_sha256,
        rollback_eligibility_sha256,
    ):
        if _HEX_64.fullmatch(digest) is None:
            raise CandidateReadinessError("active readiness digest is invalid")
    current_time = datetime.now(timezone.utc)
    tested = _parse_time(tested_at, label="activation rehearsal", now=current_time)
    binding = value["deployment_binding"]
    human = value["human_evidence"]
    recovery_time = min(
        _parse_time(human[name]["observed_at"], label=name.upper(), now=current_time)
        for name in ("htr_02", "htr_06")
    )
    return {
        "contract": ACTIVE_CONTRACT,
        "deployment_binding": {
            "release_commit": binding["candidate_commit"],
            "instance_fingerprint": binding["instance_fingerprint"],
            "host_fingerprint": binding["host_fingerprint"],
            "configuration_fingerprint": binding["prepared_configuration_fingerprint"],
        },
        "identity_acceptance": {
            "member_ids": list(human["htr_03"]["member_ids"]),
            "distinct_identities": True,
            "tested_at": human["htr_03"]["observed_at"],
        },
        "recovery": {
            "separate_admin_available": True,
            "filevault_unlock_tested": True,
            "automatic_login_disabled": True,
            "tested_at": recovery_time.isoformat(),
        },
        "backup_rollback": {
            "encrypted_backup_authenticated": True,
            "restore_rehearsed": True,
            "prior_release_retained": True,
            "automatic_rollback_eligible": True,
            "tested_at": tested.isoformat(),
        },
        "provider_effect_window": {
            "started_at": value["pre_switch_state"]["provider_effect_window_started_at"]
        },
        "activation_evidence": {
            "contract": "deployment-activation-evidence-v1",
            "candidate_pending_attestation_id": value["attestation_id"],
            "candidate_pending_attestation_sha256": pending_sha256,
            "fresh_backup_artifact_sha256": fresh_backup_sha256,
            "fresh_rehearsal_receipt_sha256": fresh_rehearsal_receipt_sha256,
            "predecessor_rollback_eligibility_sha256": rollback_eligibility_sha256,
            "crash_recoverable_replacement": True,
            "candidate_live_doctor": "pending_post_switch",
            "post_switch_acceptance": "pending_post_switch",
            "content_exposed": False,
        },
        "content_exposed": False,
    }


def _validate_private_directory(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CandidateReadinessError(
            f"private directory is unavailable: {path.name}"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise CandidateReadinessError(f"private directory is unsafe: {path.name}")
    return metadata


def _open_bound_directory(path: Path, *, label: str) -> int:
    before = _validate_private_directory(path)
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise CandidateReadinessError(f"{label} directory is unavailable") from exc
    metadata = os.fstat(descriptor)
    after = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino)
        or (after.st_dev, after.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        os.close(descriptor)
        raise CandidateReadinessError(f"{label} directory coordinate changed")
    return descriptor


def _open_or_create_private_child(parent_descriptor: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
    except FileExistsError:
        pass
    except OSError as exc:
        raise CandidateReadinessError(
            f"private directory cannot be created: {name}"
        ) from exc
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        public = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise CandidateReadinessError(f"private directory is unsafe: {name}") from exc
    assert descriptor is not None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or (metadata.st_dev, metadata.st_ino) != (public.st_dev, public.st_ino)
    ):
        os.close(descriptor)
        raise CandidateReadinessError(f"private directory is unsafe: {name}")
    return descriptor


@contextmanager
def _pending_evidence_directory(paths: DeploymentPaths) -> Iterator[tuple[int, Path]]:
    root_descriptor: int | None = None
    app_descriptor: int | None = None
    readiness_descriptor: int | None = None
    pending_descriptor: int | None = None
    try:
        root_descriptor = _open_bound_directory(paths.root, label="deployment root")
        app_descriptor = _open_or_create_private_child(root_descriptor, "app")
        app_public = paths.app_dir.lstat()
        app_open = os.fstat(app_descriptor)
        if (app_public.st_dev, app_public.st_ino) != (
            app_open.st_dev,
            app_open.st_ino,
        ):
            raise CandidateReadinessError("application directory coordinate changed")
        readiness_descriptor = _open_or_create_private_child(
            app_descriptor, "readiness"
        )
        pending_descriptor = _open_or_create_private_child(
            readiness_descriptor, "candidate-pending"
        )
        yield pending_descriptor, paths.app_dir / "readiness" / "candidate-pending"
    finally:
        for descriptor in (
            pending_descriptor,
            readiness_descriptor,
            app_descriptor,
            root_descriptor,
        ):
            if descriptor is not None:
                os.close(descriptor)


def _write_new_private_at(
    parent_descriptor: int,
    name: str,
    payload: bytes,
) -> None:
    if re.fullmatch(r"(?:[0-9a-f]{32}|htr-10-review-[0-9a-f]{32})\.json", name) is None:
        raise CandidateReadinessError("attestation evidence filename is invalid")
    temporary = f".{name}.{secrets.token_hex(16)}"
    descriptor: int | None = None
    published = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CandidateReadinessError("pending evidence write was incomplete")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(
            temporary,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        published = True
        os.unlink(temporary, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except BaseException as exc:
        cleanup_error: BaseException | None = None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as close_exc:
                cleanup_error = close_exc
        if published:
            try:
                os.unlink(name, dir_fd=parent_descriptor)
            except BaseException as unlink_exc:
                cleanup_error = cleanup_error or unlink_exc
        try:
            os.unlink(temporary, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        except BaseException as unlink_exc:
            cleanup_error = cleanup_error or unlink_exc
        try:
            os.fsync(parent_descriptor)
        except BaseException as fsync_exc:
            cleanup_error = cleanup_error or fsync_exc
        if cleanup_error is not None:
            raise CandidateReadinessError(
                "failed pending evidence publication could not be durably removed"
            ) from exc
        if isinstance(exc, FileExistsError):
            raise CandidateReadinessError(
                "attestation identifier already exists"
            ) from exc
        raise


@contextmanager
def deployment_operation_lock(paths: DeploymentPaths) -> Iterator[None]:
    """Share the exact cooperative exclusion coordinate with upgrade/rollback."""

    root_descriptor: int | None = None
    app_descriptor: int | None = None
    descriptor: int | None = None
    failed = False
    try:
        root_descriptor = _open_bound_directory(paths.root, label="deployment root")
        app_descriptor = _open_or_create_private_child(root_descriptor, "app")
        app_public = paths.app_dir.lstat()
        app_open = os.fstat(app_descriptor)
        if (app_public.st_dev, app_public.st_ino) != (
            app_open.st_dev,
            app_open.st_ino,
        ):
            raise CandidateReadinessError("application directory coordinate changed")
        descriptor = os.open(
            ".upgrade-operation.lock",
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=app_descriptor,
        )
        metadata = os.fstat(descriptor)
        public = os.stat(
            ".upgrade-operation.lock",
            dir_fd=app_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or (metadata.st_dev, metadata.st_ino) != (public.st_dev, public.st_ino)
        ):
            raise CandidateReadinessError("deployment operation lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CandidateReadinessError(
                "another deployment operation is already running"
            ) from exc
        locked = os.fstat(descriptor)
        public_after = os.stat(
            ".upgrade-operation.lock",
            dir_fd=app_descriptor,
            follow_symlinks=False,
        )
        canonical_app = paths.app_dir.lstat()
        if (locked.st_dev, locked.st_ino) != (
            public_after.st_dev,
            public_after.st_ino,
        ) or (canonical_app.st_dev, canonical_app.st_ino) != (
            app_open.st_dev,
            app_open.st_ino,
        ):
            raise CandidateReadinessError("deployment operation lock changed")
        yield
    except BaseException:
        failed = True
        raise
    finally:
        cleanup: BaseException | None = None
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except BaseException as exc:
                cleanup = exc
            try:
                os.close(descriptor)
            except BaseException as exc:
                cleanup = cleanup or exc
        for directory_descriptor in (app_descriptor, root_descriptor):
            if directory_descriptor is not None:
                try:
                    os.close(directory_descriptor)
                except BaseException as exc:
                    cleanup = cleanup or exc
        if cleanup is not None and not failed:
            raise CandidateReadinessError(
                "deployment operation lock cleanup failed"
            ) from cleanup


def record_pending_attestation(
    paths: DeploymentPaths,
    *,
    candidate: Path,
    expected_candidate_commit: str,
    rehearsal_receipt: Path,
    backup_artifact: Path,
    htr10_review: Path,
    confirmations: Mapping[str, bool],
    observed_at: Mapping[str, str],
    approve_provider_binding_migration: bool = False,
    now: datetime | None = None,
    attestation_id: str | None = None,
) -> dict[str, Any]:
    with deployment_operation_lock(paths):
        fixed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        fixed_attestation_id = attestation_id or secrets.token_hex(16)
        value, initial = build_pending_attestation(
            paths,
            candidate=candidate,
            expected_candidate_commit=expected_candidate_commit,
            rehearsal_receipt=rehearsal_receipt,
            backup_artifact=backup_artifact,
            htr10_review=htr10_review,
            confirmations=confirmations,
            observed_at=observed_at,
            approve_provider_binding_migration=approve_provider_binding_migration,
            now=fixed_now,
            attestation_id=fixed_attestation_id,
        )
        encoded = json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
        name = f"{value['attestation_id']}.json"
        with _pending_evidence_directory(paths) as (pending_descriptor, _pending):
            _write_new_private_at(pending_descriptor, name, encoded)
            final_value, final = build_pending_attestation(
                paths,
                candidate=candidate,
                expected_candidate_commit=expected_candidate_commit,
                rehearsal_receipt=rehearsal_receipt,
                backup_artifact=backup_artifact,
                htr10_review=htr10_review,
                confirmations=confirmations,
                observed_at=observed_at,
                approve_provider_binding_migration=(approve_provider_binding_migration),
                now=fixed_now,
                attestation_id=fixed_attestation_id,
            )
            if final != initial or final_value != value:
                raise CandidateReadinessError(
                    "deployment binding changed before pending evidence publication"
                )
        artifact_sha256 = hashlib.sha256(encoded).hexdigest()
        return {
            "status": "candidate_pending_recorded",
            "contract": PENDING_CONTRACT,
            "attestation_id": value["attestation_id"],
            "candidate_commit": value["deployment_binding"]["candidate_commit"],
            "artifact_sha256": artifact_sha256,
            "expires_at": value["expires_at"],
            "human_evidence_count": len(value["human_evidence"]),
            "activation_authorized": False,
            "content_exposed": False,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record immutable candidate-pending readiness evidence without activation."
    )
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--expected-candidate-commit", required=True)
    parser.add_argument("--preliminary-rehearsal-receipt", type=Path, required=True)
    parser.add_argument("--preliminary-backup-artifact", type=Path, required=True)
    parser.add_argument("--htr-10-review", type=Path, required=True)
    parser.add_argument(
        "--approve-provider-binding-migration",
        action="store_true",
        help=(
            "bind the exact additive legacy-attribution and reviewed read-only "
            "provider-enrollment transition"
        ),
    )
    for htr in ("01", "02", "03", "04", "06", "07", "10"):
        parser.add_argument(f"--confirm-htr-{htr}", action="store_true")
        if htr != "10":
            parser.add_argument(f"--htr-{htr}-observed-at", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    confirmations = {
        f"htr_{htr}": bool(getattr(args, f"confirm_htr_{htr}"))
        for htr in ("01", "02", "03", "04", "06", "07", "10")
    }
    observed = {
        f"htr_{htr}": str(getattr(args, f"htr_{htr}_observed_at"))
        for htr in ("01", "02", "03", "04", "06", "07")
    }
    try:
        requested_root = args.instance_root.expanduser()
        if (
            not requested_root.is_absolute()
            or not os.path.lexists(requested_root)
            or requested_root.is_symlink()
        ):
            raise CandidateReadinessError(
                "instance root must be an existing non-symlink absolute directory"
            )
        result = record_pending_attestation(
            DeploymentPaths.from_root(requested_root, production=True),
            candidate=args.candidate,
            expected_candidate_commit=args.expected_candidate_commit,
            rehearsal_receipt=args.preliminary_rehearsal_receipt,
            backup_artifact=args.preliminary_backup_artifact,
            htr10_review=args.htr_10_review,
            confirmations=confirmations,
            observed_at=observed,
            approve_provider_binding_migration=(
                args.approve_provider_binding_migration
            ),
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error": type(exc).__name__, "message": str(exc)},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ACTIVE_CONTRACT",
    "CandidateReadinessError",
    "DeploymentSnapshot",
    "HTR10_CONTRACT",
    "PENDING_CONTRACT",
    "build_htr10_review",
    "build_active_attestation_from_pending",
    "build_pending_attestation",
    "capture_deployment_snapshot",
    "deployment_operation_lock",
    "record_htr10_review",
    "record_pending_attestation",
    "load_pending_attestation_for_activation",
    "validate_pending_attestation",
]
