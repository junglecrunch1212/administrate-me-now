"""Bounded public-entry transition into the fixed-commit update supervisor."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn


SYSTEM_GIT = "/usr/bin/git"
CONTROL_TIMEOUT_SECONDS = 120.0
MAX_IDENTITY_OUTPUT_BYTES = 1024
MAX_SUPERVISOR_SOURCE_BYTES = 2 * 1024 * 1024


class ReviewedLauncherError(RuntimeError):
    """The public entry cannot reach a fixed reviewed controller safely."""


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


def _process_group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap_process_group(process: subprocess.Popen[bytes]) -> None:
    for signal_value, timeout in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 10.0)):
        process.poll()
        if not _process_group_exists(process.pid):
            break
        try:
            os.killpg(process.pid, signal_value)
        except ProcessLookupError:
            break
        except PermissionError as exc:
            raise ReviewedLauncherError(
                "reviewed Git process terminality is unknown"
            ) from exc
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
        raise ReviewedLauncherError("reviewed Git process terminality is unknown")


def _run_git(
    repository: Path,
    arguments: list[str],
    *,
    max_output_bytes: int,
    timeout_seconds: float = CONTROL_TIMEOUT_SECONDS,
) -> bytes:
    process: subprocess.Popen[bytes] | None = None
    failed = False
    try:
        process = subprocess.Popen(
            [
                SYSTEM_GIT,
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(repository),
                *arguments,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
            start_new_session=True,
        )
        assert process.stdout is not None
        deadline = time.monotonic() + timeout_seconds
        output = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReviewedLauncherError("reviewed Git operation timed out")
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
            if len(output) > max_output_bytes:
                raise ReviewedLauncherError(
                    "reviewed Git operation output exceeds its boundary"
                )
        return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if return_code != 0 or _process_group_exists(process.pid):
            raise ReviewedLauncherError("reviewed Git operation failed")
        return bytes(output)
    except (OSError, subprocess.SubprocessError) as exc:
        failed = True
        raise ReviewedLauncherError("reviewed Git operation failed") from exc
    except BaseException:
        failed = True
        raise
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
                    _reap_process_group(process)
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            if failed or sys.exc_info()[0] is not None:
                raise ReviewedLauncherError(
                    "reviewed Git process terminality is unknown"
                ) from cleanup_error
            raise cleanup_error


def _reviewed_commit(candidate: Path) -> tuple[Path, str]:
    if not candidate.is_absolute():
        raise ReviewedLauncherError("reviewed candidate path must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ReviewedLauncherError("reviewed candidate is unavailable") from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise ReviewedLauncherError("reviewed candidate is unsafe")
    inside = _run_git(
        resolved,
        ["rev-parse", "--is-inside-work-tree"],
        max_output_bytes=MAX_IDENTITY_OUTPUT_BYTES,
    ).strip()
    commit_bytes = _run_git(
        resolved,
        ["rev-parse", "HEAD"],
        max_output_bytes=MAX_IDENTITY_OUTPUT_BYTES,
    ).strip()
    status = _run_git(
        resolved,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        max_output_bytes=MAX_IDENTITY_OUTPUT_BYTES,
    )
    try:
        commit = commit_bytes.decode("ascii", "strict")
    except UnicodeDecodeError as exc:
        raise ReviewedLauncherError("reviewed candidate commit is invalid") from exc
    if (
        inside != b"true"
        or len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit)
    ):
        raise ReviewedLauncherError("reviewed candidate commit is invalid")
    if status:
        raise ReviewedLauncherError("reviewed candidate has local changes")
    return resolved, commit


def _validate_expected_commit(
    mode: str, controller_arguments: list[str], commit: str
) -> None:
    if mode != "bootstrap":
        return
    positions = [
        index
        for index, argument in enumerate(controller_arguments)
        if argument == "--expected-commit"
    ]
    if not positions:
        return
    if len(positions) != 1 or positions[0] + 1 >= len(controller_arguments):
        raise ReviewedLauncherError("reviewed expected commit is invalid")
    expected = controller_arguments[positions[0] + 1]
    if len(expected) != 40 or any(
        character not in "0123456789abcdef" for character in expected
    ):
        raise ReviewedLauncherError("reviewed expected commit is invalid")
    if expected != commit:
        raise ReviewedLauncherError(
            f"checked-out commit {commit} does not match reviewed commit {expected}"
        )


def _execute_supervisor(
    mode: str,
    instance: Path,
    candidate: Path,
    controller_arguments: list[str],
) -> NoReturn:
    candidate, commit = _reviewed_commit(candidate)
    _validate_expected_commit(mode, controller_arguments, commit)
    source = _run_git(
        candidate,
        ["show", f"{commit}:bootstrap/update_supervisor.py"],
        max_output_bytes=MAX_SUPERVISOR_SOURCE_BYTES,
    )
    if not source or b"def main()" not in source:
        raise ReviewedLauncherError("fixed update supervisor source is invalid")
    arguments = [str(instance), str(candidate), commit]
    if mode == "bootstrap":
        arguments.append("--bootstrap")
    arguments.extend(controller_arguments)
    sys.argv = [f"{commit}:bootstrap/update_supervisor.py", *arguments]
    namespace = {
        "__builtins__": __builtins__,
        "__file__": sys.argv[0],
        "__name__": "__main__",
        "__package__": None,
    }
    exec(compile(source, sys.argv[0], "exec"), namespace)  # noqa: S102
    raise ReviewedLauncherError("fixed update supervisor returned unexpectedly")


def main() -> int:
    try:
        if len(sys.argv) < 4 or sys.argv[1] not in {"bootstrap", "update"}:
            raise ReviewedLauncherError("invalid reviewed-launcher invocation")
        mode = sys.argv[1]
        instance = Path(sys.argv[2])
        candidate = Path(sys.argv[3])
        if not instance.is_absolute():
            raise ReviewedLauncherError("instance root must be absolute")
        _execute_supervisor(mode, instance, candidate, sys.argv[4:])
    except ReviewedLauncherError as exc:
        print(f"reviewed launcher refused: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
