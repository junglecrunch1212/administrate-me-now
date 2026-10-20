"""Fixed, no-send Apple Events permission probe for a Gateway child process.

This file deliberately uses only the standard library and can run with -I -B.
It does not start Messages, send Apple Events, open settings, or accept consent.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

TARGET = "com.apple.MobileSMS"
CONTRACT = "cos-apple-events-preflight-v1"
FRAMEWORK = "/System/Library/Frameworks/CoreServices.framework/CoreServices"
STATUS = {
    0: "authorized",
    -1744: "needs_consent",
    -1743: "denied",
    -600: "target_not_running",
    -1742: "target_not_accessible",
}


def service_environment() -> dict[str, str]:
    """Pin the reviewed helper and running interpreter without probing macOS."""
    environment = {}
    for name, path in {
        "HELPER": Path(__file__).resolve(),
        "DATABASE_HELPER": Path(__file__)
        .resolve()
        .with_name("cos_messages_database.py"),
        "PYTHON": Path(sys.executable).resolve(strict=True),
    }.items():
        environment[f"ADMINISTRATEME_PERMISSION_{name}"] = str(path)
        environment[f"ADMINISTRATEME_PERMISSION_{name}_SHA256"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return environment


class AEAddressDesc(ctypes.Structure):
    # Apple's AEDataModel.h uses #pragma pack(push, 2), including on arm64.
    _layout_ = "ms"
    _pack_ = 2
    _fields_ = [
        ("descriptorType", ctypes.c_uint32),
        ("dataHandle", ctypes.c_void_p),
    ]


def _determine(*, request: bool) -> tuple[str, int]:
    framework = ctypes.CDLL(FRAMEWORK)
    descriptor_pointer = ctypes.POINTER(AEAddressDesc)
    framework.AECreateDesc.argtypes = [
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_long,
        descriptor_pointer,
    ]
    framework.AECreateDesc.restype = ctypes.c_int16
    framework.AEDisposeDesc.argtypes = [descriptor_pointer]
    framework.AEDisposeDesc.restype = ctypes.c_int16
    framework.AEDeterminePermissionToAutomateTarget.argtypes = [
        descriptor_pointer,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_ubyte,
    ]
    framework.AEDeterminePermissionToAutomateTarget.restype = ctypes.c_int32
    target = AEAddressDesc()
    bundle = TARGET.encode("ascii")
    status = int(
        framework.AECreateDesc(
            int.from_bytes(b"bund", "big"),
            bundle,
            len(bundle),
            ctypes.byref(target),
        )
    )
    if status:
        return "descriptor", status
    try:
        wildcard = int.from_bytes(b"****", "big")
        status = int(
            framework.AEDeterminePermissionToAutomateTarget(
                ctypes.byref(target), wildcard, wildcard, int(request)
            )
        )
        return "permission", status
    finally:
        framework.AEDisposeDesc(ctypes.byref(target))


def probe(operation: str = "check") -> dict[str, Any]:
    if operation not in {"check", "request"}:
        raise ValueError("unsupported permission operation")
    result: dict[str, Any] = {
        "contract": CONTRACT,
        "operation": operation,
        "target": TARGET,
        "status": "unsupported",
        "os_status": None,
        "stage": "platform",
    }
    if sys.platform != "darwin":
        return result
    try:
        # Apple explicitly requires this potentially blocking API off the main
        # thread. The Gateway bounds and terminates this isolated child process.
        with ThreadPoolExecutor(max_workers=1) as executor:
            stage, status = executor.submit(
                _determine, request=operation == "request"
            ).result()
        result.update(
            stage=stage,
            os_status=status,
            status=STATUS.get(status, "failed") if stage == "permission" else "failed",
        )
    except (OSError, AttributeError):
        result.update(status="failed", stage="framework")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=("check", "request"), default="check")
    args = parser.parse_args()
    print(json.dumps(probe(args.operation), separators=(",", ":")))


if __name__ == "__main__":
    main()
