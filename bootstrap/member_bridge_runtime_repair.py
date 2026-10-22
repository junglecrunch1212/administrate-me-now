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
    paths = {root / name for name in UNCHANGED_FILES}
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
        if relative == COLOR_SHIM:
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
            if digest != (old_digest if retained else new_digest):
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
    current._stop()
    if launch._pending_path(candidate).exists():
        raise launch.LaunchFailure("launch_recovery_pending", unknown=True)
    if hashlib.sha256(_read(paths.config_path)).hexdigest() != journal["config_sha256"]:
        raise ValueError("configuration changed; explicit reconciliation required")
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
