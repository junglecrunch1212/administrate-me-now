"""Unit tests for ``adminme_bridge.ingest_client``.

Pins the retry/backoff contract — the bridge daemon cannot lose events
on transient central-side hiccups, so 5xx → exponential backoff, 429 →
``Retry-After``, 4xx (non-429) → fail fast.
"""

from __future__ import annotations

from typing import Any

import httpx

from adminme_bridge.ingest_client import (
    BATCH_CHUNK_SIZE,
    IngestClient,
    MAX_ATTEMPTS,
)


class _FakeResponse:
    """Minimal stand-in for ``httpx.Response`` with the bits the client reads."""

    def __init__(
        self, status_code: int, body: dict | None = None, headers: dict | None = None
    ) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}
        self.text = repr(self._body)

    def json(self) -> dict:
        return self._body


class _ScriptedClient:
    """Drop-in for ``httpx.AsyncClient`` that returns a scripted sequence
    of responses (or raises on exhaustion)."""

    def __init__(self, responses: list[_FakeResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, json: Any, headers: dict) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers})
        if not self._responses:
            raise AssertionError("scripted client ran out of responses")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def aclose(self) -> None:
        pass


def _record_sleeps() -> tuple[list[float], Any]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return sleeps, fake_sleep


async def test_submit_happy_path_returns_event_id() -> None:
    client = _ScriptedClient([_FakeResponse(202, {"event_id": "ev_abc"})])
    sleeps, sleep = _record_sleeps()
    ic = IngestClient(
        central_endpoint_url="http://127.0.0.1:3337",
        tailscale_identity_header="m1@example.ts.net",
        client=client,  # type: ignore[arg-type]
        sleep=sleep,
    )
    result = await ic.submit({"any": "envelope"})
    assert result.ok is True
    assert result.event_id == "ev_abc"
    assert result.status_code == 202
    assert client.calls[0]["url"] == "http://127.0.0.1:3337/api/bridge/ingest"
    assert client.calls[0]["headers"]["Tailscale-User-Login"] == "m1@example.ts.net"
    # Happy path = no backoff.
    assert sleeps == []


async def test_submit_5xx_then_success_records_backoff() -> None:
    """Two 500s then a 202: the client should sleep with exponential
    backoff between attempts and ultimately succeed."""
    client = _ScriptedClient(
        [
            _FakeResponse(500, {"detail": "boom"}),
            _FakeResponse(500, {"detail": "boom"}),
            _FakeResponse(202, {"event_id": "ev_xyz"}),
        ]
    )
    sleeps, sleep = _record_sleeps()
    ic = IngestClient(
        central_endpoint_url="http://127.0.0.1:3337",
        tailscale_identity_header="m1@example.ts.net",
        client=client,  # type: ignore[arg-type]
        sleep=sleep,
    )
    result = await ic.submit({"any": "envelope"})
    assert result.ok is True
    assert result.event_id == "ev_xyz"
    # First retry uses INITIAL_BACKOFF_S (1.0); the second doubles to 2.0.
    assert sleeps == [1.0, 2.0]


async def test_submit_429_respects_retry_after_header() -> None:
    """A 429 with ``Retry-After: 7`` must sleep for exactly 7 seconds
    on the next attempt, NOT fall through to exponential."""
    client = _ScriptedClient(
        [
            _FakeResponse(429, {"detail": "rate"}, headers={"Retry-After": "7"}),
            _FakeResponse(202, {"event_id": "ev_ok"}),
        ]
    )
    sleeps, sleep = _record_sleeps()
    ic = IngestClient(
        central_endpoint_url="http://127.0.0.1:3337",
        tailscale_identity_header="m1@example.ts.net",
        client=client,  # type: ignore[arg-type]
        sleep=sleep,
    )
    result = await ic.submit({"any": "envelope"})
    assert result.ok is True
    assert sleeps == [7.0]


async def test_submit_4xx_non_429_fails_fast_no_retry() -> None:
    """A 422 is a client bug — retrying just burns budget. The client
    must surface it on attempt 1 with no sleep."""
    client = _ScriptedClient(
        [_FakeResponse(422, {"detail": "envelope_validation_failed"})]
    )
    sleeps, sleep = _record_sleeps()
    ic = IngestClient(
        central_endpoint_url="http://127.0.0.1:3337",
        tailscale_identity_header="m1@example.ts.net",
        client=client,  # type: ignore[arg-type]
        sleep=sleep,
    )
    result = await ic.submit({"any": "envelope"})
    assert result.ok is False
    assert result.status_code == 422
    assert "client_error" in (result.error or "")
    assert sleeps == []


async def test_submit_transport_error_retries_then_gives_up() -> None:
    """Persistent transport errors exhaust ``MAX_ATTEMPTS`` and return
    ``ok=False`` — never let the daemon silently lose events without
    surfacing the failure."""
    errors: list[Exception] = [
        httpx.ConnectError("conn refused") for _ in range(MAX_ATTEMPTS)
    ]
    client = _ScriptedClient(errors)  # type: ignore[arg-type]
    _, sleep = _record_sleeps()
    ic = IngestClient(
        central_endpoint_url="http://127.0.0.1:3337",
        tailscale_identity_header="m1@example.ts.net",
        client=client,  # type: ignore[arg-type]
        sleep=sleep,
    )
    result = await ic.submit({"any": "envelope"})
    assert result.ok is False
    assert "transport_error" in (result.error or "")


async def test_submit_batch_chunks_at_batch_chunk_size() -> None:
    """``submit_batch`` must split envelopes into chunks of at most
    :data:`BATCH_CHUNK_SIZE` per HTTP call."""
    chunks = 3
    envelopes = [{"i": i} for i in range(BATCH_CHUNK_SIZE * chunks)]
    client = _ScriptedClient(
        [
            _FakeResponse(
                202, {"event_ids": [f"ev_{i}" for i in range(BATCH_CHUNK_SIZE)]}
            )
            for _ in range(chunks)
        ]
    )
    _, sleep = _record_sleeps()
    ic = IngestClient(
        central_endpoint_url="http://127.0.0.1:3337",
        tailscale_identity_header="m1@example.ts.net",
        client=client,  # type: ignore[arg-type]
        sleep=sleep,
    )
    result = await ic.submit_batch(envelopes)
    assert result.ok is True
    assert result.chunks_sent == chunks
    assert len(result.event_ids) == BATCH_CHUNK_SIZE * chunks
    for call in client.calls:
        assert call["url"].endswith("/api/bridge/ingest/batch")
        assert len(call["json"]["envelopes"]) <= BATCH_CHUNK_SIZE


async def test_submit_batch_failure_reports_chunk_offset() -> None:
    """If chunk #1 of 3 fails, the result surfaces the offset so the
    bridge daemon can log + alert on which slice of the batch the
    central service rejected — never report success when only part of
    the batch landed."""
    envelopes = [{"i": i} for i in range(BATCH_CHUNK_SIZE * 3)]
    client = _ScriptedClient(
        [
            _FakeResponse(
                202, {"event_ids": [f"ev_a_{i}" for i in range(BATCH_CHUNK_SIZE)]}
            ),
            _FakeResponse(
                400, {"detail": {"error": "batch_envelope_rejected", "offset": 7}}
            ),
        ]
    )
    _, sleep = _record_sleeps()
    ic = IngestClient(
        central_endpoint_url="http://127.0.0.1:3337",
        tailscale_identity_header="m1@example.ts.net",
        client=client,  # type: ignore[arg-type]
        sleep=sleep,
    )
    result = await ic.submit_batch(envelopes)
    assert result.ok is False
    assert result.error_chunk_offset == 1
    # Chunk-0 IDs are returned alongside the failure for operator triage.
    assert len(result.event_ids) == BATCH_CHUNK_SIZE
