"""Install one immutable native CoS Reminders helper, independent of releases.

The app's signed resources contain only helper component bytes. Each private
invocation binds its separately reviewed release and interpreter. Reusing the
same component never recompiles or re-signs the existing TCC identity.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

import bootstrap
from bootstrap import contracts, source_identity
from bootstrap.contracts import repository_root

BUNDLE_IDENTIFIER = "com.administrateme.cos-reminders-helper"
HELPER_APP_CONTRACT = "cos-reminders-native-app-v1"
HELPER_WRITE_APP_CONTRACT = "cos-reminders-native-app-v2"
EXECUTABLE = "CoSRemindersHelper"
LAUNCHER_SOURCE = "bootstrap/native/cos_reminders_launcher.m"
WORKER_SOURCE = "bootstrap/cos_reminders_native_worker.py"
BUILD_SOURCES = (
    "bootstrap/__init__.py",
    "bootstrap/cos_reminders_helper_app.py",
    "bootstrap/contracts.py",
    "bootstrap/source_identity.py",
    LAUNCHER_SOURCE,
    WORKER_SOURCE,
)
USAGE_DESCRIPTION = (
    "The Chief of Staff reads only the authorized Reminders list for private "
    "household coordination. It does not create, change, or send reminders."
)

WRITE_USAGE_DESCRIPTION = (
    "The Chief of Staff reads and creates reminders in the authorized household "
    "work list, only for currently authorized requests."
)


def _run(arguments: list[str], *, cwd: Path | None = None) -> bytes:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=source_identity.sanitized_git_environment(),
        check=False,
        capture_output=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError("native helper build or verification failed")
    return result.stdout


def _reviewed_sources(root: Path, expected_commit: str | None) -> dict[str, bytes]:
    commit = source_identity.reviewed_candidate_commit(root)
    if expected_commit is not None and commit != expected_commit:
        raise RuntimeError("helper source differs from the expected protected commit")
    # The operator updates origin/main through the normal protected delivery path.
    # Ancestors remain usable when a later central-only release is already main.
    _run(
        [
            "/usr/bin/git",
            "merge-base",
            "--is-ancestor",
            commit,
            "refs/remotes/origin/main",
        ],
        cwd=root,
    )
    payloads = {}
    for relative in BUILD_SOURCES:
        payload = _run(["/usr/bin/git", "show", f"{commit}:{relative}"], cwd=root)
        source = root / relative
        metadata = source.lstat()
        if not stat.S_ISREG(metadata.st_mode) or source.read_bytes() != payload:
            raise RuntimeError("helper build inputs differ from protected source")
        payloads[relative] = payload
    # A clean index alone does not prove which implementation Python imported.
    for relative, loaded in (
        ("bootstrap/__init__.py", bootstrap.__file__),
        ("bootstrap/cos_reminders_helper_app.py", __file__),
        ("bootstrap/source_identity.py", source_identity.__file__),
        ("bootstrap/contracts.py", contracts.__file__),
    ):
        if (
            loaded is None
            or Path(loaded).is_symlink()
            or Path(loaded).read_bytes() != payloads[relative]
        ):
            raise RuntimeError("loaded helper builder differs from protected source")
    return payloads


def _component_manifest(
    sources: dict[str, bytes], *, write_enabled: bool = False
) -> dict[str, object]:
    return {
        "contract": HELPER_WRITE_APP_CONTRACT if write_enabled else HELPER_APP_CONTRACT,
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "python_abi": "cpython-3.14",
        "native_in_process": True,
        "sources": {
            name: hashlib.sha256(data).hexdigest()
            for name, data in sorted(sources.items())
        },
    }


def _info(*, write_enabled: bool = False) -> dict[str, object]:
    return {
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleName": "CoS Reminders Helper",
        "CFBundleDisplayName": "CoS Reminders Helper",
        "CFBundleExecutable": EXECUTABLE,
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "2" if write_enabled else "1",
        "CFBundleShortVersionString": "2.0" if write_enabled else "1.0",
        "LSMinimumSystemVersion": "14.0",
        "LSUIElement": True,
        "NSRemindersFullAccessUsageDescription": WRITE_USAGE_DESCRIPTION
        if write_enabled
        else USAGE_DESCRIPTION,
        "NSRemindersUsageDescription": WRITE_USAGE_DESCRIPTION
        if write_enabled
        else USAGE_DESCRIPTION,
    }


def helper_artifact_sha256(app: Path) -> str:
    """Hash the exact immutable signed tree, including all resource seals/modes."""
    digest = hashlib.sha256()
    for path in sorted(
        app.rglob("*"), key=lambda entry: entry.relative_to(app).as_posix()
    ):
        metadata = path.lstat()
        relative = path.relative_to(app).as_posix()
        if path.is_symlink() or not (
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        ):
            raise RuntimeError("helper artifact contains an unsafe entry")
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
            raise RuntimeError("helper artifact is not owner controlled")
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise RuntimeError("helper artifact contains a hard-linked resource")
        digest.update(
            json.dumps(
                [
                    relative,
                    stat.S_IMODE(metadata.st_mode),
                    "d" if path.is_dir() else "f",
                ],
                separators=(",", ":"),
            ).encode()
        )
        digest.update(b"\0")
        if path.is_file():
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _verify_app(app: Path, manifest: dict[str, object]) -> dict[str, str]:
    if (
        app.is_symlink()
        or not app.is_dir()
        or app.stat().st_uid != os.getuid()
        or app.stat().st_mode & 0o022
    ):
        raise RuntimeError(
            "helper destination is not an immutable owner-controlled app"
        )
    try:
        actual_manifest = json.loads(
            (app / "Contents/Resources/helper-manifest.json").read_bytes()
        )
        actual_info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise RuntimeError("helper artifact metadata is invalid") from exc
    if actual_manifest != manifest or actual_info != _info(
        write_enabled=manifest.get("contract") == HELPER_WRITE_APP_CONTRACT
    ):
        raise RuntimeError("existing helper differs; choose a new .app destination")
    digest = helper_artifact_sha256(app)
    _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
    return {
        "app_path": str(app),
        "artifact_sha256": digest,
        "bundle_identifier": BUNDLE_IDENTIFIER,
    }


def verify_helper_app(app: Path) -> dict[str, str]:
    """Verify an installed sealed component; its returned digest must be pinned."""
    if not app.is_absolute() or app.resolve() != app:
        raise RuntimeError("helper path must be absolute and canonical")
    try:
        manifest = json.loads(
            (app / "Contents/Resources/helper-manifest.json").read_bytes()
        )
        sources = manifest["sources"]
        contract = manifest.get("contract")
        if contract not in {HELPER_APP_CONTRACT, HELPER_WRITE_APP_CONTRACT}:
            raise ValueError("unsupported helper contract")
        expected = {
            "contract": contract,
            "bundle_identifier": BUNDLE_IDENTIFIER,
            "python_abi": "cpython-3.14",
            "native_in_process": True,
            "sources": sources,
        }
        if (
            manifest != expected
            or not isinstance(sources, dict)
            or set(sources) != set(BUILD_SOURCES)
            or any(
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in sources.values()
            )
            or hashlib.sha256(
                (app / "Contents/Resources/worker.py").read_bytes()
            ).hexdigest()
            != sources[WORKER_SOURCE]
        ):
            raise ValueError("invalid component manifest")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("helper component manifest is invalid") from exc
    return _verify_app(app, manifest)


def _build_app(
    destination: Path,
    sources: dict[str, bytes],
    *,
    include_dir: Path,
    write_enabled: bool = False,
) -> dict[str, str]:
    """Internal packaging seam for hosted immutable-fixture tests, never CLI input."""
    if (
        sys.platform != "darwin"
        or sys.version_info[:2] != (3, 14)
        or sysconfig.get_config_var("Py_GIL_DISABLED")
    ):
        raise RuntimeError(
            "native helper packaging requires standard macOS CPython 3.14"
        )
    if (
        not destination.is_absolute()
        or destination.suffix != ".app"
        or destination.name in {".app", "..app"}
    ):
        raise RuntimeError("choose an explicit absolute .app destination")
    parent = destination.parent
    if (
        not parent.is_dir()
        or parent.resolve() != parent
        or parent.stat().st_mode & 0o022
    ):
        raise RuntimeError(
            "helper destination parent must already exist and be owner controlled"
        )
    manifest = _component_manifest(sources, write_enabled=write_enabled)
    if os.path.lexists(destination):
        return _verify_app(destination, manifest)
    # Sign only the new staged bundle. The published app is never replaced or re-signed.
    with tempfile.TemporaryDirectory(
        prefix=".cos-reminders-build-", dir=parent
    ) as staging:
        staged = Path(staging) / destination.name
        macos = staged / "Contents/MacOS"
        resources = staged / "Contents/Resources"
        macos.mkdir(parents=True, mode=0o700)
        resources.mkdir(mode=0o700)
        source = Path(staging) / "launcher.m"
        source.write_bytes(sources[LAUNCHER_SOURCE])
        (resources / "worker.py").write_bytes(sources[WORKER_SOURCE])
        (resources / "helper-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        )
        (staged / "Contents/Info.plist").write_bytes(
            plistlib.dumps(_info(write_enabled=write_enabled), sort_keys=True)
        )
        executable = macos / EXECUTABLE
        _run(
            [
                "/usr/bin/xcrun",
                "clang",
                "-fobjc-arc",
                "-Os",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wno-deprecated-declarations",
                "-mmacosx-version-min=14.0",
                "-I",
                str(include_dir),
                "-framework",
                "Foundation",
                "-framework",
                "Security",
                str(source),
                "-o",
                str(executable),
            ]
        )
        for entry in staged.rglob("*"):
            entry.chmod(0o700 if entry.is_dir() or entry == executable else 0o600)
        staged.chmod(0o700)
        # Default ad-hoc designated requirement binds the exact code directory.
        # Never supply a broad custom requirement to preserve grants across changes.
        _run(["/usr/bin/codesign", "--sign", "-", "--timestamp=none", str(staged)])
        _verify_app(staged, manifest)
        # macOS exclusive rename is atomic, including a non-cooperating creator.
        # A failed publish leaves an existing app byte-for-byte untouched.
        libc = ctypes.CDLL(None, use_errno=True)
        rename_exclusive = libc.renameatx_np
        rename_exclusive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        if rename_exclusive(
            -2, os.fsencode(staged), -2, os.fsencode(destination), 0x00000004
        ):
            raise RuntimeError("helper destination appeared during installation")
    return _verify_app(destination, manifest)


def build_cos_reminders_helper_app(
    destination: Path,
    *,
    root: Path | None = None,
    expected_commit: str | None = None,
    write_enabled: bool = False,
) -> dict[str, str]:
    """Install/reuse exact protected helper bytes, without central runtime edits."""
    source_root = root or repository_root()
    if not source_root.is_absolute() or source_root.resolve() != source_root:
        raise RuntimeError("helper source must be an absolute reviewed checkout")
    if expected_commit is not None and not re.fullmatch(
        r"[0-9a-f]{40}", expected_commit
    ):
        raise RuntimeError("helper requires an exact protected source commit")
    sources = _reviewed_sources(source_root, expected_commit)
    if destination.is_relative_to(source_root):
        raise RuntimeError("helper must be installed outside the reviewed source")
    include = sysconfig.get_path("include")
    if not include or not (Path(include) / "Python.h").is_file():
        raise RuntimeError("reviewed CPython headers are unavailable")
    return _build_app(
        destination, sources, include_dir=Path(include), write_enabled=write_enabled
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=repository_root())
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument(
        "--enable-writes",
        action="store_true",
        help="install the separately reviewed create-capable component; preserve any existing helper",
    )
    arguments = parser.parse_args(argv)
    try:
        receipt = build_cos_reminders_helper_app(
            arguments.destination,
            root=arguments.source,
            expected_commit=arguments.expected_commit,
            write_enabled=arguments.enable_writes,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(
            f"CoS Reminders helper installation failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
