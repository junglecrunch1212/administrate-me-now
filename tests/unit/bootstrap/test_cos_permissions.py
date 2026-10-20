"""Synthetic permission boundaries; never call the host's Apple Events API."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import threading

import pytest

from bootstrap import cos_apple_events as helper
from bootstrap.cos_permissions import ticket, verify_result


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (0, "authorized"),
        (-1744, "needs_consent"),
        (-1743, "denied"),
        (-600, "target_not_running"),
        (-1742, "target_not_accessible"),
        (-50, "failed"),
    ],
)
def test_fixed_public_api_abi_and_check_never_requests(monkeypatch, status, expected):
    calls = []

    class Function:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    class Framework:
        AECreateDesc = Function(lambda *args: calls.append(("create", args)) or 0)
        AEDisposeDesc = Function(lambda *args: calls.append(("dispose", args)) or 0)
        AEDeterminePermissionToAutomateTarget = Function(
            lambda *args: (
                calls.append(("permission", args, threading.get_ident())) or status
            )
        )

    library = Framework()
    monkeypatch.setattr(helper.sys, "platform", "darwin")
    monkeypatch.setattr(
        helper.ctypes,
        "CDLL",
        lambda path: library if path == helper.FRAMEWORK else None,
    )
    result = helper.probe()
    assert result["status"] == expected
    assert result["os_status"] == status
    assert result["stage"] == "permission"
    assert [row[0] for row in calls] == ["create", "permission", "dispose"]
    assert calls[0][1][0:3] == (
        int.from_bytes(b"bund", "big"),
        b"com.apple.MobileSMS",
        19,
    )
    assert calls[1][1][1:] == (
        int.from_bytes(b"****", "big"),
        int.from_bytes(b"****", "big"),
        0,
    )
    assert calls[1][2] != threading.get_ident()
    assert helper.AEAddressDesc.dataHandle.offset == 4
    assert ctypes.sizeof(helper.AEAddressDesc) == 4 + ctypes.sizeof(ctypes.c_void_p)
    assert library.AECreateDesc.argtypes[2] is ctypes.c_long
    assert library.AEDeterminePermissionToAutomateTarget.argtypes[3] is ctypes.c_ubyte


def test_request_is_explicit_and_unsupported_platform_never_loads_framework(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(helper.sys, "platform", "darwin")
    monkeypatch.setattr(
        helper,
        "_determine",
        lambda *, request: calls.append(request) or ("permission", -1743),
    )
    assert helper.probe("request")["status"] == "denied"
    assert calls == [True]
    with pytest.raises(ValueError, match="unsupported"):
        helper.probe("send")
    monkeypatch.setattr(helper.sys, "platform", "linux")
    monkeypatch.setattr(
        helper.ctypes, "CDLL", lambda _: pytest.fail("must not load OS API")
    )
    assert helper.probe()["status"] == "unsupported"
    assert calls == [True]


def test_signed_proof_binds_operation_nonce_and_content():
    secret = "synthetic-permission-secret-never-a-real-token"
    authorization = ticket(secret, "check")
    material = "|".join(
        [
            helper.CONTRACT,
            "check",
            authorization["nonce"],
            str(authorization["expires_at_ms"]),
        ]
    )
    assert (
        authorization["signature"]
        == hmac.new(secret.encode(), material.encode(), hashlib.sha256).hexdigest()
    )
    result = {
        "contract": helper.CONTRACT,
        "operation": "check",
        "target": helper.TARGET,
        "status": "needs_consent",
        "os_status": -1744,
        "stage": "permission",
        "nonce": authorization["nonce"],
        "started_at_ms": 1000,
        "completed_at_ms": 1001,
        "helper_sha256": "a" * 64,
        "python_sha256": "b" * 64,
        "gateway_pid": 123,
        "database": None,
    }
    result["signature"] = hmac.new(
        secret.encode(),
        json.dumps(result, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    verify_result(result, authorization, secret)
    with pytest.raises(ValueError):
        verify_result({**result, "status": "authorized"}, authorization, secret)
    with pytest.raises(ValueError):
        verify_result(result, {**authorization, "operation": "request"}, secret)
    with pytest.raises(ValueError):
        verify_result(result, {**authorization, "nonce": "c" * 32}, secret)
