"""Read-only disk admission before a deployment interrupts normal operation.

This estimates space, not backup validity. The encrypted backup, authenticated
restore and Doctor checks remain authoritative and unchanged. Other processes
can consume space after admission, so each destructive boundary checks again.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

from bootstrap.backup_restore import _is_generated_openclaw_codex_cache
from bootstrap.contracts import DeploymentPaths

_GIB = 1024**3
_MIB = 1024**2
RECOVERY_RESERVE_BYTES = 5 * _GIB


class StorageHeadroomError(RuntimeError):
    """Insufficient transaction space; no operation has been started."""


def _footprint(root: Path, *, state: bool) -> tuple[int, int]:
    """Count logical bytes and archive overhead without reading file contents."""
    if not root.is_dir() or root.is_symlink():
        raise StorageHeadroomError("disk preflight requires an existing safe source")
    size = 0
    overhead = 0

    def unreadable(error: OSError) -> None:
        raise error

    for directory, folders, names in os.walk(
        root, followlinks=False, onerror=unreadable
    ):
        parent = Path(directory)
        for name in list(folders) + names:
            path = parent / name
            if (not state and name == ".git") or (
                state and _is_generated_openclaw_codex_cache(path, state_root=root)
            ):
                if name in folders:
                    folders.remove(name)
                continue
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                # A preflight does not lock live writers. Backup will take its
                # own exact quiesced inventory; reserve allows ordinary growth.
                continue
            if stat.S_ISLNK(metadata.st_mode):
                if state and path.parent != root / "openclaw/runtime/plugin-skills":
                    raise StorageHeadroomError("disk preflight refuses a state symlink")
                continue
            if not stat.S_ISREG(metadata.st_mode):
                continue
            if state and metadata.st_uid != os.getuid():
                raise StorageHeadroomError(
                    "disk preflight found unsafe state ownership"
                )
            size += metadata.st_size
            # TAR header/padding, long-name metadata and JSON manifest entry.
            # Include path length without returning or retaining private names.
            overhead += 4096 + 2 * len(os.fsencode(path.relative_to(root)))
    return size, overhead


def _existing_directory(path: Path) -> Path:
    while not path.exists():
        parent = path.parent
        if parent == path:
            raise StorageHeadroomError("disk preflight destination is unavailable")
        path = parent
    if not path.is_dir():
        raise StorageHeadroomError("disk preflight destination is not a directory")
    return path


def _volume(path: Path) -> tuple[int, int]:
    existing = _existing_directory(path)
    return existing.stat().st_dev, shutil.disk_usage(existing).free


def require_deployment_headroom(
    paths: DeploymentPaths,
    *,
    backup_copies: int,
    release_sources: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Admit the remaining transaction, retaining a recovery reserve per device.

    Maintenance reserves three future backups (pause, update, normal restore),
    update reserves two, and normal restore one. A rehearsal peaks with both
    authenticated TAR and extracted state. Source release preparation reserves
    another installed-sized copy plus transient package/build space. Reuse of
    valid backup evidence may reduce actual consumption; it never bypasses this
    conservative admission. No content, credentials or private paths are read
    into the report, and no files or service state are changed.
    """
    if backup_copies not in (1, 2, 3):
        raise ValueError("backup copies must describe a bounded deployment phase")
    try:
        state_bytes, overhead = _footprint(paths.instance_dir, state=True)
        growth = max(64 * _MIB, (state_bytes + 9) // 10)
        archive_bytes = state_bytes + overhead + growth + _MIB
        requirements = [
            ("runtime", paths.root, 0),
            ("encrypted_backups", paths.backup_dir, backup_copies * archive_bytes),
            ("restore_workspace", Path(tempfile.gettempdir()), 2 * archive_bytes),
        ]
        preparation_bytes = 0
        if release_sources:
            # Installed dependencies are included; symlink targets and Git
            # history are not copied by release publication. Growth covers a
            # normal dependency update, not an unbounded toolchain migration.
            largest = max(
                _footprint(source, state=False)[0] for source in release_sources
            )
            preparation_bytes = max(_GIB, largest + largest // 2 + 256 * _MIB)
            requirements.extend(
                [
                    ("release_publication", paths.app_dir, preparation_bytes),
                    (
                        "release_build_workspace",
                        Path(tempfile.gettempdir()),
                        preparation_bytes,
                    ),
                ]
            )
        devices: dict[int, dict[str, Any]] = {}
        for label, destination, required in requirements:
            device, free = _volume(destination)
            record = devices.setdefault(
                device,
                {
                    "purposes": [],
                    "free_bytes": free,
                    "required_bytes": RECOVERY_RESERVE_BYTES,
                },
            )
            record["purposes"].append(label)
            record["free_bytes"] = min(record["free_bytes"], free)
            record["required_bytes"] += required
        checks = list(devices.values())
        for check in checks:
            if check["free_bytes"] < check["required_bytes"]:
                raise StorageHeadroomError(
                    "deployment disk headroom insufficient: "
                    f"{','.join(check['purposes'])}; "
                    f"required_bytes={check['required_bytes']}; "
                    f"free_bytes={check['free_bytes']}; "
                    "runtime state was not changed; free space and retry"
                )
        return {
            "contract": "deployment-storage-headroom-v1",
            "state_bytes": state_bytes,
            "archive_estimate_bytes": archive_bytes,
            "backup_copies": backup_copies,
            "preparation_estimate_bytes": preparation_bytes,
            "checks": checks,
        }
    except OSError as exc:
        raise StorageHeadroomError(
            "deployment disk headroom could not be inspected"
        ) from exc
