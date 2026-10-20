"""Authenticated, no-send permission preflight through the local Gateway."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any

from bootstrap.contracts import DeploymentPaths, load_versions
from bootstrap.cos_apple_events import CONTRACT, TARGET
from bootstrap.secrets import secret_store

METHOD = "administrateme.cosPermissions.preflight"
URL = "ws://127.0.0.1:18789"


def ticket(secret: str, operation: str) -> dict[str, Any]:
    if operation not in {"check", "request"}:
        raise ValueError("unsupported permission operation")
    nonce = secrets.token_hex(16)
    expires = int(time.time() * 1000) + 8_000
    signature = hmac.new(
        secret.encode(),
        f"{CONTRACT}|{operation}|{nonce}|{expires}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "operation": operation,
        "nonce": nonce,
        "expires_at_ms": expires,
        "signature": signature,
    }


def verify_result(
    result: dict[str, Any], authorization: dict[str, Any], secret: str
) -> None:
    fields = {
        "contract",
        "operation",
        "target",
        "status",
        "os_status",
        "stage",
        "nonce",
        "started_at_ms",
        "completed_at_ms",
        "helper_sha256",
        "python_sha256",
        "gateway_pid",
        "database",
        "signature",
    }
    if (
        set(result) != fields
        or result.get("contract") != CONTRACT
        or result.get("target") != TARGET
        or result.get("operation") != authorization["operation"]
        or result.get("nonce") != authorization["nonce"]
        or not isinstance(result.get("signature"), str)
    ):
        raise ValueError("permission preflight proof is invalid")
    unsigned = {key: value for key, value in result.items() if key != "signature"}
    expected = hmac.new(
        secret.encode(),
        json.dumps(unsigned, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, result["signature"]):
        raise ValueError("permission preflight proof signature is invalid")


async def preflight(paths: DeploymentPaths, *, request: bool = False) -> dict[str, Any]:
    from websockets.asyncio.client import connect

    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    shared = secret_store(paths).get(references["openclaw_shared"]).decode("ascii")
    if len(shared) < 24:
        raise ValueError("Gateway authentication is unavailable")
    async with connect(
        URL,
        open_timeout=5,
        close_timeout=2,
        max_size=65_536,
        max_queue=8,
        ping_interval=20,
        ping_timeout=20,
    ) as socket:

        async def receive() -> dict[str, Any]:
            value = json.loads(await socket.recv())
            if not isinstance(value, dict):
                raise ValueError("Gateway returned an invalid frame")
            return value

        async def rpc(
            method: str, params: dict[str, Any], *, timeout: int = 10
        ) -> dict[str, Any]:
            request_id = secrets.token_hex(16)
            await socket.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": request_id,
                        "method": method,
                        "params": params,
                    }
                )
            )
            async with asyncio.timeout(timeout):
                while True:
                    frame = await receive()
                    if frame.get("type") != "res" or frame.get("id") != request_id:
                        continue
                    if frame.get("ok") is not True or not isinstance(
                        frame.get("payload"), dict
                    ):
                        raise ValueError("Gateway permission preflight was refused")
                    return frame["payload"]

        challenge = await asyncio.wait_for(receive(), timeout=5)
        if (
            challenge.get("type") != "event"
            or challenge.get("event") != "connect.challenge"
            or not isinstance((challenge.get("payload") or {}).get("nonce"), str)
        ):
            raise ValueError("Gateway handshake failed")
        hello = await rpc(
            "connect",
            {
                "minProtocol": 4,
                "maxProtocol": 4,
                "client": {
                    "id": "gateway-client",
                    "version": "2.0.0-gate8",
                    "displayName": "AdministrateMe permission operator",
                    "platform": "python",
                    "mode": "backend",
                },
                "role": "operator",
                "scopes": ["operator.admin"],
                "caps": [],
                "auth": {"token": shared},
            },
        )
        if (
            hello.get("type") != "hello-ok"
            or (hello.get("server") or {}).get("version")
            != load_versions()["openclaw"]["version"]
            or "operator.admin" not in (hello.get("auth") or {}).get("scopes", [])
            or METHOD not in (hello.get("features") or {}).get("methods", [])
        ):
            raise ValueError("Gateway permission contract is unavailable")
        authorization = ticket(shared, "request" if request else "check")
        result = await rpc(METHOD, authorization, timeout=190 if request else 25)
        verify_result(result, authorization, shared)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", required=True, type=Path)
    parser.add_argument(
        "--request",
        action="store_true",
        help="Explicitly request native consent if needed; never accepts it.",
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(
            preflight(
                DeploymentPaths.from_root(args.instance_root, production=True),
                request=args.request,
            )
        )
    except Exception:
        # Neither handshake errors nor provider/library diagnostics expose secrets.
        raise SystemExit("Permission preflight unavailable") from None
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
