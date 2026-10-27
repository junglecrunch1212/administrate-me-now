"""Content identity for an owned, inactive runtime that finished preparation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import TypedDict


_RUNTIME_ROOTS = (
    ".venv",
    "openclaw/administrateme/node_modules",
    "openclaw/administrateme/dist",
)
_MAX_FILES = 200_000
_MAX_BYTES = 8 * 1024 * 1024 * 1024
_MAX_NATIVE_INVENTORY_BYTES = 128 * 1024 * 1024
NATIVE_ARTIFACT_PROFILE = "native-full-tree-metadata-v1"


class ArtifactInventory(TypedDict):
    artifact_sha256: str
    record_count: int
    content_bytes: int
    inventory_metadata_bytes: int


class NativeArtifactInventory(ArtifactInventory):
    profile: str


class PreparedRuntimeError(RuntimeError):
    pass


def _coordinate(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def runtime_artifact_fingerprint(
    release: Path, *, _roots: tuple[str, ...] = _RUNTIME_ROOTS
) -> str:
    """Preserve the existing generated-runtime identity and 200,000-record bound."""
    return _artifact_inventory(
        release, roots=_roots, max_records=_MAX_FILES, max_metadata_bytes=None
    )["artifact_sha256"]


def native_artifact_inventory(source: Path) -> NativeArtifactInventory:
    """Bind the complete native tree under metadata and content byte budgets.

    Native pnpm graphs include many directory/link records. Charge their full
    path/coordinate metadata before retaining it instead of borrowing the small
    application runtime's row-count ceiling. No source or dependency is omitted.
    """
    return {
        **_artifact_inventory(
            source,
            roots=(".",),
            max_records=None,
            max_metadata_bytes=_MAX_NATIVE_INVENTORY_BYTES,
        ),
        "profile": NATIVE_ARTIFACT_PROFILE,
    }


def _artifact_inventory(
    release: Path,
    *,
    roots: tuple[str, ...],
    max_records: int | None,
    max_metadata_bytes: int | None,
) -> ArtifactInventory:
    """Hash bytes, modes and link targets without following generated links.

    The caller already verifies tracked source, release ownership and the pinned
    interpreters. This additional proof covers generated dependency/build bytes;
    directory-relative no-follow reads detect replacement during the inventory.
    Missing roots are represented, allowing credential-free installer fixtures.
    """
    digest = hashlib.sha256(b"administrateme-prepared-runtime-v1\0")
    count = 0
    size = 0
    metadata_bytes = 0
    inventory: dict[str, tuple[int, ...]] = {}
    interpreters: dict[Path, tuple[int, ...]] = {}

    def remember(relative: str, value: os.stat_result) -> None:
        nonlocal metadata_bytes
        coordinate = _coordinate(value)
        if relative in inventory:
            if inventory[relative] != coordinate:
                raise PreparedRuntimeError("prepared runtime changed during inventory")
            return
        if max_records is not None and len(inventory) >= max_records:
            raise PreparedRuntimeError("prepared runtime exceeds file limit")
        # One bounded path/coordinate row is encoded before allocating its
        # retained dictionary entry or appending the directory entry name.
        encoded_size = (
            len(json.dumps([relative, coordinate], separators=(",", ":")).encode()) + 1
        )
        next_size = metadata_bytes + encoded_size
        if max_metadata_bytes is not None and next_size > max_metadata_bytes:
            raise PreparedRuntimeError(
                "prepared runtime exceeds inventory metadata limit "
                f"({next_size}>{max_metadata_bytes}; records={count}, content_bytes={size})"
            )
        metadata_bytes = next_size
        inventory[relative] = coordinate

    def record(row: list[str | int]) -> None:
        nonlocal count
        count += 1
        if max_records is not None and count > max_records:
            raise PreparedRuntimeError("prepared runtime exceeds file limit")
        digest.update(json.dumps(row, separators=(",", ":")).encode())
        digest.update(b"\n")

    def scan(descriptor: int, relative: str) -> None:
        nonlocal size
        initial = os.fstat(descriptor)
        if not stat.S_ISDIR(initial.st_mode) or initial.st_uid != os.getuid():
            raise PreparedRuntimeError("prepared runtime directory is unsafe")
        if initial.st_mode & 0o022:
            raise PreparedRuntimeError(
                "prepared runtime directory is writable by others"
            )
        remember(relative, initial)
        record([relative, "directory", stat.S_IMODE(initial.st_mode)])
        names: list[str] = []
        # Enumerate incrementally: a huge directory must hit the inventory
        # budget before allocating an unbounded list of names.
        with os.scandir(descriptor) as entries:
            for entry in entries:
                remember(
                    relative + "/" + entry.name,
                    os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False),
                )
                names.append(entry.name)
        for name in sorted(names):
            path = relative + "/" + name
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            remember(path, before)
            if before.st_uid != os.getuid():
                raise PreparedRuntimeError("prepared runtime ownership is unsafe")
            if stat.S_ISLNK(before.st_mode):
                target = os.readlink(name, dir_fd=descriptor)
                resolved = (release / path).resolve(strict=True)
                if not resolved.is_relative_to(release.resolve()):
                    interpreter_alias = (
                        re.fullmatch(r"\.venv/bin/python(?:3(?:\.[0-9]+)?)?", path)
                        is not None
                        or path == ".venv/bin/\U0001d70bthon"
                    )
                    if not interpreter_alias or resolved != (
                        release / ".venv/bin/python"
                    ).resolve(strict=True):
                        raise PreparedRuntimeError(
                            "prepared dependency link leaves its release"
                        )
                    # The caller separately proves the interpreter is the pinned
                    # formula. Bind its external bytes too, including patches
                    # which report the same version string.
                    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    try:
                        interpreter = os.fstat(fd)
                        if (
                            not stat.S_ISREG(interpreter.st_mode)
                            or interpreter.st_mode & 0o002
                        ):
                            raise PreparedRuntimeError("prepared interpreter is unsafe")
                        interpreters[resolved] = _coordinate(interpreter)
                        content = hashlib.sha256()
                        while chunk := os.read(fd, 1024 * 1024):
                            size += len(chunk)
                            if size > _MAX_BYTES:
                                raise PreparedRuntimeError(
                                    "prepared runtime exceeds size limit"
                                )
                            content.update(chunk)
                        if _coordinate(os.fstat(fd)) != _coordinate(interpreter):
                            raise PreparedRuntimeError(
                                "prepared interpreter changed during read"
                            )
                        # The pinned system tool can be administered by its
                        # owning group. Bind its permissions and ownership as
                        # well as bytes instead of imposing release-local modes.
                        record(
                            [
                                path,
                                "interpreter",
                                str(resolved),
                                stat.S_IMODE(interpreter.st_mode),
                                interpreter.st_uid,
                                interpreter.st_gid,
                                content.hexdigest(),
                            ]
                        )
                    finally:
                        os.close(fd)
                record([path, "link", target])
            elif stat.S_ISDIR(before.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                try:
                    if _coordinate(os.fstat(child)) != _coordinate(before):
                        raise PreparedRuntimeError(
                            "prepared runtime changed during read"
                        )
                    scan(child, path)
                finally:
                    os.close(child)
            elif stat.S_ISREG(before.st_mode) and not before.st_mode & 0o022:
                child = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
                )
                try:
                    if _coordinate(os.fstat(child)) != _coordinate(before):
                        raise PreparedRuntimeError(
                            "prepared runtime changed during read"
                        )
                    content = hashlib.sha256()
                    read_size = 0
                    while chunk := os.read(child, 1024 * 1024):
                        read_size += len(chunk)
                        size += len(chunk)
                        if size > _MAX_BYTES:
                            raise PreparedRuntimeError(
                                "prepared runtime exceeds size limit"
                            )
                        content.update(chunk)
                    if read_size != before.st_size or _coordinate(
                        os.fstat(child)
                    ) != _coordinate(before):
                        raise PreparedRuntimeError(
                            "prepared runtime changed during read"
                        )
                    record(
                        [
                            path,
                            "file",
                            stat.S_IMODE(before.st_mode),
                            read_size,
                            content.hexdigest(),
                        ]
                    )
                finally:
                    os.close(child)
            else:
                raise PreparedRuntimeError("prepared runtime entry is unsafe")
            if _coordinate(
                os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            ) != _coordinate(before):
                raise PreparedRuntimeError("prepared runtime changed during read")
        if _coordinate(os.fstat(descriptor)) != _coordinate(initial):
            raise PreparedRuntimeError("prepared runtime changed during inventory")

    try:
        root = os.open(release, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for relative in roots:
                descriptors: list[int] = []
                parent = root
                try:
                    for part in relative.split("/"):
                        parent = os.open(
                            part,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent,
                        )
                        descriptors.append(parent)
                except FileNotFoundError:
                    record([relative, "absent"])
                else:
                    scan(parent, relative)
                    if _coordinate(
                        os.stat(relative, dir_fd=root, follow_symlinks=False)
                    ) != _coordinate(os.fstat(parent)):
                        raise PreparedRuntimeError(
                            "prepared runtime root changed during read"
                        )
                finally:
                    for descriptor in reversed(descriptors):
                        os.close(descriptor)
            for interpreter, coordinate in interpreters.items():
                if _coordinate(interpreter.lstat()) != coordinate:
                    raise PreparedRuntimeError(
                        "prepared interpreter changed during inventory"
                    )
            for relative, coordinate in inventory.items():
                if (
                    _coordinate(os.stat(relative, dir_fd=root, follow_symlinks=False))
                    != coordinate
                ):
                    raise PreparedRuntimeError(
                        "prepared runtime changed during inventory"
                    )
        finally:
            os.close(root)
    except OSError as exc:
        raise PreparedRuntimeError("prepared runtime inventory is unavailable") from exc
    return {
        "artifact_sha256": digest.hexdigest(),
        "record_count": count,
        "content_bytes": size,
        "inventory_metadata_bytes": metadata_bytes,
    }
