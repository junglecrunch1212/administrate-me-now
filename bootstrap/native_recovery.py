"""Exact, stopped-state admission for a corrected native-migration artifact.

This is updater evidence, not an OpenClaw write-authority receipt. It never
restores native generations, treats a failed target as healthy, or authorizes
provider effects. A corrected artifact must still pass the ordinary candidate
backup/replay, full retained audit and post-start readiness checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat

import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bootstrap import openclaw_migration as native
from bootstrap.activation_transaction import (
    ActivationJournal,
    sha256_json,
    validate_journal,
)
from bootstrap.candidate_readiness import (
    capture_deployment_snapshot,
    deployment_operation_lock,
)
from bootstrap.contracts import DeploymentPaths, load_versions
from bootstrap.services import ServiceManager
from bootstrap.source_identity import (
    reviewed_candidate_commit,
    verify_release_source_identity,
    _process_group_exists,
)

CONTRACT = "administrateme-native-contained-recovery-v1"
MAX_AGE = timedelta(hours=4)
_HEX = re.compile(r"[0-9a-f]{64}")


class NativeRecoveryError(RuntimeError):
    pass


def lineage(paths: DeploymentPaths, journal: dict[str, Any]) -> list[dict[str, Any]]:
    """Read retained parents; a missing/conflicting link cannot become authority."""
    result = [journal]
    seen = {journal["transaction_id"]}
    while link := result[-1].get("native_recovery"):
        if len(result) >= 8 or link["parent_transaction_id"] in seen:
            raise NativeRecoveryError(
                "native recovery lineage is cyclic or exceeds its bound"
            )
        parent = native._json(
            native._read(
                paths.app_dir
                / "activation"
                / "history"
                / f"{link['parent_transaction_id']}.json"
            )
        )
        validate_journal(parent)
        if sha256_json(parent) != link["parent_journal_sha256"]:
            raise NativeRecoveryError("retained native recovery parent changed")
        seen.add(parent["transaction_id"])
        result.append(parent)
    return result


def valid_binding(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value)
        == {
            "contract",
            "plan_sha256",
            "parent_transaction_id",
            "parent_journal_sha256",
            "candidate_commit",
            "failed_target_commit",
        }
        and value.get("contract") == CONTRACT
        and re.fullmatch(r"[0-9a-f]{32}", str(value.get("parent_transaction_id")))
        and all(
            re.fullmatch(r"[0-9a-f]{40}", str(value.get(key)))
            for key in ("candidate_commit", "failed_target_commit")
        )
        and all(
            _HEX.fullmatch(str(value.get(key)))
            for key in ("plan_sha256", "parent_journal_sha256")
        )
    )


def binding(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "plan_sha256": sha256_json(plan),
        "parent_transaction_id": plan["parent_transaction_id"],
        "parent_journal_sha256": plan["parent_journal_sha256"],
        "candidate_commit": plan["candidate_commit"],
        "failed_target_commit": plan["failed_target_commit"],
    }


def _file_generation(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
    ):
        raise NativeRecoveryError("contained domain/control file is unsafe")

    def coordinate(value: os.stat_result) -> list[int]:
        return [
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        ]

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if any(
        coordinate(item) != coordinate(info) for item in (opened, after, path.lstat())
    ):
        raise NativeRecoveryError("contained state changed while inspecting it")
    return {"generation": coordinate(info), "sha256": digest.hexdigest()}


def generations(paths: DeploymentPaths) -> dict[str, Any]:
    """Bind all retained domain/config/bridge files plus managed native state.

    SQLite shared-memory coordination is not durable history; database and WAL
    bytes are bound. No SQLite connection or native candidate is started here.
    """
    rows: dict[str, Any] = {}
    for directory, subdirs, names in os.walk(paths.instance_dir, followlinks=False):
        parent = Path(directory)
        native._private(parent, directory=True)
        if parent == paths.instance_dir:
            subdirs[:] = [name for name in subdirs if name not in {"openclaw", "logs"}]
        for name in sorted([*subdirs, *names]):
            path = parent / name
            relative = path.relative_to(paths.instance_dir).as_posix()
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                native._private(path, directory=True)
                rows[relative] = {
                    "directory": True,
                    "mode": stat.S_IMODE(info.st_mode),
                    "uid": info.st_uid,
                }
            elif name.endswith("-shm") and path.with_name(name[:-4]).is_file():
                # Require the same safe file shape even though mmap housekeeping
                # may change during a supported read-only retained audit.
                native._private(path)
            else:
                rows[relative] = _file_generation(path)
            if len(rows) > 100_000:
                raise NativeRecoveryError("contained state inventory exceeds its bound")
    for name in (
        "source-install.json",
        "native-roster-migration.json",
        "native-roster-expansion.json",
    ):
        coordinate = paths.instance_dir / "openclaw" / name
        if os.path.lexists(coordinate):
            rows[f"openclaw/{name}"] = _file_generation(coordinate)
    return {
        "native_sha256": sha256_json(native._inventory(paths)),
        "domain_controls_sha256": sha256_json(rows),
        "deployment": capture_deployment_snapshot(paths).public(),
    }


def require_contained(paths: DeploymentPaths, *, test_mode: bool) -> dict[str, Any]:
    journal = ActivationJournal(paths).load()
    if not journal or journal["phase"] != "native_forward_repair_required":
        raise NativeRecoveryError("an exact contained native transaction is required")
    history = lineage(paths, journal)
    migrations = [
        item["openclaw_migration"] for item in history if item.get("openclaw_migration")
    ]
    if not migrations:
        raise NativeRecoveryError("native migration provenance is absent")
    # A stale/missing child receipt is not proof that an escaped writer stopped.
    for migration in migrations:
        doctor = migration.get("doctor")
        if doctor is not None and (
            doctor.get("settled") is not True or _process_group_exists(doctor["pid"])
        ):
            raise NativeRecoveryError("native Doctor custody is unsettled")
    status = ServiceManager(paths, test_mode=test_mode).status()
    if (
        status.status != "stopped"
        or status.runtime != "stopped"
        or status.openclaw != "stopped"
    ):
        raise NativeRecoveryError("managed services must be demonstrably stopped")
    runtime = yaml.safe_load(native._read(paths.instance_dir / "config/runtime.yaml"))
    observation = native._json(
        native._read(paths.instance_dir / "config/observation.json")
    )
    if (
        not isinstance(runtime, dict)
        or (runtime.get("observation_mode_override") or {}).get("active") is not True
        or observation.get("active") is not True
    ):
        raise NativeRecoveryError(
            "native correction requires retained observation containment"
        )
    return journal


def prepare(
    paths: DeploymentPaths,
    candidate: Path,
    *,
    test_mode: bool = False,
    _parent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ownership = ActivationJournal(paths).load()
    journal = (
        _parent
        if _parent is not None
        else require_contained(paths, test_mode=test_mode)
    )
    commit = reviewed_candidate_commit(candidate)
    if commit in {item["target_commit"] for item in lineage(paths, journal)}:
        raise NativeRecoveryError("recovery requires a distinct corrected artifact")
    failed = Path(journal["target_path"])
    verify_release_source_identity(
        failed,
        repository=candidate,
        commit=journal["target_commit"],
        allow_legacy_tree=False,
        require_tracked_tree=True,
    )
    # This slice repairs the same native format/toolchain; another migration
    # requires its own reviewed transition and cannot piggyback on containment.
    before, after = load_versions(failed), load_versions(candidate)
    for key in ("openclaw", "node", "python"):
        if before[key] != after[key]:
            raise NativeRecoveryError(
                "corrected artifact changes the contained native/toolchain identity"
            )
    now = datetime.now(timezone.utc)
    state = generations(paths)
    result = {
        "contract": CONTRACT,
        "parent_transaction_id": journal["transaction_id"],
        "parent_journal_sha256": sha256_json(journal),
        "candidate_commit": commit,
        "failed_target_commit": journal["target_commit"],
        "state": state,
        "recorded_at": now.isoformat(),
        "expires_at": (now + MAX_AGE).isoformat(),
        "source_history_retained": True,
        "prior_target_health_proven": False,
        "automatic_rollback_eligible": False,
        "content_exposed": False,
    }
    if generations(paths) != state or ActivationJournal(paths).load() != ownership:
        raise NativeRecoveryError("contained state changed during recovery preparation")
    return result


def load(
    paths: DeploymentPaths, candidate: Path, path: Path, *, test_mode: bool = False
) -> dict[str, Any]:
    value = native._json(native._read(path))
    expected = {
        "contract",
        "parent_transaction_id",
        "parent_journal_sha256",
        "candidate_commit",
        "failed_target_commit",
        "state",
        "recorded_at",
        "expires_at",
        "source_history_retained",
        "prior_target_health_proven",
        "automatic_rollback_eligible",
        "content_exposed",
    }
    if (
        set(value) != expected
        or value.get("contract") != CONTRACT
        or not valid_binding(binding(value))
    ):
        raise NativeRecoveryError("native recovery plan schema is invalid")
    try:
        recorded, expiry = (
            datetime.fromisoformat(value[key]) for key in ("recorded_at", "expires_at")
        )
        now = datetime.now(timezone.utc)
        fresh = recorded <= now < expiry and expiry - recorded <= MAX_AGE
    except (TypeError, ValueError):
        fresh = False
    if (
        not fresh
        or value["source_history_retained"] is not True
        or any(
            value[key] is not False
            for key in (
                "prior_target_health_proven",
                "automatic_rollback_eligible",
                "content_exposed",
            )
        )
    ):
        raise NativeRecoveryError("native recovery plan is stale or misstates recovery")
    owner = ActivationJournal(paths).load()
    parent = None
    if owner is not None and owner.get("native_recovery") == binding(value):
        if (
            owner["phase"]
            not in {
                "created",
                "backup_creating",
                "backup_created",
                "backup_rehearsing",
                "backup_rehearsed",
                "rollback_eligible",
            }
            or owner.get("openclaw_migration") is not None
        ):
            raise NativeRecoveryError(
                "native admission already crossed the state mutation boundary"
            )
        status = ServiceManager(paths, test_mode=test_mode).status()
        if (
            status.status != "stopped"
            or status.runtime != "stopped"
            or status.openclaw != "stopped"
        ):
            raise NativeRecoveryError(
                "native recovery services changed before admission"
            )
        parent = lineage(paths, owner)[1]
    current = prepare(paths, candidate, test_mode=test_mode, _parent=parent)
    if any(
        current[key] != value[key] for key in expected - {"recorded_at", "expires_at"}
    ):
        raise NativeRecoveryError(
            "native recovery journal, state, authority or candidate changed"
        )
    return value


def mark_forward_readiness(
    evidence: dict[str, Any], *, journal: dict[str, Any], migration_origin: str
) -> None:
    """State the actual recovery model without promoting health or rollback."""
    evidence["backup_rollback"]["automatic_rollback_eligible"] = False
    evidence["native_forward_recovery"] = {
        "contract": "deployment-native-forward-recovery-v1",
        "release_commit": journal["target_commit"],
        "transaction_id": journal["transaction_id"],
        "origin_transaction_id": migration_origin,
        "pending_attestation_sha256": journal["pending_attestation_sha256"],
        "prior_target_health_proven": False,
        "native_state_retained": True,
        "domain_history_retained": True,
        "automatic_rollback_eligible": False,
        "recovery_mode": "protected-corrected-artifact",
        "content_exposed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bind a corrected artifact to exact stopped native state; no activation."
    )
    parser.add_argument("--instance-root", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    paths = DeploymentPaths.from_root(args.instance_root, production=True)
    with deployment_operation_lock(paths):
        value = prepare(paths, args.candidate)
        native._private(args.output.parent, directory=True)
        descriptor = os.open(
            args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(value, sort_keys=True, indent=2).encode())
            stream.flush()
            os.fsync(stream.fileno())
    print(
        json.dumps(
            {
                "status": "native_recovery_prepared",
                "candidate_commit": value["candidate_commit"],
                "activation_authorized": False,
                "content_exposed": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
