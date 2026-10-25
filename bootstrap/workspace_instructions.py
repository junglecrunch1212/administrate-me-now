"""Refresh only generated agent instructions, retaining private workspace overlays.

Plans are prepared before maintenance and retained in the private activation
journal. Installation and recovery accept only their captured byte states, so an
interrupted multi-workspace switch is retryable without overwriting later edits.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from adminme.openclaw_topology import member_agent_id
from bootstrap.contracts import DeploymentPaths


CONTRACT = "administrateme-workspace-instructions-v1"
_MAX_BYTES = 256 * 1024
_MAX_WORKSPACES = 64
_COORDINATE = re.compile(
    r"(?:workspace|member-workspaces/adminme-[0-9a-f]{16})/AGENTS\.md"
)
_HEADER = (
    "# AdministrateMe standing orders\n\n"
    "Generated deterministically from version-controlled program sources. Scheduling "
    "remains in OpenClaw automations; these instructions do not create a second scheduler.\n"
)


class WorkspaceInstructionsError(RuntimeError):
    pass


def instruction_layout(release: Path) -> dict[str, Any] | None:
    """A release-local marker preserves exact legacy compilation on recovery."""
    path = release / "bootstrap/openclaw/workspace-layout.json"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise WorkspaceInstructionsError("release instruction layout is unsafe")
    try:
        layout = json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        raise WorkspaceInstructionsError(
            "release instruction layout is invalid"
        ) from exc
    if (
        not isinstance(layout, dict)
        or set(layout) != {"version", "max_composed_bytes", "priority", "background"}
        or layout["version"] != 1
        or type(layout["max_composed_bytes"]) is not int
        or not 1000 <= layout["max_composed_bytes"] <= 20000
        or any(not isinstance(layout[key], list) for key in ("priority", "background"))
    ):
        raise WorkspaceInstructionsError("release instruction layout is invalid")
    names = layout["priority"] + layout["background"]
    if (
        not layout["priority"]
        or any(
            not isinstance(name, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*\.md", name) is None
            for name in names
        )
        or len(set(names)) != len(names)
        or set(names)
        != {
            path.name for path in (release / "bootstrap/openclaw/programs").glob("*.md")
        }
    ):
        raise WorkspaceInstructionsError(
            "release instruction layout omits or repeats a program"
        )
    return layout


def compile_instructions(release: Path) -> bytes:
    """Match compile-workspace.mjs without running candidate code or Node."""
    root = release / "bootstrap/openclaw/programs"
    if not root.is_dir() or root.is_symlink():
        raise WorkspaceInstructionsError("release instruction sources are unavailable")
    layout = instruction_layout(release)
    names = (
        layout["priority"]
        if layout
        else sorted(path.name for path in root.glob("*.md"))
    )
    programs = []
    for name in names:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise WorkspaceInstructionsError("release instruction source is unsafe")
        programs.append(
            f"\n<!-- source: bootstrap/openclaw/programs/{path.name} -->\n"
            + path.read_bytes().decode("utf-8")
        )
    if not programs:
        raise WorkspaceInstructionsError("release instruction sources are empty")
    content = _HEADER + "\n".join(programs).rstrip() + "\n"
    if layout:
        content += (
            "\n# Background program references\n\n"
            "On a matching standing-order trigger, read the exact program below in the "
            "installed administrateme-household skill's programs/ directory (relative to "
            "its SKILL.md). These retained programs govern their background workflows; "
            "they do not replace the current-request authority above.\n\n"
        )
        content += "".join(f"- `programs/{name}`\n" for name in layout["background"])
    payload = content.encode("utf-8")
    if len(payload) > (layout["max_composed_bytes"] if layout else _MAX_BYTES):
        raise WorkspaceInstructionsError("release instructions exceed the bounded size")
    return payload


def validate_composed_instructions(release: Path, payload: bytes) -> None:
    """Measure the entire injected file, including retained private overlays."""
    layout = instruction_layout(release)
    if layout and len(payload) > layout["max_composed_bytes"]:
        raise WorkspaceInstructionsError(
            "composed workspace instructions exceed the native 20000-byte budget; "
            "private overlays are preserved; reconcile their placement before maintenance"
        )


def _directory(parent: int | None, name: str | Path) -> int:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent,
    )
    metadata = os.fstat(descriptor)
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
        os.close(descriptor)
        raise WorkspaceInstructionsError("instruction workspace directory is unsafe")
    return descriptor


@contextmanager
def _workspace(paths: DeploymentPaths, coordinate: str) -> Iterator[int]:
    if _COORDINATE.fullmatch(coordinate) is None:
        raise WorkspaceInstructionsError("instruction workspace coordinate is invalid")
    descriptors: list[int] = []
    try:
        descriptors.append(_directory(None, paths.instance_dir))
        for part in ("openclaw", *coordinate.split("/")[:-1]):
            descriptors.append(_directory(descriptors[-1], part))
        yield descriptors[-1]
        for parent, child, name in zip(
            descriptors, descriptors[1:], ("openclaw", *coordinate.split("/")[:-1])
        ):
            public = os.stat(name, dir_fd=parent, follow_symlinks=False)
            opened = os.fstat(child)
            if (public.st_dev, public.st_ino) != (opened.st_dev, opened.st_ino):
                raise WorkspaceInstructionsError(
                    "instruction workspace moved during selection"
                )
    except OSError as exc:
        raise WorkspaceInstructionsError(
            "instruction workspace is unavailable or unsafe"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read(descriptor: int) -> tuple[bytes, int]:
    fd = os.open(
        "AGENTS.md",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        dir_fd=descriptor,
    )
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or not 0 < before.st_size <= _MAX_BYTES
        ):
            raise WorkspaceInstructionsError("workspace instructions are unsafe")
        payload = bytearray()
        while len(payload) <= _MAX_BYTES:
            chunk = os.read(fd, min(65536, _MAX_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(fd)
        public = os.stat("AGENTS.md", dir_fd=descriptor, follow_symlinks=False)
        if (
            len(payload) != before.st_size
            or (before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_ino, after.st_size, after.st_mtime_ns)
            or (after.st_dev, after.st_ino) != (public.st_dev, public.st_ino)
        ):
            raise WorkspaceInstructionsError(
                "workspace instructions changed while reading"
            )
        return bytes(payload), stat.S_IMODE(before.st_mode)
    finally:
        os.close(fd)


def _encoded(payload: bytes) -> dict[str, str]:
    return {
        "bytes": base64.b64encode(payload).decode("ascii"),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _decoded(value: Any) -> bytes:
    if not isinstance(value, dict) or set(value) != {"bytes", "sha256"}:
        raise WorkspaceInstructionsError("instruction snapshot is invalid")
    try:
        payload = base64.b64decode(value["bytes"], validate=True)
    except (ValueError, TypeError) as exc:
        raise WorkspaceInstructionsError("instruction snapshot is invalid") from exc
    if (
        not 0 < len(payload) <= _MAX_BYTES
        or hashlib.sha256(payload).hexdigest() != value["sha256"]
    ):
        raise WorkspaceInstructionsError("instruction snapshot digest is invalid")
    return payload


def validate_transition(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"contract", "source_sha256", "target_sha256", "files"}
        or value["contract"] != CONTRACT
        or not isinstance(value["files"], list)
        or not 1 <= len(value["files"]) <= _MAX_WORKSPACES
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value[key])) is None
            for key in ("source_sha256", "target_sha256")
        )
    ):
        raise WorkspaceInstructionsError("instruction transition is invalid")
    coordinates = set()
    for row in value["files"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"coordinate", "mode", "before", "after"}
            or not isinstance(row["coordinate"], str)
            or _COORDINATE.fullmatch(row["coordinate"]) is None
            or row["coordinate"] in coordinates
            or type(row["mode"]) is not int
            or row["mode"] < 0
            or row["mode"] > 0o777
            or row["mode"] & 0o022
        ):
            raise WorkspaceInstructionsError("instruction transition file is invalid")
        coordinates.add(row["coordinate"])
        _decoded(row["before"])
        _decoded(row["after"])
    if "workspace/AGENTS.md" not in coordinates:
        raise WorkspaceInstructionsError("instruction transition template is missing")
    return value


def prepare_transition(
    paths: DeploymentPaths,
    *,
    source_release: Path,
    target_release: Path,
    adapter_config: dict[str, Any],
) -> dict[str, Any] | None:
    before_generated = compile_instructions(source_release)
    after_generated = compile_instructions(target_release)
    if before_generated == after_generated:
        return None
    members = set(
        (adapter_config.get("openclaw_actor_identities") or {}).values()
    ) | set(adapter_config.get("member_chat_enabled_members") or [])
    coordinates = ["workspace/AGENTS.md"] + [
        f"member-workspaces/{member_agent_id(member)}/AGENTS.md"
        for member in sorted(members)
    ]
    files = []
    for coordinate in coordinates:
        with _workspace(paths, coordinate) as descriptor:
            payload, mode = _read(descriptor)
        if payload.count(before_generated) != 1:
            raise WorkspaceInstructionsError(
                "workspace generated instructions differ from the active release; "
                "preserve and reconcile the custom instructions before maintenance"
            )
        selected = payload.replace(before_generated, after_generated, 1)
        validate_composed_instructions(target_release, selected)
        files.append(
            {
                "coordinate": coordinate,
                "mode": mode,
                "before": _encoded(payload),
                "after": _encoded(selected),
            }
        )
    return validate_transition(
        {
            "contract": CONTRACT,
            "source_sha256": hashlib.sha256(before_generated).hexdigest(),
            "target_sha256": hashlib.sha256(after_generated).hexdigest(),
            "files": files,
        }
    )


def _write(descriptor: int, payload: bytes, mode: int) -> None:
    temporary = f".AGENTS.md.{secrets.token_hex(16)}"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
        dir_fd=descriptor,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, "AGENTS.md", src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass


def apply_transition(
    paths: DeploymentPaths, value: Any, *, recover: bool = False
) -> None:
    if value is None:
        return
    transition = validate_transition(value)
    # Validate every destination before the first write. Hold directory handles
    # throughout installation, so a swapped symlink cannot redirect a write.
    from contextlib import ExitStack

    with ExitStack() as stack:
        targets = []
        for row in transition["files"]:
            descriptor = stack.enter_context(_workspace(paths, row["coordinate"]))
            current, mode = _read(descriptor)
            before, after = _decoded(row["before"]), _decoded(row["after"])
            if current not in (before, after) or mode != row["mode"]:
                raise WorkspaceInstructionsError(
                    "workspace instructions changed after preparation"
                )
            targets.append((descriptor, row, before if recover else after))
        for descriptor, row, selected in targets:
            current, mode = _read(descriptor)
            if (
                current not in (_decoded(row["before"]), _decoded(row["after"]))
                or mode != row["mode"]
            ):
                raise WorkspaceInstructionsError(
                    "workspace instructions changed during selection"
                )
            if current != selected:
                _write(descriptor, selected, mode)
            if _read(descriptor) != (selected, mode):
                raise WorkspaceInstructionsError(
                    "workspace instruction selection did not persist"
                )
