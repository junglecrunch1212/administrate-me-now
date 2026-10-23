"""Bounded IPC to the exact native process that owns Reminders OS permission."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import sysconfig
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.adapters.cos_apple_reminders import (
    CoSRemindersLookup,
    CoSRemindersSelection,
    NativeHelperBinding,
    local_device_identity,
    verify_device,
)
from adminme.adapters.cos_apple_reminders_native import SelectedSnapshot


NATIVE_ERROR_CODES = frozenset(
    {
        "request_file_invalid",
        "request_contract_invalid",
        "request_already_claimed",
        "helper_identity_invalid",
        "release_coordinate_invalid",
        "python_coordinate_invalid",
        "python_venv_config_invalid",
        "python_executable_missing",
        "python_executable_not_regular",
        "python_executable_not_executable",
        "python_executable_writable",
        "python_executable_owner_invalid",
        "python_library_missing",
        "python_library_metadata_invalid",
        "python_library_writable",
        "python_library_owner_invalid",
        "python_library_digest_invalid",
        "release_marker_invalid",
        "python_library_invalid",
        "python_library_load_failed",
        "python_version_invalid",
        "python_api_invalid",
        "python_initialization_failed",
        "python_path_failed",
        "PermissionError",
        "TimeoutError",
        "ValueError",
        "RuntimeError",
        "ImportError",
        "ModuleNotFoundError",
        "FileExistsError",
        "FileNotFoundError",
        "OSError",
        "ResultTooLarge",
        "helper_no_result",
        "unexpected_native_response",
    }
)


class NativeHelperError(PermissionError):
    """Only fixed diagnostics may survive deletion of private IPC files."""

    def __init__(self, code: str) -> None:
        self.code = code if code in NATIVE_ERROR_CODES else "unexpected_native_response"
        super().__init__(self.code)


def redacted_error(exc: Exception) -> str:
    return type(exc).__name__ + (
        ":" + exc.code if isinstance(exc, NativeHelperError) else ""
    )


class StrictModel(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}


class NativeIdentity(StrictModel):
    app_path: str
    artifact_sha256: str
    bundle_identifier: str
    native_executable: str
    native_pid: int = Field(gt=0)
    python_executable: str
    release_root: str
    release_commit: str
    python_library_sha256: str
    device_identity_sha256: str
    os_uid: int


class NativeItem(StrictModel):
    calendarItemIdentifier: str = Field(min_length=1)
    list_identifier: str = Field(min_length=1)
    title: str
    notes: str | None
    priority: int = Field(ge=0, le=9)
    completed: bool
    completion_date: datetime | None
    start_components: dict[str, Any] | None
    due_components: dict[str, Any] | None
    last_modified_at: datetime | None
    url: str | None
    alarms: list[dict[str, Any]]
    recurrence_rules: list[str]


class NativeSnapshot(StrictModel):
    source_id: str
    list_id: str
    list_name: str | None
    items: list[NativeItem]


class NativeResult(StrictModel):
    contract: Literal["cos-reminders-helper-result-v1"]
    nonce: str
    operation: Literal["status", "request", "inspect", "read"]
    status: Literal["ok"]
    identity: NativeIdentity
    result: dict[str, Any]


def release_coordinates(release_root: Path | None = None) -> dict[str, Any]:
    root = (release_root or Path(__file__).parents[2]).resolve()
    marker = root / ".administrateme-source-commit"
    commit = marker.read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PermissionError("selected protected release marker is invalid")
    executable = root / ".venv/bin/python"
    # Setup may select an older compatible protected release, but never substitute
    # another interpreter/toolchain under the native component's Python ABI.
    if (
        not executable.is_file()
        or executable.resolve() != Path(sys.executable).resolve()
        or sys.version_info[:2] != (3, 14)
    ):
        raise PermissionError("selected release interpreter differs from setup")
    framework = sysconfig.get_config_var("PYTHONFRAMEWORK")
    if framework:
        library = (
            Path(sysconfig.get_config_var("PYTHONFRAMEWORKPREFIX"))
            / (framework + ".framework")
            / "Versions"
            / "3.14"
            / framework
        )
    else:
        library = Path(sysconfig.get_config_var("LIBDIR")) / sysconfig.get_config_var(
            "LDLIBRARY"
        )
    library = library.resolve(strict=True)
    return {
        "release_root": str(root),
        "release_commit": commit,
        "python_executable": str(executable),
        "python_library": str(library),
        "python_library_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
    }


def invoke(
    binding: NativeHelperBinding,
    operation: Literal["status", "request", "inspect", "read"],
    payload: dict[str, Any],
    *,
    release_root: Path | None = None,
) -> dict[str, Any]:
    from bootstrap.cos_reminders_helper_app import EXECUTABLE, verify_helper_app
    from bootstrap.cos_reminders_native_worker import private_json, validate_request

    if sys.platform != "darwin":
        raise RuntimeError("native Reminders helper requires macOS")
    receipt = verify_helper_app(Path(binding.app_path))
    if receipt != binding.model_dump():
        raise PermissionError("native helper differs from its reviewed component")
    device, uid = local_device_identity()
    issued = int(time.time() * 1000)
    request = {
        "contract": "cos-reminders-helper-request-v1",
        "nonce": uuid.uuid4().hex,
        "issued_at_ms": issued,
        "expires_at_ms": issued + 110000,
        "operation": operation,
        **release_coordinates(release_root),
        "device_identity_sha256": device,
        "os_uid": uid,
        "payload": payload,
    }
    validate_request(request)
    with tempfile.TemporaryDirectory(prefix="cos-reminders-") as temporary:
        directory = Path(temporary).resolve()
        os.chmod(directory, 0o700)
        path = directory / "request.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(request, stream, sort_keys=True, separators=(",", ":"))
        process = subprocess.run(
            ["/usr/bin/open", "-W", "-n", "-a", binding.app_path, "--args", str(path)],
            capture_output=True,
            check=False,
            timeout=115,
        )
        if not (directory / "result.json").is_file():
            raise NativeHelperError("helper_no_result")
        raw = private_json(directory / "result.json", limit=16 * 1024 * 1024)
        if raw.get("contract") == "cos-reminders-helper-error-v1":
            if (
                set(raw) == {"contract", "status", "error"}
                and raw["status"] == "failed"
                and isinstance(raw["error"], str)
                and raw["error"] in NATIVE_ERROR_CODES
            ):
                raise NativeHelperError(raw["error"])
            raise NativeHelperError("unexpected_native_response")
        if process.returncode:
            raise NativeHelperError("unexpected_native_response")
        response = NativeResult.model_validate(raw)
        if int(time.time() * 1000) >= request["expires_at_ms"]:
            raise PermissionError("native helper response expired")
        expected = {
            **binding.model_dump(),
            "native_executable": str(
                Path(binding.app_path) / "Contents/MacOS" / EXECUTABLE
            ),
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
        if (
            response.nonce != request["nonce"]
            or response.operation != operation
            or response.identity.model_dump(exclude={"native_pid"}) != expected
            or verify_helper_app(Path(binding.app_path)) != receipt
        ):
            raise PermissionError("native helper response identity differs")
        result = response.result
        expected_keys = {"authorization_status"}
        if operation == "inspect":
            expected_keys.add("resource")
        if operation == "read":
            expected_keys.add("snapshot")
        if (
            set(result) != expected_keys
            or type(result["authorization_status"]) is not int
            or result["authorization_status"] not in {0, 1, 2, 3, 4}
            or (operation != "status" and result["authorization_status"] != 3)
        ):
            raise PermissionError("native helper permission or response differs")
        return result


class HelperRemindersReader:
    """Normal polling uses the same installed app used for explicit setup."""

    def __init__(self, *, release_root: Path | None = None) -> None:
        self._release_root = release_root

    def read(self, selection: CoSRemindersSelection) -> SelectedSnapshot:
        verify_device(selection, local_device_identity())
        result = invoke(
            selection.native_helper,
            "read",
            {
                "selection": selection.model_dump(
                    mode="json", exclude={"native_helper"}
                ),
            },
            release_root=self._release_root,
        )
        # JSON validation accepts only canonical datetime strings on this wire.
        snapshot = NativeSnapshot.model_validate_json(json.dumps(result["snapshot"]))
        if (
            snapshot.source_id != selection.source_id
            or snapshot.list_id != selection.list_id
            or (snapshot.list_name is None and snapshot.items)
            or any(item.list_identifier != selection.list_id for item in snapshot.items)
            or len({item.calendarItemIdentifier for item in snapshot.items})
            != len(snapshot.items)
        ):
            raise PermissionError(
                "native helper returned a foreign or ambiguous source"
            )
        return SelectedSnapshot(
            snapshot.source_id,
            snapshot.list_id,
            snapshot.list_name,
            tuple(SimpleNamespace(**item.model_dump()) for item in snapshot.items),
        )

    def inspect_selection(
        self, lookup: CoSRemindersLookup, binding: NativeHelperBinding
    ) -> dict[str, str]:
        resource = invoke(
            binding,
            "inspect",
            {"lookup": lookup.model_dump(mode="json")},
            release_root=self._release_root,
        )["resource"]
        if (
            not isinstance(resource, dict)
            or set(resource)
            != {"source_id", "source_kind", "source_title", "list_id", "list_name"}
            or any(
                not isinstance(value, str) or not value for value in resource.values()
            )
            or resource["list_name"] != lookup.list_name
            or resource["source_kind"] != lookup.source_kind
            or (
                lookup.source_id is not None
                and resource["source_id"] != lookup.source_id
            )
        ):
            raise PermissionError("native metadata result differs from exact lookup")
        return resource
