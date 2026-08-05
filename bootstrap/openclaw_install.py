"""Validation for the exact reviewed OpenClaw source installation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from bootstrap.contracts import DeploymentPaths, load_versions


class OpenClawInstallError(RuntimeError):
    pass


def source_receipt_path(paths: DeploymentPaths) -> Path:
    return paths.instance_dir / "openclaw" / "source-install.json"


def reviewed_source_status(
    paths: DeploymentPaths,
    *,
    test_mode: bool = False,
    executable: str | None = None,
) -> dict[str, Any]:
    expected = load_versions()["openclaw"]
    if test_mode:
        return {
            "ok": True,
            "detail": "fixture reviewed OpenClaw source",
            "version": expected["version"],
            "commit": expected["commit"],
            "archive_sha256": expected["source_archive_sha256"],
        }
    receipt_path = source_receipt_path(paths)
    if not receipt_path.is_file() or receipt_path.is_symlink():
        return {"ok": False, "detail": "reviewed OpenClaw source receipt is missing"}
    try:
        receipt = json.loads(receipt_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "detail": "reviewed OpenClaw source receipt is invalid"}
    source_root = paths.app_dir / "openclaw" / expected["commit"]
    package_path = source_root / "package.json"
    entry_path = source_root / "openclaw.mjs"
    if (
        not source_root.is_dir()
        or source_root.is_symlink()
        or not package_path.is_file()
        or package_path.is_symlink()
        or not entry_path.is_file()
        or entry_path.is_symlink()
    ):
        return {"ok": False, "detail": "reviewed OpenClaw source tree is incomplete"}
    try:
        package = json.loads(package_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "detail": "reviewed OpenClaw package metadata is invalid"}
    observed_executable = executable or shutil.which("openclaw")
    executable_ok = bool(observed_executable) and Path(
        str(observed_executable)
    ).resolve() == (entry_path.resolve())
    receipt_ok = (
        receipt.get("schema_version") == 1
        and receipt.get("install_kind") == "verified-github-source-archive"
        and receipt.get("source_root") == str(source_root)
        and receipt.get("version") == expected["version"]
        and receipt.get("commit") == expected["commit"]
        and receipt.get("source_archive_url") == expected["source_archive_url"]
        and receipt.get("source_archive_sha256") == expected["source_archive_sha256"]
        and receipt.get("package_manager") == expected["package_manager"]
    )
    package_ok = (
        package.get("name") == "openclaw"
        and package.get("version") == expected["version"]
        and package.get("packageManager") == expected["package_manager"]
    )
    ok = receipt_ok and package_ok and executable_ok
    return {
        "ok": ok,
        "detail": (
            "verified GitHub source archive and executable agree"
            if ok
            else "OpenClaw source provenance or executable mismatch"
        ),
        "version": expected["version"],
        "commit": expected["commit"],
        "archive_sha256": expected["source_archive_sha256"],
        "package_metadata_matches": package_ok,
        "receipt_matches": receipt_ok,
        "executable_matches": executable_ok,
    }


def resolve_reviewed_openclaw(
    paths: DeploymentPaths,
    *,
    test_mode: bool = False,
) -> str:
    executable = shutil.which("openclaw")
    if not executable:
        raise OpenClawInstallError("the pinned OpenClaw executable is unavailable")
    status = reviewed_source_status(
        paths,
        test_mode=test_mode,
        executable=executable,
    )
    if not status["ok"]:
        raise OpenClawInstallError(str(status["detail"]))
    return executable


__all__ = [
    "OpenClawInstallError",
    "resolve_reviewed_openclaw",
    "reviewed_source_status",
    "source_receipt_path",
]
