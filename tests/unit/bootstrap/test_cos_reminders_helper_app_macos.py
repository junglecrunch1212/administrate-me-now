"""Hosted packaging/embedding proof; the inert worker never imports EventKit.

The separate production protocol tests exercise the real sealed worker. These
fixtures isolate native process identity, replay protection and app reuse.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest

from bootstrap import cos_reminders_helper_app as helper

ROOT = Path(__file__).resolve().parents[3]


# No provider SDK or arbitrary callback is involved. CoreFoundation reads the
# bundle of the already-running process after PyConfig initialized Python.
INERT_WORKER = b"""
import ctypes
import json
import os
from pathlib import Path
import sys
cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
cf.CFBundleGetMainBundle.restype = ctypes.c_void_p
cf.CFBundleGetIdentifier.argtypes = [ctypes.c_void_p]
cf.CFBundleGetIdentifier.restype = ctypes.c_void_p
cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
cf.CFStringCreateWithCString.restype = ctypes.c_void_p
cf.CFBundleGetValueForInfoDictionaryKey.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
cf.CFBundleGetValueForInfoDictionaryKey.restype = ctypes.c_void_p
cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
cf.CFStringGetCString.restype = ctypes.c_bool
bundle = cf.CFBundleGetMainBundle()
def text(value):
    buffer = ctypes.create_string_buffer(4096)
    assert value and cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100)
    return buffer.value.decode()
def field(key):
    key = cf.CFStringCreateWithCString(None, key.encode(), 0x08000100)
    return text(cf.CFBundleGetValueForInfoDictionaryKey(bundle, key))
proc = ctypes.CDLL("/usr/lib/libproc.dylib")
proc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
proc.proc_pidpath.restype = ctypes.c_int
image = ctypes.create_string_buffer(4096)
assert proc.proc_pidpath(os.getpid(), image, len(image)) > 0
request_path = Path(sys.argv[1])
request = json.loads(request_path.read_bytes())
result = {
    "bundle_identifier": text(cf.CFBundleGetIdentifier(bundle)),
    "full_usage": field("NSRemindersFullAccessUsageDescription"),
    "legacy_usage": field("NSRemindersUsageDescription"),
    "native_executable": image.value.decode(),
    "native_pid": os.getpid(),
    "python_executable": sys.executable,
    "prefix": sys.prefix,
    "path": sys.path,
    "release_commit": request["release_commit"],
    "claimed": request_path.name,
    "isolated": sys.flags.isolated,
    "no_site": sys.flags.no_site,
    "provider_imported": "EventKit" in sys.modules,
}
fd = os.open(request_path.with_name("result.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as out:
    json.dump(result, out)
"""


def _sources() -> dict[str, bytes]:
    # An immutable fixture is injected only through the internal packaging seam.
    sources = {name: (ROOT / name).read_bytes() for name in helper.BUILD_SOURCES}
    sources[helper.WORKER_SOURCE] = INERT_WORKER
    return sources


def _library() -> Path:
    library_name = sysconfig.get_config_var("LDLIBRARY") or ""
    library_dir = sysconfig.get_config_var("LIBDIR") or ""
    candidates = [Path(library_dir) / library_name, Path(sys.base_prefix) / "Python"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise AssertionError("hosted macOS CPython has no shared library")


def _copy_runtime_library(private_runtime: Path) -> Path:
    original = _library()
    framework = next(
        (parent for parent in original.parents if parent.suffix == ".framework"),
        None,
    )
    if framework is None:
        library = private_runtime / "Python"
        shutil.copyfile(original, library)
        library.chmod(0o600)
    else:
        relative = original.relative_to(framework)
        assert len(relative.parts) == 3 and relative.parts[0] == "Versions"
        version = relative.parts[1]
        copied_framework = private_runtime / framework.name
        versions = copied_framework / "Versions"
        versions.mkdir(parents=True, mode=0o700)
        # A framework binary's signature includes its versioned Info.plist.
        # Flattening Python into a standalone file invalidates that signature.
        # Preserve vendor bytes and the complete selected version, without
        # copying other installed versions or re-signing the test interpreter.
        shutil.copytree(original.parent, versions / version, symlinks=True)
        for path in copied_framework.rglob("*"):
            if not path.is_symlink():
                path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o022)
        (versions / "Current").symlink_to(version)
        for name in (original.name, "Headers", "Resources"):
            assert (versions / version / name).exists()
            (copied_framework / name).symlink_to(Path("Versions/Current") / name)
        library = copied_framework / relative
        (private_runtime / "Python").symlink_to(library.relative_to(private_runtime))
        # setup-python legitimately adds pip/certificates to the installed
        # resource tree. Verify the binary pages and signed Info.plist, which
        # remain vendor-sealed; actual native dlopen is tested below.
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--verify",
                "--strict",
                "--ignore-resources",
                str(library),
            ],
            check=True,
            capture_output=True,
        )
    assert (
        hashlib.sha256(library.read_bytes()).digest()
        == hashlib.sha256(original.read_bytes()).digest()
    )
    assert not library.stat().st_mode & 0o022
    assert library.stat().st_uid == os.getuid()
    return library


@pytest.fixture(scope="module")
def native_app(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("native-reminders-app").resolve()
    app = directory / "CoS Reminders Helper.app"
    helper._build_app(app, _sources(), include_dir=Path(sysconfig.get_path("include")))
    return app


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> Path:
    release = tmp_path_factory.mktemp("protected-fixture-release").resolve()
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(release / ".venv")],
        check=True,
        capture_output=True,
    )
    # setup-python owns a shared tool cache. Its permissions are not the
    # immutable release fixture's contract, and must never be chmod'ed here.
    # Retain exact interpreter/library bytes in private fixture-owned files.
    executable = release / ".venv/bin/python"
    executable_bytes = executable.resolve().read_bytes()
    executable.unlink()
    executable.write_bytes(executable_bytes)
    executable.chmod(0o700)
    assert (
        hashlib.sha256(executable.read_bytes()).digest()
        == hashlib.sha256(executable_bytes).digest()
    )
    private_runtime = release / "native-fixture-runtime"
    private_runtime.mkdir(mode=0o700)
    _copy_runtime_library(private_runtime)
    (release / ".venv/pyvenv.cfg").chmod(0o600)
    for path in (release, release / ".venv", release / ".venv/bin"):
        path.chmod(0o700)
    marker = release / ".administrateme-source-commit"
    marker.write_text("a" * 40 + "\n")
    marker.chmod(0o600)
    return release


def _request(
    tmp_path: Path, release: Path, **changes: object
) -> tuple[Path, dict[str, object]]:
    directory = tmp_path.resolve() / "private-request"
    directory.mkdir(mode=0o700)
    library = (release / "native-fixture-runtime/Python").resolve()
    now = int(time.time() * 1000)
    value = {
        "contract": "cos-reminders-helper-request-v1",
        "nonce": "b" * 32,
        "issued_at_ms": now,
        "expires_at_ms": now + 90000,
        "operation": "status",
        "release_root": str(release),
        "release_commit": "a" * 40,
        "python_executable": str(release / ".venv/bin/python"),
        "python_library": str(library),
        "python_library_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
        "device_identity_sha256": "c" * 64,
        "os_uid": os.getuid(),
        "payload": {},
    }
    value.update(changes)
    path = directory / "request.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path, value


def _execute(app: Path, request: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(app / "Contents/MacOS" / helper.EXECUTABLE), str(request)],
        capture_output=True,
        check=False,
        timeout=30,
    )


def test_native_macho_identity_and_signed_resources(native_app: Path) -> None:
    receipt = helper.verify_helper_app(native_app)
    assert receipt["bundle_identifier"] == helper.BUNDLE_IDENTIFIER
    assert receipt["artifact_sha256"] == helper.helper_artifact_sha256(native_app)
    info = plistlib.loads((native_app / "Contents/Info.plist").read_bytes())
    assert info["NSRemindersFullAccessUsageDescription"] == helper.USAGE_DESCRIPTION
    executable = native_app / "Contents/MacOS" / helper.EXECUTABLE
    assert str(ROOT).encode() not in executable.read_bytes()
    assert executable.read_bytes()[:4] in {
        b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe",
    }
    result = subprocess.run(
        [str(executable), "--identity"], capture_output=True, check=True
    )
    identity = json.loads(result.stdout)
    assert identity["bundle_identifier"] == helper.BUNDLE_IDENTIFIER
    assert identity["native_executable"] == str(executable)
    assert identity["reminders_full_access_usage"] == helper.USAGE_DESCRIPTION


def test_existing_exact_app_is_reused_without_compiling_or_signing(
    native_app: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = helper.verify_helper_app(native_app)
    before_times = {
        str(path): path.stat().st_mtime_ns for path in native_app.rglob("*")
    }
    original = helper._run
    operations: list[list[str]] = []

    def verify_only(arguments: list[str], **kwargs: object) -> bytes:
        operations.append(arguments)
        assert arguments[:2] == ["/usr/bin/codesign", "--verify"]
        return original(arguments)

    monkeypatch.setattr(helper, "_run", verify_only)
    assert (
        helper._build_app(
            native_app, _sources(), include_dir=Path(sysconfig.get_path("include"))
        )
        == before
    )
    assert operations
    assert {
        str(path): path.stat().st_mtime_ns for path in native_app.rglob("*")
    } == before_times
    changed = _sources()
    changed[helper.WORKER_SOURCE] += b"\n# changed component\n"
    with pytest.raises(RuntimeError, match="choose a new"):
        helper._build_app(
            native_app, changed, include_dir=Path(sysconfig.get_path("include"))
        )


def test_python_is_embedded_in_native_app_and_bound_to_release(
    native_app: Path, release: Path, tmp_path: Path
) -> None:
    request, _ = _request(tmp_path, release)
    executable = native_app / "Contents/MacOS" / helper.EXECUTABLE
    process = subprocess.Popen(
        [str(executable), str(request)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = process.communicate(timeout=30)
    result = json.loads(request.with_name("result.json").read_bytes())
    assert process.returncode == 0, (result, stdout, stderr)
    assert result["native_pid"] == process.pid
    assert result["native_executable"] == str(executable)
    assert result["python_executable"] == str(release / ".venv/bin/python")
    assert result["prefix"] == str(release / ".venv")
    assert str(release) in result["path"]
    assert str(release / ".venv/lib/python3.14/site-packages") in result["path"]
    assert result["bundle_identifier"] == helper.BUNDLE_IDENTIFIER
    assert result["full_usage"] == result["legacy_usage"] == helper.USAGE_DESCRIPTION
    assert result["release_commit"] == "a" * 40
    assert result["claimed"] == "claimed.json"
    assert result["isolated"] == result["no_site"] == 1
    assert result["provider_imported"] is False
    assert not request.exists()
    assert stat.S_IMODE(request.with_name("result.json").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "changes",
    [
        {"nonce": "invalid"},
        {"os_uid": -1},
        {"issued_at_ms": 1, "expires_at_ms": 2},
        {"unexpected": True},
        {"operation": "arbitrary"},
        {"release_commit": "d" * 40},
        {"python_library_sha256": "0" * 64},
        {"python_executable": "/usr/bin/python3"},
    ],
)
def test_native_rejects_invalid_request_before_worker(
    native_app: Path, release: Path, tmp_path: Path, changes: dict[str, object]
) -> None:
    request, _ = _request(tmp_path, release, **changes)
    completed = _execute(native_app, request)
    assert completed.returncode != 0
    result = json.loads(request.with_name("result.json").read_bytes())
    assert result["status"] == "failed"
    assert "native_pid" not in result
    assert str(release) not in json.dumps(result)


@pytest.mark.parametrize(
    "unsafe", ["request_mode", "parent_mode", "symlink", "hardlink"]
)
def test_native_requires_private_regular_single_link_request(
    native_app: Path, release: Path, tmp_path: Path, unsafe: str
) -> None:
    request, _ = _request(tmp_path, release)
    if unsafe == "request_mode":
        request.chmod(0o644)
    elif unsafe == "parent_mode":
        request.parent.chmod(0o755)
    elif unsafe == "symlink":
        moved = request.with_name("other.json")
        request.rename(moved)
        request.symlink_to(moved)
    else:
        os.link(request, request.with_name("other.json"))
    assert _execute(native_app, request).returncode != 0
    assert not request.with_name("claimed.json").exists()


def test_claim_is_single_use_and_existing_result_never_overwritten(
    native_app: Path, release: Path, tmp_path: Path
) -> None:
    request, value = _request(tmp_path, release)
    completed = _execute(native_app, request)
    assert completed.returncode == 0, request.with_name("result.json").read_text()
    result_path = request.with_name("result.json")
    before = result_path.read_bytes()
    request.write_text(json.dumps(value))
    request.chmod(0o600)
    assert _execute(native_app, request).returncode != 0
    assert result_path.read_bytes() == before


def test_launchservices_preserves_native_main_bundle(
    native_app: Path, release: Path, tmp_path: Path
) -> None:
    gui = subprocess.run(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}"],
        check=False,
        capture_output=True,
    )
    assert gui.returncode == 0, "hosted macOS requires a LaunchServices GUI domain"
    request, _ = _request(tmp_path, release)
    launched = subprocess.run(
        ["/usr/bin/open", "-W", "-n", "-a", str(native_app), "--args", str(request)],
        check=False,
        capture_output=True,
        timeout=45,
    )
    assert launched.returncode == 0, launched.stderr
    result = json.loads(request.with_name("result.json").read_bytes())
    assert result.get("bundle_identifier") == helper.BUNDLE_IDENTIFIER, result
    assert result["native_executable"] == str(
        native_app / "Contents/MacOS" / helper.EXECUTABLE
    )
    assert result["python_executable"] == str(release / ".venv/bin/python")
    assert result["provider_imported"] is False


def test_changed_sealed_worker_cannot_execute(
    native_app: Path, release: Path, tmp_path: Path
) -> None:
    altered = tmp_path.resolve() / "Altered.app"
    shutil.copytree(native_app, altered)
    worker = altered / "Contents/Resources/worker.py"
    worker.write_bytes(worker.read_bytes() + b"\n# changed after signing\n")
    with pytest.raises(RuntimeError, match="manifest is invalid"):
        helper.verify_helper_app(altered)
    request, _ = _request(tmp_path, release)
    completed = _execute(altered, request)
    assert completed.returncode != 0
    result = json.loads(request.with_name("result.json").read_bytes())
    assert result["error"] == "helper_identity_invalid"


@pytest.mark.parametrize(
    "coordinate,error",
    [
        (".venv/pyvenv.cfg", "python_venv_config_invalid"),
        (".venv/bin/python", "python_executable_writable"),
        ("native-fixture-runtime/Python", "python_library_writable"),
    ],
)
def test_writable_interpreter_coordinates_remain_rejected(
    native_app: Path, release: Path, tmp_path: Path, coordinate: str, error: str
) -> None:
    request, _ = _request(tmp_path, release)
    target = release / coordinate
    original_mode = stat.S_IMODE(target.stat().st_mode)
    try:
        target.chmod(original_mode | 0o020)
        assert _execute(native_app, request).returncode != 0
        result = json.loads(request.with_name("result.json").read_bytes())
        assert result == {
            "contract": "cos-reminders-helper-error-v1",
            "status": "failed",
            "error": error,
        }
    finally:
        target.chmod(original_mode)


def test_native_library_loader_failure_is_fixed_and_redacted(
    native_app: Path, release: Path, tmp_path: Path
) -> None:
    invalid = tmp_path.resolve() / "private-invalid-library"
    invalid.write_bytes(b"This is not a Mach-O library.\n")
    invalid.chmod(0o600)
    request, _ = _request(
        tmp_path,
        release,
        python_library=str(invalid),
        python_library_sha256=hashlib.sha256(invalid.read_bytes()).hexdigest(),
    )
    completed = _execute(native_app, request)
    assert completed.returncode != 0
    result = json.loads(request.with_name("result.json").read_bytes())
    assert result == {
        "contract": "cos-reminders-helper-error-v1",
        "status": "failed",
        "error": "python_library_dlopen_failed",
    }
    assert not completed.stdout and not completed.stderr
