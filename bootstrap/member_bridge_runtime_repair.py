"""Replace a contained read-only bridge runtime without replacing its enrollment.

Use only a prepared immutable protected-main release. The command preserves the
configuration, proof key and cursor database; its journal retains the predecessor
pointer, launch definition and receipt. Rollback leaves the contained predecessor
stopped. An uncertain launch never permits pointer rollback or another start.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any
import uuid

from bootstrap.bridge_enrollment import MemberBridgeInstaller, MemberBridgePaths
from bootstrap.contracts import atomic_write
from bootstrap import member_bridge_launch as launch
from bootstrap.member_bridge_permission_controller import controller_lock
from bootstrap.member_bridge_component import PERMISSION_RECOVERY_COS_OVERLAY


CONTRACT = "administrateme-member-runtime-repair-v1"
COLOR_SHIM = "packs/adapters/reminders/apple-eventkit/_eventkit_shim.py"
ACQUISITION_PATHS = frozenset(
    {COLOR_SHIM, "packs/adapters/reminders/apple-eventkit/adapter.py"}
)
UNCHANGED_FILES = (
    "poetry.lock",
    "bridge/poetry.lock",
    "pyproject.toml",
    "bridge/pyproject.toml",
    "bootstrap/bridge_enrollment.py",
    "adminme/bridge_authorization.py",
    "adminme/lib/eventkit_recurrence.py",
)
UNCHANGED_TREES = (
    "bridge/adminme_bridge",
    "packs/adapters/calendaring/apple-calendar",
    "packs/adapters/reminders/apple-eventkit",
    "adminme/events",
)


# Reviewed additive write overlay. Both sides retain the identical read-only
# enrollment/launch/configuration contract; authority is a separate audited grant.
# Exact old/new bytes prevent this component repair from becoming a generic
# runtime-interface bypass. Refresh a pair only with its affected checks.
APPLE_SERVICE_WRITE_OVERLAY: dict[str, tuple[str | None, str]] = {
    "adminme/apple_service_permissions.py": (
        None,
        "26a6413a1eb41f51a4276007d1a7f25ec2751fac6d4b76d79f22484f46acfee1",
    ),
    "adminme/apple_notes_writes.py": (
        None,
        "11a4e2fd3e3b2373fda37986d6ebf228726f0dffa020e900ee6aa9a31d164af5",
    ),
    "adminme/events/schemas/apple_service_permissions.py": (
        None,
        "dd8e7aa2544f4e5bde272cdf3ccc2c52c2236d3352d068d0f994d830a5cfb1c9",
    ),
    "adminme/events/schemas/external_sync.py": (
        "b844efd28abe9e9de15af7ff62bc4d67b31906f76638a50e608ddc94d2c3b659",
        "c9987b6b6dc753ff9990dd9ad37f22a6b9029920524617f3e14e1334ba273d07",
    ),
    "bridge/adminme_bridge/main.py": (
        "07836c5334b87b53a5fec80aed9f325c591bd37995a38b71330dcac7ec707159",
        "55bdf2c6c41e02c4db88bb2f014e7d4d3ba27c31d76c14f8ea5a19ed5dbc1746",
    ),
    "bridge/adminme_bridge/ingest_client.py": (
        "1ca09adde4a2c23633bef66ef7f641c6e75b4194ba2e0489bd8bbe704a2f09c0",
        "77e9d70d8bcf4822dc90d3dee0918650220ec958b579c8334b14b296c7ef5baf",
    ),
    "bridge/adminme_bridge/connector_packs/apple_notes/provider.py": (
        "627ad16ce4e8559b2d0ff705c4b493cc71901eb71365cd760256ea9c82024de0",
        "66d9d895be554dba3820cca81c8136678424bfd30bbf86384bdd2370ee738a20",
    ),
    "packs/adapters/calendaring/apple-calendar/adapter.py": (
        "d508be2822271f8710fbb0843c799662fd49d3f7bfbecc53dab7713b33457e7c",
        "830e04296d6f6a341c08ed324351c6a4cada39bf5e84c6bf5c67e1f3717bea5b",
    ),
    "packs/adapters/reminders/apple-eventkit/adapter.py": (
        "c25e284adeaa872292ead2228d0e9b6d2024b837e3adeb09e28779f6bdddaa97",
        "11a7113382f15cfbb45863a4ac54fae1b8d21f6b412fb6280377662a5cecda63",
    ),
}

_DISPATCHER_PATH = "bridge/adminme_bridge/main.py"
_APPLE_PROVIDERS = frozenset({"apple_calendar", "apple_reminders", "apple_notes"})


def _retained_command_history_requires_aware_runtime(
    paths: MemberBridgePaths, config: dict[str, Any]
) -> bool:
    """Read the sealed local ledger without creating/migrating or clearing it."""
    database = paths.root / "adapter-cursors.db"
    try:
        if (
            config.get("cursor_db_path") != str(database)
            or paths.root.resolve() != paths.root
            or not launch._owned(paths.root, directory=True, private=True)
            or database.resolve() != database
            or not launch._owned(database)
        ):
            raise ValueError("unsafe command history coordinate")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = database.with_name(database.name + suffix)
            if (sidecar.exists() or sidecar.is_symlink()) and not launch._owned(
                sidecar
            ):
                raise ValueError("unsafe command history sidecar")
        identity = database.stat()
        connection = sqlite3.connect(
            database.as_uri() + "?mode=ro", uri=True, timeout=1
        )
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            expected = {
                "bridge_command_attempts": [
                    ("command_id", "TEXT", 1),
                    ("request_fingerprint", "TEXT", 0),
                    ("started_at", "TEXT", 0),
                ],
                "bridge_command_results": [
                    ("command_id", "TEXT", 1),
                    ("envelope_json", "TEXT", 0),
                ],
            }
            for table, columns in expected.items():
                kind = connection.execute(
                    "SELECT type FROM sqlite_master WHERE name = ?", (table,)
                ).fetchone()
                actual = connection.execute(f"PRAGMA table_info({table})").fetchall()
                if (
                    kind != ("table",)
                    or [(row[1], row[2], row[5]) for row in actual] != columns
                ):
                    raise ValueError("unknown command history schema")
            # Attempts do not include provider/grant coordinates. Even an exact
            # false-result pairing cannot erase one: old code could create that
            # pairing by rejecting read-only authority before checking the attempt.
            unsafe = (
                connection.execute(
                    "SELECT 1 FROM bridge_command_attempts LIMIT 1"
                ).fetchone()
                is not None
            )
            for command_id, encoded in connection.execute(
                "SELECT command_id, envelope_json FROM bridge_command_results"
            ):
                envelope = json.loads(encoded)
                payload = (
                    envelope.get("payload") if isinstance(envelope, dict) else None
                )
                if (
                    not isinstance(payload, dict)
                    or envelope.get("schema_version") != 2
                    or envelope.get("type")
                    not in {
                        "external_sync.write_completed",
                        "external_sync.write_failed",
                    }
                    or not isinstance(command_id, str)
                    or not command_id
                    or payload.get("command_id") != command_id
                    or not isinstance(payload.get("provider"), str)
                    or not payload["provider"]
                    or not isinstance(payload.get("provider_called"), bool)
                    or re.fullmatch(
                        r"[0-9a-f]{64}", str(payload.get("request_fingerprint"))
                    )
                    is None
                ):
                    raise ValueError("invalid command result history")
                result = payload.get("result", {})
                if not isinstance(result, dict):
                    raise ValueError("invalid command outcome history")
                if payload["provider"] in _APPLE_PROVIDERS and (
                    payload["provider_called"]
                    or result.get("outcome") in {"unknown", "ambiguous"}
                ):
                    unsafe = True
            current = database.stat()
            if (identity.st_dev, identity.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("command history changed identity")
            return unsafe
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        raise ValueError(
            "bridge command history cannot be verified; runtime rollback refused"
        ) from exc


def _require_command_history_compatibility(
    paths: MemberBridgePaths, target: Path, config: dict[str, Any]
) -> None:
    dispatcher = target / _DISPATCHER_PATH
    pair = APPLE_SERVICE_WRITE_OVERLAY.get(_DISPATCHER_PATH)
    if pair is None or not launch._owned(dispatcher):
        raise ValueError("unsupported bridge dispatcher recovery coordinate")
    target_digest = hashlib.sha256(dispatcher.read_bytes()).hexdigest()
    if target_digest == pair[1]:
        return
    if target_digest != pair[0]:
        raise ValueError("unsupported bridge dispatcher recovery coordinate")
    if _retained_command_history_requires_aware_runtime(paths, config):
        raise ValueError(
            "retained bridge command attempts or Apple results require a write-aware "
            "recovery target; keep the current runtime, revoke writes and settle "
            "outstanding results without restoring command history"
        )


def _read(path: Path) -> bytes:
    if not launch._owned(path, private=True):
        raise ValueError("runtime repair requires owned private state")
    return path.read_bytes()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(_read(path))
    if not isinstance(value, dict):
        raise ValueError("invalid runtime repair state")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    atomic_write(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def _inventory(root: Path, *, retained: bool) -> dict[str, str]:
    paths = {root / name for name in (*UNCHANGED_FILES, *APPLE_SERVICE_WRITE_OVERLAY)}
    for name in UNCHANGED_TREES:
        paths.update(
            path
            for path in (root / name).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and "tests" not in path.relative_to(root).parts
            and path.suffix != ".pyc"
        )
    result = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if relative in APPLE_SERVICE_WRITE_OVERLAY:
            old_digest, new_digest = APPLE_SERVICE_WRITE_OVERLAY[relative]
            if not path.exists() and retained and old_digest is None:
                result[relative] = "absent-before-apple-write-overlay"
                continue
            if not launch._owned(path):
                raise ValueError("Apple write overlay file missing or unsafe")
            current_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            accepted = {old_digest, new_digest} if retained else {new_digest}
            if current_digest not in accepted:
                raise ValueError("unreviewed Apple write overlay difference")
            result[relative] = old_digest or "absent-before-apple-write-overlay"
            continue
        if relative in ACQUISITION_PATHS:
            continue
        if not launch._owned(path):
            raise ValueError("runtime interface missing or unsafe")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if relative in {
            "adminme/events/schemas/cos_channels.py",
            "adminme/events/schemas/cos_responses.py",
        }:
            # These central-only schemas were already deployed independently;
            # the edge state and command formats remain byte-identical.
            old_digest, new_digest = PERMISSION_RECOVERY_COS_OVERLAY[relative]
            accepted = {old_digest, new_digest} if retained else {new_digest}
            if digest not in accepted:
                raise ValueError("unreviewed central schema difference")
            digest = str(old_digest)
        result[relative] = digest
    return result


def _release(paths: MemberBridgePaths, commit: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("full immutable release identity required")
    root = paths.root / "app/releases" / commit
    if root.resolve() != root or not launch._owned(root, directory=True):
        raise ValueError("unsafe runtime release")
    for relative in (
        ".administrateme-source-commit",
        ".venv/.administrateme-bridge-installed-commit",
    ):
        marker = root / relative
        if not launch._owned(marker) or marker.read_text().strip() != commit:
            raise ValueError("runtime release is not completely prepared")
    if not (root / ".venv/bin/python").is_file():
        raise ValueError("runtime interpreter missing")
    return root


def _installer(paths: MemberBridgePaths, root: Path) -> Any:
    return launch.compatible_installer_class(MemberBridgeInstaller)(paths, root=root)


def _settle(installer: Any) -> None:
    initial = launch.launch_probe(installer)
    if not initial.running:
        raise ValueError("replacement bridge did not start")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = launch.launch_probe(installer)
        if not state.running or state.pid != initial.pid or state.runs != initial.runs:
            raise ValueError("replacement bridge restarted during settlement")
        time.sleep(1)


def _record(path: Path, journal: dict[str, Any], state: str) -> None:
    journal["state"] = state
    journal.setdefault("transitions", []).append(state)
    _write(path, journal)


def _restore(
    paths: MemberBridgePaths, operation: Path, journal: dict[str, Any], candidate: Any
) -> None:
    # _stop proves quiescence and refuses a timed-out launchctl operation.
    old = _installer(paths, _release(paths, journal["predecessor"]))
    import plistlib

    definition = plistlib.loads(_read(paths.launch_agent_path))
    current = (
        candidate
        if definition == launch._expected_plist(candidate)
        else old
        if definition == launch._expected_plist(old)
        else None
    )
    if current is None:
        raise ValueError("launch definition changed; explicit reconciliation required")
    config_raw, config = launch._configuration(candidate)
    if hashlib.sha256(config_raw).hexdigest() != journal["config_sha256"]:
        raise ValueError("configuration changed; explicit reconciliation required")
    _require_command_history_compatibility(paths, old.root, config)
    current._stop()
    if launch._pending_path(candidate).exists():
        raise launch.LaunchFailure("launch_recovery_pending", unknown=True)
    config_raw, config = launch._configuration(candidate)
    if hashlib.sha256(config_raw).hexdigest() != journal["config_sha256"]:
        raise ValueError("configuration changed; explicit reconciliation required")
    # A command may have begun after preflight but before the stop completed.
    # Never restore an unaware selector over that newly durable local attempt.
    _require_command_history_compatibility(paths, old.root, config)
    originals = []
    for name, destination in (
        ("pointer.json", paths.root / "app/current-release.json"),
        ("launch-agent.plist", paths.launch_agent_path),
        ("receipt.json", paths.receipt_path),
    ):
        original = _read(operation / name)
        if hashlib.sha256(original).hexdigest() != journal["snapshot_sha256"][name]:
            raise ValueError("runtime repair snapshot mismatch")
        originals.append((destination, original))
    for destination, original in originals:
        atomic_write(destination, original)
    _record(operation / "journal.json", journal, "rolled_back_stopped")


def repair(
    paths: MemberBridgePaths, *, predecessor: str, candidate: str
) -> dict[str, Any]:
    old_root, new_root = _release(paths, predecessor), _release(paths, candidate)
    if old_root == new_root or _inventory(old_root, retained=True) != _inventory(
        new_root, retained=False
    ):
        raise ValueError("repair changes unsupported runtime interfaces")
    old, new = _installer(paths, old_root), _installer(paths, new_root)
    with controller_lock(paths.root), launch._transaction(old):
        launch._require_no_pending(old)
        pointer = _json(paths.root / "app/current-release.json")
        if pointer != {"path": str(old_root), "commit": predecessor}:
            raise ValueError("selected predecessor changed")
        config_raw, config = launch._configuration(old)
        receipt = _json(paths.receipt_path)
        if (
            receipt.get("status") != "disabled"
            or not config["enabled_packs"]
            or receipt.get("release_commit") != config.get("release_commit")
            or any(
                not config.get(key) or receipt.get(key) != config[key]
                for key in ("member_id", "enrollment_id", "request_id", "instance_id")
            )
        ):
            raise ValueError("repair requires a contained enrolled read-only bridge")
        if launch.launch_probe(old).present or launch._daemon_pids():
            raise ValueError("contain the old bridge before runtime repair")
        history = paths.root / "runtime-repairs"
        if not history.exists():
            history.mkdir(mode=0o700)
        if history.resolve() != history or not launch._owned(
            history, directory=True, private=True
        ):
            raise ValueError("unsafe runtime repair history")
        for previous in history.glob("*/journal.json"):
            if _json(previous).get("state") not in {"activated", "rolled_back_stopped"}:
                raise ValueError("prior runtime repair needs reconciliation")
        operation_id = uuid.uuid4().hex
        operation = history / operation_id
        operation.mkdir(mode=0o700)
        snapshots = {
            "pointer.json": _read(paths.root / "app/current-release.json"),
            "launch-agent.plist": _read(paths.launch_agent_path),
            "receipt.json": _read(paths.receipt_path),
        }
        for name, raw in snapshots.items():
            atomic_write(operation / name, raw)
        journal: dict[str, Any] = {
            "contract": CONTRACT,
            "operation_id": operation_id,
            "predecessor": predecessor,
            "candidate": candidate,
            "config_sha256": hashlib.sha256(config_raw).hexdigest(),
            "snapshot_sha256": {
                name: hashlib.sha256(raw).hexdigest() for name, raw in snapshots.items()
            },
            "provider_writes": False,
        }
        journal_path = operation / "journal.json"
        _record(journal_path, journal, "prepared")
        try:
            _record(journal_path, journal, "switching")
            new._install_launch_agent()
            _write(
                paths.root / "app/current-release.json",
                {"path": str(new_root), "commit": candidate},
            )
            _record(journal_path, journal, "starting")
            new._start()
            _settle(new)
            receipt["status"] = "active"
            receipt["runtime_release_commit"] = candidate
            receipt["runtime_repair_operation"] = operation_id
            _write(paths.receipt_path, receipt)
            _record(journal_path, journal, "activated")
        except Exception as exc:
            if isinstance(exc, launch.LaunchFailure) and exc.unknown:
                _record(journal_path, journal, "reconciliation_required")
            else:
                try:
                    _restore(paths, operation, journal, new)
                except Exception:
                    _record(journal_path, journal, "reconciliation_required")
                    raise
            raise
    return {
        "status": "activated",
        "operation_id": operation_id,
        "provider_writes": False,
    }


def rollback(paths: MemberBridgePaths, operation_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise ValueError("exact runtime repair operation required")
    operation = paths.root / "runtime-repairs" / operation_id
    if operation.resolve() != operation or not launch._owned(
        operation, directory=True, private=True
    ):
        raise ValueError("unsafe runtime repair operation")
    journal = _json(operation / "journal.json")
    if (
        journal.get("contract") != CONTRACT
        or journal.get("operation_id") != operation_id
    ):
        raise ValueError("runtime repair operation mismatch")
    if journal.get("state") == "rolled_back_stopped":
        return {"status": "rolled_back_stopped", "operation_id": operation_id}
    if journal.get("state") not in {"activated", "prepared", "switching", "starting"}:
        raise ValueError("runtime repair needs explicit reconciliation")
    candidate = _installer(paths, _release(paths, journal["candidate"]))
    with controller_lock(paths.root), launch._transaction(candidate):
        launch._require_no_pending(candidate)
        pointer = _json(paths.root / "app/current-release.json")
        if pointer not in (
            {"commit": journal["candidate"], "path": str(candidate.root)},
            {
                "commit": journal["predecessor"],
                "path": str(paths.root / "app/releases" / journal["predecessor"]),
            },
        ):
            raise ValueError("selected runtime changed")
        _restore(paths, operation, journal, candidate)
    return {"status": "rolled_back_stopped", "operation_id": operation_id}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-root", type=Path, required=True)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--expected-predecessor")
    parser.add_argument("--expected-candidate", required=True)
    operation.add_argument("--rollback-operation")
    args = parser.parse_args()
    paths = MemberBridgePaths.from_root(args.bridge_root, production=True)
    root = _release(paths, args.expected_candidate)
    if (
        Path(__file__).resolve().parents[1] != root
        or Path(sys.prefix) != root / ".venv"
    ):
        raise ValueError("run the prepared candidate's own repair command")
    result = (
        rollback(paths, args.rollback_operation)
        if args.rollback_operation
        else repair(
            paths,
            predecessor=args.expected_predecessor,
            candidate=args.expected_candidate,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
