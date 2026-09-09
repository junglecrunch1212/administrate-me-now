"""Fail-closed source identity for installed and newly published releases."""

from __future__ import annotations

import hashlib
import os
import re
import select
import signal
import stat
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from bootstrap.contracts import atomic_write


class SourceIdentityError(RuntimeError):
    """The reviewed Git coordinate and installed source cannot be reconciled."""


class SourceProcessNotReaped(SourceIdentityError):
    """A fixed-source Git process may still be using its private staging tree."""


SOURCE_MARKER_NAME: Final = ".administrateme-source-commit"
SOURCE_MARKER_METHOD: Final = "release-marker-v1"
LEGACY_TREE_METHOD: Final = "legacy-git-tree-v1"
GENERATED_EXTRA_POLICY: Final = "generated-runtime-extras-v1"
SYSTEM_GIT_EXECUTABLE: Final = Path("/usr/bin/git")

_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_MAX_TRACKED_FILES = 100_000
_MAX_TRACKED_PATH_BYTES = 4096
_MAX_GIT_CONTROL_OUTPUT_BYTES = 1024 * 1024
_MAX_GIT_TREE_OUTPUT_BYTES = 128 * 1024 * 1024
_MAX_TRACKED_FILE_BYTES = 1024 * 1024 * 1024
_MAX_TRACKED_TREE_BYTES = 8 * 1024 * 1024 * 1024
_MATERIALIZATION_TIMEOUT_SECONDS = 120.0
_RUNTIME_ROOTS = frozenset(
    {
        ".venv",
        "openclaw/administrateme/node_modules",
        "openclaw/administrateme/dist",
    }
)
_RUNTIME_PARENT_DIRS = frozenset(
    parent.as_posix()
    for root in _RUNTIME_ROOTS
    for parent in PurePosixPath(root).parents
    if parent != PurePosixPath(".")
)


@dataclass(frozen=True)
class GitTreeEntry:
    mode: str
    object_id: str
    path: str

    @property
    def executable(self) -> bool:
        return self.mode == "100755"


@dataclass(frozen=True)
class FileCoordinate:
    device: int
    inode: int
    mode: int
    link_count: int
    size: int
    modified_ns: int
    changed_ns: int


def _coordinate(metadata: os.stat_result) -> FileCoordinate:
    return FileCoordinate(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        link_count=metadata.st_nlink,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def sanitized_git_environment() -> dict[str, str]:
    # ``git -C`` is not authoritative while ambient Git repository-selection
    # variables remain set.  Remove every Git-specific input, then add back
    # only the two fixed safety controls used by this verifier.
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["LC_ALL"] = "C"
    return environment


def _run_git(
    repository: Path,
    arguments: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    command = [
        str(SYSTEM_GIT_EXECUTABLE),
        "--no-replace-objects",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(repository),
        *arguments,
    ]
    process: subprocess.Popen[bytes] | None = None
    failed = False
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=sanitized_git_environment(),
            start_new_session=True,
        )
        assert process.stdout is not None
        output = bytearray()
        deadline = time.monotonic() + 120
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceIdentityError("reviewed Git source identity timed out")
            readable, _writable, _exceptional = select.select(
                [process.stdout], [], [], min(0.1, remaining)
            )
            if not readable:
                if process.poll() is not None and not _process_group_exists(
                    process.pid
                ):
                    break
                continue
            chunk = os.read(process.stdout.fileno(), 64 * 1024)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > _MAX_GIT_CONTROL_OUTPUT_BYTES:
                raise SourceIdentityError(
                    "reviewed Git source identity output exceeds its boundary"
                )
        return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if _process_group_exists(process.pid):
            raise SourceIdentityError(
                "reviewed Git source identity left a background process"
            )
        completed = subprocess.CompletedProcess(
            command,
            return_code,
            bytes(output),
            b"",
        )
    except (OSError, subprocess.SubprocessError, SourceIdentityError) as exc:
        failed = True
        raise SourceIdentityError(
            "reviewed Git source identity is unavailable"
        ) from exc
    finally:
        cleanup_error: BaseException | None = None
        if process is not None:
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
            if process.poll() is None or _process_group_exists(process.pid):
                try:
                    _reap_materializer_group(process)
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
        if isinstance(cleanup_error, SourceProcessNotReaped):
            raise cleanup_error
        if cleanup_error is not None and not failed and sys.exc_info()[0] is None:
            raise cleanup_error
    if check and completed.returncode:
        raise SourceIdentityError("reviewed Git source identity is unavailable")
    return completed


def _run_git_tree_records(repository: Path, commit: str) -> tuple[bytes, ...]:
    command = [
        str(SYSTEM_GIT_EXECUTABLE),
        "--no-replace-objects",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(repository),
        "ls-tree",
        "-rz",
        "--full-tree",
        "-r",
        commit,
    ]
    process: subprocess.Popen[bytes] | None = None
    failed = False
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=sanitized_git_environment(),
            start_new_session=True,
        )
        assert process.stdout is not None
        deadline = time.monotonic() + 120
        buffer = bytearray()
        records: list[bytes] = []
        total_output = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceIdentityError("reviewed Git tree read timed out")
            readable, _writable, _exceptional = select.select(
                [process.stdout], [], [], min(0.1, remaining)
            )
            if not readable:
                if process.poll() is not None and not _process_group_exists(
                    process.pid
                ):
                    break
                continue
            chunk = os.read(process.stdout.fileno(), 64 * 1024)
            if not chunk:
                break
            total_output += len(chunk)
            if total_output > _MAX_GIT_TREE_OUTPUT_BYTES:
                raise SourceIdentityError(
                    "reviewed Git tree output exceeds its boundary"
                )
            buffer.extend(chunk)
            while True:
                boundary = buffer.find(0)
                if boundary < 0:
                    if len(buffer) > _MAX_TRACKED_PATH_BYTES + 256:
                        raise SourceIdentityError(
                            "reviewed Git tree path exceeds its boundary"
                        )
                    break
                record = bytes(buffer[:boundary])
                del buffer[: boundary + 1]
                if not record:
                    raise SourceIdentityError("reviewed Git tree output is invalid")
                records.append(record)
                if len(records) > _MAX_TRACKED_FILES:
                    raise SourceIdentityError(
                        "reviewed Git tree exceeds the supported boundary"
                    )
        if buffer:
            raise SourceIdentityError("reviewed Git tree output is incomplete")
        return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if return_code or _process_group_exists(process.pid):
            raise SourceIdentityError("reviewed Git tree is unavailable")
        return tuple(records)
    except (OSError, subprocess.SubprocessError, SourceIdentityError) as exc:
        failed = True
        if isinstance(exc, SourceIdentityError):
            raise
        raise SourceIdentityError("reviewed Git tree is unavailable") from exc
    finally:
        cleanup_error: BaseException | None = None
        if process is not None:
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
            if process.poll() is None or _process_group_exists(process.pid):
                try:
                    _reap_materializer_group(process)
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
        if isinstance(cleanup_error, SourceProcessNotReaped):
            raise cleanup_error
        if cleanup_error is not None and not failed and sys.exc_info()[0] is None:
            raise cleanup_error


def reviewed_candidate_commit(repository: Path) -> str:
    """Return one clean reviewed commit through the bounded Git boundary."""

    if not repository.is_dir() or repository.is_symlink():
        raise SourceIdentityError("reviewed Git checkout is missing or unsafe")
    inside = _run_git(
        repository,
        ["rev-parse", "--is-inside-work-tree"],
    ).stdout.strip()
    commit = _run_git(repository, ["rev-parse", "HEAD"]).stdout.strip()
    status = _run_git(
        repository,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    ).stdout
    try:
        commit_text = commit.decode("ascii", "strict")
    except UnicodeDecodeError as exc:
        raise SourceIdentityError(
            "reviewed Git checkout has an invalid commit"
        ) from exc
    if inside != b"true" or _HEX_40.fullmatch(commit_text) is None:
        raise SourceIdentityError("reviewed Git checkout has an invalid commit")
    if status:
        raise SourceIdentityError("reviewed Git checkout is dirty")
    return commit_text


def _validate_relative_path(encoded: bytes) -> str:
    if len(encoded) > _MAX_TRACKED_PATH_BYTES:
        raise SourceIdentityError("reviewed Git tree contains an unsafe path")
    try:
        path = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceIdentityError("reviewed Git tree contains an unsafe path") from exc
    pure = PurePosixPath(path)
    if (
        not path
        or path.startswith("/")
        or "\n" in path
        or "\r" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != path
        or path == SOURCE_MARKER_NAME
    ):
        raise SourceIdentityError("reviewed Git tree contains an unsafe path")
    return path


def load_git_tree(
    repository: Path,
    commit: str,
    *,
    retained_by_commit: str | None = None,
) -> tuple[tuple[GitTreeEntry, ...], str]:
    """Load one fixed commit tree without honoring Git replacement objects."""

    if _HEX_40.fullmatch(commit) is None:
        raise SourceIdentityError("reviewed Git commit is invalid")
    repository_input = Path(os.path.abspath(repository))
    if repository_input.is_symlink():
        raise SourceIdentityError("reviewed Git repository is missing or unsafe")
    repository = repository_input.resolve()
    if not repository.is_dir():
        raise SourceIdentityError("reviewed Git repository is missing or unsafe")
    object_type = _run_git(repository, ["cat-file", "-t", commit]).stdout.strip()
    if object_type != b"commit":
        raise SourceIdentityError("reviewed Git object is not a commit")
    if retained_by_commit is not None:
        if _HEX_40.fullmatch(retained_by_commit) is None:
            raise SourceIdentityError("reviewed candidate Git commit is invalid")
        retained_type = _run_git(
            repository, ["cat-file", "-t", retained_by_commit]
        ).stdout.strip()
        if retained_type != b"commit":
            raise SourceIdentityError("reviewed candidate Git object is not a commit")
        ancestor = _run_git(
            repository,
            ["merge-base", "--is-ancestor", commit, retained_by_commit],
            check=False,
        )
        if ancestor.returncode != 0:
            raise SourceIdentityError(
                "active predecessor commit is not retained by the reviewed candidate"
            )
    object_format = (
        _run_git(repository, ["rev-parse", "--show-object-format"])
        .stdout.decode("ascii", errors="strict")
        .strip()
    )
    if object_format not in {"sha1", "sha256"}:
        raise SourceIdentityError("reviewed Git object format is unsupported")
    records = _run_git_tree_records(repository, commit)
    entries: list[GitTreeEntry] = []
    normalized_paths: set[str] = set()
    for record in records:
        try:
            header, encoded_path = record.split(b"\t", 1)
            encoded_mode, object_type, encoded_object_id = header.split(b" ", 2)
            mode = encoded_mode.decode("ascii")
            git_type = object_type.decode("ascii")
            object_id = encoded_object_id.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise SourceIdentityError("reviewed Git tree output is invalid") from exc
        path = _validate_relative_path(encoded_path)
        normalized = unicodedata.normalize("NFC", path).casefold()
        if normalized in normalized_paths:
            raise SourceIdentityError("reviewed Git tree contains colliding paths")
        normalized_paths.add(normalized)
        if git_type != "blob" or mode not in {"100644", "100755"}:
            raise SourceIdentityError(
                "reviewed Git tree contains an unsupported object or mode"
            )
        object_pattern = _HEX_40 if object_format == "sha1" else _HEX_64
        if object_pattern.fullmatch(object_id) is None:
            raise SourceIdentityError("reviewed Git tree object identity is invalid")
        if any(path == root or path.startswith(f"{root}/") for root in _RUNTIME_ROOTS):
            raise SourceIdentityError(
                "reviewed Git source collides with a generated-runtime boundary"
            )
        if "__pycache__" in PurePosixPath(path).parts:
            raise SourceIdentityError(
                "reviewed Git source collides with a generated-runtime boundary"
            )
        entries.append(GitTreeEntry(mode=mode, object_id=object_id, path=path))
    if not entries:
        raise SourceIdentityError("reviewed Git tree exceeds the supported boundary")
    return tuple(entries), object_format


def _safe_directory_metadata(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and not metadata.st_mode & 0o022
    )


def _safe_regular_metadata(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and metadata.st_nlink == 1
        and not metadata.st_mode & 0o022
    )


def _validate_runtime_root(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise SourceIdentityError(
            "release inventory changed during validation"
        ) from exc
    if path.is_symlink() or not _safe_directory_metadata(metadata):
        raise SourceIdentityError("generated runtime root is unsafe")


def _tracked_directories(entries: tuple[GitTreeEntry, ...]) -> set[str]:
    directories: set[str] = set()
    for entry in entries:
        parent = PurePosixPath(entry.path).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _allowed_bytecode(path: str, tracked_files: set[str]) -> bool:
    pure = PurePosixPath(path)
    if pure.parent.name != "__pycache__" or not pure.name.endswith(".pyc"):
        return False
    source_parent = pure.parent.parent
    prefix = pure.name.split(".", 1)[0]
    if not prefix:
        return False
    source = source_parent / f"{prefix}.py"
    return source.as_posix() in tracked_files


def _scan_release_inventory(
    release: Path,
    entries: tuple[GitTreeEntry, ...],
    *,
    marker_expected: bool,
) -> tuple[int, dict[str, FileCoordinate]]:
    tracked_files = {entry.path for entry in entries}
    tracked_directories = _tracked_directories(entries)
    allowed_extra_count = 0
    marker_seen = False
    coordinates: dict[str, FileCoordinate] = {}

    def scan(directory: Path, relative: PurePosixPath) -> None:
        nonlocal allowed_extra_count, marker_seen
        try:
            children = tuple(os.scandir(directory))
        except OSError as exc:
            raise SourceIdentityError(
                "installed release inventory is unreadable"
            ) from exc
        for child in children:
            child_relative = (
                PurePosixPath(child.name)
                if relative == PurePosixPath(".")
                else relative / child.name
            )
            child_name = child_relative.as_posix()
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise SourceIdentityError(
                    "installed release inventory changed during validation"
                ) from exc
            coordinates[child_name] = _coordinate(metadata)
            if child_name == SOURCE_MARKER_NAME:
                marker_seen = True
                if not marker_expected:
                    raise SourceIdentityError(
                        "installed release source marker appeared during validation"
                    )
                continue
            if child_name in _RUNTIME_ROOTS:
                _validate_runtime_root(Path(child.path))
                allowed_extra_count += 1
                continue
            if child_name in tracked_files:
                if child.is_symlink() or not _safe_regular_metadata(metadata):
                    raise SourceIdentityError("installed tracked source is unsafe")
                continue
            if child_name in tracked_directories:
                if child.is_symlink() or not _safe_directory_metadata(metadata):
                    raise SourceIdentityError("installed source directory is unsafe")
                scan(Path(child.path), child_relative)
                continue
            if child_name in _RUNTIME_PARENT_DIRS:
                if child.is_symlink() or not _safe_directory_metadata(metadata):
                    raise SourceIdentityError("generated runtime parent is unsafe")
                scan(Path(child.path), child_relative)
                continue
            if child.name == "__pycache__":
                if child.is_symlink() or not _safe_directory_metadata(metadata):
                    raise SourceIdentityError("generated bytecode directory is unsafe")
                try:
                    bytecode_entries = tuple(os.scandir(child.path))
                except OSError as exc:
                    raise SourceIdentityError(
                        "generated bytecode inventory is unreadable"
                    ) from exc
                for bytecode in bytecode_entries:
                    bytecode_relative = child_relative / bytecode.name
                    try:
                        bytecode_metadata = bytecode.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise SourceIdentityError(
                            "generated bytecode inventory changed during validation"
                        ) from exc
                    coordinates[bytecode_relative.as_posix()] = _coordinate(
                        bytecode_metadata
                    )
                    if (
                        bytecode.is_symlink()
                        or not _safe_regular_metadata(bytecode_metadata)
                        or not _allowed_bytecode(
                            bytecode_relative.as_posix(), tracked_files
                        )
                    ):
                        raise SourceIdentityError(
                            "generated bytecode residue is unsafe"
                        )
                    allowed_extra_count += 1
                allowed_extra_count += 1
                continue
            raise SourceIdentityError(
                "installed release contains unexpected source-adjacent residue"
            )

    scan(release, PurePosixPath("."))
    if marker_seen != marker_expected:
        raise SourceIdentityError(
            "installed release source marker changed during validation"
        )
    return allowed_extra_count, coordinates


def _open_tracked_file(
    release_descriptor: int,
    entry: GitTreeEntry,
) -> tuple[int, list[int]]:
    descriptors: list[int] = []
    current_descriptor = os.dup(release_descriptor)
    descriptors.append(current_descriptor)
    parts = PurePosixPath(entry.path).parts
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        for part in parts[:-1]:
            next_descriptor = os.open(part, directory_flags, dir_fd=current_descriptor)
            descriptors.append(next_descriptor)
            current_descriptor = next_descriptor
            if not _safe_directory_metadata(os.fstat(current_descriptor)):
                raise SourceIdentityError("installed source directory is unsafe")
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=current_descriptor,
        )
        descriptors.append(file_descriptor)
        return file_descriptor, descriptors
    except BaseException:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _validate_tracked_mode(metadata: os.stat_result, entry: GitTreeEntry) -> None:
    if not _safe_regular_metadata(metadata):
        raise SourceIdentityError("installed tracked source is unsafe")
    if entry.executable:
        if not metadata.st_mode & stat.S_IXUSR:
            raise SourceIdentityError("installed tracked source mode differs from Git")
    elif metadata.st_mode & 0o111:
        raise SourceIdentityError("installed tracked source mode differs from Git")


def _read_tracked_file(
    release_descriptor: int,
    entry: GitTreeEntry,
    object_format: str,
) -> tuple[str, int, FileCoordinate]:
    descriptors: list[int] = []
    try:
        file_descriptor, descriptors = _open_tracked_file(release_descriptor, entry)
        before = os.fstat(file_descriptor)
        _validate_tracked_mode(before, entry)
        if before.st_size > _MAX_TRACKED_FILE_BYTES:
            raise SourceIdentityError("installed tracked source exceeds size boundary")
        object_digest = hashlib.new(object_format)
        object_digest.update(f"blob {before.st_size}\0".encode("ascii"))
        content_digest = hashlib.sha256()
        read_size = 0
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            read_size += len(chunk)
            object_digest.update(chunk)
            content_digest.update(chunk)
        after = os.fstat(file_descriptor)
        stable_coordinates = _coordinate(before) == _coordinate(after)
        if not stable_coordinates or read_size != before.st_size:
            raise SourceIdentityError(
                "installed tracked source changed during validation"
            )
        if object_digest.hexdigest() != entry.object_id:
            raise SourceIdentityError("installed tracked source differs from Git")
        return content_digest.hexdigest(), read_size, _coordinate(after)
    except OSError as exc:
        raise SourceIdentityError(
            "installed tracked source is missing or unsafe"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _tracked_file_coordinate(
    release_descriptor: int,
    entry: GitTreeEntry,
) -> FileCoordinate:
    descriptors: list[int] = []
    try:
        file_descriptor, descriptors = _open_tracked_file(release_descriptor, entry)
        metadata = os.fstat(file_descriptor)
        _validate_tracked_mode(metadata, entry)
        return _coordinate(metadata)
    except OSError as exc:
        raise SourceIdentityError(
            "installed tracked source changed during validation"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def verify_tracked_release_tree(
    release: Path,
    *,
    repository: Path,
    commit: str,
    retained_by_commit: str | None = None,
    marker_expected: bool = False,
) -> dict[str, object]:
    """Prove installed tracked bytes and modes equal one retained Git commit."""

    entries, object_format = load_git_tree(
        repository,
        commit,
        retained_by_commit=retained_by_commit,
    )
    release = Path(os.path.abspath(release))
    try:
        release_metadata = release.lstat()
    except FileNotFoundError as exc:
        raise SourceIdentityError("installed release is missing") from exc
    if release.is_symlink() or not _safe_directory_metadata(release_metadata):
        raise SourceIdentityError("installed release root is unsafe")
    initial_extra_count, initial_inventory = _scan_release_inventory(
        release, entries, marker_expected=marker_expected
    )
    release_descriptor = os.open(
        release,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    canonical = hashlib.sha256()
    total_bytes = 0
    tracked_coordinates: dict[str, FileCoordinate] = {}
    try:
        root_coordinate = _coordinate(os.fstat(release_descriptor))
        if root_coordinate != _coordinate(release_metadata):
            raise SourceIdentityError(
                "installed release root changed during validation"
            )
        for entry in entries:
            content_sha256, size, coordinate = _read_tracked_file(
                release_descriptor, entry, object_format
            )
            tracked_coordinates[entry.path] = coordinate
            total_bytes += size
            if total_bytes > _MAX_TRACKED_TREE_BYTES:
                raise SourceIdentityError(
                    "installed tracked tree exceeds size boundary"
                )
            canonical.update(entry.mode.encode("ascii"))
            canonical.update(b"\x00")
            canonical.update(entry.path.encode("utf-8"))
            canonical.update(b"\x00")
            canonical.update(content_sha256.encode("ascii"))
            canonical.update(b"\n")
        if _coordinate(release.lstat()) != root_coordinate:
            raise SourceIdentityError(
                "installed release root changed during validation"
            )
        verification_descriptor = os.open(
            release,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            if _coordinate(os.fstat(verification_descriptor)) != root_coordinate:
                raise SourceIdentityError(
                    "installed release root changed during validation"
                )
            for entry in entries:
                if (
                    _tracked_file_coordinate(verification_descriptor, entry)
                    != tracked_coordinates[entry.path]
                ):
                    raise SourceIdentityError(
                        "installed tracked source changed during validation"
                    )
        finally:
            os.close(verification_descriptor)
    except OSError as exc:
        raise SourceIdentityError(
            "installed release changed during validation"
        ) from exc
    finally:
        os.close(release_descriptor)
    allowed_extra_count, final_inventory = _scan_release_inventory(
        release, entries, marker_expected=marker_expected
    )
    if (
        allowed_extra_count != initial_extra_count
        or final_inventory != initial_inventory
    ):
        raise SourceIdentityError(
            "installed release inventory changed during validation"
        )
    return {
        "tracked_source_tree_matches": True,
        "tracked_source_manifest_sha256": canonical.hexdigest(),
        "tracked_source_file_count": len(entries),
        "tracked_source_byte_count": total_bytes,
        "generated_extra_policy": GENERATED_EXTRA_POLICY,
        "generated_extra_policy_matches": True,
        "generated_extra_count": allowed_extra_count,
    }


def _read_source_marker(release: Path, commit: str) -> bool:
    release = Path(os.path.abspath(release))
    try:
        release_metadata = release.lstat()
        release_descriptor = os.open(
            release,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise SourceIdentityError("installed release root is unsafe") from exc
    root_coordinate = _coordinate(os.fstat(release_descriptor))
    if not _safe_directory_metadata(release_metadata) or root_coordinate != _coordinate(
        release_metadata
    ):
        os.close(release_descriptor)
        raise SourceIdentityError("installed release root is unsafe")
    try:
        try:
            descriptor = os.open(
                SOURCE_MARKER_NAME,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=release_descriptor,
            )
        except FileNotFoundError:
            marker_matches = False
        except OSError as exc:
            raise SourceIdentityError(
                "installed release source marker is unsafe"
            ) from exc
        else:
            try:
                metadata = os.fstat(descriptor)
                if (
                    not _safe_regular_metadata(metadata)
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or metadata.st_size <= 0
                    or metadata.st_size > 128
                ):
                    raise SourceIdentityError(
                        "installed release source marker is unsafe"
                    )
                payload = os.read(descriptor, 129)
                after = os.fstat(descriptor)
                if len(payload) != metadata.st_size or _coordinate(
                    metadata
                ) != _coordinate(after):
                    raise SourceIdentityError(
                        "installed release source marker changed during validation"
                    )
                if payload not in {
                    commit.encode("ascii"),
                    f"{commit}\n".encode("ascii"),
                }:
                    raise SourceIdentityError(
                        "installed release source marker does not match"
                    )
                marker_matches = True
            finally:
                os.close(descriptor)
        if (
            _coordinate(release.lstat()) != root_coordinate
            or _coordinate(os.fstat(release_descriptor)) != root_coordinate
        ):
            raise SourceIdentityError(
                "installed release root changed during validation"
            )
    except OSError as exc:
        raise SourceIdentityError(
            "installed release root changed during validation"
        ) from exc
    finally:
        os.close(release_descriptor)
    return marker_matches


def verify_release_source_identity(
    release: Path,
    *,
    repository: Path,
    commit: str,
    allow_legacy_tree: bool,
    require_tracked_tree: bool = False,
    retained_by_commit: str | None = None,
) -> dict[str, object]:
    """Validate a release marker, or one exact markerless legacy source tree."""

    marker_matches = _read_source_marker(release, commit)
    if marker_matches and not require_tracked_tree:
        return {
            "source_identity_method": SOURCE_MARKER_METHOD,
            "source_marker_matches": True,
            "tracked_source_tree_matches": "not_evaluated",
            "generated_extra_policy_matches": "not_evaluated",
        }
    if not marker_matches and not allow_legacy_tree:
        raise SourceIdentityError("installed release source marker is missing")
    if not marker_matches and retained_by_commit is None:
        raise SourceIdentityError(
            "markerless release proof requires the captured candidate commit"
        )
    tree = verify_tracked_release_tree(
        release,
        repository=repository,
        commit=commit,
        retained_by_commit=retained_by_commit,
        marker_expected=marker_matches,
    )
    if _read_source_marker(release, commit) != marker_matches:
        raise SourceIdentityError(
            "installed release source marker changed during validation"
        )
    return {
        "source_identity_method": (
            SOURCE_MARKER_METHOD if marker_matches else LEGACY_TREE_METHOD
        ),
        "source_marker_matches": marker_matches,
        **tree,
    }


def write_source_marker(release: Path, commit: str) -> None:
    if _HEX_40.fullmatch(commit) is None:
        raise SourceIdentityError("reviewed Git commit is invalid")
    atomic_write(
        release / SOURCE_MARKER_NAME,
        f"{commit}\n".encode("ascii"),
        mode=0o600,
    )


def _read_with_deadline(
    descriptor: int,
    size: int,
    *,
    deadline: float,
) -> bytes:
    import select

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceIdentityError("reviewed Git blob materialization timed out")
        ready, _, _ = select.select([descriptor], [], [], remaining)
        if not ready:
            raise SourceIdentityError("reviewed Git blob materialization timed out")
        chunk = os.read(descriptor, size)
        if not chunk:
            raise SourceIdentityError("reviewed Git blob stream is incomplete")
        return chunk


def _read_batch_header(
    descriptor: int,
    *,
    expected_object_id: str,
    deadline: float,
) -> int:
    header = bytearray()
    while len(header) <= 256:
        byte = _read_with_deadline(descriptor, 1, deadline=deadline)
        if byte == b"\n":
            break
        header.extend(byte)
    else:
        raise SourceIdentityError("reviewed Git blob stream is invalid")
    try:
        returned_object_id, object_type, encoded_size = bytes(header).split(b" ", 2)
        size = int(encoded_size)
    except (TypeError, ValueError) as exc:
        raise SourceIdentityError("reviewed Git blob stream is invalid") from exc
    if (
        returned_object_id.decode("ascii", errors="strict") != expected_object_id
        or object_type != b"blob"
        or size < 0
        or size > _MAX_TRACKED_FILE_BYTES
    ):
        raise SourceIdentityError("reviewed Git blob stream is invalid")
    return size


def _stream_batch_blob(
    descriptor: int,
    target_descriptor: int,
    *,
    expected_object_id: str,
    object_format: str,
    size: int,
    deadline: float,
) -> None:
    digest = hashlib.new(object_format)
    digest.update(f"blob {size}\0".encode("ascii"))
    remaining = size
    while remaining:
        chunk = _read_with_deadline(
            descriptor,
            min(1024 * 1024, remaining),
            deadline=deadline,
        )
        digest.update(chunk)
        view = memoryview(chunk)
        while view:
            written = os.write(target_descriptor, view)
            if written <= 0:
                raise SourceIdentityError("release source materialization failed")
            view = view[written:]
        remaining -= len(chunk)
    if _read_with_deadline(descriptor, 1, deadline=deadline) != b"\n":
        raise SourceIdentityError("reviewed Git blob stream is incomplete")
    if digest.hexdigest() != expected_object_id:
        raise SourceIdentityError("reviewed Git blob content identity is invalid")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _open_private_directory_at(parent_descriptor: int, name: str) -> int:
    try:
        descriptor = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor)
    except OSError as exc:
        raise SourceIdentityError("generated runtime directory is unsafe") from exc
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        os.close(descriptor)
        raise SourceIdentityError("generated runtime directory is unsafe")
    return descriptor


def _open_or_create_directory_chain(
    root_descriptor: int,
    parts: tuple[str, ...],
    *,
    create: bool = True,
) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
            next_descriptor = _open_private_directory_at(descriptor, part)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _process_group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap_materializer_group(process: subprocess.Popen[bytes]) -> None:
    group_id = process.pid
    if _process_group_exists(group_id):
        try:
            os.killpg(group_id, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin can retain a zombie-only group briefly after TERM and
            # report EPERM. Only the bounded absence proof below may clear it.
            pass
        except OSError as exc:
            raise SourceProcessNotReaped(
                "reviewed Git blob materializer could not be signaled"
            ) from exc
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    if _process_group_exists(group_id):
        try:
            os.killpg(group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # EPERM is never absence: reap the leader and require ESRCH from
            # the existing final group probe before cleanup can succeed.
            pass
        except OSError as exc:
            raise SourceProcessNotReaped(
                "reviewed Git blob materializer could not be killed"
            ) from exc
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise SourceProcessNotReaped(
            "reviewed Git blob materializer could not be reaped"
        ) from exc
    deadline = time.monotonic() + 5
    while _process_group_exists(group_id):
        if time.monotonic() >= deadline:
            raise SourceProcessNotReaped(
                "reviewed Git blob materializer descendants could not be reaped"
            )
        time.sleep(0.01)


def _materialize_git_tree_into(
    repository: Path,
    entries: tuple[GitTreeEntry, ...],
    object_format: str,
    destination_descriptor: int,
) -> None:
    process: subprocess.Popen[bytes] | None = None
    failed = False
    try:
        process = subprocess.Popen(
            [
                str(SYSTEM_GIT_EXECUTABLE),
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(repository.resolve()),
                "cat-file",
                "--batch",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=sanitized_git_environment(),
            start_new_session=True,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        total_bytes = 0
        deadline = time.monotonic() + _MATERIALIZATION_TIMEOUT_SECONDS
        for entry in entries:
            relative = PurePosixPath(entry.path)
            parent_descriptor = _open_or_create_directory_chain(
                destination_descriptor,
                relative.parts[:-1],
            )
            try:
                process.stdin.write(f"{entry.object_id}\n".encode("ascii"))
                process.stdin.flush()
                size = _read_batch_header(
                    process.stdout.fileno(),
                    expected_object_id=entry.object_id,
                    deadline=deadline,
                )
                total_bytes += size
                if total_bytes > _MAX_TRACKED_TREE_BYTES:
                    raise SourceIdentityError(
                        "reviewed Git tree exceeds the supported size boundary"
                    )
                mode = 0o700 if entry.executable else 0o600
                descriptor = os.open(
                    relative.parts[-1],
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    mode,
                    dir_fd=parent_descriptor,
                )
                try:
                    _stream_batch_blob(
                        process.stdout.fileno(),
                        descriptor,
                        expected_object_id=entry.object_id,
                        object_format=object_format,
                        size=size,
                        deadline=deadline,
                    )
                    os.fchmod(descriptor, mode)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        process.stdin.close()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceIdentityError("reviewed Git blob materialization timed out")
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise SourceIdentityError(
                "reviewed Git blob materialization timed out"
            ) from exc
        if return_code:
            raise SourceIdentityError("reviewed Git blob materialization failed")
        os.fsync(destination_descriptor)
    except BaseException:
        failed = True
        raise
    finally:
        cleanup_error: BaseException | None = None
        if process is not None:
            for pipe in (process.stdin, process.stdout):
                if pipe is not None and not pipe.closed:
                    try:
                        pipe.close()
                    except BaseException as exc:
                        cleanup_error = cleanup_error or exc
            if failed or process.poll() is None or _process_group_exists(process.pid):
                try:
                    _reap_materializer_group(process)
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
        if isinstance(cleanup_error, SourceProcessNotReaped):
            raise cleanup_error
        if cleanup_error is not None and not failed and sys.exc_info()[0] is None:
            raise cleanup_error


def materialize_git_tree(
    repository: Path,
    commit: str,
    destination: Path,
    *,
    parent_descriptor: int | None = None,
) -> tuple[int, int]:
    """Create one private source tree from fixed Git blobs without path escape."""

    entries, object_format = load_git_tree(
        repository, commit, retained_by_commit=commit
    )
    close_parent = False
    if parent_descriptor is None:
        parent = Path(os.path.abspath(destination.parent))
        if parent.is_symlink():
            raise SourceIdentityError("release staging parent is unsafe")
        try:
            parent_descriptor = os.open(parent, _DIRECTORY_OPEN_FLAGS)
        except OSError as exc:
            raise SourceIdentityError("release staging parent is unsafe") from exc
        close_parent = True
    if destination.name in {"", ".", ".."} or "/" in destination.name:
        if close_parent:
            os.close(parent_descriptor)
        raise SourceIdentityError("release staging destination is unsafe")
    destination_descriptor: int | None = None
    destination_coordinate: tuple[int, int] | None = None
    try:
        try:
            os.mkdir(destination.name, 0o700, dir_fd=parent_descriptor)
        except FileExistsError as exc:
            raise SourceIdentityError(
                "release staging destination already exists"
            ) from exc
        destination_descriptor = _open_private_directory_at(
            parent_descriptor, destination.name
        )
        _materialize_git_tree_into(
            repository,
            entries,
            object_format,
            destination_descriptor,
        )
        os.fsync(destination_descriptor)
        opened = os.fstat(destination_descriptor)
        entry = os.stat(
            destination.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        destination_coordinate = (opened.st_dev, opened.st_ino)
        if destination_coordinate != (entry.st_dev, entry.st_ino):
            raise SourceIdentityError(
                "release staging destination changed during materialization"
            )
        os.fsync(parent_descriptor)
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if close_parent:
            os.close(parent_descriptor)
    assert destination_coordinate is not None
    return destination_coordinate


def write_source_marker_at(release_descriptor: int, commit: str) -> None:
    """Publish the exact source marker through a retained release directory fd."""

    if _HEX_40.fullmatch(commit) is None:
        raise SourceIdentityError("reviewed Git commit is invalid")
    temporary = f".{SOURCE_MARKER_NAME}.{os.getpid()}.{time.time_ns()}"
    descriptor: int | None = None
    linked = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=release_descriptor,
        )
        payload = f"{commit}\n".encode("ascii")
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise SourceIdentityError("release source marker write was incomplete")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(
            temporary,
            SOURCE_MARKER_NAME,
            src_dir_fd=release_descriptor,
            dst_dir_fd=release_descriptor,
            follow_symlinks=False,
        )
        linked = True
        os.unlink(temporary, dir_fd=release_descriptor)
        os.fsync(release_descriptor)
    except OSError as exc:
        raise SourceIdentityError(
            "release source marker could not be published"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not linked:
            try:
                os.unlink(temporary, dir_fd=release_descriptor)
            except FileNotFoundError:
                pass


def _remove_directory_tree_at(parent_descriptor: int, name: str) -> None:
    metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    expected_coordinate = (metadata.st_dev, metadata.st_ino)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o022
    ):
        raise SourceIdentityError("generated runtime root is unsafe")
    descriptor = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor)
    try:
        opened = os.fstat(descriptor)
        public = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != expected_coordinate or (
            public.st_dev,
            public.st_ino,
        ) != expected_coordinate:
            raise SourceIdentityError("generated runtime root changed")
        for child in os.listdir(descriptor):
            child_metadata = os.stat(
                child,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISDIR(child_metadata.st_mode):
                _remove_directory_tree_at(descriptor, child)
            else:
                os.unlink(child, dir_fd=descriptor)
        os.fsync(descriptor)
        final_opened = os.fstat(descriptor)
        final_public = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (final_opened.st_dev, final_opened.st_ino) != expected_coordinate or (
            final_public.st_dev,
            final_public.st_ino,
        ) != expected_coordinate:
            raise SourceIdentityError("generated runtime root changed")
        os.rmdir(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    finally:
        os.close(descriptor)


def remove_generated_runtime_artifacts(
    release: Path,
    *,
    repository: Path,
    commit: str,
    expected_coordinate: tuple[int, int],
) -> None:
    """Delete only policy-admitted generated roots before an owned retry."""

    entries, _object_format = load_git_tree(
        repository, commit, retained_by_commit=commit
    )
    release = Path(os.path.abspath(release))
    try:
        release_metadata = release.lstat()
        release_descriptor = os.open(release, _DIRECTORY_OPEN_FLAGS)
    except OSError as exc:
        raise SourceIdentityError("candidate release root is unsafe") from exc
    root_metadata = os.fstat(release_descriptor)
    root_coordinate = (
        root_metadata.st_dev,
        root_metadata.st_ino,
        root_metadata.st_mode,
        root_metadata.st_uid,
    )
    if (
        release.is_symlink()
        or not _safe_directory_metadata(release_metadata)
        or (root_metadata.st_dev, root_metadata.st_ino) != expected_coordinate
        or root_coordinate
        != (
            release_metadata.st_dev,
            release_metadata.st_ino,
            release_metadata.st_mode,
            release_metadata.st_uid,
        )
    ):
        os.close(release_descriptor)
        raise SourceIdentityError("candidate release root is unsafe")
    cleanup_paths = set(_RUNTIME_ROOTS)
    cleanup_paths.update(
        (PurePosixPath(entry.path).parent / "__pycache__").as_posix()
        for entry in entries
        if entry.path.endswith(".py")
    )
    try:
        for encoded in sorted(
            cleanup_paths,
            key=lambda value: (len(PurePosixPath(value).parts), value),
            reverse=True,
        ):
            relative = PurePosixPath(encoded)
            parent_descriptor = _open_or_create_directory_chain(
                release_descriptor,
                relative.parts[:-1],
                create=False,
            )
            try:
                try:
                    _remove_directory_tree_at(parent_descriptor, relative.parts[-1])
                except FileNotFoundError:
                    pass
            finally:
                os.close(parent_descriptor)
        os.fsync(release_descriptor)
        final_public_metadata = release.lstat()
        final_descriptor_metadata = os.fstat(release_descriptor)
        if (
            final_public_metadata.st_dev,
            final_public_metadata.st_ino,
            final_public_metadata.st_mode,
            final_public_metadata.st_uid,
        ) != root_coordinate or (
            final_descriptor_metadata.st_dev,
            final_descriptor_metadata.st_ino,
            final_descriptor_metadata.st_mode,
            final_descriptor_metadata.st_uid,
        ) != root_coordinate:
            raise SourceIdentityError(
                "candidate release root changed during generated cleanup"
            )
    except OSError as exc:
        raise SourceIdentityError("generated runtime cleanup failed") from exc
    finally:
        os.close(release_descriptor)


__all__ = [
    "GENERATED_EXTRA_POLICY",
    "LEGACY_TREE_METHOD",
    "SOURCE_MARKER_METHOD",
    "SOURCE_MARKER_NAME",
    "SYSTEM_GIT_EXECUTABLE",
    "SourceIdentityError",
    "SourceProcessNotReaped",
    "load_git_tree",
    "materialize_git_tree",
    "remove_generated_runtime_artifacts",
    "reviewed_candidate_commit",
    "sanitized_git_environment",
    "verify_release_source_identity",
    "verify_tracked_release_tree",
    "write_source_marker",
    "write_source_marker_at",
]
