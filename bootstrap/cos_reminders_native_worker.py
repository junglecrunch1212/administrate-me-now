# mypy: disable-error-code="import-untyped,import-not-found"
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false
"""Sealed native-app worker. Compatible with the protected a10 read contract.

This file is copied into the signed component. It must not import its controller
from the selected central release: setup precedes central-client deployment.
Exact-list EventKit operations are imported only after identity checks. A create
additionally requires the separately reviewed version-two write component.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

BUNDLE_ID = "com.administrateme.cos-reminders-helper"
REQUEST_KEYS = frozenset(
    {
        "contract",
        "nonce",
        "issued_at_ms",
        "expires_at_ms",
        "operation",
        "release_root",
        "release_commit",
        "python_executable",
        "python_library",
        "python_library_sha256",
        "device_identity_sha256",
        "os_uid",
        "payload",
    }
)
ITEM_FIELDS = (
    "calendarItemIdentifier",
    "list_identifier",
    "title",
    "notes",
    "priority",
    "completed",
    "completion_date",
    "start_components",
    "due_components",
    "last_modified_at",
    "url",
    "alarms",
    "recurrence_rules",
)
SELECTION_FIELDS = frozenset(
    {
        "adapter_id",
        "source_id",
        "list_id",
        "source_kind",
        "device_identity_sha256",
        "os_uid",
        "source_ownership_evidence_sha256",
        "owner_authorization_sha256",
    }
)


def private_json(path: Path, *, limit: int = 1024 * 1024) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 0 < info.st_size <= limit
        ):
            raise PermissionError("private request file required")
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("request object required")
    return value


def validate_request(value: dict[str, Any], *, now_ms: int | None = None) -> None:
    if (
        set(value) != REQUEST_KEYS
        or value["contract"] != "cos-reminders-helper-request-v1"
    ):
        raise ValueError("unsupported request")
    for key, length in (
        ("nonce", 32),
        ("release_commit", 40),
        ("python_library_sha256", 64),
        ("device_identity_sha256", 64),
    ):
        if not isinstance(value[key], str) or not re.fullmatch(
            r"[0-9a-f]{%s}" % length, value[key]
        ):
            raise ValueError("invalid request identity")
    if (
        any(
            type(value[key]) is not int
            for key in ("issued_at_ms", "expires_at_ms", "os_uid")
        )
        or value["os_uid"] != os.getuid()
    ):
        raise PermissionError("request owner differs")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if not (
        0
        < value["issued_at_ms"]
        <= now
        < value["expires_at_ms"]
        <= value["issued_at_ms"] + 120000
    ):
        raise PermissionError("request expired or not yet valid")
    for key in ("release_root", "python_executable", "python_library"):
        raw = value[key]
        if (
            not isinstance(raw, str)
            or not Path(raw).is_absolute()
            or str(Path(raw)) != raw
        ):
            raise ValueError("absolute canonical request paths required")
    root = Path(value["release_root"])
    if root.resolve() != root or value["python_executable"] != str(
        root / ".venv/bin/python"
    ):
        raise PermissionError("release coordinate differs")
    operation, payload = value["operation"], value["payload"]
    if not isinstance(payload, dict):
        raise ValueError("typed payload required")
    if operation in {"status", "request"}:
        if payload:
            raise ValueError("permission operation has no source payload")
    elif operation == "inspect":
        if set(payload) != {"lookup"} or not isinstance(payload["lookup"], dict):
            raise ValueError("exact lookup required")
    elif operation in {"read", "inspect_write", "create_reminder"}:
        if (
            set(payload)
            != (
                {"selection", "command"}
                if operation == "create_reminder"
                else {"selection"}
            )
            or not isinstance(payload["selection"], dict)
            or set(payload["selection"]) != SELECTION_FIELDS
        ):
            raise ValueError("exact source selection required")
        if operation == "create_reminder" and not isinstance(payload["command"], dict):
            raise ValueError("typed creation command required")
    else:
        raise ValueError("unsupported operation")


def artifact_sha256(app: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        app.rglob("*"), key=lambda entry: entry.relative_to(app).as_posix()
    ):
        info = path.lstat()
        if (
            path.is_symlink()
            or info.st_uid not in {0, os.getuid()}
            or info.st_mode & 0o022
            or (
                not stat.S_ISDIR(info.st_mode)
                and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)
            )
        ):
            raise PermissionError("helper artifact is not immutable")
        kind = "d" if stat.S_ISDIR(info.st_mode) else "f"
        digest.update(
            json.dumps(
                [path.relative_to(app).as_posix(), stat.S_IMODE(info.st_mode), kind],
                separators=(",", ":"),
            ).encode()
        )
        digest.update(b"\0")
        if kind == "f":
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def native_identity(request: dict[str, Any]) -> dict[str, Any]:
    # Foundation here must report the native main bundle AFTER Python embedding.
    from Foundation import NSBundle  # type: ignore[import-not-found]

    bundle = NSBundle.mainBundle()
    app = Path(str(bundle.bundlePath()))
    executable = str(bundle.executablePath())
    buffer = ctypes.create_string_buffer(4096)
    library = ctypes.CDLL("/usr/lib/libproc.dylib")
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.proc_pidpath.restype = ctypes.c_int
    if library.proc_pidpath(os.getpid(), buffer, len(buffer)) <= 0:
        raise PermissionError("native executable unavailable")
    if (
        str(bundle.bundleIdentifier()) != BUNDLE_ID
        or executable != os.fsdecode(buffer.value)
        or not executable.startswith(str(app / "Contents/MacOS") + "/")
        or not bundle.objectForInfoDictionaryKey_(
            "NSRemindersFullAccessUsageDescription"
        )
        or not bundle.objectForInfoDictionaryKey_("NSRemindersUsageDescription")
        or Path(__file__).resolve() != app / "Contents/Resources/worker.py"
    ):
        raise PermissionError("permission-bearing native identity differs")
    root = Path(request["release_root"])
    if (
        sys.executable != request["python_executable"]
        or sys.version_info[:2] != (3, 14)
        or str(Path(sys.prefix)) != str(root / ".venv")
        or (root / ".administrateme-source-commit").read_text().strip()
        != request["release_commit"]
        or hashlib.sha256(Path(request["python_library"]).read_bytes()).hexdigest()
        != request["python_library_sha256"]
    ):
        raise PermissionError("embedded protected release differs")
    from adminme.adapters import cos_apple_reminders as binding
    from adminme.adapters import cos_apple_reminders_native as native

    # Do not silently load another checkout or a future incompatible transport.
    if (
        Path(binding.__file__).resolve()
        != root / "adminme/adapters/cos_apple_reminders.py"
        or Path(native.__file__).resolve()
        != root / "adminme/adapters/cos_apple_reminders_native.py"
        or set(native.SelectedSnapshot.__dataclass_fields__)
        != {"source_id", "list_id", "list_name", "items"}
        or binding.local_device_identity()
        != (request["device_identity_sha256"], request["os_uid"])
    ):
        raise PermissionError("reader release or device contract differs")
    return {
        "app_path": str(app),
        "artifact_sha256": artifact_sha256(app),
        "bundle_identifier": BUNDLE_ID,
        "native_executable": executable,
        "native_pid": os.getpid(),
        **{
            key: request[key]
            for key in (
                "python_executable",
                "release_root",
                "release_commit",
                "python_library_sha256",
                "device_identity_sha256",
                "os_uid",
            )
        },
    }


def permission_status() -> int:
    from EventKit import EKEntityTypeReminder, EKEventStore  # type: ignore[import-not-found]

    return int(EKEventStore.authorizationStatusForEntityType_(EKEntityTypeReminder))


def _wire(value: Any) -> Any:
    if isinstance(value, str):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if value is None or type(value) in {str, int, bool, float}:
        return value
    raise ValueError("unsupported native result type")


def perform(request: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    from adminme.adapters.cos_apple_reminders import (
        CoSRemindersLookup,
        CoSRemindersSelection,
    )
    from adminme.adapters.cos_apple_reminders_native import (
        NativeRemindersReader,
        request_reminders_permission,
    )

    operation = request["operation"]
    if operation == "request":
        request_reminders_permission()
    status = permission_status()
    result: dict[str, Any] = {"authorization_status": status}
    if operation == "status":
        return result
    # EventKit full access/legacy authorized is 3. No other process's receipt counts.
    if status != 3:
        raise PermissionError("native helper Reminders access is not granted")
    if operation == "inspect":
        lookup = CoSRemindersLookup.model_validate(request["payload"]["lookup"])
        result["resource"] = NativeRemindersReader().inspect_selection(lookup)
    elif operation in {"read", "inspect_write", "create_reminder"}:
        selected = dict(request["payload"]["selection"])
        # a10 and the helper client release share the exact provider read contract.
        # The latter additionally pins this component in configuration, never in the
        # account coordinate or source IDs. Setup can use a10 before central upgrade.
        if "native_helper" in CoSRemindersSelection.model_fields:
            selected["native_helper"] = {
                key: identity[key]
                for key in ("app_path", "artifact_sha256", "bundle_identifier")
            }
        selection = CoSRemindersSelection.model_validate(selected)
        if (selection.device_identity_sha256, selection.os_uid) != (
            request["device_identity_sha256"],
            request["os_uid"],
        ):
            raise PermissionError("selected source belongs to another work computer")
        if operation in {"inspect_write", "create_reminder"}:
            manifest = json.loads(
                (
                    Path(identity["app_path"])
                    / "Contents/Resources/helper-manifest.json"
                ).read_bytes()
            )
            if manifest.get("contract") != "cos-reminders-native-app-v2":
                raise PermissionError("This installed helper only supports reads")
            from adminme.adapters import cos_reminders_write as writes

            if (
                Path(writes.__file__).resolve()
                != Path(request["release_root"])
                / "adminme/adapters/cos_reminders_write.py"
            ):
                raise PermissionError("Native writer release differs")
            if operation == "inspect_write":
                result["write_source"] = writes.NativeRemindersWriter().inspect(
                    selection
                )
                return result
            command = writes.CoSReminderCreate.model_validate(
                request["payload"]["command"]
            )
            result["write_result"] = writes.NativeRemindersWriter().create(
                selection, command
            )
            return result
        snapshot = NativeRemindersReader().read(selection)
        if (
            snapshot.source_id != selection.source_id
            or snapshot.list_id != selection.list_id
            or (snapshot.list_name is None and snapshot.items)
            or any(item.list_identifier != selection.list_id for item in snapshot.items)
        ):
            raise PermissionError("native source result differs")
        result["snapshot"] = {
            "source_id": snapshot.source_id,
            "list_id": snapshot.list_id,
            "list_name": snapshot.list_name,
            "items": [
                _wire({field: getattr(item, field) for field in ITEM_FIELDS})
                for item in snapshot.items
            ],
        }
    if permission_status() != 3:
        raise PermissionError("native helper permission changed during operation")
    return result


def run_with_main_loop(
    request: dict[str, Any], identity: dict[str, Any]
) -> dict[str, Any]:
    """Keep the app's main run loop available while EventKit callbacks settle."""
    from Foundation import NSDate, NSRunLoop  # type: ignore[import-not-found]

    complete = threading.Event()
    outcome: dict[str, Any] = {}

    def invoke() -> None:
        try:
            outcome["result"] = perform(request, identity)
        except Exception as exc:
            outcome["error"] = exc
        finally:
            complete.set()

    thread = threading.Thread(target=invoke, daemon=True)
    thread.start()
    loop = NSRunLoop.currentRunLoop()
    while not complete.is_set():
        if int(time.time() * 1000) >= request["expires_at_ms"]:
            raise TimeoutError("native operation did not complete")
        loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))
        complete.wait(0.005)
    if "error" in outcome:
        raise outcome["error"]
    return dict(outcome["result"])


def run(path: Path) -> int:
    parent = path.parent
    info = parent.lstat()
    if (
        path.name != "claimed.json"
        or parent.resolve() != parent
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        return 1
    output: dict[str, Any]
    try:
        # Native launcher atomically consumes request.json; the second claim prevents
        # direct worker replays even after a denied request or partially written result.
        fd = os.open(
            parent / "worker.claim",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        os.close(fd)
        request = private_json(path)
        validate_request(request)
        identity = native_identity(request)
        validate_request(request)  # identity verification must not outlive the request.
        result = run_with_main_loop(request, identity)
        if int(time.time() * 1000) >= request["expires_at_ms"]:
            raise PermissionError("request expired during operation")
        output = {
            "contract": "cos-reminders-helper-result-v1",
            "nonce": request["nonce"],
            "operation": request["operation"],
            "status": "ok",
            "identity": identity,
            "result": result,
        }
        exit_code = 0
    except Exception as exc:
        output = {
            "contract": "cos-reminders-helper-error-v1",
            "status": "failed",
            "error": type(exc).__name__,
        }
        exit_code = 1
    data = json.dumps(output, sort_keys=True, separators=(",", ":")).encode()
    if len(data) > 16 * 1024 * 1024:
        data = b'{"contract":"cos-reminders-helper-error-v1","status":"failed","error":"ResultTooLarge"}'
        exit_code = 1
    fd = os.open(
        parent / "result.json",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return exit_code


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(1)
    raise SystemExit(run(Path(sys.argv[1])))
