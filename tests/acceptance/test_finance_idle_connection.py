"""Bounded hosted diagnostic of idle-close handoff, never resource qualification.

The original failure has no transport trace. This deliberately yields between
connection selection and header transmission to establish the failure mechanism;
it cannot retrospectively prove the cause of that untraced request.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import time

import httpx
import pytest
import uvicorn
from uvicorn.protocols.http.auto import AutoHTTPProtocol

from tests.acceptance.test_finance_mixed_load import _client
from tests.fixtures.finance_resource_diagnostic import exception_chain

pytestmark = pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_IDLE_DIAGNOSTIC") != "1",
    reason="separate bounded hosted connection diagnostic only",
)


async def _case(shorter_expiry):
    events = []
    requests = []

    def record(kind, **fields):
        assert len(events) < 100
        events.append({"kind": kind, "at": time.monotonic(), **fields})

    class ObservedProtocol(AutoHTTPProtocol):
        def connection_made(self, transport):
            self.fixture_peer = transport.get_extra_info("peername")[1]
            record("connection_made", peer=self.fixture_peer)
            super().connection_made(transport)

        def timeout_keep_alive_handler(self):
            record("server_idle_expired", peer=self.fixture_peer)
            super().timeout_keep_alive_handler()

        def connection_lost(self, exc):
            record(
                "connection_lost",
                peer=self.fixture_peer,
                error_type=type(exc).__name__ if exc else None,
            )
            super().connection_lost(exc)

    async def app(scope, receive, send):
        assert scope["type"] == "http"
        requests.append({"at": time.monotonic(), "peer": scope["client"][1]})
        body = b'{"ok":true}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            http=ObservedProtocol,
            loop="asyncio",
            lifespan="off",
            log_config=None,
            access_log=False,
            timeout_keep_alive=5,
        )
    )
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    result = {
        "candidate": os.environ.get("GITHUB_SHA"),
        "acceptance_eligible": False,
        "deliberate_yield_at_header_send": True,
        "server_idle_seconds": 5,
        "protocol": AutoHTTPProtocol.__name__,
        "client_role": "corrected_candidate"
        if shorter_expiry
        else "historical_control",
        "events": events,
        "requests": requests,
    }
    try:
        async with asyncio.timeout(8):
            while not server.started:
                if serving.done():
                    await serving
                    raise AssertionError("synthetic server stopped before readiness")
                await asyncio.sleep(0.01)
        url = f"http://127.0.0.1:{port}"
        client = (
            httpx.AsyncClient(
                base_url=url,
                timeout=10,
                trust_env=False,
                transport=httpx.AsyncHTTPTransport(
                    trust_env=False,
                    limits=httpx.Limits(keepalive_expiry=5),
                    socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
                ),
            )
            if not shorter_expiry
            else _client(url)
        )
        async with client:
            result["client_idle_seconds"] = client._transport._pool._keepalive_expiry
            assert (await client.get("/")).status_code == 200
            first_finished = time.monotonic()
            await asyncio.sleep(4.5)

            async def trace(name, info):
                record("client_trace", event=name)
                if name == "http11.send_request_headers.started":
                    # Widen the real scheduling window beyond the server's
                    # expiry. The proposed client must already have discarded
                    # the old connection before this hook is reached.
                    await asyncio.sleep(
                        max(0, first_finished + 5.05 - time.monotonic())
                    )

            result["second_started_at"] = time.monotonic()
            try:
                response = await client.get("/", extensions={"trace": trace})
                result["outcome"] = "success"
                result["http_status"] = response.status_code
            except httpx.TransportError as exc:
                result["outcome"] = "error"
                result["exceptions"] = exception_chain(exc)
            result["second_finished_at"] = time.monotonic()
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        listener.close()
    return result


async def test_idle_close_handoff_preserves_failure_and_compares_shorter_expiry(
    tmp_path,
):
    results = []
    output = Path(os.environ.get("FINANCE_IDLE_ARTIFACT_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    try:
        for shorter in (False, True):
            results.append(await asyncio.wait_for(_case(shorter), 25))
    finally:
        (output / "idle-connection-diagnostic.json").write_text(
            json.dumps(results, indent=2) + "\n"
        )
    baseline, control = results
    assert baseline["client_idle_seconds"] == baseline["server_idle_seconds"] == 5
    assert baseline["outcome"] == "error"
    assert any(row["kind"] == "server_idle_expired" for row in baseline["events"])
    assert len(baseline["requests"]) == 1
    assert control["client_idle_seconds"] == 4
    assert control["outcome"] == "success" and control["http_status"] == 200
    assert len(control["requests"]) == 2
    assert control["requests"][0]["peer"] != control["requests"][1]["peer"]
