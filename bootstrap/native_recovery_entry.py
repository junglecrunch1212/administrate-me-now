"""Standard-library-only tooling selection before the recovery controller builds.

The fixed supervised update script calls this with the system interpreter. It
selects only an already consumed failed-target runtime, never installs a new
runtime, changes the live selector, or grants migration authority. Full stopped
state/pending/backup admission remains in native_recovery and UpgradeManager.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# -I omits the script directory. This is the immutable fixed controller tree,
# not an ambient PYTHONPATH or an installed failed-target Python package.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bootstrap.prepared_runtime import runtime_artifact_fingerprint
from bootstrap.source_identity import (
    reviewed_candidate_commit,
    verify_release_source_identity,
)


class NativeRecoveryEntryError(RuntimeError):
    pass


def _read(path: Path, *, private: bool = True) -> dict[str, Any]:
    before = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or before.st_mode & (0o077 if private else 0o022)
        or not 0 < before.st_size <= 4 * 1024 * 1024
    ):
        raise NativeRecoveryEntryError("native recovery entry input is unsafe")

    def coordinate(info: os.stat_result) -> tuple[int, ...]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    with path.open("rb") as stream:
        payload = stream.read(4 * 1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
    if (
        coordinate(before) != coordinate(after)
        or coordinate(after) != coordinate(path.lstat())
        or len(payload) != before.st_size
    ):
        raise NativeRecoveryEntryError("native recovery entry input changed")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, child in values:
            if key in result:
                raise NativeRecoveryEntryError(
                    "native recovery entry has duplicate fields"
                )
            result[key] = child
        return result

    result = json.loads(payload, object_pairs_hook=pairs)
    if not isinstance(result, dict):
        raise NativeRecoveryEntryError("native recovery entry requires an object")
    return result


def select_runtime(
    instance: Path, candidate: Path, commit: str, plan_path: Path
) -> Path:
    if (
        re.fullmatch(r"[0-9a-f]{40}", commit) is None
        or reviewed_candidate_commit(candidate) != commit
    ):
        raise NativeRecoveryEntryError("corrected candidate source changed")
    plan = _read(plan_path)
    parent_path = instance / "app/activation/active.json"
    parent = _read(parent_path)
    parent_digest = hashlib.sha256(
        json.dumps(parent, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    try:
        recorded, expires = (
            datetime.fromisoformat(plan[key]) for key in ("recorded_at", "expires_at")
        )
        fresh = recorded <= datetime.now(
            timezone.utc
        ) < expires and expires - recorded <= timedelta(hours=4)
    except (KeyError, TypeError, ValueError):
        fresh = False
    if (
        not fresh
        or plan.get("contract") != "administrateme-native-contained-recovery-v1"
        or plan.get("candidate_commit") != commit
        or parent.get("contract") != "administrateme-activation-transaction-v1"
        or parent.get("operation") != "activate"
        or parent.get("phase") != "native_forward_repair_required"
        or parent.get("transaction_id") != plan.get("parent_transaction_id")
        or parent_digest != plan.get("parent_journal_sha256")
        or parent.get("target_commit") != plan.get("failed_target_commit")
        or not (parent.get("openclaw_migration") or parent.get("native_recovery"))
        or plan.get("automatic_rollback_eligible") is not False
    ):
        raise NativeRecoveryEntryError(
            "exact contained recovery tooling authority is missing"
        )
    versions = _read(candidate / "bootstrap/versions.json", private=False)
    current = parent
    seen: set[str] = set()
    retained: list[tuple[Path, dict[str, Any]]] = []
    selected = None
    # A correction can fail during backup, before consuming its preparation.
    # Only an exact retained ancestor with the same runtime may supply tooling.
    # Never run an unconsumed artifact merely because its version strings match.
    for _ in range(8):
        failed_commit = current.get("target_commit")
        identity = current.get("transaction_id")
        if (
            re.fullmatch(r"[0-9a-f]{40}", str(failed_commit)) is None
            or re.fullmatch(r"[0-9a-f]{32}", str(identity)) is None
            or failed_commit == commit
            or identity in seen
        ):
            raise NativeRecoveryEntryError("native recovery lineage is invalid")
        seen.add(identity)
        release = instance / "app/releases" / failed_commit
        if current.get("target_path") != str(release) or release.is_symlink():
            raise NativeRecoveryEntryError("failed target coordinate changed")
        source = verify_release_source_identity(
            release,
            repository=candidate,
            commit=failed_commit,
            allow_legacy_tree=False,
            require_tracked_tree=True,
        )
        before = _read(release / "bootstrap/versions.json", private=False)
        if any(
            before.get(key) != versions.get(key)
            or not isinstance(versions.get(key), dict)
            for key in ("openclaw", "node", "python")
        ):
            raise NativeRecoveryEntryError(
                "recovery cannot authorize another runtime transition"
            )
        prepared_path = release.parent / f".release-state-{failed_commit}.json"
        prepared = _read(prepared_path)
        retained.append((prepared_path, prepared))
        info = release.lstat()
        if (
            prepared.get("contract") != "administrateme-release-preparation-v1"
            or prepared.get("phase") not in {"complete", "consumed"}
            or prepared.get("commit") != failed_commit
            or prepared.get("candidate_controller_commit") != failed_commit
            or prepared.get("instance_sha256")
            != hashlib.sha256(str(instance).encode()).hexdigest()
            or prepared.get("release_device") != info.st_dev
            or prepared.get("release_inode") != info.st_ino
            or prepared.get("source_manifest_sha256")
            != source["tracked_source_manifest_sha256"]
            or prepared.get("runtime_artifact_sha256")
            != runtime_artifact_fingerprint(release)
        ):
            raise NativeRecoveryEntryError("retained target preparation changed")
        if prepared["phase"] == "consumed":
            selected = release
            break
        link = current.get("native_recovery") or {}
        if (
            link.get("contract") != "administrateme-native-contained-recovery-v1"
            or link.get("candidate_commit") != failed_commit
            or re.fullmatch(r"[0-9a-f]{32}", str(link.get("parent_transaction_id")))
            is None
        ):
            raise NativeRecoveryEntryError("no consumed recovery runtime is retained")
        path = (
            instance
            / "app/activation/history"
            / f"{link['parent_transaction_id']}.json"
        )
        ancestor = _read(path)
        retained.append((path, ancestor))
        if (
            ancestor.get("contract") != "administrateme-activation-transaction-v1"
            or ancestor.get("operation") != "activate"
            or ancestor.get("phase") != "native_forward_repair_required"
            or ancestor.get("transaction_id") != link["parent_transaction_id"]
            or ancestor.get("target_commit") != link.get("failed_target_commit")
            or hashlib.sha256(
                json.dumps(ancestor, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            != link.get("parent_journal_sha256")
        ):
            raise NativeRecoveryEntryError("retained recovery lineage changed")
        current = ancestor
    if selected is None:
        raise NativeRecoveryEntryError(
            "no bounded consumed recovery runtime is retained"
        )
    if (
        _read(parent_path) != parent
        or _read(plan_path) != plan
        or any(_read(path) != value for path, value in retained)
    ):
        raise NativeRecoveryEntryError("native recovery entry binding changed")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("commit")
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    print(select_runtime(args.instance, args.candidate, args.commit, args.plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
