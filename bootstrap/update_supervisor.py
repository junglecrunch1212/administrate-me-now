"""Fixed-commit launcher supervisor for the guarded updater.

This file is executed from a reviewed Git blob by ``reviewed_launcher.py`` on
behalf of the public bootstrap/update entry. It uses only the standard library
until a private fixed-commit controller tree is materialized, then holds the
canonical deployment-operation lock while that controller runs and while its
disposable source tree is removed.
"""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import secrets
import select
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any


SYSTEM_GIT = "/usr/bin/git"
MAX_POINTER_DEPTH = 64
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 200_000
MAX_ARCHIVE_PATH_BYTES = 4096
MAX_ARCHIVE_STREAM_BYTES = MAX_ARCHIVE_BYTES + (MAX_ARCHIVE_MEMBERS * 1024)
MAX_GIT_CONTROL_OUTPUT_BYTES = 1024 * 1024
CONTROLLER_TIMEOUT_SECONDS = 2 * 60 * 60
SOURCE_MARKER = ".administrateme-source-commit"
CONTROLLER_LIFECYCLE = ".controller-lifecycle.lock"


class SupervisorError(RuntimeError):
    """The fixed controller cannot be launched without weakening safety."""


def _git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "CURL_CA_BUNDLE",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
            "REQUESTS_CA_BUNDLE",
            "SSL_CERT_DIR",
            "SSL_CERT_FILE",
            "TMPDIR",
        }
    }
    environment.update(
        {
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "LC_ALL": "C",
        }
    )
    return environment


def _controller_environment(
    private_home: Path,
    *,
    test_mode: bool,
) -> dict[str, str]:
    environment = _git_environment()
    environment.update(
        {
            "HOME": str(private_home),
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        }
    )
    if test_mode:
        acceptance_python = Path(os.environ.get("ADMINME_ACCEPTANCE_PYTHON", ""))
        try:
            acceptance_python_metadata = acceptance_python.lstat()
            resolved_acceptance_python = acceptance_python.resolve(strict=True)
            resolved_acceptance_metadata = resolved_acceptance_python.stat()
            acceptance_bin_metadata = acceptance_python.parent.stat()
            acceptance_venv_metadata = acceptance_python.parent.parent.stat()
        except OSError as exc:
            raise SupervisorError(
                "fixed supervisor test interpreter is unavailable"
            ) from exc
        if (
            not acceptance_python.is_absolute()
            or not (
                stat.S_ISREG(acceptance_python_metadata.st_mode)
                or stat.S_ISLNK(acceptance_python_metadata.st_mode)
            )
            or acceptance_python_metadata.st_uid not in {0, os.getuid()}
            or (
                stat.S_ISREG(acceptance_python_metadata.st_mode)
                and acceptance_python_metadata.st_mode & 0o022
            )
            or not stat.S_ISREG(resolved_acceptance_metadata.st_mode)
            or resolved_acceptance_metadata.st_uid not in {0, os.getuid()}
            or resolved_acceptance_metadata.st_mode & 0o022
            or acceptance_bin_metadata.st_uid not in {0, os.getuid()}
            or acceptance_bin_metadata.st_mode & 0o022
            or acceptance_venv_metadata.st_uid not in {0, os.getuid()}
            or acceptance_venv_metadata.st_mode & 0o022
            or not os.access(acceptance_python, os.X_OK)
        ):
            raise SupervisorError("fixed supervisor test interpreter is unsafe")
        environment["ADMINME_TEST_MODE"] = "1"
        environment["ADMINME_ACCEPTANCE_PYTHON"] = str(acceptance_python)
    report = os.environ.get("ADMINME_SUPERVISOR_REPORT")
    if report is not None:
        environment["ADMINME_SUPERVISOR_REPORT"] = report
    return environment


def _git(repository: Path, *arguments: str) -> bytes:
    command = [
        SYSTEM_GIT,
        "--no-replace-objects",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(repository),
        *arguments,
    ]
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
            start_new_session=True,
        )
        assert process.stdout is not None
        deadline = time.monotonic() + 120
        output = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SupervisorError("reviewed candidate Git identity timed out")
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
            if len(output) > MAX_GIT_CONTROL_OUTPUT_BYTES:
                raise SupervisorError(
                    "reviewed candidate Git identity output is too large"
                )
        return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if return_code or _process_group_exists(process.pid):
            raise SupervisorError("reviewed candidate Git identity is unavailable")
        return bytes(output)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SupervisorError("reviewed candidate Git identity is unavailable") from exc
    finally:
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.poll() is None or _process_group_exists(process.pid):
                _terminate_process_group(process)


def _private_directory(path: Path, *, exact_mode: bool = True) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SupervisorError("deployment directory is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or (
            stat.S_IMODE(metadata.st_mode) != 0o700
            if exact_mode
            else metadata.st_mode & 0o022
        )
    ):
        raise SupervisorError("deployment directory is unsafe")
    return metadata


def _open_private_directory(path: Path, *, exact_mode: bool = True) -> int:
    public = _private_directory(path, exact_mode=exact_mode)
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise SupervisorError("deployment directory is unsafe") from exc
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != (public.st_dev, public.st_ino):
        os.close(descriptor)
        raise SupervisorError("deployment directory changed")
    return descriptor


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _private_directory(path)


def _open_or_create_instance_root(path: Path) -> int:
    try:
        return _open_private_directory(path)
    except SupervisorError:
        if os.path.lexists(path):
            raise
    parent = path.parent
    parent_descriptor = _open_private_directory(parent, exact_mode=False)
    try:
        descriptor, _metadata = _open_private_child(
            parent_descriptor, path.name, create=True
        )
        return descriptor
    finally:
        os.close(parent_descriptor)


def _physical_instance_root(requested: Path) -> Path:
    """Preserve terminal-link evidence while normalizing physical parent aliases."""

    if not requested.is_absolute():
        raise SupervisorError("supervisor instance root must be absolute")
    if (
        requested == Path(requested.anchor)
        or not requested.name
        or requested.name in {".", ".."}
    ):
        raise SupervisorError("supervisor instance root is too broad")
    try:
        physical_parent = requested.parent.resolve(strict=True)
        physical = physical_parent / requested.name
    except OSError as exc:
        raise SupervisorError("supervisor instance root is unavailable") from exc
    if os.path.lexists(physical):
        try:
            requested_metadata = physical.lstat()
        except OSError as exc:
            raise SupervisorError("supervisor instance root is unavailable") from exc
        if stat.S_ISLNK(requested_metadata.st_mode):
            raise SupervisorError("supervisor instance root is unsafe")
    try:
        account_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)
    except OSError as exc:
        raise SupervisorError("supervisor instance root is unavailable") from exc
    if physical == account_home:
        raise SupervisorError("supervisor instance root is too broad")
    return physical


def _open_private_child(
    parent_descriptor: int,
    name: str,
    *,
    create: bool,
) -> tuple[int, os.stat_result]:
    if not name or "/" in name or name in {".", ".."}:
        raise SupervisorError("private child coordinate is invalid")
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        except FileExistsError:
            pass
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise SupervisorError("private child directory is unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        public = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or (metadata.st_dev, metadata.st_ino) != (public.st_dev, public.st_ino)
        ):
            raise SupervisorError("private child directory is unsafe")
        return descriptor, metadata
    except BaseException:
        os.close(descriptor)
        raise


def _assert_child_coordinate(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
) -> None:
    try:
        public = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise SupervisorError("private child coordinate changed") from exc
    if (public.st_dev, public.st_ino) != (expected.st_dev, expected.st_ino):
        raise SupervisorError("private child coordinate changed")


def _create_private_regular_at(
    parent_descriptor: int, name: str
) -> tuple[int, os.stat_result]:
    if not name or "/" in name or name in {".", ".."}:
        raise SupervisorError("private control coordinate is invalid")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        public = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or (metadata.st_dev, metadata.st_ino) != (public.st_dev, public.st_ino)
        ):
            raise SupervisorError("private control coordinate is unsafe")
        os.fsync(parent_descriptor)
        return descriptor, metadata
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        raise


def _acquire_operation_lock(app_descriptor: int) -> int:
    descriptor: int | None = None
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        try:
            descriptor = os.open(
                ".upgrade-operation.lock",
                flags,
                dir_fd=app_descriptor,
            )
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    ".upgrade-operation.lock",
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=app_descriptor,
                )
            except FileExistsError:
                descriptor = os.open(
                    ".upgrade-operation.lock",
                    flags,
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
            raise SupervisorError("upgrade operation lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SupervisorError(
                "another upgrade or rollback operation is already running"
            ) from exc
        locked = os.fstat(descriptor)
        public_after = os.stat(
            ".upgrade-operation.lock",
            dir_fd=app_descriptor,
            follow_symlinks=False,
        )
        if (locked.st_dev, locked.st_ino) != (
            public_after.st_dev,
            public_after.st_ino,
        ):
            raise SupervisorError("upgrade operation lock changed")
        os.set_inheritable(descriptor, True)
        return descriptor
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _read_pointer(app: Path, app_descriptor: int) -> tuple[dict[str, Any], Path]:
    descriptor: int | None = None
    payload = b""
    try:
        descriptor = os.open(
            "current-release.json",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=app_descriptor,
        )
        metadata = os.fstat(descriptor)
        public = os.stat(
            "current-release.json",
            dir_fd=app_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size <= 0
            or metadata.st_size > 1024 * 1024
            or (metadata.st_dev, metadata.st_ino) != (public.st_dev, public.st_ino)
        ):
            raise SupervisorError("active-release pointer is unsafe")
        payload = os.read(descriptor, metadata.st_size + 1)
        after = os.fstat(descriptor)
        if len(payload) != metadata.st_size or (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise SupervisorError("active-release pointer changed while reading")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupervisorError("active-release pointer is invalid") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupervisorError("active-release pointer is invalid") from exc
    releases = app / "releases"
    current: object = document
    seen: set[tuple[str, str]] = set()
    active: Path | None = None
    for _depth in range(MAX_POINTER_DEPTH):
        if not isinstance(current, dict) or not {"commit", "path"}.issubset(current):
            raise SupervisorError("active-release pointer chain is invalid")
        if not set(current).issubset({"commit", "path", "previous"}):
            raise SupervisorError("active-release pointer chain is invalid")
        commit = current.get("commit")
        raw_path = current.get("path")
        if (
            not isinstance(commit, str)
            or len(commit) != 40
            or any(character not in "0123456789abcdef" for character in commit)
            or not isinstance(raw_path, str)
        ):
            raise SupervisorError("active-release pointer chain is invalid")
        expected = releases / commit
        if Path(raw_path) != expected:
            raise SupervisorError("active-release pointer path is invalid")
        metadata = _private_directory(expected, exact_mode=False)
        coordinate = (commit, str(expected))
        if coordinate in seen:
            raise SupervisorError("active-release pointer chain is duplicated")
        seen.add(coordinate)
        if active is None:
            active = expected
        previous = current.get("previous")
        if previous is None:
            break
        current = previous
    else:
        raise SupervisorError("active-release pointer chain exceeds the safe depth")
    if active is None:
        raise SupervisorError("active-release pointer is invalid")
    if len(seen) >= MAX_POINTER_DEPTH:
        raise SupervisorError(
            "active-release pointer chain is at its safe retention limit"
        )
    return document, active


def _safe_member_path(name: str) -> PurePosixPath:
    pure = PurePosixPath(name)
    if (
        not name
        or len(name.encode("utf-8")) > MAX_ARCHIVE_PATH_BYTES
        or name.startswith("/")
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise SupervisorError("reviewed controller archive contains an unsafe path")
    return pure


def _open_archive_directory(
    root_descriptor: int,
    parts: tuple[str, ...],
    *,
    create: bool,
) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=descriptor,
            )
            metadata = os.fstat(child)
            public = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or (metadata.st_dev, metadata.st_ino) != (public.st_dev, public.st_ino)
            ):
                os.close(child)
                raise SupervisorError("controller archive directory is unsafe")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _archive_regular_file(
    root_descriptor: int,
    path: PurePosixPath,
    source: Any,
    *,
    size: int,
    executable: bool,
) -> None:
    parent = _open_archive_directory(
        root_descriptor, tuple(path.parts[:-1]), create=True
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent,
        )
        remaining = size
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                raise SupervisorError("reviewed controller archive is incomplete")
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            remaining -= len(chunk)
        if source.read(1):
            raise SupervisorError(
                "reviewed controller archive member exceeded its size"
            )
        os.fchmod(descriptor, 0o700 if executable else 0o600)
        os.fsync(descriptor)
        os.fsync(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _archive_regular_exists(root_descriptor: int, path: str) -> bool:
    pure = _safe_member_path(path)
    try:
        parent = _open_archive_directory(
            root_descriptor, tuple(pure.parts[:-1]), create=False
        )
    except OSError:
        return False
    try:
        metadata = os.stat(pure.name, dir_fd=parent, follow_symlinks=False)
        return stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid()
    except OSError:
        return False
    finally:
        os.close(parent)


def _process_group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    for signal_value, timeout in ((15, 5.0), (9, 30.0)):
        process.poll()
        if not _process_group_exists(process.pid):
            break
        try:
            os.killpg(process.pid, signal_value)
        except ProcessLookupError:
            break
        except PermissionError:
            process.poll()
            if not _process_group_exists(process.pid):
                break
            raise SupervisorError(
                "reviewed controller archive process could not be signaled"
            ) from None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            process.poll()
            if not _process_group_exists(process.pid):
                break
            time.sleep(0.02)
    if process.poll() is None:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    if _process_group_exists(process.pid):
        raise SupervisorError("reviewed controller archive process was not reaped")


def _remove_tree_contents(descriptor: int) -> None:
    for name in os.listdir(descriptor):
        if not name or "/" in name or name in {".", ".."}:
            raise SupervisorError("controller workspace contains an unsafe entry")
        metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=descriptor,
            )
            try:
                opened = os.fstat(child)
                public = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if (opened.st_dev, opened.st_ino) != (
                    public.st_dev,
                    public.st_ino,
                ) or (opened.st_dev, opened.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    raise SupervisorError("controller workspace changed during cleanup")
                _remove_tree_contents(child)
                final_opened = os.fstat(child)
                final_public = os.stat(
                    name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                if (final_opened.st_dev, final_opened.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ) or (final_public.st_dev, final_public.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    raise SupervisorError("controller workspace changed during cleanup")
                os.rmdir(name, dir_fd=descriptor)
            finally:
                os.close(child)
        else:
            final_public = os.stat(
                name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if (final_public.st_dev, final_public.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise SupervisorError("controller workspace changed during cleanup")
            os.unlink(name, dir_fd=descriptor)
    os.fsync(descriptor)


def _remove_private_child(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
) -> None:
    descriptor, opened = _open_private_child(parent_descriptor, name, create=False)
    try:
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise SupervisorError("private child coordinate changed")
        _remove_tree_contents(descriptor)
        _assert_child_coordinate(parent_descriptor, name, expected)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent_descriptor)
    os.fsync(parent_descriptor)


def _materialize_controller(
    repository: Path,
    commit: str,
    destination_descriptor: int,
) -> None:
    with tempfile.TemporaryFile() as archive_stream:
        process = subprocess.Popen(
            [
                SYSTEM_GIT,
                "--no-replace-objects",
                "-C",
                str(repository),
                "archive",
                commit,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
            start_new_session=True,
        )
        assert process.stdout is not None
        deadline = time.monotonic() + 120
        stream_bytes = 0
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_process_group(process)
                    raise SupervisorError("reviewed controller archive timed out")
                readable, _writable, _exceptional = select.select(
                    [process.stdout], [], [], min(0.1, remaining)
                )
                if readable:
                    chunk = os.read(process.stdout.fileno(), 1024 * 1024)
                    if not chunk:
                        break
                    stream_bytes += len(chunk)
                    if stream_bytes > MAX_ARCHIVE_STREAM_BYTES:
                        _terminate_process_group(process)
                        raise SupervisorError(
                            "reviewed controller archive stream is too large"
                        )
                    archive_stream.write(chunk)
                    continue
                if process.poll() is not None and _process_group_exists(process.pid):
                    _terminate_process_group(process)
                    raise SupervisorError(
                        "reviewed controller archive left a background process"
                    )
            return_code = process.wait(timeout=1)
            if return_code != 0:
                raise SupervisorError("reviewed controller archive failed")
            if _process_group_exists(process.pid):
                _terminate_process_group(process)
                raise SupervisorError(
                    "reviewed controller archive left a background process"
                )
        except BaseException:
            if process.poll() is None or _process_group_exists(process.pid):
                _terminate_process_group(process)
            raise
        finally:
            process.stdout.close()

        archive_stream.seek(0)
        total = 0
        members = 0
        with tarfile.open(fileobj=archive_stream, mode="r|") as archive:
            for member in archive:
                members += 1
                if members > MAX_ARCHIVE_MEMBERS:
                    raise SupervisorError(
                        "reviewed controller archive has too many entries"
                    )
                pure = _safe_member_path(member.name)
                if member.isdir():
                    directory = _open_archive_directory(
                        destination_descriptor, tuple(pure.parts), create=True
                    )
                    os.fsync(directory)
                    os.close(directory)
                    continue
                if (
                    not member.isreg()
                    or member.size < 0
                    or member.size > MAX_ARCHIVE_MEMBER_BYTES
                ):
                    raise SupervisorError(
                        "reviewed controller archive contains an unsupported entry"
                    )
                total += member.size
                if total > MAX_ARCHIVE_BYTES:
                    raise SupervisorError("reviewed controller archive is too large")
                source = archive.extractfile(member)
                if source is None:
                    raise SupervisorError("reviewed controller archive is incomplete")
                _archive_regular_file(
                    destination_descriptor,
                    pure,
                    source,
                    size=member.size,
                    executable=bool(member.mode & 0o111),
                )
    marker = os.open(
        SOURCE_MARKER,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=destination_descriptor,
    )
    try:
        os.write(marker, f"{commit}\n".encode("ascii"))
        os.fsync(marker)
    finally:
        os.close(marker)
    os.fsync(destination_descriptor)
    for required in (
        "bootstrap/bootstrap-macos.sh",
        "bootstrap/update.sh",
        "bootstrap/update_controller_guardian.py",
        "bootstrap/upgrade.py",
        "bootstrap/cli.py",
    ):
        if not _archive_regular_exists(destination_descriptor, required):
            raise SupervisorError("fixed controller source is incomplete")


def _run() -> int:
    if len(sys.argv) < 5:
        raise SupervisorError("invalid update-supervisor invocation")
    instance = Path(sys.argv[1])
    candidate = Path(sys.argv[2])
    expected_commit = sys.argv[3]
    controller_arguments = sys.argv[4:]
    bootstrap_mode = bool(
        controller_arguments and controller_arguments[0] == "--bootstrap"
    )
    if bootstrap_mode:
        controller_arguments = controller_arguments[1:]
    test_mode = bool(controller_arguments and controller_arguments[0] == "--test-mode")
    if test_mode:
        controller_arguments = controller_arguments[1:]
    if not instance.is_absolute() or not candidate.is_absolute():
        raise SupervisorError("supervisor coordinates must be absolute")
    instance = _physical_instance_root(instance)
    candidate = candidate.resolve()
    observed_commit = _git(candidate, "rev-parse", "HEAD").decode("ascii").strip()
    status = _git(candidate, "status", "--porcelain", "--untracked-files=all")
    if observed_commit != expected_commit or status:
        raise SupervisorError("reviewed candidate changed before supervision")
    app = instance / "app"
    root_descriptor = (
        _open_or_create_instance_root(instance)
        if bootstrap_mode
        else _open_private_directory(instance)
    )
    app_descriptor, app_coordinate = _open_private_child(
        root_descriptor, "app", create=bootstrap_mode
    )
    lock_descriptor: int | None = None
    controller_workspace: Path | None = None
    controller_terminal = True
    return_code = 1
    tooling_descriptor: int | None = None
    workspace_descriptor: int | None = None
    controller_source_descriptor: int | None = None
    controller_runtime_descriptor: int | None = None
    lifecycle_descriptor: int | None = None
    workspace_name: str | None = None
    workspace_coordinate: os.stat_result | None = None
    try:
        lock_descriptor = _acquire_operation_lock(app_descriptor)
        if bootstrap_mode:
            if os.path.lexists(app / "current-release.json"):
                raise SupervisorError(
                    "bootstrap refuses an initialized release pointer; use update.sh"
                )
            active_release = None
        else:
            _document, active_release = _read_pointer(app, app_descriptor)
        tooling = app / "tooling"
        tooling_descriptor, tooling_coordinate = _open_private_child(
            app_descriptor, "tooling", create=True
        )
        workspace_name = (
            f".upgrade-controller-source-{expected_commit[:12]}.{secrets.token_hex(8)}"
        )
        controller_workspace = tooling / workspace_name
        workspace_descriptor, workspace_coordinate = _open_private_child(
            tooling_descriptor, workspace_name, create=True
        )
        controller_source = controller_workspace / "source"
        controller_source_descriptor, controller_source_coordinate = (
            _open_private_child(workspace_descriptor, "source", create=True)
        )
        _materialize_controller(
            candidate, expected_commit, controller_source_descriptor
        )
        controller_runtime = controller_workspace / "runtime"
        controller_runtime_descriptor, controller_runtime_coordinate = (
            _open_private_child(workspace_descriptor, "runtime", create=True)
        )
        private_home = controller_runtime / "home"
        private_home_descriptor, _private_home_coordinate = _open_private_child(
            controller_runtime_descriptor, "home", create=True
        )
        os.close(private_home_descriptor)
        lifecycle_descriptor, lifecycle_coordinate = _create_private_regular_at(
            workspace_descriptor, CONTROLLER_LIFECYCLE
        )
        lifecycle_path = controller_workspace / CONTROLLER_LIFECYCLE
        _assert_child_coordinate(root_descriptor, "app", app_coordinate)
        _assert_child_coordinate(app_descriptor, "tooling", tooling_coordinate)
        _assert_child_coordinate(
            tooling_descriptor, workspace_name, workspace_coordinate
        )
        _assert_child_coordinate(
            workspace_descriptor, "source", controller_source_coordinate
        )
        _assert_child_coordinate(
            workspace_descriptor, "runtime", controller_runtime_coordinate
        )
        environment = _controller_environment(private_home, test_mode=test_mode)
        environment.update(
            {
                "ADMINME_INSTANCE_ROOT": str(instance),
                "ADMINME_UPGRADE_OPERATION_LOCK_FD": str(lock_descriptor),
                "ADMINME_FIXED_CONTROLLER_ROOT": str(controller_source),
                "ADMINME_CONTROLLER_RUNTIME_ROOT": str(controller_runtime),
                "ADMINME_SUPERVISED_CANDIDATE_COMMIT": expected_commit,
                "ADMINME_CONTROLLER_LIFECYCLE_PATH": str(lifecycle_path),
                "ADMINME_CONTROLLER_LIFECYCLE_DEVICE": str(lifecycle_coordinate.st_dev),
                "ADMINME_CONTROLLER_LIFECYCLE_INODE": str(lifecycle_coordinate.st_ino),
            }
        )
        if bootstrap_mode:
            environment.update(
                {
                    "ADMINME_BOOTSTRAP_SUPERVISED": "1",
                    "ADMINME_SUPERVISED_CANDIDATE_ROOT": str(candidate),
                }
            )
            controller_script = controller_source / "bootstrap" / "bootstrap-macos.sh"
        else:
            assert active_release is not None
            environment.update(
                {
                    "ADMINME_UPDATE_SUPERVISED": "1",
                    "ADMINME_SUPERVISED_ACTIVE_RELEASE_ROOT": str(active_release),
                }
            )
            controller_script = controller_source / "bootstrap" / "update.sh"
        ready_read, ready_write = os.pipe()
        supervisor_read, supervisor_write = os.pipe()
        terminal_read, terminal_write = os.pipe()
        guardian: subprocess.Popen[bytes] | None = None
        controller_terminal = False
        try:
            guardian = subprocess.Popen(
                [
                    "/usr/bin/python3",
                    "-I",
                    str(
                        controller_source
                        / "bootstrap"
                        / "update_controller_guardian.py"
                    ),
                    str(ready_write),
                    str(supervisor_read),
                    str(terminal_write),
                    str(lock_descriptor),
                    str(lifecycle_path),
                    str(lifecycle_coordinate.st_dev),
                    str(lifecycle_coordinate.st_ino),
                    str(CONTROLLER_TIMEOUT_SECONDS),
                    "--",
                    "/bin/bash",
                    "-p",
                    str(controller_script),
                    *controller_arguments,
                ],
                cwd=controller_source,
                env=environment,
                pass_fds=(
                    lock_descriptor,
                    ready_write,
                    supervisor_read,
                    terminal_write,
                ),
                start_new_session=True,
            )
            os.close(ready_write)
            ready_write = -1
            os.close(supervisor_read)
            supervisor_read = -1
            os.close(terminal_write)
            terminal_write = -1
            readable, _writable, _exceptional = select.select([ready_read], [], [], 30)
            if not readable or os.read(ready_read, 1) != b"1":
                raise SupervisorError("fixed update controller did not start safely")
            return_code = guardian.wait(timeout=CONTROLLER_TIMEOUT_SECONDS + 60)
            controller_terminal = os.read(terminal_read, 2) == b"1"
            if not controller_terminal:
                raise SupervisorError("fixed update controller terminality is unknown")
        except BaseException:
            if supervisor_write >= 0:
                os.close(supervisor_write)
                supervisor_write = -1
            if guardian is not None:
                try:
                    guardian.wait(timeout=40)
                except subprocess.TimeoutExpired as exc:
                    raise SupervisorError(
                        "fixed update controller terminality is unknown"
                    ) from exc
                controller_terminal = os.read(terminal_read, 2) == b"1"
                if not controller_terminal:
                    raise SupervisorError(
                        "fixed update controller terminality is unknown"
                    )
            raise
        finally:
            os.close(ready_read)
            if ready_write >= 0:
                os.close(ready_write)
            if supervisor_read >= 0:
                os.close(supervisor_read)
            if supervisor_write >= 0:
                os.close(supervisor_write)
            os.close(terminal_read)
            if terminal_write >= 0:
                os.close(terminal_write)
        if not controller_terminal:
            raise SupervisorError("fixed update controller terminality is unknown")
        assert tooling_descriptor is not None
        assert workspace_name is not None
        assert workspace_coordinate is not None
        _assert_child_coordinate(
            tooling_descriptor, workspace_name, workspace_coordinate
        )
        _remove_private_child(
            tooling_descriptor,
            workspace_name,
            workspace_coordinate,
        )
        controller_workspace = None
        return return_code
    finally:
        if (
            controller_terminal
            and controller_workspace is not None
            and tooling_descriptor is not None
            and workspace_name is not None
            and workspace_coordinate is not None
        ):
            try:
                _assert_child_coordinate(
                    tooling_descriptor, workspace_name, workspace_coordinate
                )
            except SupervisorError:
                pass
            else:
                _remove_private_child(
                    tooling_descriptor,
                    workspace_name,
                    workspace_coordinate,
                )
        if lock_descriptor is not None:
            try:
                if controller_terminal:
                    fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(lock_descriptor)
        for descriptor in (
            controller_source_descriptor,
            controller_runtime_descriptor,
            lifecycle_descriptor,
            workspace_descriptor,
            tooling_descriptor,
        ):
            if descriptor is not None:
                os.close(descriptor)
        os.close(app_descriptor)
        os.close(root_descriptor)


def main() -> int:
    try:
        return _run()
    except Exception as exc:
        print(f"update supervisor refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
